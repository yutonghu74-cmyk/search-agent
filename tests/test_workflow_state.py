from copy import deepcopy
import json
from http.server import ThreadingHTTPServer
import threading
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from unittest.mock import patch
from unittest.mock import Mock

from dsh_retrieval.progress_models import build_task_progress
from dsh_retrieval.workflow_state import binding, project_workflow, status_question_topic


def status_context(*, stopped=True, confirmed=False, cleaning=False):
    task = {'task_id': 'task', 'confirmed_revision': 1, 'task_version': 2, 'run_generation': 1,
            'run_status': 'RUNNING', 'stage': 'CLEANING' if cleaning else 'RETRIEVING'}
    proposal = {'query_spec': {'target_count': 10},
                'node_modes': dict.fromkeys(('expansion', 'retrieval', 'cleaning', 'refill', 'packaging'), 'manual')}
    progress = build_task_progress(task, proposal).to_view()
    search = {**binding(task), 'status': 'approved', 'pagination_stopped': stopped, 'members': []}
    manifest = {**binding(task), 'confirmed': True, 'candidate_count': 3} if confirmed else None
    return {'workflow': project_workflow(task, progress, search=search, manifest=manifest,
                                        cleaning={'status': 'running'} if cleaning else None)}


class WorkflowStateTest(unittest.TestCase):
    def setUp(self):
        self.task = {'task_id': 'task', 'confirmed_revision': 1, 'task_version': 2, 'run_generation': 1,
                     'run_status': 'RUNNING', 'stage': 'RETRIEVING'}
        proposal = {'query_spec': {'target_count': 10},
                    'node_modes': dict.fromkeys(('expansion', 'retrieval', 'cleaning', 'refill', 'packaging'), 'manual')}
        self.progress = build_task_progress(self.task, proposal).to_view()
        self.search = {**binding(self.task), 'status': 'approved', 'pagination_stopped': True, 'members': []}

    def test_finished_search_is_pending_confirmation_not_cleaning_or_delivery_success(self):
        value = project_workflow(self.task, self.progress, search=self.search)
        self.assertEqual(value['state'], 'awaiting_candidates')
        self.assertEqual(value['next_action']['id'], 'confirm_candidates')
        self.assertIsNone(value['counts']['accepted'])
        self.assertIsNone(value['counts']['gap'])
        self.assertEqual(value['nodes'][1]['display_status'], '检索已结束')
        self.assertNotIn('交付已发布', value['status_reply'])
        self.assertNotIn('网络', value['slow_reply'])
        self.assertNotIn('数据量', value['slow_reply'])

    def test_initial_window_can_await_owner_without_stopping_future_refill(self):
        search = {**self.search, 'pagination_stopped': False, 'initial_window_complete': True}
        value = project_workflow(self.task, self.progress, search=search)
        self.assertEqual(value['state'], 'awaiting_candidates')
        self.assertEqual(value['nodes'][1]['display_status'], '首轮候选已就绪')

    def test_manifest_must_match_exact_current_version(self):
        for key in ('generation', 'task_version', 'revision', 'task_id'):
            with self.subTest(key=key):
                manifest = {**binding(self.task), 'confirmed': True, 'candidate_count': 4, key: 'stale'}
                value = project_workflow(self.task, self.progress, search=self.search, manifest=manifest)
                self.assertEqual(value['state'], 'awaiting_candidates')
                self.assertEqual(value['quality'], 'partial')
        manifest = {**binding(self.task), 'confirmed': True, 'candidate_count': 4}
        value = project_workflow(self.task, self.progress, search=self.search, manifest=manifest)
        self.assertEqual(value['state'], 'awaiting_cleaning')
        self.assertEqual(value['next_action']['id'], 'prepare_cleaning')
        self.assertIsNone(value['counts']['accepted'])

    def test_failed_job_does_not_become_success_when_pagination_has_stopped(self):
        value = project_workflow(self.task, self.progress, search={**self.search, 'job_status': 'failed'})
        self.assertEqual(value['state'], 'retrieval_failed')
        self.assertEqual(value['nodes'][1]['node_status'], 'failed')
        self.assertNotIn('confirm_candidates', [row['id'] for row in value['available_actions']])

    def test_superseded_job_never_offers_candidate_confirmation(self):
        for stage in ('RETRIEVING', 'REFILL'):
            value = project_workflow({**self.task, 'stage': stage}, self.progress,
                                     search={**self.search, 'job_status': 'superseded'})
            self.assertNotIn('confirm_candidates', [row['id'] for row in value['available_actions']])
            self.assertEqual(value['nodes'][1]['display_status'], '已替代')

    def test_cleaning_checkpoint_failure_and_pause_are_authoritative(self):
        for status, expected in (('queued', 'cleaning'), ('running', 'cleaning'), ('retry_wait', 'cleaning'), ('failed', 'cleaning_failed')):
            value = project_workflow(self.task, self.progress, cleaning={'status': status})
            self.assertEqual(value['state'], expected)
            self.assertEqual(value['next_action']['id'], 'view_cleaning')
        for run_status in ('PAUSED', 'CANCELLED'):
            value = project_workflow({**self.task, 'run_status': run_status}, self.progress, search=self.search)
            self.assertEqual(value['state'], run_status.lower())
            self.assertTrue(all(row['kind'] == 'view' for row in value['available_actions']))

    def test_counts_and_delivery_never_come_from_target_or_raw_hits(self):
        progress = deepcopy(self.progress)
        progress['counts'].update(returned=200, accepted=0, gap=10)
        value = project_workflow(self.task, progress)
        self.assertIn('入选 0 张', value['summary'])
        self.assertIn('缺口 10 张', value['summary'])
        self.assertNotIn('view_delivery', [row['id'] for row in value['available_actions']])
        progress['nodes'][-1].update(node_status='succeeded', gate_status='approved')
        value = project_workflow({**self.task, 'run_status': 'PARTIAL'}, progress)
        self.assertEqual(value['state'], 'published')
        self.assertIn('部分交付已发布', value['summary'])

    def test_unavailable_sources_or_permissions_do_not_offer_a_write(self):
        value = project_workflow(self.task, self.progress, search={**self.search, 'generation': 99})
        self.assertEqual(value['state'], 'unknown')
        self.assertEqual(value['next_action']['id'], 'refresh')
        value = project_workflow(self.task, self.progress, search=self.search, capabilities={'can_approve': False})
        self.assertTrue(all(row['kind'] != 'confirm' for row in value['available_actions']))
        manifest = {**binding(self.task), 'confirmed': True, 'candidate_count': 4}
        value = project_workflow(self.task, self.progress, manifest=manifest, capabilities={'cleaning': False})
        self.assertNotIn('prepare_cleaning', [row['id'] for row in value['available_actions']])
        self.assertIn('尚未配置', value['summary'])

    def test_status_detection_does_not_eat_actual_edits(self):
        for text in ('为什么还没完成', '我现在该点哪里', '有结果了吗', '清洗卡住了'):
            self.assertIsNotNone(status_question_topic(text), text)
        for text in ('下一步增加无水印检测', '不要闭眼的', '把清洗条件改成高清', '找10张慢跑的狗', '想要慢动作的照片'):
            self.assertIsNone(status_question_topic(text), text)


class WorkspaceApiTest(unittest.TestCase):
    def test_status_reuses_unchanged_clock_and_never_scans_revision_history(self):
        from dsh_retrieval.task_events import TaskEventFeed
        f = self.fixture
        f.local.auto_start(f.actor, f.task_id, key='timed-search')
        f.wait_stopped(f.local, f.task_id)
        self.handler.task_events = TaskEventFeed(f.ledger)
        task = f.tasks.get_task(f.actor, f.task_id)
        body = {'text': '接下来做什么', 'llm_id': '', 'expected_revision': task['confirmed_revision'],
                'expected_task_version': task['task_version']}
        with (patch.object(f.tasks, 'list_revisions', side_effect=AssertionError('history scan')),
              patch.object(f.local, 'progress', wraps=f.local.progress) as reads):
            status, answer = self.call('/conversation-turn', body, key='clock-stable')
        self.assertEqual(status, 200, answer)
        self.assertEqual(reads.call_count, 1)
        self.assertEqual(answer['workspace']['workflow'], answer['workflow'])
        self.assertEqual(answer['reply'], answer['workflow']['status_reply'])
        self.assertNotIn('workspace_refresh', answer['timings']['stages'])
        self.assertNotIn('model_initial', answer['timings']['stages'])
        self.assertEqual(answer['timings']['stages']['workspace_read']['calls'], 1)
        self.assertIn('Server-Timing', self.last_headers)
        status, replay = self.call('/conversation-turn', body, key='clock-stable')
        self.assertEqual(status, 200, replay)
        self.assertTrue(replay['command_replayed'])
        self.assertEqual(replay['timings'], answer['timings'])
        self.assertNotIn('Server-Timing', self.last_headers)
        with patch.object(self.handler.task_events, 'task_mutation_sequence', side_effect=[1, 1, 2, 2, 2]), \
             patch.object(f.local, 'progress', wraps=f.local.progress) as reads:
            status, answer = self.call('/conversation-turn', body, key='clock-moved')
        self.assertEqual(status, 200, answer)
        self.assertEqual(reads.call_count, 2)
        self.assertEqual(answer['timings']['stages']['workspace_refresh']['calls'], 1)
        with patch.object(self.handler.task_events, 'task_mutation_sequence', return_value=None), \
             patch.object(f.local, 'progress', wraps=f.local.progress) as reads:
            status, answer = self.call('/conversation-turn', body, key='clock-unavailable')
        self.assertEqual(status, 200, answer)
        self.assertEqual(reads.call_count, 2)

    def setUp(self):
        from test_first_search_local import FirstSearchLocalTest
        from dsh_retrieval.api import create_handler
        self.fixture = FirstSearchLocalTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.handler = create_handler(self.fixture.tasks, first_search=self.fixture.local)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), self.handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        def close():
            self.server.shutdown(); self.server.server_close(); thread.join()
        self.addCleanup(close)
        self.base = f'http://127.0.0.1:{self.server.server_port}/v1/retrieval/tasks/{self.fixture.task_id}'

    def call(self, suffix, body=None, *, owner='user-a', key='status'):
        request = Request(self.base + suffix, data=json.dumps(body).encode() if body is not None else None,
            headers={'Content-Type': 'application/json', 'X-Tenant-Id': 'tenant-a', 'X-User-Id': owner,
                     'Idempotency-Key': key})
        try:
            with urlopen(request) as response:
                self.last_headers = dict(response.headers)
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    def test_workspace_and_chat_share_read_only_search_and_confirmation_facts(self):
        f = self.fixture
        f.local.auto_start(f.actor, f.task_id, key='workspace-search')
        f.wait_stopped(f.local, f.task_id)
        with patch.object(f.local, 'preview_manifest', side_effect=AssertionError('status read mapped candidates')):
            status, workspace = self.call('/workspace')
            self.assertEqual(status, 200, workspace)
            self.assertEqual(workspace['workflow']['state'], 'awaiting_candidates')
            self.assertIsNone(workspace['progress']['counts']['accepted'])
            status, answer = self.call('/conversation-turn', {'text': '接下来做什么', 'llm_id': '',
                'expected_revision': workspace['confirmed_revision'], 'expected_task_version': workspace['task_version']})
            self.assertEqual(status, 200, answer)
            self.assertTrue(answer['status_requested'])
            self.assertEqual(answer['reply'], workspace['workflow']['status_reply'])
            self.assertEqual(answer['workflow']['state_digest'], workspace['workflow']['state_digest'])
        preview = f.local.preview_manifest(f.actor, f.task_id)
        f.local.confirm_manifest(f.actor, f.task_id, proposal_digest=preview['proposal_digest'],
            expected_task_version=preview['expected_task_version'], key='workspace-confirm', action='confirm')
        status, workspace = self.call('/workspace')
        self.assertEqual(status, 200, workspace)
        self.assertEqual(workspace['workflow']['state'], 'awaiting_cleaning')
        self.assertIsNotNone(workspace['manifest'])
        self.assertEqual(workspace['manifest']['candidate_count'], preview['candidate_count'])
        self.assertIsNone(workspace['progress']['counts']['accepted'])
        status, denied = self.call('/workspace', owner='another')
        self.assertNotEqual(status, 200, denied)

    def test_model_status_routing_refreshes_facts_after_the_model_returns(self):
        from dsh_retrieval.model_runtime import ModelRuntime, ModelProfile
        from dsh_retrieval.intent_model import ModelConfig
        from test_model_runtime import Transport
        f = self.fixture
        f.local.auto_start(f.actor, f.task_id, key='status-search')
        ended = f.wait_stopped(f.local, f.task_id)
        transport = Transport([{'kind': 'status', 'target_count': None, 'requested_terms_per_group': None}])
        self.handler.model_runtime = ModelRuntime([
            ModelProfile('text', 'llm', ModelConfig('fixture', 'text', 'https://fixture.invalid/v1', 'secret'))], opener=transport)
        calls = []
        def read(*args):
            calls.append(1)
            return {**ended, 'pagination_stopped': len(calls) > 1, 'initial_window_complete': len(calls) > 1}
        task = f.tasks.get_task(f.actor, f.task_id)
        with patch.object(f.local, 'progress', side_effect=read):
            status, answer = self.call('/conversation-turn', {'text': '嗯，这个事情呢？', 'llm_id': 'text',
                'expected_revision': task['confirmed_revision'], 'expected_task_version': task['task_version']})
        self.assertEqual(status, 200, answer)
        self.assertGreaterEqual(len(calls), 2)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(answer['workflow']['state'], 'awaiting_candidates')
        self.assertIn('检索已结束', answer['reply'])
        self.assertEqual(answer['timings']['stages']['model_initial']['calls'], 1)
        self.assertEqual(answer['timings']['stages']['workspace_refresh']['calls'], 1)
        self.assertEqual(answer['timings']['counters']['model_http_attempts'], 1)


class WorkspaceConsistencyTest(unittest.TestCase):
    def test_projected_summary_avoids_full_snapshot_and_downstream_search_reads(self):
        from dsh_retrieval.api import create_handler
        from types import SimpleNamespace
        task = {'task_id': 'task', 'project_id': 'demo', 'dimension_id': 'default', 'confirmed_revision': 1,
                'task_version': 7, 'run_generation': 1, 'run_status': 'RUNNING', 'stage': 'CLEANING_FINISHED'}
        progress = status_context(cleaning=True)['workflow']
        progress = {'task_id': 'task', 'revision': 1, 'task_version': 7, 'run_generation': 1,
                    'nodes': progress['nodes'], 'counts': {**progress['counts'], 'accepted': 3, 'gap': 7}}
        progress['nodes'][1].update(node_status='succeeded')
        progress['nodes'][2].update(node_status='succeeded')
        summary = {**task, 'progress': progress}
        service = SimpleNamespace(task_read_summary=Mock(return_value=summary), projects=Mock(),
                                  repository=SimpleNamespace(get_task_read_summary=lambda: None))
        search = SimpleNamespace(progress=Mock(side_effect=AssertionError('stale downstream search read')),
                                 preview_manifest=Mock(side_effect=AssertionError('mapping on status read')))
        handler = create_handler(service, first_search=search)
        instance = handler.__new__(handler)
        with patch.object(handler, '_task_snapshot', side_effect=AssertionError('full snapshot restore')):
            workspace = instance._task_workspace(SimpleNamespace(tenant_id='tenant', user_id='owner'), 'task')
        self.assertEqual(workspace['workflow']['state'], 'results_ready')
        search.progress.assert_not_called()
        search.preview_manifest.assert_not_called()

    def test_changing_mutation_clock_is_retried_once_then_fenced(self):
        from dsh_retrieval.api import create_handler
        from types import SimpleNamespace
        from dsh_retrieval.errors import ServiceError
        task = {'task_id': 'task', 'project_id': 'demo', 'dimension_id': 'default', 'confirmed_revision': 1,
                'task_version': 2, 'run_generation': 1, 'run_status': 'RUNNING', 'stage': 'RETRIEVING'}
        workflow = status_context()['workflow']
        summary = {**task, 'progress': {'task_id': 'task', 'revision': 1, 'task_version': 2, 'run_generation': 1,
                   'nodes': workflow['nodes'], 'counts': workflow['counts']}}
        service = SimpleNamespace(task_read_summary=Mock(return_value=summary), projects=Mock(),
                                  repository=SimpleNamespace(get_task_read_summary=lambda: None))
        events = SimpleNamespace(task_mutation_sequence=Mock(side_effect=[1, 2, 2, 2]))
        handler = create_handler(service, task_events=events)
        instance = handler.__new__(handler)
        actor = SimpleNamespace(tenant_id='tenant', user_id='owner')
        result = instance._task_workspace(actor, 'task')
        self.assertEqual(result['mutation_sequence'], 2)
        self.assertEqual(service.task_read_summary.call_count, 4)
        events.task_mutation_sequence.side_effect = [1, 2, 3, 4]
        with self.assertRaises(ServiceError) as caught:
            instance._task_workspace(actor, 'task')
        self.assertEqual(caught.exception.code, 'REVISION_CONFLICT')
