"""The local fallback must not pay for a depth head it may never use.

Step 22 measured the three forward crops at ~92 ms on this laptop's MPS, and
the depth factory's posture table turns depth *on* for the ``ultralytics``
detector -- which is exactly what the bridge's local backend loads. But plan
Sec 4.2 forbids sending a ``steer`` frame in local fallback at all, so that
92 ms would buy a ``GapProfile`` that step 23 is required to throw away,
while pushing local inference from ~170 ms to ~260 ms and eating the
cyclist's 500 ms TTL margin.

So the local settings say ``depth_enabled=False`` and the local service is
built with no head. Both are asserted, because they fail differently: the
settings are what a future caller that routes through ``build_depth_head``
would read, and the service is what runs today.
"""

from __future__ import annotations

from typing import Callable

from cane.bridge.backends import LocalBackend, local_settings
from cane.cloud.depth.factory import OFF, resolve_backend
from cane.cloud.api_models import InferenceMeta

LOCAL_DETECTORS = ("fake", "ultralytics")


def test_the_local_settings_turn_the_depth_head_off() -> None:
    for detector in LOCAL_DETECTORS:
        settings = local_settings(detector)
        assert settings.depth_enabled is False, detector
        # The posture table would answer "torch" for ultralytics; disabled wins.
        assert resolve_backend(settings) == OFF, detector


def test_the_local_service_is_built_with_no_depth_head() -> None:
    backend = LocalBackend.build(local_settings("fake"), warm_up_px=None)

    assert backend.service.depth is None


async def test_the_local_backend_answers_with_no_gap_profiles(
    crop_jpegs: Callable[..., tuple[bytes, ...]],
    meta: Callable[..., InferenceMeta],
) -> None:
    """The other half of "no steer in local": there is no profile to steer from."""
    backend = LocalBackend.build(local_settings("fake"), warm_up_px=None)

    response = await backend.infer(meta(), crop_jpegs())

    assert response.gap_profiles == ()
