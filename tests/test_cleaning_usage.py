from dataclasses import replace
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from cleaning.test_generic_runner import make_job
import test_model_runtime as runtime_fixture
from dsh_retrieval.cleaning_usage import CleaningUsageRecorder, read_usage
from dsh_retrieval.errors import ServiceError


class CleaningUsageTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'usage.sqlite3'
        self.job = make_job(contents=(b'first',))
        self.recorder = CleaningUsageRecorder(self.path, self.job.binding, lambda: None)

    def records(self, job=None):
        with sqlite3.connect(self.path) as db:
            return read_usage(db, {self.job.binding.operation_id: SimpleNamespace(job=job or self.job)})

    def test_actual_http_retry_is_counted_and_payload_is_not_persisted(self):
        f = runtime_fixture.ModelRuntimeTest()
        runtime = f.runtime([HTTPError('https://fixture.invalid', 503, 'retry', {}, None),
                             {'verdict': 'pass', 'reason': 'visible'}])
        with patch('dsh_retrieval.model_runtime.time.sleep'), self.recorder.step(
                self.job.candidates[0], self.job.recipe.steps[0], supported=True):
            runtime.assess('private requirement', b'\xff\xd8\xfffixture', 'image/jpeg',
                           runtime.binding('text', 'vision'))
        rows = self.records()
        self.assertEqual((rows[0]['http_attempts'], rows[0]['http_errors'], rows[0]['status']),
                         (2, 1, 'completed'))
        self.assertGreaterEqual(rows[0]['http_elapsed_ms'], 0)
        with sqlite3.connect(self.path) as db:
            saved = '\n'.join(db.iterdump())
        for secret in ('test-secret', 'private requirement', 'fixture.invalid', 'image/jpeg'):
            self.assertNotIn(secret, saved)

    def test_pending_completion_is_not_reported_as_known_call(self):
        f = runtime_fixture.ModelRuntimeTest()
        runtime = f.runtime([{'verdict': 'unknown', 'reason': 'unclear'}])
        with patch.object(self.recorder, '_finish'), self.recorder.step(
                self.job.candidates[0], self.job.recipe.steps[0], supported=True):
            runtime.assess('image', b'\xff\xd8\xfffixture', 'image/jpeg', runtime.binding('text', 'vision'))
        row = self.records()[0]
        self.assertEqual((row['status'], row['http_attempts'], row['pending_http_attempts']),
                         ('started', 0, 1))

    def test_generation_mismatch_is_rejected_on_read(self):
        with self.recorder.step(self.job.candidates[0], self.job.recipe.steps[0], supported=True):
            pass
        wrong = replace(self.job, binding=replace(self.job.binding, generation=99))
        with self.assertRaises(ValueError): self.records(wrong)

    def test_revocation_prevents_network_attempt(self):
        f = runtime_fixture.ModelRuntimeTest(); runtime = f.runtime([])
        calls = []
        def guard():
            calls.append(1)
            if len(calls) > 1: raise ServiceError('ACCESS_DENIED', 'revoked', 403)
        self.recorder.require_current = guard
        with self.assertRaises(ServiceError), self.recorder.step(
                self.job.candidates[0], self.job.recipe.steps[0], supported=True):
            runtime.assess('image', b'\xff\xd8\xfffixture', 'image/jpeg', runtime.binding('text', 'vision'))
        self.assertEqual(f.transport.calls, [])
        self.assertEqual(self.records()[0]['http_attempts'], 0)
