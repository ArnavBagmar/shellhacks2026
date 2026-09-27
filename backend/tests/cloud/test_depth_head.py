"""The depth head's failure policy: absence, never a fake (plan Sec 4.2, step 8 S1).

Acceptance criterion 4 lives here. A depth model that cannot load, a crop the
model chokes on, a map that comes back the wrong shape -- every one of them
must cost the frame its `GapProfile` and cost the *detections* nothing. A
zero-filled profile would read downstream as "the floor is 0.0 away".
"""

from __future__ import annotations

from typing import Any, Sequence

import numpy as np
import pytest
from numpy.typing import NDArray

from cane.cloud.api_models import COLUMNS_PER_CROP, GapProfile, InferenceResponse
from cane.cloud.depth.base import DepthEstimator
from cane.cloud.depth.constants import DEPTH_CROPS
from cane.cloud.depth.fake import ScriptedDepth
from cane.cloud.depth.head import DepthHead

from .depth_synthetic import CROP_PX, ideal_ground, wall_on_the_left

MARKER = tuple(range(6))


def images(crop_px: int = CROP_PX) -> tuple[NDArray[np.uint8], ...]:
    """Six crops, each flood-filled with its own index so the head cannot be
    caught handing crop 2's pixels to crop 5's profile.
    """
    return tuple(
        np.full((crop_px, crop_px, 3), value, dtype=np.uint8) for value in MARKER
    )


class _Raising:
    """Every call fails, the way a torch import failure or an OOM does."""

    name = "raising"
    engine_ready = False
    input_size = 280

    def __init__(self, message: str = "no depth model here") -> None:
        self.message = message
        self.calls = 0

    def predict(self, image: NDArray[np.uint8]) -> NDArray[np.float32]:
        self.calls += 1
        raise RuntimeError(self.message)


class _WrongShape:
    name = "wrong-shape"
    engine_ready = True
    input_size = 280

    def predict(self, image: NDArray[np.uint8]) -> NDArray[np.float32]:
        return np.zeros((7, 9), dtype=np.float32)


class _OneBadCrop:
    """Crop 0 fails; 5 and 1 are fine. Per plan Sec 3.1 ruling 3 a crop whose
    depth failed is simply absent, and the others still ship.
    """

    name = "one-bad-crop"
    engine_ready = True
    input_size = 280

    def __init__(self) -> None:
        self.seen: list[int] = []

    def predict(self, image: NDArray[np.uint8]) -> NDArray[np.float32]:
        marker = int(image[0, 0, 0])
        self.seen.append(marker)
        if marker == 0:
            raise RuntimeError("crop 0 exploded")
        return ideal_ground(base=wall_on_the_left(int(image.shape[0])), from_row=542)


def head(load: Any, **kwargs: Any) -> DepthHead:
    return DepthHead(load, label="test", **kwargs)


def test_the_scripted_fake_satisfies_the_estimator_protocol() -> None:
    assert isinstance(ScriptedDepth(), DepthEstimator)


def test_the_head_profiles_exactly_the_three_forward_crops_in_order() -> None:
    """Plan Sec 1.2: depth on six crops costs twice what steering needs, and
    you steer where you walk. C5/C0/C1 is -60/0/+60.
    """
    estimator = ScriptedDepth()
    result = head(lambda: estimator).profiles(images())
    assert tuple(p.crop_index for p in result.profiles) == DEPTH_CROPS == (5, 0, 1)
    assert [int(image[0, 0, 0]) for image in estimator.seen] == [5, 0, 1]
    assert estimator.calls == 3
    assert all(isinstance(p, GapProfile) for p in result.profiles)
    assert result.ready is True
    assert result.depth_ms >= 0.0


def test_the_head_never_emits_two_profiles_for_one_crop() -> None:
    """Acceptance criterion 6, at the producer. The response validator is the
    backstop; this is the invariant that keeps it from ever firing.
    """
    result = head(lambda: ScriptedDepth()).profiles(images())
    indices = [p.crop_index for p in result.profiles]
    assert len(set(indices)) == len(indices)
    # The response the service will build from them must validate.
    InferenceResponse(
        schema=1, t_capture_ms=1_790_000_000_000, inference_ms=1.0,
        gap_profiles=result.profiles,
    )


def test_a_loader_that_raises_yields_no_profiles_and_no_exception() -> None:
    """Acceptance criterion 4. The request must survive; the profile must not
    exist. Not one zero-filled `GapProfile`, not a 503.
    """
    attempts = {"count": 0}

    def load() -> DepthEstimator:
        attempts["count"] += 1
        raise ValueError("Small checkpoint missing: /nowhere/depth.pth")

    depth_head = head(load)
    result = depth_head.profiles(images())
    assert result.profiles == ()
    assert result.ready is False
    assert depth_head.ready is False
    assert attempts["count"] == 1


def test_a_failed_load_is_attempted_exactly_once_ever() -> None:
    """At 3 fps a retrying loader would re-import torch and re-read a missing
    checkpoint three times a second for the whole demo.
    """
    attempts = {"count": 0}

    def load() -> DepthEstimator:
        attempts["count"] += 1
        raise RuntimeError("boom")

    depth_head = head(load)
    for _ in range(5):
        assert depth_head.profiles(images()).profiles == ()
    assert attempts["count"] == 1


def test_a_model_that_raises_on_every_crop_yields_no_profiles() -> None:
    estimator = _Raising()
    result = head(lambda: estimator).profiles(images())
    assert result.profiles == ()
    assert estimator.calls == len(DEPTH_CROPS)


def test_one_bad_crop_does_not_take_the_other_two_with_it() -> None:
    estimator = _OneBadCrop()
    result = head(lambda: estimator).profiles(images())
    assert tuple(p.crop_index for p in result.profiles) == (5, 1)
    assert estimator.seen == [5, 0, 1]


def test_a_depth_map_of_the_wrong_shape_is_refused() -> None:
    """`predict_depth` already raises when the map does not match the frame;
    this is the belt for a TensorRT path that resizes to the engine's shape
    and forgets to resize back.
    """
    assert head(lambda: _WrongShape()).profiles(images()).profiles == ()


def test_a_profile_is_never_zero_filled() -> None:
    """The failure mode plan Sec 4.2 names by hand: sixteen zeros is a wall at
    the wearer's feet in every direction, and it validates perfectly.
    """
    for load in (lambda: _Raising(), lambda: _WrongShape()):
        for wire in head(load).profiles(images()).profiles:
            assert wire.columns != (0.0,) * COLUMNS_PER_CROP


def test_fewer_crops_than_the_ring_expects_is_survivable() -> None:
    """A malformed request never reaches here (the app requires ``CROP_COUNT`` parts),
    but the local-fallback caller is in-process and could pass anything.
    """
    estimator = ScriptedDepth()
    result = head(lambda: estimator).profiles(images()[:2])
    assert tuple(p.crop_index for p in result.profiles) == (0, 1)
    assert head(lambda: estimator).profiles(()).profiles == ()


def test_warmup_loads_the_model_and_reports_a_duration() -> None:
    estimator = ScriptedDepth()
    depth_head = head(lambda: estimator)
    warmed = depth_head.warm(CROP_PX)
    assert warmed is not None and warmed >= 0.0
    assert depth_head.ready is True
    assert estimator.calls >= 1
    # The frame after the warm-up is not the warm-up's frame.
    estimator.calls = 0
    assert len(depth_head.profiles(images()).profiles) == len(DEPTH_CROPS)
    assert estimator.calls == len(DEPTH_CROPS)


def test_warmup_of_a_broken_model_reports_absence_not_a_crash() -> None:
    def load() -> DepthEstimator:
        raise RuntimeError("no engine on this box")

    depth_head = head(load)
    assert depth_head.warm(CROP_PX) is None
    assert depth_head.ready is False


def test_the_head_reports_its_label_for_healthz_and_warmup() -> None:
    depth_head = DepthHead(lambda: ScriptedDepth(), label="torch")
    assert depth_head.label == "torch"


def test_a_stricter_residual_threshold_only_changes_anchoring() -> None:
    """The threshold is a knob (plan Sec 2.3 names no value), so it must be
    plumbed, not inlined -- and it must move `anchored`, never the columns.
    """
    ground = ideal_ground(base=wall_on_the_left(), from_row=542)
    maps: Sequence[NDArray[np.float32]] = (ground, ground, ground)
    loose = head(lambda: ScriptedDepth(maps), max_residual=0.25).profiles(images()).profiles
    strict = head(lambda: ScriptedDepth(maps), max_residual=1e-12).profiles(images()).profiles
    assert [p.anchored for p in loose] == [True] * 3
    assert [p.anchored for p in strict] == [False] * 3
    assert [p.columns for p in loose] == [p.columns for p in strict]


def test_the_scripted_fake_replays_then_repeats_its_last_map() -> None:
    """Same contract as `ScriptedDetector`: past the end of the script the
    last entry repeats, so a 3-map script answers a 50-frame bench.
    """
    left = wall_on_the_left()
    estimator = ScriptedDepth((left,))
    for _ in range(4):
        assert estimator.predict(images()[0]) is left


def test_the_default_fake_matches_the_frame_it_is_given() -> None:
    estimator = ScriptedDepth()
    depth = estimator.predict(np.zeros((512, 512, 3), dtype=np.uint8))
    assert depth.shape == (512, 512)
    assert depth.dtype == np.float32
    assert np.isfinite(depth).all()


def test_the_fake_estimator_is_obviously_synthetic() -> None:
    """It exists for `CANE_DEPTH_BACKEND=fake` and for tests. If it ever ends
    up serving a demo, the profile it produces must be recognisably fake --
    a perfectly linear ramp -- not something a wall could plausibly produce.
    """
    columns = head(lambda: ScriptedDepth()).profiles(images()).profiles[0].columns
    steps = [round(b - a, 4) for a, b in zip(columns, columns[1:])]
    assert len(set(steps)) == 1, steps


def test_a_frame_whose_depth_is_all_nan_yields_no_profile() -> None:
    nan_map = np.full((CROP_PX, CROP_PX), np.nan, dtype=np.float32)
    result = head(lambda: ScriptedDepth((nan_map,))).profiles(images())
    assert result.profiles == ()
    assert result.ready is True, "the model loaded; it was the output that was unusable"


@pytest.mark.parametrize("crop_px", (256, 512, 640, 1280))
def test_every_rung_size_profiles(crop_px: int) -> None:
    result = head(lambda: ScriptedDepth()).profiles(images(crop_px))
    assert len(result.profiles) == len(DEPTH_CROPS)
    for wire in result.profiles:
        assert 0 <= wire.band_rows[0] < wire.band_rows[1] <= crop_px
