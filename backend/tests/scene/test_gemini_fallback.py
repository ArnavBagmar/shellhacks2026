"""GeminiVLM's hedged attempts (primary, primary again, fallback), with the
network call replaced. Skipped without the optional ``scene`` extra.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("google.genai")

from cane.scene.vlm import GeminiVLM, LabeledImage, VlmReply, VlmUnavailableError  # noqa: E402

IMAGES = [LabeledImage("Image 0: front", b"\xff\xd8")]
HEDGE_S = 0.05


def _vlm(
    monkeypatch: pytest.MonkeyPatch, outcomes: list[object], parallel: int = 1
) -> tuple[GeminiVLM, list[str]]:
    """``outcomes[i]`` is what attempt ``i`` does: a status string, "slow",
    or an exception to raise.
    """
    vlm = GeminiVLM("test-key", model="primary", fallback_model="backup", hedge_s=HEDGE_S, parallel=parallel)
    calls: list[str] = []

    async def fake_call(model: str, contents: list[object], *, thinking: object | None) -> VlmReply:
        outcome = outcomes[len(calls)]
        calls.append(model)
        if outcome == "slow":
            await asyncio.sleep(5)
        if isinstance(outcome, Exception):
            raise outcome
        return VlmReply(status=str(outcome))

    monkeypatch.setattr(vlm, "_call", fake_call)
    return vlm, calls


async def test_with_one_copy_a_fast_reply_sends_one_request(monkeypatch: pytest.MonkeyPatch) -> None:
    vlm, calls = _vlm(monkeypatch, ["found"])
    assert (await vlm.ask("p", IMAGES)).status == "found"
    assert calls == ["primary"]


async def test_two_copies_go_out_at_once_and_the_first_reply_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    vlm, calls = _vlm(monkeypatch, ["slow", "found"], parallel=2)
    started = asyncio.get_running_loop().time()
    assert (await vlm.ask("p", IMAGES)).status == "found"
    assert asyncio.get_running_loop().time() - started < HEDGE_S
    assert calls == ["primary", "primary"]


async def test_the_backup_model_joins_after_the_hedge_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    vlm, calls = _vlm(monkeypatch, ["slow", "slow", "not_found"], parallel=2)
    started = asyncio.get_running_loop().time()
    assert (await vlm.ask("p", IMAGES)).status == "not_found"
    assert asyncio.get_running_loop().time() - started >= HEDGE_S
    assert calls == ["primary", "primary", "backup"]


async def test_a_failed_attempt_starts_the_next_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    vlm, calls = _vlm(monkeypatch, [VlmUnavailableError("400"), "found"])
    started = asyncio.get_running_loop().time()
    assert (await vlm.ask("p", IMAGES)).status == "found"
    assert asyncio.get_running_loop().time() - started < HEDGE_S
    assert calls == ["primary", "backup"]


async def test_every_attempt_failing_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    vlm, _ = _vlm(monkeypatch, [VlmUnavailableError("a"), VlmUnavailableError("b"), VlmUnavailableError("c")], parallel=2)
    with pytest.raises(VlmUnavailableError) as raised:
        await vlm.ask("p", IMAGES)
    # The two parallel copies can fail in either order.
    assert sorted(str(raised.value).split("; ")) == ["a", "b", "c"]


async def test_the_callers_timeout_cancels_every_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    vlm, calls = _vlm(monkeypatch, ["slow", "slow", "slow"], parallel=2)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(vlm.ask("p", IMAGES), timeout=0.3)
    assert len(calls) == 3


def test_fallback_none_disables_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("CANE_GEMINI_FALLBACK_MODEL", "none")
    assert "fallback" not in GeminiVLM.from_env().name
