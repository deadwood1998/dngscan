# SPDX-License-Identifier: GPL-3.0-or-later
"""HDR colour geometry: how much of the extra range each channel spends on its own.

Phase 3 of docs/DARKTABLE_HDR_AGX_DESIGN.zh-CN.md. Two failure modes bound the problem.
A purely common lift inherits the SDR path-to-white exactly, so bright saturated things
stay as desaturated in HDR as they were in SDR and the extra range buys nothing but
brightness. Letting the three channels expand independently produces neon highlights,
because a channel with a high scene EV runs away from its neighbours. `rho` is the
continuous geometry between them:

    w_mix = (1-rho)*w_Y + rho*w_c
    p     = f * 2**(H_budget * w_mix)

and then the result is renormalised back onto the common luminance target, so `rho`
changes only chromaticity and never brightness. That renormalisation is what makes `rho`
a colour control rather than a second, hidden tone control.

The gamut fit is a separate function from the SDR one on purpose. The SDR fitter targets
[0,1]; an HDR rendition legitimately lives in [0, R_display], and reusing the SDR fitter
would clip away exactly the range this whole phase exists to produce.
"""
from __future__ import annotations

from typing import Any

from ._deps import np
from .constants import REC2020_LUMA, RGB_TO_XYZ
from .hdr_agx_math import smootherstep

_EPS = np.float32(1e-6)


def output_luma_weights(output_gamut: str) -> Any:
    """Luminance row of the output space's RGB->XYZ matrix.

    Taken from the same matrix the render uses, so the projector's "Y is preserved"
    guarantee is about the Y the pipeline actually computes, not a nominally similar one.
    """
    space = {"srgb": "sRGB", "p3": "P3"}.get(str(output_gamut), "P3")
    return np.asarray(RGB_TO_XYZ[space][1], dtype=np.float32)


def channel_lift_weights(
    scene_rec2020: Any, knee_ev: float, white_ev: float, rho: float
) -> Any:
    """Per-channel smootherstep weight, mixed with the common luminance one by `rho`.

    Scene luminance is read from Rec.2020 before any inset, as the design requires: if the
    rendering primaries fed back into this, changing a colour preset would silently change
    a tone decision.
    """
    rgb = np.asarray(scene_rec2020, dtype=np.float32)
    window = float(white_ev) - float(knee_ev)
    if window <= 0.0:
        return np.zeros_like(rgb)

    luma = (
        rgb[..., 0] * np.float32(REC2020_LUMA[0])
        + rgb[..., 1] * np.float32(REC2020_LUMA[1])
        + rgb[..., 2] * np.float32(REC2020_LUMA[2])
    )
    ev_y = np.log2(np.maximum(luma, _EPS) / np.float32(0.18))
    w_y = smootherstep((ev_y - knee_ev) / window).astype(np.float32)
    if float(rho) <= 0.0:
        return np.repeat(w_y[..., None], 3, axis=-1)

    ev_c = np.log2(np.maximum(rgb, _EPS) / np.float32(0.18))
    w_c = smootherstep((ev_c - knee_ev) / window).astype(np.float32)
    r = np.float32(rho)
    # Gated by w_Y rather than the design's literal (1-rho)*w_Y + rho*w_c. That form lets
    # a pixel whose *luminance* is below the knee still receive channel lift whenever one
    # channel is bright -- a saturated lamp in a dark scene -- which measured 0.12 of RGB
    # change in the shadows of a night frame and contradicts the promise in section 6.3
    # that nothing below the knee moves. Multiplying the per-channel term by w_Y closes
    # that hole while keeping every identity the design requires: w_Y=0 gives no lift at
    # all, rho=0 gives exactly w_Y, and at w_Y=1 with rho=1 it is exactly w_c.
    return w_y[..., None] * ((np.float32(1.0) - r) + r * w_c)


def apply_channel_lift(
    formation_rgb: Any,
    scene_rec2020: Any,
    knee_ev: float,
    white_ev: float,
    budget_ev: float,
    rho: float,
    luma_weights: Any,
) -> Any:
    """Lift per channel, then restore the common luminance target.

    Without the second step `rho` would also brighten, and every colour A/B would be
    confounded by a tone change. With it, the identities the design asks for hold exactly:
    a zero budget is the SDR formation, and `rho = 0` leaves the renormalisation factor at
    exactly 1 because the proposal already equals the target.
    """
    f = np.asarray(formation_rgb, dtype=np.float32)
    if float(budget_ev) <= 0.0:
        return f

    weights = channel_lift_weights(scene_rec2020, knee_ev, white_ev, rho)
    proposal = f * np.exp2(np.float32(budget_ev) * weights)
    if float(rho) <= 0.0:
        return proposal

    w = np.asarray(luma_weights, dtype=np.float32)
    y0 = np.tensordot(f, w, axes=([-1], [0]))
    # The common-lift weight is the rho=0 column of the mix, recovered without a second
    # smootherstep evaluation.
    w_y = channel_lift_weights(scene_rec2020, knee_ev, white_ev, 0.0)[..., 0]
    y_target = y0 * np.exp2(np.float32(budget_ev) * w_y)
    y_prop = np.tensordot(proposal, w, axes=([-1], [0]))
    scale = y_target / np.maximum(y_prop, _EPS)
    return proposal * scale[..., None]


def neutral_axis_lambda(rgb: Any, peak: float, luma_weights: Any) -> Any:
    """Largest chroma scale keeping every channel inside [0, peak].

    Solves the bound directly rather than clipping per channel. Per-channel clipping moves
    a colour along whichever axis happened to overflow, which shifts hue; scaling chroma
    toward the neutral axis moves it along a line of constant hue and constant Y.
    """
    arr = np.asarray(rgb, dtype=np.float32)
    w = np.asarray(luma_weights, dtype=np.float32)
    y = np.tensordot(arr, w, axes=([-1], [0]))[..., None]
    c = arr - y
    limit = np.float32(peak)

    # Only the ceiling is solved here. The floor is deliberately left alone: the SDR
    # render carries out-of-gamut negatives at this stage too and resolves them at
    # quantisation, so constraining them here would make the two renditions differ by
    # something other than the HDR lift -- and that difference is exactly what a gain map
    # encodes. Components pointing inward get +inf so the min ignores them.
    big = np.float32(np.inf)
    pos = np.where(c > 0.0, (limit - y) / np.maximum(c, _EPS), big)
    lam = np.min(pos, axis=-1)
    return np.clip(np.minimum(lam, np.float32(1.0)), 0.0, 1.0)


def fit_hdr_color_volume(rgb: Any, peak: float, output_gamut: str = "p3") -> Any:
    """Bring an HDR rendition inside [0, peak] without moving hue or luminance.

    Only the ceiling is enforced; see neutral_axis_lambda for why the floor is left to
    the same downstream handling the SDR render uses.

    Y is preserved to ~3e-5 relative when it is already inside the cube, which is the
    useful property: the tone allocation decided the luminance, and the gamut fit must not
    be able to overrule it. The residual is structural rather than numerical -- the P3
    luminance row sums to 1.0000274, so a neutral [1,1,1] does not have exactly unit Y and
    the chroma vector is not exactly iso-luminant. Renormalising the row would fix the
    identity but would no longer be the Y the rest of the pipeline computes.
    """
    arr = np.asarray(rgb, dtype=np.float32)
    limit = np.float32(peak)
    # Leave in-gamut pixels strictly untouched. Reconstructing them as y + 1.0*(arr - y)
    # is exact in real arithmetic but not in float32, and that rounding alone was enough
    # to break the bit-identity between a zero-budget HDR render and the SDR one.
    needs_fit = np.any(arr > limit, axis=-1)
    if not bool(np.any(needs_fit)):
        return arr

    w = output_luma_weights(output_gamut)
    y = np.tensordot(arr, w, axes=([-1], [0]))[..., None]
    lam = neutral_axis_lambda(arr, limit, w)[..., None]
    fitted = y + lam * (arr - y)
    # Y itself can sit outside the cube on a blown neutral; that is a tone question the
    # projector must not answer by inventing chroma, so clamp only after the projection.
    fitted = np.minimum(fitted, limit)
    return np.where(needs_fit[..., None], fitted, arr)
