"""The HTTP boundary: validation, limits, errors, serialisation, the lock.

Every assertion about "no metres" and "the stamp is echoed" is made on the
*raw body*, not the parsed dict, because the parsed dict cannot tell a
`1790123456789` from a `1790123456789.0`.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import statistics
import time
from typing import Any, Callable, Sequence

import numpy as np
import pytest
from numpy.typing import NDArray

from cane.cloud.api_models import CROP_COUNT, Detection, InferenceResponse, crop_field_name
from cane.cloud.detector.base import RawDetection
from cane.cloud.detector.fake import ScriptedDetector

from .conftest import T_CAPTURE_MS, Harness, cloud_harness

FORBIDDEN_DETECTION_FIELDS = frozenset({"range_m", "bearing_deg", "urgency", "ttl_ms"})
# A15.3: six 640x640 cv2.imdecode calls alone are 6-9 ms on an M2, so the
# brief's 5 ms was unreachable. This is the amended budget.
NON_INFERENCE_BUDGET_MS = 20.0
OVERHEAD_REQUESTS = 50
WARMUP_SHAPES = (640, 512)


async def _post(harness: Harness, meta: str, files: Sequence[Any], **kw: Any) -> Any:
    return await harness.client.post("/infer", data={"meta": meta}, files=list(files), **kw)


async def test_infer_returns_valid_response(
    app_with_fake: Harness, meta_json: Callable[..., str], six_jpegs: Callable[..., Any]
) -> None:
    response = await _post(app_with_fake, meta_json(), six_jpegs(640))
    assert response.status_code == 200
    body = InferenceResponse.model_validate_json(response.content)
    assert body.schema_version == 1
    assert body.inference_ms > 0.0
    assert len(body.detections) == 1
    detection = body.detections[0]
    assert detection.crop_index == 0
    assert detection.track_id.startswith("0:")
    assert detection.range_band is not None
    assert detection.closing_mps is None  # first frame of the track


async def test_t_capture_ms_is_echoed_byte_identical(
    app_with_fake: Harness, meta_json: Callable[..., str], six_jpegs: Callable[..., Any]
) -> None:
    response = await _post(app_with_fake, meta_json(), six_jpegs(640))
    assert response.json()["t_capture_ms"] == T_CAPTURE_MS
    assert f'"t_capture_ms":{T_CAPTURE_MS}' in response.text.replace(", ", ",")


async def test_the_wire_key_is_schema_not_schema_version(
    app_with_fake: Harness, meta_json: Callable[..., str], six_jpegs: Callable[..., Any]
) -> None:
    response = await _post(app_with_fake, meta_json(), six_jpegs(640))
    assert response.json()["schema"] == 1
    assert "schema_version" not in response.text


async def test_response_json_mentions_no_metres_and_no_bearing(
    app_with_fake: Harness, meta_json: Callable[..., str], six_jpegs: Callable[..., Any]
) -> None:
    response = await _post(app_with_fake, meta_json(), six_jpegs(640))
    assert "range_m" not in response.text
    assert "bearing" not in response.text


def test_detection_model_has_no_forbidden_fields() -> None:
    assert FORBIDDEN_DETECTION_FIELDS.isdisjoint(Detection.model_fields)


class _NoClock(datetime.datetime):
    """Every wall-clock entry point on `datetime.datetime`, made fatal."""

    @classmethod
    def now(cls, tz: Any = None) -> Any:
        raise AssertionError("the request path read datetime.now")

    @classmethod
    def utcnow(cls) -> Any:
        raise AssertionError("the request path read datetime.utcnow")

    @classmethod
    def today(cls) -> Any:
        raise AssertionError("the request path read datetime.today")

    @classmethod
    def fromtimestamp(cls, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the request path read datetime.fromtimestamp")


def _raiser(*args: Any, **kwargs: Any) -> float:
    raise AssertionError("the request path read the wall clock")


class _ClockBomb:
    """ASGI wrapper that makes every wall-clock entry point fatal *only*
    while the application handles a request.

    The monkeypatch cannot be process-wide for the test's duration: httpx's
    own cookie jar calls `time.time()` after the response is produced, and
    failing on that would prove nothing about the server.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        saved = (time.time, time.time_ns, datetime.datetime)
        time.time, time.time_ns, datetime.datetime = _raiser, _raiser, _NoClock  # type: ignore[assignment]
        # `logging.LogRecord.__init__` calls `time.time()`, so one legitimate
        # warning would fail this spuriously. Disable, and always restore.
        logging.disable(logging.CRITICAL)
        try:
            await self._inner(scope, receive, send)
        finally:
            time.time, time.time_ns, datetime.datetime = saved  # type: ignore[assignment]
            logging.disable(logging.NOTSET)


async def test_request_path_survives_a_raising_time_time(
    meta_json: Callable[..., str], six_jpegs: Callable[..., Any]
) -> None:
    import httpx

    from cane.cloud.app import app as real_app

    async with cloud_harness():
        transport = httpx.ASGITransport(app=_ClockBomb(real_app))
        async with httpx.AsyncClient(transport=transport, base_url="http://cloud") as client:
            response = await client.post(
                "/infer", data={"meta": meta_json()}, files=list(six_jpegs(640))
            )
            health = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["t_capture_ms"] == T_CAPTURE_MS
    assert health.status_code == 200


async def test_malformed_meta_returns_422(
    app_with_fake: Harness, six_jpegs: Callable[..., Any]
) -> None:
    for bad in ("{not json", "", "[]", '{"schema": 1}'):
        response = await _post(app_with_fake, bad, six_jpegs(640))
        assert response.status_code == 422, bad


async def test_a_422_never_echoes_the_client_payload(
    app_with_fake: Harness, meta_json: Callable[..., str], six_jpegs: Callable[..., Any]
) -> None:
    secret = "../secret-" + "A" * 30
    response = await _post(app_with_fake, meta_json(session_id=secret), six_jpegs(640))
    assert response.status_code == 422
    assert secret not in response.text


async def test_missing_crop_returns_422(
    app_with_fake: Harness, meta_json: Callable[..., str], six_jpegs: Callable[..., Any]
) -> None:
    response = await _post(app_with_fake, meta_json(), six_jpegs(640)[: CROP_COUNT - 1])
    assert response.status_code == 422


async def test_crop_size_mismatch_returns_422(
    app_with_fake: Harness, meta_json: Callable[..., str], six_jpegs: Callable[..., Any]
) -> None:
    """Never trust the meta to describe the bytes."""
    response = await _post(app_with_fake, meta_json(crop_px=512), six_jpegs(640))
    assert response.status_code == 422
    assert "crop" in response.text


async def test_undecodable_jpeg_returns_422(
    app_with_fake: Harness, meta_json: Callable[..., str], six_jpegs: Callable[..., Any]
) -> None:
    files = list(six_jpegs(640))
    files[3] = (crop_field_name(3), ("crop_3.jpg", b"\xff\xd8not-an-image", "image/jpeg"))
    response = await _post(app_with_fake, meta_json(), files)
    assert response.status_code == 422
    assert "crop_3" in response.text


async def test_oversize_crop_returns_413(
    meta_json: Callable[..., str], six_jpegs: Callable[..., Any]
) -> None:
    """One huge multipart part must not be spooled to the VM's disk."""
    async with cloud_harness(max_crop_bytes=1024) as harness:
        response = await _post(harness, meta_json(), six_jpegs(640))
        assert response.status_code == 413


async def test_api_key_required_when_configured(
    meta_json: Callable[..., str], six_jpegs: Callable[..., Any]
) -> None:
    async with cloud_harness(api_key="s3cret") as harness:
        assert (await _post(harness, meta_json(), six_jpegs(640))).status_code == 401
        wrong = await _post(harness, meta_json(), six_jpegs(640), headers={"X-Cane-Key": "no"})
        assert wrong.status_code == 401
        good = await _post(
            harness, meta_json(), six_jpegs(640), headers={"X-Cane-Key": "s3cret"}
        )
        assert good.status_code == 200
        assert (await harness.client.get("/warmup")).status_code == 401
        # /healthz stays open: step 8's monitor polls it.
        assert (await harness.client.get("/healthz")).status_code == 200


async def test_healthz_shape(
    app_with_fake: Harness, meta_json: Callable[..., str], six_jpegs: Callable[..., Any]
) -> None:
    before = (await app_with_fake.client.get("/healthz")).json()
    assert set(before) == {
        "detector", "engine_ready", "gpu", "weights", "sessions", "last_inference_ms"
    }
    assert before == {
        "detector": "fake", "engine_ready": True, "gpu": None,
        "weights": "scripted", "sessions": 0, "last_inference_ms": None,
    }
    await _post(app_with_fake, meta_json(), six_jpegs(640))
    after = (await app_with_fake.client.get("/healthz")).json()
    assert after["sessions"] == 1
    assert after["last_inference_ms"] > 0.0


async def test_healthz_carries_no_uptime(app_with_fake: Harness) -> None:
    assert "uptime" not in (await app_with_fake.client.get("/healthz")).text


async def test_warmup_warms_both_shapes(app_with_fake: Harness) -> None:
    """The dynamic engine pays a one-time context reshape per input shape,
    and the first MPS inference compiles graphs for 5-10 s.
    """
    body = (await app_with_fake.client.get("/warmup")).json()
    assert body["engine_ready"] is True
    assert [entry["crop_px"] for entry in body["warmed"]] == list(WARMUP_SHAPES)
    assert all(entry["inference_ms"] >= 0.0 for entry in body["warmed"])
    single = (await app_with_fake.client.get("/warmup", params={"crop_px": 512})).json()
    assert [entry["crop_px"] for entry in single["warmed"]] == [512]
    # Warm-up bypasses the tracker entirely: no session state is created.
    assert (await app_with_fake.client.get("/healthz")).json()["sessions"] == 0


async def test_warmup_rejects_a_bad_crop_size(app_with_fake: Harness) -> None:
    assert (await app_with_fake.client.get("/warmup", params={"crop_px": 7})).status_code == 422


class _ExplodingDetector:
    name = "fake"
    engine_ready = True
    gpu_name = None
    weights = "exploding"
    secret = "CUDA error at /opt/secret/path/trt.cpp:1234"

    def detect(self, images: Sequence[NDArray[np.uint8]]) -> Any:
        raise RuntimeError(self.secret)


async def test_detector_failure_returns_503_without_a_traceback(
    meta_json: Callable[..., str], six_jpegs: Callable[..., Any], caplog: pytest.LogCaptureFixture
) -> None:
    async with cloud_harness(_ExplodingDetector()) as harness:  # type: ignore[arg-type]
        with caplog.at_level(logging.ERROR):
            response = await _post(harness, meta_json(), six_jpegs(640))
    assert response.status_code == 503
    assert response.json() == {"detail": "inference unavailable"}
    assert _ExplodingDetector.secret not in response.text
    assert _ExplodingDetector.secret in caplog.text  # logged server-side only


class _CountingDetector(ScriptedDetector):
    """Records the maximum number of `detect` calls running at once."""

    def __init__(self, sleep_s: float = 0.02) -> None:
        super().__init__()
        self._sleep_s = sleep_s
        self.inflight = 0
        self.max_inflight = 0

    def detect(self, images: Sequence[NDArray[np.uint8]]) -> Any:
        self.inflight += 1
        self.max_inflight = max(self.max_inflight, self.inflight)
        try:
            time.sleep(self._sleep_s)
            return super().detect(images)
        finally:
            self.inflight -= 1


async def test_concurrent_requests_are_serialized(
    meta_json: Callable[..., str], six_jpegs: Callable[..., Any]
) -> None:
    """The bridge sends one request at a time, but /warmup, the smoke tool
    and step 8's health poll can all overlap it -- and a TensorRT execution
    context is not thread-safe.
    """
    detector = _CountingDetector()
    async with cloud_harness(detector) as harness:  # type: ignore[arg-type]
        await asyncio.gather(
            _post(harness, meta_json(), six_jpegs(640)),
            _post(harness, meta_json(t_capture_ms=T_CAPTURE_MS + 333), six_jpegs(640)),
        )
    assert detector.max_inflight == 1


async def test_the_lock_is_not_a_module_global() -> None:
    """A module-level `asyncio.Lock` shared across pytest event loops is a
    latent hang; each state owns its own.
    """
    async with cloud_harness() as first, cloud_harness() as second:
        assert first.state.lock is not second.state.lock  # type: ignore[attr-defined]


async def test_non_inference_overhead_p50_under_budget(
    meta_json: Callable[..., str], six_jpegs: Callable[..., Any]
) -> None:
    files = six_jpegs(640)  # encoded once, outside the loop
    empty = ScriptedDetector([tuple(() for _ in range(CROP_COUNT))])
    async with cloud_harness(empty) as harness:  # type: ignore[arg-type]
        samples: list[float] = []
        for index in range(OVERHEAD_REQUESTS):
            meta = meta_json(t_capture_ms=T_CAPTURE_MS + 333 * index)
            started = time.perf_counter()
            response = await _post(harness, meta, files)
            samples.append((time.perf_counter() - started) * 1000.0)
            assert response.status_code == 200
    ordered = sorted(samples)
    p50 = statistics.median(ordered)
    p95 = ordered[int(0.95 * (len(ordered) - 1))]
    print(f"\nnon-inference overhead: p50 {p50:.2f} ms, p95 {p95:.2f} ms")
    assert p50 < NON_INFERENCE_BUDGET_MS


async def test_a_detection_that_grows_produces_a_closing_speed(
    meta_json: Callable[..., str], six_jpegs: Callable[..., Any]
) -> None:
    """End to end through HTTP, not just the service: the id is stable and
    the second frame carries a signed range rate.
    """
    async with cloud_harness() as harness:
        ids, closings = [], []
        for index in range(3):
            meta = meta_json(t_capture_ms=T_CAPTURE_MS + 333 * index)
            body = InferenceResponse.model_validate_json((await _post(harness, meta, six_jpegs(640))).content)
            ids.append(body.detections[0].track_id)
            closings.append(body.detections[0].closing_mps)
    assert len(set(ids)) == 1
    assert closings[0] is None
    assert closings[1] is not None and closings[1] > 0.0


def test_raw_detection_is_frozen() -> None:
    raw = RawDetection(1.0, 2.0, 3.0, 4.0, "person", 0.9)
    with pytest.raises((AttributeError, TypeError)):
        raw.x1 = 9.0  # type: ignore[misc]
