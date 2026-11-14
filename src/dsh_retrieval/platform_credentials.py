"""Encrypted refresh delegation with memory-only PATs and restart recovery.

Exchange is injected from the verified platform protocol, never inferred.
"""
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time

from .errors import ServiceError
from .model_settings import SecretBox
from .models import Actor


class DelegatedCredentials:
    def __init__(self, path, key_path, *, identity, exchange, clock=time.time):
        path = Path(path)
        if path.exists() and path.stat().st_size and not Path(key_path).exists():
            raise ValueError('existing delegation vault requires original key')
        self.box = SecretBox(key_path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute('CREATE TABLE IF NOT EXISTS delegations (owner TEXT PRIMARY KEY, secret BLOB NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS sessions (digest TEXT PRIMARY KEY, owner TEXT NOT NULL, csrf TEXT NOT NULL, expires REAL NOT NULL)')
        self.db.commit()
        self.identity, self.exchange, self.clock = identity, exchange, clock
        self.lock, self.pats = threading.RLock(), {}
        self.retry_after = {}

    @staticmethod
    def owner(actor):
        return json.dumps([actor.tenant_id, actor.user_id], separators=(',', ':'))

    def register(self, token, refresh_key=None, *, ttl=3600):
        actor = self.identity.authenticate({'Authorization': 'Bearer ' + token})
        if type(ttl) is not int or not 0 < ttl <= 3600:
            raise ServiceError('INVALID_CREDENTIAL', 'invalid credential lifetime', 422)
        owner = self.owner(actor)
        with self.lock:
            if refresh_key is not None:
                if not isinstance(refresh_key, str) or not refresh_key or len(refresh_key) > 8192:
                    raise ServiceError('INVALID_CREDENTIAL', 'invalid refresh delegation', 422)
                # Verify refresh belongs to the same user before persisting it.
                refreshed, expires = self._exchange(refresh_key)
                if self.identity.authenticate({'Authorization': 'Bearer ' + refreshed}) != actor:
                    raise ServiceError('UNAUTHENTICATED', 'refresh owner mismatch', 401)
                self.db.execute('INSERT OR REPLACE INTO delegations VALUES (?,?)',
                                (owner, self.box.seal(refresh_key, 'platform-delegation:' + owner)))
                token, ttl = refreshed, expires
                self.db.commit()
            else:
                self.db.execute('DELETE FROM delegations WHERE owner=?', (owner,))
                self.db.commit()
            self.retry_after.pop(owner, None)
            self.pats[owner] = (token, self.clock() + ttl, self.clock() + ttl * .9)
        return actor

    def _exchange(self, refresh_key):
        try:
            token, ttl = self.exchange(refresh_key)
            if not isinstance(token, str) or not token or any(c.isspace() for c in token):
                raise ValueError()
            if type(ttl) is not int or not 0 < ttl <= 3600:
                raise ValueError()
            return token, ttl
        except ServiceError:
            raise
        except Exception:
            raise ServiceError('DELEGATION_UNAVAILABLE', 'personal delegation cannot be refreshed', 503) from None

    def credential_for_actor(self, actor):
        owner = self.owner(actor)
        with self.lock:
            cached = self.pats.get(owner)
            if cached and self.clock() < cached[2]:
                return cached[0]
            row = self.db.execute('SELECT secret FROM delegations WHERE owner=?', (owner,)).fetchone()
            if row is None:
                if cached and self.clock() < cached[1]:
                    return cached[0]
                raise ServiceError('UNAUTHENTICATED', 'personal reauthentication required', 401)
            if self.clock() < self.retry_after.get(owner, 0):
                raise ServiceError('DELEGATION_UNAVAILABLE', 'personal delegation retry deferred', 503)
            try:
                refresh = self.box.open(row[0], 'platform-delegation:' + owner)
                token, ttl = self._exchange(refresh)
                if self.identity.authenticate({'Authorization': 'Bearer ' + token}) != actor:
                    raise ServiceError('UNAUTHENTICATED', 'delegated identity mismatch', 401)
            except ServiceError as error:
                self.pats.pop(owner, None)
                self.retry_after[owner] = self.clock() + 300
                if error.status in (401, 403):
                    self._revoke(owner)
                    raise
                raise ServiceError('DELEGATION_UNAVAILABLE', 'personal delegation unavailable; retry deferred', 503) from None
            self.retry_after.pop(owner, None)
            self.pats[owner] = (token, self.clock() + ttl, self.clock() + ttl * .9)
            return token

    def _revoke(self, owner):
        self.pats.pop(owner, None)
        self.db.execute('DELETE FROM delegations WHERE owner=?', (owner,))
        self.db.execute('DELETE FROM sessions WHERE owner=?', (owner,))
        self.db.commit()

    def revoke(self, actor):
        with self.lock:
            self._revoke(self.owner(actor))

    def create_session(self, actor, *, ttl=28800):
        cookie, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.lock:
            self.db.execute('DELETE FROM sessions WHERE expires<=?', (self.clock(),))
            self.db.execute('INSERT INTO sessions VALUES (?,?,?,?)',
                (hashlib.sha256(cookie.encode()).hexdigest(), self.owner(actor), csrf, self.clock() + ttl))
            self.db.commit()
        return cookie, csrf

    def session(self, cookie, *, csrf=None, mutation=False):
        with self.lock:
            row = self.db.execute('SELECT owner,csrf,expires FROM sessions WHERE digest=?',
                                 (hashlib.sha256(cookie.encode()).hexdigest(),)).fetchone()
        if not row or row[2] <= self.clock():
            raise ServiceError('UNAUTHENTICATED', 'session expired', 401)
        if mutation and (not isinstance(csrf, str) or not secrets.compare_digest(csrf, row[1])):
            raise ServiceError('CSRF_DENIED', 'session request verification failed', 403)
        actor = Actor(*json.loads(row[0]))
        token = self.credential_for_actor(actor)
        try:
            verified = self.identity.authenticate({'Authorization': 'Bearer ' + token})
            if verified != actor:
                raise ServiceError('UNAUTHENTICATED', 'session identity mismatch', 401)
        except ServiceError as error:
            if error.status in (401, 403):
                self.revoke(actor)
            raise
        return actor, token, row[1]

    def logout(self, cookie):
        with self.lock:
            self.db.execute('DELETE FROM sessions WHERE digest=?', (hashlib.sha256(cookie.encode()).hexdigest(),))
            self.db.commit()

    def close(self):
        with self.lock:
            self.pats.clear()
            self.db.close()
