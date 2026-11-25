"""Synthetic integration through public draft confirmation and the real outboxes."""
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from draft_helpers import make_service
from test_video_query import video_body
from dsh_retrieval.adapters.task_storage.sqlite import SQLiteTaskRepository
from dsh_retrieval.adapters.projects import LocalProjectAccess
from dsh_retrieval.adapters.cleaning.video import WholeVideoConditionDetector
from dsh_retrieval.detector_artifacts import DetectorArtifact, DetectorArtifactBinding, DetectorArtifactManifest
from dsh_retrieval.intent_compiler import DetectorBinding
from dsh_retrieval.intent_review import Capability
from dsh_retrieval.integration_models import IndexBinding, Library, MediaRecord, MediaResolution, QueryVector, SearchHit, SearchPage
from dsh_retrieval.models import Actor
from dsh_retrieval.model_runtime import ModelRuntime
from dsh_retrieval.multi_rule_contracts import CleaningRuleVerdict, bytes_digest
from dsh_retrieval.platform_resources import PlatformResources, DatasetVersionRef
from dsh_retrieval.platform_services import build_platform_services
from dsh_retrieval.physical_query_preparation import TextQueryCapability
from dsh_retrieval.released_detectors import ReleasedDetectorRegistry
from dsh_retrieval.video_media import VideoResourcePolicy
from dsh_retrieval.whole_video import VIDEO_PREDICATE
from dsh_retrieval.errors import ServiceError


class VideoRuntimeFlowTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.actor = Actor('tenant-a', 'user-a')
        self.tasks = SQLiteTaskRepository(self.root/'tasks.db')
        self.service = make_service(self.tasks)
        self.binding = IndexBinding('video-index','videos','v1','aligned-video-text',2,'COSINE')
        self.ref = DatasetVersionRef('videos','dataset-v1'); self.reads=0
        self.content = {'video:1':b'pass-one','video:2':b'pass-two'}
        def search(context, request, project, versions):
            n = 1 if request.cursor is None else 2
            return SearchPage((SearchHit(request.binding,n,0.8),), 'next' if n == 1 else None,
                              'more_available' if n == 1 else 'exhausted','snapshot','snapshot-v1')
        def resolve(context,hits,project):
            return tuple(MediaResolution(h, MediaRecord('source-unit-'+str(h.vector_id),'v1','source',str(h.vector_id),
                'video:'+str(h.vector_id), bytes_digest(self.content['video:'+str(h.vector_id)])),'resolved') for h in hits)
        @contextmanager
        def read(context, project, ref, limit):
            self.reads += 1
            with BytesIO(self.content[ref]) as stream: yield stream
        self.resources = PlatformResources(tasks=self.tasks, projects=self.service.projects,
            project_datasets=lambda *a:(self.ref,),user_datasets=lambda *a:(self.ref,),
            libraries=lambda *a:(Library('videos','whole video source','video',(self.binding,)),),
            resolve_hits=resolve,search=search,open_media=read,media_version=lambda *a:self.ref,
            whole_video_bindings=lambda:(self.binding,))
        self.resources.video_policy=VideoResourcePolicy()
        self.resources.video_inspector=None
        self.resources.video_file_inspector=lambda path:{'media_type':'video','container':'mp4','duration_ms':1000}
        self.app=SimpleNamespace(service=self.service,repository=self.tasks,projects=self.service.projects,
            runtime=ModelRuntime(),resources=self.resources,closers=[])
        self.hooks={'capabilities':lambda app:(TextQueryCapability(self.binding,'video','trusted-filter',8),),
            'encode_query':lambda *a:QueryVector('aligned-video-text',(1.,0.)),
            'whole_video_bindings':lambda app:(self.binding,), 'video_preflight':lambda *a:{'ready':True},
            'video_registry':lambda app,actor,task:self.registry(task),
            'inspect_video_file':lambda app,path:self.resources.video_file_inspector(path)}
        self.components=build_platform_services(self.app,{'task_ledger':str(self.tasks.path),
            'catalog_ledger':str(self.root/'catalog.db')},self.hooks,start=False)
        self.addCleanup(lambda:[obj.close() for obj in reversed(self.app.closers)])
        artifact=self.root/'synthetic-evaluator.py';artifact.write_bytes(b'synthetic evaluator')

    def registry(self,task):
        def evaluate(content,candidate,binding,conditions):
            return tuple(CleaningRuleVerdict(c['id'],'pass','OBSERVED','test.evidence') for c in conditions['clauses']), 'test.evidence'
        def evaluate_file(path,candidate,binding,conditions):
            if hasattr(self,'file_paths'): self.file_paths.append(path)
            with path.open('rb') as stream: prefix=stream.read(4)
            self.assertEqual(prefix,b'pass')
            return tuple(CleaningRuleVerdict(c['id'],'pass','OBSERVED','test.evidence') for c in conditions['clauses']), 'test.evidence'
        detector=WholeVideoConditionDetector(task,evaluate,evaluate_file=evaluate_file)
        artifact=self.root/'synthetic-evaluator.py'
        binding=DetectorArtifactBinding(detector.backend_ref,detector.validator_ref,detector.evidence_ref,
            (DetectorArtifact('implementation',artifact.name,bytes_digest(artifact.read_bytes())),))
        detector.artifact_binding_digest=binding.binding_digest
        return ReleasedDetectorRegistry((Capability(VIDEO_PREDICATE,'constraint','video',('eq',),'boolean','verified',
            detector.validator_ref,detector.evidence_ref),), (DetectorBinding(VIDEO_PREDICATE,'video',detector.validator_ref,
            detector.backend_ref,detector.evidence_ref),),{detector.backend_ref:detector},DetectorArtifactManifest(self.root,(binding,)))

    def create(self,auto=True,limits=None,fixed=False):
        body=video_body();body['query_spec']['target_count']=2
        body['query_spec']['refill_policy']={'mode':'maintain_target','target_count':2,
            'approved_library_ids':['videos'],'progression':'auto' if auto else 'manual'}
        if fixed:
            body['query_spec']['refill_policy']={'mode':'input_only','target_count':2,'approved_library_ids':[],'progression':'manual'}
        for node in ('retrieval','cleaning','refill'):body['node_modes'][node]='manual'
        if limits is not None: body['query_spec']['refill_limits']=limits
        if auto:
            for node in ('retrieval','cleaning','refill'):body['node_modes'][node]='auto'
        draft,_=self.service.save_draft(self.actor,'demo','draft',body)
        self.assertTrue(draft['video_summary']['execution']['available'])
        task,_=self.service.create_task(self.actor,'confirm',{'draft_id':draft['draft_id'],'expected_draft_version':1,
            'proposal_digest':draft['proposal_digest'],'action':'confirm'})
        return task['task_id']

    def test_public_confirmation_search_validation_refill_and_reference_delivery(self):
        tid=self.create();c=self.components;first=c['first_search']
        c['search_dispatch'].dispatch_task(tid)
        first.tick(self.actor,tid,max_requests=1)
        c['video_initial_intake'].drain()
        c['cleaning_dispatch']._drain()
        c['refill_service'].runtime.auto.drain()
        c['refill_service'].runtime.auto_intake.drain()
        c['cleaning_dispatch']._drain()
        c['refill_service'].runtime.auto.drain()
        result=c['cleaning_reader'].get_current_result_set(self.actor,tid)
        self.assertEqual((result['accepted_count'],result['gap']),(2,0))
        delivery=c['delivery_service']
        package,_=delivery.prepare(self.actor,tid,expected_task_version=result['as_of_task_version'],result_set_digest=result['result_set_digest'])
        self.assertEqual(package['manifest']['schema'],'whole-video-delivery@2')
        delivery.publish(self.actor,tid,package_id=package['package_id'],manifest_digest=package['manifest_digest'],
            expected_task_version=package['current_version'],action='confirm')
        cid=package['manifest']['members'][0]['candidate_id']
        with delivery.open_original(self.actor,tid,cid,source_digest=package['source_digest']) as (file,metadata,guard):
            self.assertTrue(file.path.read_bytes().startswith(b'pass-'));guard()

    def start_window(self, tid):
        self.components['search_dispatch'].dispatch_task(tid)
        self.components['first_search'].tick(self.actor,tid,max_requests=1)

    def drain_workflow(self):
        c=self.components
        for _ in range(3):
            c['video_initial_intake'].drain();c['cleaning_dispatch']._drain()
            c['refill_service'].runtime.auto.drain();c['refill_service'].runtime.auto_intake.drain()

    def manual_search(self,tid,batch_size=1):
        first=self.components['first_search'];preview=first.preview(self.actor,tid)
        choices=[{'branch_id':r['branch_id'],'binding_id':r['options'][0]['binding_id'],'batch_size':batch_size} for r in preview['routes']]
        proposal=first.prepare(self.actor,tid,routing_digest=preview['routing_digest'],choices=choices)
        first.confirm(self.actor,tid,proposal_digest=proposal['proposal_digest'],
            expected_task_version=proposal['expected_task_version'],action='confirm',key='manual-search')
        return first

    def test_fixed_input_manual_search_validation_and_partial_delivery(self):
        tid=self.create(auto=False,fixed=True)
        self.components['search_dispatch'].dispatch_task(tid)
        self.assertEqual(self.tasks.search_dispatch_result(tid)['result']['status'],'manual')
        self.assertIsNone(self.components['first_search'].repository.get_current(self.actor,tid))
        first=self.manual_search(tid);first.tick(self.actor,tid,max_requests=1)
        progress=first.progress(self.actor,tid)
        self.assertTrue(progress['initial_window_complete']);self.assertFalse(progress['pagination_stopped'])
        self.assertIsNone(self.tasks.get(self.actor,tid).active_cleaning_operation_id)
        manifest=first.preview_manifest(self.actor,tid)
        manifest,_=first.confirm_manifest(self.actor,tid,proposal_digest=manifest['proposal_digest'],
            expected_task_version=manifest['expected_task_version'],key='manual-manifest',action='confirm')
        c=self.components;proposal=c['cleaning_service'].prepare(self.actor,tid,manifest['manifest_ref'])
        c['cleaning_service'].confirm(self.actor,tid,proposal_digest=proposal['proposal_digest'],
            expected_task_version=proposal['expected_task_version'],key='manual-clean',action='confirm')
        c['cleaning_dispatch']._drain();self.drain_workflow()
        result=c['cleaning_reader'].get_current_result_set(self.actor,tid)
        self.assertEqual((result['accepted_count'],result['gap']),(1,1))
        self.assertEqual(result['stop_reason'],'INPUT_SCOPE_COMPLETE')
        self.assertEqual(self.tasks.get(self.actor,tid).run_status.value,'AWAITING_INPUT')
        from dsh_retrieval.adapters.task_storage.multi_rule import SQLiteMultiRuleApprovalRepository
        reopened=SQLiteMultiRuleApprovalRepository(self.tasks.path,projects=self.service.projects,authorization=self.resources)
        self.assertEqual(reopened.get_current_result_set(self.actor,tid)['result_set_digest'],result['result_set_digest'])
        self.assertEqual(self.tasks.search_dispatch_result(tid)['result']['video_intake']['status'],'manual')
        delivery=c['delivery_service'];package,_=delivery.prepare(self.actor,tid,
            expected_task_version=result['as_of_task_version'],result_set_digest=result['result_set_digest'])
        self.assertEqual(package['manifest']['completion'],'partial')
        delivery.publish(self.actor,tid,package_id=package['package_id'],manifest_digest=package['manifest_digest'],
            expected_task_version=package['current_version'],action='confirm')
        self.assertEqual(self.tasks.get(self.actor,tid).run_status.value,'PARTIAL')
        self.assertEqual(first.progress(self.actor,tid)['members'][0]['committed_pages'],1)
        with self.assertRaises(ServiceError):first.tick(self.actor,tid,max_requests=1)

    def test_manual_routes_filter_unverified_indexes_and_allow_budgeted_subset(self):
        from dataclasses import replace
        second=replace(self.binding,binding_id='unverified-index')
        self.resources.libraries=lambda *a:(Library('videos','whole video source','video',(self.binding,second)),)
        self.hooks['capabilities']=lambda app:tuple(TextQueryCapability(b,'video','trusted-filter',8) for b in (self.binding,second))
        tid=self.create(auto=False,fixed=True,limits={'max_rounds':3,'max_vlm_calls':2,'no_progress_rounds':2})
        preview=self.components['first_search'].preview(self.actor,tid)
        self.assertEqual([o['binding_id'] for r in preview['routes'] for o in r['options']],['video-index'])
        self.manual_search(tid)

    def test_budget_cannot_confirm_a_video_task_without_one_candidate_check(self):
        body=video_body();body['query_spec']['refill_limits']={'max_rounds':3,'max_vlm_calls':1,'no_progress_rounds':2}
        draft,_=self.service.save_draft(self.actor,'demo','no-budget',body)
        self.assertEqual(draft['video_summary']['execution']['reason_code'],'VIDEO_CALL_BUDGET')
        with self.assertRaises(ServiceError) as error:
            self.service.create_task(self.actor,'no-budget',{'draft_id':draft['draft_id'],'expected_draft_version':1,
                'proposal_digest':draft['proposal_digest'],'action':'confirm'})
        self.assertEqual(error.exception.code,'VIDEO_CALL_BUDGET')
        self.assertEqual(self.tasks.list_task_read_page(self.actor,'demo',limit=20),())

    def test_manual_search_cannot_exceed_confirmed_video_call_budget(self):
        tid=self.create(auto=False,fixed=True,limits={'max_rounds':3,'max_vlm_calls':2,'no_progress_rounds':2})
        with self.assertRaises(ServiceError) as error:self.manual_search(tid,batch_size=2)
        self.assertEqual(error.exception.code,'VIDEO_CALL_BUDGET')
        self.assertIsNone(self.components['first_search'].repository.get_current(self.actor,tid))

    def test_extra_pages_cannot_bypass_manual_video_cleaning_budget(self):
        tid=self.create(auto=False,fixed=True,limits={'max_rounds':3,'max_vlm_calls':2,'no_progress_rounds':2})
        first=self.manual_search(tid)
        first.tick(self.actor,tid,max_requests=1);first.tick(self.actor,tid,max_requests=1)
        manifest=first.preview_manifest(self.actor,tid)
        manifest,_=first.confirm_manifest(self.actor,tid,proposal_digest=manifest['proposal_digest'],
            expected_task_version=manifest['expected_task_version'],key='extra-manifest',action='confirm')
        with self.assertRaises(ServiceError) as error:
            self.components['cleaning_service'].prepare(self.actor,tid,manifest['manifest_ref'])
        self.assertEqual(error.exception.code,'VIDEO_CALL_BUDGET')
        self.assertIsNone(self.tasks.get(self.actor,tid).active_cleaning_operation_id)

    def test_large_video_uses_file_worker_and_small_reference_package(self):
        self.content['video:1']=b'pass'+b'x'*(11*1024**2);self.file_paths=[]
        tid=self.create();self.start_window(tid);self.drain_workflow()
        self.assertTrue(self.file_paths);self.assertTrue(all(not p.exists() for p in self.file_paths))
        c=self.components;result=c['cleaning_reader'].get_current_result_set(self.actor,tid)
        self.assertEqual(result['accepted_count'],2)
        delivery=c['delivery_service'];package,_=delivery.prepare(self.actor,tid,
            expected_task_version=result['as_of_task_version'],result_set_digest=result['result_set_digest'])
        self.assertEqual(package['manifest']['schema'],'whole-video-delivery@2')
        published,_=delivery.publish(self.actor,tid,package_id=package['package_id'],
            manifest_digest=package['manifest_digest'],expected_task_version=package['current_version'],action='confirm')
        payload=delivery.download(self.actor,tid,package['package_id'])
        self.assertLess(len(payload),100000)

    def test_initial_approval_receipt_recovers_after_failure(self):
        from unittest.mock import patch
        tid=self.create();self.start_window(tid)
        c=self.components
        with patch.object(c['cleaning_service'],'confirm',side_effect=ServiceError('DEPENDENCY_UNAVAILABLE','test',503)):
            c['video_initial_intake'].drain()
        receipt=self.tasks.search_dispatch_result(tid)['result']['video_intake']
        self.assertEqual(receipt['status'],'prepared')
        self.assertIsNone(self.tasks.get(self.actor,tid).active_cleaning_operation_id)
        self.drain_workflow()
        result=c['cleaning_reader'].get_current_result_set(self.actor,tid)
        self.assertEqual(result['accepted_count'],2)
        self.assertEqual(self.tasks.search_dispatch_result(tid)['result']['video_intake']['status'],'delivered')

    def test_budget_includes_initial_video_checks(self):
        tid=self.create(limits={'max_rounds':3,'max_vlm_calls':3,'no_progress_rounds':2})
        self.start_window(tid);self.drain_workflow()
        c=self.components;result=c['cleaning_reader'].get_current_result_set(self.actor,tid)
        self.assertEqual(result['accepted_count'],1)
        self.assertEqual(result['stop_reason'],'VLM_CALL_LIMIT_REACHED')
        evidence=c['refill_service'].runtime.reservations.execution_evidence(self.actor,tid)
        self.assertEqual(evidence['vlm_calls'],2)

    def test_duplicate_refill_does_not_count_another_original_as_new_content(self):
        self.content['video:2']=self.content['video:1']
        tid=self.create();self.start_window(tid);self.drain_workflow()
        result=self.components['cleaning_reader'].get_current_result_set(self.actor,tid)
        self.assertEqual((result['accepted_count'],result['gap'],result['duplicate_count']),(1,1,1))
        self.assertEqual(result['stop_reason'],'SEARCH_STRATEGIES_EXHAUSTED')

    def test_missing_source_registration_does_not_create_a_task(self):
        self.hooks['whole_video_bindings']=lambda app:()
        body=video_body();draft,_=self.service.save_draft(self.actor,'demo','blocked',body)
        self.assertFalse(draft['video_summary']['execution']['available'])
        with self.assertRaises(ServiceError):
            self.service.create_task(self.actor,'blocked',{'draft_id':draft['draft_id'],'expected_draft_version':1,
                'proposal_digest':draft['proposal_digest'],'action':'confirm'})
        self.assertEqual(self.tasks.list_task_read_page(self.actor,'demo',limit=20),())
