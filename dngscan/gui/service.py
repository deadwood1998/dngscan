# SPDX-License-Identifier: GPL-3.0-or-later
"""Preview/export job logic for the local web GUI."""
from __future__ import annotations

import base64
import io
import math
import multiprocessing as mp
import threading
from queue import Empty
from pathlib import Path
from typing import Any

import dngscan as dg
from dngscan.debug_util import maybe_print_exc
from dngscan.grade import RENDER_MODE, resolve_grade_params

from .constants import RAW_EXTS
from .preview_cache import PREVIEW_STORE, PreviewEntry, downsample_mean


# Kept as aliases for callers/tests that clear the in-process proxy cache.
PREVIEW_CACHE = PREVIEW_STORE.entries
PREVIEW_CACHE_LOCK = PREVIEW_STORE.lock
RENDER_LOCK = threading.Lock()


def make_preview_b64(path: Path, width: int | None = 1280, icc_profile: bytes | None = None) -> str:
    from PIL import Image

    with Image.open(path) as src:
        if icc_profile is None:
            icc_profile = src.info.get("icc_profile")
        im = src.convert("RGB")
    if width is not None and im.width > width:
        im = im.resize((width, round(im.height * width / im.width)))
    buf = io.BytesIO()
    save_kwargs = {"format": "JPEG", "quality": 85}
    if icc_profile:
        save_kwargs["icc_profile"] = icc_profile
    im.save(buf, **save_kwargs)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def preview_b64_from_u8(
    rgb_u8: object,
    icc_profile: bytes | None = None,
    width: int | None = None,
) -> str:
    from PIL import Image

    im = Image.fromarray(rgb_u8, "RGB")
    if width is not None and im.width > width:
        im = im.resize((width, round(im.height * width / im.width)))
    buf = io.BytesIO()
    save_kwargs = {"format": "JPEG", "quality": 85}
    if icc_profile:
        save_kwargs["icc_profile"] = icc_profile
    im.save(buf, **save_kwargs)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def annotate_preview_rgb_u8(rgb_u8: object, lines: list[str]) -> object:
    from PIL import Image, ImageDraw, ImageFont

    np = dg.np
    if np is None or not lines:
        return rgb_u8
    base = np.asarray(rgb_u8, dtype=np.uint8)
    im = Image.fromarray(base, "RGB")
    overlay = Image.new("RGBA", im.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    w, h = im.size
    pad = max(10, h // 100)
    font_size = max(16, h // 42)
    font = None
    for path in (
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
    ):
        try:
            font = ImageFont.truetype(path, font_size)
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()
    line_gap = max(4, font_size // 6)
    text_heights = []
    text_widths = []
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        text_widths.append(bbox[2] - bbox[0])
        text_heights.append(bbox[3] - bbox[1])
    box_w = max(text_widths) + pad * 2
    box_h = sum(text_heights) + line_gap * (len(lines) - 1) + pad * 2
    draw.rectangle((pad, pad, pad + box_w, pad + box_h), fill=(12, 16, 24, 210))
    y_cursor = pad + pad // 2
    for line, th in zip(lines, text_heights):
        draw.text((pad * 2, y_cursor), line, fill=(255, 236, 170, 255), font=font)
        y_cursor += th + line_gap
    composed = Image.alpha_composite(im.convert("RGBA"), overlay)
    return np.asarray(composed.convert("RGB"), dtype=np.uint8)


def auto_ev_payload(result: dg.AutoEvResult | None) -> dict | None:
    if result is None:
        return None
    return {
        "ev": result.ev,
        "ev_boost": result.ev_boost,
        "ev_median_target": result.ev_median_target,
        "highlight_limited": result.highlight_limited,
        "highlight_cap_ev": result.highlight_cap_ev,
        "anchored_median_ev": result.anchored_median_ev,
    }


def preview_metrics_from_u8(rgb_u8: object, gamut: str) -> dict[str, float]:
    np = dg.np
    if np is None:
        return {}
    rgb = np.asarray(rgb_u8, dtype=np.uint8)
    flat_u8 = rgb.reshape(-1, 3)
    encoded = flat_u8.astype(np.float32) / np.float32(255.0)
    linear = dg.srgb_decode(encoded)
    max_channel = np.max(flat_u8, axis=1)
    weights = dg.RGB_TO_XYZ[dg.output_gamut_space(gamut)][1].astype(np.float32)
    y = (
        weights[0] * linear[:, 0].astype(np.float32)
        + weights[1] * linear[:, 1].astype(np.float32)
        + weights[2] * linear[:, 2].astype(np.float32)
    )
    return {
        "luma_p999_pct": float(np.percentile(y, 99.9) * 100.0),
        "near_white_pct": float(np.mean(max_channel >= 250) * 100.0),
        "clipped_channel_pct": float(np.mean(max_channel >= 254) * 100.0),
    }


def output_luminance_metrics_u8(encoded_u8: object, gamut: str, ev: float) -> dict[str, float]:
    np = dg.np
    if np is None:
        return {}
    encoded_u8 = np.asarray(encoded_u8, dtype=np.uint8)
    flat_u8 = encoded_u8.reshape(-1, 3)
    matrix = dg.RGB_TO_XYZ[dg.output_gamut_space(gamut)]
    y = np.empty((flat_u8.shape[0],), dtype=np.float32)
    max_channel = np.empty((flat_u8.shape[0],), dtype=np.float32)
    near_count = 0
    clipped_count = 0
    chunk = 1_000_000
    for start in range(0, flat_u8.shape[0], chunk):
        end = min(start + chunk, flat_u8.shape[0])
        piece_u8 = flat_u8[start:end]
        encoded = piece_u8.astype(np.float32) / np.float32(255.0)
        linear = dg.srgb_decode(encoded)
        y[start:end] = (
            matrix[1, 0] * linear[:, 0]
            + matrix[1, 1] * linear[:, 1]
            + matrix[1, 2] * linear[:, 2]
        )
        max_channel[start:end] = np.max(linear, axis=1)
        max_u8 = np.max(piece_u8, axis=1)
        near_count += int(np.count_nonzero(max_u8 >= 250))
        clipped_count += int(np.count_nonzero(max_u8 >= 254))
    y = np.clip(np.nan_to_num(y, nan=0.0, posinf=1.0, neginf=0.0), 0.0, 1.0)
    y_p99, y_p999 = [float(v) for v in np.percentile(y, [99.0, 99.9])]
    max_p999 = float(np.percentile(max_channel, 99.9))
    headroom_luma_ev = math.log2(0.95 / max(y_p999, 1e-9))
    headroom_rgb_ev = math.log2(0.98 / max(max_p999, 1e-9))
    return {
        "median_luma_pct": float(np.median(y) * 100.0),
        "mean_luma_pct": float(np.mean(y) * 100.0),
        "luma_p99_pct": y_p99 * 100.0,
        "luma_p999_pct": y_p999 * 100.0,
        "max_channel_p999_pct": max_p999 * 100.0,
        "near_white_pct": float(near_count / max(flat_u8.shape[0], 1) * 100.0),
        "clipped_channel_pct": float(clipped_count / max(flat_u8.shape[0], 1) * 100.0),
        "headroom_luma_ev": float(headroom_luma_ev),
        "headroom_rgb_ev": float(headroom_rgb_ev),
        "estimated_ev_before_luma_limit": float(ev + headroom_luma_ev),
    }


def output_luminance_metrics(path: Path, gamut: str, ev: float) -> dict[str, float]:
    from PIL import Image

    with Image.open(path) as im:
        encoded_u8 = dg.np.asarray(im.convert("RGB"), dtype=dg.np.uint8)
    return output_luminance_metrics_u8(encoded_u8, gamut, ev)


def output_metrics_from_linear(rgb_linear: object, gamut: str) -> dict[str, float]:
    np = dg.np
    if np is None:
        return {}
    rgb = np.clip(np.nan_to_num(rgb_linear, nan=0.0, posinf=1.0, neginf=0.0), 0.0, 1.0)
    matrix = dg.RGB_TO_XYZ[dg.output_gamut_space(gamut)]
    y = matrix[1, 0] * rgb[:, 0] + matrix[1, 1] * rgb[:, 1] + matrix[1, 2] * rgb[:, 2]
    y = np.clip(np.nan_to_num(y, nan=0.0, posinf=1.0, neginf=0.0), 0.0, 1.0)
    max_channel = np.max(rgb, axis=1)
    y_p999 = float(np.percentile(y, 99.9))
    max_p999 = float(np.percentile(max_channel, 99.9))
    return {
        "luma_p999_pct": y_p999 * 100.0,
        "max_channel_p999_pct": max_p999 * 100.0,
        "near_white_pct": float(np.mean(max_channel >= np.float32(0.956)) * 100.0),
        "clipped_channel_pct": float(np.mean(np.any(rgb >= np.float32(0.999), axis=1)) * 100.0),
    }


def estimate_ev_headroom(
    bundle: dg.RawBundle,
    analysis: dg.Analysis | None,
    gamut: str,
    current_ev: float,
    max_samples: int = 220_000,
    look: str = "none",
    look_strength: float = 1.0,
    display_filter: str = "none",
    filter_strength: float = 1.0,
    scene_transform: str = "none",
    scene_transform_strength: float = 1.0,
    punch_scale: float = 1.0,
    tone_core: str = "agx",
    lum_norm: str = "y",
    agx_primaries: str = "base",
    adjustments: dg.RenderAdjustments | None = None,
) -> dict[str, float | str]:
    if analysis is None:
        return {}
    safe_ev = dg.max_safe_ev(
        bundle,
        analysis,
        gamut,
        from_ev=current_ev,
        max_samples=max_samples,
        look=look,
        look_strength=look_strength,
        display_filter=display_filter,
        filter_strength=filter_strength,
        scene_transform=scene_transform,
        scene_transform_strength=scene_transform_strength,
        punch_scale=punch_scale,
        tone_core=tone_core,
        lum_norm=lum_norm,
        agx_primaries=agx_primaries,
        adjustments=adjustments,
    )
    return {
        "safe_ev_remaining": max(0.0, float(safe_ev - current_ev)),
        "estimated_safe_ev": float(safe_ev),
        "headroom_limit": "p99.9高光/通道顶白/近白比例阈值",
    }


def list_dir(raw: str) -> dict:
    p = Path(raw).expanduser() if raw else Path.home()
    if not p.is_dir():
        p = Path.home()
    dirs: list[str] = []
    files: list[str] = []
    try:
        for entry in sorted(p.iterdir(), key=lambda x: x.name.lower()):
            try:
                if entry.name.startswith("."):
                    continue
                if entry.is_dir():
                    dirs.append(entry.name)
                elif entry.suffix.lower() in RAW_EXTS:
                    files.append(entry.name)
            except OSError:
                continue
    except PermissionError:
        pass
    return {"cwd": str(p), "parent": str(p.parent), "dirs": dirs, "files": files}


def raw9_support(params: dict) -> dict:
    """Return a cheap per-file RAW 9 capability probe for the GUI."""
    inp = Path(str(params.get("input", ""))).expanduser()
    if not inp.is_file():
        raise FileNotFoundError(f"文件不存在：{inp}")
    from dngscan import coreimage_decode
    from dngscan.decode_support import probe_decode_support

    try:
        support_lines = probe_decode_support(inp)["lines"]
    except Exception as exc:  # the probe must never block the GUI flow
        support_lines = [f"支持探测失败：{exc}"]
    probe = coreimage_decode.probe_raw9_support(inp)
    offered = [str(value) for value in probe["versions_offered"]]
    fallback = probe["fallback_version"]
    if not probe["coreimage_available"]:
        message = "此系统没有可用的 Apple Core Image RAW 解码器。"
    elif probe["error"]:
        message = f"Apple RAW 无法打开这个文件：{probe['error']}"
    elif probe["raw9_supported"]:
        message = "此文件支持 Apple RAW 9。"
    elif fallback is not None:
        message = f"此文件不支持 Apple RAW 9；系统最高可使用 RAW {fallback}。"
    else:
        detail = "、".join(offered) if offered else "无"
        message = f"此文件不支持 Apple RAW 9，也没有可用的 RAW 8/7 降级路径（报告版本：{detail}）。"
    return {
        "ok": True,
        "support_lines": support_lines,
        "coreimage_available": bool(probe["coreimage_available"]),
        "raw9_supported": bool(probe["raw9_supported"]),
        "versions_offered": offered,
        "fallback_version": fallback,
        "probe_error": probe["error"],
        "message": message,
    }


def parse_job_params(params: dict) -> tuple[Path, str, str, str, float, float, int, bool, Path | None, bool]:
    inp = Path(str(params["input"])).expanduser()
    if not inp.is_file():
        raise FileNotFoundError(f"文件不存在：{inp}")
    highlight = str(params.get("highlight", "clip"))
    if highlight not in ("clip", "blend", "reconstruct"):
        raise ValueError(f"未知高光处理：{highlight}")
    gamut = str(params.get("gamut", "srgb"))
    if gamut not in ("srgb", "p3"):
        raise ValueError(f"未知输出色域：{gamut}")
    output_format = str(params.get("format", "sdr"))
    if output_format not in dg.JPEG_OUTPUT_FORMATS:
        raise ValueError(f"未知输出格式：{output_format}")
    if dg.is_hdr_output_format(output_format):
        gamut = "p3"
    ev = float(params.get("ev", 0.0))
    hdr_headroom = float(params.get("hdrHeadroom", dg.DEFAULT_HDR_HEADROOM_EV))
    if not 0.0 <= hdr_headroom <= float(dg.MAX_HDR_HEADROOM_EV) + 1e-9:
        raise ValueError(
            f"HDR capacity 必须在 0–{dg.MAX_HDR_HEADROOM_EV:.6f} EV "
            "（对应最多 4000 nit）"
        )
    quality = int(params.get("quality", 100))
    if not 1 <= quality <= 100:
        raise ValueError("质量需在 1-100 之间")
    want_png = bool(params.get("png", False))
    outdir = Path(str(params["outdir"])).expanduser() if params.get("outdir") else None
    ev_auto = bool(params.get("evAuto", False))
    return inp, highlight, gamut, output_format, ev, hdr_headroom, quality, want_png, outdir, ev_auto


def plan_for_bundle(bundle: dg.RawBundle, analysis: dg.Analysis, gamut: str) -> dg.RenderPlan:
    return dg.build_render_plan(bundle, analysis, RENDER_MODE, gamut)


def parse_punch(params: dict) -> float:
    try:
        value = float(params.get("punch", 1.0))
    except (TypeError, ValueError):
        return 1.0
    return max(0.0, min(1.5, value))


def parse_render_adjustments(params: dict) -> dg.RenderAdjustments:
    fields = {
        "midtone_brightness": "midtoneBrightness",
        "midtone_contrast": "midtoneContrast",
        "shadow_transition": "shadowTransition",
        "highlight_transition": "highlightTransition",
        "highlight_fade": "highlightFade",
    }
    values: dict[str, float] = {}
    for field, key in fields.items():
        raw = params.get(key, params.get(field, 0.0))
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} 必须是数字") from exc
        if not math.isfinite(value) or not -1.0 <= value <= 1.0:
            raise ValueError(f"{key} 需在 -1 到 1 之间")
        values[field] = value
    return dg.RenderAdjustments(**values)


def parse_scene_transform(params: dict) -> tuple[str, float]:
    transform = dg.validate_scene_transform(str(params.get("sceneTransform", "none")))
    strength = float(params.get("sceneTransformStrength", params.get("scene_transform_strength", 1.0)))
    if not 0.0 <= strength <= 3.0:
        raise ValueError("scene transform 强度需在 0-3 之间")
    return transform, strength


def parse_tone_core(params: dict) -> tuple[str, str]:
    core = str(params.get("toneCore", params.get("tone_core", "agx")))
    norm = str(params.get("lumNorm", params.get("lum_norm", "y")))
    if core not in dg.TONE_CORE_CHOICES:
        raise ValueError(f"未知 tone 核：{core}")
    if norm not in dg.LUM_NORM_CHOICES:
        raise ValueError(f"未知 lum norm：{norm}")
    return core, norm


def parse_agx_primaries(params: dict) -> str:
    value = str(params.get("agxPrimaries", params.get("agx_primaries", "base")))
    resolved = dg.agx_engine.resolve_agx_primaries(value)
    if resolved not in dg.agx_engine.AGX_PRIMARIES_PRESETS:
        raise ValueError(f"未知 AgX 基调：{value}")
    return resolved


def parse_decoder(params: dict) -> tuple[str, str]:
    from dngscan.constants import COREIMAGE_VERSION_CHOICES, DECODER_CHOICES
    from dngscan import coreimage_decode

    decoder = str(params.get("decoder", "libraw"))
    version = str(params.get("coreimageVersion", params.get("coreimage_version", "auto")))
    if decoder not in DECODER_CHOICES:
        raise ValueError(f"未知解码器：{decoder}")
    if version not in COREIMAGE_VERSION_CHOICES:
        raise ValueError(f"未知 Core Image 版本：{version}")
    if decoder == "coreimage" and not coreimage_decode.available():
        raise RuntimeError("Core Image 解码器在此系统不可用（需要 macOS + PyObjC Quartz）")
    wb = str(params.get("wb", "camera"))
    if decoder == "coreimage" and wb == "daylight":
        raise ValueError(
            "Core Image 解码器不支持“相机日光标定”模式（LibRaw 元数据乘数无验证映射）；"
            "固定色温声明（如 5500K）与拍摄值均可用"
        )
    return decoder, version


def export_preview_jpeg(
    inp: Path,
    highlight: str,
    gamut: str,
    ev: float,
    quality: int,
    max_width: int = 1400,
    wb: str = "camera",
    look: str = "none",
    look_strength: float = 1.0,
    display_filter: str = "none",
    filter_strength: float = 1.0,
    scene_transform: str = "none",
    scene_transform_strength: float = 1.0,
    auto_ev: dg.AutoEvResult | None = None,
    punch_scale: float = 1.0,
    tone_core: str = "agx",
    lum_norm: str = "y",
    agx_primaries: str = "base",
    cached: PreviewEntry | None = None,
    adjustments: dg.RenderAdjustments | None = None,
    decoder: str = "libraw",
    coreimage_version: str = "auto",
    lens_filter: str = "none",
    film_curve: str = "none",
) -> dict:
    dg.require_dependencies()
    if decoder == "coreimage":
        highlight = "reconstruct"
    if cached is None:
        cached = PREVIEW_STORE.get(
            inp, highlight, wb, tone_core == "gated", decoder, coreimage_version
        )

    proxy_bundle = dg.with_intent_exposure(
        cached.bundle, user_ev=ev, tone_core=tone_core
    )
    if lens_filter != "none":
        # Shallow copy: cached proxy bundles are shared across requests and must not
        # inherit one request's declared glass.
        import dataclasses as _dc

        proxy_bundle = _dc.replace(proxy_bundle, lens_filter=lens_filter)
    with RENDER_LOCK:
        render_plan = dg.build_render_plan(
            proxy_bundle,
            cached.analysis,
            RENDER_MODE,
            gamut,
            scene_transform,
            scene_transform_strength,
            punch_scale,
            tone_core,
            lum_norm,
            agx_primaries=agx_primaries,
            film_curve=film_curve,
            adjustments=adjustments,
        )
        icc_profile = dg.output_icc_profile_bytes(gamut)
        rgb_u8 = dg.render_output_u8(
            proxy_bundle, cached.analysis, gamut, render_plan,
            look, look_strength, display_filter, filter_strength,
            scene_transform, scene_transform_strength,
            tone_core, lum_norm, agx_primaries,
        )
        if auto_ev is not None:
            rgb_u8 = annotate_preview_rgb_u8(rgb_u8, dg.auto_ev_overlay_lines(auto_ev))
        metrics = preview_metrics_from_u8(rgb_u8, gamut)
        preview = preview_b64_from_u8(rgb_u8, icc_profile=icc_profile)
    payload = {
        "ok": True,
        "preview": preview,
        "metrics": metrics,
        "metrics_kind": "preview",
        "gain": proxy_bundle.exposure_gain,
        "ev": ev,
        "highlight": dg.highlight_mode_cn(highlight),
        "gamut": dg.output_gamut_label(gamut),
        "scene_transform": dg.scene_transform_label(scene_transform),
        "scene_transform_strength": scene_transform_strength,
        "tone_core": tone_core,
        "lum_norm": lum_norm,
        "decoder": str(getattr(proxy_bundle, "scene_decoder", decoder) or decoder),
        "decoder_version": getattr(proxy_bundle, "scene_decoder_version", None),
        "ev_auto": auto_ev_payload(auto_ev),
    }
    return payload


def parse_grade(params: dict) -> tuple[str, float, str, float]:
    return resolve_grade_params(params)


def parse_film_params(params: dict) -> tuple[str, str]:
    lens_filter = dg.validate_lens_filter(str(params.get("lensFilter", params.get("lens_filter", "none"))))
    film_curve = str(params.get("filmCurve", params.get("film_curve", "none")))
    if film_curve not in dg.FILM_CURVE_CHOICES:
        raise ValueError(f"未知曲线预设：{film_curve}")
    return lens_filter, film_curve


def run_preview(params: dict) -> dict:
    inp, highlight, gamut, output_format, ev, _, quality, _, _, ev_auto = parse_job_params(params)
    wb = str(params.get("wb", "camera"))
    if wb not in dg.WB_CHOICES:
        raise ValueError(f"未知白平衡模式：{wb}")
    decoder, coreimage_version = parse_decoder(params)
    if decoder == "coreimage":
        highlight = "reconstruct"
    look, look_strength, display_filter, filter_strength = parse_grade(params)
    scene_transform, scene_transform_strength = parse_scene_transform(params)
    punch_scale = parse_punch(params)
    adjustments = parse_render_adjustments(params)
    if dg.is_hdr_output_format(output_format) and abs(float(adjustments.highlight_fade)) > 1e-9:
        raise RuntimeError("HDR 尚未定义显示侧高光褪白；请将该项恢复为自动")
    tone_core, lum_norm = parse_tone_core(params)
    if dg.is_hdr_output_format(output_format) and tone_core != "agx":
        raise RuntimeError("HDR 输出当前只实现 AgX tone core")
    agx_primaries = parse_agx_primaries(params)
    lens_filter, film_curve = parse_film_params(params)
    cached = PREVIEW_STORE.get(
        inp, highlight, wb, tone_core == "gated", decoder, coreimage_version
    )
    auto_ev_result = None
    if ev_auto:
        auto_ev_result = dg.compute_auto_ev(
            cached.bundle,
            cached.analysis,
            gamut,
            look=look,
            look_strength=look_strength,
            display_filter=display_filter,
            filter_strength=filter_strength,
            scene_transform=scene_transform,
            scene_transform_strength=scene_transform_strength,
            punch_scale=punch_scale,
            tone_core=tone_core,
            lum_norm=lum_norm,
            agx_primaries=agx_primaries,
            adjustments=adjustments,
        )
        ev = auto_ev_result.ev
    return export_preview_jpeg(
        inp,
        highlight,
        gamut,
        ev,
        min(quality, 95),
        wb=wb,
        look=look,
        look_strength=look_strength,
        display_filter=display_filter,
        filter_strength=filter_strength,
        scene_transform=scene_transform,
        scene_transform_strength=scene_transform_strength,
        auto_ev=auto_ev_result,
        punch_scale=punch_scale,
        tone_core=tone_core,
        lum_norm=lum_norm,
        agx_primaries=agx_primaries,
        cached=cached,
        adjustments=adjustments,
        decoder=decoder,
        coreimage_version=coreimage_version,
        lens_filter=lens_filter,
        film_curve=film_curve,
    )


def _finite_or_none(value: object) -> float | None:
    """JSON-safe float: json.dumps emits bare NaN, which JSON.parse rejects."""
    try:
        v = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def detected_scene_params(bundle: dg.RawBundle, analysis: dg.Analysis) -> dict:
    """Measured scene facts that inform the user's later adjustments.

    Compiled from the same plan machinery the render will use, at the proxy decode's
    resolution. These are the numbers the pipeline itself consults — the reliable tail
    that budgets HDR, the clipping share that withdraws chroma freedom, the compiled
    curve endpoints — surfaced before any slider is touched.
    """
    plan = dg.build_render_plan(bundle, analysis, RENDER_MODE, "p3")
    scene = plan.scene
    tone = plan.tone
    reliable_tail = _finite_or_none(getattr(scene, "reliable_tail_ev_p9999", None))
    earned = None
    if reliable_tail is not None:
        earned = max(0.0, reliable_tail - float(dg.OUTPUT_REFERENCE_WHITE_STOPS))
    return {
        "data_support": getattr(bundle, "camera_data_support", None),
        "wb_degradation": getattr(bundle, "wb_degradation", None),
        "raw_clip_union_pct": _finite_or_none(analysis.cell_union_pct),
        "reliable_tail_ev": reliable_tail,
        "tail_ev": _finite_or_none(getattr(scene, "tail_ev_p9999", None)),
        "body_median_ev": _finite_or_none(getattr(scene, "body_ev_p50", None)),
        "sparse_emitter": bool(getattr(scene, "sparse_emitter_tail", False)),
        "black_ev": _finite_or_none(tone.black_ev),
        "white_ev": _finite_or_none(tone.white_ev),
        "contrast": _finite_or_none(tone.contrast),
        "hdr_earned_ev": earned,
    }


def prepare_preview(params: dict) -> dict:
    """Warm a proxy session after file selection without rendering an image."""
    inp, highlight, _, _, _, _, _, _, _, _ = parse_job_params(params)
    wb = str(params.get("wb", "camera"))
    if wb not in dg.WB_CHOICES:
        raise ValueError(f"未知白平衡模式：{wb}")
    decoder, coreimage_version = parse_decoder(params)
    if decoder == "coreimage":
        highlight = "reconstruct"
    tone_core, _ = parse_tone_core(params)
    # Do not compete with the full-resolution export worker for memory bandwidth.
    with RENDER_LOCK:
        entry = PREVIEW_STORE.get(
            inp, highlight, wb, tone_core == "gated", decoder, coreimage_version
        )
    height, width = entry.bundle.scene_rec2020_render.shape[:2]
    try:
        detected = detected_scene_params(entry.bundle, entry.analysis)
    except Exception:
        # Detection is guidance, not a gate: a plan-compile failure here must not
        # block the preview session it decorates.
        detected = None
    return {
        "ok": True,
        "prepared": True,
        "width": int(width),
        "height": int(height),
        "decoder": str(getattr(entry.bundle, "scene_decoder", decoder) or decoder),
        "decoder_version": getattr(entry.bundle, "scene_decoder_version", None),
        "detected": detected,
    }


def export_suffix_parts(
    highlight: str,
    gamut: str,
    output_format: str,
    grade: str = "none",
    grade_strength: float = 1.0,
    scene_transform: str = "none",
    scene_transform_strength: float = 1.0,
    tone_core: str = "agx",
    lum_norm: str = "y",
    agx_primaries: str = "base",
) -> str:
    """Build the filename stem suffix for GUI JPEG/PNG exports."""
    parts = [tone_core]
    if tone_core == "lum" and lum_norm != "y":
        parts.append(lum_norm)
    if tone_core == "agx" and agx_primaries != "base":
        parts.append(agx_primaries)
    if highlight != "clip":
        parts.append(highlight)
    if gamut != "srgb":
        parts.append(gamut)
    if dg.is_hdr_output_format(output_format):
        parts.append("hdr_heic" if output_format == "ultrahdr-heic" else "hdr")
    if grade != "none":
        parts.append(grade.replace(":", "_"))
        if abs(float(grade_strength) - 1.0) > 1e-6:
            parts.append(f"gs{float(grade_strength):g}")
    if scene_transform != "none":
        parts.append(scene_transform)
        if abs(float(scene_transform_strength) - 1.0) > 1e-6:
            parts.append(f"st{float(scene_transform_strength):g}")
    return "_".join(parts)


def run_export(params: dict) -> dict:
    dg.require_dependencies()
    inp, highlight, gamut, output_format, ev, hdr_headroom, quality, want_png, outdir_arg, ev_auto = parse_job_params(
        params
    )
    if dg.is_hdr_output_format(output_format):
        available, reason = dg.apple_gainmap_backend_status()
        if not available:
            raise RuntimeError(reason)
    outdir = outdir_arg if outdir_arg is not None else inp.parent
    outdir.mkdir(parents=True, exist_ok=True)

    demosaic = str(params.get("demosaic", "auto"))
    chroma = str(params.get("chroma", "444"))
    delivery_name = str(params.get("deliveryProfile", params.get("delivery_profile", "archive")))
    try:
        delivery = dg.resolve_delivery_profile(
            delivery_name,
            quality=int(params["quality"]) if params.get("quality") is not None else None,
            chroma=chroma if params.get("chroma") is not None else None,
            container=dg.container_for_output_format(output_format),
        )
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    # Profile owns the encode knobs once resolved (archive forces 100/444).
    quality = int(delivery.quality)
    chroma = str(delivery.chroma)
    if dg.is_hdr_output_format(output_format):
        # Mirror the CLI's honesty contract: the HDR container's primary-image
        # subsampling is emergent from quality inside Core Image, so a request the
        # encoder cannot honour must fail loudly, not write a contradicting file.
        # The page constrains its own controls; this guards direct API clients.
        if chroma == "422":
            raise ValueError(
                "HDR gain-map 容器不提供 4:2:2 主图采样；"
                "Core Image 按 quality 决定采样（q100→4:4:4，share→通常 4:2:0）"
            )
        if chroma == "444" and not delivery.is_archive:
            raise ValueError(
                "HDR 容器的 4:4:4 只在 q100（archive 档）下产生并被门禁验证；"
                "请改用 archive 交付档（或让色度采样跟随交付档）"
            )
    wb = str(params.get("wb", "camera"))
    if wb not in dg.WB_CHOICES:
        raise ValueError(f"未知白平衡模式：{wb}")
    decoder, coreimage_version = parse_decoder(params)
    if decoder == "coreimage":
        highlight = "reconstruct"
        demosaic = "auto"
    look, look_strength, display_filter, filter_strength = parse_grade(params)
    if dg.is_hdr_output_format(output_format) and (look != "none" or display_filter != "none"):
        raise RuntimeError(
            "Ultrahdr 第一版仅支持 look=none 与 display_filter=none；"
            "现有 display look/filter 尚未 HDR 化"
        )
    scene_transform, scene_transform_strength = parse_scene_transform(params)
    punch_scale = parse_punch(params)
    adjustments = parse_render_adjustments(params)
    if dg.is_hdr_output_format(output_format) and abs(float(adjustments.highlight_fade)) > 1e-9:
        raise RuntimeError("HDR 尚未定义显示侧高光褪白；请将该项恢复为自动")
    tone_core, lum_norm = parse_tone_core(params)
    if dg.is_hdr_output_format(output_format) and tone_core != "agx":
        raise RuntimeError("HDR 输出当前只实现 AgX tone core")
    agx_primaries = parse_agx_primaries(params)
    lens_filter, film_curve = parse_film_params(params)
    bundle = dg.load_raw(
        inp,
        highlight,
        demosaic=demosaic,
        wb_mode=wb,
        decoder=decoder,
        coreimage_version=coreimage_version,
    )
    bundle.lens_filter = lens_filter

    analysis, y, ev_img = dg.analyze(
        bundle,
        4,
        diagnostics=want_png,
        gamut_names=None if want_png else (dg.output_gamut_space(gamut),),
    )
    auto_ev_result = None
    if ev_auto:
        auto_ev_result = dg.compute_auto_ev(
            bundle,
            analysis,
            gamut,
            look=look,
            look_strength=look_strength,
            display_filter=display_filter,
            filter_strength=filter_strength,
            scene_transform=scene_transform,
            scene_transform_strength=scene_transform_strength,
            punch_scale=punch_scale,
            tone_core=tone_core,
            lum_norm=lum_norm,
            agx_primaries=agx_primaries,
            adjustments=adjustments,
        )
        ev = auto_ev_result.ev
    bundle = dg.with_intent_exposure(bundle, user_ev=ev, tone_core=tone_core)
    render_plan = dg.build_render_plan(
        bundle,
        analysis,
        RENDER_MODE,
        gamut,
        scene_transform,
        scene_transform_strength,
        punch_scale,
        tone_core,
        lum_norm,
        agx_primaries=agx_primaries,
        adjustments=adjustments,
        film_curve=film_curve,
    )

    grade_id = str(params.get("grade", "none"))
    grade_strength = float(params.get("gradeStrength", params.get("grade_strength", 1.0)))
    suffix = export_suffix_parts(
        highlight,
        gamut,
        output_format,
        grade_id,
        grade_strength,
        scene_transform,
        scene_transform_strength,
        tone_core,
        lum_norm,
        agx_primaries,
    )
    out_ext = ".heic" if output_format == "ultrahdr-heic" else ".jpg"
    out_path = outdir / f"{inp.stem}_{suffix}{out_ext}"
    with RENDER_LOCK:
        # Intent exposure already applied via with_intent_exposure above; do not
        # mutate a shared bundle in place under the lock.
        icc_profile = dg.output_icc_profile_bytes(gamut)
        export_result = dg.export_jpeg(
            path=inp,
            out_path=out_path,
            quality=quality,
            bundle=bundle,
            analysis=analysis,
            tone_plan=render_plan,
            output_gamut=gamut,
            output_format=output_format,
            hdr_headroom=hdr_headroom,
            subsampling=dg.chroma_to_subsampling(chroma),
            look=look,
            look_strength=look_strength,
            display_filter=display_filter,
            filter_strength=filter_strength,
            scene_transform=scene_transform,
            scene_transform_strength=scene_transform_strength,
            tone_core=tone_core,
            lum_norm=lum_norm,
            agx_primaries=agx_primaries,
            punch_scale=punch_scale,
            return_rgb=output_format == "sdr",
            delivery=delivery,
            chroma=chroma,
        )
        hdr_export_info = export_result if isinstance(export_result, dict) else None
        if hdr_export_info is not None and hdr_export_info.get("output_path"):
            # The writer corrects a container/suffix mismatch; report the real file.
            out_path = Path(str(hdr_export_info["output_path"]))
        if hdr_export_info is not None and out_path.is_file():
            hdr_export_info["file_size_bytes"] = out_path.stat().st_size
        rendered_u8 = export_result[1] if isinstance(export_result, tuple) else None
        if rendered_u8 is None and output_format == "ultrahdr-heic":
            from dngscan.gainmap import read_primary_rgb_u8

            rendered_u8 = read_primary_rgb_u8(out_path)
        if rendered_u8 is not None:
            metrics = output_luminance_metrics_u8(rendered_u8, gamut, ev)
        else:
            metrics = output_luminance_metrics(out_path, gamut, ev)
        metrics.update(
            estimate_ev_headroom(
                bundle,
                analysis,
                gamut,
                ev,
                max_samples=600_000,
                look=look,
                look_strength=look_strength,
                display_filter=display_filter,
                filter_strength=filter_strength,
                scene_transform=scene_transform,
                scene_transform_strength=scene_transform_strength,
                punch_scale=punch_scale,
                tone_core=tone_core,
                lum_norm=lum_norm,
                agx_primaries=agx_primaries,
                adjustments=adjustments,
            )
        )
        preview = (
            preview_b64_from_u8(rendered_u8, icc_profile=icc_profile, width=1280)
            if rendered_u8 is not None
            else make_preview_b64(out_path, icc_profile=icc_profile)
        )
        if auto_ev_result is not None:
            np = dg.np
            if rendered_u8 is None:
                from PIL import Image

                with Image.open(out_path) as im:
                    rendered_u8 = np.asarray(im.convert("RGB"), dtype=np.uint8)
            annotated = annotate_preview_rgb_u8(
                rendered_u8, dg.auto_ev_overlay_lines(auto_ev_result)
            )
            preview = preview_b64_from_u8(annotated, icc_profile=icc_profile, width=1280)
        saved = [str(out_path)]

        if want_png:
            png_path = outdir / f"{inp.stem}_{suffix}_scan.png"
            dg.plot_dashboard(bundle, analysis, y, ev_img, png_path, auto_ev=auto_ev_result)
            saved.append(str(png_path))

    return {
        "ok": True,
        "saved": saved,
        "preview": preview,
        "metrics": metrics,
        "metrics_kind": "full",
        "gain": bundle.exposure_gain,
        "ev": ev,
        "ev_auto": auto_ev_payload(auto_ev_result),
        "format": (
            "HDR gain-map HEIC"
            if output_format == "ultrahdr-heic"
            else "HDR gain-map JPEG"
            if dg.is_hdr_output_format(output_format)
            else "SDR JPEG"
        ),
        "hdr_headroom": hdr_headroom if dg.is_hdr_output_format(output_format) else 0.0,
        "hdr_diagnostics": (
            hdr_export_info.get("diagnostics") if hdr_export_info is not None else None
        ),
        "hdr_container": hdr_export_info,
        "highlight": dg.highlight_mode_cn(highlight),
        "gamut": dg.output_gamut_label(gamut),
        "scene_transform": dg.scene_transform_label(scene_transform),
        "scene_transform_strength": scene_transform_strength,
        "tone_core": tone_core,
        "lum_norm": lum_norm,
        "decoder": str(getattr(bundle, "scene_decoder", decoder) or decoder),
        "decoder_version": getattr(bundle, "scene_decoder_version", None),
    }


def _export_worker(params: dict, result_queue: Any) -> None:
    """Spawn target: keep full-resolution arrays out of the GUI server process."""
    try:
        result_queue.put(("ok", run_export(params)))
    except Exception as exc:
        maybe_print_exc()
        result_queue.put(("error", str(exc)))


def run_export_isolated(params: dict) -> dict:
    """Run one full export in a disposable process and return its small result payload.

    The process is deliberately short-lived: NumPy/libraw allocations then return to the
    OS after every export instead of accumulating in the long-running GUI process.
    """
    context = mp.get_context("spawn")
    result_queue = context.Queue(maxsize=1)
    process = context.Process(
        target=_export_worker,
        args=(dict(params), result_queue),
        name="dngscan-export",
    )
    message: tuple[str, Any] | None = None
    with RENDER_LOCK:
        process.start()
        while message is None:
            try:
                message = result_queue.get(timeout=0.25)
            except Empty:
                if not process.is_alive():
                    break
        process.join()
    try:
        result_queue.close()
        result_queue.join_thread()
    except (OSError, ValueError):
        pass
    if message is None:
        raise RuntimeError(f"导出工作进程异常退出（exit code {process.exitcode}）")
    status, payload = message
    if status != "ok":
        raise RuntimeError(str(payload))
    return payload
