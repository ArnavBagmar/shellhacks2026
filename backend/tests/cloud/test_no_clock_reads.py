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


def test_the_guard_would_catch_a_clock_read() -> None:
    """A test that can never fail is not a test."""
    assert _offences(ast.parse("import time\nx = time.time()\n"))
    assert _offences(ast.parse("import datetime\n"))
    assert _offences(ast.parse("from cane.transport.wall_clock import now_ms\n"))
    assert not _offences(ast.parse("import time\nx = time.perf_counter()\n"))
