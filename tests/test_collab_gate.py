import importlib.util
import json
from pathlib import Path
import copy
import subprocess
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "collab_gate", Path(__file__).resolve().parents[1] / "tools/collab_gate.py"
)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class CollaborationGateTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run_git("init", "-b", "codex/task-core")
        policy = {"roles": {"core": {"branch": "codex/task-core", "allow": [
            "src/core/", "docs/dev/handoffs/core.md"
        ]}}}
        self.write(gate.POLICY, json.dumps(policy))
        self.write("src/core/existing.py", "original\n")
        self.write("shared.py", "protected\n")
        self.run_git("add", ".")
        self.commit()
        self.base = self.run_git("rev-parse", "HEAD")
        self.write("docs/dev/handoffs/core.md", "Ready for review\n")

    def run_git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.root, stderr=subprocess.PIPE).decode().strip()

    def commit(self):
        self.run_git("-c", "user.name=Gate Test", "-c", "user.email=gate@example.invalid",
                     "-c", "commit.gpgsign=false", "commit", "-m", "test fixture")

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def check(self):
        gate.check_scope(self.root, "core", self.base)

    def test_allowed_untracked_file_passes(self):
        self.write("src/core/new.py", "pass\n")
        self.check()

    def test_untracked_shared_file_rejected(self):
        self.write("contracts/new.json", "{}")
        with self.assertRaisesRegex(ValueError, "越界"):
            self.check()

    def test_staged_shared_edit_rejected_even_if_worktree_restored(self):
        self.write("shared.py", "changed\n")
        self.run_git("add", "shared.py")
        self.write("shared.py", "protected\n")
        with self.assertRaisesRegex(ValueError, "越界"):
            self.check()

    def test_committed_shared_edit_rejected(self):
        self.write("shared.py", "changed\n")
        self.run_git("add", "shared.py")
        self.commit()
        with self.assertRaisesRegex(ValueError, "越界"):
            self.check()

    def test_rename_from_shared_into_allowed_still_rejected(self):
        self.run_git("mv", "shared.py", "src/core/moved.py")
        with self.assertRaisesRegex(ValueError, "shared.py"):
            self.check()

    def test_worker_cannot_relax_policy(self):
        policy = json.loads((self.root / gate.POLICY).read_text())
        policy["roles"]["core"]["allow"].append("shared.py")
        self.write(gate.POLICY, json.dumps(policy))
        self.write("shared.py", "changed\n")
        with self.assertRaisesRegex(ValueError, "shared.py"):
            self.check()

    def test_wrong_branch_rejected(self):
        self.run_git("switch", "-c", "codex/wrong")
        with self.assertRaisesRegex(ValueError, "分支"):
            self.check()

    def test_missing_handoff_rejected(self):
        (self.root / "docs/dev/handoffs/core.md").unlink()
        with self.assertRaisesRegex(ValueError, "交接"):
            self.check()

    def test_nested_instruction_and_env_rejected(self):
        for name in ("src/core/AGENTS.md", "src/core/.env", "src/core/.env.local"):
            self.assertFalse(gate.allowed(name, ["src/core/"]))
        self.assertFalse(gate.allowed("src/core_extra/a.py", ["src/core/"]))

    def test_invalid_base_rejected(self):
        with self.assertRaises(ValueError):
            gate.check_scope(self.root, "core", "does-not-exist")


class IntegrationGateTest(unittest.TestCase):
    run_git = CollaborationGateTest.run_git
    commit = CollaborationGateTest.commit
    write = CollaborationGateTest.write

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "main"
        self.root.mkdir()
        self.run_git("init", "-b", "main")
        self.write("seed.txt", "fixture")
        self.run_git("add", ".")
        self.commit()
        source = self.run_git("rev-parse", "HEAD")
        self.policy = {"roles": {
            "integration": {"branch": "codex/integration", "allow": ["src/", "docs/", "tests/"]},
            "core": {"branch": "codex/task-core", "allow": ["src/core/", "docs/dev/handoffs/core.md"]},
            "retrieval": {"branch": "codex/retrieval-adapters", "allow": ["src/retrieval/"]},
            "cleaning": {"branch": "codex/cleaning-adapter", "allow": ["src/cleaning/"]}}}
        self.task_path = "docs/dev/tasks/sample.json"
        self.task = {"role": "integration", "branch": "codex/integration", "source_base": source,
                     "files": ["src/shared.py", "docs/dev/handoffs/integration.md"], "acceptance": "all tests"}
        self.write(gate.POLICY, json.dumps(self.policy))
        self.write(self.task_path, json.dumps(self.task))
        self.write("src/shared.py", "original")
        self.write("src/core/a.py", "protected")
        self.run_git("add", ".")
        self.commit()
        self.base = self.run_git("rev-parse", "HEAD")
        self.main = self.root
        linked = Path(self.temp.name) / "linked"
        self.run_git("worktree", "add", "-b", "codex/integration", str(linked), self.base)
        self.root = linked
        self.write("docs/dev/handoffs/integration.md", "fixture evidence")

    def check(self):
        gate.check_scope(self.root, "integration", self.base, self.task_path)

    def publish_task(self, task):
        # Tests emulate an externally reviewed/published assignment, not candidate edits.
        self.write(self.task_path, json.dumps(task))
        self.run_git("add", self.task_path)
        self.commit()
        self.base = self.run_git("rev-parse", "HEAD")

    def test_exact_assigned_file_passes(self):
        self.write("src/shared.py", "changed")
        self.check()

    def test_unassigned_shared_file_fails(self):
        self.write("src/other.py", "not assigned")
        with self.assertRaisesRegex(ValueError, "越界"):
            self.check()

    def test_missing_task_fails(self):
        with self.assertRaisesRegex(ValueError, "任务卡"):
            gate.check_scope(self.root, "integration", self.base)

    def test_candidate_task_cannot_expand_scope(self):
        self.task["files"].append("src/other.py")
        self.write(self.task_path, json.dumps(self.task))
        self.write("src/other.py", "not assigned")
        with self.assertRaisesRegex(ValueError, "任务卡"):
            self.check()

    def test_candidate_policy_cannot_expand_scope(self):
        self.policy["roles"]["integration"]["allow"].append("seed.txt")
        self.write(gate.POLICY, json.dumps(self.policy))
        with self.assertRaisesRegex(ValueError, "越界"):
            self.check()

    def test_published_task_cannot_claim_other_roles(self):
        for path in ("src/core/a.py", "src/retrieval/a.py", "src/cleaning/a.py"):
            with self.subTest(path=path):
                task = copy.deepcopy(self.task)
                task["files"].append(path)
                self.publish_task(task)
                with self.assertRaisesRegex(ValueError, "其他角色"):
                    self.check()

    def test_published_task_cannot_claim_outside_a_scope(self):
        self.task["files"].append("vendor/source.py")
        self.publish_task(self.task)
        with self.assertRaisesRegex(ValueError, "范围外"):
            self.check()

    def test_invalid_task_paths_fall_closed(self):
        for path in ("../src/a.py", "/tmp/a.py", "src/", "src/*.py", "src//a.py", "src/./a.py"):
            with self.subTest(path=path):
                task = copy.deepcopy(self.task)
                task["files"].append(path)
                self.publish_task(task)
                with self.assertRaisesRegex(ValueError, "结构"):
                    self.check()

    def test_bad_task_metadata_fails(self):
        for key, value in (("role", "core"), ("branch", "main"), ("source_base", "HEAD"),
                           ("acceptance", ""), ("files", [])):
            with self.subTest(key=key):
                task = copy.deepcopy(self.task)
                task[key] = value
                self.publish_task(task)
                with self.assertRaisesRegex(ValueError, "结构"):
                    self.check()

    def test_staged_unassigned_change_fails_even_if_restored(self):
        self.write("src/core/a.py", "changed")
        self.run_git("add", "src/core/a.py")
        self.write("src/core/a.py", "protected")
        with self.assertRaisesRegex(ValueError, "越界"):
            self.check()

    def test_committed_unassigned_change_fails(self):
        self.write("src/core/a.py", "changed")
        self.run_git("add", "src/core/a.py")
        self.commit()
        with self.assertRaisesRegex(ValueError, "越界"):
            self.check()

    def test_rename_cannot_hide_other_owner(self):
        self.run_git("mv", "src/core/a.py", "src/new.py")
        with self.assertRaisesRegex(ValueError, "src/core/a.py"):
            self.check()

    def test_symlink_fails(self):
        path = self.root / "src/shared.py"
        path.unlink()
        path.symlink_to(self.main / "src/shared.py")
        with self.assertRaisesRegex(ValueError, "符号链接"):
            self.check()

    def test_wrong_branch_fails(self):
        self.run_git("switch", "-c", "codex/other")
        with self.assertRaisesRegex(ValueError, "分支"):
            self.check()

    def test_staged_link_hidden_by_worktree_file_fails(self):
        path = self.root / "src/shared.py"
        path.unlink()
        path.symlink_to(self.main / "src/shared.py")
        self.run_git("add", "src/shared.py")
        path.unlink()
        path.write_text("original")
        with self.assertRaisesRegex(ValueError, "链接"):
            self.check()

    def test_scope_success_still_requires_regression(self):
        with patch.object(gate.Path, "cwd", return_value=self.root), patch.object(
                gate.sys, "argv", ["gate", "check", "--role", "integration", "--base", self.base,
                                   "--task", self.task_path]), patch.object(gate, "baseline", return_value=1) as regression:
            self.assertEqual(gate.main(), 1)
            regression.assert_called_once_with(self.root)

    def test_scope_failure_does_not_execute_candidate_tests(self):
        self.write("src/unassigned.py", "pass")
        with patch.object(gate.Path, "cwd", return_value=self.root), patch.object(
                gate.sys, "argv", ["gate", "check", "--role", "integration", "--base", self.base,
                                   "--task", self.task_path]), patch.object(gate, "baseline") as regression:
            self.assertEqual(gate.main(), 1)
            regression.assert_not_called()

    def test_missing_handoff_fails(self):
        (self.root / "docs/dev/handoffs/integration.md").unlink()
        with self.assertRaisesRegex(ValueError, "交接"):
            self.check()

    def test_main_checkout_fails(self):
        clone = Path(self.temp.name) / "clone"
        self.run_git("clone", "--local", "--branch", "codex/integration", str(self.main), str(clone))
        with self.assertRaisesRegex(ValueError, "独立 worktree"):
            gate.check_scope(clone, "integration", self.base, self.task_path)

    def test_legacy_roles_unchanged_in_real_policy(self):
        root = Path(__file__).resolve().parents[1]
        current = json.loads((root / gate.POLICY).read_text())
        original = json.loads(gate.git(root, "show", "22c62143dd20d49f0426f1fccd2232fd13de6699:" + gate.POLICY))
        for role in ("core", "retrieval", "cleaning"):
            self.assertEqual(current["roles"][role], original["roles"][role])


if __name__ == "__main__":
    unittest.main()
