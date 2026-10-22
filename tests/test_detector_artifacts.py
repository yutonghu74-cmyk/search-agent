from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tests import test_multi_rule_pipeline as pipeline_fixture
from dsh_retrieval.detector_artifacts import (
    DetectorArtifact, DetectorArtifactBinding, DetectorArtifactManifest,
)
from dsh_retrieval.errors import ServiceError


class DetectorArtifactTests(unittest.TestCase):
    def setUp(self):
        self.f = pipeline_fixture.MultiRulePipelineTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def assert_unreleased(self, action):
        with self.assertRaises(ServiceError) as caught:
            action()
        self.assertEqual(caught.exception.code, "DETECTOR_NOT_RELEASED")

    def verify(self):
        return self.f.registry.require_recipe(self.f.approval.job.recipe)

    def test_implementation_and_model_artifacts_verify(self):
        self.assertEqual(self.verify(), {self.f.detector.backend_ref: self.f.detector})

    def test_manifest_identity_is_ordered_canonical_and_root_independent(self):
        binding = replace(self.f.artifact_binding,
                          artifacts=tuple(reversed(self.f.artifact_binding.artifacts)))
        reordered = DetectorArtifactManifest(self.f.artifact_root, (binding,))
        self.assertEqual(reordered.manifest_digest,
                         self.f.artifact_manifest.manifest_digest)
        self.assertEqual(binding.binding_digest, self.f.artifact_binding.binding_digest)
        self.assertEqual((reordered.detector_count, reordered.artifact_count), (1, 2))

        other_temp = TemporaryDirectory()
        self.addCleanup(other_temp.cleanup)
        other_root = Path(other_temp.name)
        for source in (self.f.artifact_path, self.f.model_path):
            (other_root / source.name).write_bytes(source.read_bytes())
        moved = DetectorArtifactManifest(other_root, (binding,))
        self.assertEqual(moved.manifest_digest, reordered.manifest_digest)

        alternate = other_root / "alternate-model.bin"
        alternate.write_bytes(self.f.model_path.read_bytes())
        changed_artifact = replace(binding.artifacts[0], relative_path=alternate.name)
        changed = replace(binding, artifacts=(changed_artifact, binding.artifacts[1]))
        self.assertNotEqual(DetectorArtifactManifest(other_root, (changed,)).manifest_digest,
                            reordered.manifest_digest)

    def test_missing_or_changed_artifact_fails_before_media_read(self):
        for mutate in (lambda: self.f.model_path.unlink(),
                       lambda: self.f.model_path.write_bytes(b"tampered")):
            with self.subTest(mutate=mutate):
                mutate()
                self.assert_unreleased(self.f.run_pipeline)
                self.assertEqual(self.f.gateway.reads, 0)
                self.f.model_path.write_bytes(b"synthetic model weights")

    def test_runtime_detector_must_declare_exact_binding(self):
        self.f.detector.artifact_binding_digest = "sha256:" + "0" * 64
        self.assert_unreleased(self.f.run_pipeline)
        self.assertEqual(self.f.gateway.reads, 0)

    def test_deployment_directory_replacement_is_rejected(self):
        original = self.f.artifact_root.with_name(self.f.artifact_root.name + "-moved")
        self.f.artifact_root.rename(original)
        self.f.artifact_root.mkdir()
        def restore_root():
            if original.exists():
                if self.f.artifact_root.exists():
                    for child in self.f.artifact_root.iterdir():
                        child.unlink()
                    self.f.artifact_root.rmdir()
                original.rename(self.f.artifact_root)
        self.addCleanup(restore_root)
        (self.f.artifact_root / self.f.artifact_path.name).write_bytes(
            b"synthetic detector implementation")
        (self.f.artifact_root / self.f.model_path.name).write_bytes(
            b"synthetic model weights")
        self.assert_unreleased(self.f.run_pipeline)
        self.assertEqual(self.f.gateway.reads, 0)

    def test_manifest_rejects_unsafe_paths_and_symlinked_files(self):
        digest_value = "sha256:" + sha256(b"synthetic model weights").hexdigest()
        with self.assertRaises(ValueError):
            DetectorArtifact("model", "../model.bin", digest_value)
        link = self.f.artifact_root / "linked-model.bin"
        link.symlink_to(self.f.model_path)
        artifact = DetectorArtifact("model", link.name, digest_value)
        binding = replace(self.f.artifact_binding,
                          artifacts=(self.f.artifact_binding.artifacts[0], artifact))
        self.f.detector.artifact_binding_digest = binding.binding_digest
        manifest = DetectorArtifactManifest(self.f.artifact_root, (binding,))
        registry = type(self.f.registry)(self.f.capabilities, self.f.bindings,
            {self.f.detector.backend_ref: self.f.detector}, manifest)
        self.assert_unreleased(lambda: registry.require_recipe(self.f.approval.job.recipe))


if __name__ == "__main__":
    unittest.main()
