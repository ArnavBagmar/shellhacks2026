"""The live view's car-alert switch: off, no vehicle reaches anything."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from cane.bridge.car_alerts import CAR_ALERTS_ENV, CarAlertSwitch, without_vehicles
from cane.cloud.api_models import Detection, InferenceResponse
from cane.scene.debug_view import DebugHub, serve_debug

from .test_steer_pipeline import Harness


def _det(cls: str) -> Detection:
    return Detection(crop_index=1, cx=0.5, cy=0.5, w=0.1, h=0.3, cls=cls, confidence=0.8, track_id="t",
                     range_band="near")


def _response(*classes: str) -> InferenceResponse:
    return InferenceResponse(schema=1, t_capture_ms=1_790_000_000_000, inference_ms=1.0,
                             detections=tuple(_det(c) for c in classes))


def test_without_vehicles_keeps_everything_else() -> None:
    kept = without_vehicles(_response("car", "person", "bus", "parked_car", "pole"))
    assert [d.cls.value for d in kept.detections] == ["person", "pole"]
    same = _response("person")
    assert without_vehicles(same) is same


def test_the_switch_starts_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CAR_ALERTS_ENV, raising=False)
    assert CarAlertSwitch.from_env().on
    monkeypatch.setenv(CAR_ALERTS_ENV, "off")
    assert not CarAlertSwitch.from_env().on


def _cars_harness(switch: CarAlertSwitch) -> Harness:
    harness = Harness()
    original = harness.cloud.infer

    async def cars(meta, jpegs):  # type: ignore[no-untyped-def]
        response = await original(meta, jpegs)
        return response.model_copy(update={"detections": tuple(
            d.model_copy(update={"cls": _det("car").cls}) for d in response.detections)})

    harness.cloud.infer = cars  # type: ignore[method-assign]
    harness.pipeline._car_alerts = switch  # noqa: SLF001
    return harness


async def test_with_car_alerts_off_no_car_reaches_the_phone() -> None:
    off = _cars_harness(CarAlertSwitch(on=False))
    await off.run()
    assert off.sink.events == []
    assert off.sink.steers, "steering still runs"

    on = _cars_harness(CarAlertSwitch(on=True))
    await on.run()
    assert on.sink.events, "the same cars alert with the switch on"


def test_off_saves_nothing_and_drops_a_recording_in_progress() -> None:
    switch = CarAlertSwitch(on=True)
    hub = DebugHub(car_alerts=switch)
    offered: list[object] = []
    discarded: list[bool] = []
    hub.clips.offer = lambda t, rows: offered.append(rows)  # type: ignore[method-assign]
    hub.clips.discard = lambda: discarded.append(True)  # type: ignore[method-assign]
    hub.record_hazards(_response("person"), (), ())
    assert len(offered) == 1
    assert hub.set_car_alerts(False)
    assert discarded == [True] and not switch.on
    hub.record_hazards(_response("person"), (), ())
    assert len(offered) == 1, "nothing offered to the recorder while off"
    assert hub.state()["car_alerts"] == {"available": True, "on": False}


def _post(base: str, body: bytes, content_type: str = "application/json") -> int:
    request = urllib.request.Request(f"{base}/car-alerts", data=body, method="POST",
                                     headers={"Content-Type": content_type})
    try:
        return urllib.request.urlopen(request).status
    except urllib.error.HTTPError as exc:
        return exc.code


def test_the_page_flips_the_switch_and_nothing_else_can() -> None:
    switch = CarAlertSwitch(on=True)
    server = serve_debug(DebugHub(car_alerts=switch), port=0)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        assert _post(base, b'{"on": false}') == 200 and not switch.on
        assert _post(base, b'{"on": true}') == 200 and switch.on
        # A form post (what another web page could send without a preflight).
        assert _post(base, b"on=false", "application/x-www-form-urlencoded") == 403 and switch.on
        assert _post(base, b'{"on": "no"}') == 400
        assert b"car-switch" in urllib.request.urlopen(f"{base}/").read()
    finally:
        server.shutdown()
    plain = serve_debug(DebugHub(), port=0)
    try:
        base = f"http://127.0.0.1:{plain.server_address[1]}"
        assert _post(base, b'{"on": false}') == 409
        assert json.loads(urllib.request.urlopen(f"{base}/state.json").read())["car_alerts"]["available"] is False
    finally:
        plain.shutdown()
