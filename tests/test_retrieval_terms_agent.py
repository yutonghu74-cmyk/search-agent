from copy import deepcopy
from contextlib import closing
from pathlib import Path
import json
import sqlite3
import subprocess
import tempfile
import threading
import time
import unittest

import test_confirmed_terms_agent as execution_fixture
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.expansion_jobs import ExpansionJobs
from dsh_retrieval.expansion_models import preview_edited_groups
from dsh_retrieval.models import Actor
from dsh_retrieval.retrieval_terms_agent import RetrievalTermsAgent, WORKFLOW


class Dialogue:
    version = 1
    def snapshot(self, actor, project, session):
        if actor != Actor('tenant-a', 'user-a') or project != 'demo' or session != 'dlg_terms':
            raise ServiceError('DIALOGUE_NOT_FOUND', 'Missing fixture dialogue', 404)
        return {'version': self.version, 'stage': 'terms'}


class Models:
    def __init__(self):
        self.calls = []
        self.config = 'original-model'
    def binding(self, llm, vlm):
        return {'llm': llm, 'vlm': vlm, 'fixture_config': self.config}
    def expand_chunk(self, raw_request, llm, vlm, **kwargs):
        self.calls.append(deepcopy((raw_request, llm, vlm, kwargs)))
        label = kwargs.get('label')
        if label:
            offset = len(kwargs['existing_terms'])
            groups = [{'primary_label': label, 'auxiliary_terms':
                       [label + str(i) for i in range(offset, offset + kwargs['limit'])]}]
        else:
            groups = [{'primary_label': '狗', 'auxiliary_terms': ['柯基']},
                      {'primary_label': '猫', 'auxiliary_terms': ['橘猫']}]
        return preview_edited_groups(None, groups)
    def bind(self, result, binding, *, generated=False):
        return result


class RetrievalTermsAgentTest(unittest.TestCase):
    def body(self):
        return {'dialogue_session_id': 'dlg_terms', 'dialogue_version': 1,
                'raw_request': '狗3张、猫2张', 'target_count': 5, 'dimension_id': 'default',
                'requested_terms_per_group': 1, 'model_selection': {'llm': 'chosen-llm', 'vlm': 'chosen-vlm'}}

    def wait(self, jobs, actor, started, status):
        for _ in range(200):
            result = jobs.get(actor, 'demo', 'dlg_terms', started['job_id'])
            if result['status'] == status:
                return result
            time.sleep(.01)
        self.fail(f"Term draft did not reach {status}: {result}")

    def fixture(self, path=None):
        from dsh_retrieval.adapters.projects import LocalProjectAccess
        projects = LocalProjectAccess([('tenant-a', 'user-a', 'demo', 'default')])
        models, dialogue = Models(), Dialogue()
        jobs = ExpansionJobs(path, models, dialogue, projects)
        self.addCleanup(jobs.close)
        return jobs, models, dialogue, Actor('tenant-a', 'user-a')

    def test_agent_draft_to_original_confirmation_to_retrieval_preserves_groups(self):
        f = execution_fixture.ConfirmedTermsAgentTest()
        f.setUp(); self.addCleanup(f.doCleanups)
        jobs, models, _, actor = self.fixture()
        handler = f.server.RequestHandlerClass
        handler.expansion_jobs = handler.retrieval_terms = jobs
        root = '/v1/retrieval/projects/demo/dialogue-sessions/dlg_terms/'
        with closing(sqlite3.connect(f.service.repository.path)) as db:
            before = db.execute('SELECT COUNT(*) FROM task_read_summaries').fetchone()[0]
        status, started = f.call(root + 'retrieval-agent/terms', self.body())
        self.assertEqual(status, 202, started)
        done = self.wait(jobs, actor, started, 'completed')
        self.assertEqual(done['agent']['workflow_ref'], WORKFLOW)
        self.assertEqual([g['delivery_target'] for g in done['result']['plan']['groups']], [3, 2])
        self.assertEqual(len(models.calls), 1)
        self.assertEqual(models.calls[0][1:3], ('chosen-llm', 'chosen-vlm'))
        # Canonical and old URLs resolve the exact same intent, not two planners.
        status, replay = f.call(root + 'expansion-jobs', self.body())
        self.assertEqual(replay['job_id'], started['job_id'])
        self.assertEqual(len(models.calls), 1)
        with closing(sqlite3.connect(f.service.repository.path)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM task_read_summaries').fetchone()[0], before)
        body = f.body(); body['query_spec']['grouped_expansion'] = done['result']['plan']
        draft = f.draft(body)
        status, task = f.confirm(draft)
        self.assertEqual(status, 202, task)
        ref = task['search_enqueue']['job_ref']
        job = f.wait_review(task['task_id'], ref)
        self.assertEqual(len(job['query_terms']), 5)
        self.assertEqual(len(models.calls), 1, 'Execution must not repeat term generation')
        self.assertEqual(job['status'], 'waiting_confirmation')
        status, _ = f.call(root + 'retrieval-agent/terms/' + started['job_id'], user='other')
        self.assertIn(status, (403, 404))

    def test_adapter_output_is_validated_before_any_batch_checkpoint(self):
        jobs, models, _, actor = self.fixture()
        original = models.expand_chunk
        def invalid(*args, **kwargs):
            result = original(*args, **kwargs)
            result['plan']['groups'][0]['auxiliary_terms'].append('未经摘要绑定的词')
            return result
        models.expand_chunk = invalid
        start = jobs.start(actor, 'demo', self.body())
        failed = self.wait(jobs, actor, start, 'failed')
        self.assertEqual(failed['error']['code'], 'MODEL_OUTPUT_INVALID')
        self.assertEqual(failed['completed_terms'], 0)
        self.assertIsNone(failed['result'])
        self.assertEqual(failed['preview_groups'], [])

    def test_late_model_response_after_dialogue_edit_does_not_publish_preview(self):
        for change in ('dialogue', 'model'):
            with self.subTest(change=change):
                jobs, models, dialogue, actor = self.fixture()
                entered, release = threading.Event(), threading.Event()
                self.addCleanup(release.set)
                original = models.expand_chunk
                def blocked(*args, **kwargs):
                    entered.set(); release.wait(3)
                    return original(*args, **kwargs)
                models.expand_chunk = blocked
                start = jobs.start(actor, 'demo', self.body())
                self.assertTrue(entered.wait(2))
                if change == 'dialogue': dialogue.version += 1
                else: models.config = 'changed-model'
                release.set()
                failed = self.wait(jobs, actor, start, 'failed')
                self.assertEqual(failed['error']['code'], 'DIALOGUE_CONFLICT' if change == 'dialogue' else 'MODEL_CONFIG_CHANGED')
                self.assertEqual(failed['completed_terms'], 0)
                self.assertIsNone(failed['result'])

    def test_failed_batch_reopen_uses_same_agent_and_does_not_regenerate_seed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'terms.sqlite3'
            jobs, models, dialogue, actor = self.fixture(path)
            body = self.body(); body['requested_terms_per_group'] = 12
            original = models.expand_chunk
            def fail(*args, **kwargs):
                if kwargs.get('label'):
                    raise ServiceError('MODEL_RATE_LIMIT', 'Fixture rate limit', 429)
                return original(*args, **kwargs)
            models.expand_chunk = fail
            start = jobs.start(actor, 'demo', body)
            failed = self.wait(jobs, actor, start, 'failed')
            self.assertEqual(failed['completed_terms'], 2)
            models.expand_chunk = original
            reopened = ExpansionJobs(path, models, dialogue, jobs.projects)
            self.addCleanup(reopened.close)
            reopened.retry(actor, 'demo', 'dlg_terms', start['job_id'])
            done = self.wait(reopened, actor, start, 'completed')
            self.assertEqual(done['completed_terms'], 24)
            self.assertEqual(sum(not row[3].get('label') for row in models.calls), 1)
            self.assertEqual(done['agent'], RetrievalTermsAgent.descriptor())

    def test_binding_adapter_cannot_replace_generated_terms(self):
        jobs, models, _, actor = self.fixture()
        models.bind = lambda result, *args, **kwargs: preview_edited_groups(None, [
            {'primary_label': '猫', 'auxiliary_terms': ['任意替换']}])
        start = jobs.start(actor, 'demo', self.body())
        failed = self.wait(jobs, actor, start, 'failed')
        self.assertEqual(failed['error']['code'], 'MODEL_OUTPUT_INVALID')
        self.assertEqual(failed['completed_terms'], 2)
        self.assertIsNone(failed['result'])

    def test_invalid_followup_batch_keeps_seed_and_retries_only_missing_terms(self):
        for kind in ('wrong_group', 'repeated_term'):
            with self.subTest(kind=kind):
                jobs, models, _, actor = self.fixture()
                body = self.body(); body['requested_terms_per_group'] = 2
                original = models.expand_chunk
                def invalid(*args, **kwargs):
                    if kwargs.get('label'):
                        return preview_edited_groups(None, [{'primary_label':
                            '另一组' if kind == 'wrong_group' else kwargs['label'],
                            'auxiliary_terms': ['新词' if kind == 'wrong_group' else kwargs['existing_terms'][0]]}])
                    return original(*args, **kwargs)
                models.expand_chunk = invalid
                start = jobs.start(actor, 'demo', body)
                failed = self.wait(jobs, actor, start, 'failed')
                self.assertEqual(failed['error']['code'], 'MODEL_OUTPUT_INVALID')
                self.assertEqual(failed['completed_terms'], 2)
                self.assertIsNone(failed['result'])
                self.assertEqual(failed['failed_batch']['offset'], 1)
                models.expand_chunk = original
                jobs.retry(actor, 'demo', 'dlg_terms', start['job_id'])
                done = self.wait(jobs, actor, start, 'completed')
                self.assertEqual(done['completed_terms'], 4)
                self.assertEqual(sum(not row[3].get('label') for row in models.calls), 1)

    def test_chat_calls_the_agent_terms_phase_for_new_and_existing_tasks(self):
        from dsh_retrieval.chat_ui import CHAT_JS
        source = '\n'.join(line for line in CHAT_JS.splitlines() if line.startswith((
            'function expansionJobPath(', 'function taskExpansionPath(', 'function pipelineEndpoint(')))
        script = "const assert=require('node:assert/strict');const state={dialogueId:'dlg_terms'};\n" + source + """
assert.equal(expansionJobPath(),'/v1/retrieval/projects/demo/dialogue-sessions/dlg_terms/retrieval-agent/terms');
assert.equal(taskExpansionPath('rt_abc'),'/v1/retrieval/tasks/rt_abc/retrieval-agent/terms');
for(const path of [expansionJobPath(), taskExpansionPath('rt_abc')+'/expjob_abc', '/v1/x/expansion-jobs']) {
  assert.equal(pipelineEndpoint(path),'expansion');
}
"""
        result = subprocess.run(['node', '-e', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
