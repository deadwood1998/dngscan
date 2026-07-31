# SPDX-License-Identifier: GPL-3.0-or-later
"""RAW decode via rawpy and scene-linear render buffers."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ._deps import np, rawpy
from . import metadata as dng_metadata
from .wb import kelvin_mode_cct, solve_kelvin_wb
from .constants import (
    COREIMAGE_SCALE_DEFAULT_MODE,
    DECODER_CHOICES,
    DEMOSAIC_AUTO_PREFERENCE,
    DEMOSAIC_CHOICES,
    WB_CHOICES,
)
from .models import RawBundle

def decode_color_desc(desc: Any) -> str:
    if isinstance(desc, bytes):
        text = desc.decode("ascii", errors="replace")
    else:
        text = str(desc)
    return text.replace("\x00", "").strip()


def rawpy_highlight_mode(name: str) -> Any:
    modes = getattr(rawpy, "HighlightMode", object)
    mapping = {
        "clip": getattr(modes, "Clip", 0),
        "blend": getattr(modes, "Blend", getattr(modes, "Clip", 0)),
        "reconstruct": getattr(modes, "ReconstructDefault", getattr(modes, "Clip", 0)),
    }
    if name not in mapping:
        raise ValueError(f"unknown highlight mode: {name}")
    return mapping[name]


def highlight_mode_cn(name: str) -> str:
    return {
        "clip": "硬剪切",
        "blend": "高光混合",
        "reconstruct": "高光重建",
    }.get(name, name)


def wb_postprocess_kwargs(
    wb_mode: str,
    daylight_wb: list[float] | None,
    kelvin_wb: list[float] | None = None,
) -> dict[str, Any]:
    """Film-style fixed balances or the as-shot camera balance (default).

    'daylight' uses libraw's calibrated daylight multipliers; the fixed-Kelvin modes
    take pre-solved multipliers from dngscan.wb (declared references, computed through
    the file's own colour calibration). One dict so every render agrees."""
    if wb_mode == "daylight" and daylight_wb is not None and any(v > 0 for v in daylight_wb[:3]):
        return {"use_camera_wb": False, "user_wb": [float(v) for v in daylight_wb[:4]]}
    if kelvin_mode_cct(wb_mode) is not None:
        if kelvin_wb is None or not any(v > 0 for v in kelvin_wb[:3]):
            raise ValueError(f"kelvin wb mode {wb_mode} requires solved multipliers")
        return {"use_camera_wb": False, "user_wb": [float(v) for v in kelvin_wb[:4]]}
    if wb_mode not in WB_CHOICES:
        raise ValueError(f"unknown wb mode: {wb_mode}")
    return {"use_camera_wb": True}




def _apply_gain_maps_mosaic(raw: Any, maps: list, black_levels: list[float], white_level: int) -> None:
    """Apply pre-demosaic GainMap opcodes to the live rawpy mosaic in place.

    Runs AFTER the evidence copies (bundle.raw_image, clip masks) are taken: clip
    evidence is sensor truth and must stay pre-correction. Values gain toward the
    corners (fp measures up to x1.384); results clip at white_level — a corner pixel
    pushed past white saturates exactly as the DNG rendering path intends.
    """
    img = raw.raw_image_visible
    colors = raw.raw_colors_visible
    h, w = img.shape
    blacks = np.asarray(black_levels or [0.0], dtype=np.float32)
    for m in maps:
        rows = np.arange(m.top, min(m.bottom, h), m.row_pitch)
        cols = np.arange(m.left, min(m.right, w), m.col_pitch)
        if rows.size == 0 or cols.size == 0:
            continue
        gains_grid = np.mean(np.asarray(m.gains, dtype=np.float64), axis=2)
        iv = np.clip(((rows + 0.5) / h - m.origin_v) / max(m.spacing_v, 1e-9), 0, m.points_v - 1)
        ih = np.clip(((cols + 0.5) / w - m.origin_h) / max(m.spacing_h, 1e-9), 0, m.points_h - 1)
        v0 = np.clip(np.floor(iv).astype(int), 0, m.points_v - 2) if m.points_v > 1 else np.zeros(rows.size, int)
        h0 = np.clip(np.floor(ih).astype(int), 0, m.points_h - 2) if m.points_h > 1 else np.zeros(cols.size, int)
        fv = (iv - v0)[:, None] if m.points_v > 1 else np.zeros((rows.size, 1))
        fh = (ih - h0)[None, :] if m.points_h > 1 else np.zeros((1, cols.size))
        g00 = gains_grid[v0][:, h0]
        g01 = gains_grid[v0][:, np.minimum(h0 + 1, m.points_h - 1)]
        g10 = gains_grid[np.minimum(v0 + 1, m.points_v - 1)][:, h0]
        g11 = gains_grid[np.minimum(v0 + 1, m.points_v - 1)][:, np.minimum(h0 + 1, m.points_h - 1)]
        gains = (g00 * (1 - fv) * (1 - fh) + g01 * (1 - fv) * fh
                 + g10 * fv * (1 - fh) + g11 * fv * fh)
        sub = img[np.ix_(rows, cols)].astype(np.float32)
        cidx = colors[np.ix_(rows, cols)]
        b = blacks[np.clip(cidx, 0, blacks.size - 1)] if blacks.size > 1 else np.float32(blacks[0])
        corrected = np.clip(b + (sub - b) * gains, 0.0, float(white_level))
        img[np.ix_(rows, cols)] = corrected.astype(img.dtype)


def _apply_vignette_render(render: Any, vignette: Any) -> Any:
    """Apply a post-demosaic FixVignetteRadial to the scene render, in row bands.

    g(r) = 1 + sum k_i (r/m)^(2(i+1)) with the optical centre at (cx_hat, cy_hat) and
    m the max centre-to-corner distance (DNG 1.4). A pure per-pixel scalar gain: it
    commutes with WB and matrices, so applying it to the finished linear render is
    exact. Output clips at the container maximum.
    """
    h, w = render.shape[:2]
    cx, cy = float(vignette.cx_hat) * w, float(vignette.cy_hat) * h
    m2 = max((cx) ** 2 + (cy) ** 2, (w - cx) ** 2 + (cy) ** 2,
             (cx) ** 2 + (h - cy) ** 2, (w - cx) ** 2 + (h - cy) ** 2)
    limit = float(np.iinfo(render.dtype).max) if np.issubdtype(render.dtype, np.integer) else None
    xs = (np.arange(w, dtype=np.float64) + 0.5 - cx) ** 2
    k = [float(v) for v in vignette.k]
    out = render
    for y0 in range(0, h, 512):
        y1 = min(y0 + 512, h)
        ys = (np.arange(y0, y1, dtype=np.float64) + 0.5 - cy) ** 2
        r2 = (ys[:, None] + xs[None, :]) / m2
        g = 1.0 + r2 * (k[0] + r2 * (k[1] + r2 * (k[2] + r2 * (k[3] + r2 * k[4]))))
        band = out[y0:y1].astype(np.float32) * g[:, :, None].astype(np.float32)
        if limit is not None:
            band = np.clip(band, 0.0, limit)
        out[y0:y1] = band.astype(render.dtype)
    return out

def _applied_wb_for_mode(
    wb_mode: str,
    camera_wb: list[float] | None,
    daylight_wb: list[float] | None,
    kelvin_wb: list[float] | None,
) -> list[float] | None:
    """The multipliers this decode actually applied, by mode."""
    if wb_mode == "daylight":
        return daylight_wb
    if kelvin_wb is not None:
        return kelvin_wb
    return camera_wb


def solve_wb_for_mode(
    wb_mode: str,
    path: Path,
    xyz_to_cam: Any | None,
    make: str | None = None,
    model: str | None = None,
) -> tuple[list[float] | None, str | None]:
    """(Fixed-Kelvin multipliers or None, degradation/provenance note or None).

    Calibration ladder, most trusted first: the file's own DNG dual-illuminant
    tags -> LibRaw's per-model Adobe matrix -> this project's fallback matrix
    table for bodies the installed LibRaw predates (camera_matrices.py; the note
    records the borrowed provenance). When every rung is missing the request
    DEGRADES instead of refusing: the caller renders with the camera's as-shot
    balance and must surface the returned note — a declared degradation is
    usable, a silent one would be a hidden white balance.
    """
    cct = kelvin_mode_cct(wb_mode)
    if cct is None:
        return None, None
    calibration = dng_metadata.read_dng_color_calibration(path)
    matrix = None
    if xyz_to_cam is not None:
        candidate = np.asarray(xyz_to_cam, dtype=np.float64)
        if candidate.size >= 9 and float(np.abs(candidate[:3, :3]).sum()) > 1e-9:
            matrix = candidate[:3, :3]
    note: str | None = None
    if calibration is None and matrix is None:
        from .camera_matrices import fallback_xyz_to_cam

        fallback = fallback_xyz_to_cam(make, model)
        if fallback is not None:
            matrix, source_note = fallback
            note = f"颜色标定来自回退矩阵表：{source_note}"
    try:
        return solve_kelvin_wb(cct, dng_calibration=calibration, xyz_to_cam=matrix), note
    except ValueError as exc:
        return None, (
            f"声明 {wb_mode} 白平衡不可用（{exc}）；已退化为相机 AsShot。"
            "该机型缺少颜色标定数据：结果可用，但白平衡声明与色彩精度可能有偏差"
        )


def camera_data_support_note(
    has_dng_calibration: bool,
    has_libraw_matrix: bool,
    fallback_available: bool,
    has_priors: bool,
    make: str | None,
    model: str | None,
) -> str | None:
    """One consolidated per-file marker: does this body have enough data to render
    accurately? None means fully supported. The render always proceeds — the marker
    is a truthful label, never a gate.

    Colour calibration is the rendering-accuracy data: without any matrix the
    decoder's colour conversion for this model is unanchored and the deviation is
    unpredictable (not merely "slightly off"). Missing sensor priors only degrade
    the *analysis* numbers (absolute stops/DR), so alone they do not raise this
    marker — the priors line already reports that honestly.
    """
    if has_dng_calibration or has_libraw_matrix:
        return None
    ident = f"{make or '?'} {model or '?'}".strip()
    if fallback_available:
        return (
            f"机型 {ident} 的颜色标定不在解码器数据表内：白平衡求解已由内置回退"
            "矩阵代偿，但解码器内部色彩转换仍无该机型矩阵，色彩精度可能有偏差。"
            "功能照常执行"
        )
    note = (
        f"机型 {ident} 暂无足够数据支撑准确运算（DNG 标签 / LibRaw 表 / 回退矩阵"
        "均无颜色标定）：输出图片结果可能有无法预测的偏差。功能照常执行"
    )
    if not has_priors:
        note += "；该机型亦无传感器先验，绝对档位/动态范围为单帧估计"
    return note


def libraw_wb_headroom_gain(wb_values: list[float] | None) -> float:
    """Container headroom LibRaw reserves for non-clipping highlight modes.

    With blend/reconstruct, LibRaw divides the whole post-WB image by the largest
    normalized WB multiplier so the boosted channel can be reconstructed above nominal
    sensor white without overflowing uint16. That is storage scaling, not exposure.
    """
    if not wb_values:
        return 1.0
    values = np.asarray(wb_values[:4], dtype=np.float64)
    values = values[np.isfinite(values) & (values > 0.0)]
    if values.size == 0:
        return 1.0
    normalized = values / max(float(np.min(values)), 1e-12)
    return float(max(1.0, np.max(normalized)))


def baseline_exposure_gain(baseline_exposure: float | None) -> float:
    """Linear gain for a DNG BaselineExposure, as a scale divisor rather than a multiply.

    BaselineExposure is file-authored baseline rendering compensation. It is not a
    measurement of capture exposure and does not target image content to middle gray.
    LibRaw ignores it outright (verified on iPhone files whose tags differ by 2 EV), while
    Core Image applies it unless overridden; honouring it here keeps both decoders faithful
    to the same DNG recipe before the user's explicit EV adjustment.

    It is applied by dividing scene_scale, never by scaling the buffer: the gain reaches
    5.65x on an iPhone low-light frame, which would clip everything above 0.18 in a uint16
    buffer normalised to sensor saturation. Dividing the scale leaves the codes untouched
    and re-interprets them, so no precision is lost and no highlight is destroyed.
    """
    if baseline_exposure is None:
        return 1.0
    value = float(baseline_exposure)
    if not np.isfinite(value):
        return 1.0
    # Guard against a corrupt tag rewriting the exposure by an absurd amount.
    return float(2.0 ** max(-8.0, min(8.0, value)))


def scene_green_median(scene_rgb: Any) -> float:
    """Robust green-channel level of one decoded scene-linear frame.

    The Core Image alignment ratio uses this statistic from both decoders. An earlier
    implementation divided both values by the same RAW-green median and called the
    results sensor gains; that common term cancels exactly. The operation is therefore a
    per-file decoded-level comparison, not absolute sensor calibration. It applies one
    scalar to the whole frame, so it preserves within-image light ratios and does not
    force a night scene or any other content toward 18% gray.
    """
    green = np.asarray(scene_rgb, dtype=np.float32)[:, :, 1].ravel()
    green = green[np.isfinite(green) & (green > 1e-4)]
    if green.size == 0:
        return float("nan")
    return float(np.median(green))


# Bounds on the Core Image alignment factor. Measured factors run 0.57..0.94 across iPhone
# 16 Pro and Sigma fp; anything far outside that means a statistic failed rather than a
# decoder disagreeing, and a render must not be destroyed by a bad measurement.
COREIMAGE_ALIGN_MIN = 0.25
COREIMAGE_ALIGN_MAX = 4.0


def coreimage_uses_file_alignment(mode: str) -> bool:
    """Whether a Core Image scale policy requests the LibRaw A/B reference render."""
    return mode == "aligned"


def coreimage_alignment_factor(reference_level: float, coreimage_level: float) -> float:
    """Scalar that matches RAW 9's decoded green median to LibRaw's for the same file.

    This makes decoder A/B comparisons share a practical exposure ruler. It is neither a
    sensor-absolute calibration nor auto exposure: there is no external brightness target,
    and all pixels receive the same factor. Because decoder color matrices, reconstruction,
    lens opcodes, and framing can affect the medians, ``unity`` remains available when the
    native Core Image scale itself is what should be inspected.

    Invalid or implausible measurements return identity. The caller records the failure so
    a silent fallback cannot be mistaken for a successful alignment.
    """
    if not (np.isfinite(reference_level) and np.isfinite(coreimage_level)):
        return 1.0
    if reference_level <= 0.0 or coreimage_level <= 0.0:
        return 1.0
    factor = float(reference_level) / float(coreimage_level)
    if factor < COREIMAGE_ALIGN_MIN or factor > COREIMAGE_ALIGN_MAX:
        return 1.0
    return factor


def libraw_scene_scale(
    encoded_max: float,
    highlight_mode_name: str,
    wb_values: list[float] | None,
    baseline_exposure: float | None = None,
) -> float:
    """Decode uint16 code values into one exposure unit independent of highlight mode."""
    scale = float(encoded_max)
    if highlight_mode_name != "clip":
        scale /= libraw_wb_headroom_gain(wb_values)
    return scale / baseline_exposure_gain(baseline_exposure)


def scene_rec2020_to_xyz_render(scene_rec2020: Any, scene_scale: float) -> Any:
    """Derive XYZ render buffer from a single Rec.2020 demosaic (same geometry as scene)."""
    from .color import rec2020_to_xyz

    scene = np.asarray(scene_rec2020)
    if np.issubdtype(scene.dtype, np.integer):
        flat = scene.reshape(-1, 3)
        out = np.empty((flat.shape[0], 3), dtype=np.uint16)
        chunk = 1_000_000
        for start in range(0, flat.shape[0], chunk):
            end = min(start + chunk, flat.shape[0])
            # Keep float64 here for byte-for-byte compatibility with the original
            # analysis buffer, but never materialize a full-frame float64 RGB copy.
            linear = flat[start:end].astype(np.float64) / float(scene_scale)
            xyz = rec2020_to_xyz(linear)
            max_linear = float(np.iinfo(out.dtype).max) / float(scene_scale)
            out[start:end] = (
                np.clip(xyz, 0.0, max_linear) * float(scene_scale)
            ).astype(np.uint16)
        return out.reshape(scene.shape)
    xyz = rec2020_to_xyz(scene.reshape(-1, 3)).reshape(scene.shape)
    return xyz.astype(scene.dtype, copy=False)


def render_to_xyz(
    raw: Any,
    highlight_mode_name: str = "clip",
    demosaic: Any = None,
    half_size: bool = False,
    wb_kwargs: dict[str, Any] | None = None,
) -> Any:
    if not hasattr(rawpy.ColorSpace, "XYZ"):
        raise RuntimeError("rawpy.ColorSpace.XYZ is not available; cannot make device-independent EV/gamut metrics")
    # Render-dependent analysis (luminance, EV, gamut risk) uses the SAME demosaic and
    # highlight mode as the export buffer, so the stats match the image you actually get.
    # user_flip=0 keeps it unrotated and aligned with the raw-domain CFA maps.
    return raw.postprocess(
        output_color=rawpy.ColorSpace.XYZ,
        gamma=(1, 1),
        half_size=half_size,
        demosaic_algorithm=(None if half_size else demosaic),
        no_auto_bright=True,
        adjust_maximum_thr=0.0,
        highlight_mode=rawpy_highlight_mode(highlight_mode_name),
        output_bps=16,
        user_flip=0,
        **(wb_kwargs or {"use_camera_wb": True}),
    )


def resolve_demosaic_algorithm(raw: Any, requested: str) -> Any:
    """Pick a DemosaicAlgorithm for the full-res export, or None (libraw default).

    Non-Bayer sensors (e.g. X-Trans) keep libraw's native path. 'auto' takes the best
    available Bayer detail algorithm (DHT preferred); an explicit request is honored when
    the build supports it, else it falls back to auto."""
    if rawpy is None:
        return None
    pattern = getattr(raw, "raw_pattern", None)
    is_bayer = pattern is not None and getattr(pattern, "shape", None) == (2, 2)
    if not is_bayer:
        return None

    def supported(name: str) -> Any:
        alg = getattr(rawpy.DemosaicAlgorithm, name.upper(), None)
        if alg is not None and getattr(alg, "isSupported", False):
            return alg
        return None

    if requested and requested != "auto":
        chosen = supported(requested)
        if chosen is not None:
            return chosen
    for name in DEMOSAIC_AUTO_PREFERENCE:
        chosen = supported(name)
        if chosen is not None:
            return chosen
    return None


def render_to_scene_rec2020(
    raw: Any,
    highlight_mode_name: str = "clip",
    half_size: bool = False,
    demosaic: Any = None,
    wb_kwargs: dict[str, Any] | None = None,
) -> Any:
    if not hasattr(rawpy.ColorSpace, "Rec2020"):
        raise RuntimeError("rawpy.ColorSpace.Rec2020 is not available; cannot make scene-linear export buffer")
    return raw.postprocess(
        output_color=rawpy.ColorSpace.Rec2020,
        gamma=(1, 1),
        half_size=half_size,
        demosaic_algorithm=(None if half_size else demosaic),
        no_auto_bright=True,
        adjust_maximum_thr=0.0,
        highlight_mode=rawpy_highlight_mode(highlight_mode_name),
        output_bps=16,
        user_flip=None,
        **(wb_kwargs or {"use_camera_wb": True}),
    )


def render_to_srgb8(raw: Any, highlight_mode_name: str = "clip") -> Any:
    return raw.postprocess(
        output_color=rawpy.ColorSpace.sRGB,
        gamma=(2.222, 4.5),
        no_auto_bright=True,
        adjust_maximum_thr=0.0,
        use_camera_wb=True,
        highlight_mode=rawpy_highlight_mode(highlight_mode_name),
        output_bps=8,
        user_flip=None,
    )


def channel_label(color_desc: str, cid: int) -> str:
    if 0 <= int(cid) < len(color_desc):
        return color_desc[int(cid)].upper()
    return str(cid)


def channel_black_level(black_levels: list[float], cid: int) -> float:
    if black_levels:
        return float(black_levels[int(cid) % len(black_levels)])
    return 0.0


def channel_fullwell(white_level: int, camera_white_levels: list[float], cid: int) -> float:
    if camera_white_levels and int(cid) < len(camera_white_levels) and camera_white_levels[int(cid)] > 0:
        return float(camera_white_levels[int(cid)])
    return float(white_level)


def _smoothstep(edge0: float, edge1: float, x: Any) -> Any:
    t = np.clip((x - np.float32(edge0)) / np.float32(max(edge1 - edge0, 1e-9)), 0.0, 1.0)
    return t * t * (np.float32(3.0) - np.float32(2.0) * t)


def _bin_2x2_max(mask: Any) -> Any:
    h, w = mask.shape[:2]
    h2 = max(1, h // 2)
    w2 = max(1, w // 2)
    cropped = mask[: h2 * 2, : w2 * 2]
    return cropped.reshape(h2, 2, w2, 2, mask.shape[2]).max(axis=(1, 3))


def _orient_like_libraw(arr: Any, flip: int) -> Any:
    # LibRaw/rawpy orientation values follow dcraw's common 0/3/5/6 codes.
    # Keep support for the full EXIF-style range so synthetic tests and unusual RAWs work.
    flip = int(flip or 0)
    if flip == 0 or flip == 1:
        return arr
    if flip == 2:
        return np.fliplr(arr)
    if flip == 3:
        return np.rot90(arr, 2)
    if flip == 4:
        return np.flipud(arr)
    if flip == 5:
        return np.rot90(arr, 1)
    if flip == 6:
        return np.rot90(arr, 3)
    if flip == 7:
        return np.fliplr(np.rot90(arr, 1))
    if flip == 8:
        return np.rot90(arr, 1)
    return arr


def _resize_mask_to_shape(mask: Any, shape: tuple[int, int]) -> Any:
    target_h, target_w = shape
    if mask.shape[:2] == (target_h, target_w):
        return mask
    from PIL import Image

    out = np.empty((target_h, target_w, mask.shape[2]), dtype=np.float32)
    for idx in range(mask.shape[2]):
        im = Image.fromarray(mask[:, :, idx].astype(np.float32, copy=False), mode="F")
        im = im.resize((target_w, target_h), Image.Resampling.BILINEAR)
        out[:, :, idx] = np.asarray(im, dtype=np.float32)
    return out


def _feather_masks(mask: Any) -> Any:
    # Small separable Gaussian-like kernel, enough to hide demosaic/half-size seams.
    kernel = np.asarray([1, 4, 6, 4, 1], dtype=np.float32) / np.float32(16.0)
    radius = len(kernel) // 2
    source = mask.astype(np.float32, copy=False)
    out = np.empty_like(source, dtype=np.float32)
    for channel in range(source.shape[2]):
        plane = source[:, :, channel]
        for axis in (0, 1):
            pad = [(0, 0), (0, 0)]
            pad[axis] = (radius, radius)
            padded = np.pad(plane, pad, mode="edge")
            acc = np.zeros_like(plane, dtype=np.float32)
            scratch = np.empty_like(plane, dtype=np.float32)
            for i, weight in enumerate(kernel):
                sl = [slice(None), slice(None)]
                sl[axis] = slice(i, i + plane.shape[axis])
                np.multiply(padded[tuple(sl)], np.float32(weight), out=scratch)
                np.add(acc, scratch, out=acc)
            plane = acc
        out[:, :, channel] = plane
    return np.clip(out, 0.0, 1.0)


def _build_bayer_clip_mask_planes(
    raw_image: Any,
    raw_pattern: Any,
    color_desc: str,
    white_level: int,
    black_levels: list[float],
    camera_white_levels: list[float],
) -> Any:
    """Build the 2x2-binned mask directly from Bayer planes.

    This is equivalent to constructing a full-resolution RGB mask and taking a
    2x2 maximum, but avoids the much larger intermediate arrays.
    """
    pattern = np.asarray(raw_pattern)
    if pattern.shape != (2, 2):
        return None
    h2 = raw_image.shape[0] // 2
    w2 = raw_image.shape[1] // 2
    if h2 == 0 or w2 == 0:
        return None
    binned = np.zeros((h2, w2, 3), dtype=np.float32)
    for row in range(2):
        for col in range(2):
            cid = int(pattern[row, col])
            label = channel_label(color_desc, cid)
            if label.startswith("R"):
                out_idx = 0
            elif label.startswith("G"):
                out_idx = 1
            elif label.startswith("B"):
                out_idx = 2
            else:
                continue
            black = channel_black_level(black_levels, cid)
            fullwell = channel_fullwell(white_level, camera_white_levels, cid)
            denom = max(fullwell - black, 1.0)
            plane = raw_image[
                row : row + h2 * 2 : 2,
                col : col + w2 * 2 : 2,
            ].astype(np.float32, copy=False)
            raw_norm = (plane - np.float32(black)) / np.float32(denom)
            channel_soft = _smoothstep(0.95, 0.99, raw_norm)
            np.maximum(binned[:, :, out_idx], channel_soft, out=binned[:, :, out_idx])
    return binned


def build_clip_masks(
    raw_image: Any,
    raw_colors: Any,
    color_desc: str,
    white_level: int,
    black_levels: list[float],
    camera_white_levels: list[float],
    orientation_flip: int,
    scene_shape: tuple[int, int],
    raw_pattern: Any | None = None,
) -> Any:
    """Build half-resolution RGB soft clip masks from pre-WB raw DN values."""
    binned = _build_bayer_clip_mask_planes(
        raw_image,
        raw_pattern,
        color_desc,
        white_level,
        black_levels,
        camera_white_levels,
    )
    if binned is None:
        h, w = raw_image.shape[:2]
        soft = np.zeros((h, w, 3), dtype=np.float32)
        for cid in np.unique(raw_colors):
            cid_int = int(cid)
            label = channel_label(color_desc, cid_int)
            if label.startswith("R"):
                out_idx = 0
            elif label.startswith("G"):
                out_idx = 1
            elif label.startswith("B"):
                out_idx = 2
            else:
                continue
            black = channel_black_level(black_levels, cid_int)
            fullwell = channel_fullwell(white_level, camera_white_levels, cid_int)
            denom = max(fullwell - black, 1.0)
            raw_norm = (raw_image.astype(np.float32, copy=False) - np.float32(black)) / np.float32(denom)
            channel_soft = _smoothstep(0.95, 0.99, raw_norm)
            soft[:, :, out_idx] = np.maximum(
                soft[:, :, out_idx], np.where(raw_colors == cid_int, channel_soft, 0.0)
            )
        binned = _bin_2x2_max(soft)
    oriented = _orient_like_libraw(binned, orientation_flip)
    aligned = _resize_mask_to_shape(oriented, scene_shape)
    return _feather_masks(aligned).astype(np.float16, copy=False)


def refresh_clip_masks_from_fullwell(
    bundle: RawBundle, channel_fullwell: dict[int, int]
) -> bool:
    """Rebuild LibRaw's soft headroom mask when analysis found a real saturation pile.

    load_raw needs an initial mask before analysis exists, so it starts from metadata
    per-channel white levels. Once analysis has a trustworthy observed full well, the
    render permission map must use that same per-channel endpoint; otherwise hard clip
    statistics and near-clip color retreat can disagree on cameras whose metadata white
    is inaccurate. Returns whether a rebuild was needed.
    """
    if getattr(bundle, "scene_decoder", "libraw") != "libraw":
        return False
    if getattr(bundle, "clip_masks", None) is None or not channel_fullwell:
        return False
    channel_ids = [int(x) for x in sorted(np.unique(bundle.raw_colors).tolist())]
    metadata_levels = {
        cid: int(
            bundle.camera_white_levels[cid]
            if cid < len(bundle.camera_white_levels)
            and bundle.camera_white_levels[cid] > 0
            else bundle.white_level
        )
        for cid in channel_ids
    }
    resolved = {
        cid: int(channel_fullwell.get(cid, metadata_levels[cid])) for cid in channel_ids
    }
    current = getattr(bundle, "_clip_mask_fullwell", None) or metadata_levels
    if resolved == current:
        return False
    resolved_levels = [0.0] * (max(channel_ids) + 1 if channel_ids else 0)
    for cid in channel_ids:
        resolved_levels[cid] = float(channel_fullwell.get(cid, metadata_levels[cid]))
    bundle.clip_masks = build_clip_masks(
        bundle.raw_image,
        bundle.raw_colors,
        bundle.color_desc,
        bundle.white_level,
        bundle.black_levels,
        resolved_levels,
        bundle.orientation_flip,
        bundle.scene_rec2020_render.shape[:2],
        bundle.raw_pattern,
    )
    bundle._clip_masks_cache_shape = None
    bundle._clip_masks_resized = None
    bundle._clip_mask_fullwell = resolved
    bundle.raw_guidance = None
    bundle._raw_guidance_cache_shape = None
    bundle._raw_guidance_resized = None
    bundle._raw_guidance_has_sensor_snr = False
    return True


def _unsupported_format_guidance(path: Path, shot: Any, exc: Exception) -> str:
    """A targeted refusal for files LibRaw cannot open — name the cause, give outs.

    New-body decode failures split into two classes. Colour-table gaps degrade
    gracefully elsewhere (SENSOR_SUPPORT ladder); FORMAT gaps stop the decoder
    cold, and the honest response is a precise diagnosis instead of a generic
    "unsupported". The canonical case: Nikon High Efficiency (HE/HE*) NEFs use
    intoPIX TicoRAW, which LibRaw (and darktable's rawspeed) cannot licence —
    even LibRaw master fails on them, so the fallback matrix table cannot help.
    """
    ident = f"{shot.make or '?'} {shot.model or '?'}".strip()
    lines = [
        f"LibRaw 无法打开此文件（机型 {ident}，{path.suffix or '无后缀'}）：{exc}",
    ]
    if path.suffix.lower() == ".nef":
        lines += [
            "若这是较新的尼康机身（Z9/Z8/Z6III/Z50II 世代）且拍摄时选择了"
            "高效压缩（HE/HE*），则该格式使用 intoPIX TicoRAW 编码，LibRaw "
            "因授权无法解码——升级 LibRaw 也无济于事。可用的出路：",
            "  1. 用 Adobe DNG Converter（免费，支持 HE）把 NEF 转成 DNG，"
            "转换后本工具全功能可用；",
            "  2. 相机内改用『无损压缩』RAW（同世代机身的无损 NEF 可正常解码）；",
            "  3. Apple RAW 独立解码路径在开发中（当前 --decoder coreimage "
            "仍依赖 LibRaw 提供证据层，对此格式暂同样不可用）。",
        ]
    else:
        lines += [
            "若这是较新的机型，可尝试 tools/build_libraw_master.sh 升级到 "
            "LibRaw master 快照；仍失败请反馈样张（机型支持策略见 "
            "docs/SENSOR_SUPPORT.zh-CN.md）。",
        ]
    lines.append("用 `--support` 可查看此文件在两条解码线上的逐档支持报告。")
    return "\n".join(lines)


def load_raw(
    path: Path,
    scene_highlight_mode: str = "clip",
    scene_half_size: bool = False,
    demosaic: str = "auto",
    wb_mode: str = "camera",
    decoder: str = "libraw",
    coreimage_version: str = "auto",
    coreimage_scale: str = COREIMAGE_SCALE_DEFAULT_MODE,
) -> RawBundle:
    if not path.exists():
        raise FileNotFoundError(f"Input file does not exist: {path}")
    if not path.is_file():
        raise FileNotFoundError(f"Input path is not a file: {path}")
    if decoder not in DECODER_CHOICES:
        raise ValueError(f"unknown decoder: {decoder}; expected one of {DECODER_CHOICES}")
    rawpy_highlight_mode(scene_highlight_mode)
    # CIRAWFilter exposes one calibrated reconstruction path rather than LibRaw's
    # clip/blend/reconstruct switch. Its comparison reference must always use
    # reconstruction too, including direct Python API calls that kept the old "clip"
    # default; otherwise the reported mode and the scale calculation describe different
    # pipelines.
    effective_highlight_mode = (
        "reconstruct" if decoder == "coreimage" else scene_highlight_mode
    )
    shot = dng_metadata.read_dng_shot_info(path)

    scene_decoder = "libraw"
    scene_decoder_version: str | None = None
    scene_decoder_runtime: str | None = None
    scene_scale_mode: str | None = None
    scene_align_factor: float = 1.0
    scene_align_error: str | None = None
    scene_opcode_names: tuple[str, ...] = ()
    evidence_shape: tuple[int, int] | None = None
    scene_geometry_crop: tuple[float, float, float, float] | None = None
    scene_geometry_corr: float | None = None
    scene_rec2020_render: Any | None = None
    xyz_render: Any | None = None
    scene_scale = 1.0
    render_scale = 1.0
    clip_masks: Any | None = None
    lens_shading: str | None = None
    effective_baseline_exposure = shot.baseline_exposure
    baseline_exposure_baked_in = False

    try:
        with rawpy.imread(str(path)) as raw:
            raw_image = np.asarray(raw.raw_image_visible).copy()
            raw_colors = np.asarray(raw.raw_colors_visible).copy()
            if raw_image.size == 0 or raw_colors.size == 0:
                raise RuntimeError("decoded RAW has no visible sensor pixels")
            if raw_image.shape != raw_colors.shape:
                raise RuntimeError("raw_image_visible and raw_colors_visible shapes differ")

            white_level = getattr(raw, "white_level", None)
            if white_level is None:
                white_level = int(np.max(raw_image))
            else:
                white_level = int(white_level)

            daylight_attr = getattr(raw, "daylight_whitebalance", None)
            daylight_wb = [float(v) for v in daylight_attr] if daylight_attr is not None else None

            # Capture the CFA pattern BEFORE postprocess: libraw mutates raw_pattern
            # during demosaic on some sensors (X-Trans collapses 6x6 -> [[6]]), which
            # would poison the CFA-plane analysis downstream.
            raw_pattern_arr = getattr(raw, "raw_pattern", None)
            if raw_pattern_arr is None:
                raw_pattern: list[list[int]] = []
            else:
                raw_pattern = np.asarray(raw_pattern_arr).astype(int).tolist()

            black_attr = getattr(raw, "black_level_per_channel", None)
            wb_attr = getattr(raw, "camera_whitebalance", None)
            white_pc_attr = getattr(raw, "camera_white_level_per_channel", None)
            orientation_flip = int(getattr(getattr(raw, "sizes", object), "flip", 0) or 0)
            black_levels = list(black_attr) if black_attr is not None else []
            camera_wb = list(wb_attr) if wb_attr is not None else []
            camera_white_levels = list(white_pc_attr) if white_pc_attr is not None else []
            color_desc = decode_color_desc(getattr(raw, "color_desc", ""))
            # Declared fixed-Kelvin balances solve to multipliers through the file's
            # own calibration; the solve happens here so both decoders and the aligned
            # reference decode all share one set of numbers. A body missing every
            # calibration rung degrades to as-shot with a reported note instead of
            # refusing (opened-interface contract: usable, loudly imperfect).
            kelvin_wb, wb_note = solve_wb_for_mode(
                wb_mode, path, getattr(raw, "rgb_xyz_matrix", None),
                make=shot.make, model=shot.model,
            )
            # Consolidated per-body support marker (report + GUI): computed for the
            # LibRaw path only — a file Core Image agrees to decode is calibrated by
            # Apple's own tables by construction.
            camera_data_support: str | None = None
            if decoder == "libraw":
                from .camera_matrices import fallback_xyz_to_cam
                from .priors import find_priors

                matrix_attr = getattr(raw, "rgb_xyz_matrix", None)
                has_matrix = False
                if matrix_attr is not None:
                    m = np.asarray(matrix_attr, dtype=np.float64)
                    has_matrix = m.size >= 9 and float(np.abs(m[:3, :3]).sum()) > 1e-9
                camera_data_support = camera_data_support_note(
                    dng_metadata.read_dng_color_calibration(path) is not None,
                    has_matrix,
                    fallback_xyz_to_cam(shot.make, shot.model) is not None,
                    find_priors(shot.make, shot.model) is not None,
                    shot.make, shot.model,
                )
            wb_degradation: str | None = None
            kelvin_requested = kelvin_mode_cct(wb_mode) is not None
            if kelvin_requested and kelvin_wb is None:
                wb_degradation = wb_note
                if decoder == "libraw":
                    # The render below must not claim a balance it cannot realise.
                    wb_mode = "camera"
            elif wb_note:
                wb_degradation = wb_note
            if decoder == "libraw":
                # Dark-field correction the DNG asks for and LibRaw ignores: pre-demosaic
                # GainMaps (fp) and post-demosaic radial vignette (iPhone ProRAW). Warp
                # opcodes stay deliberately unapplied — geometry would break CFA-mask
                # alignment. Evidence copies above are pre-correction sensor truth.
                shading_ops = dng_metadata.read_dng_shading_ops(path)
                if shading_ops["gain_maps"]:
                    _apply_gain_maps_mosaic(
                        raw, shading_ops["gain_maps"],
                        [float(x) for x in black_levels], white_level,
                    )
                    lens_shading = "gainmap"
                wb_kwargs = wb_postprocess_kwargs(wb_mode, daylight_wb, kelvin_wb)
                demosaic_alg = resolve_demosaic_algorithm(raw, demosaic)
                scene_rec2020_render = render_to_scene_rec2020(
                    raw, effective_highlight_mode, scene_half_size, demosaic_alg, wb_kwargs
                )
                if scene_rec2020_render.ndim != 3 or scene_rec2020_render.shape[2] < 3:
                    raise RuntimeError("scene Rec.2020 render did not produce a 3-channel image")

                if np.issubdtype(scene_rec2020_render.dtype, np.integer):
                    encoded_max = float(np.iinfo(scene_rec2020_render.dtype).max)
                    applied_wb = _applied_wb_for_mode(
                        wb_mode, camera_wb, daylight_wb, kelvin_wb
                    )
                    scene_scale = libraw_scene_scale(
                        encoded_max,
                        effective_highlight_mode,
                        applied_wb,
                        baseline_exposure=shot.baseline_exposure,
                    )
                if shading_ops["vignette"] is not None:
                    scene_rec2020_render = _apply_vignette_render(
                        scene_rec2020_render, shading_ops["vignette"]
                    )
                    lens_shading = (
                        "vignette" if lens_shading is None else lens_shading + "+vignette"
                    )
                xyz_render = scene_rec2020_to_xyz_render(scene_rec2020_render, scene_scale)
                render_scale = scene_scale
                clip_masks = build_clip_masks(
                    raw_image,
                    raw_colors,
                    color_desc,
                    white_level,
                    [float(x) for x in black_levels],
                    [float(x) for x in camera_white_levels],
                    orientation_flip,
                    scene_rec2020_render.shape[:2],
                    raw_pattern,
                )
                evidence_shape = (
                    int(scene_rec2020_render.shape[0]),
                    int(scene_rec2020_render.shape[1]),
                )
    except FileNotFoundError:
        raise
    except rawpy.LibRawFileUnsupportedError as exc:
        raise RuntimeError(_unsupported_format_guidance(path, shot, exc)) from exc
    except Exception as exc:
        message = f"Cannot decode RAW file with rawpy/libraw: {exc}"
        if "unsupported file format" in str(exc).lower():
            message = _unsupported_format_guidance(path, shot, exc)
        raise RuntimeError(message) from exc

    if decoder == "coreimage":
        neutral_cct = kelvin_mode_cct(wb_mode)
        if wb_mode != "camera" and neutral_cct is None:
            # Fixed-Kelvin modes are declarations CIRAWFilter accepts natively
            # (neutralTemperature/neutralTint); "daylight" is defined as LibRaw's
            # metadata multipliers, and that specific mapping remains unvalidated.
            raise ValueError(
                "Core Image decoder supports --wb camera and the fixed-Kelvin modes; "
                "'daylight' (LibRaw's metadata multipliers) has no validated "
                "CIRAWFilter mapping"
            )
        from . import coreimage_decode

        if not coreimage_decode.available():
            raise RuntimeError(
                "Core Image decoder unavailable on this system "
                "(macOS + PyObjC Quartz / CIRAWFilter required)"
            )
        ci_float, info = coreimage_decode.decode_scene_rec2020(
            path,
            half_size=scene_half_size,
            version=coreimage_version,
            scale_compensation=coreimage_decode.scale_compensation_for_mode(
                coreimage_scale
            ),
            neutral_cct=neutral_cct,
        )
        scene_rec2020_render, scene_scale = coreimage_decode.scene_float_to_half(ci_float)
        ci_authored_baseline = info.get("baseline_exposure_authored")
        if ci_authored_baseline is not None:
            try:
                candidate = float(ci_authored_baseline)
            except (TypeError, ValueError, OverflowError):
                candidate = float("nan")
            if np.isfinite(candidate):
                # This is the exact decoder-version-specific value that was cleared, so
                # it is the authoritative value to restore on the Core Image path. The
                # metadata parser remains the fallback when the getter is unavailable.
                effective_baseline_exposure = candidate

        # Apple's direct scene-linear recipe clears BaselineExposure inside CIRAWFilter.
        # Restore the recorded file intent as a scale divisor, exactly as LibRaw does. If
        # an older API could not clear the property, leave the already baked gain alone.
        if bool(info.get("baseline_exposure_cleared")):
            scene_scale = float(scene_scale) / baseline_exposure_gain(
                effective_baseline_exposure
            )
        else:
            applied = info.get("baseline_exposure_applied")
            try:
                baseline_exposure_baked_in = abs(float(applied)) > 1e-6
            except (TypeError, ValueError, OverflowError):
                baseline_exposure_baked_in = True
        if coreimage_uses_file_alignment(coreimage_scale):
            # Align one decoded statistic per file. The half-size LibRaw reference bins
            # 2x2 superpixels and is cheap; measured factors track full resolution within
            # about 0.02 EV. This is a comparison policy, not a sensor calibration.
            reference_level = float("nan")
            coreimage_level = float("nan")
            try:
                # A fresh handle: reading the mosaic above leaves this LibRaw handle
                # unable to postprocess (LibRawOutOfOrderCallError).
                # The reference must decode under the same declared balance as the
                # Core Image render, or the green-median alignment would compare two
                # different illuminant interpretations of one scene.
                # If the Kelvin solve degraded (no calibration for this body), the
                # reference decode falls back to as-shot while Core Image realises
                # the declaration natively — a cross-balance alignment. Declared in
                # the degradation note rather than hidden; the scale keeps working
                # with reduced trust.
                reference_wb_mode = (
                    "camera" if (kelvin_requested and kelvin_wb is None) else wb_mode
                )
                if reference_wb_mode != wb_mode and wb_degradation:
                    wb_degradation += (
                        "；RAW9 主解码仍按声明色温（原生接口），但对齐参考解码退化为"
                        "相机 AsShot——尺度对齐跨光源，可信度降低"
                    )
                with rawpy.imread(str(path)) as reference_raw:
                    reference_scene = render_to_scene_rec2020(
                        reference_raw,
                        effective_highlight_mode,
                        True,
                        None,
                        wb_postprocess_kwargs(reference_wb_mode, daylight_wb, kelvin_wb),
                    )
                # Decode the reference with the same storage-scale contract as the main
                # LibRaw path. Normalising reconstruct by 65535 would lose its reserved
                # WB headroom and can shift this statistic by more than one EV.
                reference_scale = libraw_scene_scale(
                    float(np.iinfo(reference_scene.dtype).max),
                    effective_highlight_mode,
                    _applied_wb_for_mode(
                        reference_wb_mode, camera_wb, daylight_wb, kelvin_wb
                    ),
                    baseline_exposure=effective_baseline_exposure,
                )
                reference_level = scene_green_median(
                    np.asarray(reference_scene, dtype=np.float32) / reference_scale
                )
                coreimage_level = scene_green_median(
                    np.asarray(scene_rec2020_render, dtype=np.float32) / float(scene_scale)
                )
                raw_factor = reference_level / coreimage_level
                if not np.isfinite(raw_factor) or not (
                    COREIMAGE_ALIGN_MIN <= raw_factor <= COREIMAGE_ALIGN_MAX
                ):
                    raise ValueError(
                        f"implausible decoded-green alignment factor {raw_factor!r}"
                    )
            except Exception as exc:  # noqa: BLE001 - a render must not fail over a metric
                scene_align_error = f"{type(exc).__name__}: {exc}"
            scene_align_factor = coreimage_alignment_factor(
                reference_level, coreimage_level
            )
            scene_scale = float(scene_scale) / scene_align_factor
        xyz_render = scene_rec2020_to_xyz_render(scene_rec2020_render, scene_scale)
        render_scale = scene_scale
        scene_decoder = "coreimage"
        scene_decoder_version = str(info.get("version") or coreimage_version)
        scene_decoder_runtime = str(info.get("decoder_runtime_id") or "") or None
        scene_scale_mode = coreimage_scale
        scene_opcode_names = tuple(coreimage_decode.read_dng_opcodes(path)["names"])
        # Strict Core Image pipeline: this is a SEPARATE path, not a LibRaw back end.
        # Core Image executes the file's DNG opcodes (measured on Sigma fp: per-plane
        # WarpRectilinear plus a lens-shading GainMap), so its frame is a nonlinear warp
        # of LibRaw's — corners move by tens of pixels. Per-pixel CFA evidence therefore
        # cannot be carried across, and pretending otherwise would put clip retreat on
        # the wrong pixels. Masks are dropped rather than re-mapped; the aggregate RAW
        # facts (levels, clip %, SNR, noise floor, WB testimony) stay valid because they
        # are distributions, not pixel positions, and continue to come from LibRaw.
        clip_masks = None
        evidence_shape = None
        scene_geometry_crop = None

    if scene_rec2020_render is None or xyz_render is None:
        raise RuntimeError("scene decoder did not produce a render buffer")

    return RawBundle(
        path=path,
        raw_image=raw_image,
        raw_colors=raw_colors,
        xyz_render=xyz_render,
        render_scale=render_scale,
        scene_rec2020_render=scene_rec2020_render,
        scene_scale=scene_scale,
        white_level=white_level,
        black_levels=[float(x) for x in black_levels],
        camera_wb=[float(x) for x in camera_wb],
        color_desc=color_desc,
        raw_pattern=raw_pattern,
        camera_white_levels=[float(x) for x in camera_white_levels],
        # RAW 9 has one calibrated reconstruction path. LibRaw's clip/blend/gated
        # selector does not map onto CIRAWFilter and must not be reported as if it did.
        scene_highlight_mode=effective_highlight_mode,
        orientation_flip=orientation_flip,
        wb_mode=wb_mode,
        wb_degradation=wb_degradation,
        camera_data_support=camera_data_support,
        daylight_wb=daylight_wb,
        shot_make=shot.make,
        shot_model=shot.model,
        shot_iso=shot.iso,
        baseline_exposure=effective_baseline_exposure,
        baseline_exposure_baked_in=baseline_exposure_baked_in,
        applied_wb=_applied_wb_for_mode(wb_mode, camera_wb, daylight_wb, kelvin_wb),
        lens_shading=lens_shading,
        clip_masks=clip_masks,
        scene_decoder=scene_decoder,
        scene_decoder_version=scene_decoder_version,
        scene_decoder_runtime=scene_decoder_runtime,
        scene_scale_mode=scene_scale_mode,
        scene_align_factor=scene_align_factor,
        scene_align_error=scene_align_error,
        scene_opcode_names=scene_opcode_names,
        evidence_shape=evidence_shape,
        scene_geometry_crop=scene_geometry_crop,
        scene_geometry_corr=scene_geometry_corr,
    )
