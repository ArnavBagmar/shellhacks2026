"""Depth normalization, coordinate, and capture-independent error checks."""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from contextlib import nullcontext

from cane.benchmarks.depth import center_region, parser
from cane.benchmarks.depth_model import load_depth, normalize_depth, predict_depth

try:
    import numpy as np
except ImportError:
    np = None


class DepthTests(unittest.TestCase):
    def test_defaults_and_unlimited_flag(self):
        args = parser().parse_args(["--camera", "1", "--max-frames", "0"])
        self.assertEqual(args.max_frames, 0)
        self.assertEqual(args.input_size, 280)
        self.assertEqual(args.camera, 1)

    def test_missing_source_has_useful_error(self):
        with self.assertRaisesRegex(ValueError, "Official implementation missing"):
            load_depth(Path('/definitely-not-present-depth-repo'), Path('absent.pth'), 'cpu')

    def test_center_region_uses_x_y_order(self):
        self.assertEqual(center_region(1920, 1080), (864, 1056, 486, 594))
        self.assertEqual(center_region(1, 1), (0, 1, 0, 1))

    def test_output_shape_must_match_frame(self):
        model = SimpleNamespace(infer_image=lambda *args: SimpleNamespace(shape=(20, 30)))
        torch = SimpleNamespace(inference_mode=nullcontext)
        with self.assertRaisesRegex(ValueError, "does not match original image"):
            predict_depth(model, SimpleNamespace(shape=(30, 20, 3)), 280, torch, 'cpu')

    @unittest.skipIf(np is None, 'Install depth extra for array checks')
    def test_nearer_values_remain_larger_after_normalization(self):
        raw = np.array([[2, 6], [4, 10]], dtype=np.float32)
        relative, low, high, flat = normalize_depth(raw)
        np.testing.assert_allclose(relative, [[0, .5], [.25, 1]])
        self.assertEqual((low, high, flat), (2, 10, False))
        np.testing.assert_array_equal(raw, [[2, 6], [4, 10]])

    @unittest.skipIf(np is None, 'Install depth extra for array checks')
    def test_flat_map_does_not_divide_by_zero(self):
        relative, _, _, flat = normalize_depth(np.ones((3, 4)))
        self.assertTrue(flat)
        self.assertTrue(np.isfinite(relative).all())
        self.assertFalse(relative.any())

    @unittest.skipIf(np is None, 'Install depth extra for array checks')
    def test_reject_invalid_maps(self):
        for raw in (np.array([[float('nan')]]), np.array([[float('inf')]]), np.empty((0, 1)), np.zeros((2, 2, 3))):
            with self.assertRaises(ValueError): normalize_depth(raw)


if __name__ == '__main__':
    unittest.main()
