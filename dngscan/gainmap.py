# SPDX-License-Identifier: GPL-3.0-or-later
"""Apple-native ISO 21496-1 gain-map JPEG construction.

The SDR primary remains the ordinary, fully rendered Display P3 JPEG image. The HDR
alternate differs only by a scene-supported luminance gain above diffuse white. One
reliable scene stop restores at most one display stop; the user headroom is a capacity
ceiling, not a normalization target. Core Image derives the ISO gain map from this
coupled SDR/HDR pair and writes the container metadata.
"""
from __future__ import annotations

import math
import os
import platform
import tempfile
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any

from ._deps import np
from .color import luminance_from_rec2020, srgb_decode
from .constants import EPS, GRAY_EV
from .models import RawBundle, RenderPlan, ToneCompressionPlan
from . import scene_transform as scene_transform_engine
from .tone import scene_rec2020_to_float


HDR_BUILD_CHUNK = 1_000_000
HDR_DIFFUSE_WHITE_LINEAR = 0.90
HDR_DIFFUSE_WHITE_EV = math.log2(HDR_DIFFUSE_WHITE_LINEAR / 0.18)
HDR_GAIN_ROLLIN_EV = 0.50
HDR_GAIN_CAP_ROLLOFF_EV = 0.50


def _apple_gainmap_api_status() -> tuple[bool, str]:
    """Report API availability without claiming RGB round-trip correctness."""
    if platform.system() != "Darwin":
        return False, "HDR gain-map JPEG 当前需要 macOS Core Image"
    try:
        import Quartz  # type: ignore
    except Exception as exc:
        return False, f"缺少 PyObjC Quartz 绑定：{exc}；请安装 pyobjc-framework-Quartz"

    required = (
        "CIContext",
        "CIImage",
        "kCIFormatRGBA8",
        "kCIFormatRGBAh",
        "kCIImageRepresentationHDRImage",
        "kCIImageRepresentationHDRGainMapAsRGB",
        "kCIImageAuxiliaryHDRGainMap",
        "kCGImageDestinationEncodeRequest",
        "kCGImageDestinationEncodeToISOGainmap",
        "kCGImageDestinationEncodeRequestOptions",
        "kCGImageDestinationEncodeBaseIsSDR",
        "kCGColorSpaceDisplayP3",
        "kCGColorSpaceExtendedLinearDisplayP3",
    )
    missing = [name for name in required if not hasattr(Quartz, name)]
    if missing:
        return False, "当前 macOS/PyObjC 不暴露 ISO gain-map 编码 API：" + ", ".join(missing)
    if not hasattr(Quartz.CIContext, "writeJPEGRepresentationOfImage_toURL_colorSpace_options_error_"):
        return False, "当前 Core Image 不支持直接写入 HDR JPEG"
    return True, "Apple Core Image ISO 21496-1 gain-map APIs available"


def _read_expanded_hdr_rgba_half(path: Path) -> Any:
    """Decode the composite HDR rendition, not merely the auxiliary container."""
    import Quartz  # type: ignore
    from Foundation import NSURL  # type: ignore

    linear_p3 = Quartz.CGColorSpaceCreateWithName(Quartz.kCGColorSpaceExtendedLinearDisplayP3)
    url = NSURL.fileURLWithPath_(str(path))
    base = Quartz.CIImage.imageWithContentsOfURL_(url)
    gainmap = Quartz.CIImage.imageWithContentsOfURL_options_(
        url,
        {Quartz.kCIImageAuxiliaryHDRGainMap: _nsnumber_bool(True)},
    )
    if base is None or gainmap is None or linear_p3 is None:
        raise RuntimeError("Core Image 无法回读扩展 HDR rendition")
    if not hasattr(base, "imageByApplyingGainMap_"):
        raise RuntimeError("当前 Core Image 不支持应用 HDR gain map")
    image = base.imageByApplyingGainMap_(gainmap)
    if image is None:
        raise RuntimeError("Core Image 无法组合 SDR 底图与 HDR gain map")
    extent = image.extent()
    width = int(round(float(extent.size.width)))
    height = int(round(float(extent.size.height)))
    if width <= 0 or height <= 0:
        raise RuntimeError("Core Image 回读 HDR rendition 得到空图像")
    row_bytes = width * 8
    buf = bytearray(height * row_bytes)
    context = Quartz.CIContext.contextWithOptions_(
        {Quartz.kCIContextCacheIntermediates: _nsnumber_bool(False)}
    )
    context.render_toBitmap_rowBytes_bounds_format_colorSpace_(
        image,
        buf,
        row_bytes,
        extent,
        Quartz.kCIFormatRGBAh,
        linear_p3,
    )
    return np.frombuffer(memoryview(buf), dtype=np.float16).reshape(height, width, 4).copy()


@lru_cache(maxsize=1)
def _apple_rgb_gainmap_roundtrip_status() -> tuple[bool, str]:
    """Prove that independent RGB HDR geometry survives encode and expansion."""
    ok, reason = _apple_gainmap_api_status()
    if not ok:
        return ok, reason

    h, patch_w = 24, 24
    gains = np.array(
        [[3.0, 3.0, 3.0], [1.25, 2.0, 3.0], [3.0, 1.25, 2.0], [2.0, 3.0, 1.25]],
        dtype=np.float32,
    )
    base = np.full((h, patch_w * len(gains), 3), 180, dtype=np.uint8)
    base_linear = srgb_decode(base.astype(np.float32) / np.float32(255.0))
    hdr = np.empty(base.shape[:2] + (4,), dtype=np.float16)
    for index, gain in enumerate(gains):
        x0, x1 = index * patch_w, (index + 1) * patch_w
        hdr[:, x0:x1, :3] = (base_linear[:, x0:x1] * gain).astype(np.float16)
    hdr[..., 3] = np.float16(1.0)

    try:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "rgb_gainmap_probe.jpg"
            write_apple_gainmap_jpeg(
                base,
                hdr,
                path,
                100,
                3.0,
                _verify_roundtrip_capability=False,
            )
            expanded = _read_expanded_hdr_rgba_half(path)[..., :3].astype(np.float32)
        expected_means = []
        actual_means = []
        inset = 4
        for index in range(len(gains)):
            x0, x1 = index * patch_w + inset, (index + 1) * patch_w - inset
            expected_means.append(np.mean(hdr[inset:-inset, x0:x1, :3], axis=(0, 1)))
            actual_means.append(np.mean(expanded[inset:-inset, x0:x1, :3], axis=(0, 1)))
        expected = np.asarray(expected_means, dtype=np.float32)
        actual = np.asarray(actual_means, dtype=np.float32)
        expected_chroma = expected / np.maximum(np.sum(expected, axis=1, keepdims=True), 1e-6)
        actual_chroma = actual / np.maximum(np.sum(actual, axis=1, keepdims=True), 1e-6)
        chroma_error = float(np.max(np.abs(actual_chroma - expected_chroma)))
        relative_error = float(
            np.mean(np.abs(actual - expected) / np.maximum(np.abs(expected), 0.05))
        )
        if chroma_error > 0.06 or relative_error > 0.25:
            return False, (
                "当前 Core Image 虽能写 RGB gain-map 容器，但扩展回读不能还原独立 RGB "
                f"HDR rendition（chroma error={chroma_error:.3f}, relative error={relative_error:.3f}）；"
                "已停用 HDR 导出，避免生成语义错误的文件"
            )
    except Exception as exc:
        return False, f"Apple RGB gain-map encode/decode round-trip 探针失败：{exc}"
    return True, "Apple Core Image ISO gain-map RGB round-trip verified"


def apple_gainmap_backend_status() -> tuple[bool, str]:
    """Keep production HDR disabled while the darktable-style HDR AgX is designed.

    The private round-trip probe remains available for packaging experiments, but a
    successful container backend is not enough: the ACES-derived renderer that used to
    supply the alternate has been removed, and the darktable-style HDR AgX that replaces
    it does not exist yet. Public callers must not promote packaging readiness into a
    supported output merely because a future OS passes the probe.
    """
    return False, (
        "HDR 输出已暂停：正在重新设计独立的 darktable-style HDR AgX 核；"
        "gain-map 封装可用，但没有可写入的 HDR rendition"
    )


def gain_stops_for_scene_ev(
    scene_ev: Any,
    white_ev: float,
    hdr_headroom_ev: float,
    diffuse_white_ev: float = HDR_DIFFUSE_WHITE_EV,
) -> Any:
    """Recover real scene stops above diffuse white, capped by display headroom.

    The AgX shoulder is intentionally not an HDR reference-white anchor: it can begin near
    mid-gray so the SDR DRT can compress smoothly.  Treating that whole interval as HDR
    content stretches broad, already-white AgX highlights to the display peak.  Here one
    recoverable scene stop produces at most one gain stop.  A C1 roll-in avoids a seam at
    diffuse white and a C1 cap keeps the user-selected headroom an upper bound, not a
    target that every image must fill.
    """
    headroom = np.float32(max(0.0, float(hdr_headroom_ev)))
    if headroom <= 0.0:
        return np.zeros_like(np.asarray(scene_ev, dtype=np.float32))

    reliable_ev = np.minimum(np.asarray(scene_ev, dtype=np.float32), np.float32(white_ev))
    excess = np.maximum(reliable_ev - np.float32(diffuse_white_ev), np.float32(0.0))
    rollin = np.float32(HDR_GAIN_ROLLIN_EV)
    t = np.clip(excess / rollin, 0.0, 1.0)
    gain = excess * (t * t * (np.float32(3.0) - np.float32(2.0) * t))

    cap_width = np.float32(min(HDR_GAIN_CAP_ROLLOFF_EV, max(0.05, float(headroom) * 0.25)))
    cap_start = headroom - cap_width
    cap_end = headroom + cap_width
    middle = (gain > cap_start) & (gain < cap_end)
    if np.any(middle):
        u = (gain[middle] - cap_start) / (cap_end - cap_start)
        h00 = np.float32(2.0) * u**3 - np.float32(3.0) * u**2 + np.float32(1.0)
        h10 = u**3 - np.float32(2.0) * u**2 + u
        h01 = -np.float32(2.0) * u**3 + np.float32(3.0) * u**2
        gain[middle] = h00 * cap_start + h10 * (cap_end - cap_start) + h01 * headroom
    gain[gain >= cap_end] = headroom
    return np.clip(gain, 0.0, headroom).astype(np.float32, copy=False)


def build_hdr_alternate_rgba_half(
    base_rgb_u8: Any,
    bundle: RawBundle,
    plan: ToneCompressionPlan | RenderPlan,
    hdr_headroom_ev: float,
    scene_transform: str = "none",
    scene_transform_strength: float = 1.0,
) -> Any:
    """Build an extended-linear Display P3 HDR alternate from the exact SDR primary.

    Multiplying all three P3 channels by one scalar keeps the SDR hue and chromaticity
    intact. The scalar is driven by pre-tone scene luminance above a diffuse-white
    reference, then limited by the reliable scene-white endpoint and the requested
    capacity. Quantized SDR code values are decoded here deliberately: the ratio seen by
    Core Image is anchored to the actual 8-bit compatibility image.
    """
    base = np.asarray(base_rgb_u8)
    scene = np.asarray(bundle.scene_rec2020_render)
    if base.dtype != np.uint8 or base.ndim != 3 or base.shape[2] != 3:
        raise ValueError("HDR gain-map 底图必须是 HxWx3 uint8")
    if scene.shape[:2] != base.shape[:2] or scene.shape[-1] < 3:
        raise ValueError("HDR alternate 与 SDR 底图尺寸不一致")
    if not 0.0 < float(hdr_headroom_ev) <= 8.0:
        raise ValueError("HDR headroom 必须在 0-8 EV 之间")

    tone = plan.tone if isinstance(plan, RenderPlan) else plan
    scene_flat = scene.reshape(-1, scene.shape[-1])
    base_flat = base.reshape(-1, 3)
    hdr = np.empty((scene_flat.shape[0], 4), dtype=np.float16)
    wb_adapt = scene_transform_engine.wb_adaptation_ratios(
        bundle.wb_mode, bundle.camera_wb, bundle.daylight_wb
    )
    max_linear = np.float32(2.0 ** float(hdr_headroom_ev))

    for start in range(0, scene_flat.shape[0], HDR_BUILD_CHUNK):
        end = min(start + HDR_BUILD_CHUNK, scene_flat.shape[0])
        rec = scene_rec2020_to_float(
            scene_flat[start:end, :3], bundle.scene_scale, bundle.exposure_gain
        )
        rec = scene_transform_engine.apply_scene_transform_rec2020(
            rec, scene_transform, scene_transform_strength, wb_adapt
        )
        scene_y = luminance_from_rec2020(rec)
        scene_ev = np.log2(np.maximum(scene_y, np.float32(EPS))) - np.float32(GRAY_EV)
        gain_stops = gain_stops_for_scene_ev(
            scene_ev,
            float(tone.white_ev),
            hdr_headroom_ev,
        )
        base_linear = srgb_decode(base_flat[start:end].astype(np.float32) / np.float32(255.0))
        gain = np.exp2(gain_stops).astype(np.float32, copy=False)
        hdr[start:end, :3] = np.clip(base_linear * gain[:, None], 0.0, max_linear).astype(
            np.float16, copy=False
        )
        hdr[start:end, 3] = np.float16(1.0)
    return hdr.reshape(base.shape[:2] + (4,))


def _nsnumber_bool(value: bool) -> Any:
    from Foundation import NSNumber  # type: ignore

    return NSNumber.numberWithBool_(bool(value))


def _nsdata_no_copy(array: Any) -> Any:
    from Foundation import NSData  # type: ignore

    if not array.flags.c_contiguous:
        raise ValueError("Core Image 输入必须是 C-contiguous array")
    return NSData.dataWithBytesNoCopy_length_freeWhenDone_(array, int(array.nbytes), False)


def _ciimage_from_rgba(array: Any, pixel_format: int, color_space: Any) -> tuple[Any, Any]:
    import Quartz  # type: ignore

    h, w = array.shape[:2]
    data = _nsdata_no_copy(array)
    image = Quartz.CIImage.imageWithBitmapData_bytesPerRow_size_format_colorSpace_(
        data,
        int(array.strides[0]),
        (float(w), float(h)),
        pixel_format,
        color_space,
    )
    if image is None:
        raise RuntimeError("Core Image 无法创建 HDR 编码输入")
    return image, data


def inspect_gainmap_jpeg(path: Path) -> dict[str, Any]:
    """Read the properties needed to prove an Apple-written ISO gain-map JPEG."""
    import Quartz  # type: ignore
    from Foundation import NSURL  # type: ignore

    source = Quartz.CGImageSourceCreateWithURL(NSURL.fileURLWithPath_(str(path)), None)
    if source is None:
        raise RuntimeError(f"ImageIO 无法读取输出 JPEG：{path}")
    primary = Quartz.CGImageSourceCopyPropertiesAtIndex(source, 0, None) or {}
    file_props = Quartz.CGImageSourceCopyProperties(source, None) or {}
    contents = file_props.get(Quartz.kCGImagePropertyFileContentsDictionary, {})
    images = contents.get("Images", ()) if contents else ()
    first = images[0] if images else {}
    auxiliary = first.get(Quartz.kCGImagePropertyAuxiliaryData, ()) if first else ()
    iso_type = str(getattr(Quartz, "kCGImageAuxiliaryDataTypeISOGainMap", ""))
    gainmap = next(
        (item for item in auxiliary if str(item.get("AuxiliaryDataType", "")) == iso_type),
        None,
    )
    pixel_format = int(gainmap.get("PixelFormat", 0)) if gainmap is not None else 0
    pixel_format_name = (
        pixel_format.to_bytes(4, "big").decode("ascii", errors="replace") if pixel_format else ""
    )
    return {
        "has_iso_gainmap": gainmap is not None,
        "headroom": float(primary.get("Headroom", 1.0)),
        "profile": str(primary.get(Quartz.kCGImagePropertyProfileName, "")),
        "width": int(primary.get(Quartz.kCGImagePropertyPixelWidth, 0)),
        "height": int(primary.get(Quartz.kCGImagePropertyPixelHeight, 0)),
        "chroma_subsampling": str(first.get("ChromaSubsampling", "")),
        "gainmap_width": int(gainmap.get("Width", 0)) if gainmap is not None else 0,
        "gainmap_height": int(gainmap.get("Height", 0)) if gainmap is not None else 0,
        "gainmap_pixel_format": pixel_format_name,
    }


def write_apple_gainmap_jpeg(
    base_rgb_u8: Any,
    hdr_rgba_half: Any,
    out_path: Path,
    quality: int,
    hdr_headroom_ev: float,
    *,
    _verify_roundtrip_capability: bool = True,
) -> dict[str, Any]:
    """Write and validate a Display P3 JPEG carrying an ISO 21496-1 gain map."""
    ok, reason = (
        apple_gainmap_backend_status()
        if _verify_roundtrip_capability
        else _apple_gainmap_api_status()
    )
    if not ok:
        raise RuntimeError(reason)
    if not 1 <= int(quality) <= 100:
        raise ValueError("JPEG quality 必须在 1-100 之间")

    import Quartz  # type: ignore
    from Foundation import NSURL  # type: ignore

    base = np.asarray(base_rgb_u8)
    hdr = np.asarray(hdr_rgba_half)
    if base.dtype != np.uint8 or base.ndim != 3 or base.shape[2] != 3:
        raise ValueError("HDR gain-map 底图必须是 HxWx3 uint8")
    if hdr.dtype != np.float16 or hdr.shape != base.shape[:2] + (4,):
        raise ValueError("HDR alternate 必须是与底图同尺寸的 HxWx4 float16")
    if not base.flags.c_contiguous:
        base = np.ascontiguousarray(base)
    if not hdr.flags.c_contiguous:
        hdr = np.ascontiguousarray(hdr)

    p3 = Quartz.CGColorSpaceCreateWithName(Quartz.kCGColorSpaceDisplayP3)
    linear_p3 = Quartz.CGColorSpaceCreateWithName(Quartz.kCGColorSpaceExtendedLinearDisplayP3)
    if p3 is None or linear_p3 is None:
        raise RuntimeError("系统未提供 Display P3 / Extended Linear Display P3 色彩空间")

    base_rgba = np.empty(base.shape[:2] + (4,), dtype=np.uint8)
    base_rgba[:, :, :3] = base
    base_rgba[:, :, 3] = np.uint8(255)
    base_image, base_data = _ciimage_from_rgba(base_rgba, Quartz.kCIFormatRGBA8, p3)
    hdr_image, hdr_data = _ciimage_from_rgba(hdr, Quartz.kCIFormatRGBAh, linear_p3)
    base_image = base_image.imageBySettingContentHeadroom_(1.0)
    requested_headroom = float(2.0 ** float(hdr_headroom_ev))
    actual_headroom = float(np.max(hdr[:, :, :3]))
    if actual_headroom > requested_headroom * 1.001:
        raise RuntimeError(
            f"HDR rendition 超过所选余量：{actual_headroom:.3f}x > {requested_headroom:.3f}x"
        )
    if actual_headroom <= 1.0 + 1e-3:
        raise RuntimeError("该场景没有高于 reference white 的有效 HDR 内容")
    hdr_image = hdr_image.imageBySettingContentHeadroom_(actual_headroom)

    context = Quartz.CIContext.contextWithOptions_(
        {Quartz.kCIContextCacheIntermediates: _nsnumber_bool(False)}
    )
    options = {
        Quartz.kCGImageDestinationLossyCompressionQuality: float(quality) / 100.0,
        Quartz.kCIImageRepresentationHDRImage: hdr_image,
        Quartz.kCIImageRepresentationHDRGainMapAsRGB: _nsnumber_bool(True),
        Quartz.kCGImageDestinationEncodeRequest: Quartz.kCGImageDestinationEncodeToISOGainmap,
        Quartz.kCGImageDestinationEncodeRequestOptions: {
            Quartz.kCGImageDestinationEncodeBaseIsSDR: _nsnumber_bool(True),
        },
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = out_path.with_name(f".{out_path.name}.{uuid.uuid4().hex}.tmp.jpg")
    try:
        result = context.writeJPEGRepresentationOfImage_toURL_colorSpace_options_error_(
            base_image,
            NSURL.fileURLWithPath_(str(temp_path)),
            p3,
            options,
            None,
        )
        success, error = result if isinstance(result, tuple) else (bool(result), None)
        if not success:
            raise RuntimeError(f"Core Image 写入 ISO gain-map JPEG 失败：{error}")

        # Keep no-copy backing arrays alive until Core Image has finished the lazy graph.
        _ = (base_data, hdr_data, base_rgba, hdr)
        info = inspect_gainmap_jpeg(temp_path)
        if not info["has_iso_gainmap"]:
            raise RuntimeError("Core Image 输出不含 ISO 21496-1 gain map")
        if info["profile"] != "Display P3":
            raise RuntimeError(f"HDR JPEG 底图色彩配置错误：{info['profile'] or '无 ICC'}")
        if info["chroma_subsampling"] != "4:4:4":
            raise RuntimeError(
                f"HDR JPEG 主图未保持 4:4:4：{info['chroma_subsampling'] or '未知'}"
            )
        fmt = str(info["gainmap_pixel_format"] or "")
        # RGB gain maps are required for independent HDR color geometry. Reject L008.
        if fmt in ("", "L008"):
            raise RuntimeError(
                f"HDR JPEG gain map 不是 RGB 辅助图（got {fmt or '未知'}）；"
                "独立 HDR color geometry 需要 RGB gain map"
            )
        if info["headroom"] <= 1.0:
            raise RuntimeError("HDR JPEG 未声明有效的扩展动态范围")
        os.replace(temp_path, out_path)
        info["gainmap_as_rgb"] = True
        return info
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
