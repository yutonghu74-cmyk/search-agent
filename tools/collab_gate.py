"""Local collaboration gate. Run from the candidate repository root."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
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


def check_scope(root: Path, role: str, base_ref: str) -> None:
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
    violations = sorted(p for p in changed_paths(root, base) if not allowed(p, entry["allow"]))
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
    check.add_argument("--role", required=True, choices=("core", "retrieval", "cleaning"))
    check.add_argument("--base", required=True, help="总控分派的可信基线提交 SHA")
    args = parser.parse_args()
    try:
        root = Path.cwd()
        if args.command == "check":
            check_scope(root, args.role, args.base)
        code = baseline(root)
        print("LOCAL GATE PASS (不是全量业务验收)" if code == 0 else "LOCAL GATE FAIL")
        return code
    except (ValueError, OSError, KeyError) as exc:
        print(f"LOCAL GATE FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
