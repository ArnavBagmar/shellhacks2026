"""One inference interface, two backends, one switch (task-8-brief E1).

The fallback must not be a second detection pipeline. `LocalBackend` runs
the *same* `cane.cloud.service.InferenceService` in-process behind a local
`FrameDetector`, decoding the very JPEG bytes the cloud would have received
-- so `x4_cloud` and `x4_local` cannot disagree about bands or closing speed
at the moment the health monitor flips (step-07-design H1).

Two things are deliberately allowed to differ across the flip: `source`, and
`track_id` (the local session id carries a `-local` suffix so cloud and local
trackers never share state). Step 9's spatial refractory is what stops the
phone announcing the same object twice across the switch (H2); nothing here
suppresses anything.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Final, Protocol, Sequence

import cv2
import httpx
import numpy as np
from numpy.typing import NDArray

# One number for the cloud budget and one for the local one, both owned by
# the module that describes a run (F8).
from cane.bridge.config import DEFAULT_LOCAL_TIMEOUT_S, DEFAULT_REQUEST_TIMEOUT_S
# The step-6 uplink face, defined once where the sender declares it.
from cane.bridge.sender import InferenceTransport
from cane.bridge.transport_http import CloudResponseError
from cane.cloud.api_models import (
    CROP_COUNT,
    SESSION_ID_PATTERN,
    InferenceMeta,
    InferenceRequest,
    InferenceResponse,
)
from cane.cloud.detector.base import FrameDetector
from cane.cloud.detector.factory import build_detector
from cane.cloud.service import MS_PER_S, InferenceService
from cane.cloud.settings import CloudSettings, DetectorName
from cane.cloud.tracker import TrackerRegistry
from cane.contracts.awareness_event import Source

logger = logging.getLogger(__name__)

#: A request slower than this is a *timeout outcome*, not a slow RTT (E1).
#: The same object as the CLI default, so the two can never drift apart.
DEFAULT_BACKEND_TIMEOUT_S: Final = DEFAULT_REQUEST_TIMEOUT_S
#: Rung 1's crop size: the largest the pipeline will ever ask for, so a
#: detector warmed at this size is warm for every rung.
DEFAULT_WARM_UP_PX: Final = 640
LOCAL_SESSION_SUFFIX: Final = "-local"
MAX_SESSION_ID_LENGTH: Final = 64
CHANNELS: Final = 3
HTTP_OK: Final = 200
#: `.pt` on MPS is the acceptable fallback of the fallback; the CoreML
#: package is one documented export away (E1).
COREML_WEIGHTS: Final = Path("yolo11s.mlpackage")
TORCH_WEIGHTS: Final = Path("yolo11s.pt")
LOCAL_DEVICE: Final = "mps"
LOCAL_DETECTOR_ENV: Final = "CANE_LOCAL_DETECTOR"
LOCAL_WEIGHTS_ENV: Final = "CANE_LOCAL_WEIGHTS"
LOCAL_DETECTOR_NAMES: Final = ("fake", "ultralytics")
_SESSION_ID_RE: Final = re.compile(SESSION_ID_PATTERN)


class BackendFailure(RuntimeError):
    """This frame produced no detections. Never fatal: the loop keeps going."""


class BackendTimeout(BackendFailure):
    """No answer inside the backend's own budget."""


class BackendError(BackendFailure):
    """An answer arrived, but not a usable one.

    `status` is the HTTP status when there was one, and `None` when the
    request never reached a server at all (a refused connection, DNS, a
    local decode fault). Both count as "no answer in time" for the health
    monitor's three-strike rule.
    """

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class InferenceBackend(Protocol):
    """Six JPEG crops in, one `InferenceResponse` out."""

    name: str
    source: Source

    async def infer(
        self, meta: InferenceMeta, jpegs: tuple[bytes, ...]
    ) -> InferenceResponse: ...

    async def aclose(self) -> None: ...


class ModeSource(Protocol):
    """Whatever decides which backend answers -- the `HealthMonitor`."""

    @property
    def is_local(self) -> bool: ...


class CloudBackend:
    """The step-6 multipart POST, bounded by one timeout and one parse."""

    def __init__(
        self,
        transport: InferenceTransport,
        timeout_s: float = DEFAULT_BACKEND_TIMEOUT_S,
    ) -> None:
        if timeout_s <= 0:
            raise ValueError(f"timeout_s must be > 0, got {timeout_s}")
        self.name = "cloud"
        self.source = Source.X4_CLOUD
        self._transport = transport
        self._timeout_s = timeout_s

    @property
    def timeout_s(self) -> float:
        return self._timeout_s

    async def infer(
        self, meta: InferenceMeta, jpegs: tuple[bytes, ...]
    ) -> InferenceResponse:
        request = InferenceRequest(meta=meta, crops=jpegs)
        raw = await self._post(request)
        try:
            return InferenceResponse.model_validate_json(raw)
        except ValueError as exc:
            # A 200 with a body we cannot read is a failing cloud, and the
            # monitor must be allowed to treat it as one.
            raise BackendError(f"unparseable inference response: {exc}", HTTP_OK) from exc

    async def _post(self, request: InferenceRequest) -> bytes:
        """The one place a transport fault becomes a backend fault."""
        try:
            async with asyncio.timeout(self._timeout_s):
                return await self._transport.post_crops(request)
        except TimeoutError as exc:
            raise BackendTimeout(f"no answer within {self._timeout_s:.2f}s") from exc
        except httpx.TimeoutException as exc:
            raise BackendTimeout(f"uplink timed out: {exc}") from exc
        except CloudResponseError as exc:
            raise BackendError(str(exc), exc.status_code) from exc
        except httpx.HTTPError as exc:
            raise BackendError(f"uplink failed: {type(exc).__name__}: {exc}") from exc

    async def aclose(self) -> None:
        await self._transport.aclose()


def local_session_id(session_id: str) -> str:
    """`<session>-local`, truncated to stay inside the contract's 64 chars."""
    keep = MAX_SESSION_ID_LENGTH - len(LOCAL_SESSION_SUFFIX)
    candidate = f"{session_id[:keep]}{LOCAL_SESSION_SUFFIX}"
    if _SESSION_ID_RE.fullmatch(candidate) is None:  # pragma: no cover - defensive
        raise ValueError(f"cannot build a local session id from {session_id!r}")
    return candidate


def decode_crops(jpegs: Sequence[bytes], crop_px: int) -> list[NDArray[np.uint8]]:
    """The cloud's decode, byte for byte -- including its shape check.

    Copied in behaviour, not in code: `cane.cloud.app` raises `HTTPException`,
    which has no meaning on the laptop. Everything the pixels touch after
    this point is the cloud's own module.
    """
    images: list[NDArray[np.uint8]] = []
    for index, raw in enumerate(jpegs):
        image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise BackendError(f"crop_{index} is not a decodable image")
        if image.shape != (crop_px, crop_px, CHANNELS):
            raise BackendError(
                f"crop_{index} decoded to {image.shape}, meta says {crop_px}"
            )
        images.append(image)
    return images


def warm_up_detector(detector: FrameDetector, crop_px: int) -> float:
    """One batch of six blank crops straight at the detector, before the
    pipeline starts. Returns the elapsed milliseconds.

    A cold CoreML/MPS graph compiles on its first forward pass -- about
    eleven seconds on this laptop. Paying that inside the first tick after
    a flip would mean the fallback arrives long after the hazard did, and
    the only symptom would be a gap in the event stream. The service is
    bypassed on purpose: no session is opened and no tracker sees a frame
    of blank pixels.
    """
    blank = np.zeros((crop_px, crop_px, CHANNELS), dtype=np.uint8)
    started = time.perf_counter()
    detector.detect([blank] * CROP_COUNT)
    elapsed_ms = (time.perf_counter() - started) * MS_PER_S
    logger.info("local detector warm-up: %.0f ms", elapsed_ms)
    return elapsed_ms


class LocalBackend:
    """The step-7 service, in-process, on this laptop.

    Not thread-safe by design: `InferenceService` is stateful and the
    one-in-flight sender is what serialises it -- the same contract the
    cloud app states. No lock here, because a lock would *queue*, and a
    queued frame is the failure this whole step exists to avoid.

    One sharp edge follows from `timeout_s` (below): a forward pass that
    overran its budget is abandoned, not cancelled, so it may still be
    inside `service.infer` when the next frame arrives. A non-blocking
    worker lock turns that frame into an immediate, counted `BackendError`
    -- never a queue, never a concurrent entry -- and the backend stays
    "busy" until the abandoned worker finishes and releases it.
    """

    def __init__(
        self, service: InferenceService, timeout_s: float = DEFAULT_LOCAL_TIMEOUT_S
    ) -> None:
        if timeout_s <= 0:
            raise ValueError(f"timeout_s must be > 0, got {timeout_s}")
        self.name = "local"
        self.source = Source.X4_LOCAL
        self._service = service
        self._timeout_s = timeout_s
        # Taken on the event loop, released only by the worker thread.
        self._worker_lock = threading.Lock()

    @classmethod
    def build(
        cls,
        settings: CloudSettings,
        detector: FrameDetector | None = None,
        warm_up_px: int | None = DEFAULT_WARM_UP_PX,
        timeout_s: float = DEFAULT_LOCAL_TIMEOUT_S,
    ) -> LocalBackend:
        """`warm_up_px=None` skips the warm-up -- only tests that count the
        detector's calls should ask for that.
        """
        registry = TrackerRegistry(
            max_sessions=settings.max_sessions, activation_threshold=settings.conf_emit
        )
        chosen = detector if detector is not None else build_detector(settings)
        if warm_up_px is not None:
            warm_up_detector(chosen, warm_up_px)
        return cls(InferenceService(chosen, registry, settings), timeout_s=timeout_s)

    @property
    def service(self) -> InferenceService:
        return self._service

    @property
    def detector_name(self) -> str:
        """What is *actually* loaded -- the banner and the stats line must
        never re-derive this from the CLI flags.
        """
        return self._service.detector.name

    @property
    def timeout_s(self) -> float:
        return self._timeout_s

    async def infer(
        self, meta: InferenceMeta, jpegs: tuple[bytes, ...]
    ) -> InferenceResponse:
        """Decode + detect off the event loop: six JPEG decodes and a model
        forward pass would otherwise stall the grab/tick loop outright.

        Bounded like the cloud call is. `asyncio.to_thread` cannot cancel
        the worker, so an overrunning forward pass keeps running to its end
        in the background and its result is discarded -- but the *slot* is
        freed, which is what the next frame needs. Without this, a wedged
        local model would hold the one-in-flight slot for the rest of the
        walk and every frame after it would be dropped as `dropped_busy`:
        the fallback's own version of the failure the fallback exists for.
        """
        if not self._worker_lock.acquire(blocking=False):
            raise BackendError(
                "local inference from the previous frame is still running; frame dropped"
            )
        try:
            async with asyncio.timeout(self._timeout_s):
                return await asyncio.to_thread(self._infer_guarded, meta, jpegs)
        except TimeoutError as exc:
            raise BackendTimeout(
                f"local inference exceeded {self._timeout_s:.2f}s"
            ) from exc

    def _infer_guarded(
        self, meta: InferenceMeta, jpegs: tuple[bytes, ...]
    ) -> InferenceResponse:
        """Worker-thread side of `infer`; releases the lock here and only here.
        An executor never drops a submitted callable, so an abandoned pass
        always ends by freeing the backend; until then frames are rejected.
        """
        try:
            return self._infer_sync(meta, jpegs)
        finally:
            self._worker_lock.release()

    def _infer_sync(
        self, meta: InferenceMeta, jpegs: tuple[bytes, ...]
    ) -> InferenceResponse:
        started = time.perf_counter()
        images = decode_crops(jpegs, meta.crop_px)
        decode_ms = (time.perf_counter() - started) * MS_PER_S
        local_meta = meta.model_copy(
            update={"session_id": local_session_id(meta.session_id)}
        )
        return self._service.infer(local_meta, images, decode_ms=decode_ms)

    async def aclose(self) -> None:
        return None


class SwitchingBackend:
    """Delegates every call to the backend the monitor currently selects.

    Reads the mode once per call and keeps it for that call: the sender's
    one-in-flight rule means there is never a second request to confuse,
    and the answer must be attributed to the backend that produced it, not
    to whatever the mode became while the request was in the air.
    """

    def __init__(
        self, mode: ModeSource, cloud: InferenceBackend, local: InferenceBackend
    ) -> None:
        self._mode = mode
        self._cloud = cloud
        self._local = local

    @property
    def current(self) -> InferenceBackend:
        return self._local if self._mode.is_local else self._cloud

    @property
    def name(self) -> str:
        return self.current.name

    @property
    def source(self) -> Source:
        return self.current.source

    async def infer(
        self, meta: InferenceMeta, jpegs: tuple[bytes, ...]
    ) -> InferenceResponse:
        return await self.current.infer(meta, jpegs)

    async def aclose(self) -> None:
        await self._cloud.aclose()
        await self._local.aclose()


def default_local_detector() -> DetectorName:
    """`ultralytics` when torch is importable, `fake` otherwise (E1).

    `find_spec` resolves the module without executing it, so asking the
    question never drags torch into a test process.
    """
    return "ultralytics" if importlib.util.find_spec("torch") is not None else "fake"


def default_local_weights() -> Path:
    """The CoreML export when it has been produced, else the `.pt`."""
    return COREML_WEIGHTS if COREML_WEIGHTS.exists() else TORCH_WEIGHTS


def local_settings(
    detector: str | None = None, weights: str | None = None
) -> CloudSettings:
    """The laptop's detector configuration (E1).

    Precedence: the CLI flag, then `CANE_LOCAL_DETECTOR` / `CANE_LOCAL_WEIGHTS`,
    then the environment's own answer. The `CANE_LOCAL_*` names are separate
    from the service's `CANE_DETECTOR` / `CANE_WEIGHTS_PATH` on purpose: the
    bridge and a co-located cloud service must be configurable apart.
    """
    chosen = detector or os.environ.get(LOCAL_DETECTOR_ENV) or default_local_detector()
    if chosen not in LOCAL_DETECTOR_NAMES:
        raise ValueError(
            f"local detector must be one of {LOCAL_DETECTOR_NAMES}, got {chosen!r}"
        )
    raw_weights = weights or os.environ.get(LOCAL_WEIGHTS_ENV)
    path = Path(raw_weights) if raw_weights else default_local_weights()
    name: DetectorName = "ultralytics" if chosen == "ultralytics" else "fake"
    return CloudSettings(detector=name, weights_path=path, device=LOCAL_DEVICE)
