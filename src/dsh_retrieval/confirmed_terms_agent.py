"""Execution Agent for owner-confirmed term sets over the original search tools.

The planner has already produced the reviewed terms. This workflow owns the
durable execution handoff, recovery and original candidate artifact references;
it does not ask a second model to replace those terms with one query.
"""
import json

from .confirmed_terms_contract import WORKFLOW, execution_policy
from .errors import ServiceError, invalid
from .models import Actor
from .query_branches import compile_query_branches
from .query_spec import digest
from .service import require_key


def delegated(task):
    return 'retrieval_execution' in json.loads(task.confirmed_proposal_json)['query_spec']


class ConfirmedTermsLinks:
    """Versioned workflow payload in the existing job links, not a second task."""
    def __init__(self, journal):
        self.journal, self.storage_identity = journal, journal.storage_identity
        from .adapters.task_storage.agent_jobs import ensure_job_links
        ensure_job_links(journal)
        self._pending_after = 0

    @staticmethod
    def ref(task):
        return 'agentdraft_' + digest([WORKFLOW, task.task_id, task.confirmed_revision, task.run_generation])[7:39]

    def _read(self, db, actor, task_id, ref):
        task = self.journal.task_in_transaction(db, actor, task_id)
        row = db.execute('SELECT * FROM agent_job_links WHERE job_ref=? AND task_id=? AND workflow_ref=?',
                         (ref, task_id, WORKFLOW)).fetchone()
        if row is None: raise ServiceError('NOT_FOUND', 'Execution Agent job not found', 404)
        links = json.loads(row['payload'])
        if (links.get('schema_version') != 'confirmed-terms-job-links@1' or digest(links) != row['payload_digest']
                or row['stage_digest'] != links['request_digest']):
            raise ServiceError('STORAGE_CORRUPT', 'Execution Agent job payload changed', 500)
        return {'task': task, 'links': links, 'workflow_ref': WORKFLOW}

    def get(self, actor, task_id, ref):
        with self.journal._tx() as db: return self._read(db, actor, task_id, ref)

    @staticmethod
    def current(task, links):
        return ((task.confirmed_revision, task.run_generation) == (links['revision'], links['generation'])
                and digest(json.loads(task.confirmed_proposal_json)) == links['request_digest'])

    @staticmethod
    def _write(db, ref, links):
        db.execute('UPDATE agent_job_links SET payload=?,payload_digest=? WHERE job_ref=?',
                   (json.dumps(links, sort_keys=True), digest(links), ref))

    def ensure(self, actor, task_id, key, expected):
        with self.journal._tx() as db:
            task = self.journal.task_in_transaction(db, actor, task_id)
            ref = self.ref(task)
            if db.execute('SELECT 1 FROM agent_job_links WHERE job_ref=?', (ref,)).fetchone():
                value = self._read(db, actor, task_id, ref)
                if expected != (task.confirmed_revision, task.run_generation) or not self.current(task, value['links']):
                    raise ServiceError('REVISION_CONFLICT', 'Confirmed terms changed', 409)
                return ref, True
            if expected != (task.confirmed_revision, task.run_generation):
                raise ServiceError('REVISION_CONFLICT', 'Confirmed terms changed', 409)
            spec = json.loads(task.confirmed_proposal_json)['query_spec']
            if 'retrieval_execution' not in spec:
                raise ServiceError('AGENT_CONFIRMATION_REQUIRED', 'Task has no confirmed execution delegation', 409)
            from .retrieval_contracts import RetrievalScope
            from .search_budget import SearchBudget
            policy = spec['retrieval_execution']
            SearchBudget.bind_limits(db, RetrievalScope.from_task(task),
                max_calls=policy['max_calls'], max_hit_slots=policy['max_hit_slots'])
            links = dict(schema_version='confirmed-terms-job-links@1', revision=task.confirmed_revision,
                generation=task.run_generation, expected_version=task.task_version,
                request_digest=digest(json.loads(task.confirmed_proposal_json)), policy=spec['retrieval_execution'],
                dispatch_key=key, status='queued', dispatch_result=None, review=None, manifest_ref=None, code=None)
            db.execute('INSERT INTO agent_job_links VALUES(?,?,?,?,?,?)',
                (ref, task_id, WORKFLOW, links['request_digest'], json.dumps(links, sort_keys=True), digest(links)))
            return ref, False

    def update(self, actor, task_id, ref, **changes):
        with self.journal._tx() as db:
            value = self._read(db, actor, task_id, ref)
            if not self.current(value['task'], value['links']):
                raise ServiceError('REVISION_CONFLICT', 'Old execution cannot publish into a new request', 409)
            links = value['links']
            if links['status'] in ('cancelled', 'completed'): return
            links.update(changes); self._write(db, ref, links)

    def pending(self, limit=32):
        with self.journal._tx() as db:
            rows = db.execute("SELECT rowid,task_id,job_ref,payload FROM agent_job_links WHERE workflow_ref=? AND rowid>? "
                "AND json_extract(payload,'$.status') IN ('queued','running','waiting_confirmation') ORDER BY rowid LIMIT ?",
                (WORKFLOW, self._pending_after, limit)).fetchall()
            self._pending_after = rows[-1]['rowid'] if len(rows) == limit else 0
            result = []
            memory = self.journal.repository._load(db.execute('SELECT format,payload FROM local_ledger WHERE id=1').fetchone())
            for row in rows:
                # Resolve identity from the root ledger, never from a supplied model payload.
                task = memory._tasks.get(row['task_id'])
                links = json.loads(row['payload'])
                if task is not None and self.current(task, links):
                    result.append((Actor(task.tenant_id, task.owner_user_id), task.task_id, row['job_ref']))
            return tuple(result)

    def record_error(self, actor, task_id, ref, code):
        from .processing_requirements import identifier
        if not identifier(code): code = 'AGENT_JOB_FAILED'
        self.update(actor, task_id, ref, status='failed', code=code)

    def cancel(self, actor, task_id, ref, key, body):
        require_key(key)
        if (type(body) is not dict or set(body) != {'action', 'expected_task_version'}
                or body['action'] != 'cancel' or type(body['expected_task_version']) is not int or body['expected_task_version'] < 1):
            raise invalid('explicit current-version cancellation required')
        with self.journal._tx() as db:
            value = self._read(db, actor, task_id, ref); task, links = value['task'], value['links']
            if links.get('cancel_key') == key:
                if links.get('cancel_body') != body: raise ServiceError('IDEMPOTENCY_CONFLICT', 'Cancellation changed', 409)
                return True
            if not self.current(task, links) or task.task_version != body['expected_task_version']:
                raise ServiceError('REVISION_CONFLICT', 'Task changed before cancellation', 409)
            if links['status'] in ('completed', 'cancelled'): raise ServiceError('INVALID_STATE_TRANSITION', 'Job is terminal', 409)
            repo = self.journal.repository
            memory = repo._load(db.execute('SELECT format,payload FROM local_ledger WHERE id=1').fetchone())
            from .adapters.task_storage.task_controls import cancel_task
            cancel_task(db, repo, self.journal.projects, actor, memory, memory.get(actor, task_id))
            links.update(status='cancelled', cancel_key=key, cancel_body=body)
            self._write(db, ref, links)
            return False


class RoutedJobLinks:
    """Resolve each installed workflow before interpreting its versioned payload."""
    def __init__(self, original, confirmed):
        self.original, self.confirmed = original, confirmed
        self.workflows = {WORKFLOW: confirmed}
        self.storage_identity = original.storage_identity
        if confirmed.storage_identity != self.storage_identity: raise ValueError('one job ledger required')

    def __getattr__(self, name): return getattr(self.original, name)

    def register_workflow(self, ref, port):
        if ref in self.workflows or port.storage_identity != self.storage_identity:
            raise ValueError('unique installed workflow on the same ledger required')
        self.workflows[ref] = port

    def _port(self, actor, task_id, ref):
        with self.original.journal._tx() as db:
            self.original.journal.task_in_transaction(db, actor, task_id)
            row = db.execute('SELECT workflow_ref FROM agent_job_links WHERE job_ref=? AND task_id=?', (ref, task_id)).fetchone()
        return self.workflows.get(row[0], self.original) if row is not None else self.original

    def get(self, actor, task_id, ref): return self._port(actor, task_id, ref).get(actor, task_id, ref)
    def record_error(self, actor, task_id, ref, code): return self._port(actor, task_id, ref).record_error(actor, task_id, ref, code)
    def pending(self, limit=32):
        return tuple(item for port in tuple(self.workflows.values()) for item in port.pending(limit)) + tuple(self.original.pending(limit))


class ConfirmedTermsRetrievalAgent:
    contract_ref, capability = WORKFLOW, 'retrieval'

    def __init__(self, service, engine, links, sources_provider):
        self.service, self.engine, self.links, self.sources_provider = service, engine, links, sources_provider
        self.notify = lambda: None
        self.cleaning_service = self.cleaning_dispatch = None

    def configure_intake(self, cleaning_service, cleaning_dispatch):
        if not callable(getattr(cleaning_service, 'prepare', None)) or not callable(getattr(cleaning_service, 'confirm', None)) or not callable(getattr(cleaning_dispatch, 'notify', None)):
            raise ValueError('installed cleaning preparation and dispatch required')
        self.cleaning_service, self.cleaning_dispatch = cleaning_service, cleaning_dispatch

    def policy(self, actor, project, *, candidate_intake='default'):
        self.service.projects.require_access(actor, project, 'default', 'read')
        sources = self.sources_provider(actor, project)
        if not sources: raise ServiceError('AGENT_SOURCES_UNAVAILABLE', 'No authorized retrieval sources remain', 503)
        if candidate_intake == 'default':
            candidate_intake = 'auto' if self.cleaning_service is not None else None
        if candidate_intake is not None and self.cleaning_service is None:
            raise ServiceError('CLEANING_NOT_CONFIGURED', 'Automatic intake requires an installed cleaning service', 503)
        return execution_policy(sources, self.engine.search_limits, candidate_intake=candidate_intake)

    def require(self, actor, project, proposal, *, task=None):
        spec = proposal['query_spec']
        policy = spec.get('retrieval_execution') or {}
        intake = policy.get('candidate_intake') if policy.get('schema_version') == 'confirmed-terms-execution@2' else None
        if (policy != self.policy(actor, project, candidate_intake=intake) or spec['media_type'] != 'image'
                or not spec.get('grouped_expansion') or 'agent_requirements' in spec):
            raise ServiceError('AGENT_EXECUTION_POLICY_CHANGED', 'Review the current terms and execution scope again', 409)
        if intake is not None and (proposal['node_modes']['cleaning'] != 'auto' or not spec.get('rules')):
            raise ServiceError('AGENT_CONFIRMATION_REQUIRED', 'Confirm the cleaning rules and automatic intake first', 409)

    def prepare(self, actor, task_id, key, body):
        require_key(key)
        fields = {'expected_revision', 'expected_generation', 'expected_task_version'}
        if (type(body) is not dict or set(body) != fields
                or any(type(v) is not int or v < 1 for v in body.values())):
            raise invalid('confirmed terms task version required')
        task = self.service.repository.get(actor, task_id)
        self.require(actor, task.project_id, json.loads(task.confirmed_proposal_json))
        if body['expected_task_version'] != task.task_version:
            ref = self.links.ref(task)
            old = self.links.get(actor, task_id, ref)
            if body['expected_task_version'] != old['links']['expected_version']:
                raise ServiceError('REVISION_CONFLICT', 'Task version changed before execution', 409)
        ref, replayed = self.links.ensure(actor, task_id, key, (body['expected_revision'], body['expected_generation']))
        self.notify()
        return self.get(actor, task_id, ref), replayed

    def enqueue(self, actor, task_id, *, key):
        task = self.service.repository.get(actor, task_id)
        view, replayed = self.prepare(actor, task_id, key, dict(expected_revision=task.confirmed_revision,
            expected_generation=task.run_generation, expected_task_version=task.task_version))
        return {'status': 'queued', 'backend': 'confirmed-terms-agent', 'job_ref': view['job_ref'],
                'task_id': task_id, 'workflow_ref': WORKFLOW, 'replayed': replayed}

    def current_job(self, actor, task_id):
        task = self.service.repository.get(actor, task_id)
        if not delegated(task): return None
        ref = self.links.ref(task)
        try: return self.get(actor, task_id, ref)
        except ServiceError as error:
            if error.code != 'NOT_FOUND': raise
            with self.links.journal._tx() as db:
                self.links.journal.task_in_transaction(db, actor, task_id)
                event = db.execute('SELECT 1 FROM search_dispatch_outbox WHERE task_id=? AND revision=? AND generation=?',
                    (task_id, task.confirmed_revision, task.run_generation)).fetchone()
            if event is None: return None  # A cleaning-only revision does not start retrieval again.
            return {'status': 'queued', 'job_ref': None, 'workflow_ref': WORKFLOW, 'execution_mode': 'confirmed_terms',
                    'revision':task.confirmed_revision,'generation':task.run_generation,'task_version':task.task_version}

    def get(self, actor, task_id, ref):
        value = self.links.get(actor, task_id, ref); task, links = value['task'], value['links']
        current = self.links.current(task, links)
        status = links['status'] if current or links['status'] == 'cancelled' else 'cancelled'
        published = self._published(actor, task_id, links) if current and status!='cancelled' else None
        if published is not None and 'candidate_intake' not in links['policy']: status = 'completed'
        spec = json.loads(task.confirmed_proposal_json)['query_spec']
        terms = compile_query_branches(spec)['branches'] if current else []
        view = dict(schema_version='agent-job@1', job_ref=ref, task_id=task_id, capability='retrieval',
            workflow_ref=WORKFLOW, status=status, phase='cancelled' if status=='cancelled' else 'candidates' if status in ('completed','waiting_confirmation') else 'retrieval',
            revision=links['revision'], generation=links['generation'], task_version=task.task_version,
            confirmation=links['review'] if status == 'waiting_confirmation' else None,
            artifacts_ready=bool(current and published), accepted_count=None,
            query_terms=[b['query_text'] for b in terms], execution_scope=links['policy'],
            recall_analysis=({'status': 'recorded',
                'evidence_digest': links['recall_analysis']['evidence_digest'],
                'scope': links['recall_analysis']['scope']}
                if current and links.get('recall_analysis') else
                {'status': 'unavailable', 'code': links['recall_analysis_error']}
                if current and links.get('recall_analysis_error') else None),
            code=None if published is not None and status=='completed' else links['code'] if current or links['status']=='cancelled' else 'REVISION_CONFLICT',
            publication_fenced=not current or status=='cancelled',
            candidate_intake=links['policy'].get('candidate_intake', 'manual'),
            cleaning_operation_id=(links.get('intake') or {}).get('operation_id'),
            execution_mode='confirmed_terms', implementation_ref='confirmed-terms-executor@1')
        owner = getattr(self, 'owner', None)
        if owner is not None:
            view.update(owner.execution_context(actor, task_id, view['phase'],
                plan_digest=spec['grouped_expansion']['plan_digest']))
        return view

    def _published(self, actor, task_id, links):
        published = self.engine.repository.get_current_candidate_manifest(actor, task_id)
        if published is not None and (published.proposal.scope.revision, published.proposal.scope.generation) == (links['revision'], links['generation']):
            return published
        return None

    def advance(self, actor, task_id, ref):
        value = self.links.get(actor, task_id, ref); task, links = value['task'], value['links']
        if not self.links.current(task, links) or links['status'] not in ('queued','running','waiting_confirmation'): return
        published = self._published(actor, task_id, links)
        if published is not None:
            if 'candidate_intake' in links['policy']:
                self._clean_intake(actor, task_id, ref, published.manifest_ref)
            else:
                self.links.update(actor, task_id, ref, status='completed', manifest_ref=published.manifest_ref, review=None, code=None)
            return
        self.require(actor, task.project_id, json.loads(task.confirmed_proposal_json))
        if links['dispatch_result'] is None:
            try:
                result = self.engine.auto_start(actor, task_id, key='terms-agent:'+ref,
                    allowed_bindings=frozenset(links['policy']['binding_ids']))
            except ServiceError as error:
                if error.code in ('SEARCH_QUEUE_FULL','SEARCH_USER_QUEUE_FULL','SEARCH_EXECUTOR_STOPPED'):
                    return  # Original approved plan persists; retry only local queue delivery.
                raise
            self.links.update(actor, task_id, ref, status='running' if result['status']=='queued' else 'failed',
                dispatch_result=result, code=None if result['status']=='queued' else 'AGENT_SOURCE_UNAVAILABLE')
            return
        if links['status'] == 'waiting_confirmation': return
        progress = self.engine.progress(actor, task_id)
        queue = progress.get('execution_queue') or {}
        if queue.get('state') == 'failed':
            code = queue.get('error_code') or 'AGENT_SEARCH_FAILED'
            self.links.update(actor, task_id, ref, status='unknown' if code=='AGENT_SEARCH_OUTCOME_UNKNOWN' else 'failed', code=code); return
        if not progress.get('initial_window_complete', progress.get('pagination_stopped')): return
        manifest = self.engine.preview_manifest(actor, task_id)
        reader = getattr(self.engine, 'recall', None)
        if callable(reader):
            try:
                analysis = reader(actor, task_id)
                self.links.update(actor, task_id, ref, recall_analysis=analysis, recall_analysis_error=None)
            except ServiceError as error:
                # Analytics availability does not invalidate original committed
                # candidates. A fresh authorized read is still required to refine.
                self.links.update(actor, task_id, ref, recall_analysis_error=error.code)
        if not manifest['candidate_count']:
            self.links.update(actor, task_id, ref, status='completed', code='NO_CANDIDATES'); return
        if manifest.get('confirmation_supported') is False:
            raise ServiceError('AGENT_ARTIFACT_UNAVAILABLE', 'Original manifest confirmation required', 409)
        if links['policy'].get('candidate_intake') == 'auto':
            # The owner delegated this exact transition in the term confirmation.
            fixed, _ = self.engine.confirm_manifest(actor, task_id,
                proposal_digest=manifest['proposal_digest'], expected_task_version=manifest['expected_task_version'],
                action='confirm', key='terms-auto-manifest:' + ref)
            self._clean_intake(actor, task_id, ref, fixed['manifest_ref'])
            return
        self.links.update(actor, task_id, ref, status='waiting_confirmation', review={
            'checkpoint':'candidates', 'proposal_digest':manifest['proposal_digest'],
            'expected_task_version':manifest['expected_task_version'], 'candidate_count':manifest['candidate_count']})

    def _clean_intake(self, actor, task_id, ref, manifest_ref):
        value = self.links.get(actor, task_id, ref)
        task, links = value['task'], value['links']
        if not self.links.current(task, links):
            raise ServiceError('REVISION_CONFLICT', 'Automatic intake is no longer current', 409)
        self.require(actor, task.project_id, json.loads(task.confirmed_proposal_json))
        self.service.projects.require_access(actor, task.project_id, task.dimension_id, 'approve')
        intake = links.get('intake') or {}
        if not intake.get('proposal_digest'):
            proposal = self.cleaning_service.prepare(actor, task_id, manifest_ref)
            calls = proposal.get('estimated_vlm_calls') or 0
            from .refill_planning import normalize_refill_limits
            limit = normalize_refill_limits(json.loads(task.confirmed_proposal_json)['query_spec'].get('refill_limits'))['max_vlm_calls']
            if limit is not None and calls > limit:
                raise ServiceError('VLM_CALL_LIMIT_REACHED', 'Initial cleaning exceeds the confirmed detection budget', 409)
            intake = dict(manifest_ref=manifest_ref, proposal_digest=proposal['proposal_digest'],
                expected_task_version=proposal['expected_task_version'], vlm_calls=calls)
            self.links.update(actor, task_id, ref, status='running', review=None,
                              manifest_ref=manifest_ref, intake=intake)
        if intake.get('manifest_ref') != manifest_ref:
            raise ServiceError('INPUT_SNAPSHOT_CHANGED', 'Automatic cleaning input changed', 409)
        fresh = self.service.repository.get(actor, task_id)
        self.require(actor, fresh.project_id, json.loads(fresh.confirmed_proposal_json))
        approval, _ = self.cleaning_service.confirm(actor, task_id, intake['proposal_digest'],
            expected_task_version=intake['expected_task_version'], action='confirm', key='terms-auto-cleaning:' + ref)
        self.links.update(actor, task_id, ref, status='completed', review=None, code=None,
            manifest_ref=manifest_ref, intake={**intake, 'operation_id': approval['operation_id']})
        self.cleaning_dispatch.notify()

    def act(self, actor, task_id, ref, operation, key, body):
        if operation == 'cancel': replayed = self.links.cancel(actor, task_id, ref, key, body)
        elif operation == 'confirm':
            if type(body) is not dict or set(body) != {'checkpoint','action','proposal_digest','expected_task_version'} or body['checkpoint'] != 'candidates':
                raise invalid('candidate confirmation required; terms were already confirmed')
            value = self.links.get(actor, task_id, ref)
            if not self.links.current(value['task'], value['links']): raise ServiceError('REVISION_CONFLICT','Execution scope changed',409)
            manifest, replayed = self.engine.confirm_manifest(actor, task_id, key=key,
                **{k:v for k,v in body.items() if k!='checkpoint'})
            if 'candidate_intake' in value['links']['policy']:
                self._clean_intake(actor, task_id, ref, manifest['manifest_ref'])
            else:
                self.links.update(actor, task_id, ref, status='completed', manifest_ref=manifest['manifest_ref'], review=None, code=None)
        else: raise ServiceError('INVALID_STATE_TRANSITION', 'Execution is queued by the original terms confirmation', 409)
        return self.get(actor, task_id, ref), replayed

    def artifacts(self, actor, task_id, ref):
        value = self.links.get(actor, task_id, ref); links = value['links']
        if not self.links.current(value['task'], links) or links['status']=='cancelled':
            raise ServiceError('REVISION_CONFLICT', 'Original execution output is no longer current', 409)
        items = []
        published = self._published(actor, task_id, links)
        if published is not None:
            manifest = self.engine.get_manifest(actor, task_id, published.manifest_ref)
            items.append(dict(manifest, kind='candidate_input_manifest', ref=published.manifest_ref,
                verification='media_verified_rules_unchecked'))
        return dict(schema_version='agent-job-artifacts@1', job_ref=ref, task_id=task_id, items=items)
