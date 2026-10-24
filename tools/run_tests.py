"""Run repository tests without requiring callers to assemble ``PYTHONPATH``."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"


def _install_import_paths() -> None:
    """Give tests the same imports from any caller working directory."""
    required = (ROOT / "src", ROOT, TESTS)
    required_values = tuple(map(str, required))
    for path in reversed(required):
        value = str(path)
        if value in sys.path:
            sys.path.remove(value)
        sys.path.insert(0, value)
    inherited = [value for value in os.environ.get("PYTHONPATH", "").split(os.pathsep) if value]
    os.environ["PYTHONPATH"] = os.pathsep.join(
        list(required_values) + [value for value in inherited if value not in required_values]
    )
    sys.dont_write_bytecode = True


def _module_from_file(value: str) -> str:
    path = Path(value)
    if not path.is_absolute():
        cwd_candidate = (Path.cwd() / path).resolve()
        path = cwd_candidate if cwd_candidate.is_file() else (ROOT / path).resolve()
    else:
        path = path.resolve()
    try:
        relative = path.relative_to(ROOT)
    except ValueError as exc:
        raise ValueError(f"test file must be inside repository: {value}") from exc
    if not path.is_file() or path.suffix != ".py" or not relative.parts or relative.parts[0] != "tests":
        raise ValueError(f"not a Python test file under tests/: {value}")
    return ".".join(relative.with_suffix("").parts)


def _normalise_target(value: str) -> str:
    target = value.strip()
    if not target:
        raise ValueError("test target cannot be empty")
    if target.endswith(".py") or "/" in target or "\\" in target:
        return _module_from_file(target)
    if target == "tests" or target.startswith("tests."):
        return target
    first = target.split(".", 1)[0]
    if first in {"core", "cleaning", "retrieval"} or (TESTS / f"{first}.py").is_file():
        return f"tests.{target}"
    return target


def _build_suite(targets: list[str]) -> unittest.TestSuite:
    loader = unittest.TestLoader()
    if not targets:
        return loader.discover(str(TESTS), pattern="test*.py")
    return loader.loadTestsFromNames([_normalise_target(target) for target in targets])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("targets", nargs="*", help="module/class/method name or a tests/*.py path")
    parser.add_argument("--all", action="store_true", help="explicitly run full test discovery")
    parser.add_argument("-v", "--verbose", action="store_true", help="show each test name")
    args = parser.parse_args(argv)
    if args.all and args.targets:
        parser.error("--all cannot be combined with explicit targets")

    _install_import_paths()
    try:
        suite = _build_suite([] if args.all else args.targets)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    result = unittest.TextTestRunner(verbosity=2 if args.verbose else 1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
