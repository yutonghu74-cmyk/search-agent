"""Explicit real-DJ smoke for published manifests; synthetic retrieval/media only."""
import argparse
from contextlib import contextmanager
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import tempfile
import time

from cleaning_smoke import png
from dsh_retrieval.adapters.projects import LocalProjectAccess
from dsh_retrieval.adapters.task_storage.sqlite import SQLiteTaskRepository
from dsh_retrieval.adapters.task_storage.retrieval import SQLiteRetrievalPlanRepository
from dsh_retrieval.adapters.task_storage.candidates import SQLiteCandidateManifestRepository
from dsh_retrieval.adapters.retrieval.materialization import LocalCandidateMaterializer
from dsh_retrieval.adapters.dj_cleaning import DJCleaningExecutor
from dsh_retrieval.candidate_publication import CandidatePublicationService
from dsh_retrieval.cleaning_service import CleaningService
from dsh_retrieval.core.counting import CountingPreviewService
from dsh_retrieval.integration_models import IndexBinding, QueryVector, SearchRequest, SearchHit, SearchPage, MediaRecord, MediaResolution
from dsh_retrieval.manifest_cleaning import PublishedManifestCatalog, ManifestCleaningExecutor
from dsh_retrieval.models import Actor
from dsh_retrieval.retrieval_contracts import RetrievalPlan, RetrievalScope
from dsh_retrieval.service import TaskService


class SyntheticMedia:
    def __init__(self):
        self.bodies = {f"synthetic:{i}": png(width, 64) for i, width in enumerate((16, 64, 128, 64))}

    def require_access(self, context, resource_ref, action):
        if resource_ref != "library:synthetic" and resource_ref not in self.bodies:
            raise ValueError("Not a synthetic resource")

    def resolve_hits(self, context, hits):
        result = []
        for hit in hits:
            ref = f"synthetic:{hit.vector_id}"
            self.require_access(context, ref, "read")
            result.append(MediaResolution(hit, MediaRecord(str(hit.vector_id), "v1", "synthetic",
                str(hit.vector_id), ref, "sha256:" + sha256(self.bodies[ref]).hexdigest()), "resolved"))
        return tuple(result)

    @contextmanager
    def open_read(self, context, media_ref, max_bytes):
        self.require_access(context, media_ref, "read")
        if len(self.bodies[media_ref]) > max_bytes:
            raise ValueError("Synthetic input exceeds limit")
        with BytesIO(self.bodies[media_ref]) as stream:
            yield stream


def run(workspace, dj_python, dj_source):
    root = Path(__file__).resolve().parents[1]
    actor = Actor("synthetic-tenant", "synthetic-owner")
    projects = LocalProjectAccess([(actor.tenant_id, actor.user_id, "demo", "default")])
    media = SyntheticMedia()
    path = workspace / "ledger.sqlite3"
    tasks = SQLiteTaskRepository(path)
    service = TaskService(tasks, projects)
    body = json.loads((root / "contracts/examples/draft.json").read_text())
    draft, _ = service.save_draft(actor, "demo", "draft", body)
    task, _ = service.create_task(actor, "confirm", {"draft_id": draft["draft_id"],
        "expected_draft_version": draft["draft_version"], "proposal_digest": draft["proposal_digest"], "action": "confirm"})
    tid = task["task_id"]
    retrieval = SQLiteRetrievalPlanRepository(path, projects=projects, authorization=media)
    binding = IndexBinding("synthetic-binding", "synthetic", "v1", "synthetic-vector", 2, "COSINE")
    request = SearchRequest("synthetic-plan", 1, binding, QueryVector("synthetic-vector", (1.0, 0.0)), "synthetic-filter", 4)
    plan = RetrievalPlan(RetrievalScope.from_task(tasks.get(actor, tid)), 1, request)
    retrieval.put_proposal(actor, plan)
    plan, _ = retrieval.confirm(actor, tid, request.plan_id, key="plan", proposal_digest=plan.proposal_digest,
                               expected_task_version=plan.scope.task_version, action="confirm")
    oid, _ = retrieval.reserve_page(actor, plan, request, "page")
    page = SearchPage(tuple(SearchHit(binding, i, 1.0) for i in range(4)), None, "exhausted", "snapshot", "synthetic-v1")
    retrieval.commit_page(actor, oid, plan, request, page)
    manifests = SQLiteCandidateManifestRepository(path, projects=projects, authorization=media)
    materializer = LocalCandidateMaterializer(ledger=retrieval, media=media, gateway=media,
        authorization=media, workspace_root=workspace / "preview-temp")
    publication = CandidatePublicationService(materializer, manifests)
    preview = publication.preview(actor, tid, oid)
    selected, _ = publication.confirm(actor, tid, oid, proposal_digest=preview["proposal_digest"],
        expected_task_version=preview["expected_task_version"], key="select", action="confirm")
    catalog = PublishedManifestCatalog(manifests, actor, tid)
    backend = DJCleaningExecutor(dj_python, dj_source, workspace, root / "tools/dj_cleaning_bridge.py",
                                timeout=45, ephemeral_inputs=True)
    executor = ManifestCleaningExecutor(catalog, media, backend, workspace / "cleaning-temp")
    cleaning = CleaningService(tasks, projects, catalog, executor)
    proposal = cleaning.prepare(actor, tid, selected["manifest_ref"])
    def start():
        return cleaning.start(actor, "clean", proposal["proposal_id"], proposal["proposal_digest"],
                              proposal["expected_task_version"], action="confirm")
    operation, _ = start()
    deadline = time.monotonic() + 120
    while operation["status"] == "running" and time.monotonic() < deadline:
        time.sleep(1)
        operation = cleaning.poll(actor, operation["operation_id"])
    if operation["status"] != "succeeded":
        raise RuntimeError(f"Cleaning did not succeed: {operation['status']}; operation={operation['operation_id']}")
    result = cleaning.results(actor, tid)
    assert result["counts"] == {"pass": 3, "fail": 1, "unknown": 0}, result
    assert result["accepted_count"] is None
    counter = CountingPreviewService(tasks, projects, catalog)
    dedup = counter.preview(actor, tid)
    assert dedup["unique_pass_count"] == 2 and dedup["duplicate_count"] == 1, dedup
    assert dedup["accepted_count"] is None and not dedup["refill_dispatched"]
    assert len(manifests.get(actor, tid, selected["manifest_ref"]).proposal.items) == 4
    assert tasks.get(actor, tid).run_status.value == "RUNNING"
    persisted = tasks.get_cleaning_operation(actor, operation["operation_id"])
    assert all(c.source_path == "" for c in persisted.proposal.candidates)
    # Bypass only the service's replay shortcut to exercise the real bridge's
    # idempotency with fresh download paths; reuse the already-approved operation.
    replay_backend = executor.submit(persisted)
    assert replay_backend == operation["backend"]
    for name in ("preview-temp", "cleaning-temp"):
        assert not list((workspace / name).iterdir())
    cleaning.repository = SQLiteTaskRepository(path)
    replay, reused = start()
    assert reused and replay == operation
    assert cleaning.poll(actor, operation["operation_id"]) == operation
    assert CountingPreviewService(cleaning.repository, projects, catalog).preview(actor, tid) == dedup
    return {"status": "PASS", "mode": "REAL_DJ_SYNTHETIC_MEDIA", "retrieval_mode": "SIMULATED",
            "counts": result["counts"], "dedup_preview": dedup, "accepted_count": None, "operation": operation,
            "manifest": selected, "workspace": str(workspace), "fresh_path_replay_same_backend": True,
            "temporary_downloads_cleaned": True, "task_status": tasks.get(actor, tid).run_status.value,
            "not_verified": ["internal_network", "semantic_retrieval", "full_business_rules", "published_delivery"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-real", action="store_true", required=True)
    parser.add_argument("--dj-python", type=Path, required=True)
    parser.add_argument("--dj-source", type=Path, required=True)
    args = parser.parse_args()
    if not args.dj_python.is_file() or not args.dj_source.is_dir():
        parser.error("Existing DJ runtime and source required; no automatic installation")
    # macOS's default /var/folders path can exceed AF_UNIX socket limits in
    # DJ's multiprocessing workers. Keep this unique synthetic workspace short.
    workspace = Path(tempfile.mkdtemp(prefix="dsm-", dir="/tmp"))
    report_path = workspace / "manifest-cleaning-report.json"
    try:
        report = run(workspace, args.dj_python.absolute(), args.dj_source.resolve())
    except Exception as exc:
        report_path.write_text(json.dumps({"status": "FAILED", "workspace": str(workspace),
                                          "error_type": type(exc).__name__, "error": str(exc)}, indent=2))
        print(f"FAILED evidence: {report_path}", flush=True)
        raise
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({"status": report["status"], "counts": report["counts"], "report": str(report_path)}), flush=True)


if __name__ == "__main__":
    main()
