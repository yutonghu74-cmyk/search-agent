"""Scoped, keyset-paged history summaries; messages stay in their own ledgers."""
import base64
import json
from contextlib import closing
from .errors import ServiceError
from .models import Actor


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS chat_read_summaries (
        chat_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
        project_id TEXT NOT NULL, dimension_id TEXT NOT NULL, created_at TEXT NOT NULL,
        summary_json TEXT NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS chat_history_scope ON chat_read_summaries '
               '(tenant_id,user_id,project_id,created_at DESC,chat_id DESC)')
    db.execute('CREATE TABLE IF NOT EXISTS chat_read_meta (id INTEGER PRIMARY KEY, ready INTEGER NOT NULL)')
    db.execute('INSERT OR IGNORE INTO chat_read_meta VALUES (1,0)')


def sync(db, memory, *, task_ids=None):
    rows = []
    targets = None if task_ids is None else frozenset(task_ids)
    conversations = {task.conversation_id for task in memory._tasks.values()
                     if targets is not None and task.task_id in targets and task.conversation_id}
    tasks = {task.conversation_id: task for task in memory._tasks.values() if task.conversation_id}
    for cid, messages in memory._conversation_messages.items():
        if targets is not None and cid not in conversations:
            continue
        if not messages:
            continue
        first = min(messages.values(), key=lambda item: (item.created_at, item.message_id))
        actor = Actor(first.tenant_id, first.owner_user_id)
        snap = memory.get_conversation(actor, cid)
        draft = memory.get_draft(actor, first.draft_id)
        task = tasks.get(cid)
        item = {**snap.to_summary(), 'id': cid, 'kind': 'conversation',
                'title': task.raw_request if task else first.text[:80],
                'task': task.to_summary() if task else None}
        rows.append((cid, first.tenant_id, first.owner_user_id, first.project_id,
                     draft.dimension_id, item['created_at'], json.dumps(item, ensure_ascii=False)))
    for task in memory._tasks.values():
        if task.conversation_id or targets is not None and task.task_id not in targets:
            continue
        item = {'id': task.task_id, 'kind': 'task', 'title': task.raw_request,
                'created_at': task.created_at.isoformat(), 'task': task.to_summary()}
        rows.append((task.task_id, task.tenant_id, task.owner_user_id, task.project_id,
                     task.dimension_id, item['created_at'], json.dumps(item, ensure_ascii=False)))
    if targets is None:
        retained = {row[0] for row in rows}
        existing = {row[0] for row in db.execute('SELECT chat_id FROM chat_read_summaries')}
        db.executemany('DELETE FROM chat_read_summaries WHERE chat_id=?', ((key,) for key in existing - retained))
    else:
        retained = {row[0] for row in rows}
        db.executemany('DELETE FROM chat_read_summaries WHERE chat_id=?',
                       ((key,) for key in (targets | conversations) - retained))
    db.executemany('''INSERT INTO chat_read_summaries VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(chat_id) DO UPDATE SET tenant_id=excluded.tenant_id,user_id=excluded.user_id,
        project_id=excluded.project_id,dimension_id=excluded.dimension_id,
        created_at=excluded.created_at,summary_json=excluded.summary_json
        WHERE chat_read_summaries.summary_json != excluded.summary_json
           OR chat_read_summaries.tenant_id != excluded.tenant_id
           OR chat_read_summaries.user_id != excluded.user_id
           OR chat_read_summaries.project_id != excluded.project_id
           OR chat_read_summaries.dimension_id != excluded.dimension_id
           OR chat_read_summaries.created_at != excluded.created_at''', rows)
    db.execute('UPDATE chat_read_meta SET ready=1 WHERE id=1 AND ready!=1')


def read_rows(repository, actor, project, projects, after, limit):
    with closing(repository._connect()) as db:
        db.execute('BEGIN')
        if (not db.execute('SELECT ready FROM chat_read_meta WHERE id=1').fetchone()[0]
                or db.execute('SELECT dirty FROM task_read_summaries_meta WHERE id=1').fetchone()[0]):
            raise ServiceError('READ_PROJECTION_REQUIRED', 'Chat history must be migrated', 503)
        result = []
        position = after
        while len(result) < limit:
            where = 'tenant_id=? AND user_id=? AND project_id=?'
            args = [actor.tenant_id, actor.user_id, project]
            if position:
                where += ' AND (created_at,chat_id)<(?,?)'
                args.extend(position)
            rows = db.execute('SELECT created_at,chat_id,dimension_id,summary_json FROM '
                'chat_read_summaries WHERE ' + where + ' ORDER BY created_at DESC,chat_id DESC LIMIT ?',
                (*args, limit)).fetchall()
            if not rows:
                break
            for created, cid, dimension, payload in rows:
                position = (created, cid)
                try:
                    projects.require_access(actor, project, dimension, 'read')
                except ServiceError as error:
                    if error.code == 'PROJECT_ACCESS_DENIED':
                        continue
                    raise
                result.append(json.loads(payload))
                if len(result) == limit:
                    break
            if len(rows) < limit:
                break
        return result


def page(service, dialogue, actor, project, *, limit=20, cursor=None):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ServiceError('INVALID_SPEC', 'history limit must be 1..100', 422)
    scope = [actor.tenant_id, actor.user_id, project]
    after = None
    if cursor is not None:
        try:
            if not isinstance(cursor, str) or len(cursor) > 4096:
                raise ValueError
            value = json.loads(base64.urlsafe_b64decode(cursor + '=' * (-len(cursor) % 4)))
            if set(value) != {'scope', 'after'} or value['scope'] != scope:
                raise ValueError
            after = value['after']
            if not isinstance(after, list) or len(after) != 2 or any(not isinstance(v, str) or not v for v in after):
                raise ValueError
        except (ValueError, TypeError, KeyError):
            raise ServiceError('INVALID_CURSOR', 'Invalid history cursor', 422) from None
    repository = service.repository
    if not hasattr(repository, '_connect'):
        raise ServiceError('READ_PROJECTION_REQUIRED', 'Persistent history required', 503)
    items = read_rows(repository, actor, project, service.projects, after, limit + 1)
    if dialogue is not None:
        try:
            service.projects.require_access(actor, project, 'default', 'read')
        except ServiceError as error:
            if error.code != 'PROJECT_ACCESS_DENIED':
                raise
        else:
            items += dialogue.store.history_page(actor, project, after=after, limit=limit+1)
    items.sort(key=lambda item: (item['created_at'], item['id']), reverse=True)
    more = len(items) > limit
    items = items[:limit]
    next_cursor = None
    if more:
        next_cursor = base64.urlsafe_b64encode(json.dumps({'scope': scope,
            'after': [items[-1]['created_at'], items[-1]['id']]}).encode()).decode().rstrip('=')
    return {'items': items, 'next_cursor': next_cursor}
