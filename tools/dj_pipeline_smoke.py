"""Real, offline image-shape plan_flow probe; requires the isolated DJ Python.

Synthetic images only. This is a trusted local diagnostic, not an Agent tool or
an implementation of business authorization. Never bypasses runtime preflight.
"""
import json
import os
from pathlib import Path
import tempfile
import time


def main():
    # macOS AF_UNIX has a small path limit; DJ nests multiprocessing sockets
    # below workspace/.dj/tasks/.../runs/.../tmp. Keep this diagnostic root short.
    workspace = Path(tempfile.mkdtemp(prefix="d", dir="/tmp"))
    os.environ.update(DJ_PLAN_FLOW_SINGLE_USER="1", HF_HUB_OFFLINE="1", HF_DATASETS_OFFLINE="1",
                      UV_OFFLINE="1", PYTHONDONTWRITEBYTECODE="1",
                      HF_HOME=str(workspace / "hf"), DSH_USER_DATA_ROOT=str(workspace / "users"))
    from PIL import Image
    from data_juicer.tools.plan_flow.service import PlanFlowService
    from data_juicer.tools.plan_flow.common import PlanFlowError
    from data_juicer.tools.plan_flow.runtime_environment_lock import inspect_plan_runtime

    service = PlanFlowService.native()
    report = {"workspace": str(workspace), "status": "RUNNING", "checks": {}}
    running = None
    print(f"WORKSPACE={workspace}", flush=True)
    try:
        runtime = inspect_plan_runtime({})
        report["runtime"] = runtime
        assert runtime["ok"], runtime["blocking_issues"]
        rows = []
        for i, size in enumerate(((16, 16), (64, 64), (128, 64))):
            path = workspace / f"image-{i}.png"
            Image.new("RGB", size, (80 + i, 120, 160)).save(path)
            rows.append({"candidate_id": f"c{i}", "asset_version": "v1",
                         "text": "<__dj__image>", "images": [str(path)]})
        dataset = workspace / "input.jsonl"
        dataset.write_text("".join(json.dumps(row) + "\n" for row in rows))
        plan = {"user_intent": "Synthetic image width at least 32", "modality": "image",
                "approval_required": True, "risk_notes": [],
                "recipe": {"dataset_path": str(dataset), "export_path": "result.jsonl", "np": 1,
                           "executor_type": "default", "use_cache": False,
                           "process": [{"image_shape_filter": {"min_width": 32}}]},
                "postprocess": [{"kind": "image_audit", "copy_kept": True,
                                 "rules": [{"id": "width", "field": "image_width", "min": 32}]}]}
        prepared = service.prepare_plan(str(workspace), plan, request_id="prepare-smoke")
        again = service.prepare_plan(str(workspace), plan, request_id="prepare-smoke")
        assert (again["task_id"], again["plan_version"]) == (prepared["task_id"], prepared["plan_version"])
        report["checks"]["prepare_replay"] = "PASS"
        args = (str(workspace), prepared["task_id"], prepared["plan_version"])
        try:
            service.run_plan(*args, request_id="unapproved", timeout_seconds=60)
        except PlanFlowError as exc:
            assert exc.code == "APPROVAL_REQUIRED", exc.code
        else:
            raise AssertionError("unapproved plan executed")
        report["checks"]["approval_required"] = "PASS"
        service.approve_plan(*args, prepared["content_hash"])
        started = service.run_plan(*args, request_id="run-smoke", timeout_seconds=60)["run"]
        running = (str(workspace), prepared["task_id"], started["run_id"])
        again = service.run_plan(*args, request_id="run-smoke", timeout_seconds=60)["run"]
        assert again["run_id"] == started["run_id"]
        report["checks"]["run_replay"] = "PASS"
        try:
            service.run_plan(*args, request_id="run-smoke", timeout_seconds=61)
        except PlanFlowError as exc:
            assert exc.code == "REQUEST_CONFLICT", exc.code
        else:
            raise AssertionError("changed request replay accepted")
        report["checks"]["run_conflict"] = "PASS"
        deadline = time.monotonic() + 80
        while time.monotonic() < deadline:
            state = service.get_run(*running)["run"]
            if state["status"] not in {"starting", "running"}:
                break
            time.sleep(0.25)
        report["run"] = state
        assert state["status"] == "succeeded", state
        output_root = Path(state["recipe_output"]).parent
        decisions = [json.loads(line) for line in (output_root / "audit/decisions.jsonl").read_text().splitlines()]
        by_source = {str(Path(row["images"][0]).resolve()): row for row in rows}
        assert len(decisions) == 3 and len({d["source"] for d in decisions}) == 3
        results = [{"candidate_id": by_source[d["source"]]["candidate_id"], "asset_version": "v1",
                    "verdict": "pass" if d["kept"] else "fail", "checks": d["checks"]} for d in decisions]
        assert {r["candidate_id"] for r in results if r["verdict"] == "pass"} == {"c1", "c2"}
        report["candidate_results"] = results
        report["manifest"] = json.loads(Path(state["result_manifest_path"]).read_text())
        report["checks"]["real_image_filter_and_full_coverage"] = "PASS"
        report["status"] = "PASS"
    except Exception as exc:
        report.update(status="FAIL", error=str(exc), error_code=getattr(exc, "code", None),
                      details=getattr(exc, "details", None))
        raise
    finally:
        if running:
            try:
                if service.get_run(*running)["run"]["status"] in {"starting", "running"}:
                    service.cancel_run(*running)
            except Exception as exc:
                report.update(status="FAIL", cleanup_error=str(exc))
        (workspace / "smoke-report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        print(json.dumps({"status": report["status"], "checks": report["checks"],
                          "report": str(workspace / "smoke-report.json")}), flush=True)


if __name__ == "__main__":
    main()
