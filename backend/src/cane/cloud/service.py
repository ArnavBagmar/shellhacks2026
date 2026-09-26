"""`detect -> map -> clamp -> track -> range/band -> motion -> Detection` (A8).

This module imports **no** web framework. Step 8's local fallback runs this
exact class in-process on the laptop behind a CoreML detector, so `x4_local`
and `x4_cloud` cannot disagree about bands and closing speed at the moment
the health monitor flips.

It reads no clock either: `perf_counter` measures a duration, and every
motion delta is taken over the *echoed* `t_capture_ms` of consecutive
requests in the same session.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Final, Sequence

import numpy as np
from numpy.typing import NDArray

from cane.cloud.api_models import (
    SCHEMA_VERSION,
    Detection,
    GapProfile,
    InferenceMeta,
    InferenceResponse,
)
from cane.cloud.class_map import height_m, map_coco
from cane.cloud.depth.head import DepthHead
from cane.cloud.detector.base import FrameDetector, RawDetection
from cane.cloud.motion import (
    EDGE_EPS_PX,
    MIN_BOX_H_PX,
    band_for_range,
    range_m_from_height,
    update_motion,
)
from cane.cloud.settings import CloudSettings
from cane.cloud.tracker import SessionCrop, TrackerRegistry
from cane.contracts.awareness_event import ObjectClass, RangeBand

logger = logging.getLogger(__name__)

MAX_DETECTIONS_PER_CROP: Final = 20
MS_PER_S: Final = 1000.0
NORM_MIN: Final = 0.0
NORM_MAX: Final = 1.0

Candidate = tuple[RawDetection, ObjectClass]
_CLASS_IDS: Final = {cls: index for index, cls in enumerate(ObjectClass)}


def _clamp_unit(value: float) -> float:
    return min(max(value, NORM_MIN), NORM_MAX)


@dataclass(frozen=True, slots=True)
class _CropContext:
    """Everything one crop's boxes are interpreted against.

    `height_px`/`width_px` come from the *decoded* image, never from
    `meta.crop_px`: a lying meta must not be able to move an object two
    bands closer.
    """

    meta: InferenceMeta
    entry: SessionCrop
    crop_index: int
    height_px: int
    width_px: int


class InferenceService:
    """One frame in, one `InferenceResponse` out. Synchronous and stateful:
    the tracker registry is the state, and the caller serialises access.
    """

    def __init__(
        self,
        detector: FrameDetector,
        registry: TrackerRegistry,
        settings: CloudSettings,
        depth: DepthHead | None = None,
    ) -> None:
        self._detector = detector
        self._registry = registry
        self._settings = settings
        # Optional and last: step 8's local fallback constructs this class
        # positionally with three arguments and has no depth model at all, so
        # `x4_local` simply carries no gap profiles (plan Sec 4.2).
        self._depth = depth

    @property
    def detector(self) -> FrameDetector:
        return self._detector

    @property
    def depth(self) -> DepthHead | None:
        return self._depth

    @property
    def registry(self) -> TrackerRegistry:
        return self._registry

    def infer(
        self,
        meta: InferenceMeta,
        images: Sequence[NDArray[np.uint8]],
        decode_ms: float = 0.0,
    ) -> InferenceResponse:
        """`decode_ms` is the caller's JPEG decode cost, folded in so that
        `inference_ms` is the whole server-side number step 8's health
        monitor and step 16's bench compare against -- decode included.
        """
        started = time.perf_counter()
        per_crop = self._detector.detect(images)
        detect_ms = (time.perf_counter() - started) * MS_PER_S

        detections: list[Detection] = []
        for crop_index, raws in enumerate(per_crop):
            detections.extend(self._for_crop(meta, crop_index, images[crop_index], raws))
        self._registry.sweep(meta.t_capture_ms)

        # Depth runs *after* the detections are built: they are the
        # safety-critical half of this response, and steering is never allowed
        # to take them down with it.
        gap_profiles, depth_ms = self._gap_profiles(images)

        inference_ms = decode_ms + (time.perf_counter() - started) * MS_PER_S
        logger.debug(
            "decode %.1f ms, detect %.1f ms, depth %.1f ms, total %.1f ms, %d detections, %d profiles",
            decode_ms, detect_ms, depth_ms, inference_ms, len(detections), len(gap_profiles),
        )
        return InferenceResponse(
            schema=SCHEMA_VERSION,
            t_capture_ms=meta.t_capture_ms,
            inference_ms=inference_ms,
            detections=tuple(detections),
            gap_profiles=gap_profiles,
        )

    def _gap_profiles(
        self, images: Sequence[NDArray[np.uint8]]
    ) -> tuple[tuple[GapProfile, ...], float]:
        """The depth head already swallows its own failures; this is the belt.

        An unexpected escape -- a CUDA OOM, a driver fault -- must cost the
        frame its profiles and nothing else, so `/infer` still answers 200 with
        the detections the wearer's safety actually depends on.
        """
        if self._depth is None:
            return (), 0.0
        try:
            result = self._depth.profiles(images)
        except Exception:  # noqa: BLE001 - never at the cost of the detections
            logger.exception("the depth head failed; this response carries no gap profiles")
            return (), 0.0
        return result.profiles, result.depth_ms

    def _for_crop(
        self,
        meta: InferenceMeta,
        crop_index: int,
        image: NDArray[np.uint8],
        raws: Sequence[RawDetection],
    ) -> list[Detection]:
        candidates = self._candidates(raws)
        context = _CropContext(
            meta=meta,
            entry=self._registry.entry_for(
                meta.session_id, crop_index, meta.rung, meta.t_capture_ms
            ),
            crop_index=crop_index,
            height_px=int(image.shape[0]),
            width_px=int(image.shape[1]),
        )
        emitted = []
        for box in context.entry.tracker.update(*_arrays(candidates)):
            raw, cls = candidates[box.det_index]
            # Compared before any float32 -> float64 widening (A8).
            if raw.confidence < self._settings.conf_emit:
                continue
            detection = self._build(context, box.track_id, raw, cls)
            if detection is not None:
                emitted.append(detection)
        return emitted

    def _candidates(self, raws: Sequence[RawDetection]) -> list[Candidate]:
        """Mapped, non-degenerate, highest confidence first, capped so that a
        crowd cannot produce a multi-megabyte response.
        """
        kept = [
            (raw, cls)
            for raw in raws
            if (cls := map_coco(raw.coco_name)) is not None
            and raw.height_px > 0.0
            and raw.width_px > 0.0
        ]
        kept.sort(key=lambda pair: pair[0].confidence, reverse=True)
        return kept[: min(self._settings.max_det_per_crop, MAX_DETECTIONS_PER_CROP)]

    def _build(
        self, ctx: _CropContext, track_id: str, raw: RawDetection, cls: ObjectClass
    ) -> Detection | None:
        norm_w = _clamp_unit(raw.width_px / ctx.width_px)
        norm_h = _clamp_unit(raw.height_px / ctx.height_px)
        if norm_w <= 0.0 or norm_h <= 0.0:
            return None
        band, closing_mps = self._range_and_motion(ctx, track_id, raw, cls)
        return Detection(
            crop_index=ctx.crop_index,
            cx=_clamp_unit((raw.x1 + raw.x2) / 2.0 / ctx.width_px),
            cy=_clamp_unit((raw.y1 + raw.y2) / 2.0 / ctx.height_px),
            w=norm_w,
            h=norm_h,
            cls=cls,
            confidence=_clamp_unit(raw.confidence),
            track_id=track_id,
            closing_mps=closing_mps,
            range_band=band,
        )

    def _range_and_motion(
        self, ctx: _CropContext, track_id: str, raw: RawDetection, cls: ObjectClass
    ) -> tuple[RangeBand, float | None]:
        """A band always; a motion sample only from a box whose height means
        something. `range_m` is computed here and dies here -- the X4 path
        states bands, never metres.
        """
        box_height_m = height_m(raw.coco_name, cls)
        if raw.height_px < MIN_BOX_H_PX:
            # A few pixels of noise: "something far away" is all we can say.
            return RangeBand.FAR, None
        range_m = range_m_from_height(raw.height_px, box_height_m, ctx.height_px)
        if range_m is None:
            return RangeBand.FAR, None
        band = band_for_range(range_m)
        if raw.y1 <= EDGE_EPS_PX or raw.y2 >= ctx.height_px - EDGE_EPS_PX:
            # Vertically truncated: the height now tracks the crop border, not
            # the object. Emit the band, take no sample, leave the baseline.
            return band, None
        state, closing_mps = update_motion(
            ctx.entry.motion.get(track_id), range_m, box_height_m, ctx.meta.t_capture_ms
        )
        ctx.entry.motion[track_id] = state
        return band, closing_mps


def _arrays(
    candidates: Sequence[Candidate],
) -> tuple[NDArray[np.float32], NDArray[np.float32], NDArray[np.int32]]:
    """ByteTrack needs non-None confidence and class_id, and an empty crop
    must still be fed through so lost tracks keep ageing.
    """
    count = len(candidates)
    xyxy = np.empty((count, 4), dtype=np.float32)
    confidence = np.empty(count, dtype=np.float32)
    class_ids = np.empty(count, dtype=np.int32)
    for index, (raw, cls) in enumerate(candidates):
        xyxy[index] = (raw.x1, raw.y1, raw.x2, raw.y2)
        confidence[index] = raw.confidence
        class_ids[index] = _CLASS_IDS[cls]
    return xyxy, confidence, class_ids
