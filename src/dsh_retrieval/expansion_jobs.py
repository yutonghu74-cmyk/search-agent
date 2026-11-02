"""Durable, provisional keyword generation. Jobs never approve a search."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path

from .dialogue import resolve_count_edits
from .errors import ServiceError
from .expansion_models import MAX_EXPANSIONS, preview_edited_groups
from .explicit_term_edits import explicit_term_list
from .query_spec import digest
from .model_runtime import _INITIAL_GROUP_TERMS, _grounded_followup_term_count, _number_value


class ExpansionJobs:
    def __init__(self, path: Path | None, runtime, dialogue, projects, service=None):
        self.runtime, self.dialogue, self.projects, self.service = runtime, dialogue, projects, service
        self.lock = threading.RLock()
        self.slots = threading.Semaphore(2)
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='expansion-job')
        self.running = set()
        self.db = sqlite3.connect(str(path) if path else ':memory:', check_same_thread=False)
        self.db.execute('''CREATE TABLE IF NOT EXISTS expansion_jobs (
            job_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
            project_id TEXT NOT NULL, session_id TEXT NOT NULL, session_version INTEGER NOT NULL,
            request_digest TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL,
            updated_at TEXT NOT NULL)''')
        self.db.execute('CREATE INDEX IF NOT EXISTS expansion_jobs_session ON expansion_jobs (tenant_id,user_id,project_id,session_id,updated_at)')
        self.db.commit()

    def start_task(self, actor, task_id, body):
        if self.service is None:
            raise ServiceError('EXPANSION_JOBS_UNAVAILABLE', 'Task expansion jobs are unavailable', 503)
        task = self.service.get_task(actor, task_id)
        if (not isinstance(body, dict) or type(body.get('expected_task_version')) is not int
                or type(body.get('expected_revision')) is not int
                or task['task_version'] != body['expected_task_version']
                or task['confirmed_revision'] != body['expected_revision']):
            raise ServiceError('REVISION_CONFLICT', 'Task changed; reload before expanding', 409)
        transformed = {key: value for key, value in body.items()
                       if key not in ('expected_task_version', 'expected_revision')}
        transformed.update(dialogue_session_id='task:' + task_id,
                           dialogue_version=body['expected_task_version'],
                           source_revision=body['expected_revision'])
        return self.start(actor, task['project_id'], transformed, task_id=task_id)

    def start(self, actor, project_id, body, *, task_id=None):
        self.projects.require_access(actor, project_id, 'default', 'create')
        required = {'dialogue_session_id', 'dialogue_version', 'raw_request', 'dimension_id',
                    'target_count', 'model_selection'}
        optional = {'feedback_text', 'current_groups', 'count_edits', 'current_target_count',
                    'regeneration_key', 'source_revision', 'requested_terms_per_group',
                    'confirmation_feedback_text', 'term_count_only'}
        if (not isinstance(body, dict) or not required <= set(body) or set(body) - required - optional
                or body['dimension_id'] != 'default' or type(body['dialogue_version']) is not int
                or not isinstance(body['raw_request'], str) or not 1 <= len(body['raw_request'].strip()) <= 2000
                or type(body['target_count']) is not int or not 1 <= body['target_count'] <= 10000
                or not isinstance(body['model_selection'], dict)
                or set(body['model_selection']) != {'llm', 'vlm'}):
            raise ServiceError('INVALID_SPEC', 'Invalid expansion job request', 422)
        selection = body['model_selection']
        if 'regeneration_key' in body and (not isinstance(body['regeneration_key'], str)
                                           or not 1 <= len(body['regeneration_key']) <= 80):
            raise ServiceError('INVALID_SPEC', 'Invalid regeneration key', 422)
        binding = self.runtime.binding(selection['llm'], selection['vlm'])
        self._current(actor, project_id, body['dialogue_session_id'], body['dialogue_version'],
                      body.get('source_revision'))
        current = body.get('current_groups')
        feedback = body.get('feedback_text')
        if (current is None) != (feedback is None) and not (task_id and current is None and feedback is not None):
            raise ServiceError('INVALID_SPEC', 'Feedback requires current groups', 422)
        if current is not None:
            try:
                preview_edited_groups(None, current)
            except ValueError as error:
                raise ServiceError('INVALID_SPEC', str(error), 422) from None
        if feedback is not None and (not isinstance(feedback, str) or not 1 <= len(feedback.strip()) <= 2000):
            raise ServiceError('INVALID_SPEC', 'Invalid expansion feedback', 422)
        confirmation_feedback = body.get('confirmation_feedback_text')
        if confirmation_feedback is not None and (not task_id or not isinstance(confirmation_feedback, str)
                                                  or not 1 <= len(confirmation_feedback.strip()) <= 2000):
            raise ServiceError('INVALID_SPEC', 'Invalid confirmation feedback', 422)
        direct_groups = (explicit_term_list(confirmation_feedback or feedback, current,
                                            target_count=body.get('current_target_count', body['target_count']))
                         if task_id and current else None)
        deferred_edits = body.get('count_edits', []) if current is None else []
        if deferred_edits and (not task_id or not isinstance(deferred_edits, list)
                               or len(deferred_edits) > 20 or any(
                                   not isinstance(edit, dict) or edit.get('target') != 'terms'
                                   for edit in deferred_edits)):
            raise ServiceError('INVALID_SPEC', 'Invalid deferred term edits', 422)
        try:
            _, targets = resolve_count_edits([] if deferred_edits else body.get('count_edits', []),
                                             body['target_count'], current)
        except ValueError as error:
            raise ServiceError('INVALID_SPEC', str(error), 422) from None
        if targets and any(not 0 <= value <= MAX_EXPANSIONS for value in targets.values()):
            raise ServiceError('INVALID_SPEC', 'Term target is out of range', 422)
        requested_terms = body.get('requested_terms_per_group')
        if requested_terms is not None:
            if type(requested_terms) is not int or not 1 <= requested_terms <= MAX_EXPANSIONS or targets:
                raise ServiceError('INVALID_SPEC', 'Invalid requested term count', 422)
            targets = {group['primary_label']: requested_terms for group in current} if current else None
        if direct_groups is not None:
            targets = {direct_groups[0]['primary_label']: len(direct_groups[0]['auxiliary_terms'])}
        if task_id and confirmation_feedback and current is not None and len(current) == 1 and direct_groups is None:
            # A previous unconfirmed preview may carry an older count. Never
            # schedule a job whose target contradicts the latest user turn.
            latest_feedback = confirmation_feedback.rsplit('后续调整：', 1)[-1]
            label = current[0]['primary_label']
            current_target = targets.get(label) if targets else None
            try:
                _grounded_followup_term_count(latest_feedback, current_target)
            except ServiceError as error:
                raise ServiceError('INVALID_SPEC',
                                   '检索词目标与最新反馈不一致，请重新提交这条修改', 422) from error
        count_only = body.get('term_count_only', False)
        if type(count_only) is not bool or count_only and (
                not task_id or current is None or not targets
                or set(targets) != {group['primary_label'] for group in current}
                or any(targets[group['primary_label']] >= len(group['auxiliary_terms'])
                       for group in current)):
            raise ServiceError('INVALID_SPEC', 'Count-only reduction needs a smaller existing term target', 422)
        if direct_groups is not None and count_only:
            raise ServiceError('INVALID_SPEC', 'Explicit replacement cannot be count-only', 422)
        if current is not None and body.get('current_target_count') is not None and type(body['current_target_count']) is not int:
            raise ServiceError('INVALID_SPEC', 'Invalid prior image count', 422)
        request_digest = digest(body)
        with self.lock:
            row = self.db.execute('''SELECT job_id,request_digest,state FROM expansion_jobs
                WHERE tenant_id=? AND user_id=? AND project_id=? AND session_id=?
                ORDER BY rowid DESC LIMIT 1''',
                (actor.tenant_id, actor.user_id, project_id, body['dialogue_session_id'])).fetchone()
            if row and row[1] == request_digest:
                if json.loads(row[2])['status'] in ('queued', 'running'):
                    self._schedule(row[0])
                return self._public(row[0])
            job_id = 'expjob_' + uuid.uuid4().hex
            initial_counts = {_number_value(match.group(1)) for match in _INITIAL_GROUP_TERMS.finditer(body['raw_request'])} if current is None else set()
            if initial_counts and (len(initial_counts) != 1 or not 1 <= next(iter(initial_counts)) <= MAX_EXPANSIONS):
                raise ServiceError('INVALID_SPEC', 'Initial term count must be 1-200', 422)
            starting_groups = direct_groups or ([{'primary_label': group['primary_label'],
                                 'auxiliary_terms': list(group['auxiliary_terms'])[:targets[group['primary_label']]]
                                 if count_only else list(group['auxiliary_terms'])} for group in current]
                               if current is not None and targets and (count_only or all(
                                   targets[group['primary_label']] >= len(group['auxiliary_terms'])
                                   for group in current)) else None)
            initial = {'status': 'queued', 'groups': starting_groups, 'targets': targets,
                       'direct_edit': direct_groups is not None,
                       'model_binding': binding,
                       'deferred_count_edits': deferred_edits,
                       'initial_count': requested_terms or (next(iter(initial_counts)) if initial_counts else None),
                       'completed': sum(min(len(group['auxiliary_terms']), targets[group['primary_label']])
                                        for group in starting_groups) if starting_groups else 0,
                       'total': sum(targets.values()) if targets else None,
                       'result': None, 'error': None, 'failed_batch': None}
            self.db.execute('INSERT INTO expansion_jobs VALUES (?,?,?,?,?,?,?,?,?,?)',
                            (job_id, actor.tenant_id, actor.user_id, project_id,
                             body['dialogue_session_id'], body['dialogue_version'], request_digest,
                             json.dumps(body, ensure_ascii=False), json.dumps(initial, ensure_ascii=False), self._now()))
            self.db.commit()
            self._schedule(job_id)
            return self._public(job_id)

    def get(self, actor, project_id, session_id, job_id=None):
        self.projects.require_access(actor, project_id, 'default', 'create')
        if session_id.startswith('task:'):
            self.service.get_task(actor, session_id[5:])
        else:
            self.dialogue.snapshot(actor, project_id, session_id)
        with self.lock:
            if job_id:
                row = self.db.execute('''SELECT job_id FROM expansion_jobs WHERE job_id=? AND tenant_id=?
                    AND user_id=? AND project_id=? AND session_id=?''',
                    (job_id, actor.tenant_id, actor.user_id, project_id, session_id)).fetchone()
            else:
                row = self.db.execute('''SELECT job_id FROM expansion_jobs WHERE tenant_id=? AND user_id=?
                    AND project_id=? AND session_id=? ORDER BY rowid DESC LIMIT 1''',
                    (actor.tenant_id, actor.user_id, project_id, session_id)).fetchone()
            if not row:
                raise ServiceError('EXPANSION_JOB_NOT_FOUND', 'Expansion job not found', 404)
            return self._public(row[0])

    def retry(self, actor, project_id, session_id, job_id):
        result = self.get(actor, project_id, session_id, job_id)
        if result['status'] != 'failed':
            return result
        with self.lock:
            state = self._load(job_id)[1]
            state['status'], state['error'] = 'queued', None
            self._save(job_id, state)
            self._schedule(job_id)
            return self._public(job_id)

    def recover(self):
        with self.lock:
            rows = self.db.execute('SELECT job_id FROM expansion_jobs').fetchall()
            for (job_id,) in rows:
                if self._load(job_id)[1]['status'] in ('queued', 'running'):
                    self._schedule(job_id)

    def _schedule(self, job_id):
        if job_id not in self.running:
            self.running.add(job_id)
            self.pool.submit(self._work, job_id)

    def _load(self, job_id):
        row = self.db.execute('SELECT payload,state,tenant_id,user_id,project_id,session_id,session_version FROM expansion_jobs WHERE job_id=?', (job_id,)).fetchone()
        return json.loads(row[0]), json.loads(row[1]), row[2:]

    def _save(self, job_id, state):
        self.db.execute('UPDATE expansion_jobs SET state=?,updated_at=? WHERE job_id=?',
                        (json.dumps(state, ensure_ascii=False), self._now(), job_id))
        self.db.commit()

    def _public(self, job_id):
        payload, state, meta = self._load(job_id)
        return {'job_id': job_id, 'dialogue_session_id': meta[3], 'dialogue_version': meta[4],
                'task_id': meta[3][5:] if meta[3].startswith('task:') else None,
                'task_version': meta[4] if meta[3].startswith('task:') else None,
                'revision': payload.get('source_revision'),
                'raw_request': payload['raw_request'], 'target_count': payload['target_count'],
                'feedback_text': payload.get('feedback_text'),
                'confirmation_feedback_text': payload.get('confirmation_feedback_text'),
                'requested_terms_per_group': payload.get('requested_terms_per_group'),
                'source_groups': payload.get('current_groups') if payload.get('term_count_only') else None,
                'status': state['status'], 'completed_terms': state['completed'],
                'target_terms': state['total'], 'failed_batch': state['failed_batch'],
                'error': state['error'], 'result': state['result']}

    def _work(self, job_id):
        with self.slots:
            try:
                with self.lock:
                    _, _, identity = self._load(job_id)
                settings = getattr(self.runtime, 'settings', None)
                scope = getattr(self.runtime, 'request_scope', None)
                business = getattr(self.runtime, 'business_scope', None)
                with (settings.scope(identity[0], identity[1]) if settings else nullcontext()), (
                    scope(identity[0], identity[1], job_id,
                          f'/v1/retrieval/projects/{identity[2]}/expansion-jobs') if scope else nullcontext()), (
                    business(project_id=identity[2], **(
                        {'task_id': identity[3][5:], 'task_version': identity[4]}
                        if identity[3].startswith('task:') else
                        {'dialogue_session_id': identity[3], 'expected_version': identity[4]}))
                    if business else nullcontext()):
                    self._run_batches(job_id)
            except Exception as error:
                with self.lock:
                    payload, state, _ = self._load(job_id)
                    state['status'] = 'failed'
                    state['error'] = {'code': getattr(error, 'code', 'EXPANSION_FAILED'),
                                      'message': getattr(error, 'message', '后台扩词失败，请重试失败批次')[:300]}
                    self._save(job_id, state)
            finally:
                with self.lock:
                    self.running.discard(job_id)

    def _run_batches(self, job_id):
        from .models import Actor
        batch_marker = None
        try:
                while True:
                    with self.lock:
                        payload, state, meta = self._load(job_id)
                        if state['status'] not in ('queued', 'running'):
                            return
                        state['status'] = 'running'
                        self._save(job_id, state)
                    actor = Actor(meta[0], meta[1])
                    self._current(actor, meta[2], meta[3], meta[4], payload.get('source_revision'))
                    selection = payload['model_selection']
                    if self.runtime.binding(selection['llm'], selection['vlm']) != state['model_binding']:
                        raise ServiceError('MODEL_CONFIG_CHANGED', 'Model profile changed; restart expansion', 409)
                    if state['groups'] is None:
                        batch_marker = {'group': '初始分组', 'offset': 0, 'size': 8}
                        seed = self.runtime.expand_chunk(payload['raw_request'], selection['llm'], selection['vlm'],
                            limit=8, feedback_text=payload.get('feedback_text'), current_groups=payload.get('current_groups'))
                        groups = seed['plan']['groups']
                        groups = [{'primary_label': group['primary_label'], 'auxiliary_terms': group['auxiliary_terms']} for group in groups]
                        if state.get('deferred_count_edits'):
                            try:
                                _, targets = resolve_count_edits(state['deferred_count_edits'],
                                                                 payload['target_count'], groups)
                            except ValueError as error:
                                raise ServiceError('INVALID_SPEC', str(error), 422) from None
                        else:
                            targets = state['targets'] or {group['primary_label']: state.get('initial_count') or len(group['auxiliary_terms']) for group in groups}
                        for group in groups:
                            group['auxiliary_terms'] = group['auxiliary_terms'][:targets[group['primary_label']]]
                        if set(targets) != {group['primary_label'] for group in groups}:
                            raise ServiceError('MODEL_OUTPUT_INVALID', 'Generated group labels changed', 422)
                        state['groups'] = groups
                        state['targets'] = targets
                        state['total'] = sum(targets.values())
                        state['completed'] = sum(len(group['auxiliary_terms']) for group in groups)
                        with self.lock:
                            self._save(job_id, state)
                    else:
                        groups = state['groups']
                        targets = state['targets']
                    next_group = next((group for group in groups if len(group['auxiliary_terms']) < targets[group['primary_label']]), None)
                    if next_group is None:
                        self._current(actor, meta[2], meta[3], meta[4], payload.get('source_revision'))
                        current = payload.get('current_groups')
                        if (current is not None and payload.get('current_target_count') in (None, payload['target_count'])
                                and {group['primary_label']: set(group['auxiliary_terms']) for group in current}
                                == {group['primary_label']: set(group['auxiliary_terms']) for group in groups}):
                            raise ServiceError('EXPANSION_UNCHANGED', '新词表没有落实修改，旧词表已保留', 422)
                        result = preview_edited_groups(None, groups)
                        state['result'] = self.runtime.bind(result, state['model_binding'],
                                                            generated=not (payload.get('term_count_only') or state.get('direct_edit')))
                        if payload.get('term_count_only'):
                            state['result']['note'] = '仅按原词表顺序保留指定数量的检索词；未调用扩词模型。当前仍是待确认草稿。'
                        if state.get('direct_edit'):
                            state['result']['note'] = '按用户逐字指定的完整词表生成；未调用扩词模型。当前仍是待确认草稿。'
                        state['status'] = 'completed'
                        state['completed'] = state['total']
                        state['failed_batch'] = None
                        with self.lock:
                            self._save(job_id, state)
                        return
                    label = next_group['primary_label']
                    missing = targets[label] - len(next_group['auxiliary_terms'])
                    batch_marker = {'group': label, 'offset': len(next_group['auxiliary_terms']), 'size': min(10, missing)}
                    try:
                        addition = self.runtime.expand_chunk(payload['raw_request'], selection['llm'], selection['vlm'],
                            limit=min(10, missing), label=label, existing_terms=next_group['auxiliary_terms'],
                            feedback_text=payload.get('feedback_text'))
                        extra = addition['plan']['groups'][0]['auxiliary_terms']
                        next_group['auxiliary_terms'].extend(extra)
                        state['completed'] = sum(min(len(group['auxiliary_terms']), targets[group['primary_label']]) for group in groups)
                        state['failed_batch'] = None
                        with self.lock:
                            self._save(job_id, state)
                    except Exception:
                        # Keep the failing offset without persisting a partial result.
                        state['failed_batch'] = batch_marker
                        raise
        except Exception as error:
            with self.lock:
                _, state, _ = self._load(job_id)
                state['failed_batch'] = batch_marker
                self._save(job_id, state)
            raise

    @staticmethod
    def _now():
        return datetime.now(UTC).isoformat()

    def _current(self, actor, project_id, session_id, version, revision):
        if session_id.startswith('task:'):
            if self.service is None:
                raise ServiceError('EXPANSION_JOBS_UNAVAILABLE', 'Task expansion jobs are unavailable', 503)
            task = self.service.get_task(actor, session_id[5:])
            if (task['project_id'] != project_id or task['task_version'] != version
                    or task['confirmed_revision'] != revision):
                raise ServiceError('REVISION_CONFLICT', 'Task changed; job result discarded', 409)
        else:
            snapshot = self.dialogue.snapshot(actor, project_id, session_id)
            if snapshot['version'] != version or snapshot['stage'] != 'terms':
                raise ServiceError('DIALOGUE_CONFLICT', 'Conversation changed; job result discarded', 409)
