from dataclasses import asdict, replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from dsh_retrieval.errors import ServiceError
from dsh_retrieval.integration_models import AccessContext, IndexBinding
from dsh_retrieval.models import Actor
from dsh_retrieval.physical_index_registry import PhysicalIndex, PhysicalIndexRegistry, load_physical_indexes


class PhysicalIndexRegistryTest(unittest.TestCase):
    def setUp(self):
        self.binding = IndexBinding('dogs-v1', 'dogs', 'v1', 'encoder-a@1', 512, 'COSINE')
        self.target = PhysicalIndex(self.binding, 'connection-a', 'media', 'dogs', 'id', 'vector',
            'model-a', 'v1', 'resize-normalize@1', 'authorized-dogs@1', 'iterator', 'live', 20, 1000)
        self.registry = PhysicalIndexRegistry((self.target,))
        self.context = AccessContext(Actor('tenant', 'owner'), 'task', 'search', 'auth@1')

    def test_exact_resolve_and_immutable_target(self):
        self.assertEqual(self.registry.resolve(self.binding), self.target)
        with self.assertRaises(AttributeError):
            self.target.collection = 'other'

    def test_same_dimension_different_encoder_and_version_rejected(self):
        for binding in (replace(self.binding, encoder_ref='encoder-b@1'),
                        replace(self.binding, index_version='v2'), replace(self.binding, library_id='cats')):
            with self.assertRaises(ServiceError):
                self.registry.resolve(binding)

    def test_same_encoder_reference_cannot_point_to_two_models(self):
        other = replace(self.target, binding=replace(self.binding, binding_id='cats-v1', library_id='cats'),
                        collection='cats', model_id='model-b')
        with self.assertRaises(ServiceError):
            PhysicalIndexRegistry((self.target, other))

    def test_same_physical_vector_space_cannot_mix_models_or_primary_keys(self):
        other = replace(self.target, binding=replace(self.binding, binding_id='other', encoder_ref='encoder-b'),
                        model_id='model-b')
        with self.assertRaises(ServiceError):
            PhysicalIndexRegistry((self.target, other))
        other = replace(self.target, binding=replace(self.binding, binding_id='other'), primary_key_field='another_id')
        with self.assertRaises(ServiceError):
            PhysicalIndexRegistry((self.target, other))

    def test_two_business_scopes_may_share_physical_collection(self):
        other = replace(self.target, binding=replace(self.binding, binding_id='other', library_id='other'),
                        filter_template_ref='another-scope')
        registry = PhysicalIndexRegistry((self.target, other))
        self.assertEqual(registry.resolve(other.binding), other)
        self.assertNotEqual(registry.vector_location_key(self.binding, 1), registry.vector_location_key(other.binding, 1))

    def test_changed_physical_target_requires_new_digest(self):
        changed = PhysicalIndexRegistry((replace(self.target, collection='dogs_rebuilt'),))
        with self.assertRaises(ServiceError):
            changed.resolve(self.binding, expected_config_digest=self.target.config_digest)

    def test_cross_collection_and_version_and_id_type_are_isolated(self):
        keys = {self.registry.vector_location_key(self.binding, 1), self.registry.vector_location_key(self.binding, '1')}
        for target in (replace(self.target, collection='cats'),
                       replace(self.target, connection_ref='another-cluster'),
                       replace(self.target, binding=replace(self.binding, index_version='v2'))):
            keys.add(PhysicalIndexRegistry((target,)).vector_location_key(target.binding, 1))
        self.assertEqual(len(keys), 5)

    def test_bad_ids_and_bool_dimension_rejected(self):
        for value in (True, None, 1.0, '', 2**63, 'x' * 1025):
            with self.assertRaises(ServiceError):
                self.registry.vector_location_key(self.binding, value)
        one = replace(self.target, binding=replace(self.binding, vector_dimension=1))
        with self.assertRaises(ServiceError):
            PhysicalIndexRegistry((one,)).resolve(replace(one.binding, vector_dimension=True))

    def test_cache_key_binds_authorization_query_and_preprocessing(self):
        key = self.registry.query_cache_key(self.context, '狗', self.binding)
        for context in (replace(self.context, actor=Actor('other', 'owner')),
                        replace(self.context, actor=Actor('tenant', 'other')),
                        replace(self.context, task_id='another'), replace(self.context, authorization_version='auth@2')):
            self.assertNotEqual(key, self.registry.query_cache_key(context, '狗', self.binding))
        self.assertNotEqual(key, self.registry.query_cache_key(self.context, '猫', self.binding))
        changed = PhysicalIndexRegistry((replace(self.target, preprocessing_version='v2'),))
        self.assertNotEqual(key, changed.query_cache_key(self.context, '狗', self.binding))

    def test_invalid_config_bounds_and_read_modes(self):
        for change in ({'connection_ref': 'https://private.invalid'}, {'max_batch_size': True},
                       {'max_depth': 1}, {'primary_key_field': 'vector'}, {'model_version': ''},
                       {'pagination_mode': 'snapshot_cursor', 'read_mode': 'live'},
                       {'pagination_mode': 'single_topk', 'read_mode': 'stable_cursor'}):
            with self.subTest(change=change), self.assertRaises(ServiceError):
                PhysicalIndexRegistry((replace(self.target, **change),))

    def test_order_independent_registry_digest_and_duplicate_rejection(self):
        other = replace(self.target, binding=replace(self.binding, binding_id='other'), collection='other')
        self.assertEqual(PhysicalIndexRegistry((self.target, other)).config_digest,
                         PhysicalIndexRegistry((other, self.target)).config_digest)
        with self.assertRaises(ServiceError):
            PhysicalIndexRegistry((self.target, self.target))

    def test_strict_json_loader_and_sanitized_errors(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'private.json'
            data = {'schema': 'physical-index-registry@1', 'entries': [asdict(self.target)]}
            path.write_text(json.dumps(data))
            self.assertEqual(load_physical_indexes(path).config_digest, self.registry.config_digest)
            for raw in ('{"schema":"one","schema":"two","entries":[]}',
                        json.dumps({**data, 'api_key': 'fixture-sensitive'}), 'x' * 1_000_001, 'null'):
                path.write_text(raw)
                with self.assertRaises(ServiceError) as caught:
                    load_physical_indexes(path)
                self.assertNotIn('fixture-sensitive', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
