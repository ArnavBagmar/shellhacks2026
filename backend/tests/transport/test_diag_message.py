"""The phone's ``diag`` frames: decoded, validated, and written to the
``cane.phone`` logger as one JSON line each (step 20 black-box recorder).
"""

from __future__ import annotations

import asyncio
import json
import logging

import pytest
import websockets
from jsonschema import Draft202012Validator

from cane.transport.file_logging import configure_file_logging
from cane.transport.messages import MalformedMessageError, PhoneDiagnostic, decode
from cane.transport.server import EventLinkServer

FRAME = {"type": "diag", "t_phone_ms": 1_790_000_000_000, "kind": "transcript",
         "data": {"text": "hey cane is there a bathroom", "final": False, "level": 0.4}}


def test_a_diag_frame_decodes() -> None:
    parsed = decode(json.dumps(FRAME))
    assert isinstance(parsed, PhoneDiagnostic)
    assert parsed.data["text"] == "hey cane is there a bathroom"


@pytest.mark.parametrize("patch", [
    {"kind": "Has Spaces"},
    {"data": {"nested": {"no": 1}}},
    {"data": {"text": "x" * 501}},
    {"extra": 1},
])
def test_a_bad_diag_frame_is_rejected(patch: dict[str, object]) -> None:
    with pytest.raises(MalformedMessageError):
        decode(json.dumps({**FRAME, **patch}))


def test_the_schema_accepts_the_frame(envelope_validator: Draft202012Validator) -> None:
    assert envelope_validator.is_valid(FRAME)
    assert not envelope_validator.is_valid({**FRAME, "kind": "Bad Kind"})


async def test_the_server_writes_diag_lines_to_the_phone_log(tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    _, phone_log = configure_file_logging(tmp_path)
    try:
        async with EventLinkServer("127.0.0.1", 0, ping_interval_s=0.05) as srv:
            async with websockets.connect(f"ws://127.0.0.1:{srv.port}") as phone:
                await asyncio.wait_for(phone.recv(), timeout=2)
                await phone.send(json.dumps(FRAME))
                for _ in range(100):
                    if phone_log.exists() and phone_log.read_text().strip():
                        break
                    await asyncio.sleep(0.01)
        line = json.loads(phone_log.read_text().strip().splitlines()[-1])
        assert line["kind"] == "transcript"
        assert line["data"]["final"] is False
        assert srv.frames_ignored == 0
    finally:
        for name in ("", "cane.phone"):
            for handler in list(logging.getLogger(name).handlers):
                if isinstance(handler, logging.FileHandler) and str(tmp_path) in handler.baseFilename:
                    logging.getLogger(name).removeHandler(handler)
                    handler.close()
