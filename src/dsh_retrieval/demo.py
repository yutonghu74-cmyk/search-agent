"""Local, interactive, synthetic-only demo: python -m dsh_retrieval.demo."""
import argparse
import json
import os
from pathlib import Path
import struct
import tempfile
import time
import zlib

from .adapters.cleaning_catalog import LocalCleaningCatalog
from .adapters.dj_cleaning import DJCleaningExecutor
from .adapters.task_storage.sqlite import SQLiteTaskRepository
from .adapters.projects import LocalProjectAccess
from .candidate_intake import CandidateIntake
from .cleaning_service import CleaningService
from .core.counting import CountingPreviewService
from .errors import ServiceError
from .models import Actor
from .service import TaskService


WIDTHS = (16, 64, 128, 64)


def save_json(path, value):
    """Atomic local checkpoint replacement, with file contents synced first."""
    fd, temp = tempfile.mkstemp(prefix="checkpoint-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


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
    repo = SQLiteTaskRepository(workspace / "ledger.sqlite3")
    tasks = TaskService(repo, projects)
    task_id, cleaning = None, None
    report = {"execution_mode": execution_mode, "source_mode": "synthetic_fixed_input",
              "workspace": str(workspace), "accepted_count": None, "preview_only": True,
              "warning": "仅本地合成数据与本地 SQLite；已确认清洗可恢复，不是语义检索或正式业务完成。"}

    def finish(status, **fields):
        report.update(status=status, **fields)
        if task_id:
            report["task_status"] = tasks.get_task(actor, task_id)["run_status"]
        path = workspace / "demo-report.json"
        save_json(path, report)
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
        save_json(workspace / "session.json", {"format": 1, "execution_mode": execution_mode,
                  "task_id": task_id, "proposal_id": plan["proposal_id"], "cleaning_confirmed": True})
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


class RecoveredCatalog(LocalCleaningCatalog):
    """Revalidate original fixture bytes from the durable, approved proposal."""
    def __init__(self, workspace, proposal):
        self.proposal = proposal
        media = (workspace / "media").resolve()
        if any(not Path(c.source_path).resolve().is_relative_to(media) for c in proposal.candidates):
            raise ServiceError("INPUT_NOT_AVAILABLE", "recovery inputs are outside demo workspace", 409)
        super().__init__(media, {(proposal.tenant_id, proposal.project_id, proposal.manifest_ref):
                               [(c.candidate_id, c.asset_version, c.source_path) for c in proposal.candidates]}, proposal.min_width)

    def require_scope(self, task, manifest_ref):
        p = self.proposal
        if (task.tenant_id, task.owner_user_id, task.project_id, task.task_id, task.confirmed_revision,
            task.run_generation, manifest_ref) != (p.tenant_id, p.owner_user_id, p.project_id, p.task_id,
                                                  p.revision, p.generation, p.manifest_ref):
            raise ServiceError("MANIFEST_SCOPE_MISMATCH", "recovery task or generation changed", 409)

    def resolve(self, *args):
        current = super().resolve(*args)
        if current != (self.proposal.candidates, self.proposal.min_width):
            raise ServiceError("INPUT_SNAPSHOT_CHANGED", "recovery bytes differ from approved input", 409)
        return current


def resume_session(workspace, executor, execution_mode, approve, emit, *, timeout=100, interval=1):
    checkpoint = json.loads((workspace / "session.json").read_text())
    if (checkpoint.get("format") != 1 or checkpoint.get("execution_mode") != execution_mode
            or checkpoint.get("cleaning_confirmed") is not True or not (workspace / "ledger.sqlite3").is_file()):
        raise ServiceError("RECOVERY_NOT_AVAILABLE", "matching approved session and ledger required", 409)
    actor = Actor("tenant-a", "user-a")
    projects = LocalProjectAccess([("tenant-a", "user-a", "demo", "default")])
    repo = SQLiteTaskRepository(workspace / "ledger.sqlite3")
    proposal = repo.get_cleaning_proposal(actor, checkpoint["proposal_id"])
    task_id = checkpoint["task_id"]
    if proposal.task_id != task_id:
        raise ServiceError("RECOVERY_NOT_AVAILABLE", "proposal task mismatch", 409)
    task = repo.get(actor, task_id)
    if task.run_status.value not in ("PLANNING", "RUNNING"):
        raise ServiceError("RECOVERY_NOT_AVAILABLE", "paused/cancelled task requires a separate explicit retry workflow", 409)
    catalog = RecoveredCatalog(workspace, proposal)
    catalog.require_scope(task, proposal.manifest_ref)
    service = CleaningService(repo, projects, catalog, executor)
    emit("恢复原已批准方案（不会更换候选或新建业务任务）", proposal.view())
    if not approve("继续查询或执行原清洗作业？"):
        return {"status": "declined", "stopped_at": "resume"}
    try:
        operation, _ = service.start(actor, "clean", proposal.proposal_id, proposal.plan_digest,
                                      proposal.task_version, action="confirm")
        deadline = time.monotonic() + timeout
        while operation["status"] in ("queued", "running"):
            if time.monotonic() >= deadline:
                raise ServiceError("RECOVERY_TIMEOUT", "recovery timed out", 504)
            operation = service.poll(actor, operation["operation_id"])
            if operation["status"] in ("queued", "running"):
                time.sleep(interval)
        preview = CountingPreviewService(repo, projects, catalog).preview(actor, task_id)
        report = {"status": "preview_ready" if operation["status"] == "succeeded" else "cleaning_failed",
                  "resumed": True, "execution_mode": execution_mode, "source_mode": "synthetic_fixed_input",
                  "workspace": str(workspace), "operation": operation, "preview": preview,
                  "accepted_count": None, "task_status": repo.get(actor, task_id).run_status.value}
        save_json(workspace / "demo-report.json", report)
        emit("恢复结果", report)
        return report
    except (ServiceError, KeyboardInterrupt, EOFError):
        current = repo.get(actor, task_id)
        if current.active_cleaning_operation_id:
            service.cancel(actor, current.active_cleaning_operation_id, "resume-stop", current.task_version)
        raise


def main():
    parser = argparse.ArgumentParser(description="仅本地合成数据的交互演示；不提供自动批准选项")
    parser.add_argument("--executor", required=True, choices=("simulated", "real"))
    parser.add_argument("--resume", type=Path, help="恢复本次升级后生成且已确认清洗的 /tmp/d... 目录")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    if args.executor == "real":
        if not (root / "var/dj-runtime/bin/python").is_file() or not (root / "data-juicer-codex-dj-plan-explorer").is_dir():
            parser.error("真实 DJ 环境尚未准备；可先用 --executor simulated，不会自动安装或降级。")
    workspace = args.resume.resolve() if args.resume else Path(tempfile.mkdtemp(prefix="d", dir="/tmp"))
    executor = SimulatedExecutor() if args.executor == "simulated" else DJCleaningExecutor(
        root / "var/dj-runtime/bin/python", root / "data-juicer-codex-dj-plan-explorer",
        workspace, root / "tools/dj_cleaning_bridge.py")

    def emit(title, value):
        print("\n" + title + "\n" + json.dumps(value, ensure_ascii=False, indent=2), flush=True)

    def approve(question):
        return input(question + " 输入“确认”或 y 继续，其他输入停止：").strip() in ("确认", "y", "Y")

    try:
        result = (resume_session(workspace, executor, args.executor, approve, emit) if args.resume else
                  run_session(root, workspace, executor, args.executor, approve, emit))
    except (ServiceError, OSError, ValueError, KeyError) as error:
        emit("恢复未执行（原记录保留）", getattr(error, "code", "RECOVERY_NOT_AVAILABLE"))
        raise SystemExit(1)
    except (KeyboardInterrupt, EOFError):
        print("已停止恢复。")
        raise SystemExit(130)
    if result["status"] in ("error", "timeout", "cleaning_failed"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
