# SPDX-License-Identifier: GPL-3.0-or-later
"""Hellwig 2022 JMh conversions ported from Lib.Academy.OutputTransform.ctl."""
from __future__ import annotations

import numpy as np

from .constants import (
    AP0_RGB_TO_XYZ,
    AP1_XYZ_TO_RGB,
    BA,
    L_A,
    MATRIX_16,
    MATRIX_16_INV,
    PANLRCM,
    RA,
    REFERENCE_LUMINANCE,
    VIEWING_CONDITIONS_DIM,
    Y_b,
    wrap_to_360,
)


def _as_array(x: np.ndarray | float, *, dtype=np.float64) -> np.ndarray:
    return np.asarray(x, dtype=dtype)


def _viewing_params(surround_c: float = VIEWING_CONDITIONS_DIM[1]) -> tuple[float, float, float, float]:
    k = 1.0 / (5.0 * L_A + 1.0)
    k4 = k * k * k * k
    f_l = 0.2 * k4 * (5.0 * L_A) + 0.1 * ((1.0 - k4) ** 2) * ((5.0 * L_A) ** (1.0 / 3.0))
    n = Y_b / 100.0
    z = 1.48 + np.sqrt(n)
    f_l_w = f_l ** 0.42
    a_w = (400.0 * f_l_w) / (27.13 + f_l_w)
    return f_l, z, a_w, surround_c


def post_adaptation_nlr_forward(rgb: np.ndarray, f_l: float | np.ndarray) -> np.ndarray:
    f_l_rgb = np.power(np.abs(rgb) * f_l / 100.0, 0.42)
    return (400.0 * np.sign(rgb) * f_l_rgb) / (27.13 + f_l_rgb)


def post_adaptation_nlr_inverse(rgb: np.ndarray, f_l: float | np.ndarray) -> np.ndarray:
    return (
        np.sign(rgb)
        * 100.0
        / f_l
        * np.power((27.13 * np.abs(rgb)) / (400.0 - np.abs(rgb)), 1.0 / 0.42)
    )


def hellwig_j_to_y(
    j: np.ndarray,
    surround: float = VIEWING_CONDITIONS_DIM[1],
) -> np.ndarray:
    f_l, z, a_w, _ = _viewing_params(surround)
    a = a_w * np.power(np.abs(j) / 100.0, 1.0 / (surround * z))
    return np.sign(j) * 100.0 / f_l * np.power((27.13 * a) / (400.0 - a), 1.0 / 0.42)


def y_to_hellwig_j(
    y: np.ndarray,
    surround: float = VIEWING_CONDITIONS_DIM[1],
) -> np.ndarray:
    f_l, z, a_w, _ = _viewing_params(surround)
    f_l_y = np.power(f_l * np.abs(y) / 100.0, 0.42)
    return np.sign(y) * 100.0 * np.power(((400.0 * f_l_y) / (27.13 + f_l_y)) / a_w, surround * z)


def xyz_to_jmh(xyz: np.ndarray, xyz_w: np.ndarray) -> np.ndarray:
    """XYZ_to_Hellwig2022_JMh (Dim surround, D=1)."""
    xyz = _as_array(xyz)
    xyz_w = _as_array(xyz_w)
    orig_shape = xyz.shape
    flat = xyz.reshape(-1, 3)
    flat_w = xyz_w.reshape(-1, 3) if xyz_w.ndim > 1 else np.broadcast_to(xyz_w, flat.shape)
    y_w = flat_w[:, 1]
    f_l, z, _, surround = _viewing_params()
    n_c = VIEWING_CONDITIONS_DIM[2]

    rgb_w = flat_w @ MATRIX_16
    d = 1.0
    d_rgb = d * y_w[:, None] / rgb_w + (1.0 - d)
    rgb_wc = d_rgb * rgb_w
    rgb_aw = post_adaptation_nlr_forward(rgb_wc, f_l)
    a_w = RA * rgb_aw[:, 0] + rgb_aw[:, 1] + BA * rgb_aw[:, 2]

    rgb = flat @ MATRIX_16
    rgb_c = d_rgb * rgb
    rgb_a = post_adaptation_nlr_forward(rgb_c, f_l)
    a = RA * rgb_a[:, 0] + rgb_a[:, 1] + BA * rgb_a[:, 2]
    ab_a = rgb_a[:, 0] - 12.0 * rgb_a[:, 1] / 11.0 + rgb_a[:, 2] / 11.0
    ab_b = (rgb_a[:, 0] + rgb_a[:, 1] - 2.0 * rgb_a[:, 2]) / 9.0
    h = wrap_to_360(np.degrees(np.arctan2(ab_b, ab_a)))
    j = np.sign(a) * 100.0 * np.power(np.abs(a) / a_w, surround * z)
    m = 43.0 * n_c * np.hypot(ab_a, ab_b)
    m = np.where((j == 0.0) | ~np.isfinite(j), 0.0, m)
    out = np.stack([j, m, h], axis=-1)
    return out.reshape(*orig_shape[:-1], 3)


def jmh_to_xyz(jmh: np.ndarray, xyz_w: np.ndarray) -> np.ndarray:
    """Hellwig2022_JMh_to_XYZ (Dim surround, D=1)."""
    jmh = _as_array(jmh)
    xyz_w = _as_array(xyz_w)
    orig_shape = jmh.shape
    flat = jmh.reshape(-1, 3)
    flat_w = xyz_w.reshape(-1, 3) if xyz_w.ndim > 1 else np.broadcast_to(xyz_w, (flat.shape[0], 3))
    j, m, h = flat[:, 0], flat[:, 1], flat[:, 2]
    y_w = flat_w[:, 1]
    f_l, z, _, surround = _viewing_params()
    n_c = VIEWING_CONDITIONS_DIM[2]

    rgb_w = flat_w @ MATRIX_16
    d = 1.0
    d_rgb = d * y_w[:, None] / rgb_w + (1.0 - d)
    rgb_wc = d_rgb * rgb_w
    rgb_aw = post_adaptation_nlr_forward(rgb_wc, f_l)
    a_w = RA * rgb_aw[:, 0] + rgb_aw[:, 1] + BA * rgb_aw[:, 2]

    hr = np.radians(h)
    a = np.sign(j) * a_w * np.power(np.abs(j) / 100.0, 1.0 / (surround * z))
    p_p_1 = 43.0 * n_c
    gamma = m / p_p_1
    ab_a = gamma * np.cos(hr)
    ab_b = gamma * np.sin(hr)
    vec = np.stack([a, ab_a, ab_b], axis=-1)
    rgb_a = (vec @ PANLRCM) / 1403.0
    rgb_c = post_adaptation_nlr_inverse(rgb_a, f_l)
    rgb = rgb_c / d_rgb
    xyz = rgb @ MATRIX_16_INV
    return xyz.reshape(*orig_shape[:-1], 3)


def clamp_xyz_to_ap1(xyz: np.ndarray, peak_luminance: float) -> np.ndarray:
    xyz = _as_array(xyz)
    orig = xyz.shape
    flat = xyz.reshape(-1, 3)
    ap1 = flat @ AP1_XYZ_TO_RGB
    r_hit_min = 128.0
    r_hit_max = 896.0
    r_hit = r_hit_min + (r_hit_max - r_hit_min) * (
        np.log(peak_luminance / 100.0) / np.log(10000.0 / 100.0)
    )
    upper = 8.0 * r_hit
    ap1_clamped = np.clip(ap1, 0.0, upper)
    out = ap1_clamped @ np.linalg.inv(AP1_XYZ_TO_RGB)
    return out.reshape(orig)


def aces_to_jmh(aces: np.ndarray, peak_luminance: float) -> np.ndarray:
    aces = _as_array(aces)
    orig = aces.shape
    flat = aces.reshape(-1, 3)
    xyz = flat @ AP0_RGB_TO_XYZ
    xyz = clamp_xyz_to_ap1(xyz, peak_luminance)
    rgb_w = np.array([REFERENCE_LUMINANCE, REFERENCE_LUMINANCE, REFERENCE_LUMINANCE], dtype=np.float64)
    xyz_w = rgb_w @ AP0_RGB_TO_XYZ
    xyz_lum = xyz * REFERENCE_LUMINANCE
    jmh = xyz_to_jmh(xyz_lum, xyz_w)
    return jmh.reshape(*orig[:-1], 3)


def rgb_to_jmh(rgb: np.ndarray, rgb_to_xyz: np.ndarray, peak_luminance: float) -> np.ndarray:
    rgb = _as_array(rgb)
    orig = rgb.shape
    flat = rgb.reshape(-1, 3)
    luminance_rgb = flat * peak_luminance
    xyz = luminance_rgb @ rgb_to_xyz
    rgb_w = np.array([REFERENCE_LUMINANCE, REFERENCE_LUMINANCE, REFERENCE_LUMINANCE], dtype=np.float64)
    xyz_w = rgb_w @ rgb_to_xyz
    jmh = xyz_to_jmh(xyz, xyz_w)
    return jmh.reshape(*orig[:-1], 3)


def jmh_to_rgb(
    jmh: np.ndarray,
    xyz_to_rgb: np.ndarray,
    peak_luminance: float,
    xyz_w: np.ndarray,
) -> np.ndarray:
    jmh = _as_array(jmh)
    orig = jmh.shape
    flat = jmh.reshape(-1, 3)
    flat_w = xyz_w.reshape(-1, 3) if np.ndim(xyz_w) > 1 else np.broadcast_to(xyz_w, (flat.shape[0], 3))
    luminance_xyz = jmh_to_xyz(flat, flat_w)
    luminance_rgb = luminance_xyz @ xyz_to_rgb
    rgb = luminance_rgb / peak_luminance
    return rgb.reshape(*orig[:-1], 3)
