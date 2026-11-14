from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from dsh_retrieval.errors import ServiceError
from dsh_retrieval.models import Actor
from dsh_retrieval.platform_credentials import DelegatedCredentials


class Identity:
    def authenticate(self, headers):
        token = headers.get('Authorization', '')
        if token == 'Bearer token-a':
            return Actor('platform', 'a')
        if token == 'Bearer token-b':
            return Actor('platform', 'b')
        raise ServiceError('UNAUTHENTICATED', 'invalid token', 401)


class CredentialsTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'vault.sqlite'
        self.key = Path(self.temp.name) / 'key'
        self.now = 1000
        self.calls = []
        self.identity = Identity()
        self.exchange_token = 'token-a'
        self.failure = None
        self.vault = self.open()
        self.addCleanup(lambda: self.vault.close())
        self.actor = Actor('platform', 'a')

    def exchange(self, refresh):
        self.calls.append(refresh)
        if self.failure:
            raise self.failure
        return self.exchange_token, 100

    def open(self):
        return DelegatedCredentials(self.path, self.key, identity=self.identity,
                                    exchange=self.exchange, clock=lambda: self.now)

    def test_restart_refresh_and_no_plaintext_secret(self):
        self.vault.register('token-a', 'private-refresh')
        self.assertNotIn(b'private-refresh', self.path.read_bytes())
        self.assertNotIn(b'token-a', self.path.read_bytes())
        self.vault.close()
        self.vault = self.open()
        self.assertEqual(self.vault.credential_for_actor(self.actor), 'token-a')
        self.assertEqual(len(self.calls), 2)

    def test_refresh_owner_mismatch_never_persisted(self):
        self.exchange_token = 'token-b'
        with self.assertRaises(ServiceError):
            self.vault.register('token-a', 'wrong-owner')
        self.assertEqual(self.vault.db.execute('SELECT count(*) FROM delegations').fetchone()[0], 0)

    def test_expiry_and_concurrent_refresh_only_once(self):
        self.vault.register('token-a', 'refresh')
        self.now += 91
        with ThreadPoolExecutor(max_workers=5) as pool:
            values = list(pool.map(lambda _: self.vault.credential_for_actor(self.actor), range(5)))
        self.assertEqual(values, ['token-a'] * 5)
        self.assertEqual(len(self.calls), 2)

    def test_no_refresh_after_restart_requires_authentication(self):
        self.vault.register('token-a')
        self.vault.close()
        self.vault = self.open()
        with self.assertRaises(ServiceError):
            self.vault.credential_for_actor(self.actor)

    def test_csrf_logout_and_restart_session(self):
        self.vault.register('token-a', 'refresh')
        cookie, csrf = self.vault.create_session(self.actor)
        self.assertNotIn(cookie.encode(), self.path.read_bytes())
        with self.assertRaises(ServiceError):
            self.vault.session(cookie, mutation=True, csrf='wrong')
        self.vault.close()
        self.vault = self.open()
        self.assertEqual(self.vault.session(cookie, mutation=True, csrf=csrf)[0], self.actor)
        self.vault.logout(cookie)
        with self.assertRaises(ServiceError):
            self.vault.session(cookie)
        # Ordinary logout does not revoke already approved background delegation.
        self.assertEqual(self.vault.credential_for_actor(self.actor), 'token-a')

    def test_revocation_removes_session_and_refresh(self):
        self.vault.register('token-a', 'refresh')
        cookie, _ = self.vault.create_session(self.actor)
        self.now += 101
        self.failure = ServiceError('UNAUTHENTICATED', 'revoked', 401)
        with self.assertRaises(ServiceError):
            self.vault.credential_for_actor(self.actor)
        self.assertEqual(self.vault.db.execute('SELECT count(*) FROM sessions').fetchone()[0], 0)
        self.assertEqual(self.vault.db.execute('SELECT count(*) FROM delegations').fetchone()[0], 0)

    def test_exchange_failure_backoff_and_no_stale_token(self):
        self.vault.register('token-a', 'refresh')
        self.now += 91
        self.failure = RuntimeError('secret endpoint detail')
        for _ in range(3):
            with self.assertRaises(ServiceError) as caught:
                self.vault.credential_for_actor(self.actor)
            self.assertEqual(caught.exception.status, 503)
            self.assertNotIn('secret endpoint', str(caught.exception))
        self.assertEqual(len(self.calls), 2)
        self.failure = None
        self.now += 301
        self.assertEqual(self.vault.credential_for_actor(self.actor), 'token-a')

    def test_encrypted_delegation_tampering_fails(self):
        self.vault.register('token-a', 'refresh')
        self.vault.pats.clear()
        self.vault.db.execute('UPDATE delegations SET secret=?', (b'broken',))
        self.vault.db.commit()
        with self.assertRaises(ServiceError):
            self.vault.credential_for_actor(self.actor)

    def test_missing_original_key_does_not_replace_it(self):
        self.vault.register('token-a', 'refresh')
        self.key.unlink()
        with self.assertRaises(ValueError):
            self.open()
        self.assertFalse(self.key.exists())
