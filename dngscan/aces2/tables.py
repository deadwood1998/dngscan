# SPDX-License-Identifier: GPL-3.0-or-later
"""Gamut table lookup helpers ported from Lib.Academy.OutputTransform.ctl."""
from __future__ import annotations

import numpy as np

from .constants import (
    BASE_INDEX,
    GAMUT_TABLE_SIZE,
    TOTAL_TABLE_SIZE,
    base_hue_for_position,
    hue_position_in_uniform_table,
    wrap_to_360,
)


def _lerp(a: np.ndarray, b: np.ndarray, t: np.ndarray) -> np.ndarray:
    return a + t * (b - a)


def cusp_from_table(h: np.ndarray, table: np.ndarray) -> np.ndarray:
    """cuspFromTable — binary search on wrapped hue table."""
    h = np.asarray(h, dtype=np.float64)
    flat_h = h.reshape(-1)
    out = np.zeros((flat_h.size, 2), dtype=np.float64)
    for idx, hue in enumerate(flat_h):
        low_i = 0
        high_i = BASE_INDEX + GAMUT_TABLE_SIZE
        i = int(hue_position_in_uniform_table(np.array([hue]), GAMUT_TABLE_SIZE)[0]) + BASE_INDEX
        while low_i + 1 < high_i:
            if hue > table[i, 2]:
                low_i = i
            else:
                high_i = i
            i = (low_i + high_i) // 2
        lo = table[high_i - 1]
        hi = table[high_i]
        denom = hi[2] - lo[2]
        t = 0.0 if denom == 0.0 else (hue - lo[2]) / denom
        out[idx, 0] = lo[0] + t * (hi[0] - lo[0])
        out[idx, 1] = lo[1] + t * (hi[1] - lo[1])
    return out.reshape(*h.shape, 2)


def cusp_from_table_vectorized(h: np.ndarray, table: np.ndarray) -> np.ndarray:
    """Vectorized cusp lookup via periodic interpolation on sorted hue column."""
    h = np.asarray(h, dtype=np.float64)
    return np.stack(
        [
            np.interp(wrap_to_360(h), table[:, 2], table[:, 0], period=360.0),
            np.interp(wrap_to_360(h), table[:, 2], table[:, 1], period=360.0),
        ],
        axis=-1,
    )


def reach_m_from_table(h: np.ndarray, reach_table: np.ndarray) -> np.ndarray:
    h = np.asarray(h, dtype=np.float64)
    i_lo = hue_position_in_uniform_table(h, reach_table.size)
    i_hi = (i_lo + 1) % reach_table.size
    t = (h - i_lo) / (i_hi - i_lo)
    return _lerp(reach_table[i_lo], reach_table[i_hi], t)


def hue_dependent_upper_hull_gamma(h: np.ndarray, gamma_table: np.ndarray) -> np.ndarray:
    h = np.asarray(h, dtype=np.float64)
    i_lo = hue_position_in_uniform_table(h, GAMUT_TABLE_SIZE) + BASE_INDEX
    i_hi = (i_lo + 1) % gamma_table.size
    base_hue = base_hue_for_position(i_lo - BASE_INDEX, GAMUT_TABLE_SIZE)
    t = wrap_to_360(h) - base_hue
    return _lerp(gamma_table[i_lo], gamma_table[i_hi], t)
