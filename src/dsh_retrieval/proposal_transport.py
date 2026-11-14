"""Model-only short references; business models and receipts keep stable IDs."""
from copy import deepcopy

from .errors import ServiceError
from .query_spec import digest
from .requirement_changes import conditions_for


def invalid(message):
    raise ServiceError('MODEL_OUTPUT_INVALID', message, 502)


class ProposalReferences:
    def __init__(self, context):
        spec = context['requirement_spec']
        self.conditions = {f'c{i}': row for i, row in enumerate(conditions_for(spec), 1)}
        self.groups = {f'g{i}': deepcopy(row) for i, row in enumerate((spec.get('grouped_expansion') or {}).get('groups', []), 1)}
        self.reverse = {row['condition_id']: ref for ref, row in self.conditions.items()}
        self.reverse.update({row['group_id']: ref for ref, row in self.groups.items()})
        self.scope = 's_' + digest([context.get('reference_binding'), context.get('task_id'), spec,
                                    list(self.conditions), list(self.groups)])[7:19]

    def project(self, context):
        def visit(value):
            if isinstance(value, list):
                return [visit(row) for row in value]
            if not isinstance(value, dict):
                return value
            return {key: self.reverse.get(item, item) if key in ('condition_id', 'group_id', 'node_id')
                    and isinstance(item, str) else visit(item) for key, item in value.items()}
        value = visit(deepcopy(context))
        value.get('task_context', {}).pop('reference_binding', None)
        value['reference_scope'] = self.scope
        value['reference_catalog'] = {'conditions': list(self.conditions), 'groups': list(self.groups)}
        return value

    def _condition(self, ref):
        if not isinstance(ref, str) or ref not in self.conditions:
            invalid('条件短引用不存在，请使用当前 c 引用。')
        return self.conditions[ref]

    def _group(self, ref):
        if not isinstance(ref, str) or ref not in self.groups:
            invalid('类别短引用不存在，请使用当前 g 引用。')
        return self.groups[ref]

    def decode(self, value):
        if not isinstance(value, dict): invalid('提案必须为对象。')
        legacy = {'target_count', 'requested_terms_per_group', 'cleaning_changes', 'retrieval_changes', 'count_edits'}
        if legacy.intersection(value):
            # Compatibility for in-flight v1 providers and old adapters. Any
            # stable ID must still belong to this exact invocation's table.
            value = deepcopy(value)
            if 'scope' in value:
                if value.pop('scope') != self.scope: invalid('旧格式提案的引用版本已变化。')
            for field, id_key, table in (('cleaning_changes', 'condition_id', self.conditions),
                                         ('retrieval_changes', 'group_id', self.groups)):
                for row in value.get(field) or []:
                    if not isinstance(row, dict):
                        invalid('操作格式无效。')
                    identifier = row.get(id_key)
                    if isinstance(identifier, str) and identifier in table:
                        identifier = row[id_key] = table[identifier][id_key]
                    valid = {item[id_key] for item in table.values()}
                    if identifier is not None and (not isinstance(identifier, str) or identifier not in valid):
                        invalid('旧格式引用不属于当前方案。')
                    group = row.get('group_id')
                    if isinstance(group, str) and group in self.groups:
                        group = row['group_id'] = self.groups[group]['group_id']
                    if group is not None and (not isinstance(group, str) or group not in {item['group_id'] for item in self.groups.values()}):
                        invalid('类别引用不属于当前方案。')
            return value
        if (not isinstance(value, dict) or 'kind' not in value or set(value) -
                {'kind', 'scope', 'images', 'conditions', 'groups', 'term_targets', 'instruction', 'count_only', 'message'}):
            invalid('精简提案包含不支持的字段。')
        kind = value['kind']
        result = {'kind': kind, 'target_count': None, 'requested_terms_per_group': None,
                  'count_edits': [], 'cleaning_changes': [], 'retrieval_changes': []}
        if kind in ('answer', 'clarify', 'status', 'new_request'):
            if set(value) - {'kind', 'message', 'scope'}:
                invalid('只读或新需求分流不能夹带修改。')
            if kind == 'clarify' and not value.get('message'):
                invalid('澄清须说明需要补充什么。')
            result['reply'] = value.get('message', '')
            return result
        if kind not in ('requirement', 'expansion') or value.get('scope') != self.scope:
            invalid('提案引用版本已变化，请使用当前 reference_scope。')
        if 'message' in value:
            invalid('修改提案只返回操作，确认文案由服务端生成。')
        if 'images' in value:
            images = value['images']
            if not isinstance(images, dict) or set(images) != {'op', 'value'}:
                invalid('图片数量只接受 op/value。')
            result['count_edits'].append({'target': 'images', 'labels': [], 'operation': images['op'], 'value': images['value']})
        touched = {row.get('ref') for row in value.get('conditions', []) if isinstance(row, dict)
                   and row.get('op') in ('replace', 'remove') and isinstance(row.get('ref'), str)} if isinstance(value.get('conditions', []), list) else set()
        added = set()
        for field in ('conditions', 'groups', 'term_targets'):
            rows = value.get(field, [])
            if not isinstance(rows, list) or len(rows) > (16 if field == 'conditions' else 20):
                invalid('操作列表超出范围。')
            for row in rows:
                if not isinstance(row, dict): invalid('操作必须是对象。')
                if field == 'conditions':
                    op = row.get('op')
                    if not isinstance(op, str): invalid('条件操作类型无效。')
                    allowed = {'add': {'op', 'text', 'group'}, 'replace': {'op', 'ref', 'text'}, 'remove': {'op', 'ref'}}.get(op)
                    required = {'add': {'op', 'text'}, 'replace': {'op', 'ref', 'text'}, 'remove': {'op', 'ref'}}.get(op)
                    if allowed is None or not required <= set(row) or set(row) - allowed: invalid('条件操作字段无效。')
                    old = self._condition(row['ref']) if op != 'add' else None
                    group = self._group(row['group'])['group_id'] if row.get('group') is not None else None
                    if op == 'add' and isinstance(row.get('text'), str):
                        identity = (row['text'].strip(), group)
                        if identity in added or any(ref not in touched and item['text'] == identity[0]
                                and item['group_id'] == group for ref, item in self.conditions.items()):
                            continue
                        added.add(identity)
                    result['cleaning_changes'].append({'op': op, 'condition_id': old['condition_id'] if old else None,
                        'source_text': row.get('text'), 'group_id': old['group_id'] if old else group})
                elif field == 'groups':
                    op = row.get('op')
                    if not isinstance(op, str): invalid('类别操作类型无效。')
                    allowed = {'add': {'op', 'label', 'words', 'source'}, 'replace': {'op', 'ref', 'label', 'words', 'source'}, 'remove': {'op', 'ref', 'source'}}.get(op)
                    required = {'add': {'op', 'label', 'words', 'source'}, 'replace': {'op', 'ref', 'words', 'source'}, 'remove': {'op', 'ref', 'source'}}.get(op)
                    if allowed is None or not required <= set(row) or set(row) - allowed: invalid('类别操作字段无效。')
                    old = self._group(row['ref']) if op != 'add' else None
                    result['retrieval_changes'].append({'op': 'remove' if op == 'remove' else 'upsert',
                        'group_id': old['group_id'] if old else None,
                        'primary_label': None if op == 'remove' else row.get('label', old['primary_label'] if old else None),
                        'auxiliary_terms': None if op == 'remove' else row['words'], 'source_text': row['source']})
                else:
                    if set(row) != {'group', 'op', 'value'}: invalid('词数操作只接受 group/op/value。')
                    result['count_edits'].append({'target': 'terms', 'labels': [self._group(row['group'])['primary_label']],
                                                 'operation': row['op'], 'value': row['value']})
        if kind == 'requirement' and any(key in value for key in ('instruction', 'count_only', 'term_targets')):
            invalid('统一修改须提交完整词表，不能夹带扩词指令或重复词数。')
        if kind == 'expansion':
            if value.get('conditions') or value.get('groups'): invalid('混合修改须用 requirement。')
            result.update(expansion_instruction=value.get('instruction'), term_count_only=value.get('count_only', False))
        return result
