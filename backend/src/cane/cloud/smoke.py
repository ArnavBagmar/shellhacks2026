"""`python -m cane.cloud.smoke` -- the team's spike-7 tool (A14).

    python -m cane.cloud.smoke --url http://HOST:8000 [--image PATH]
        [--crop-px 640] [--frames 3] [--session smoke] [--key KEY]
        [--max-inference-ms 25.0]

Posts the same six crops a few hundred milliseconds apart so ByteTrack
reaches the two consistent range samples a non-zero `closing_mps` needs
(frame 1 reports null, frame 2 reports 0.0), then prints one line
per detection and exits non-zero if the service is not ready, if any request
failed, or if the median inference time (excluding the first frame, which
pays the one-time shape warm-up) exceeds the budget.

Exempt from the timeless rule: this is an operator tool, not the request path.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import dataclass
from typing import Any, Final, Mapping, Sequence

import cv2
import httpx
import numpy as np

from cane.cloud.api_models import CROP_COUNT, crop_field_name

SMOKE_MAX_INFERENCE_MS: Final = 25.0
DEFAULT_FRAMES: Final = 3
DEFAULT_CROP_PX: Final = 640
DEFAULT_SESSION: Final = "smoke"
FRAME_GAP_MS: Final = 350
JPEG_QUALITY: Final = 70
REQUEST_TIMEOUT_S: Final = 30.0
API_KEY_HEADER: Final = "X-Cane-Key"
# A stamp inside the contract window; the cloud only echoes it.
BASE_T_CAPTURE_MS: Final = 1_790_000_000_000

EXIT_OK: Final = 0
EXIT_ENGINE_NOT_READY: Final = 2
EXIT_HTTP_ERROR: Final = 3
EXIT_TOO_SLOW: Final = 4
EXIT_NO_FRAMES: Final = 5


@dataclass(frozen=True, slots=True)
class FrameResult:
    status_code: int
    inference_ms: float
    detections: int


def evaluate(
    health: Mapping[str, Any], results: Sequence[FrameResult], budget_ms: float
) -> tuple[int, str]:
    """The whole pass/fail rule, pure so it can be unit-tested."""
    if not health.get("engine_ready"):
        return EXIT_ENGINE_NOT_READY, f"engine not ready: {dict(health)}"
    failures = [r.status_code for r in results if r.status_code != 200]
    if failures:
        return EXIT_HTTP_ERROR, f"non-200 responses: {failures}"
    if not results:
        return EXIT_NO_FRAMES, "no frames were sent"
    judged = [r.inference_ms for r in results[1:]] or [results[0].inference_ms]
    median_ms = statistics.median(judged)
    if median_ms > budget_ms:
        return EXIT_TOO_SLOW, f"median inference {median_ms:.1f} ms > budget {budget_ms:.1f} ms"
    return EXIT_OK, f"median inference {median_ms:.1f} ms within budget {budget_ms:.1f} ms"


def build_crop(image_path: str | None, crop_px: int) -> bytes:
    """Centre-crop and resize `--image`, or synthesise a gradient."""
    if image_path:
        source = cv2.imread(image_path, cv2.IMREAD_COLOR)
        if source is None:
            raise SystemExit(f"could not read --image {image_path}")
        height, width = source.shape[:2]
        side = min(height, width)
        top, left = (height - side) // 2, (width - side) // 2
        square = source[top : top + side, left : left + side]
    else:
        ramp = np.linspace(0, 255, crop_px, dtype=np.uint8)
        square = np.dstack([np.tile(ramp, (crop_px, 1))] * 3)
    resized = cv2.resize(square, (crop_px, crop_px), interpolation=cv2.INTER_AREA)
    ok, buffer = cv2.imencode(".jpg", resized, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
    if not ok:
        raise SystemExit("could not JPEG-encode the smoke crop")
    return bytes(buffer)


def _post_frame(
    client: httpx.Client, args: argparse.Namespace, jpeg: bytes, index: int
) -> tuple[FrameResult, list[str]]:
    meta = {
        "schema": 1,
        "t_capture_ms": BASE_T_CAPTURE_MS + index * FRAME_GAP_MS,
        "rung": 1 if args.crop_px >= DEFAULT_CROP_PX else 3,
        "crop_px": args.crop_px,
        "yaws_deg": [0.0, 60.0, 120.0, 180.0, -120.0, -60.0],
        "session_id": args.session,
    }
    files = [
        (crop_field_name(i), (f"crop_{i}.jpg", jpeg, "image/jpeg")) for i in range(CROP_COUNT)
    ]
    response = client.post("/infer", data={"meta": json.dumps(meta)}, files=files)
    if response.status_code != 200:
        return FrameResult(response.status_code, 0.0, 0), [f"  HTTP {response.status_code}"]
    body = response.json()
    lines = [
        f"  crop={d['crop_index']} {d['cls']:<16} conf={d['confidence']:.2f} "
        f"id={d['track_id']:<10} band={d['range_band']} closing={d['closing_mps']}"
        for d in body["detections"]
    ]
    return FrameResult(200, float(body["inference_ms"]), len(body["detections"])), lines


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - CLI wiring
    parser = argparse.ArgumentParser(prog="python -m cane.cloud.smoke")
    parser.add_argument("--url", required=True)
    parser.add_argument("--image", default=None)
    parser.add_argument("--crop-px", type=int, default=DEFAULT_CROP_PX)
    parser.add_argument("--frames", type=int, default=DEFAULT_FRAMES)
    parser.add_argument("--session", default=DEFAULT_SESSION)
    parser.add_argument("--key", default=None)
    parser.add_argument("--max-inference-ms", type=float, default=SMOKE_MAX_INFERENCE_MS)
    args = parser.parse_args(argv)

    headers = {API_KEY_HEADER: args.key} if args.key else {}
    jpeg = build_crop(args.image, args.crop_px)
    results: list[FrameResult] = []
    with httpx.Client(base_url=args.url, timeout=REQUEST_TIMEOUT_S, headers=headers) as client:
        health = client.get("/healthz").json()
        print(f"healthz: {health}")
        for index in range(args.frames):
            result, lines = _post_frame(client, args, jpeg, index)
            results.append(result)
            print(f"frame {index + 1}: {result.inference_ms:.1f} ms, {result.detections} detections")
            print("\n".join(lines))

    code, message = evaluate(health, results, args.max_inference_ms)
    print(("PASS: " if code == EXIT_OK else "FAIL: ") + message)
    return code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
