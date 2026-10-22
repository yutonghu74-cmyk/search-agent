from pathlib import Path
import subprocess
import sys
import unittest


class ManifestSmokeEntryTest(unittest.TestCase):
    def invoke(self, *args):
        script = Path(__file__).resolve().parents[1] / "tools/manifest_cleaning_smoke.py"
        return subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True)

    def test_real_execution_must_be_explicit(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 2)
        self.assertIn("--run-real", result.stderr)

    def test_missing_runtime_not_silently_simulated_or_installed(self):
        result = self.invoke("--run-real", "--dj-python", "/nonexistent/synthetic-runtime",
                             "--dj-source", "/nonexistent/synthetic-source")
        self.assertEqual(result.returncode, 2)
        self.assertIn("no automatic installation", result.stderr)
