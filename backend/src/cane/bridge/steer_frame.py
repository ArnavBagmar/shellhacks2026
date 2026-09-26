"""One inference response -> one ``steer`` frame, or honestly nothing (step 23 D).

The glue between the pure halves (``gap_ring``, ``gap_anchor``, ``steering``)
and the wire. It owns three refusals, and every one of them is the same rule:
**never send a frame that looks like a measurement when it is not** (step 8's
no-silent-fake discipline, plan Sec 4.2).

1. The local detector has no depth head, so a local response sends nothing --
   not a zero-steer frame. A zero steer is indistinguishable from a clear
   corridor, and the phone would hold a confident neutral cue while the
   bridge is blind. The gate is the *source*, not merely an empty profile
   list, so a local backend that somehow answered with profiles is refused
   too.
2. A response with no profiles, or a ring with no evidence in it, sends
   nothing.
3. A ring that was *assembled* outside the forward arc sends nothing, loudly
   -- see ``CAMERA_BEARING_ABS_MAX_DEG``. Note the frame the invariant is
   read in: the ring's **camera** bearings, before the mount offset. A mount
   offset is not a bug and never costs a frame (plan Sec 3.3 ruling 1).

No clock is read here. ``t_capture_ms`` is the stamp the cloud echoed, which
the bridge had already converted to phone time and corrected by D before
sending (step 6/7); re-deriving it would put the steering cue in a different
clock domain from the events beside it.
"""

from __future__ import annotations

import logging
from typing import Final, Sequence

from cane.bridge.config import DELTA_DEG_MAX
from cane.bridge.gap_anchor import (
    RELATIVE_DEPTH_SCALE_M,
    DepthAnchor,
    anchor_for_frame,
    inverse_metre_anchor,
    to_inverse_metres,
)
from cane.bridge.gap_ring import RING_ARC_DEG, GapRing, ring_from_profiles
from cane.bridge.steering import solve_steering
from cane.cloud.api_models import GapProfile, InferenceResponse
from cane.contracts.awareness_event import Source
from cane.transport.messages import STEER_TTL_MS, Steer

logger = logging.getLogger(__name__)

#: Plan Sec 3.3 ruling 1. The contract's ``gap_bearing_deg`` is deliberately
#: (-180, 180] so a future rear ring needs no amendment, which means the
#: contract cannot catch a step-23 bug that assembles a forward ring over
#: +140. This is that missing bound -- half the arc -- and it is read in the
#: **camera** frame, which is the only frame in which it is a statement about
#: the ring rather than about how the camera happens to be mounted.
CAMERA_BEARING_ABS_MAX_DEG: Final = RING_ARC_DEG / 2.0
#: The widest bearing this step may legitimately emit: the edge of the arc
#: rotated by the largest mount offset ``BridgeConfig`` accepts, so 120 rather
#: than 90 (plan Sec 3.3 ruling 1). Nothing is *checked* against it -- the
#: point of bounding ``delta_deg`` at the config boundary is that a bearing
#: past +-180, where ``normalize_bearing_deg`` would flip the gap's side, is
#: unreachable rather than guarded. It is here because it is the span the
#: phone is entitled to see, and the tests sweep it.
MAX_EMITTED_BEARING_ABS_DEG: Final = CAMERA_BEARING_ABS_MAX_DEG + DELTA_DEG_MAX
#: Only the cloud runs a depth head (plan Sec 2.1).
DEPTH_SOURCES: Final[frozenset[Source]] = frozenset({Source.X4_CLOUD})


def steer_from_response(
    response: InferenceResponse,
    yaws_deg: tuple[float, ...],
    delta_deg: float,
    source: Source = Source.X4_CLOUD,
    scale_m: float = RELATIVE_DEPTH_SCALE_M,
) -> Steer | None:
    """The tick's steering frame, or ``None`` when there is nothing honest to send."""
    if source not in DEPTH_SOURCES:
        return None
    if not response.gap_profiles:
        return None
    anchor = anchor_for_frame(response.gap_profiles, response.detections, scale_m)
    ring, anchor = _ring_for(response.gap_profiles, anchor, yaws_deg, delta_deg)
    if not _camera_arc_holds(ring, response.t_capture_ms):
        return None
    solution = solve_steering(ring, anchor)
    if solution is None:
        logger.debug(
            "no steer frame for t_capture_ms=%d: the ring holds no evidence",
            response.t_capture_ms,
        )
        return None
    return Steer(
        t_capture_ms=response.t_capture_ms,
        ttl_ms=STEER_TTL_MS,
        steer=solution.steer,
        proximity=solution.proximity,
        gap_bearing_deg=solution.gap_bearing_deg,
        blocked=solution.blocked,
        proximity_anchored=solution.proximity_anchored,
        source=Source.X4_CLOUD.value,
    )


def _ring_for(
    profiles: Sequence[GapProfile],
    anchor: DepthAnchor,
    yaws_deg: tuple[float, ...],
    delta_deg: float,
) -> tuple[GapRing, DepthAnchor]:
    """The ring to steer from, and the anchor that reads it.

    The fork is the whole of step 27's "turn the metric path on", and it is here
    rather than inside ``ring_from_profiles`` so that the scale-free path keeps
    assembling and smoothing exactly the bytes step 23 shipped.

    **Anchored:** each crop's columns are converted with *its own* fitted affine
    pair before assembly, so the 48 columns become one metric space and the
    smoothing kernel -- which deliberately runs across the crop seams -- averages
    like with like. Without this the seam columns blend two crops' unit systems;
    measured at a uniform 3 m, one came back as 2.03 m and, since ``d_min`` is
    taken over the whole arc, set ``proximity`` for the frame.

    **Rejected:** nothing is converted. ``relative_freedom`` is a ranking, and
    there is no metre to rank in.
    """
    if not anchor.accepted:
        return ring_from_profiles(profiles, yaws_deg, delta_deg), anchor
    metric = to_inverse_metres(profiles, anchor)
    return ring_from_profiles(metric, yaws_deg, delta_deg), inverse_metre_anchor(anchor)


def _camera_arc_holds(ring: GapRing, t_capture_ms: int) -> bool:
    """Ruling 1's invariant, asserted in code as well as in a test.

    Checked on the **ring**, in **camera** bearings, and neither half of that
    is incidental (plan Sec 3.3 ruling 1).

    *On the ring*, because the emitted bearing is a centroid and sits well
    inside the arc long after the arc's own edges have left it -- a ring whose
    outer columns were at 133 was measured emitting a perfectly legal-looking
    69.375.

    *In camera bearings*, because that is the only frame in which +-90 is a
    claim about the ring. Asserted on the rotated ring instead, as step 23
    first built it, the invariant is really a claim about the mount: the outer
    column centre is 88.125, so |``delta_deg``| above 1.875 dropped every
    frame -- a shoulder-worn X4 steering "as designed" and never once firing.
    The mount offset is now bounded at ``DELTA_DEG_MAX`` by ``BridgeConfig``
    instead, so a crooked mount refuses the *run*, once, at startup, rather
    than costing a frame per tick for as long as the demo lasts.

    What survives is the bug this was always for: a ring assembled over the
    wrong arc (a wrong yaw table, a rear crop binned forward). The camera
    bearings are the raw ``yaw + (cx - 0.5) * FOV`` sums, so nothing can fold
    one back into looking legal, and no mount offset can push a correct ring
    out. Such a frame is dropped rather than clamped: a clamp would state a
    direction the ring never measured.
    """
    worst = ring.camera_arc_max_deg
    if worst <= CAMERA_BEARING_ABS_MAX_DEG:
        return True
    logger.warning(
        "dropping the steer frame for t_capture_ms=%d: the ring was assembled over "
        "+-%.2f deg in the camera frame, outside the +-%.0f forward arc it is built "
        "from (plan Sec 3.3 ruling 1). This is a ring-assembly fault -- a yaw table "
        "or a crop index, not a mount offset, which is bounded at +-%.0f in the "
        "config and never costs a frame.",
        t_capture_ms,
        worst,
        CAMERA_BEARING_ABS_MAX_DEG,
        DELTA_DEG_MAX,
    )
    return False
