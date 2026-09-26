"""docs/shepherd-steering-plan.md Sec 3's constant table -- the cloud's rows.

One table, no magic numbers in code. Everything here is either quoted from the
plan or, where the plan leaves a value open, defined once with its derivation
written down (`BAND_REFERENCE_M`, `DEFAULT_MAX_GROUND_RESIDUAL`, `RESIDUAL_CAP`).

This is also the only module in the package allowed to name a filesystem
location, so that a path can be changed in one place and nowhere else.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

#: Crop indices 5, 0, 1 -- yaw centres -60, 0, +60, in that order, so the
#: profiles read left to right. Plan Sec 1.2: depth on six crops costs twice
#: what steering needs, and you steer where you walk.
DEPTH_CROPS: Final[tuple[int, ...]] = (5, 0, 1)
#: Measured at build, per wearer: the X4 sits on the shoulder at this height.
MOUNT_HEIGHT_M: Final = 1.40
#: Shepherd's "ignore ceiling and floor", as metres above the ground.
OBSTACLE_BAND_M: Final[tuple[float, float]] = (0.20, 2.20)
D_STOP_M: Final = 0.60
D_CLEAR_M: Final = 3.00
#: The band filter is a rectangle in (row, distance) space, so it needs one
#: reference distance -- and the plan names none. `D_CLEAR_M` is the farthest
#: distance the steering ramp reacts to at all, so rows that could only hold
#: floor or ceiling at 3 m carry no steering evidence and are dropped. Nearer
#: obstacles span the band and more, so none of them is lost by this choice.
BAND_REFERENCE_M: Final = D_CLEAR_M
#: Fewer ground rows than this and the affine fit has no leverage.
MIN_GROUND_ROWS: Final = 8
#: The distance the ground fit's residual is expressed *at* -- see
#: `DEFAULT_MAX_GROUND_RESIDUAL`. `D_CLEAR_M` because that is the far end of the
#: steering ramp: a scale error out there is the largest one that still changes
#: a cue, and anything beyond it reads "clear" whatever the number.
RESIDUAL_REFERENCE_M: Final = D_CLEAR_M
#: `ground_fit_residual` is a *ratio* -- the fractional distance error the fit's
#: RMS misfit implies at `RESIDUAL_REFERENCE_M` -- so it is comparable across
#: frames even though relative depth is not. Capped so the wire always carries
#: a finite, sane number.
RESIDUAL_CAP: Final = 10.0
NO_FIT_RESIDUAL: Final = RESIDUAL_CAP
#: Plan Sec 2.3 says "reject a high-residual fit" and names no threshold.
#:
#: Step 27 re-derived it, because the value 0.25 was calibrated for a fit that
#: could not work. Three lines of evidence, all measured on the real X4 equirect
#: (`deploy/affine_ground_fit_check.py`):
#:
#: 1. **The bar the plan set itself.** Step 22 refused its own one-parameter
#:    scale because it "would have placed a row whose true distance is 3.49 m at
#:    2.92 m, a 16 % error". 0.15 is just inside that: a fit whose misfit implies
#:    a worse-than-16 %-class distance error at the far end of the ramp is
#:    exactly the fit step 22 was right to refuse.
#: 2. **Margin.** The three real forward crops score 0.056 / 0.016 / 0.029 under
#:    the affine fit, so 0.15 leaves 2.7x on the worst of them and all three
#:    anchor -- plan Sec 2.5's acceptance.
#: 3. **Why not 0.25.** In step 22's units the residual was `rms / (a*std(1/d))`;
#:    `std(1/d)` over the floor rows is 0.0365 per metre, so 0.25 there is 0.027
#:    here -- an implicit demand for 2.7 % distance accuracy at 3 m, which no
#:    real crop meets even with the right model. Re-using it would have anchored
#:    one crop of three.
#:
#: What the threshold does *not* do is reject a wall: two free parameters fit a
#: flat strip perfectly (`a = 0`, `b = const`, rms 0). That is caught
#: structurally instead, by requiring a positive gradient -- see `fit_ground`.
DEFAULT_MAX_GROUND_RESIDUAL: Final = 0.15
#: 280 px is what deploy/depth_crop_timing.py measured at 31.8 ms p50 per
#: 640-px crop on Apple MPS. It must stay on the ViT patch grid: the official
#: transform rounds to a multiple of 14, so any other value silently changes
#: the shape an exported engine was built for.
DEFAULT_INPUT_SIZE: Final = 280
INPUT_SIZE_MULTIPLE_OF: Final = 14

BGR_CHANNELS: Final = 3
MS_PER_S: Final = 1000.0

BACKEND_ROOT: Final = Path(__file__).resolve().parents[4]
#: The same official-revision checkout and checkpoint `cane.benchmarks.depth`
#: benchmarks against; pinned together by a test, because two copies of a
#: weights path are how a service ends up serving a different model than the
#: one that was measured.
DEFAULT_DEPTH_REPO: Final = BACKEND_ROOT / "third_party/Depth-Anything-V2"
DEFAULT_DEPTH_WEIGHTS: Final = BACKEND_ROOT / "checkpoints/depth_anything_v2_vits.pth"
#: On the L4 only, and on the persistent disk: an engine is GPU-specific and
#: costs minutes to rebuild, exactly like the detector's (step-07-design A11).
DEFAULT_DEPTH_ENGINE_PATH: Final = Path(
    f"/models/depth_anything_v2_vits_{DEFAULT_INPUT_SIZE}_fp16.engine"
)
