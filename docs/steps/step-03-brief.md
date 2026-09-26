# Task 3 brief — NTP clock handshake + WebSocket transport

Extracted from docs/orchestration-runbook.md (Step 3) and docs/camera-sync-design.md §4.1–§4.3, plus controller rulings. This file is the single source of requirements; exact values here are binding.

## A. Runbook step text (verbatim)

## Step 3 — NTP clock handshake + WebSocket transport

**Intent:** One shared clock, so stale warnings get dropped instead of announced late.
**Chain rationale:** Cross-language by nature — the server is Python and the client is Swift, so
both reviewers are required. `tdd-guide` leads because offset math is exactly the kind of thing
that looks right and is wrong.

```bash
/everything-claude-code:orchestrate custom "ecc:tdd-guide,ecc:python-reviewer,ecc:swift-reviewer" "[Plan: docs/camera-sync-design.md#step-3] Implement the §4.2 NTP handshake over the WebSocket: laptop PING(t1) then phone PONG(t2,t3), offset=((t2-t1)+(t3-t4))/2, rtt=(t4-t1)-(t3-t2), every 5 s, keeping the last 9 samples and using the LOWEST-RTT sample, not the median of the offsets. Apply the measured pipeline constant D on the laptop so t_capture_ms lands in phone-clock terms. Python WebSocket server, Swift URLSessionWebSocketTask client. Acceptance: offset stable within ±10 ms over hotspot LAN; a simulated clock jump self-corrects within 10 s; events round-trip end to end"
```

---


## B. Spec: camera-sync-design.md §4.1–§4.3 (verbatim)

## 4. Clock, staleness, and the event contract

### 4.1 Three clock decisions

1. **The phone is the master clock.** It is what acts and what the user experiences.
2. **The cloud is timeless.** It echoes `t_capture` and never reads its own clock.
3. **Events route cloud → laptop → phone**, not cloud → phone. Costs ~10 ms; buys a single clock
   pair to sync, and both detectors share one event path.

### 4.2 Offset handshake

Standard NTP four-timestamp exchange over the existing WebSocket:

```
laptop --PING(t1)--> phone          t1, t4 = laptop clock
laptop <--PONG(t2,t3)-- phone       t2, t3 = phone clock

offset = ((t2 - t1) + (t3 - t4)) / 2
rtt    =  (t4 - t1) - (t3 - t2)
```

Run every 5 s, keep the last 9 samples, and **use the sample with the lowest RTT — not the median
of the offsets.** That is what real NTP does: the lowest-RTT sample has the least path asymmetry,
and averaging drags it toward the jittery ones.

Over a hotspot LAN (RTT 5–20 ms) this pins the offset to ±10 ms. Re-handshaking every 5 s also
means a system clock jump self-corrects — do not build anything for that.

### 4.3 Pipeline delay constant, D

The offset converts laptop time to phone time. It does **not** say when photons hit the sensor.
UVC gives no trustworthy capture timestamp, so `t_arrival_laptop` trails true capture by exposure
+ ISP + USB + decode. That is `D`, roughly constant for a fixed resolution and fps.

Do not guess it. 30 ms and 150 ms are both plausible and mean very different things for a cyclist.

**Primary measurement (~5 min, no second device):** render a large millisecond counter on the
*laptop's own screen*, point the X4 at it, and in the grab loop compare the digits visible in the
frame against the laptop clock when the frame arrived.

```
D = t_arrival_laptop − t_displayed_in_frame
```

The displayed time *is* the capture time in the laptop's own clock, so this is self-referential and
exact. Take 20 samples, use the median, record the spread.

*Caveat:* the panel adds 1–2 frames (16–33 ms) of its own, so this slightly **under**-estimates D.
Acceptable inside a 100 ms+ budget, but know it is there.

**End-to-end cross-check:** flash the iPhone torch at the X4 on a bench and compare the phone's
timestamp for the flash against the laptop's `t_arrival` for the frame containing it. Yields
`D + offset` directly in phone-clock terms.

> **If the two measurements disagree by more than ~30 ms, the NTP offset is wrong.**
> Find that out on Friday afternoon, not on stage.


## C. What Step 2 already built (use it, do not re-implement)

- Python: `backend/` is a uv project (Python 3.12, `uv run pytest`). `cane.contracts.awareness_event` exports `AwarenessEvent` (frozen Pydantic v2), `parse_awareness_event(payload) -> AwarenessEvent` raising `AwarenessEventError`, and the `Source`, `ObjectClass`, `RangeBand` enums. Check the real names with a quick read of that file before coding.
- Swift: `ios/CaneKit` SwiftPM package (`swift test` on macOS). Target `CaneContracts` exports `AwarenessEvent` with `static func decode(_ data: Data) throws -> AwarenessEvent`, `func encoded() throws -> Data`, and `AwarenessEventError`. Check the real names before coding.
- Fixture: `contracts/fixtures/awareness_events.golden.json` — its first `"expect": "valid"` case is a ready-made valid event for transport tests.

## D. Controller rulings — binding

### D1. Roles and clocks
- The **laptop is the WebSocket server** (Python, `websockets` library, asyncio). The **phone is the client** (Swift, `URLSessionWebSocketTask`). The laptop initiates every PING; the phone only answers.
- **All timestamps on the wire are integer Unix-epoch milliseconds.** No floats.
- `offset_ms = ((t2 − t1) + (t3 − t4)) / 2` is **phone minus laptop**. `rtt_ms = (t4 − t1) − (t3 − t2)`.
- Converting a laptop arrival time to the phone clock: `t_capture_ms = t_arrival_laptop_ms − D_ms + offset_ms`, where `D_ms` is the measured pipeline delay constant (config value, default `0`, documented as "MUST be measured — spike 4").
- **The phone never estimates an offset.** Events arrive already in phone-clock terms, so the Swift side has no clock-sync math beyond answering PONG.

### D2. Message envelope (the second shared artifact after the schema)
All WebSocket frames are JSON text objects with a `type` string:

```json
{"type": "ping", "t1": 1790000000000}
{"type": "pong", "t1": 1790000000000, "t2": 1790000000012, "t3": 1790000000013}
{"type": "event", "event": { ...the 11-field AwarenessEvent... }}
```
- `pong` echoes `t1` so the laptop can match it to its ping; `t2` = phone receive time, `t3` = phone send time.
- Types `health` and `location` are **reserved** (phone → laptop; defined in later steps). A receiver that sees an unknown `type` **logs at debug level and ignores it** — never errors, never disconnects. Malformed JSON, or an `event` payload that fails contract validation, is logged (warning) and dropped; the connection stays up.
- Document the envelope in `contracts/README.md` (append a short "Transport envelope" section) and in a new `contracts/transport_envelope.schema.json` (draft 2020-12, `oneOf` over the three message shapes, `event` referencing `awareness_event.schema.json` by `$ref`). Both language test suites do NOT need to validate against this second schema — it is documentation plus a Python unit test that every message the Python side emits validates against it.

### D3. Offset estimator (Python, pure, immutable)
File `backend/src/cane/transport/clock_sync.py`:
- `@dataclass(frozen=True) class NtpSample: t1, t2, t3, t4 (int ms)` with properties `offset_ms: float` and `rtt_ms: int`.
- `@dataclass(frozen=True) class OffsetEstimator` holding a tuple of at most `WINDOW = 9` samples. `add(sample) -> OffsetEstimator` returns a **new** estimator (no mutation). `best: NtpSample | None` = the **lowest-RTT** sample in the window (ties → most recent). `offset_ms` = `best.offset_ms`. `is_synced` = at least one sample.
- **Clock-jump rule (controller addition):** if the new sample's offset differs from the current `best.offset_ms` by more than `JUMP_THRESHOLD_MS = 100`, the window is **flushed** and the new sample becomes the only one. Why: with a 9-sample lowest-RTT window, a pre-jump low-RTT sample would otherwise pin a stale offset for up to 45 s; the runbook requires self-correction within 10 s (two handshakes). A rejected/negative RTT sample (`rtt_ms < 0`) is discarded and does not change the estimator.
- `class PhoneClock` (frozen): `from_estimator(est, pipeline_delay_d_ms)` with `capture_time_ms(t_arrival_laptop_ms) -> int` implementing D1's formula; raises `ClockNotSyncedError` if there is no sample yet.
- Constants live at module top: `WINDOW = 9`, `JUMP_THRESHOLD_MS = 100`, `PING_INTERVAL_S = 5.0`.

### D4. Python transport
Files under `backend/src/cane/transport/`:
- `messages.py` — frozen dataclasses `Ping(t1)`, `Pong(t1, t2, t3)`, `EventMessage(event: AwarenessEvent)`, `Unknown(type: str)`; `encode(msg) -> str`, `decode(text) -> Ping | Pong | EventMessage | Unknown` raising `MalformedMessageError` (subclass of `ValueError`) on bad JSON / missing fields / invalid event. Use the contract's `parse_awareness_event` for the event payload.
- `server.py` — `class EventLinkServer`: `async with EventLinkServer(host, port, ping_interval_s, pipeline_delay_d_ms) as srv:` serves one phone at a time (a second connection replaces the first, logged). Runs a ping loop every `ping_interval_s`; on pong stamps `t4` and updates an `OffsetEstimator` (kept immutably: the server holds the latest estimator instance). Exposes `srv.phone_clock -> PhoneClock` and `async srv.send_event(event: AwarenessEvent)`. Unknown/malformed inbound frames follow D2. A dropped socket must not raise out of `send_event`; it returns `False` and logs.
- `__main__.py` (so `uv run python -m cane.transport.server` works — or an equivalent `cane-link` console script in pyproject): flags `--host 0.0.0.0 --port 8765 --ping-interval 5 --pipeline-delay-ms 0 --demo-event-every 0` where a non-zero `--demo-event-every N` sends the fixture's first valid event every N seconds with `t_capture_ms` restamped via `phone_clock.capture_time_ms(now)`. Prints the current offset and RTT to stderr on every pong so a human can watch the ±10 ms LAN acceptance test. This is the LAN acceptance tool; document its use in `backend/README.md` (short).
- Add `websockets>=13` to `pyproject.toml` deps and `pytest-asyncio` to dev deps.

### D5. Swift transport
Target `CaneTransport` (depends on `CaneContracts`) under `ios/CaneKit/Sources/CaneTransport/`:
- `PhoneClockResponder.swift` — pure: `static func pong(forPing data: Data, now: () -> Int64) throws -> Data`; parses a `ping`, produces a `pong` with `t1` echoed, `t2 = now()` at parse, `t3 = now()` at encode. Throws `TransportError.malformed` on anything else.
- `Envelope.swift` — `enum InboundMessage { case ping(t1: Int64), case event(AwarenessEvent), case unknown(type: String) }` with `static func decode(_ data: Data) throws -> InboundMessage` (contract failures become `TransportError.invalidEvent(AwarenessEventError)`).
- `EventLinkClient.swift` — `public final class EventLinkClient` wrapping `URLSessionWebSocketTask`: `init(url: URL, session: URLSession = .shared, now: @escaping () -> Int64 = { Int64(Date().timeIntervalSince1970 * 1000) })`; `func connect()`; `func disconnect()`; `var events: AsyncStream<AwarenessEvent>`; auto-answers pings; on unknown/malformed frames logs (os.Logger, subsystem `cane`, category `transport`) and continues; reconnects with capped exponential backoff (0.5 s → 8 s) when the socket drops. Keep it under ~200 lines; split if larger.
- No offset estimation in Swift (D1).

### D6. Tests (TDD — RED then GREEN, evidence in the report)
Python (`backend/tests/transport/`):
- `test_clock_sync.py`: known-value offset/rtt (e.g. t1=1000, t2=1105, t3=1106, t4=1011 → offset 100, rtt 10); lowest-RTT selection beats median (construct 8 jittery samples whose offsets are off by ±30 and one low-RTT sample with the true offset; assert `offset_ms` equals the true one within 1 ms); window eviction at 10 samples; jump rule (9 samples at offset ≈ 0, then one at 5000 → `offset_ms == 5000` immediately, window length 1); negative RTT discarded; `PhoneClock.capture_time_ms` formula with D = 40 and offset = 100; `ClockNotSyncedError` before any sample; `add` returns a new instance and leaves the old one unchanged.
- `test_messages.py`: encode/decode round-trip for all three types; unknown type → `Unknown`; malformed JSON → `MalformedMessageError`; `event` with an invalid contract payload → `MalformedMessageError`; every encoded message validates against `contracts/transport_envelope.schema.json` (jsonschema, with a `$ref` resolver/registry for the event schema).
- `test_server.py` (pytest-asyncio, real sockets on 127.0.0.1, random free port): a fake phone client receives a ping and answers pong → `srv.phone_clock` becomes synced with the expected offset (fake phone uses `laptop_now + 250` as its clock); `send_event` delivers a frame the fake phone decodes back to an equal `AwarenessEvent`; a garbage frame from the phone does not kill the connection (next ping still arrives); `send_event` with no client returns `False`.
Swift (`ios/CaneKit/Tests/CaneTransportTests/`):
- `PhoneClockResponderTests`: pong echoes t1; `t2 <= t3`; integer ms; malformed ping throws `TransportError.malformed`, never traps.
- `EnvelopeTests`: decodes an `event` frame built from the fixture's first valid case; `unknown` type; invalid event → `TransportError.invalidEvent`.
- `EventLinkClientEndToEndTests`: spawns the Python server via `Process` (`uv run python -m cane.transport.server --port <free> --ping-interval 0.2 --demo-event-every 0.3`, cwd `backend/`, path computed from `#filePath`), connects the client, awaits the first event from `events`, asserts it decodes and has `source == .phone`, then disconnects and terminates the process. **`throw XCTSkip` if `uv` is not on PATH or `backend/.venv` does not exist**, and say so in the report. Give it a 15 s timeout.
- Runbook acceptance mapping: ±10 ms LAN stability = human step using the D4 CLI (document it); clock jump within 10 s = the jump-rule unit test; end-to-end round trip = `test_server.py` + the Swift e2e test.

### D7. Out of scope
Health messages, location messages, rung stepping (step 8); any camera or detector code; TLS/auth (hotspot LAN, hackathon).

## E. Global constraints (verbatim, from the spec)
- "The phone is the master clock." "The cloud is timeless. It echoes t_capture and never reads its own clock." "Events route cloud → laptop → phone."
- "keep the last 9 samples, and use the sample with the lowest RTT — not the median of the offsets."
- "Acceptance: offset stable within ±10 ms over hotspot LAN; a simulated clock jump self-corrects within 10 s; events round-trip end to end."
- Commit format `<type>: <description>`; no attribution trailers. Immutable data; small files (<400 lines); explicit error handling; constants not magic numbers.

## F. Architect hazards carried from task 2 (binding)
- Never add a key to the AwarenessEvent object (no `type` inside the event). The envelope is a separate schema; the event is nested untouched under `"event"`. Never validate the envelope against `awareness_event.schema.json`.
- Never serialize an event with `exclude_none` / `response_model_exclude_none`; all 11 keys go on the wire. Serialize via the contract module's own helper (check what step 2 exposes — e.g. a `to_wire_dict()` / `model_dump(by_alias=True)` path) so the version key is spelled `schema`.
- Batching (`{"events": [...]}`) is an envelope decision; not needed now — one event per frame.

## G. Confirmed step-2 names (verified by the controller after task 2 landed)
Python: `from cane.contracts.awareness_event import AwarenessEvent, AwarenessEventError, Source, ObjectClass, RangeBand, parse_awareness_event`. The Pydantic field for the wire key `schema` is aliased (`schema_version`), so ALWAYS serialize with `event.model_dump(by_alias=True)` / `model_dump_json(by_alias=True)` — add a tiny `to_wire_dict(event)` helper in `cane/transport/messages.py` (or reuse one if step 2 added it) so no caller can forget `by_alias`.
Swift: `AwarenessEvent.decode(_ data: Data) throws -> AwarenessEvent`, `event.encoded() throws -> Data`, `AwarenessEventError`, `AwarenessEventBounds`; enums `Source`, `ObjectClass`, `RangeBand`. The fixture's first valid case is named `phone_metric_both_range_fields`.
