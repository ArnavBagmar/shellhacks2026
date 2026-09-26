"""CLI entry point for the laptop-side event link server (D4).

Run with ``uv run python -m cane.transport.server`` (delegated from
``server.py``) or the ``cane-link`` console script (see
``backend/README.md`` for the LAN acceptance-test workflow). Prints the
current offset/RTT to stderr on every pong so a human can watch the ±10 ms
hotspot-LAN acceptance test; with ``--demo-event-every N`` (N > 0) it also
sends the golden fixture's first valid event every N seconds, with
``t_capture_ms`` restamped through the phone clock.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

from cane.contracts.awareness_event import parse_awareness_event
from cane.transport.clock_sync import PING_INTERVAL_S, ClockNotSyncedError
from cane.transport.server import EventLinkServer
from cane.transport.wall_clock import now_ms

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8765
DEFAULT_PIPELINE_DELAY_MS = 0
DEFAULT_DEMO_EVENT_EVERY_S = 0.0

# How often the CLI polls the estimator to notice a newly-arrived pong and
# print its offset/RTT. Small relative to the default 5 s ping interval so
# a human watching the LAN acceptance test sees each handshake promptly.
OFFSET_PRINT_POLL_INTERVAL_S = 0.1

# This file lives at backend/src/cane/transport/__main__.py; the fixture is
# four directories up from there, at the repo root.
_FIXTURE_PATH = (
    Path(__file__).resolve().parents[4] / "contracts" / "fixtures" / "awareness_events.golden.json"
)


def _load_demo_event_payload() -> dict[str, Any]:
    """The fixture's first ``expect: valid`` case
    (``phone_metric_both_range_fields``, task-3-brief.md G) as a raw dict,
    ready to have ``t_capture_ms`` restamped.
    """
    with _FIXTURE_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    first_valid = next(case for case in data["cases"] if case["expect"] == "valid")
    return dict(first_valid["event"])


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="cane-link", description="Laptop-side NTP handshake + event link server."
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--ping-interval", type=float, default=PING_INTERVAL_S)
    parser.add_argument("--pipeline-delay-ms", type=int, default=DEFAULT_PIPELINE_DELAY_MS)
    parser.add_argument(
        "--demo-event-every",
        type=float,
        default=DEFAULT_DEMO_EVENT_EVERY_S,
        help="Seconds between demo AwarenessEvent sends; 0 disables the demo loop.",
    )
    return parser.parse_args(argv)


async def _print_offset_on_every_pong(srv: EventLinkServer) -> None:
    """Poll for a newly-arrived pong and print its offset/RTT to stderr.

    Polls ``estimator.samples[-1]`` (the most-recently added raw sample,
    not necessarily the window's lowest-RTT ``best``) rather than the
    server's own internals, so this stays outside ``EventLinkServer``'s
    public surface entirely.
    """
    last_sample = None
    while True:
        await asyncio.sleep(OFFSET_PRINT_POLL_INTERVAL_S)
        samples = srv.phone_clock.estimator.samples
        if not samples:
            continue
        newest = samples[-1]
        if newest != last_sample:
            last_sample = newest
            print(f"offset={newest.offset_ms:.1f}ms rtt={newest.rtt_ms}ms", file=sys.stderr)


async def _demo_event_loop(srv: EventLinkServer, interval_s: float) -> None:
    payload = _load_demo_event_payload()
    while True:
        await asyncio.sleep(interval_s)
        try:
            t_capture_ms = srv.phone_clock.capture_time_ms(now_ms())
        except ClockNotSyncedError:
            print("demo-event: clock not synced yet, skipping", file=sys.stderr)
            continue
        event = parse_awareness_event({**payload, "t_capture_ms": t_capture_ms})
        delivered = await srv.send_event(event)
        status = "sent" if delivered else "skipped (no phone connected)"
        print(f"demo-event: {status} (t_capture_ms={t_capture_ms})", file=sys.stderr)


async def _run(args: argparse.Namespace) -> None:
    async with EventLinkServer(
        args.host,
        args.port,
        ping_interval_s=args.ping_interval,
        pipeline_delay_d_ms=args.pipeline_delay_ms,
    ) as srv:
        print(
            f"cane-link listening on {args.host}:{srv.port} "
            f"(ping every {args.ping_interval}s, D={args.pipeline_delay_ms}ms)",
            file=sys.stderr,
        )
        tasks = [asyncio.create_task(_print_offset_on_every_pong(srv))]
        if args.demo_event_every > 0:
            tasks.append(asyncio.create_task(_demo_event_loop(srv, args.demo_event_every)))
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO)
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
