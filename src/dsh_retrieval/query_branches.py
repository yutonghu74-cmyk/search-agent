"""Deterministic logical query preview, never an approved physical RetrievalPlan."""

from copy import deepcopy

from .expansion_models import normalize_grouped_expansion
from .query_spec import digest


COMPILER_VERSION = "query-branches@1"


def compile_query_branches(query: dict) -> dict:
    """Compile stored QuerySpec without guessing semantics or authorizing work.

    Whitespace is the only equivalence rule. Case, punctuation, product numbers
    and negation remain untouched. Each duplicate retains all distinct origins.
    """
    if not isinstance(query, dict):
        raise ValueError("query must be an object")
    raw = query.get("raw_request")
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("original request is required")
    rules = query.get("rules", [])
    sources = query.get("source_constraints", [])
    if (not isinstance(rules, list) or not isinstance(sources, list)
            or any(not isinstance(rule, dict) or not isinstance(rule.get("rule_id"), str)
                   or not rule["rule_id"] for rule in rules)):
        raise ValueError("constraint references are invalid")
    video = query.get("media_type") == "video"
    constraints = ["rule:" + rule["rule_id"] for rule in rules]
    if video:
        constraints += ["video_condition:" + clause["id"]
                        for clause in query["video_conditions"]["clauses"]]
    constraints += [f"source_constraints:{index}" for index in range(len(sources))]
    grouped = query.get("grouped_expansion")
    plan = normalize_grouped_expansion(grouped) if grouped is not None else None
    branches = {}

    def add(text, role, term, group_id=None, version="user-input@1"):
        normalized = " ".join(text.split())
        fingerprint_input = {"compiler": COMPILER_VERSION, "query_text": normalized,
                             "media_type": query.get("media_type"),
                             "rules": rules, "source_constraints": sources}
        # Keep every existing text-only branch ID byte-for-byte stable. Only a
        # real reference-image input extends the fingerprint namespace.
        if query.get("query_input") is not None:
            fingerprint_input["query_input"] = query["query_input"]
        if video:
            fingerprint_input["video_conditions"] = query["video_conditions"]
            fingerprint_input["count_unit"] = query["count_unit"]
            fingerprint_input["video_options"] = query.get("video_options")
        fingerprint = digest(fingerprint_input)
        origin = {"group_id": group_id, "query_role": role,
                  "source_term": term, "skill_version": version}
        if fingerprint not in branches:
            branches[fingerprint] = {
                "branch_id": "branch_" + fingerprint[7:], "query_fingerprint": fingerprint,
                "query_text": normalized, "query_role": role, "source_term": term,
                "skill_version": version, "intended_libraries": [],
                "constraint_refs": list(constraints), "origins": [],
            }
        if origin not in branches[fingerprint]["origins"]:
            branches[fingerprint]["origins"].append(origin)

    query_input = query.get("query_input")
    reference_image = query_input is not None and query_input.get("type") == "reference_image"
    add(query_input["text"] if video else raw, "reference_image" if reference_image else "original", raw)
    if plan and not reference_image:
        for group in plan["groups"]:
            label = group["primary_label"]
            add(label, "primary", label, group["group_id"], plan["generator"])
            for term in group["auxiliary_terms"]:
                # Keep the explicit subject with user-supplied variants. This is
                # composition, not a claim that synonyms or categories were inferred.
                text = term if term.startswith(label) else label + " " + term
                add(text, "expansion", term, group["group_id"], plan["generator"])
    payload = {
        "schema_version": COMPILER_VERSION, "query_spec_digest": digest(query),
        "original_request": raw, "branches": list(branches.values()),
        "constraints": {"rules": deepcopy(rules), "source_constraints": deepcopy(sources),
                        **({"video_conditions": deepcopy(query["video_conditions"])} if video else {})},
        "status": "preview_only", "executable": False,
        "library_resolution": "unresolved",
        "note": ("参考图仅作为图像向量输入；文字作为选库与后置清洗约束。尚未选库、编码或批准执行。"
                 if reference_image else
                 "仅逻辑查询预览，尚未选库、编码或批准执行。否定条件仍须编译为后置验证规则；召回命中不代表合格。"),
    }
    return {**payload, "preview_digest": digest(payload)}


def build_query_branch_view(task: dict, proposal: dict) -> dict:
    if not isinstance(proposal, dict) or not isinstance(task, dict):
        raise ValueError("confirmed task and proposal required")
    if any(type(task.get(key)) is not int or task[key] < 1
           for key in ("confirmed_revision", "task_version", "run_generation")):
        raise ValueError("invalid task binding")
    return {"task_id": task["task_id"], "revision": task["confirmed_revision"],
            "task_version": task["task_version"], "run_generation": task["run_generation"],
            "proposal_digest": digest(proposal),
            "preview": compile_query_branches(proposal.get("query_spec"))}
