"""Explicit grouped keyword preview; auxiliary terms never define delivery units."""

from __future__ import annotations

import re

from .query_spec import digest


MAX_GROUPS = 20
MAX_EXPANSIONS = 30
MODIFIERS = (
    "特写", "全身", "正面", "侧面", "背面", "近景", "中景", "远景", "单体", "群体",
    "静态", "动态", "室内", "户外", "自然光", "棚拍", "纯色背景", "生活场景", "自然环境", "城市环境",
    "白天", "夜间", "横图", "竖图", "高角度", "低角度", "高清", "写实", "主体完整", "无遮挡",
)
GROUP_PATTERN = re.compile(
    r"^(?P<label>[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9_-]{0,31})\s*"
    r"(?:扩|扩展)\s*(?P<count>\d{1,2})\s*个?(?:词|关键词)$"
)


def _term(label: str, modifier: str) -> str:
    separator = " " if label.isascii() else ""
    return label + separator + modifier


def _group_id(parent_label: str | None, primary_label: str) -> str:
    return "group_" + digest({"parent_label": parent_label, "primary_label": primary_label})[7:23]


def _plan_payload(parent_label, groups, generator="local-template@1"):
    return {
        "schema_version": "grouped-expansion@1",
        "parent_label": parent_label,
        "groups": groups,
        "overlap_policy": "single_primary_per_aggregate",
        "aggregate_manifest": True,
        "generator": generator,
        "requires_confirmation": True,
    }


def preview_grouped_expansion(raw_request: str) -> dict:
    """Recognize only explicit `主标签扩N个词`; do not guess user taxonomy."""
    if not isinstance(raw_request, str) or not raw_request.strip() or len(raw_request) > 2_000:
        raise ValueError("raw_request must be 1-2000 characters")
    segments = [part.strip() for part in re.split(r"[，,；;\n]+", raw_request.strip()) if part.strip()]
    matches = []
    first_match = None
    for index, segment in enumerate(segments):
        match = GROUP_PATTERN.fullmatch(segment)
        if match:
            first_match = index if first_match is None else first_match
            matches.append((match.group("label"), int(match.group("count"))))
    if not matches:
        return {"recognized": False, "plan": None,
                "note": "未识别明确的“主标签扩N个词”分组；系统不会猜测主标签。"}
    if len(matches) > MAX_GROUPS:
        raise ValueError("too many label groups")
    parent_label = None
    if first_match:
        candidate = segments[first_match - 1]
        candidate = re.sub(r"^(?:主题|分类|上级标签)[:：]?\s*", "", candidate).strip()
        if 0 < len(candidate) <= 32 and not any(char.isdigit() for char in candidate):
            parent_label = candidate
    seen = set()
    groups = []
    for primary_label, count in matches:
        if primary_label in seen:
            raise ValueError("primary labels must be unique")
        if count < 1 or count > MAX_EXPANSIONS:
            raise ValueError("expansion count must be between 1 and 30")
        seen.add(primary_label)
        groups.append({
            "group_id": _group_id(parent_label, primary_label),
            "primary_label": primary_label,
            "requested_expansion_count": count,
            "auxiliary_terms": [_term(primary_label, modifier) for modifier in MODIFIERS[:count]],
            "package_name": primary_label,
            "package_mode": "separate",
            "delivery_target": None,
        })
    payload = _plan_payload(parent_label, groups)
    plan = {**payload, "plan_digest": digest(payload)}
    return {"recognized": True, "plan": plan,
            "note": "主标签决定独立交付包；辅助词仅用于召回，需确认后才能固定到正式版本。"}


def preview_edited_groups(parent_label, groups):
    """Build a reviewed proposal from explicit user input, never inferred labels."""
    if parent_label is not None and not isinstance(parent_label, str):
        raise ValueError("parent label must be text")
    parent_label = parent_label.strip() or None if parent_label is not None else None
    if not isinstance(groups, list) or not 1 <= len(groups) <= MAX_GROUPS:
        raise ValueError("provide 1-20 groups")
    rows = []
    for group in groups:
        if not isinstance(group, dict) or set(group) != {"primary_label", "auxiliary_terms"}:
            raise ValueError("each group requires primary_label and auxiliary_terms")
        label, terms = group["primary_label"], group["auxiliary_terms"]
        if not isinstance(label, str) or not isinstance(terms, list):
            raise ValueError("invalid group")
        if any(not isinstance(term, str) for term in terms):
            raise ValueError("terms must be text")
        label, terms = label.strip(), [term.strip() for term in terms]
        rows.append({"group_id": _group_id(parent_label, label), "primary_label": label,
                     "requested_expansion_count": len(terms), "auxiliary_terms": terms,
                     "package_name": label, "package_mode": "separate", "delivery_target": None})
    payload = _plan_payload(parent_label, rows, "user-edited@1")
    return {"recognized": True,
            "plan": normalize_grouped_expansion({**payload, "plan_digest": digest(payload)}),
            "note": "用户填写的召回词已保存为待确认方案；查询命中不代表素材标签已验证。"}


def normalize_grouped_expansion(value: dict) -> dict:
    """Validate a preview before it becomes part of an immutable query revision."""
    if not isinstance(value, dict):
        raise ValueError("grouped expansion must be an object")
    expected = set(_plan_payload(None, [])) | {"plan_digest"}
    binding = value.get('model_binding')
    if 'model_binding' in value:
        expected.add('model_binding')
        if (not isinstance(binding, dict) or set(binding) != {'llm', 'vlm', 'config_digest', 'prompt_version'}
                or any(not isinstance(v, str) or not v or len(v) > 100 for v in binding.values())
                or not re.fullmatch(r'sha256:[0-9a-f]{64}', binding['config_digest'])):
            raise ValueError('invalid model binding')
    if set(value) != expected:
        raise ValueError("grouped expansion fields are invalid")
    if value["schema_version"] != "grouped-expansion@1":
        raise ValueError("unsupported grouped expansion version")
    generator = value["generator"]
    if generator not in ("local-template@1", "user-edited@1", "llm@1") or (generator == 'llm@1' and binding is None):
        raise ValueError("unsupported generator")
    parent = value["parent_label"]
    if parent is not None and (not isinstance(parent, str) or not parent or len(parent) > 32):
        raise ValueError("parent_label is invalid")
    groups = value["groups"]
    if not isinstance(groups, list) or not 1 <= len(groups) <= MAX_GROUPS:
        raise ValueError("groups are invalid")
    normalized = []
    labels = set()
    group_ids = set()
    for group in groups:
        fields = {"group_id", "primary_label", "requested_expansion_count", "auxiliary_terms",
                  "package_name", "package_mode", "delivery_target"}
        if not isinstance(group, dict) or set(group) != fields:
            raise ValueError("label group fields are invalid")
        label, count = group["primary_label"], group["requested_expansion_count"]
        if (not isinstance(label, str) or not label or len(label) > 32 or label in labels
                or type(count) is not int or not (1 if generator == "local-template@1" else 0) <= count <= MAX_EXPANSIONS):
            raise ValueError("label group identity is invalid")
        terms = group["auxiliary_terms"]
        if (not isinstance(terms, list) or len(terms) != count
                or any(not isinstance(term, str) or not term or len(term) > 80 for term in terms)):
            raise ValueError("auxiliary terms are invalid")
        if len(set(terms)) != count or label in terms:
            raise ValueError("auxiliary terms are invalid")
        if (group["group_id"] != _group_id(parent, label) or group["group_id"] in group_ids
                or group["package_name"] != label or group["package_mode"] != "separate"
                or group["delivery_target"] is not None):
            raise ValueError("label group packaging is invalid")
        labels.add(label)
        group_ids.add(group["group_id"])
        normalized.append({key: (list(terms) if key == "auxiliary_terms" else group[key])
                           for key in fields})
    payload = _plan_payload(parent, normalized, generator)
    if binding is not None:
        payload['model_binding'] = dict(binding)
    if (value["overlap_policy"] != payload["overlap_policy"]
            or value["aggregate_manifest"] is not True
            or value["generator"] != payload["generator"]
            or value["requires_confirmation"] is not True
            or value["plan_digest"] != digest(payload)):
        raise ValueError("grouped expansion digest or policy is invalid")
    return {**payload, "plan_digest": value["plan_digest"]}
