"""Shared relative-depth palette: light green is farther, dark orange is nearer.

Normalized depth is 0 at this frame's far end and 1 at its near end (see
`depth_model.normalize_depth`). The ramp gets darker and warmer as it approaches
1 so "close" reads as heavy without any calibrated distance being implied.
"""
from __future__ import annotations

# (position, R, G, B) for readability; the built lookup table is BGR for OpenCV.
# Luminance falls monotonically from the pale far end to the burnt near end.
STOPS = (
    (0.00, 219, 247, 210),  # farthest: very light green
    (0.22, 150, 219, 141),  # light green
    (0.48, 86, 168, 96),    # mid green
    (0.66, 198, 140, 60),   # amber hand-off
    (0.85, 168, 86, 28),    # orange
    (1.00, 104, 40, 8),     # nearest: dark burnt orange
)
FAR_LABEL, NEAR_LABEL = "farther", "nearer"
_LUT = None


def depth_lut(np):
    """256x1x3 BGR colormap accepted by cv2.applyColorMap; built once."""
    global _LUT
    if _LUT is None:
        stops = np.array(STOPS, dtype=np.float64)
        position = np.linspace(0.0, 1.0, 256)
        channels = [np.interp(position, stops[:, 0], stops[:, index]) for index in (3, 2, 1)]
        _LUT = np.round(np.stack(channels, axis=-1)).astype(np.uint8).reshape(256, 1, 3)
    return _LUT


def depth_heatmap(cv2, np, relative):
    """Colorize a normalized HxW depth map without modifying the input."""
    quantized = np.round(np.clip(relative, 0.0, 1.0) * 255).astype(np.uint8)
    return cv2.applyColorMap(quantized, depth_lut(np))


def depth_color(np, value):
    """BGR tuple for one normalized depth value, matching the heatmap exactly."""
    row = depth_lut(np)[int(round(min(max(float(value), 0.0), 1.0) * 255)), 0]
    return int(row[0]), int(row[1]), int(row[2])


def readable_text(color):
    """Black on the light far end, white on the dark near end, for labels."""
    blue, green, red = color
    luminance = 0.0722 * blue + 0.7152 * green + 0.2126 * red
    return (20, 20, 20) if luminance > 140 else (255, 255, 255)


def depth_legend(cv2, np, width, height, background=(28, 28, 32)):
    """Horizontal far-to-near strip with end labels, sized to the chrome row."""
    strip = np.full((height, width, 3), background, dtype=np.uint8)
    (far_width, _), _ = cv2.getTextSize(FAR_LABEL, 0, 0.42, 1)
    (near_width, _), _ = cv2.getTextSize(NEAR_LABEL, 0, 0.42, 1)
    x0 = 12 + far_width + 8
    x1 = width - 12 - near_width - 8
    baseline = height // 2 + 5
    cv2.putText(strip, FAR_LABEL, (12, baseline), 0, 0.42, (188, 188, 192), 1)
    if x1 - x0 >= 24:
        gradient = np.repeat(np.linspace(0, 255, x1 - x0, dtype=np.uint8)[None, :], height - 16, axis=0)
        strip[8:height - 8, x0:x1] = cv2.applyColorMap(gradient, depth_lut(np))
        cv2.putText(strip, NEAR_LABEL, (x1 + 8, baseline), 0, 0.42, (188, 188, 192), 1)
    return strip
