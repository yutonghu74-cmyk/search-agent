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


def instruction_plan(raw_request):
    if not isinstance(raw_request, str) or not raw_request.strip():
        raise ServiceError('VLM_RULE_REQUIRED', 'Confirmed image requirement is empty', 409)
    source = raw_request.strip()
    chunks = tuple(part.strip() for part in _SEPARATOR.split(source) if part.strip())
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


def group_instruction_plan(raw_request, grouped):
    """One check per quota group, each asked only of candidates that group retrieved.

    The punctuation splitter cannot serve quota tasks: "狗20张，猫30张" would
    become two nodes ANDed together and require a dog photo to also be a cat.
    Each check instead carries the whole request as context, so conditions that
    apply to every group still bind without being parsed out of the text.
    """
    source = raw_request.strip() if isinstance(raw_request, str) else ''
    groups = grouped['groups']
    if not source or len(source) > MAX_SOURCE_CHARS or len(groups) > MAX_NODES:
        raise ServiceError('VLM_PLAN_LIMIT',
                           'Group quotas need at most eight groups and a request of 320 characters; revise and confirm the request', 422)
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
    return tuple(plan)


def confirmed_instruction_plan(spec):
    """The plan a confirmed query spec owes, quota-aware. Single source of truth."""
    grouped = spec.get('grouped_expansion')
    if delivery_targets(grouped):
        return group_instruction_plan(spec['raw_request'], grouped)
    return instruction_plan(spec['raw_request'])


def with_model_instructions(plan, instructions):
    if (type(instructions) is not tuple or len(instructions) != len(plan)
            or any(not isinstance(text, str) or not 1 <= len(text) <= 1000
                   or node['source'] not in text
                   for node, text in zip(plan, instructions))):
        raise ServiceError('VLM_PLAN_INVALID',
                           'Frozen VLM instructions do not cover confirmed source', 409)
    return tuple({**node, 'instruction': text} for node, text in zip(plan, instructions))
