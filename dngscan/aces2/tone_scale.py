# SPDX-License-Identifier: GPL-3.0-or-later
"""Daniele Evo tonescale ported from Lib.Academy.Tonescale.ctl."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .constants import REFERENCE_LUMINANCE


@dataclass(frozen=True)
class TSParams:
    n: float
    n_r: float
    g: float
    t_1: float
    c_t: float
    s_2: float
    u_2: float
    m_2: float


def init_ts_params(peak_luminance: float) -> TSParams:
    n = peak_luminance
    n_r = 100.0
    g = 1.15
    c = 0.18
    c_d = 10.013
    w_g = 0.14
    t_1 = 0.04
    r_hit_min = 128.0
    r_hit_max = 896.0
    r_hit = r_hit_min + (r_hit_max - r_hit_min) * (np.log(n / n_r) / np.log(10000.0 / 100.0))
    m_0 = n / n_r
    m_1 = 0.5 * (m_0 + np.sqrt(m_0 * (m_0 + 4.0 * t_1)))
    u = np.power((r_hit / m_1) / ((r_hit / m_1) + 1.0), g)
    m = m_1 / u
    w_i = np.log(n / 100.0) / np.log(2.0)
    c_t = c_d / n_r * (1.0 + w_i * w_g)
    g_ip = 0.5 * (c_t + np.sqrt(c_t * (c_t + 4.0 * t_1)))
    g_ipp2 = -(m_1 * np.power(g_ip / m, 1.0 / g)) / (np.power(g_ip / m, 1.0 / g) - 1.0)
    w_2 = c / g_ipp2
    s_2 = w_2 * m_1
    u_2 = np.power((r_hit / m_1) / ((r_hit / m_1) + w_2), g)
    m_2 = m_1 / u_2
    return TSParams(
        n=float(n),
        n_r=float(n_r),
        g=float(g),
        t_1=float(t_1),
        c_t=float(c_t),
        s_2=float(s_2),
        u_2=float(u_2),
        m_2=float(m_2),
    )


def tonescale_fwd(x: np.ndarray, params: TSParams) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    y = np.maximum(0.0, x) / (x + params.s_2)
    f = params.m_2 * np.power(y, params.g)
    h = np.maximum(0.0, f * f / (f + params.t_1))
    return h * params.n_r


def tonescale_inv(y: np.ndarray, params: TSParams) -> np.ndarray:
    """Inverse tonescale; input is internal h (luminance / n_r when n_r=100)."""
    y = np.asarray(y, dtype=np.float64)
    z = np.maximum(0.0, np.minimum(params.n / (params.u_2 * params.n_r), y))
    h = (z + np.sqrt(z * (4.0 * params.t_1 + z))) / 2.0
    denom = np.power(params.m_2 / np.maximum(h, 1e-30), 1.0 / params.g) - 1.0
    f = np.where(np.abs(denom) < 1e-30, 0.0, params.s_2 / denom)
    return f


def apply_tonescale_to_j(j: np.ndarray, ts: TSParams) -> np.ndarray:
    from .jmh import hellwig_j_to_y, y_to_hellwig_j

    linear = hellwig_j_to_y(j) / REFERENCE_LUMINANCE
    luminance = tonescale_fwd(linear, ts)
    return y_to_hellwig_j(luminance)
