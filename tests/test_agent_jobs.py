from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from dataclasses import replace
from hashlib import sha256
from io import BytesIO
import json
import time
import unittest

import test_agent_framework as fixtures
from image_fixtures import image_bytes
from dsh_retrieval.adapters.harness.composition import build_retrieval_agent_jobs
from dsh_retrieval.adapters.retrieval.materialization import LocalCandidateMaterializer
from dsh_retrieval.adapters.task_storage.agent_retrieval import SQLiteAgentRetrievalHandoffs
from dsh_retrieval.adapters.task_storage.candidates import SQLiteCandidateManifestRepository
from dsh_retrieval.adapters.task_storage.retrieval import SQLiteRetrievalPlanRepository
from dsh_retrieval.adapters.task_storage.sqlite import SQLiteTaskRepository
from dsh_retrieval.agent_jobs import AgentJobDispatcher
from dsh_retrieval.agent_retrieval import AgentRetrievalService, AgentRetrievalExecutor
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.integration_models import MediaRecord, MediaResolution, QueryVector, SearchHit, SearchPage
from dsh_retrieval.models import Actor
from dsh_retrieval.physical_query_preparation import TextQueryCapability
from dsh_retrieval.search_budget import SearchBudget
from dsh_retrieval.search_scheduling import SearchLimits


class AgentJobsTest(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.AgentFrameworkTest(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.domain, self.actor, self.task = self.f.domain, self.f.actor, self.f.task
        def retrieval_only(task):
            value = json.loads(task.confirmed_proposal_json)
            value['query_spec']['agent_requirements'].update(schema_version='agent-requirements@2', processing=[])
            task.confirmed_proposal_json = json.dumps(value)
        self.domain.mutate_task(retrieval_only)
        self.denied, self.media_reads, self.encodings, self.searches = False, 0, 0, 0
        self.on_search = lambda: None
        self.content = image_bytes('PNG')
        self.media = MediaRecord('unit', 'v1', 'synthetic', 'asset', 'trusted:synthetic',
            'sha256:' + sha256(self.content).hexdigest())
        self.retrieval = SQLiteRetrievalPlanRepository(self.domain.repo.path,
            projects=self.domain.service.projects, authorization=self)
        self.manifests = SQLiteCandidateManifestRepository(self.domain.repo.path,
            projects=self.domain.service.projects, authorization=self)
        self.handoffs = SQLiteAgentRetrievalHandoffs(journal=self.f.app.journal, retrieval=self.retrieval,
            authority=lambda inv: self.f.authority(self.actor, inv),
            context_provider=lambda actor, task, role: {'role': role, 'snapshot_version': 1})
        self.compiler = AgentRetrievalService(handoffs=self.handoffs, embeddings=self,
            capabilities=(TextQueryCapability(self.domain.binding, 'image', 'filter@1', 10),))
        self.budget = SearchBudget(self.domain.repo.path, SearchLimits(max_calls=3, max_hit_slots=6))
        self.executor = AgentRetrievalExecutor(retrieval=self.retrieval, gateway=self, budget=self.budget)
        self.materializer = LocalCandidateMaterializer(ledger=self.retrieval, media=self, gateway=self,
            authorization=self, workspace_root=self.domain.repo.path.parent/'materialized')
        self.jobs = self.build()
        self.dispatch = AgentJobDispatcher(self.jobs, start=False)
        self.addCleanup(self.dispatch.close)
        self.body = {'capability': 'retrieval', 'planning': {k: v for k, v in self.f.f.body.items() if k != 'role'}}

    def build(self, planning=None):
        return build_retrieval_agent_jobs(planning=planning or self.f.app, compiler=self.compiler,
            executor=self.executor, materializer=self.materializer, manifests=self.manifests, batch_size=2)

    def require_access(self, *args):
        if self.denied: raise ServiceError('ACCESS_DENIED', 'synthetic revocation', 403)

    def encode_query(self, context, query, binding):
        self.encodings += 1
        return QueryVector(binding.encoder_ref, (.1,) * binding.vector_dimension)

    def search(self, context, request):
        self.searches += 1; self.on_search()
        return SearchPage((SearchHit(request.binding, 'synthetic-vector', .9),), None, 'exhausted', 'snapshot', 'snapshot')

    def resolve_hits(self, context, hits):
        self.require_access(context, 'trusted:synthetic', 'read')
        return tuple(MediaResolution(hit=hit, media=self.media, status='resolved') for hit in hits)

    @contextmanager
    def open_read(self, context, ref, *, max_bytes):
        self.require_access(context, ref, 'read'); self.media_reads += 1
        with BytesIO(self.content) as stream: yield stream

    def prepare(self, key='prepare'):
        return self.jobs.prepare(self.actor, self.task.task_id, key, self.body)[0]

    def confirm(self, view, key=None):
        review = view['confirmation']
        return self.jobs.act(self.actor, self.task.task_id, view['job_ref'], 'confirm',
            key or 'confirm-'+review['checkpoint'], {'checkpoint': review['checkpoint'], 'action': 'confirm',
                'proposal_digest': review['proposal_digest'], 'expected_task_version': review['expected_task_version']})[0]

    def start(self, view, key='start'):
        return self.jobs.act(self.actor, self.task.task_id, view['job_ref'], 'start', key,
            {'action': 'start', **view['planning_review']})[0]

    def wait_phase(self, ref, phase):
        for _ in range(150):
            self.dispatch.drain()
            view = self.jobs.get(self.actor, self.task.task_id, ref)
            if view['phase'] == phase: return view
            if view['status'] in ('failed', 'unknown'): self.fail(str(view))
            time.sleep(.01)
        self.fail('job did not reach '+phase)

    def planned(self):
        view = self.prepare(); self.confirm(view); self.start(view)
        return self.wait_phase(view['job_ref'], 'retrieval')

    def candidate_review(self):
        view = self.planned(); self.confirm(view)
        return self.wait_phase(view['job_ref'], 'candidates')

    def test_prepare_is_original_draft_only_and_queries_do_not_run_any_work(self):
        view = self.prepare()
        self.assertEqual(view['status'], 'waiting_confirmation')
        self.assertTrue(view['job_ref'].startswith('agentdraft_'))
        for _ in range(3):
            self.jobs.get(self.actor, self.task.task_id, view['job_ref'])
            self.jobs.artifacts(self.actor, self.task.task_id, view['job_ref'])
        self.assertEqual((self.encodings, self.searches, self.media_reads), (0, 0, 0))
        self.assertEqual(self.f.calls, [])
        with self.domain.journal._tx() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM harness_attempts').fetchone()[0], 0)
        with self.assertRaises(ServiceError): self.start(view)

    def test_unified_job_completes_and_delivers_authorized_original_manifest_without_cleaning(self):
        view = self.candidate_review()
        self.assertEqual(view['status'], 'waiting_confirmation')
        self.assertEqual(self.searches, 1)
        self.assertEqual(view['confirmation']['candidate_count'], 1)
        self.assertEqual(self.jobs.artifacts(self.actor, self.task.task_id, view['job_ref'])['items'], [])
        finished = self.confirm(view)
        self.assertEqual(finished['status'], 'completed')
        output = self.jobs.artifacts(self.actor, self.task.task_id, view['job_ref'])['items'][0]
        self.assertEqual(output['verification'], 'media_verified_rules_unchecked')
        self.assertTrue(output['manifest_ref'].startswith('manifest_'))
        self.assertIsNone(output['accepted_count'])
        self.assertNotIn('trusted:synthetic', json.dumps(output))
        self.assertNotIn('source_path', json.dumps(output))
        current = self.domain.repo.get(self.actor, self.task.task_id)
        self.assertIsNone(current.active_cleaning_operation_id)
        self.assertEqual(current.stage, 'retrieving')
        self.assertEqual((self.encodings, self.searches, len(self.f.calls)), (1, 1, 1))

    def test_manual_checkpoints_block_dispatch_and_all_retries_keep_one_effect(self):
        initial = self.prepare(); self.confirm(initial); self.start(initial)
        plan = self.wait_phase(initial['job_ref'], 'retrieval')
        for _ in range(3): self.dispatch.drain()
        self.assertEqual(self.searches, 0)
        self.confirm(plan); self.confirm(plan, 'another-owner-click')
        candidates = self.wait_phase(plan['job_ref'], 'candidates')
        finished = self.confirm(candidates)
        self.confirm(candidates); self.confirm(candidates, 'another-candidate-click')
        self.confirm(initial, 'old-planning-click')
        self.start(initial, 'old-start-click')
        repeated, replayed = self.jobs.prepare(self.actor, self.task.task_id, 'prepare', self.body)
        self.assertTrue(replayed); self.assertEqual(repeated['job_ref'], finished['job_ref'])
        self.assertEqual((self.encodings, self.searches, len(self.f.calls)), (1, 1, 1))
        with self.assertRaises(ServiceError):
            self.jobs.prepare(self.actor, self.task.task_id, 'prepare',
                dict(self.body, planning=dict(self.body['planning'], model_calls=3)))

    def test_restart_after_approval_recovers_without_an_in_memory_queue(self):
        view = self.planned(); self.confirm(view)
        self.dispatch.close()
        planning = self.f.build(); self.addCleanup(planning.close)
        jobs = self.build(planning)
        worker = AgentJobDispatcher(jobs, start=False); self.addCleanup(worker.close)
        worker.drain()
        recovered = jobs.get(self.actor, self.task.task_id, view['job_ref'])
        self.assertEqual(recovered['phase'], 'candidates', recovered)
        self.assertEqual(self.searches, 1)
        worker.drain(); self.assertEqual(self.searches, 1)

    def test_read_and_artifact_queries_reject_other_actors_and_revoked_media(self):
        view = self.candidate_review(); self.confirm(view)
        for reader in (self.jobs.get, self.jobs.artifacts):
            with self.assertRaises(ServiceError): reader(Actor('tenant', 'other'), self.task.task_id, view['job_ref'])
            with self.assertRaises(ServiceError): reader(Actor('other', 'owner'), self.task.task_id, view['job_ref'])
        self.denied = True
        with self.assertRaises(ServiceError): self.jobs.artifacts(self.actor, self.task.task_id, view['job_ref'])
        with self.assertRaises(ServiceError): self.jobs.get(self.actor, self.task.task_id, view['job_ref'])

    def test_candidate_bytes_change_prevents_owner_publication(self):
        view = self.candidate_review(); self.content = b'changed'
        with self.assertRaises(ServiceError) as error: self.confirm(view)
        self.assertEqual(error.exception.code, 'INPUT_SNAPSHOT_CHANGED')
        self.assertEqual(self.jobs.artifacts(self.actor, self.task.task_id, view['job_ref'])['items'], [])

    def test_cancel_fences_root_task_and_is_idempotent_even_after_original_stage_finished(self):
        view = self.planned(); self.confirm(view)
        current = self.jobs.get(self.actor, self.task.task_id, view['job_ref'])
        body = {'action': 'cancel', 'expected_task_version': current['task_version']}
        first, replayed = self.jobs.act(self.actor, self.task.task_id, view['job_ref'], 'cancel', 'cancel', body)
        self.assertFalse(replayed); self.assertEqual(first['status'], 'cancelled')
        second, replayed = self.jobs.act(self.actor, self.task.task_id, view['job_ref'], 'cancel', 'cancel', body)
        self.assertTrue(replayed); self.assertEqual(first, second)
        self.dispatch.drain(); self.assertEqual(self.searches, 0)
        with self.assertRaises(ServiceError): self.jobs.artifacts(self.actor, self.task.task_id, view['job_ref'])

    def test_cancel_during_search_blocks_late_page_publication(self):
        view = self.planned(); self.confirm(view)
        def cancel():
            current = self.jobs.get(self.actor, self.task.task_id, view['job_ref'])
            self.jobs.act(self.actor, self.task.task_id, view['job_ref'], 'cancel', 'inflight',
                {'action': 'cancel', 'expected_task_version': current['task_version']})
        self.on_search = cancel
        with self.assertRaises(ServiceError): self.jobs.advance(self.actor, self.task.task_id, view['job_ref'])
        self.assertEqual(self.jobs.get(self.actor, self.task.task_id, view['job_ref'])['status'], 'cancelled')
        with closing(self.domain.repo._connect()) as db:
            from dsh_retrieval.adapters.task_storage.retrieval import load_state
            state = load_state(db.execute('SELECT format,payload FROM retrieval_ledger WHERE id=1').fetchone())
            self.assertTrue(all(value[2] is None for value in state['operations'].values()))

    def test_ambiguous_search_remains_unknown_and_is_not_resent_after_restart(self):
        view = self.planned(); self.confirm(view)
        def timeout(): raise ServiceError('SEARCH_TIMEOUT', 'synthetic timeout', 504, True)
        self.on_search = timeout
        self.dispatch.drain()
        self.assertEqual(self.jobs.get(self.actor, self.task.task_id, view['job_ref'])['status'], 'unknown')
        worker = AgentJobDispatcher(self.build(), start=False); self.addCleanup(worker.close)
        worker.drain(); self.assertEqual(self.searches, 1)

    def test_compiler_publication_is_recovered_after_crash_before_linking_without_reencoding(self):
        view = self.prepare(); self.confirm(view); self.start(view)
        for _ in range(100):
            snapshot = self.jobs.links.get(self.actor, self.task.task_id, view['job_ref'])
            if snapshot['attempt']['status'] == 'finished': break
            time.sleep(.01)
        self.assertTrue(self.jobs.links.begin_compile(self.actor, self.task.task_id, view['job_ref']))
        self.compiler.prepare(self.actor, snapshot['handle'], epoch=1, batch_size=2)
        self.dispatch.drain()
        recovered = self.jobs.get(self.actor, self.task.task_id, view['job_ref'])
        self.assertEqual(recovered['phase'], 'retrieval', recovered)
        self.assertEqual(self.encodings, 1)

    def test_concurrent_advances_create_one_plan_and_one_page(self):
        view = self.prepare(); self.confirm(view); self.start(view)
        for _ in range(100):
            state = self.jobs.links.get(self.actor, self.task.task_id, view['job_ref'])['attempt']['status']
            if state == 'finished': break
            time.sleep(.01)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: self.jobs.advance(self.actor, self.task.task_id, view['job_ref']), range(4)))
        view = self.jobs.get(self.actor, self.task.task_id, view['job_ref'])
        self.assertEqual(self.encodings, 1); self.confirm(view)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: self.jobs.advance(self.actor, self.task.task_id, view['job_ref']), range(4)))
        self.assertEqual(self.searches, 1)

    def test_hard_delete_cascades_links_and_existing_ledger_reopens(self):
        self.prepare()
        self.domain.repo.hard_delete_conversation(self.actor, self.task.conversation_id)
        with closing(self.domain.repo._connect()) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM agent_job_links').fetchone()[0], 0)
        SQLiteTaskRepository(self.domain.repo.path)

    def test_workflow_and_request_injection_do_not_select_uninstalled_capabilities(self):
        for body in (dict(self.body, capability='training'), dict(self.body, module='arbitrary'),
                     dict(self.body, planning=dict(self.body['planning'], role='cleaning'))):
            with self.assertRaises(ServiceError): self.jobs.prepare(self.actor, self.task.task_id, 'bad', body)
        self.assertEqual(self.f.calls, [])

    def test_job_link_failure_rolls_back_original_business_plan_confirmation(self):
        view = self.planned()
        original = self.jobs.links._write
        def fail(db, ref, links):
            if links['approved_digest']: raise ServiceError('SYNTHETIC_LINK_FAILURE', 'rollback fixture', 503)
            return original(db, ref, links)
        self.jobs.links._write = fail
        try:
            with self.assertRaises(ServiceError): self.confirm(view)
        finally: self.jobs.links._write = original
        self.assertIsNone(self.retrieval.get_current(self.actor, self.task.task_id))
        self.assertEqual(self.domain.repo.get(self.actor, self.task.task_id).task_version, 1)
        self.assertEqual(self.jobs.get(self.actor, self.task.task_id, view['job_ref'])['status'], 'waiting_confirmation')
        self.confirm(view)
        self.assertEqual(self.domain.repo.get(self.actor, self.task.task_id).task_version, 2)

    def test_job_link_failure_rolls_back_original_candidate_publication(self):
        view = self.candidate_review()
        original = self.jobs.links._write
        def fail(db, ref, links):
            if links['manifest_ref']: raise ServiceError('SYNTHETIC_LINK_FAILURE', 'rollback fixture', 503)
            return original(db, ref, links)
        self.jobs.links._write = fail
        try:
            with self.assertRaises(ServiceError): self.confirm(view)
        finally: self.jobs.links._write = original
        with closing(self.domain.repo._connect()) as db:
            from dsh_retrieval.adapters.task_storage.candidates import load_state
            state = load_state(db.execute('SELECT format,payload FROM candidate_ledger WHERE id=1').fetchone())
            self.assertEqual(state['published'], {})
        self.assertEqual(self.jobs.artifacts(self.actor, self.task.task_id, view['job_ref'])['items'], [])
        self.assertEqual(self.confirm(view)['status'], 'completed')

    def test_candidate_review_reauthorizes_media_before_it_is_published(self):
        view = self.candidate_review(); self.denied = True
        with self.assertRaises(ServiceError): self.jobs.get(self.actor, self.task.task_id, view['job_ref'])
        with self.assertRaises(ServiceError): self.confirm(view)

    def test_concurrent_drains_of_one_dispatcher_do_not_overlap_advancement(self):
        import threading
        from types import SimpleNamespace
        entered, release = threading.Event(), threading.Event()
        active = 0
        peak = 0
        calls = 0
        guard = threading.Lock()
        def advance(*args):
            nonlocal active, peak, calls
            with guard:
                active += 1; peak = max(peak, active); calls += 1
            entered.set(); release.wait(2)
            with guard: active -= 1
        jobs = SimpleNamespace(links=SimpleNamespace(pending=lambda: [(self.actor, 'task', 'job')]), advance=advance)
        worker = AgentJobDispatcher(jobs, start=False)
        self.addCleanup(worker.close)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(worker.drain)
            self.assertTrue(entered.wait(1))
            second = pool.submit(worker.drain)
            try:
                time.sleep(.03)
                self.assertEqual(peak, 1)
            finally: release.set()
            first.result(timeout=2); second.result(timeout=2)
        self.assertEqual(calls, 2)
        self.assertEqual(peak, 1)

    def test_query_terms_are_exact_compiled_strategy_and_reads_do_not_execute(self):
        view = self.prepare()
        self.assertEqual(view['query_terms'], [])
        view = self.planned()
        expected = [self.domain.proposal['proposal']['query']]
        self.assertEqual(view['query_terms'], expected)
        self.assertGreaterEqual(view['planning_progress']['model_calls_admitted'], 1)
        encodings = self.encodings
        self.confirm(view)
        self.assertEqual(self.jobs.get(self.actor, self.task.task_id, view['job_ref'])['query_terms'], expected)
        self.assertEqual(self.encodings, encodings)
        self.assertEqual(self.searches, 0)

    def test_planning_unknown_error_remains_unknown_without_dispatch_retry(self):
        view = self.prepare()
        self.jobs.links.record_error(self.actor, self.task.task_id, view['job_ref'], 'AGENT_PLANNING_UNKNOWN')
        value = self.jobs.get(self.actor, self.task.task_id, view['job_ref'])
        self.assertEqual(value['status'], 'unknown')
        self.assertEqual(value['code'], 'AGENT_PLANNING_UNKNOWN')
        self.dispatch.drain()
        self.assertEqual((self.encodings, self.searches, len(self.f.calls)), (0, 0, 0))

    def test_unpublished_compile_intent_is_not_reexecuted_or_starving_other_jobs(self):
        view = self.prepare(); self.confirm(view); self.start(view)
        for _ in range(100):
            snapshot = self.jobs.links.get(self.actor, self.task.task_id, view['job_ref'])
            if snapshot['attempt']['status'] == 'finished': break
            time.sleep(.01)
        self.jobs.links.begin_compile(self.actor, self.task.task_id, view['job_ref'])
        started = self.jobs.links.get(self.actor, self.task.task_id, view['job_ref'])['links']['compile_started_at']
        clock = self.jobs.links.journal.clock
        self.jobs.links.journal.clock = lambda: started + 31
        self.addCleanup(lambda: setattr(self.jobs.links.journal, 'clock', clock))
        self.assertEqual(self.jobs.get(self.actor, self.task.task_id, view['job_ref'])['status'], 'unknown')
        self.dispatch.drain()
        self.assertEqual(self.encodings, 0)
        self.assertEqual(self.jobs.links.pending(), ())

    def test_unexpected_worker_error_is_sanitized_and_preserves_the_durable_intent(self):
        view = self.planned(); self.confirm(view)
        def fail(): raise RuntimeError('synthetic-private-connector-detail')
        self.on_search = fail
        self.dispatch.drain()
        value = self.jobs.get(self.actor, self.task.task_id, view['job_ref'])
        self.assertEqual(value['status'], 'unknown')
        self.assertEqual(value['code'], 'AGENT_JOB_WORKER_FAILED')
        self.assertNotIn('synthetic-private', json.dumps(value))
        self.dispatch.drain(); self.assertEqual(self.searches, 1)
