"""The official Depth-Anything-V2 input transform, for the engine path only.

The torch backend never comes here: it calls the official `infer_image`, which
owns its own transform. An engine has no such wrapper, so this is the one place
the preprocessing is written twice -- and the reason it is safe to do so is
that **our crops are square**. The official chain is

    Resize(keep_aspect_ratio=True, ensure_multiple_of=14, lower_bound)
    NormalizeImage(ImageNet mean/std)
    PrepareForNet (HWC -> CHW float32)

and for a square input with `input_size` already on the patch grid, the resize
reduces exactly to `cv2.resize(..., (input_size, input_size), INTER_CUBIC)`.
Both preconditions are therefore enforced, loudly, rather than assumed.
"""

from __future__ import annotations

from typing import Final

import cv2
import numpy as np
from numpy.typing import NDArray

from cane.cloud.depth.constants import INPUT_SIZE_MULTIPLE_OF

IMAGENET_MEAN: Final[tuple[float, float, float]] = (0.485, 0.456, 0.406)
IMAGENET_STD: Final[tuple[float, float, float]] = (0.229, 0.224, 0.225)
_MEAN: Final = np.asarray(IMAGENET_MEAN, dtype=np.float32)
_STD: Final = np.asarray(IMAGENET_STD, dtype=np.float32)


def to_model_input(image: NDArray[np.uint8], input_size: int) -> NDArray[np.float32]:
    """BGR HWC uint8 crop -> contiguous `(1, 3, S, S)` float32 NCHW batch."""
    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError(f"expected a BGR HWC frame, got {image.shape}")
    if image.shape[0] != image.shape[1]:
        raise ValueError(f"the crops are square; a {image.shape} frame needs the keep-aspect resize")
    if input_size <= 0 or input_size % INPUT_SIZE_MULTIPLE_OF:
        raise ValueError(
            f"input_size must be a positive multiple of {INPUT_SIZE_MULTIPLE_OF}, got {input_size}"
        )
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    resized = cv2.resize(rgb, (input_size, input_size), interpolation=cv2.INTER_CUBIC)
    normalised = (resized - _MEAN) / _STD
    return np.ascontiguousarray(normalised.transpose(2, 0, 1)[None, ...], dtype=np.float32)


def to_frame_size(depth: NDArray[np.float32], height: int, width: int) -> NDArray[np.float32]:
    """Interpolate the model's map back to the crop's own size.

    Bilinear, and nothing else: no rescale, no clipping, no normalisation (plan
    Sec 1.1). `INTER_LINEAR` also cannot overshoot the data the way `INTER_CUBIC`
    can, which keeps a resized map inside the range the model actually produced.
    """
    if depth.ndim != 2:
        raise ValueError(f"expected an HxW depth map, got {depth.shape}")
    array = np.asarray(depth, dtype=np.float32)
    if array.shape == (height, width):
        return array
    return cv2.resize(array, (width, height), interpolation=cv2.INTER_LINEAR)
