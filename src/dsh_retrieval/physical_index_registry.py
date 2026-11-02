"""Private physical binding declarations, not connection health or authorization."""
from dataclasses import asdict, dataclass, fields
import json
from pathlib import Path
import re
from types import MappingProxyType

from .errors import ServiceError
from .integration_models import AccessContext, IndexBinding
from .models import Actor
from .query_spec import digest


def _invalid():
    raise ServiceError('PHYSICAL_INDEX_CONFIG_INVALID', 'Invalid physical index configuration', 422)


def _identifier(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.@/-]{1,128}', value) is not None


@dataclass(frozen=True, slots=True)
class PhysicalIndex:
    binding: IndexBinding
    connection_ref: str
    database: str
    collection: str
    primary_key_field: str
    vector_field: str
    model_id: str
    model_version: str
    preprocessing_version: str
    filter_template_ref: str
    pagination_mode: str
    read_mode: str
    max_batch_size: int
    max_depth: int

    @property
    def config_digest(self):
        return digest({'schema': 'physical-index@1', **asdict(self)})

    @property
    def vector_space(self):
        return (self.model_id, self.model_version, self.preprocessing_version,
                self.binding.vector_dimension, self.binding.metric)

    def validate(self):
        if type(self.binding) is not IndexBinding:
            _invalid()
        if not all(_identifier(value) for value in (
                self.binding.binding_id, self.binding.library_id, self.binding.index_version,
                self.binding.encoder_ref, self.connection_ref, self.database, self.collection,
                self.primary_key_field, self.vector_field, self.model_id, self.model_version,
                self.preprocessing_version, self.filter_template_ref)):
            _invalid()
        if (type(self.binding.vector_dimension) is not int or not 1 <= self.binding.vector_dimension <= 65536
                or self.binding.metric not in ('COSINE', 'IP', 'L2')
                or self.pagination_mode not in ('single_topk', 'offset', 'iterator', 'snapshot_cursor')
                or self.read_mode not in ('snapshot', 'stable_cursor', 'live')
                or (self.pagination_mode == 'snapshot_cursor' and self.read_mode != 'snapshot')
                or (self.read_mode == 'stable_cursor' and self.pagination_mode not in ('iterator', 'offset'))
                or type(self.max_batch_size) is not int or not 1 <= self.max_batch_size <= 100
                or type(self.max_depth) is not int or not self.max_batch_size <= self.max_depth <= 1_000_000
                or self.primary_key_field == self.vector_field):
            _invalid()


class PhysicalIndexRegistry:
    def __init__(self, entries):
        if type(entries) is not tuple or not 1 <= len(entries) <= 256:
            _invalid()
        by_id, encoders, spaces = {}, {}, {}
        for item in entries:
            if type(item) is not PhysicalIndex:
                _invalid()
            item.validate()
            binding = item.binding
            if binding.binding_id in by_id:
                _invalid()
            # A logical encoder reference cannot silently acquire another model.
            if binding.encoder_ref in encoders and encoders[binding.encoder_ref] != item.vector_space:
                _invalid()
            encoders[binding.encoder_ref] = item.vector_space
            physical = (item.connection_ref, item.database, item.collection,
                        item.vector_field, binding.index_version)
            layout = (item.vector_space, item.primary_key_field)
            if physical in spaces and spaces[physical] != layout:
                _invalid()
            spaces[physical] = layout
            by_id[binding.binding_id] = item
        self._entries = MappingProxyType(by_id)

    @property
    def bindings(self):
        return tuple(self._entries[key].binding for key in sorted(self._entries))

    @property
    def config_digest(self):
        return digest({'schema': 'physical-index-registry@1',
                       'entries': [asdict(self._entries[key]) for key in sorted(self._entries)]})

    def resolve(self, binding, *, expected_config_digest=None):
        if type(binding) is not IndexBinding or not isinstance(binding.binding_id, str):
            _invalid()
        target = self._entries.get(binding.binding_id)
        if (target is None or target.binding != binding
                or any(type(getattr(binding, f.name)) is not type(getattr(target.binding, f.name))
                       for f in fields(IndexBinding))):
            raise ServiceError('INDEX_BINDING_MISMATCH', 'Exact registered binding required', 409)
        if expected_config_digest is not None and target.config_digest != expected_config_digest:
            raise ServiceError('PHYSICAL_INDEX_CHANGED', 'Physical target configuration changed', 409)
        return target

    def vector_location_key(self, binding, vector_id, *, expected_config_digest=None):
        """Opaque mapping namespace; not a dedup key or an access grant."""
        target = self.resolve(binding, expected_config_digest=expected_config_digest)
        if not (type(vector_id) is int and -(2 ** 63) <= vector_id < 2 ** 63
                or type(vector_id) is str and 0 < len(vector_id) <= 1024):
            _invalid()
        return digest({'target_digest': target.config_digest,
                       'id_type': type(vector_id).__name__, 'vector_id': vector_id})

    def query_cache_key(self, context, text, binding):
        """Only key construction. Caller must still authorize every cache hit."""
        target = self.resolve(binding)
        if (type(context) is not AccessContext or type(context.actor) is not Actor
                or context.purpose != 'search'
                or not all(isinstance(v, str) and 0 < len(v.strip()) <= 256 for v in
                    (context.actor.tenant_id, context.actor.user_id, context.task_id, context.authorization_version))
                or not isinstance(text, str) or not 1 <= len(text.strip()) <= 4000):
            _invalid()
        return digest({'target_digest': target.config_digest, 'scope': asdict(context), 'query': text})


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _invalid()
        result[key] = value
    return result


def load_physical_indexes(path):
    """Bounded private JSON; no endpoints, tokens, expressions or SDK defaults."""
    try:
        with Path(path).open('rb') as stream:
            raw = stream.read(1_000_001)
        if len(raw) > 1_000_000:
            _invalid()
        value = json.loads(raw, object_pairs_hook=_unique_object)
        if (not isinstance(value, dict) or set(value) != {'schema', 'entries'}
                or value['schema'] != 'physical-index-registry@1'
                or not isinstance(value['entries'], list)):
            _invalid()
        entries = []
        for item in value['entries']:
            if not isinstance(item, dict) or set(item) != {f.name for f in fields(PhysicalIndex)}:
                _invalid()
            entries.append(PhysicalIndex(**{**item, 'binding': IndexBinding(**item['binding'])}))
        return PhysicalIndexRegistry(tuple(entries))
    except (OSError, ValueError, TypeError, KeyError):
        _invalid()
