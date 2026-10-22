"""Stable, read-only pages over an already published authoritative result set."""

from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from binascii import Error as Base64Error
from dataclasses import dataclass, is_dataclass
from hashlib import sha256
import json


VIEWS = {"accepted", "reserve"}


class ResultCursorError(ValueError):
    """The client cursor is malformed or belongs to another immutable view."""


@dataclass(frozen=True, slots=True)
class ResultItem:
    candidate_id: str
    asset_version: str

    def to_view(self):
        return {"candidate_id": self.candidate_id, "asset_version": self.asset_version,
                "preview_status": "unavailable"}


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
    elif isinstance(value, dict):
        candidate_id, asset_version = value.get("candidate_id"), value.get("asset_version")
    else:
        raise ValueError("result item is invalid")
    if not isinstance(candidate_id, str) or not candidate_id or not isinstance(asset_version, str) or not asset_version:
        raise ValueError("result item identity is invalid")
    return ResultItem(candidate_id, asset_version)


def build_result_page(task: dict, proposal: dict, result_set: dict | None, *,
                      view_type: str = "accepted", limit: int = 20,
                      cursor: str | None = None) -> dict:
    """Return a frozen page without inventing results, previews, or authorization."""
    task_id, revision, task_version, generation, target = _binding(task, proposal)
    if view_type not in VIEWS:
        raise ValueError("view must be accepted or reserve")
    if type(limit) is not int or limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100")
    base = {"schema_version": "task-results@1", "task_id": task_id,
            "revision": revision, "task_version": task_version,
            "run_generation": generation, "view": view_type,
            "target_count": target}
    if result_set is None:
        if cursor is not None:
            raise ResultCursorError("cursor cannot be used before results are published")
        return {**base, "available": False, "result_set_id": None,
                "result_set_digest": None, "counts": {
                    "eligible_unique": None, "accepted": None, "reserve": None,
                    "duplicate": None, "failed": None, "pending_review": None,
                    "gap": None}, "counts_status": "unavailable", "stop_reason": None,
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
    rows = accepted if view_type == "accepted" else reserve
    offset = 0 if cursor is None else _decode_cursor(cursor, result_digest, view_type)
    if offset > len(rows):
        raise ResultCursorError("cursor is outside the frozen result view")
    page = rows[offset:offset + limit]
    next_offset = offset + len(page)
    next_cursor = _encode_cursor(result_digest, view_type, next_offset) if next_offset < len(rows) else None
    return {**base, "available": True, "result_set_id": result_set_id,
            "result_set_digest": result_digest, "counts": {
                "eligible_unique": expected["eligible_unique_count"],
                "accepted": expected["accepted_count"], "reserve": expected["reserve_count"],
                "duplicate": result_set["duplicate_count"], "failed": result_set["failed_count"],
                "pending_review": result_set["pending_review_count"], "gap": result_set["gap"]},
            "counts_status": result_set.get("counts_status"),
            "stop_reason": result_set.get("stop_reason"),
            "items": [item.to_view() for item in page], "next_cursor": next_cursor,
            "note": "素材预览尚未接入授权媒体网关；这里只显示逻辑标识和权威统计。"}
