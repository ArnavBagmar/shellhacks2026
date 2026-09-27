"""``PYTHONPATH=src uv run python -m cane.bridge`` -- run the X4 bridge.

    python -m cane.bridge --source x4 --rung 1 \
        --inference-url http://127.0.0.1:8000/infer --ws-port 8765

Sources: ``synthetic`` (no hardware), ``file:PATH``, ``index:N``, ``x4``
(macOS AVFoundation, the stitched 2880x1440 panorama mode).

Step 8 adds the silent fallback: the health monitor steps rungs, flips to
the in-process local detector and probes its way back, all without a
keystroke. ``--local-detector`` / ``--local-weights`` choose what the local
half runs (defaults: ultralytics on MPS when torch is importable, the
scripted detector otherwise), and ``--start-mode local`` starts there --
useful when the venue's uplink is known-bad before the walk begins.

Step 20 adds ``--scene``: the phone's spoken questions ("Hey Cane, is there a
bathroom around?") are answered by Gemini over the freshest X4 frame, on the
same WebSocket. Needs the ``scene`` extra and ``GEMINI_API_KEY``.

Step 26 feeds the steering evidence into that answer: with ``--scene`` the
``steer`` stream also passes through ``SteerTap``, so "what is in front of me"
names the side the haptic cue is steering toward, and ``blocked`` -- which the
haptic alphabet has no room to say -- gets spoken.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import sys
from pathlib import Path
from typing import Sequence

from cane.bridge.capture.base import FrameSource
from cane.bridge.capture.grab_thread import GrabThread
from cane.bridge.capture.opencv_source import open_file, open_index
from cane.bridge.backends import (
    CloudBackend,
    LocalBackend,
    SwitchingBackend,
    local_settings,
)
from cane.bridge.capture.synthetic_source import SyntheticEquirectSource
from cane.bridge.config import (
    DEFAULT_INFERENCE_URL,
    DEFAULT_LOCAL_TIMEOUT_S,
    DEFAULT_REQUEST_TIMEOUT_S,
    DEFAULT_WS_HOST,
    DEFAULT_WS_PORT,
    BridgeConfig,
)
from cane.bridge.health import HealthMonitor, Mode
from cane.bridge.pipeline import (
    BridgePipeline,
    CaptureClock,
    LaptopClock,
    SteerSink,
    format_stats_line,
)
from cane.bridge.probe import CloudProbe, HttpHealthz, healthz_url_for
from cane.bridge.rungs import RUNGS, rung_by_index
from cane.bridge.sender import OneInFlightSender
from cane.bridge.transport_http import HttpInferenceTransport
from cane.bridge.uplink import MonitoredUplink
from cane.scene.frames import GrabProvider
from cane.scene.gap import LatestClearWay, SteerTap
from cane.scene.service import HEAD_WORN_LAYOUT, SceneQueryService
from cane.scene.vlm import GeminiVLM, VlmUnavailableError
from cane.scene.voice import ElevenLabsVoice
from cane.scene.weather import weather_from_env
from cane.transport.file_logging import configure_file_logging
from cane.transport.messages import PhoneHealth, SceneQuery
from cane.transport.server import EventLinkServer
from cane.transport.wall_clock import now_ms

logger = logging.getLogger("cane.bridge")

SOURCE_SYNTHETIC = "synthetic"
SOURCE_X4 = "x4"
FILE_PREFIX = "file:"
INDEX_PREFIX = "index:"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
LOCAL_DETECTORS = ("fake", "ultralytics")
START_MODES = ("cloud", "local")
# Exit codes: 1 a failed run, 2 a bad configuration, 3 a local detector that
# would not load. Distinct so a launch script can tell them apart.
EXIT_RUN_FAILED = 1
EXIT_BAD_CONFIG = 2
EXIT_NO_LOCAL_DETECTOR = 3
EXIT_NO_SCENE_MODEL = 4
# httpx logs one INFO line per request; at 3 fps that buries the 5 s stats line.
NOISY_LOGGERS = ("httpx", "httpcore")
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
    parser.add_argument(
        "--local-detector",
        default=None,
        choices=LOCAL_DETECTORS,
        help="what the local fallback runs (default: CANE_LOCAL_DETECTOR, "
        "else ultralytics when torch is importable, else fake)",
    )
    parser.add_argument(
        "--local-weights",
        default=None,
        help="weights for the local fallback (default: CANE_LOCAL_WEIGHTS, "
        "else yolo11s.mlpackage if present, else yolo11s.pt)",
    )
    parser.add_argument(
        "--start-mode",
        default=START_MODES[0],
        choices=START_MODES,
        help="start on the cloud (default) or straight on the local detector",
    )
    parser.add_argument(
        "--scene",
        action="store_true",
        help="answer the phone's spoken questions with Gemini over the X4 view "
        "(needs the scene extra and GEMINI_API_KEY)",
    )
    parser.add_argument("--duration-s", type=float, default=None, help="stop after N seconds")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument(
        "--log-dir", default="logs",
        help="also write laptop-YYYYMMDD.log and the phone's phone-YYYYMMDD.jsonl here ('' to disable)",
    )
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


def build_monitor(args: argparse.Namespace) -> HealthMonitor:
    """The monitor starts where ``--start-mode`` says, on ``--rung``."""
    rung = rung_by_index(args.rung).index
    start = Mode.local(rung) if args.start_mode == "local" else Mode.cloud(rung)
    return HealthMonitor(start)


def build_local_backend(
    args: argparse.Namespace, timeout_s: float = DEFAULT_LOCAL_TIMEOUT_S
) -> LocalBackend:
    """Load the fallback detector now -- and warm it -- not at the flip.

    A loader fault is fatal on purpose. Silently substituting the scripted
    detector would leave a bridge that looks healthy, flips at the worst
    possible moment and then detects nothing at all; the only symptom would
    be an event stream that stopped. Better to refuse to start, name the
    fault, and let the operator choose `--local-detector fake` if a
    detection-free fallback really is what they want.
    """
    settings = local_settings(args.local_detector, args.local_weights)
    try:
        return LocalBackend.build(
            settings,
            warm_up_px=rung_by_index(args.rung).crop_px,
            timeout_s=timeout_s,
        )
    except Exception as exc:  # noqa: BLE001 - any loader fault, torch included
        logger.error(
            "could not build the local detector %r from %s (%s: %s); refusing to "
            "start without the fallback -- fix the weights, or pass "
            "--local-detector fake to run with no local detections",
            settings.detector, settings.weights_path, type(exc).__name__, exc,
        )
        raise SystemExit(EXIT_NO_LOCAL_DETECTOR) from exc


def build_uplink(
    config: BridgeConfig, monitor: HealthMonitor, local: LocalBackend
) -> tuple[MonitoredUplink, CloudProbe]:
    """Cloud + local behind one switch, plus the prober that recovers."""
    transport = HttpInferenceTransport(config.inference_url, config.request_timeout_s)
    switching = SwitchingBackend(
        monitor,
        cloud=CloudBackend(transport, timeout_s=config.request_timeout_s),
        local=local,
    )
    healthz = HttpHealthz(healthz_url_for(config.inference_url), config.request_timeout_s)
    return MonitoredUplink(switching, monitor), CloudProbe(healthz, monitor)


def build_steer_sink(
    server: EventLinkServer, clear_ways: LatestClearWay | None
) -> SteerSink:
    """The phone's steer stream, tapped for the voice layer when it is on.

    Without ``--scene`` this is the server itself and the steer path is
    byte-for-byte step 23's. With it, every frame still reaches the phone
    unchanged and a copy of its decision is kept for the next spoken question
    (step 26) -- so the words and the cue can only ever come from one number.
    """
    if clear_ways is None:
        return server
    return SteerTap(server, clear_ways)


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


async def run_bridge(
    config: BridgeConfig, args: argparse.Namespace, duration_s: float | None = None
) -> None:
    """Wire everything up and run until the duration elapses or Ctrl-C."""
    # Before the camera opens: a --scene that cannot work fails the run
    # without leaving a capture device held open.
    scene_vlm = build_scene_vlm(args)
    grab = GrabThread(open_source(config.source_spec))
    monitor = build_monitor(args)
    local = build_local_backend(args, timeout_s=config.local_timeout_s)
    uplink, probe = build_uplink(config, monitor, local)
    sender = OneInFlightSender(uplink)
    health_hook = PhoneHealthHook(monitor)
    scene_hook = None
    # Step 26: the voice layer answers "what is in front of me" from the same
    # ring the steering cue was rendered from. The scene service runs in this
    # process, so the live evidence is reachable by decorating the steer sink
    # below -- no IPC, and nothing in the steering modules changes. Left as
    # None without --scene, which keeps the default run byte-identical.
    clear_ways: LatestClearWay | None = None
    if scene_vlm is not None:
        from cane.scene.debug_view import start_debug_view

        clear_ways = LatestClearWay()
        scene_hook = SceneHook(
            SceneQueryService(
                scene_vlm,
                GrabProvider(grab),
                clear_ways=clear_ways,
                delta_deg=config.delta_deg,
                weather=weather_from_env(),
                # Local page at http://localhost:8780 (CANE_DEBUG_PORT=0: off).
                debug=start_debug_view(grab=grab),
                # Gemini's views only; the detector keeps its level crops.
                layout=HEAD_WORN_LAYOUT,
            )
        )
    # The natural voice rides with --scene: it speaks the answers.
    scene_voice = ElevenLabsVoice.from_env() if scene_vlm is not None else None
    async with EventLinkServer(
        config.ws_host, config.ws_port,
        pipeline_delay_d_ms=config.pipeline_delay_d_ms,
        on_health=health_hook,
        on_query=scene_hook,
        on_speak=scene_voice.clip if scene_voice is not None else None,
    ) as server:
        health_hook.server = server
        warm: asyncio.Task[None] | None = None
        if scene_hook is not None and scene_vlm is not None:
            scene_hook.server = server
            # Never let the wearer's first question pay for the TLS handshake.
            warm = asyncio.create_task(scene_vlm.warm_up())
        pipeline = BridgePipeline(
            config=config, grab=grab, sender=sender,
            clock_provider=_clock_provider(config, server), event_sink=server,
            # The same object twice: `EventLinkServer` satisfies both sinks,
            # and step 23's `steer` frames ride the socket the events already
            # hold open.
            steer_sink=build_steer_sink(server, clear_ways),
            monitor=monitor, source_provider=lambda: uplink.last_source,
            local_detector=local.detector_name,
            frames_ignored=lambda: server.frames_ignored,
        )
        grab.start()
        _log_banner(config, args, local)
        # Outside the one-in-flight sender on purpose: the prober must never
        # occupy the slot a frame needs, and it only fires while local.
        prober = asyncio.create_task(probe.run())
        try:
            stats = await pipeline.run(duration_s=duration_s)
        finally:
            if warm is not None:
                warm.cancel()
            prober.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await prober
            await probe.aclose()
            await sender.aclose()
            grab.stop()
    logger.info("final: %s", format_stats_line(stats))


def _log_banner(
    config: BridgeConfig, args: argparse.Namespace, local: LocalBackend
) -> None:
    """`fallback=` is read off the backend that was actually built, never
    re-derived from the flags: those two disagreeing is how a run ends up
    believing it has a detector it does not have.
    """
    logger.info(
        "bridge up: source=%s rung=%d crop=%dpx q%d %.1f fps -> %s (ws :%d) "
        "fallback=%s start=%s",
        config.source_spec, config.rung.index, config.rung.crop_px,
        config.rung.jpeg_quality, config.effective_send_fps,
        config.inference_url, config.ws_port,
        local.detector_name,
        args.start_mode,
    )


class PhoneHealthHook:
    """Feeds `health` frames to the monitor in the laptop's clock domain.

    The phone stamps its report with its own clock, so the freshness test
    (rule 4) needs the same NTP offset the capture stamps use. The server
    is attached after construction because it does not exist until the
    listener is up, and the offset is 0 until the first pong lands -- which
    is correct: with no handshake the two clocks are all we can assume.
    """

    def __init__(self, monitor: HealthMonitor) -> None:
        self._monitor = monitor
        self.server: EventLinkServer | None = None

    def __call__(self, health: PhoneHealth) -> None:
        self._monitor.observe_phone_health(health, now_ms(), self.offset_ms())

    def offset_ms(self) -> int:
        if self.server is None:
            return 0
        estimator = self.server.phone_clock.estimator
        return int(estimator.offset_ms) if estimator.is_synced else 0


class SceneHook:
    """Answers `query` frames (step 20). Like :class:`PhoneHealthHook`, the
    server is attached once it exists; an answer computed with no phone left
    to hear it is dropped by ``send_answer``.
    """

    def __init__(self, service: SceneQueryService) -> None:
        self._service = service
        self.server: EventLinkServer | None = None

    async def __call__(self, query: SceneQuery) -> None:
        answer = await self._service.handle(query)
        if answer is not None and self.server is not None:
            await self.server.send_answer(answer)


def build_scene_vlm(args: argparse.Namespace) -> GeminiVLM | None:
    """``None`` without ``--scene``. Raises :class:`VlmUnavailableError` when
    asked for but impossible (no key, no SDK) -- a flag the operator set must
    not silently do nothing.
    """
    if not args.scene:
        return None
    try:
        return GeminiVLM.from_env()
    except ImportError as exc:
        raise VlmUnavailableError("the scene extra is not installed (uv sync --extra scene)") from exc


def _clock_provider(config: BridgeConfig, server: EventLinkServer):
    if not config.allow_unsynced:
        return lambda: server.phone_clock
    logger.warning(UNSYNCED_BANNER)
    laptop_clock: CaptureClock = LaptopClock(pipeline_delay_d_ms=config.pipeline_delay_d_ms)
    return lambda: laptop_clock


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), format=LOG_FORMAT, stream=sys.stderr)
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    if args.log_dir:
        configure_file_logging(Path(args.log_dir))
    try:
        config = build_config(args)
    except ValueError as exc:
        logger.error("bad configuration: %s", exc)
        return EXIT_BAD_CONFIG
    try:
        asyncio.run(run_bridge(config, args, duration_s=args.duration_s))
    except VlmUnavailableError as exc:
        logger.error("--scene: %s", exc)
        return EXIT_NO_SCENE_MODEL
    except KeyboardInterrupt:
        logger.info("interrupted; shutting down")
    except (OSError, ValueError, RuntimeError) as exc:
        logger.error("bridge failed: %s", exc)
        return EXIT_RUN_FAILED
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
