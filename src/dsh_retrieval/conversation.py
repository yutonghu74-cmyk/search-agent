"""Natural replies share the existing response contract and never mutate drafts."""
import json
import re

from .errors import ServiceError


def conversation_effort(model):
    # GLM-5.3's documented lightweight setting; unsupported models keep their defaults.
    return 'low' if model.lower() in ('glm-5.3', 'glm-5.3-flash') else None


def natural_reply(text, mode):
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 600:
        raise ServiceError('MODEL_OUTPUT_INVALID', 'A natural reply must contain 1–600 characters', 502)
    text = text.strip()
    try:
        structured = json.loads(text)
    except ValueError:
        structured = None
    if isinstance(structured, dict) and ('action' in structured or 'kind' in structured):
        raise ServiceError('MODEL_OUTPUT_INVALID', 'Use the proposal tool for structured changes', 502)
    if re.search(r'已(?:经)?(?:(?:把|将).{0,40})?(?:改成|改为|修改|调整|替换|设置|更新|撤销|取消)', text):
        raise ServiceError('MODEL_OUTPUT_INVALID', 'A draft change requires a successful proposal tool', 502)
    if mode == 'request' and re.search(r'正在检索|已(?:经)?(?:开始检索|完成检索|找到|批准|确认)|无需(?:确认|批准)', text):
        raise ServiceError('MODEL_OUTPUT_INVALID', 'No search has been approved in a request draft', 502)
    if mode == 'request':
        return {'action': 'answer', 'reply': text, 'subject': None, 'target_count': None,
                'media_type': None, 'constraints': None, 'question': None,
                'count_edits': [], 'expansion_instruction': None}
    if mode == 'followup':
        return {'kind': 'answer', 'reply': text, 'target_count': None,
                'requested_terms_per_group': None, 'count_edits': [],
                'term_count_only': False, 'expansion_instruction': None}
    raise ValueError('Unsupported conversation mode')


def proposal_tool(name):
    if name == 'submit_task_change':
        return compact_proposal_tool()
    nullable_text = lambda size: {'type': ['string', 'null'], 'maxLength': size}
    counts = {'type': 'array', 'maxItems': 21, 'items': {'type': 'object', 'properties': {
        'target': {'enum': ['images', 'terms']}, 'labels': {'type': 'array', 'items': {'type': 'string'}},
        'operation': {'enum': ['set', 'add', 'scale']}, 'value': {'type': 'number'}},
        'required': ['target', 'labels', 'operation', 'value'], 'additionalProperties': False}}
    properties = {'reply': nullable_text(600), 'count_edits': counts,
                  'target_count': {'type': ['integer', 'null'], 'minimum': 1, 'maximum': 10000},
                  'expansion_instruction': nullable_text(1000)}
    if name == 'update_request_draft':
        discriminator = 'action'
        properties.update(action={'enum': ['update', 'revise_terms', 'replace', 'clarify', 'confirm', 'confirm_terms', 'answer']},
                          subject=nullable_text(160), constraints=nullable_text(300), question=nullable_text(200),
                          media_type={'enum': ['image', 'video', 'text', None]})
    elif name == 'submit_proposal':
        discriminator = 'kind'
        properties.update(kind={'enum': ['expansion', 'new_request', 'cleaning', 'requirement', 'clarify', 'answer', 'status']},
                          requested_terms_per_group={'type': ['integer', 'null'], 'minimum': 1, 'maximum': 200},
                          cleaning_instruction=nullable_text(1000),
                          retrieval_changes={'type': 'array', 'maxItems': 20, 'items': {
                              'type': 'object', 'properties': {
                                  'op': {'enum': ['upsert', 'remove']}, 'group_id': nullable_text(128),
                                  'primary_label': nullable_text(32),
                                  'auxiliary_terms': {'type': ['array', 'null'], 'maxItems': 200,
                                                      'items': {'type': 'string', 'minLength': 1, 'maxLength': 80}},
                                  'source_text': {'type': 'string', 'minLength': 1, 'maxLength': 1000}},
                              'required': ['op', 'group_id', 'primary_label', 'auxiliary_terms', 'source_text'],
                              'additionalProperties': False}},
                          cleaning_changes={'type': 'array', 'maxItems': 16, 'items': {
                              'type': 'object', 'properties': {
                                  'op': {'enum': ['add', 'replace', 'remove']},
                                  'condition_id': nullable_text(40), 'source_text': nullable_text(320),
                                  'group_id': nullable_text(128)},
                              'required': ['op', 'condition_id', 'source_text', 'group_id'],
                              'additionalProperties': False}},
                          term_count_only={'type': 'boolean'})
    else:
        raise ValueError('Unsupported conversation proposal tool')
    return {'type': 'function', 'function': {
        'name': name,
        'description': ('Save newly supplied request facts, even when partial: content, quantity, or constraints. '
                        'Call before asking for missing information so the supplied facts are retained. '
                        'Use ordinary text only for greetings, explanations, and read-only questions. '
                        'This is a draft and never approves or executes search.' if name == 'update_request_draft' else
                        'Submit an actual modification or new-request routing decision. Use ordinary text for conversation and explanations. This never approves or executes a task.'),
        'parameters': {'type': 'object', 'properties': {'proposal': {
            'type': 'object', 'properties': properties, 'required': ([discriminator, 'target_count', 'requested_terms_per_group'] if name == 'submit_proposal' else [discriminator]), 'additionalProperties': False}},
                       'required': ['proposal'], 'additionalProperties': False}}}


def task_status_reply(text, context):
    """Route common status questions to the shared workflow projection."""
    from .workflow_state import status_question_topic, workflow_reply
    topic = status_question_topic(text)
    return workflow_reply(context.get('workflow'), topic) if topic else None


def compact_proposal_tool(*, scope=None, condition_refs=None, group_refs=None):
    def ref(prefix):
        choices = condition_refs if prefix == 'c' else group_refs
        return {'type': 'string', 'enum': choices} if choices else {'type': 'string', 'pattern': '^' + prefix + '[1-9][0-9]?$'}
    text = lambda size: {'type': 'string', 'minLength': 1, 'maxLength': size}
    operation = {'enum': ['set', 'add', 'scale']}
    properties = {
        'kind': {'enum': ['requirement', 'expansion', 'answer', 'clarify', 'status', 'new_request']},
        'scope': {'type': 'string', 'enum': [scope]} if scope else {'type': 'string', 'pattern': '^s_[0-9a-f]{12}$'},
        'images': {'type': 'object', 'properties': {'op': operation, 'value': {'type': 'number'}},
                   'required': ['op', 'value'], 'additionalProperties': False},
        'conditions': {'type': 'array', 'maxItems': 16, 'items': {'type': 'object',
            'properties': {'op': {'enum': ['add', 'replace', 'remove']}, 'ref': ref('c'), 'text': text(320),
                           'group': {'type': ['string', 'null'], 'enum': group_refs + [None]} if group_refs is not None else
                                    {'type': ['string', 'null'], 'pattern': '^g[1-9][0-9]?$'}},
            'required': ['op'], 'additionalProperties': False}},
        'groups': {'type': 'array', 'maxItems': 20, 'items': {'type': 'object',
            'properties': {'op': {'enum': ['add', 'replace', 'remove']}, 'ref': ref('g'), 'label': text(32),
                           'words': {'type': 'array', 'maxItems': 200, 'items': text(80)}, 'source': text(1000)},
            'required': ['op', 'source'], 'additionalProperties': False}},
        'term_targets': {'type': 'array', 'maxItems': 20, 'items': {'type': 'object',
            'properties': {'group': ref('g'), 'op': operation, 'value': {'type': 'number'}},
            'required': ['group', 'op', 'value'], 'additionalProperties': False}},
        'instruction': text(1000), 'count_only': {'type': 'boolean'}, 'message': text(600)}
    if group_refs == []:
        properties['conditions']['items']['properties'].pop('group')
        properties['groups']['items']['properties'].pop('ref')
        properties['groups']['items']['properties']['op'] = {'enum': ['add']}
        properties.pop('term_targets')
    return {'type': 'function', 'function': {'name': 'submit_task_change',
        'description': 'Propose draft operations with current short references. Never approves or executes. Omit unused fields.',
        'parameters': {'type': 'object', 'properties': {'proposal': {'type': 'object', 'properties': properties,
                       'required': ['kind'], 'additionalProperties': False}},
                       'required': ['proposal'], 'additionalProperties': False}}}
