"""One interface, two backends (E1).

The acceptance clause this file exists for is "user-visible output is
unchanged across the switch": the same six JPEGs and the same meta, put
through the step-7 ASGI app and through the in-process `LocalBackend`, must
produce `AwarenessEvent`s that agree on every field except `source` and
`track_id` -- the two that are *required* to differ (step-07-design H2).
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator, Callable

import httpx
import pytest

from cane.bridge.backends import (
    DEFAULT_WARM_UP_PX,
    LOCAL_SESSION_SUFFIX,
    BackendError,
    BackendTimeout,
    CloudBackend,
    LocalBackend,
    SwitchingBackend,
    default_local_detector,
    local_session_id,
)
from cane.bridge.events import events_from_response
from cane.bridge.transport_http import CloudResponseError
from cane.cloud.api_models import (
    CROP_COUNT,
    SESSION_ID_PATTERN,
    InferenceRequest,
    InferenceResponse,
    crop_field_name,
)
from cane.cloud.detector.base import RawDetection
from cane.cloud.detector.fake import ScriptedDetector
from cane.cloud.settings import CloudSettings
from cane.contracts.awareness_event import AwarenessEvent, Source

from .conftest import (
    DEFAULT_CROP_PX,
    SESSION_ID,
    T_CAPTURE_MS,
    YAWS,
    Jpegs,
    make_meta,
)

HEAVY = frozenset({"torch", "torchvision", "ultralytics"})
DELTA_DEG = 7.5
FRAMES = 3
FRAME_GAP_MS = 500
SHORT_TIMEOUT_S = 0.05
HANG_S = 5.0
HTTP_SERVER_ERROR = 500
HTTP_OK = 200
CHANNELS = 3
SLOW_DETECT_S = 0.3
LOCAL_BUDGET_S = 0.05
COCO_NAMES = ("person", "bicycle", "motorcycle", "truck", "bus", "stop sign")


def _one_per_crop(shrink: float) -> tuple[tuple[RawDetection, ...], ...]:
    """One approaching object of a different class in every crop."""
    return tuple(
        (
            RawDetection(
                x1=200.0 + 10.0 * index,
                y1=120.0 + shrink,
                x2=320.0 + 10.0 * index,
                y2=480.0 - shrink,
                coco_name=name,
                confidence=0.82,
            ),
        )
        for index, name in enumerate(COCO_NAMES)
    )


def _script() -> list[tuple[tuple[RawDetection, ...], ...]]:
    return [_one_per_crop(30.0), _one_per_crop(10.0), _one_per_crop(0.0)]


def _local_backend(**overrides: object) -> LocalBackend:
    settings = CloudSettings(detector="fake", **overrides)  # type: ignore[arg-type]
    # `warm_up_px=None`: these tests count the scripted detector's calls, and
    # a warm-up would consume the first entry of the script.
    return LocalBackend.build(
        settings, detector=ScriptedDetector(_script()), warm_up_px=None
    )


class CountingDetector:
    """A `FrameDetector` that records the shapes it was handed."""

    def __init__(self, sleep_s: float = 0.0) -> None:
        self.name = "fake"
        self.engine_ready = True
        self.gpu_name = None
        self.weights = "counting"
        self.sleep_s = sleep_s
        self.batches: list[tuple[tuple[int, ...], ...]] = []

    def detect(self, images):  # type: ignore[no-untyped-def]
        self.batches.append(tuple(image.shape for image in images))
        if self.sleep_s:
            time.sleep(self.sleep_s)
        return tuple(() for _ in images)


# --------------------------------------------------------------------------
# The local path is bounded: warmed once, and never unbounded per request
# --------------------------------------------------------------------------


def test_the_local_detector_is_warmed_up_exactly_once_when_it_is_built(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The cold MPS compile is ~11 s; it must not land inside the first
    tick after a flip, and it must not touch the tracker state.
    """
    detector = CountingDetector()

    with caplog.at_level(logging.INFO, logger="cane.bridge.backends"):
        backend = LocalBackend.build(CloudSettings(detector="fake"), detector=detector)

    assert len(detector.batches) == 1
    assert detector.batches[0] == (
        (DEFAULT_WARM_UP_PX, DEFAULT_WARM_UP_PX, CHANNELS),
    ) * CROP_COUNT
    # Warming bypasses the service, so no session was opened by it.
    assert backend.service.registry.sessions == ()
    assert any("warm-up" in r.getMessage() for r in caplog.records)


def test_the_warm_up_runs_at_the_crop_size_it_is_given() -> None:
    detector = CountingDetector()

    LocalBackend.build(CloudSettings(detector="fake"), detector=detector, warm_up_px=512)

    assert detector.batches[0] == ((512, 512, CHANNELS),) * CROP_COUNT


async def test_a_local_inference_over_its_budget_is_a_timeout_and_frees_the_slot(
    six_jpegs: Callable[..., Jpegs],
) -> None:
    """Local inference also takes time; a stuck model must not hold the
    one-in-flight slot for the rest of the walk.
    """
    slow = CountingDetector(sleep_s=SLOW_DETECT_S)
    backend = LocalBackend.build(
        CloudSettings(detector="fake"),
        detector=slow,
        warm_up_px=None,
        timeout_s=LOCAL_BUDGET_S,
    )

    started = time.monotonic()
    with pytest.raises(BackendTimeout):
        await backend.infer(make_meta(), six_jpegs())
    elapsed = time.monotonic() - started

    assert elapsed < SLOW_DETECT_S
    # Nothing is wedged: the next request is answered normally.
    slow.sleep_s = 0.0
    response = await backend.infer(make_meta(), six_jpegs())
    assert response.t_capture_ms == T_CAPTURE_MS


def test_the_local_budget_default_is_the_configurable_one() -> None:
    from cane.bridge.config import DEFAULT_LOCAL_TIMEOUT_S, BridgeConfig

    assert DEFAULT_LOCAL_TIMEOUT_S == 2.0
    assert BridgeConfig(inference_url="http://h/infer").local_timeout_s == 2.0
    with pytest.raises(ValueError, match="local_timeout_s"):
        BridgeConfig(inference_url="http://h/infer", local_timeout_s=0.0)


# --------------------------------------------------------------------------
# LocalBackend
# --------------------------------------------------------------------------


async def test_local_backend_answers_with_a_valid_response_echoing_the_stamp(
    six_jpegs: Callable[..., Jpegs],
) -> None:
    backend = _local_backend()
    meta = make_meta()

    response = await backend.infer(meta, six_jpegs())

    assert isinstance(response, InferenceResponse)
    assert response.t_capture_ms == meta.t_capture_ms
    assert response.detections
    assert backend.source is Source.X4_LOCAL


async def test_local_backend_keeps_its_tracker_state_under_a_local_session_id(
    six_jpegs: Callable[..., Jpegs],
) -> None:
    """Cloud and local must never share tracker state (E1)."""
    backend = _local_backend()

    await backend.infer(make_meta(), six_jpegs())

    assert backend.service.registry.sessions == (f"{SESSION_ID}{LOCAL_SESSION_SUFFIX}",)


def test_a_maximum_length_session_id_still_yields_a_contract_valid_local_id() -> None:
    import re

    base = "s" * 64
    local = local_session_id(base)

    assert local.endswith(LOCAL_SESSION_SUFFIX)
    assert re.fullmatch(SESSION_ID_PATTERN, local) is not None


async def test_local_backend_rejects_a_crop_that_does_not_decode(
    six_jpegs: Callable[..., Jpegs],
) -> None:
    backend = _local_backend()
    jpegs = (b"not a jpeg",) + six_jpegs()[1:]

    with pytest.raises(BackendError):
        await backend.infer(make_meta(), jpegs)


async def test_local_backend_rejects_a_crop_whose_size_contradicts_meta(
    six_jpegs: Callable[..., Jpegs],
) -> None:
    backend = _local_backend()

    with pytest.raises(BackendError):
        await backend.infer(make_meta(crop_px=512), six_jpegs(DEFAULT_CROP_PX))


# --------------------------------------------------------------------------
# CloudBackend
# --------------------------------------------------------------------------


class FakeTransport:
    """Whatever the uplink does this frame, expressed as one raiser."""

    def __init__(self, behaviour: Callable[[], object]) -> None:
        self._behaviour = behaviour
        self.requests: list[InferenceRequest] = []
        self.closed = False

    async def post_crops(self, request: InferenceRequest) -> bytes:
        self.requests.append(request)
        result = self._behaviour()
        if isinstance(result, BaseException):
            raise result
        assert isinstance(result, bytes)
        return result

    async def aclose(self) -> None:
        self.closed = True


def _ok_body(t_capture_ms: int) -> bytes:
    return InferenceResponse(
        schema=1, t_capture_ms=t_capture_ms, inference_ms=3.0, detections=()
    ).model_dump_json(by_alias=True).encode()


async def test_cloud_backend_maps_an_httpx_timeout_to_backend_timeout(
    six_jpegs: Callable[..., Jpegs],
) -> None:
    transport = FakeTransport(lambda: httpx.ReadTimeout("too slow"))
    backend = CloudBackend(transport)

    with pytest.raises(BackendTimeout):
        await backend.infer(make_meta(), six_jpegs())


async def test_cloud_backend_times_out_a_request_slower_than_its_own_budget(
    six_jpegs: Callable[..., Jpegs],
) -> None:
    """A slow answer is a *timeout outcome*, not a slow RTT (E1)."""

    class Hanging:
        async def post_crops(self, request: InferenceRequest) -> bytes:
            await asyncio.sleep(HANG_S)
            return b""

        async def aclose(self) -> None:
            return None

    backend = CloudBackend(Hanging(), timeout_s=SHORT_TIMEOUT_S)

    with pytest.raises(BackendTimeout):
        await backend.infer(make_meta(), six_jpegs())


async def test_cloud_backend_maps_a_5xx_to_backend_error_with_its_status(
    six_jpegs: Callable[..., Jpegs],
) -> None:
    transport = FakeTransport(
        lambda: CloudResponseError("boom", status_code=HTTP_SERVER_ERROR)
    )
    backend = CloudBackend(transport)

    with pytest.raises(BackendError) as excinfo:
        await backend.infer(make_meta(), six_jpegs())
    assert excinfo.value.status == HTTP_SERVER_ERROR


async def test_cloud_backend_maps_a_refused_connection_to_a_statusless_error(
    six_jpegs: Callable[..., Jpegs],
) -> None:
    transport = FakeTransport(lambda: httpx.ConnectError("connection refused"))
    backend = CloudBackend(transport)

    with pytest.raises(BackendError) as excinfo:
        await backend.infer(make_meta(), six_jpegs())
    assert excinfo.value.status is None


async def test_cloud_backend_maps_an_unparseable_200_to_a_backend_error(
    six_jpegs: Callable[..., Jpegs],
) -> None:
    transport = FakeTransport(lambda: b"{not json")
    backend = CloudBackend(transport)

    with pytest.raises(BackendError) as excinfo:
        await backend.infer(make_meta(), six_jpegs())
    assert excinfo.value.status == HTTP_OK


async def test_cloud_backend_posts_the_six_crops_and_the_meta_it_was_given(
    six_jpegs: Callable[..., Jpegs],
) -> None:
    meta = make_meta()
    transport = FakeTransport(lambda: _ok_body(meta.t_capture_ms))
    backend = CloudBackend(transport)

    response = await backend.infer(meta, six_jpegs())

    assert response.t_capture_ms == meta.t_capture_ms
    assert transport.requests[0].meta == meta
    assert transport.requests[0].crops == six_jpegs()
    assert backend.source is Source.X4_CLOUD
    await backend.aclose()
    assert transport.closed


# --------------------------------------------------------------------------
# SwitchingBackend
# --------------------------------------------------------------------------


class FakeMode:
    def __init__(self, is_local: bool = False) -> None:
        self.is_local = is_local


class RecordingBackend:
    def __init__(self, name: str, source: Source) -> None:
        self.name = name
        self.source = source
        self.calls = 0
        self.closed = False

    async def infer(self, meta: object, jpegs: Jpegs) -> InferenceResponse:
        self.calls += 1
        return InferenceResponse(
            schema=1, t_capture_ms=T_CAPTURE_MS, inference_ms=0.0, detections=()
        )

    async def aclose(self) -> None:
        self.closed = True


async def test_switching_backend_routes_by_the_monitors_current_mode(
    six_jpegs: Callable[..., Jpegs],
) -> None:
    mode = FakeMode(is_local=False)
    cloud = RecordingBackend("cloud", Source.X4_CLOUD)
    local = RecordingBackend("local", Source.X4_LOCAL)
    switching = SwitchingBackend(mode, cloud=cloud, local=local)

    await switching.infer(make_meta(), six_jpegs())
    assert (cloud.calls, local.calls) == (1, 0)
    assert switching.source is Source.X4_CLOUD

    mode.is_local = True
    await switching.infer(make_meta(), six_jpegs())
    assert (cloud.calls, local.calls) == (1, 1)
    assert switching.source is Source.X4_LOCAL

    await switching.aclose()
    assert cloud.closed and local.closed


# --------------------------------------------------------------------------
# The acceptance clause: user-visible output is unchanged across the switch
# --------------------------------------------------------------------------


@asynccontextmanager
async def _cloud_client(detector: ScriptedDetector) -> AsyncIterator[httpx.AsyncClient]:
    from cane.cloud.app import app, build_state, get_app_state

    state = build_state(CloudSettings(detector="fake"), detector)
    app.dependency_overrides[get_app_state] = lambda: state
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://cloud"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def _comparable(event: AwarenessEvent) -> dict[str, object]:
    """Every field except the two that must differ across the flip (H2)."""
    return {
        key: value
        for key, value in event.model_dump(by_alias=True).items()
        if key not in {"source", "track_id"}
    }


async def test_the_same_frames_produce_the_same_events_on_both_backends(
    six_jpegs: Callable[..., Jpegs],
) -> None:
    jpegs = six_jpegs(DEFAULT_CROP_PX)
    local = LocalBackend.build(
        CloudSettings(detector="fake"),
        detector=ScriptedDetector(_script()),
        warm_up_px=None,
    )
    cloud_events: list[tuple[AwarenessEvent, ...]] = []
    local_events: list[tuple[AwarenessEvent, ...]] = []

    async with _cloud_client(ScriptedDetector(_script())) as client:
        for frame in range(FRAMES):
            meta = make_meta(t_capture_ms=make_meta().t_capture_ms + frame * FRAME_GAP_MS)
            http = await client.post(
                "/infer",
                data={"meta": meta.model_dump_json(by_alias=True)},
                files=[
                    (crop_field_name(i), (f"crop_{i}.jpg", data, "image/jpeg"))
                    for i, data in enumerate(jpegs)
                ],
            )
            assert http.status_code == HTTP_OK
            cloud_events.append(
                events_from_response(
                    InferenceResponse.model_validate_json(http.content), YAWS, DELTA_DEG
                )
            )
            local_events.append(
                events_from_response(
                    await local.infer(meta, jpegs), YAWS, DELTA_DEG, source=Source.X4_LOCAL
                )
            )

    assert any(events for events in cloud_events)
    for frame, (from_cloud, from_local) in enumerate(zip(cloud_events, local_events, strict=True)):
        assert [_comparable(e) for e in from_cloud] == [
            _comparable(e) for e in from_local
        ], f"frame {frame} differs beyond source/track_id"
        assert all(e.source is Source.X4_CLOUD for e in from_cloud)
        assert all(e.source is Source.X4_LOCAL for e in from_local)
    # The ids must NOT be reused across sources: step 9's spatial refractory
    # is what covers the flip, and it needs two distinct keys.
    flat_cloud = [e.track_id for events in cloud_events for e in events]
    flat_local = [e.track_id for events in local_events for e in events]
    assert set(flat_cloud).isdisjoint(flat_local)


# --------------------------------------------------------------------------
# Environment
# --------------------------------------------------------------------------


def test_the_bridge_backends_do_not_drag_torch_into_the_test_process() -> None:
    import cane.bridge.backends  # noqa: F401

    assert default_local_detector() in {"fake", "ultralytics"}
    assert {name for name in HEAVY if name in sys.modules} == set()
