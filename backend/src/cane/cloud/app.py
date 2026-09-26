"""`POST /infer`, `GET /healthz`, `GET /warmup` (A12, A13).

The whole module is clock-free: `perf_counter` measures durations, the
capture stamp is echoed, and `/healthz` deliberately carries no `uptime`, so
the "timeless cloud" rule needs no carve-outs.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated, Any, Final, Sequence

import cv2
import numpy as np
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from numpy.typing import NDArray
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from cane.cloud.api_models import (
    CROP_COUNT,
    CROP_PX_MAX,
    CROP_PX_MIN,
    CROP_PX_MULTIPLE_OF,
    InferenceMeta,
    InferenceResponse,
)
from cane.cloud.debug_dump import maybe_write_sheet
from cane.cloud.depth.factory import build_depth_head
from cane.cloud.depth.head import DepthHead
from cane.cloud.detector.base import FrameDetector
from cane.cloud.detector.factory import build_detector
from cane.cloud.service import MS_PER_S, InferenceService
from cane.cloud.settings import CloudSettings
from cane.cloud.tracker import TrackerRegistry

logger = logging.getLogger("cane.cloud")

HTTP_UNAUTHORIZED: Final = 401
HTTP_PAYLOAD_TOO_LARGE: Final = 413
HTTP_UNPROCESSABLE: Final = 422
HTTP_UNAVAILABLE: Final = 503
API_KEY_HEADER: Final = "X-Cane-Key"
UNAVAILABLE_DETAIL: Final = "inference unavailable"
CHANNELS: Final = 3
# The dynamic engine pays a one-time context reshape at each new input shape,
# and the first MPS inference compiles graphs for 5-10 s: warm both rungs.
WARMUP_SHAPES: Final = (640, 512)

app = FastAPI(title="cane cloud inference", version="0.1.0")


@dataclass
class AppState:
    """Process-wide serving state. Mutable by design: `last_inference_ms` is
    what step 8's health monitor reads without paying for a probe.
    """

    service: InferenceService
    settings: CloudSettings
    lock: asyncio.Lock
    last_inference_ms: float | None = None
    frames_seen: int = 0


def build_state(
    settings: CloudSettings, detector: FrameDetector, depth: DepthHead | None = None
) -> AppState:
    """`depth=None` means "whatever the settings say", which in the default
    posture is no depth head at all (see `depth/factory.py`).
    """
    registry = TrackerRegistry(
        max_sessions=settings.max_sessions, activation_threshold=settings.conf_emit
    )
    return AppState(
        service=InferenceService(
            detector, registry, settings, depth=depth or build_depth_head(settings)
        ),
        settings=settings,
        # An attribute, never a module global: a module-level asyncio.Lock
        # shared across event loops is a latent hang.
        lock=asyncio.Lock(),
    )


@lru_cache(maxsize=1)
def get_app_state() -> AppState:
    """Built on first request, not in a lifespan handler: `ASGITransport`
    does not run lifespan, so a lifespan-built detector is `None` in tests.
    """
    settings = CloudSettings()
    logging.getLogger("cane").setLevel(settings.log_level.upper())
    return build_state(settings, build_detector(settings))


State = Annotated[AppState, Depends(get_app_state)]
ApiKey = Annotated[str | None, Header(alias=API_KEY_HEADER)]


def _require_key(state: AppState, provided: str | None) -> None:
    configured = state.settings.api_key
    if configured and not hmac.compare_digest(configured, provided or ""):
        raise HTTPException(HTTP_UNAUTHORIZED, detail="invalid or missing API key")


def _parse_meta(raw: str) -> InferenceMeta:
    """422 carrying field *locations* only -- the payload is untrusted client
    data and must never be reflected back.
    """
    try:
        return InferenceMeta.model_validate_json(raw)
    except ValidationError as exc:
        fields = sorted({".".join(str(p) for p in e["loc"]) or "meta" for e in exc.errors()})
        raise HTTPException(HTTP_UNPROCESSABLE, detail={"meta": fields}) from exc


async def _read_crop(index: int, upload: UploadFile, max_bytes: int) -> bytes:
    """Read at most one byte past the limit: `await upload.read()` with no
    argument pulls the whole part into memory (Starlette spools it to the
    VM's disk past 1 MB) *before* any size check could reject it. One extra
    byte is enough to tell "at the limit" from "over it".
    """
    raw = await upload.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise HTTPException(
            HTTP_PAYLOAD_TOO_LARGE,
            detail=f"crop_{index} exceeds the {max_bytes} byte limit",
        )
    return raw


def _decode(index: int, raw: bytes, crop_px: int) -> NDArray[np.uint8]:
    image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(HTTP_UNPROCESSABLE, detail=f"crop_{index} is not a decodable image")
    if image.shape != (crop_px, crop_px, CHANNELS):
        raise HTTPException(
            HTTP_UNPROCESSABLE,
            detail=f"crop_{index} decoded to {image.shape}, meta says {crop_px}",
        )
    return image


def _decode_and_infer(
    state: AppState, meta: InferenceMeta, raws: Sequence[bytes]
) -> InferenceResponse:
    """Runs in the threadpool: `cv2.imdecode` releases the GIL, and keeping
    it off the event loop stops six decodes from stalling `/healthz`.
    """
    started = time.perf_counter()
    images = [_decode(index, raw, meta.crop_px) for index, raw in enumerate(raws)]
    decode_ms = (time.perf_counter() - started) * MS_PER_S
    response = state.service.infer(meta, images, decode_ms=decode_ms)
    state.frames_seen += 1
    # After `infer` has stamped `inference_ms`, so switching the dump on can
    # never move the number step 8 and step 16 read.
    maybe_write_sheet(
        state.settings.dump_dir, state.settings.dump_every_n,
        state.frames_seen, images, response,
    )
    return response


def _warm(state: AppState, shapes: Sequence[int]) -> list[dict[str, float | int | None]]:
    """Inference on zeros, bypassing the tracker: no session state is made.

    The depth model is warmed on the same shapes and in the same call: its
    first MPS inference compiles graphs for seconds too, and the bridge's first
    real frame must not pay for either model's cold start.
    """
    depth = state.service.depth
    warmed: list[dict[str, float | int | None]] = []
    for size in shapes:
        blank = np.zeros((size, size, CHANNELS), dtype=np.uint8)
        started = time.perf_counter()
        state.service.detector.detect([blank] * CROP_COUNT)
        detect_ms = (time.perf_counter() - started) * MS_PER_S
        warmed.append({
            "crop_px": size,
            "inference_ms": detect_ms,
            "depth_ms": None if depth is None else depth.warm(size),
        })
    return warmed


async def _guarded(state: AppState, call: Any, *args: Any) -> Any:
    """Serialise every path that touches the model or the registry: TensorRT
    contexts are not thread-safe and the registry is mutable.
    """
    async with state.lock:
        try:
            return await run_in_threadpool(call, state, *args)
        except HTTPException:
            raise
        except Exception:  # noqa: BLE001 - the traceback stays server-side
            logger.exception("inference failed")
            raise HTTPException(HTTP_UNAVAILABLE, detail=UNAVAILABLE_DETAIL) from None


@app.post("/infer", response_model=InferenceResponse)
async def infer(
    meta: Annotated[str, Form()],
    crop_0: Annotated[UploadFile, File()],
    crop_1: Annotated[UploadFile, File()],
    crop_2: Annotated[UploadFile, File()],
    crop_3: Annotated[UploadFile, File()],
    crop_4: Annotated[UploadFile, File()],
    crop_5: Annotated[UploadFile, File()],
    state: State,
    api_key: ApiKey = None,
) -> InferenceResponse:
    _require_key(state, api_key)
    parsed = _parse_meta(meta)
    uploads = (crop_0, crop_1, crop_2, crop_3, crop_4, crop_5)
    raws = [
        await _read_crop(index, upload, state.settings.max_crop_bytes)
        for index, upload in enumerate(uploads)
    ]
    response: InferenceResponse = await _guarded(state, _decode_and_infer, parsed, raws)
    state.last_inference_ms = response.inference_ms
    return response


@app.get("/healthz")
async def healthz(state: State) -> dict[str, object]:
    detector = state.service.detector
    return {
        "detector": detector.name,
        "engine_ready": detector.engine_ready,
        "gpu": detector.gpu_name,
        "weights": detector.weights,
        "sessions": state.service.registry.session_count,
        "last_inference_ms": state.last_inference_ms,
    }


@app.get("/warmup")
async def warmup(state: State, crop_px: Annotated[int | None, Query()] = None,
                 api_key: ApiKey = None) -> dict[str, object]:
    _require_key(state, api_key)
    shapes = WARMUP_SHAPES if crop_px is None else (crop_px,)
    for size in shapes:
        if not CROP_PX_MIN <= size <= CROP_PX_MAX or size % CROP_PX_MULTIPLE_OF:
            raise HTTPException(HTTP_UNPROCESSABLE, detail=f"crop_px {size} is not a valid shape")
    warmed = await _guarded(state, _warm, shapes)
    depth = state.service.depth
    # `/healthz` deliberately keeps exactly its step-7 shape (step 8's monitor
    # asserts on it), so depth readiness is reported here instead.
    return {
        "engine_ready": state.service.detector.engine_ready,
        "warmed": warmed,
        "depth": {
            "backend": None if depth is None else depth.label,
            "ready": depth is not None and depth.ready,
        },
    }
