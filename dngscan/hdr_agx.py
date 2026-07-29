# SPDX-License-Identifier: GPL-3.0-or-later
"""dngscan's independent extended-white HDR AgX formation.

The HDR and SDR renditions share capture data and scene intent, then split before display
formation. HDR owns its curve, extended colour volume and scene-driven white endpoint; it
never renders or corrects against SDR pixels. The endpoint is solved inside AgX itself:

    inset -> native extended-white C1 sigmoid -> hue restore/outset

`rho` mixes only chromaticity between a reference-white AgX path and the native extended
path, both normalized to the native curve's luminance. CFA masks can withdraw that freedom
locally. The completed image receives a bounded extended-P3 neutral-axis projection. The
projector preserves linear Y and an RGB opponent direction, not a perceptual CAM hue.

The two dispatchers share AgX primitives, but their completed curves are independent and
no pixel region is required to match between SDR and HDR.
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
from .hdr_curve import apply_hdr_curve
from .hdr_color import (
    blend_native_hdr_paths,
    fit_hdr_color_volume,
    formation_luma_weights,
    raw_gated_channel_separation,
)
from .models import HdrAgxPlan, RawBundle, RenderPlan, ToneCompressionPlan
from .render import scene_rec2020_to_float

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

    # The scene-authorized native curve endpoint. Display capacity is only the container's
    # outer ceiling; using it here would normalize every photograph to display peak.
    peak = float(hdr_plan.tone.peak_linear)
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
        # Both chroma candidates run the same v2 primitive and share one compiled knee
        # anchor, so they leave the body at identical value and slope and differ only in
        # where they head. A tone difference between them would confound every rho A/B.
        native_formation = apply_hdr_curve(inset, hdr_plan.tone, hdr_tone_plan)
        reference_formation = (
            native_formation
            if float(hdr_plan.tone.rendered_headroom_ev) <= 0.0
            else apply_hdr_curve(inset, hdr_plan.tone, hdr_tone_plan, peak_linear=1.0)
        )
        rho = float(hdr_plan.color.channel_separation) * float(hdr_plan.color.snr_gate)
        rho = raw_gated_channel_separation(
            rho, clip_masks[start:end] if clip_masks is not None else None
        )
        formation = blend_native_hdr_paths(
            reference_formation,
            native_formation,
            rho,
            formation_y,
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
