"""Application boundary for confirmed candidate manifests and cleaning rules.

The HTTP layer may select an already-published manifest and confirm a proposal,
but it cannot submit recipes, detector identifiers, artifact paths or results.
All executable material is rebuilt from the current task and the server-owned
released detector registry.
"""

from contextlib import nullcontext

from .adapters.cleaning.runner import MAX_CANDIDATE_BYTES
from .errors import ServiceError
from .integration_models import AccessContext
from .multi_rule_pipeline import prepare_released_multi_rule, run_approved_multi_rule
from .recipe_bridge import compile_confirmed_recipe
from .released_detectors import ReleasedDetectorRegistry


class ConfirmedCleaningService:
    def __init__(self, repository, *, registry=None, registry_provider=None, gateway,
                 require_read_projection=False):
        required = ("get_manifest", "prepare", "confirm", "step_results")
        if any(not callable(getattr(repository, name, None)) for name in required):
            raise TypeError("multi-rule cleaning repository required")
        if ((registry is None) == (registry_provider is None)
                or registry is not None and type(registry) is not ReleasedDetectorRegistry
                or registry_provider is not None
                and not callable(getattr(registry_provider, "build", None))):
            raise ValueError("one trusted released detector registry source required")
        if not callable(getattr(gateway, "open_read", None)):
            raise ValueError("authorized media gateway required")
        if registry is not None:
            registry.preflight()
        self.repository = repository
        self.require_read_projection = require_read_projection
        self.registry = registry
        self.registry_provider = registry_provider
        self.gateway = gateway

    def _scope(self, actor):
        provider = self.registry_provider
        scope = getattr(provider, "scope", None)
        return scope(actor) if callable(scope) else nullcontext()

    def _registry(self, actor, task, *, instructions=None):
        registry = (self.registry if self.registry is not None
                    else self.registry_provider.build(actor, task, instructions=instructions)
                    if instructions is not None else self.registry_provider.build(actor, task))
        if type(registry) is not ReleasedDetectorRegistry:
            raise ValueError("trusted released detector registry required")
        registry.preflight()
        return registry

    @staticmethod
    def _proposal_view(proposal):
        return {
            "kind": "cleaning_approval_proposal",
            "task_id": proposal.task_id,
            "revision": proposal.revision,
            "generation": proposal.generation,
            "expected_task_version": proposal.expected_task_version,
            "manifest_ref": proposal.manifest_ref,
            "candidate_count": len(proposal.candidates),
            "recipe_digest": proposal.recipe.recipe_digest,
            "proposal_digest": proposal.proposal_digest,
            "rules": tuple({
                "rule_id": rule.rule_id,
                "predicate_ref": rule.predicate_ref,
                "operator": rule.operator,
                "value": rule.value,
            } for rule in proposal.recipe.rules),
        }

    @staticmethod
    def _approval_view(approval):
        return {
            "kind": "approved_cleaning_operation",
            "task_id": approval.proposal.task_id,
            "revision": approval.proposal.revision,
            "generation": approval.proposal.generation,
            "task_version": approval.task_version,
            "operation_id": approval.operation_id,
            "manifest_ref": approval.proposal.manifest_ref,
            "candidate_count": len(approval.proposal.candidates),
            "recipe_digest": approval.proposal.recipe.recipe_digest,
            "status": "queued",
        }

    def prepare(self, actor, task_id, manifest_ref):
        # Resolve and authorize the immutable manifest before compiling.  The
        # repository rechecks it atomically when persisting the proposal.
        with self._scope(actor):
            manifest = self.repository.get_manifest(actor, task_id, manifest_ref)
            task = self.repository.tasks.get(actor, task_id)
            generate = getattr(self.registry_provider, 'generate', None)
            instructions = generate(task) if callable(generate) else None
            registry = self._registry(actor, task, instructions=instructions)
            recipe = compile_confirmed_recipe(task, registry)
            # Group checks run only for a candidate's origin groups; this figure
            # also reserves refill VLM budget, so it must not count skipped ones.
            estimated_calls = (
                sum(len(item.group_ids) for item in manifest.proposal.items)
                if any(rule.group_id is not None for rule in recipe.rules)
                else len(manifest.proposal.items) * len(recipe.steps))
            describe = getattr(self.registry_provider, "describe", None)
            execution = (describe(task, instructions=instructions) if instructions is not None
                         else describe(task)) if callable(describe) else None
            if execution and execution.get('model') and estimated_calls > 400:
                raise ServiceError('VLM_CALL_BUDGET',
                                   'This cleaning batch exceeds 400 VLM checks; reduce the candidate batch or split the request', 422)
            proposal = prepare_released_multi_rule(
                self.repository, actor, task_id, manifest_ref, recipe,
                registry=registry,
            )
        return {
            **self._proposal_view(proposal),
            "execution": execution,
            "detector_release_digest": registry.release_digest,
            "steps": tuple({"step_id": step.step_id,
                            "backend_ref": step.backend_ref,
                            "instruction": next((node['instruction'] for node in
                                (execution or {}).get('nodes', ())
                                if node['backend_ref'] == step.backend_ref), None),
                            "source": next((node['source'] for node in
                                (execution or {}).get('nodes', ())
                                if node['backend_ref'] == step.backend_ref), None)}
                           for step in proposal.recipe.steps),
            "unknown_policy": "hold_for_review",
            "estimated_vlm_calls": estimated_calls if execution and execution.get('model') else None,
            "confirmation_note": (
                "确认仅批准当前版本、候选清单与发布配方；模型配置或规则变化后"
                "旧批准不能执行。未知和失败不计入有效素材。"
            ),
        }

    def prepare_incremental_recompute(self, actor, task_id):
        with self._scope(actor):
            task = self.repository.tasks.get(actor, task_id)
            generate = getattr(self.registry_provider, 'generate', None)
            instructions = generate(task) if callable(generate) else None
            registry = self._registry(actor, task, instructions=instructions)
            recipe = compile_confirmed_recipe(task, registry)
            proposal = self.repository.prepare_incremental_recompute(
                actor, task_id, recipe,
                detector_release_digest=registry.release_digest)
            plan = self.repository.incremental_recompute(
                actor, task_id, recipe,
                detector_release_digest=registry.release_digest)
        if not plan.get('available'):
            plan = {'available': True, 'candidate_source': 'confirmed_candidate_manifest',
                    'reused_candidate_count': 0, 'revalidated_candidate_count': len(proposal.candidates)}
        describe = getattr(self.registry_provider, 'describe', None)
        execution = (describe(task, instructions=instructions) if callable(describe) else None)
        return {**self._proposal_view(proposal), 'incremental_recompute': plan,
                'execution': execution,
                'detector_release_digest': registry.release_digest,
                'steps': tuple({'step_id': step.step_id,
                                'backend_ref': step.backend_ref,
                                'instruction': (recipe.instructions[index]
                                                if recipe.instructions else None)}
                               for index, step in enumerate(recipe.steps)),
                'unknown_policy': 'hold_for_review',
                'confirmation_note': ('确认后使用历史原始候选重算；兼容事实只减少模型调用，'
                                      '结果仍重新绑定并发布到当前 revision。')}

    def confirm(self, actor, task_id, proposal_digest, *, key,
                expected_task_version, action):
        approval, replayed = self.repository.confirm(
            actor, task_id, proposal_digest, key=key,
            expected_task_version=expected_task_version, action=action,
        )
        return self._approval_view(approval), replayed

    def execute(self, actor, task_id, operation_id):
        with self._scope(actor):
            task = self.repository.tasks.get(actor, task_id)
            approval = self.repository.get_current(actor, task_id, operation_id)
            instructions = approval.proposal.recipe.instructions or None
            registry = self._registry(actor, task, instructions=instructions)
            describe = getattr(self.registry_provider, 'describe', None)
            execution = (describe(task, instructions=instructions) if instructions is not None
                         else describe(task)) if callable(describe) else None
            view, replayed = run_approved_multi_rule(
                self.repository, actor, task_id, operation_id,
                gateway=self.gateway, registry=registry, execution=execution,
            )
        return {**view, "kind": "cleaning_preview", "publication_kind": view["kind"]}, replayed

    def steps(self, actor, task_id):
        view = self.repository.step_results(actor, task_id)
        return self._steps_view(view)

    def steps_summary(self, actor, task_id):
        return self._steps_view(
            self.repository.step_results_summary(actor, task_id))

    def step_output_read_page(self, actor, task_id, **kwargs):
        return self.repository.step_output_read_page(actor, task_id, **kwargs)

    def progress_inputs(self, actor, task_id):
        result, steps = self.repository.progress_inputs(actor, task_id)
        return result, self._steps_view(steps)

    @staticmethod
    def _steps_view(view):
        # Historical execution must never be reconstructed from today's model
        # settings. Legacy runs explicitly have no saved execution snapshot.
        execution = next((run.get('execution') for run in view.get('runs', ())
                          if run.get('execution', {}).get('model')), None)
        return {**view, 'execution': execution,
                'execution_snapshot_available': execution is not None,
                'candidate_preview_available': any(
                    run.get('candidate_preview_available', False)
                    for run in view.get('runs', ()))}

    def incremental_recompute(self, actor, task_id):
        with self._scope(actor):
            task = self.repository.tasks.get(actor, task_id)
            instructions = (self.registry_provider.generate(task)
                            if callable(getattr(self.registry_provider, 'generate', None)) else None)
            registry = self._registry(actor, task, instructions=instructions)
            recipe = compile_confirmed_recipe(task, registry)
            view = self.repository.incremental_recompute(
                actor, task_id, recipe,
                detector_release_digest=registry.release_digest)
        execution = (self.registry_provider.describe(task, instructions=instructions)
                     if callable(getattr(self.registry_provider, 'describe', None)) else None)
        return {**view, 'execution': execution}

    def candidate_preview(self, actor, task_id, candidate_id):
        if not isinstance(candidate_id, str):
            raise ServiceError('PREVIEW_UNAVAILABLE', 'Cleaning candidate is unavailable', 404)
        direct = getattr(self.repository, 'get_current_preview_candidate', None)
        if callable(direct):
            try:
                candidate, media_ref = direct(actor, task_id, candidate_id)
                operation_id = None
            except ServiceError as error:
                if error.code != 'READ_PROJECTION_MISSING' or self.require_read_projection:
                    raise
                direct = None
        if not callable(direct):
            view = self.repository.step_results(actor, task_id)
            matches = [run['operation_id'] for run in view.get('runs', ())
                       if any(item['candidate_id'] == candidate_id
                              for step in run['steps'] for item in step['items'])]
            if not matches:
                raise ServiceError('PREVIEW_UNAVAILABLE', 'Cleaning candidate is unavailable', 404)
            operation_id = matches[0]
            candidate, media_ref = self.repository.get_published_candidate(
                actor, task_id, operation_id, candidate_id)
        context = AccessContext(actor, task_id, 'candidate_preview', 'current')
        thumbnail = getattr(self.gateway, 'thumbnail', None)
        if callable(thumbnail):
            content = thumbnail(context, media_ref, max_bytes=MAX_CANDIDATE_BYTES)
        else:
            content = bytearray()
            with self.gateway.open_read(context, media_ref,
                                        max_bytes=MAX_CANDIDATE_BYTES) as stream:
                while True:
                    block = stream.read(min(65536, MAX_CANDIDATE_BYTES - len(content) + 1))
                    if type(block) is not bytes:
                        raise ServiceError('MEDIA_READ_INVALID', 'Binary media required', 502)
                    if not block:
                        break
                    content.extend(block)
                    if len(content) > MAX_CANDIDATE_BYTES:
                        raise ServiceError('MEDIA_LIMIT_EXCEEDED',
                                           'Approved media exceeds worker limit', 413)
        current = (direct(actor, task_id, candidate_id) if callable(direct) else
                   self.repository.get_published_candidate(
                       actor, task_id, operation_id, candidate_id))
        if current != (candidate, media_ref):
            raise ServiceError('INPUT_SNAPSHOT_CHANGED',
                               'Cleaning candidate changed during preview', 409)
        content = bytes(content)
        content_type = ('image/jpeg' if content.startswith(b'\xff\xd8\xff')
                        else 'image/png' if content.startswith(b'\x89PNG\r\n\x1a\n')
                        else None)
        if content_type is None:
            raise ServiceError('PREVIEW_UNAVAILABLE', 'Cleaning candidate is not an image', 404)
        return content_type, content
