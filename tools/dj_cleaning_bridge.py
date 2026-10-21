"""Trusted JSON bridge run by DJ Python 3.10, not an externally exposed endpoint.

Only a fixed image-width recipe is allowed. Business ownership/approval is
checked by CleaningService before invoking this process. No arbitrary scripts.
"""
import hashlib
import json
import math
from pathlib import Path
import sys


def sha(content):
    return "sha256:" + hashlib.sha256(content).hexdigest()


def execute(request):
    from filelock import FileLock
    from data_juicer.tools.plan_flow.common import PlanFlowError, write_json_atomic, write_text_atomic
    from data_juicer.tools.plan_flow.service import PlanFlowService

    workspace = Path(request["workspace"]).resolve(strict=True)
    job, action = request["job"], request["action"]
    if job["recipe_ref"] != "image-width@1" or type(job["min_width"]) is not int or not 1 <= job["min_width"] <= 8192:
        raise ValueError("recipe is not allowlisted")
    if not 1 <= len(job["candidates"]) <= 100 or job["deadline_seconds"] != 60:
        raise ValueError("job limits are invalid")
    fingerprint = sha(json.dumps(job, sort_keys=True, separators=(",", ":")).encode())
    # Stable operation identity must not fork into a new job when its payload changes.
    identity = {name: job["binding"][name] for name in ("tenant_id", "project_id", "operation_id")}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    state_root = workspace / ".bridge"
    state_root.mkdir(exist_ok=True)
    state_path = state_root / (key + ".json")
    service = PlanFlowService.native()
    with FileLock(str(state_path) + ".lock", timeout=25):
        record = json.loads(state_path.read_text()) if state_path.exists() else {"fingerprint": fingerprint}
        if record["fingerprint"] != fingerprint:
            raise PlanFlowError("REQUEST_CONFLICT", "operation binding or inputs changed")
        if action == "cancel":
            record["cancelled"] = True
            write_json_atomic(state_path, record)  # before external cancellation; prevents late dispatch
            if "backend" in record:
                b = record["backend"]
                service.cancel_run(str(workspace), b["dj_task_id"], b["dj_run_id"])
            return {"status": "cancelled", "binding": job["binding"]}
        if record.get("cancelled"):
            raise PlanFlowError("OPERATION_CANCELLED", "operation has been cancelled")
        if action == "submit":
            if "backend" in record:
                return record["backend"]
            write_json_atomic(state_path, record)
            folder = workspace / "inputs" / key
            folder.mkdir(parents=True, exist_ok=True)
            rows, sources = [], {}
            seen = set()
            for index, candidate in enumerate(job["candidates"]):
                if candidate["candidate_id"] in seen:
                    raise ValueError("duplicate candidate id")
                seen.add(candidate["candidate_id"])
                with Path(candidate["source_path"]).open("rb") as handle:
                    content = handle.read(10_000_001)
                if len(content) > 10_000_000 or sha(content) != candidate["content_digest"]:
                    raise PlanFlowError("INPUT_SNAPSHOT_CHANGED", "input bytes changed")
                target = folder / f"{index}.png"
                if target.exists():
                    if sha(target.read_bytes()) != candidate["content_digest"]:
                        raise PlanFlowError("INPUT_SNAPSHOT_CHANGED", "frozen input changed")
                else:
                    with target.open("xb") as handle:
                        handle.write(content)
                sources[str(target)] = candidate
                rows.append({"text": "<__dj__image>", "images": [str(target)],
                             "candidate_id": candidate["candidate_id"], "asset_version": candidate["asset_version"]})
            dataset = folder / "input.jsonl"
            write_text_atomic(dataset, "".join(json.dumps(row) + "\n" for row in rows))
            plan = {"user_intent": "Approved image-width cleaning", "modality": "image",
                    "approval_required": True, "risk_notes": [],
                    "recipe": {"dataset_path": str(dataset), "export_path": "result.jsonl", "np": 1,
                               "executor_type": "default", "use_cache": False,
                               "process": [{"image_shape_filter": {"min_width": job["min_width"]}}]},
                    "postprocess": [{"kind": "image_audit", "copy_kept": False,
                                     "rules": [{"id": "width", "field": "image_width", "min": job["min_width"]}]}]}
            prepared = service.prepare_plan(str(workspace), plan, request_id="prepare-" + key)
            args = (str(workspace), prepared["task_id"], prepared["plan_version"])
            # This is execution of the exact server-held proposal already confirmed by the owner.
            service.approve_plan(*args, prepared["content_hash"], note=job["binding"]["plan_digest"])
            run = service.run_plan(*args, request_id="run-" + key, timeout_seconds=job["deadline_seconds"])["run"]
            record.update(backend={"dj_task_id": prepared["task_id"], "dj_plan_version": prepared["plan_version"],
                                   "dj_run_id": run["run_id"]}, sources=sources)
            write_json_atomic(state_path, record)
            return record["backend"]
        if action != "poll" or "backend" not in record:
            raise ValueError("unknown action or unsubmitted job")
        b = record["backend"]
        state = service.get_run(str(workspace), b["dj_task_id"], b["dj_run_id"])["run"]
        if state["status"] in {"starting", "running"}:
            return {"status": "running", "binding": job["binding"]}
        base = {"binding": job["binding"], "backend": b}
        try:
            if state["status"] != "succeeded":
                raise ValueError("EXECUTION_FAILED")
            output = Path(state["output_dir"]).resolve()
            manifest_path = Path(state["result_manifest_path"]).resolve()
            if not output.is_relative_to(workspace) or not manifest_path.is_relative_to(output):
                raise ValueError("OUTPUT_PATH_INVALID")
            manifest = json.loads(manifest_path.read_text())
            if manifest["run_id"] != b["dj_run_id"] or manifest["status"] != "succeeded":
                raise ValueError("OUTPUT_BINDING_INVALID")
            entries = [e for e in manifest["outputs"] if e["path"] == "audit/decisions.jsonl"]
            decisions_path = output / "audit/decisions.jsonl"
            if (len(entries) != 1 or decisions_path.is_symlink()
                    or not decisions_path.resolve().is_relative_to(output)):
                raise ValueError("OUTPUT_INTEGRITY_INVALID")
            data = decisions_path.read_bytes()
            if sha(data) != entries[0]["sha256"] or len(data) != entries[0]["size_bytes"]:
                raise ValueError("OUTPUT_INTEGRITY_INVALID")
            decisions = [json.loads(line) for line in data.splitlines()]
            if len(decisions) != len(record["sources"]):
                raise ValueError("RESULT_COVERAGE_MISMATCH")
            results, seen = [], set()
            for decision in decisions:
                source = decision["source"]
                if source in seen or source not in record["sources"] or decision.get("image_index") != 0:
                    raise ValueError("RESULT_COVERAGE_MISMATCH")
                seen.add(source)
                candidate = record["sources"][source]
                width = decision.get("scores", {}).get("image_width")
                if isinstance(width, bool) or not isinstance(width, (float, int)) or not math.isfinite(width):
                    verdict, reason = "unknown", "MISSING_SCORE"
                else:
                    verdict = "pass" if width >= job["min_width"] else "fail"
                    expected = "passed" if verdict == "pass" else "failed"
                    if decision.get("checks") != {"width": expected} or decision.get("kept") is not (verdict == "pass"):
                        raise ValueError("AUDIT_INCONSISTENT")
                    reason = None if verdict == "pass" else "WIDTH_TOO_SMALL"
                results.append({"candidate_id": candidate["candidate_id"], "asset_version": candidate["asset_version"],
                                "verdict": verdict, "reason_code": reason,
                                "evidence_ref": f"dj:{b['dj_run_id']}:audit:{decision['image_id']}"})
            return {**base, "status": "succeeded", "results": results,
                    "evidence_digest": sha(data), "error": None}
        except (ValueError, KeyError, TypeError, OSError) as exc:
            return {**base, "status": "failed", "error": "CLEANING_OUTPUT_INVALID_OR_FAILED",
                    "results": [{"candidate_id": c["candidate_id"], "asset_version": c["asset_version"],
                                 "verdict": "unknown", "reason_code": "EXECUTION_OR_EVIDENCE_FAILED",
                                 "evidence_ref": None} for c in job["candidates"]]}


def main():
    try:
        result = execute(json.load(sys.stdin))
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"ok": False, "code": getattr(exc, "code", type(exc).__name__)}))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
