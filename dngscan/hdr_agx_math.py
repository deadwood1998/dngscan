# SPDX-License-Identifier: GPL-3.0-or-later
"""HDR AgX tone allocation: the one-dimensional math, with no image in sight.

The HDR rendition is a second AgX rendered from the same scene-linear data, not the SDR
image with gain applied. What this module owns is only the question of how the extra
display stops are spent along the EV axis:

    TH(e) = T0(e) * 2 ** (H_budget * S(u))

`T0` is the frozen SDR response, `S` a quintic smootherstep over the window between the
knee and the plan's white endpoint, and `H_budget` the number of extra stops this scene
justifies. That shape is chosen because `S`, `S'` and `S''` all vanish at both ends, so
the HDR lift joins the SDR curve at the knee with matching value, slope and curvature --
no seam near diffuse white, which is exactly where a seam would be visible.

Why not simply stretch the existing curve: with dngscan's contrast 3.0, current gamma 2.2
and the upstream reference window, holding the pivot fixed while reaching a 1000/100 HDR
white demands an average shoulder slope of 2.13 from a starting slope of 1.05. A concave
shoulder's slope only falls, so that parameterization has no solution. Generalised, it
would need a white endpoint beyond 13.1 EV (11.6 EV at the 800 nit preset) against the
8.5 EV dngscan actually compiles -- the black endpoint cancels out of that condition
entirely. The test suite pins the current operating range, without pretending no
imaginable gamma could ever solve a different curve.

Everything here is float64 and array-free per-sample math, so it can act as the oracle the
float32 runtime is checked against.
"""
from __future__ import annotations

import math

from ._deps import np
from .constants import DIFFUSE_WHITE_EV

# Smootherstep's derivative peaks at S'(0.5) = 1.875. Dividing that by the window width
# gives the steepest rate at which HDR gain is added, in EV of lift per EV of scene.
SMOOTHERSTEP_PEAK_SLOPE = 1.875

# Temporary policy gate. Smootherstep itself remains numerically well-defined for any
# positive window; 0.5 EV is therefore not a mathematical stability threshold. Keep it
# explicit until the corpus decides whether a hard gate is useful at all.
MINIMUM_WINDOW_EV = 0.5

# Temporary aesthetic cap, in added log2-output EV per scene EV. It is deliberately not
# identified with AgX contrast=3.0: AgX contrast is an encoded-curve slope in normalized
# x, so the two 3.0 values live in different coordinate systems. This value needs EDR
# corpus calibration before production HDR can be enabled.
MAX_LIFT_RATE = 3.0


# Upstream's parameter normalisation base, from the -10..+6.5 EV reference window. AgX
# contrast is quoted against this span, so a plan with a different window has a different
# encoded slope for the same contrast value.
REFERENCE_WINDOW_EV = 16.5


def sdr_encoded_slope(
    contrast: float,
    black_ev: float,
    white_ev: float,
    reference_window_ev: float = REFERENCE_WINDOW_EV,
) -> float:
    """Encoded-curve slope at the pivot: `C * (W - B) / 16.5`.

    Contrast alone is not the slope. It is quoted against the upstream 16.5 EV reference
    window, so a plan compiled to a narrower window has a proportionally smaller encoded
    slope. Reading `contrast = 3.0` as "slope 3.0" only happens to be right when the plan
    window is exactly the reference one.
    """
    return float(contrast) * (float(white_ev) - float(black_ev)) / float(reference_window_ev)


def single_curve_minimum_white_ev(
    contrast: float,
    peak_ratio: float,
    curve_gamma: float,
    reference_window_ev: float = REFERENCE_WINDOW_EV,
) -> float:
    """Smallest white EV at which stretching one C1 curve to HDR could work at all.

    From requiring the HDR pivot slope to be at least the average slope needed to reach
    encoded white:

        s_hdr >= s_avg
        C*(W-B)/16.5 * R^(-1/g) >= (1-q_pivot)*(W-B)/W

    The (W-B) factors cancel, so the black endpoint drops out entirely and the condition
    reduces to a bound on W alone. Returning that bound rather than a yes/no keeps the
    refutation checkable against whatever window a plan actually compiles.
    """
    q_pivot = (0.18 / float(peak_ratio)) ** (1.0 / float(curve_gamma))
    return (
        float(reference_window_ev)
        * (1.0 - q_pivot)
        / (float(contrast) * float(peak_ratio) ** (-1.0 / float(curve_gamma)))
    )


def hdr_encoded_pivot_slope(
    sdr_encoded_slope: float,
    peak_ratio: float,
    curve_gamma: float,
    window_ratio: float = 1.0,
) -> float:
    """Encoded-curve slope an HDR pivot would need to match SDR linear contrast.

    Used only to show that a single stretched curve cannot work. Both curves must put
    Y=0.18 at the pivot, so with Y_sdr = q^g and Y_hdr = R*q^g the encoded pivot values
    differ by R^(-1/g). Requiring equal dY/de there and substituting that relation
    collapses the whole expression to

        s_hdr = s_sdr * R^(-1/g)

    -- the q^(g-1) factors cancel exactly. `window_ratio` carries the HDR/SDR EV window
    length ratio when the two windows differ; at equal windows it is 1.

    This is an *encoded* slope, not a linear-luminance one and not a universal constant.
    """
    return float(sdr_encoded_slope) * float(peak_ratio) ** (-1.0 / float(curve_gamma)) * float(
        window_ratio
    )


def smootherstep(u):
    """Quintic 6u^5-15u^4+10u^3, clamped to [0,1].

    Chosen over smoothstep because the second derivative also vanishes at both ends: the
    knee then inherits SDR curvature, not just SDR slope.
    """
    x = np.clip(np.asarray(u, dtype=np.float64), 0.0, 1.0)
    return x * x * x * (x * (x * 6.0 - 15.0) + 10.0)


def smootherstep_derivative(u):
    """30u^2(1-u)^2, zero at both ends and peaking at 1.875."""
    x = np.clip(np.asarray(u, dtype=np.float64), 0.0, 1.0)
    return 30.0 * x * x * (1.0 - x) * (1.0 - x)


def allocation_window(knee_ev: float, white_ev: float) -> float:
    """EV span the HDR lift is spread over."""
    return float(white_ev) - float(knee_ev)


def max_lift_rate(budget_ev: float, window_ev: float) -> float:
    """Steepest HDR gain rate, in EV of lift per EV of scene.

    The number to look at when asking whether HDR will read as a smooth reveal or as a
    hard edge, because C2 continuity at the knee says nothing about how steep the curve
    becomes just past it.
    """
    if window_ev <= 0.0:
        return float("inf")
    return float(budget_ev) * SMOOTHERSTEP_PEAK_SLOPE / float(window_ev)


def clamp_budget_to_lift_rate(
    budget_ev: float, window_ev: float, max_rate: float = MAX_LIFT_RATE
) -> float:
    """Cut the budget until the HDR segment is no steeper than `max_rate`.

    A pass/zero gate on window width alone is not enough. dngscan's white endpoint floor
    of +3.00 EV puts a large share of real frames at a 0.526 EV window, which clears a
    0.5 EV gate by a hair and would then compress the whole lift into half a stop. Scaling
    the budget down degrades continuously instead: narrow windows still get HDR, just less
    of it, and the transition respects the explicitly configured prototype rate cap.
    """
    if budget_ev <= 0.0:
        return 0.0
    if window_ev <= 0.0:
        return 0.0
    ceiling = float(max_rate) * float(window_ev) / SMOOTHERSTEP_PEAK_SLOPE
    return float(min(float(budget_ev), ceiling))


def compile_budget(
    reliable_tail_ev: float,
    knee_ev: float,
    white_ev: float,
    display_headroom_ev: float,
    minimum_window_ev: float = MINIMUM_WINDOW_EV,
    max_rate: float = MAX_LIFT_RATE,
) -> float:
    """How many extra stops this scene justifies, bounded by what the display offers.

    Reads only the reliable tail, so reconstructed highlights cannot buy HDR range the
    sensor never recorded. A scene whose tail sits at diffuse white gets zero, which is
    the point: HDR capacity is not a target every frame must reach.
    """
    window = allocation_window(knee_ev, white_ev)
    if window <= float(minimum_window_ev):
        return 0.0
    signal = max(0.0, float(reliable_tail_ev) - float(knee_ev))
    budget = min(float(display_headroom_ev), signal)
    return clamp_budget_to_lift_rate(budget, window, max_rate)


def lift_stops(scene_ev, knee_ev: float, white_ev: float, budget_ev: float):
    """Extra stops applied at each scene EV: `H_budget * S(u)`."""
    ev = np.asarray(scene_ev, dtype=np.float64)
    window = allocation_window(knee_ev, white_ev)
    if budget_ev <= 0.0 or window <= 0.0:
        return np.zeros_like(ev)
    u = (ev - float(knee_ev)) / window
    return float(budget_ev) * smootherstep(u)


def apply_hdr_allocation(sdr_response, scene_ev, knee_ev: float, white_ev: float, budget_ev: float):
    """TH(e) = T0(e) * 2**(H*S(u)).

    Monotonicity follows from the product rule directly -- both TH' terms are non-negative
    when T0, T0' and S' are -- and deliberately not from the log-derivative form, which
    divides by T0 and so invents a singularity at the black end where T0 is legitimately 0.
    """
    t0 = np.asarray(sdr_response, dtype=np.float64)
    return t0 * np.exp2(lift_stops(scene_ev, knee_ev, white_ev, budget_ev))


def achieved_headroom_ev(hdr_response) -> float:
    """H_actual: what the render reached, which is not what it was allowed."""
    arr = np.asarray(hdr_response, dtype=np.float64)
    peak = float(np.max(arr)) if arr.size else 0.0
    return float(math.log2(peak)) if peak > 1.0 else 0.0
