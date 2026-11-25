"""Deployment-owned video readiness and image/video registry dispatch.

All callbacks come from the trusted integration factory. Browser/model fields
cannot enable a deployment capability or provide an executable backend.
"""
from copy import deepcopy
from contextlib import nullcontext

from .errors import ServiceError
from .integration_models import IndexBinding
from .recipe_bridge import compile_confirmed_recipe
from .released_detectors import ReleasedDetectorRegistry
from .video_query import normalize_video_fields


class VideoExecutionPolicy:
    def __init__(self, projects, *, libraries, capabilities, whole_video_bindings,
                 preflight, registry, inspector):
        self.projects = projects
        self.libraries, self.capabilities = libraries, capabilities
        self.whole_video_bindings = whole_video_bindings
        self.preflight, self.registry, self.inspector = preflight, registry, inspector

    def require(self, actor, project_id, spec):
        self.projects.require_access(actor, project_id, 'default', 'approve')
        if spec is not None:
            normalize_video_fields(spec)
        callbacks = (self.libraries, self.capabilities, self.whole_video_bindings,
                     self.preflight, self.registry, self.inspector)
        if not all(callable(item) for item in callbacks):
            raise ServiceError('VIDEO_EXECUTION_UNAVAILABLE', 'Video integration capabilities are incomplete', 422)
        libraries = tuple(self.libraries(actor, project_id))
        caps = tuple(self.capabilities())
        verified = tuple(self.whole_video_bindings())
        if any(type(binding) is not IndexBinding for binding in verified):
            raise ServiceError('VIDEO_SOURCE_UNVERIFIED', 'Invalid whole-video source registration', 503)
        available = {b for lib in libraries if lib.media_type == 'video' for b in lib.bindings}
        available &= {c.binding for c in caps if c.media_type == 'video'} & set(verified)
        if not available:
            raise ServiceError('VIDEO_SOURCE_UNVERIFIED', 'No authorized verified whole-video text index', 422)
        if spec and spec['refill_policy']['mode'] == 'maintain_target':
            permitted = set(spec['refill_policy']['approved_library_ids'])
            if not permitted <= {b.library_id for b in available}:
                raise ServiceError('VIDEO_SOURCE_UNVERIFIED', 'Refill libraries lack verified video capability', 422)
            available = {b for b in available if b.library_id in permitted}
        if spec:
            limit = spec.get('refill_limits', {}).get('max_vlm_calls')
            minimum_sources = len(available) if spec['refill_policy']['mode'] == 'maintain_target' else 1
            if limit is not None and limit < minimum_sources * len(spec['video_conditions']['clauses']):
                raise ServiceError('VIDEO_CALL_BUDGET', 'Budget cannot cover one verified candidate per selected index', 422)
        # Fail closed: this server callback must probe its actual query/decoder/
        # evaluator protocols and raise on missing or unverified dependencies.
        try:
            evidence = self.preflight(actor, project_id, deepcopy(spec))
        except ServiceError:
            raise
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise ServiceError('VIDEO_EXECUTION_UNAVAILABLE', 'Video preflight protocol is unavailable', 503) from exc
        if not isinstance(evidence, dict) or evidence.get('ready') is not True:
            raise ServiceError('VIDEO_EXECUTION_UNAVAILABLE', 'Video integration preflight did not pass', 422)
        return {'available': True, 'count_unit': 'distinct_video',
                'source_count': len({b.library_id for b in available}),
                'source_library_ids': sorted({b.library_id for b in available}), 'reason_code': None}

    def describe(self, actor, project_id, spec=None):
        self.projects.require_access(actor, project_id, 'default', 'read')
        try:
            return self.require(actor, project_id, spec)
        except ServiceError as error:
            if error.status in (401, 404):
                raise
            return {'available': False, 'reason_code': error.code,
                    'message': '视频能力尚未就绪，需求可保存为草稿。'}


class MediaRegistryProvider:
    def __init__(self, image_provider, video_registry=None):
        self.images, self.video_registry = image_provider, video_registry

    def scope(self, actor):
        return self.images.scope(actor) if callable(getattr(self.images, 'scope', None)) else nullcontext()

    def generate(self, task):
        return None if task.media_type == 'video' else self.images.generate(task)

    def build(self, actor, task, *, instructions=None):
        if task.media_type != 'video':
            return self.images.build(actor, task, instructions=instructions)
        if not callable(self.video_registry):
            raise ServiceError('VIDEO_EXECUTION_UNAVAILABLE', 'Video evaluator registry is not configured', 422)
        registry = self.video_registry(actor, task)
        if type(registry) is not ReleasedDetectorRegistry:
            raise ServiceError('DETECTOR_NOT_RELEASED', 'Trusted video detector registry required', 409)
        registry.preflight()
        compile_confirmed_recipe(task, registry)
        return registry

    def describe(self, task, *, instructions=None):
        if task.media_type != 'video':
            return self.images.describe(task, instructions=instructions)
        import json
        clauses = json.loads(task.confirmed_proposal_json)['query_spec']['video_conditions']['clauses']
        return {'media_type': 'video', 'backend_ref': 'video.conditions@1',
                'estimated_calls_per_candidate': len(clauses),
                'nodes': [{'node_id': 'video-content', 'backend_ref': 'video.conditions@1',
                           'source': '；'.join(c['predicate'] for c in clauses),
                           'instruction': '按已确认的全部视频条件及 all/any 关系验证，证据不足返回 unknown。'}],
                'conditions': clauses}
