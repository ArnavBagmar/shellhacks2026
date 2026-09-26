"""The per-crop pipeline: detect -> map -> clamp -> track -> range/band ->
motion -> Detection (A8), including the three box geometries that manufacture
a fake closing speed (A6.6).

`InferenceService` is deliberately FastAPI-free: step 8 runs this exact class
in-process on the laptop behind a local detector, so `x4_local` and `x4_cloud`
cannot disagree about bands and closing speed at the moment the health
monitor flips.
"""

from __future__ import annotations

import numpy as np
import pytest

from cane.cloud.api_models import CROP_COUNT, InferenceMeta
from cane.cloud.detector.base import FrameDetector, RawDetection
from cane.cloud.detector.fake import ScriptedDetector
from cane.cloud.motion import MIN_BOX_H_PX
from cane.cloud.service import MAX_DETECTIONS_PER_CROP, InferenceService
from cane.cloud.settings import CloudSettings
from cane.cloud.tracker import TrackerRegistry
from cane.contracts.awareness_event import ObjectClass, RangeBand

T0 = 1_790_000_000_000
CROP_PX = 640
YAWS = (0.0, 60.0, 120.0, 180.0, -120.0, -60.0)


def meta(t_capture_ms: int = T0, crop_px: int = CROP_PX, rung: int = 1) -> InferenceMeta:
    return InferenceMeta(
        schema=1,
        t_capture_ms=t_capture_ms,
        rung=rung,
        crop_px=crop_px,
        yaws_deg=YAWS,
        session_id="svc-test",
    )


def images(crop_px: int = CROP_PX) -> tuple[np.ndarray, ...]:
    return tuple(np.zeros((crop_px, crop_px, 3), dtype=np.uint8) for _ in range(CROP_COUNT))


def person(y1: float, y2: float, x1: float = 260.0, x2: float = 340.0, conf: float = 0.9):
    return RawDetection(x1=x1, y1=y1, x2=x2, y2=y2, coco_name="person", confidence=conf)


def frame(*crop0: RawDetection) -> tuple[tuple[RawDetection, ...], ...]:
    return (tuple(crop0),) + tuple(() for _ in range(CROP_COUNT - 1))


def build(*frames: tuple[tuple[RawDetection, ...], ...], **overrides: object) -> InferenceService:
    settings = CloudSettings(**overrides)  # type: ignore[arg-type]
    return InferenceService(
        detector=ScriptedDetector(frames),
        registry=TrackerRegistry(max_sessions=settings.max_sessions,
                                 activation_threshold=settings.conf_emit),
        settings=settings,
    )


def test_scripted_detector_satisfies_the_protocol() -> None:
    assert isinstance(ScriptedDetector(), FrameDetector)
    assert ScriptedDetector().name == "fake"


def test_unmapped_coco_names_are_dropped() -> None:
    junk = RawDetection(x1=10.0, y1=10.0, x2=90.0, y2=210.0, coco_name="banana", confidence=0.99)
    service = build(frame(junk, person(200.0, 400.0)))
    response = service.infer(meta(), images())
    assert [d.cls for d in response.detections] == [ObjectClass.PERSON]


def test_a_detection_at_0_40_is_announced_on_its_first_frame() -> None:
    """R-A8. supervision's new-track floor is `track_activation_threshold +
    NEW_TRACK_SCORE_MARGIN`, so the tracker is built at `conf_emit - 0.1` and
    a box above the emit threshold starts a track the moment it is seen.
    Built at `conf_emit`, a 0.40 cyclist would never be announced at all.
    """
    service = build(frame(person(200.0, 400.0, conf=0.40)))
    detections = service.infer(meta(), images()).detections
    assert len(detections) == 1
    assert detections[0].confidence == pytest.approx(0.40)


def test_a_detection_at_0_30_never_starts_a_track_and_is_never_emitted() -> None:
    """Lowering the activation threshold must not lower what reaches the
    wire: the emit filter (`confidence < conf_emit`) is unchanged.
    """
    service = build(*[frame(person(200.0 + 4 * i, 400.0 + 4 * i, conf=0.30))
                      for i in range(3)])
    for index in range(3):
        assert service.infer(meta(T0 + 333 * index), images()).detections == ()


def test_a_0_20_detection_keeps_a_track_alive_but_is_never_emitted() -> None:
    """The BYTE half of ByteTrack: a 0.20 box cannot start a track and never
    reaches the wire, but it keeps an existing track alive through the blurry
    frame -- which is exactly where an overtaking cyclist's id churns.
    """
    service = build(
        frame(person(200.0, 400.0, conf=0.90)),
        frame(person(204.0, 404.0, conf=0.20)),
        frame(person(208.0, 408.0, conf=0.90)),
    )
    first = service.infer(meta(), images()).detections
    blurry = service.infer(meta(T0 + 333), images()).detections
    recovered = service.infer(meta(T0 + 666), images()).detections
    assert len(first) == 1 and blurry == () and len(recovered) == 1
    assert recovered[0].track_id == first[0].track_id


def test_boxes_are_normalised_by_the_decoded_shape() -> None:
    service = build(frame(person(y1=160.0, y2=480.0, x1=160.0, x2=320.0)))
    detection = service.infer(meta(), images()).detections[0]
    assert detection.cx == pytest.approx(240.0 / CROP_PX)
    assert detection.cy == pytest.approx(320.0 / CROP_PX)
    assert detection.w == pytest.approx(160.0 / CROP_PX)
    assert detection.h == pytest.approx(320.0 / CROP_PX)


def test_normalised_box_is_clamped_into_the_unit_square() -> None:
    """A detector overshoot of 0.0001 would otherwise be a pydantic 500."""
    overshoot = RawDetection(x1=-5.0, y1=-5.0, x2=CROP_PX + 5.0, y2=CROP_PX + 5.0,
                             coco_name="person", confidence=0.9)
    detection = build(frame(overshoot)).infer(meta(), images()).detections[0]
    assert 0.0 <= detection.cx <= 1.0 and 0.0 <= detection.w <= 1.0
    assert 0.0 <= detection.cy <= 1.0 and 0.0 <= detection.h <= 1.0


def test_zero_height_box_is_dropped() -> None:
    flat = RawDetection(x1=10.0, y1=100.0, x2=90.0, y2=100.0, coco_name="person", confidence=0.9)
    assert build(frame(flat)).infer(meta(), images()).detections == ()


def test_a_sub_8px_box_is_far_with_no_motion_sample() -> None:
    tiny_h = MIN_BOX_H_PX - 2.0
    approaching = [
        frame(person(y1=300.0, y2=300.0 + tiny_h)),
        frame(person(y1=300.0, y2=300.0 + tiny_h - 1.0)),
    ]
    service = build(*approaching)
    first = service.infer(meta(), images()).detections[0]
    second = service.infer(meta(T0 + 333), images()).detections[0]
    assert first.range_band is RangeBand.FAR and second.range_band is RangeBand.FAR
    assert first.closing_mps is None and second.closing_mps is None


def test_a_truncated_box_emits_a_band_but_never_a_closing_speed() -> None:
    """A box growing into the crop edge is the largest source of fake closing
    speed at 60-degree crops: its height tracks the border, not the object.
    """
    frames = [
        frame(person(y1=1.0, y2=400.0)),
        frame(person(y1=0.0, y2=520.0)),
        frame(person(y1=0.0, y2=CROP_PX - 1.0)),
    ]
    service = build(*frames)
    for index in range(3):
        detection = service.infer(meta(T0 + 333 * index), images()).detections[0]
        assert detection.range_band is not None
        assert detection.closing_mps is None


def test_closing_speed_appears_on_the_third_frame_of_a_track() -> None:
    """Frame 1 has no baseline (`None`), frame 2 has one raw sample and
    nothing to check its sign against (`0.0`, i.e. tracked and not moving),
    frame 3 is the first frame two consistent samples can speak for.
    """
    frames = [
        frame(person(y1=200.0, y2=280.0)),
        frame(person(y1=195.0, y2=290.0)),
        frame(person(y1=190.0, y2=300.0)),
    ]
    service = build(*frames)
    first = service.infer(meta(), images()).detections[0]
    second = service.infer(meta(T0 + 333), images()).detections[0]
    third = service.infer(meta(T0 + 666), images()).detections[0]
    assert first.closing_mps is None
    assert second.closing_mps == 0.0
    assert third.closing_mps is not None and third.closing_mps > 0.0
    assert first.track_id == second.track_id == third.track_id


def test_track_id_is_namespaced_per_crop_and_generation() -> None:
    both = ((person(200.0, 400.0),), (person(200.0, 400.0),)) + tuple(
        () for _ in range(CROP_COUNT - 2)
    )
    response = build(both).infer(meta(), images())
    ids = sorted(d.track_id for d in response.detections)
    assert len(ids) == 2 and ids[0] != ids[1]
    assert ids[0].startswith("0:") and ids[1].startswith("1:")


def test_max_detections_per_crop_caps_the_response() -> None:
    """A crowd must not produce a 2 MB response."""
    crowd = tuple(
        RawDetection(x1=float(24 * i), y1=100.0, x2=float(24 * i + 20), y2=300.0,
                     coco_name="person", confidence=0.60 + 0.001 * i)
        for i in range(MAX_DETECTIONS_PER_CROP + 15)
    )
    response = build(frame(*crowd)).infer(meta(), images())
    assert len(response.detections) == MAX_DETECTIONS_PER_CROP
    # The cap keeps the most confident boxes, not the first ones seen.
    assert min(d.confidence for d in response.detections) > 0.60


def test_the_response_echoes_the_capture_stamp_and_reports_a_duration() -> None:
    service = build(frame(person(200.0, 400.0)))
    response = service.infer(meta(T0 + 7), images())
    assert response.t_capture_ms == T0 + 7
    assert response.schema_version == 1
    assert response.inference_ms >= 0.0


def test_inference_ms_includes_the_decode_time_the_caller_measured() -> None:
    """Step 8's health monitor and step 16's bench must be able to trust it."""
    service = build(frame(person(200.0, 400.0)))
    plain = service.infer(meta(), images())
    with_decode = service.infer(meta(T0 + 333), images(), decode_ms=50.0)
    assert with_decode.inference_ms >= 50.0 > plain.inference_ms


def test_idle_sessions_are_swept_using_echoed_stamps() -> None:
    service = build(frame(person(200.0, 400.0)))
    service.infer(meta(), images())
    assert service.registry.session_count == 1
    service.infer(meta(T0 + 60_000), images())
    assert service.registry.session_count == 1  # only the live session survives


def test_service_module_does_not_import_fastapi() -> None:
    """Importing fastapi here would block step 8's in-process fallback."""
    import ast
    import pathlib

    import cane.cloud.service as service_module

    source = pathlib.Path(service_module.__file__).read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "fastapi" not in imported and "starlette" not in imported
