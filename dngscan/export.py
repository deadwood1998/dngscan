# SPDX-License-Identifier: GPL-3.0-or-later
"""SDR and Apple ISO gain-map HDR JPEG export."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ._deps import mpimg, np
from .color import output_gamut_label, output_icc_profile_bytes
from .constants import (
    DEFAULT_HDR_DRT, DEFAULT_HDR_HEADROOM_EV, HDR_DRT_CHOICES,
)
from .gainmap import apple_gainmap_backend_status, write_apple_gainmap_jpeg
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
    hdr_drt: str = DEFAULT_HDR_DRT,
) -> dict[str, Any]:
    """Write a Display P3 JPEG carrying an ISO 21496-1 gain map.

    Both renditions come from the same scene-linear buffer, then enter independent SDR and
    HDR display formations. The gain map is only their delivery representation; it does
    not constrain the HDR rendition to match SDR below a knee. A viewer that ignores it
    still sees the existing SDR photograph.

    That base is the same *rendition* as an ordinary SDR export -- the identical
    render_output_u8 call on the identical plan -- but not the same bytes. Core Image
    writes this file while Pillow writes the SDR one, and two JPEG encoders do not agree
    bit for bit: measured, up to 8/255 on 54 % of pixels at quality 100. The invariant
    worth stating is the one that holds before the encoder, not after it.

    Display looks and filters are refused rather than dropped: they are SDR operators with
    no HDR meaning yet, and silently ignoring them would make the two renditions disagree
    for a reason no diagnostic would surface.
    """
    output_gamut = "p3"
    if str(hdr_drt) not in HDR_DRT_CHOICES:
        raise RuntimeError(f"未知 HDR DRT：{hdr_drt}（可选：{'/'.join(HDR_DRT_CHOICES)}）")
    if look != "none" or display_filter != "none":
        raise RuntimeError(
            "Ultrahdr 第一版仅支持 look=none 与 display_filter=none；"
            "现有 display look/filter 尚未 HDR 化，不能静默忽略"
        )
    supported, reason = apple_gainmap_backend_status()
    if not supported:
        raise RuntimeError(f"Cannot export Apple ISO gain-map HDR JPEG: {reason}")

    try:
        from .grade import RENDER_MODE
        from .hdr_agx import achieved_headroom, scene_render_to_hdr_display_linear, to_gainmap_alternate
        from .hdr_agx_plan import compile_hdr_agx_plan, describe_hdr_plan
        from .models import HdrDisplayTarget, RenderPlan as _RenderPlan
        from .tone import build_render_plan

        plan = tone_plan if tone_plan is not None else build_render_plan(
            bundle, analysis, RENDER_MODE, output_gamut, scene_transform,
            scene_transform_strength, punch_scale, tone_core, lum_norm,
            agx_primaries=agx_primaries,
        )
        if not isinstance(plan, _RenderPlan):
            raise RuntimeError("Ultrahdr 需要完整 RenderPlan")
        if abs(float(plan.color.display_highlight_chroma_retreat)) > 1e-9:
            raise RuntimeError(
                "HDR 尚未定义 SDR 显示侧的高光褪白算子；"
                "请将 highlight fade 设为 0"
            )

        target = HdrDisplayTarget(peak_nits=100.0 * float(2.0 ** float(hdr_headroom)))
        hdr_plan = compile_hdr_agx_plan(
            plan, target, analysis=analysis, scene_decoder=str(bundle.scene_decoder)
        )
        if hdr_plan.tone.budget_headroom_ev <= 0.0:
            raise RuntimeError(
                "该场景的可靠高光尾部不支持任何 HDR 余量："
                f"{describe_hdr_plan(hdr_plan)}。请改用 --output-format sdr"
            )

        base_u8 = render_output_u8(
            bundle, analysis, output_gamut, plan, look, look_strength, display_filter,
            filter_strength, scene_transform, scene_transform_strength, tone_core,
            lum_norm, agx_primaries,
        )
        hdr_linear = scene_render_to_hdr_display_linear(
            bundle, plan, hdr_plan, output_gamut, scene_transform, scene_transform_strength
        )
        # A robust p99.99 headroom is useful for the report, but it is not the container
        # declaration. The writer derives Apple content headroom from the exact finite peak
        # of the packed rendition and verifies it after round-trip.
        actual = achieved_headroom(hdr_linear)
        peak = float(2.0 ** hdr_plan.tone.display_headroom_ev)
        info = write_apple_gainmap_jpeg(
            base_u8, to_gainmap_alternate(hdr_linear, peak), out_path, quality,
            hdr_plan.tone.display_headroom_ev,
        )
        info["hdr_plan"] = describe_hdr_plan(hdr_plan)
        info["budget_headroom_ev"] = float(hdr_plan.tone.budget_headroom_ev)
        info["actual_headroom_ev"] = float(actual)
        info["channel_separation"] = float(hdr_plan.color.channel_separation)
        return info
    except RuntimeError:
        raise
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
    hdr_drt: str = DEFAULT_HDR_DRT,
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
            hdr_drt=hdr_drt,
        )
    if output_format != "sdr":
        raise ValueError(f"unknown output format: {output_format}")
    return export_srgb_jpeg(
        path, out_path, quality, bundle, analysis, tone_plan, output_gamut, subsampling,
        look, look_strength, display_filter, filter_strength,
        scene_transform, scene_transform_strength,
        tone_core, lum_norm, agx_primaries, return_rgb,
    )
