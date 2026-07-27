# SPDX-License-Identifier: GPL-3.0-or-later
"""RAW decode via rawpy and scene-linear render buffers."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ._deps import np, rawpy
from . import metadata as dng_metadata
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


def wb_postprocess_kwargs(wb_mode: str, daylight_wb: list[float] | None) -> dict[str, Any]:
    """Film-style fixed balance ('daylight', libraw's calibrated daylight multipliers)
    or the as-shot camera balance (default). One dict so every render agrees."""
    if wb_mode == "daylight" and daylight_wb is not None and any(v > 0 for v in daylight_wb[:3]):
        return {"use_camera_wb": False, "user_wb": [float(v) for v in daylight_wb[:4]]}
    if wb_mode not in WB_CHOICES:
        raise ValueError(f"unknown wb mode: {wb_mode}")
    return {"use_camera_wb": True}


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

    BaselineExposure is the file telling the renderer how much exposure it is expected to
    apply on top of the raw data. LibRaw ignores it outright (verified: on two iPhone
    frames whose tags differ by 2 EV, LibRaw's output ratio does not move with the tag),
    and Core Image applies it unless overridden, so honouring it is what makes the two
    paths agree about what a photograph's exposure is.

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


def raw_green_reference(
    raw_image: Any, raw_colors: Any, black_levels: list[float], white_level: float
) -> float:
    """Median green photosite level, black-subtracted and white-normalised.

    The one statistic both decoders can be measured against, because it is upstream of
    both. Green because it is the least-amplified channel — the most sensitive one, so
    white balance gives it the smallest multiplier and it normalises to 1.0 — which keeps
    white balance out of the comparison. Note that this is a property of the applied gain,
    not of the reported multiplier array: Fuji returns camera_whitebalance unnormalised
    ([581, 302, 544] rather than green at 1.0), so the array must never be read for this.
    Measuring the effective gain empirically sidesteps the whole convention question.
    """
    colors = np.asarray(raw_colors)
    green = np.isin(colors, (1, 3))
    if not np.any(green):
        return float("nan")
    black = float(np.mean(black_levels)) if black_levels else 0.0
    span = max(float(white_level) - black, 1.0)
    values = (np.asarray(raw_image, dtype=np.float32)[green] - black) / span
    values = values[values > 0.0]
    if values.size == 0:
        return float("nan")
    return float(np.median(values))


def scene_green_gain(scene_rgb: Any, raw_green_median: float) -> float:
    """Effective raw -> scene-linear green gain of a decoded buffer.

    A gain rather than a level: a dark scene shrinks numerator and denominator together,
    so normalising it aligns the two decoders' scales without touching how bright the
    photograph is. That distinction is the whole point — auto exposure moves the level and
    turns a night scene grey; this moves the ruler.
    """
    if not np.isfinite(raw_green_median) or raw_green_median <= 0.0:
        return float("nan")
    green = np.asarray(scene_rgb, dtype=np.float32)[:, :, 1].ravel()
    green = green[green > 1e-4]
    if green.size == 0:
        return float("nan")
    return float(np.median(green)) / float(raw_green_median)


# Bounds on the Core Image alignment factor. Measured factors run 0.57..0.94 across iPhone
# 16 Pro and Sigma fp; anything far outside that means a statistic failed rather than a
# decoder disagreeing, and a render must not be destroyed by a bad measurement.
COREIMAGE_ALIGN_MIN = 0.25
COREIMAGE_ALIGN_MAX = 4.0


def coreimage_alignment_factor(reference_gain: float, coreimage_gain: float) -> float:
    """Scale that puts the Core Image buffer on the LibRaw path's exposure scale.

    LibRaw's raw->output green gain is analytic and stable: measured 2**BaselineExposure
    times 1.02 +/- 0.08 EV across Sigma fp, iPhone 16 Pro and Fuji X-Trans. Apple's is
    not — 1.08 to 2.18 over the same files, varying by camera and by scene, because its
    1.0 is an estimate of *this frame's* diffuse white. No constant can align them, which
    is why two fitted ones (1/0.9314, then 1/1.0293) both failed; it has to be measured
    per file.
    """
    if not (np.isfinite(reference_gain) and np.isfinite(coreimage_gain)):
        return 1.0
    if reference_gain <= 0.0 or coreimage_gain <= 0.0:
        return 1.0
    factor = float(reference_gain) / float(coreimage_gain)
    return float(min(COREIMAGE_ALIGN_MAX, max(COREIMAGE_ALIGN_MIN, factor)))


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
    shot = dng_metadata.read_dng_shot_info(path)

    scene_decoder = "libraw"
    scene_decoder_version: str | None = None
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
            if decoder == "libraw":
                wb_kwargs = wb_postprocess_kwargs(wb_mode, daylight_wb)
                demosaic_alg = resolve_demosaic_algorithm(raw, demosaic)
                scene_rec2020_render = render_to_scene_rec2020(
                    raw, scene_highlight_mode, scene_half_size, demosaic_alg, wb_kwargs
                )
                if scene_rec2020_render.ndim != 3 or scene_rec2020_render.shape[2] < 3:
                    raise RuntimeError("scene Rec.2020 render did not produce a 3-channel image")

                if np.issubdtype(scene_rec2020_render.dtype, np.integer):
                    encoded_max = float(np.iinfo(scene_rec2020_render.dtype).max)
                    applied_wb = daylight_wb if wb_mode == "daylight" else camera_wb
                    scene_scale = libraw_scene_scale(
                        encoded_max,
                        scene_highlight_mode,
                        applied_wb,
                        baseline_exposure=shot.baseline_exposure,
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
    except Exception as exc:
        raise RuntimeError(f"Cannot decode RAW file with rawpy/libraw: {exc}") from exc

    if decoder == "coreimage":
        if wb_mode != "camera":
            raise ValueError(
                "Core Image decoder currently supports only --wb camera; "
                "daylight multipliers have no validated CIRAWFilter mapping yet"
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
        )
        scene_rec2020_render, scene_scale = coreimage_decode.scene_float_to_half(ci_float)
        # Put the buffer on the LibRaw path's exposure scale, measured per file. The
        # reference render is half-size on purpose: at that size LibRaw bins 2x2
        # superpixels instead of demosaicing, so it costs ~0.2 s, and the factor it
        # yields matches the full-resolution one to 1.3 % (0.019 EV) across four frames.
        reference_gain = float("nan")
        coreimage_gain = float("nan")
        try:
            # A fresh handle: `raw` has already had its mosaic read on this path, and
            # LibRaw refuses postprocess afterwards (LibRawOutOfOrderCallError).
            with rawpy.imread(str(path)) as reference_raw:
                reference_scene = render_to_scene_rec2020(
                    reference_raw, scene_highlight_mode, True, None,
                    wb_postprocess_kwargs(wb_mode, daylight_wb),
                )
            raw_green = raw_green_reference(
                raw_image, raw_colors, black_levels, white_level
            )
            # Decode the reference exactly as the LibRaw path would, via its own scale
            # function. Normalising by the container maximum instead silently loses the
            # WB headroom division that non-clip highlight modes apply — and this path
            # forces "reconstruct", so that error is always live here. Measured on an
            # iPhone frame whose largest WB multiplier is 2.981, it made the reference
            # gain 2.98x too small and drove the factor into its guard rail.
            reference_scale = libraw_scene_scale(
                float(np.iinfo(reference_scene.dtype).max),
                scene_highlight_mode,
                daylight_wb if wb_mode == "daylight" else camera_wb,
                baseline_exposure=shot.baseline_exposure,
            )
            reference_gain = scene_green_gain(
                np.asarray(reference_scene, dtype=np.float32) / reference_scale,
                raw_green,
            )
            coreimage_gain = scene_green_gain(
                np.asarray(scene_rec2020_render, dtype=np.float32) / float(scene_scale),
                raw_green,
            )
        except Exception as exc:  # noqa: BLE001 - a render must not fail over a metric
            # Never silently: an identity factor here is indistinguishable from a working
            # alignment in the output, which is exactly how this shipped broken once.
            scene_align_error = f"{type(exc).__name__}: {exc}"
        scene_align_factor = coreimage_alignment_factor(reference_gain, coreimage_gain)
        scene_scale = float(scene_scale) / scene_align_factor
        xyz_render = scene_rec2020_to_xyz_render(scene_rec2020_render, scene_scale)
        render_scale = scene_scale
        scene_decoder = "coreimage"
        scene_decoder_version = str(info.get("version") or coreimage_version)
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
        scene_highlight_mode=("reconstruct" if decoder == "coreimage" else scene_highlight_mode),
        orientation_flip=orientation_flip,
        wb_mode=wb_mode,
        daylight_wb=daylight_wb,
        shot_make=shot.make,
        shot_model=shot.model,
        shot_iso=shot.iso,
        baseline_exposure=shot.baseline_exposure,
        clip_masks=clip_masks,
        scene_decoder=scene_decoder,
        scene_decoder_version=scene_decoder_version,
        scene_scale_mode=scene_scale_mode,
        scene_align_factor=scene_align_factor,
        scene_align_error=scene_align_error,
        scene_opcode_names=scene_opcode_names,
        evidence_shape=evidence_shape,
        scene_geometry_crop=scene_geometry_crop,
        scene_geometry_corr=scene_geometry_corr,
    )
