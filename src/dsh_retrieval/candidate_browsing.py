"""Durable append-only read projection; never itself grants media access."""
from contextlib import closing
import base64
import hashlib
import hmac
import json
import sqlite3
import time

from .errors import ServiceError


def create_projection(db):
    db.execute('''CREATE TABLE IF NOT EXISTS candidate_browse_hits (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL,
        entry TEXT NOT NULL, plan TEXT NOT NULL, branch TEXT NOT NULL,
        payload TEXT NOT NULL, candidate TEXT, UNIQUE(source,entry))''')
    db.execute('CREATE INDEX IF NOT EXISTS browse_source ON candidate_browse_hits(source,seq)')
    db.execute('CREATE INDEX IF NOT EXISTS browse_candidate ON candidate_browse_hits(source,candidate)')


def append_hit(db, source, entry, plan, branch, payload):
    db.execute('INSERT OR IGNORE INTO candidate_browse_hits(source,entry,plan,branch,payload) VALUES(?,?,?,?,?)',
               (source, entry, plan, branch, payload))


class CandidateBrowser:
    def __init__(self, path, secret):
        self.path, self.secret = path, secret

    def token(self, value):
        value = {**value, 'expires': int(time.time()) + 86400}
        raw = json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
        signature = hmac.new(self.secret, raw, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(signature + raw).decode()

    def decode(self, token):
        try:
            if not isinstance(token, str) or len(token) > 8192:
                raise ValueError()
            data = base64.b64decode(token, altchars=b'-_', validate=True)
            signature, raw = data[:32], data[32:]
            if not hmac.compare_digest(signature, hmac.new(self.secret, raw, hashlib.sha256).digest()):
                raise ValueError()
            value = json.loads(raw)
            if not isinstance(value, dict) or value.get('expires', 0) < time.time():
                raise ServiceError('CURSOR_EXPIRED', 'Candidate cursor expired; reload', 409)
            return value
        except (ValueError, TypeError, UnicodeError):
            raise ServiceError('INVALID_CURSOR', 'Invalid candidate cursor; reload', 422) from None

    def page(self, actor, task_id, source, *, limit=60, cursor=None, query='', branches=None, changes=False):
        if type(limit) is not int or not 1 <= limit <= 200 or not isinstance(query, str) or len(query) > 1000:
            raise ServiceError('INVALID_RESULT_VIEW', 'Candidate page limit must be 1..200', 422)
        binding = [actor.tenant_id, actor.user_id, task_id, source, query]
        saved = self.decode(cursor) if cursor else None
        if saved and (saved.get('binding') != binding or saved.get('kind') != 'page'):
            raise ServiceError('CANDIDATE_VERSION_CHANGED', 'Candidate scope or filter changed; reload', 409)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('BEGIN')
            total, latest = db.execute('SELECT COUNT(*),COALESCE(MAX(seq),0) FROM candidate_browse_hits WHERE source=?', (source,)).fetchone()
            after = saved['after'] if saved else 0
            upper = latest if not saved or (changes and saved['tail']) else saved['upper']
            where, args = 'source=? AND seq>? AND seq<=?', [source, after, upper]
            if branches is not None:
                where += ' AND branch IN (' + ','.join('?' for _ in branches) + ')'
                args.extend(branches)
            rows = db.execute('SELECT seq,plan,branch,payload FROM candidate_browse_hits WHERE '+where+' ORDER BY seq LIMIT ?', (*args, limit+1)).fetchall()
        more = len(rows) > limit
        rows = rows[:limit]
        position = rows[-1][0] if more else upper
        continuation = self.token(dict(kind='page', binding=binding, after=position, upper=upper, tail=not more))
        return rows, dict(raw_hit_count=total, as_of_sequence=upper, search_version=source,
                          next_cursor=continuation if more else None, changes_cursor=continuation,
                          truncated=more)

    def remember(self, source, seq, candidate_id):
        if candidate_id:
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute('UPDATE candidate_browse_hits SET candidate=? WHERE source=? AND seq=?', (candidate_id, source, seq))

    def remember_many(self, source, pairs):
        pairs = [(candidate_id, source, seq) for seq, candidate_id in pairs if candidate_id]
        if pairs:
            with closing(sqlite3.connect(self.path)) as db, db:
                db.executemany('UPDATE candidate_browse_hits SET candidate=? WHERE source=? AND seq=?', pairs)

    def preview_ref(self, actor, task_id, source, seq, candidate_id):
        return self.token(dict(kind='preview', binding=[actor.tenant_id, actor.user_id, task_id, source, candidate_id], seq=seq))

    def locate(self, actor, task_id, source, candidate_id, ref=None):
        saved = self.decode(ref) if ref else None
        if saved and (saved.get('kind') != 'preview' or saved.get('binding') != [actor.tenant_id, actor.user_id, task_id, source, candidate_id]):
            raise ServiceError('CANDIDATE_VERSION_CHANGED', 'Preview scope changed; reload', 409)
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute('SELECT payload FROM candidate_browse_hits WHERE source=? AND '+
                ('seq=?' if saved else 'candidate=?')+' ORDER BY seq LIMIT 1',
                (source, saved['seq'] if saved else candidate_id)).fetchone()
        if not row:
            raise ServiceError('PREVIEW_UNAVAILABLE', 'Candidate is not in this search', 404)
        return row[0]
