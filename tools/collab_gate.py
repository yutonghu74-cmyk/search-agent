"""Local collaboration gate. Run from the candidate repository root."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

POLICY = "docs/dev/ownership.json"


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True)
    if result.returncode:
        raise ValueError(result.stderr.decode(errors="replace").strip())
    return result.stdout.decode("utf-8", errors="surrogateescape")


def allowed(path: str, rules: list[str]) -> bool:
    # Prevent nested instructions or common credential files in allowed directories.
    parts = Path(path).parts
    if any(p in {"AGENTS.md", "AGENTS.override.md", ".env"} or p.startswith(".env.")
           for p in parts):
        return False
    return any(path.startswith(rule) if rule.endswith("/") else path == rule
               for rule in rules)


def changed_paths(root: Path, base: str) -> set[str]:
    paths = set()
    # Separate comparisons also catch staged edits reverted only in the worktree.
    for args in (
        ("diff", "--name-only", "--no-renames", "-z", base, "HEAD", "--"),
        ("diff", "--name-only", "--no-renames", "-z", "--cached", "HEAD", "--"),
        ("diff", "--name-only", "--no-renames", "-z", "--"),
        ("ls-files", "--others", "--exclude-standard", "-z"),
    ):
        paths.update(p for p in git(root, *args).split("\0") if p)
    return paths


def exact_path(value: str) -> bool:
    return (isinstance(value, str) and bool(value) and not value.startswith("/")
            and "\\" not in value and not any(c in value for c in "\n\r\0*?[]")
            and all(p not in ("", ".", "..") for p in value.split("/")))


def integration_scope(root: Path, base: str, policy: dict, task_path: str | None,
                      paths: set[str]) -> list[str]:
    if (not exact_path(task_path) or not task_path.startswith("docs/dev/tasks/")
            or not task_path.endswith(".json")):
        raise ValueError("integration 必须指定可信基线中的 JSON 任务卡 --task docs/dev/tasks/<名称>.json")
    git_dir = Path(git(root, "rev-parse", "--absolute-git-dir").strip()).resolve()
    common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()).resolve()
    if git_dir == common:
        raise ValueError("integration 必须使用独立 worktree")
    # Both policy and assignment come from the assigned trusted base, never HEAD.
    task = json.loads(git(root, "show", f"{base}:{task_path}"))
    fields = {"role", "branch", "source_base", "files", "acceptance"}
    entry = policy["roles"]["integration"]
    if (not isinstance(task, dict) or set(task) != fields or task["role"] != "integration"
            or task["branch"] != entry["branch"]
            or not isinstance(task["source_base"], str)
            or not re.fullmatch(r"[0-9a-f]{40}", task["source_base"])
            or not isinstance(task["acceptance"], str) or not task["acceptance"].strip()
            or not isinstance(task["files"], list) or not task["files"]
            or any(not exact_path(p) for p in task["files"])):
        raise ValueError("integration 任务卡结构/角色/分支/验收/精确文件清单无效")
    git(root, "merge-base", "--is-ancestor", task["source_base"], base)
    files = task["files"]
    if len(files) != len(set(files)) or "docs/dev/handoffs/integration.md" not in files:
        raise ValueError("任务卡必须含交接文件且不能重复授权")
    other_rules = [rule for name in ("core", "retrieval", "cleaning")
                   for rule in policy["roles"][name]["allow"]]
    if any(not allowed(p, entry["allow"]) or allowed(p, other_rules) for p in files):
        raise ValueError("任务卡不能授权其他角色或 A 范围外文件")
    if task_path in paths:
        raise ValueError("不得在候选修改中更改自己的已发布任务卡")
    for name in paths | {task_path, "docs/dev/handoffs/integration.md"}:
        if not exact_path(name):
            raise ValueError("无效文件路径")
        current = root
        for part in name.split("/"):
            current = current / part
            if current.is_symlink():
                raise ValueError(f"integration 不允许符号链接：{name}")
        # A staged/committed link can be hidden by a regular working-tree file.
        for args in (("ls-files", "--stage", "-z", "--", name),
                     ("ls-tree", "-z", "HEAD", "--", name)):
            for record in git(root, *args).split("\0"):
                if record and record.split(" ", 1)[0] in {"120000", "160000"}:
                    raise ValueError(f"integration 不允许链接或子模块：{name}")
    return files


def check_scope(root: Path, role: str, base_ref: str, task_path: str | None = None) -> None:
    top = Path(git(root, "rev-parse", "--show-toplevel").strip()).resolve()
    if top != root.resolve():
        raise ValueError("必须在候选仓库根目录运行")
    base = git(root, "rev-parse", "--verify", "--end-of-options", base_ref + "^{commit}").strip()
    git(root, "merge-base", "--is-ancestor", base, "HEAD")
    # Use the assigned baseline policy, not a worker's edited policy.
    policy = json.loads(git(root, "show", f"{base}:{POLICY}"))
    entry = policy["roles"][role]
    branch = git(root, "symbolic-ref", "--short", "HEAD").strip()
    if branch != entry["branch"]:
        raise ValueError(f"分支应为 {entry['branch']}，实际为 {branch}")
    paths = changed_paths(root, base)
    rules = entry["allow"]
    if role == "integration":
        rules = integration_scope(root, base, policy, task_path, paths)
    elif task_path is not None:
        raise ValueError("--task 仅用于 integration，不能替换现有角色范围")
    violations = sorted(p for p in paths if not allowed(p, rules))
    if violations:
        raise ValueError("越界修改（含删除/重命名/未跟踪文件）：\n" + "\n".join(violations))
    if not (root / f"docs/dev/handoffs/{role}.md").is_file():
        raise ValueError("缺少本角色交接文件")
    print(f"SCOPE PASS: role={role}, base={base}", flush=True)


def baseline(root: Path) -> int:
    policy = json.loads((root / POLICY).read_text())
    for name in ("AGENTS.md", policy["spec"], "docs/dev/parallel-plan.md",
                 "docs/dev/window-prompts.md", "docs/dev/handoff-template.md"):
        if not (root / name).is_file():
            raise ValueError(f"缺少协作基线文件：{name}")
    # unittest skips non-package subdirectories; fail instead of silently skipping.
    tests = root / "tests"
    if not list(tests.rglob("test*.py")):
        raise ValueError("没有可发现的测试")
    for test in tests.rglob("test*.py"):
        for parent in test.parents:
            if parent == tests:
                break
            if not (parent / "__init__.py").is_file():
                raise ValueError(f"测试目录需要 __init__.py：{parent.relative_to(root)}")
    env = dict(os.environ, PYTHONPATH=str(root / "src"), PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=root, env=env,
    )
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("baseline", help="基础文件与测试检查，不检查角色范围")
    check = commands.add_parser("check", help="角色边界检查，然后运行基础测试")
    check.add_argument("--role", required=True, choices=("core", "retrieval", "cleaning", "integration"))
    check.add_argument("--base", required=True, help="总控分派的可信基线提交 SHA")
    check.add_argument("--task", help="integration 在可信基线中已发布的 JSON 任务卡")
    args = parser.parse_args()
    try:
        root = Path.cwd()
        if args.command == "check":
            check_scope(root, args.role, args.base, args.task)
        code = baseline(root)
        print("LOCAL GATE PASS (不是全量业务验收)" if code == 0 else "LOCAL GATE FAIL")
        return code
    except (ValueError, OSError, KeyError) as exc:
        print(f"LOCAL GATE FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
