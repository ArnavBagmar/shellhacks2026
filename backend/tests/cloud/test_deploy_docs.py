"""The tier-2 deploy artefacts, checked as *text*.

None of this can be executed here -- no GPU, no gcloud, no project -- so the
only defence against a fresh-boot surprise is reading the files against each
other. The one that bit: the persistent disk is bind-mounted at `/models`, so
anything baked into the image at `/models` is hidden the moment the container
starts, and ultralytics silently downloads a replacement from GitHub.
"""

from __future__ import annotations

import pathlib
import re

import pytest

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[2]
DOCKERFILE = BACKEND_ROOT / "docker" / "Dockerfile"
STARTUP = BACKEND_ROOT / "deploy" / "startup.sh"
README = BACKEND_ROOT / "deploy" / "README.md"

UNVERIFIED_LINE = (
    "# UNVERIFIED as of 2026-09-26: no GPU, no gcloud, no GCP project. "
    "Nothing here has been executed."
)
TIER_2_SCRIPTS = (
    "docker/Dockerfile",
    "docker/entrypoint.sh",
    "deploy/create_l4_vm.sh",
    "deploy/startup.sh",
    "deploy/warmup_loop.sh",
)


def _env(name: str) -> str:
    match = re.search(rf"^\s*{name}=(\S+)", DOCKERFILE.read_text(encoding="utf-8"), re.MULTILINE)
    assert match is not None, f"{name} is not set in the Dockerfile"
    return match.group(1)


def _container_mount() -> str:
    match = re.search(r'-v "\$MOUNT":(\S+)', STARTUP.read_text(encoding="utf-8"))
    assert match is not None, "startup.sh no longer bind-mounts the persistent disk"
    return match.group(1).rstrip("/")


@pytest.mark.parametrize("relative", TIER_2_SCRIPTS)
def test_every_tier_2_script_carries_the_unverified_header(relative: str) -> None:
    """A1: an unexecuted artefact that does not say so reads as a deliverable."""
    lines = (BACKEND_ROOT / relative).read_text(encoding="utf-8").splitlines()
    assert UNVERIFIED_LINE in lines[:2]


def test_the_baked_weights_survive_the_persistent_mount() -> None:
    """On a fresh disk the bind mount hides everything baked under it, so a
    weights path inside the mount means the container downloads yolo11s.pt
    from GitHub on first boot -- offline, or on a rate-limited venue wifi,
    that is a dead demo with no error until the first inference.
    """
    mount = _container_mount()
    weights = _env("CANE_WEIGHTS_PATH")
    assert not weights.startswith(f"{mount}/"), (
        f"{weights} is inside the bind mount {mount} and is hidden on a fresh disk"
    )
    assert f'YOLO("{weights}")' in DOCKERFILE.read_text(encoding="utf-8") or (
        f"YOLO('{weights}')" in DOCKERFILE.read_text(encoding="utf-8")
    ), "the baked weights and CANE_WEIGHTS_PATH must be the same file"


def test_the_ultralytics_config_dir_is_also_outside_the_mount() -> None:
    """`yolo settings sync=False` is run at build time; under the mount that
    file is hidden too and ultralytics re-creates a default that phones home.
    """
    assert not _env("YOLO_CONFIG_DIR").startswith(f"{_container_mount()}/")


def test_the_engine_cache_still_lives_on_the_persistent_disk() -> None:
    """The engine is the thing that *must* persist: it is GPU-specific and
    costs 3-5 minutes to rebuild. Only it and its sidecar belong in /models.
    """
    assert _env("CANE_ENGINE_PATH").startswith(f"{_container_mount()}/")


def test_the_readme_describes_the_mount_as_the_engine_cache_only() -> None:
    text = README.read_text(encoding="utf-8")
    assert _env("CANE_WEIGHTS_PATH") in text
    assert "UNVERIFIED as of 2026-09-26" in text
