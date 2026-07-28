# SPDX-License-Identifier: GPL-3.0-or-later
"""Peak white limiting from ACES output presets."""
from __future__ import annotations

import numpy as np

from .constants import REFERENCE_LUMINANCE


def clamp_to_peak_luminance(xyz: np.ndarray, peak_luminance: float) -> np.ndarray:
    """Clamp relative XYZ to [0, peakLuminance/referenceLuminance] per output preset."""
    upper = peak_luminance / REFERENCE_LUMINANCE
    return np.clip(np.asarray(xyz, dtype=np.float64), 0.0, upper)


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
    p3 = luminance_xyz @ xyz_to_rgb.T
    p3 = p3 / reference_white_nits
    if peak_luminance is not None:
        upper = peak_luminance / reference_white_nits
        p3 = np.clip(p3, 0.0, upper)
    return np.nan_to_num(p3, nan=0.0, posinf=0.0, neginf=0.0)
