"""Merged view: box depth sampling, palette direction, and layer gating."""
import unittest

try:
    import numpy as np
except ImportError:
    np = None

from cane.benchmarks.fused import (box_depth, center_region, display_payload, draw_detections, overlaps,
                                   parser, render, status_lines)
from cane.benchmarks.palette import STOPS, depth_color, depth_lut, readable_text


class FakeCV2:
    """Only the drawing calls render() makes, recorded for assertions."""
    LINE_AA = 16

    def __init__(self):
        self.calls, self.rects = [], []
    def addWeighted(self, first, alpha, second, beta, gamma):
        self.calls.append('blend')
        return (first * alpha + second * beta).astype(first.dtype)
    def line(self, *args): self.calls.append('line')
    def rectangle(self, canvas, first, second, *args):
        self.calls.append('rectangle')
        self.rects.append((*first, *second))
    def polylines(self, *args): self.calls.append('polylines')
    def putText(self, canvas, text, *args): self.calls.append(('text', text))
    def labels(self): return [text for call in self.calls if isinstance(call, tuple) for text in call[1:]]
    def getTextSize(self, text, font, scale, thickness): return (len(text) * 8, 11), 3
    def resize(self, image, size): return np.zeros((size[1], size[0]) + image.shape[2:], dtype=image.dtype)
    def applyColorMap(self, image, lut): return np.dstack([image] * 3)


class ArgumentTests(unittest.TestCase):
    def test_defaults_run_both_layers(self):
        args = parser().parse_args(['--camera', '0', '--show'])
        self.assertFalse(args.no_depth or args.no_detection)
        # 196, deliberately not the depth benchmark's 280: see parser().
        self.assertEqual((args.input_size, args.imgsz, args.opacity), (196, 640, 35))
        self.assertFalse(args.no_latest_frame)
        self.assertEqual(args.model, 'yoloe-26s-seg.pt')

    def test_either_layer_can_be_switched_off_at_startup(self):
        self.assertTrue(parser().parse_args(['--camera', '0', '--no-depth']).no_depth)
        self.assertTrue(parser().parse_args(['--camera', '0', '--no-detection']).no_detection)

    def test_center_region_uses_x_y_order(self):
        self.assertEqual(center_region(1920, 1080), (864, 1056, 486, 594))
        self.assertEqual(center_region(1, 1), (0, 1, 0, 1))

    def test_status_lines_state_what_is_off_and_never_claim_meters(self):
        row = {'frame_ms': 50.0, 'depth_predict_ms': 30.0, 'detect_ms': 20.0}
        both = status_lines('mps', row, 3, True, True, {'center_relative_depth': 0.5, 'flat': False})
        self.assertIn('20.0 FPS', both[0])
        self.assertIn('3 objects', both[0])
        self.assertIn('not meters', both[1])
        self.assertIn('Center 0.50', both[1])
        off = status_lines('cpu', row, 0, False, False, None)
        self.assertIn('depth off', off[0])
        self.assertIn('detection off', off[0])
        self.assertIn('Depth layer off', off[1])
        self.assertNotIn('Center', off[1])

    def test_flat_frame_is_announced(self):
        row = {'frame_ms': 0.0, 'depth_predict_ms': 0.0, 'detect_ms': 0.0}
        lines = status_lines('cpu', row, 0, True, False, {'center_relative_depth': 0.0, 'flat': True})
        self.assertIn('-- FPS', lines[0])
        self.assertIn('FLAT', lines[1])


@unittest.skipIf(np is None, 'Install depth extra for array checks')
class BoxDepthTests(unittest.TestCase):
    def test_uses_only_the_central_quarter_area_of_the_box(self):
        relative = np.zeros((100, 100), dtype=np.float32)
        relative[25:75, 25:75] = 1.0  # exactly the central 25% area of the box
        self.assertEqual(box_depth(np, relative, [0, 0, 100, 100]), 1.0)

    def test_reversed_and_out_of_frame_boxes_are_clipped(self):
        relative = np.arange(40, dtype=np.float32).reshape(5, 8) / 39
        self.assertEqual(box_depth(np, relative, [8, 5, 0, 0]), box_depth(np, relative, [0, 0, 8, 5]))
        self.assertIsNotNone(box_depth(np, relative, [-50, -50, 400, 400]))

    def test_degenerate_box_still_samples_one_pixel(self):
        relative = np.full((6, 6), 0.25, dtype=np.float32)
        self.assertEqual(box_depth(np, relative, [3, 3, 3, 3]), 0.25)
        self.assertEqual(box_depth(np, relative, [2.4, 2.4, 2.6, 2.6]), 0.25)

    def test_depth_is_read_at_full_resolution_not_display_resolution(self):
        relative = np.zeros((200, 400), dtype=np.float32)
        relative[100:150, 200:300] = 0.8
        self.assertAlmostEqual(box_depth(np, relative, [200, 100, 300, 150]), 0.8)


@unittest.skipIf(np is None, 'Install depth extra for array checks')
class PaletteTests(unittest.TestCase):
    def test_far_end_is_light_green_and_near_end_is_dark_orange(self):
        far, near = depth_color(np, 0.0), depth_color(np, 1.0)
        far_blue, far_green, far_red = far
        near_blue, near_green, near_red = near
        self.assertGreater(far_green, max(far_red, far_blue), 'farthest colour should read as green')
        self.assertGreater(near_red, near_green, 'nearest colour should read as orange')
        self.assertGreater(near_green, near_blue)
        self.assertGreater(sum(far), sum(near) * 2, 'far end must be much lighter than the near end')

    def test_lightness_falls_as_objects_get_closer(self):
        luminance = [sum(channel * weight for channel, weight in zip(depth_color(np, value), (0.0722, 0.7152, 0.2126)))
                     for value in (0.0, 0.25, 0.5, 0.75, 1.0)]
        self.assertEqual(luminance, sorted(luminance, reverse=True))

    def test_lut_is_a_valid_opencv_colormap_and_matches_sampled_colours(self):
        lut = depth_lut(np)
        self.assertEqual(lut.shape, (256, 1, 3))
        self.assertEqual(lut.dtype, np.uint8)
        for value in (0.0, 0.33, 1.0):
            self.assertEqual(depth_color(np, value), tuple(int(c) for c in lut[round(value * 255), 0]))
        self.assertEqual(depth_color(np, -5), depth_color(np, 0.0))
        self.assertEqual(depth_color(np, 5), depth_color(np, 1.0))

    def test_stops_are_sorted_and_span_the_full_range(self):
        positions = [stop[0] for stop in STOPS]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual((positions[0], positions[-1]), (0.0, 1.0))

    def test_label_text_stays_readable_on_both_ends(self):
        self.assertEqual(readable_text(depth_color(np, 0.0)), (20, 20, 20))
        self.assertEqual(readable_text(depth_color(np, 1.0)), (255, 255, 255))


@unittest.skipIf(np is None, 'Install depth extra for array checks')
class LabelTests(unittest.TestCase):
    def detection(self, label, relative, xyxy):
        return {'label': label, 'confidence': 0.9, 'xyxy': xyxy, 'mask_polygon_xy': None,
                'relative_depth': relative}

    def test_the_nearer_label_wins_an_overlapping_slot(self):
        cv2 = FakeCV2()
        canvas = np.zeros((200, 400, 3), dtype=np.uint8)
        # Sorted far to near, exactly as the frame loop hands them over.
        far = self.detection('person', 0.2, [50, 90, 90, 160])
        near = self.detection('car', 0.8, [52, 92, 120, 170])
        draw_detections(cv2, np, canvas, [far, near], masks=False)
        drawn = cv2.labels()
        self.assertEqual(drawn, ['car 0.90 | rel 0.80'])

    def test_separated_objects_both_keep_their_labels(self):
        cv2 = FakeCV2()
        canvas = np.zeros((200, 400, 3), dtype=np.uint8)
        pair = [self.detection('person', 0.2, [10, 20, 60, 90]),
                self.detection('car', 0.8, [200, 150, 300, 190])]
        draw_detections(cv2, np, canvas, pair, masks=False)
        self.assertEqual(len(cv2.labels()), 2)

    def test_label_plates_stay_inside_the_canvas(self):
        cv2 = FakeCV2()
        canvas = np.zeros((200, 400, 3), dtype=np.uint8)
        edges = [self.detection('overhanging obstacle', 0.5, [380, 0, 399, 30]),
                 self.detection('pole', 0.5, [-20, 180, 40, 199])]
        draw_detections(cv2, np, canvas, edges, masks=False)
        self.assertEqual(len(cv2.labels()), 2)
        # The last two rectangles are the label plates, drawn after the boxes.
        for x0, y0, x1, y1 in cv2.rects[-2:]:
            self.assertGreaterEqual(x0, 0)
            self.assertGreaterEqual(y0, 0)
            self.assertLessEqual(x1, 400)

    def test_overlap_test_is_exclusive_at_the_shared_edge(self):
        self.assertFalse(overlaps((0, 0, 10, 10), [(10, 0, 20, 10)]))
        self.assertTrue(overlaps((0, 0, 10, 10), [(9, 9, 20, 20)]))
        self.assertFalse(overlaps((0, 0, 10, 10), []))


@unittest.skipIf(np is None, 'Install depth extra for array checks')
class RenderTests(unittest.TestCase):
    def payload(self, cv2, with_depth=True):
        frame = np.full((40, 60, 3), 100, dtype=np.uint8)
        relative = np.linspace(0, 1, 40 * 60, dtype=np.float32).reshape(40, 60) if with_depth else None
        detections = [{'label': 'chair', 'confidence': 0.9, 'xyxy': [5, 5, 30, 30],
                       'mask_polygon_xy': [[5, 5], [30, 5], [30, 30]], 'relative_depth': 0.9 if with_depth else None}]
        return display_payload(cv2, np, frame, relative, detections, 1.0, (60, 40))

    def test_one_canvas_carries_both_layers(self):
        cv2 = FakeCV2()
        payload = self.payload(cv2)
        state = {'depth': True, 'objects': True, 'masks': True, 'corridor': True}
        canvas = render(cv2, np, payload, 0.5, state)
        self.assertEqual(canvas.shape, payload['frame'].shape)
        self.assertIn('blend', cv2.calls)
        self.assertIn('polylines', cv2.calls)
        self.assertGreaterEqual(cv2.calls.count('line'), 2)

    def test_layers_switch_off_independently(self):
        cv2 = FakeCV2()
        payload = self.payload(cv2)
        render(cv2, np, payload, 0.5, {'depth': False, 'objects': True, 'masks': False, 'corridor': False})
        self.assertNotIn('blend', cv2.calls)
        self.assertNotIn('polylines', cv2.calls)
        self.assertNotIn('line', cv2.calls)
        self.assertIn('rectangle', cv2.calls)
        cv2.calls.clear()
        render(cv2, np, payload, 0.5, {'depth': True, 'objects': False, 'masks': True, 'corridor': False})
        self.assertIn('blend', cv2.calls)
        self.assertNotIn('rectangle', cv2.calls)

    def test_render_never_mutates_the_frame_it_was_given(self):
        cv2 = FakeCV2()
        payload = self.payload(cv2)
        before = payload['frame'].copy()
        render(cv2, np, payload, 0.5, {'depth': True, 'objects': True, 'masks': True, 'corridor': True})
        np.testing.assert_array_equal(payload['frame'], before)

    def test_detection_only_run_has_no_heatmap_and_neutral_boxes(self):
        cv2 = FakeCV2()
        payload = self.payload(cv2, with_depth=False)
        self.assertIsNone(payload['heat'])
        self.assertFalse(payload['has_depth'])
        render(cv2, np, payload, 0.5, {'depth': True, 'objects': True, 'masks': True, 'corridor': False})
        self.assertNotIn('blend', cv2.calls)

    def test_display_payload_scales_boxes_and_masks_together(self):
        cv2 = FakeCV2()
        frame = np.zeros((40, 60, 3), dtype=np.uint8)
        relative = np.zeros((40, 60), dtype=np.float32)
        detections = [{'label': 'car', 'confidence': 0.5, 'xyxy': [10, 10, 20, 20],
                       'mask_polygon_xy': [[10, 10], [20, 20]], 'relative_depth': 0.1}]
        payload = display_payload(cv2, np, frame, relative, detections, 0.5, (30, 20))
        self.assertEqual(payload['frame'].shape[:2], (20, 30))
        self.assertEqual(payload['detections'][0]['xyxy'], [5.0, 5.0, 10.0, 10.0])
        self.assertEqual(payload['detections'][0]['mask_polygon_xy'], [[5.0, 5.0], [10.0, 10.0]])
        self.assertEqual(detections[0]['xyxy'], [10, 10, 20, 20], 'full-resolution records must be untouched')


if __name__ == '__main__':
    unittest.main()
