"""Tall, tilted views for a head-worn X4: pixels land on the right
directions, bearings come back exact, and the phone photo goes first.
"""

from __future__ import annotations

import base64
import math

import cv2
import numpy as np
import pytest

from cane.scene.frames import StillProvider
from cane.scene.service import HEAD_WORN_LAYOUT, SceneQueryService, View
from cane.scene.tall_views import (
    HEAD_PITCH_DEG,
    HEAD_VFOV_DEG,
    bearing_offset,
    height_for,
    tall_views,
    x_for_offset,
)
from cane.scene.vlm import FakeVLM, LabeledImage, VlmReply
from cane.transport.messages import PhoneFrame, SceneQuery

W, H = 720, 360


def _encoded_equirect() -> np.ndarray:
    """Blue = longitude, green = latitude, so a pixel says where it came from."""
    lon = np.linspace(-180, 180, W, endpoint=False) + 180 / W
    lat = 90 - (np.arange(H) + 0.5) * 180 / H
    img = np.zeros((H, W, 3), np.uint8)
    img[:, :, 0] = ((lon + 180) / 360 * 255).astype(np.uint8)[None, :]
    img[:, :, 1] = ((lat + 90) / 180 * 255).astype(np.uint8)[:, None]
    return img


def _lon_lat(pixel: np.ndarray) -> tuple[float, float]:
    return float(pixel[0]) / 255 * 360 - 180, float(pixel[1]) / 255 * 180 - 90


def test_each_view_points_where_its_heading_says() -> None:
    views = tall_views(_encoded_equirect(), 120, vfov_deg=HEAD_VFOV_DEG, pitch_deg=HEAD_PITCH_DEG)
    assert views[0].shape == (height_for(120, 60, HEAD_VFOV_DEG), 120, 3)
    for view, heading in zip(views, (0, 60, 120, 180, -120, -60)):
        h = view.shape[0]
        lon, lat = _lon_lat(view[h // 2, 60])
        assert abs(((lon - heading) + 180) % 360 - 180) < 3, heading
        assert lat == pytest.approx(HEAD_PITCH_DEG, abs=3)  # the centre looks 20 degrees down


def test_the_view_reaches_the_floor_ahead_and_above_eye_level() -> None:
    (ahead, *_) = tall_views(_encoded_equirect(), 120, vfov_deg=HEAD_VFOV_DEG, pitch_deg=HEAD_PITCH_DEG)
    _, top = _lon_lat(ahead[0, 60])
    _, bottom = _lon_lat(ahead[-1, 60])
    assert top == pytest.approx(30, abs=3)
    assert bottom == pytest.approx(-70, abs=3)


def test_bearing_and_its_inverse_agree_on_the_horizon_row() -> None:
    kw = {"hfov_deg": 60.0, "pitch_deg": HEAD_PITCH_DEG}
    horizon_y = 0.5 - math.tan(math.radians(-HEAD_PITCH_DEG)) / (2 * math.tan(math.radians(HEAD_VFOV_DEG / 2)))
    for offset in (-28.0, -10.0, 0.0, 12.5, 29.0):
        x = x_for_offset(offset, **kw)
        assert x is not None
        assert bearing_offset(x, horizon_y, vfov_deg=HEAD_VFOV_DEG, **kw) == pytest.approx(offset, abs=1e-6)
    assert x_for_offset(120.0, **kw) is None


def test_low_in_a_tilted_view_is_wider_than_a_linear_guess() -> None:
    # A table near the bottom edge: the same column is further round than
    # at the horizon, which a linear x -> bearing mapping would miss.
    view = View(LabeledImage("", b""), centre_deg=0.0, fov_deg=60.0, pitch_deg=-20.0, vfov_deg=100.0)
    linear = (0.9 - 0.5) * 60.0
    assert view.bearing_at(0.9, 0.95) > linear + 10  # looking down, a column sweeps wider
    assert view.bearing_at(0.9, 0.2) < linear  # above the horizon it is narrower


def _photo() -> PhoneFrame:
    jpeg = cv2.imencode(".jpg", np.full((64, 48, 3), 128, np.uint8))[1].tobytes()
    return PhoneFrame(jpeg_b64=base64.b64encode(jpeg).decode(), hfov_deg=60.0, width=48, height=64)


def _query() -> SceneQuery:
    return SceneQuery(query_id="q-tall", kind="find", target="rubik's cube", utterance="how far is the rubik's cube",
                      t_phone_ms=0, phone_frame=_photo())


def _service(reply: VlmReply) -> tuple[SceneQueryService, FakeVLM]:
    vlm = FakeVLM(reply)
    service = SceneQueryService(vlm, StillProvider(_encoded_equirect()), settle_s=0.0, crop_px=64,
                                layout=HEAD_WORN_LAYOUT)
    return service, vlm


async def test_the_phone_photo_is_image_zero_and_the_views_follow_tall() -> None:
    service, vlm = _service(VlmReply(status="not_found"))
    await service.answer(_query())
    labels = [image.label for image in vlm.calls[0][1]]
    assert labels[0].startswith("Image 0: phone camera")
    assert [label.split(":")[0] for label in labels] == [f"Image {i}" for i in range(7)]
    assert all("tall" in label for label in labels[1:])


async def test_found_in_the_phone_photo_brings_its_box_as_image_zero() -> None:
    service, _ = _service(VlmReply(status="found", image_index=0, subject="rubik's cube",
                                   box_2d=[520, 520, 600, 600]))
    answer = await service.answer(_query())
    assert answer.phone_box is not None
    assert answer.bearing_deg == pytest.approx((0.56 - 0.5) * 60.0, abs=0.2)


async def test_found_in_a_tall_view_uses_its_heading_and_tilt() -> None:
    # Image 2 is the view centred 60 degrees right.
    service, _ = _service(VlmReply(status="found", image_index=2, subject="door", box_2d=[400, 450, 600, 550]))
    answer = await service.answer(_query())
    assert answer.phone_box is None
    assert answer.bearing_deg == pytest.approx(60.0, abs=0.5)


async def test_every_image_carries_its_number_and_the_prompt_says_so() -> None:
    service, vlm = _service(VlmReply(status="not_found"))
    await service.answer(_query())
    prompt, images = vlm.calls[0]
    assert "printed" in prompt and "image_index must be" in prompt
    for image in images:
        pixels = cv2.imdecode(np.frombuffer(image.jpeg, np.uint8), cv2.IMREAD_COLOR)
        bar = pixels[: int(30 * max(0.5, pixels.shape[1] / 420)), :]
        assert bar.max() > 150 and np.median(bar) < 40  # a dark bar with white text on it
