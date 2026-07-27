# SPDX-License-Identifier: GPL-3.0-or-later
"""JPEG / Ultra HDR export writers."""
from __future__ import annotations

import platform
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ._deps import mpimg, np
from .color import EPS, luminance_from_rgb_space, output_gamut_label, output_icc_profile_bytes
from .constants import DEFAULT_GAINMAP_SCALE, DEFAULT_HDR_HEADROOM_EV
from .models import Analysis, GainMapMetadata, RawBundle, RenderPlan, ToneCompressionPlan
from .render import (
    output_linear_to_u8,
    render_hdr_numerator_linear,
    render_output_u8,
    scene_render_to_display_linear,
)

def resize_gainmap_u8(gain_u8: Any, scale: int) -> Any:
    if scale <= 1:
        return gain_u8
    from PIL import Image

    h, w = gain_u8.shape[:2]
    target = (max(1, round(w / scale)), max(1, round(h / scale)))
    im = Image.fromarray(gain_u8)
    return np.asarray(im.resize(target, Image.Resampling.LANCZOS), dtype=np.uint8)


def compute_gainmap_u8(
    sdr_linear: Any,
    hdr_linear: Any,
    output_gamut: str,
    hdr_headroom: float,
    gamma: float = 1.0,
    scale: int = DEFAULT_GAINMAP_SCALE,
) -> Any:
    sdr_base = np.clip(np.nan_to_num(sdr_linear.reshape(-1, 3), nan=0.0, posinf=1.0, neginf=0.0), 0.0, 1.0)
    hdr = np.clip(
        np.nan_to_num(hdr_linear.reshape(-1, 3), nan=0.0, posinf=2.0 ** hdr_headroom, neginf=0.0),
        0.0,
        2.0 ** hdr_headroom,
    )
    sdr_y = luminance_from_rgb_space(sdr_base, output_gamut)
    hdr_y = luminance_from_rgb_space(np.maximum(hdr, sdr_base), output_gamut)
    gain_stops = np.log2(np.maximum(hdr_y, sdr_y + EPS) / np.maximum(sdr_y, EPS))
    gain_stops = np.clip(np.nan_to_num(gain_stops, nan=0.0, posinf=hdr_headroom, neginf=0.0), 0.0, hdr_headroom)
    normalized = gain_stops.reshape(sdr_linear.shape[:2]) / max(hdr_headroom, EPS)
    if gamma != 1.0:
        normalized = np.power(np.clip(normalized, 0.0, 1.0), 1.0 / gamma)
    gain_u8 = np.clip(np.round(normalized * 255.0), 0, 255).astype(np.uint8)
    return resize_gainmap_u8(gain_u8, scale)


def build_gainmap_metadata(hdr_headroom: float, scale: int) -> GainMapMetadata:
    return GainMapMetadata(
        headroom=float(hdr_headroom),
        gamma=1.0,
        min_gain=0.0,
        max_gain=float(hdr_headroom),
        hdr_capacity_min=0.0,
        hdr_capacity_max=float(hdr_headroom),
        gainmap_scale=int(scale),
    )


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


def imageio_gainmap_backend_status() -> tuple[bool, str]:
    if platform.system() != "Darwin":
        return False, "ISO gain-map JPEG 写入当前只实现了 macOS ImageIO 后端"
    try:
        import Quartz  # type: ignore
    except Exception as exc:
        return False, f"缺少 PyObjC Quartz 绑定：{exc}；请安装 pyobjc-framework-Quartz"
    required = [
        "CGImageDestinationCreateWithURL",
        "CGImageDestinationAddImage",
        "CGImageDestinationAddAuxiliaryDataInfo",
        "CGImageDestinationFinalize",
        "kCGImageAuxiliaryDataTypeISOGainMap",
    ]
    missing = [name for name in required if not hasattr(Quartz, name)]
    if missing:
        return False, "当前 macOS/PyObjC ImageIO 不暴露 ISO gain-map API：" + ", ".join(missing)
    return True, "macOS ImageIO ISO gain-map backend available"


def _nsdata_from_bytes(data: bytes) -> Any:
    from Foundation import NSData  # type: ignore

    return NSData.dataWithBytes_length_(data, len(data))


def _cgimage_from_rgba_u8(rgba: Any, color_space: Any) -> Any:
    import Quartz  # type: ignore

    h, w = rgba.shape[:2]
    provider = Quartz.CGDataProviderCreateWithCFData(_nsdata_from_bytes(rgba.tobytes(order="C")))
    bitmap_info = Quartz.kCGImageAlphaLast | Quartz.kCGBitmapByteOrder32Big
    image = Quartz.CGImageCreate(
        w,
        h,
        8,
        32,
        w * 4,
        color_space,
        bitmap_info,
        provider,
        None,
        False,
        Quartz.kCGRenderingIntentDefault,
    )
    if image is None:
        raise RuntimeError("无法创建 SDR 底图 CGImage")
    return image


def _cgimage_from_gray_u8(gray: Any, color_space: Any) -> Any:
    import Quartz  # type: ignore

    h, w = gray.shape[:2]
    provider = Quartz.CGDataProviderCreateWithCFData(_nsdata_from_bytes(gray.tobytes(order="C")))
    image = Quartz.CGImageCreate(
        w,
        h,
        8,
        8,
        w,
        color_space,
        Quartz.kCGImageAlphaNone,
        provider,
        None,
        False,
        Quartz.kCGRenderingIntentDefault,
    )
    if image is None:
        raise RuntimeError("无法创建 gain map CGImage")
    return image


def _display_color_space(output_gamut: str) -> Any:
    import Quartz  # type: ignore

    if output_gamut == "p3":
        name = getattr(Quartz, "kCGColorSpaceDisplayP3", None)
    else:
        name = getattr(Quartz, "kCGColorSpaceSRGB", None)
    if name is not None:
        cs = Quartz.CGColorSpaceCreateWithName(name)
        if cs is not None:
            return cs
    return Quartz.CGColorSpaceCreateDeviceRGB()


def _gainmap_aux_description(gainmap_u8: Any, meta: GainMapMetadata) -> dict[str, Any]:
    import Quartz  # type: ignore

    h, w = gainmap_u8.shape[:2]
    description: dict[str, Any] = {
        Quartz.kCGImagePropertyWidth: int(w),
        Quartz.kCGImagePropertyHeight: int(h),
        Quartz.kCGImagePropertyBytesPerRow: int(w),
        # ISO 21496-1 stores logarithmic gain. These string keys are kept in the
        # description so non-ImageIO fallback backends can map the same metadata.
        "HDRCapacityMin": float(meta.hdr_capacity_min),
        "HDRCapacityMax": float(meta.hdr_capacity_max),
        "BaseHeadroom": 0.0,
        "AlternateHeadroom": float(meta.headroom),
        "GainMapMin": float(meta.min_gain),
        "GainMapMax": float(meta.max_gain),
        "GainMapGamma": float(meta.gamma),
        "GainMapScale": int(meta.gainmap_scale),
    }
    if hasattr(Quartz, "kCGImagePropertyPixelFormat"):
        description[Quartz.kCGImagePropertyPixelFormat] = "L008"
    return description


def write_gainmap_jpeg(
    base_rgb_u8: Any,
    gainmap_u8: Any,
    meta: GainMapMetadata,
    out_path: Path,
    quality: int,
    output_gamut: str = "p3",
) -> bool:
    ok, reason = imageio_gainmap_backend_status()
    if not ok:
        raise RuntimeError(reason)

    import Quartz  # type: ignore
    from Foundation import NSURL  # type: ignore

    if output_gamut != "p3":
        raise RuntimeError("Ultra HDR JPEG 当前强制使用 Display P3 SDR 底图")
    if base_rgb_u8.dtype != np.uint8:
        base_rgb_u8 = np.clip(base_rgb_u8, 0, 255).astype(np.uint8)
    if gainmap_u8.dtype != np.uint8:
        gainmap_u8 = np.clip(gainmap_u8, 0, 255).astype(np.uint8)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    color_space = _display_color_space(output_gamut)
    gray_space = Quartz.CGColorSpaceCreateDeviceGray()
    alpha = np.full(base_rgb_u8.shape[:2] + (1,), 255, dtype=np.uint8)
    base_image = _cgimage_from_rgba_u8(np.concatenate([base_rgb_u8, alpha], axis=2), color_space)
    gain_image = _cgimage_from_gray_u8(gainmap_u8, gray_space)

    url = NSURL.fileURLWithPath_(str(out_path))
    dest = Quartz.CGImageDestinationCreateWithURL(url, "public.jpeg", 1, None)
    if dest is None:
        raise RuntimeError(f"无法创建 JPEG 写入目标：{out_path}")

    props: dict[Any, Any] = {
        Quartz.kCGImageDestinationLossyCompressionQuality: float(quality) / 100.0,
        Quartz.kCGImagePropertyColorModel: Quartz.kCGImagePropertyColorModelRGB,
    }
    icc_profile = output_icc_profile_bytes(output_gamut)
    if icc_profile is not None:
        props[Quartz.kCGImagePropertyProfileName] = output_gamut_label(output_gamut)
    Quartz.CGImageDestinationAddImage(dest, base_image, props)

    aux_info: dict[Any, Any] = {
        Quartz.kCGImageAuxiliaryDataInfoData: _nsdata_from_bytes(gainmap_u8.tobytes(order="C")),
        Quartz.kCGImageAuxiliaryDataInfoDataDescription: _gainmap_aux_description(gainmap_u8, meta),
        Quartz.kCGImageAuxiliaryDataInfoColorSpace: gray_space,
    }
    if hasattr(Quartz, "kCGImageAuxiliaryDataInfoImage"):
        aux_info[getattr(Quartz, "kCGImageAuxiliaryDataInfoImage")] = gain_image
    Quartz.CGImageDestinationAddAuxiliaryDataInfo(dest, Quartz.kCGImageAuxiliaryDataTypeISOGainMap, aux_info)
    if not Quartz.CGImageDestinationFinalize(dest):
        raise RuntimeError("ImageIO 写入 ISO gain-map JPEG 失败")
    return True


def ultrahdr_app_path() -> Path | None:
    candidates = [
        shutil.which("ultrahdr_app"),
        "/opt/homebrew/opt/libultrahdr/bin/ultrahdr_app",
        "/usr/local/opt/libultrahdr/bin/ultrahdr_app",
    ]
    for candidate in candidates:
        if candidate:
            path = Path(candidate)
            if path.is_file():
                return path
    return None


def write_ultrahdr_jpeg_libultrahdr(
    base_rgb_u8: Any,
    hdr_linear: Any,
    meta: GainMapMetadata,
    out_path: Path,
    quality: int,
    output_gamut: str = "p3",
) -> bool:
    app = ultrahdr_app_path()
    if app is None:
        raise RuntimeError("未找到 ultrahdr_app；可安装 Homebrew libultrahdr 作为 fallback")
    if output_gamut != "p3":
        raise RuntimeError("Ultra HDR JPEG 当前强制使用 Display P3 SDR 底图")
    if base_rgb_u8.dtype != np.uint8:
        base_rgb_u8 = np.clip(base_rgb_u8, 0, 255).astype(np.uint8)

    h, w = base_rgb_u8.shape[:2]
    hdr_limit = np.float32(2.0 ** meta.headroom)
    hdr = np.clip(np.nan_to_num(hdr_linear, nan=0.0, posinf=float(hdr_limit), neginf=0.0), 0.0, float(hdr_limit))
    rgba = np.empty((h, w, 4), dtype=np.float16)
    rgba[:, :, :3] = hdr.astype(np.float16, copy=False)
    rgba[:, :, 3] = np.float16(1.0)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dngscan_ultrahdr_") as td:
        temp_dir = Path(td)
        base_path = temp_dir / "base_p3.jpg"
        hdr_path = temp_dir / "hdr_rgba_half.raw"
        save_jpeg_array(base_rgb_u8, base_path, quality, output_gamut)
        hdr_path.write_bytes(rgba.tobytes(order="C"))
        cmd = [
            str(app),
            "-m",
            "0",
            "-p",
            str(hdr_path),
            "-i",
            str(base_path),
            "-w",
            str(w),
            "-h",
            str(h),
            "-a",
            "4",  # rgba half float
            "-t",
            "0",  # linear
            "-C",
            "1",  # HDR intent P3
            "-c",
            "1",  # SDR intent P3
            "-s",
            str(max(1, int(meta.gainmap_scale))),
            "-M",
            "0",  # single-channel gain map
            "-Q",
            str(int(quality)),
            "-q",
            str(int(quality)),
            "-G",
            f"{meta.gamma:.6g}",
            "-k",
            "1.0",
            "-K",
            f"{2.0 ** meta.headroom:.6g}",
            "-z",
            str(out_path),
        ]
        result = subprocess.run(cmd, check=False, capture_output=True, text=True)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"ultrahdr_app 编码失败：{detail}")
    if not out_path.exists() or out_path.stat().st_size <= 0:
        raise RuntimeError("ultrahdr_app 未生成输出 JPEG")
    return True


def export_ultrahdr_jpeg(
    path: Path,
    out_path: Path,
    quality: int,
    bundle: RawBundle,
    analysis: Analysis,
    tone_plan: ToneCompressionPlan | RenderPlan | None = None,
    hdr_headroom: float = DEFAULT_HDR_HEADROOM_EV,
    gainmap_scale: int = DEFAULT_GAINMAP_SCALE,
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
        sdr_linear = scene_render_to_display_linear(
            bundle,
            plan,
            output_gamut,
            scene_transform=scene_transform,
            scene_transform_strength=scene_transform_strength,
        )
        hdr_linear = render_hdr_numerator_linear(bundle, sdr_linear, output_gamut, hdr_headroom)
        base_u8 = output_linear_to_u8(
            sdr_linear,
            output_gamut,
            color_plan=plan.color if isinstance(plan, RenderPlan) else None,
        )
        meta = build_gainmap_metadata(hdr_headroom, gainmap_scale)
        if ultrahdr_app_path() is not None:
            return write_ultrahdr_jpeg_libultrahdr(base_u8, hdr_linear, meta, out_path, quality, output_gamut)
        gainmap = compute_gainmap_u8(sdr_linear, hdr_linear, output_gamut, hdr_headroom, meta.gamma, gainmap_scale)
        try:
            return write_gainmap_jpeg(base_u8, gainmap, meta, out_path, quality, output_gamut)
        except Exception as imageio_exc:
            try:
                return write_ultrahdr_jpeg_libultrahdr(base_u8, hdr_linear, meta, out_path, quality, output_gamut)
            except Exception as fallback_exc:
                raise RuntimeError(f"ImageIO 后端失败：{imageio_exc}; libultrahdr fallback 也失败：{fallback_exc}") from fallback_exc
    except Exception as exc:
        raise RuntimeError(f"Cannot export Ultra HDR gain-map JPEG: {exc}") from exc


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
    gainmap_scale: int = DEFAULT_GAINMAP_SCALE,
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
        if look != "none" or display_filter != "none":
            raise ValueError("成片风格暂不支持 Ultra HDR 输出（SDR/HDR 底图一致性优先）")
        return export_ultrahdr_jpeg(
            path,
            out_path,
            quality,
            bundle,
            analysis,
            tone_plan,
            hdr_headroom,
            gainmap_scale,
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
