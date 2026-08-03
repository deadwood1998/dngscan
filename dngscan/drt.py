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


# Display-linear reference for the compiled "toe end": the level at which output is
# practically black on an SDR delivery (~sRGB code 12/255). The toe-end scene EV is
# where the curve crosses this level coming up from the black endpoint. It is a
# measurement coordinate for reporting and for the bounded toe_end_offset adjustment,
# not a curve parameter: view brightness and display looks apply after it.
TOE_END_DISPLAY_LINEAR = 0.002

# Legality bounds for the re-solved toe power. The lower bound keeps the sigmoid toe
# meaningfully shaped (below ~0.35 the toe flattens into a near-plateau whose crossing
# becomes numerically flat, so the solve loses conditioning); the upper bound matches
# the hardest toe the existing shadow-transition bias can reach with margin. Requests
# whose target crossing is unreachable inside these bounds clamp to the bound and the
# compiled toe-end fact reports the value actually achieved.
TOE_POWER_SOLVE_MIN = 0.35
TOE_POWER_SOLVE_MAX = 3.5


def _value_at_ev(ev: float, params: dict[str, float | bool]) -> float:
    x = (ev - float(params["black_ev"])) / float(params["range_ev"])
    x = min(1.0, max(0.0, x))
    encoded = float(agx.apply_curve(np.asarray([x], dtype=np.float32), params)[0])
    return max(0.0, encoded) ** float(params["gamma"])


def toe_end_ev_from_params(
    params: dict[str, float | bool], level: float = TOE_END_DISPLAY_LINEAR
) -> float:
    """Scene EV where the compiled curve's display-linear output crosses ``level``.

    The curve is monotone in EV, so a plain bisection is exact enough. Returns the
    black endpoint when the curve never falls to the level (a lifted target_black
    floor, e.g. film paper Dmax), and 0.0 in the degenerate case where even mid gray
    sits below it.
    """
    black = float(params["black_ev"])
    lo, hi = black + 1e-3, 0.0
    if _value_at_ev(lo, params) >= level:
        return black
    if _value_at_ev(hi, params) < level:
        return 0.0
    for _ in range(48):
        mid = 0.5 * (lo + hi)
        if _value_at_ev(mid, params) < level:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def compiled_curve_transitions(plan: Any) -> dict[str, float]:
    """Measured facts of the compiled curve, after every clamp and guard.

    ``toe_end_ev`` is the near-black crossing defined above. ``toe_start_ev`` and
    ``shoulder_start_ev`` are the actual latitude transition anchors the solver kept
    after reserving its minimum segment runs and display-range clamps — the values a
    report may print as truth, as opposed to the requested plan fields.
    """
    params = curve_params_from_plan(plan)
    black = float(params["black_ev"])
    range_ev = float(params["range_ev"])
    return {
        "toe_end_ev": toe_end_ev_from_params(params),
        "toe_start_ev": black + float(params["toe_transition_x"]) * range_ev,
        "shoulder_start_ev": black + float(params["shoulder_transition_x"]) * range_ev,
    }


def _params_for_toe_power(key: tuple, toe_power: float) -> dict[str, float | bool]:
    """Uncached curve build for the bisection: keep solver probes out of the caches."""
    (
        black_ev, white_ev, contrast, _toe_power, shoulder_power,
        latitude_lo_ev, latitude_hi_ev, pivot,
        target_black_linear, target_white_linear, keep_pivot_diagonal, curve_gamma,
    ) = key
    return agx.curve_params.__wrapped__(
        black_ev, white_ev, contrast, float(toe_power), shoulder_power,
        latitude_lo_ev, latitude_hi_ev,
        pivot_ev_offset=pivot,
        target_black_linear=target_black_linear,
        target_white_linear=target_white_linear,
        keep_pivot_diagonal=keep_pivot_diagonal,
        curve_gamma=curve_gamma,
    )


def solve_toe_power_for_toe_end(plan: Any, target_toe_end_ev: float) -> float:
    """Toe power whose compiled curve crosses near-black at the requested scene EV.

    The crossing is monotone in toe power (a lower power opens the toe, so the same
    display level is reached deeper in EV). The solve runs at plan-compile time only
    and touches nothing but ``toe_power``: black/white endpoints, pivot anchor,
    latitude anchors and the shoulder are all fixed inputs, so the sky-side of the
    curve cannot move. Out-of-reach targets clamp to the legality bounds.
    """
    key = _curve_params_key(plan)
    black = float(getattr(plan, "black_ev", -10.0))
    target = min(-0.25, max(black + 0.05, float(target_toe_end_ev)))
    lo, hi = TOE_POWER_SOLVE_MIN, TOE_POWER_SOLVE_MAX
    # A lower toe power lifts the toe -> deeper crossing. Check reachability first.
    if toe_end_ev_from_params(_params_for_toe_power(key, lo)) > target:
        return lo
    if toe_end_ev_from_params(_params_for_toe_power(key, hi)) < target:
        return hi
    for _ in range(28):
        mid = 0.5 * (lo + hi)
        crossing = toe_end_ev_from_params(_params_for_toe_power(key, mid))
        if abs(crossing - target) <= 0.01:
            return mid
        if crossing < target:
            # crossing too deep -> toe too open -> raise power
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


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
