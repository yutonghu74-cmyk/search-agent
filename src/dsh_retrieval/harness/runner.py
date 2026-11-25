"""Application entry point: explicit per-role routes, no vendor branches."""
import threading

from ..errors import ServiceError
from .contracts import HarnessInvocation, RuntimeRelease
from .tools import ToolBroker, PlanningReceipts


def published_releases(routes, releases=None):
    """Installed publications available to backend project routing, never imports."""
    values = dict(releases) if releases is not None else {r.release_ref: r for r in routes.values()}
    if (any(type(value) is not RuntimeRelease or ref != value.release_ref for ref, value in values.items())
            or any(values.get(value.release_ref) != value for value in routes.values())):
        from ..errors import invalid
        raise invalid('runtime publication registry does not match default routes')
    from types import MappingProxyType
    return MappingProxyType(values)


class HarnessRunner:
    def __init__(self, registry, routes, roles, catalog, *, guard, model_binding, releases=None):
        self.registry, self.routes, self.roles = registry, dict(routes), dict(roles)
        self.releases = published_releases(self.routes, releases)
        self.catalog, self.guard, self.model_binding = catalog, guard, model_binding
        self._lock, self._runs, self._brokers = threading.RLock(), {}, {}
        self.receipts = PlanningReceipts()

    def start(self, stage, *, run_id, attempt_id, model_profile_ref, task_kind, deadline, context_json):
        role = self.roles.get(stage.role)
        release = self.routes.get(stage.role)
        if role is None or release is None:
            raise ServiceError('HARNESS_ROUTE_UNAVAILABLE', 'No explicit runtime route for role', 503)
        with self._lock:
            key = (run_id, attempt_id)
            previous = self._runs.get(key)
            if previous is not None:
                role, release = previous[0].role, previous[0].runtime
            invocation = HarnessInvocation(stage, run_id, attempt_id, role, release,
                                           model_profile_ref, task_kind, deadline, context_json)
            if previous is not None:
                if previous[0].invocation_digest != invocation.invocation_digest:
                    raise ServiceError('IDEMPOTENCY_CONFLICT', 'Attempt already bound to a runtime', 409)
                self.guard(previous[0])
                return previous[2]
            self.guard(invocation)
            if self.model_binding(model_profile_ref) != stage.model_binding_digest:
                raise ServiceError('HARNESS_MODEL_BINDING_CONFLICT', 'Model profile differs from approved binding', 409)
            adapter = self.registry.resolve(release)
            broker = ToolBroker(invocation, self.catalog, guard=self.guard, receipts=self.receipts)
            handle = adapter.start(invocation, broker)
            self._runs[key] = (invocation, adapter, handle)
            self._brokers[key] = broker
            return handle

    def _get(self, handle):
        with self._lock:
            found = self._runs.get((handle.run_id, handle.attempt_id))
            if found is None or found[2] != handle:
                raise ServiceError('HARNESS_HANDLE_NOT_FOUND', 'Attempt unavailable', 404)
            return found

    def poll(self, handle, after_cursor=0):
        invocation, adapter, _ = self._get(handle)
        self.guard(invocation)
        return adapter.poll(handle, after_cursor)

    def outputs(self, handle):
        invocation, adapter, _ = self._get(handle)
        self.guard(invocation)
        cursor, last = 0, None
        while True:
            events = adapter.poll(handle, cursor)
            if not events: break
            last = events[-1]; cursor = last.cursor
        if last is None or last.kind != 'finished':
            raise ServiceError('HARNESS_OUTPUT_NOT_READY', 'Attempt has no completed validated output', 409)
        return self._brokers[(handle.run_id, handle.attempt_id)].outputs()

    def cancel(self, handle):
        invocation, adapter, _ = self._get(handle)
        self.guard(invocation)
        return adapter.cancel(handle)

    def close(self, handle):
        _, adapter, _ = self._get(handle)
        adapter.close(handle)


def load_role_routes(config, releases, *, roles=None):
    """JSON config selects explicit installed releases, never imports modules."""
    expected = {'retrieval', 'cleaning'} if roles is None else set(roles)
    if (not expected or type(config) is not dict or set(config) != expected
            or any(not isinstance(ref, str) or ref not in releases for ref in config.values())):
        from ..errors import invalid
        raise invalid('all registered role routes must reference runtime releases')
    routes = {role: releases[ref] for role, ref in config.items()}
    if any(release.release_ref != config[role] for role, release in routes.items()):
        from ..errors import invalid
        raise invalid('runtime release registry identity mismatch')
    return routes
