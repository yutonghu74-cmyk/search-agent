from contextlib import closing
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from dsh_retrieval.candidate_browsing import CandidateBrowser, create_projection, append_hit
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.models import Actor


class CandidateBrowsingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = self.temp.name + '/browse.sqlite'
        with closing(sqlite3.connect(self.path)) as db, db:
            create_projection(db)
        self.browser = CandidateBrowser(self.path, b'x' * 32)
        self.actor = Actor('tenant', 'user')

    def append(self, number, branch='dog'):
        with closing(sqlite3.connect(self.path)) as db, db:
            append_hit(db, 'version', str(number), 'plan', branch, json.dumps({'number': number}))

    def page(self, **kwargs):
        return self.browser.page(self.actor, 'task', 'version', **kwargs)

    def test_snapshot_pages_and_incremental_append_no_loss_or_duplicate(self):
        for i in range(205):
            self.append(i)
        rows, first = self.page(limit=60)
        self.append(205)
        sequences = [r[0] for r in rows]
        cursor = first['next_cursor']
        while cursor:
            page, meta = self.page(limit=60, cursor=cursor)
            sequences.extend(r[0] for r in page)
            cursor = meta['next_cursor']
        self.assertEqual(len(sequences), 205)
        self.assertEqual(len(set(sequences)), 205)
        self.assertEqual(meta['as_of_sequence'], first['as_of_sequence'])
        updates, current = self.page(cursor=meta['changes_cursor'], changes=True)
        self.assertEqual([json.loads(r[3])['number'] for r in updates], [205])
        self.assertEqual(self.page(cursor=current['changes_cursor'], changes=True)[0], [])
        self.assertEqual(self.page(cursor=meta['changes_cursor'], changes=True)[0], updates)

    def test_filter_scope_tamper_expiration_and_direct_lookup(self):
        self.append(1)
        self.append(2, 'cat')
        rows, meta = self.page(query='dogs', branches=['dog'])
        self.assertEqual(len(rows), 1)
        with self.assertRaises(ServiceError):
            self.page(cursor=meta['changes_cursor'], query='cats')
        with self.assertRaises(ServiceError):
            self.browser.page(Actor('tenant', 'other'), 'task', 'version', cursor=meta['changes_cursor'], query='dogs')
        with self.assertRaises(ServiceError):
            self.browser.page(self.actor, 'task', 'new-version', cursor=meta['changes_cursor'], query='dogs')
        with self.assertRaises(ServiceError):
            self.page(cursor='tampered')
        with patch('dsh_retrieval.candidate_browsing.time.time', return_value=10**12):
            with self.assertRaises(ServiceError) as error:
                self.page(cursor=meta['changes_cursor'], query='dogs')
            self.assertEqual(error.exception.code, 'CURSOR_EXPIRED')
        ref = self.browser.preview_ref(self.actor, 'task', 'version', rows[0][0], 'candidate')
        self.assertEqual(json.loads(self.browser.locate(self.actor, 'task', 'version', 'candidate', ref)), {'number': 1})
        with self.assertRaises(ServiceError):
            self.browser.locate(self.actor, 'other-task', 'version', 'candidate', ref)

    def test_new_hit_for_same_candidate_is_an_incremental_event_and_restart_stable(self):
        self.append(1)
        rows, meta = self.page()
        self.browser.remember('version', rows[0][0], 'candidate')
        self.append(2, 'cat')
        self.browser = CandidateBrowser(self.path, b'x' * 32)
        updates, _ = self.page(cursor=meta['changes_cursor'], changes=True)
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0][2], 'cat')
        self.assertEqual(json.loads(self.browser.locate(self.actor, 'task', 'version', 'candidate')), {'number': 1})

    def test_invalid_limit_and_unknown_filter(self):
        for limit in [0, 201, True, '60']:
            with self.assertRaises(ServiceError):
                self.page(limit=limit)
        self.append(1)
        self.assertEqual(self.page(query='unknown', branches=[])[0], [])
