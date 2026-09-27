"""One inference response -> one ``ground`` frame, or honestly nothing.

Mirrors ``steer_frame`` exactly, including its discipline: every refusal is the
same rule, **never send a frame that looks like a measurement when it is not**.
Sec 2.3's argument that a half-working curb detector is worse than none is the
whole reason the confidence floor is here rather than on the phone.
"""

from __future__ import annotations

import pytest

from cane.bridge.ground_frame import (
    GROUND_TTL_MS,
    MIN_ANNOUNCE_CONFIDENCE,
    ground_from_response,
)
from cane.cloud.api_models import (
    SCHEMA_VERSION,
    GapProfile,
    GroundHazardReading,
    InferenceResponse,
)
from cane.contracts.awareness_event import Source

T_CAPTURE_MS = 1_790_123_456_789
YAWS = (0.0, 60.0, 120.0, 180.0, -120.0, -60.0)
CLEAR_COLUMNS = tuple(2.0 for _ in range(16))


def _hazard(column: int = 8, span: int = 1, drop_ratio: float = 2.4,
            confidence: float = 0.8) -> GroundHazardReading:
    return GroundHazardReading(
        column=column, span=span, drop_ratio=drop_ratio, confidence=confidence
    )


def _profile(crop_index: int = 0, hazard: GroundHazardReading | None = None) -> GapProfile:
    return GapProfile(
        crop_index=crop_index,
        columns=CLEAR_COLUMNS,
        band_rows=(120, 480),
        ground_fit_residual=0.02,
        anchored=True,
        ground_scale=4.0,
        ground_offset=3.0,
        ground_hazard=hazard,
    )


def _response(*profiles: GapProfile) -> InferenceResponse:
    return InferenceResponse(
        schema=SCHEMA_VERSION,
        t_capture_ms=T_CAPTURE_MS,
        inference_ms=3.0,
        detections=(),
        gap_profiles=tuple(profiles),
    )


class TestRefusals:
    def test_a_local_response_sends_nothing(self) -> None:
        """The local backend is built with depth=None, so a ground frame from
        it would be a silent fake of having looked at the floor."""
        response = _response(_profile(hazard=_hazard()))

        assert ground_from_response(response, YAWS, 0.0, Source.X4_LOCAL) is None

    def test_no_profiles_sends_nothing(self) -> None:
        assert ground_from_response(_response(), YAWS, 0.0, Source.X4_CLOUD) is None

    def test_a_clear_floor_sends_nothing(self) -> None:
        response = _response(_profile(hazard=None))

        assert ground_from_response(response, YAWS, 0.0, Source.X4_CLOUD) is None

    def test_a_hazard_under_the_confidence_floor_sends_nothing(self) -> None:
        timid = _hazard(confidence=MIN_ANNOUNCE_CONFIDENCE / 2)
        response = _response(_profile(hazard=timid))

        assert ground_from_response(response, YAWS, 0.0, Source.X4_CLOUD) is None

    def test_a_hazard_behind_the_wearer_sends_nothing(self) -> None:
        """You do not walk backwards. Crop 3 looks at 180 degrees."""
        response = _response(_profile(crop_index=3, hazard=_hazard()))

        assert ground_from_response(response, YAWS, 0.0, Source.X4_CLOUD) is None


class TestTheFrame:
    def test_a_hazard_ahead_becomes_a_frame(self) -> None:
        response = _response(_profile(crop_index=0, hazard=_hazard(column=8)))

        frame = ground_from_response(response, YAWS, 0.0, Source.X4_CLOUD)

        assert frame is not None
        assert frame.ttl_ms == GROUND_TTL_MS
        assert frame.source == Source.X4_CLOUD.value

    def test_the_capture_stamp_is_echoed_never_re_read(self) -> None:
        """Same rule as the steer frame: re-deriving it would put the cue in a
        different clock domain from the events beside it."""
        response = _response(_profile(hazard=_hazard()))

        frame = ground_from_response(response, YAWS, 0.0, Source.X4_CLOUD)

        assert frame is not None and frame.t_capture_ms == T_CAPTURE_MS

    def test_the_bearing_comes_from_the_column(self) -> None:
        left = _response(_profile(crop_index=0, hazard=_hazard(column=1)))
        right = _response(_profile(crop_index=0, hazard=_hazard(column=14)))

        left_frame = ground_from_response(left, YAWS, 0.0, Source.X4_CLOUD)
        right_frame = ground_from_response(right, YAWS, 0.0, Source.X4_CLOUD)

        assert left_frame is not None and right_frame is not None
        assert left_frame.bearing_deg < 0 < right_frame.bearing_deg

    def test_the_mount_offset_rotates_the_bearing(self) -> None:
        response = _response(_profile(hazard=_hazard(column=8)))

        plain = ground_from_response(response, YAWS, 0.0, Source.X4_CLOUD)
        offset = ground_from_response(response, YAWS, 10.0, Source.X4_CLOUD)

        assert plain is not None and offset is not None
        assert offset.bearing_deg == pytest.approx(plain.bearing_deg + 10.0)

    def test_a_wider_hazard_reports_a_wider_span(self) -> None:
        narrow = _response(_profile(hazard=_hazard(span=1)))
        wide = _response(_profile(hazard=_hazard(span=4)))

        one = ground_from_response(narrow, YAWS, 0.0, Source.X4_CLOUD)
        many = ground_from_response(wide, YAWS, 0.0, Source.X4_CLOUD)

        assert one is not None and many is not None
        assert many.span_deg > one.span_deg

    def test_the_most_confident_hazard_across_crops_wins(self) -> None:
        response = _response(
            _profile(crop_index=0, hazard=_hazard(column=2, confidence=0.5)),
            _profile(crop_index=1, hazard=_hazard(column=9, confidence=0.95)),
        )

        frame = ground_from_response(response, YAWS, 0.0, Source.X4_CLOUD)

        assert frame is not None
        assert frame.confidence == pytest.approx(0.95)

    def test_the_ratio_travels_and_no_depth_does(self) -> None:
        """The frame says 2.4x farther than the floor. It must never carry a
        centimetre, because the sensor cannot know one."""
        response = _response(_profile(hazard=_hazard(drop_ratio=2.4)))

        frame = ground_from_response(response, YAWS, 0.0, Source.X4_CLOUD)

        assert frame is not None
        assert frame.drop_ratio == pytest.approx(2.4)
        assert not hasattr(frame, "depth_m")
