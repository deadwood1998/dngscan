# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent ACES 2-derived HDR renderer with SDR low/mid bridge."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ._deps import np
from .aces2 import render_aces2_hdr_p3_linear
from .aces2.constants import P3_RGB_TO_XYZ, REFERENCE_LUMINANCE
from .aces2.jmh import jmh_to_rgb, rgb_to_jmh
from .color import luminance_from_rec2020, srgb_decode
from .constants import DIFFUSE_WHITE_EV, EPS, GRAY_EV
from .hdr_evidence import build_hdr_evidence_maps
from .hdr_tone import build_hdr_render_plan, clamp_hdr_capacity_ev
from .models import Analysis, HdrRenderDiagnostics, HdrRenderPlan, RawBundle, RenderPlan
from .render import render_output_linear
from .tone import scene_rec2020_to_float


@dataclass(frozen=True)
class HdrRenderResult:
    hdr_linear_p3: Any
    diagnostics: HdrRenderDiagnostics
    plan: HdrRenderPlan


def _p3_luminance(rgb: Any) -> Any:
    # ACES matrices use row-vector layout; Y is the second matrix column.
    m = P3_RGB_TO_XYZ
    rgb = np.asarray(rgb, dtype=np.float32)
    return (m[0, 1] * rgb[..., 0] + m[1, 1] * rgb[..., 1] + m[2, 1] * rgb[..., 2]).astype(
        np.float32, copy=False
    )


def midgray_match_scale_for_plans(
    sdr_plan: RenderPlan,
    capacity_ev: float,
    *,
    reference_white_nits: float = 100.0,
) -> float:
    """Content-independent mid-gray match from a neutral 0.18 patch.

    Never reads photograph percentiles. Scale lives in the dual-rendition bridge,
    not inside the ACES 2 reference kernel.
    """
    from .color import rec2020_to_output
    from .render import apply_tone_core

    patch = np.full((1, 3), 0.18, dtype=np.float32)
    mapped = apply_tone_core(patch, sdr_plan.tone, sdr_plan.color, None, None)
    if mapped.ndim == 1:
        mapped = mapped.reshape(1, 3)
    elif mapped.ndim == 3:
        mapped = mapped.reshape(-1, 3)
    sdr = rec2020_to_output(mapped, "p3")
    y_sdr = float(_p3_luminance(sdr.reshape(1, 1, 3))[0, 0])

    capacity = clamp_hdr_capacity_ev(capacity_ev)
    hdr = render_aces2_hdr_p3_linear(
        patch.reshape(1, 1, 3), capacity_ev=capacity, reference_white_nits=reference_white_nits
    )
    y_hdr = float(_p3_luminance(hdr)[0, 0])
    if y_hdr <= EPS:
        return 1.0
    scale = y_sdr / y_hdr
    # Soft clamp extreme mismatches; keep within ~±2 stops.
    return float(np.clip(scale, 0.25, 4.0))


def _intent_scene_rec2020(
    bundle: RawBundle,
    *,
    scene_transform: str,
    scene_transform_strength: float,
) -> Any:
    from . import scene_transform as scene_transform_engine

    scene = np.asarray(bundle.scene_rec2020_render)
    flat = scene.reshape(-1, scene.shape[-1])
    out = np.empty((flat.shape[0], 3), dtype=np.float32)
    chunk = 1_000_000
    wb_adapt = scene_transform_engine.wb_adaptation_ratios(
        bundle.wb_mode, bundle.camera_wb, bundle.daylight_wb
    )
    for start in range(0, flat.shape[0], chunk):
        end = min(start + chunk, flat.shape[0])
        rec = scene_rec2020_to_float(flat[start:end, :3], bundle.scene_scale, bundle.exposure_gain)
        out[start:end] = scene_transform_engine.apply_scene_transform_rec2020(
            rec, scene_transform, scene_transform_strength, wb_adapt
        )
    return out.reshape(scene.shape[0], scene.shape[1], 3)


def _c1_reveal(scene_ev: Any, start_ev: float, end_ev: float) -> Any:
    span = max(1e-6, float(end_ev) - float(start_ev))
    t = (np.asarray(scene_ev, dtype=np.float32) - np.float32(start_ev)) / np.float32(span)
    t = np.clip(t, 0.0, 1.0)
    return (t * t * (3.0 - 2.0 * t)).astype(np.float32, copy=False)


def _upsample_bilinear(map2d: Any, shape: tuple[int, int]) -> Any:
    src = np.asarray(map2d, dtype=np.float32)
    th, tw = int(shape[0]), int(shape[1])
    if src.shape[:2] == (th, tw):
        return src
    from PIL import Image

    image = Image.fromarray(src, mode="F")
    resized = image.resize((tw, th), Image.Resampling.BILINEAR)
    return np.asarray(resized, dtype=np.float32)


def _relative_p3_to_jmh(rgb: Any, peak_luminance: float) -> Any:
    """Convert P3 linear values relative to 100-nit white into display JMh.

    ``rgb_to_jmh`` follows the ACES output-transform convention where 1.0 is
    display peak.  SDR/HDR bridge buffers instead use 1.0 == reference white.
    """
    peak_scale = float(peak_luminance) / float(REFERENCE_LUMINANCE)
    return rgb_to_jmh(
        np.asarray(rgb, dtype=np.float64) / peak_scale,
        P3_RGB_TO_XYZ,
        float(peak_luminance),
    )


def _jmh_to_relative_p3(jmh: Any, peak_luminance: float) -> Any:
    """Inverse of :func:`_relative_p3_to_jmh`."""
    peak = float(peak_luminance)
    rgb_w = np.full(3, REFERENCE_LUMINANCE, dtype=np.float64)
    xyz_w = rgb_w @ P3_RGB_TO_XYZ
    xyz_to_rgb = np.linalg.inv(P3_RGB_TO_XYZ)
    peak_scale = peak / float(REFERENCE_LUMINANCE)
    return jmh_to_rgb(jmh, xyz_to_rgb, peak, xyz_w) * peak_scale


def _fit_p3_to_peak_preserve_y(rgb: Any, max_linear: float) -> Any:
    """Fit extended-linear P3 to its RGB cube along the neutral axis.

    The fit preserves P3 luminance whenever it lies inside the display range and
    reduces chroma instead of clipping channels independently.  This is the final
    bridge safety limit; ACES already handles the candidate's color geometry.
    """
    from .aces2.white_limiting import fit_rgb_to_peak_preserve_y

    fitted = fit_rgb_to_peak_preserve_y(rgb, P3_RGB_TO_XYZ, float(max_linear))
    return fitted.astype(np.float32, copy=False)


def _bridge_jmh(
    sdr_linear: Any,
    hdr_candidate: Any,
    reveal: Any,
    peak_luminance: float,
) -> Any:
    """Blend SDR and HDR candidates in JMh; enforce Y_hdr >= Y_sdr without channel max."""
    s = np.asarray(sdr_linear, dtype=np.float32)
    a = np.asarray(hdr_candidate, dtype=np.float32)
    w = np.asarray(reveal, dtype=np.float32)
    if w.ndim == 2:
        w = w[..., None]
    out = s.copy()
    mask = w[..., 0] > 1e-6
    if not np.any(mask):
        return out

    peak = float(peak_luminance)
    max_linear = peak / float(REFERENCE_LUMINANCE)
    s_sel = s[mask]
    a_sel = a[mask]
    w_sel = w[mask, 0]

    jmh_s = _relative_p3_to_jmh(s_sel, peak)
    jmh_a = _relative_p3_to_jmh(a_sel, peak)
    # Cartesian hue interpolation.
    ms, hs = jmh_s[..., 1], np.deg2rad(jmh_s[..., 2])
    ma, ha = jmh_a[..., 1], np.deg2rad(jmh_a[..., 2])
    xs = ms * np.cos(hs)
    ys = ms * np.sin(hs)
    xa = ma * np.cos(ha)
    ya = ma * np.sin(ha)
    x = xs * (1.0 - w_sel) + xa * w_sel
    y = ys * (1.0 - w_sel) + ya * w_sel
    j = jmh_s[..., 0] * (1.0 - w_sel) + jmh_a[..., 0] * w_sel
    # J must not fall below SDR J.
    j = np.maximum(j, jmh_s[..., 0])
    m = np.sqrt(x * x + y * y)
    h = np.rad2deg(np.arctan2(y, x))
    h = np.where(h < 0.0, h + 360.0, h)
    blended = np.stack([j, m, h], axis=-1)
    rgb = _jmh_to_relative_p3(blended, peak).astype(np.float32)

    y_s = _p3_luminance(s_sel)
    y_h = _p3_luminance(rgb)
    # If interpolation lowered luminance, restore the SDR floor by scaling at
    # constant chromaticity. The final neutral-axis fit preserves that luminance.
    need = y_h < (y_s - 1e-6)
    if np.any(need):
        valid = need & (y_h > 1e-10) & np.isfinite(y_h)
        rgb[valid] *= (y_s[valid] / y_h[valid])[:, None]
        rgb[need & ~valid] = s_sel[need & ~valid]

    rgb = _fit_p3_to_peak_preserve_y(rgb, max_linear)
    # Numerical or pathological fallback: exact SDR is always a valid lower bound.
    still = _p3_luminance(rgb) < (y_s - 2e-5)
    if np.any(still):
        rgb[still] = s_sel[still]

    out[mask] = rgb
    # Exact bypass for zero-reveal pixels already set to S.
    return np.nan_to_num(out, nan=0.0, posinf=max_linear, neginf=0.0)


def render_hdr_p3_linear(
    bundle: RawBundle,
    analysis: Analysis,
    sdr_plan: RenderPlan,
    sdr_encoded_linear_p3: Any,
    *,
    capacity_ev: float = 3.0,
    scene_transform: str = "none",
    scene_transform_strength: float = 1.0,
) -> HdrRenderResult:
    """Render bridged HDR extended-linear Display P3 from intent scene + SDR base."""
    capacity = clamp_hdr_capacity_ev(capacity_ev)
    if capacity <= 1e-9:
        sdr = np.asarray(sdr_encoded_linear_p3, dtype=np.float32)
        diag = HdrRenderDiagnostics(
            actual_content_headroom=1.0,
            peak_luminance_ratio=float(np.max(_p3_luminance(sdr)) if sdr.size else 1.0),
            pct_above_reference_white=0.0,
            pct_above_2x=0.0,
            pct_above_4x=0.0,
            min_channel_gain=(1.0, 1.0, 1.0),
            max_channel_gain=(1.0, 1.0, 1.0),
            sdr_hdr_midgray_delta_ev=0.0,
        )
        plan = build_hdr_render_plan(
            bundle, analysis, capacity_ev=0.0, midgray_match_scale=1.0
        )
        return HdrRenderResult(hdr_linear_p3=sdr.copy(), diagnostics=diag, plan=plan)

    match = midgray_match_scale_for_plans(sdr_plan, capacity)
    evidence = build_hdr_evidence_maps(
        bundle,
        analysis,
        scene_transform=scene_transform,
        scene_transform_strength=scene_transform_strength,
    )
    plan = build_hdr_render_plan(
        bundle,
        analysis,
        capacity_ev=capacity,
        scene_transform=scene_transform,
        scene_transform_strength=scene_transform_strength,
        midgray_match_scale=match,
        evidence=evidence,
    )

    intent = _intent_scene_rec2020(
        bundle,
        scene_transform=scene_transform,
        scene_transform_strength=scene_transform_strength,
    )
    candidate = render_aces2_hdr_p3_linear(
        intent,
        capacity_ev=capacity,
        reference_white_nits=plan.target.reference_white_nits,
    ).astype(np.float32, copy=False)
    candidate = candidate * np.float32(match)

    scene_y = luminance_from_rec2020(intent.reshape(-1, 3)).reshape(intent.shape[:2])
    scene_ev = np.log2(np.maximum(scene_y, np.float32(EPS))) - np.float32(GRAY_EV)
    reveal = _c1_reveal(scene_ev, plan.color.reveal_start_ev, plan.color.reveal_end_ev)
    # Fold evidence permission (upsampled).
    perm = _upsample_bilinear(evidence.reveal_permission, reveal.shape)
    reveal = np.minimum(reveal, np.maximum(perm, reveal * 0.25 + perm * 0.75))

    sdr = np.asarray(sdr_encoded_linear_p3, dtype=np.float32)
    peak = float(plan.target.peak_nits)
    hdr = _bridge_jmh(sdr, candidate, reveal, peak)
    # The bridge performs a luminance-preserving neutral-axis gamut fit. Keep an
    # assertion here instead of a second per-channel clip that could create plateaus.
    max_lin = float(2.0 ** capacity)
    if np.any(hdr < -1e-6) or np.any(hdr > max_lin + 1e-5):
        raise RuntimeError("HDR bridge produced values outside the Display P3 peak cube")

    y_s = _p3_luminance(sdr)
    y_h = _p3_luminance(hdr)
    gain = (hdr + 1.0 / 64.0) / (sdr + 1.0 / 64.0)
    actual = float(np.max(hdr)) if hdr.size else 1.0
    mid_mask = (y_s >= 0.09) & (y_s <= 0.36) & (y_h > EPS)
    mid_delta = (
        float(np.median(np.log2(np.maximum(y_h[mid_mask], EPS) / y_s[mid_mask])))
        if np.any(mid_mask)
        else 0.0
    )
    diag = HdrRenderDiagnostics(
        actual_content_headroom=max(1.0, actual),
        peak_luminance_ratio=float(np.max(y_h)) if y_h.size else 0.0,
        pct_above_reference_white=float(np.mean(y_h > 1.0) * 100.0) if y_h.size else 0.0,
        pct_above_2x=float(np.mean(y_h > 2.0) * 100.0) if y_h.size else 0.0,
        pct_above_4x=float(np.mean(y_h > 4.0) * 100.0) if y_h.size else 0.0,
        min_channel_gain=tuple(float(x) for x in np.min(gain.reshape(-1, 3), axis=0)),
        max_channel_gain=tuple(float(x) for x in np.max(gain.reshape(-1, 3), axis=0)),
        sdr_hdr_midgray_delta_ev=mid_delta,
    )
    return HdrRenderResult(hdr_linear_p3=hdr, diagnostics=diag, plan=plan)


def build_hdr_alternate_from_dual_rendition(
    bundle: RawBundle,
    analysis: Analysis,
    sdr_plan: RenderPlan,
    sdr_u8_p3: Any,
    *,
    capacity_ev: float,
    scene_transform: str = "none",
    scene_transform_strength: float = 1.0,
) -> tuple[Any, HdrRenderDiagnostics, HdrRenderPlan]:
    """Encode-linear SDR denominator + bridged HDR alternate as float16 RGBA."""
    sdr_u8 = np.asarray(sdr_u8_p3)
    sdr_linear = srgb_decode(sdr_u8.astype(np.float32) / np.float32(255.0))
    result = render_hdr_p3_linear(
        bundle,
        analysis,
        sdr_plan,
        sdr_linear,
        capacity_ev=capacity_ev,
        scene_transform=scene_transform,
        scene_transform_strength=scene_transform_strength,
    )
    rgba = np.empty(result.hdr_linear_p3.shape[:2] + (4,), dtype=np.float16)
    rgba[..., :3] = result.hdr_linear_p3.astype(np.float16, copy=False)
    rgba[..., 3] = np.float16(1.0)
    return rgba, result.diagnostics, result.plan
