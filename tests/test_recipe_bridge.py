from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from dsh_retrieval.adapters.projects import LocalProjectAccess
from dsh_retrieval.adapters.memory import InMemoryTaskRepository
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.intent_compiler import DetectorBinding, IntentCompiler
from dsh_retrieval.intent_draft import IntentDraftStore
from dsh_retrieval.intent_review import Capability, IntentReviewService
from dsh_retrieval.models import Actor, TaskLifecycle
from dsh_retrieval.query_spec import digest
from dsh_retrieval.recipe_bridge import compile_reviewed_recipe, require_confirmed_rules
from draft_helpers import BODY, make_service


RAW = "不要文字 不要水印 横图优先"


def interpretation():
    rows = []
    for item_id, phrase, kind, hardness, predicate, value in (
            ("text", "不要文字", "constraint", "hard", "text.present@1", False),
            ("mark", "不要水印", "constraint", "hard", "watermark.present@1", False),
            ("shape", "横图优先", "preference", "soft", "orientation@1", "landscape")):
        start = RAW.index(phrase)
        rows.append({"item_id": item_id, "start": start, "end": start + len(phrase),
                     "quote": phrase, "kind": kind, "hardness": hardness,
                     "predicate_ref": predicate, "operator": "eq", "value": value,
                     "question": None})
    return {"schema_version": "intent-interpretation@1", "raw_digest": digest({"raw_request": RAW}),
            "media_type": "image", "items": rows}


class RecipeBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.actor = Actor("tenant", "owner")
        self.access = LocalProjectAccess({("tenant", "owner", "project", "dimension")})
        refs = ("text.present@1", "watermark.present@1")
        caps = [Capability(ref, "constraint", "image", ("eq",), "boolean", "verified",
                           "fixture.validator@1", "fixture.eval@1") for ref in refs]
        reviewer = IntentReviewService(caps, registry_version="fixture@1")
        self.store = IntentDraftStore(Path(self.temp.name) / "drafts.db", self.access, reviewer)
        bindings = [DetectorBinding(ref, "image", "fixture.validator@1", "fixture.detector@1",
                                    "fixture.eval@1") for ref in refs]
        self.compiler = IntentCompiler(self.store, bindings)
        self.created = self.store.create(self.actor, "project", "dimension", "create", RAW, interpretation())

    def compile(self, compiler=None, **changes):
        args = dict(draft_id=self.created["draft_id"], expected_version=self.created["draft_version"],
                    expected_review_digest=self.created["review"]["review_digest"])
        args.update(changes)
        return compile_reviewed_recipe(compiler or self.compiler, self.actor, **args)

    def assert_code(self, code, call):
        with self.assertRaises(ServiceError) as caught:
            call()
        self.assertEqual(caught.exception.code, code)

    def test_current_review_produces_two_rules_one_step_without_soft_preference(self):
        recipe = self.compile()
        self.assertEqual(len(recipe.steps), 1)
        self.assertEqual({rule.rule_id for rule in recipe.rules}, {"text", "mark"})
        self.assertEqual({rule.predicate_ref for rule in recipe.rules},
                         {"text.present@1", "watermark.present@1"})
        self.assertEqual(recipe.source_review_digest, self.created["review"]["review_digest"])
        self.assertEqual(recipe.registry_digest, self.created["review"]["registry_digest"])
        self.assertNotIn("shape", str(recipe))

    def test_old_draft_or_registry_change_cannot_compile(self):
        self.assert_code("DRAFT_VERSION_CONFLICT", lambda: self.compile(expected_version=2))
        self.store.reviewer.registry_version = "changed"
        self.assert_code("DRAFT_VERSION_CONFLICT", self.compile)

    def test_revoked_project_access_cannot_compile(self):
        self.access.grants.clear()
        self.assert_code("PROJECT_ACCESS_DENIED", self.compile)

    def test_tampered_preview_and_path_backend_rejected(self):
        original = self.compiler.preview(self.actor, self.created["draft_id"], 1,
                                         self.created["review"]["review_digest"], action="confirm_preview")
        class AlteredCompiler:
            def __init__(self, plan):
                self.plan = plan
            def preview(self, *args, **kwargs):
                return self.plan
        changed = deepcopy(original)
        changed["rules"][0]["value"] = True
        self.assert_code("COMPILED_PREVIEW_INVALID", lambda: self.compile(AlteredCompiler(changed)))
        changed = deepcopy(original)
        changed["steps"][0]["backend_ref"] = "../../run.py"
        changed["preview_digest"] = digest({k: v for k, v in changed.items() if k != "preview_digest"})
        self.assert_code("COMPILED_PREVIEW_INVALID", lambda: self.compile(AlteredCompiler(changed)))

    def test_fake_executable_preview_rejected_even_with_matching_digest(self):
        original = self.compiler.preview(self.actor, self.created["draft_id"], 1,
                                         self.created["review"]["review_digest"], action="confirm_preview")
        original["execution_allowed"] = True
        original["preview_digest"] = digest({k: v for k, v in original.items() if k != "preview_digest"})
        class AlteredCompiler:
            def preview(self, *args, **kwargs):
                return original
        self.assert_code("COMPILED_PREVIEW_INVALID", lambda: self.compile(AlteredCompiler()))

    def confirmed_task(self, rules=None):
        body = deepcopy(BODY)
        body["query_spec"]["raw_request"] = RAW
        body["query_spec"]["rules"] = rules if rules is not None else [
            {"rule_id": ident, "predicate_ref": predicate, "operator": "eq", "value": False,
             "hardness": "hard", "origin": "user_explicit"}
            for ident, predicate in (("text", "text.present@1"), ("mark", "watermark.present@1"))]
        repository = InMemoryTaskRepository()
        service = make_service(repository)
        draft, _ = service.save_draft(self.actor, "demo", "formal-draft", body)
        summary, _ = service.create_task(self.actor, "formal-confirm", {
            "draft_id": draft["draft_id"], "expected_draft_version": draft["draft_version"],
            "proposal_digest": draft["proposal_digest"], "action": "confirm"})
        return repository.get(self.actor, summary["task_id"])

    def test_exact_formal_rules_match_without_approving_execution(self):
        recipe = self.compile()
        task = self.confirmed_task()
        self.assertIsNone(require_confirmed_rules(task, recipe))
        self.assertIsNone(task.active_cleaning_operation_id)
        self.assertEqual(task.task_version, 1)

    def test_missing_extra_or_changed_hard_rule_rejected(self):
        recipe = self.compile()
        task = self.confirmed_task()
        self.assert_code("RECIPE_RULE_MISMATCH", lambda: require_confirmed_rules(
            task, replace(recipe, rules=recipe.rules[:1])))
        changed = replace(recipe.rules[0], value=True)
        self.assert_code("RECIPE_RULE_MISMATCH", lambda: require_confirmed_rules(
            task, replace(recipe, rules=(changed, recipe.rules[1]))))
        extra = {"rule_id": "person", "predicate_ref": "person.present@1", "operator": "eq",
                 "value": False, "hardness": "hard", "origin": "user_explicit"}
        self.assert_code("RECIPE_RULE_MISMATCH", lambda: require_confirmed_rules(
            self.confirmed_task(rules=[extra] + [
                {"rule_id": r.rule_id, "predicate_ref": r.predicate_ref, "operator": "eq",
                 "value": r.value, "hardness": "hard", "origin": "user_explicit"}
                for r in recipe.rules]), recipe))

    def test_soft_rule_is_not_a_hard_cleaning_filter(self):
        recipe = self.compile()
        rules = [{"rule_id": r.rule_id, "predicate_ref": r.predicate_ref,
                  "operator": r.operator, "value": r.value, "hardness": "hard",
                  "origin": "user_explicit"} for r in recipe.rules]
        rules.append({"rule_id": "orientation", "predicate_ref": "orientation@1", "operator": "eq",
                      "value": "landscape", "hardness": "soft", "origin": "user_explicit"})
        self.assertIsNone(require_confirmed_rules(self.confirmed_task(rules), recipe))

    def test_unconfirmed_archived_or_corrupt_task_rejected(self):
        task, recipe = self.confirmed_task(), self.compile()
        for changed in (replace(task, confirmed_revision=None),
                        replace(task, lifecycle=TaskLifecycle.ARCHIVED),
                        replace(task, request_digest="sha256:" + "0" * 64),
                        replace(task, confirmed_proposal_json="broken"),
                        replace(task, media_type="video")):
            self.assert_code("RECIPE_RULE_MISMATCH", lambda changed=changed:
                             require_confirmed_rules(changed, recipe))


if __name__ == "__main__":
    unittest.main()
