"""One frame, one deadline: a frame older than this is never sent.

Design Sec 3.3's fail-safe philosophy -- drop rather than deliver late -- is
written there for the uplink ("one request in flight, ever"), but the same
argument applies to everything upstream of the wire. A warning about where a
cyclist was a tenth of a second ago is worse than no warning, because the
phone will act on it as if it were current.

So the whole tick is bounded by the frame's *age*, not by any per-stage
budget: pickup, dewarp and encode each re-check it, and the sender checks it
once more immediately before posting. One constant, one meaning, and the
check itself is a single integer compare.
"""

from __future__ import annotations

from cane.transport.wall_clock import now_ms

#: Milliseconds from the frame's arrival on the laptop. Above this the frame
#: is dropped wherever it happens to be, and the drop is counted.
FRAME_DEADLINE_MS = 100


def frame_age_ms(t_arrival_laptop_ms: int) -> int:
    """How old the frame is right now, in milliseconds."""
    return now_ms() - t_arrival_laptop_ms


def past_deadline(t_arrival_laptop_ms: int) -> bool:
    """True when this frame is too old to be worth sending."""
    return frame_age_ms(t_arrival_laptop_ms) > FRAME_DEADLINE_MS
