from dataclasses import replace
import unittest

from tests import test_multi_rule_pipeline as pipeline_fixture
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.multi_rule_contracts import DetectionStep
from dsh_retrieval.released_detectors import ReleasedDetectorRegistry


class ReleasedDetectorTests(unittest.TestCase):
    def setUp(self):
        self.f = pipeline_fixture.MultiRulePipelineTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def assert_unreleased(self, registry, recipe):
        with self.assertRaises(ServiceError) as caught:
            registry.require_recipe(recipe)
        self.assertEqual(caught.exception.code, "DETECTOR_NOT_RELEASED")

    def test_verified_catalog_and_step_match(self):
        self.assertEqual(self.f.registry.require_recipe(self.f.approval.job.recipe),
                         {self.f.detector.backend_ref: self.f.detector})

    def test_changed_catalog_digest_rejected_before_media_read(self):
        changed = ReleasedDetectorRegistry(self.f.capabilities[:1], self.f.bindings,
                                           {self.f.detector.backend_ref: self.f.detector})
        self.assert_unreleased(changed, self.f.approval.job.recipe)
        self.f.registry = changed
        self.f.assert_code("DETECTOR_NOT_RELEASED", self.f.run_pipeline)
        self.assertEqual(self.f.gateway.reads, 0)
        self.assertEqual(self.f.f.state()["results"], {})

    def test_revoked_release_blocks_unpublished_saved_result(self):
        self.f.repo.publish_result(self.f.actor, self.f.task_id,
                                   self.f.operation_id, self.f.make_result())
        self.f.registry = ReleasedDetectorRegistry(self.f.capabilities[:1], self.f.bindings,
            {self.f.detector.backend_ref: self.f.detector})
        self.f.assert_code("DETECTOR_NOT_RELEASED", self.f.run_pipeline)
        self.assertEqual(self.f.f.state()["result_sets"], {})

    def test_manual_or_unavailable_capability_cannot_run(self):
        for status in ("manual", "unavailable"):
            capabilities = (replace(self.f.capabilities[0], status=status),
                            self.f.capabilities[1])
            registry = ReleasedDetectorRegistry(capabilities, self.f.bindings,
                {self.f.detector.backend_ref: self.f.detector})
            recipe = replace(self.f.approval.job.recipe,
                             registry_digest=registry.registry_digest)
            self.assert_unreleased(registry, recipe)

    def test_wrong_value_type_or_operator_is_not_a_released_rule(self):
        recipe = self.f.approval.job.recipe
        wrong_type = replace(recipe, rules=(replace(recipe.rules[0], value=1), recipe.rules[1]))
        self.assert_unreleased(self.f.registry, wrong_type)
        changed_caps = (replace(self.f.capabilities[0], operators=("in",)),
                        self.f.capabilities[1])
        registry = ReleasedDetectorRegistry(changed_caps, self.f.bindings,
            {self.f.detector.backend_ref: self.f.detector})
        self.assert_unreleased(registry, replace(recipe, registry_digest=registry.registry_digest))

    def test_wrong_backend_binding_or_runtime_detector_fails(self):
        recipe = self.f.approval.job.recipe
        step = replace(recipe.steps[0], backend_ref="other.detector")
        self.assert_unreleased(self.f.registry, replace(recipe, steps=(step,)))
        self.f.detector.validator_ref = "changed.validator"
        self.assert_unreleased(self.f.registry, recipe)

    def test_unused_step_cannot_read_media_without_a_rule(self):
        recipe = self.f.approval.job.recipe
        extra = DetectionStep("unused", "fixture.detector", "fixture.validator", "fixture.eval")
        self.assert_unreleased(self.f.registry, replace(recipe, steps=recipe.steps + (extra,)))


if __name__ == "__main__":
    unittest.main()
