# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional Core Image (CIRAWFilter / RAW9) scene-linear Rec.2020 decoder.

Evidence (CFA clip masks, mosaic, levels) always stays on LibRaw. This module only
produces an alternate scene-linear RGB buffer. Importing this module must not raise
when Quartz/PyObjC is absent.
"""
from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

from ._deps import np

# Measured 2026-07 on macOS 27.0 against Sigma fp DNG: Core Image linear values are
# 0.9314× the LibRaw Rec.2020 pipeline (−0.10 EV). Absorb once; do not re-fit per image.
# Re-derived after shadowBias was zeroed: the old 1/0.9314 was mostly compensating for
# that subtraction darkening the buffer, not for a real scale difference between the
# decoders. With Apple's full linear recipe applied the two paths nearly agree on their
# own. Median CI/LibRaw midtone ratio over four Sigma fp frames, spread 0.94..1.12.
COREIMAGE_SCALE_COMPENSATION = 1.0 / 1.0293
COREIMAGE_SCALE_COMPENSATION_NOTE = "measured 2026-07 Sigma fp; CI/LibRaw median ratio 1.0293"

COREIMAGE_DECODER_VERSIONS = ("auto", "9", "8", "7")

# Diagnostic-only alignment floors. The Core Image path is a SEPARATE pipeline: it does
# not reuse LibRaw's per-pixel evidence, so frame alignment is no longer a correctness
# precondition and is never gated on. Retained for the report and for tools that want to
# quantify how far Apple's corrected geometry sits from LibRaw's uncorrected frame.
GEOMETRY_CORR_MIN = 0.80
GEOMETRY_CORR_FLIP_MARGIN = 0.30

# DNG opcode IDs (DNG 1.7 spec, Chapter 6) that make Apple's decoded frame geometrically
# or radiometrically incomparable to LibRaw's. WarpRectilinear is the decisive one: it is
# a per-plane radial polynomial, so corners move by tens of pixels (measured ~70 px on a
# 24 MP Sigma fp frame) and no affine crop can reconcile the two frames.
DNG_OPCODE_WARP_RECTILINEAR = 1
DNG_OPCODE_WARP_FISHEYE = 2
DNG_OPCODE_FIX_VIGNETTE_RADIAL = 3
DNG_OPCODE_GAIN_MAP = 9
DNG_GEOMETRY_OPCODES = (DNG_OPCODE_WARP_RECTILINEAR, DNG_OPCODE_WARP_FISHEYE)
_DNG_OPCODE_NAMES = {
    DNG_OPCODE_WARP_RECTILINEAR: "WarpRectilinear",
    DNG_OPCODE_WARP_FISHEYE: "WarpFisheye",
    DNG_OPCODE_FIX_VIGNETTE_RADIAL: "FixVignetteRadial",
    DNG_OPCODE_GAIN_MAP: "GainMap",
}


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
    # Apple's own recipe for linear scene-referred extraction (WWDC21 "Capture and
    # process ProRAW images") is to zero baselineExposure, shadowBias, boostAmount and
    # localToneMapAmount and disable gamut mapping, then render into
    # extendedLinearITUR_2020. All five are applied here and in _render_linear_rec2020.
    _set_amount(filt, "setBoostAmount_", None, 0.0)
    # No effect while boostAmount is 0, cleared so the pair cannot drift apart.
    _set_amount(filt, "setBoostShadowAmount_", None, 0.0)
    _set_amount(filt, "setBaselineExposure_", None, 0.0)
    # shadowBias subtracts from the shadows and defaults to 5.0, which is a black-level
    # pedestal: a display-referred operation with no place in a scene-linear buffer.
    # Leaving it at the default drove components to exactly zero on 1.4 % of an ISO 12800
    # frame and 21.0 % of an ISO 25600 one, against 0.04 % and 0.18 % once zeroed, and
    # pushed the 1st percentile of luminance negative. Shadow detail that looked like it
    # had been eaten by the CoreML denoiser was mostly this subtraction.
    _set_amount(filt, "setShadowBias_", None, 0.0)
    _set_amount(filt, "setExposure_", None, float(exposure))
    _set_amount(filt, "setLuminanceNoiseReductionAmount_", "isLuminanceNoiseReductionSupported", 0.0)
    color_nr_cleared = _set_amount(
        filt, "setColorNoiseReductionAmount_", "isColorNoiseReductionSupported", 0.0
    )
    # RAW 9 is a tiled CoreML model that fuses demosaic WITH denoise (WWDC26 session
    # 305), so denoising is architectural rather than a stage that can be switched off.
    # Apple documents colorNoiseReductionAmount as having no effect there, and the
    # isSupported flag reports False — but the flag gates the call above, and measurement
    # contradicts the documentation: colorNoiseReductionAmount / detailAmount /
    # moireReductionAmount behave as aliases of one internal control on version 9
    # (setting any of the three to 1.0 gives bit-identical output that differs from
    # all-zero on 93.6 % of pixels). Its default is 0.5, so trusting the flag would leave
    # half-strength denoising on. Clear it unconditionally; zero is the minimum under
    # either reading.
    if not color_nr_cleared:
        color_nr_cleared = _set_amount(filt, "setColorNoiseReductionAmount_", None, 0.0)
    _set_amount(filt, "setDetailAmount_", "isDetailSupported", 0.0)
    _set_amount(filt, "setContrastAmount_", "isContrastSupported", 0.0)
    # Sharpening defaults to 0.485 and is a spatial operator, so leaving it on made the
    # buffer no longer a plain scene-linear decode. It was inert on decoder version 8
    # (measured: setting 0 vs 1 changed nothing) but is live on version 9, so it only
    # started mattering once RAW 9 became the default choice. Moire reduction is zero by
    # default on both, cleared here so a future default change cannot slip through.
    sharpness_cleared = _set_amount(filt, "setSharpnessAmount_", "isSharpnessSupported", 0.0)
    if not sharpness_cleared:
        sharpness_cleared = _set_amount(filt, "setSharpnessAmount_", None, 0.0)
    _set_amount(filt, "setMoireReductionAmount_", "isMoireReductionSupported", 0.0)
    _set_amount(filt, "setLocalToneMapAmount_", "isLocalToneMapSupported", 0.0)
    # Gamut mapping is an output-referred clamp and belongs after the view transform, not
    # before it. Enabling it collapses the buffer into the destination gamut: measured on
    # a Sigma fp frame it cut p99.995 from 2.08 to 1.07, zeroed every negative component
    # (real scene colours outside Rec.2020), and changed 14 % of pixels. AgX does its own
    # gamut work downstream, so this stays off and the handoff stays scene-referred.
    if hasattr(filt, "setGamutMappingEnabled_"):
        filt.setGamutMappingEnabled_(False)
    # Highlight recovery is reconstruction, not taste, so unlike the subjective controls
    # above it is left at Apple's default of on. Disabling it does not yield a "purer"
    # decode, it yields a wrong one: clipped highlights come back with green pinned far
    # below red and blue (measured near-white mean R 1.933 / G 0.681 / B 1.816, green the
    # largest channel in 0 % of them), which renders as magenta highlight cores. With
    # recovery on the same pixels average 1.981 / 1.980 / 1.980 and the specular headroom
    # survives intact (p99.995 2.08, max 2.22), so it is strictly better than the LibRaw
    # path's clip-to-common-white, which buys neutral highlights by discarding roll-off.
    highlight_recovery = None
    if hasattr(filt, "isHighlightRecoveryEnabled"):
        highlight_recovery = bool(filt.isHighlightRecoveryEnabled())
    filt.setScaleFactor_(float(scale_factor))

    color_nr = float(filt.colorNoiseReductionAmount()) if hasattr(filt, "colorNoiseReductionAmount") else None
    return {
        "version": str(filt.decoderVersion()) if hasattr(filt, "decoderVersion") else version,
        "scale_factor": float(filt.scaleFactor()) if hasattr(filt, "scaleFactor") else float(scale_factor),
        "color_noise_reduction_amount": color_nr,
        "color_noise_cleared": color_nr is not None and abs(float(color_nr)) <= 1e-6,
        "sharpness_amount": (
            float(filt.sharpnessAmount()) if hasattr(filt, "sharpnessAmount") else None
        ),
        "highlight_recovery": highlight_recovery,
        "shadow_bias": float(filt.shadowBias()) if hasattr(filt, "shadowBias") else None,
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


def read_dng_opcodes(path: Path) -> dict[str, Any]:
    """Report which DNG OpcodeList entries the file carries.

    Core Image executes these during decode; LibRaw does not. They are therefore the
    reason the two decoders are separate pipelines rather than interchangeable back
    ends, and knowing which are present is worth stating in the render report.
    Best-effort and never fatal: a parse failure returns an empty result."""
    result: dict[str, Any] = {"ids": (), "names": (), "geometry": False, "parsed": False}
    try:
        data = path.read_bytes()
        if len(data) < 8 or data[:2] not in (b"II", b"MM"):
            return result
        order = "<" if data[:2] == b"II" else ">"
        found: list[int] = []
        seen_ifds: set[int] = set()

        def walk(offset: int, depth: int = 0) -> None:
            if depth > 3 or offset in seen_ifds or offset <= 0 or offset + 2 > len(data):
                return
            seen_ifds.add(offset)
            count = struct.unpack(order + "H", data[offset : offset + 2])[0]
            for i in range(count):
                entry = offset + 2 + i * 12
                if entry + 12 > len(data):
                    return
                tag, _typ, cnt = struct.unpack(order + "HHI", data[entry : entry + 8])
                if tag in (0xC740, 0xC741, 0xC74E):  # OpcodeList1/2/3
                    value_off = struct.unpack(order + "I", data[entry + 8 : entry + 12])[0]
                    if value_off + 4 > len(data):
                        continue
                    # Opcode payloads are always big-endian, independent of TIFF order.
                    n_ops = struct.unpack(">I", data[value_off : value_off + 4])[0]
                    cursor = value_off + 4
                    for _ in range(min(n_ops, 16)):
                        if cursor + 16 > len(data):
                            break
                        op_id = struct.unpack(">I", data[cursor : cursor + 4])[0]
                        size = struct.unpack(">I", data[cursor + 12 : cursor + 16])[0]
                        found.append(int(op_id))
                        cursor += 16 + size
                elif tag == 0x014A:  # SubIFDs
                    sub_off = struct.unpack(order + "I", data[entry + 8 : entry + 12])[0]
                    for k in range(min(cnt, 8)):
                        pos = sub_off + k * 4
                        if pos + 4 <= len(data):
                            walk(struct.unpack(order + "I", data[pos : pos + 4])[0], depth + 1)

        walk(struct.unpack(order + "I", data[4:8])[0])
        ids = tuple(sorted(set(found)))
        result.update(
            ids=ids,
            names=tuple(_DNG_OPCODE_NAMES.get(i, f"opcode{i}") for i in ids),
            geometry=any(i in DNG_GEOMETRY_OPCODES for i in ids),
            parsed=True,
        )
    except Exception:  # pragma: no cover - diagnostics must never break a render
        return result
    return result


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
        "sharpness_amount": cfg.get("sharpness_amount"),
        "highlight_recovery": cfg.get("highlight_recovery"),
        "shadow_bias": cfg.get("shadow_bias"),
        "exposure": float(exposure),
    }
    return rgb.astype(np.float32, copy=False), info


# Quantisation headroom for the Core Image buffer. LibRaw's 1.0 is sensor saturation,
# and the pipeline anchors that at +3.00 EV (0.18 * 2**MIDGRAY_HEADROOM_STOPS), which is
# also the white endpoint's lower bound. Apple's 1.0 is diffuse white instead and real
# specular detail lives above it (measured up to 3.0 linear on a Sigma fp frame), so
# clipping at 1.0 does not merely lose those pixels — it caps the reliable tail at
# +3.00 EV and prevents the compiled white endpoint from ever rising above its floor.
# Allocating headroom keeps that detail; the cost is a coarser quantisation step, still
# finer than the 14-bit sensor data underneath (allowance 4.0 -> 6.1e-5 linear).
COREIMAGE_MAX_HEADROOM = 4.0


def scene_headroom(rgb: np.ndarray, *, percentile: float = 99.995) -> float:
    """Headroom to reserve above diffuse white, ignoring single-pixel outliers."""
    arr = np.asarray(rgb, dtype=np.float32)
    if arr.size == 0:
        return 1.0
    top = float(np.percentile(arr, percentile))
    return float(min(COREIMAGE_MAX_HEADROOM, max(1.0, top)))


def scene_float_to_u16(
    rgb: np.ndarray, scene_scale: float = 65535.0
) -> tuple[np.ndarray, float]:
    """Quantise linear float Rec.2020 into the pipeline's uint16 + scene_scale pair.

    Returns the buffer and the scene_scale that reproduces it, so that
    ``buffer / scene_scale`` recovers the original linear values including any headroom
    above diffuse white. Callers must use the returned scale, not the requested one."""
    linear = np.asarray(rgb, dtype=np.float32)
    headroom = scene_headroom(linear)
    scale = float(scene_scale) / headroom
    buffer = np.clip(linear * scale, 0.0, float(scene_scale)).astype(np.uint16)
    return buffer, scale
