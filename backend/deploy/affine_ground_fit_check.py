"""Step 27 acceptance 1: does the affine ground fit anchor a real X4 frame?

Runs the **shipped** `cane.cloud.depth.profile.fit_ground` -- not a local
re-implementation -- on the three forward crops of
`docs/steps/assets/step-06-live-equirect.jpg`, dewarped by `cane.bridge.dewarp`,
through the same Depth Anything V2 Small loader step 22 benchmarked.

It lives here rather than in `tests/cloud` on purpose: that suite is deliberately
torch-free (step-07-design A4, `test_no_heavy_imports`), and the same split is
how `deploy/depth_crop_timing.py` already handles a torch-dependent measurement.
The numbers it prints are asserted without torch in
`tests/cloud/test_affine_ground_fit.py::MEASURED_REAL_CROPS`, so a drift here
shows up there too.

Run:
    cd backend && UV_PROJECT_ENVIRONMENT=.venv-vision \
        uv run --extra depth --extra vision python deploy/affine_ground_fit_check.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
import torch

sys.path.insert(0, "src")

from cane.benchmarks.depth_model import (  # noqa: E402
    DEFAULT_REPO,
    DEFAULT_WEIGHTS,
    load_depth,
    predict_depth,
)
from cane.bridge.dewarp import dewarp_equirect  # noqa: E402
from cane.cloud.depth.constants import (  # noqa: E402
    DEFAULT_INPUT_SIZE,
    DEFAULT_MAX_GROUND_RESIDUAL,
    DEPTH_CROPS,
    RESIDUAL_REFERENCE_M,
)
from cane.cloud.depth.geometry import ground_rows  # noqa: E402
from cane.cloud.depth.profile import build_profile, fit_ground  # noqa: E402

FRAME = Path(__file__).resolve().parents[2] / "docs/steps/assets/step-06-live-equirect.jpg"
CROP_PX = 640
DEVICE = "mps" if torch.backends.mps.is_available() else "cpu"
#: Plan Sec 2.5's measured affine rms, the number acceptance 1 asks us to
#: reproduce: `(crop, rms)`.
EXPECTED_RMS = {5: 0.068, 0: 0.005, 1: 0.013}
RMS_TOLERANCE = 0.002


def main() -> int:
    frame = cv2.imread(str(FRAME), cv2.IMREAD_COLOR)
    if frame is None:
        print(f"FAIL: cannot read {FRAME}")
        return 2
    cropset = dewarp_equirect(frame, CROP_PX, 0)
    model, provenance = load_depth(DEFAULT_REPO, DEFAULT_WEIGHTS, DEVICE)

    print(f"frame      {FRAME.name}  {frame.shape[1]}x{frame.shape[0]}")
    print(f"device     {DEVICE}   input_size {DEFAULT_INPUT_SIZE}")
    print(f"weights    sha256 {provenance['weights_sha256'][:16]}  "
          f"revision {provenance['official_revision'][:12]}")
    print(f"ground rows {ground_rows(CROP_PX)}  residual ceiling "
          f"{DEFAULT_MAX_GROUND_RESIDUAL} (= fractional distance error at "
          f"{RESIDUAL_REFERENCE_M:.2f} m)")
    print()
    header = f"{'crop':>5} {'a':>9} {'b':>9} {'rms':>9} {'residual':>10} {'anchored':>9}  verdict"
    print(header)
    print("-" * len(header))

    anchored = 0
    rms_ok = True
    for crop_index in DEPTH_CROPS:
        depth, _ = predict_depth(model, cropset.images[crop_index], DEFAULT_INPUT_SIZE, torch, DEVICE)
        fit = fit_ground(depth, CROP_PX, max_residual=DEFAULT_MAX_GROUND_RESIDUAL)
        expected = EXPECTED_RMS[crop_index]
        matches = abs(fit.rms - expected) <= RMS_TOLERANCE
        rms_ok = rms_ok and matches
        anchored += int(fit.anchored)
        print(
            f"{crop_index:>5} {fit.scale or float('nan'):>9.3f} "
            f"{fit.offset if fit.offset is not None else float('nan'):>9.3f} "
            f"{fit.rms:>9.4f} {fit.residual:>10.4f} {str(fit.anchored):>9}  "
            f"rms vs plan Sec 2.5 {expected:.3f}: {'OK' if matches else 'DRIFTED'}"
        )
        # The profile as the wire will carry it, so the producer invariant
        # (never `anchored` without the pair) is exercised on real data.
        built = build_profile(crop_index, depth, max_residual=DEFAULT_MAX_GROUND_RESIDUAL)
        assert built is not None, crop_index
        wire = built.to_gap_profile()
        assert wire.has_affine_scale == (fit.scale is not None)

    print()
    print(f"anchored {anchored} of {len(DEPTH_CROPS)} forward crops "
          f"(acceptance 1 needs at least 2)")
    ok = anchored >= 2 and rms_ok
    print("ACCEPTANCE 1:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
