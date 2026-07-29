# SPDX-License-Identifier: GPL-3.0-or-later
"""dngscan's independent HDR extension around darktable-style AgX formation.

The HDR and SDR renditions share capture data and scene intent, then split before display
formation.  HDR owns its formation plan, extended colour volume and scene-driven highlight
allocation.  It never renders or corrects against SDR pixels.  Extra display stops are
inserted after the HDR per-channel curve and before hue restore/outset:

    p = f * 2 ** (H_budget * S(u(e_Y)))

`rho` continuously mixes common luminance progress with inset-channel progress. CFA masks
can withdraw that freedom locally, and a formation-space luminance normalization prevents
rho from becoming a hidden tone control. The completed image then receives hue
restore/outset and a bounded extended-P3 neutral-axis projection. The final projector
preserves linear Y and an RGB opponent direction, not a perceptual colour-appearance hue.

The two dispatchers may share AgX primitives, but neither their completed curves nor the
pixels below the HDR allocation knee are an equality contract.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from ._deps import np
from . import agx as agx_engine
from . import punch as punch_engine
from . import scene_transform as scene_transform_engine
from . import retreat as retreat_engine
from .color import rec2020_to_output
from .constants import GRAY_EV, REC2020_LUMA
from .hdr_agx_math import lift_stops
from .hdr_color import (
    apply_channel_lift,
    fit_hdr_color_volume,
    formation_luma_weights,
    raw_gated_channel_separation,
)
from .models import HdrAgxPlan, RawBundle, RenderPlan, ToneCompressionPlan
from .render import scene_rec2020_to_float

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

    Exactly 1.0 everywhere when the budget is zero. This is an identity of the HDR
    allocation stage, not a promise that the completed HDR and SDR renditions are equal.
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

    Display looks and filters are refused by the exporter rather than ignored: they are SDR
    operators that have not been given an independent HDR meaning.
    """
    source_tone = plan.tone if isinstance(plan, RenderPlan) else plan
    if str(getattr(source_tone, "tone_core", "agx")) != "agx":
        raise RuntimeError("HDR AgX 仅支持 tone_core=agx；不能把其他 SDR tone core 标成 HDR AgX")

    # HdrColorGeometry is the source of truth for the HDR branch. The values currently
    # start from shared scene intent, but both the tone object and geometry are HDR-owned.
    hdr_tone_plan = replace(
        hdr_plan.formation,
        hue_restore=float(hdr_plan.color.hue_restore),
        agx_primaries=str(hdr_plan.color.primaries_preset),
    )
    inset_matrix, outset_matrix = agx_engine.formation_matrices(hdr_tone_plan)
    formation_y = formation_luma_weights(outset_matrix)

    scene = bundle.scene_rec2020_render
    h, w = scene.shape[:2]
    flat_scene = scene.reshape(-1, scene.shape[-1])
    out = np.empty((flat_scene.shape[0], 3), dtype=np.float32)
    chunk = 1_000_000

    clip_masks = None
    if getattr(bundle, "clip_masks", None) is not None:
        clip_masks = retreat_engine.clip_masks_for_shape(bundle, (h, w)).reshape(-1, 3)

    # The scene-authorized HDR colour volume. Display capacity is only the outer ceiling;
    # using it here would let outset/gamut geometry spend stops the reliable RAW tail never
    # earned even when the explicit allocation stayed within budget.
    peak = float(2.0 ** hdr_plan.tone.budget_headroom_ev)
    peak *= max(0.0, 1.0 - float(hdr_plan.color.gamut_fit_margin))

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
        retreat_strength = float(hdr_plan.color.raw_clip_retreat)
        if clip_masks is not None and retreat_strength > 0.0:
            rec = retreat_engine.apply_clip_retreat_rec2020(
                rec, clip_masks[start:end], retreat_strength
            )
        inset, pre_hue = agx_engine.prepare_formation(rec, hdr_tone_plan, inset_matrix)
        base_formation = agx_engine.apply_formation_curve(inset, hdr_tone_plan)
        rho = float(hdr_plan.color.channel_separation) * float(hdr_plan.color.snr_gate)
        rho = raw_gated_channel_separation(
            rho, clip_masks[start:end] if clip_masks is not None else None
        )
        formation = apply_channel_lift(
            base_formation,
            rec,
            float(hdr_plan.tone.knee_ev),
            float(hdr_plan.tone.white_ev),
            float(hdr_plan.tone.budget_headroom_ev),
            rho,
            formation_y,
            channel_scene_rgb=inset,
        )
        mapped_rec = agx_engine.finish_formation(
            formation, pre_hue, hdr_tone_plan, outset_matrix
        )
        mapped_rec = punch_engine.apply_punch_rec2020(
            mapped_rec, float(getattr(hdr_tone_plan, "punch_strength", 0.0))
        )
        output_linear = rec2020_to_output(mapped_rec, output_gamut)
        output_linear = np.nan_to_num(output_linear, nan=0.0, posinf=1e6, neginf=-1e6)
        out[start:end] = fit_hdr_color_volume(output_linear, peak, output_gamut).astype(
            np.float32, copy=False
        )
    return out.reshape(h, w, 3)


def to_gainmap_alternate(hdr_display_linear: Any, peak: float) -> Any:
    """Pack the HDR rendition as the float16 RGBA alternate the gain-map writer expects.

    The HDR renderer normally returns an in-volume image. Clipping remains here as a
    defensive encode-boundary guard for callers that provide their own rendition.
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
