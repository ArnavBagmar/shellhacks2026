"""Standalone Depth Anything V2 Small benchmark and OpenCV preview."""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import platform
import resource
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .depth_model import DEFAULT_REPO, DEFAULT_WEIGHTS, load_depth, normalize_depth, predict_depth
from .metrics import summarize
from .palette import depth_heatmap, depth_legend
from .preview_ui import PreviewQuit, PreviewWindow, Toggle, fit_display, status_bar
from .yolo import Frames, choose_device

WINDOW = "Depth Anything V2 Small - relative depth only"
CORRIDOR = (240, 196, 130)  # Cool blue; the green-to-orange ramp means depth.


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--source", type=Path, help="Image, video, or sorted image directory")
    source.add_argument("--camera", type=int)
    p.add_argument("--camera-timeout", type=float, default=10)
    p.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    p.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    p.add_argument("--device", choices=["auto", "cuda", "mps", "cpu"], default="auto")
    p.add_argument("--input-size", type=int, default=280, help="Short-side target; 280 for speed, 518 for official default")
    p.add_argument("--warmup", type=int, default=10)
    p.add_argument("--max-frames", type=int, default=300, help="0 runs until q, Ctrl-C or end of file")
    p.add_argument("--save-every", type=int, default=30, help="Save preview and raw NPY every N frames; 0 disables")
    p.add_argument("--show", action="store_true")
    p.add_argument("--opacity", type=int, default=35, help="Initial overlay percentage")
    p.add_argument("--output", type=Path)
    return p


def display_payload(cv2, np, frame, relative, scale, size):
    """Resize UI buffers only; the inference input and raw depth keep their size."""
    shown = cv2.resize(frame, size) if scale < 1 else frame
    heat = depth_heatmap(cv2, np, cv2.resize(relative, size) if scale < 1 else relative)
    height, width = shown.shape[:2]
    return {"frame": shown, "heat": heat, "probe": center_region(width, height)}


def preview(cv2, np, payload, opacity, state):
    # Original image left; same-frame aligned heatmap/overlay right.
    left, heat = payload["frame"], payload["heat"]
    right = cv2.addWeighted(left, 1 - opacity, heat, opacity, 0) if state["overlay"] else heat.copy()
    h, w = left.shape[:2]
    if state["corridor"]:
        for x in (w // 3, 2 * w // 3):
            cv2.line(right, (x, 0), (x, h - 1), CORRIDOR, 2)
    # Center probe is a 10%-wide/high square, in displayed-image coordinates.
    x0, x1, y0, y1 = payload["probe"]
    cv2.rectangle(right, (x0, y0), (x1 - 1, y1 - 1), (255, 255, 255), 2)
    return np.concatenate([left, right], axis=1)


def center_region(w, h):
    return max(0, int(w * .45)), max(1, int(w * .55)), max(0, int(h * .45)), max(1, int(h * .55))


def save_sample(cv2, np, output, index, annotated, depth):
    if not cv2.imwrite(str(output / f"frame_{index:06d}.jpg"), annotated):
        raise ValueError("Failed to save depth preview")
    np.save(output / f"depth_{index:06d}.npy", depth, allow_pickle=False)


def run(args):
    if args.input_size < 14 or args.input_size % 14:
        raise ValueError("input-size must be a positive multiple of 14, e.g. 280 or 518")
    if args.warmup < 1 or args.max_frames < 0 or args.save_every < 0 or args.camera_timeout <= 0:
        raise ValueError("Use warmup >= 1, max-frames/save-every >= 0, camera-timeout > 0")
    if not 0 <= args.opacity <= 100:
        raise ValueError("opacity must be 0–100")
    try:
        import cv2
        import numpy as np
        import torch
    except ImportError as exc:
        raise ValueError("Install dependencies: uv sync --extra vision --extra depth") from exc
    device = choose_device(torch, args.device)
    start = time.perf_counter()
    model, provenance = load_depth(args.repo, args.weights, device)
    setup_ms = (time.perf_counter() - start) * 1000
    frames = Frames(cv2, args.source, args.camera, args.camera_timeout)
    output = args.output or Path("benchmark_runs") / datetime.now(timezone.utc).strftime("depth-%Y%m%dT%H%M%S.%fZ")
    rows, shapes = [], set()
    overlay, corridor, stop_reason = True, True, "input_complete_or_limit"
    ui = None
    try:
        if args.show:
            ui = PreviewWindow(cv2, np, WINDOW, args.opacity, preview,
                               [Toggle("overlay", "d", "Overlay"), Toggle("corridor", "c", "Corridor")],
                               opacity_label="Overlay %", opacity_toggle="overlay", legend=depth_legend,
                               starting="Starting depth preview...")
            frames.stop_event = ui.stop
        def call(function, *positional):
            return ui.call(function, *positional) if ui else function(*positional)

        def read_frame():
            start = time.perf_counter()
            item = frames.read()
            return item, (time.perf_counter() - start) * 1000

        start = time.perf_counter()
        try:
            first, read_ms = call(read_frame)
        except PreviewQuit:
            print("Stopped before camera startup completed.")
            return None
        startup = read_ms if args.camera is not None else None
        if startup is not None:
            read_ms = 0.0
        if first is None:
            raise ValueError("Input contains no decodable frames")
        output.mkdir(parents=True, exist_ok=False)
        print(f"Depth Anything V2 Small; device: {device}; input-size: {args.input_size}; warming up {args.warmup} times", flush=True)
        try:
            for _ in range(args.warmup):
                call(predict_depth, model, first[3], args.input_size, torch, device)
        except PreviewQuit:
            (output / "summary.json").write_text(json.dumps({"status": "stopped", "frames": 0, "stop_reason": "user_quit_during_warmup"}, indent=2))
            return output
        if device == "cuda":
            torch.cuda.reset_peak_memory_stats()
        metadata = {"schema": 1, "component": "depth_anything_v2_small", "status": "running",
                    **provenance, "device": device, "precision": "float32", "input_size": args.input_size,
                    "source": str(args.source.resolve()) if args.source else f"camera:{args.camera}",
                    "warmup": args.warmup, "max_frames": args.max_frames, "setup_ms": setup_ms,
                    "camera_startup_ms": startup, "platform": platform.platform(), "python": sys.version,
                    "versions": {n: importlib.metadata.version(n) for n in ("torch", "torchvision", "numpy", "opencv-python")},
                    "normalization": "Per frame: (raw - raw.min()) / (raw.max() - raw.min()); 0 far, 1 near. Flat map => zeros, flat=true. Not metric or temporally calibrated.",
                    "timing_note": "Synchronized sequential stages. postprocess_ms is predict minus preprocessing and network time, including original-resolution interpolation and CPU copy. frame_ms includes read, predict, normalization/center probe; excludes drawing, saving, hashing, display, warmup. Not capture-to-display latency."}
        summary_path = output / "summary.json"
        summary_path.write_text(json.dumps(metadata, indent=2))
        with (output / "timings.csv").open("w", newline="") as stream, (output / "frames.jsonl").open("w") as manifest:
            writer = None
            item = first
            try:
                while item is not None and (args.max_frames == 0 or len(rows) < args.max_frames):
                    index, identity, position, frame = item
                    depth, timing = call(predict_depth, model, frame, args.input_size, torch, device)
                    shapes.add(tuple(model.input_shape))
                    start = time.perf_counter()
                    relative, low, high, flat = normalize_depth(depth)
                    h, w = depth.shape
                    x0, x1, y0, y1 = center_region(w, h)
                    center = float(np.median(relative[y0:y1, x0:x1]))
                    result_ms = (time.perf_counter() - start) * 1000
                    row = {"frame_id": index, "read_ms": read_ms, **timing, "result_ms": result_ms,
                           "frame_ms": read_ms + timing["predict_ms"] + result_ms}
                    rows.append(row)
                    if writer is None:
                        writer = csv.DictWriter(stream, fieldnames=list(row))
                        writer.writeheader()
                    writer.writerow(row)
                    manifest.write(json.dumps({"frame_id": index, "input": identity, "video_position_ms": position,
                        "shape_hw": list(depth.shape), "network_input_shape": model.input_shape,
                        "bgr_sha256": hashlib.sha256(frame.tobytes()).hexdigest(), "raw_min": low, "raw_max": high,
                        "center_relative_depth": center, "flat": flat}) + "\n")
                    save = args.save_every > 0 and index % args.save_every == 0
                    if args.show or save:
                        info = f"{device} | predict {timing['predict_ms']:.1f} ms | {1000 / timing['predict_ms']:.1f} pred FPS | center relative depth: {center:.2f}"
                        if flat:
                            info += " | FLAT: no depth ordering"
                        lines = [info, "Left: camera. Right: relative depth, not meters - light green is farther, dark orange is nearer."]
                        scale, size = fit_display(frame, max_width=720, max_height=780)
                        payload = display_payload(cv2, np, frame, relative, scale, size)
                        if ui:
                            ui.set_frame(index, payload, depth, lines)
                            ui.pump()
                            if ui.stop.is_set():
                                raise PreviewQuit()
                            annotated = ui.annotated
                        else:
                            canvas = preview(cv2, np, payload, args.opacity / 100,
                                             {"overlay": overlay, "corridor": corridor})
                            annotated = np.concatenate([status_bar(cv2, np, canvas.shape[1], lines), canvas], axis=0)
                        if save:
                            call(save_sample, cv2, np, output, index, annotated, depth)
                        request = ui.take_save_request() if ui else None
                        if request is not None:
                            saved_index, saved_depth, saved_preview = request
                            call(save_sample, cv2, np, output, saved_index, saved_preview, saved_depth)
                    if len(rows) % 30 == 0:
                        print(f"{len(rows)} frames; latest depth predict {timing['predict_ms']:.1f} ms", flush=True)
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
        metadata.update(status="complete", stop_reason=stop_reason, network_input_shapes=[list(s) for s in sorted(shapes)])
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        metadata["process_peak_rss_bytes"] = rss if sys.platform == "darwin" else rss * 1024
        metadata["cuda_peak_allocated_bytes"] = torch.cuda.max_memory_allocated() if device == "cuda" else None
        metadata["mps_current_allocated_bytes"] = torch.mps.current_allocated_memory() if device == "mps" else None
        summary_path.write_text(json.dumps(metadata, indent=2))
        print(json.dumps({"output": str(output), **summarize(rows)}, indent=2))
        return output
    finally:
        if ui:
            ui.close()
        frames.close()


def main():
    try:
        run(parser().parse_args())
    except (ValueError, RuntimeError, OSError, ImportError) as exc:
        raise SystemExit(f"Depth benchmark failed: {exc}") from exc


if __name__ == "__main__":
    main()
