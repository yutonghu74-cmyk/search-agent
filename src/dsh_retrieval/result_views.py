"""Stable, read-only pages over an already published authoritative result set."""

from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from binascii import Error as Base64Error
from dataclasses import dataclass, is_dataclass
from hashlib import sha256
import json
import math


VIEWS = {"accepted", "reserve", "failed", "review", "user_excluded", "duplicate", "historical"}


class ResultCursorError(ValueError):
    """The client cursor is malformed or belongs to another immutable view."""


@dataclass(frozen=True, slots=True)
class ResultItem:
    candidate_id: str
    asset_version: str
    candidate_ids: tuple[str, ...] = ()
    sources: tuple[dict, ...] = ()

    def to_view(self, near_duplicate_matches=(), *, preview_available=False,
                assessment=None):
        return {"candidate_id": self.candidate_id, "asset_version": self.asset_version,
                "preview_status": "available" if preview_available else "unavailable",
                "duplicate_candidate_count": max(0, len(self.candidate_ids) - 1),
                "source_count": len(self.sources), "sources": list(self.sources),
                "near_duplicate_matches": list(near_duplicate_matches),
                "cleaning_status": (assessment or {}).get("cleaning_status", "pass"),
                "reasons": list((assessment or {}).get("reasons", ())),
                "disposition": (assessment or {}).get('disposition', 'accepted'),
                "elimination_stage": (assessment or {}).get('elimination_stage')}


def _positive(value, name):
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _binding(task, proposal):
    if not isinstance(task, dict) or not isinstance(proposal, dict):
        raise ValueError("task and proposal must be objects")
    revision = _positive(task.get("confirmed_revision"), "revision")
    task_version = _positive(task.get("task_version"), "task_version")
    generation = _positive(task.get("run_generation"), "generation")
    target = proposal.get("query_spec", {}).get("target_count")
    _positive(target, "target_count")
    task_id = task.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("task_id is required")
    return task_id, revision, task_version, generation, target


def _cursor_payload(result_digest, view_type, offset):
    return {"result_set_digest": result_digest, "view": view_type, "offset": offset}


def _encode_cursor(result_digest, view_type, offset):
    payload = _cursor_payload(result_digest, view_type, offset)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    envelope = {"payload": payload, "checksum": sha256(canonical).hexdigest()}
    encoded = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
    return urlsafe_b64encode(encoded).decode().rstrip("=")


def _decode_cursor(cursor, result_digest, view_type):
    if not isinstance(cursor, str) or not cursor or len(cursor) > 2048:
        raise ResultCursorError("cursor is invalid")
    try:
        raw = urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        envelope = json.loads(raw)
        if set(envelope) != {"payload", "checksum"} or not isinstance(envelope["payload"], dict):
            raise ValueError
        payload = envelope["payload"]
        if set(payload) != {"result_set_digest", "view", "offset"}:
            raise ValueError
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        if envelope["checksum"] != sha256(canonical).hexdigest():
            raise ValueError
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError, Base64Error):
        raise ResultCursorError("cursor is invalid") from None
    if payload["result_set_digest"] != result_digest or payload["view"] != view_type:
        raise ResultCursorError("cursor belongs to another result view")
    if type(payload["offset"]) is not int or payload["offset"] < 0:
        raise ResultCursorError("cursor offset is invalid")
    return payload["offset"]


def _item(value):
    if is_dataclass(value):
        candidate_id, asset_version = getattr(value, "candidate_id", None), getattr(value, "asset_version", None)
        candidate_ids, sources = getattr(value, "candidate_ids", ()), getattr(value, "sources", ())
    elif isinstance(value, dict):
        candidate_id, asset_version = value.get("candidate_id"), value.get("asset_version")
        candidate_ids, sources = value.get("candidate_ids", ()), value.get("sources", ())
    else:
        raise ValueError("result item is invalid")
    if not isinstance(candidate_id, str) or not candidate_id or not isinstance(asset_version, str) or not asset_version:
        raise ValueError("result item identity is invalid")
    candidate_ids = candidate_ids or (candidate_id,)
    if (not isinstance(candidate_ids, (tuple, list))
            or not candidate_ids or candidate_id not in candidate_ids
            or len(set(candidate_ids)) != len(candidate_ids)
            or any(not isinstance(item, str) or not item for item in candidate_ids)):
        raise ValueError("fused candidate identities are invalid")
    source_views = []
    if not isinstance(sources, (tuple, list)):
        raise ValueError("fused sources are invalid")
    for source in sources:
        get = (lambda key: getattr(source, key, None)) if is_dataclass(source) else (
              lambda key: source.get(key) if isinstance(source, dict) else None)
        row = {key: get(key) for key in (
            "candidate_id", "binding_id", "library_id", "index_version",
            "vector_id", "score")}
        if (row["candidate_id"] not in candidate_ids
                or any(not isinstance(row[key], str) or not row[key]
                       for key in ("candidate_id", "binding_id", "library_id", "index_version"))
                or type(row["vector_id"]) not in (str, int)
                or type(row["score"]) not in (int, float)
                or not math.isfinite(row["score"])):
            raise ValueError("fused source evidence is invalid")
        source_views.append(row)
    return ResultItem(candidate_id, asset_version, tuple(candidate_ids), tuple(source_views))


def _group_progress(proposal, result_set):
    """Per-group target/accepted/gap for display; a total alone can hide a shortfall."""
    grouped = proposal.get("query_spec", {}).get("grouped_expansion") or {}
    groups = [row for row in grouped.get("groups", []) if row.get("delivery_target") is not None]
    if not groups:
        return []
    if result_set is None:
        return [{"group_id": row["group_id"], "label": row["primary_label"],
                 "target_count": row["delivery_target"], "accepted_count": None,
                 "eligible_reserve_count": None, "gap": None} for row in groups]
    saved = {row.group_id: row for row in result_set.get("group_quotas", ())}
    # A quota task must never fall back to showing only the misleading total.
    if (set(saved) != {row["group_id"] for row in groups}
            or any(saved[row["group_id"]].target_count != row["delivery_target"] for row in groups)
            or sum(row.accepted_count for row in saved.values()) != result_set.get("accepted_count")
            or sum(row.gap for row in saved.values()) != result_set.get("gap")):
        raise ValueError("result set group quotas are inconsistent")
    return [{"group_id": row["group_id"], "label": row["primary_label"],
             "target_count": row["delivery_target"],
             "accepted_count": saved[row["group_id"]].accepted_count,
             "eligible_reserve_count": saved[row["group_id"]].eligible_reserve_count,
             "gap": saved[row["group_id"]].gap} for row in groups]


def build_result_page(task: dict, proposal: dict, result_set: dict | None, *,
                      view_type: str = "accepted", limit: int = 20,
                      cursor: str | None = None, _projection: bool = False) -> dict:
    """Return a frozen page without inventing results, previews, or authorization."""
    task_id, revision, task_version, generation, target = _binding(task, proposal)
    if view_type not in VIEWS:
        raise ValueError("invalid result view")
    if type(limit) is not int or limit < 1 or limit > (1_000_000 if _projection else 100):
        raise ValueError("limit must be between 1 and 100")
    base = {"schema_version": "task-results@1", "task_id": task_id,
            "revision": revision, "task_version": task_version,
            "run_generation": generation, "view": view_type,
            "target_count": target}
    group_progress = _group_progress(proposal, result_set)
    if group_progress:
        base["group_progress"] = group_progress
    if result_set is None:
        if cursor is not None:
            raise ResultCursorError("cursor cannot be used before results are published")
        return {**base, "available": False, "result_set_id": None,
                "result_set_digest": None, "counts": {
                    "eligible_unique": None, "accepted": None, "reserve": None,
                    "duplicate": None, "failed": None, "user_excluded": None,
                    "pending_review": None,
                    "near_duplicate_review": None, "gap": None},
                "counts_status": "unavailable", "stop_reason": None,
                "near_duplicate_review": {"pending_clusters": None,
                                          "decision_count": None},
                "items": [], "next_cursor": None,
                "note": "尚未发布绑定当前版本的权威结果集；不展示模拟素材或推算数量。"}
    if not isinstance(result_set, dict):
        raise ValueError("result set must be an object")
    result_set_id = result_set.get("result_set_id")
    result_digest = result_set.get("result_set_digest")
    if (not isinstance(result_set_id, str) or not result_set_id
            or not isinstance(result_digest, str) or not result_digest.startswith("sha256:")):
        raise ValueError("result set identity is invalid")
    if result_set.get("generation") != generation or result_set.get("as_of_task_version") != task_version:
        raise ValueError("result set is not bound to the current task version")
    if (result_set.get("target_count") != target or result_set.get("status") != "published"
            or result_set.get("counts_status") != "fresh"):
        raise ValueError("result set metadata is inconsistent")
    accepted = tuple(_item(value) for value in result_set.get("accepted", ()))
    reserve = tuple(_item(value) for value in result_set.get("reserve", ()))
    item_ids = {item.candidate_id for item in accepted + reserve}
    raw_pairs = result_set.get("near_duplicate_pairs", ())
    if not isinstance(raw_pairs, (tuple, list)):
        raise ValueError("near-duplicate review pairs are invalid")
    near_by_candidate = {candidate_id: [] for candidate_id in item_ids}
    normalized_pairs = []
    for pair in raw_pairs:
        get = (lambda key: getattr(pair, key, None)) if is_dataclass(pair) else (
              lambda key: pair.get(key) if isinstance(pair, dict) else None)
        row = {key: get(key) for key in ("candidate_id", "other_candidate_id",
               "hamming_distance", "review_threshold", "policy_ref")}
        if (row["candidate_id"] not in item_ids or row["other_candidate_id"] not in item_ids
                or row["candidate_id"] >= row["other_candidate_id"]
                or type(row["hamming_distance"]) is not int
                or type(row["review_threshold"]) is not int
                or not 0 <= row["hamming_distance"] <= row["review_threshold"] <= 16
                or row["policy_ref"] != "near-duplicate-review@1"):
            raise ValueError("near-duplicate review pair is invalid")
        normalized_pairs.append(row)
        for candidate_id, other_id in ((row["candidate_id"], row["other_candidate_id"]),
                                       (row["other_candidate_id"], row["candidate_id"])):
            near_by_candidate[candidate_id].append({
                "candidate_id": other_id,
                "hamming_distance": row["hamming_distance"],
                "review_threshold": row["review_threshold"],
                "policy_ref": row["policy_ref"]})
    expected = {
        "eligible_unique_count": len(accepted) + len(reserve),
        "accepted_count": len(accepted),
        "reserve_count": len(reserve),
    }
    if any(result_set.get(name) != value for name, value in expected.items()):
        raise ValueError("result set counts are inconsistent")
    numeric = ("duplicate_count", "failed_count", "pending_review_count", "gap")
    if any(type(result_set.get(name)) is not int or result_set[name] < 0 for name in numeric):
        raise ValueError("result set counts are invalid")
    user_excluded_count = result_set.get("user_excluded_count", 0)
    if type(user_excluded_count) is not int or user_excluded_count < 0:
        raise ValueError("result exclusion count is invalid")
    near_count = result_set.get("near_duplicate_review_count", len(normalized_pairs))
    if type(near_count) is not int or near_count != len(normalized_pairs):
        raise ValueError("near-duplicate review count is invalid")
    adjacency = {}
    for row in normalized_pairs:
        adjacency.setdefault(row["candidate_id"], set()).add(row["other_candidate_id"])
        adjacency.setdefault(row["other_candidate_id"], set()).add(row["candidate_id"])
    computed_clusters = 0
    unseen = set(adjacency)
    while unseen:
        computed_clusters += 1
        frontier = [next(iter(unseen))]
        visited = set()
        while frontier:
            candidate_id = frontier.pop()
            if candidate_id in visited:
                continue
            visited.add(candidate_id)
            frontier.extend(adjacency[candidate_id] - visited)
        unseen -= visited
    cluster_count = result_set.get("near_duplicate_cluster_count", computed_clusters)
    decisions = result_set.get("near_duplicate_decisions", ())
    if (type(cluster_count) is not int or cluster_count != computed_clusters
            or not isinstance(decisions, (tuple, list))):
        raise ValueError("near-duplicate review summary is invalid")
    audit_field = {"failed": "failed_items", "review": "review_items",
                   "user_excluded": "user_excluded_items",
                   "duplicate": "duplicate_items", 'historical': 'historical_items'}.get(view_type)
    if audit_field is None:
        rows = accepted if view_type == "accepted" else reserve
    else:
        rows = result_set.get(audit_field, ())
        if not isinstance(rows, (tuple, list)) or any(
                not isinstance(item, dict)
                or not isinstance(item.get("candidate_id"), str)
                or not isinstance(item.get("asset_version"), str)
                or item.get("cleaning_status") not in (
                    {'pass'} if view_type in {'duplicate', 'historical'} else {'fail', 'unknown', 'user_excluded'})
                or not isinstance(item.get("reasons"), (tuple, list))
                for item in rows):
            raise ValueError("result audit items are invalid")
        expected_count = {"failed": result_set["failed_count"],
                          "review": result_set["pending_review_count"],
                          "user_excluded": user_excluded_count,
                          'historical': result_set.get('historical_audit_count', 0),
                          "duplicate": result_set.get('duplicate_audit_count',
                                                       result_set['duplicate_count'])}[view_type]
        if len(rows) != expected_count:
            raise ValueError("result audit count is inconsistent")
    offset = 0 if cursor is None else _decode_cursor(cursor, result_digest, view_type)
    if offset > len(rows):
        raise ResultCursorError("cursor is outside the frozen result view")
    page = rows[offset:offset + limit]
    next_offset = offset + len(page)
    next_cursor = _encode_cursor(result_digest, view_type, next_offset) if next_offset < len(rows) else None
    return {**base, "available": True, "result_set_id": result_set_id,
            "publication_kind": result_set.get("kind", "preview"),
            "result_set_digest": result_digest, "counts": {
                **({'historical_duplicate': result_set['historical_exclusion_count']}
                   if 'historical_exclusion_count' in result_set else {}),
                "eligible_unique": expected["eligible_unique_count"],
                "accepted": expected["accepted_count"], "reserve": expected["reserve_count"],
                "duplicate": result_set["duplicate_count"], "failed": result_set["failed_count"],
                "user_excluded": user_excluded_count,
                "pending_review": result_set["pending_review_count"],
                "near_duplicate_review": near_count, "gap": result_set["gap"]},
            "counts_status": result_set.get("counts_status"),
            "stop_reason": result_set.get("stop_reason"),
            "near_duplicate_review": {"pending_clusters": cluster_count,
                                      "decision_count": len(decisions)},
            "items": ([{**item, "reasons": list(item.get("reasons", ())),
                         **({"user_exclusion": {**item["user_exclusion"],
                             "candidate_ids": list(item["user_exclusion"]["candidate_ids"])}}
                            if item.get("user_exclusion") else {})}
                       for item in page] if audit_field else [
                      item.to_view(tuple(sorted(near_by_candidate[item.candidate_id],
                          key=lambda match: (match["hamming_distance"], match["candidate_id"]))),
                          preview_available=bool(result_set.get("candidate_preview_available")),
                          assessment=result_set.get("candidate_assessments", {}).get(
                              item.candidate_id)) for item in page]),
            "next_cursor": next_cursor,
            "note": ("当前交付已发布，可在打包节点下载；访问仍受当前权限约束。" if result_set.get('kind') == 'delivery'
                     else "当前为合格预览，入选不代表正式交付；图片可在清洗详情查看，此列表仅显示逻辑标识和权威统计。")}
