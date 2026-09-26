"""One view: YOLOE detections and Depth Anything V2 Small on the same frame.

One capture read per iteration feeds both models sequentially, so a drawn box
and the depth under it always describe the identical pixels. Either layer can be
switched off from the window (or at startup) and then costs nothing.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import math
import platform
import resource
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .depth_model import DEFAULT_REPO, DEFAULT_WEIGHTS, load_depth, normalize_depth, predict_depth
from .metrics import summarize
from .palette import depth_color, depth_heatmap, depth_legend, readable_text
from .preview_ui import PreviewQuit, PreviewWindow, Toggle, fit_display, status_bar
from .yolo import (DEFAULT_CLASSES, Frames, LatestFrames, check_model, choose_device,
                   instrument_stages, records, synchronize)

WINDOW = "Smart cane perception - objects + relative depth"
NO_DEPTH_TIMING = {"predict_ms": 0.0, "preprocess_ms": 0.0, "inference_ms": 0.0, "postprocess_ms": 0.0}
NEUTRAL_BOX = (215, 215, 215)
# Cool blue: the green-to-orange ramp is reserved for depth, so guides and
# chrome never read as a distance.
CORRIDOR = (240, 196, 130)
FONT = 0


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--source", type=Path, help="Image, video, or sorted image directory")
    source.add_argument("--camera", type=int)
    p.add_argument("--camera-timeout", type=float, default=10.0)
    p.add_argument("--model", default="yoloe-26s-seg.pt", help="YOLOE text-prompt .pt checkpoint")
    p.add_argument("--classes", nargs="+", default=DEFAULT_CLASSES)
    p.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    p.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    p.add_argument("--device", choices=["auto", "cuda", "mps", "cpu"], default="auto")
    p.add_argument("--imgsz", type=int, default=640)
    p.add_argument("--conf", type=float, default=0.25)
    p.add_argument("--iou", type=float, default=0.7)
    # 196, not the depth benchmark's 280: depth is over half the frame budget
    # here and 196 nearly halves it. The standalone runner keeps 280 so its
    # recorded baseline stays comparable with every run already taken.
    p.add_argument("--input-size", type=int, default=196,
                   help="Depth short side; 196 for the live view, 280 to match the depth benchmark, 518 for detail")
    p.add_argument("--warmup", type=int, default=10)
    p.add_argument("--max-frames", type=int, default=300, help="0 runs until q, Ctrl-C or end of input")
    p.add_argument("--save-every", type=int, default=30, help="Save preview, raw depth and detections every N frames; 0 disables")
    p.add_argument("--show", action="store_true")
    p.add_argument("--opacity", type=int, default=35, help="Initial depth overlay percentage")
    p.add_argument("--no-detection", action="store_true", help="Skip loading and running YOLOE entirely")
    p.add_argument("--no-depth", action="store_true", help="Skip loading and running the depth model entirely")
    p.add_argument("--no-latest-frame", action="store_true",
                   help="Read the camera inline instead of dropping stale frames; slower and laggier, but every frame is processed")
    p.add_argument("--output", type=Path)
    return p


def center_region(width, height):
    """Central 10%-wide, 10%-high probe rectangle in image coordinates."""
    return max(0, int(width * .45)), max(1, int(width * .55)), max(0, int(height * .45)), max(1, int(height * .55))


def box_depth(np, relative, xyxy):
    """Median normalized depth over the central 25% area of a clipped box.

    Trimming a quarter off each side keeps background pixels and neighbouring
    objects inside a rectangular box from dragging the median around.
    """
    height, width = relative.shape
    x_low, x_high = sorted((float(xyxy[0]), float(xyxy[2])))
    y_low, y_high = sorted((float(xyxy[1]), float(xyxy[3])))
    inset_x, inset_y = (x_high - x_low) / 4, (y_high - y_low) / 4
    x0 = min(max(math.floor(x_low + inset_x), 0), width - 1)
    y0 = min(max(math.floor(y_low + inset_y), 0), height - 1)
    x1 = min(max(math.ceil(x_high - inset_x), x0 + 1), width)
    y1 = min(max(math.ceil(y_high - inset_y), y0 + 1), height)
    region = relative[y0:y1, x0:x1]
    return float(np.median(region)) if region.size else None


def predict_objects(model, frame, options, torch, device):
    synchronize(torch, device)
    start = time.perf_counter()
    result = model.predict(frame, **options)[0]
    synchronize(torch, device)
    elapsed = (time.perf_counter() - start) * 1000
    if tuple(result.orig_shape) != tuple(frame.shape[:2]):
        raise ValueError("Detection coordinates do not match the shared input image shape")
    return result, elapsed


def measure_depth(model, frame, input_size, torch, device, np):
    """Depth for one frame plus its normalization and central probe reading."""
    depth, timing = predict_depth(model, frame, input_size, torch, device)
    relative, low, high, flat = normalize_depth(depth)
    height, width = relative.shape
    x0, x1, y0, y1 = center_region(width, height)
    return depth, relative, timing, {"raw_min": low, "raw_max": high, "flat": flat,
                                     "center_relative_depth": float(np.median(relative[y0:y1, x0:x1]))}


def display_payload(cv2, np, frame, relative, detections, scale, size):
    """Resize UI buffers once per frame; inference inputs stay untouched."""
    shown = cv2.resize(frame, size) if scale < 1 else frame
    heat = None
    if relative is not None:
        heat = depth_heatmap(cv2, np, cv2.resize(relative, size) if scale < 1 else relative)
    scaled = [dict(item, xyxy=[value * scale for value in item["xyxy"]],
                   mask_polygon_xy=None if item["mask_polygon_xy"] is None
                   else [[x * scale, y * scale] for x, y in item["mask_polygon_xy"]])
              for item in detections]
    height, width = shown.shape[:2]
    return {"frame": shown, "heat": heat, "detections": scaled,
            "probe": center_region(width, height), "has_depth": relative is not None}


def overlaps(rect, placed):
    x0, y0, x1, y1 = rect
    return any(x0 < other[2] and other[0] < x1 and y0 < other[3] and other[1] < y1 for other in placed)


def box_color(np, relative):
    return NEUTRAL_BOX if relative is None else depth_color(np, relative)


def draw_detections(cv2, np, canvas, detections, masks):
    """Boxes far-to-near so nearer ones sit on top; labels near-first so a
    crowded scene keeps the nearest object's text instead of stacking both."""
    for item in detections:
        color = box_color(np, item.get("relative_depth"))
        x0, y0, x1, y1 = (int(round(value)) for value in item["xyxy"])
        if masks and item["mask_polygon_xy"]:
            polygon = np.round(np.array(item["mask_polygon_xy"], dtype=np.float32)).astype(np.int32)
            cv2.polylines(canvas, [polygon], True, color, 1, cv2.LINE_AA)
        # Dark backing keeps a light far-end box visible against a light scene.
        cv2.rectangle(canvas, (x0, y0), (x1, y1), (25, 25, 25), 4)
        cv2.rectangle(canvas, (x0, y0), (x1, y1), color, 2)
    placed = []
    for item in reversed(detections):
        relative = item.get("relative_depth")
        color = box_color(np, relative)
        x0, y0 = int(round(item["xyxy"][0])), int(round(item["xyxy"][1]))
        caption = f"{item['label']} {item['confidence']:.2f}"
        if relative is not None:
            caption += f" | rel {relative:.2f}"
        (text_width, text_height), _ = cv2.getTextSize(caption, FONT, 0.45, 1)
        top = y0 - text_height - 8 if y0 - text_height - 8 >= 0 else y0 + 2
        left = min(max(x0, 0), max(0, canvas.shape[1] - text_width - 11))
        rect = (left, top, left + text_width + 10, top + text_height + 7)
        if overlaps(rect, placed):
            continue  # The nearer object already owns this patch of screen.
        placed.append(rect)
        cv2.rectangle(canvas, rect[:2], rect[2:], color, -1)
        cv2.putText(canvas, caption, (left + 5, top + text_height + 2), FONT, 0.45, readable_text(color), 1)


def render(cv2, np, payload, opacity, state):
    """One merged canvas: camera image, depth overlay, corridor, detections."""
    canvas = payload["frame"].copy()
    height, width = canvas.shape[:2]
    depth_shown = state["depth"] and payload["heat"] is not None
    if depth_shown:
        canvas = cv2.addWeighted(canvas, 1 - opacity, payload["heat"], opacity, 0)
    if state["corridor"]:
        for x in (width // 3, 2 * width // 3):
            cv2.line(canvas, (x, 0), (x, height - 1), CORRIDOR, 1)
        if depth_shown:
            # The probe only means something next to a depth reading.
            x0, x1, y0, y1 = payload["probe"]
            cv2.rectangle(canvas, (x0, y0), (x1 - 1, y1 - 1), (255, 255, 255), 1)
    if state["objects"]:
        draw_detections(cv2, np, canvas, payload["detections"], state["masks"])
    return canvas


def status_lines(device, row, objects, depth_on, objects_on, probe):
    frame_ms = row["frame_ms"]
    first = [f"{device}", f"frame {frame_ms:.0f} ms", f"{1000 / frame_ms:.1f} FPS" if frame_ms else "-- FPS"]
    first.append(f"depth {row['depth_predict_ms']:.0f} ms" if depth_on else "depth off")
    first.append(f"detect {row['detect_ms']:.0f} ms | {objects} objects" if objects_on else "detection off")
    if not depth_on:
        second = "Depth layer off: boxes carry no range. Switch depth on for the light-green to dark-orange scale."
    else:
        second = "Relative depth, not meters: light green is farther, dark orange is nearer."
        if probe is not None:
            second += f" Center {probe['center_relative_depth']:.2f}."
            if probe["flat"]:
                second += " FLAT: no depth ordering in this frame."
    return [" | ".join(first), second]


def save_sample(cv2, np, output, index, annotated, depth, detections):
    if not cv2.imwrite(str(output / f"frame_{index:06d}.jpg"), annotated):
        raise ValueError("Failed to save fused preview")
    if depth is not None:
        np.save(output / f"depth_{index:06d}.npy", depth, allow_pickle=False)
    (output / f"detections_{index:06d}.json").write_text(json.dumps(detections, indent=2))


def build_window(cv2, np, args, depth_available, detection_available):
    toggles = [
        Toggle("depth", "d", "Depth", value=depth_available, available=depth_available),
        Toggle("objects", "o", "Objects", value=detection_available, available=detection_available),
        Toggle("masks", "m", "Masks", value=detection_available, available=detection_available),
        Toggle("corridor", "c", "Corridor", value=True),
    ]
    return PreviewWindow(cv2, np, WINDOW, args.opacity, render, toggles, opacity_label="Depth %",
                         opacity_toggle="depth", legend=depth_legend if depth_available else None,
                         starting="Loading models... (q or the Quit button stops)")


def run(args):
    if args.input_size < 14 or args.input_size % 14:
        raise ValueError("input-size must be a positive multiple of 14, e.g. 280 or 518")
    if args.imgsz <= 0 or args.imgsz % 32:
        raise ValueError("imgsz must be a positive multiple of 32")
    if args.warmup < 1 or args.max_frames < 0 or args.save_every < 0 or args.camera_timeout <= 0:
        raise ValueError("Use warmup >= 1, max-frames/save-every >= 0, camera-timeout > 0")
    if not 0 <= args.conf <= 1 or not 0 <= args.iou <= 1:
        raise ValueError("conf and iou must be between 0 and 1")
    if not 0 <= args.opacity <= 100:
        raise ValueError("opacity must be 0-100")
    if args.no_depth and args.no_detection:
        raise ValueError("Disabling both layers leaves nothing to run; drop one of the flags")
    if not args.no_detection:
        check_model(args.model)
    try:
        import cv2
        import numpy as np
        import torch
    except ImportError as exc:
        raise ValueError("Install dependencies: uv sync --extra vision --extra depth") from exc
    if not args.no_detection:
        try:
            from ultralytics import YOLOE
        except ImportError as exc:
            raise ValueError("Install detection dependencies: uv sync --extra vision") from exc

    device = choose_device(torch, args.device)
    output = args.output or Path("benchmark_runs") / datetime.now(timezone.utc).strftime("fused-%Y%m%dT%H%M%S.%fZ")
    rows, stop_reason = [], "input_complete_or_limit"
    ui, frames, detector, depth = None, None, None, None
    try:
        if args.show:
            ui = build_window(cv2, np, args, not args.no_depth, not args.no_detection)

        def call(function, *positional, **named):
            return ui.call(function, *positional, **named) if ui else function(*positional, **named)

        def read_frame():
            start = time.perf_counter()
            item = frames.read()
            return item, (time.perf_counter() - start) * 1000

        try:
            setup_start = time.perf_counter()
            if not args.no_depth:
                depth, provenance = call(load_depth, args.repo, args.weights, device)
            else:
                provenance = {}
            if not args.no_detection:
                detector = call(YOLOE, args.model)
                call(detector.set_classes, args.classes)
            setup_ms = (time.perf_counter() - setup_start) * 1000
            if ui:
                ui.status(f"Opening {'camera ' + str(args.camera) if args.camera is not None else str(args.source)}...")
            frames = Frames(cv2, args.source, args.camera, args.camera_timeout)
            if args.camera is not None and not args.no_latest_frame:
                # Newest-frame-only, cameras only: files keep the no-skipping
                # guarantee that makes two runs comparable frame for frame.
                frames = LatestFrames(frames, args.camera_timeout)
            if ui:
                frames.stop_event = ui.stop
            first, read_ms = call(read_frame)
        except PreviewQuit:
            print("Stopped before the first frame was processed.")
            return None
        startup = read_ms if args.camera is not None else None
        if startup is not None:
            # Starting Continuity Camera is not steady-state processing.
            read_ms = 0.0
        if first is None:
            raise ValueError("Input contains no decodable frames")
        output.mkdir(parents=True, exist_ok=False)
        options = dict(device=device, imgsz=args.imgsz, conf=args.conf, iou=args.iou,
                       quantize=32, rect=False, verbose=False, save=False)
        print(f"device: {device}; depth: {'off' if args.no_depth else args.input_size}; "
              f"detection: {'off' if args.no_detection else args.model}; warming up {args.warmup} times", flush=True)
        try:
            if ui:
                ui.status("Warming up both models on the first frame...")
            for _ in range(args.warmup):
                if depth is not None:
                    call(predict_depth, depth, first[3], args.input_size, torch, device)
                if detector is not None:
                    call(detector.predict, first[3], **options)
        except PreviewQuit:
            (output / "summary.json").write_text(json.dumps(
                {"status": "stopped", "frames": 0, "stop_reason": "user_quit_during_warmup"}, indent=2))
            return output
        synchronize(torch, device)
        stage_times = instrument_stages(detector.predictor, torch, device) if detector is not None else {}
        if device == "cuda":
            torch.cuda.reset_peak_memory_stats()
        metadata = {
            "schema": 1, "component": "fused_yoloe_depth", "status": "running", **provenance,
            "detector_model": None if args.no_detection else args.model,
            "classes": None if args.no_detection else args.classes,
            "depth_input_size": None if args.no_depth else args.input_size,
            "device": device, "precision": "float32", "imgsz": args.imgsz, "rect": False,
            "conf": args.conf, "iou": args.iou, "warmup": args.warmup, "max_frames": args.max_frames,
            "source": str(args.source.resolve()) if args.source else f"camera:{args.camera}",
            "camera_startup_ms": startup, "camera_timeout_s": args.camera_timeout, "setup_ms": setup_ms,
            "latest_frame_grab": isinstance(frames, LatestFrames),
            "platform": platform.platform(), "python": sys.version,
            "versions": {name: importlib.metadata.version(name) for name in
                         ["torch", "torchvision", "numpy", "opencv-python"]
                         + ([] if args.no_detection else ["ultralytics"])},
            "same_frame_guarantee": "One capture read per iteration. Both models receive the identical "
                                    "unannotated frame; drawing happens afterwards on a copy. No interleaving, "
                                    "frame skipping, or reuse of a previous frame's depth.",
            "normalization": "Per frame: (raw - raw.min()) / (raw.max() - raw.min()); 0 far, 1 near. "
                             "Per-object value is the median over the central 25% area of its box. "
                             "Not metric, not temporally calibrated, and not comparable across frames.",
            "timing_note": "Synchronized sequential stages. predict_ms is depth plus detection. frame_ms adds "
                           "read, normalization and box medians; it excludes drawing, saving, hashing and "
                           "display, so it is not capture-to-feedback latency. A layer switched off contributes "
                           "0 ms to its own stages, so aggregates are only comparable across frames where the "
                           "same layers ran: see layer_frames. With latest_frame_grab true, a background thread "
                           "keeps only the newest camera frame, so read_ms measures taking an already-waiting "
                           "frame (near zero) rather than waiting for the driver, and frames_dropped counts the "
                           "camera frames skipped. Camera read_ms and frame_ms from such a run are therefore NOT "
                           "comparable with runs made without it, nor with the file path, which never drops.",
        }
        summary_path = output / "summary.json"
        summary_path.write_text(json.dumps(metadata, indent=2))
        layer_frames = {"depth": 0, "objects": 0}
        with (output / "timings.csv").open("w", newline="") as stream, (output / "frames.jsonl").open("w") as manifest:
            writer, item = None, first
            try:
                while item is not None and (args.max_frames == 0 or len(rows) < args.max_frames):
                    index, identity, position, frame = item
                    layers = ui.state() if ui else {"depth": depth is not None, "objects": detector is not None,
                                                    "masks": True, "corridor": True}
                    depth_on = layers["depth"] and depth is not None
                    objects_on = layers["objects"] and detector is not None
                    raw, relative, probe_reading = None, None, None
                    depth_timing = dict(NO_DEPTH_TIMING)
                    if depth_on:
                        raw, relative, depth_timing, probe_reading = call(
                            measure_depth, depth, frame, args.input_size, torch, device, np)
                    detections, detect_ms = [], 0.0
                    if objects_on:
                        result, detect_ms = call(predict_objects, detector, frame, options, torch, device)
                        detections = records(result)
                    start = time.perf_counter()
                    for entry in detections:
                        entry["relative_depth"] = None if relative is None else box_depth(np, relative, entry["xyxy"])
                    detections.sort(key=lambda entry: -1.0 if entry["relative_depth"] is None else entry["relative_depth"])
                    result_ms = (time.perf_counter() - start) * 1000
                    layer_frames["depth"] += depth_on
                    layer_frames["objects"] += objects_on
                    row = {"frame_id": index, "read_ms": read_ms,
                           "depth_predict_ms": depth_timing["predict_ms"],
                           "depth_preprocess_ms": depth_timing["preprocess_ms"],
                           "depth_inference_ms": depth_timing["inference_ms"],
                           "depth_postprocess_ms": depth_timing["postprocess_ms"],
                           "detect_ms": detect_ms,
                           "detect_preprocess_ms": stage_times.get("preprocess", 0.0) if objects_on else 0.0,
                           "detect_inference_ms": stage_times.get("inference", 0.0) if objects_on else 0.0,
                           "detect_postprocess_ms": stage_times.get("postprocess", 0.0) if objects_on else 0.0,
                           "result_ms": result_ms,
                           "predict_ms": depth_timing["predict_ms"] + detect_ms,
                           "frame_ms": read_ms + depth_timing["predict_ms"] + detect_ms + result_ms,
                           "detections": len(detections), "depth_enabled": int(depth_on),
                           "detection_enabled": int(objects_on)}
                    rows.append(row)
                    if writer is None:
                        writer = csv.DictWriter(stream, fieldnames=list(row))
                        writer.writeheader()
                    writer.writerow(row)
                    manifest.write(json.dumps({
                        "frame_id": index, "input": identity, "video_position_ms": position,
                        "shape_hw": list(frame.shape[:2]),
                        "bgr_sha256": hashlib.sha256(frame.tobytes()).hexdigest(),
                        "layers": {"depth": depth_on, "objects": objects_on},
                        **(probe_reading or {}), "detections": detections}) + "\n")
                    save = args.save_every > 0 and index % args.save_every == 0
                    if args.show or save:
                        lines = status_lines(device, row, len(detections), depth_on, objects_on, probe_reading)
                        scale, size = fit_display(frame)
                        payload = display_payload(cv2, np, frame, relative, detections, scale, size)
                        if ui:
                            # The sample travels with the frame, so s saves the
                            # depth and detections of the image on screen.
                            ui.set_frame(index, payload, (raw, detections), lines)
                            ui.pump()
                            if ui.stop.is_set():
                                raise PreviewQuit()
                            annotated = ui.annotated
                        else:
                            canvas = render(cv2, np, payload, args.opacity / 100, layers)
                            annotated = np.concatenate([status_bar(cv2, np, canvas.shape[1], lines), canvas], axis=0)
                        if save:
                            call(save_sample, cv2, np, output, index, annotated, raw, detections)
                        request = ui.take_save_request() if ui else None
                        if request is not None:
                            saved_index, (saved_depth, saved_objects), saved_preview = request
                            call(save_sample, cv2, np, output, saved_index, saved_preview, saved_depth, saved_objects)
                    if len(rows) % 30 == 0:
                        print(f"{len(rows)} frames; latest frame {row['frame_ms']:.1f} ms", flush=True)
                    if args.max_frames and len(rows) >= args.max_frames:
                        break
                    item, read_ms = call(read_frame)
            except PreviewQuit:
                stop_reason = "user_quit"
            except KeyboardInterrupt:
                stop_reason = "keyboard_interrupt"
        if not rows:
            metadata.update(status="stopped", frames=0, stop_reason=stop_reason)
            summary_path.write_text(json.dumps(metadata, indent=2))
            return output
        metadata.update(summarize(rows))
        metadata.update(status="complete", stop_reason=stop_reason, layer_frames=layer_frames)
        if isinstance(frames, LatestFrames):
            # Dropping is the point, but it must never be silent.
            metadata.update(frames_grabbed=frames.grabbed, frames_dropped=frames.dropped)
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        metadata["process_peak_rss_bytes"] = rss if sys.platform == "darwin" else rss * 1024
        metadata["cuda_peak_allocated_bytes"] = torch.cuda.max_memory_allocated() if device == "cuda" else None
        metadata["mps_current_allocated_bytes"] = torch.mps.current_allocated_memory() if device == "mps" else None
        summary_path.write_text(json.dumps(metadata, indent=2))
        dropped = {"frames_dropped": frames.dropped} if isinstance(frames, LatestFrames) else {}
        print(json.dumps({"output": str(output), "layer_frames": layer_frames, **dropped,
                          **summarize(rows)}, indent=2))
        return output
    finally:
        if ui:
            ui.close()
        if frames is not None:
            frames.close()


def main():
    try:
        run(parser().parse_args())
    except (ValueError, RuntimeError, OSError, ImportError) as exc:
        raise SystemExit(f"Fused preview failed: {exc}") from exc


if __name__ == "__main__":
    main()
