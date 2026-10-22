from contextlib import closing
from datetime import UTC, datetime
import json
import sqlite3
import tempfile
import unittest

from dsh_retrieval.adapters.memory import InMemoryTaskRepository
from dsh_retrieval.adapters.task_storage.sqlite import SQLiteTaskRepository
from dsh_retrieval.conversation_models import ConversationMessage
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.models import Actor
from dsh_retrieval.proposals import Draft
from draft_helpers import BODY, confirmation, make_service


class ConversationStorageTest(unittest.TestCase):
    def setUp(self):
        self.repo = InMemoryTaskRepository()
        self.service = make_service(self.repo)
        self.actor = Actor("tenant-a", "user-a")

    @staticmethod
    def feedback_body(task):
        return {"text": "不要包含文字", "scope": "all",
                "expected_revision": task["confirmed_revision"],
                "expected_task_version": task["task_version"]}

    @staticmethod
    def feedback_confirmation(draft):
        return {"draft_id": draft["draft_id"],
                "expected_draft_version": draft["draft_version"],
                "proposal_digest": draft["proposal_digest"],
                "expected_task_version": draft["base_task_version"], "action": "confirm"}

    def test_request_feedback_and_confirmations_share_independent_conversation(self):
        draft, _ = self.service.save_draft(self.actor, "demo", "draft", BODY)
        conversation_id = draft["conversation_id"]
        before = self.service.get_conversation(self.actor, conversation_id)
        self.assertEqual(["request"], [item["kind"] for item in before["messages"]])
        self.assertEqual([], before["confirmations"])

        task, _ = self.service.create_task(self.actor, "confirm", {
            "draft_id": draft["draft_id"], "expected_draft_version": draft["draft_version"],
            "proposal_digest": draft["proposal_digest"], "action": "confirm"})
        self.assertEqual(conversation_id, task["conversation_id"])
        feedback, _ = self.service.save_feedback(
            self.actor, task["task_id"], "feedback", self.feedback_body(task))
        middle = self.service.get_conversation(self.actor, conversation_id)
        self.assertEqual(["request", "feedback"], [item["kind"] for item in middle["messages"]])
        self.assertEqual([1], [item["revision"] for item in middle["confirmations"]])

        self.service.confirm_feedback(self.actor, task["task_id"], "apply",
                                      self.feedback_confirmation(feedback))
        after = self.service.get_conversation(self.actor, conversation_id)
        self.assertEqual([1, 2], [item["revision"] for item in after["confirmations"]])
        self.assertNotIn("task_id", after["messages"][1])

    def test_replays_do_not_duplicate_messages(self):
        first, _ = self.service.save_draft(self.actor, "demo", "same", BODY)
        replay, replayed = self.service.save_draft(self.actor, "demo", "same", BODY)
        self.assertTrue(replayed)
        self.assertEqual(first["draft_id"], replay["draft_id"])
        task, _ = self.service.create_task(self.actor, "confirm", {
            "draft_id": first["draft_id"], "expected_draft_version": first["draft_version"],
            "proposal_digest": first["proposal_digest"], "action": "confirm"})
        one, _ = self.service.save_feedback(self.actor, task["task_id"], "same-feedback",
                                            self.feedback_body(task))
        two, replayed = self.service.save_feedback(self.actor, task["task_id"], "same-feedback",
                                                   self.feedback_body(task))
        self.assertTrue(replayed)
        self.assertEqual(one["draft_id"], two["draft_id"])
        snapshot = self.repo.get_conversation(self.actor, first["conversation_id"])
        self.assertEqual(2, len(snapshot.messages))
        self.assertEqual(1, len(snapshot.confirmations))

    def test_invalid_message_rolls_back_draft(self):
        draft = Draft("draft_bad", self.actor.tenant_id, self.actor.user_id, "demo", "default",
                      json.dumps(BODY, sort_keys=True), conversation_id="conversation_bad")
        message = ConversationMessage(
            "message_bad", "conversation_bad", self.actor.tenant_id, self.actor.user_id,
            "other_project", "draft_bad", 1, "request", "text", datetime.now(UTC))
        with self.assertRaises(ServiceError) as raised:
            self.repo.save_draft(self.actor, "bad", draft, conversation_message=message)
        self.assertEqual("CONVERSATION_BINDING_CONFLICT", raised.exception.code)
        with self.assertRaises(ServiceError):
            self.repo.get_draft(self.actor, "draft_bad")

    def test_owner_and_current_project_access_are_required(self):
        draft, _ = self.service.save_draft(self.actor, "demo", "draft", BODY)
        with self.assertRaises(ServiceError) as raised:
            self.service.get_conversation(Actor("tenant-a", "other"), draft["conversation_id"])
        self.assertEqual("NOT_FOUND", raised.exception.code)
        self.service.projects.grants.clear()
        with self.assertRaises(ServiceError) as raised:
            self.service.get_conversation(self.actor, draft["conversation_id"])
        self.assertEqual("PROJECT_ACCESS_DENIED", raised.exception.code)
        self.assertEqual((), self.service.list_conversations(self.actor, "demo"))


class SQLiteConversationStorageTest(unittest.TestCase):
    def test_restart_recovers_messages_confirmations_and_legacy_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            path = directory + "/ledger.sqlite3"
            actor = Actor("tenant-a", "user-a")
            service = make_service(SQLiteTaskRepository(path))
            task = service.create_task(actor, "confirm", confirmation(service, actor))[0]
            conversation_id = task["conversation_id"]
            reopened = make_service(SQLiteTaskRepository(path))
            view = reopened.get_conversation(actor, conversation_id)
            self.assertEqual(1, len(view["messages"]))
            self.assertEqual(task["task_id"], view["confirmations"][0]["task_id"])

            with closing(sqlite3.connect(path)) as db, db:
                payload = json.loads(db.execute(
                    "SELECT payload FROM local_ledger WHERE id=1").fetchone()[0])

                def downgrade(node):
                    if isinstance(node, dict):
                        if node.get("type") in {"Task", "Draft"}:
                            node["value"].pop("conversation_id", None)
                        if node.get("type") == "dict":
                            node["value"] = [pair for pair in node["value"]
                                             if not (isinstance(pair[0], str)
                                                     and pair[0] in {"_conversation_messages",
                                                                     "_conversation_confirmations"})]
                        for value in node.values():
                            downgrade(value)
                    elif isinstance(node, list):
                        for value in node:
                            downgrade(value)

                downgrade(payload)
                db.execute("UPDATE local_ledger SET payload=? WHERE id=1",
                           (json.dumps(payload, ensure_ascii=False, separators=(",", ":")),))
            legacy = SQLiteTaskRepository(path)
            self.assertIsNone(legacy.get(actor, task["task_id"]).conversation_id)
            with self.assertRaises(ServiceError) as raised:
                legacy.get_conversation(actor, conversation_id)
            self.assertEqual("NOT_FOUND", raised.exception.code)


if __name__ == "__main__":
    unittest.main()
