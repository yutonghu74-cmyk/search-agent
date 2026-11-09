"""Stable, bounded pagination over immutable cleaning-step outputs."""

import base64
import hashlib
import json


VIEW_TYPES = {"input", "retained", "rejected", "review", "pending", "blocked"}


class StepOutputCursorError(ValueError):
    pass


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode()


def _cursor(binding, offset):
    payload = {**binding, "offset": offset}
    envelope = {"payload": payload,
                "checksum": hashlib.sha256(_canonical(payload)).hexdigest()}
    return base64.urlsafe_b64encode(_canonical(envelope)).decode().rstrip("=")


def _read_cursor(value, binding):
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        envelope = json.loads(raw)
        payload = envelope["payload"]
        checksum = envelope["checksum"]
        if (set(envelope) != {"payload", "checksum"}
                or checksum != hashlib.sha256(_canonical(payload)).hexdigest()
                or {key: payload.get(key) for key in binding} != binding
                or set(payload) != set(binding) | {"offset"}
                or type(payload["offset"]) is not int or payload["offset"] < 0):
            raise ValueError
        return payload["offset"]
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        raise StepOutputCursorError("step output cursor is invalid or stale") from None



def validate_step_filters(rule_id, query):
    for name, value, maximum in [('rule_id', rule_id, 200), ('query', query, 200)]:
        if value is not None and (not isinstance(value, str) or not 1 <= len(value) <= maximum):
            raise ValueError(name + ' is invalid')


def step_reason_matches(item, query):
    labels = {'MATCH': '符合条件', 'MISMATCH': '不符合条件',
              'FACT_UNAVAILABLE': '证据不足', 'DETECTOR_ERROR': '检测器异常',
              'DETECTOR_OUTPUT_INVALID': '检测结果格式无效'}
    for reason in item.get('reasons', ()):
        text = (str(reason.get('rule_id', '')) + '：'
                + labels.get(reason.get('reason_code'), str(reason.get('reason_code', '')))
                + '（' + str(reason.get('verdict', '')) + '）')
        if reason.get('reason_summary'):
            text += '：' + reason['reason_summary']
            if reason.get('reason_truncated'):
                text += '（摘要已截断）'
        elif reason.get('verdict') != 'pass':
            text += '；未保存具体原因摘要'
        if query in text:
            return True
    return False

def build_step_output_page(view, *, operation_id, step_execution_id,
                           output_version, view_type="input", reason_code=None,
                           limit=20, cursor=None, as_of_sequence=None, rule_id=None, query=None):
    validate_step_filters(rule_id, query)
    if not isinstance(view, dict) or not view.get("available"):
        raise ValueError("complete cleaning step output is unavailable")
    if view_type not in VIEW_TYPES:
        raise ValueError(
            "view_type must be input, retained, rejected, review, pending or blocked")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    if reason_code is not None and (not isinstance(reason_code, str)
                                    or not 1 <= len(reason_code) <= 100):
        raise ValueError("reason_code is invalid")
    if as_of_sequence is not None and (type(as_of_sequence) is not int
                                       or as_of_sequence < 0):
        raise ValueError("as_of_sequence is invalid")
    run = next((row for row in view.get("runs", ())
                if row.get("operation_id") == operation_id), None)
    if run is None or run.get("output_version") != output_version:
        raise StepOutputCursorError("cleaning output version is no longer current")
    step = next((row for row in run.get("steps", ())
                 if row.get("step_execution_id") == step_execution_id), None)
    if step is None:
        raise StepOutputCursorError("cleaning step execution was not found")
    rows = list(step.get("items", ()))
    counts = step.get("counts")
    count_keys = {"input", "retained", "rejected", "review", "pending", "blocked"}
    if (not isinstance(counts, dict) or set(counts) != count_keys
            or any(type(value) is not int or value < 0 for value in counts.values())
            or counts["input"] != sum(counts[key] for key in (
                "retained", "rejected", "review", "pending", "blocked"))
            or not isinstance(rows, list)
            or any(not isinstance(row, dict)
                   or row.get("status") not in {
                       "retained", "rejected", "review", "pending", "blocked"}
                   or not isinstance(row.get("candidate_id"), str)
                   or not isinstance(row.get("asset_version"), str)
                   or not isinstance(row.get("reasons"), (list, tuple)) for row in rows)
            or len({row["candidate_id"] for row in rows}) != len(rows)
            or len(rows) != counts["input"]
            or any(sum(row["status"] == status for row in rows) != counts[status]
                   for status in (
                       "retained", "rejected", "review", "pending", "blocked"))):
        raise ValueError("cleaning step counts or items are inconsistent")
    if view_type != "input":
        rows = [row for row in rows if row.get("status") == view_type]
    if reason_code is not None:
        rows = [row for row in rows if any(reason.get("reason_code") == reason_code
                                           for reason in row.get("reasons", ()))]
    if rule_id is not None:
        rows = [row for row in rows if any(reason.get('rule_id') == rule_id
                                           for reason in row.get('reasons', ()))]
    if query is not None:
        rows = [row for row in rows if step_reason_matches(row, query)]
    binding = {"operation_id": operation_id,
               "step_execution_id": step_execution_id,
               "output_version": output_version, "view_type": view_type,
               "reason_code": reason_code}
    if rule_id is not None:
        binding['rule_id'] = rule_id
    if query is not None:
        binding['query'] = query
    offset = 0 if cursor is None else _read_cursor(cursor, binding)
    if offset > len(rows):
        raise StepOutputCursorError("step output cursor is outside the fixed view")
    page = rows[offset:offset + limit]
    next_offset = offset + len(page)
    next_cursor = _cursor(binding, next_offset) if next_offset < len(rows) else None
    return {"schema_version": "cleaning-step-outputs@1",
            "task_id": view.get("task_id"), "revision": view.get("revision"),
            "generation": view.get("generation"), "operation_id": operation_id,
            "step_execution_id": step_execution_id, "step_id": step["step_id"],
            "input_version": step["input_version"], "output_version": output_version,
            "status": step["status"], "execution_mode": run.get("execution_mode"),
            "view_type": view_type, "reason_code": reason_code,
            "as_of_sequence": as_of_sequence, "counts": dict(counts),
            "total_items": len(rows), "items": [{**row,
                "preview_status": ("available" if run.get(
                    "candidate_preview_available",
                    view.get("candidate_preview_available"))
                                   else "unavailable")} for row in page],
            "next_cursor": next_cursor,
            "note": ("本步保留只表示通过当前步骤，不代表最终合格；"
                     "待复核与未处理分别计数。")}
