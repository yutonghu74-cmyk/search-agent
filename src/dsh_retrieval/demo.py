"""Local, interactive, synthetic-only demo: python -m dsh_retrieval.demo."""
import argparse
import json
from pathlib import Path
import struct
import tempfile
import time
import zlib

from .adapters.cleaning_catalog import LocalCleaningCatalog
from .adapters.dj_cleaning import DJCleaningExecutor
from .adapters.memory import InMemoryTaskRepository
from .adapters.projects import LocalProjectAccess
from .candidate_intake import CandidateIntake
from .cleaning_service import CleaningService
from .core.counting import CountingPreviewService
from .errors import ServiceError
from .models import Actor
from .service import TaskService


WIDTHS = (16, 64, 128, 64)


def synthetic_png(width):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, 64, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\0" + b"\x30\x90\xc0" * width) * 64))
            + chunk(b"IEND", b""))


class SimulatedExecutor:
    """Fixture outcomes only: never claim image decoding or real DJ execution."""
    def submit(self, operation):
        return {"dj_run_id": "SIMULATED:" + operation.operation_id}

    def poll(self, operation):
        return {"binding": operation.binding(), "status": "succeeded", "results": [
            {"candidate_id": c.candidate_id, "asset_version": c.asset_version,
             "verdict": "fail" if WIDTHS[i] < 32 else "pass",
             "reason_code": "WIDTH_TOO_SMALL" if WIDTHS[i] < 32 else None,
             "evidence_ref": "SIMULATED:fixture-width"}
            for i, c in enumerate(operation.proposal.candidates)]}

    def cancel(self, operation):
        return {"status": "cancelled"}


def run_session(root, workspace, executor, execution_mode, approve, emit, *, timeout=100, interval=1):
    """Callbacks allow deterministic testing of the interactive boundary."""
    actor = Actor("tenant-a", "user-a")
    projects = LocalProjectAccess([("tenant-a", "user-a", "demo", "default")])
    repo = InMemoryTaskRepository()
    tasks = TaskService(repo, projects)
    task_id, cleaning = None, None
    report = {"execution_mode": execution_mode, "source_mode": "synthetic_fixed_input",
              "workspace": str(workspace), "accepted_count": None, "preview_only": True,
              "warning": "仅本地合成数据；内存状态不支持重启恢复；不是语义检索或正式业务完成。"}

    def finish(status, **fields):
        report.update(status=status, **fields)
        if task_id:
            report["task_status"] = tasks.get_task(actor, task_id)["run_status"]
        path = workspace / "demo-report.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        emit("报告路径", str(path))
        emit("演示结果", report)
        return report

    def stop_worker():
        if task_id and cleaning:
            task = repo.get(actor, task_id)
            if task.active_cleaning_operation_id:
                try:
                    cleaning.cancel(actor, task.active_cleaning_operation_id, "demo-stop", task.task_version)
                    return "publication_fenced; interruption_requested"
                except ServiceError as error:
                    return "cancel_error:" + error.code
        return "no_worker_started"

    try:
        emit("演示范围", report)
        body = json.loads((root / "contracts/examples/draft.json").read_text())
        body["query_spec"].update(raw_request="合成图片演示：检查宽度至少 32 像素并展示精确内容去重预览", target_count=3)
        body["query_spec"]["refill_policy"]["target_count"] = 3
        draft, _ = tasks.save_draft(actor, "demo", "draft", body)
        emit("1. 需求草稿（目标 3，仅手动确认，范围外不补数）", draft)
        if not approve("确认需求并创建任务？"):
            return finish("declined", stopped_at="draft")
        task, _ = tasks.create_task(actor, "task", {"draft_id": draft["draft_id"],
            "expected_draft_version": draft["draft_version"], "proposal_digest": draft["proposal_digest"], "action": "confirm"})
        task_id = task["task_id"]
        media = workspace / "media"
        media.mkdir()
        rows = []
        for i, width in enumerate(WIDTHS):
            path = media / f"c{i}.png"
            path.write_bytes(synthetic_png(width))
            rows.append((f"c{i}", "v1", path))
        sources = LocalCleaningCatalog(media, {("tenant-a", "demo", "fixture"): rows})
        intake = CandidateIntake(repo, projects, sources)
        selection = intake.preview(actor, task_id, "fixture")
        emit("2. 固定候选（非搜索结果，c1/c3 内容相同）", selection)
        if not approve("确认这 4 张合成候选？"):
            return finish("declined", stopped_at="candidates")
        selected, _ = intake.confirm(actor, task_id, "fixture", selection["proposal_digest"],
                                     selection["expected_task_version"], "select", action="confirm")
        cleaning = CleaningService(repo, projects, intake, executor)
        plan = cleaning.prepare(actor, task_id, selected["manifest_ref"])
        emit("3. 清洗方案（只检查宽度，不检查语义、质量或水印）", plan)
        if not approve(f"确认启动清洗（执行模式：{execution_mode}）？"):
            return finish("declined", stopped_at="cleaning")
        operation, _ = cleaning.start(actor, "clean", plan["proposal_id"], plan["proposal_digest"],
                                      plan["expected_task_version"], action="confirm")
        deadline = time.monotonic() + timeout
        emit("执行中", "可按 Ctrl-C 取消；演示报告会保留。")
        while operation["status"] in ("queued", "running"):
            if time.monotonic() >= deadline:
                return finish("timeout", cancellation=stop_worker())
            operation = cleaning.poll(actor, operation["operation_id"])
            if operation["status"] in ("queued", "running"):
                time.sleep(interval)
        report["operation"] = operation
        report["preview"] = CountingPreviewService(repo, projects, intake).preview(actor, task_id)
        return finish("preview_ready" if operation["status"] == "succeeded" else "cleaning_failed")
    except (KeyboardInterrupt, EOFError):
        return finish("interrupted", cancellation=stop_worker())
    except ServiceError as error:
        return finish("error", error_code=error.code, cancellation=stop_worker())


def main():
    parser = argparse.ArgumentParser(description="仅本地合成数据的交互演示；不提供自动批准选项")
    parser.add_argument("--executor", required=True, choices=("simulated", "real"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    if args.executor == "real":
        if not (root / "var/dj-runtime/bin/python").is_file() or not (root / "data-juicer-codex-dj-plan-explorer").is_dir():
            parser.error("真实 DJ 环境尚未准备；可先用 --executor simulated，不会自动安装或降级。")
    workspace = Path(tempfile.mkdtemp(prefix="d", dir="/tmp"))
    executor = SimulatedExecutor() if args.executor == "simulated" else DJCleaningExecutor(
        root / "var/dj-runtime/bin/python", root / "data-juicer-codex-dj-plan-explorer",
        workspace, root / "tools/dj_cleaning_bridge.py")

    def emit(title, value):
        print("\n" + title + "\n" + json.dumps(value, ensure_ascii=False, indent=2), flush=True)

    def approve(question):
        return input(question + " 输入“确认”或 y 继续，其他输入停止：").strip() in ("确认", "y", "Y")

    result = run_session(root, workspace, executor, args.executor, approve, emit)
    if result["status"] in ("error", "timeout", "cleaning_failed"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
