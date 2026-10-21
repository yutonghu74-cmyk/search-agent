import unittest

from dsh_retrieval.errors import ServiceError
from dsh_retrieval.query_spec import digest, normalize_query_spec


def valid_spec():
    return {
        "schema_version": "query-spec@1",
        "raw_request": "找室内家居图，不要水印",
        "media_type": "image",
        "target_count": 10,
        "business_domain": "home_lifestyle",
        "rules": [
            {
                "rule_id": "no_watermark",
                "predicate_ref": "watermark.present@1",
                "operator": "eq",
                "value": False,
                "hardness": "hard",
                "origin": "user_explicit",
            }
        ],
    }


class QuerySpecTest(unittest.TestCase):
    def test_negation_is_preserved_and_defaults_are_explicit(self):
        normalized = normalize_query_spec(valid_spec())
        self.assertIs(normalized["rules"][0]["value"], False)
        self.assertEqual(normalized["count_unit"], "image")
        self.assertEqual(normalized["unknown_policy"], "hold_for_review")

    def test_digest_is_stable_across_object_key_order(self):
        first = normalize_query_spec(valid_spec())
        reversed_keys = dict(reversed(list(valid_spec().items())))
        second = normalize_query_spec(reversed_keys)
        self.assertEqual(digest(first), digest(second))

    def test_unknown_field_is_rejected(self):
        spec = valid_spec()
        spec["tenant_id"] = "model-must-not-control-this"
        with self.assertRaises(ServiceError) as raised:
            normalize_query_spec(spec)
        self.assertEqual(raised.exception.code, "INVALID_SPEC")
        self.assertEqual(raised.exception.field_path, "$.tenant_id")

    def test_unregistered_rule_is_rejected(self):
        spec = valid_spec()
        spec["rules"][0]["predicate_ref"] = "arbitrary.code@1"
        with self.assertRaises(ServiceError) as raised:
            normalize_query_spec(spec)
        self.assertEqual(raised.exception.code, "UNSUPPORTED_RULE")

    def test_quota_is_explicitly_rejected_in_p1(self):
        spec = valid_spec()
        spec["quotas"] = [{"dimension": "source", "target": 2}]
        with self.assertRaises(ServiceError) as raised:
            normalize_query_spec(spec, stage="P1")
        self.assertEqual(raised.exception.code, "QUOTA_NOT_SUPPORTED")

    def test_video_is_not_silently_accepted_in_p1(self):
        spec = valid_spec()
        spec["media_type"] = "video"
        spec["count_unit"] = "clip"
        with self.assertRaises(ServiceError) as raised:
            normalize_query_spec(spec, stage="P1")
        self.assertEqual(raised.exception.code, "FEATURE_NOT_SUPPORTED")


if __name__ == "__main__":
    unittest.main()
