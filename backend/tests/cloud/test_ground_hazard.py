"""Ground-plane discontinuities: the pothole and the kerb the cane finds late.

The whole design constraint is in one sentence from the measured record: Depth
Anything V2 is affine-invariant in disparity, so there are no metres. What the
detector reports is therefore a **ratio** -- this patch of ground is 1.4x
farther than a flat floor would be here -- never a depth in centimetres.
"""

from __future__ import annotations

import numpy as np
import pytest

from cane.cloud.depth.geometry import ground_rows, row_inverse_distance
from cane.cloud.depth.ground_hazard import (
    MIN_DROP_RATIO,
    GroundHazard,
    detect_ground_hazard,
)
from cane.cloud.depth.profile import fit_ground

from .depth_synthetic import CROP_PX, affine_ground

SCALE = 4.0
OFFSET = 3.0
MAX_RESIDUAL = 0.25


def _fit(depth):
    return fit_ground(depth, CROP_PX, max_residual=MAX_RESIDUAL)


def _carve(depth, *, first_col: int, last_col: int, ratio: float, crop_px: int = CROP_PX):
    """Push one slab of floor `ratio` times farther away than it really is.

    Farther means a larger `d`, so a smaller `1/d`, so a **smaller** raw
    relative depth -- inverse depth grows towards the feet. A hole reads low.
    """
    rows = ground_rows(crop_px)
    assert rows is not None
    out = depth.copy()
    inverse_d = row_inverse_distance(np.arange(rows[0], rows[1], dtype=np.float64), crop_px)
    dropped = SCALE * (inverse_d / ratio) + OFFSET
    out[rows[0] : rows[1], first_col : last_col + 1] = dropped[:, None].astype(np.float32)
    return out


def _flat():
    return affine_ground(scale=SCALE, offset=OFFSET)


class TestNothingThere:
    def test_a_flat_floor_has_no_hazard(self) -> None:
        depth = _flat()

        assert detect_ground_hazard(depth, CROP_PX, _fit(depth)) is None

    def test_a_drop_shallower_than_the_floor_is_ignored(self) -> None:
        """Below the threshold is noise, a wet patch, or a shallow seam."""
        shallow = (1.0 + MIN_DROP_RATIO) / 2.0
        depth = _carve(_flat(), first_col=280, last_col=319, ratio=shallow)

        assert detect_ground_hazard(depth, CROP_PX, _fit(depth)) is None

    def test_a_step_up_is_not_reported(self) -> None:
        """MVP is descending hazards only: you fall down a kerb, you stub a toe
        on one. Rising ground must not be announced as a drop."""
        depth = _carve(_flat(), first_col=280, last_col=319, ratio=0.5)

        assert detect_ground_hazard(depth, CROP_PX, _fit(depth)) is None


class TestFindingIt:
    def test_a_drop_ahead_is_found(self) -> None:
        depth = _carve(_flat(), first_col=280, last_col=319, ratio=2.0)

        hazard = detect_ground_hazard(depth, CROP_PX, _fit(depth))

        assert isinstance(hazard, GroundHazard)
        assert hazard.drop_ratio > MIN_DROP_RATIO

    def test_the_column_points_at_the_drop(self) -> None:
        """Columns 0..15 split the 640 px crop into 40 px each, so a drop
        carved across 280..319 is exactly column 7."""
        depth = _carve(_flat(), first_col=280, last_col=319, ratio=2.0)

        hazard = detect_ground_hazard(depth, CROP_PX, _fit(depth))

        assert hazard is not None
        assert hazard.column == 7

    def test_a_drop_on_the_right_reports_a_right_column(self) -> None:
        depth = _carve(_flat(), first_col=600, last_col=639, ratio=2.0)

        hazard = detect_ground_hazard(depth, CROP_PX, _fit(depth))

        assert hazard is not None
        assert hazard.column == 15

    def test_a_wide_drop_reports_its_span(self) -> None:
        narrow = _carve(_flat(), first_col=280, last_col=319, ratio=2.0)
        # Four columns, a quarter of the width. Deliberately under half: the
        # fit's per-row median is the floor only while the floor is the
        # majority, so a hazard wider than the crop defeats its own detection.
        wide = _carve(_flat(), first_col=200, last_col=359, ratio=2.0)

        one = detect_ground_hazard(narrow, CROP_PX, _fit(narrow))
        many = detect_ground_hazard(wide, CROP_PX, _fit(wide))

        assert one is not None and many is not None
        assert many.span > one.span

    def test_a_deeper_drop_is_more_confident(self) -> None:
        shallow = _carve(_flat(), first_col=280, last_col=319, ratio=1.6)
        deep = _carve(_flat(), first_col=280, last_col=319, ratio=3.0)

        near = detect_ground_hazard(shallow, CROP_PX, _fit(shallow))
        far = detect_ground_hazard(deep, CROP_PX, _fit(deep))

        assert near is not None and far is not None
        assert far.confidence > near.confidence

    def test_confidence_never_leaves_the_unit_interval(self) -> None:
        depth = _carve(_flat(), first_col=280, last_col=319, ratio=40.0)

        hazard = detect_ground_hazard(depth, CROP_PX, _fit(depth))

        assert hazard is not None
        assert 0.0 <= hazard.confidence <= 1.0


class TestRefusingToGuess:
    """Sec 2.3 is explicit that a half-working curb detector is worse than
    none, so every uncertain input returns None rather than a maybe."""

    def test_no_ground_fit_means_no_hazard(self) -> None:
        depth = _flat()
        wall = fit_ground(
            np.full((CROP_PX, CROP_PX), 2.0, dtype=np.float32), CROP_PX, max_residual=MAX_RESIDUAL
        )
        assert wall.scale is None

        assert detect_ground_hazard(depth, CROP_PX, wall) is None

    def test_a_non_finite_map_is_refused(self) -> None:
        depth = _carve(_flat(), first_col=280, last_col=319, ratio=2.0)
        depth[-1, -1] = np.nan

        assert detect_ground_hazard(depth, CROP_PX, _fit(depth)) is None

    def test_a_crop_with_no_ground_rows_is_refused(self) -> None:
        tiny = 24
        depth = np.full((tiny, tiny), 2.0, dtype=np.float32)

        assert detect_ground_hazard(depth, tiny, _fit(_flat())) is None

    @pytest.mark.parametrize("bad", [0, -1])
    def test_a_nonsense_crop_size_is_refused(self, bad: int) -> None:
        depth = _flat()

        assert detect_ground_hazard(depth, bad, _fit(depth)) is None
