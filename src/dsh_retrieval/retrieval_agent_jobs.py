"""Versioned bounded image/text retrieval workflows over original business records."""
import json

from .agent_contracts import _hash
from .candidate_contracts import CandidateManifestProposal, merge_manifest_items
from .agent_job_reviews import collection_review, retrieval_review_digest
from .errors import ServiceError, invalid
from .query_spec import digest


class RetrievalAgentJobs:
    capability = 'retrieval'
    contract_ref = 'retrieval-agent-job@1'

    def __init__(self, *, planning, compiler, executor, materializer, links, batch_size=25, max_pages=1):
        if (type(batch_size) is not int or not 1 <= batch_size <= 100
                or type(max_pages) is not int or not 1 <= max_pages <= 10):
            raise ValueError('bounded retrieval job page size required')
        if planning.store.storage_identity != links.storage_identity:
            raise ValueError('workflow and planning must share the stage ledger')
        self.planning, self.compiler, self.executor = planning, compiler, executor
        self.materializer, self.links, self.batch_size = materializer, links, batch_size
        self.max_pages = max_pages
        if max_pages > 1: self.contract_ref = 'retrieval-agent-job@2'

    @staticmethod
    def _key(key, checkpoint):
        return 'job_' + digest({'key': key, 'checkpoint': checkpoint})[7:]

    def prepare(self, actor, task_id, key, body):
        if type(body) is not dict or 'role' in body: raise invalid('planning role is selected by installed capability')
        # No formal task is made here. The existing owner-confirmed task is required.
        previous = self.links.prepared(actor, task_id, self._key(key, 'prepare'), dict(body, role='retrieval'))
        if previous is not None: return self.get(actor, task_id, previous), True
        task = self.planning.repository.get(actor, task_id)
        if self.max_pages > 1 and task.target_count > 1000:
            raise ServiceError('INPUT_LIMIT', 'Multi-page candidate target must not exceed 1000', 422)
        view, replayed = self.planning.prepare(actor, task_id, self._key(key, 'prepare'), dict(body, role='retrieval'))
        task = self.planning.repository.get(actor, task_id)
        req = json.loads(task.confirmed_proposal_json)['query_spec']['agent_requirements']
        pages = self.max_pages if 'paginate' in req['autonomy']['actions'] else 1
        caps = [c.max_batch_size for b,c in self.compiler.capabilities.items()
                if b.library_id in req['autonomy']['library_ids']]
        batch = min(self.batch_size, task.target_count, min(caps) if caps else self.batch_size)
        self.links.attach(actor, task_id, view['stage_draft_id'], self.contract_ref,
            batch_size=batch, max_pages=pages,
            target_count=task.target_count if self.contract_ref == 'retrieval-agent-job@2' else None)
        return self.get(actor, task_id, view['stage_draft_id']), replayed

    def get(self, actor, task_id, job_ref):
        value = self.links.get(actor, task_id, job_ref); links = value['links']
        status, phase, confirmation = 'running', 'planning', None
        row, review = value['row'], value['review']
        multi = links['schema_version'] == 'agent-job-links@2'
        if links['cancelled'] or row['status'] == 'cancelled' and not links['approved_digest']:
            status, phase = 'cancelled', 'cancelled'
        elif links['manifest_ref']:
            # Published output must remain readable under current media permissions.
            for ref in links.get('manifest_refs', [links['manifest_ref']]):
                self.links.manifests.get(actor, task_id, ref)
            status, phase = 'completed', 'candidates'
        elif multi and links['stop_reason']:
            proposals = self.links.candidate_proposals(actor, task_id, job_ref)
            confirmation = collection_review(value, proposals)
            status, phase = 'waiting_confirmation', 'candidates'
        elif links['manifest_proposal_digest']:
            proposal = self.links.candidate_proposal(actor, task_id, job_ref)
            status, phase = 'waiting_confirmation', 'candidates'
            confirmation = dict(checkpoint='candidates', **proposal.view())
        elif any(op.page is None for op in value['operations']):
            status, phase = 'unknown', 'retrieval'
        elif links['error_code']:
            status, phase = ('unknown' if links['error_code'] == 'AGENT_PLANNING_UNKNOWN'
                or links['compile_intent'] and not links['plan_id'] else 'failed'), 'execution'
        elif links['approved_digest']:
            status, phase = 'queued', 'retrieval'
        elif value['proposal'] is not None:
            plan = value['proposal']
            status, phase = 'waiting_confirmation', 'retrieval'
            confirmation = {'checkpoint': 'retrieval', 'proposal_digest': plan.proposal_digest,
                'expected_task_version': plan.scope.task_version, 'plan_id': plan.request.plan_id,
                'library_id': plan.request.binding.library_id, 'batch_size': plan.request.batch_size,
                'max_pages': links.get('max_pages', 1), 'output_kind': 'unverified_candidates'}
            if multi:
                confirmation.update(proposal_digest=retrieval_review_digest(value), target_count=links['target_count'],
                    max_raw_hits=links['batch_size']*links['max_pages'])
        elif row['status'] == 'prepared':
            status = 'waiting_confirmation'
            confirmation = dict(checkpoint='planning', proposal_digest=row['proposal_digest'],
                expected_task_version=review['stage']['scope']['task_version'],
                role='retrieval', runtime_release_ref=review['runtime']['release_ref'],
                model_profile_ref=review['model_profile_ref'], model_calls=review['model_calls'], max_seconds=review['max_seconds'])
        elif row['status'] == 'confirmed': status = 'ready'
        elif value['attempt'] is not None:
            state = value['attempt']['status']
            if state in ('failed', 'unknown', 'cancelled'): status = state
            elif state == 'queued': status = 'queued'
            elif links['compile_intent']:
                status = 'unknown' if self.planning.journal.clock() - links['compile_started_at'] > 30 else 'running'
                phase = 'compilation'
        result = {'schema_version': 'agent-job@1', 'job_ref': job_ref, 'task_id': task_id,
            'capability': self.capability, 'workflow_ref': value['workflow_ref'], 'status': status, 'phase': phase,
            'revision': value['stage'].scope.revision, 'generation': value['stage'].scope.generation,
            'task_version': value['task'].task_version, 'confirmation': confirmation,
            'planning_review': {'proposal_digest': row['proposal_digest'],
                'expected_task_version': value['stage'].scope.task_version},
            'accepted_count': None, 'artifacts_ready': bool(links['manifest_ref']),
            'execution_scope': {'max_pages': links.get('max_pages', 1), 'batch_size': links['batch_size']}}
        result['query_terms'] = value['query_terms']
        if value['attempt'] is not None:
            result['planning_progress'] = {
                'elapsed_seconds': max(0, int(min(self.planning.journal.clock(), row['deadline']) - value['attempt']['queued_at'])),
                'max_seconds': review['max_seconds'],
                'model_calls_admitted': value['attempt']['admitted'],
                'deadline_reached': self.planning.journal.clock() >= row['deadline']}
        if multi and not links['cancelled']:
            proposals = self.links.candidate_proposals(actor, task_id, job_ref)
            result['retrieval_progress'] = {**collection_review(value, proposals)}
            result['retrieval_progress'].pop('checkpoint'); result['retrieval_progress'].pop('proposal_digest')
        if links['error_code']: result['code'] = links['error_code']
        if links['cancelled']: result.update(publication_fenced=True, termination='unknown')
        return result

    @staticmethod
    def _action_body(body, operation, *, checkpoint=False):
        expected = {'action', 'proposal_digest', 'expected_task_version'} | ({'checkpoint'} if checkpoint else set())
        if (type(body) is not dict or set(body) != expected or body['action'] != operation
                or not _hash(body['proposal_digest']) or type(body['expected_task_version']) is not int
                or body['expected_task_version'] < 1):
            raise invalid('explicit action, displayed digest and task version required')
        return {key: value for key, value in body.items() if key != 'checkpoint'}

    def act(self, actor, task_id, job_ref, operation, key, body):
        if operation == 'cancel':
            replayed = self.links.cancel(actor, task_id, job_ref, self._key(key, 'cancel'), body)
            snapshot = self.links.get(actor, task_id, job_ref)
            if snapshot['handle'] is not None:
                self.planning.journal.cancel(actor, snapshot['handle'])
                with self.planning._lock:
                    runner = self.planning._runners.get((actor.tenant_id, actor.user_id))
                if runner is not None: runner.cancel(snapshot['handle'])
        elif operation == 'start':
            action = self._action_body(body, 'start')
            replayed = self.links.replay_stage_action(actor, task_id, job_ref, 'start', self._key(key, 'start'), action)
            if replayed is None:
                _, replayed = self.planning.act(actor, task_id, job_ref, 'start', self._key(key, 'start'), action)
        else:
            action = self._action_body(body, 'confirm', checkpoint=True)
            checkpoint = body['checkpoint']; action_key = self._key(key, checkpoint)
            if checkpoint == 'planning':
                replayed = self.links.replay_stage_action(actor, task_id, job_ref, 'confirm', action_key, action)
                if replayed is None:
                    _, replayed = self.planning.act(actor, task_id, job_ref, 'confirm', action_key, action)
            elif checkpoint == 'retrieval':
                _, replayed = self.links.confirm_plan(actor, task_id, job_ref, key=action_key, body=action)
            elif checkpoint == 'candidates':
                if self.contract_ref == 'retrieval-agent-job@2':
                    snapshot = self.links.get(actor, task_id, job_ref)
                    proposals = self.links.candidate_proposals(actor, task_id, job_ref)
                    if not snapshot['links']['manifest_ref']:
                        for proposal in proposals:
                            self._recheck_candidate_page(actor, task_id, proposal)
                    _, replayed = self.links.confirm_pages(actor, task_id, job_ref, key=action_key, body=action)
                    return self.get(actor, task_id, job_ref), replayed
                proposal = self.links.candidate_proposal(actor, task_id, job_ref)
                if proposal.proposal_digest != action['proposal_digest']:
                    raise ServiceError('PLAN_DIGEST_MISMATCH', 'Displayed candidates changed', 409)
                snapshot = self.links.get(actor, task_id, job_ref)
                if not snapshot['links']['manifest_ref']:
                    self._recheck_candidate_page(actor, task_id, proposal)
                _, replayed = self.links.confirm_candidates(actor, task_id, job_ref, proposal, key=action_key, body=action)
            else: raise invalid('unknown Agent job confirmation checkpoint')
        return self.get(actor, task_id, job_ref), replayed

    def _recheck_candidate_page(self, actor, task_id, proposal):
        with self.materializer.materialize(actor, task_id, proposal.operation_id) as page:
            if CandidateManifestProposal.from_materialized(page) != proposal:
                raise ServiceError('INPUT_SNAPSHOT_CHANGED', 'Candidate content changed before publication', 409)

    def advance(self, actor, task_id, job_ref):
        value = self.links.get(actor, task_id, job_ref); links = value['links']
        if links['cancelled'] or links['manifest_ref'] or links['error_code']: return
        if value['proposal'] is None:
            if value['attempt'] is None: return
            state = value['attempt']['status']
            if state == 'queued':
                self.planning._runner(actor).dispatch(value['handle'], lease_seconds=self.planning.lease_seconds)
                return
            if state == 'running':
                self.planning.journal.recover(actor, value['handle'])
                return
            if state != 'finished':
                self.links.record_error(actor, task_id, job_ref, 'AGENT_PLANNING_' + state.upper())
                return
            if not self.links.link_compiled(actor, task_id, job_ref):
                if not self.links.begin_compile(actor, task_id, job_ref): return
                self.compiler.prepare(actor, value['handle'], epoch=links['epoch'], batch_size=links['batch_size'])
                self.links.link_compiled(actor, task_id, job_ref)
            return  # explicit owner confirmation of the compiled physical plan
        if value['approved'] is None or links['manifest_proposal_digest']: return
        if links['schema_version'] == 'agent-job-links@2':
            return self._advance_pages(actor, task_id, job_ref, value)
        operation = value['operation']
        if operation is None:
            result = self.executor.execute_page(actor, task_id, plan_id=value['approved'].request.plan_id,
                proposal_digest=value['approved'].proposal_digest)
            operation = result['operation']
        if operation.page is None: return
        with self.materializer.materialize(actor, task_id, operation.operation_id) as page:
            proposal = CandidateManifestProposal.from_materialized(page)
            self.links.manifests.put_proposal(actor, proposal)
            self.links.link_candidates(actor, task_id, job_ref, proposal)

    def _advance_pages(self, actor, task_id, job_ref, value):
        links = value['links']
        if links['stop_reason']: return
        index = len(links['page_proposal_digests'])
        operations = value['operations']
        operation = operations[index] if index < len(operations) else None
        if operation is None:
            cursor = operations[-1].page.next_cursor if operations else None
            operation = self.executor.execute_page(actor, task_id, plan_id=value['approved'].request.plan_id,
                proposal_digest=value['approved'].proposal_digest, cursor=cursor)['operation']
        if operation.page is None: return
        with self.materializer.materialize(actor, task_id, operation.operation_id) as page:
            proposal = CandidateManifestProposal.from_materialized(page)
            self.links.manifests.put_proposal(actor, proposal)
            self.links.link_page(actor, task_id, job_ref, proposal)

    def artifacts(self, actor, task_id, job_ref):
        value = self.links.get(actor, task_id, job_ref)
        if value['links']['cancelled']:
            raise ServiceError('INVALID_STATE_TRANSITION', 'Cancelled job cannot deliver artifacts', 409)
        ref = value['links']['manifest_ref']
        items = []
        refs = value['links'].get('manifest_refs', [ref] if ref else [])
        for ref in refs:
            manifest = self.links.manifests.get(actor, task_id, ref)
            items.append({'kind': 'candidate_input_manifest', 'ref': ref, 'digest': manifest.proposal.proposal_digest,
                'revision': manifest.proposal.scope.revision, 'generation': manifest.proposal.scope.generation,
                'verification': 'media_verified_rules_unchecked', **manifest.view()})
        result = {'schema_version': 'agent-job-artifacts@1', 'job_ref': job_ref, 'task_id': task_id, 'items': items}
        if value['links']['schema_version'] == 'agent-job-links@2':
            proposals = tuple(self.links.manifests.get(actor, task_id, r).proposal for r in refs)
            result.update(candidate_count=len(merge_manifest_items(proposals)), accepted_count=None,
                page_count=len(refs), stop_reason=value['links']['stop_reason'])
        return result
