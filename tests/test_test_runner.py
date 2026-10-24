"""Black-box tests for the PYTHONPATH-independent test entry point."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tools" / "run_tests.py"


class TestRunnerTest(unittest.TestCase):
    def run_runner(self, *arguments: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            [sys.executable, str(RUNNER), *arguments],
            cwd=cwd or ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=30,
        )

    def test_module_class_and_method_target_from_external_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_runner(
                "tests.runner_fixtures.PassingTest.test_passes", "-v", cwd=Path(directory)
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("test_passes", result.stderr)

    def test_top_level_module_shorthand_adds_tests_package(self) -> None:
        result = self.run_runner("runner_fixtures.PassingTest")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("OK", result.stderr)

    def test_nested_package_shorthand_adds_tests_package(self) -> None:
        result = self.run_runner(
            "core.test_sqlite_storage.SQLiteTest.test_new_process_recovers_result_and_binding"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Ran 0 tests", result.stderr)

    def test_file_path_target_resolves_without_pythonpath(self) -> None:
        result = self.run_runner("tests/runner_fixtures.py")
        self.assertEqual(result.returncode, 1)
        self.assertIn("intentional runner fixture failure", result.stderr)
        self.assertIn("FAILED", result.stderr)

    def test_failure_is_propagated(self) -> None:
        result = self.run_runner("tests.runner_fixtures.FailingTest.test_fails")
        self.assertEqual(result.returncode, 1)
        self.assertIn("FAILED", result.stderr)

    def test_unknown_target_fails(self) -> None:
        result = self.run_runner("tests.does_not_exist")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ERROR", result.stderr)

    def test_repository_external_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            outside = Path(directory) / "test_outside.py"
            outside.write_text("pass\n")
            result = self.run_runner(str(outside))
        self.assertEqual(result.returncode, 2)
        self.assertIn("must be inside repository", result.stderr)

    def test_all_cannot_hide_an_explicit_target(self) -> None:
        result = self.run_runner("--all", "tests.runner_fixtures.PassingTest")
        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot be combined", result.stderr)


if __name__ == "__main__":
    unittest.main()
