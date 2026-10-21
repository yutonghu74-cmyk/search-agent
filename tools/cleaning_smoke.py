"""Real business-service -> isolated DJ -> per-candidate result smoke, synthetic data only."""
import json
from pathlib import Path
import struct
import tempfile
import time
import zlib

from dsh_retrieval.adapters.cleaning_catalog import LocalCleaningCatalog
from dsh_retrieval.adapters.dj_cleaning import DJCleaningExecutor
from dsh_retrieval.adapters.memory import InMemoryTaskRepository
from dsh_retrieval.adapters.projects import LocalProjectAccess
from dsh_retrieval.cleaning_service import CleaningService
from dsh_retrieval.models import Actor
from dsh_retrieval.service import TaskService


def png(width, height):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    pixels = (b"\0" + b"\x30\x90\xc0" * width) * height
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))


def main():
    root = Path(__file__).resolve().parents[1]
    workspace = Path(tempfile.mkdtemp(prefix="d", dir="/tmp"))
    media = workspace / "media"
    media.mkdir()
    rows = []
    for i, width in enumerate((16, 64, 128)):
        path = media / f"{i}.png"
        path.write_bytes(png(width, 64))
        rows.append((f"c{i}", "v1", path))
    actor = Actor("tenant-a", "user-a")
    projects = LocalProjectAccess([("tenant-a", "user-a", "demo", "default")])
    repo = InMemoryTaskRepository()
    tasks = TaskService(repo, projects)
    body = json.loads((root / "contracts/examples/draft.json").read_text())
    draft, _ = tasks.save_draft(actor, "demo", "draft", body)
    task, _ = tasks.create_task(actor, "confirm", {
        "draft_id": draft["draft_id"], "expected_draft_version": draft["draft_version"],
        "proposal_digest": draft["proposal_digest"], "action": "confirm"})
    executor = DJCleaningExecutor(root / "var/dj-runtime/bin/python",
                                  root / "data-juicer-codex-dj-plan-explorer", workspace,
                                  root / "tools/dj_cleaning_bridge.py")
    catalog = LocalCleaningCatalog(media, {("tenant-a", "demo", "fixture"): rows})
    service = CleaningService(repo, projects, catalog, executor)
    proposal = service.prepare(actor, task["task_id"], "fixture")
    def start():
        return service.start(actor, "clean", proposal["proposal_id"], proposal["proposal_digest"],
                             proposal["expected_task_version"], action="confirm")
    operation, _ = start()
    deadline = time.monotonic() + 100
    while operation["status"] in {"queued", "running"} and time.monotonic() < deadline:
        time.sleep(1)
        operation = service.poll(actor, operation["operation_id"])
    result = service.results(actor, task["task_id"])
    report = {"workspace": str(workspace), "status": operation["status"], "operation": operation,
              "result": result, "task_status": repo.get(actor, task["task_id"]).run_status.value}
    (root / "var/cleaning-smoke.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    assert operation["status"] == "succeeded", report
    assert result["counts"] == {"pass": 2, "fail": 1, "unknown": 0}, report
    assert result["accepted_count"] is None
    assert report["task_status"] == "RUNNING"
    replay, reused = start()
    assert reused and replay == operation
    assert service.poll(actor, operation["operation_id"]) == operation
    assert len(repo._cleaning_events) == 1
    print(json.dumps({"status": "PASS", "counts": result["counts"], "workspace": str(workspace),
                      "report": str(root / "var/cleaning-smoke.json")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
