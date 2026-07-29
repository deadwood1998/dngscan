# SPDX-License-Identifier: GPL-3.0-or-later
"""HDR AgX formation runtime: the SDR formation plus a scene-driven luminance lift.

Phase 2 of docs/DARKTABLE_HDR_AGX_DESIGN.zh-CN.md. The HDR rendition is rendered from the
same scene-linear buffer as the SDR one, through the same AgX core, and then given extra
display stops wherever the scene justifies them:

    p = f * 2 ** (H_budget * S(u(e_Y)))

with `rho = 0` fixed for this phase, so every channel receives the same lift and the SDR
chroma is untouched. Per-channel separation, the luminance renormalisation it requires,
and the HDR gamut volume fit are Phase 3 and are deliberately absent here -- getting a
neutral HDR right first means a colour bug later cannot hide inside a tone bug.

This is a separate dispatcher rather than a flag on the SDR one. The SDR path must keep
producing the bytes it produces today, and the surest way to guarantee that is to leave
it alone. What the two share is primitives, not control flow.
"""
from __future__ import annotations

from typing import Any

from ._deps import np
from . import scene_transform as scene_transform_engine
from . import retreat as retreat_engine
from .color import rec2020_to_output
from .constants import GRAY_EV, REC2020_LUMA
from .hdr_agx_math import lift_stops
from .hdr_color import apply_channel_lift, fit_hdr_color_volume, output_luma_weights
from .models import HdrAgxPlan, RawBundle, RenderPlan, ToneCompressionPlan
from .render import apply_tone_core, scene_rec2020_to_float

_EPS = 1e-12


def scene_luminance_ev(scene_rec2020: Any) -> Any:
    """Scene EV relative to mid gray, from Rec.2020 luminance.

    Clamped at the bottom only to keep log2 finite; the value there is irrelevant because
    the allocation window sits far above it and smootherstep clamps to zero anyway.
    """
    rgb = np.asarray(scene_rec2020, dtype=np.float32)
    y = (
        rgb[..., 0] * np.float32(REC2020_LUMA[0])
        + rgb[..., 1] * np.float32(REC2020_LUMA[1])
        + rgb[..., 2] * np.float32(REC2020_LUMA[2])
    )
    return np.log2(np.maximum(y, np.float32(_EPS))) - np.float32(GRAY_EV)


def hdr_lift_factor(scene_rec2020: Any, hdr_plan: HdrAgxPlan) -> Any:
    """Linear gain per pixel: `2 ** (H_budget * S(u))`.

    Exactly 1.0 everywhere when the budget is zero, which is what lets the H=0 HDR render
    be bit-identical to the SDR one rather than merely close.
    """
    tone = hdr_plan.tone
    if float(tone.budget_headroom_ev) <= 0.0:
        return np.ones(np.asarray(scene_rec2020).shape[:-1], dtype=np.float32)
    stops = lift_stops(
        scene_luminance_ev(scene_rec2020),
        float(tone.knee_ev),
        float(tone.white_ev),
        float(tone.budget_headroom_ev),
    )
    return np.exp2(stops).astype(np.float32, copy=False)


def scene_render_to_hdr_display_linear(
    bundle: RawBundle,
    plan: ToneCompressionPlan | RenderPlan,
    hdr_plan: HdrAgxPlan,
    output_gamut: str = "p3",
    scene_transform: str = "none",
    scene_transform_strength: float = 1.0,
) -> Any:
    """Scene-linear -> extended display-linear, values above 1.0 permitted.

    Mirrors scene_render_to_display_linear step for step so that a zero budget reproduces
    it exactly. Display looks and filters are refused rather than ignored: they are SDR
    operators that have not been given HDR meaning, and silently dropping them would make
    the two renditions disagree for a reason no diagnostic would show.
    """
    tone_plan = plan.tone if isinstance(plan, RenderPlan) else plan
    color_plan = plan.color if isinstance(plan, RenderPlan) else None
    if str(getattr(tone_plan, "tone_core", "agx")) == "gated":
        raise RuntimeError("HDR AgX 尚不支持 gated tone core（需要逐像素 RAW 证据的 HDR 语义）")

    scene = bundle.scene_rec2020_render
    h, w = scene.shape[:2]
    flat_scene = scene.reshape(-1, scene.shape[-1])
    out = np.empty((flat_scene.shape[0], 3), dtype=np.float32)
    chunk = 1_000_000

    clip_masks = None
    if color_plan is not None and getattr(bundle, "clip_masks", None) is not None:
        clip_masks = retreat_engine.clip_masks_for_shape(bundle, (h, w)).reshape(-1, 3)

    luma_weights = output_luma_weights(output_gamut)
    # The display cube's ceiling. Budget is what the scene earned; peak is what the
    # display can show, and the fit must respect the latter even if the former is smaller.
    peak = float(2.0 ** hdr_plan.display.display_headroom_ev)

    wb_adapt = scene_transform_engine.wb_adaptation_ratios(
        bundle.wb_mode, bundle.camera_wb, bundle.daylight_wb
    )
    for start in range(0, flat_scene.shape[0], chunk):
        end = min(start + chunk, flat_scene.shape[0])
        rec = scene_rec2020_to_float(
            flat_scene[start:end, :3], bundle.scene_scale, bundle.exposure_gain
        )
        rec = scene_transform_engine.apply_scene_transform_rec2020(
            rec, scene_transform, scene_transform_strength, wb_adapt
        )
        if clip_masks is not None and float(color_plan.raw_clip_retreat_strength) > 0.0:
            rec = retreat_engine.apply_clip_retreat_rec2020(
                rec, clip_masks[start:end], float(color_plan.raw_clip_retreat_strength)
            )
        mapped_rec = apply_tone_core(
            rec,
            tone_plan,
            color_plan,
            clip_masks[start:end] if clip_masks is not None else None,
            None,
        )
        output_linear = rec2020_to_output(mapped_rec, output_gamut)
        output_linear = np.nan_to_num(output_linear, nan=0.0, posinf=1e6, neginf=-1e6)
        # Scene luminance drives the lift, and it is read from `rec` -- before the tone
        # core -- so the allocation is decided by the photograph rather than by where the
        # curve happened to put a pixel. At rho = 0 this is one scalar per pixel, which
        # commutes with the linear Rec.2020 -> P3 matrix and leaves chromaticity exactly
        # where the SDR render put it.
        lifted = apply_channel_lift(
            output_linear,
            rec,
            float(hdr_plan.tone.knee_ev),
            float(hdr_plan.tone.white_ev),
            float(hdr_plan.tone.budget_headroom_ev),
            float(hdr_plan.color.channel_separation),
            luma_weights,
        )
        out[start:end] = fit_hdr_color_volume(lifted, peak, output_gamut).astype(
            np.float32, copy=False
        )
    return out.reshape(h, w, 3)


def to_gainmap_alternate(hdr_display_linear: Any, peak: float) -> Any:
    """Pack the HDR rendition as the float16 RGBA alternate the gain-map writer expects.

    Negatives are clipped here rather than earlier. Up to this point they are legitimate
    out-of-gamut scene colour carried identically by both renditions, and clamping them in
    the formation would have made the two differ by something other than the HDR lift.
    This is the encode boundary, which is where the SDR path resolves them too.
    """
    arr = np.clip(np.asarray(hdr_display_linear, dtype=np.float32), 0.0, float(peak))
    rgba = np.empty(arr.shape[:2] + (4,), dtype=np.float16)
    rgba[:, :, :3] = arr.astype(np.float16, copy=False)
    rgba[:, :, 3] = np.float16(1.0)
    return rgba


def achieved_headroom(hdr_display_linear: Any, percentile: float = 99.99) -> float:
    """H_actual, reported from a percentile rather than the single brightest pixel.

    One specular pixel is not evidence that a render used its headroom, and accepting it
    as such is how a capacity ceiling turns into a normalisation target.
    """
    arr = np.asarray(hdr_display_linear, dtype=np.float32)
    if arr.size == 0:
        return 0.0
    top = float(np.percentile(arr, percentile))
    return float(np.log2(top)) if top > 1.0 else 0.0
