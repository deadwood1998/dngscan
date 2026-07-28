# SPDX-License-Identifier: GPL-3.0-or-later
"""Gamut compression ported from Lib.Academy.OutputTransform.ctl."""
from __future__ import annotations

import numpy as np

from . import tables
from .constants import (
    BASE_INDEX,
    COMPRESSION_THRESHOLD,
    CUSP_MID_BLEND,
    FOCUS_ADJUST_GAIN,
    FOCUS_GAIN_BLEND,
    GAMMA_ACCURACY,
    GAMMA_MAXIMUM,
    GAMMA_SEARCH_STEP,
    GAMMA_MINIMUM,
    GAMUT_TABLE_SIZE,
    LOWER_HULL_GAMMA,
    REFERENCE_LUMINANCE,
    SMOOTH_CUSPS,
    SMOOTH_M,
    TOTAL_TABLE_SIZE,
    base_hue_for_position,
    hsv_to_rgb,
    smin,
)
from .jmh import jmh_to_rgb, rgb_to_jmh


def _lerp(a, b, t):
    return a + t * (b - a)


def compression_function(v, thr, lim, *, invert=False):
    v = np.asarray(v, dtype=np.float64)
    s = (lim - thr) * (1.0 - thr) / (lim - 1.0)
    nd = (v - thr) / s
    if invert:
        return np.where(
            (v < thr) | (lim < 1.0001) | (v > thr + s),
            v,
            thr + s * (-nd / (nd - 1.0)),
        )
    return np.where(
        (v < thr) | (lim < 1.0001),
        v,
        thr + s * nd / (1.0 + nd),
    )


def solve_j_intersect(j, m, focus_j, max_j, slope_gain):
    a = m / (focus_j * slope_gain)
    b = np.where(
        j < focus_j,
        1.0 - m / slope_gain,
        -(1.0 + m / slope_gain + max_j * m / (focus_j * slope_gain)),
    )
    c = np.where(j < focus_j, -j, max_j * m / slope_gain + j)
    root = np.sqrt(np.maximum(b * b - 4.0 * a * c, 0.0))
    return np.where(j < focus_j, 2.0 * c / (-b - root), 2.0 * c / (-b + root))


def get_focus_gain(j, cusp_j, limit_jmax):
    thr = _lerp(cusp_j, limit_jmax, FOCUS_GAIN_BLEND)
    j_bound = np.maximum(0.0001, limit_jmax - np.minimum(limit_jmax, j))
    gain = (limit_jmax - thr) / j_bound
    with np.errstate(invalid="ignore"):
        approx = np.power(np.log10(np.maximum(gain, 1e-30)), 1.0 / FOCUS_ADJUST_GAIN) + 1.0
    return np.where(j > thr, approx, 1.0)


def find_gamut_boundary_intersection(
    jmh,
    jm_cusp,
    j_focus,
    j_max,
    slope_gain,
    gamma_top,
    gamma_bottom,
):
    j = jmh[..., 0]
    m = jmh[..., 1]
    s = max(0.000001, SMOOTH_CUSPS)
    cusp = jm_cusp.copy()
    cusp[..., 1] = jm_cusp[..., 1] * (1.0 + SMOOTH_M * s)
    j_src = solve_j_intersect(j, m, j_focus, j_max, slope_gain)
    j_cusp = solve_j_intersect(cusp[..., 0], cusp[..., 1], j_focus, j_max, slope_gain)
    slope = np.where(
        j_src < j_focus,
        j_src * (j_src - j_focus) / (j_focus * slope_gain),
        (j_max - j_src) * (j_src - j_focus) / (j_focus * slope_gain),
    )
    m_lower = (
        j_cusp
        * np.power(j_src / j_cusp, 1.0 / gamma_bottom)
        / (cusp[..., 0] / cusp[..., 1] - slope)
    )
    m_upper = (
        cusp[..., 1]
        * (j_max - j_cusp)
        * np.power((j_max - j_src) / (j_max - j_cusp), 1.0 / gamma_top)
        / (slope * cusp[..., 1] + j_max - cusp[..., 0])
    )
    m_boundary = cusp[..., 1] * smin(m_lower / cusp[..., 1], m_upper / cusp[..., 1], s)
    j_boundary = j_src + slope * m_boundary
    return np.stack([j_boundary, m_boundary, j_src], axis=-1)


def get_reach_boundary(j, m, h, params, jm_cusp, focus_j, reach_table):
    slope_gain = params.limit_jmax * params.focus_dist * get_focus_gain(j, jm_cusp[..., 0], params.limit_jmax)
    intersect_j = solve_j_intersect(j, m, focus_j, params.limit_jmax, slope_gain)
    slope = np.where(
        intersect_j < focus_j,
        intersect_j * (intersect_j - focus_j) / (focus_j * slope_gain),
        (params.limit_jmax - intersect_j)
        * (intersect_j - focus_j)
        / (focus_j * slope_gain),
    )
    reach_max_m = tables.reach_m_from_table(h, reach_table)
    boundary = (
        params.limit_jmax
        * np.power(intersect_j / params.limit_jmax, params.model_gamma)
        * reach_max_m
        / (params.limit_jmax - slope * reach_max_m)
    )
    return np.stack([j, boundary, h], axis=-1)


def compress_gamut(
    jmh,
    params,
    jx,
    gamut_cusp_table,
    gamut_top_gamma,
    reach_m_table,
    *,
    invert=False,
):
    jmh = np.asarray(jmh, dtype=np.float64)
    j = jmh[..., 0]
    m = jmh[..., 1]
    h = jmh[..., 2]
    limit_jmax = params.limit_jmax
    out = jmh.copy()
    inactive = (m < 0.0001) | (j > limit_jmax)
    out[..., 1] = np.where(inactive, 0.0, out[..., 1])

    active = ~inactive
    if not np.any(active):
        return out

    jm_cusp = tables.cusp_from_table_vectorized(h, gamut_cusp_table)
    focus_j = _lerp(
        jm_cusp[..., 0],
        params.mid_j,
        np.minimum(1.0, CUSP_MID_BLEND - (jm_cusp[..., 0] / limit_jmax)),
    )
    slope_gain = limit_jmax * params.focus_dist * get_focus_gain(jx, jm_cusp[..., 0], limit_jmax)
    gamma_top = tables.hue_dependent_upper_hull_gamma(h, gamut_top_gamma)
    boundary = find_gamut_boundary_intersection(
        jmh, jm_cusp, focus_j, limit_jmax, slope_gain, gamma_top, LOWER_HULL_GAMMA
    )
    jm_boundary = boundary[..., :2]
    project_j = boundary[..., 2]
    project_to = np.stack([project_j, np.zeros_like(project_j)], axis=-1)
    reach_boundary = get_reach_boundary(
        jm_boundary[..., 0],
        jm_boundary[..., 1],
        h,
        params,
        jm_cusp,
        focus_j,
        reach_m_table,
    )
    difference = np.maximum(1.0001, reach_boundary[..., 1] / jm_boundary[..., 1])
    threshold = np.maximum(COMPRESSION_THRESHOLD, 1.0 / difference)
    v = m / jm_boundary[..., 1]
    v = compression_function(v, threshold, difference, invert=invert)
    compressed_j = project_to[..., 0] + v * (jm_boundary[..., 0] - project_to[..., 0])
    compressed_m = project_to[..., 1] + v * (jm_boundary[..., 1] - project_to[..., 1])
    out[..., 0] = np.where(active, compressed_j, out[..., 0])
    out[..., 1] = np.where(active, compressed_m, out[..., 1])
    return out


def gamut_map_fwd(jmh, params, gamut_cusp_table, gamut_top_gamma, reach_m_table):
    return compress_gamut(
        jmh,
        params,
        jmh[..., 0],
        gamut_cusp_table,
        gamut_top_gamma,
        reach_m_table,
        invert=False,
    )


def _outside_hull(rgb):
    return np.any(rgb > 1.0, axis=-1)


def _evaluate_gamma_fit(jm_cusp, test_jmh, params, top_gamma, peak_luminance):
    focus_j = _lerp(
        jm_cusp[0],
        params.mid_j,
        min(1.0, CUSP_MID_BLEND - (jm_cusp[0] / params.limit_jmax)),
    )
    slope_gain = params.limit_jmax * params.focus_dist * float(
        get_focus_gain(np.array([test_jmh[0]]), np.array([jm_cusp[0]]), params.limit_jmax)[0]
    )
    approx = find_gamut_boundary_intersection(
        np.array(test_jmh),
        np.array(jm_cusp),
        np.array([focus_j]),
        np.array([params.limit_jmax]),
        np.array([slope_gain]),
        np.array([top_gamma]),
        np.array([LOWER_HULL_GAMMA]),
    )[0]
    approx_jmh = np.array([approx[0], approx[1], test_jmh[2]])
    rgb = jmh_to_rgb(
        approx_jmh,
        params.limit_xyz_to_rgb,
        peak_luminance,
        params.xyz_w_limit,
    )
    return bool(_outside_hull(rgb))


def make_gamut_table(chroma, rgb_to_xyz, peak_luminance):
    unsorted = np.zeros((GAMUT_TABLE_SIZE, 3), dtype=np.float64)
    for i in range(GAMUT_TABLE_SIZE):
        h_norm = float(i) / GAMUT_TABLE_SIZE
        rgb = hsv_to_rgb(np.array([h_norm, 1.0, 1.0]))
        unsorted[i] = rgb_to_jmh(rgb, rgb_to_xyz, peak_luminance)
    min_h = int(np.argmin(unsorted[:, 2]))
    table = np.zeros((TOTAL_TABLE_SIZE, 3), dtype=np.float64)
    for i in range(GAMUT_TABLE_SIZE):
        table[i + BASE_INDEX] = unsorted[(min_h + i) % GAMUT_TABLE_SIZE]
    table[0] = table[BASE_INDEX + GAMUT_TABLE_SIZE - 1]
    table[BASE_INDEX + GAMUT_TABLE_SIZE] = table[BASE_INDEX]
    table[0, 2] -= 360.0
    table[BASE_INDEX + GAMUT_TABLE_SIZE, 2] += 360.0
    return table


def make_reach_m_table(xyz_to_rgb, params, peak_luminance):
    reach = np.zeros(GAMUT_TABLE_SIZE, dtype=np.float64)
    for i in range(GAMUT_TABLE_SIZE):
        hue = float(base_hue_for_position(i, GAMUT_TABLE_SIZE))
        low, high = 0.0, 50.0
        outside = False
        while (not outside) and high < 1300.0:
            rgb = jmh_to_rgb(
                np.array([params.limit_jmax, high, hue]),
                xyz_to_rgb,
                peak_luminance,
                params.xyz_w_limit,
            )
            outside = bool(np.any(rgb < 0.0))
            if not outside:
                low = high
                high += 50.0
        while high - low > 1e-2:
            sample = (high + low) / 2.0
            rgb = jmh_to_rgb(
                np.array([params.limit_jmax, sample, hue]),
                xyz_to_rgb,
                peak_luminance,
                params.xyz_w_limit,
            )
            if np.any(rgb < 0.0):
                high = sample
            else:
                low = sample
        reach[i] = high
    return reach


def make_upper_hull_gamma_table(gamut_cusp_table, params, peak_luminance):
    gamma = np.full(GAMUT_TABLE_SIZE, -1.0, dtype=np.float64)
    gamut_top = np.zeros(TOTAL_TABLE_SIZE, dtype=np.float64)
    test_positions = (0.01, 0.5, 0.99)
    for i in range(GAMUT_TABLE_SIZE):
        hue = float(base_hue_for_position(i, GAMUT_TABLE_SIZE))
        jm_cusp = tables.cusp_from_table_vectorized(np.array([hue]), gamut_cusp_table)[0]
        test_jmh = [
            np.array([jm_cusp[0] + ((params.limit_jmax - jm_cusp[0]) * pos), jm_cusp[1], hue])
            for pos in test_positions
        ]
        low = GAMMA_MINIMUM
        high = low + GAMMA_SEARCH_STEP
        found = False
        while (not found) and high < GAMMA_MAXIMUM:
            found = all(_evaluate_gamma_fit(jm_cusp, t, params, high, peak_luminance) for t in test_jmh)
            if not found:
                low = high
                high += GAMMA_SEARCH_STEP
        while high - low > GAMMA_ACCURACY:
            test_gamma = (high + low) / 2.0
            if all(_evaluate_gamma_fit(jm_cusp, t, params, test_gamma, peak_luminance) for t in test_jmh):
                high = test_gamma
                gamma[i] = high
            else:
                low = test_gamma
        gamut_top[i + BASE_INDEX] = gamma[i]
    gamut_top[0] = gamma[GAMUT_TABLE_SIZE - 1]
    gamut_top[TOTAL_TABLE_SIZE - 1] = gamma[0]
    return gamut_top
