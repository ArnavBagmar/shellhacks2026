"""Live view of the six crops the cloud actually receives.

``cane.benchmarks.fused`` reads a camera through OpenCV, which on macOS can
only give the X4's raw dual-fisheye -- a picture no part of this system ever
processes. This runner takes the same path the bridge does: AVFoundation for
the stitched 2:1 equirect, then ``cane.bridge.dewarp`` for six 60-degree
square crops, and shows them arranged the way a wearer would read them.

What you see here is what the inference service sees, one dewarp apart.

    cd backend
    UV_PROJECT_ENVIRONMENT=.venv-vision PYTHONPATH=src .venv-vision/bin/python \\
        -m cane.benchmarks.crops --source x4 --show

``cane.bridge.snapshot`` writes a one-shot contact sheet of the same crops for
the docs; this is the live, model-annotated view. Detection is optional
(``--no-detection``) so the geometry alone can be checked without a model.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cane.benchmarks.crop_layout import grid_order, labels_in_grid_order, tile_grid
from cane.benchmarks.yolo import DEFAULT_CLASSES, check_model, choose_device, records
from cane.bridge.capture.synthetic_source import SyntheticEquirectSource
from cane.bridge.dewarp import CROP_COUNT, dewarp_equirect

logger = logging.getLogger(__name__)

WINDOW = "Smart cane - the six crops the cloud sees"
SOURCE_X4 = "x4"
SOURCE_SYNTHETIC = "synthetic"
LABEL_BAR_PX = 22
LABEL_BG = (28, 28, 32)
LABEL_FG = (235, 235, 235)
FRONT_ACCENT = (240, 196, 130)
FONT = 0  # cv2.FONT_HERSHEY_SIMPLEX
READ_RETRY_LIMIT = 30


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--source",
        default=SOURCE_X4,
        choices=[SOURCE_X4, SOURCE_SYNTHETIC],
        help="x4 = the real camera via AVFoundation; synthetic needs no hardware",
    )
    p.add_argument("--crop-px", type=int, default=640, help="Square crop size, as the rungs use")
    p.add_argument("--model", default="yoloe-26s-seg.pt", help="YOLOE text-prompt .pt checkpoint")
    p.add_argument("--classes", nargs="+", default=DEFAULT_CLASSES)
    p.add_argument("--device", choices=["auto", "cuda", "mps", "cpu"], default="auto")
    p.add_argument("--imgsz", type=int, default=640)
    p.add_argument("--conf", type=float, default=0.25)
    p.add_argument("--iou", type=float, default=0.7)
    p.add_argument("--max-frames", type=int, default=0, help="0 runs until q or Ctrl-C")
    p.add_argument("--save-every", type=int, default=0, help="Save a tiled JPG every N frames")
    p.add_argument("--show", action="store_true", help="Open a preview window")
    p.add_argument("--no-detection", action="store_true", help="Geometry only, no model")
    p.add_argument("--output", type=Path)
    return p


def validate(args: argparse.Namespace) -> None:
    """Fail fast and specifically, before any camera or model is touched."""
    if args.crop_px <= 0:
        raise ValueError(f"crop-px must be > 0, got {args.crop_px}")
    if args.imgsz <= 0 or args.imgsz % 32:
        raise ValueError("imgsz must be a positive multiple of 32")
    if args.max_frames < 0 or args.save_every < 0:
        raise ValueError("max-frames and save-every must be >= 0")
    if not 0 <= args.conf <= 1 or not 0 <= args.iou <= 1:
        raise ValueError("conf and iou must be between 0 and 1")
    if not args.show and not args.save_every:
        raise ValueError(
            "nothing would be observable: pass --show, or --save-every to write frames"
        )


def open_source(name: str) -> Any:
    """The bridge's own sources, so this preview cannot drift from it."""
    if name == SOURCE_SYNTHETIC:
        return SyntheticEquirectSource()
    from cane.bridge.capture.avfoundation_source import open_x4

    return open_x4()


def crop_labels() -> dict[int, str]:
    """Crop index -> direction name, for both drawing and saved records."""
    return dict(zip(grid_order(), labels_in_grid_order()))


def label_crop(cv2: Any, image: Any, text: str, is_front: bool) -> Any:
    """Return a copy of ``image`` with a direction bar along its top."""
    canvas = image.copy()
    width = canvas.shape[1]
    cv2.rectangle(canvas, (0, 0), (width, LABEL_BAR_PX), LABEL_BG, -1)
    colour = FRONT_ACCENT if is_front else LABEL_FG
    cv2.putText(canvas, text, (6, LABEL_BAR_PX - 7), FONT, 0.5, colour, 1, cv2.LINE_AA)
    cv2.rectangle(canvas, (0, 0), (width - 1, canvas.shape[0] - 1), LABEL_BG, 1)
    return canvas


def detect_on_crops(
    detector: Any, images: tuple[Any, ...], options: dict, torch: Any, device: str
) -> tuple[list[list[dict]], float]:
    """Run the detector over every crop. Returns per-crop records and total ms."""
    from cane.benchmarks.fused import predict_objects

    per_crop: list[list[dict]] = []
    start = time.perf_counter()
    for image in images:
        result, _ = predict_objects(detector, image, options, torch, device)
        per_crop.append(records(result))
    return per_crop, (time.perf_counter() - start) * 1000


def compose(cv2: Any, np: Any, images: tuple[Any, ...], per_crop: list[list[dict]] | None) -> Any:
    """Annotate each crop, label it, and tile into the wearer-ordered grid."""
    from cane.benchmarks.fused import draw_detections

    labels = crop_labels()
    decorated = []
    for index, image in enumerate(images):
        canvas = image.copy()
        if per_crop is not None:
            draw_detections(cv2, np, canvas, per_crop[index], True)
        label = labels[index]
        counted = "" if per_crop is None else f"  [{len(per_crop[index])}]"
        decorated.append(label_crop(cv2, canvas, f"{label}{counted}", label == "Front"))
    return tile_grid(tuple(decorated), np)


def save_sample(
    cv2: Any, output: Path, index: int, canvas: Any, per_crop: list[list[dict]] | None
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output / f"crops_{index:06d}.jpg"), canvas)
    if per_crop is None:
        return
    labels = crop_labels()
    flat = [
        dict(item, crop=labels[crop_index])
        for crop_index, items in enumerate(per_crop)
        for item in items
    ]
    (output / f"detections_{index:06d}.json").write_text(json.dumps(flat, indent=1))


def build_detector(args: argparse.Namespace, torch: Any) -> tuple[Any, str]:
    """Load YOLOE, or return (None, 'cpu') when detection is switched off."""
    if args.no_detection:
        return None, "cpu"
    try:
        from ultralytics import YOLOE
    except ImportError as exc:
        raise ValueError("Install detection dependencies: uv sync --extra vision") from exc
    device = choose_device(torch, args.device)
    detector = YOLOE(args.model)
    detector.set_classes(args.classes)
    return detector, device


def run(args: argparse.Namespace) -> Path | None:
    validate(args)
    if not args.no_detection:
        check_model(args.model)
    try:
        import cv2
        import numpy as np
        import torch
    except ImportError as exc:
        raise ValueError("Install dependencies: uv sync --extra vision --extra depth") from exc

    detector, device = build_detector(args, torch)
    output = args.output or Path("benchmark_runs") / datetime.now(UTC).strftime(
        "crops-%Y%m%dT%H%M%S.%fZ"
    )
    options = {
        "imgsz": args.imgsz,
        "conf": args.conf,
        "iou": args.iou,
        "device": device,
        "verbose": False,
    }
    source = open_source(args.source)
    logger.info(
        "crops preview: source=%s crop=%dpx detector=%s device=%s",
        args.source,
        args.crop_px,
        "off" if detector is None else args.model,
        device,
    )
    print(f"grid order: {' | '.join(labels_in_grid_order())}", flush=True)

    processed, empty_reads = 0, 0
    try:
        while args.max_frames == 0 or processed < args.max_frames:
            frame = source.read()
            if frame is None:
                empty_reads += 1
                if empty_reads >= READ_RETRY_LIMIT:
                    raise RuntimeError(
                        f"no frame from the {args.source} source after "
                        f"{READ_RETRY_LIMIT} reads; is the X4 awake and in webcam mode?"
                    )
                continue
            empty_reads = 0
            started = time.perf_counter()
            cropset = dewarp_equirect(frame.image, args.crop_px, frame.t_arrival_laptop_ms)
            per_crop, detect_ms = (
                (None, 0.0)
                if detector is None
                else detect_on_crops(detector, cropset.images, options, torch, device)
            )
            canvas = compose(cv2, np, cropset.images, per_crop)
            frame_ms = (time.perf_counter() - started) * 1000
            total = 0 if per_crop is None else sum(len(items) for items in per_crop)
            cv2.putText(
                canvas,
                f"{device} | {CROP_COUNT} crops @ {args.crop_px}px | "
                f"frame {frame_ms:.0f} ms | detect {detect_ms:.0f} ms | {total} objects",
                (6, canvas.shape[0] - 8),
                FONT,
                0.5,
                LABEL_FG,
                1,
                cv2.LINE_AA,
            )
            if args.save_every and processed % args.save_every == 0:
                save_sample(cv2, output, processed, canvas, per_crop)
            if args.show:
                cv2.imshow(WINDOW, canvas)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
            processed += 1
    except KeyboardInterrupt:
        logger.info("interrupted after %d frames", processed)
    finally:
        source.close()
        if args.show:
            cv2.destroyAllWindows()
    logger.info("crops preview: %d frames", processed)
    return output if args.save_every else None


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    run(parser().parse_args())


if __name__ == "__main__":
    main()
