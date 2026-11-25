"""Workflow links over existing stage/plan/manifest records, not another task model."""
from dataclasses import asdict, replace
import json
import math

from ...errors import ServiceError, invalid
from ...agent_contracts import _hash
from ...processing_requirements import identifier
from ...candidate_contracts import merge_manifest_items
from ...agent_job_reviews import collection_review, retrieval_review_digest
from ...models import Actor
from ...query_spec import digest
from ...retrieval_contracts import RetrievalOperation, RetrievalScope, validate_scope
from ...service import require_key
from .retrieval import load_state as retrieval_state
from .candidates import load_state as candidate_state


def initial_links(epoch, batch_size, *, max_pages=1, target_count=None):
    result = dict(schema_version='agent-job-links@1', epoch=epoch, batch_size=batch_size,
        compile_intent=False, compile_started_at=None, plan_id=None, plan_digest=None, approved_digest=None,
        operation_id=None, manifest_proposal_digest=None, manifest_ref=None,
        error_code=None, cancelled=False, cancel_generation=None)
    if target_count is not None:
        result.update(schema_version='agent-job-links@2', max_pages=max_pages, target_count=target_count,
            page_proposal_digests=[], manifest_refs=[], stop_reason=None)
    return result



def ensure_job_links(journal):
    with journal._tx() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS agent_job_links(
            job_ref TEXT PRIMARY KEY, task_id TEXT NOT NULL, workflow_ref TEXT NOT NULL,
            stage_digest TEXT NOT NULL, payload TEXT NOT NULL, payload_digest TEXT NOT NULL)''')


class SQLiteAgentJobLinks:
    def __init__(self, *, stages, retrieval, manifests):
        journal = stages.journal
        if any(journal.path.resolve() != port.tasks.path.resolve() for port in (retrieval, manifests)):
            raise ValueError('job links, stage, plan and manifest must share one ledger')
        self.stages, self.journal, self.retrieval, self.manifests = stages, journal, retrieval, manifests
        self.storage_identity = journal.storage_identity
        ensure_job_links(journal)

    @staticmethod
    def _write(db, job_ref, links):
        db.execute('UPDATE agent_job_links SET payload=?,payload_digest=? WHERE job_ref=?',
            (json.dumps(links, sort_keys=True, allow_nan=False), digest(links), job_ref))

    def _read(self, db, actor, task_id, job_ref, *, confirming=False):
        cursor = db.execute('SELECT * FROM agent_job_links WHERE job_ref=? AND task_id=?', (job_ref, task_id))
        record = cursor.fetchone()
        if record is None: raise ServiceError('NOT_FOUND', 'Agent job not found', 404)
        record = dict(zip((c[0] for c in cursor.description), record))
        row, review = self.stages._row(db, actor, task_id, job_ref, cleanup=True)
        stage = self.stages.invocation(row, review).stage if row['run_id'] else None
        from ...harness.codec import stage_from
        stage = stage or stage_from(review['stage'])
        task = self.journal._task(db, actor, stage)
        try:
            links = json.loads(record['payload'])
            multi = type(links) is dict and links.get('schema_version') == 'agent-job-links@2'
            template = initial_links(1, 1, target_count=1) if multi else initial_links(1, 1)
            if (type(links) is not dict or set(links) != set(template)
                    or links['schema_version'] != template['schema_version']
                    or record['workflow_ref'] != ('retrieval-agent-job@2' if multi else 'retrieval-agent-job@1')
                    or digest(links) != record['payload_digest'] or stage.stage_digest != record['stage_digest']
                    or type(links['compile_intent']) is not bool or type(links['cancelled']) is not bool
                    or type(links['epoch']) is not int or links['epoch'] < 1
                    or type(links['batch_size']) is not int or not 1 <= links['batch_size'] <= 100):
                raise ValueError()
            if multi and (type(links['max_pages']) is not int or not 1 <= links['max_pages'] <= 10
                    or type(links['target_count']) is not int or not 1 <= links['target_count'] <= 1000
                    or type(links['page_proposal_digests']) is not list
                    or not len(links['page_proposal_digests']) <= links['max_pages']
                    or any(not _hash(p) for p in links['page_proposal_digests'])
                    or len(set(links['page_proposal_digests'])) != len(links['page_proposal_digests'])
                    or type(links['manifest_refs']) is not list
                    or any(not identifier(r) for r in links['manifest_refs'])
                    or len(set(links['manifest_refs'])) != len(links['manifest_refs'])
                    or links['manifest_refs'] and len(links['manifest_refs']) != len(links['page_proposal_digests'])
                    or bool(links['manifest_refs']) != bool(links['manifest_ref'])
                    or links['manifest_refs'] and (links['manifest_refs'][0] != links['manifest_ref'] or not links['stop_reason'])
                    or links['page_proposal_digests'] and not links['approved_digest']
                    or links['stop_reason'] not in (None,'candidate_target','page_limit','exhausted','depth_limit')):
                raise ValueError()
            if (any(links[k] is not None and not _hash(links[k]) for k in
                    ('plan_digest', 'approved_digest', 'manifest_proposal_digest'))
                    or any(links[k] is not None and not identifier(links[k]) for k in
                           ('plan_id', 'operation_id', 'manifest_ref', 'error_code'))):
                raise ValueError()
            started = links['compile_started_at']
            if (links['compile_intent'] != (started is not None)
                    or started is not None and (type(started) not in (int, float) or not math.isfinite(started) or started <= 0)
                    or links['cancelled'] != (links['cancel_generation'] is not None)
                    or links['cancelled'] and (type(links['cancel_generation']) is not int or links['cancel_generation'] < 1)
                    or (links['plan_id'] is None) != (links['plan_digest'] is None)
                    or links['approved_digest'] is not None and links['plan_id'] is None):
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            raise ServiceError('STORAGE_CORRUPT', 'Agent workflow links are corrupt', 500) from None
        state = retrieval_state(db.execute('SELECT format,payload FROM retrieval_ledger WHERE id=1').fetchone())
        proposal = state['proposals'].get((task_id, links['plan_id'])) if links['plan_id'] else None
        approved = state['approved'].get((task_id, links['plan_id'])) if links['approved_digest'] else None
        if links['plan_id'] and (proposal is None or proposal.proposal_digest != links['plan_digest']):
            raise ServiceError('STORAGE_CORRUPT', 'Linked retrieval proposal is unavailable', 500)
        if links['approved_digest'] and (approved is None or approved.proposal_digest != links['approved_digest']):
            raise ServiceError('STORAGE_CORRUPT', 'Linked approval changed', 500)
        if not links['cancelled']:
            scope = RetrievalScope.from_task(task)
            expected = approved.scope if approved is not None else stage.scope
            if confirming:
                if replace(scope, task_version=stage.scope.task_version) != stage.scope:
                    raise ServiceError('REVISION_CONFLICT', 'Agent job scope changed', 409)
            else:
                validate_scope(actor, task, expected)
            if approved is not None and state['current'].get(task_id) != approved:
                raise ServiceError('PLAN_NOT_CURRENT', 'Agent job plan was superseded', 409)
            if not confirming and approved is None and row['status'] in ('confirmed', 'started'):
                self.journal._guard(db, actor, stage)
            if proposal is not None:
                memory = self.journal.repository._load(db.execute('SELECT format,payload FROM local_ledger WHERE id=1').fetchone())
                self.retrieval._authorize(memory, actor, task_id, approved or proposal)
        attempt, handle = None, None
        if row['run_id']:
            inv = self.stages.invocation(row, review); handle = self.journal.handle(inv)
            attempt, _ = self.journal._attempt(db, actor, handle, cleanup=True)
        operation = None
        operations = []
        if approved is not None:
            request = approved.request
            for _ in range(links.get('max_pages', 1)):
                fingerprint = digest({'plan': approved.proposal_digest, 'request': asdict(request)})
                op_id = state['requests'].get(fingerprint)
                if not op_id: break
                value = state['operations'].get(op_id)
                if value is None or value[:2] != (approved, request):
                    raise ServiceError('STORAGE_CORRUPT', 'Job page source changed', 500)
                current_operation = RetrievalOperation(op_id, *value)
                operations.append(current_operation)
                if current_operation.page is None or current_operation.page.stop_reason != 'more_available': break
                request = replace(approved.request, cursor=current_operation.page.next_cursor)
            operation = operations[0] if operations else None
        if links['operation_id'] and (operation is None or links['operation_id'] != operation.operation_id):
            raise ServiceError('STORAGE_CORRUPT', 'Linked first page changed', 500)
        query_terms = []
        if proposal is not None and handle is not None:
            # Show the exact validated strategy behind this compiled plan, never
            # substitute the original request or uncommitted model prose.
            proof = db.execute('SELECT receipt_digest FROM agent_retrieval_handoffs '
                'WHERE task_id=? AND plan_id=? AND stage_digest=? AND run_id=? AND attempt_id=?',
                (task_id, proposal.request.plan_id, stage.stage_digest, handle.run_id, handle.attempt_id)).fetchone()
            outputs = [o for o in self.journal._outputs(db, handle, stage.stage_digest)
                       if o['tool_id'] == 'submit_retrieval_strategy']
            if proof is None or len(outputs) != 1 or digest(outputs[0]) != proof[0]:
                raise ServiceError('STORAGE_CORRUPT', 'Linked strategy receipt is unavailable', 500)
            query_terms = [outputs[0]['arguments']['proposal']['query']]
        return dict(job_ref=job_ref, workflow_ref=record['workflow_ref'], links=links, row=row,
            review=review, stage=stage, task=task, attempt=attempt, handle=handle,
            proposal=proposal, approved=approved, operation=operation, operations=tuple(operations), query_terms=query_terms)

    def get(self, actor, task_id, job_ref):
        with self.journal._tx() as db: return self._read(db, actor, task_id, job_ref)

    def prepared(self, actor, task_id, key, body):
        with self.journal._tx() as db:
            self.journal.task_in_transaction(db, actor, task_id)
            row = db.execute('''SELECT d.draft_id,d.fingerprint FROM harness_stage_drafts d
                JOIN agent_job_links l ON l.job_ref=d.draft_id WHERE d.tenant_id=? AND d.owner_id=?
                AND d.task_id=? AND d.prepare_key=?''', (actor.tenant_id, actor.user_id, task_id, key)).fetchone()
            if row is None: return None
            if row[1] != digest(body): raise ServiceError('IDEMPOTENCY_CONFLICT', 'Agent job preparation changed', 409)
            self._read(db, actor, task_id, row[0])
            return row[0]

    def replay_stage_action(self, actor, task_id, job_ref, operation, key, body):
        with self.journal._tx() as db:
            value = self._read(db, actor, task_id, job_ref)
            if value['links']['cancelled']:
                raise ServiceError('INVALID_STATE_TRANSITION', 'Agent job is cancelled', 409)
            if not value['links']['plan_id']: return None
            if (body['proposal_digest'] != value['row']['proposal_digest']
                    or body['expected_task_version'] != value['stage'].scope.task_version
                    or value['row']['run_id'] is None):
                raise ServiceError('PLAN_DIGEST_MISMATCH', 'Original planning review required', 409)
            self.stages._action(db, actor, task_id, operation, key, job_ref, body)
            return True

    def attach(self, actor, task_id, job_ref, workflow_ref, *, batch_size, max_pages=1, target_count=None):
        with self.journal._tx() as db:
            row, review = self.stages._row(db, actor, task_id, job_ref)
            old = db.execute('SELECT workflow_ref FROM agent_job_links WHERE job_ref=?', (job_ref,)).fetchone()
            if old:
                if old[0] != workflow_ref: raise ServiceError('IDEMPOTENCY_CONFLICT', 'Job workflow changed', 409)
                return
            state = retrieval_state(db.execute('SELECT format,payload FROM retrieval_ledger WHERE id=1').fetchone())
            links = initial_links(state['epochs'].get(task_id, 0) + 1, batch_size,
                max_pages=max_pages, target_count=target_count)
            from ...harness.codec import stage_from
            stage = stage_from(review['stage'])
            db.execute('INSERT INTO agent_job_links VALUES(?,?,?,?,?,?)', (job_ref, task_id, workflow_ref,
                stage.stage_digest, json.dumps(links, sort_keys=True), digest(links)))

    def begin_compile(self, actor, task_id, job_ref):
        with self.journal._tx() as db:
            value = self._read(db, actor, task_id, job_ref); links = value['links']
            if links['cancelled'] or links['compile_intent'] or links['plan_id']: return False
            if value['attempt'] is None or value['attempt']['status'] != 'finished': return False
            links.update(compile_intent=True, compile_started_at=self.journal.clock())
            self._write(db, job_ref, links)
            return True

    def link_compiled(self, actor, task_id, job_ref):
        """Discover a durable compiler publication, including crash before linking."""
        with self.journal._tx() as db:
            value = self._read(db, actor, task_id, job_ref); links = value['links']
            if links['cancelled']: return False
            if links['plan_id']: return True
            rows = db.execute('SELECT plan_id,plan_digest FROM agent_retrieval_handoffs WHERE task_id=? AND stage_digest=?',
                              (task_id, value['stage'].stage_digest)).fetchall()
            state = retrieval_state(db.execute('SELECT format,payload FROM retrieval_ledger WHERE id=1').fetchone())
            plans = [state['proposals'].get((task_id, row[0])) for row in rows]
            plans = [p for p in plans if p is not None and p.epoch == links['epoch'] and p.request.batch_size == links['batch_size']]
            if not plans: return False
            if len(plans) != 1: raise ServiceError('AGENT_JOB_CONFLICT', 'Multiple plans match the job compiler allocation', 409)
            plan = plans[0]
            links.update(plan_id=plan.request.plan_id, plan_digest=plan.proposal_digest, error_code=None)
            self._write(db, job_ref, links)
            return True

    def confirm_plan(self, actor, task_id, job_ref, *, key, body):
        value = self.get(actor, task_id, job_ref)
        plan = value['proposal']
        if plan is None: raise ServiceError('AGENT_JOB_NOT_READY', 'Retrieval review is not ready', 409)
        multi = value['links']['schema_version'] == 'agent-job-links@2'
        if multi and body['proposal_digest'] != retrieval_review_digest(value):
            raise ServiceError('PLAN_DIGEST_MISMATCH', 'Displayed page allocation changed', 409)
        def link(db, approved):
            current = self._read(db, actor, task_id, job_ref, confirming=True)
            links = current['links']
            if links['cancelled'] or approved.request.plan_id != links['plan_id']:
                raise ServiceError('AGENT_JOB_CONFLICT', 'Job changed before business confirmation', 409)
            if multi and retrieval_review_digest(current) != body['proposal_digest']:
                raise ServiceError('PLAN_DIGEST_MISMATCH', 'Page allocation changed', 409)
            links.update(approved_digest=approved.proposal_digest, error_code=None)
            self._write(db, job_ref, links)
        return self.retrieval.confirm(actor, task_id, plan.request.plan_id, key=key,
            proposal_digest=plan.proposal_digest if multi else body['proposal_digest'], expected_task_version=body['expected_task_version'],
            action=body['action'], on_confirm=link)

    def link_candidates(self, actor, task_id, job_ref, proposal):
        with self.journal._tx() as db:
            value = self._read(db, actor, task_id, job_ref); links = value['links']
            state = candidate_state(db.execute('SELECT format,payload FROM candidate_ledger WHERE id=1').fetchone())
            if (links['cancelled'] or value['operation'] is None
                    or proposal.operation_id != value['operation'].operation_id
                    or state['proposals'].get(proposal.proposal_digest) != proposal):
                raise ServiceError('AGENT_JOB_CONFLICT', 'Candidate proposal is outside the job', 409)
            if links['manifest_proposal_digest'] not in (None, proposal.proposal_digest):
                raise ServiceError('INPUT_SNAPSHOT_CHANGED', 'Job candidates are immutable', 409)
            links.update(operation_id=proposal.operation_id, manifest_proposal_digest=proposal.proposal_digest, error_code=None)
            self._write(db, job_ref, links)

    def candidate_proposal(self, actor, task_id, job_ref):
        with self.journal._tx() as db:
            value = self._read(db, actor, task_id, job_ref)
            proposal_digest = value['links']['manifest_proposal_digest']
            if proposal_digest is None: raise ServiceError('AGENT_JOB_NOT_READY', 'Candidate review is not ready', 409)
        return self.manifests.get_proposal(actor, task_id, proposal_digest)

    def candidate_proposals(self, actor, task_id, job_ref):
        value = self.get(actor, task_id, job_ref)
        if value['links']['schema_version'] == 'agent-job-links@1':
            return (self.candidate_proposal(actor, task_id, job_ref),) if value['links']['manifest_proposal_digest'] else ()
        return self._page_proposals(value, actor, task_id)

    def _page_proposals(self, value, actor, task_id):
        proposals = tuple(self.manifests.get_proposal(actor, task_id, p)
            for p in value['links']['page_proposal_digests'])
        if len(proposals) > len(value['operations']) or any(
                p.operation_id != op.operation_id for p,op in zip(proposals,value['operations'])):
            raise ServiceError('STORAGE_CORRUPT', 'Candidate page order changed', 500)
        merge_manifest_items(proposals)  # Never hide inconsistent identities across pages.
        return proposals

    def link_page(self, actor, task_id, job_ref, proposal):
        with self.journal._tx() as db:
            value = self._read(db, actor, task_id, job_ref); links = value['links']
            existing = links['page_proposal_digests']
            state = candidate_state(db.execute('SELECT format,payload FROM candidate_ledger WHERE id=1').fetchone())
            if proposal.proposal_digest in existing: return
            index = len(existing)
            if (links['cancelled'] or links['stop_reason'] or index >= len(value['operations'])
                    or value['operations'][index].operation_id != proposal.operation_id
                    or state['proposals'].get(proposal.proposal_digest) != proposal):
                raise ServiceError('AGENT_JOB_CONFLICT', 'Candidate page is outside the allocation', 409)
            proposals = tuple(state['proposals'][p] for p in existing) + (proposal,)
            count = len(merge_manifest_items(proposals))
            last = value['operations'][index]
            reason = None
            if last.page.stop_reason != 'more_available': reason = last.page.stop_reason
            elif count >= links['target_count']: reason = 'candidate_target'
            elif len(proposals) >= links['max_pages']: reason = 'page_limit'
            links.update(page_proposal_digests=existing+[proposal.proposal_digest],
                operation_id=value['operations'][0].operation_id, stop_reason=reason, error_code=None)
            self._write(db, job_ref, links)

    def confirm_pages(self, actor, task_id, job_ref, *, key, body):
        value = self.get(actor, task_id, job_ref)
        proposals = self._page_proposals(value, actor, task_id)
        if not value['links']['stop_reason'] or not proposals:
            raise ServiceError('AGENT_JOB_NOT_READY', 'Candidate pages are not ready', 409)
        if body['proposal_digest'] != collection_review(value, proposals)['proposal_digest']:
            raise ServiceError('PLAN_DIGEST_MISMATCH', 'Displayed candidate pages changed', 409)
        def link(db, manifests):
            current = self._read(db, actor, task_id, job_ref); links = current['links']
            if (links['cancelled'] or links['page_proposal_digests'] != [m.proposal.proposal_digest for m in manifests]
                    or collection_review(current, proposals)['proposal_digest'] != body['proposal_digest']):
                raise ServiceError('AGENT_JOB_CONFLICT', 'Candidate pages changed before publication', 409)
            links.update(manifest_refs=[m.manifest_ref for m in manifests],
                manifest_ref=manifests[0].manifest_ref, error_code=None)
            self._write(db, job_ref, links)
        return self.manifests.confirm_many(actor, proposals, key=key,
            expected_task_version=body['expected_task_version'], action=body['action'], on_confirm=link)

    def confirm_candidates(self, actor, task_id, job_ref, proposal, *, key, body):
        def link(db, manifest):
            current = self._read(db, actor, task_id, job_ref); links = current['links']
            if links['cancelled'] or links['manifest_proposal_digest'] != manifest.proposal.proposal_digest:
                raise ServiceError('AGENT_JOB_CONFLICT', 'Job changed before candidate confirmation', 409)
            links.update(manifest_ref=manifest.manifest_ref, error_code=None)
            self._write(db, job_ref, links)
        return self.manifests.confirm(actor, proposal, key=key, expected_task_version=body['expected_task_version'],
            action=body['action'], on_confirm=link)

    def record_error(self, actor, task_id, job_ref, code):
        with self.journal._tx() as db:
            value = self._read(db, actor, task_id, job_ref)
            if not value['links']['cancelled']:
                value['links']['error_code'] = code if identifier(code) else 'AGENT_JOB_FAILED'
                self._write(db, job_ref, value['links'])

    def cancel(self, actor, task_id, job_ref, key, body):
        require_key(key)
        if (type(body) is not dict or set(body) != {'action', 'expected_task_version'} or body['action'] != 'cancel'
                or type(body['expected_task_version']) is not int or body['expected_task_version'] < 1):
            raise invalid('explicit root-task cancellation and current version required')
        with self.journal._tx() as db:
            value = self._read(db, actor, task_id, job_ref); links = value['links']
            replayed = self.stages._action(db, actor, task_id, 'job_cancel', key, job_ref, body)
            if replayed: return True
            if links['cancelled'] and (value['task'].run_generation == links['cancel_generation']
                    and body['expected_task_version'] == value['task'].task_version - 1): return True
            if links['cancelled'] or links['manifest_ref']:
                raise ServiceError('INVALID_STATE_TRANSITION', 'Agent job cannot be cancelled', 409)
            if body['expected_task_version'] != value['task'].task_version:
                raise ServiceError('REVISION_CONFLICT', 'Task changed before cancellation', 409)
            repo = self.journal.repository
            memory = repo._load(db.execute('SELECT format,payload FROM local_ledger WHERE id=1').fetchone())
            from .task_controls import cancel_task
            result = cancel_task(db, repo, self.journal.projects, actor, memory, memory.get(actor, task_id))
            links.update(cancelled=True, cancel_generation=result['generation'])
            self._write(db, job_ref, links)
            return False

    def list(self, actor, task_id, limit=32):
        with self.journal._tx() as db:
            self.journal.task_in_transaction(db, actor, task_id)
            return tuple(r[0] for r in db.execute('SELECT job_ref FROM agent_job_links WHERE task_id=? ORDER BY rowid DESC LIMIT ?',
                                               (task_id, limit)))

    def first_in_scope(self, actor, task_id):
        """Read the first job in this confirmed scope; never create a replacement."""
        with self.journal._tx() as db:
            task = self.journal.task_in_transaction(db, actor, task_id)
            row = db.execute('''SELECT l.job_ref FROM agent_job_links l
                JOIN harness_stage_drafts d ON d.draft_id=l.job_ref WHERE l.task_id=?
                AND d.tenant_id=? AND d.owner_id=?
                AND json_extract(d.payload,'$.stage.scope.revision')=?
                AND json_extract(d.payload,'$.stage.scope.generation')=? ORDER BY l.rowid LIMIT 1''',
                (task_id, actor.tenant_id, actor.user_id, task.confirmed_revision, task.run_generation)).fetchone()
            if row: return row[0]
            # A root cancellation keeps its original job reference for status reads.
            row = db.execute('''SELECT l.job_ref FROM agent_job_links l
                JOIN harness_stage_drafts d ON d.draft_id=l.job_ref WHERE l.task_id=?
                AND d.tenant_id=? AND d.owner_id=? AND json_extract(l.payload,'$.cancel_generation')=?
                ORDER BY l.rowid LIMIT 1''', (task_id, actor.tenant_id, actor.user_id, task.run_generation)).fetchone()
            return row[0] if row else None

    def pending(self, limit=32):
        with self.journal._tx() as db:
            rows = db.execute('''SELECT l.task_id,l.job_ref,d.tenant_id,d.owner_id FROM agent_job_links l
                JOIN harness_stage_drafts d ON d.draft_id=l.job_ref WHERE d.status='started'
                AND json_extract(l.payload,'$.cancelled')=0 AND json_extract(l.payload,'$.manifest_ref') IS NULL
                AND json_extract(l.payload,'$.error_code') IS NULL
                AND json_extract(l.payload,'$.stop_reason') IS NULL
                AND (json_extract(l.payload,'$.compile_intent')=0 OR json_extract(l.payload,'$.plan_id') IS NOT NULL
                     OR EXISTS(SELECT 1 FROM agent_retrieval_handoffs h WHERE h.stage_digest=l.stage_digest))
                AND (json_extract(l.payload,'$.plan_id') IS NULL OR json_extract(l.payload,'$.approved_digest') IS NOT NULL)
                ORDER BY l.rowid LIMIT ?''', (limit,)).fetchall()
            return tuple((Actor(r[2], r[3]), r[0], r[1]) for r in rows)


def purge_for_tasks(db, task_ids):
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='agent_job_links'").fetchone():
        db.executemany('DELETE FROM agent_job_links WHERE task_id=?', [(t,) for t in task_ids])
