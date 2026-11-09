"""Truthful read-only projection of the current five-node workflow."""

from dataclasses import dataclass


NODE_DEFINITIONS = (
    ("expansion", "检索词扩词"),
    ("retrieval", "检索"),
    ("cleaning", "清洗"),
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


def build_task_progress(task: dict, proposal: dict, result_set: dict | None = None,
                        *, raw_returned: int | None = None,
                        _published_page: dict | None = None) -> TaskProgressSnapshot:
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
    if raw_returned is not None and (type(raw_returned) is not int or raw_returned < 0):
        raise ValueError("raw returned count is invalid")

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

    counts = {"target": target, "returned": raw_returned, "passed": None,
              "duplicate": None, "accepted": None, "pending_review": None,
              "near_duplicate_review": None, "gap": None}
    note = "尚未发布权威结果计数；不显示估算百分比，null 表示未知。"
    if result_set is not None:
        from .result_views import build_result_page
        page = (_published_page if _published_page is not None
                else build_result_page(task, proposal, result_set))
        if _published_page is not None:
            if (page.get('task_id') != task['task_id']
                    or page.get('revision') != revision
                    or page.get('run_generation') != generation
                    or page.get('task_version') != task_version
                    or not page.get('available')):
                raise ValueError('published progress page binding is invalid')
            required_counts = ('eligible_unique', 'duplicate', 'accepted', 'reserve',
                               'pending_review', 'near_duplicate_review', 'gap')
            if any(type(page.get('counts', {}).get(key)) is not int
                   or page['counts'][key] < 0 for key in required_counts):
                raise ValueError('published progress counts are invalid')
        published = page["counts"]
        counts.update(passed=published["eligible_unique"] + published["duplicate"],
                      accepted=published["accepted"],
                      reserve=published["reserve"], duplicate=published["duplicate"],
                      pending_review=published["pending_review"],
                      near_duplicate_review=published["near_duplicate_review"],
                      gap=published["gap"])
        group_progress = page.get("group_progress") or []
        if group_progress:
            counts["groups"] = group_progress
        short = [f"{row['label']} {row['gap']}" for row in group_progress if row["gap"]]
        gap_text = f"缺口 {published['gap']}" + (f"（{'、'.join(short)}）" if short else "")
        note = "当前版本合格预览计数；入选与候补分开，尚不代表打包或正式交付完成。"
        if result_set.get('kind') == 'delivery' and run_status in ('COMPLETED', 'PARTIAL'):
            nodes = [ProgressNode(node.node, node.label, node.mode, 'succeeded', 'approved',
                                 '当前版本交付已发布，可在打包节点下载') for node in nodes]
            note = ('当前版本部分交付已发布；缺口与停止原因保留在交付清单中。'
                    if run_status == 'PARTIAL' else
                    '当前版本交付已发布；下载仍需重新校验当前权限和素材版本。')
        elif normalized_stage == 'FINALIZING' and run_status == 'AWAITING_INPUT':
            nodes[4] = ProgressNode('packaging', '打包', modes['packaging'], 'succeeded',
                                    'awaiting_approval', '待发布包已生成；等待发起人确认发布，尚不可下载')
        if normalized_stage == "REFILL" and run_status in ("RUNNING", "AWAITING_INPUT"):
            policy = proposal['query_spec'].get('refill_policy', {})
            automatic = (policy.get('mode') == 'maintain_target'
                         and policy.get('progression') == 'auto'
                         and modes['refill'] == 'auto')
            if published['gap'] == 0:
                detail = '已达到目标，无需补数；待打包门禁'
            elif automatic:
                reasons = {
                    'EXECUTION_LIMIT_REACHED': '已达到补数轮次上限',
                    'VLM_CALL_LIMIT_REACHED': '已达到 VLM 调用预算',
                    'NO_PROGRESS': '连续补数没有新增有效结果',
                    'SEARCH_STRATEGIES_EXHAUSTED': '当前批准的检索策略已耗尽',
                }
                reason = reasons.get(result_set.get('stop_reason'))
                next_step = ('可部分交付' if published['accepted'] > 0
                             else '当前无有效素材，请修改方案重新检索')
                detail = (f"{gap_text}；自动补数已停止：{reason}；{next_step}"
                          if reason else
                          f"{gap_text}；自动补数已启用，逐轮证据见本节点输出")
            else:
                detail = (f"{gap_text}；已确认范围仅限固定输入，可部分交付；"
                          '继续检索需新方案确认')
            nodes[3] = ProgressNode(
                'refill', '补数', modes['refill'],
                'succeeded' if published['gap'] == 0 else 'pending', 'blocked', detail)
            if run_status == 'AWAITING_INPUT' and result_set.get('stop_reason'):
                nodes[3] = ProgressNode('refill', '补数', modes['refill'], 'succeeded',
                                        'approved', detail)
                nodes[4] = ProgressNode(
                    'packaging', '打包', modes['packaging'], 'pending',
                    'awaiting_approval',
                    ('请选择按当前有效素材部分交付，或修改方案后重新检索'
                     if published['accepted'] > 0 else
                     '当前无有效素材，不能生成空交付包；请修改方案后重新检索'))
    return TaskProgressSnapshot(
        task["task_id"], revision, task_version, generation, run_status, stage,
        tuple(nodes), counts,
        note,
    )
