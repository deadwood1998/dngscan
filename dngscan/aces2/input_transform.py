# SPDX-License-Identifier: GPL-3.0-or-later
"""Rec.2020 D65 to ACES2065-1 (AP0) input handoff."""
from __future__ import annotations

import numpy as np

from .constants import (
    AP0,
    REC2020_D65,
    calculate_rgb_to_rgb_matrix,
)

REC2020_TO_AP0_CAT = calculate_rgb_to_rgb_matrix(REC2020_D65, AP0)


def rec2020_d65_to_ap0(scene_rec2020_d65: np.ndarray) -> np.ndarray:
    """Convert scene-linear Rec.2020 D65 to ACES2065-1.

    Uses the row-vector matrix convention of ``Lib.Academy.ColorSpaces.ctl``.
    Bradford adaptation maps D65 neutral directly to AP0/D60 neutral, so no
    separate neutral-axis branch is required.
    """
    rgb = np.asarray(scene_rec2020_d65, dtype=np.float64)
    orig = rgb.shape
    flat = rgb.reshape(-1, 3)
    return (flat @ REC2020_TO_AP0_CAT).reshape(orig)


def sanitize_scene_rgb(rgb: np.ndarray) -> np.ndarray:
    """Replace non-finite values with zero before entering the reference kernel."""
    out = np.asarray(rgb, dtype=np.float64)
    return np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)
