# SPDX-License-Identifier: GPL-3.0-or-later
"""HDR colour geometry: how much of the extra range each channel spends on its own.

Two failure modes bound the problem. A purely common lift keeps the HDR base formation's
path-to-white exactly, so extra range buys brightness but no additional highlight colour.
Letting the three channels expand independently produces neon highlights,
because a channel with a high scene EV runs away from its neighbours. `rho` is the
continuous geometry between them:

    w_mix = (1-rho)*w_Y + rho*w_c
    p     = f * 2**(H_budget * w_mix)

The result is renormalised back onto the common luminance target, so `rho`
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
    # Copy before normalising. RGB_TO_XYZ is shared by the frozen SDR pipeline; using an
    # ndarray view here and dividing in place would silently rewrite its P3 Y row merely
    # because the HDR module was imported.
    weights = np.array(RGB_TO_XYZ[space][1], dtype=np.float64, copy=True)
    # The repository matrices are rounded display constants. A neutral RGB triplet must
    # nevertheless have Y equal to its component value, otherwise the neutral-axis
    # projector cannot preserve Y exactly by construction.
    weights /= np.sum(weights)
    return weights.astype(np.float32)


def formation_luma_weights(outset_matrix: Any) -> Any:
    """Rec.2020 luminance row for RGB immediately before the actual AgX outset.

    darktable's outset is intentionally not the inverse of its inset: purity restoration
    and unrotation are independent controls. Deriving this row from ``inverse(inset)``
    therefore normalises rho against a transform the pixels never take.
    """
    matrix = np.asarray(outset_matrix, dtype=np.float64)
    weights = np.asarray(REC2020_LUMA, dtype=np.float64) @ matrix
    weights /= np.sum(weights)
    return weights.astype(np.float32)


def raw_gated_channel_separation(rho: float, clip_masks_rgb: Any | None) -> Any:
    """Turn global colour freedom into per-pixel/channel permission from CFA evidence.

    A single clipped CFA channel loses half of its independent path at a fully soft-clipped
    site while the two measured channels remain available. Once two channels clip, the
    second-largest mask continuously withdraws all independent separation; at full
    multi-channel clipping the pixel follows the common luminance path only.
    """
    base = np.float32(np.clip(float(rho), 0.0, 1.0))
    if clip_masks_rgb is None:
        return base
    masks = np.clip(np.asarray(clip_masks_rgb, dtype=np.float32), 0.0, 1.0)
    second = np.partition(masks, 1, axis=-1)[..., 1]
    channel_permission = np.float32(1.0) - np.float32(0.5) * masks
    multi_permission = np.float32(1.0) - second
    return base * channel_permission * multi_permission[..., None]


def channel_lift_weights(
    scene_rec2020: Any,
    knee_ev: float,
    white_ev: float,
    rho: Any,
    channel_scene_rgb: Any | None = None,
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
    r = np.asarray(rho, dtype=np.float32)
    if r.ndim == 0 and float(r) <= 0.0:
        return np.repeat(w_y[..., None], 3, axis=-1)

    channels = rgb if channel_scene_rgb is None else np.asarray(channel_scene_rgb, dtype=np.float32)
    ev_c = np.log2(np.maximum(channels, _EPS) / np.float32(0.18))
    w_c = smootherstep((ev_c - knee_ev) / window).astype(np.float32)
    if r.ndim > 0 and r.shape == w_y.shape:
        r = r[..., None]
    r = np.clip(r, 0.0, 1.0)
    # HDR owns its colour formation, so a bright channel may enter its shoulder before the
    # common luminance does. This is intentional: forcing w_Y=0 to freeze every channel
    # would reintroduce the discarded requirement that HDR equal SDR below one scalar knee.
    common = w_y[..., None]
    return (np.float32(1.0) - r) * common + r * w_c


def apply_channel_lift(
    formation_rgb: Any,
    scene_rec2020: Any,
    knee_ev: float,
    white_ev: float,
    budget_ev: float,
    rho: Any,
    luma_weights: Any,
    channel_scene_rgb: Any | None = None,
) -> Any:
    """Lift per channel, then restore the common luminance target.

    Without the second step `rho` would also brighten, and every colour A/B would be
    confounded by a tone change. With it, the identities the design asks for hold exactly:
    a zero budget is the HDR base formation, and `rho = 0` leaves the renormalisation at
    exactly 1 because the proposal already equals the target.
    """
    f = np.asarray(formation_rgb, dtype=np.float32)
    if float(budget_ev) <= 0.0:
        return f

    weights = channel_lift_weights(
        scene_rec2020, knee_ev, white_ev, rho, channel_scene_rgb=channel_scene_rgb
    )
    proposal = f * np.exp2(np.float32(budget_ev) * weights)
    if bool(np.all(np.asarray(rho, dtype=np.float32) <= 0.0)):
        return proposal

    w = np.asarray(luma_weights, dtype=np.float32)
    y0 = np.tensordot(f, w, axes=([-1], [0]))
    # The common-lift weight is the rho=0 column of the mix, recovered without a second
    # smootherstep evaluation.
    w_y = channel_lift_weights(scene_rec2020, knee_ev, white_ev, 0.0)[..., 0]
    y_target = y0 * np.exp2(np.float32(budget_ev) * w_y)
    y_prop = np.tensordot(proposal, w, axes=([-1], [0]))
    scale = y_target / np.maximum(y_prop, _EPS)
    lifted = proposal * scale[..., None]
    # Where both common and channel paths are inactive, avoid sending an identity proposal
    # through a luminance divide: y/y can round away from one and very dark y is clamped.
    inactive = np.all(weights == np.float32(0.0), axis=-1)
    return np.where(inactive[..., None], f, lifted)


def neutral_axis_lambda(rgb: Any, peak: float, luma_weights: Any) -> Any:
    """Largest linear-RGB opponent scale keeping every channel inside [0, peak].

    Solves the bound directly rather than clipping per channel. Per-channel clipping moves
    a colour along whichever axis happened to overflow. Scaling the vector from the neutral
    axis keeps linear Y and the output-RGB opponent direction when Y is in range. That is a
    stable RGB geometry, but it is not a claim of constant perceptual hue: a CAM such as the
    JMh space used by ACES 2 is required for that stronger guarantee.
    """
    arr = np.asarray(rgb, dtype=np.float32)
    w = np.asarray(luma_weights, dtype=np.float32)
    y_raw = np.tensordot(arr, w, axes=([-1], [0]))[..., None]
    limit = np.float32(peak)
    y = np.clip(y_raw, 0.0, limit)
    c = arr - y_raw

    big = np.float32(np.inf)
    neg = np.where(c < 0.0, y / np.maximum(-c, _EPS), big)
    pos = np.where(c > 0.0, (limit - y) / np.maximum(c, _EPS), big)
    lam = np.minimum(np.min(neg, axis=-1), np.min(pos, axis=-1))
    return np.clip(np.minimum(lam, np.float32(1.0)), 0.0, 1.0)


def fit_hdr_color_volume(rgb: Any, peak: float, output_gamut: str = "p3") -> Any:
    """Bring an HDR rendition inside [0, peak] along its linear-RGB neutral-axis ray.

    Y is preserved when it lies inside the display volume. If Y itself is below zero or
    above peak no in-volume colour can preserve it, so the neutral anchor is clipped to the
    nearest endpoint and the RGB opponent vector collapses as needed. Perceptual hue is only
    approximate here; this intentionally remains much simpler than ACES 2 JMh compression.
    """
    arr = np.asarray(rgb, dtype=np.float32)
    limit = np.float32(peak)
    # Leave in-gamut pixels strictly untouched. Reconstructing them as y + 1.0*(arr - y)
    # is exact in real arithmetic but still introduces needless float32 roundoff.
    needs_fit = np.any((arr < 0.0) | (arr > limit), axis=-1)
    if not bool(np.any(needs_fit)):
        return arr

    w = output_luma_weights(output_gamut)
    y_raw = np.tensordot(arr, w, axes=([-1], [0]))[..., None]
    y = np.clip(y_raw, 0.0, limit)
    lam = neutral_axis_lambda(arr, limit, w)[..., None]
    fitted = y + lam * (arr - y_raw)
    # Exact equations can miss the boundary by a few float32 ulps. This clip is a numerical
    # guard after the neutral-axis solve, not the projector itself.
    fitted = np.clip(fitted, 0.0, limit)
    return np.where(needs_fit[..., None], fitted, arr)
