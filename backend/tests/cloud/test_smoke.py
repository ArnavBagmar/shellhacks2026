"""The spike-7 tool's decision logic (A14).

Only the pure `evaluate` is unit-tested: standing a live server up inside
pytest is out of scope, and the part that can be wrong in a way that matters
on stage is the pass/fail rule, not the HTTP plumbing.
"""

from __future__ import annotations

from cane.cloud.smoke import (
    EXIT_ENGINE_NOT_READY,
    EXIT_HTTP_ERROR,
    EXIT_NO_FRAMES,
    EXIT_OK,
    EXIT_TOO_SLOW,
    SMOKE_MAX_INFERENCE_MS,
    FrameResult,
    evaluate,
)

READY = {"detector": "tensorrt", "engine_ready": True, "gpu": "NVIDIA L4"}
BUDGET = SMOKE_MAX_INFERENCE_MS


def frame(inference_ms: float, status_code: int = 200, detections: int = 2) -> FrameResult:
    return FrameResult(status_code=status_code, inference_ms=inference_ms, detections=detections)


def test_a_healthy_fast_run_passes() -> None:
    code, message = evaluate(READY, [frame(90.0), frame(12.0), frame(13.0)], BUDGET)
    assert code == EXIT_OK
    assert "25.0" in message or "budget" in message


def test_evaluate_fails_when_engine_not_ready() -> None:
    code, message = evaluate({**READY, "engine_ready": False}, [frame(10.0)] * 3, BUDGET)
    assert code == EXIT_ENGINE_NOT_READY
    assert "engine" in message.lower()


def test_evaluate_fails_on_slow_median() -> None:
    code, message = evaluate(READY, [frame(10.0), frame(80.0), frame(90.0)], BUDGET)
    assert code == EXIT_TOO_SLOW
    assert "median" in message.lower()


def test_evaluate_ignores_the_first_frame() -> None:
    """Frame 1 pays the one-time context reshape at a new input shape."""
    code, _ = evaluate(READY, [frame(5000.0), frame(11.0), frame(12.0)], BUDGET)
    assert code == EXIT_OK


def test_a_single_frame_run_is_judged_on_that_frame() -> None:
    assert evaluate(READY, [frame(5.0)], BUDGET)[0] == EXIT_OK
    assert evaluate(READY, [frame(5000.0)], BUDGET)[0] == EXIT_TOO_SLOW


def test_evaluate_fails_on_any_non_200() -> None:
    code, message = evaluate(READY, [frame(10.0), frame(10.0, status_code=503)], BUDGET)
    assert code == EXIT_HTTP_ERROR
    assert "503" in message


def test_evaluate_fails_with_no_frames() -> None:
    assert evaluate(READY, [], BUDGET)[0] == EXIT_NO_FRAMES


def test_an_overridden_budget_is_honoured() -> None:
    """The 25 ms figure is the L4 number; a local MPS run needs a bigger one."""
    slow = [frame(10.0), frame(120.0), frame(130.0)]
    assert evaluate(READY, slow, BUDGET)[0] == EXIT_TOO_SLOW
    assert evaluate(READY, slow, 200.0)[0] == EXIT_OK


def test_exit_codes_are_distinct_and_only_ok_is_zero() -> None:
    codes = [EXIT_OK, EXIT_ENGINE_NOT_READY, EXIT_HTTP_ERROR, EXIT_TOO_SLOW, EXIT_NO_FRAMES]
    assert len(set(codes)) == len(codes)
    assert EXIT_OK == 0 and all(c != 0 for c in codes[1:])
