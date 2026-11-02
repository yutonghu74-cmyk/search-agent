from copy import deepcopy
import unittest

from dsh_retrieval.errors import ServiceError
from dsh_retrieval.expansion_models import normalize_grouped_expansion, preview_grouped_expansion
from dsh_retrieval.proposals import normalize_proposal
from draft_helpers import BODY
from dsh_retrieval.expansion_models import preview_edited_groups


class GroupedExpansionTest(unittest.TestCase):
    def test_confirmation_retires_edit_actions_only_after_success(self):
        import shutil
        import subprocess
        from dsh_retrieval.chat_ui import CHAT_JS
        if not shutil.which("node"):
            self.skipTest("Node is required for JavaScript behavior verification")
        edit = CHAT_JS[CHAT_JS.index("function editProposal"):CHAT_JS.index("function showSavedGroups")]
        confirm = CHAT_JS[CHAT_JS.index("async function confirmTask"):CHAT_JS.index("async function prepareFeedback")]
        script = r"""
const assert = require('node:assert/strict');
const state = {task: null, draft: {draft_id: 'draft', draft_version: 1, proposal_digest: 'digest'}};
let removed = 0, requests = 0, fail = true;
const timeline = {querySelectorAll: () => [{remove: () => removed++}],
                  replaceChildren: () => {throw new Error('confirmed draft must not reopen')}};
const status = {textContent: ''};
const $ = id => id === 'status' ? status : {classList: {remove: () => {}}};
const setBusy = () => {}, key = () => 'key', message = () => {};
const refreshHistory = async () => {}, localStorage = {setItem: () => {}};
async function api() {requests++; if(fail) throw new Error('retryable');
  return {task_id: 'task', confirmed_revision: 1, run_status: 'PLANNING'};}
""" + edit + confirm + r"""
(async () => {
  await confirmTask();
  assert.equal(state.draft.draft_id, 'draft');
  assert.equal(removed, 0);
  assert.equal(status.textContent, 'retryable');
  fail = false;
  await confirmTask();
  assert.equal(removed, 1);
  assert.equal(state.draft, null);
  editProposal();
  await confirmTask();
  assert.equal(requests, 2);
})().catch(error => {console.error(error); process.exitCode = 1});
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_editor_javascript_multiline_and_error_visibility(self):
        import shutil
        import subprocess
        from dsh_retrieval.chat_ui import CHAT_JS
        if not shutil.which("node"):
            self.skipTest("Node is required for JavaScript behavior verification")
        parse = CHAT_JS[CHAT_JS.index("function groupPreviewBody"):CHAT_JS.index("function editProposal")]
        busy = CHAT_JS[CHAT_JS.index("function setBusy"):CHAT_JS.index("function resetTimeline")]
        script = r"""
const assert = require('node:assert/strict');
const elements = {'group-lines': {value: '狗 | 金毛，柯基\n猫 |'},
                  'group-parent': {value: '动物'}, status: {textContent: ''}};
const $ = id => elements[id];
const document = {querySelectorAll: () => []};
""" + parse + busy + r"""
assert.deepEqual(groupPreviewBody('动物').groups, [
  {primary_label: '狗', auxiliary_terms: ['金毛', '柯基']},
  {primary_label: '猫', auxiliary_terms: []}]);
setBusy(true);
elements.status.textContent = 'FORBIDDEN: project access denied';
setBusy(false);
assert.match(elements.status.textContent, /FORBIDDEN/);
setBusy(true); setBusy(false);
assert.equal(elements.status.textContent, '');
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_manual_groups_allow_zero_terms_and_reject_bad_shapes(self):
        result = preview_edited_groups("动物", [
            {"primary_label": "狗", "auxiliary_terms": ["金毛", "柯基"]},
            {"primary_label": "猫", "auxiliary_terms": []}])
        self.assertEqual(result["plan"]["generator"], "user-edited@1")
        self.assertEqual(result["plan"]["groups"][1]["requested_expansion_count"], 0)
        for terms in ([{}], ["金毛", "金毛"], [" "]):
            with self.assertRaises(ValueError):
                preview_edited_groups(None, [{"primary_label": "狗", "auxiliary_terms": terms}])
        altered = deepcopy(result["plan"])
        altered["schema_version"] = "untrusted@9"
        with self.assertRaises(ValueError):
            normalize_grouped_expansion(altered)

    def test_edit_changes_confirmation_digest_and_survives_sqlite_restart(self):
        import tempfile
        from pathlib import Path
        from dsh_retrieval.adapters.task_storage.sqlite import SQLiteTaskRepository
        from dsh_retrieval.models import Actor
        from draft_helpers import make_service, confirmation
        actor = Actor("tenant", "owner")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.sqlite3"
            service = make_service(SQLiteTaskRepository(path))
            body = deepcopy(BODY)
            body["query_spec"]["grouped_expansion"] = preview_edited_groups(
                "动物", [{"primary_label": "狗", "auxiliary_terms": ["金毛"]}])["plan"]
            first = confirmation(service, actor, key="first", body=body)
            body["query_spec"]["grouped_expansion"] = preview_edited_groups(
                "动物", [{"primary_label": "狗", "auxiliary_terms": ["柯基"]}])["plan"]
            second = confirmation(service, actor, key="second", body=body)
            self.assertNotEqual(first["proposal_digest"], second["proposal_digest"])
            task, _ = service.create_task(actor, "confirm", second)
            reopened = make_service(SQLiteTaskRepository(path))
            stored = reopened.list_revisions(actor, task["task_id"])[0]["proposal"]
            self.assertEqual(stored["query_spec"]["grouped_expansion"],
                             body["query_spec"]["grouped_expansion"])

    def test_explicit_dog_and_cat_groups_have_separate_packages(self):
        result = preview_grouped_expansion("动物，狗扩20个词，猫扩30个词")
        self.assertTrue(result["recognized"])
        plan = result["plan"]
        self.assertEqual(plan["parent_label"], "动物")
        self.assertEqual([group["primary_label"] for group in plan["groups"]], ["狗", "猫"])
        self.assertEqual([len(group["auxiliary_terms"]) for group in plan["groups"]], [20, 30])
        self.assertEqual([group["package_name"] for group in plan["groups"]], ["狗", "猫"])
        self.assertTrue(all(group["package_mode"] == "separate" for group in plan["groups"]))
        self.assertTrue(all(group["delivery_target"] is None for group in plan["groups"]))
        self.assertEqual(plan["overlap_policy"], "single_primary_per_aggregate")
        self.assertEqual(normalize_grouped_expansion(plan), plan)

    def test_auxiliary_terms_are_not_primary_labels_or_packages(self):
        plan = preview_grouped_expansion("动物，狗扩3个词")["plan"]
        group = plan["groups"][0]
        self.assertEqual(group["auxiliary_terms"], ["狗特写", "狗全身", "狗正面"])
        self.assertNotIn("狗", group["auxiliary_terms"])
        self.assertEqual(group["package_name"], "狗")

    def test_no_explicit_syntax_does_not_guess_taxonomy(self):
        result = preview_grouped_expansion("帮我找一些可爱的动物图片")
        self.assertEqual((result["recognized"], result["plan"]), (False, None))
        self.assertIn("不会猜测", result["note"])

    def test_duplicate_limit_and_tampering_are_rejected(self):
        with self.assertRaises(ValueError):
            preview_grouped_expansion("动物，狗扩20个词，狗扩10个词")
        with self.assertRaises(ValueError):
            preview_grouped_expansion("动物，狗扩201个词")
        expanded = preview_grouped_expansion("动物，狗扩200个词")
        self.assertEqual(len(expanded["plan"]["groups"][0]["auxiliary_terms"]), 200)
        plan = preview_grouped_expansion("动物，狗扩2个词")["plan"]
        plan["groups"][0]["package_name"] = "动物"
        with self.assertRaises(ValueError):
            normalize_grouped_expansion(plan)

    def test_confirmed_query_proposal_keeps_immutable_group_plan(self):
        body = deepcopy(BODY)
        plan = preview_grouped_expansion("动物，狗扩2个词，猫扩3个词")["plan"]
        body["query_spec"]["raw_request"] = "动物，狗扩2个词，猫扩3个词"
        body["query_spec"]["grouped_expansion"] = plan
        normalized = normalize_proposal(body)
        self.assertEqual(normalized["query_spec"]["grouped_expansion"], plan)
        changed = deepcopy(body)
        changed["query_spec"]["grouped_expansion"]["groups"][0]["auxiliary_terms"][0] = "伪造词"
        with self.assertRaises(ServiceError) as raised:
            normalize_proposal(changed)
        self.assertEqual(raised.exception.code, "INVALID_SPEC")


if __name__ == "__main__":
    unittest.main()
