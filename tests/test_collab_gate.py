import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


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


if __name__ == "__main__":
    unittest.main()
