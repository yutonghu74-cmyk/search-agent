from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
import json
import unittest

import test_agent_jobs as fixtures
import test_agent_retrieval_window as window_fixtures
from dsh_retrieval.adapters.harness.composition import build_retrieval_agent_jobs
from dsh_retrieval.adapters.task_storage.candidates import load_state
from dsh_retrieval.agent_jobs import AgentJobDispatcher
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.integration_models import SearchHit, SearchPage, MediaResolution
from dsh_retrieval.models import Actor
from dsh_retrieval.search_budget import SearchBudget
from dsh_retrieval.search_scheduling import SearchLimits


def build(f, planning=None, *, max_pages=3):
    return build_retrieval_agent_jobs(planning=planning or f.f.app, compiler=f.compiler,
        executor=f.executor, materializer=f.materializer, manifests=f.manifests, batch_size=2, max_pages=max_pages)


class AgentJobPagesTest(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.AgentJobsTest(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.f.jobs = build(self.f)
        self.f.dispatch = AgentJobDispatcher(self.f.jobs, start=False); self.addCleanup(self.f.dispatch.close)
        self.actor, self.task_id = self.f.actor, self.f.task.task_id
        self.pages = [('a','b'),('b','c'),('d','e')]
        self.requests = []
        self.on_search = lambda: None
        self.f.search = self.search
        self.f.resolve_hits = self.resolve_hits
        self.set_target(4)

    def set_target(self, count, *, paginate=True):
        def mutate(task):
            spec = json.loads(task.confirmed_proposal_json)
            spec['query_spec']['agent_requirements']['autonomy']['actions'] = ['rewrite_query','switch_source']+(['paginate'] if paginate else [])
            spec['query_spec']['target_count'] = count
            spec['query_spec']['refill_policy']['target_count'] = count
            task.confirmed_proposal_json = json.dumps(spec); task.target_count = count
        self.f.domain.mutate_task(mutate)

    def search(self, context, request):
        self.requests.append(request); self.on_search()
        index = 0 if request.cursor is None else int(request.cursor.split('-')[-1])
        more = index+1 < len(self.pages)
        return SearchPage(tuple(SearchHit(request.binding, name, .9) for name in self.pages[index]),
            'cursor-'+str(index+1) if more else None, 'more_available' if more else 'exhausted', 'snapshot','snapshot')

    def resolve_hits(self, context, hits):
        return tuple(MediaResolution(hit=hit, media=replace(self.f.media,
            media_unit_id=hit.vector_id, source_asset_id=hit.vector_id), status='resolved') for hit in hits)

    def planned(self):
        view = self.f.planned()
        self.assertEqual(view['workflow_ref'],'retrieval-agent-job@2')
        self.assertEqual(view['confirmation']['max_pages'],3)
        self.assertEqual(view['confirmation']['max_raw_hits'],6)
        self.f.confirm(view)
        return view

    def review(self):
        view = self.planned()
        return self.f.wait_phase(view['job_ref'],'candidates')

    def manifests(self):
        with closing(self.f.domain.repo._connect()) as db:
            return load_state(db.execute('SELECT format,payload FROM candidate_ledger WHERE id=1').fetchone())['manifests']

    def test_pages_use_real_cursors_aggregate_unique_candidates_and_publish_original_manifests(self):
        view = self.review(); c=view['confirmation']
        self.assertEqual((c['candidate_count'],c['raw_hit_count'],c['page_count']),(5,6,3))
        self.assertEqual([r.cursor for r in self.requests],[None,'cursor-1','cursor-2'])
        self.assertEqual(len(self.manifests()),0)
        self.f.confirm(view); self.f.confirm(view)
        artifacts = self.f.jobs.artifacts(self.actor,self.task_id,view['job_ref'])
        self.assertEqual((artifacts['candidate_count'],artifacts['page_count']),(5,3))
        self.assertEqual(len(artifacts['items']),3); self.assertEqual(len(self.manifests()),3)
        self.assertIsNone(artifacts['accepted_count'])
        for _ in range(3): self.f.dispatch.drain()
        self.assertEqual(len(self.requests),3)

    def test_target_reached_stops_before_the_page_cap(self):
        self.set_target(3); self.pages=[('a','b'),('c','d'),('e','f')]
        view=self.review()
        self.assertEqual(view['confirmation']['stop_reason'],'candidate_target')
        self.assertEqual((len(self.requests),view['confirmation']['candidate_count']),(2,4))

    def test_duplicate_pages_reach_page_limit_without_inventing_effective_count(self):
        self.pages=[('a',),('a',),('a',),('a',)]
        view=self.review(); c=view['confirmation']
        self.assertEqual((c['stop_reason'],c['candidate_count'],c['raw_hit_count']),('page_limit',1,3))
        self.assertIsNone(c['accepted_count']); self.assertEqual(len(self.requests),3)

    def test_missing_paginate_scope_keeps_one_page(self):
        self.set_target(4,paginate=False)
        view=self.f.planned(); self.assertEqual(view['confirmation']['max_pages'],1)
        self.f.confirm(view); final=self.f.wait_phase(view['job_ref'],'candidates')
        self.assertEqual(final['confirmation']['stop_reason'],'page_limit'); self.assertEqual(len(self.requests),1)

    def test_recovery_after_page_commit_before_linking_never_searches_that_page_again(self):
        view=self.planned(); approved=self.f.retrieval.get_current(self.actor,self.task_id)
        self.f.executor.execute_page(self.actor,self.task_id,plan_id=approved.request.plan_id,proposal_digest=approved.proposal_digest)
        self.assertEqual(len(self.requests),1)
        planning=self.f.f.build(); self.addCleanup(planning.close)
        jobs=build(self.f,planning,max_pages=5); worker=AgentJobDispatcher(jobs,start=False); self.addCleanup(worker.close)
        worker.drain(); self.assertEqual(len(self.requests),1)
        worker.drain(); self.assertEqual(len(self.requests),2)
        worker.drain(); result=jobs.get(self.actor,self.task_id,view['job_ref'])
        self.assertEqual(result['confirmation']['page_count'],3); self.assertEqual(len(self.requests),3)
        self.assertEqual(result['execution_scope']['max_pages'],3)

    def test_budget_denial_keeps_uncommitted_page_unknown_without_extra_search(self):
        view=self.planned()
        self.f.executor.budget=SearchBudget(self.f.domain.repo.path,SearchLimits(max_calls=1,max_hit_slots=2))
        self.f.dispatch.drain();self.f.dispatch.drain();self.f.dispatch.drain()
        result=self.f.jobs.get(self.actor,self.task_id,view['job_ref'])
        self.assertEqual(result['status'],'unknown');self.assertEqual(len(self.requests),1)
        self.assertEqual(result['retrieval_progress']['page_count'],1)

    def test_cancel_after_first_page_blocks_late_second_page_and_candidate_publication(self):
        view=self.planned();self.f.dispatch.drain()
        def cancel():
            current=self.f.jobs.get(self.actor,self.task_id,view['job_ref'])
            self.f.jobs.act(self.actor,self.task_id,view['job_ref'],'cancel','cancel-pages',
                {'action':'cancel','expected_task_version':current['task_version']})
        self.on_search=cancel
        self.f.dispatch.drain()
        self.assertEqual(self.f.jobs.get(self.actor,self.task_id,view['job_ref'])['status'],'cancelled')
        self.f.dispatch.drain();self.assertEqual(len(self.requests),2);self.assertEqual(self.manifests(),{})
        with self.assertRaises(ServiceError):self.f.jobs.artifacts(self.actor,self.task_id,view['job_ref'])

    def test_empty_exhausted_first_page_is_truthful_empty_candidate_delivery(self):
        self.pages=[()];view=self.review()
        self.assertEqual((view['confirmation']['candidate_count'],view['confirmation']['stop_reason']),(0,'exhausted'))
        self.f.confirm(view);self.assertEqual(len(self.manifests()),1)

    def test_second_page_unknown_preserves_first_page_and_never_auto_resends(self):
        view=self.planned(); self.f.dispatch.drain()
        self.on_search=lambda: (_ for _ in ()).throw(ServiceError('SEARCH_TIMEOUT','synthetic unknown',504))
        self.f.dispatch.drain()
        for _ in range(3): self.f.dispatch.drain()
        result=self.f.jobs.get(self.actor,self.task_id,view['job_ref'])
        self.assertEqual(result['status'],'unknown'); self.assertEqual(len(self.requests),2)
        self.assertEqual(result['retrieval_progress']['page_count'],1)
        self.assertEqual(self.f.jobs.artifacts(self.actor,self.task_id,view['job_ref'])['items'],[])

    def test_concurrent_advances_dispatch_each_cursor_once(self):
        view=self.planned()
        for _ in range(4):
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda _:self.f.jobs.advance(self.actor,self.task_id,view['job_ref']),range(4)))
        self.assertEqual([r.cursor for r in self.requests],[None,'cursor-1','cursor-2'])
        self.assertEqual(self.f.jobs.get(self.actor,self.task_id,view['job_ref'])['confirmation']['page_count'],3)

    def test_all_page_publications_and_links_roll_back_together(self):
        view=self.review(); original=self.f.jobs.links._write
        def fail(db,ref,links):
            if links.get('manifest_refs'): raise ServiceError('SYNTHETIC_LINK_FAILURE','rollback all pages',503)
            return original(db,ref,links)
        self.f.jobs.links._write=fail
        try:
            with self.assertRaises(ServiceError):self.f.confirm(view)
        finally:self.f.jobs.links._write=original
        self.assertEqual(self.manifests(),{})
        self.assertEqual(self.f.jobs.artifacts(self.actor,self.task_id,view['job_ref'])['items'],[])
        self.f.confirm(view); self.assertEqual(len(self.manifests()),3)

    def test_source_permissions_and_changed_later_page_block_all_publication(self):
        view=self.review(); resolve=self.f.resolve_hits
        def changed(context,hits):
            return tuple(replace(r,media=replace(r.media,asset_version='changed')) if r.hit.vector_id=='d' else r for r in resolve(context,hits))
        self.f.resolve_hits=changed
        with self.assertRaises(ServiceError) as caught:self.f.confirm(view)
        self.assertEqual(caught.exception.code,'INPUT_SNAPSHOT_CHANGED'); self.assertEqual(self.manifests(),{})
        self.f.resolve_hits=resolve; self.f.confirm(view)
        self.f.denied=True
        with self.assertRaises(ServiceError):self.f.jobs.artifacts(self.actor,self.task_id,view['job_ref'])
        with self.assertRaises(ServiceError):self.f.jobs.get(Actor('tenant','other'),self.task_id,view['job_ref'])

    def test_v1_job_keeps_one_page_when_v2_is_installed(self):
        legacy=fixtures.AgentJobsTest.build(self.f)
        view=legacy.prepare(self.actor,self.task_id,'legacy-job',self.f.body)[0]
        upgraded=build(self.f)
        old=upgraded.get(self.actor,self.task_id,view['job_ref'])
        self.assertEqual(old['workflow_ref'],'retrieval-agent-job@1')
        self.assertEqual(old['execution_scope']['max_pages'],1)
        repeated,replayed=upgraded.prepare(self.actor,self.task_id,'legacy-job',self.f.body)
        self.assertTrue(replayed);self.assertEqual(repeated['workflow_ref'],'retrieval-agent-job@1')
        self.assertEqual(repeated['execution_scope']['max_pages'],1)

    def test_plan_digest_without_displayed_page_budget_cannot_authorize_v2(self):
        view=self.f.planned();snapshot=self.f.jobs.links.get(self.actor,self.task_id,view['job_ref'])
        view['confirmation']['proposal_digest']=snapshot['proposal'].proposal_digest
        with self.assertRaises(ServiceError) as caught:self.f.confirm(view)
        self.assertEqual(caught.exception.code,'PLAN_DIGEST_MISMATCH')
        self.assertIsNone(self.f.retrieval.get_current(self.actor,self.task_id));self.assertEqual(self.requests,[])

    def test_same_candidate_identity_with_changed_media_reference_is_not_silently_merged(self):
        view=self.planned();self.f.dispatch.drain()
        resolve=self.f.resolve_hits
        self.f.resolve_hits=lambda context,hits:tuple(replace(r,media=replace(r.media,authorized_media_ref='trusted:changed'))
            if r.hit.vector_id=='b' else r for r in resolve(context,hits))
        self.f.dispatch.drain()
        current=self.f.jobs.get(self.actor,self.task_id,view['job_ref'])
        self.assertEqual((current['status'],current['code']),('failed','INPUT_SNAPSHOT_CHANGED'))
        self.assertEqual(self.manifests(),{})


class AgentPagesWindowTest(unittest.TestCase):
    def test_public_task_multi_page_admission_same_candidate_window_and_manifest_collection(self):
        f=window_fixtures.AgentRetrievalWindowTest();f.setUp();self.addCleanup(f.doCleanups)
        f.worker.close();jobs=build(f.f)
        worker=AgentJobDispatcher(jobs,start=True,poll_seconds=.05);self.addCleanup(worker.close)
        f.window.jobs=jobs;f.window.workflow=jobs.registry.capability('retrieval');f.window.dispatcher=worker
        f.server.RequestHandlerClass.agent_jobs=jobs
        f.f.jobs=jobs
        requests=[]
        def search(context,request):
            requests.append(request)
            return SearchPage((SearchHit(request.binding,'synthetic-vector',.9),),
                'next' if request.cursor is None else None,'more_available' if request.cursor is None else 'exhausted','snapshot','snapshot')
        f.f.search=search
        body=f.body();body['query_spec']['target_count']=4;body['query_spec']['refill_policy']['target_count']=4
        body['query_spec']['agent_requirements']['autonomy']['actions'].append('paginate')
        draft=f.draft(body);code,task=f.confirm_task(draft);self.assertEqual(code,202,task)
        task_id=task['task_id'];planning=f.workspace(task_id)['agent_job'];f.confirm_job(task_id,planning)
        code,value=f.call(f'/v1/retrieval/tasks/{task_id}/agent-jobs/{planning["job_ref"]}/start',
            {'action':'start',**planning['planning_review']},key='start-pages');self.assertEqual(code,202,value)
        retrieval=f.wait(task_id,'retrieval');self.assertEqual(retrieval['agent_job']['confirmation']['max_pages'],3)
        f.confirm_job(task_id,retrieval['agent_job']);review=f.wait(task_id,'candidates')
        self.assertEqual((review['candidate_count'],review['agent_job']['confirmation']['candidate_count']),(2,1))
        code,page=f.call(f'/v1/retrieval/tasks/{task_id}/candidates?limit=1');self.assertEqual(code,200,page)
        self.assertEqual((page['raw_hit_count'],page['resolved_count']),(2,1))
        code,second=f.call(f'/v1/retrieval/tasks/{task_id}/candidates?limit=1&cursor='+page['next_cursor'])
        self.assertEqual(code,200,second);self.assertEqual(len(second['items']),1)
        f.confirm_job(task_id,review['agent_job'])
        code,artifacts=f.call(f'/v1/retrieval/tasks/{task_id}/agent-jobs/{planning["job_ref"]}/artifacts')
        self.assertEqual((code,len(artifacts['items']),artifacts['candidate_count']),(200,2,1))
        self.assertEqual(len(requests),2);self.assertEqual(f.legacy_calls,[])
