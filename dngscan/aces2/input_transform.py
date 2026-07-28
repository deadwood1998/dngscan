# SPDX-License-Identifier: GPL-3.0-or-later
"""Rec.2020 D65 to ACES2065-1 (AP0) input handoff."""
from __future__ import annotations

import numpy as np

from .constants import (
    AP0,
    AP0_RGB_TO_XYZ,
    REC2020_D65,
    REC2020_RGB_TO_XYZ,
    calculate_rgb_to_rgb_matrix,
)

REC2020_TO_AP0_CAT = calculate_rgb_to_rgb_matrix(REC2020_D65, AP0)
REC2020_WHITE_Y = float((np.array([1.0, 1.0, 1.0]) @ REC2020_RGB_TO_XYZ.T)[1])
AP0_WHITE_Y = float((np.array([1.0, 1.0, 1.0]) @ AP0_RGB_TO_XYZ.T)[1])
NEUTRAL_EPS = 1e-9


def rec2020_d65_to_ap0(scene_rec2020_d65: np.ndarray) -> np.ndarray:
    """Convert scene-linear Rec.2020 D65 to ACES2065-1.

    Uses Bradford CAT per Lib.Academy.ColorSpaces.ctl for chromatic colors.
    Neutral triplets are mapped to AP0 gray at the same scene luminance (Y).
    """
    rgb = np.asarray(scene_rec2020_d65, dtype=np.float64)
    orig = rgb.shape
    flat = rgb.reshape(-1, 3)
    spread = np.max(flat, axis=1) - np.min(flat, axis=1)
    neutral = spread <= NEUTRAL_EPS * np.maximum(np.max(np.abs(flat), axis=1), 1.0)
    chromatic = flat @ REC2020_TO_AP0_CAT.T
    level = flat[:, 0] * (REC2020_WHITE_Y / AP0_WHITE_Y)
    neutral_ap0 = np.stack([level, level, level], axis=1)
    ap0 = np.where(neutral[:, None], neutral_ap0, chromatic)
    return ap0.reshape(orig)


def sanitize_scene_rgb(rgb: np.ndarray) -> np.ndarray:
    """Replace non-finite values with zero before entering the reference kernel."""
    out = np.asarray(rgb, dtype=np.float64)
    return np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)
