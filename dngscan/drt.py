# SPDX-License-Identifier: GPL-3.0-or-later
"""Endpoint-normalized C1 DRT using darktable's AgX curve construction.

Black/white endpoints are scene-derived, but the calibrated 0 EV pivot stays at 18%
output. This avoids the failure mode of attaching an endpoint segment across the pivot:
that makes sparse lights glare while the rest of a dark frame stays unreadable.
"""
from __future__ import annotations

from typing import Any

from ._deps import np
from . import agx

EPS = 1e-6


def curve_params_from_plan(plan: Any) -> dict[str, float | bool]:
    """Compile the darktable-style C1 curve for one scene plan.

    Endpoints stay scene-derived and EV=0 remains the calibrated mid-gray anchor for
    exposure. When pivot_ev_offset is non-zero the contrast pivot moves toward the
    scene body (brightness-preserving shifted pivot + adaptive gamma).
    """
    pivot = round(float(getattr(plan, "pivot_ev_offset", 0.0)), 3)
    return agx.curve_params(
        round(float(getattr(plan, "black_ev", -10.0)), 3),
        round(float(getattr(plan, "white_ev", 6.5)), 3),
        round(float(getattr(plan, "contrast", 3.0)), 3),
        round(float(getattr(plan, "toe_power", 1.5)), 3),
        round(float(getattr(plan, "shoulder_power", 3.3)), 3),
        round(float(getattr(plan, "latitude_lo_ev", 0.0)), 3),
        round(float(getattr(plan, "latitude_hi_ev", 0.0)), 3),
        pivot_ev_offset=pivot,
        target_black_linear=float(getattr(plan, "target_black_linear", 0.0)),
        target_white_linear=float(getattr(plan, "target_white_linear", 1.0)),
        keep_pivot_diagonal=abs(pivot) > 1e-6,
        curve_gamma=float(getattr(plan, "curve_gamma", agx.DEFAULT_CURVE_GAMMA)),
    )


def apply_c1_endpoints(ev: Any, plan: Any) -> Any:
    """Apply darktable-style C1 sigmoid segments in the shared scene-EV domain."""
    e = np.asarray(ev, dtype=np.float32)
    params = curve_params_from_plan(plan)
    x = (e - float(params["black_ev"])) / float(params["range_ev"])
    encoded = agx.apply_curve(np.clip(x, 0.0, 1.0), params)
    return np.power(np.maximum(encoded, 0.0), float(params["gamma"])).astype(np.float32, copy=False)
