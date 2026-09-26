"""``PYTHONPATH=src uv run python -m cane.bridge`` -- run the X4 bridge.

    python -m cane.bridge --source x4 --rung 1 \
        --inference-url http://127.0.0.1:8000/infer --ws-port 8765

Sources: ``synthetic`` (no hardware), ``file:PATH``, ``index:N``, ``x4``
(macOS AVFoundation, the stitched 2880x1440 panorama mode).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Sequence

from cane.bridge.capture.base import FrameSource
from cane.bridge.capture.grab_thread import GrabThread
from cane.bridge.capture.opencv_source import open_file, open_index
from cane.bridge.capture.synthetic_source import SyntheticEquirectSource
from cane.bridge.config import (
    DEFAULT_INFERENCE_URL,
    DEFAULT_REQUEST_TIMEOUT_S,
    DEFAULT_WS_HOST,
    DEFAULT_WS_PORT,
    BridgeConfig,
)
from cane.bridge.pipeline import BridgePipeline, CaptureClock, LaptopClock, format_stats_line
from cane.bridge.rungs import RUNGS, rung_by_index
from cane.bridge.sender import OneInFlightSender
from cane.bridge.transport_http import HttpInferenceTransport
from cane.transport.server import EventLinkServer

logger = logging.getLogger("cane.bridge")

SOURCE_SYNTHETIC = "synthetic"
SOURCE_X4 = "x4"
FILE_PREFIX = "file:"
INDEX_PREFIX = "index:"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
UNSYNCED_BANNER = (
    "--allow-unsynced: stamping t_capture_ms with the LAPTOP clock (offset 0). "
    "Events sent to a phone in this mode carry the wrong clock domain -- "
    "bench/acceptance runs only."
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m cane.bridge", description=__doc__)
    parser.add_argument("--source", default=SOURCE_SYNTHETIC, help="synthetic | file:PATH | index:N | x4")
    parser.add_argument("--rung", type=int, default=1, choices=[r.index for r in RUNGS])
    parser.add_argument("--inference-url", default=DEFAULT_INFERENCE_URL)
    parser.add_argument("--request-timeout-s", type=float, default=DEFAULT_REQUEST_TIMEOUT_S)
    parser.add_argument("--ws-host", default=DEFAULT_WS_HOST)
    parser.add_argument("--ws-port", type=int, default=DEFAULT_WS_PORT)
    parser.add_argument("--delta-deg", type=float, default=0.0, help="yaw calibration (F3)")
    parser.add_argument("--pipeline-delay-ms", type=int, default=0, help="the measured constant D")
    parser.add_argument("--send-fps", type=float, default=None, help="override the rung's fps")
    parser.add_argument("--session-id", default=None)
    parser.add_argument(
        "--allow-unsynced",
        action="store_true",
        help="run with no phone: stamp with the laptop clock instead of skipping frames",
    )
    parser.add_argument("--duration-s", type=float, default=None, help="stop after N seconds")
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args(argv)


def build_config(args: argparse.Namespace) -> BridgeConfig:
    overrides = {} if args.session_id is None else {"session_id": args.session_id}
    return BridgeConfig(
        inference_url=args.inference_url,
        source_spec=args.source,
        rung=rung_by_index(args.rung),
        delta_deg=args.delta_deg,
        pipeline_delay_d_ms=args.pipeline_delay_ms,
        request_timeout_s=args.request_timeout_s,
        send_fps=args.send_fps,
        ws_host=args.ws_host,
        ws_port=args.ws_port,
        allow_unsynced=args.allow_unsynced,
        **overrides,
    )


def open_source(spec: str) -> FrameSource:
    """Resolve a ``--source`` string into a live :class:`FrameSource`."""
    if spec == SOURCE_SYNTHETIC:
        return SyntheticEquirectSource()
    if spec.startswith(FILE_PREFIX):
        return open_file(spec[len(FILE_PREFIX) :])
    if spec.startswith(INDEX_PREFIX):
        return open_index(int(spec[len(INDEX_PREFIX) :]))
    if spec == SOURCE_X4:
        # Imported here: PyObjC is an optional extra and must not be a hard
        # dependency of `python -m cane.bridge --source synthetic`.
        from cane.bridge.capture.avfoundation_source import open_x4

        return open_x4()
    raise ValueError(
        f"unknown --source {spec!r}; use {SOURCE_SYNTHETIC}, file:PATH, index:N or {SOURCE_X4}"
    )


async def run_bridge(config: BridgeConfig, duration_s: float | None = None) -> None:
    """Wire everything up and run until the duration elapses or Ctrl-C."""
    source = open_source(config.source_spec)
    grab = GrabThread(source)
    transport = HttpInferenceTransport(config.inference_url, config.request_timeout_s)
    sender = OneInFlightSender(transport)
    async with EventLinkServer(
        config.ws_host, config.ws_port, pipeline_delay_d_ms=config.pipeline_delay_d_ms
    ) as server:
        clock_provider = _clock_provider(config, server)
        pipeline = BridgePipeline(
            config=config, grab=grab, sender=sender,
            clock_provider=clock_provider, event_sink=server,
        )
        grab.start()
        logger.info(
            "bridge up: source=%s rung=%d crop=%dpx q%d %.1f fps -> %s (ws :%d)",
            config.source_spec, config.rung.index, config.rung.crop_px,
            config.rung.jpeg_quality, config.effective_send_fps,
            config.inference_url, config.ws_port,
        )
        try:
            stats = await pipeline.run(duration_s=duration_s)
        finally:
            await sender.aclose()
            grab.stop()
    logger.info("final: %s", format_stats_line(stats))


def _clock_provider(config: BridgeConfig, server: EventLinkServer):
    if not config.allow_unsynced:
        return lambda: server.phone_clock
    logger.warning(UNSYNCED_BANNER)
    laptop_clock: CaptureClock = LaptopClock(pipeline_delay_d_ms=config.pipeline_delay_d_ms)
    return lambda: laptop_clock


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), format=LOG_FORMAT, stream=sys.stderr)
    try:
        config = build_config(args)
    except ValueError as exc:
        logger.error("bad configuration: %s", exc)
        return 2
    try:
        asyncio.run(run_bridge(config, duration_s=args.duration_s))
    except KeyboardInterrupt:
        logger.info("interrupted; shutting down")
    except (OSError, ValueError, RuntimeError) as exc:
        logger.error("bridge failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
