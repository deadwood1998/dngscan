# SPDX-License-Identifier: GPL-3.0-or-later
"""ACES 2-derived constants ported from Lib.Academy.*.ctl (Apache-2.0)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

# --- Output transform globals (Lib.Academy.OutputTransform.ctl) ---

REFERENCE_LUMINANCE = 100.0
L_A = 100.0
Y_b = 20.0

SMOOTH_CUSPS = 0.12
SMOOTH_M = 0.27
CUSP_MID_BLEND = 1.3

FOCUS_GAIN_BLEND = 0.3
FOCUS_ADJUST_GAIN = 0.55
FOCUS_DISTANCE = 1.35
FOCUS_DISTANCE_SCALING = 1.75

COMPRESSION_THRESHOLD = 0.75

GAMUT_TABLE_SIZE = 360
ADDITIONAL_TABLE_ENTRIES = 2
TOTAL_TABLE_SIZE = GAMUT_TABLE_SIZE + ADDITIONAL_TABLE_ENTRIES
BASE_INDEX = 1

GAMMA_MINIMUM = 0.0
GAMMA_MAXIMUM = 5.0
GAMMA_SEARCH_STEP = 0.4
GAMMA_ACCURACY = 1e-5

AC_RESP = 1.0
RA = 2.0 * AC_RESP
BA = 0.05 + (2.0 - RA)

LOWER_HULL_GAMMA = 1.14

CHROMA_COMPRESS = 2.4
CHROMA_COMPRESS_FACT = 3.3
CHROMA_EXPAND = 1.3
CHROMA_EXPAND_FACT = 0.69
CHROMA_EXPAND_THR = 0.5

VIEWING_CONDITIONS_DIM = (0.9, 0.59, 0.9)


@dataclass(frozen=True)
class Chromaticities:
    red: tuple[float, float]
    green: tuple[float, float]
    blue: tuple[float, float]
    white: tuple[float, float]


AP0 = Chromaticities(
    red=(0.73470, 0.26530),
    green=(0.00000, 1.00000),
    blue=(0.00010, -0.07700),
    white=(0.32168, 0.33767),
)

AP1 = Chromaticities(
    red=(0.713, 0.293),
    green=(0.165, 0.830),
    blue=(0.128, 0.044),
    white=(0.32168, 0.33767),
)

REC2020_D65 = Chromaticities(
    red=(0.708, 0.292),
    green=(0.170, 0.797),
    blue=(0.131, 0.046),
    white=(0.3127, 0.3290),
)

DISPLAY_P3_D65 = Chromaticities(
    red=(0.6800, 0.3200),
    green=(0.2650, 0.6900),
    blue=(0.1500, 0.0600),
    white=(0.3127, 0.3290),
)

CAM16_PRI = Chromaticities(
    red=(0.8336, 0.1735),
    green=(2.3854, -1.4659),
    blue=(0.087, -0.125),
    white=(0.333, 0.333),
)

CONE_RESP_MAT_BRADFORD = np.array(
    [
        [0.89510, -0.75020, 0.03890],
        [0.26640, 1.71350, -0.06850],
        [-0.16140, 0.03670, 1.02960],
    ],
    dtype=np.float64,
)


def rgb_to_xyz_f33(chroma: Chromaticities, y: float = 1.0) -> np.ndarray:
    """Lib.Academy.Utilities.ctl RGBtoXYZ_f33."""
    w = chroma.white
    r, g, b = chroma.red, chroma.green, chroma.blue
    x = w[0] * y / w[1]
    z = (1.0 - w[0] - w[1]) * y / w[1]
    d = r[0] * (b[1] - g[1]) + b[0] * (g[1] - r[1]) + g[0] * (r[1] - b[1])
    sr = (
        x * (b[1] - g[1])
        - g[0] * (y * (b[1] - 1.0) + b[1] * (x + z))
        + b[0] * (y * (g[1] - 1.0) + g[1] * (x + z))
    ) / d
    sg = (
        x * (r[1] - b[1])
        + r[0] * (y * (b[1] - 1.0) + b[1] * (x + z))
        - b[0] * (y * (r[1] - 1.0) + r[1] * (x + z))
    ) / d
    sb = (
        x * (g[1] - r[1])
        - r[0] * (y * (g[1] - 1.0) + g[1] * (x + z))
        + g[0] * (y * (r[1] - 1.0) + r[1] * (x + z))
    ) / d
    return np.array(
        [
            [sr * r[0], sr * r[1], sr * (1.0 - r[0] - r[1])],
            [sg * g[0], sg * g[1], sg * (1.0 - g[0] - g[1])],
            [sb * b[0], sb * b[1], sb * (1.0 - b[0] - b[1])],
        ],
        dtype=np.float64,
    )


def xyz_to_rgb_f33(chroma: Chromaticities, y: float = 1.0) -> np.ndarray:
    return np.linalg.inv(rgb_to_xyz_f33(chroma, y))


def xy_y_to_xyz(xy_y: Sequence[float]) -> np.ndarray:
    x, y, yy = xy_y
    divisor = max(y, 1e-10)
    return np.array([x * yy / divisor, yy, (1.0 - x - y) * yy / divisor], dtype=np.float64)


def calculate_cat_matrix(
    src_xy: Sequence[float],
    dest_xy: Sequence[float],
    cone_resp: np.ndarray = CONE_RESP_MAT_BRADFORD,
) -> np.ndarray:
    """Lib.Academy.ColorSpaces.ctl calculate_cat_matrix."""
    src_xyz = xy_y_to_xyz((src_xy[0], src_xy[1], 1.0))
    dest_xyz = xy_y_to_xyz((dest_xy[0], dest_xy[1], 1.0))
    src_cone = cone_resp @ src_xyz
    dest_cone = cone_resp @ dest_xyz
    vk = np.diag(dest_cone / src_cone)
    return cone_resp @ vk @ np.linalg.inv(cone_resp)


def calculate_rgb_to_rgb_matrix(
    source: Chromaticities,
    dest: Chromaticities,
    cone_resp: np.ndarray = CONE_RESP_MAT_BRADFORD,
) -> np.ndarray:
    """Lib.Academy.ColorSpaces.ctl calculate_rgb_to_rgb_matrix."""
    rgb_to_xyz = rgb_to_xyz_f33(source, 1.0)
    xyz_to_rgb = xyz_to_rgb_f33(dest, 1.0)
    cat = calculate_cat_matrix(source.white, dest.white, cone_resp)
    return rgb_to_xyz @ cat @ xyz_to_rgb


def generate_panlrcm(ra: float = RA, ba: float = BA) -> np.ndarray:
    data = np.array(
        [
            [ra, 1.0, 1.0 / 9.0],
            [1.0, -12.0 / 11.0, 1.0 / 9.0],
            [ba, 1.0 / 11.0, -2.0 / 9.0],
        ],
        dtype=np.float64,
    )
    pan = np.linalg.inv(data)
    for i in range(3):
        n = 460.0 / pan[0, i]
        pan[:, i] *= n
    return pan


def hsv_to_rgb(hsv: np.ndarray) -> np.ndarray:
    """Lib.Academy.Utilities.ctl HSV_to_RGB."""
    hsv = np.asarray(hsv, dtype=np.float64)
    single = hsv.ndim == 1
    if single:
        hsv = hsv.reshape(1, 3)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    c = v * s
    x = c * (1.0 - np.abs(np.mod(h * 6.0, 2.0) - 1.0))
    m = v - c
    h6 = h * 6.0
    rgb = np.zeros_like(hsv)
    rgb[..., 0] = np.select(
        [h6 < 1, h6 < 2, h6 < 3, h6 < 4, h6 < 5, h6 >= 5],
        [c, x, 0, 0, x, c],
        default=c,
    )
    rgb[..., 1] = np.select(
        [h6 < 1, h6 < 2, h6 < 3, h6 < 4, h6 < 5, h6 >= 5],
        [x, c, c, x, 0, 0],
        default=0,
    )
    rgb[..., 2] = np.select(
        [h6 < 1, h6 < 2, h6 < 3, h6 < 4, h6 < 5, h6 >= 5],
        [0, 0, x, c, c, x],
        default=0,
    )
    out = rgb + m[..., None]
    return out[0] if single else out


def smin(a: np.ndarray, b: np.ndarray, s: float) -> np.ndarray:
    h = np.maximum(s - np.abs(a - b), 0.0) / s
    return np.minimum(a, b) - h * h * h * s * (1.0 / 6.0)


def wrap_to_360(hue: np.ndarray) -> np.ndarray:
    y = np.mod(hue, 360.0)
    return np.where(y < 0.0, y + 360.0, y)


def hue_position_in_uniform_table(hue: np.ndarray, table_size: int) -> np.ndarray:
    wrapped = wrap_to_360(hue)
    return (wrapped / 360.0 * table_size).astype(np.int64)


def base_hue_for_position(i: int | np.ndarray, table_size: int) -> np.ndarray:
    return np.asarray(i, dtype=np.float64) * 360.0 / table_size


# Precomputed matrices used across the pipeline.
AP0_RGB_TO_XYZ = rgb_to_xyz_f33(AP0, 1.0)
AP0_XYZ_TO_RGB = xyz_to_rgb_f33(AP0, 1.0)
AP1_RGB_TO_XYZ = rgb_to_xyz_f33(AP1, 1.0)
AP1_XYZ_TO_RGB = xyz_to_rgb_f33(AP1, 1.0)
P3_RGB_TO_XYZ = rgb_to_xyz_f33(DISPLAY_P3_D65, 1.0)
P3_XYZ_TO_RGB = xyz_to_rgb_f33(DISPLAY_P3_D65, 1.0)
REC2020_RGB_TO_XYZ = rgb_to_xyz_f33(REC2020_D65, 1.0)
MATRIX_16 = xyz_to_rgb_f33(CAM16_PRI, 1.0)
MATRIX_16_INV = np.linalg.inv(MATRIX_16)
PANLRCM = generate_panlrcm()
REC2020_TO_AP0 = calculate_rgb_to_rgb_matrix(REC2020_D65, AP0)
