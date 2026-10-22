import unittest
from datetime import UTC, datetime

from dsh_retrieval.conversation_models import (
    ConversationConfirmation, ConversationMessage, ConversationSnapshot,
)
from dsh_retrieval.models import Task
from dsh_retrieval.proposals import Draft


NOW = datetime(2026, 10, 22, 16, 0, tzinfo=UTC)


def message(**changes):
    values = dict(message_id="msg_1", conversation_id="conv_1", tenant_id="tenant_1",
                  owner_user_id="user_1", project_id="project_1", draft_id="draft_1",
                  sequence=1, kind="request", text="找 12 张清晰的客厅图片", created_at=NOW)
    values.update(changes)
    return ConversationMessage(**values)


class ConversationContractTests(unittest.TestCase):
    def test_public_views_hide_storage_ownership_and_keep_draft_boundary(self):
        item = message()
        link = ConversationConfirmation("confirm_1", "conv_1", "draft_1", "task_1", 1, NOW)
        snapshot = ConversationSnapshot("conv_1", "project_1", (item,), (link,))

        view = snapshot.to_view()
        self.assertNotIn("tenant_id", view["messages"][0])
        self.assertNotIn("owner_user_id", view["messages"][0])
        self.assertNotIn("task_id", view["messages"][0])
        self.assertEqual("task_1", view["confirmations"][0]["task_id"])
        self.assertEqual(1, view["confirmations"][0]["revision"])

    def test_message_rejects_invalid_identity_sequence_kind_text_and_time(self):
        cases = (
            {"message_id": "bad id"}, {"sequence": 0}, {"sequence": True},
            {"kind": "assistant_thought"}, {"text": "   "}, {"text": "x" * 4001},
            {"created_at": datetime(2026, 10, 22, 16, 0)},
        )
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                message(**changes)

    def test_snapshot_requires_strict_sequence_and_matching_bindings(self):
        with self.assertRaises(ValueError):
            ConversationSnapshot("conv_1", "project_1",
                                 (message(sequence=2), message(message_id="msg_2", sequence=1)))
        with self.assertRaises(ValueError):
            ConversationSnapshot("conv_1", "project_1", (message(project_id="project_2"),))
        with self.assertRaises(ValueError):
            ConversationSnapshot("conv_1", "project_1", (message(),),
                                 (ConversationConfirmation("confirm_1", "conv_2", "draft_1",
                                                           "task_1", 1, NOW),))

    def test_confirmation_requires_complete_formal_binding(self):
        for task_id, revision in (("", 1), ("task_1", 0), ("task_1", True)):
            with self.subTest(task_id=task_id, revision=revision), self.assertRaises(ValueError):
                ConversationConfirmation("confirm_1", "conv_1", "draft_1",
                                         task_id, revision, NOW)

    def test_task_and_draft_conversation_links_are_optional_and_public(self):
        task = Task("task_1", "tenant_1", "user_1", "request", "image", 1,
                    "demo", False, "manual", "sha256:x", conversation_id="conv_1")
        draft = Draft("draft_1", "tenant_1", "user_1", "project_1", "dimension_1",
                      '{"dimension_id":"dimension_1","query_spec":{}}',
                      conversation_id="conv_1")
        self.assertEqual("conv_1", task.to_summary()["conversation_id"])
        self.assertEqual("conv_1", draft.to_summary()["conversation_id"])

        legacy_task = Task("task_2", "tenant_1", "user_1", "request", "image", 1,
                           "demo", False, "manual", "sha256:y")
        legacy_draft = Draft("draft_2", "tenant_1", "user_1", "project_1",
                             "dimension_1", '{"dimension_id":"dimension_1","query_spec":{}}')
        self.assertIsNone(legacy_task.conversation_id)
        self.assertIsNone(legacy_draft.conversation_id)


if __name__ == "__main__":
    unittest.main()
