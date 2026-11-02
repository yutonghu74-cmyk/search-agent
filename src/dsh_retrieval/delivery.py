"""Bounded local image delivery: immutable ZIP, explicit publication, gated reads.

Media I/O happens outside ledger transactions. SQLite commits the staged object,
task fence and event together. This is not a downstream training publication.
"""
from contextlib import closing
from hashlib import sha256
from io import BytesIO
import json
import uuid
from zipfile import ZipFile, ZipInfo, ZIP_STORED, BadZipFile

from .errors import ServiceError
from .integration_models import AccessContext
from .models import RunStatus
from .service import require_version

MAX_PACKAGE_BYTES = 64 * 1024 * 1024
MAX_IMAGE_BYTES = 10 * 1024 * 1024


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def digest(content):
    return 'sha256:' + sha256(content).hexdigest()


def load_delivery_records(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='image_deliveries'").fetchone():
        return {}
    records = {}
    try:
        for package_id, payload in db.execute('SELECT package_id,metadata FROM image_deliveries'):
            record = json.loads(payload)
            if (record['package_id'] != package_id or record['status'] not in ('staged', 'published')
                    or any(type(record[key]) is not int or record[key] < 1 for key in (
                        'revision', 'generation', 'source_version', 'stage_version', 'current_version', 'byte_count'))
                    or record['stage_version'] != record['source_version'] + 1
                    or record['current_version'] != record['stage_version'] + (record['status'] == 'published')
                    or record['manifest_digest'] != digest(canonical(record['manifest']))
                    or not 0 < record['byte_count'] <= MAX_PACKAGE_BYTES
                    or record['source_id'] in records
                    or any(record['manifest'][key] != record[key] for key in ('task_id', 'revision', 'generation'))
                    or record['manifest']['source_result_id'] != record['source_id']
                    or record['manifest']['source_result_digest'] != record['source_digest']):
                raise ValueError('invalid delivery metadata')
            records[record['source_id']] = record
    except (KeyError, TypeError, ValueError) as exc:
        raise ServiceError('STORAGE_CORRUPT', 'Invalid delivery metadata', 500) from exc
    return records


def current_delivery(task, state, source_id):
    record = state.get('_deliveries', {}).get(source_id)
    if record is None:
        return None
    if (record['task_id'] != task.task_id or record['revision'] != task.confirmed_revision
            or record['generation'] != task.run_generation
            or record['current_version'] != task.task_version):
        return None
    if record['status'] == 'staged':
        return record if task.stage == 'FINALIZING' and task.run_status in (
            RunStatus.AWAITING_INPUT, RunStatus.RUNNING) else None
    return record if task.stage == 'FINALIZING' and task.run_status in (
        RunStatus.COMPLETED, RunStatus.PARTIAL) else None


def publication_is_current(task, state, source_id, source_version):
    return task.task_version == source_version or current_delivery(task, state, source_id) is not None


class ImageDeliveryService:
    def __init__(self, repository, gateway):
        self.repo, self.gateway = repository, gateway
        with closing(repository.tasks._connect()) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS image_deliveries '
                       '(package_id TEXT PRIMARY KEY, metadata TEXT NOT NULL, content BLOB NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS image_delivery_events '
                       '(event_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, payload TEXT NOT NULL)')

    @staticmethod
    def view(record):
        return {key: record[key] for key in (
            'package_id', 'task_id', 'revision', 'generation', 'status', 'current_version',
            'source_id', 'source_digest', 'source_version', 'manifest_digest', 'zip_digest', 'byte_count',
            'manifest', 'delivery_result_id')}

    def _read(self, memory, retrieval, candidates, state, actor, task_id):
        task = self.repo.manifests.retrieval._authorize(memory, actor, task_id, action='read')
        source_id = state['current_result_sets'].get(task_id)
        if not source_id:
            raise ServiceError('NOT_FOUND', 'No current qualified preview', 404)
        result = self.repo._read_result_set(memory, retrieval, candidates, state, actor, task_id, source_id)
        record = current_delivery(task, state, source_id)
        if record is not None and any(record['manifest'][key] != result[key] for key in (
                'accepted_count', 'reserve_count', 'target_count', 'operation_id')):
            raise ServiceError('STORAGE_CORRUPT', 'Delivery counts or operation differ from result', 500)
        return task, result, record

    def status(self, actor, task_id):
        def read(memory, retrieval, candidates, state):
            task = self.repo.manifests.retrieval._authorize(memory, actor, task_id, action='read')
            source_id = state['current_result_sets'].get(task_id)
            if not source_id or current_delivery(task, state, source_id) is None:
                return {'available': False}
            _, _, record = self._read(memory, retrieval, candidates, state, actor, task_id)
            return {'available': True, **self.view(record)}
        return self.repo._transaction(False, read)

    def _image(self, actor, task_id, operation_id, item):
        candidate, media_ref = self.repo.get_published_candidate(
            actor, task_id, operation_id, item.candidate_id)
        if (candidate.asset_version != item.asset_version
                or candidate.content_digest != item.content_digest):
            raise ServiceError('INPUT_SNAPSHOT_CHANGED', 'Delivery media binding changed', 409)
        context = AccessContext(actor, task_id, 'delivery_export', 'current')
        data = bytearray()
        try:
            with self.gateway.open_read(context, media_ref, max_bytes=MAX_IMAGE_BYTES) as stream:
                while True:
                    block = stream.read(min(65536, MAX_IMAGE_BYTES + 1 - len(data)))
                    if not isinstance(block, bytes):
                        raise ServiceError('MEDIA_READ_INVALID', 'Binary image required', 502)
                    if not block:
                        break
                    data.extend(block)
                    if len(data) > MAX_IMAGE_BYTES:
                        raise ServiceError('MEDIA_LIMIT_EXCEEDED', 'Delivery image exceeds limit', 413)
        except OSError as exc:
            raise ServiceError('MEDIA_UNAVAILABLE', 'Delivery image cannot be read', 503) from exc
        data = bytes(data)
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise ServiceError('MEDIA_LIMIT_EXCEEDED', 'Invalid delivery media size', 413)
        if digest(data) != item.content_digest:
            raise ServiceError('INPUT_SNAPSHOT_CHANGED', 'Delivery media digest changed', 409)
        extension = '.jpg' if data.startswith(b'\xff\xd8\xff') else '.png' if data.startswith(b'\x89PNG\r\n\x1a\n') else None
        if extension is None:
            raise ServiceError('FEATURE_NOT_SUPPORTED', 'Only JPEG/PNG delivery is supported', 422)
        metadata = self.gateway.export_metadata(media_ref) if callable(getattr(self.gateway, 'export_metadata', None)) else {}
        return data, extension, metadata

    def prepare(self, actor, task_id, *, expected_task_version, result_set_digest):
        require_version(expected_task_version)
        existing = self.status(actor, task_id)
        if existing['available']:
            if (existing['source_digest'] != result_set_digest
                    or existing['source_version'] != expected_task_version):
                raise ServiceError('REVISION_CONFLICT', 'Package belongs to another result', 409)
            return existing, True
        task = self.repo.tasks.get(actor, task_id)
        if (task.task_version != expected_task_version or task.stage != 'refill'
                or task.run_status not in (RunStatus.RUNNING, RunStatus.AWAITING_INPUT)):
            raise ServiceError('REVISION_CONFLICT', 'Task is not ready for packaging', 409)
        proposal = json.loads(task.confirmed_proposal_json)
        if proposal['node_modes']['packaging'] != 'manual':
            raise ServiceError('FEATURE_NOT_SUPPORTED', 'This slice requires manual package approval', 422)
        groups = (proposal['query_spec'].get('grouped_expansion') or {}).get('groups', [])
        if len(groups) > 1:
            raise ServiceError('FEATURE_NOT_SUPPORTED', 'Multi-group packaging is not yet supported', 422)
        result = self.repo.get_current_result_set(actor, task_id)
        if result['result_set_digest'] != result_set_digest or result['as_of_task_version'] != expected_task_version:
            raise ServiceError('REVISION_CONFLICT', 'Qualified result changed', 409)
        if (result.get('policy_ref') != 'exact-content@1'
                or result.get('near_duplicate_pairs')
                or result.get('near_duplicate_decisions')):
            raise ServiceError('FEATURE_NOT_SUPPORTED',
                               'Only SHA-256 exact-content delivery is enabled', 422)
        accepted_digests = [item.content_digest for item in result['accepted']]
        if len(accepted_digests) != len(set(accepted_digests)):
            raise ServiceError('STORAGE_CORRUPT',
                               'Delivery result contains duplicate content digests', 500)
        partial_reasons = {'EXECUTION_LIMIT_REACHED', 'VLM_CALL_LIMIT_REACHED',
                           'NO_PROGRESS', 'SEARCH_STRATEGIES_EXHAUSTED',
                           'INPUT_SCOPE_COMPLETE'}
        if (not result['accepted'] or len(result['accepted']) > 100
                or (result['gap'] and (task.run_status != RunStatus.AWAITING_INPUT
                                       or result.get('stop_reason') not in partial_reasons))):
            raise ServiceError('FEATURE_NOT_SUPPORTED',
                               'A full result or a legally stopped non-empty partial result is required', 422)
        completion = 'partial' if result['gap'] else 'full'
        near_duplicate_pairs = [{
            'candidate_id': pair.candidate_id,
            'other_candidate_id': pair.other_candidate_id,
            'hamming_distance': pair.hamming_distance,
            'review_threshold': pair.review_threshold,
            'policy_ref': pair.policy_ref,
        } for pair in result.get('near_duplicate_pairs', ())]
        near_duplicate_decisions = [{
            'cluster_id': decision.cluster_id,
            'candidate_ids': list(decision.candidate_ids),
            'action': decision.action,
            'representative_candidate_id': decision.representative_candidate_id,
        } for decision in result.get('near_duplicate_decisions', ())]
        manifest = {'schema': 'image-delivery@2', 'task_id': task_id,
                    'revision': task.confirmed_revision, 'generation': task.run_generation,
                    'source_result_id': result['result_set_id'], 'source_result_digest': result_set_digest,
                    'request_digest': task.request_digest, 'operation_id': result['operation_id'],
                    'rules': proposal['query_spec'].get('rules', []),
                    'model_binding': (proposal['query_spec'].get('grouped_expansion') or {}).get('model_binding'),
                    'selection_ref': result['selection_ref'], 'dedup_policy': result['policy_ref'],
                    'target_count': result['target_count'], 'accepted_count': result['accepted_count'],
                    'reserve_count': result['reserve_count'],
                    'eligible_unique_count': result['eligible_unique_count'],
                    'exact_duplicate_count': result['duplicate_count'],
                    'packaged_file_count': result['accepted_count'],
                    'completion': completion,
                    'gap': result['gap'], 'stop_reason': result.get('stop_reason'),
                    'near_duplicate_review_count': len(near_duplicate_pairs),
                    'near_duplicate_pairs': near_duplicate_pairs,
                    'near_duplicate_decisions': near_duplicate_decisions,
                    'members': []}
        output = BytesIO()
        with ZipFile(output, 'w', compression=ZIP_STORED) as archive:
            for index, item in enumerate(result['accepted'], 1):
                data, extension, metadata = self._image(actor, task_id, result['operation_id'], item)
                filename = f'images/{index:04d}' + extension
                if output.tell() + len(data) > MAX_PACKAGE_BYTES - 1024 * 1024:
                    raise ServiceError('PACKAGE_LIMIT_EXCEEDED', 'Local package limit is 64 MiB', 413)
                archive.writestr(ZipInfo(filename, date_time=(1980, 1, 1, 0, 0, 0)), data)
                manifest['members'].append({'file': filename, 'candidate_id': item.candidate_id,
                    'asset_version': item.asset_version, 'content_digest': item.content_digest,
                    'candidate_ids': list(item.candidate_ids or (item.candidate_id,)),
                    'retrieval_sources': [
                        {'candidate_id': source.candidate_id,
                         'binding_id': source.binding_id,
                         'library_id': source.library_id,
                         'index_version': source.index_version,
                         'vector_id': source.vector_id,
                         'score': source.score}
                        for source in item.sources],
                    'source': metadata})
            archive.writestr(ZipInfo('manifest.json', date_time=(1980, 1, 1, 0, 0, 0)), canonical(manifest))
        content = output.getvalue()
        if len(content) > MAX_PACKAGE_BYTES:
            raise ServiceError('PACKAGE_LIMIT_EXCEEDED', 'Local package limit is 64 MiB', 413)
        def stage(memory, retrieval, candidates, state, db):
            current, fresh, record = self._read(memory, retrieval, candidates, state, actor, task_id)
            self.repo.manifests.retrieval._authorize(memory, actor, task_id, action='approve')
            if record is not None:
                if record['source_digest'] != result_set_digest:
                    raise ServiceError('REVISION_CONFLICT', 'Package source changed', 409)
                return {'available': True, **self.view(record)}, True
            comparable = lambda value: {key: item for key, item in value.items()
                                        if key not in {'stop_reason', 'refill_dispatched'}}
            stop = (db.execute("""SELECT stop_reason FROM refill_execution_stops
                WHERE task_id=? AND revision=? AND generation=?""",
                (task_id, current.confirmed_revision, current.run_generation)).fetchone()
                if result['gap'] else None)
            if (current.task_version != expected_task_version or current.stage != 'refill'
                    or current.run_status not in (RunStatus.RUNNING, RunStatus.AWAITING_INPUT)
                    or comparable(fresh) != comparable(result)
                    or (result['gap'] and (stop is None or stop[0] != result.get('stop_reason')))):
                raise ServiceError('REVISION_CONFLICT', 'Task changed while packaging', 409)
            record = {'package_id': 'package_' + uuid.uuid4().hex, 'task_id': task_id,
                'revision': current.confirmed_revision, 'generation': current.run_generation,
                'source_id': result['result_set_id'], 'source_digest': result_set_digest,
                'source_version': expected_task_version, 'stage_version': expected_task_version + 1,
                'current_version': expected_task_version + 1, 'status': 'staged',
                'manifest': manifest, 'manifest_digest': digest(canonical(manifest)),
                'zip_digest': digest(content), 'byte_count': len(content),
                'delivery_result_id': 'delivery_' + uuid.uuid4().hex}
            current = memory._tasks[task_id]
            current.task_version += 1
            current.stage = 'FINALIZING'
            current.run_status = RunStatus.AWAITING_INPUT
            db.execute('INSERT INTO image_deliveries VALUES (?,?,?)',
                       (record['package_id'], canonical(record).decode(), content))
            self._event(db, record, 'staged')
            return {'available': True, **self.view(record)}, False
        return self.repo._transaction(True, stage, with_connection=True)

    @staticmethod
    def _event(db, record, kind):
        event = {'event_id': record['package_id'] + ':' + kind, 'kind': 'delivery.' + kind,
                 'package_id': record['package_id'], 'task_id': record['task_id'],
                 'revision': record['revision'], 'generation': record['generation'],
                 'manifest_digest': record['manifest_digest']}
        db.execute('INSERT INTO image_delivery_events VALUES (?,?,?)',
                   (event['event_id'], record['task_id'], canonical(event).decode()))

    def _content(self, actor, task_id, package_id, *, published):
        def read(memory, retrieval, candidates, state, db):
            _, result, record = self._read(memory, retrieval, candidates, state, actor, task_id)
            if record is None or record['package_id'] != package_id:
                raise ServiceError('REVISION_CONFLICT', 'Package is not current', 409)
            if published and record['status'] != 'published':
                raise ServiceError('PACKAGE_APPROVAL_REQUIRED', 'Package has not been published', 409)
            content = db.execute('SELECT content FROM image_deliveries WHERE package_id=?',
                                 (package_id,)).fetchone()[0]
            if len(content) != record['byte_count'] or digest(content) != record['zip_digest']:
                raise ServiceError('STORAGE_CORRUPT', 'Package content digest mismatch', 500)
            try:
                with ZipFile(BytesIO(content)) as archive:
                    members = record['manifest']['members']
                    expected = [item['file'] for item in members] + ['manifest.json']
                    if (archive.namelist() != expected or len(set(expected)) != len(expected)
                            or any(item['file'] not in (f'images/{index:04d}.jpg', f'images/{index:04d}.png')
                                   for index, item in enumerate(members, 1))
                            or any(item.compress_type != ZIP_STORED for item in archive.infolist())
                            or sum(item.file_size for item in archive.infolist()) > MAX_PACKAGE_BYTES
                            or archive.read('manifest.json') != canonical(record['manifest'])
                            or any(digest(archive.read(item['file'])) != item['content_digest'] for item in members)):
                        raise ValueError('manifest mismatch')
            except (BadZipFile, KeyError, ValueError, RuntimeError) as exc:
                raise ServiceError('STORAGE_CORRUPT', 'Package manifest or member mismatch', 500) from exc
            return record, result, content
        return self.repo._transaction(False, read, with_connection=True)

    def _revalidate_media(self, actor, task_id, result):
        for item in result['accepted']:
            self._image(actor, task_id, result['operation_id'], item)

    def publish(self, actor, task_id, *, package_id, manifest_digest, expected_task_version, action):
        require_version(expected_task_version)
        if action != 'confirm':
            raise ServiceError('CONFIRMATION_REQUIRED', 'Explicit package approval required', 422)
        record, result, _ = self._content(actor, task_id, package_id, published=False)
        if record['manifest_digest'] != manifest_digest or expected_task_version != record['stage_version']:
            raise ServiceError('REVISION_CONFLICT', 'Package approval binding changed', 409)
        self._revalidate_media(actor, task_id, result)
        def commit(memory, retrieval, candidates, state, db):
            task, fresh, current = self._read(memory, retrieval, candidates, state, actor, task_id)
            self.repo.manifests.retrieval._authorize(memory, actor, task_id, action='approve')
            if current is None or current['package_id'] != package_id or current['manifest_digest'] != manifest_digest:
                raise ServiceError('REVISION_CONFLICT', 'Package changed during approval', 409)
            if current['status'] == 'published':
                return {'available': True, **self.view(current)}, True
            if task.task_version != expected_task_version or fresh != result:
                raise ServiceError('REVISION_CONFLICT', 'Task changed during approval', 409)
            current = {**current, 'status': 'published', 'current_version': task.task_version + 1}
            db.execute('UPDATE image_deliveries SET metadata=? WHERE package_id=?',
                       (canonical(current).decode(), package_id))
            memory._tasks[task_id].task_version += 1
            memory._tasks[task_id].run_status = (
                RunStatus.PARTIAL if current['manifest'].get('completion') == 'partial'
                else RunStatus.COMPLETED)
            self._event(db, current, 'published')
            return {'available': True, **self.view(current)}, False
        return self.repo._transaction(True, commit, with_connection=True)

    def download(self, actor, task_id, package_id):
        record, result, content = self._content(actor, task_id, package_id, published=True)
        self._revalidate_media(actor, task_id, result)
        final, _, checked = self._content(actor, task_id, package_id, published=True)
        if final != record or checked != content:
            raise ServiceError('REVISION_CONFLICT', 'Package changed during download', 409)
        return content
