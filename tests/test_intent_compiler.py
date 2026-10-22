from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from dsh_retrieval.adapters.projects import LocalProjectAccess
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.intent_compiler import DetectorBinding, IntentCompiler, evaluate_facts
from dsh_retrieval.intent_draft import IntentDraftStore
from dsh_retrieval.intent_review import Capability, IntentReviewService
from dsh_retrieval.models import Actor
from dsh_retrieval.query_spec import digest


RAW = "不要文字 不要水印 横图优先"


def interpretation(raw=RAW, *, unknown=False):
    text, watermark, preference = "不要文字", "不要水印", "横图优先"
    def row(ident, phrase, kind, hardness, predicate, value):
        start = raw.index(phrase)
        return {"item_id": ident, "start": start, "end": start + len(phrase), "quote": phrase,
                "kind": kind, "hardness": hardness, "predicate_ref": predicate,
                "operator": "eq", "value": value, "question": None}
    items = [row("r1", text, "constraint", "hard", "text.present@1", False),
             row("r2", watermark, "constraint", "hard", "watermark.present@1", False),
             row("r3", preference, "preference", "soft", "orientation@1", "landscape")]
    if unknown:
        items[1]["predicate_ref"] = "imaginary@1"
    return {"schema_version": "intent-interpretation@1", "raw_digest": digest({"raw_request": raw}),
            "media_type": "image", "items": items}


class IntentCompilerTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.actor = Actor("tenant", "owner")
        self.access = LocalProjectAccess({("tenant", "owner", "project", "dimension")})
        self.caps = [Capability(ref, "constraint", "image", ("eq",), "boolean", "verified",
                                "fixture-validator@1", "fixture-evidence@1")
                     for ref in ("text.present@1", "watermark.present@1")]
        self.reviewer = IntentReviewService(self.caps, registry_version="fixture@1")
        self.store = IntentDraftStore(Path(self.temp.name) / "drafts.db", self.access, self.reviewer)
        self.bindings = [DetectorBinding(ref, "image", "fixture-validator@1", "fixture-detector@1",
                                         "fixture-evidence@1")
                         for ref in ("text.present@1", "watermark.present@1")]
        self.compiler = IntentCompiler(self.store, self.bindings)

    def create(self, proposal=None):
        return self.store.create(self.actor, "project", "dimension", "new", RAW,
                                 interpretation() if proposal is None else proposal)

    def preview(self, created, *, compiler=None, digest_value=None, version=None, action="confirm_preview"):
        return (compiler or self.compiler).preview(self.actor, created["draft_id"],
            created["draft_version"] if version is None else version,
            created["review"]["review_digest"] if digest_value is None else digest_value, action=action)

    def assert_code(self, code, action):
        with self.assertRaises(ServiceError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)

    def test_two_hard_rules_share_detector_and_soft_preference_is_separate(self):
        plan = self.preview(self.create())
        self.assertEqual(len(plan["rules"]), 2)
        self.assertEqual(len(plan["steps"]), 1)
        self.assertEqual({rule["value"] for rule in plan["rules"]}, {False})
        self.assertEqual(plan["preferences"][0]["capability_status"], "unsupported")
        self.assertFalse(plan["execution_allowed"])
        self.assertFalse(plan["creates_task"])
        self.assertEqual(plan["preview_digest"], self.preview(self.store.get(self.actor, plan["draft_id"]))["preview_digest"])

    def test_unknown_hard_condition_blocks_all_rules(self):
        self.assert_code("INTERPRETATION_BLOCKED", lambda: self.preview(self.create(interpretation(unknown=True))))

    def test_missing_or_mismatched_detector_fails_closed(self):
        created = self.create()
        self.assert_code("DETECTOR_NOT_RELEASED", lambda: self.preview(created, compiler=IntentCompiler(
            self.store, self.bindings[:1])))
        bad = DetectorBinding("watermark.present@1", "image", "different-validator@1",
                              "fixture-detector@1", "fixture-evidence@1")
        self.assert_code("DETECTOR_NOT_RELEASED", lambda: self.preview(created, compiler=IntentCompiler(
            self.store, (self.bindings[0], bad))))

    def test_action_version_digest_and_access_required(self):
        created = self.create()
        self.assert_code("CONFIRMATION_REQUIRED", lambda: self.preview(created, action="view"))
        self.assert_code("DRAFT_VERSION_CONFLICT", lambda: self.preview(created, version=2))
        self.assert_code("DRAFT_VERSION_CONFLICT", lambda: self.preview(created, digest_value="stale"))
        self.access.grants.clear()
        self.assert_code("PROJECT_ACCESS_DENIED", lambda: self.preview(created))

    def test_registry_change_invalidates_review_digest(self):
        created = self.create()
        self.store.reviewer = IntentReviewService(self.caps, registry_version="fixture@2")
        self.assert_code("DRAFT_VERSION_CONFLICT", lambda: self.preview(created))

    def test_unknown_or_failed_hard_fact_cannot_count_as_valid(self):
        plan = self.preview(self.create())
        step = plan["steps"][0]["step_id"]
        self.assertEqual(evaluate_facts(plan, {step: {"text.present@1": False}})["verdict"], "unknown")
        self.assertEqual(evaluate_facts(plan, {step: {"text.present@1": False}})["rules"][1]["verdict"], "unknown")
        self.assertEqual(evaluate_facts(plan, {step: {"text.present@1": False,
                                                     "watermark.present@1": True}})["verdict"], "fail")
        complete = evaluate_facts(plan, {step: {"text.present@1": False, "watermark.present@1": False}})
        self.assertEqual(complete["verdict"], "pass")
        self.assertNotIn("accepted_count", complete)

    def test_mutated_or_empty_preview_cannot_pass(self):
        plan = self.preview(self.create())
        plan["rules"] = []
        self.assert_code("INVALID_SPEC", lambda: evaluate_facts(plan, {}))
        plan = self.preview(self.store.get(self.actor, plan["draft_id"]))
        plan["rules"][0]["value"] = True
        self.assert_code("INVALID_SPEC", lambda: evaluate_facts(plan, {}))


if __name__ == "__main__":
    unittest.main()
