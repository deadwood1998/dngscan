# SPDX-License-Identifier: GPL-3.0-or-later
"""SDR and Apple ISO gain-map HDR JPEG export."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ._deps import mpimg, np
from .color import output_gamut_label, output_icc_profile_bytes
from .constants import DEFAULT_HDR_HEADROOM_EV
from .gainmap import build_hdr_alternate_rgba_half, write_apple_gainmap_jpeg
from .models import Analysis, RawBundle, RenderPlan, ToneCompressionPlan
from .render import render_output_u8


def chroma_to_subsampling(name: str) -> int:
    # PIL subsampling: 0 = 4:4:4 (full chroma), 1 = 4:2:2, 2 = 4:2:0 (smallest).
    return {"444": 0, "422": 1, "420": 2}.get(name, 0)


def save_jpeg_array(
    rgb_u8: Any, out_path: Path, quality: int, output_gamut: str = "srgb", subsampling: int = 0
) -> bool:
    if mpimg is None:
        raise RuntimeError("matplotlib.image is not available; cannot write JPEG")
    try:
        import PIL  # noqa: F401
    except Exception as exc:
        raise RuntimeError("JPEG 导出需要 Pillow，请先安装 pillow 再重试") from exc
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if rgb_u8.dtype != np.uint8:
        rgb_u8 = np.clip(rgb_u8, 0, 255).astype(np.uint8)
    pil_kwargs: dict[str, Any] = {"quality": int(quality), "subsampling": int(subsampling), "optimize": True}
    icc_profile = output_icc_profile_bytes(output_gamut)
    if icc_profile is not None:
        pil_kwargs["icc_profile"] = icc_profile
    mpimg.imsave(
        str(out_path),
        rgb_u8,
        format="jpeg",
        pil_kwargs=pil_kwargs,
    )
    return icc_profile is not None


def export_ultrahdr_jpeg(
    path: Path,
    out_path: Path,
    quality: int,
    bundle: RawBundle,
    analysis: Analysis,
    tone_plan: ToneCompressionPlan | RenderPlan | None = None,
    hdr_headroom: float = DEFAULT_HDR_HEADROOM_EV,
    look: str = "none",
    look_strength: float = 1.0,
    display_filter: str = "none",
    filter_strength: float = 1.0,
    scene_transform: str = "none",
    scene_transform_strength: float = 1.0,
    tone_core: str = "agx",
    lum_norm: str = "y",
    agx_primaries: str = "base",
    punch_scale: float = 1.0,
) -> bool:
    output_gamut = "p3"
    try:
        from .grade import RENDER_MODE
        from .tone import build_render_plan

        # When no plan is supplied, honour the caller's core/primaries so a no-plan HDR
        # export cannot silently diverge from the matching SDR settings.
        plan = tone_plan if tone_plan is not None else build_render_plan(
            bundle, analysis, RENDER_MODE, output_gamut, scene_transform, scene_transform_strength,
            punch_scale, tone_core, lum_norm, agx_primaries=agx_primaries,
        )
        base_u8 = render_output_u8(
            bundle,
            analysis,
            output_gamut,
            plan,
            look,
            look_strength,
            display_filter,
            filter_strength,
            scene_transform,
            scene_transform_strength,
            tone_core,
            lum_norm,
            agx_primaries,
        )
        hdr_rgba = build_hdr_alternate_rgba_half(
            base_u8,
            bundle,
            plan,
            hdr_headroom,
            scene_transform,
            scene_transform_strength,
        )
        write_apple_gainmap_jpeg(base_u8, hdr_rgba, out_path, quality, hdr_headroom)
        return True
    except Exception as exc:
        raise RuntimeError(f"Cannot export Apple ISO gain-map HDR JPEG: {exc}") from exc


def export_srgb_jpeg(
    path: Path,
    out_path: Path,
    quality: int,
    bundle: RawBundle,
    analysis: Analysis,
    tone_plan: ToneCompressionPlan | RenderPlan | None = None,
    output_gamut: str = "srgb",
    subsampling: int = 0,
    look: str = "none",
    look_strength: float = 1.0,
    display_filter: str = "none",
    filter_strength: float = 1.0,
    scene_transform: str = "none",
    scene_transform_strength: float = 1.0,
    tone_core: str = "agx",
    lum_norm: str = "y",
    agx_primaries: str = "base",
    return_rgb: bool = False,
) -> Any:
    try:
        rgb = render_output_u8(
            bundle, analysis, output_gamut, tone_plan,
            look, look_strength, display_filter, filter_strength,
            scene_transform, scene_transform_strength,
            tone_core, lum_norm, agx_primaries,
        )
        embedded = save_jpeg_array(rgb, out_path, quality, output_gamut, subsampling)
        return (embedded, rgb) if return_rgb else embedded
    except Exception as exc:
        raise RuntimeError(f"Cannot export 8-bit {output_gamut_label(output_gamut)} JPEG: {exc}") from exc


def export_jpeg(
    path: Path,
    out_path: Path,
    quality: int,
    bundle: RawBundle,
    analysis: Analysis,
    tone_plan: ToneCompressionPlan | RenderPlan | None = None,
    output_gamut: str = "srgb",
    output_format: str = "sdr",
    hdr_headroom: float = DEFAULT_HDR_HEADROOM_EV,
    subsampling: int = 0,
    look: str = "none",
    look_strength: float = 1.0,
    display_filter: str = "none",
    filter_strength: float = 1.0,
    scene_transform: str = "none",
    scene_transform_strength: float = 1.0,
    tone_core: str = "agx",
    lum_norm: str = "y",
    agx_primaries: str = "base",
    punch_scale: float = 1.0,
    return_rgb: bool = False,
) -> Any:
    if output_format == "ultrahdr":
        return export_ultrahdr_jpeg(
            path,
            out_path,
            quality,
            bundle,
            analysis,
            tone_plan,
            hdr_headroom,
            look,
            look_strength,
            display_filter,
            filter_strength,
            scene_transform,
            scene_transform_strength,
            tone_core,
            lum_norm,
            agx_primaries,
            punch_scale,
        )
    if output_format != "sdr":
        raise ValueError(f"unknown output format: {output_format}")
    return export_srgb_jpeg(
        path, out_path, quality, bundle, analysis, tone_plan, output_gamut, subsampling,
        look, look_strength, display_filter, filter_strength,
        scene_transform, scene_transform_strength,
        tone_core, lum_norm, agx_primaries, return_rgb,
    )
