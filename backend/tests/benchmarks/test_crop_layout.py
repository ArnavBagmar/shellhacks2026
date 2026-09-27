"""Layout of the seven pipeline crops into a spatial grid.

Pure geometry and composition: no camera, no models, no OpenCV window. The
crop yaws themselves are owned by ``cane.bridge.dewarp`` and must never be
restated here -- these tests read them from the production constant so a
change there fails loudly instead of silently drifting.
"""

from __future__ import annotations

import numpy as np
import pytest

from cane.benchmarks.crop_layout import (
    GRID_CELLS,
    GRID_COLS,
    GRID_ROWS,
    RING_GRID_COLS,
    grid_order,
    label_for_crop,
    label_for_yaw,
    labels_in_grid_order,
    tile_grid,
)
from cane.bridge.dewarp import CROP_COUNT, CROP_YAWS_DEG, NADIR_CROP_INDEX


class TestLabels:
    def test_every_production_yaw_has_a_label(self) -> None:
        for yaw in CROP_YAWS_DEG:
            assert label_for_yaw(yaw), f"no label for yaw {yaw}"

    def test_labels_name_the_direction(self) -> None:
        assert label_for_yaw(0.0) == "Front"
        assert label_for_yaw(180.0) == "Rear"
        assert label_for_yaw(60.0) == "Front-R"
        assert label_for_yaw(-60.0) == "Front-L"
        assert label_for_yaw(120.0) == "Rear-R"
        assert label_for_yaw(-120.0) == "Rear-L"

    def test_an_unknown_yaw_is_rejected_not_guessed(self) -> None:
        with pytest.raises(ValueError, match="45"):
            label_for_yaw(45.0)

    def test_negative_180_reads_as_rear(self) -> None:
        # -180 and +180 are the same bearing; the grid must not depend on sign.
        assert label_for_yaw(-180.0) == "Rear"

    def test_the_nadir_crop_is_labelled_down_not_by_its_placeholder_yaw(self) -> None:
        assert label_for_crop(NADIR_CROP_INDEX) == "Down"
        assert label_for_crop(0) == "Front"


class TestGridOrder:
    def test_covers_every_crop_exactly_once(self) -> None:
        order = grid_order()
        assert sorted(order) == list(range(CROP_COUNT))

    def test_grid_is_two_rows_of_four_with_one_empty_cell(self) -> None:
        assert GRID_ROWS * GRID_COLS == CROP_COUNT + 1
        assert (GRID_ROWS, GRID_COLS) == (2, 4)
        assert GRID_CELLS.count(None) == 1 and GRID_CELLS[-1] is None

    def test_front_row_on_top_left_to_right_then_down(self) -> None:
        assert labels_in_grid_order()[:GRID_COLS] == ["Front-L", "Front", "Front-R", "Down"]

    def test_rear_row_beneath_it_left_to_right(self) -> None:
        assert labels_in_grid_order()[GRID_COLS:] == ["Rear-L", "Rear", "Rear-R"]

    def test_front_sits_directly_above_rear(self) -> None:
        """The ring's centre column is the wearer's axis: Front over Rear."""
        labels = labels_in_grid_order()
        centre = RING_GRID_COLS // 2
        assert labels[centre] == "Front"
        assert labels[GRID_COLS + centre] == "Rear"


class TestTileGrid:
    @staticmethod
    def _crops(size: int = 8) -> tuple[np.ndarray, ...]:
        return tuple(
            np.full((size, size, 3), fill_value=index * 10, dtype=np.uint8)
            for index in range(CROP_COUNT)
        )

    def test_output_shape_is_rows_by_cols_of_the_crop(self) -> None:
        canvas = tile_grid(self._crops(8), np)
        assert canvas.shape == (8 * GRID_ROWS, 8 * GRID_COLS, 3)

    def test_each_cell_holds_the_crop_the_grid_order_names(self) -> None:
        size = 8
        canvas = tile_grid(self._crops(size), np)
        for cell, crop_index in enumerate(grid_order()):
            row, col = divmod(cell, GRID_COLS)
            patch = canvas[row * size : (row + 1) * size, col * size : (col + 1) * size]
            assert int(patch[0, 0, 0]) == crop_index * 10

    def test_the_empty_cell_is_black(self) -> None:
        size = 8
        canvas = tile_grid(self._crops(size), np)
        row, col = divmod(GRID_CELLS.index(None), GRID_COLS)
        patch = canvas[row * size : (row + 1) * size, col * size : (col + 1) * size]
        assert int(patch.max()) == 0

    def test_wrong_crop_count_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="7"):
            tile_grid(self._crops()[:4], np)

    def test_mismatched_crop_sizes_are_rejected(self) -> None:
        crops = list(self._crops(8))
        crops[2] = np.zeros((4, 4, 3), dtype=np.uint8)
        with pytest.raises(ValueError, match="same shape"):
            tile_grid(tuple(crops), np)

    def test_does_not_mutate_its_inputs(self) -> None:
        crops = self._crops(8)
        before = [crop.copy() for crop in crops]
        tile_grid(crops, np)
        for original, copy in zip(crops, before, strict=True):
            assert np.array_equal(original, copy)
