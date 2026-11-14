"""Bounded, source-grounded VLM checks for one confirmed image request.

The splitter only uses explicit punctuation boundaries. It never paraphrases
away an exclusion or silently truncates a requirement. The owner sees every
instruction before approving the resulting recipe.
"""

import re

from .errors import ServiceError
from .expansion_models import delivery_targets


MAX_NODES = 8
MAX_SOURCE_CHARS = 320
_SEPARATOR = re.compile(r'[，,；;。\n、]+')
_QUANTITY = re.compile(r'(?:共|总共|一共|各|每类|每组)?\s*[0-9０-９]+\s*(?:张|只|幅|个)(?:左右)?')


def _added_checks(text):
    """Remove only explicit conversational scaffolding, retaining source text."""
    source = text.strip()
    prefix = re.compile(r'^(?:清洗(?:时|阶段|环节)|(?:请|再)?帮我(?:判断|检查|检测)|(?:请|再)?(?:新增|增加|添加|补充|加)(?:一个|一项|个)?(?:清洗)?(?:检测|检查)?(?:节点|条件|步骤|要求|检测|检查)(?:检查|检测)?|修改清洗条件|增加清洗检测)[：:,，\s]*')
    for _ in range(4):
        stripped = prefix.sub('', source, count=1).strip()
        if stripped == source:
            break
        source = stripped
    chunks = tuple(part.strip() for part in _SEPARATOR.split(source) if part.strip())
    if not chunks:
        raise ServiceError('VLM_RULE_REQUIRED', 'Please specify the observable image condition', 422)
    return chunks


def instruction_plan(raw_request):
    if not isinstance(raw_request, str) or not raw_request.strip():
        raise ServiceError('VLM_RULE_REQUIRED', 'Confirmed image requirement is empty', 409)
    source = raw_request.strip()
    parts = source.split('\n追加检测条件：')
    chunks = tuple(part.strip() for part in _SEPARATOR.split(parts[0]) if part.strip())
    chunks += tuple(chunk for text in parts[1:] for chunk in _added_checks(text))
    if (not chunks or len(chunks) > MAX_NODES
            or any(len(part) > MAX_SOURCE_CHARS for part in chunks)):
        raise ServiceError('VLM_PLAN_LIMIT',
                           'Image conditions need at most eight checks of 320 characters each; revise and confirm the request', 422)
    if len(chunks) == 1:
        # Keep the existing released detector identity for prior one-node jobs.
        instruction = ('判断当前单张图片是否符合下列已确认需求中可观察的画面条件；'
                       '忽略张数、检索、打包等非单图画面条件。已确认需求：' + source)
        if len(instruction) > 2000:
            raise ServiceError('VLM_PLAN_LIMIT', 'Image check instruction is too long', 422)
        return ({'node_id': 'step_1', 'source': source, 'instruction': instruction,
                 'backend_ref': 'vlm.requirement@1',
                 'predicate_ref': 'image.semantic-match@1',
                 'rule_id': 'semantic-match'},)
    plan = []
    for index, chunk in enumerate(chunks, 1):
        instruction = ('判断当前单张图片是否满足本节点已确认的可观察画面条件；'
                       '只判断本节点，不替其他节点作出通过结论。'
                       '无法从画面判断则返回 unknown；忽略张数、检索与打包。'
                       f'节点原文：{chunk}。主体上下文仅用于消解指代：{chunks[0]}')
        if len(instruction) > 2000:
            raise ServiceError('VLM_PLAN_LIMIT', 'Image check instruction is too long', 422)
        plan.append({'node_id': f'step_{index}', 'source': chunk,
                     'instruction': instruction,
                     'backend_ref': f'vlm.requirement.node{index}@1',
                     'predicate_ref': f'image.semantic-match.node{index}@1',
                     'rule_id': f'semantic-match-node{index}'})
    return tuple(plan)


def group_instruction_plan(raw_request, grouped, *, strip_quantities=True):
    """One check per quota group, each asked only of candidates that group retrieved.

    The punctuation splitter cannot serve quota tasks: "狗20张，猫30张" would
    become two nodes ANDed together and require a dog photo to also be a cat.
    Each check instead carries the whole request as context, so conditions that
    apply to every group still bind without being parsed out of the text.
    """
    source = raw_request.strip() if isinstance(raw_request, str) else ''
    parts = source.split('\n追加检测条件：')
    source = parts[0]
    additions = tuple(chunk for text in parts[1:] for chunk in _added_checks(text))
    groups = grouped['groups']
    if (len(groups) * (1 + len(additions)) > MAX_NODES
            or any(len(chunk) > MAX_SOURCE_CHARS for chunk in additions)):
        raise ServiceError('VLM_PLAN_LIMIT', 'At most eight bounded image checks are supported', 422)
    if not source or len(source) > MAX_SOURCE_CHARS or len(groups) > MAX_NODES:
        raise ServiceError('VLM_PLAN_LIMIT',
                           'Group quotas need at most eight groups and a request of 320 characters; revise and confirm the request', 422)
    # Quantities are the quota, not something one image can show. Left in, a
    # real VLM read "狗2张" as "this image must contain two dog photos" and
    # held nearly every dog as unknown.
    if strip_quantities:
        source = re.sub(r'\s+', ' ', _QUANTITY.sub('', source)).strip(' ，,、；;') or source
    plan = []
    for index, group in enumerate(groups, 1):
        group_id = group['group_id']
        node_source = f'类别「{group["primary_label"]}」；已确认需求：{source}'
        plan.append({'node_id': f'step_{index}', 'source': node_source,
                     'instruction': ('判断当前单张图片是否属于本节点类别，并符合已确认需求中'
                                     '适用于该类别的可观察画面条件；其他类别的条件不适用于本节点。'
                                     '无法从画面判断则返回 unknown；忽略张数、检索与打包。'
                                     + node_source),
                     'backend_ref': f'vlm.group.{group_id}@1',
                     'predicate_ref': f'image.group.{group_id}@1',
                     'rule_id': f'group-check-{group_id}',
                     'group_id': group_id})
    for group in groups:
        for index, condition in enumerate(additions, 1):
            group_id = group['group_id']
            node_source = f'类别「{group["primary_label"]}」；新增检测条件：{condition}'
            plan.append({'node_id': f'step_{len(plan)+1}', 'source': node_source,
                         'instruction': '判断当前单张图片是否满足本节点新增的画面条件，无法判断返回 unknown。' + node_source,
                         'backend_ref': f'vlm.group.{group_id}.extra{index}@1',
                         'predicate_ref': f'image.group.{group_id}.extra{index}@1',
                         'rule_id': f'group-check-{group_id}-extra{index}', 'group_id': group_id})
    return tuple(plan)


def confirmed_instruction_plan(spec):
    """The plan a confirmed query spec owes, quota-aware. Single source of truth."""
    if 'cleaning_conditions' in spec:
        from .requirement_changes import normalize_conditions
        rows = normalize_conditions(spec['cleaning_conditions'], spec)
        labels = {group['group_id']: group['primary_label']
                  for group in (spec.get('grouped_expansion') or {}).get('groups', [])}
        return tuple({'node_id': row['condition_id'], 'source': row['text'],
                      'instruction': ('判断当前图片是否满足本节点条件；只判断本项，无法判断返回 unknown。'
                                      '忽略张数、检索与打包。节点原文：' + row['text'] +
                                      ('。仅适用于类别：' + labels[row['group_id']] if row['group_id'] else '')),
                      'backend_ref': f'vlm.{row["condition_id"]}@1',
                      'predicate_ref': f'image.{row["condition_id"]}@1',
                      'rule_id': row['condition_id'],
                      **({'group_id': row['group_id']} if row['group_id'] else {})}
                     for row in rows)
    grouped = spec.get('grouped_expansion')
    if delivery_targets(grouped):
        return group_instruction_plan(spec['raw_request'], grouped)
    return instruction_plan(spec['raw_request'])


def plan_for_instructions(spec, instructions):
    """The confirmed plan that frozen model instructions were released against.

    Releases from before quantities were stripped froze the full request text.
    Both variants derive only from the confirmed spec, so accepting either for
    an existing release reads history without trusting caller input.
    """
    current = confirmed_instruction_plan(spec)
    try:
        return with_model_instructions(current, instructions)
    except ServiceError:
        if 'cleaning_conditions' in spec:
            raise
        grouped = spec.get('grouped_expansion')
        if not delivery_targets(grouped):
            raise
        legacy = group_instruction_plan(spec['raw_request'], grouped, strip_quantities=False)
        return with_model_instructions(legacy, instructions)


def with_model_instructions(plan, instructions):
    if (type(instructions) is not tuple or len(instructions) != len(plan)
            or any(not isinstance(text, str) or not 1 <= len(text) <= 1000
                   or node['source'] not in text
                   for node, text in zip(plan, instructions))):
        raise ServiceError('VLM_PLAN_INVALID',
                           'Frozen VLM instructions do not cover confirmed source', 409)
    return tuple({**node, 'instruction': text} for node, text in zip(plan, instructions))
