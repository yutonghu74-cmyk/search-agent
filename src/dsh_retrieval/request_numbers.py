"""Human-readable confirmed request numbers; never an authorization credential."""
import re
import secrets
from datetime import datetime
from zoneinfo import ZoneInfo

from .errors import ServiceError

TIME_ZONE = 'Asia/Shanghai'
ALPHABET = '23456789ABCDEFGHJKLMNPQRSTUVWXYZ'
PATTERN = r'REQ-[0-9]{8}-[0-9]{6}-[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{6}'


def normalize_number(value):
    value = value.strip().upper() if isinstance(value, str) else ''
    if not re.fullmatch(PATTERN, value):
        raise ServiceError('INVALID_SPEC', '请输入完整需求编号，例如 REQ-20261127-143025-A7K9Q2', 422)
    try:
        datetime.strptime(value[4:19], '%Y%m%d-%H%M%S')
    except ValueError:
        raise ServiceError('INVALID_SPEC', '需求编号的日期或时间不正确', 422) from None
    return value


def issue_number(created_at, used):
    if created_at.tzinfo is None:
        raise ValueError('request number requires an aware creation time')
    prefix = 'REQ-' + created_at.astimezone(ZoneInfo(TIME_ZONE)).strftime('%Y%m%d-%H%M%S') + '-'
    for _ in range(16):
        number = prefix + ''.join(secrets.choice(ALPHABET) for _ in range(6))
        if number not in used:
            return number
    raise ServiceError('REQUEST_NUMBER_UNAVAILABLE', '需求编号暂时无法分配，请用原请求重试', 503, True)


def ensure_table(db):
    db.execute('''CREATE TABLE IF NOT EXISTS task_request_numbers (
        request_number TEXT PRIMARY KEY, task_id TEXT NOT NULL UNIQUE,
        tenant_id TEXT NOT NULL, owner_user_id TEXT NOT NULL, project_id TEXT NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS task_request_numbers_owner '
               'ON task_request_numbers(tenant_id,owner_user_id,project_id,request_number)')


def sync_numbers(db, memory):
    # The Task field is authoritative. The SQL map enforces uniqueness and gives
    # exact, owner/project-scoped lookup without scanning the history pages.
    existing = {row[1]: tuple(row) for row in db.execute(
        'SELECT request_number,task_id,tenant_id,owner_user_id,project_id FROM task_request_numbers')}
    for task_id in existing:
        if task_id not in memory._tasks:
            db.execute('DELETE FROM task_request_numbers WHERE task_id=?', (task_id,))
    for task in memory._tasks.values():
        if task.request_number is None:
            if task.task_id in existing:
                raise ServiceError('STORAGE_CORRUPT', 'Confirmed request number cannot be removed', 500)
            continue
        number = normalize_number(task.request_number)
        value = (number, task.task_id, task.tenant_id, task.owner_user_id, task.project_id)
        old = existing.get(task.task_id)
        if old is not None and tuple(old) != value:
            raise ServiceError('STORAGE_CORRUPT', 'Confirmed request number is immutable', 500)
        if old is None:
            db.execute('INSERT INTO task_request_numbers VALUES (?,?,?,?,?)', value)
