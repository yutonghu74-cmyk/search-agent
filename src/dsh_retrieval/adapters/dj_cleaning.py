"""Python-version-isolated adapter to the trusted DJ bridge, without shell execution."""
import json
import os
from pathlib import Path
import subprocess

from ..errors import ServiceError


class DJCleaningExecutor:
    def __init__(self, python, source_root, workspace, bridge_script, *, timeout=30, ephemeral_inputs=False):
        self.python = str(Path(python).absolute())  # preserve virtualenv path, do not resolve symlink
        self.source_root = str(Path(source_root).resolve())
        self.workspace = str(Path(workspace).resolve())
        self.bridge_script = str(Path(bridge_script).resolve())
        self.timeout = timeout
        if type(ephemeral_inputs) is not bool:
            raise ValueError("ephemeral_inputs must be bool")
        self.ephemeral_inputs = ephemeral_inputs
        self.captures_inputs_on_submit = True
        self.path_independent_identity = ephemeral_inputs

    def _call(self, action, operation):
        # Do not hand business-service credentials to the cleaning subprocess.
        environment = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "TMPDIR", "LANG") if key in os.environ}
        environment.update(PYTHONPATH=self.source_root, PYTHONDONTWRITEBYTECODE="1",
                           HF_HUB_OFFLINE="1", HF_DATASETS_OFFLINE="1", UV_OFFLINE="1",
                           DJ_PLAN_FLOW_SINGLE_USER="1", HF_HOME=str(Path(self.workspace) / "hf"),
                           DSH_USER_DATA_ROOT=str(Path(self.workspace) / "users"))
        try:
            result = subprocess.run([self.python, self.bridge_script],
                                    input=json.dumps({"action": action, "workspace": self.workspace, "job": operation.job(),
                                                      "ephemeral_inputs": self.ephemeral_inputs}),
                                    text=True, capture_output=True, env=environment, timeout=self.timeout,
                                    cwd=self.workspace)
            payload = json.loads(result.stdout)
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            raise ServiceError("CLEANING_DEPENDENCY_UNAVAILABLE", "cleaning transport failed; retry the same operation", 503,
                               retryable=True) from exc
        if result.returncode or not payload.get("ok"):
            raise ServiceError("CLEANING_EXECUTOR_ERROR", "cleaning executor rejected the operation", 503,
                               retryable=True, details={"executor_code": payload.get("code", "UNKNOWN")})
        return payload["result"]

    def submit(self, operation):
        return self._call("submit", operation)

    def poll(self, operation):
        return self._call("poll", operation)

    def cancel(self, operation):
        return self._call("cancel", operation)
