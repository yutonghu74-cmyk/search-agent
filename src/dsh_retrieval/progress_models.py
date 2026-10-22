"""Truthful read-only projection of the current five-node workflow."""

from dataclasses import dataclass


NODE_DEFINITIONS = (
    ("expansion", "检索词扩词"),
    ("retrieval", "检索"),
    ("cleaning", "清洗（DJ）"),
    ("refill", "补数"),
    ("packaging", "打包"),
)
STAGE_NODE = {
    "RETRIEVING": "retrieval",
    "VALIDATING": "cleaning",
    "DEDUPLICATING": "cleaning",
    "COUNTING": "cleaning",
    "DECIDING": "refill",
    "FINALIZING": "packaging",
    "CLEANING": "cleaning",
    "MULTI_RULE_CLEANING": "cleaning",
    "CLEANING_FINISHED": "cleaning",
    "REFILL": "refill",
}


@dataclass(frozen=True, slots=True)
class ProgressNode:
    node: str
    label: str
    mode: str
    node_status: str
    gate_status: str
    detail: str

    def to_view(self):
        return {"node": self.node, "label": self.label, "mode": self.mode,
                "node_status": self.node_status, "gate_status": self.gate_status,
                "detail": self.detail}


@dataclass(frozen=True, slots=True)
class TaskProgressSnapshot:
    task_id: str
    revision: int
    task_version: int
    run_generation: int
    run_status: str
    stage: str | None
    nodes: tuple[ProgressNode, ...]
    counts: dict
    note: str

    def to_view(self):
        return {"task_id": self.task_id, "revision": self.revision,
                "task_version": self.task_version, "run_generation": self.run_generation,
                "run_status": self.run_status, "stage": self.stage,
                "nodes": [node.to_view() for node in self.nodes],
                "counts": dict(self.counts), "note": self.note}


def build_task_progress(task: dict, proposal: dict) -> TaskProgressSnapshot:
    """Project existing authority without inventing worker events or counts."""
    if not isinstance(task, dict) or not isinstance(proposal, dict):
        raise ValueError("task and proposal must be objects")
    revision = task.get("confirmed_revision")
    task_version = task.get("task_version")
    generation = task.get("run_generation")
    if any(type(value) is not int or value < 1 for value in (revision, task_version, generation)):
        raise ValueError("progress binding is incomplete")
    query = proposal.get("query_spec")
    modes = proposal.get("node_modes")
    if not isinstance(query, dict) or not isinstance(modes, dict):
        raise ValueError("confirmed proposal is incomplete")
    expected = {node for node, _ in NODE_DEFINITIONS}
    if set(modes) != expected or any(modes[node] not in {"manual", "auto"} for node in expected):
        raise ValueError("node modes are invalid")
    target = query.get("target_count")
    if type(target) is not int or target < 1:
        raise ValueError("target count is invalid")

    run_status = task.get("run_status")
    stage = task.get("stage")
    normalized_stage = stage.upper() if isinstance(stage, str) else None
    active = STAGE_NODE.get(normalized_stage)
    if run_status == "PLANNING" and active is None:
        active = "expansion"
    active_index = next((index for index, item in enumerate(NODE_DEFINITIONS)
                         if item[0] == active), None)
    nodes = []
    for index, (node, label) in enumerate(NODE_DEFINITIONS):
        status, gate, detail = "pending", "blocked", "尚无已提交节点输出"
        if active_index is not None and index < active_index:
            status, gate, detail = "succeeded", "approved", "后继阶段已开始，证明本节点已通过推进门禁"
        elif index == active_index:
            if normalized_stage == "CLEANING_FINISHED":
                status = "succeeded"
                gate = "awaiting_approval" if modes[node] == "manual" else "blocked"
                detail = "清洗作业已结束；尚无已批准的后继节点记录"
            elif run_status == "FAILED":
                status, detail = "failed", "当前运行在此阶段失败"
            elif run_status == "CANCELLED":
                status, detail = "cancelled", "当前运行已取消"
            else:
                status, detail = "running", "当前权威运行阶段"
        nodes.append(ProgressNode(node, label, modes[node], status, gate, detail))

    counts = {"target": target, "returned": None, "passed": None,
              "duplicate": None, "accepted": None, "pending_review": None, "gap": None}
    return TaskProgressSnapshot(
        task["task_id"], revision, task_version, generation, run_status, stage,
        tuple(nodes), counts,
        "尚未发布权威结果计数；不显示估算百分比，null 表示未知。",
    )
