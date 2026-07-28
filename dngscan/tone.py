# SPDX-License-Identifier: GPL-3.0-or-later
"""Exposure gain and analysis-driven tone compression plans."""
from __future__ import annotations

import math
from dataclasses import replace
from typing import Any

from ._deps import np
from . import agx as agx_engine
from . import scene_transform as scene_transform_engine
from .color import (
    apply_rgb_matrix3, clamp_float, luminance_from_rgb_space, output_gamut_space,
    rec2020_to_xyz, XYZ_TO_RGB,
)
from .constants import EPS, EV_REPORT_FLOOR, GAMUT_EPS, GRAY_EV, MIDGRAY_HEADROOM_STOPS
from . import retreat as retreat_engine
from .models import (
    Analysis, ColorGeometryPlan, RawBundle, RenderAdjustments, RenderPlan,
    SceneToneMetrics, ToneCompressionPlan,
)

TONE_CORE_CHOICES = ("gated", "agx", "lum", "neutral")
LUM_NORM_CHOICES = ("y", "power", "max")


def exposure_mode_for_tone_core(tone_core: str) -> str:
    """Exposure anchor for every tone core.

    All cores including neutral share the same fixed mid-gray reference so manual/auto
    EV means the same thing when A/B-ing against AgX. Only the tone operator differs.
    """
    return "agx"


def neutral_tone_plan(target_gamut: str) -> ToneCompressionPlan:
    """Fixed Y-ratio diagnostic curve, not a production or camera-render baseline.

    Endpoints are constants, not compiled from scene body/tail statistics. The operator
    is luminance-ratio compression only; AgX inset/outset and scene C1 planning are
    skipped. It intentionally exposes what ratio-preserving color does near a narrow
    output-gamut boundary; saturated highlights may look harder or more neon than AgX.
    Shared EV anchor and delivery gamut fit still apply. CFA clip retreat applies only
    when the selected capture decoder has a spatial mask.
    """
    from .neutral import (
        NEUTRAL_BLACK_EV, NEUTRAL_CONTRAST, NEUTRAL_SHOULDER_POWER,
        NEUTRAL_TOE_POWER, NEUTRAL_WHITE_EV,
    )

    return ToneCompressionPlan(
        target_gamut=target_gamut,
        luma_p1=0.0,
        luma_p50=0.0,
        luma_p99=0.0,
        luma_p999=0.0,
        black_ev=NEUTRAL_BLACK_EV,
        white_ev=NEUTRAL_WHITE_EV,
        dynamic_range_ev=NEUTRAL_WHITE_EV - NEUTRAL_BLACK_EV,
        contrast=NEUTRAL_CONTRAST,
        toe_power=NEUTRAL_TOE_POWER,
        shoulder_power=NEUTRAL_SHOULDER_POWER,
        chroma_p95=0.0,
        negative_rgb_pct=0.0,
        over_rgb_pct=0.0,
        tone_core="neutral",
        lum_norm="y",
        use_c1_endpoints=False,
        view_brightness=1.0,
    )

def compute_exposure_gain(mode: str, ev: float) -> float:
    """Constant, content-independent exposure anchor plus manual EV compensation.

    Every tone core uses the same anchor: a nominally-exposed mid gray (~clip /
    2**headroom) maps to 0.18 scene-linear Rec.2020 at EV=0, then manual EV scales
    from there. This is a fixed scalar, never derived from scene content.
    """
    manual = 2.0 ** float(ev)
    return 0.18 * (2.0 ** MIDGRAY_HEADROOM_STOPS) * manual


def scene_rec2020_to_float(
    values: Any,
    scene_scale: float,
    gain: float = 1.0,
    *,
    contract: Any | None = None,
) -> Any:
    """Convert stored decoder RGB to intent-scene float32.

    Prefer passing ``contract`` (:class:`~dngscan.models.SceneScaleContract`) so
    callers do not have to reassemble ``storage_scale`` and ``total_render_gain``.
    The legacy ``scene_scale`` / ``gain`` pair remains for migration callers and
    must stay bit-identical to the contract path.
    """
    if contract is not None:
        scale = float(contract.storage_scale)
        gain = float(contract.total_render_gain)
    else:
        scale = float(scene_scale)
    if not np.isfinite(scale) or scale <= 0.0:
        scale = 1.0
    # A float decoder can legitimately carry scene_scale below one (for example when an
    # A/B alignment gain is above one). Clamping the divisor to one would make analysis
    # and rendering disagree exactly in that case.
    rgb = values.astype(np.float32, copy=False) / np.float32(scale)
    if gain != 1.0:
        rgb = rgb * np.float32(gain)
    return np.nan_to_num(rgb, nan=0.0, posinf=1e6, neginf=0.0)


def subsample_step(pixel_count: int, max_samples: int = 800_000) -> int:
    return max(1, int(math.ceil(pixel_count / max_samples)))


def rank_trim_reconstructed_highlights(
    ev: Any, valid: Any, clipped_cell_pct: float
) -> Any:
    """Exclude a RAW-measured fraction of the brightest reconstructed samples.

    Core Image applies DNG warps, so LibRaw's CFA clip mask cannot be mapped to its
    pixels without a calibrated geometric transform. The aggregate clipped-cell rate is
    still valid. Removing that fraction from the top of the luminance ranking restores
    the body/tail contract without pretending that the two frames align spatially.
    """
    values = np.asarray(ev, dtype=np.float32)
    keep = np.asarray(valid, dtype=bool).copy()
    indices = np.flatnonzero(keep)
    if indices.size == 0:
        return keep
    fraction = clamp_float(float(clipped_cell_pct) / 100.0, 0.0, 1.0)
    trim_count = int(math.ceil(indices.size * fraction))
    min_keep = max(256, indices.size // 20)
    trim_count = min(trim_count, max(0, indices.size - min_keep))
    if trim_count <= 0:
        return keep
    ranked = values[indices]
    top = np.argpartition(ranked, ranked.size - trim_count)[-trim_count:]
    keep[indices[top]] = False
    return keep


def tone_plan_sample_scene_rec2020(
    bundle: RawBundle,
    max_samples: int = 800_000,
    scene_transform: str = "none",
    scene_transform_strength: float = 1.0,
    exposure_gain: float | None = None,
) -> Any:
    flat = bundle.scene_rec2020_render.reshape(-1, bundle.scene_rec2020_render.shape[-1])
    step = subsample_step(flat.shape[0], max_samples)
    gain = bundle.exposure_gain if exposure_gain is None else exposure_gain
    rec2020 = scene_rec2020_to_float(flat[::step, :3], bundle.scene_scale, gain)
    wb_adapt = scene_transform_engine.wb_adaptation_ratios(
        bundle.wb_mode, bundle.camera_wb, bundle.daylight_wb
    )
    return scene_transform_engine.apply_scene_transform_rec2020(
        rec2020, scene_transform, scene_transform_strength, wb_adapt
    )


def scene_tone_metrics(
    bundle: RawBundle,
    analysis: Analysis,
    scene_transform: str = "none",
    scene_transform_strength: float = 1.0,
    plan_exposure_gain: float | None = None,
    max_samples: int = 800_000,
) -> SceneToneMetrics:
    """Measure the reliable scene body separately from its highlight tail.

    Reconstruction may make a clipped lamp visually plausible, but it cannot restore its
    sensor headroom. On LibRaw we therefore exclude soft CFA-clipped sites from body
    percentiles. Core Image's opcode geometry prevents spatial reuse, so that path removes
    the aggregate clipped-cell fraction from the brightest luminance ranks instead. The
    complete rendered tail remains available only for topology classification.
    """
    flat = bundle.scene_rec2020_render.reshape(-1, bundle.scene_rec2020_render.shape[-1])
    step = subsample_step(flat.shape[0], max_samples)
    gain = bundle.exposure_gain if plan_exposure_gain is None else plan_exposure_gain
    rec = scene_rec2020_to_float(flat[::step, :3], bundle.scene_scale, gain)
    wb_adapt = scene_transform_engine.wb_adaptation_ratios(
        bundle.wb_mode, bundle.camera_wb, bundle.daylight_wb
    )
    rec = scene_transform_engine.apply_scene_transform_rec2020(
        rec, scene_transform, scene_transform_strength, wb_adapt
    )
    y = np.clip(rec2020_to_xyz(rec)[:, 1], 2.0 ** EV_REPORT_FLOOR, None)
    ev = np.log2(y) - GRAY_EV

    # Samples sitting at the representable floor are clamped values, not measurements:
    # the decoder produced zero (or below one code value) and the clip above pinned them
    # to EV_REPORT_FLOOR. Letting them into the body percentiles is the black-end twin of
    # letting reconstructed highlights define the white point, so they are excluded while
    # enough real samples remain.
    #
    # This was written when the Core Image path left 1.78 % of pixels on the floor against
    # LibRaw's 0.001 %, and the cause was misread as Apple's black handling. It was not:
    # CIRAWFilter's shadowBias defaults to 5.0 and had not been zeroed, and once it was
    # (see coreimage_decode) that path leaves 0.006..0.18 %, below LibRaw's own 0.16..1.9 %.
    # The exclusion stays because it is right for any decoder — LibRaw reaches the floor
    # too, and a clamped sample is not evidence whoever produced it — but it is a
    # correctness guard, not a workaround for one back end.
    floor_ev = float(EV_REPORT_FLOOR) - GRAY_EV
    above_floor = ev > (floor_ev + 1e-3)
    reliable = above_floor.copy()
    if getattr(bundle, "clip_masks", None) is not None:
        masks = retreat_engine.clip_masks_for_shape(bundle, bundle.scene_rec2020_render.shape[:2])
        reliable &= np.max(masks.reshape(-1, 3)[::step], axis=1) < np.float32(0.10)
    elif getattr(bundle, "scene_decoder", "libraw") == "coreimage":
        # RAW 9's reconstructed highlight pixels are geometrically warped relative to
        # the CFA mosaic. Use the full-resolution RAW clipped-cell rate as a rank-domain
        # constraint so those invented values cannot compile the global white endpoint.
        reliable = rank_trim_reconstructed_highlights(ev, reliable, analysis.cell_union_pct)
    if int(np.count_nonzero(reliable)) < max(256, ev.size // 20):
        reliable = above_floor if int(np.count_nonzero(above_floor)) >= 256 else np.ones_like(above_floor)
    reliable_ev = ev[reliable]
    if reliable_ev.size < max(256, ev.size // 20):
        reliable_ev = ev
        reliable = np.ones((ev.shape[0],), dtype=bool)

    p1, p5, p50, p95, p99, p999 = [
        float(v) for v in np.percentile(reliable_ev, [1.0, 5.0, 50.0, 95.0, 99.0, 99.9])
    ]
    tail_p9999 = float(np.percentile(ev, 99.99))
    reliable_tail_p9999 = float(np.percentile(reliable_ev, 99.99))
    tail0 = float(np.mean(ev > 0.0) * 100.0)
    tail2 = float(np.mean(ev > 2.0) * 100.0)
    extremity = tail2 / max(tail0, 1e-4)
    sparse_emitter = bool(tail0 < 3.0 and extremity > 0.12)
    return SceneToneMetrics(
        reliable_sample_pct=float(np.mean(reliable) * 100.0),
        body_ev_p1=p1,
        body_ev_p5=p5,
        body_ev_p50=p50,
        body_ev_p95=p95,
        body_ev_p99=p99,
        body_ev_p999=p999,
        tail_ev_p9999=tail_p9999,
        tail_area_ev0_pct=tail0,
        tail_area_ev2_pct=tail2,
        tail_extremity=extremity,
        sparse_emitter_tail=sparse_emitter,
        raw_clip_union_pct=float(analysis.cell_union_pct),
        reliable_tail_ev_p9999=reliable_tail_p9999,
    )


def build_color_geometry_plan(
    analysis: Analysis, output_gamut: str, tone_core: str = "agx"
) -> ColorGeometryPlan:
    space = output_gamut_space(output_gamut)
    pressure = float(analysis.gamut_out_pct.get(space, 0.0))
    # The output fit reacts slightly sooner in the smaller sRGB container and grows its
    # adaptive-L0 safety margin as measured output-gamut pressure rises. It remains a
    # colour-only decision: no tone endpoint or contrast parameter reads this value.
    base_alpha = 0.045 if output_gamut == "p3" else 0.060
    alpha = base_alpha + 0.015 * clamp_float(pressure / 5.0, 0.0, 1.0)
    if tone_core == "gated":
        noise_floor = -12.0
        if math.isfinite(analysis.usable_dr_eff_ev):
            noise_floor = -float(analysis.usable_dr_eff_ev) - 1.0
        return ColorGeometryPlan(
            target_gamut=output_gamut,
            raw_clip_retreat_strength=0.0,
            output_gamut_pressure_pct=pressure,
            gamut_fit_alpha=alpha,
            display_highlight_chroma_retreat=0.28,
            color_path_master=1.0,
            gated_midtone_protect=0.92,
            color_path_highlight_ev_lo=0.25,
            color_path_highlight_ev_hi=2.75,
            gated_noise_ev_floor=noise_floor,
        )
    return ColorGeometryPlan(
        target_gamut=output_gamut,
        # Every non-gated core requests clip retreat, but it executes only when the
        # capture decoder supplied a spatial CFA mask. RAW 9 has aggregate evidence only.
        raw_clip_retreat_strength=1.0,
        output_gamut_pressure_pct=pressure,
        gamut_fit_alpha=alpha,
        display_highlight_chroma_retreat=0.35 if tone_core == "lum" else 0.0,
    )


def _smoothstep_f(edge0: float, edge1: float, x: float) -> float:
    """Scalar smoothstep retained for the separate AgX colour-punch gate."""
    t = clamp_float((x - edge0) / max(edge1 - edge0, 1e-9), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def build_tone_compression_plan(
    bundle: RawBundle,
    analysis: Analysis,
    target_gamut: str,
    ev_from_agx_inset: bool = False,
    scene_transform: str = "none",
    scene_transform_strength: float = 1.0,
    punch_scale: float = 1.0,
    tone_core: str = "agx",
    lum_norm: str = "y",
    agx_primaries: str = "base",
    plan_exposure_gain: float | None = None,
    scene_metrics: SceneToneMetrics | None = None,
) -> ToneCompressionPlan:
    agx_primaries = agx_engine.resolve_agx_primaries(agx_primaries)
    if tone_core == "neutral":
        return neutral_tone_plan(target_gamut)

    plan_gain = plan_exposure_gain if plan_exposure_gain is not None else bundle.exposure_gain
    metrics = scene_metrics if scene_metrics is not None else scene_tone_metrics(
        bundle,
        analysis,
        scene_transform,
        scene_transform_strength,
        plan_gain,
    )
    rec2020 = tone_plan_sample_scene_rec2020(
        bundle, scene_transform=scene_transform, scene_transform_strength=scene_transform_strength,
        exposure_gain=plan_gain,
    )
    xyz = rec2020_to_xyz(rec2020)
    y = np.clip(xyz[:, 1], 0.0, None)
    ev_p1 = metrics.body_ev_p1
    ev_p50 = metrics.body_ev_p50
    ev_p99 = metrics.body_ev_p99
    ev_p999 = metrics.body_ev_p999
    luma_p1, luma_p50, luma_p99, luma_p999 = [float(v) for v in np.percentile(y, [1.0, 50.0, 99.0, 99.9])]

    plan_dr = analysis.usable_dr_eff_ev if math.isfinite(analysis.usable_dr_eff_ev) else analysis.usable_dr_ev
    if math.isfinite(plan_dr):
        noise_limited_black = -plan_dr - 1.5
    else:
        noise_limited_black = -12.0
    black_ev = max(ev_p1 - 0.25, noise_limited_black)
    black_ev = clamp_float(black_ev, -14.0, -1.5)
    # darktable's C1 curve starts toe/shoulder at the pivot by default. Do not map a
    # dark scene's p95 directly to the shoulder: when p95 < 0 EV, that segment crosses
    # the calibrated pivot and creates exactly the dark-frame / glaring-lamp failure.
    latitude_lo_ev = 0.10
    latitude_hi_ev = 0.20 if not metrics.sparse_emitter_tail else 0.0
    toe_start_ev = -latitude_lo_ev
    shoulder_start_ev = latitude_hi_ev

    # The complete tail describes topology (for example, sparse emitters), but has no
    # authority over the global white endpoint: reconstructed/RAW-clipped values are not
    # measured scene radiometry. Only the reliable tail may set the shoulder endpoint.
    white_margin = 0.50 if metrics.sparse_emitter_tail else 0.30
    min_white_ev = 3.50 if metrics.sparse_emitter_tail else 3.00
    reliable_white_tail = metrics.reliable_tail_ev_p9999
    if not math.isfinite(reliable_white_tail):
        reliable_white_tail = metrics.tail_ev_p9999
    white_ev = max(reliable_white_tail + white_margin, min_white_ev)
    white_ev = clamp_float(white_ev, min_white_ev, 8.5)

    # These are strictly tone decisions. Colour clipping and output gamut live in
    # ColorGeometryPlan and must not change either curve endpoint or pivot contrast.
    dynamic_range_ev = white_ev - black_ev
    contrast = 3.0
    dark_body = clamp_float((-metrics.body_ev_p50 - 1.5) / 3.0, 0.0, 1.0)
    toe_power = 1.50 - 0.35 * dark_body
    shoulder_power = 2.55 if metrics.sparse_emitter_tail else 2.90
    # Scene-adaptive pivot stays OFF, now for a measured reason rather than an unsolved
    # constraint. agx.curve_params can hold the EV0 -> 18% anchor while the pivot moves
    # (bisection on the pivot output, see Ev0AnchorSolverTest), but measuring both ends
    # of that trade on a -3.4 EV night frame refutes the idea itself: anchoring EV0
    # crushes the subject (output at -2 EV falls 0.024 -> 0.007) for no contrast gain,
    # while preserving subject brightness instead drives EV0 to 0.95 — nearly white.
    # A pivot move needs contrast/toe/shoulder re-solved with it (darktable relies on
    # the user for exactly that); one automatic knob cannot do it. Left compiled-in and
    # tested so the capability is ready if that 2-D solve is ever attempted.
    pivot_ev_offset = 0.0
    target_black_linear = 0.0
    shadow_quality = _smoothstep_f(5.5, 8.5, plan_dr) if math.isfinite(plan_dr) else 0.5
    view_brightness = 1.0 + 0.30 * dark_body * shadow_quality
    # Punch is a post-core chroma operator, not a tone decision: it is calculated after
    # endpoint selection and cannot feed back into pivot, toe, shoulder or exposure.
    # The luminance core deliberately stays at zero because it already retains the
    # original RGB ratio through the body; neutral is a diagnostic reference.
    if tone_core in ("agx", "gated"):
        w_bright = _smoothstep_f(-3.0, -1.2, metrics.body_ev_p50)
        w_quality = _smoothstep_f(7.5, 9.5, plan_dr) if math.isfinite(plan_dr) else 0.5
        w_dr = _smoothstep_f(6.5, 8.0, dynamic_range_ev)
        punch_strength = clamp_float(
            w_bright * w_quality * (0.55 + 0.45 * w_dr) * clamp_float(punch_scale, 0.0, 1.5),
            0.0,
            1.0,
        )
    else:
        punch_strength = 0.0

    if target_gamut == "Rec2020":
        rgb = rec2020
    else:
        rgb = apply_rgb_matrix3(xyz, XYZ_TO_RGB[target_gamut])
    rgb = np.nan_to_num(rgb, nan=0.0, posinf=1e6, neginf=-1e6)
    anchor = np.maximum(y, 0.0)
    chroma_ratio = np.max(np.abs(rgb - anchor[:, None]), axis=1) / np.maximum(anchor, EPS)
    finite_chroma = chroma_ratio[np.isfinite(chroma_ratio) & (anchor > 2.0 ** EV_REPORT_FLOOR)]
    chroma_p95 = float(np.percentile(finite_chroma, 95.0)) if finite_chroma.size else 0.0

    negative_rgb_pct = float(np.mean(np.min(rgb, axis=1) < -GAMUT_EPS) * 100.0)
    over_rgb_pct = float(np.mean(np.max(rgb, axis=1) > 1.0 + GAMUT_EPS) * 100.0)
    # The pinned darktable scene default uses Blender-like/base geometry with 60% hue
    # restoration. Its sigmoid-like smooth preset deliberately disables hue restoration.
    hue_restore = 0.0 if agx_primaries == "smooth" else 0.6

    return ToneCompressionPlan(
        target_gamut=target_gamut,
        luma_p1=luma_p1,
        luma_p50=luma_p50,
        luma_p99=luma_p99,
        luma_p999=luma_p999,
        black_ev=black_ev,
        white_ev=white_ev,
        dynamic_range_ev=dynamic_range_ev,
        contrast=contrast,
        toe_power=toe_power,
        shoulder_power=shoulder_power,
        latitude_lo_ev=latitude_lo_ev,
        latitude_hi_ev=latitude_hi_ev,
        punch_strength=punch_strength,
        chroma_p95=chroma_p95,
        negative_rgb_pct=negative_rgb_pct,
        over_rgb_pct=over_rgb_pct,
        tone_core=tone_core,
        lum_norm=lum_norm,
        pivot_ev_offset=pivot_ev_offset,
        target_black_linear=target_black_linear,
        target_white_linear=1.0,
        agx_primaries=agx_primaries,
        hue_restore=hue_restore,
        toe_start_ev=toe_start_ev,
        shoulder_start_ev=shoulder_start_ev,
        use_c1_endpoints=True,
        view_brightness=view_brightness,
    )


def apply_render_adjustments(
    plan: RenderPlan, adjustments: RenderAdjustments | None
) -> RenderPlan:
    """Apply restrained user biases without recompiling the scene analysis.

    The calibrated pivot and scene-compiled endpoints remain authoritative. Tone controls alter
    only the local curve shape; highlight fade is a display-side chroma control. The
    fixed neutral reference intentionally ignores all of these adjustments.
    """
    if (
        adjustments is None
        or adjustments.is_identity()
        or plan.tone.tone_core == "neutral"
    ):
        return plan

    brightness_bias = clamp_float(float(adjustments.midtone_brightness), -1.0, 1.0)
    contrast_bias = clamp_float(float(adjustments.midtone_contrast), -1.0, 1.0)
    shadow_bias = clamp_float(float(adjustments.shadow_transition), -1.0, 1.0)
    highlight_bias = clamp_float(float(adjustments.highlight_transition), -1.0, 1.0)
    fade_bias = clamp_float(float(adjustments.highlight_fade), -1.0, 1.0)

    tone = replace(
        plan.tone,
        # At 18% gray this range is approximately -0.5 to +0.4 display EV. It is a
        # darktable-style interior power, not scene exposure, so both endpoints hold.
        view_brightness=clamp_float(
            float(plan.tone.view_brightness) * (2.0 ** (0.25 * brightness_bias)),
            0.65,
            1.65,
        ),
        contrast=clamp_float(
            float(plan.tone.contrast) * (2.0 ** (0.25 * contrast_bias)),
            1.5,
            4.5,
        ),
        # Positive UI direction means a more open toe and a softer shoulder. Lower
        # endpoint powers produce those two shapes in the C1 solver.
        toe_power=clamp_float(
            float(plan.tone.toe_power) * (2.0 ** (-0.45 * shadow_bias)),
            0.65,
            2.5,
        ),
        shoulder_power=clamp_float(
            float(plan.tone.shoulder_power) * (2.0 ** (-0.45 * highlight_bias)),
            1.25,
            5.0,
        ),
    )
    color = replace(
        plan.color,
        display_highlight_chroma_retreat=clamp_float(
            float(plan.color.display_highlight_chroma_retreat) + 0.30 * fade_bias,
            -0.30,
            0.70,
        ),
        display_highlight_chroma_start=clamp_float(
            float(plan.color.display_highlight_chroma_start) - 0.12 * fade_bias,
            0.58,
            0.90,
        ),
    )
    return replace(plan, tone=tone, color=color)


def build_render_plan(
    bundle: RawBundle,
    analysis: Analysis,
    mode: str,
    output_gamut: str = "srgb",
    scene_transform: str = "none",
    scene_transform_strength: float = 1.0,
    punch_scale: float = 1.0,
    tone_core: str = "agx",
    lum_norm: str = "y",
    agx_primaries: str = "base",
    adjustments: RenderAdjustments | None = None,
) -> RenderPlan:
    """Compile independent scene, tone and colour plans from an immutable capture."""
    tone_core = tone_core if tone_core in TONE_CORE_CHOICES else "agx"
    lum_norm = lum_norm if lum_norm in LUM_NORM_CHOICES else "y"
    agx_primaries = agx_engine.resolve_agx_primaries(agx_primaries)
    if tone_core == "gated":
        from .guidance import ensure_raw_guidance

        ensure_raw_guidance(bundle, analysis)
    # All four current cores operate in the Rec.2020 working space; the
    # `else` stays a defensive fallback for any future output-space-native core.
    if mode == "agx" or tone_core in ("lum", "neutral", "gated"):
        target_gamut = "Rec2020"
    else:
        target_gamut = output_gamut_space(output_gamut)
    plan_gain = compute_exposure_gain(exposure_mode_for_tone_core(tone_core), 0.0)
    scene = scene_tone_metrics(
        bundle, analysis, scene_transform if mode == "agx" else "none",
        scene_transform_strength, plan_gain,
    )
    tone = build_tone_compression_plan(
        bundle,
        analysis,
        target_gamut,
        ev_from_agx_inset=False,
        scene_transform=scene_transform if mode == "agx" else "none",
        scene_transform_strength=scene_transform_strength,
        punch_scale=punch_scale if mode == "agx" else 0.0,
        tone_core=tone_core,
        lum_norm=lum_norm,
        # RAW-gated rendering uses the same pinned darktable scene-default geometry as
        # full-frame AgX. RAW evidence changes permission to use that color path, not the
        # definition of the path itself.
        agx_primaries=agx_primaries if mode == "agx" and tone_core == "agx" else "base",
        plan_exposure_gain=plan_gain,
        scene_metrics=scene,
    )
    plan = RenderPlan(
        tone=tone,
        color=build_color_geometry_plan(analysis, output_gamut, tone_core),
        scene=scene,
    )
    return apply_render_adjustments(plan, adjustments)


def plan_for_mode(
    bundle: RawBundle,
    analysis: Analysis,
    mode: str,
    output_gamut: str = "srgb",
    scene_transform: str = "none",
    scene_transform_strength: float = 1.0,
    punch_scale: float = 1.0,
    tone_core: str = "agx",
    lum_norm: str = "y",
    agx_primaries: str = "base",
    adjustments: RenderAdjustments | None = None,
) -> ToneCompressionPlan:
    """Compatibility accessor for callers that only need the tone sub-plan."""
    return build_render_plan(
        bundle,
        analysis,
        mode,
        output_gamut,
        scene_transform,
        scene_transform_strength,
        punch_scale,
        tone_core,
        lum_norm,
        agx_primaries,
        adjustments,
    ).tone
