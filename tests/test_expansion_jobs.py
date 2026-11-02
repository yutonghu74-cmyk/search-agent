import tempfile
import time
import unittest
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

from dsh_retrieval.errors import ServiceError
from dsh_retrieval.expansion_jobs import ExpansionJobs
from dsh_retrieval.expansion_models import preview_edited_groups
from dsh_retrieval.models import Actor
from dsh_retrieval.model_runtime import ModelRuntime
from dsh_retrieval.api import create_handler
from dsh_retrieval.adapters.projects import LocalProjectAccess
from dsh_retrieval.service import InMemoryTaskRepository, TaskService


class Projects:
    def require_access(self, actor, project, dimension, action):
        if actor.user_id != 'owner' or project != 'demo':
            raise ServiceError('FORBIDDEN', 'No access', 403)


class Dialogue:
    def snapshot(self, actor, project, session):
        if actor.user_id != 'owner' or session != 'dlg_one':
            raise ServiceError('DIALOGUE_NOT_FOUND', 'Missing', 404)
        return {'version': 3, 'stage': 'terms'}


class TaskServiceStub:
    def get_task(self, actor, task_id):
        if actor.user_id != 'owner' or task_id != 'rt_' + 'a' * 32:
            raise ServiceError('TASK_NOT_FOUND', 'Missing task', 404)
        return {'project_id': 'demo', 'task_version': 7, 'confirmed_revision': 2}


class Runtime:
    def __init__(self):
        self.calls = []
        self.fail_once = True

    def binding(self, llm, vlm):
        if (llm, vlm) != ('llm', 'vlm'):
            raise ServiceError('MODEL_NOT_FOUND', 'Missing model', 404)
        return {'llm': llm, 'vlm': vlm}

    def expand_chunk(self, raw, llm, vlm, *, limit, label=None, existing_terms=None,
                     feedback_text=None, current_groups=None):
        self.calls.append((label, len(existing_terms or []), limit))
        if label and self.fail_once and len(existing_terms) >= 10:
            self.fail_once = False
            raise ServiceError('DSH_TIMEOUT', 'Batch timed out', 504)
        if label:
            groups = [{'primary_label': label,
                       'auxiliary_terms': [f'{label}场景{i}' for i in range(len(existing_terms), len(existing_terms) + limit)]}]
        else:
            groups = [{'primary_label': '猫', 'auxiliary_terms': [f'猫场景{i}' for i in range(limit)]}]
        return preview_edited_groups(None, groups)

    def bind(self, result, binding, *, generated=False):
        return result


class ExpansionJobsTest(unittest.TestCase):
    def wait_for(self, jobs, status):
        for _ in range(100):
            value = jobs.get(Actor('tenant', 'owner'), 'demo', 'dlg_one')
            if value['status'] == status:
                return value
            time.sleep(.02)
        self.fail(f'job did not reach {status}')

    def test_quoted_complete_replacement_skips_generation_and_stays_provisional(self):
        runtime = Runtime()
        jobs = ExpansionJobs(None, runtime, Dialogue(), Projects(), TaskServiceStub())
        actor = Actor('tenant', 'owner')
        task_id = 'rt_' + 'a' * 32
        feedback = '检索词减少到两个，保留“猫”和“猫在草地上”，其他需求和1张目标不变。'
        body = {'expected_task_version': 7, 'expected_revision': 2,
                'raw_request': '找1张猫图片', 'dimension_id': 'default', 'target_count': 1,
                'model_selection': {'llm': 'llm', 'vlm': 'vlm'},
                'current_groups': [{'primary_label': '猫', 'auxiliary_terms': ['猫在室内', '猫在树上']}],
                'feedback_text': feedback, 'confirmation_feedback_text': feedback,
                'current_target_count': 1, 'requested_terms_per_group': 1}
        first = jobs.start_task(actor, task_id, body)
        for _ in range(100):
            done = jobs.get(actor, 'demo', 'task:' + task_id, first['job_id'])
            if done['status'] == 'completed':
                break
            time.sleep(.02)
        self.assertEqual(done['status'], 'completed')
        self.assertEqual(done['result']['plan']['groups'][0]['auxiliary_terms'], ['猫在草地上'])
        self.assertFalse(runtime.calls)
        self.assertIn('待确认', done['result']['note'])
        self.assertEqual(jobs.start_task(actor, task_id, body)['job_id'], first['job_id'])

    def test_failed_batch_resumes_without_regenerating_prior_batches_and_survives_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'dialogue.sqlite3'
            runtime = Runtime()
            jobs = ExpansionJobs(path, runtime, Dialogue(), Projects())
            actor = Actor('tenant', 'owner')
            body = {'dialogue_session_id': 'dlg_one', 'dialogue_version': 3,
                    'raw_request': '每类生成25个检索词，找猫图片', 'dimension_id': 'default',
                    'target_count': 20, 'model_selection': {'llm': 'llm', 'vlm': 'vlm'}}
            first = jobs.start(actor, 'demo', body)
            self.assertEqual(first['status'], 'queued')
            self.assertEqual(jobs.start(actor, 'demo', body)['job_id'], first['job_id'])
            failed = self.wait_for(jobs, 'failed')
            self.assertEqual(failed['completed_terms'], 18)
            self.assertEqual(failed['failed_batch']['offset'], 18)
            self.assertEqual(failed['error']['code'], 'DSH_TIMEOUT')
            jobs.retry(actor, 'demo', 'dlg_one', first['job_id'])
            done = self.wait_for(jobs, 'completed')
            self.assertEqual(done['completed_terms'], 25)
            self.assertEqual(len(done['result']['plan']['groups'][0]['auxiliary_terms']), 25)
            self.assertEqual(runtime.calls, [(None, 0, 8), ('猫', 8, 10), ('猫', 18, 7), ('猫', 18, 7)])
            reopened = ExpansionJobs(path, runtime, Dialogue(), Projects())
            self.assertEqual(reopened.get(actor, 'demo', 'dlg_one')['status'], 'completed')
            with self.assertRaises(ServiceError):
                reopened.get(Actor('tenant', 'other'), 'demo', 'dlg_one')

    def test_count_increase_keeps_existing_terms_and_only_generates_missing_batches(self):
        runtime = Runtime()
        runtime.fail_once = False
        jobs = ExpansionJobs(None, runtime, Dialogue(), Projects())
        actor = Actor('tenant', 'owner')
        body = {'dialogue_session_id': 'dlg_one', 'dialogue_version': 3,
                'raw_request': '找猫图片', 'dimension_id': 'default', 'target_count': 20,
                'model_selection': {'llm': 'llm', 'vlm': 'vlm'},
                'current_groups': [{'primary_label': '猫', 'auxiliary_terms': ['猫场景0', '猫场景1']}],
                'feedback_text': '检索词增加到 12 个', 'current_target_count': 20,
                'count_edits': [{'target': 'terms', 'labels': ['猫'], 'operation': 'set', 'value': 12}]}
        jobs.start(actor, 'demo', body)
        done = self.wait_for(jobs, 'completed')
        self.assertEqual(done['target_terms'], 12)
        self.assertEqual(done['result']['plan']['groups'][0]['auxiliary_terms'][:2], ['猫场景0', '猫场景1'])
        self.assertEqual(runtime.calls, [('猫', 2, 10)])

    def test_explicit_reduction_replaces_hundred_term_preview_with_two_term_proposal(self):
        runtime = Runtime()
        runtime.fail_once = False
        jobs = ExpansionJobs(None, runtime, Dialogue(), Projects())
        actor = Actor('tenant', 'owner')
        old_terms = [f'猫场景{i}' for i in range(100)]
        jobs.start(actor, 'demo', {
            'dialogue_session_id': 'dlg_one', 'dialogue_version': 3,
            'raw_request': '找猫图片', 'dimension_id': 'default', 'target_count': 20,
            'model_selection': {'llm': 'llm', 'vlm': 'vlm'},
            'current_groups': [{'primary_label': '猫', 'auxiliary_terms': old_terms}],
            'feedback_text': '检索词减少到两个', 'current_target_count': 20,
            'requested_terms_per_group': 2})
        done = self.wait_for(jobs, 'completed')
        self.assertEqual(done['target_terms'], 2)
        self.assertEqual(done['completed_terms'], 2)
        self.assertEqual(len(done['result']['plan']['groups'][0]['auxiliary_terms']), 2)
        self.assertEqual(len(old_terms), 100)
        self.assertEqual(runtime.calls, [(None, 0, 8)])

    def test_task_job_rejects_stale_hundred_target_after_latest_two_word_feedback(self):
        runtime = Runtime()
        runtime.fail_once = False
        jobs = ExpansionJobs(None, runtime, Dialogue(), Projects(), TaskServiceStub())
        actor = Actor('tenant', 'owner')
        task_id = 'rt_' + 'a' * 32
        body = {'expected_task_version': 7, 'expected_revision': 2,
                'raw_request': '找猫图片', 'dimension_id': 'default', 'target_count': 20,
                'model_selection': {'llm': 'llm', 'vlm': 'vlm'},
                'current_groups': [{'primary_label': '猫',
                                    'auxiliary_terms': [f'猫场景{i}' for i in range(100)]}],
                'feedback_text': '将猫组检索词减少到两个',
                'confirmation_feedback_text': '先扩到100个检索词\n后续调整：检索词减少到两个',
                'current_target_count': 20, 'requested_terms_per_group': 100}
        with self.assertRaises(ServiceError) as caught:
            jobs.start_task(actor, task_id, body)
        self.assertEqual(caught.exception.code, 'INVALID_SPEC')
        self.assertFalse(runtime.calls)
        body['requested_terms_per_group'] = 2
        done_id = jobs.start_task(actor, task_id, body)['job_id']
        for _ in range(100):
            done = jobs.get(actor, 'demo', 'task:' + task_id, done_id)
            if done['status'] == 'completed':
                break
            time.sleep(.02)
        self.assertEqual(done['status'], 'completed')
        self.assertEqual(done['target_terms'], 2)

    def test_count_only_reduction_reuses_existing_terms_without_model_call(self):
        runtime = Runtime()
        jobs = ExpansionJobs(None, runtime, Dialogue(), Projects(), TaskServiceStub())
        actor = Actor('tenant', 'owner')
        task_id = 'rt_' + 'a' * 32
        terms = [f'猫场景{i}' for i in range(100)]
        jobs.start_task(actor, task_id, {
            'expected_task_version': 7, 'expected_revision': 2,
            'raw_request': '找猫图片', 'dimension_id': 'default', 'target_count': 20,
            'model_selection': {'llm': 'llm', 'vlm': 'vlm'},
            'current_groups': [{'primary_label': '猫', 'auxiliary_terms': terms}],
            'feedback_text': '仅将检索词减少到两个，其他条件不变',
            'confirmation_feedback_text': '检索词减少到两个',
            'current_target_count': 20, 'requested_terms_per_group': 2,
            'term_count_only': True})
        for _ in range(100):
            done = jobs.get(actor, 'demo', 'task:' + task_id)
            if done['status'] == 'completed':
                break
            time.sleep(.02)
        self.assertEqual(done['status'], 'completed')
        self.assertEqual(done['result']['plan']['groups'][0]['auxiliary_terms'], terms[:2])
        self.assertEqual(done['source_groups'][0]['auxiliary_terms'], terms)
        self.assertEqual(runtime.calls, [])
        self.assertIn('未调用扩词模型', done['result']['note'])
        revised = jobs.start_task(actor, task_id, {
            'expected_task_version': 7, 'expected_revision': 2,
            'raw_request': '找猫图片', 'dimension_id': 'default', 'target_count': 20,
            'model_selection': {'llm': 'llm', 'vlm': 'vlm'},
            'current_groups': done['source_groups'],
            'feedback_text': '仅将检索词改为6个，其他条件不变',
            'confirmation_feedback_text': '改为6个',
            'current_target_count': 20, 'requested_terms_per_group': 6,
            'term_count_only': True})
        for _ in range(100):
            revised = jobs.get(actor, 'demo', 'task:' + task_id, revised['job_id'])
            if revised['status'] == 'completed':
                break
            time.sleep(.02)
        self.assertEqual(revised['status'], 'completed')
        self.assertEqual(revised['result']['plan']['groups'][0]['auxiliary_terms'], terms[:6])
        self.assertEqual(runtime.calls, [])

    def test_seed_is_checkpointed_before_first_followup_batch(self):
        runtime = Runtime()
        runtime.fail_once = False
        original = runtime.expand_chunk
        failed = [False]

        def first_batch_fails(*args, **kwargs):
            if kwargs.get('label') == '猫' and not failed[0]:
                failed[0] = True
                raise ServiceError('DSH_TIMEOUT', 'First batch timed out', 504)
            return original(*args, **kwargs)

        runtime.expand_chunk = first_batch_fails
        jobs = ExpansionJobs(None, runtime, Dialogue(), Projects())
        actor = Actor('tenant', 'owner')
        body = {'dialogue_session_id': 'dlg_one', 'dialogue_version': 3,
                'raw_request': '每类生成12个检索词，找猫图片', 'dimension_id': 'default',
                'target_count': 20, 'model_selection': {'llm': 'llm', 'vlm': 'vlm'}}
        started = jobs.start(actor, 'demo', body)
        self.assertEqual(self.wait_for(jobs, 'failed')['completed_terms'], 8)
        jobs.retry(actor, 'demo', 'dlg_one', started['job_id'])
        self.assertEqual(self.wait_for(jobs, 'completed')['completed_terms'], 12)
        self.assertEqual(sum(1 for label, _, _ in runtime.calls if label is None), 1)

    def test_recover_requeues_interrupted_running_job_from_saved_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'dialogue.sqlite3'
            runtime = Runtime()
            jobs = ExpansionJobs(path, runtime, Dialogue(), Projects())
            actor = Actor('tenant', 'owner')
            started = jobs.start(actor, 'demo', {
                'dialogue_session_id': 'dlg_one', 'dialogue_version': 3,
                'raw_request': '每类生成25个检索词，找猫图片', 'dimension_id': 'default',
                'target_count': 20, 'model_selection': {'llm': 'llm', 'vlm': 'vlm'}})
            self.wait_for(jobs, 'failed')
            with jobs.lock:
                _, state, _ = jobs._load(started['job_id'])
                state['status'] = 'running'
                jobs._save(started['job_id'], state)
            reopened = ExpansionJobs(path, runtime, Dialogue(), Projects())
            reopened.recover()
            self.assertEqual(self.wait_for(reopened, 'completed')['completed_terms'], 25)
            self.assertEqual(sum(1 for label, _, _ in runtime.calls if label is None), 1)

    def test_hundred_terms_are_generated_in_bounded_batches(self):
        runtime = Runtime()
        runtime.fail_once = False
        jobs = ExpansionJobs(None, runtime, Dialogue(), Projects())
        jobs.start(Actor('tenant', 'owner'), 'demo', {
            'dialogue_session_id': 'dlg_one', 'dialogue_version': 3,
            'raw_request': '每类生成100个检索词，找猫图片', 'dimension_id': 'default',
            'target_count': 20, 'model_selection': {'llm': 'llm', 'vlm': 'vlm'}})
        done = self.wait_for(jobs, 'completed')
        self.assertEqual(done['completed_terms'], 100)
        self.assertEqual(len(done['result']['plan']['groups'][0]['auxiliary_terms']), 100)
        self.assertTrue(all(size <= 10 for _, _, size in runtime.calls))

    def test_confirmed_task_feedback_uses_same_background_job(self):
        runtime = Runtime()
        runtime.fail_once = False
        jobs = ExpansionJobs(None, runtime, Dialogue(), Projects(), TaskServiceStub())
        task_id = 'rt_' + 'a' * 32
        started = jobs.start_task(Actor('tenant', 'owner'), task_id, {
            'expected_task_version': 7, 'expected_revision': 2,
            'raw_request': '找猫图片', 'dimension_id': 'default', 'target_count': 20,
            'model_selection': {'llm': 'llm', 'vlm': 'vlm'},
            'current_groups': [{'primary_label': '猫', 'auxiliary_terms': ['猫场景0', '猫场景1']}],
            'feedback_text': '把猫组调整为100个检索词', 'confirmation_feedback_text': '检索词数量提高到100个',
            'current_target_count': 20,
            'count_edits': [{'target': 'terms', 'labels': ['猫'], 'operation': 'set', 'value': 100}]})
        self.assertEqual(started['task_id'], task_id)
        for _ in range(100):
            done = jobs.get(Actor('tenant', 'owner'), 'demo', 'task:' + task_id)
            if done['status'] == 'completed':
                break
            time.sleep(.02)
        self.assertEqual(done['completed_terms'], 100)
        self.assertEqual(done['confirmation_feedback_text'], '检索词数量提高到100个')
        self.assertEqual(runtime.calls[0], ('猫', 2, 10))

    def test_task_without_old_terms_defers_relative_edit_until_seed_exists(self):
        runtime = Runtime()
        runtime.fail_once = False
        jobs = ExpansionJobs(None, runtime, Dialogue(), Projects(), TaskServiceStub())
        task_id = 'rt_' + 'a' * 32
        jobs.start_task(Actor('tenant', 'owner'), task_id, {
            'expected_task_version': 7, 'expected_revision': 2,
            'raw_request': '找猫图片', 'dimension_id': 'default', 'target_count': 20,
            'model_selection': {'llm': 'llm', 'vlm': 'vlm'},
            'feedback_text': '每组词数翻倍', 'confirmation_feedback_text': '词数翻倍',
            'current_target_count': 20,
            'count_edits': [{'target': 'terms', 'labels': [], 'operation': 'scale', 'value': 2}]})
        for _ in range(100):
            result = jobs.get(Actor('tenant', 'owner'), 'demo', 'task:' + task_id)
            if result['status'] == 'completed':
                break
            time.sleep(.02)
        self.assertEqual(result['completed_terms'], 16)
        self.assertEqual(runtime.calls[0], (None, 0, 8))

    def test_http_start_poll_and_retry_route(self):
        runtime = Runtime()
        service = TaskService(InMemoryTaskRepository(), LocalProjectAccess([
            ('tenant', 'owner', 'demo', 'default')]))
        dialogue = Dialogue()
        jobs = ExpansionJobs(None, runtime, dialogue, service.projects)
        server = ThreadingHTTPServer(('127.0.0.1', 0), create_handler(
            service, model_runtime=runtime, dialogue=dialogue, expansion_jobs=jobs))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            root = f'http://127.0.0.1:{server.server_port}/v1/retrieval/projects/demo/dialogue-sessions/dlg_one/expansion-jobs'
            headers = {'X-Tenant-Id': 'tenant', 'X-User-Id': 'owner', 'Content-Type': 'application/json'}
            body = {'dialogue_session_id': 'dlg_one', 'dialogue_version': 3,
                    'raw_request': '每类生成25个检索词，找猫图片', 'dimension_id': 'default',
                    'target_count': 20, 'model_selection': {'llm': 'llm', 'vlm': 'vlm'}}
            with urlopen(Request(root, headers=headers, data=json.dumps(body).encode(), method='POST')) as response:
                self.assertEqual(response.status, 202)
                started = json.load(response)
            with urlopen(Request(root + '/' + started['job_id'], headers=headers)) as response:
                self.assertIn(json.load(response)['status'], ('queued', 'running', 'failed'))
            self.wait_for(jobs, 'failed')
            with urlopen(Request(root + '/' + started['job_id'] + '/retry', headers=headers,
                                 data=b'{}', method='POST')) as response:
                self.assertEqual(response.status, 202)
            self.assertEqual(self.wait_for(jobs, 'completed')['target_terms'], 25)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_http_confirmed_task_expansion_route(self):
        runtime = Runtime()
        runtime.fail_once = False
        service = TaskServiceStub()
        jobs = ExpansionJobs(None, runtime, Dialogue(), Projects(), service)
        server = ThreadingHTTPServer(('127.0.0.1', 0), create_handler(
            service, model_runtime=runtime, dialogue=Dialogue(), expansion_jobs=jobs))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            root = f'http://127.0.0.1:{server.server_port}/v1/retrieval/tasks/rt_{"a" * 32}/expansion-jobs'
            headers = {'X-Tenant-Id': 'tenant', 'X-User-Id': 'owner', 'Content-Type': 'application/json'}
            body = {'expected_task_version': 7, 'expected_revision': 2,
                    'raw_request': '找猫图片', 'dimension_id': 'default', 'target_count': 20,
                    'model_selection': {'llm': 'llm', 'vlm': 'vlm'},
                    'current_groups': [{'primary_label': '猫', 'auxiliary_terms': ['猫场景0']}],
                    'feedback_text': '猫组调整为12个检索词', 'confirmation_feedback_text': '词数改为12个',
                    'count_edits': [{'target': 'terms', 'labels': ['猫'], 'operation': 'set', 'value': 12}]}
            with urlopen(Request(root, headers=headers, data=json.dumps(body).encode(), method='POST')) as response:
                self.assertEqual(response.status, 202)
                job = json.load(response)
            with urlopen(Request(root + '/' + job['job_id'], headers=headers)) as response:
                self.assertEqual(json.load(response)['task_id'], 'rt_' + 'a' * 32)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_model_batch_rejects_old_words_then_accepts_only_new_small_batch(self):
        runtime = ModelRuntime()
        runtime.select = lambda _id, _kind: object()
        outputs = iter([
            {'parent_label': None, 'groups': [{'primary_label': '猫', 'auxiliary_terms': ['旧词', '新词1']}]},
            {'parent_label': None, 'groups': [{'primary_label': '猫', 'auxiliary_terms': ['新词1', '新词2']}]},
        ])
        runtime._request = lambda *_args, **_kwargs: next(outputs)
        result = runtime.expand_chunk('找猫图片', 'llm', 'vlm', limit=2, label='猫', existing_terms=['旧词'])
        self.assertEqual(result['plan']['groups'][0]['auxiliary_terms'], ['新词1', '新词2'])


if __name__ == '__main__':
    unittest.main()
