"""Dependency-free checks for the benchmark."""
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from cane.benchmarks.metrics import percentile, summarize
from cane.benchmarks.yolo import Frames, choose_device, instrument_stages, parser, synchronize


def fake_torch(cuda=False, mps=False):
    return SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: cuda),
        backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: mps)))


class BenchmarkTests(unittest.TestCase):
    def test_percentile(self):
        self.assertEqual(percentile([100, 0], 95), 95)
        self.assertEqual(percentile([7], 50), 7)
        with self.assertRaises(ValueError): percentile([], 95)

    def test_fps_uses_total_duration(self):
        s = summarize([{"predict_ms": 10, "frame_ms": 20}, {"predict_ms": 90, "frame_ms": 180}])
        self.assertEqual(s["predict_fps"], 20)
        self.assertEqual(s["processed_fps"], 10)
        self.assertEqual(s["timings_ms"]["predict_ms"]["p95"], 86)

    def test_device_selection(self):
        self.assertEqual(choose_device(fake_torch(True, True), "auto"), "cuda")
        self.assertEqual(choose_device(fake_torch(False, True), "auto"), "mps")
        self.assertEqual(choose_device(fake_torch(), "auto"), "cpu")
        with self.assertRaises(ValueError): choose_device(fake_torch(), "mps")

    def test_accelerator_completion(self):
        called = []
        torch = SimpleNamespace(cuda=SimpleNamespace(synchronize=lambda: called.append("cuda")),
                                mps=SimpleNamespace(synchronize=lambda: called.append("mps")))
        for device in ("cuda", "mps", "cpu"): synchronize(torch, device)
        self.assertEqual(called, ["cuda", "mps"])

    def test_single_capture_and_no_skipping(self):
        class Capture:
            reads = 0
            released = False
            def isOpened(self): return True
            def read(self):
                self.reads += 1
                return (True, f"image{self.reads}") if self.reads <= 2 else (False, None)
            def release(self): self.released = True
        capture = Capture()
        frames = Frames(SimpleNamespace(VideoCapture=lambda *args: capture, CAP_AVFOUNDATION=1200), None, 0, camera_timeout=0)
        self.assertEqual(frames.read(), (0, "frame:0", None, "image1"))
        self.assertEqual(frames.read(), (1, "frame:1", None, "image2"))
        with self.assertRaises(ValueError): frames.read()
        frames.close()
        self.assertTrue(capture.released)

    def test_camera_retries_startup_empty_reads_without_advancing_index(self):
        reads = iter([(False, None), (True, None), (True, "image")])
        capture = SimpleNamespace(isOpened=lambda: True, read=lambda: next(reads), release=lambda: None)
        cv2 = SimpleNamespace(VideoCapture=lambda *args: capture, CAP_AVFOUNDATION=1200)
        with patch("cane.benchmarks.yolo.time.sleep"):
            frames = Frames(cv2, None, 1)
            self.assertEqual(frames.read(), (0, "frame:0", None, "image"))
            self.assertEqual(frames.index, 1)

    def test_camera_timeout_is_bounded_and_actionable(self):
        capture = SimpleNamespace(isOpened=lambda: True, read=lambda: (False, None), release=lambda: None)
        cv2 = SimpleNamespace(VideoCapture=lambda *args: capture, CAP_AVFOUNDATION=1200)
        frames = Frames(cv2, None, 2, camera_timeout=1)
        with patch("cane.benchmarks.yolo.time.monotonic", side_effect=[0, 2]):
            with self.assertRaisesRegex(ValueError, "--list-cameras"):
                frames.read()
        self.assertEqual(frames.index, 0)

    def test_video_end_does_not_retry(self):
        capture = SimpleNamespace(isOpened=lambda: True, read=lambda: (False, None), release=lambda: None)
        cv2 = SimpleNamespace(VideoCapture=lambda *args: capture, CAP_AVFOUNDATION=1200)
        from pathlib import Path
        with patch.object(Path, "exists", return_value=True):
            frames = Frames(cv2, Path("route.mp4"), None)
        with patch("cane.benchmarks.yolo.time.sleep") as sleep:
            self.assertIsNone(frames.read())
            sleep.assert_not_called()

    def test_cli(self):
        args = parser().parse_args(["--source", "route.mp4", "--classes", "door", "overhanging obstacle"])
        self.assertEqual(args.classes, ["door", "overhanging obstacle"])
        self.assertEqual(args.model, "yoloe-26s-seg.pt")

    def test_stages_wait_before_and_after_and_preserve_result(self):
        calls = []
        torch = SimpleNamespace(mps=SimpleNamespace(synchronize=lambda: calls.append("sync")))
        def stage(value):
            calls.append("work")
            return value
        predictor = SimpleNamespace(preprocess=stage, inference=stage, postprocess=stage)
        timing = instrument_stages(predictor, torch, "mps")
        for name in ("preprocess", "inference", "postprocess"):
            self.assertEqual(getattr(predictor, name)(42), 42)
            self.assertGreaterEqual(timing[name], 0)
        self.assertEqual(calls, ["sync", "work", "sync"] * 3)


if __name__ == "__main__": unittest.main()
