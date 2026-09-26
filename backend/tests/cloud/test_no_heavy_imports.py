"""Torch stays out of the serving path's import graph (A4).

Asserted on `sys.modules`, not on "is it installed": that is the only form
that still means something when the suite is run inside `.venv-yolo`, where
torch *is* installed and a stray top-level import would be invisible.
"""

from __future__ import annotations

import sys
from typing import Any, Callable

from .conftest import Harness, cloud_harness

HEAVY = frozenset({"torch", "torchvision", "ultralytics"})


def _loaded() -> set[str]:
    return {name for name in HEAVY if name in sys.modules}


def test_importing_the_app_does_not_import_torch_or_ultralytics() -> None:
    import cane.cloud.app  # noqa: F401
    import cane.cloud.detector.factory  # noqa: F401
    import cane.cloud.service  # noqa: F401

    assert _loaded() == set()


async def test_full_request_does_not_import_torch(
    app_with_fake: Harness, meta_json: Callable[..., str], six_jpegs: Callable[..., Any]
) -> None:
    response = await app_with_fake.client.post(
        "/infer", data={"meta": meta_json()}, files=list(six_jpegs(640))
    )
    assert response.status_code == 200
    assert _loaded() == set()


async def test_the_fake_detector_path_builds_without_ultralytics() -> None:
    from cane.cloud.detector.factory import build_detector
    from cane.cloud.settings import CloudSettings

    assert build_detector(CloudSettings()).name == "fake"
    assert _loaded() == set()
    async with cloud_harness() as harness:
        assert (await harness.client.get("/warmup")).status_code == 200
    assert _loaded() == set()
