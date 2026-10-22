from copy import deepcopy
import unittest

from dsh_retrieval.errors import ServiceError
from dsh_retrieval.expansion_models import normalize_grouped_expansion, preview_grouped_expansion
from dsh_retrieval.proposals import normalize_proposal
from draft_helpers import BODY


class GroupedExpansionTest(unittest.TestCase):
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
            preview_grouped_expansion("动物，狗扩31个词")
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
