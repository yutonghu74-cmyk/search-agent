from dsh_retrieval.adapters.projects import LocalProjectAccess
from dsh_retrieval.proposals import NODES
from dsh_retrieval.service import TaskService

BODY = {
    "dimension_id": "default",
    "query_spec": {
        "schema_version": "query-spec@2", "raw_request": "找 10 张客厅图片",
        "media_type": "image", "target_count": 10, "business_domain": "home_lifestyle",
        "intra_batch_dedup": {"policy_ref": "exact-content", "config_version": "1"},
        "cross_batch_dedup": {"scope": "none", "references": [], "comparison_methods": [], "reference_digest": None},
        "refill_policy": {"mode": "input_only", "target_count": 10, "approved_library_ids": [], "progression": "manual"},
    },
    "node_modes": dict.fromkeys(NODES, "manual"),
}


def make_service(repository):
    actors = [("tenant-a", "user-a"), ("tenant", "owner"), ("a", "user"), ("b", "user")]
    return TaskService(repository, LocalProjectAccess([(t, u, "demo", "default") for t, u in actors]))


def confirmation(service, actor, key="draft", body=None):
    draft, _ = service.save_draft(actor, "demo", key, body or BODY)
    return {"draft_id": draft["draft_id"], "expected_draft_version": draft["draft_version"],
            "proposal_digest": draft["proposal_digest"], "action": "confirm"}
