"""Single-model YOLOE benchmark. Run with --help without ML dependencies."""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import platform
import re
import resource
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .metrics import summarize

DEFAULT_CLASSES = [
    "person", "bicycle", "scooter", "car", "bus", "truck", "chair",
    "door", "wall", "suitcase", "pole", "bollard", "barrier", "overhanging obstacle", "table", "waymo", "mouse",
]
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--source", type=Path, help="Image, video, or directory of images (sorted)")
    source.add_argument("--camera", type=int, help="Optional single-model live preview; prefer files for comparisons")
    source.add_argument("--list-cameras", action="store_true", help="Probe indices 0–5 without loading YOLOE")
    p.add_argument("--camera-timeout", type=float, default=10.0, help="Seconds to retry empty camera reads (default: 10)")
    p.add_argument("--model", default="yoloe-26s-seg.pt", help="YOLOE text-prompt .pt checkpoint (auto-downloads official names)")
    p.add_argument("--classes", nargs="+", default=DEFAULT_CLASSES)
    p.add_argument("--device", choices=["auto", "cpu", "mps", "cuda"], default="auto")
    p.add_argument("--imgsz", type=int, default=640)
    p.add_argument("--conf", type=float, default=0.25)
    p.add_argument("--iou", type=float, default=0.7)
    p.add_argument("--warmup", type=int, default=10)
    p.add_argument("--max-frames", type=int, default=300)
    p.add_argument("--save-every", type=int, default=30, help="Save an annotated JPG every N frames; 0 disables")
    p.add_argument("--show", action="store_true", help="OpenCV preview; q quits")
    p.add_argument("--output", type=Path, help="New output directory; existing directories are rejected")
    return p


def check_model(model: str) -> None:
    """Reject checkpoints this text-prompt baseline cannot drive, before imports."""
    if not model.endswith(".pt") or "-pf" in model:
        raise ValueError("Use a text-prompt YOLOE .pt checkpoint. Core ML and prompt-free models are not supported by this baseline.")
    if not Path(model).is_file() and not re.fullmatch(r"yoloe-(26[nsmlx]|11[sml]|v8[sml])-seg\.pt", model):
        raise ValueError(f"Weights not found: {model}. Use an existing file or an official YOLOE checkpoint name.")


def choose_device(torch, requested: str) -> str:
    available = {"cuda": torch.cuda.is_available(), "mps": torch.backends.mps.is_available(), "cpu": True}
    if requested == "auto":
        return next(name for name in ("cuda", "mps", "cpu") if available[name])
    if not available[requested]:
        raise ValueError(f"Requested {requested} is unavailable in this Python environment. Try --device cpu.")
    return requested


def synchronize(torch, device: str) -> None:
    if device == "cuda":
        torch.cuda.synchronize()
    elif device == "mps":
        torch.mps.synchronize()


def instrument_stages(predictor, torch, device: str) -> dict[str, float]:
    """Time the local predictor's stages; Ultralytics omits MPS stage sync."""
    timings = {}

    def wrap(name, original):
        def measured(*args, **kwargs):
            synchronize(torch, device)
            start = time.perf_counter()
            value = original(*args, **kwargs)
            synchronize(torch, device)
            timings[name] = (time.perf_counter() - start) * 1000
            return value
        return measured

    for name in ("preprocess", "inference", "postprocess"):
        setattr(predictor, name, wrap(name, getattr(predictor, name)))
    return timings


def records(result) -> list[dict]:
    """Keep original image coordinates and raw labels, not the wire enum."""
    boxes = result.boxes.cpu()
    polygons = result.masks.xy if result.masks is not None else None
    return [
        {"class_id": int(cls), "label": result.names[int(cls)], "confidence": float(conf),
         "xyxy": xyxy.tolist(),
         "mask_polygon_xy": polygons[i].tolist() if polygons is not None else None}
        for i, (xyxy, cls, conf) in enumerate(zip(boxes.xyxy, boxes.cls, boxes.conf))
    ]


class Frames:
    """One capture handle, one read per measured frame; no frame skipping."""
    def __init__(self, cv2, source: Path | None, camera: int | None, camera_timeout: float = 10.0):
        self.cv2, self.capture, self.index = cv2, None, 0
        self.camera_index, self.camera_timeout = camera, camera_timeout
        self.stop_event = None
        self.paths = None
        if source is not None:
            if not source.exists():
                raise ValueError(f"Input does not exist: {source}")
            if source.is_dir():
                self.paths = sorted(p for p in source.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
                if not self.paths:
                    raise ValueError(f"No supported images in {source}")
            elif source.suffix.lower() in IMAGE_SUFFIXES:
                self.paths = [source]
        if self.paths is None:
            if camera is not None and sys.platform == "darwin":
                self.capture = cv2.VideoCapture(camera, cv2.CAP_AVFOUNDATION)
            else:
                self.capture = cv2.VideoCapture(camera if camera is not None else str(source))
            if not self.capture.isOpened():
                self.capture.release()
                raise ValueError(f"Cannot open {'camera ' + str(camera) if camera is not None else source}. Check camera permission/index or video format.")
        self.is_camera = camera is not None

    def read(self):
        index = self.index
        if self.paths is not None:
            if index >= len(self.paths):
                return None
            path = self.paths[index]
            frame = self.cv2.imread(str(path))
            if frame is None:
                raise ValueError(f"Cannot decode image: {path}")
            identity = str(path.resolve())
            position = None
        else:
            deadline = time.monotonic() + self.camera_timeout
            ok, frame = self.capture.read()
            empty = not ok or frame is None or getattr(frame, "size", 1) == 0
            if empty and self.is_camera:
                print(f"Waiting up to {self.camera_timeout:g}s for camera {self.camera_index} to deliver a frame…", flush=True)
                while empty and time.monotonic() < deadline:
                    if self.stop_event is not None and self.stop_event.is_set():
                        return None
                    time.sleep(0.05)
                    ok, frame = self.capture.read()
                    empty = not ok or frame is None or getattr(frame, "size", 1) == 0
            if empty:
                if self.is_camera:
                    raise ValueError(
                        f"Camera {self.camera_index} opened but delivered no frame within "
                        f"{self.camera_timeout:g}s. Try --list-cameras to find a working index. "
                        "Allow Camera access for your terminal app in System Settings > Privacy & Security > Camera. "
                        "Close other camera apps. For an iPhone, enable Continuity Camera, "
                        "lock and mount the phone, or connect it by USB and trust this Mac. "
                        "Check that the iPhone works in FaceTime, then close FaceTime before retrying."
                    )
                return None
            identity = f"frame:{index}"
            position = None if self.is_camera else self.capture.get(self.cv2.CAP_PROP_POS_MSEC)
        self.index += 1
        return index, identity, position, frame

    def close(self):
        if self.capture is not None:
            self.capture.release()


class LatestFrames:
    """Newest-frame-only camera grab; one slot, never a queue.

    A thread reads the camera in a tight loop and keeps exactly one frame, so
    the consumer always gets the freshest one. Without it the driver's own
    buffering serves a frame from 100-300 ms ago and nothing in the logs says
    so -- the same failure the X4 bridge's `capture/grab_thread.py` exists to
    prevent.

    **Camera sources only.** Files, directories and video keep `Frames`'
    no-skipping guarantee, which is what makes two benchmark runs comparable
    frame for frame; dropping frames there would silently break that.

    `frame_id` therefore counts frames *processed*, and `identity` carries the
    camera's own grab sequence, so `dropped` is visible rather than implied.
    """

    def __init__(self, frames: Frames, timeout_s: float = 10.0):
        if not frames.is_camera:
            raise ValueError("LatestFrames is for cameras only; file inputs must not drop frames")
        self.frames, self.timeout_s = frames, timeout_s
        self.index = self.grabbed = self.dropped = 0
        self._state = threading.Condition()
        self._latest = None
        self._seq = self._taken = 0
        self._error: Exception | None = None
        self._ended = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._grab, name="camera-grab", daemon=True)
        self._thread.start()

    @property
    def stop_event(self):
        return self.frames.stop_event

    @stop_event.setter
    def stop_event(self, event):
        self.frames.stop_event = event

    def _grab(self) -> None:
        while not self._stop.is_set():
            try:
                item = self.frames.read()
            except (ValueError, OSError) as exc:  # camera timeout, decode failure
                with self._state:
                    self._error = exc
                    self._state.notify_all()
                return
            if item is None:
                with self._state:
                    self._ended = True
                    self._state.notify_all()
                return
            with self._state:
                if self._latest is not None:
                    # The consumer never took the previous frame: drop it and
                    # count it, rather than letting it queue up behind.
                    self.dropped += 1
                self._seq += 1
                self.grabbed += 1
                self._latest = item[3]
                self._state.notify_all()

    def read(self):
        with self._state:
            ready = self._state.wait_for(
                lambda: self._seq > self._taken or self._error is not None or self._ended,
                timeout=self.timeout_s)
            if self._error is not None:
                raise self._error
            if not ready or self._latest is None:
                return None
            frame, self._taken = self._latest, self._seq
            self._latest = None
        index, self.index = self.index, self.index + 1
        return index, f"camera-grab:{self._taken}", None, frame

    def close(self) -> None:
        self._stop.set()
        # The thread can be inside a blocking camera read; it is a daemon, so a
        # bounded join is enough and never holds up shutdown.
        self._thread.join(timeout=2.0)
        self.frames.close()


def run(args) -> Path | None:
    if args.camera_timeout <= 0:
        raise ValueError("camera-timeout must be greater than zero")
    if args.list_cameras:
        try:
            import cv2
        except ImportError as exc:
            raise ValueError("Install benchmark dependencies: uv sync --extra vision") from exc
        print("Probing indices 0–5; these are devices, not individual iPhone lenses.", flush=True)
        for index in range(6):
            capture = None
            try:
                capture = Frames(cv2, None, index, args.camera_timeout)
                frame = capture.read()[3]
                print(f"--camera {index}: OK, {frame.shape[1]}x{frame.shape[0]}", flush=True)
            except ValueError as exc:
                print(f"--camera {index}: unavailable ({exc})", flush=True)
            finally:
                if capture is not None:
                    capture.close()
        return None
    if args.imgsz <= 0 or args.imgsz % 32 or args.max_frames <= 0 or args.warmup < 1 or args.save_every < 0:
        raise ValueError("Use imgsz divisible by 32, max-frames > 0, warmup >= 1, save-every >= 0")
    if not 0 <= args.conf <= 1 or not 0 <= args.iou <= 1:
        raise ValueError("conf and iou must be between 0 and 1")
    check_model(args.model)
    try:
        import cv2
        import torch
        from ultralytics import YOLOE
    except ImportError as exc:
        raise ValueError("Install benchmark dependencies: uv sync --extra vision") from exc

    device = choose_device(torch, args.device)
    frames = Frames(cv2, args.source, args.camera, args.camera_timeout)
    output = args.output or Path("benchmark_runs") / datetime.now(timezone.utc).strftime("yoloe-%Y%m%dT%H%M%S.%fZ")
    rows = []
    try:
        start = time.perf_counter()
        first = frames.read()
        first_read_ms = (time.perf_counter() - start) * 1000
        camera_startup_ms = first_read_ms if args.camera is not None else None
        if camera_startup_ms is not None:
            # Opening/starting Continuity Camera is not steady-state processing.
            first_read_ms = 0.0
        if first is None:
            raise ValueError("Input contains no decodable frames")
        output.mkdir(parents=True, exist_ok=False)
        start = time.perf_counter()
        model = YOLOE(args.model)
        model.set_classes(args.classes)
        options = dict(device=device, imgsz=args.imgsz, conf=args.conf, iou=args.iou,
                       quantize=32, rect=False, verbose=False, save=False)
        setup_ms = (time.perf_counter() - start) * 1000
        print(f"Model: {args.model}; device: {device}; warming up {args.warmup} times", flush=True)
        for _ in range(args.warmup):
            model.predict(first[3], **options)
        synchronize(torch, device)
        stage_times = instrument_stages(model.predictor, torch, device)
        if device == "cuda":
            torch.cuda.reset_peak_memory_stats()
        metadata = {
            "schema": 1, "component": "yoloe_instance_segmentation", "status": "running",
            "model": args.model, "classes": args.classes, "device": device, "precision": "float32 requested",
            "imgsz": args.imgsz, "rect": False, "conf": args.conf, "iou": args.iou,
            "warmup": args.warmup, "max_frames": args.max_frames,
            "camera_startup_ms": camera_startup_ms, "camera_timeout_s": args.camera_timeout,
            "source": str(args.source.resolve()) if args.source else f"camera:{args.camera}",
            "platform": platform.platform(), "python": sys.version, "setup_ms": setup_ms,
            "hardware": torch.cuda.get_device_name() if device == "cuda" else platform.machine(),
            "versions": {n: importlib.metadata.version(n) for n in ("ultralytics", "torch", "numpy", "opencv-python")},
            "timing_note": "predict_ms and each predictor stage use synchronized wall time. frame_ms includes read, prediction, result conversion; excludes warmup, hashing, drawing, saving, display. Stage barriers add profiling overhead. No network or capture-to-feedback measurement.",
        }
        (output / "summary.json").write_text(json.dumps(metadata, indent=2))
        with (output / "timings.csv").open("w", newline="") as timings, (output / "detections.jsonl").open("w") as detections:
            writer = None
            item = first
            read_ms = first_read_ms
            while item is not None and len(rows) < args.max_frames:
                index, identity, position, frame = item
                synchronize(torch, device)
                start = time.perf_counter()
                result = model.predict(frame, **options)[0]
                synchronize(torch, device)
                predict_ms = (time.perf_counter() - start) * 1000
                if tuple(result.orig_shape) != tuple(frame.shape[:2]):
                    raise ValueError("Model result coordinates do not match the input image shape")
                start = time.perf_counter()
                objects = records(result)
                result_ms = (time.perf_counter() - start) * 1000
                row = {"frame_id": index, "read_ms": read_ms, "predict_ms": predict_ms,
                       "preprocess_ms": stage_times["preprocess"], "inference_ms": stage_times["inference"],
                       "postprocess_ms": stage_times["postprocess"], "result_ms": result_ms,
                       "frame_ms": read_ms + predict_ms + result_ms, "detections": len(objects)}
                rows.append(row)
                if writer is None:
                    writer = csv.DictWriter(timings, fieldnames=list(row))
                    writer.writeheader()
                writer.writerow(row)
                detections.write(json.dumps({"frame_id": index, "input": identity, "video_position_ms": position,
                    "shape_hw": list(frame.shape[:2]), "bgr_sha256": hashlib.sha256(frame.tobytes()).hexdigest(),
                    "detections": objects}) + "\n")
                save_image = args.save_every > 0 and index % args.save_every == 0
                if args.show or save_image:
                    annotated = result.plot()
                    cv2.putText(annotated, f"YOLOE {device} | predict {predict_ms:.1f} ms | {1000 / predict_ms:.1f} FPS", (12, 28),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)
                    if save_image and not cv2.imwrite(str(output / f"frame_{index:06d}.jpg"), annotated):
                        raise ValueError("Failed to save annotated frame")
                    if args.show:
                        cv2.imshow("YOLOE only - q to quit", annotated)
                        if cv2.waitKey(1) & 0xFF == ord("q"):
                            break
                if len(rows) % 30 == 0:
                    print(f"{len(rows)} frames; latest predict {predict_ms:.1f} ms", flush=True)
                if len(rows) >= args.max_frames:
                    break
                start = time.perf_counter()
                item = frames.read()
                read_ms = (time.perf_counter() - start) * 1000
        metadata.update(summarize(rows))
        metadata["status"] = "complete"
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        metadata["process_peak_rss_bytes"] = rss if sys.platform == "darwin" else rss * 1024
        metadata["cuda_peak_allocated_bytes"] = torch.cuda.max_memory_allocated() if device == "cuda" else None
        metadata["mps_current_allocated_bytes"] = torch.mps.current_allocated_memory() if device == "mps" else None
        (output / "summary.json").write_text(json.dumps(metadata, indent=2))
        print(json.dumps({"output": str(output), **summarize(rows)}, indent=2))
        return output
    finally:
        frames.close()
        if args.show:
            cv2.destroyAllWindows()


def main() -> None:
    args = parser().parse_args()
    try:
        run(args)
    except (ValueError, OSError, RuntimeError, ImportError) as exc:
        raise SystemExit(f"Benchmark failed: {exc}") from exc


if __name__ == "__main__":
    main()
