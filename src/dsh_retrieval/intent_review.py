"""Lossless review boundary for untrusted interpretation proposals, not an LLM.

This module never creates tasks or compiles executable recipes. A provider may
suggest interpretations; current capabilities and review gates are server-owned.
"""
from dataclasses import asdict, dataclass
import json

from .errors import invalid
from .query_spec import digest

KINDS = frozenset({"intent", "constraint", "preference", "source", "quantity", "dedup", "unresolved"})


@dataclass(frozen=True, slots=True)
class Capability:
    predicate_ref: str
    kind: str
    media_type: str
    operators: tuple[str, ...]
    value_type: str
    status: str
    validator_ref: str
    evidence_ref: str

    def __post_init__(self):
        if (not all(isinstance(s, str) and s.strip() for s in
                    (self.predicate_ref, self.kind, self.media_type, self.value_type, self.status))
                or self.kind not in KINDS - {"unresolved"}
                or self.media_type not in {"image", "video", "text"}
                or self.status not in {"verified", "unavailable", "manual"}
                or self.value_type not in {"boolean", "integer", "number", "string"}
                or not isinstance(self.operators, tuple) or not self.operators
                or any(not isinstance(op, str) or not op for op in self.operators)):
            raise ValueError("Invalid trusted capability definition")
        if self.status in {"verified", "manual"} and not all(
                isinstance(s, str) and s.strip() for s in (self.validator_ref, self.evidence_ref)):
            raise ValueError("Released capability requires validator and evidence references")


class IntentReviewService:
    def __init__(self, capabilities=(), *, registry_version="unconfigured@1"):
        self.capabilities = tuple(capabilities)
        if not isinstance(registry_version, str) or not registry_version.strip():
            raise ValueError("Registry version required")
        keys = [(c.kind, c.predicate_ref, c.media_type) for c in self.capabilities]
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate capability definition")
        self.registry_version = registry_version
        self.registry_digest = digest([asdict(c) for c in self.capabilities])

    def review(self, raw_request, proposal=None):
        if not isinstance(raw_request, str) or not raw_request.strip() or len(raw_request) > 16000:
            raise invalid("raw_request must contain 1..16000 characters")
        raw_digest = digest({"raw_request": raw_request})
        if proposal is None:
            proposal = {"schema_version": "intent-interpretation@1", "raw_digest": raw_digest,
                        "media_type": None, "items": []}
        if (not isinstance(proposal, dict) or set(proposal) != {"schema_version", "raw_digest", "media_type", "items"}
                or proposal["schema_version"] != "intent-interpretation@1" or proposal["raw_digest"] != raw_digest):
            raise invalid("Interpretation must match schema and original text digest")
        media_type = proposal["media_type"]
        if media_type not in (None, "image", "video", "text"):
            raise invalid("Invalid target media type")
        if not isinstance(proposal["items"], list) or len(proposal["items"]) > 100:
            raise invalid("Expected at most 100 interpretation items")
        covered, ids, items = set(), set(), []
        fields = {"item_id", "start", "end", "quote", "kind", "hardness", "predicate_ref", "operator", "value", "question"}
        for row in proposal["items"]:
            if not isinstance(row, dict) or set(row) != fields:
                raise invalid("Unexpected interpretation fields")
            start, end, ident = row["start"], row["end"], row["item_id"]
            if (not isinstance(ident, str) or not ident.strip() or len(ident) > 100 or ident in ids
                    or ident.startswith("unparsed-") or type(start) is not int or type(end) is not int
                    or not 0 <= start < end <= len(raw_request)
                    or row["quote"] != raw_request[start:end] or not row["quote"].strip()):
                raise invalid("Invalid identity or original-text span")
            if (not isinstance(row["kind"], str) or row["kind"] not in KINDS
                    or not isinstance(row["hardness"], str) or row["hardness"] not in {"hard", "soft", "unspecified"}):
                raise invalid("Invalid kind or hardness")
            if ((row["kind"] == "preference" and row["hardness"] != "soft")
                    or (row["kind"] == "constraint" and row["hardness"] != "hard")):
                raise invalid("Preference/constraint hardness mismatch")
            for name in ("predicate_ref", "operator", "question"):
                if row[name] is not None and (not isinstance(row[name], str) or not row[name].strip() or len(row[name]) > 2000):
                    raise invalid("Expected nonempty string or null", name)
            # Only scalar rule values at this review boundary. Rich structures
            # require a versioned schema extension, never unchecked code/config.
            if row["value"] is not None and type(row["value"]) not in (str, int, float, bool):
                raise invalid("Expected scalar value")
            if isinstance(row["value"], str) and len(row["value"]) > 16000:
                raise invalid("Interpretation value too long")
            try:
                snapshot = json.loads(json.dumps(row, ensure_ascii=False, allow_nan=False))
            except (ValueError, OverflowError) as exc:
                raise invalid("Non-finite interpretation value") from exc
            ids.add(ident)
            covered.update(range(start, end))
            items.append(snapshot)
        # Every untouched non-whitespace interval remains visible and blocking.
        cursor = 0
        while cursor < len(raw_request):
            if cursor in covered or raw_request[cursor].isspace():
                cursor += 1
                continue
            end = cursor + 1
            while end < len(raw_request) and end not in covered and not raw_request[end].isspace():
                end += 1
            items.append({"item_id": f"unparsed-{cursor}", "start": cursor, "end": end,
                          "quote": raw_request[cursor:end], "kind": "unresolved", "hardness": "unspecified",
                          "predicate_ref": None, "operator": None, "value": None,
                          "question": "这段要求应如何理解？请补充或确认。"})
            cursor = end
        items.sort(key=lambda row: (row["start"], row["end"], row["item_id"]))
        for item in items:
            item["capability_status"], item["reason"] = self._assess(item, media_type)
            item["blocks_automatic_execution"] = item["capability_status"] != "automatable" and item["hardness"] != "soft"
            if item["kind"] == "unresolved" or item["hardness"] == "unspecified" or item["question"]:
                item["blocks_automatic_execution"] = True
        # Conservative check for directly opposed equality constraints. This is
        # not a general logical/semantic contradiction solver.
        equalities = {}
        for item in items:
            if item["hardness"] == "hard" and item["operator"] == "eq" and item["predicate_ref"]:
                equalities.setdefault((item["kind"], item["predicate_ref"]), []).append(item)
        for group in equalities.values():
            if len({json.dumps(i["value"], ensure_ascii=False, sort_keys=True) for i in group}) > 1:
                for item in group:
                    item.update(capability_status="needs_clarification", reason="CONFLICTING_EQUALITIES",
                                blocks_automatic_execution=True)
        report = {"schema_version": "intent-review@1", "raw_request": raw_request, "raw_digest": raw_digest,
                  "media_type": media_type, "registry_version": self.registry_version,
                  "registry_digest": self.registry_digest, "items": items,
                  "issues": ["TARGET_MEDIA_UNRESOLVED"] if media_type is None else [],
                  "requires_user_review": True, "execution_allowed": False,
                  "kind": "interpretation_review_only", "creates_task": False,
                  "has_blockers": media_type is None or any(i["blocks_automatic_execution"] for i in items)}
        report["review_digest"] = digest(report)
        return report

    def _assess(self, item, media_type):
        if (media_type is None or item["kind"] == "unresolved" or item["hardness"] == "unspecified"
                or item["question"] or not item["predicate_ref"] or not item["operator"]):
            return "needs_clarification", "INTERPRETATION_INCOMPLETE"
        capability = next((c for c in self.capabilities if
            (c.kind, c.predicate_ref, c.media_type) == (item["kind"], item["predicate_ref"], media_type)), None)
        if capability is None:
            return "unsupported", "NO_REGISTERED_CAPABILITY"
        types = {"boolean": (bool,), "integer": (int,), "number": (int, float), "string": (str,)}
        if item["operator"] not in capability.operators or type(item["value"]) not in types[capability.value_type]:
            return "needs_clarification", "CAPABILITY_PARAMETER_MISMATCH"
        return {"verified": ("automatable", "REGISTERED_VERIFIED_CAPABILITY"),
                "manual": ("manual_review", "MANUAL_EVIDENCE_REQUIRED"),
                "unavailable": ("unsupported", "CAPABILITY_UNAVAILABLE")}[capability.status]
