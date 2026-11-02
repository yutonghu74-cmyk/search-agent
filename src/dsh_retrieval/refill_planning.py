"""Deterministic refill gate; it never dispatches a search or grants access.

Callers must supply only server-verified, already approved search positions. A
position fingerprint includes the physical index version, query, parameters
and cursor/depth, so replay cannot count the same page as a new iteration.
"""

from dataclasses import dataclass
from datetime import datetime
import json
from typing import Any

from .query_spec import canonical_json, digest


DEFAULT_REFILL_LIMITS = {
    "max_rounds": None,
    "max_vlm_calls": None,
    "no_progress_rounds": 2,
}


def normalize_refill_limits(value=None):
    """Validate task-confirmed automatic execution limits.

    Background mode intentionally has no universal round/call ceiling.  A
    task may freeze either ceiling; the LOCAL_CLIP strategy always freezes a
    bounded no-progress window.
    """
    if value is None:
        return dict(DEFAULT_REFILL_LIMITS)
    if (not isinstance(value, dict) or set(value) != set(DEFAULT_REFILL_LIMITS)):
        raise ValueError("invalid refill execution limits")
    result = dict(value)
    for key in ("max_rounds", "max_vlm_calls"):
        if result[key] is not None and (type(result[key]) is not int or result[key] < 1):
            raise ValueError("invalid refill execution limit")
    if type(result["no_progress_rounds"]) is not int or result["no_progress_rounds"] < 1:
        raise ValueError("invalid no-progress window")
    return result


@dataclass(frozen=True, slots=True)
class RefillPosition:
    library_id: str
    fingerprint: str
    binding_json: str | None = None


@dataclass(frozen=True, slots=True)
class RefillDecision:
    action: str
    stop_reason: str | None
    gap: int
    next_position: RefillPosition | None = None


@dataclass(frozen=True, slots=True)
class RefillReservation:
    reservation_id: str
    task_id: str
    revision: int
    generation: int
    task_version: int
    iteration: int
    library_id: str
    position_fingerprint: str
    policy_digest: str
    created_at: datetime
    position_binding_json: str | None = None


def build_refill_position(*, library_id: str, index_version: str, query: str,
                          parameters: dict[str, Any], cursor: str | None = None,
                          depth: int | None = None) -> RefillPosition:
    """Build the durable identity of one approved search position.

    Cursor and depth are explicit because repeating the same query at another
    position is a new batch, while replaying the same position is not.
    """
    if (not isinstance(library_id, str) or not library_id.strip()
            or not isinstance(index_version, str) or not index_version.strip()
            or not isinstance(query, str) or not query.strip()
            or not isinstance(parameters, dict)
            or (cursor is not None and (not isinstance(cursor, str) or not cursor))
            or (depth is not None and (type(depth) is not int or depth < 0))
            or (cursor is None) == (depth is None)):
        raise ValueError("invalid refill position binding")
    binding = {
        "library_id": library_id,
        "index_version": index_version,
        "query": query,
        "parameters": parameters,
        "position": {"cursor": cursor, "depth": depth},
    }
    try:
        binding_json = canonical_json(binding).decode("utf-8")
        fingerprint = digest(binding)
    except (TypeError, ValueError):
        raise ValueError("refill parameters must be canonical JSON") from None
    return RefillPosition(library_id, fingerprint, binding_json)


def parse_refill_position(position: RefillPosition) -> dict[str, Any]:
    """Validate and decode the immutable dispatch payload behind a position."""
    if type(position) is not RefillPosition or not isinstance(position.binding_json, str):
        raise ValueError("refill position has no dispatch binding")
    try:
        binding = json.loads(position.binding_json)
        canonical = canonical_json(binding).decode("utf-8")
    except (TypeError, ValueError):
        raise ValueError("invalid refill position binding") from None
    if (canonical != position.binding_json or not isinstance(binding, dict)
            or set(binding) != {"library_id", "index_version", "query", "parameters", "position"}
            or binding["library_id"] != position.library_id
            or digest(binding) != position.fingerprint):
        raise ValueError("refill position binding does not match fingerprint")
    return binding


def decide_refill(*, target_count: int, accepted_count: int, policy: dict,
                  node_mode: str, positions: tuple[RefillPosition, ...] = (),
                  issued_fingerprints: frozenset[str] = frozenset(),
                  remaining_batches: int | None = None,
                  stagnant_rounds: int = 0,
                  no_progress_limit: int | None = None) -> RefillDecision:
    """Choose one approved next position or explain why execution must stop.

    This is a planning primitive, not authorization or a complete coordinator.
    The caller must persist the issued fingerprint atomically before dispatch.
    """
    if (type(target_count) is not int or target_count < 1
            or type(accepted_count) is not int or accepted_count < 0
            or accepted_count > target_count or not isinstance(policy, dict)
            or set(policy) != {"mode", "target_count", "approved_library_ids", "progression"}
            or type(policy["target_count"]) is not int or policy["target_count"] != target_count
            or policy["mode"] not in {"input_only", "maintain_target"}
            or policy["progression"] not in {"manual", "auto"}
            or node_mode not in {"manual", "auto"}):
        raise ValueError("invalid confirmed refill binding")
    libraries = policy["approved_library_ids"]
    if (type(libraries) is not list or any(not isinstance(x, str) or not x for x in libraries)
            or len(libraries) != len(set(libraries))
            or (policy["mode"] == "input_only" and libraries)
            or (policy["mode"] == "input_only" and positions)
            or (policy["mode"] == "maintain_target" and not libraries)
            or type(positions) is not tuple
            or any(type(p) is not RefillPosition or not p.library_id or not p.fingerprint
                   for p in positions)
            or len({p.fingerprint for p in positions}) != len(positions)
            or type(issued_fingerprints) is not frozenset
            or any(not isinstance(x, str) or not x for x in issued_fingerprints)
            or (remaining_batches is not None
                and (type(remaining_batches) is not int or remaining_batches < 0))
            or type(stagnant_rounds) is not int or stagnant_rounds < 0
            or (no_progress_limit is not None
                and (type(no_progress_limit) is not int or no_progress_limit < 1))):
        raise ValueError("invalid refill evidence or limits")
    gap = target_count - accepted_count
    if gap == 0:
        return RefillDecision("finalize", "TARGET_MET", 0)
    if policy["mode"] == "input_only":
        return RefillDecision("partial_delivery", "INPUT_SCOPE_COMPLETE", gap)

    # A caller passing an out-of-scope position indicates a plan/authority bug.
    if any(p.library_id not in libraries for p in positions):
        raise ValueError("refill position exceeds approved library scope")
    if no_progress_limit is not None and stagnant_rounds >= no_progress_limit:
        return RefillDecision("partial_delivery", "NO_PROGRESS", gap)
    if remaining_batches == 0:
        return RefillDecision("partial_delivery", "EXECUTION_LIMIT_REACHED", gap)
    remaining = tuple(p for p in positions if p.fingerprint not in issued_fingerprints)
    if not remaining:
        return RefillDecision("partial_delivery", "SEARCH_STRATEGIES_EXHAUSTED", gap)
    if policy["progression"] == "manual" or node_mode == "manual":
        return RefillDecision("await_owner", "NEED_USER_INPUT", gap, remaining[0])
    return RefillDecision("search_more", None, gap, remaining[0])
