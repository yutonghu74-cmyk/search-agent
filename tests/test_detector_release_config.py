from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tests import test_multi_rule_pipeline as pipeline_fixture
from dsh_retrieval.detector_release_config import (
    MAX_RELEASE_CONFIG_BYTES, load_released_detector_registry,
)
from dsh_retrieval.errors import ServiceError


class DetectorReleaseConfigTests(unittest.TestCase):
    def setUp(self):
        self.f = pipeline_fixture.MultiRulePipelineTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.config_temp = TemporaryDirectory()
        self.addCleanup(self.config_temp.cleanup)
        self.config_path = Path(self.config_temp.name) / "detectors.json"
        binding = self.f.artifact_binding
        self.config = {
            "schema_version": "detector-artifact-manifest@1",
            "detectors": [{
                "backend_ref": binding.backend_ref,
                "validator_ref": binding.validator_ref,
                "evidence_ref": binding.evidence_ref,
                "artifacts": [{
                    "role": artifact.role,
                    "relative_path": artifact.relative_path,
                    "content_digest": artifact.content_digest,
                } for artifact in binding.artifacts],
            }],
        }

    def write_config(self, value=None):
        self.config_path.write_text(json.dumps(self.config if value is None else value),
                                    encoding="utf-8")

    def load(self, **changes):
        args = dict(config_path=self.config_path, deployment_root=self.f.artifact_root,
                    capabilities=self.f.capabilities, bindings=self.f.bindings,
                    detectors={self.f.detector.backend_ref: self.f.detector})
        args.update(changes)
        return load_released_detector_registry(**args)

    def assert_invalid(self, value):
        self.write_config(value)
        with self.assertRaises(ValueError):
            self.load()

    def assert_unreleased(self, action):
        with self.assertRaises(ServiceError) as caught:
            action()
        self.assertEqual(caught.exception.code, "DETECTOR_NOT_RELEASED")

    def test_valid_server_config_preflights_complete_release(self):
        self.write_config()
        registry = self.load()
        self.assertEqual(registry.require_recipe(self.f.approval.job.recipe),
                         {self.f.detector.backend_ref: self.f.detector})

    def test_schema_unknown_fields_and_untrusted_root_are_rejected(self):
        cases = []
        wrong_schema = deepcopy(self.config)
        wrong_schema["schema_version"] = "other@1"
        cases.append(wrong_schema)
        unknown = {**self.config, "artifact_root": "/client/path"}
        cases.append(unknown)
        empty = {**self.config, "detectors": []}
        cases.append(empty)
        artifact_unknown = deepcopy(self.config)
        artifact_unknown["detectors"][0]["artifacts"][0]["url"] = "https://example.invalid"
        cases.append(artifact_unknown)
        for value in cases:
            with self.subTest(value=value):
                self.assert_invalid(value)

    def test_duplicate_keys_paths_artifacts_and_digests_are_rejected(self):
        self.config_path.write_text(
            '{"schema_version":"detector-artifact-manifest@1",'
            '"schema_version":"detector-artifact-manifest@1","detectors":[]}',
            encoding="utf-8")
        with self.assertRaises(ValueError):
            self.load()
        for field, value in (("relative_path", "/absolute/model.bin"),
                             ("relative_path", "../model.bin"),
                             ("content_digest", "sha256:not-a-digest")):
            changed = deepcopy(self.config)
            changed["detectors"][0]["artifacts"][0][field] = value
            with self.subTest(field=field, value=value):
                self.assert_invalid(changed)
        duplicate = deepcopy(self.config)
        duplicate["detectors"][0]["artifacts"].append(
            deepcopy(duplicate["detectors"][0]["artifacts"][0]))
        self.assert_invalid(duplicate)
        duplicate_backend = deepcopy(self.config)
        duplicate_backend["detectors"].append(deepcopy(duplicate_backend["detectors"][0]))
        self.assert_invalid(duplicate_backend)

    def test_config_must_be_small_regular_and_not_a_symlink(self):
        with self.assertRaises(ValueError):
            self.load(config_path=Path("relative-detectors.json"))
        target = Path(self.config_temp.name) / "target.json"
        target.write_text(json.dumps(self.config), encoding="utf-8")
        self.config_path.symlink_to(target)
        with self.assertRaises(ValueError):
            self.load()
        self.config_path.unlink()
        self.config_path.mkdir()
        with self.assertRaises(ValueError):
            self.load()
        self.config_path.rmdir()
        self.config_path.write_bytes(b" " * (MAX_RELEASE_CONFIG_BYTES + 1))
        with self.assertRaises(ValueError):
            self.load()

    def test_startup_rejects_missing_extra_or_unbound_runtime_detector(self):
        self.write_config()
        self.assert_unreleased(lambda: self.load(detectors={}))
        extra = {self.f.detector.backend_ref: self.f.detector, "extra.detector": object()}
        self.assert_unreleased(lambda: self.load(detectors=extra))
        self.assert_unreleased(lambda: self.load(bindings=()))

    def test_startup_rejects_runtime_binding_and_artifact_drift(self):
        self.write_config()
        original_digest = self.f.detector.artifact_binding_digest
        self.f.detector.artifact_binding_digest = "sha256:" + "0" * 64
        self.assert_unreleased(self.load)
        self.f.detector.artifact_binding_digest = original_digest
        self.f.model_path.unlink()
        self.assert_unreleased(self.load)

    def test_execution_rechecks_artifacts_after_successful_startup(self):
        self.write_config()
        self.f.registry = self.load()
        self.f.model_path.write_bytes(b"changed after startup")
        self.assert_unreleased(self.f.run_pipeline)
        self.assertEqual(self.f.gateway.reads, 0)


if __name__ == "__main__":
    unittest.main()
