from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from dsh_retrieval.adapters.projects import LocalProjectAccess
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.intent_draft import IntentDraftStore
from dsh_retrieval.models import Actor
from dsh_retrieval.query_spec import digest


def proposal(raw, *, value=False):
    return {"schema_version": "intent-interpretation@1", "raw_digest": digest({"raw_request": raw}),
            "media_type": "image", "items": [{"item_id": "r1", "start": 0, "end": len(raw),
                "quote": raw, "kind": "constraint", "hardness": "hard", "predicate_ref": "text.present@1",
                "operator": "eq", "value": value, "question": None}]}


class IntentDraftTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "drafts.db"
        self.actor = Actor("tenant", "owner")
        self.access = LocalProjectAccess({("tenant", "owner", "project", "dimension")})
        self.store = IntentDraftStore(self.path, self.access)
        self.raw = "不要文字"

    def create(self, key="create", raw=None, interpretation=None):
        raw = self.raw if raw is None else raw
        interpretation = proposal(raw) if interpretation is None else interpretation
        return self.store.create(self.actor, "project", "dimension", key, raw, interpretation)

    def assert_code(self, code, action):
        with self.assertRaises(ServiceError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)

    def test_create_revise_and_restart_preserve_original_and_version(self):
        created = self.create()
        self.assertFalse(created["creates_task"])
        self.assertEqual(created["draft_version"], 1)
        self.assertEqual(self.path.stat().st_mode & 0o077, 0)
        with closing(sqlite3.connect(self.path)) as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(tables, {"intent_drafts", "intent_draft_ops"})
        raw = "不要文字，客厅"
        revised = self.store.revise(self.actor, created["draft_id"], "revise", 1, raw, proposal(raw))
        self.assertEqual(revised["draft_version"], 2)
        reopened = IntentDraftStore(self.path, self.access).get(self.actor, created["draft_id"])
        self.assertEqual(reopened["review"]["raw_request"], raw)
        self.assertEqual(reopened["draft_version"], 2)
        self.assertTrue(reopened["review"]["has_blockers"])

    def test_create_and_revise_replay_or_conflict(self):
        created = self.create()
        self.assertTrue(self.create()["replayed"])
        self.assert_code("IDEMPOTENCY_CONFLICT", lambda: self.create(raw="需要文字"))
        key = created["draft_id"]
        self.store.revise(self.actor, key, "revise", 1, self.raw, proposal(self.raw))
        replay = self.store.revise(self.actor, key, "revise", 1, self.raw, proposal(self.raw))
        self.assertEqual(replay["draft_version"], 2)
        self.assertTrue(replay["replayed"])
        self.assert_code("IDEMPOTENCY_CONFLICT", lambda: self.store.revise(
            self.actor, key, "revise", 1, self.raw, proposal(self.raw, value=True)))

    def test_stale_version_and_invalid_interpretation_leave_draft_unchanged(self):
        key = self.create()["draft_id"]
        self.assert_code("DRAFT_VERSION_CONFLICT", lambda: self.store.revise(
            self.actor, key, "other", 2, self.raw, proposal(self.raw)))
        wrong = proposal(self.raw)
        wrong["raw_digest"] = "forged"
        self.assert_code("INVALID_SPEC", lambda: self.store.revise(self.actor, key, "bad", 1, self.raw, wrong))
        self.assertEqual(self.store.get(self.actor, key)["draft_version"], 1)

    def test_owner_and_project_access_rechecked(self):
        key = self.create()["draft_id"]
        self.assert_code("NOT_FOUND", lambda: self.store.get(Actor("tenant", "other"), key))
        self.access.grants.clear()
        self.assert_code("PROJECT_ACCESS_DENIED", lambda: self.store.get(self.actor, key))
        self.assert_code("PROJECT_ACCESS_DENIED", lambda: self.store.revise(
            self.actor, key, "revise", 1, self.raw, proposal(self.raw)))

    def test_unsafe_database_permissions_rejected(self):
        self.path.chmod(0o644)
        with self.assertRaises(ValueError):
            IntentDraftStore(self.path, self.access)


if __name__ == "__main__":
    unittest.main()
