"""The depth head inside the existing `/infer` (plan Sec 2.1, step 22).

Two properties matter more than the profiles themselves:

1. with depth off, the detection path is **byte-identical** to step 7's, and
2. with depth on and broken, the detections still arrive.

The detections are the safety-critical half of this response. Steering is the
new half, and it is never allowed to take the old half down with it.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Sequence

import numpy as np
import pytest
from numpy.typing import NDArray

from cane.cloud.api_models import COLUMNS_PER_CROP, CROP_COUNT, InferenceMeta
from cane.cloud.depth.constants import DEPTH_CROPS
from cane.cloud.depth.fake import ScriptedDepth
from cane.cloud.depth.head import DepthHead
from cane.cloud.detector.base import RawDetection
from cane.cloud.detector.fake import ScriptedDetector
from cane.cloud.service import InferenceService
from cane.cloud.settings import CloudSettings
from cane.cloud.tracker import TrackerRegistry

from .conftest import Harness, cloud_harness, post_infer
from .depth_synthetic import CROP_PX, ideal_ground, wall_on_the_left

T0 = 1_790_000_000_000
YAWS = (0.0, 60.0, 120.0, 180.0, -120.0, -60.0, 0.0)  # C6 last
GAP_PROFILE_FIELDS = {
    "crop_index",
    "columns",
    "band_rows",
    "ground_fit_residual",
    "anchored",
    # Step 27: the recovered affine pair, `d = scale / (column - offset)`.
    "ground_scale",
    "ground_offset",
    # Step 29: a *column index* and a *ratio*. Still no bearing (the bridge
    # owns that), still no metres (the sensor cannot know them), still no
    # clock -- so this field does not weaken what this set is guarding.
    "ground_hazard",
}


def meta(crop_px: int = CROP_PX) -> InferenceMeta:
    return InferenceMeta(
        schema=1, t_capture_ms=T0, rung=1, crop_px=crop_px, yaws_deg=YAWS,
        session_id="depth-test",
    )


def images(crop_px: int = CROP_PX) -> tuple[NDArray[np.uint8], ...]:
    return tuple(np.zeros((crop_px, crop_px, 3), dtype=np.uint8) for _ in range(CROP_COUNT))


def a_person() -> tuple[tuple[RawDetection, ...], ...]:
    box = RawDetection(x1=260.0, y1=200.0, x2=340.0, y2=420.0, coco_name="person", confidence=0.9)
    return ((box,),) + tuple(() for _ in range(CROP_COUNT - 1))


def scripted_head(**kwargs: Any) -> DepthHead:
    ground = ideal_ground(base=wall_on_the_left(), from_row=542)
    return DepthHead(lambda: ScriptedDepth((ground,)), label="fake", **kwargs)


def build(depth: DepthHead | None, **overrides: object) -> InferenceService:
    settings = CloudSettings(**overrides)  # type: ignore[arg-type]
    return InferenceService(
        detector=ScriptedDetector((a_person(),)),
        registry=TrackerRegistry(max_sessions=settings.max_sessions,
                                 activation_threshold=settings.conf_emit),
        settings=settings,
        depth=depth,
    )


# --------------------------------------------------------------------------
# The service
# --------------------------------------------------------------------------


def test_the_response_carries_one_profile_per_forward_crop() -> None:
    response = build(scripted_head()).infer(meta(), images())
    assert tuple(p.crop_index for p in response.gap_profiles) == DEPTH_CROPS
    assert all(len(p.columns) == COLUMNS_PER_CROP for p in response.gap_profiles)
    assert len(response.detections) == 1


def test_no_depth_head_means_no_profiles() -> None:
    response = build(None).infer(meta(), images())
    assert response.gap_profiles == ()
    assert len(response.detections) == 1


def test_the_detection_path_is_byte_identical_with_depth_off() -> None:
    """Acceptance criterion 7. `inference_ms` is a duration and cannot be
    compared; everything else in the response must be the same bytes.
    """
    with_depth = build(scripted_head()).infer(meta(), images())
    without = build(None).infer(meta(), images())

    def detections_only(response: Any) -> str:
        body = json.loads(response.model_dump_json(by_alias=True))
        body.pop("inference_ms")  # a duration, not a fact
        body.pop("gap_profiles")
        for detection in body["detections"]:
            # `track_id` carries `tracker.next_generation()`, a process-wide
            # counter: two services built in one process can never stamp the
            # same generation, with or without depth. The crop and the
            # ByteTrack id around it must still match exactly.
            crop, _generation, track = detection["track_id"].split(":")
            detection["track_id"] = f"{crop}:*:{track}"
        return json.dumps(body, sort_keys=True)

    assert detections_only(with_depth) == detections_only(without)
    assert with_depth.t_capture_ms == without.t_capture_ms == T0


def test_a_broken_depth_head_still_returns_the_detections() -> None:
    """Acceptance criterion 4, at the service boundary."""

    def load() -> Any:
        raise RuntimeError("no checkpoint on this box")

    response = build(DepthHead(load, label="torch")).infer(meta(), images())
    assert response.gap_profiles == ()
    assert len(response.detections) == 1


def test_a_depth_head_that_itself_explodes_does_not_kill_the_frame() -> None:
    """Belt as well as braces: the head swallows its own failures, and the
    service still refuses to let an unexpected one reach the client.
    """

    class _Exploding:
        label = "exploding"
        ready = True

        def profiles(self, _images: Sequence[NDArray[np.uint8]]) -> Any:
            raise MemoryError("cuda oom")

        def warm(self, _crop_px: int) -> float | None:
            return None

    response = build(_Exploding()).infer(meta(), images())  # type: ignore[arg-type]
    assert response.gap_profiles == ()
    assert len(response.detections) == 1


def test_depth_cost_is_inside_inference_ms() -> None:
    """Step 8's health monitor and step 16's bench both read `inference_ms` as
    the whole server-side cost. Depth rides the same frame, so it is part of
    that number -- 95 ms of it on MPS (plan Sec 4.5).
    """
    slow_ms = 25.0

    class _Slow:
        name = "slow"
        engine_ready = True
        input_size = 280

        def predict(self, image: NDArray[np.uint8]) -> NDArray[np.float32]:
            time.sleep(slow_ms / 1000.0)
            return wall_on_the_left(int(image.shape[0]))

    response = build(DepthHead(lambda: _Slow(), label="slow")).infer(meta(), images())
    assert response.inference_ms >= slow_ms * len(DEPTH_CROPS) * 0.9


def test_the_geometry_follows_the_decoded_frame_not_the_meta() -> None:
    """A lying `crop_px` in `meta` must not be able to move the band: the same
    rule step 7 applies to box heights (a lying meta cannot move a band).
    """
    response = build(scripted_head()).infer(meta(crop_px=512), images(CROP_PX))
    assert all(p.band_rows == (171, 542) for p in response.gap_profiles)


def test_the_profile_carries_no_bearing_no_metres_and_no_clock() -> None:
    """Plan Sec 2.1: the cloud emits timeless facts. Anything shaped like a
    bearing or a distance here would be the cloud stealing the bridge's job.

    Step 27 added `ground_scale`/`ground_offset`, and the distinction matters:
    they are a *calibration* -- the pair that lets the bridge turn a raw column
    into a metre -- and not a metre. The cloud still states no distance to
    anything, no bearing to anything, and reads no clock. `range_m` remains
    absent from the whole response, which is the assertion below.
    """
    response = build(scripted_head()).infer(meta(), images())
    body = json.loads(response.model_dump_json(by_alias=True))
    for profile in body["gap_profiles"]:
        assert set(profile) == GAP_PROFILE_FIELDS
    assert "range_m" not in json.dumps(body)
    assert "bearing" not in json.dumps(body)


# --------------------------------------------------------------------------
# The app: /infer and /warmup
# --------------------------------------------------------------------------


async def test_infer_returns_gap_profiles_over_http(
    meta_json: Callable[..., str], crop_jpegs: Callable[..., Any]
) -> None:
    async with cloud_harness(depth=scripted_head()) as harness:
        response = await post_infer(harness, meta_json(), crop_jpegs(CROP_PX))  # type: ignore[misc]
    assert response.status_code == 200
    body = response.json()
    assert [p["crop_index"] for p in body["gap_profiles"]] == list(DEPTH_CROPS)
    assert len(body["gap_profiles"][0]["columns"]) == COLUMNS_PER_CROP
    assert body["t_capture_ms"] == json.loads(meta_json())["t_capture_ms"]


async def test_infer_without_a_depth_head_answers_an_empty_list(
    app_with_fake: Harness, meta_json: Callable[..., str], crop_jpegs: Callable[..., Any]
) -> None:
    """The default posture: an older cloud and a depth-less cloud look the same
    on the wire, which is what makes the amendment additive (plan Sec 4.1).
    """
    response = await post_infer(app_with_fake, meta_json(), crop_jpegs(CROP_PX))  # type: ignore[misc]
    assert response.status_code == 200
    assert response.json()["gap_profiles"] == []


async def test_a_broken_depth_model_is_not_a_503(
    meta_json: Callable[..., str], crop_jpegs: Callable[..., Any]
) -> None:
    def load() -> Any:
        raise ValueError("Official implementation missing at /nowhere")

    async with cloud_harness(depth=DepthHead(load, label="torch")) as harness:
        response = await post_infer(harness, meta_json(), crop_jpegs(CROP_PX))  # type: ignore[misc]
        assert response.status_code == 200
        assert response.json()["gap_profiles"] == []
        assert (await harness.client.get("/healthz")).status_code == 200


async def test_warmup_warms_the_depth_model_too() -> None:
    """The first MPS depth inference compiles graphs for seconds, exactly like
    the detector's: the bridge's first real frame must not pay for it.
    """
    estimator = ScriptedDepth()
    async with cloud_harness(depth=DepthHead(lambda: estimator, label="fake")) as harness:
        body = (await harness.client.get("/warmup")).json()
    assert body["depth"] == {"backend": "fake", "ready": True}
    assert all(entry["depth_ms"] >= 0.0 for entry in body["warmed"])
    assert estimator.calls == len(body["warmed"])


async def test_warmup_reports_an_unloadable_depth_model_as_not_ready() -> None:
    def load() -> Any:
        raise RuntimeError("no engine")

    async with cloud_harness(depth=DepthHead(load, label="tensorrt")) as harness:
        body = (await harness.client.get("/warmup")).json()
    assert body["depth"] == {"backend": "tensorrt", "ready": False}
    assert all(entry["depth_ms"] is None for entry in body["warmed"])
    assert body["engine_ready"] is True, "the detector is unaffected"


async def test_warmup_without_depth_says_so(app_with_fake: Harness) -> None:
    body = (await app_with_fake.client.get("/warmup")).json()
    assert body["depth"] == {"backend": None, "ready": False}
    assert [entry["crop_px"] for entry in body["warmed"]] == [640, 512]


async def test_healthz_keeps_exactly_step_sevens_shape(app_with_fake: Harness) -> None:
    """Step 7's `test_healthz_shape` asserts set equality, so adding depth
    there would break the suite step 22 must leave green; depth readiness is
    reported by `/warmup` instead. Pinned here so the choice is deliberate.
    """
    body = (await app_with_fake.client.get("/healthz")).json()
    assert set(body) == {
        "detector", "engine_ready", "gpu", "weights", "sessions", "last_inference_ms"
    }


@pytest.mark.parametrize("crop_px", (512, 640))
async def test_both_rung_sizes_profile_over_http(
    crop_px: int, meta_json: Callable[..., str], crop_jpegs: Callable[..., Any]
) -> None:
    async with cloud_harness(depth=DepthHead(lambda: ScriptedDepth(), label="fake")) as harness:
        response = await post_infer(  # type: ignore[misc]
            harness, meta_json(crop_px=crop_px), crop_jpegs(crop_px)
        )
    assert response.status_code == 200
    profiles = response.json()["gap_profiles"]
    assert len(profiles) == len(DEPTH_CROPS)
    for profile in profiles:
        top, bottom = profile["band_rows"]
        assert 0 <= top < bottom <= crop_px
