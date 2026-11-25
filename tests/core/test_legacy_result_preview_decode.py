from copy import deepcopy
import unittest

from dsh_retrieval.adapters.task_storage.sqlite import decode, encode
from dsh_retrieval.result_contracts import ExactResultSetPreview


class LegacyResultPreviewDecodeTest(unittest.TestCase):
    def test_missing_optional_historical_fields_preserves_old_digest_and_roundtrip(self):
        preview = ExactResultSetPreview('op', 1, 2, 'sha256:'+'a'*64, 1, (), (), 0, 0, 0, 1, 'partial')
        legacy = encode(preview)
        del legacy['value']['historical_reference_digest']
        del legacy['value']['historical_exclusions']
        decoded = decode(legacy)
        self.assertEqual(decoded, preview)
        self.assertEqual(decoded.result_set_digest, preview.result_set_digest)
        self.assertEqual(decode(encode(decoded)), preview)
        required_missing = deepcopy(legacy)
        del required_missing['value']['target_count']
        with self.assertRaises(ValueError):
            decode(required_missing)
        unknown = deepcopy(legacy)
        unknown['value']['unexpected_field'] = None
        with self.assertRaises(ValueError):
            decode(unknown)
