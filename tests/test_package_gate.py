"""Isolated Git fixtures: new workflow must retain the old safety boundaries."""
import copy
import subprocess
import unittest
from unittest.mock import patch

from tests import test_collab_gate as fixtures

gate = fixtures.gate


class PackageGateTest(unittest.TestCase):
    run_git = fixtures.CollaborationGateTest.run_git
    commit = fixtures.CollaborationGateTest.commit
    write = fixtures.CollaborationGateTest.write
    publish_task = fixtures.IntegrationGateTest.publish_task

    def setUp(self):
        fixtures.IntegrationGateTest.setUp(self)
        (self.root / "docs/dev/handoffs/integration.md").unlink()
        self.run_git("switch", "-c", "codex/sample")
        self.task.update(branch="codex/sample", handoff="docs/dev/handoffs/sample.md",
                         files=["src/shared.py", "src/core/a.py", "docs/dev/handoffs/sample.md"],
                         tests={"mode": "targeted", "targets": ["tests.test_sample"], "reason": "isolated helper"})
        self.publish_task(self.task)
        self.write(self.task["handoff"], "fixture evidence")

    def check(self):
        return gate.package_scope(self.root, self.base, self.task_path)

    def test_assigned_cross_role_changes_on_package_branch_pass(self):
        self.write("src/core/a.py", "changed")
        self.write("src/shared.py", "changed")
        self.assertEqual(self.check(), self.task)

    def test_wrong_branch_rejected(self):
        self.run_git("switch", "-c", "codex/other")
        with self.assertRaisesRegex(ValueError, "分支"):
            self.check()

    def test_unassigned_file_rejected(self):
        self.write("src/other.py", "changed")
        with self.assertRaisesRegex(ValueError, "越界"):
            self.check()

    def test_candidate_card_cannot_relax_scope_or_tests(self):
        self.task["files"].append("src/other.py")
        self.task["tests"]["mode"] = "docs"
        import json
        self.write(self.task_path, json.dumps(self.task))
        with self.assertRaisesRegex(ValueError, "越界"):
            self.check()

    def test_policy_and_nested_instructions_protected(self):
        for name in (gate.POLICY, "tools/collab_gate.py", "tests/test_package_gate.py",
                     "src/core/AGENTS.md", "src/core/.env", "docs/dev/parallel-plan.md",
                     "docs/dev/tasks/another.json"):
            with self.subTest(name=name):
                task = copy.deepcopy(self.task)
                task["files"].append(name)
                self.publish_task(task)
                with self.assertRaisesRegex(ValueError, "授权规则"):
                    self.check()

    def test_bad_metadata_rejected(self):
        for key, value in (("role", []), ("branch", "master"), ("files", ["src/*.py"]),
                           ("source_base", "HEAD"), ("acceptance", ""), ("handoff", "docs/dev/handoffs/core.md")):
            with self.subTest(key=key):
                task = copy.deepcopy(self.task)
                task[key] = value
                self.publish_task(task)
                with self.assertRaises(ValueError):
                    self.check()

    def test_missing_handoff_fails(self):
        (self.root / self.task["handoff"]).unlink()
        with self.assertRaisesRegex(ValueError, "交接"):
            self.check()

    def test_committed_unassigned_change_rejected(self):
        self.write("src/other.py", "changed")
        self.run_git("add", "src/other.py")
        self.commit()
        with self.assertRaisesRegex(ValueError, "越界"):
            self.check()

    def test_staged_change_cannot_be_hidden(self):
        self.write("seed.txt", "changed")
        self.run_git("add", "seed.txt")
        self.write("seed.txt", "fixture")
        with self.assertRaisesRegex(ValueError, "越界"):
            self.check()

    def test_symlink_cannot_be_hidden(self):
        path = self.root / "src/shared.py"
        path.unlink()
        path.symlink_to(self.main / "src/shared.py")
        self.run_git("add", "src/shared.py")
        path.unlink()
        path.write_text("original")
        with self.assertRaisesRegex(ValueError, "链接"):
            self.check()

    def test_main_checkout_rejected(self):
        clone = self.root.parent / "clone"
        self.run_git("clone", "--local", "--branch", "codex/sample", str(self.main), str(clone))
        with self.assertRaisesRegex(ValueError, "独立 worktree"):
            gate.package_scope(clone, self.base, self.task_path)

    def test_scope_failure_prevents_test_execution(self):
        self.write("src/other.py", "changed")
        with patch.object(gate.Path, "cwd", return_value=self.root), patch.object(
                gate.sys, "argv", ["gate", "package", "--base", self.base, "--task", self.task_path]), patch.object(
                gate, "package_tests") as runner:
            self.assertEqual(gate.main(), 1)
            runner.assert_not_called()

    def test_cli_propagates_test_failure(self):
        with patch.object(gate.Path, "cwd", return_value=self.root), patch.object(
                gate.sys, "argv", ["gate", "package", "--base", self.base, "--task", self.task_path]), patch.object(
                gate, "package_tests", return_value=1) as runner:
            self.assertEqual(gate.main(), 1)
            runner.assert_called_once_with(self.root, self.task["tests"])

    def test_targeted_runner_real_pass_fail_missing_and_empty(self):
        self.write("tests/__init__.py", "")
        self.write("tests/test_sample.py", "import unittest\nclass Sample(unittest.TestCase):\n def test_ok(self): self.assertTrue(True)\n def test_fail(self): self.fail('expected')\n")
        self.write("tests/empty.py", "")
        for target, expected in (("tests.test_sample.Sample.test_ok", 0),
                                 ("tests.test_sample.Sample.test_fail", 1),
                                 ("tests.missing", 1), ("tests.empty", 1)):
            with self.subTest(target=target):
                plan = dict(self.task["tests"], targets=[target])
                self.assertEqual(gate.package_tests(self.root, plan), expected)

    def test_full_mode_calls_existing_discovery(self):
        with patch.object(gate, "baseline", return_value=1) as runner:
            self.assertEqual(gate.package_tests(self.root, {"mode": "full"}), 1)
            runner.assert_called_once_with(self.root)

    def test_docs_only_does_not_run_python_tests(self):
        plan = {"mode": "docs", "targets": [], "reason": "spelling only"}
        gate.validate_test_plan(plan, ["README.md"])
        with patch.object(gate.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as runner:
            self.assertEqual(gate.package_tests(self.root, plan), 0)
            self.assertEqual(runner.call_args.args[0], ["git", "diff", "--check"])

    def test_critical_paths_require_full(self):
        for name in ("contracts/api.md", "src/dsh_retrieval/models.py", "src/dsh_retrieval/service.py",
                     "src/dsh_retrieval/adapters/task_storage/ledger.py", "pyproject.toml", "tools/check.py"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "完整回归"):
                gate.validate_test_plan(self.task["tests"], [name])

    def test_invalid_plans_rejected(self):
        for plan in ({"mode": "targeted", "targets": [], "reason": "empty"},
                     {"mode": "docs", "targets": [], "reason": "code disguised"},
                     {"mode": "targeted", "targets": ["-c"], "reason": "injection"},
                     {"mode": [], "targets": [], "reason": "bad mode"}):
            with self.subTest(plan=plan), self.assertRaises(ValueError):
                gate.validate_test_plan(plan, ["src/shared.py"])


if __name__ == "__main__":
    unittest.main()
