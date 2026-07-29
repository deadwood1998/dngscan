# SPDX-License-Identifier: GPL-3.0-or-later
"""Peak white limiting from ACES output presets."""
from __future__ import annotations

import numpy as np

from .constants import REFERENCE_LUMINANCE


def fit_rgb_to_peak_preserve_y(
    rgb: np.ndarray,
    rgb_to_xyz: np.ndarray,
    max_linear: float,
) -> np.ndarray:
    """Fit linear RGB to the peak cube by retreating toward the neutral axis.

    For representable luminance this keeps Y unchanged and reduces chroma. It is
    a numerical output safety limit after the perceptual gamut mapping, not an
    additional tone curve.
    """
    max_lin = float(max_linear)
    src = np.nan_to_num(
        np.asarray(rgb, dtype=np.float64),
        nan=0.0,
        posinf=max_lin,
        neginf=0.0,
    )
    matrix = np.asarray(rgb_to_xyz, dtype=np.float64)
    y = src @ matrix[:, 1]
    target_y = np.clip(y, 0.0, max_lin)
    scale = np.ones_like(target_y)
    nonzero = np.abs(y) > 1e-12
    scale[nonzero] = target_y[nonzero] / y[nonzero]
    work = src * scale[..., None]
    neutral = target_y[..., None]
    delta = work - neutral

    alpha = np.ones_like(target_y)
    for channel in range(3):
        d = delta[..., channel]
        upper = d > 0.0
        lower = d < 0.0
        bound = np.ones_like(target_y)
        bound[upper] = (max_lin - target_y[upper]) / d[upper]
        bound[lower] = -target_y[lower] / d[lower]
        alpha = np.minimum(alpha, bound)
    fitted = neutral + np.clip(alpha, 0.0, 1.0)[..., None] * delta
    return np.clip(fitted, 0.0, max_lin)


def xyz_relative_to_p3_linear(
    xyz: np.ndarray,
    xyz_to_rgb: np.ndarray,
    *,
    reference_white_nits: float = REFERENCE_LUMINANCE,
    peak_luminance: float | None = None,
) -> np.ndarray:
    """Convert relative XYZ (1.0 = 100 nits) to extended-linear Display P3."""
    xyz = np.asarray(xyz, dtype=np.float64)
    luminance_xyz = xyz * REFERENCE_LUMINANCE
    p3 = luminance_xyz @ xyz_to_rgb
    p3 = p3 / reference_white_nits
    if peak_luminance is not None:
        upper = peak_luminance / reference_white_nits
        p3 = fit_rgb_to_peak_preserve_y(p3, np.linalg.inv(xyz_to_rgb), upper)
    return np.nan_to_num(p3, nan=0.0, posinf=0.0, neginf=0.0)
