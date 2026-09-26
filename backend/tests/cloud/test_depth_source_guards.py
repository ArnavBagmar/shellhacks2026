"""Source-level guards for step 22: the Sec 1.1 trap, and honest labels.

The behavioural test that a constant depth offset changes the columns lives in
`test_depth_profile.py`. This file is the belt: `normalize_depth` must not
appear anywhere the request path can reach, so nobody can reintroduce
per-frame min-max normalisation in a helper the behavioural test happens not
to cover.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import cane.cloud as cloud_package

CLOUD_ROOT = pathlib.Path(cloud_package.__file__).parent
DEPTH_ROOT = CLOUD_ROOT / "depth"
# Same wording as step 7's tier-2 artefacts (see tests/cloud/test_deploy_docs.py):
# an unexecuted artefact that does not say so reads as a deliverable.
UNVERIFIED_PREFIX = "# UNVERIFIED as of 2026-09-26: not executed."
# The L4 halves of step 22. Neither can be run from a Mac: no CUDA, no
# TensorRT, and the PyPI tensorrt sdist must never be built here (A4).
UNVERIFIED_DEPTH_MODULES = ("depth/trt_depth", "depth/export_depth_engine")
FORBIDDEN_CALLS = frozenset({"normalize_depth"})


def _cloud_sources() -> list[pathlib.Path]:
    return sorted(CLOUD_ROOT.rglob("*.py"))


def test_the_depth_package_exists_and_is_made_of_small_modules() -> None:
    modules = sorted(path.name for path in DEPTH_ROOT.glob("*.py"))
    assert "__init__.py" in modules
    for path in DEPTH_ROOT.glob("*.py"):
        lines = len(path.read_text(encoding="utf-8").splitlines())
        assert lines <= 400, f"{path.name} is {lines} lines"


@pytest.mark.parametrize("relative", UNVERIFIED_DEPTH_MODULES)
def test_the_l4_only_modules_carry_the_unverified_header(relative: str) -> None:
    """Plan Sec 4.5's 25 ms target is on the L4, and nothing on this Mac can
    measure or even build it. The file has to say so in its first line.
    """
    lines = (CLOUD_ROOT / f"{relative}.py").read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith(UNVERIFIED_PREFIX), relative


def test_no_module_claims_a_measured_l4_number() -> None:
    """A number nobody measured is worse than no number: step 25 measures it."""
    for path in DEPTH_ROOT.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "ms on the L4" not in text, path.name
        assert "Verified on an NVIDIA L4" not in text, path.name


def test_normalize_depth_is_absent_from_the_whole_cloud_package() -> None:
    """Plan Sec 1.1: per-frame min-max normalisation would make an empty
    corridor score identically to a wall, because the nearest thing in frame
    always normalises to 1.0. `benchmarks.depth_model.normalize_depth` is that
    function; it belongs to the preview UI and must never reach the profile.
    """
    offenders = []
    for path in _cloud_sources():
        source = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source, filename=str(path))):
            if isinstance(node, ast.ImportFrom):
                offenders += [
                    f"{path.name}:{node.lineno}" for a in node.names if a.name in FORBIDDEN_CALLS
                ]
            elif isinstance(node, ast.Name) and node.id in FORBIDDEN_CALLS:
                offenders.append(f"{path.name}:{node.lineno}")
            elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_CALLS:
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, f"per-frame normalisation reached the cloud: {offenders}"


def test_the_guard_would_catch_a_normalisation_call() -> None:
    """A test that can never fail is not a test."""
    tree = ast.parse("from cane.benchmarks.depth_model import normalize_depth\n")
    names = [a.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for a in node.names]
    assert set(names) & FORBIDDEN_CALLS


def test_only_the_constants_module_names_a_checkpoint_or_engine_path() -> None:
    """No hardcoded paths in the logic: every filesystem location is a settings
    field with a default in one place.
    """
    for path in DEPTH_ROOT.glob("*.py"):
        if path.name == "constants.py":
            continue
        text = path.read_text(encoding="utf-8")
        for literal in ("/models/", "third_party", "checkpoints/"):
            assert literal not in text, f"{path.name} hardcodes {literal}"


def test_the_depth_path_imports_the_bridges_focal_length_and_not_a_copy() -> None:
    """Plan Sec 2.3 anchors on `dewarp.focal_px`. A second implementation of
    the same pinhole formula is how the band and the crops drift apart.
    """
    imported = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "cane.bridge.dewarp"
        and any(alias.name == "focal_px" for alias in node.names)
        for path in DEPTH_ROOT.glob("*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
    )
    assert imported
    for path in DEPTH_ROOT.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "math.tan" not in text, f"{path.name} recomputes the focal length"
