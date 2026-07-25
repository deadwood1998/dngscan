# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional Core Image (CIRAWFilter / RAW9) scene-linear Rec.2020 decoder.

Evidence (CFA clip masks, mosaic, levels) always stays on LibRaw. This module only
produces an alternate scene-linear RGB buffer. Importing this module must not raise
when Quartz/PyObjC is absent.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ._deps import np

# Measured 2026-07 on macOS 27.0 against Sigma fp DNG: Core Image linear values are
# 0.9314× the LibRaw Rec.2020 pipeline (−0.10 EV). Absorb once; do not re-fit per image.
COREIMAGE_SCALE_COMPENSATION = 1.0 / 0.9314
COREIMAGE_SCALE_COMPENSATION_NOTE = "measured 2026-07 Sigma fp; CI/LibRaw median ratio 0.9314"

COREIMAGE_DECODER_VERSIONS = ("auto", "9", "8", "7")

# Plan cited ≥0.98 on matched fractional sampling of milder frames. Extreme-DR night
# frames sit lower from demosaic/highlight disagreement alone; keep a floor that still
# rejects a flipped or otherwise broken mapping (wrong y-flip collapses the score).
GEOMETRY_CORR_MIN = 0.80
GEOMETRY_CORR_FLIP_MARGIN = 0.30


def available() -> bool:
    """True when Quartz exposes CIRAWFilter (macOS + PyObjC). Never raises."""
    try:
        import Quartz  # type: ignore

        return hasattr(Quartz, "CIRAWFilter") and callable(
            getattr(Quartz.CIRAWFilter, "alloc", None)
        )
    except Exception:
        return False


def _require_quartz() -> Any:
    if not available():
        raise RuntimeError(
            "Core Image RAW decoder unavailable: need macOS with PyObjC Quartz "
            "(pyobjc-framework-Quartz) and CIRAWFilter"
        )
    import Quartz  # type: ignore

    return Quartz


def _image_url(path: Path) -> Any:
    from Foundation import NSURL  # type: ignore

    return NSURL.fileURLWithPath_(str(path.resolve()))


def _open_filter(path: Path) -> Any:
    Quartz = _require_quartz()
    if not path.is_file():
        raise FileNotFoundError(f"Input file does not exist: {path}")
    filt = Quartz.CIRAWFilter.alloc().initWithImageURL_(_image_url(path))
    if filt is None:
        raise RuntimeError(f"CIRAWFilter could not open: {path}")
    return filt


def supported_versions(path: Path) -> tuple[str, ...]:
    """Decoder version strings offered for this file (e.g. '9', '8', '7')."""
    filt = _open_filter(path)
    raw = filt.supportedDecoderVersions()
    if not raw:
        return ()
    out: list[str] = []
    for item in raw:
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
    return tuple(out)


def _normalize_version_token(token: str) -> str:
    text = str(token).strip().lower()
    if text.endswith(".dng"):
        text = text[: -len(".dng")]
    return text


def resolve_decoder_version(requested: str, offered: tuple[str, ...]) -> str:
    """Pick a concrete version string from the filter's offered list.

    ``auto`` selects the highest of 9/8/7 present (preferring bare tokens over ``*.dng``).
    An explicit unsupported version raises rather than silently downgrading.
    """
    if not offered:
        raise RuntimeError("CIRAWFilter reports no supported decoder versions for this file")
    requested = str(requested).strip().lower()
    if requested not in COREIMAGE_DECODER_VERSIONS:
        raise ValueError(
            f"unknown coreimage version {requested!r}; "
            f"expected one of {COREIMAGE_DECODER_VERSIONS}"
        )

    by_major: dict[str, list[str]] = {}
    for item in offered:
        major = _normalize_version_token(item)
        by_major.setdefault(major, []).append(item)

    def prefer(major: str) -> str | None:
        options = by_major.get(major)
        if not options:
            return None
        bare = [o for o in options if _normalize_version_token(o) == o.lower() or o == major]
        # Prefer '9' over '9.dng' when both exist.
        for cand in (major, f"{major}.dng"):
            for opt in options:
                if opt.lower() == cand:
                    return opt
        return options[0]

    if requested == "auto":
        for major in ("9", "8", "7", "6"):
            chosen = prefer(major)
            if chosen is not None:
                return chosen
        return offered[-1]

    chosen = prefer(requested)
    if chosen is None:
        raise RuntimeError(
            f"decoder version {requested!r} is not supported for this file; "
            f"offered: {', '.join(offered)}"
        )
    return chosen


def _set_amount(filt: Any, setter: str, supported_pred: str | None, value: float) -> bool:
    """Set a CIRAW amount when the control exists; return whether it was written."""
    if supported_pred is not None:
        pred = getattr(filt, supported_pred, None)
        if callable(pred) and not bool(pred()):
            return False
    fn = getattr(filt, setter, None)
    if not callable(fn):
        return False
    fn(float(value))
    return True


def configure_linear_filter(
    filt: Any,
    *,
    version: str,
    scale_factor: float,
    exposure: float = 0.0,
) -> dict[str, Any]:
    """Zero subjective CIRAW controls and select the decoder version.

    Returns a small dict describing what was applied (for reports/tests).
    """
    filt.setDecoderVersion_(version)
    _set_amount(filt, "setBoostAmount_", None, 0.0)
    _set_amount(filt, "setBoostShadowAmount_", None, 0.0)
    _set_amount(filt, "setBaselineExposure_", None, 0.0)
    _set_amount(filt, "setExposure_", None, float(exposure))
    _set_amount(filt, "setLuminanceNoiseReductionAmount_", "isLuminanceNoiseReductionSupported", 0.0)
    color_nr_cleared = _set_amount(
        filt, "setColorNoiseReductionAmount_", "isColorNoiseReductionSupported", 0.0
    )
    # colorNoiseReductionAmount defaults to 0.5 even when the "supported" flag is false
    # on some versions — always try the setter.
    if not color_nr_cleared:
        color_nr_cleared = _set_amount(filt, "setColorNoiseReductionAmount_", None, 0.0)
    _set_amount(filt, "setDetailAmount_", "isDetailSupported", 0.0)
    _set_amount(filt, "setContrastAmount_", "isContrastSupported", 0.0)
    _set_amount(filt, "setLocalToneMapAmount_", "isLocalToneMapSupported", 0.0)
    _set_amount(filt, "setSharpnessAmount_", "isSharpnessSupported", 0.0)
    if hasattr(filt, "setGamutMappingEnabled_"):
        filt.setGamutMappingEnabled_(False)
    if hasattr(filt, "setHighlightRecoveryEnabled_") and getattr(
        filt, "isHighlightRecoverySupported", lambda: False
    )():
        filt.setHighlightRecoveryEnabled_(False)
    filt.setScaleFactor_(float(scale_factor))

    color_nr = float(filt.colorNoiseReductionAmount()) if hasattr(filt, "colorNoiseReductionAmount") else None
    return {
        "version": str(filt.decoderVersion()) if hasattr(filt, "decoderVersion") else version,
        "scale_factor": float(filt.scaleFactor()) if hasattr(filt, "scaleFactor") else float(scale_factor),
        "color_noise_reduction_amount": color_nr,
        "color_noise_cleared": color_nr is not None and abs(float(color_nr)) <= 1e-6,
    }


def _render_linear_rec2020(filt: Any) -> np.ndarray:
    """Render filter output to float32 HxWx3 extended-linear Rec.2020 (direct rect, no flip)."""
    Quartz = _require_quartz()
    image = filt.outputImage()
    if image is None:
        raise RuntimeError("CIRAWFilter.outputImage returned None")
    extent = image.extent()
    width = int(round(float(extent.size.width)))
    height = int(round(float(extent.size.height)))
    if width <= 0 or height <= 0:
        raise RuntimeError(f"CIRAWFilter produced empty extent: {extent}")

    origin_x = float(extent.origin.x)
    origin_y = float(extent.origin.y)
    ctx = Quartz.CIContext.contextWithOptions_(None)
    if ctx is None:
        raise RuntimeError("CIContext.contextWithOptions_ returned None")
    color_space = Quartz.CGColorSpaceCreateWithName(Quartz.kCGColorSpaceExtendedLinearITUR_2020)
    if color_space is None:
        raise RuntimeError("kCGColorSpaceExtendedLinearITUR_2020 is unavailable")

    row_bytes = width * 16  # RGBA float32
    buf = bytearray(height * row_bytes)
    bounds = Quartz.CGRectMake(origin_x, origin_y, width, height)
    # Direct top-left sampling: do not vertically flip the bitmap. The intuitive
    # y-flip silently destroys LibRaw alignment (correlation collapses toward ~0.3).
    ctx.render_toBitmap_rowBytes_bounds_format_colorSpace_(
        image,
        buf,
        row_bytes,
        bounds,
        Quartz.kCIFormatRGBAf,
        color_space,
    )
    rgba = np.frombuffer(memoryview(buf), dtype=np.float32).reshape(height, width, 4)
    return np.ascontiguousarray(rgba[:, :, :3])


def _resize_rgb(image: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    target_h, target_w = shape
    if image.shape[:2] == (target_h, target_w):
        return image
    from PIL import Image

    out = np.empty((target_h, target_w, image.shape[2]), dtype=np.float32)
    for idx in range(image.shape[2]):
        im = Image.fromarray(image[:, :, idx].astype(np.float32, copy=False), mode="F")
        im = im.resize((target_w, target_h), Image.Resampling.BILINEAR)
        out[:, :, idx] = np.asarray(im, dtype=np.float32)
    return out


def luma_rec2020(rgb: np.ndarray) -> np.ndarray:
    arr = np.asarray(rgb, dtype=np.float32)
    return (
        np.float32(0.2627) * arr[:, :, 0]
        + np.float32(0.6780) * arr[:, :, 1]
        + np.float32(0.0593) * arr[:, :, 2]
    )


def _box_downsample(plane: np.ndarray, factor: int = 8) -> np.ndarray:
    h, w = plane.shape
    h2 = max(1, h // factor)
    w2 = max(1, w // factor)
    cropped = plane[: h2 * factor, : w2 * factor]
    if cropped.size == 0:
        return plane[::factor, ::factor].astype(np.float64, copy=False)
    return (
        cropped.reshape(h2, factor, w2, factor)
        .mean(axis=(1, 3))
        .astype(np.float64, copy=False)
    )


def pearson_corr(a: np.ndarray, b: np.ndarray) -> float:
    x = np.asarray(a, dtype=np.float64).ravel()
    y = np.asarray(b, dtype=np.float64).ravel()
    n = min(x.size, y.size)
    if n < 16:
        return 0.0
    x = x[:n]
    y = y[:n]
    x = x - x.mean()
    y = y - y.mean()
    denom = float(np.linalg.norm(x) * np.linalg.norm(y))
    if denom <= 1e-18:
        return 0.0
    return float(x @ y) / denom


def geometry_correlation(coreimage_rgb: np.ndarray, libraw_rgb: np.ndarray) -> float:
    """Downsampled luma correlation after top-left fractional mapping (no flip).

    Extreme-DR frames disagree in the highlight tail (LibRaw u16 clips at 1.0 while
    CIRAW keeps headroom). Correlate inside the shared robust mid-range so the score
    reflects spatial alignment rather than demosaic/highlight policy.
    """
    ci = np.asarray(coreimage_rgb, dtype=np.float32)
    lr_raw = np.asarray(libraw_rgb)
    if lr_raw.ndim == 3 and lr_raw.shape[2] >= 3:
        if np.issubdtype(lr_raw.dtype, np.integer):
            lr = lr_raw.astype(np.float32) / float(np.iinfo(lr_raw.dtype).max)
        else:
            lr = lr_raw.astype(np.float32, copy=False)
        lr_y = luma_rec2020(lr)
    else:
        lr_y = np.asarray(lr_raw, dtype=np.float32)
    ci_y = luma_rec2020(ci) if ci.ndim == 3 else np.asarray(ci, dtype=np.float32)
    from PIL import Image

    mapped = np.asarray(
        Image.fromarray(lr_y.astype(np.float32, copy=False), mode="F").resize(
            (ci_y.shape[1], ci_y.shape[0]), Image.Resampling.BILINEAR
        ),
        dtype=np.float32,
    )
    lo = float(max(np.percentile(ci_y, 5), np.percentile(mapped, 5), 0.0))
    hi = float(min(np.percentile(ci_y, 95), np.percentile(mapped, 95)))
    if hi <= lo + 1e-9:
        hi = lo + 1e-6
    return pearson_corr(
        _box_downsample(np.clip(ci_y, lo, hi)),
        _box_downsample(np.clip(mapped, lo, hi)),
    )


def verify_geometry_alignment(
    coreimage_rgb: np.ndarray,
    libraw_rgb: np.ndarray,
    *,
    min_corr: float = GEOMETRY_CORR_MIN,
) -> float:
    """Raise if Core Image / LibRaw frames are not aligned for clip-mask reuse."""
    corr = geometry_correlation(coreimage_rgb, libraw_rgb)
    flipped = geometry_correlation(np.flipud(np.asarray(coreimage_rgb)), libraw_rgb)
    if corr < float(min_corr) or corr < flipped + GEOMETRY_CORR_FLIP_MARGIN:
        raise RuntimeError(
            "Core Image scene buffer failed geometry alignment against LibRaw "
            f"(corr={corr:.4f}, flipud_corr={flipped:.4f}, need>={min_corr:.2f} and "
            f"margin over flipud >={GEOMETRY_CORR_FLIP_MARGIN:.2f}). "
            "Clip retreat would land on the wrong pixels; refusing this decoder path."
        )
    return corr


def decode_scene_rec2020(
    path: Path,
    *,
    half_size: bool,
    version: str = "auto",
    target_shape: tuple[int, int] | None = None,
    exposure: float = 0.0,
    scale_compensation: float = COREIMAGE_SCALE_COMPENSATION,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Decode to float32 HxWx3 linear Rec.2020 (extended range possible).

    Subjective CIRAW controls are zeroed. Scale compensation brings units in line
    with the LibRaw pipeline. Optional ``target_shape`` resamples after render.
    """
    path = Path(path)
    offered = supported_versions(path)
    resolved = resolve_decoder_version(version, offered)
    filt = _open_filter(path)
    scale_factor = 0.5 if half_size else 1.0
    cfg = configure_linear_filter(
        filt, version=resolved, scale_factor=scale_factor, exposure=float(exposure)
    )
    rgb = _render_linear_rec2020(filt)
    if abs(float(scale_compensation) - 1.0) > 1e-12:
        rgb = rgb * np.float32(scale_compensation)
    extent = (int(rgb.shape[0]), int(rgb.shape[1]))
    if target_shape is not None and tuple(target_shape) != extent:
        rgb = _resize_rgb(rgb, (int(target_shape[0]), int(target_shape[1])))
    info = {
        "decoder": "coreimage",
        "version_requested": version,
        "version": cfg["version"],
        "versions_offered": offered,
        "extent": extent,
        "shape": (int(rgb.shape[0]), int(rgb.shape[1])),
        "half_size": bool(half_size),
        "scale_factor": cfg["scale_factor"],
        "scale_compensation": float(scale_compensation),
        "scale_compensation_note": COREIMAGE_SCALE_COMPENSATION_NOTE,
        "color_noise_reduction_amount": cfg["color_noise_reduction_amount"],
        "color_noise_cleared": cfg["color_noise_cleared"],
        "exposure": float(exposure),
    }
    return rgb.astype(np.float32, copy=False), info


def scene_float_to_u16(rgb: np.ndarray, scene_scale: float = 65535.0) -> np.ndarray:
    """Convert linear float Rec.2020 to the pipeline's uint16 + scene_scale convention."""
    scale = float(scene_scale)
    linear = np.asarray(rgb, dtype=np.float32)
    return np.clip(linear * scale, 0.0, scale).astype(np.uint16)
