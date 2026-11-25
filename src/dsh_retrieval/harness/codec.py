"""Explicit application-envelope codec, independent of any vendor session format."""
from dataclasses import asdict
import json

from ..agent_contracts import AgentStageTask
from ..retrieval_contracts import RetrievalScope
from .contracts import HarnessInvocation, RoleDefinition, RuntimeRelease
from ..errors import ServiceError, invalid
from ..processing_requirements import identifier


class StageCodecRegistry:
    """Explicit installed domain codecs; serialized input cannot select imports."""
    def __init__(self): self._codecs = {}

    def register(self, contract_ref, stage_type, *, encode, decode):
        if (not identifier(contract_ref) or contract_ref in self._codecs
                or not isinstance(stage_type, type) or not callable(encode) or not callable(decode)
                or any(item[0] is stage_type for item in self._codecs.values())):
            raise invalid('duplicate or invalid installed stage codec')
        self._codecs[contract_ref] = (stage_type, encode, decode)

    def encode(self, stage):
        matches = [(ref, item) for ref, item in self._codecs.items() if type(stage) is item[0]]
        if len(matches) != 1:
            raise ServiceError('HARNESS_STAGE_CODEC_UNAVAILABLE', 'Business stage codec is not installed', 503)
        ref, (_, encode, _) = matches[0]
        value = encode(stage)
        if type(value) is not dict: raise invalid('stage codec must return an object')
        return ref, value

    def decode(self, contract_ref, value):
        if not identifier(contract_ref) or contract_ref not in self._codecs:
            raise ServiceError('HARNESS_STAGE_CODEC_UNAVAILABLE', 'Business stage codec is not installed', 503)
        stage_type, _, decode = self._codecs[contract_ref]
        if type(value) is not dict: raise invalid('stage payload must be an object')
        stage = decode(value)
        if type(stage) is not stage_type: raise invalid('stage decoder returned a different domain type')
        return stage


def stage_from(value):
    value = dict(value)
    value['scope'] = RetrievalScope(**value['scope'])
    return AgentStageTask(**value)


def dump_invocation(invocation, *, stage_codecs=None):
    # Keep legacy bytes/identities unchanged; new domains opt into a versioned
    # envelope and an explicitly installed codec. No RetrievalScope conversion.
    value = {name: getattr(invocation, name) for name in invocation.__dataclass_fields__ if name != 'stage'}
    value['role'], value['runtime'] = asdict(invocation.role), asdict(invocation.runtime)
    if type(invocation.stage) is AgentStageTask:
        value['stage'] = asdict(invocation.stage)
    else:
        if stage_codecs is None:
            raise ServiceError('HARNESS_STAGE_CODEC_UNAVAILABLE', 'Install the business-domain stage codec', 503)
        value['stage_contract_ref'], value['stage'] = stage_codecs.encode(invocation.stage)
        value['schema_version'] = 'harness-invocation@2'
    value['runtime']['required_capabilities'] = sorted(value['runtime']['required_capabilities'])
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def load_invocation(payload, *, stage_codecs=None):
    value = json.loads(payload)
    if 'schema_version' in value:
        if value.pop('schema_version') != 'harness-invocation@2' or stage_codecs is None:
            raise ServiceError('HARNESS_STAGE_CODEC_UNAVAILABLE', 'Unsupported business-domain envelope or codec', 503)
        value['stage'] = stage_codecs.decode(value.pop('stage_contract_ref'), value['stage'])
    else:
        value['stage'] = stage_from(value['stage'])
    value['role']['tool_ids'] = tuple(value['role']['tool_ids'])
    value['role'] = RoleDefinition(**value['role'])
    value['runtime']['required_capabilities'] = frozenset(value['runtime']['required_capabilities'])
    value['runtime'] = RuntimeRelease(**value['runtime'])
    return HarnessInvocation(**value)
