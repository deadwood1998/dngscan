# SPDX-License-Identifier: GPL-3.0-or-later
"""Endpoint-normalized C1 DRT using darktable's AgX curve construction.

Black/white endpoints are scene-derived, but the calibrated 0 EV pivot stays at 18%
output. This avoids the failure mode of attaching an endpoint segment across the pivot:
that makes sparse lights glare while the rest of a dark frame stays unreadable.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from ._deps import np
from . import agx

EPS = 1e-6


def _curve_params_key(plan: Any) -> tuple:
    pivot = round(float(getattr(plan, "pivot_ev_offset", 0.0)), 3)
    return (
        round(float(getattr(plan, "black_ev", -10.0)), 3),
        round(float(getattr(plan, "white_ev", 6.5)), 3),
        round(float(getattr(plan, "contrast", 3.0)), 3),
        round(float(getattr(plan, "toe_power", 1.5)), 3),
        round(float(getattr(plan, "shoulder_power", 3.3)), 3),
        round(float(getattr(plan, "latitude_lo_ev", 0.0)), 3),
        round(float(getattr(plan, "latitude_hi_ev", 0.0)), 3),
        pivot,
        float(getattr(plan, "target_black_linear", 0.0)),
        float(getattr(plan, "target_white_linear", 1.0)),
        abs(pivot) > 1e-6,
        float(getattr(plan, "curve_gamma", agx.DEFAULT_CURVE_GAMMA)),
    )


@lru_cache(maxsize=128)
def _curve_params_cached(key: tuple) -> dict[str, float | bool]:
    (
        black_ev,
        white_ev,
        contrast,
        toe_power,
        shoulder_power,
        latitude_lo_ev,
        latitude_hi_ev,
        pivot,
        target_black_linear,
        target_white_linear,
        keep_pivot_diagonal,
        curve_gamma,
    ) = key
    return agx.curve_params(
        black_ev,
        white_ev,
        contrast,
        toe_power,
        shoulder_power,
        latitude_lo_ev,
        latitude_hi_ev,
        pivot_ev_offset=pivot,
        target_black_linear=target_black_linear,
        target_white_linear=target_white_linear,
        keep_pivot_diagonal=keep_pivot_diagonal,
        curve_gamma=curve_gamma,
    )


def curve_params_from_plan(plan: Any) -> dict[str, float | bool]:
    """Compile the darktable-style C1 curve for one scene plan.

    Endpoints stay scene-derived and EV=0 remains the calibrated mid-gray anchor for
    exposure. When pivot_ev_offset is non-zero the contrast pivot moves toward the
    scene body (brightness-preserving shifted pivot + adaptive gamma).

    Results are cached by the rounded parameter tuple: the hot path rebuilds the same
    plan for every chunk and every HDR candidate.
    """
    return _curve_params_cached(_curve_params_key(plan))


def apply_c1_endpoints(
    ev: Any, plan: Any, params: dict[str, float | bool] | None = None
) -> Any:
    """Apply darktable-style C1 sigmoid segments in the shared scene-EV domain."""
    e = np.asarray(ev, dtype=np.float32)
    resolved = params if params is not None else curve_params_from_plan(plan)
    x = (e - float(resolved["black_ev"])) / float(resolved["range_ev"])
    encoded = agx.apply_curve(np.clip(x, 0.0, 1.0), resolved)
    return np.power(np.maximum(encoded, 0.0), float(resolved["gamma"])).astype(
        np.float32, copy=False
    )


def c1_value_and_derivative_at_ev(ev: float, plan: Any) -> tuple[float, float]:
    """Rendered body value and analytic dT/de at one scene-EV coordinate.

    The value follows the production float32 path exactly. Its tangent is evaluated from
    the same rounded curve parameters and piece equation, avoiding finite differences on
    a float32 renderer. This is the authoritative attachment point for the HDR shoulder.
    """
    params = curve_params_from_plan(plan)
    sample = np.asarray([ev], dtype=np.float32)
    x = (sample - float(params["black_ev"])) / float(params["range_ev"])
    x = np.clip(x, 0.0, 1.0)
    encoded = agx.apply_curve(x, params)
    gamma = float(params["gamma"])
    value = float(
        np.power(np.maximum(encoded, 0.0), gamma).astype(np.float32, copy=False)[0]
    )

    encoded_value = float(encoded[0])
    if value <= 0.0 or encoded_value <= 0.0:
        return value, 0.0
    encoded_slope_x = agx.curve_derivative(float(x[0]), params)
    slope_t_ev = (
        gamma
        * encoded_value ** (gamma - 1.0)
        * encoded_slope_x
        / float(params["range_ev"])
    )
    return value, float(slope_t_ev)
