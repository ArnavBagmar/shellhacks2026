"""The timeless rule, proved statically (A9 rule 1).

The monkeypatch test in `test_app.py` only covers the lines one request
happens to execute. This one reads the source of every request-path module
and fails on any wall-clock call at all, executed or not.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import cane.cloud as cloud_package

# `smoke.py` and `export_engine.py` are operator tools, not the request path.
REQUEST_PATH_MODULES = (
    "api_models",
    "app",
    "class_map",
    "debug_dump",
    "motion",
    "service",
    "settings",
    "tracker",
    "detector/base",
    "detector/fake",
    "detector/factory",
    "detector/ultralytics_yolo",
)
FORBIDDEN_ATTRIBUTES = frozenset(
    {"time", "time_ns", "clock_gettime", "clock_gettime_ns", "now", "utcnow",
     "today", "fromtimestamp", "utcfromtimestamp"}
)
ALLOWED_TIME_ATTRIBUTES = frozenset({"perf_counter", "perf_counter_ns", "monotonic", "monotonic_ns", "sleep"})
FORBIDDEN_MODULES = frozenset({"datetime", "calendar", "cane.transport.wall_clock"})
CLOUD_ROOT = pathlib.Path(cloud_package.__file__).parent


def _source(relative: str) -> tuple[pathlib.Path, str]:
    path = CLOUD_ROOT / f"{relative}.py"
    assert path.is_file(), f"{path} is listed in REQUEST_PATH_MODULES but does not exist"
    return path, path.read_text(encoding="utf-8")


def _offences(tree: ast.AST) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [f"import {a.name}" for a in node.names if a.name in FORBIDDEN_MODULES]
        elif isinstance(node, ast.ImportFrom) and node.module in FORBIDDEN_MODULES:
            found.append(f"from {node.module} import ...")
        elif isinstance(node, ast.Attribute):
            root = node.value
            is_time_module = isinstance(root, ast.Name) and root.id == "time"
            if node.attr in FORBIDDEN_ATTRIBUTES and not (
                is_time_module and node.attr in ALLOWED_TIME_ATTRIBUTES
            ):
                found.append(f"line {node.lineno}: .{node.attr}")
    return found


@pytest.mark.parametrize("relative", REQUEST_PATH_MODULES)
def test_request_path_modules_contain_no_wall_clock_reads(relative: str) -> None:
    path, source = _source(relative)
    offences = _offences(ast.parse(source, filename=str(path)))
    assert not offences, f"{path.name} reads a wall clock: {offences}"


ENTRY_MODULE = "app"
# Operator tools, reachable only from a shell, never from a request.
EXEMPT_MODULES = frozenset({"smoke", "export_engine"})


def _cloud_imports(relative: str) -> set[str]:
    """The `cane.cloud.*` modules this one pulls in, lazy imports included:
    the AST sees a function-scoped import exactly as well as a top-level one.
    """
    _, source = _source(relative)
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            if name.startswith("cane.cloud.") and (CLOUD_ROOT / f"{name[11:].replace('.', '/')}.py").is_file():
                found.add(name[11:].replace(".", "/"))
    return found


def _reachable_from_the_request_path() -> set[str]:
    seen: set[str] = set()
    pending = [ENTRY_MODULE]
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        pending.extend(_cloud_imports(current))
    return seen - EXEMPT_MODULES


def test_every_module_the_request_path_imports_is_guarded() -> None:
    """The list above is hand-written, so it rots the moment someone adds a
    module to the request path -- `debug_dump.py` ran inside `/infer` for a
    whole step without being guarded. This walks the real import graph.
    """
    unguarded = sorted(_reachable_from_the_request_path() - set(REQUEST_PATH_MODULES))
    assert not unguarded, f"reachable from /infer but not clock-guarded: {unguarded}"


def test_the_guard_would_catch_a_clock_read() -> None:
    """A test that can never fail is not a test."""
    assert _offences(ast.parse("import time\nx = time.time()\n"))
    assert _offences(ast.parse("import datetime\n"))
    assert _offences(ast.parse("from cane.transport.wall_clock import now_ms\n"))
    assert not _offences(ast.parse("import time\nx = time.perf_counter()\n"))
