"""Versioned, pre-task conversation state. Model output is a proposal, never approval."""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import threading
import uuid
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path

from .errors import ServiceError
from .requirement_draft import (apply_edits, project_draft, render_draft, check_clarification, draft_from_state)
from .image_quantities import NUMBER, number_value, relative_image_count, is_agreement
from .expansion_models import MAX_EXPANSIONS, preview_edited_groups


PROMPT = '''你是图片检索对话助手。结合最新需求、当前词表、执行事实和近期双方对话，自然地用中文回应。
普通交流、解释、询问原因或状态：直接回答文字，不输出 JSON，不调用工具；默认最多两个短句，用户明确要详细说明时再展开。不要写提单说明书，不重复主体、数量、格式范围、流程或内部标识。问题本身不构成修改。
用户提供新需求事实、补充缺项或明确要求修改时，调用 update_request_draft 保存提案。部分信息也应先保存，不要求用户重说整段。reply 只说明本次变化，需要时只问一个真正缺失的问题。不要通过普通文字声称已修改。
例如“想找树下的小猫，不要水印”已有主体和约束：先用工具保存 subject、media_type=image、constraints，再在 reply 问张数；不要只发一条追问却不保存已有信息。“为什么要张数？”则直接文字解释。
current_request/effective_request 是当前草稿；raw_request 仅为原话资料，可能包含已撤销的旧要求。指代和“其他不变”结合当前草稿及对话理解。reference_request 属于另一需求，只继承用户明确引用的条件；新主体替换旧主体，旧数量不自动继承。
stage=terms 表示用户正在审阅检索词，未说明“图片”时有关组和词数的修改针对词表。解释为什么选词时结合 current_groups。current_term_counts 是现有词数。
允许虚构或不常见画面，不推断图片是否存在。“每个类型要20张”指每类图片20张，结合当前列举的类别计算总数，保留各类配额，不要再问总张数；类别无法确定时只问具体类别。
一只动物、两个人等是画面内容数量，不是目标图片数；“一些”没有指定图片数量。当前只支持图片。无执行事实不声称已检索、已找到、已批准或已交付。
例如 execution_context.search_started=false 且 stage=terms 时，用户问“为什么还没开始检索？”：直接说明词表仍待用户点击确认，确认后才会检索；不要重新保存需求、修改词表或扩词。
理解完整语义后再保存。多类需求以 requirement_draft.categories 为权威；每个类别分别保存 label、scene、target_count、conditions，category_id 稳定。逐图对象数量保留在 scene 中，不用于图片张数。共同条件用 constraints，类别专属条件用下列操作，不能提升成共同条件。
首次保存或替换主体时，无论一个还是多个类别，都必须用 requirement_edits 的 add_category 保存逐类画面；subject 仅兼容历史草稿，不能代替首次类别拆解。类别数量/画面/专属条件的后续修改必须用 requirement_edits；仅改变本轮明确提到的字段，未提及项保留，已有类别专属条件不能复制到共同 constraints；没有修改共同条件时 constraints=null。不要为多类修改同时重写 subject/target_count；总数由后端根据各类数量计算。用户只给总数时可用 target_count，不平均分配。无依据不补动作、环境、品种或条件。
requirement_edits: 操作数组，每项含 op、targets、source_text 和该操作需要的字段；省略无关字段，不能把原话填入无关字段。targets 不用时为 []。
- add_category: targets=[]，label 为原话中的类别短名，scene 为原话中的完整画面，count 可为明确图片数量，否则 null。
- update_category: targets=[已有 category_id]，只给变化的 label/scene，不传其他字段。remove_category: targets=[已有 category_id]，不传其他字段。
- set_count: targets 显式列出作用类别的 category_id（同轮新类别可用 label），count 为每类张数，不传其他字段。“各20张”对所有当前类别用一项操作，不能把20当总数。
- add_condition/remove_condition: targets 列出适用类别，text 为条件；删除引用已有条件，不删除其他条件。
所有操作 source_text 逐字引用本轮原话。仅迁移尚未结构化的旧主体时，add_category 可引用 current_request.subject 的原文片段；随后用本轮原话补数量。不修改已有草稿的字段不要重新生成。用户只改画面时只给 scene，保留原 label 和 category_id；不要顺便改名。scene 可保留原画面中的主体和对象数量，不能新增无依据细节。
例如用户“毛驴奔跑、狗吃饭、兔子睡觉的图，每类20张”：新增三个类别，scene 分别是“毛驴奔跑”“狗吃饭”“兔子睡觉”，再 set_count targets=[毛驴,狗,兔子] count=20 source_text=“每类20张”，media_type=image，不再问总数。后续“狗改为30张”只对狗 set_count。后续“兔子不要了”只 remove_category。后续“狗不要水印”只对狗 add_condition。
保存前检查本轮每个类别、动作、否定和数量作用范围是否已覆盖。没有依据的具体细节不追问。clarification 可为 {field:media_type|subject|target_count|scope|conflict,source_text:本轮原话,question:具体问题}；只在缺项或影响检索的歧义/冲突时给出，否则 null。已知张数不得再次问张数。能保存的非歧义信息先保存，用 action=update 配合 clarification，只追问未决部分。
工具 proposal 字段（仅修改工具需要这些字段，未改变项省略或 null）：
action: update 补充/修改需求；revise_terms 修改已有词表或其图片目标；replace 另起完全不同需求；clarify 记录修改歧义；confirm/confirm_terms 仅表达对已有摘要/词表的同意，实际批准仍须本人点击确认按钮。
subject: 完整主体或场景，不混入图片张数。target_count: 新指定的图片总数 1–10000，不能用词数或画面物体数量。
media_type: image/video/text。constraints: 完整的新约束文本，保留未删除条件及否定。null 表示不修改；撤销全部约束必须传空字符串 ""，不能传 null。例如原约束“不要有人”，用户说“算了有人也行，无所谓”，提交 action=update、constraints=""。
已有猫图100张时，用户说“不要有人”：提交 action=update、constraints="不要有人"，不改主体数量；随后说“算了有人也行，无所谓”：提交 action=update、constraints=""。这两轮都不是 confirm 或普通 answer；只有成功保存后才能说已更新。
reply: 对用户的自然回应，最多 600 字。question: 确实缺失/歧义时的具体问题，否则 null。类型、主体、图片数量已明确时不再问摘要对不对。
count_edits: 数量操作列表，每项 {target:images|terms, labels:[], operation:set|add|scale, value:数字}。图片 labels 为空；词数每项只选一个已有组，多组分开写。翻倍 scale 2，减半 scale 0.5，加减用 add，明确总数用 set。后端计算并向上取整。每组最终词数 1–200。不要同时在 target_count 重复同一相对操作。
expansion_instruction: 需要改词义/范围时的完整指令，消解指代并保留其他组。只改数量时可省略。不要承诺未在字段/操作中表达的改动。
例如：主体已知、正在问张数时，“需要20张”只补 target_count=20，不再要求重复主体或确认摘要。缺项时保存已知字段并在 reply 中自然追问。已有修复反馈 repair 时按其原因修正，不重复被拒绝提案。'''


_CATEGORY_COUNT = re.compile(r'(?P<count>[1-9][0-9]{0,4})\s*(?:只|张)\s*'
                             r'(?P<label>[\u4e00-\u9fff]{1,12}?)'
                             r'(?=(?:(?:和|及|与)?[1-9][0-9]{0,4}\s*(?:只|张))|[，,、；;\s]|$)')
# "狗20张、猫30张". Anchored to a list boundary so the label can't swallow what
# precedes it mid-sentence; a leading verb such as "找" is stripped afterwards.
_LABEL_FIRST_COUNT = re.compile(r'(?:^|(?<=[，,、；;\s和及与]))'
                                r'(?P<label>[一-鿿]{1,12}?)'
                                r'(?:的?(?:图片|照片|图像|图))?\s*'
                                r'(?P<count>[1-9][0-9]{0,4})\s*(?:只|张)')


def explicit_category_targets(message, subject=None):
    """Read explicit animal-category quantities, not an inferred search result count."""
    # Resolve an explicit equal allocation only against an enumerated subject.
    each_type = re.fullmatch(
        rf'\s*(?:(?P<subject>.+?)[，,；;]\s*)?(?:每(?:个)?(?:类型|类别|种类)|每类|每种)'
        rf'\s*(?:都)?(?:要|需要)?\s*(?P<count>{NUMBER})\s*张(?:图片|照片)?[。.!！]?\s*', message)
    if each_type:
        source = each_type['subject'] or subject or ''
        source = re.sub(r'的?(?:图片|照片|图像|图)$', '', source.strip())
        labels = [label.strip() for label in re.split(r'[、，,；;]|和|与|及', source)]
        count = number_value(each_type['count'])
        if (2 <= len(labels) <= 20 and len(set(labels)) == len(labels)
                and all(re.fullmatch(r'[一-鿿A-Za-z0-9 ]{1,32}', label) for label in labels)
                and 1 <= count and count * len(labels) <= 10000):
            return [{'label': label, 'count': count} for label in labels]
        return []
    # A per-image scene or an explicit overall total is never a category list.
    if re.search(r'每(?:张|幅|个)|一共|总共|总计|合计', message):
        return []
    each = re.fullmatch(rf'\s*(.+?)各\s*({NUMBER})\s*张[。.!！]?\s*', message)
    if each:
        labels = re.split(r'和|与|及|、', each[1])
        labels = [re.sub(r'的?(?:图片|照片|图像|图)$', '', label.strip()) for label in labels]
        count = number_value(each[2])
        if (2 <= len(labels) <= 20 and all(re.fullmatch(r'[一-鿿A-Za-z]{1,12}', label) for label in labels)
                and len(set(labels)) == len(labels) and 1 <= count and count * len(labels) <= 10000):
            return [{'label': label, 'count': count} for label in labels]
        return []
    targets = []
    for match in _CATEGORY_COUNT.finditer(message):
        label = match.group('label')
        for suffix in ('的图片', '的照片', '图片', '照片', '图像', '图'):
            if label.endswith(suffix):
                label = label[:-len(suffix)]
                break
        if not label:
            targets = []
            break
        targets.append({'label': label, 'count': int(match.group('count'))})
    if len(targets) < 2:
        targets = [{'label': re.sub(r'^(?:帮我找|帮我|请找|查找|寻找|找|要)', '', match.group('label')),
                    'count': int(match.group('count'))}
                   for match in _LABEL_FIRST_COUNT.finditer(message)]
        if any(not item['label'] for item in targets):
            return []
    # This helper represents an explicit allocation across categories. A single
    # ordinary phrase such as "5张猫图片" still belongs to normal dialogue
    # interpretation so constraints and intent are not reduced to a regex.
    if len(targets) < 2 or sum(item['count'] for item in targets) > 10000:
        return []
    return targets


_TERM_COUNT_PATTERNS = (
    re.compile(r'(?:扩词|检索词|召回词|词数)(?:数量)?(?:改成|调整到|到|为|要)?\s*([1-9][0-9]{0,3})\s*(?:个|条)?'),
    re.compile(r'([1-9][0-9]{0,3})\s*(?:个|条)\s*(?:扩词|检索词|召回词)'),
)

_IMAGE_COUNT_ONLY = re.compile(
    r'^\s*(?:(?:把|将)?(?:图片|照片|目标|数量|张数)?\s*)?'
    r'(?:改成|调整为|设为|换成)\s*([1-9][0-9]{0,4})\s*张(?:图片|照片)?'
    r'(?:[，,、；;\s]*其他不变)?[。.]?\s*$')


def explicit_image_count_update(message, current_count):
    """A complete, absolute image-count edit with no other request change."""
    match = _IMAGE_COUNT_ONLY.fullmatch(message)
    if match is None or current_count is None:
        return None
    count = int(match.group(1))
    return count if 1 <= count <= 10000 and count != current_count else None


def explicit_simple_image_request(message):
    """Read only a fully explicit, unqualified first request; keep subjects literal."""
    subject = r'(?P<subject>[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9、· &+\-]{0,79}?)'
    opening = r'\s*(?:我)?(?:想要|需要|帮我找|请找|找)\s*'
    object_opening = r'\s*(?=[一二两三四五六七八九十百]+(?:只|头|条|匹|位|个|辆|棵|朵|座))'
    count = r'(?P<count>[1-9][0-9]{0,4})\s*张'
    for pattern in (opening + subject + r'(?:的)?(?:照片|图片)[，,\s]+' + count + r'[。！!.]?\s*',
                    object_opening + subject + r'(?:的)?(?:照片|图片)[，,\s]+' + count + r'[。！!.]?\s*',
                    opening + count + r'\s*' + subject + r'(?:的)?(?:照片|图片)[。！!.]?\s*'):
        match = re.fullmatch(pattern, message)
        if not match:
            continue
        value = match['subject'].strip()
        # A literal object count (一只骆驼) belongs to the scene. It is not
        # the restrictive word 只, nor the requested number of images.
        unqualified = re.sub(r'^[一二两三四五六七八九十百]+(?:只|头|条|匹|位|个|辆|棵|朵|座)', '', value)
        if (not value or value in {'素材', '这些', '那些', '它们', '图片', '照片'}
                or not unqualified or re.search(r'不|无|没|别|非|除|仅|只|的|每|各|分别|条件|同样|一样|相同|之前|刚才|上述|至少|最多|最少|至多|为什么|怎么|多少|能否|是否|吗|呢|什么', unqualified)):
            return None
        quantity = int(match['count'])
        if quantity <= 10000:
            return {'subject': value, 'target_count': quantity}
    return None


def explicit_scene_request(message):
    """A literal scene's object count is never the requested image count."""
    value = message.strip().rstrip('。.!！')
    if (re.fullmatch(r'[一二两三四五六七八九十百0-9]+(?:只|头|条|匹|位|个|辆|棵|朵|座)'
                     r'[\u4e00-\u9fffA-Za-z0-9 、·&+\-]{1,100}', value)
            and not re.search(r'多少|什么|怎么|为何|为什么|吗|呢|是否|能否|不|无|没|别|非|除|至少|最多|最少|至多|图片|照片|张', value)):
        return {'subject': value, 'target_count': None}
    return None


def explicit_group_term_target(message, groups):
    """Return one explicit existing-group term target, otherwise defer to the model."""
    counts = {int(match.group(1)) for pattern in _TERM_COUNT_PATTERNS
              for match in pattern.finditer(message)}
    labels = [group['primary_label'] for group in groups or []
              if group['primary_label'] in message]
    if len(counts) != 1 or len(labels) != 1:
        return None
    return {'label': labels[0], 'count': counts.pop()}


def resolve_count_edits(edits, image_count, groups):
    """Evaluate bounded model proposals against the current draft; never approve them."""
    if not isinstance(edits, list) or len(edits) > 21:
        raise ValueError('count_edits must be a bounded list')
    counts = {group['primary_label']: len(group['auxiliary_terms']) for group in groups or []}
    touched, term_targets = set(), None
    for edit in edits:
        if (not isinstance(edit, dict) or set(edit) != {'target', 'labels', 'operation', 'value'}
                or edit['target'] not in ('images', 'terms')
                or edit['operation'] not in ('set', 'add', 'scale')
                or not isinstance(edit['labels'], list)
                or any(not isinstance(label, str) for label in edit['labels'])
                or len(set(edit['labels'])) != len(edit['labels'])
                or type(edit['value']) not in (int, float) or not math.isfinite(edit['value'])
                or abs(edit['value']) > 10000):
            raise ValueError('invalid count edit')
        if edit['target'] == 'images':
            if edit['labels']:
                raise ValueError('image total cannot select term groups')
            selected = {'images': image_count}
            limit = 10000
        else:
            labels = edit['labels'] or list(counts)
            if not labels or any(label not in counts for label in labels):
                raise ValueError('term edit requires existing group labels')
            selected = {label: counts[label] for label in labels}
            term_targets = dict(counts) if term_targets is None else term_targets
            limit = MAX_EXPANSIONS
        for label, previous in selected.items():
            key = (edit['target'], label)
            if key in touched:
                raise ValueError('conflicting edits for the same count')
            touched.add(key)
            operation, value = edit['operation'], edit['value']
            if operation != 'scale' and value != int(value):
                raise ValueError('set/add requires an integer')
            if operation != 'set' and previous is None:
                raise ValueError('relative edit needs an existing count')
            result = value if operation == 'set' else previous + value if operation == 'add' else previous * value
            result = math.ceil(result)
            if not 1 <= result <= limit:
                raise ValueError(f'{edit["target"]} count must be 1–{limit}')
            if edit['target'] == 'images':
                image_count = result
            else:
                term_targets[label] = result
    return image_count, term_targets


def _recover_explicit_category_count(state):
    """Repair older unconfirmed sessions that lost a plainly stated category total."""
    if state.get('stage') != 'request' or state.get('target_count') is not None:
        return state
    messages = state.get('messages') or []
    for item in reversed(messages[state.get('context_start', 0):]):
        if item.get('role') != 'user':
            continue
        targets = explicit_category_targets(item.get('text', ''))
        if not targets:
            continue
        if state.get('subject') and not all(target['label'] in state['subject'] for target in targets):
            continue
        repaired = dict(state)
        repaired['category_targets'] = targets
        repaired['target_count'] = sum(target['count'] for target in targets)
        repaired['media_type'] = repaired.get('media_type') or 'image'
        repaired['subject'] = repaired.get('subject') or '、'.join(target['label'] for target in targets)
        repaired['question'] = 'confirm_summary'
        return repaired
    return state


def _safe_natural_reply(value):
    if not isinstance(value, str):
        return None
    reply = value.strip()
    if (not reply or len(reply) > 600
            or re.search(r'[\x00-\x09\x0b-\x1f\x7f]', reply)
            or re.search(r'已(?:经)?(?:批准|确认|开始检索|完成检索|找到|清洗)|正在检索|检索结果(?:已|为)|无需(?:确认|批准)', reply)):
        return None
    return reply


def _new_dialogue_state():
    return {'subject': None, 'target_count': None, 'media_type': None,
            'constraints': [], 'stage': 'request', 'question': None,
            'messages': [], 'context_start': 0, 'raw_request': '',
            'message_count': 0,
            'effective_request': None, 'reference_request': None,
            # Distinguish real Chat containers from legacy rows that older UI
            # versions created for individual requests.  Legacy rows remain
            # addressable, but the history UI can stop presenting each one as
            # an independent Chat.
            'chat_container_version': 1}


class DialogueStore:
    def __init__(self, path: Path | None = None):
        self.lock = threading.RLock()
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if path.is_symlink():
                raise ValueError('dialogue ledger must not be a symlink')
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
            except FileExistsError:
                pass
            else:
                os.close(fd)
            os.chmod(path, 0o600)
        self.connection = sqlite3.connect(str(path) if path else ':memory:', check_same_thread=False)
        self.connection.execute('''CREATE TABLE IF NOT EXISTS dialogue_sessions (
            session_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
            project_id TEXT NOT NULL, version INTEGER NOT NULL, state TEXT NOT NULL,
            created_at TEXT, updated_at TEXT)''')
        columns = {row[1] for row in self.connection.execute('PRAGMA table_info(dialogue_sessions)')}
        for column in ('created_at', 'updated_at'):
            if column not in columns:
                self.connection.execute(f'ALTER TABLE dialogue_sessions ADD COLUMN {column} TEXT')
        self.connection.execute('CREATE INDEX IF NOT EXISTS dialogue_history_scope ON dialogue_sessions '
                                '(tenant_id,user_id,project_id,created_at DESC,session_id DESC)')
        now = datetime.now(UTC).isoformat()
        self.connection.execute('UPDATE dialogue_sessions SET created_at=COALESCE(created_at,?), updated_at=COALESCE(updated_at,?)',
                                (now, now))
        self.connection.execute('''CREATE TABLE IF NOT EXISTS task_dialogue_turns (
            seq INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
            project_id TEXT NOT NULL, task_id TEXT NOT NULL, task_version INTEGER NOT NULL,
            user_text TEXT NOT NULL, assistant_text TEXT NOT NULL)''')
        self.connection.execute('''CREATE TABLE IF NOT EXISTS task_dialogue_origins (
            task_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
            project_id TEXT NOT NULL, session_id TEXT NOT NULL, messages TEXT NOT NULL)''')
        self.connection.execute('''CREATE TABLE IF NOT EXISTS dialogue_transcript (
            seq INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
            role TEXT NOT NULL, text TEXT NOT NULL)''')
        self.connection.execute('CREATE INDEX IF NOT EXISTS dialogue_transcript_session '
                                'ON dialogue_transcript(session_id,seq)')
        self.connection.commit()

    def close(self):
        self.connection.close()

    def __del__(self):
        connection = getattr(self, 'connection', None)
        if connection is not None:
            connection.close()

    def read(self, actor, project_id, session_id, *, include_transcript=False):
        with self.lock:
            row = self.connection.execute('SELECT tenant_id,user_id,project_id,version,state FROM dialogue_sessions WHERE session_id=?',
                                          (session_id,)).fetchone()
            if row is None or row[:3] != (actor.tenant_id, actor.user_id, project_id):
                raise ServiceError('DIALOGUE_NOT_FOUND', 'Conversation not found', 404)
            state = {'session_id': session_id, 'version': row[3], **json.loads(row[4])}
            if include_transcript:
                rows = self.connection.execute('SELECT role,text FROM dialogue_transcript '
                    'WHERE session_id=? ORDER BY seq', (session_id,)).fetchall()
                state['_transcript'] = ([{'role': role, 'text': text} for role, text in rows]
                                        if rows else state.get('messages', []))
            return state

    def messages(self, actor, project_id, session_id):
        return self.read(actor, project_id, session_id, include_transcript=True)['_transcript']

    def _append_messages(self, session_id, messages):
        self.connection.executemany('INSERT INTO dialogue_transcript(session_id,role,text) VALUES (?,?,?)',
                                   [(session_id, item['role'], item['text']) for item in messages
                                    if item.get('role') in ('user', 'assistant')
                                    and isinstance(item.get('text'), str) and item['text'].strip()])

    def task_history(self, actor, project_id, task_id, *, limit=10):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ServiceError('INVALID_SPEC', 'Task history limit must be 1..100', 422)
        with self.lock:
            rows = self.connection.execute('''SELECT user_text, assistant_text FROM task_dialogue_turns
                WHERE tenant_id=? AND user_id=? AND project_id=? AND task_id=? ORDER BY seq DESC LIMIT ?''',
                (actor.tenant_id, actor.user_id, project_id, task_id, limit)).fetchall()
        return [message for user, assistant in reversed(rows) for message in
                ({'role': 'user', 'text': user}, {'role': 'assistant', 'text': assistant})]

    def history_page(self, actor, project_id, *, after=None, limit=21):
        where = 'tenant_id=? AND user_id=? AND project_id=? AND json_array_length(state, "$.messages")>0'
        args = [actor.tenant_id, actor.user_id, project_id]
        if after:
            where += ' AND (created_at,session_id)<(?,?)'
            args.extend(after)
        with self.lock:
            rows = self.connection.execute("SELECT session_id,version,state,created_at,updated_at "
                "FROM dialogue_sessions WHERE " + where +
                " ORDER BY created_at DESC,session_id DESC LIMIT ?", (*args, limit)).fetchall()
        result = []
        for sid, version, payload, created, updated in rows:
            state = json.loads(payload)
            messages = state.get('messages') or []
            title = next((m.get('text', '').strip() for m in messages
                          if m.get('role') == 'user' and m.get('text', '').strip()), '新 Chat')
            result.append({'id': sid, 'kind': 'dialogue', 'title': title[:80],
                           'message_count': state.get('message_count', len(messages)), 'created_at': created,
                           'updated_at': updated, 'version': version})
        return result

    def list(self, actor, project_id):
        with self.lock:
            rows = self.connection.execute('''SELECT session_id,version,state,created_at,updated_at
                FROM dialogue_sessions WHERE tenant_id=? AND user_id=? AND project_id=?
                ORDER BY updated_at DESC LIMIT 100''',
                (actor.tenant_id, actor.user_id, project_id)).fetchall()
        result = []
        for session_id, version, raw_state, created_at, updated_at in rows:
            state = json.loads(raw_state)
            messages = state.get('messages') or []
            first = next((item.get('text', '').strip() for item in messages
                          if item.get('role') == 'user' and item.get('text', '').strip()), '')
            result.append({'session_id': session_id, 'version': version,
                           'stage': state.get('stage', 'request'),
                           'title': first[:80] or '新 Chat',
                           'message_count': state.get('message_count', len(messages)),
                           'created_at': created_at, 'updated_at': updated_at,
                           'deletable': True,
                           'history_visible': state.get('chat_container_version') == 1})
        return result

    def delete(self, actor, project_id, session_id):
        with self.lock:
            cursor = self.connection.execute('''DELETE FROM dialogue_sessions
                WHERE session_id=? AND tenant_id=? AND user_id=? AND project_id=?''',
                (session_id, actor.tenant_id, actor.user_id, project_id))
            if cursor.rowcount == 1:
                self.connection.execute('DELETE FROM dialogue_transcript WHERE session_id=?', (session_id,))
            self.connection.commit()
        if cursor.rowcount != 1:
            raise ServiceError('DIALOGUE_NOT_FOUND', 'Conversation not found', 404)
        return {'session_id': session_id, 'deleted': True}

    def archive_to_task(self, actor, project_id, session_id, task_id):
        """Keep the pre-confirmation clarification with its task, then retire the session."""
        owner = (actor.tenant_id, actor.user_id, project_id)
        with self.lock:
            origin = self.connection.execute('''SELECT tenant_id,user_id,project_id,session_id,messages
                FROM task_dialogue_origins WHERE task_id=?''', (task_id,)).fetchone()
            if origin is not None:
                if origin[:4] != (*owner, session_id):
                    raise ServiceError('DIALOGUE_CONFLICT', 'Task already has another clarification', 409)
                self.connection.execute('''DELETE FROM dialogue_sessions WHERE session_id=?
                    AND tenant_id=? AND user_id=? AND project_id=?''', (session_id, *owner))
                self.connection.commit()
                return {'session_id': session_id, 'task_id': task_id, 'archived': True,
                        'message_count': len(json.loads(origin[4]))}
            row = self.connection.execute('''SELECT tenant_id,user_id,project_id,state
                FROM dialogue_sessions WHERE session_id=?''', (session_id,)).fetchone()
            if row is None or row[:3] != owner:
                raise ServiceError('DIALOGUE_NOT_FOUND', 'Conversation not found', 404)
            transcript = self.connection.execute('SELECT role,text FROM dialogue_transcript '
                                                 'WHERE session_id=? ORDER BY seq', (session_id,)).fetchall()
            source = ([{'role': role, 'text': text} for role, text in transcript] if transcript
                      else json.loads(row[3]).get('messages') or [])
            messages = [{'role': item['role'], 'text': item['text']}
                        for item in source
                        if item.get('role') in {'user', 'assistant'}
                        and isinstance(item.get('text'), str) and item['text'].strip()]
            self.connection.execute('INSERT INTO task_dialogue_origins VALUES (?,?,?,?,?,?)',
                (task_id, *owner, session_id, json.dumps(messages, ensure_ascii=False)))
            self.connection.execute('DELETE FROM dialogue_sessions WHERE session_id=?', (session_id,))
            self.connection.execute('DELETE FROM dialogue_transcript WHERE session_id=?', (session_id,))
            self.connection.commit()
        return {'session_id': session_id, 'task_id': task_id, 'archived': True,
                'message_count': len(messages)}

    def task_origin(self, actor, project_id, task_id):
        with self.lock:
            row = self.connection.execute('''SELECT messages FROM task_dialogue_origins
                WHERE task_id=? AND tenant_id=? AND user_id=? AND project_id=?''',
                (task_id, actor.tenant_id, actor.user_id, project_id)).fetchone()
        return json.loads(row[0]) if row else []

    def append_task_turn(self, actor, project_id, task_id, task_version, user, assistant):
        with self.lock:
            self.connection.execute('''INSERT INTO task_dialogue_turns
                (tenant_id,user_id,project_id,task_id,task_version,user_text,assistant_text) VALUES (?,?,?,?,?,?,?)''',
                (actor.tenant_id, actor.user_id, project_id, task_id, task_version, user, assistant))
            self.connection.commit()

    def save(self, actor, project_id, session_id, version, state, *, new_messages=None):
        with self.lock, self.connection:
            return self._save(actor, project_id, session_id, version, state, new_messages=new_messages)

    def _save(self, actor, project_id, session_id, version, state, *, new_messages=None, new_session_id=None):
        """Internal write; the caller owns the connection transaction and lock."""
        if session_id is None:
            session_id = new_session_id or 'dlg_' + uuid.uuid4().hex
            now = datetime.now(UTC).isoformat()
            self.connection.execute('INSERT INTO dialogue_sessions VALUES (?,?,?,?,?,?,?,?)',
                (session_id, actor.tenant_id, actor.user_id, project_id, 1,
                 json.dumps(state, ensure_ascii=False), now, now))
            self._append_messages(session_id, state.get('messages', []))
            return session_id, 1
        previous = self.connection.execute('SELECT state FROM dialogue_sessions WHERE session_id=? '
            'AND version=? AND tenant_id=? AND user_id=? AND project_id=?',
            (session_id, version, actor.tenant_id, actor.user_id, project_id)).fetchone()
        if previous is None:
            raise ServiceError('DIALOGUE_CONFLICT', 'Conversation changed; reload it', 409)
        cursor = self.connection.execute('UPDATE dialogue_sessions SET version=?,state=?,updated_at=? WHERE session_id=? AND version=? AND tenant_id=? AND user_id=? AND project_id=?',
            (version + 1, json.dumps(state, ensure_ascii=False), datetime.now(UTC).isoformat(), session_id, version,
             actor.tenant_id, actor.user_id, project_id))
        if cursor.rowcount != 1:
            raise ServiceError('DIALOGUE_CONFLICT', 'Conversation changed; reload it', 409)
        if new_messages is None:
            self.connection.execute('DELETE FROM dialogue_transcript WHERE session_id=?', (session_id,))
            self._append_messages(session_id, state.get('messages', []))
        else:
            if not self.connection.execute('SELECT 1 FROM dialogue_transcript WHERE session_id=? LIMIT 1',
                                           (session_id,)).fetchone():
                self._append_messages(session_id, json.loads(previous[0]).get('messages', []))
            self._append_messages(session_id, new_messages)
        return session_id, version + 1


class DialogueCoordinator:
    def __init__(self, store, runtime, projects):
        self.store, self.runtime, self.projects = store, runtime, projects

    def start(self, actor, project_id):
        """Create exactly one empty Chat container before its first message."""
        self.projects.require_access(actor, project_id, 'default', 'create')
        session_id, version = self.store.save(actor, project_id, None, 0, _new_dialogue_state())
        return {'schema_version': 'dialogue-turn@1', 'session_id': session_id,
                'version': version, 'stage': 'request', 'question': None,
                'summary': '尚未填写需求', 'request': {'raw_request': '', 'effective_request': '', 'target_count': None,
                                                     'media_type': None, 'subject': None},
                'messages': []}

    def turn(self, actor, project_id, body, *, receipt=None):
        if receipt is not None and receipt.plan is not None:
            self.projects.require_access(actor, project_id, 'default', 'create')
            if receipt.plan.get('kind') != 'dialogue' or receipt.plan['project_id'] != project_id:
                raise ServiceError('COMMAND_SCOPE_CONFLICT', 'Conversation checkpoint scope differs', 409)
            return receipt.plan['result']
        if set(body) - {'session_id', 'expected_version', 'message', 'llm_id', 'phase', 'count_hint', 'current_groups', 'current_target_count', 'generate_terms_when_complete', 'reference_request'} or not {'session_id', 'expected_version', 'message', 'llm_id', 'phase'} <= set(body):
            raise ServiceError('INVALID_SPEC', 'Invalid conversation turn', 422)
        session_id, version, message, llm_id, phase = (body[key] for key in
            ('session_id', 'expected_version', 'message', 'llm_id', 'phase'))
        count_hint = body.get('count_hint')
        if (session_id is not None and (not isinstance(session_id, str) or not session_id.startswith('dlg_'))
                or type(version) is not int or version < 0 or not isinstance(message, str)
                or not 1 <= len(message.strip()) <= 2000 or not isinstance(llm_id, str)
                or phase not in ('request', 'terms')
                or count_hint is not None and (type(count_hint) is not int or not 1 <= count_hint <= 10000)
                or type(body.get('generate_terms_when_complete', False)) is not bool):
            raise ServiceError('INVALID_SPEC', 'Invalid conversation turn', 422)
        self.projects.require_access(actor, project_id, 'default', 'create')
        if session_id is None:
            if version != 0 or phase != 'request':
                raise ServiceError('DIALOGUE_CONFLICT', 'New conversation must start at version zero', 409)
            prior = _new_dialogue_state()
        else:
            current = self.store.read(actor, project_id, session_id)
            if version != current['version'] or phase != current['stage']:
                raise ServiceError('DIALOGUE_CONFLICT', 'Conversation changed; reload it', 409)
            prior = {key: value for key, value in current.items() if key not in ('version', 'session_id')}
            # Continuing an old session upgrades it to the current one-Chat
            # container model without rewriting or deleting its history.
            prior['chat_container_version'] = 1
            if 'raw_request' not in prior:
                prior['raw_request'] = '\n'.join(item['text'] for item in
                    prior['messages'][prior.get('context_start', 0):] if item.get('role') == 'user')
            prior = _recover_explicit_category_count(prior)
        reference = body.get('reference_request')
        if reference is not None:
            if (not isinstance(reference, str) or not 1 <= len(reference.strip()) <= 2000
                    or prior['messages'] or phase != 'request'):
                raise ServiceError('INVALID_SPEC', 'Reference request is only accepted on the first turn', 422)
            prior['reference_request'] = reference.strip()
        if 'current_groups' in body:
            try:
                if phase != 'terms':
                    raise ValueError('groups require terms phase')
                preview_edited_groups(None, body['current_groups'])
            except ValueError:
                raise ServiceError('INVALID_SPEC', 'Invalid current draft terms', 422) from None
            prior['current_groups'] = body['current_groups']
        if 'current_target_count' in body:
            count = body['current_target_count']
            if phase != 'terms' or type(count) is not int or not 1 <= count <= 10000:
                raise ServiceError('INVALID_SPEC', 'Invalid current draft image count', 422)
            prior['target_count'] = count
        structured = prior.get('requirement_draft') is not None
        category_targets = (explicit_category_targets(message, prior.get('subject'))
                            if phase == 'request' and not structured else [])
        term_target = explicit_group_term_target(message, prior.get('current_groups')) if phase == 'terms' else None
        image_target = (explicit_image_count_update(message, prior.get('target_count'))
                        if prior.get('media_type') == 'image' else None)
        if image_target is None and prior.get('media_type') == 'image':
            image_target = relative_image_count(message, prior.get('target_count'))
        if structured and phase == 'request':
            image_target = None
        if count_hint is not None and count_hint != image_target:
            image_target = None
        simple_request = (explicit_simple_image_request(message)
                          if body.get('generate_terms_when_complete', False) and phase == 'request'
                          and not prior['messages'] and not prior.get('subject')
                          and not prior.get('reference_request') else None)
        if (simple_request is None and body.get('generate_terms_when_complete', False)
                and phase == 'request' and not prior['messages'] and not prior.get('subject')
                and not prior.get('reference_request')):
            simple_request = explicit_scene_request(message)
            if simple_request is not None and count_hint is not None:
                simple_request['target_count'] = count_hint
        if (body.get('generate_terms_when_complete', False) and phase == 'request'
                and (not structured or len(prior['requirement_draft']['categories']) == 1)
                and prior.get('question') == 'target_count' and prior.get('subject')
                and prior.get('media_type') == 'image'):
            answer = re.fullmatch(r'\s*(?:要|需要|共|总共)?\s*([1-9][0-9]{0,4})\s*(?:张(?:图片|照片)?)?[。.!！]?\s*', message)
            if answer and int(answer[1]) <= 10000:
                image_target = int(answer[1])
                if count_hint is not None and count_hint != image_target:
                    image_target = None
        if simple_request and count_hint is not None and simple_request['target_count'] != count_hint:
            simple_request = None
        agreement = (phase == 'request' and prior.get('question') == 'confirm_summary'
                     and is_agreement(message))
        model_interpreted = False
        if term_target and term_target['count'] > MAX_EXPANSIONS:
            proposal = {'action': 'clarify', 'subject': None, 'target_count': None,
                        'media_type': None, 'constraints': None,
                        'question': (f"每组最多 {MAX_EXPANSIONS} 个检索词。请把"
                                     f"{term_target['label']}组词数改为 1 到 {MAX_EXPANSIONS} 之间的整数。"),
                        'reply': None, 'count_edits': [], 'expansion_instruction': None}
        elif term_target:
            proposal = {'action': 'revise_terms', 'subject': None, 'target_count': None,
                        'media_type': None, 'constraints': None, 'question': None,
                        'reply': None, 'count_edits': [{'target': 'terms',
                            'labels': [term_target['label']], 'operation': 'set',
                            'value': term_target['count']}],
                        'expansion_instruction': (f"把{term_target['label']}组调整为"
                                                  f"{term_target['count']}个检索词，其他组不变。")}
        elif image_target is not None:
            proposal = {'action': 'revise_terms' if phase == 'terms' else 'update',
                        'subject': None, 'target_count': image_target,
                        'media_type': None, 'constraints': None, 'question': None,
                        'reply': f'已改为 {image_target} 张。', 'count_edits': [],
                        'expansion_instruction': None}
        elif agreement:
            proposal = {'action': 'confirm', 'subject': None, 'target_count': None,
                        'media_type': None, 'constraints': None, 'question': None,
                        'reply': None, 'count_edits': [], 'expansion_instruction': None}
        elif simple_request:
            proposal = {'action': 'update', **simple_request,
                        'media_type': 'image', 'constraints': None, 'question': None,
                        'reply': None, 'count_edits': [], 'expansion_instruction': None}
        elif (category_targets and count_hint is None
              and all(re.fullmatch(r'[一-鿿A-Za-z]{1,4}', item['label']) for item in category_targets)):
            # A bounded list of explicit per-category image quantities is already
            # authoritative. Avoid a slow model round trip merely to restate it;
            # the user still reviews the generated summary and term table.
            proposal = {'action': 'update',
                        'subject': '、'.join(item['label'] for item in category_targets),
                        'target_count': sum(item['count'] for item in category_targets),
                        'media_type': 'image', 'constraints': None, 'question': None,
                        'reply': None, 'count_edits': [], 'expansion_instruction': None}
        else:
            try:
                scope = getattr(self.runtime, 'business_scope', None)
                with (scope(project_id=project_id, dialogue_session_id=session_id, expected_version=version)
                      if scope else nullcontext()):
                    proposal = self.runtime.interpret_dialogue_turn(prior, message, llm_id, count_hint=count_hint)
                    model_interpreted = True
            except ServiceError as error:
                if error.code != 'MODEL_OUTPUT_INVALID':
                    raise
                proposal = {'action': 'clarify', 'subject': None, 'target_count': None,
                            'media_type': None, 'constraints': None,
                            'question': '这次生成的修改未通过校验，之前的需求保持不变。请重试这条消息。'}
        if proposal.get('requirement_edits'):
            category_targets = []
        action = proposal['action']
        model_action = action
        if (phase == 'request' and prior.get('question') == 'confirm_summary'
                and is_agreement(message)):
            action = 'confirm'
        if category_targets and count_hint is None:
            if action in ('clarify', 'confirm'):
                action = 'update'
            proposal['target_count'] = sum(item['count'] for item in category_targets)
            if proposal['media_type'] is None:
                proposal['media_type'] = 'image'
            if not proposal['subject']:
                proposal['subject'] = '、'.join(item['label'] for item in category_targets)
        if phase == 'terms':
            if action == 'update':
                action = 'revise_terms'
            elif action == 'confirm':
                action = 'confirm_terms'
            elif action == 'replace' and prior['subject'] and proposal['subject']:
                old, new = prior['subject'].strip(), proposal['subject'].strip()
                if old == new or old in new or new in old:
                    action = 'revise_terms'
        if phase == 'request' and action == 'confirm' and count_hint is not None and count_hint != prior['target_count']:
            action = 'update'
            proposal['target_count'] = count_hint
        next_state = dict(prior)
        if action == 'replace':
            next_state.update(subject=None, target_count=None, media_type=None, constraints=[],
                              question=None, stage='request', context_start=len(prior['messages']),
                              raw_request='', effective_request=None, reference_request=None,
                              category_targets=[], current_groups=[], requirement_draft=None)
        if action in ('replace', 'update'):
            for field in ('subject', 'target_count', 'media_type'):
                if proposal[field] is not None:
                    next_state[field] = proposal[field]
            if proposal['constraints'] is not None:
                next_state['constraints'] = [proposal['constraints']] if proposal['constraints'] else []
            if category_targets and count_hint is None:
                next_state['category_targets'] = category_targets
            elif (proposal['target_count'] is not None and proposal['target_count'] != prior['target_count']
                  or proposal['subject'] is not None and proposal['subject'] != prior['subject']):
                next_state['category_targets'] = []
        if action == 'revise_terms':
            for field in ('subject', 'target_count', 'media_type'):
                if proposal[field] is not None:
                    next_state[field] = proposal[field]
            if proposal['constraints'] is not None:
                next_state['constraints'] = [proposal['constraints']] if proposal['constraints'] else []
            if next_state['target_count'] != prior['target_count'] or next_state['subject'] != prior['subject']:
                next_state['category_targets'] = []
        if category_targets and count_hint is None and action == 'update':
            next_state['requirement_draft'] = draft_from_state({
                'category_targets': category_targets})
        semantic_edits = proposal.get('requirement_edits', [])
        if semantic_edits:
            if action not in ('replace', 'update', 'revise_terms'):
                raise ServiceError('MODEL_OUTPUT_INVALID', 'Category edits require a request update', 502)
            try:
                base = {} if action == 'replace' else prior
                draft = apply_edits(base, semantic_edits, message)
                projected = project_draft(next_state, draft, proposal.get('target_count'))
                next_state.update(projected)
            except ValueError as error:
                raise ServiceError('MODEL_OUTPUT_INVALID', str(error), 502) from None
        elif next_state.get('requirement_draft') is not None and action == 'update':
            try:
                next_state.update(project_draft(next_state, next_state['requirement_draft'],
                                                proposal.get('target_count')))
            except ValueError as error:
                raise ServiceError('MODEL_OUTPUT_INVALID', str(error), 502) from None
        try:
            check_clarification(proposal, next_state, message)
        except ValueError as error:
            raise ServiceError('MODEL_OUTPUT_INVALID', str(error), 502) from None
        if next_state.get('requirement_draft') is not None:
            next_state['effective_request'] = render_draft(next_state)
        if action == 'clarify':
            # Rejected edits remain auditable, but cannot become effective via
            # the legacy raw-text fallback. Freeze the previous accepted draft.
            next_state['effective_request'] = self._effective_request(prior)
        if phase == 'request' and (action in ('replace', 'update', 'clarify')) and (model_action != 'confirm' or category_targets):
            source = ((next_state.get('raw_request', '') + '\n') if next_state.get('raw_request') else '') + message
            # The full transcript is stored separately. Keep this bounded source
            # for compatibility; effective_request carries the latest facts.
            if len(source) <= 2000:
                next_state['raw_request'] = source
        if action in ('replace', 'update') or (action == 'revise_terms' and any(
                next_state.get(field) != prior.get(field)
                for field in ('subject', 'target_count', 'constraints'))):
            # The raw transcript remains auditable. Downstream generation uses
            # the latest resolved fields once a turn changes the request.
            if (action in ('update', 'revise_terms') and (prior.get('raw_request') or prior['messages'])) or reference is not None:
                next_state['effective_request'] = self._summary(next_state)
            else:
                next_state['effective_request'] = None
            if next_state.get('requirement_draft') is not None:
                next_state['effective_request'] = render_draft(next_state)
        if action == 'confirm' and (phase != 'request' or prior.get('question') != 'confirm_summary'):
            action = 'clarify'
        if action == 'confirm_terms' and (phase != 'terms' or prior.get('question') not in (None, 'review_terms')):
            action = 'clarify'
        if action == 'revise_terms' and phase != 'terms':
            action = 'clarify'
        if action == 'revise_terms' and next_state['media_type'] != 'image':
            action = 'update'
        direct_terms = (body.get('generate_terms_when_complete', False)
                        and phase == 'request' and action in ('replace', 'update')
                        and proposal.get('question') is None
                        and next_state['media_type'] == 'image'
                        and bool(next_state['subject']) and next_state['target_count'] is not None)
        if action == 'confirm' or direct_terms:
            next_state['stage'] = 'terms'
            next_state['question'] = 'review_terms' if direct_terms else None
        elif action == 'confirm_terms':
            next_state['question'] = None
        elif action == 'revise_terms':
            next_state['question'] = 'review_terms'
        elif action != 'answer':
            next_state['stage'] = 'terms' if phase == 'terms' and action == 'clarify' else 'request'
            if next_state['stage'] == 'terms':
                next_state['question'] = 'review_terms'
            elif next_state['media_type'] is None:
                next_state['question'] = 'media_type'
            elif next_state['media_type'] != 'image':
                next_state['question'] = 'unsupported_media'
            elif not next_state['subject']:
                next_state['question'] = 'subject'
            elif next_state['target_count'] is None:
                next_state['question'] = 'target_count'
            else:
                next_state['question'] = 'confirm_summary'
            if (action == 'clarify' or proposal.get('clarification')) and proposal['question']:
                next_state['question'] = 'custom_clarification'
        if semantic_edits:
            if (next_state['media_type'] == 'image' and next_state['subject']
                    and next_state['target_count'] is not None and proposal.get('question') is None
                    and (phase == 'terms' or body.get('generate_terms_when_complete', False))):
                next_state['stage'], next_state['question'] = 'terms', 'review_terms'
            elif not next_state['subject'] or next_state['target_count'] is None or proposal.get('clarification'):
                next_state['stage'] = 'request'
                next_state['question'] = ('custom_clarification' if proposal.get('clarification') else
                    'subject' if not next_state['subject'] else 'target_count')
        messages = prior['messages'] + [{'role': 'user', 'text': message}]
        dropped = max(0, len(messages) - 29)
        next_state['messages'] = messages[-29:]
        next_state['context_start'] = max(0, next_state.get('context_start', 0) - dropped)
        self.projects.require_access(actor, project_id, 'default', 'create')
        question = next_state['question']
        summary = self._summary(next_state)
        changed_fields = [field for field in ('subject', 'target_count', 'media_type', 'constraints')
                          if next_state.get(field) != prior.get(field)]
        concise_confirmation = None
        if question == 'confirm_summary' and changed_fields == ['target_count']:
            concise_confirmation = f'收到，共 {next_state["target_count"]} 张，对吗？'
        elif question == 'confirm_summary' and changed_fields == ['media_type']:
            concise_confirmation = '已改为图片，对吗？'
        elif question == 'confirm_summary' and changed_fields == ['constraints']:
            concise_confirmation = '已更新要求，对吗？'
        reply = ({'media_type': '你要找的是图片吗？', 'unsupported_media': ('视频可通过“视频草稿”入口保存整段视频需求，检索与交付尚未开放。' if next_state['media_type'] == 'video' else '文字资料检索暂不开放。'),
                  'subject': '想找什么内容或场景的图片？', 'target_count': '需要多少张图片？',
                  'confirm_summary': concise_confirmation or summary + '，对吗？',
                  'review_terms': ('需求信息已齐，正在生成检索词供你确认。' if direct_terms
                                   else '我会按你的反馈重新调整检索词。')}
                 .get(question, '收到，我会继续处理。'))
        if (action == 'clarify' or proposal.get('clarification')) and proposal['question']:
            reply = proposal['question']
        natural = _safe_natural_reply(proposal.get('reply'))
        if proposal.get('clarification'):
            natural = None
        if natural:
            if action == 'answer' and not body.get('generate_terms_when_complete', False):
                reply = ('视频草稿可以保存；内网视频检索、验证和交付链路接通前暂不能执行。'
                         if prior.get('question') == 'unsupported_media'
                         and re.search(r'为什么|为何|支持.*视频|视频.*支持', message)
                         else natural)
            elif question == 'confirm_summary' and not body.get('generate_terms_when_complete', False):
                # The verified summary above is the single confirmation
                # sentence.  Do not concatenate a model paraphrase with it;
                # that repeats the user's request and can still disagree in
                # wording even when the structured fields are correct.
                pass
            else:
                reply = natural
        if question == 'unsupported_media' and action != 'answer':
            reply = ('视频可通过“视频草稿”入口保存整段视频需求，检索与交付尚未开放。'
                     if next_state['media_type'] == 'video' else '文字资料检索暂不开放。')
        next_state['messages'].append({'role': 'assistant', 'text': reply})
        next_state['message_count'] = prior.get('message_count', len(prior['messages'])) + 2
        self.projects.require_access(actor, project_id, 'default', 'create')
        original_session_id = session_id
        if receipt is None:
            session_id, new_version = self.store.save(actor, project_id, session_id, version, next_state,
                                                     new_messages=next_state['messages'][-2:])
        else:
            session_id, new_version = session_id or 'dlg_' + uuid.uuid4().hex, version + 1
        result = {'schema_version': 'dialogue-turn@1', 'session_id': session_id, 'version': new_version,
                'action': action, 'stage': next_state['stage'], 'question': question,
                'count_edits': proposal.get('count_edits', []),
                'requirement_edits': semantic_edits,
                'expansion_instruction': proposal.get('expansion_instruction'),
                'reply': reply, 'reply_source': 'model' if model_interpreted and reply == natural else 'system',
                'summary': summary, 'request': {
                    'raw_request': self._request(next_state),
                    'effective_request': self._effective_request(next_state),
                    'target_count': next_state['target_count'],
                    'media_type': next_state['media_type'], 'subject': next_state['subject'],
                    'requirement_draft': next_state.get('requirement_draft')}}
        if receipt is not None:
            receipt.checkpoint({'kind': 'dialogue', 'project_id': project_id,
                'session_id': original_session_id, 'expected_version': version,
                'state': next_state, 'new_messages': next_state['messages'][-2:], 'result': result})
        return result

    def snapshot(self, actor, project_id, session_id):
        self.projects.require_access(actor, project_id, 'default', 'create')
        state = _recover_explicit_category_count(self.store.read(actor, project_id, session_id, include_transcript=True))
        return {'schema_version': 'dialogue-turn@1', 'session_id': session_id,
                'version': state['version'], 'stage': state['stage'],
                'question': state['question'], 'summary': self._summary(state),
                'request': {'raw_request': self._request(state),
                            'effective_request': self._effective_request(state),
                            'target_count': state['target_count'],
                            'media_type': state['media_type'], 'subject': state['subject'],
                            'requirement_draft': state.get('requirement_draft')},
                'messages': state['_transcript']}

    @staticmethod
    def _summary(state):
        if state.get('requirement_draft') is not None and any(
                c['scene'] != c['label'] or c['conditions']
                for c in state['requirement_draft']['categories']):
            return render_draft(state)
        suffix = '；' + '；'.join(state['constraints']) if state['constraints'] else ''
        targets = state.get('category_targets') or []
        if targets and sum(item['count'] for item in targets) == state['target_count']:
            return '找' + '、'.join(f"{item['count']} 张{item['label']}图片" for item in targets) + f"（共 {state['target_count']} 张）" + suffix
        content = state['subject'] or '图片'
        count = state['target_count']
        noun = '' if content.endswith(('图片', '照片', '图像')) else '图片'
        return f'找 {count} 张{content}{noun}{suffix}' if count else f'找{content}{noun}{suffix}'

    @staticmethod
    def _request(state):
        return state.get('raw_request') or DialogueCoordinator._summary(state)

    @staticmethod
    def _effective_request(state):
        return state.get('effective_request') or DialogueCoordinator._request(state)
