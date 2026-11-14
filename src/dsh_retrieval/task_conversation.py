"""Application boundary for task dialogue; the browser never constructs a patch."""
from .errors import ServiceError
from .requirement_changes import conditions_for, preview_changes


class TaskConversationCoordinator:
    def __init__(self, service):
        self.service = service

    def context(self, actor, task, confirmed_spec, feedback_ref=None):
        draft = None
        if feedback_ref is not None:
            if (not isinstance(feedback_ref, dict)
                    or set(feedback_ref) != {'draft_id', 'draft_version', 'proposal_digest'}
                    or not isinstance(feedback_ref['draft_id'], str)
                    or type(feedback_ref['draft_version']) is not int
                    or not isinstance(feedback_ref['proposal_digest'], str)):
                raise ServiceError('INVALID_SPEC', '需要绑定当前清洗草稿。', 422)
            draft = self.service.get_feedback(actor, task['task_id'], feedback_ref['draft_id'])
            if (draft['status'] != 'ready' or draft['draft_version'] != feedback_ref['draft_version']
                    or draft['proposal_digest'] != feedback_ref['proposal_digest']
                    or draft['base_revision'] != task['confirmed_revision']
                    or draft['base_task_version'] != task['task_version']):
                raise ServiceError('DRAFT_VERSION_CONFLICT', '草稿已变化，请刷新后重试。', 409)
            if 'cleaning_conditions' not in draft['proposal']['query_spec']:
                raise ServiceError('PENDING_DRAFT_EXISTS', '请先处理之前的需求修改草稿。', 409)
        spec = draft['proposal']['query_spec'] if draft else confirmed_spec
        return {'structured_changes': True, 'cleaning_conditions': conditions_for(spec),
                'reference_binding': {'task_id': task['task_id'], 'task_version': task['task_version'],
                    'revision': task['confirmed_revision'], 'generation': task['run_generation'],
                    'draft': {key: draft[key] for key in ('draft_id', 'draft_version', 'proposal_digest')} if draft else None},
                'requirement_spec': spec, 'has_pending_requirement_draft': draft is not None}, draft

    def apply(self, actor, task, confirmed_spec, draft, text, key, intent):
        return self.commit(actor, self.prepare(actor, task, confirmed_spec, draft, text, key, intent))

    def prepare(self, actor, task, confirmed_spec, draft, text, key, intent):
        if intent['kind'] not in ('cleaning', 'requirement'):
            return {'intent': intent, 'task': task, 'write': None}
        body = {'text': text, 'scope': 'all', 'cleaning_feedback': True,
                'expected_revision': task['confirmed_revision'],
                'expected_task_version': task['task_version']}
        if intent['kind'] == 'requirement':
            body['requirement_changes'] = {'count_edits': intent.get('count_edits', []),
                                           'cleaning_changes': intent.get('cleaning_changes', []),
                                           'retrieval_changes': intent.get('retrieval_changes', [])}
        else:
            body['cleaning_changes'] = intent.get('cleaning_changes')
        if draft:
            body['expected_draft_version'] = draft['draft_version']
        write = self.service.prepare_feedback(actor, task['task_id'], key, body,
                                             draft_id=draft['draft_id'] if draft else None)
        return {'intent': intent, 'task': task, 'confirmed_spec': confirmed_spec, 'write': write}

    def commit(self, actor, plan):
        intent = plan['intent']
        if plan['write'] is None:
            return intent
        saved, _ = self.service.commit_feedback(actor, plan['write'])
        preview = preview_changes(plan['confirmed_spec'], saved['proposal']['query_spec'])
        return {**intent, 'reply': '需求修改已整理，确认后才会更新。',
                'feedback_draft': saved,
                'cleaning_review': {'feedback_status': 'ready', 'needs_clarification': False,
                                    'requirement': '', 'rationale': '',
                                    'cleaning_plan_preview': preview}}
