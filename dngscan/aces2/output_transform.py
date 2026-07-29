# SPDX-License-Identifier: GPL-3.0-or-later
"""ACES 2-derived development-snapshot output transform and public render API."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from . import gamut_compression, white_limiting
from .chroma_compression import tonemap_and_compress_fwd
from .constants import (
    AP1,
    AP1_RGB_TO_XYZ,
    AP1_XYZ_TO_RGB,
    CHROMA_COMPRESS,
    CHROMA_COMPRESS_FACT,
    CHROMA_EXPAND,
    CHROMA_EXPAND_FACT,
    CHROMA_EXPAND_THR,
    DISPLAY_P3_D65,
    FOCUS_DISTANCE,
    FOCUS_DISTANCE_SCALING,
    P3_XYZ_TO_RGB,
    REFERENCE_LUMINANCE,
    VIEWING_CONDITIONS_DIM,
    rgb_to_xyz_f33,
    xyz_to_rgb_f33,
)
from .input_transform import rec2020_d65_to_ap0, sanitize_scene_rgb
from .jmh import aces_to_jmh, jmh_to_xyz
from .jmh import y_to_hellwig_j
from .tone_scale import init_ts_params


@dataclass(frozen=True)
class ODTParams:
    peak_luminance: float
    reference_luminance: float
    n_r: float
    g: float
    t_1: float
    c_t: float
    s_2: float
    u_2: float
    m_2: float
    limit_jmax: float
    mid_j: float
    model_gamma: float
    sat: float
    sat_thr: float
    compr: float
    focus_dist: float
    limit_rgb_to_xyz: np.ndarray
    limit_xyz_to_rgb: np.ndarray
    xyz_w_limit: np.ndarray
    output_rgb_to_xyz: np.ndarray
    output_xyz_to_rgb: np.ndarray
    xyz_w_output: np.ndarray
    lower_hull_gamma: float


@dataclass(frozen=True)
class ODTContext:
    params: ODTParams
    gamut_cusp_table: np.ndarray
    gamut_top_gamma: np.ndarray
    reach_gamut_table: np.ndarray
    reach_m_table: np.ndarray


def init_odt_params(
    peak_luminance: float,
    limiting=DISPLAY_P3_D65,
    encoding=DISPLAY_P3_D65,
) -> ODTParams:
    ts = init_ts_params(peak_luminance)
    limit_jmax = float(y_to_hellwig_j(peak_luminance))
    mid_j = float(y_to_hellwig_j(ts.c_t * 100.0))
    log_peak = np.log10(ts.n / ts.n_r)
    compr = CHROMA_COMPRESS + (CHROMA_COMPRESS * CHROMA_COMPRESS_FACT) * log_peak
    sat = max(0.2, CHROMA_EXPAND - (CHROMA_EXPAND * CHROMA_EXPAND_FACT) * log_peak)
    sat_thr = CHROMA_EXPAND_THR / ts.n
    model_gamma = 1.0 / (VIEWING_CONDITIONS_DIM[1] * (1.48 + np.sqrt(20.0 / 100.0)))
    focus_dist = FOCUS_DISTANCE + FOCUS_DISTANCE * FOCUS_DISTANCE_SCALING * log_peak
    rgb_w = np.array([100.0, 100.0, 100.0], dtype=np.float64)
    limit_rgb_to_xyz = rgb_to_xyz_f33(limiting, 1.0)
    limit_xyz_to_rgb = xyz_to_rgb_f33(limiting, 1.0)
    output_rgb_to_xyz = rgb_to_xyz_f33(encoding, 1.0)
    output_xyz_to_rgb = xyz_to_rgb_f33(encoding, 1.0)
    return ODTParams(
        peak_luminance=float(peak_luminance),
        reference_luminance=REFERENCE_LUMINANCE,
        n_r=ts.n_r,
        g=ts.g,
        t_1=ts.t_1,
        c_t=ts.c_t,
        s_2=ts.s_2,
        u_2=ts.u_2,
        m_2=ts.m_2,
        limit_jmax=limit_jmax,
        mid_j=mid_j,
        model_gamma=float(model_gamma),
        sat=float(sat),
        sat_thr=float(sat_thr),
        compr=float(compr),
        focus_dist=float(focus_dist),
        limit_rgb_to_xyz=limit_rgb_to_xyz,
        limit_xyz_to_rgb=limit_xyz_to_rgb,
        xyz_w_limit=rgb_w @ limit_rgb_to_xyz,
        output_rgb_to_xyz=output_rgb_to_xyz,
        output_xyz_to_rgb=output_xyz_to_rgb,
        xyz_w_output=rgb_w @ output_rgb_to_xyz,
        lower_hull_gamma=1.14,
    )


@lru_cache(maxsize=16)
def _build_context(peak_luminance: float) -> ODTContext:
    params = init_odt_params(peak_luminance)
    gamut_cusp = gamut_compression.make_gamut_table(
        DISPLAY_P3_D65, params.limit_rgb_to_xyz, peak_luminance
    )
    reach_gamut = gamut_compression.make_gamut_table(AP1, AP1_RGB_TO_XYZ, peak_luminance)
    reach_m = gamut_compression.make_reach_m_table(AP1_XYZ_TO_RGB, params, peak_luminance)
    top_gamma = gamut_compression.make_upper_hull_gamma_table(
        gamut_cusp, params, peak_luminance
    )
    return ODTContext(
        params=params,
        gamut_cusp_table=gamut_cusp,
        gamut_top_gamma=top_gamma,
        reach_gamut_table=reach_gamut,
        reach_m_table=reach_m,
    )


def output_transform_fwd(aces: np.ndarray, peak_luminance: float) -> np.ndarray:
    """ACES AP0 in -> relative XYZ out (Lib.Academy.OutputTransform.ctl)."""
    ctx = _build_context(peak_luminance)
    aces = np.asarray(aces, dtype=np.float64)
    orig = aces.shape
    flat = aces.reshape(-1, 3)
    jmh = aces_to_jmh(flat, peak_luminance)
    tonemapped = tonemap_and_compress_fwd(
        jmh, ctx.params, ctx.reach_gamut_table, ctx.reach_m_table
    )
    compressed = gamut_compression.gamut_map_fwd(
        tonemapped,
        ctx.params,
        ctx.gamut_cusp_table,
        ctx.gamut_top_gamma,
        ctx.reach_m_table,
    )
    xyz = jmh_to_xyz(compressed, ctx.params.xyz_w_limit) / REFERENCE_LUMINANCE
    return xyz.reshape(orig)


def render_aces2_hdr_p3_linear(
    scene_rec2020_d65: np.ndarray,
    *,
    capacity_ev: float = 3.0,
    reference_white_nits: float = 100.0,
) -> np.ndarray:
    """Return extended-linear Display P3 D65 where 1.0 is reference white."""
    peak_nits = reference_white_nits * (2.0 ** capacity_ev)
    scene = sanitize_scene_rgb(scene_rec2020_d65)
    ap0 = rec2020_d65_to_ap0(scene)
    xyz = output_transform_fwd(ap0, peak_nits)
    p3 = white_limiting.xyz_relative_to_p3_linear(
        xyz,
        P3_XYZ_TO_RGB,
        reference_white_nits=reference_white_nits,
        peak_luminance=peak_nits,
    )
    if scene_rec2020_d65.dtype == np.float32:
        return p3.astype(np.float32)
    return p3
