"""The walk recorder: every event that reaches the phone, appended as JSONL.

The recorder exists to measure, so it must never be able to change what it
measures. Two properties carry that: it forwards the inner sink's result
byte-for-byte, and a disk fault is swallowed after logging -- losing a line of
a baseline recording is an inconvenience, dropping a warning to the wearer is
not.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from cane.bridge.recorder import RecordingEventSink, open_recording, wrap_event_sink
from cane.contracts.awareness_event import AwarenessEvent, parse_awareness_event

T_CAPTURE_MS = 1_790_000_000_123
T_EMIT_MS = 1_790_000_000_200


def _event(**patch: object) -> AwarenessEvent:
    """The car-from-the-right event the PRD's geometry table is built on."""
    base: dict[str, object] = {
        "schema": 1,
        "source": "x4_cloud",
        "track_id": "0:12",
        "t_capture_ms": T_CAPTURE_MS,
        "ttl_ms": 400,
        "cls": "car",
        "confidence": 0.81,
        "bearing_deg": 47.0,
        "range_m": None,
        "range_band": "near",
        "closing_mps": 6.2,
    }
    return parse_awareness_event({**base, **patch})


class _Inner:
    """An `EventSink` that remembers what it was handed."""

    def __init__(self, result: bool = True) -> None:
        self.result = result
        self.seen: list[AwarenessEvent] = []

    async def send_event(self, event: AwarenessEvent) -> bool:
        self.seen.append(event)
        return self.result


class _CountingStream(io.StringIO):
    """Counts flushes so the per-event flush promise is testable."""

    def __init__(self) -> None:
        super().__init__()
        self.flushes = 0

    def flush(self) -> None:
        self.flushes += 1
        super().flush()


class _BrokenStream(io.StringIO):
    def write(self, _: str) -> int:
        raise OSError("disk full")


def _lines(stream: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line]


def _clock() -> int:
    return T_EMIT_MS


class TestForwarding:
    """Whatever else it does, the wearer's warning gets through unchanged."""

    @pytest.mark.asyncio
    async def test_forwards_the_event_unchanged(self) -> None:
        inner, stream = _Inner(), io.StringIO()
        event = _event()

        await RecordingEventSink(inner, stream, clock=_clock).send_event(event)

        assert inner.seen == [event]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("result", [True, False])
    async def test_returns_the_inner_sinks_result(self, result: bool) -> None:
        inner, stream = _Inner(result=result), io.StringIO()

        returned = await RecordingEventSink(inner, stream, clock=_clock).send_event(_event())

        assert returned is result

    @pytest.mark.asyncio
    async def test_a_write_failure_never_stops_the_warning(self) -> None:
        """A disk fault degrades the recording, never the device."""
        inner = _Inner()
        subject = RecordingEventSink(inner, _BrokenStream(), clock=_clock)

        returned = await subject.send_event(_event())

        assert returned is True
        assert inner.seen == [_event()]
        assert subject.write_failures == 1


class TestTheRecording:
    @pytest.mark.asyncio
    async def test_writes_one_json_line_per_event(self) -> None:
        stream = io.StringIO()
        subject = RecordingEventSink(_Inner(), stream, clock=_clock)

        await subject.send_event(_event(track_id="0:1"))
        await subject.send_event(_event(track_id="0:2"))

        assert [line["track_id"] for line in _lines(stream)] == ["0:1", "0:2"]

    @pytest.mark.asyncio
    async def test_the_line_is_the_wire_form_plus_the_emit_stamp(self) -> None:
        stream = io.StringIO()
        event = _event()

        await RecordingEventSink(_Inner(), stream, clock=_clock).send_event(event)

        assert _lines(stream) == [{**event.model_dump(by_alias=True), "t_emit_ms": T_EMIT_MS}]

    @pytest.mark.asyncio
    async def test_the_recording_parses_back_into_the_same_event(self) -> None:
        """The replay tool reads these lines; the contract must survive the trip."""
        stream = io.StringIO()
        event = _event()

        await RecordingEventSink(_Inner(), stream, clock=_clock).send_event(event)

        line = _lines(stream)[0]
        assert parse_awareness_event({k: v for k, v in line.items() if k != "t_emit_ms"}) == event

    @pytest.mark.asyncio
    async def test_flushes_every_event_so_a_killed_walk_keeps_its_data(self) -> None:
        stream = _CountingStream()
        subject = RecordingEventSink(_Inner(), stream, clock=_clock)

        await subject.send_event(_event())
        await subject.send_event(_event())

        assert stream.flushes == 2


class TestOpenRecording:
    def test_refuses_to_overwrite_an_existing_recording(self, tmp_path: Path) -> None:
        existing = tmp_path / "walk.jsonl"
        existing.write_text("the baseline you were about to lose\n")

        with pytest.raises(FileExistsError):
            open_recording(existing)

        assert existing.read_text() == "the baseline you were about to lose\n"

    def test_opens_a_new_recording_for_writing(self, tmp_path: Path) -> None:
        path = tmp_path / "walk.jsonl"

        with open_recording(path) as stream:
            stream.write("line\n")

        assert path.read_text() == "line\n"

    def test_creates_the_parent_directory(self, tmp_path: Path) -> None:
        path = tmp_path / "recordings" / "walk.jsonl"

        with open_recording(path) as stream:
            stream.write("line\n")

        assert path.read_text() == "line\n"


class TestWrapEventSink:
    """The no-flag path must be the object graph the bridge has always had."""

    def test_no_recording_returns_the_very_same_sink(self) -> None:
        inner = _Inner()

        assert wrap_event_sink(inner, None) is inner

    def test_a_stream_wraps_the_sink(self) -> None:
        inner = _Inner()

        wrapped = wrap_event_sink(inner, io.StringIO())

        assert isinstance(wrapped, RecordingEventSink)
