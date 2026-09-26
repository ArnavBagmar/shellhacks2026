"""Scene questions without the rest of the system.

One-shot, laptop only (no phone), on the live X4 (USB webcam mode, 2880x1440)::

    GEMINI_API_KEY=... uv run --extra scene python -m cane.scene --source x4 --find door --save-views views

One-shot over a still image instead (no X4)::

    GEMINI_API_KEY=... uv run --extra scene python -m cane.scene \\
        --equirect ../docs/steps/assets/step-06-live-equirect.jpg --find bathroom

Serve the phone over a still image (no X4), so the app's "Hey Cane" flow can
be tested end to end::

    GEMINI_API_KEY=... uv run --extra scene python -m cane.scene --serve \\
        --equirect ../docs/steps/assets/step-06-live-equirect.jpg

``--fake`` swaps Gemini for a stub that never finds anything (no key needed).
With the X4 attached, use ``python -m cane.bridge ... --scene`` instead.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import sys
import time
import uuid
from pathlib import Path
from typing import Sequence

import cv2

from cane.bridge.capture.grab_thread import GrabThread
from cane.scene.frames import EquirectProvider, GrabProvider, StillProvider
from cane.scene.service import DEFAULT_VLM_TIMEOUT_S, SceneQueryService
from cane.scene.vlm import FakeVLM, GeminiVLM, VisionLanguageModel, VlmUnavailableError
from cane.transport.file_logging import configure_file_logging
from cane.transport.messages import PhoneFrame, SceneQuery

logger = logging.getLogger("cane.scene")

DEFAULT_WS_HOST = "0.0.0.0"
DEFAULT_WS_PORT = 8765
DEFAULT_PHONE_HFOV_DEG = 70.0
FIRST_FRAME_WAIT_S = 10.0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m cane.scene", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--equirect", type=Path, default=None, help="a 2:1 equirect still (BGR JPEG/PNG)")
    parser.add_argument(
        "--source", default=None,
        help="live frames instead of --equirect: x4 | synthetic | file:PATH | index:N (as cane.bridge)",
    )
    parser.add_argument(
        "--save-views", type=Path, default=None, metavar="DIR",
        help="save the images, prompt and model reply of every question under DIR/<query_id>/",
    )
    ask = parser.add_mutually_exclusive_group()
    ask.add_argument("--find", metavar="TARGET", help='one-shot: "is there a TARGET around?"')
    ask.add_argument("--describe-around", action="store_true", help="one-shot: what's around me")
    ask.add_argument("--describe-front", action="store_true", help="one-shot: what's in front of me")
    ask.add_argument("--serve", action="store_true", help="run the phone link and answer its queries")
    parser.add_argument("--phone-image", type=Path, default=None, help="one-shot: attach this JPEG as the phone photo")
    parser.add_argument("--phone-hfov-deg", type=float, default=DEFAULT_PHONE_HFOV_DEG)
    parser.add_argument("--fake", action="store_true", help="use a stub VLM (no key, never finds anything)")
    parser.add_argument(
        "--vlm-timeout-s", type=float, default=DEFAULT_VLM_TIMEOUT_S,
        help="give up on the model after this long (answer 'unavailable')",
    )
    parser.add_argument("--ws-host", default=DEFAULT_WS_HOST)
    parser.add_argument("--ws-port", type=int, default=DEFAULT_WS_PORT)
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument(
        "--log-dir", default="logs",
        help="also write laptop-YYYYMMDD.log and the phone's phone-YYYYMMDD.jsonl here ('' to disable)",
    )
    args = parser.parse_args(argv)
    if not (args.find or args.describe_around or args.describe_front or args.serve):
        parser.error("choose one of --find, --describe-around, --describe-front or --serve")
    if args.equirect is not None and args.source is not None:
        parser.error("use --equirect or --source, not both")
    return args


def build_vlm(fake: bool) -> VisionLanguageModel:
    return FakeVLM() if fake else GeminiVLM.from_env()


def one_shot_query(args: argparse.Namespace) -> SceneQuery:
    if args.find:
        kind, target, utterance = "find", args.find, f"is there a {args.find} around"
    elif args.describe_around:
        kind, target, utterance = "describe_around", None, "what's around me"
    else:
        kind, target, utterance = "describe_front", None, "what's in front of me"
    phone_frame = None
    if args.phone_image is not None:
        image = cv2.imread(str(args.phone_image), cv2.IMREAD_COLOR)
        if image is None:
            raise FileNotFoundError(f"could not read an image from {args.phone_image}")
        phone_frame = PhoneFrame(
            jpeg_b64=base64.b64encode(args.phone_image.read_bytes()).decode("ascii"),
            hfov_deg=args.phone_hfov_deg, width=int(image.shape[1]), height=int(image.shape[0]),
        )
    return SceneQuery(
        query_id=f"cli-{uuid.uuid4().hex[:8]}", kind=kind, target=target, utterance=utterance,
        t_phone_ms=int(time.time() * 1000), phone_frame=phone_frame,
    )


async def warm_up(vlm: VisionLanguageModel) -> None:
    if isinstance(vlm, GeminiVLM):
        await vlm.warm_up()


async def run_one_shot(service: SceneQueryService, vlm: VisionLanguageModel, query: SceneQuery) -> int:
    # Warm first, so latency_ms is what the phone sees mid-walk, not a cold start.
    await warm_up(vlm)
    answer = await service.answer(query)
    print(json.dumps(answer.model_dump(), indent=2))
    return 0 if answer.status != "unavailable" else 1


async def serve(service: SceneQueryService, vlm: VisionLanguageModel, host: str, port: int) -> None:
    from cane.transport.server import EventLinkServer

    box: list[EventLinkServer] = []

    async def on_query(query: SceneQuery) -> None:
        answer = await service.handle(query)
        if answer is not None and box:
            await box[0].send_answer(answer)

    async with EventLinkServer(host, port, on_query=on_query) as server:
        box.append(server)
        logger.info("scene link on ws://%s:%d -- point the phone app here", host, server.port)
        await warm_up(vlm)
        await asyncio.Event().wait()


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.log_dir:
        laptop_log, phone_log = configure_file_logging(Path(args.log_dir))
        logger.info("logging to %s and %s", laptop_log, phone_log)
    try:
        vlm = build_vlm(args.fake)
    except VlmUnavailableError as exc:
        print(f"error: {exc} (or pass --fake)", file=sys.stderr)
        return 2
    frames: EquirectProvider | None = None
    grab = None
    if args.equirect is not None:
        frames = StillProvider.from_path(args.equirect)
    elif args.source is not None:
        grab = start_live_source(args.source)
        if grab is None:
            return 2
        frames = GrabProvider(grab)
    service = SceneQueryService(
        vlm, frames, settle_s=0.0 if not args.serve else 0.4, vlm_timeout_s=args.vlm_timeout_s,
        dump_dir=args.save_views,
    )
    try:
        return run(args, service, vlm)
    finally:
        if grab is not None:
            grab.stop()


def start_live_source(spec: str) -> GrabThread | None:
    """Open the camera and wait for its first frame, so a one-shot question
    is never asked of a camera that is still starting up.
    """
    from cane.bridge.__main__ import open_source

    try:
        grab = GrabThread(open_source(spec))
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: could not open --source {spec}: {exc}", file=sys.stderr)
        return None
    grab.start()
    if grab.wait_for_new(0, FIRST_FRAME_WAIT_S) is None:
        print(f"error: no frame from {spec} within {FIRST_FRAME_WAIT_S:.0f} s "
              "(is the X4 plugged in and in USB webcam mode?)", file=sys.stderr)
        grab.stop()
        return None
    logger.info("live source %s is streaming", grab.source_name)
    return grab


def run(args: argparse.Namespace, service: SceneQueryService, vlm: VisionLanguageModel) -> int:
    if args.serve:
        try:
            asyncio.run(serve(service, vlm, args.ws_host, args.ws_port))
        except KeyboardInterrupt:
            pass
        return 0
    return asyncio.run(run_one_shot(service, vlm, one_shot_query(args)))


if __name__ == "__main__":
    sys.exit(main())
