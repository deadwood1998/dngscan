# SPDX-License-Identifier: GPL-3.0-or-later
"""In-gamut chroma compression ported from Lib.Academy.OutputTransform.ctl."""
from __future__ import annotations

import numpy as np

from . import tables
from .constants import VIEWING_CONDITIONS_DIM


def toe(
    x: np.ndarray,
    limit: np.ndarray | float,
    k1_in: np.ndarray | float,
    k2_in: np.ndarray | float,
    *,
    invert: bool = False,
) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    over = x > limit
    k2 = np.maximum(k2_in, 0.001)
    k1 = np.sqrt(k1_in * k1_in + k2 * k2)
    k3 = (limit + k1) / (limit + k2)
    if invert:
        xt = (x * x + k1 * x) / (k3 * (x + k2))
    else:
        minus_b = k3 * x - k1
        minus_c = k2 * k3 * x
        xt = 0.5 * (minus_b + np.sqrt(minus_b * minus_b + 4.0 * minus_c))
    return np.where(over, x, xt)


def chroma_compression(
    jmh: np.ndarray,
    orig_j: np.ndarray,
    params,
    reach_gamut_table: np.ndarray,
    reach_m_table: np.ndarray,
    *,
    invert: bool = False,
) -> np.ndarray:
    jmh = np.asarray(jmh, dtype=np.float64)
    orig_j = np.asarray(orig_j, dtype=np.float64)
    j = jmh[..., 0]
    m = jmh[..., 1]
    h = jmh[..., 2]
    out = m.copy()
    mask = m != 0.0
    if not np.any(mask):
        return out

    n_j = j / params.limit_jmax
    sn_j = np.maximum(0.0, 1.0 - n_j)
    m_norm = tables.cusp_from_table(h, reach_gamut_table)[..., 1]
    reach_m = tables.reach_m_from_table(h, reach_m_table)
    limit = np.power(n_j, params.model_gamma) * reach_m / m_norm

    toe_limit = limit - 0.001
    toe_snj_sat = sn_j * params.sat
    toe_sqrt = np.sqrt(n_j * n_j + params.sat_thr)
    toe_nj_compr = n_j * params.compr

    if not invert:
        m_adj = m * np.power(j / orig_j, params.model_gamma)
        m_adj = m_adj / m_norm
        m_adj = limit - toe(limit - m_adj, toe_limit, toe_snj_sat, toe_sqrt, invert=False)
        m_adj = toe(m_adj, limit, toe_nj_compr, sn_j, invert=False)
        m_adj = m_adj * m_norm
    else:
        m_adj = m / m_norm
        m_adj = toe(m_adj, limit, toe_nj_compr, sn_j, invert=True)
        m_adj = limit - toe(limit - m_adj, toe_limit, toe_snj_sat, toe_sqrt, invert=True)
        m_adj = m_adj * m_norm
        m_adj = m_adj * np.power(j / orig_j, -params.model_gamma)

    out = np.where(mask, m_adj, 0.0)
    return out


def tonemap_and_compress_fwd(
    input_jmh: np.ndarray,
    params,
    reach_gamut_table: np.ndarray,
    reach_m_table: np.ndarray,
) -> np.ndarray:
    from .jmh import hellwig_j_to_y, y_to_hellwig_j
    from .tone_scale import init_ts_params, tonescale_fwd

    input_jmh = np.asarray(input_jmh, dtype=np.float64)
    linear = hellwig_j_to_y(input_jmh[..., 0]) / params.reference_luminance
    ts = init_ts_params(params.peak_luminance)
    luminance = tonescale_fwd(linear, ts)
    tonemapped_j = y_to_hellwig_j(luminance)
    out = input_jmh.copy()
    out[..., 0] = tonemapped_j
    out[..., 1] = chroma_compression(
        out,
        input_jmh[..., 0],
        params,
        reach_gamut_table,
        reach_m_table,
        invert=False,
    )
    return out
