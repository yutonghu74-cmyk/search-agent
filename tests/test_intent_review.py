from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import unittest

from dsh_retrieval.intent_review import Capability, IntentReviewService
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.query_spec import digest


def proposal(raw, **changes):
    row = {"item_id": "r1", "start": 0, "end": len(raw), "quote": raw,
           "kind": "constraint", "hardness": "hard", "predicate_ref": "text.present@1",
           "operator": "eq", "value": False, "question": None}
    row.update(changes)
    return {"schema_version": "intent-interpretation@1", "raw_digest": digest({"raw_request": raw}),
            "media_type": "image", "items": [row]}


class IntentReviewTest(unittest.TestCase):
    def setUp(self):
        # Synthetic capability only: this is not an installed OCR validator.
        self.cap = Capability("text.present@1", "constraint", "image", ("eq",), "boolean",
                              "verified", "fixture-validator@1", "fixture-evaluation@1")
        self.service = IntentReviewService((self.cap,), registry_version="fixture@1")

    def test_without_interpreter_everything_remains_unresolved(self):
        raw = "找客厅图，不要文字，横图优先，沙发完整"
        report = self.service.review(raw)
        self.assertEqual(report["raw_request"], raw)
        self.assertEqual(report["items"][0]["quote"], raw)
        self.assertTrue(report["has_blockers"])
        self.assertFalse(report["execution_allowed"])
        self.assertFalse(report["creates_task"])

    def test_negative_value_and_original_words_preserved(self):
        raw = "不要文字"
        report = self.service.review(raw, proposal(raw))
        self.assertEqual(report["items"][0]["quote"], raw)
        self.assertIs(report["items"][0]["value"], False)
        self.assertEqual(report["items"][0]["capability_status"], "automatable")
        self.assertTrue(report["requires_user_review"])
        self.assertFalse(report["execution_allowed"])

    def test_unknown_new_condition_kept_not_dropped(self):
        raw = "有故事感的空间"
        result = self.service.review(raw, proposal(raw, predicate_ref="novel.story@1"))
        self.assertEqual(result["items"][0]["quote"], raw)
        self.assertEqual(result["items"][0]["capability_status"], "unsupported")
        self.assertTrue(result["has_blockers"])

    def test_default_registry_never_claims_ocr_supported(self):
        result = IntentReviewService().review("不要文字", proposal("不要文字"))
        self.assertEqual(result["items"][0]["reason"], "NO_REGISTERED_CAPABILITY")

    def test_omitted_suffix_added_as_blocking_unparsed(self):
        raw = "不要文字 沙发完整"
        p = proposal(raw, end=4, quote="不要文字")
        result = self.service.review(raw, p)
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(result["items"][1]["quote"], "沙发完整")
        self.assertTrue(result["items"][1]["blocks_automatic_execution"])

    def test_preference_is_not_hard_constraint(self):
        raw = "横图优先"
        p = proposal(raw, kind="preference", hardness="soft", predicate_ref="orientation@1", value="landscape")
        report = self.service.review(raw, p)
        self.assertEqual(report["items"][0]["hardness"], "soft")
        self.assertFalse(report["items"][0]["blocks_automatic_execution"])
        self.assertEqual(report["items"][0]["capability_status"], "unsupported")
        p["items"][0]["hardness"] = "hard"
        with self.assertRaises(ServiceError):
            self.service.review(raw, p)

    def test_ambiguity_stays_blocking_even_with_verified_capability(self):
        report = self.service.review("文字少一些", proposal("文字少一些", question="允许多大的文字区域？"))
        self.assertEqual(report["items"][0]["capability_status"], "needs_clarification")

    def test_conflicting_equalities_need_clarification(self):
        raw = "不要文字 需要文字"
        p = proposal(raw, end=4, quote="不要文字")
        other = dict(p["items"][0], item_id="r2", start=5, end=9, quote="需要文字", value=True)
        p["items"].append(other)
        report = self.service.review(raw, p)
        self.assertTrue(all(i["reason"] == "CONFLICTING_EQUALITIES" for i in report["items"]))

    def test_source_not_reinterpreted_as_style_capability(self):
        cap = replace(self.cap, kind="constraint", predicate_ref="style.home@1")
        raw = "来自某平台的图片"
        report = IntentReviewService((cap,)).review(raw, proposal(raw, kind="source", predicate_ref="style.home@1"))
        self.assertEqual(report["items"][0]["capability_status"], "unsupported")

    def test_model_cannot_claim_approval_or_supply_code(self):
        for field in ("execution_allowed", "status", "script", "validator_ref", "tenant_id"):
            p = proposal("不要文字")
            p["items"][0][field] = "untrusted"
            with self.assertRaises(ServiceError):
                self.service.review("不要文字", p)

    def test_original_text_digest_and_spans_checked(self):
        for changes in ({"start": -1}, {"start": True}, {"end": 99}, {"quote": "需要文字"}, {"kind": []}):
            with self.assertRaises(ServiceError):
                self.service.review("不要文字", proposal("不要文字", **changes))
        with self.assertRaises(ServiceError):
            self.service.review("需要文字", proposal("不要文字"))

    def test_unavailable_and_manual_capabilities_cannot_auto_pass(self):
        for status, expected in (("unavailable", "unsupported"), ("manual", "manual_review")):
            service = IntentReviewService((replace(self.cap, status=status),))
            report = service.review("不要文字", proposal("不要文字"))
            self.assertEqual(report["items"][0]["capability_status"], expected)
            self.assertTrue(report["has_blockers"])

    def test_capability_release_requires_evidence(self):
        with self.assertRaises(ValueError):
            replace(self.cap, evidence_ref="")
        with self.assertRaises(ValueError):
            IntentReviewService((self.cap, self.cap))

    def test_type_operator_media_mismatch(self):
        for changes in ({"value": 1}, {"operator": "gt"}):
            report = self.service.review("不要文字", proposal("不要文字", **changes))
            self.assertEqual(report["items"][0]["reason"], "CAPABILITY_PARAMETER_MISMATCH")
        p = proposal("不要文字")
        p["media_type"] = "video"
        self.assertEqual(self.service.review("不要文字", p)["items"][0]["capability_status"], "unsupported")

    def test_non_finite_or_structured_value_rejected(self):
        for value in (float("nan"), float("inf"), {"code": "x"}, [1]):
            with self.assertRaises(ServiceError):
                self.service.review("不要文字", proposal("不要文字", value=value))

    def test_digest_stable_and_covers_registry_and_original(self):
        p = proposal("不要文字")
        before = deepcopy(p)
        report = self.service.review("不要文字", p)
        self.assertEqual(p, before)
        self.assertEqual(report, self.service.review("不要文字", p))
        changed = IntentReviewService((replace(self.cap, evidence_ref="new-evaluation"),), registry_version="fixture@2")
        self.assertNotEqual(report["review_digest"], changed.review("不要文字", p)["review_digest"])
        report["items"][0]["value"] = True
        self.assertIs(self.service.review("不要文字", p)["items"][0]["value"], False)

    def test_cli_truthfully_returns_unresolved_without_model(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, str(root / "tools/intent_review.py"), "--request", "找客厅图，不要水印"],
                                check=True, capture_output=True, text=True)
        report = json.loads(result.stdout)
        self.assertFalse(report["execution_allowed"])
        self.assertEqual(report["items"][0]["kind"], "unresolved")
