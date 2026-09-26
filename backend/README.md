# backend/

Python side of the smart cane: the `cane.contracts` AwarenessEvent model
(Step 2) and the `cane.transport` NTP handshake + WebSocket event link
server (Step 3, `docs/camera-sync-design.md` §4.2-4.3).

## Setup

```bash
cd backend
uv sync
uv run pytest
```

## Running the event link server (`cane-link`)

```bash
uv run python -m cane.transport.server --host 0.0.0.0 --port 8765 \
    --ping-interval 5 --pipeline-delay-ms 0 --demo-event-every 0
```

equivalently, the console script:

```bash
uv run cane-link --port 8765
```

Flags:

| Flag | Default | Meaning |
|---|---|---|
| `--host` | `0.0.0.0` | Interface to bind. |
| `--port` | `8765` | TCP port. |
| `--ping-interval` | `5` | Seconds between NTP handshakes (D3's `PING_INTERVAL_S`). |
| `--pipeline-delay-ms` | `0` | The measured pipeline constant `D` (§4.3) -- MUST be measured, spike 4; `0` is a placeholder, not a real value. |
| `--demo-event-every` | `0` | If > 0, sends the golden fixture's first valid `AwarenessEvent` every N seconds, with `t_capture_ms` restamped through the current phone clock. `0` disables the demo loop. |

### LAN acceptance test (±10 ms offset stability)

This is the runbook Step 3 acceptance tool for "offset stable within ±10 ms
over hotspot LAN": on every pong, the offset and RTT print to stderr, e.g.

```
offset=6.2ms rtt=11ms
```

1. Start `cane-link` on the laptop, joined to the same hotspot LAN as the
   phone.
2. Connect the Swift `EventLinkClient` (or any WebSocket client that
   answers `ping` with `pong`) from the phone.
3. Watch the `offset=...ms rtt=...ms` lines settle and stay within ±10 ms
   of each other over a few minutes.
4. To exercise the clock-jump self-correction (must settle within 10 s,
   i.e. two handshakes), change the phone's or laptop's system clock by a
   few seconds mid-run and watch the offset line jump once, then hold at
   the new value.

## Environment note: editable install and `-m`/console-script invocation

`uv sync`'s editable install of this project writes a `.pth` file that can
end up with the macOS `UF_HIDDEN` flag set (a filesystem quirk of this
dev machine); when that happens, CPython's `site.py` silently skips it and
plain `import cane` fails outside pytest (pytest works around this itself
via `pythonpath = ["src"]` in `pyproject.toml`). If `uv run python -m
cane.transport.server` or `uv run cane-link` reports `ModuleNotFoundError:
No module named 'cane'` on this machine, the fix is a `sitecustomize.py`
dropped into `.venv/lib/python3.12/site-packages/` that unconditionally
puts `backend/src` on `sys.path`; it is local to this `.venv` (not part of
the package, not committed) and needs recreating if `.venv` itself is ever
rebuilt from scratch.
