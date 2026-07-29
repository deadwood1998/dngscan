# SPDX-License-Identifier: GPL-3.0-or-later
"""Command-line entry point."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .debug_util import maybe_print_exc

from ._deps import IMPORT_ERRORS
from .agx import AGX_PRIMARIES_CLI_CHOICES, resolve_agx_primaries
from .analysis import analyze
from .auto_ev import AutoEvResult, compute_auto_ev, is_ev_auto, parse_ev_value, resolve_export_ev
from .color import output_gamut_space
from .constants import (
    CHROMA_CHOICES, COREIMAGE_SCALE_CHOICES, COREIMAGE_SCALE_DEFAULT_MODE,
    COREIMAGE_SCALE_MEASURED_RATIO, COREIMAGE_VERSION_CHOICES, DECODER_CHOICES,
    DEFAULT_HDR_DRT, DEFAULT_HDR_HEADROOM_EV, DEMOSAIC_CHOICES, HDR_DRT_CHOICES,
    JPEG_OUTPUT_FORMATS, MAX_HDR_HEADROOM_EV, WB_CHOICES,
)
from .export import chroma_to_subsampling, export_jpeg
from .grade import RENDER_MODE, grade_choices, resolve_grade
from .plot import default_png_path, plot_dashboard
from .raw_io import load_raw
from .report import csv_row, print_report, write_csv
from .scene_transform import SCENE_TRANSFORM_CHOICES
from .models import RenderAdjustments
from .scene_scale import with_intent_exposure
from .tone import (
    LUM_NORM_CHOICES, TONE_CORE_CHOICES, apply_render_adjustments,
    build_render_plan,
)


def require_dependencies() -> None:
    if IMPORT_ERRORS:
        joined = "\n  ".join(IMPORT_ERRORS)
        raise RuntimeError(
            "Missing or broken dependency. Install only the required packages "
            "(rawpy, numpy, matplotlib) and rerun.\n  " + joined
        )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="AgX RAW/DNG → JPEG；可选六面板诊断 PNG。"
    )
    parser.add_argument("path", type=Path, help="RAW/DNG 文件路径")
    parser.add_argument(
        "--margin",
        type=int,
        default=4,
        help="每通道满阱剪切阈值的 DN 回退量 (默认: 4)",
    )
    parser.add_argument(
        "--scan",
        action="store_true",
        help="导出六面板诊断 PNG；纯 JPEG 转换默认不画图",
    )
    parser.add_argument("--out", type=Path, default=None, help="诊断 PNG 输出路径；设置后隐含 --scan")
    parser.add_argument("--csv", type=Path, default=None, help="可选指标 CSV 路径")
    parser.add_argument(
        "--jpeg",
        type=Path,
        default=None,
        help="可选 8-bit JPEG 输出路径",
    )
    parser.add_argument(
        "--jpeg-quality",
        type=int,
        default=100,
        help="JPEG 质量 1-100（默认 100）",
    )
    parser.add_argument(
        "--chroma",
        choices=CHROMA_CHOICES,
        default="444",
        help="色度采样: 444=满色度(最高保真、体积最大，默认)；422/420=更小体积（420 最小，投递推荐）",
    )
    parser.add_argument(
        "--output-format",
        choices=JPEG_OUTPUT_FORMATS,
        default="sdr",
        help="JPEG 输出格式: sdr=普通 JPEG；ultrahdr=Apple 原生 ISO 21496-1 HDR JPEG（P3 SDR 底图）",
    )
    parser.add_argument(
        "--hdr-headroom",
        type=float,
        default=DEFAULT_HDR_HEADROOM_EV,
        help=(
            f"HDR display capacity（EV，相对 100 nit reference white）；"
            f"默认 {DEFAULT_HDR_HEADROOM_EV}（800 nit），上限 {MAX_HDR_HEADROOM_EV:.6f}（4000 nit）。"
            "实际内容余量由成片决定，不是归一化目标。"
        ),
    )
    parser.add_argument(
        "--hdr-drt",
        choices=HDR_DRT_CHOICES,
        default=DEFAULT_HDR_DRT,
        help="HDR display rendering transform（当前仅 aces2=ACES 2-derived）",
    )
    parser.add_argument(
        "--hdr-debug-dir",
        type=Path,
        default=None,
        help="可选：写出 HDR 诊断中间结果目录",
    )
    parser.add_argument(
        "--ev",
        default="0",
        help="手动曝光补偿（档），或 auto=按可靠 scene body 中位计算 18%% 灰参考（高光保护；仅显式指定时应用）",
    )
    parser.add_argument(
        "--highlight-mode",
        choices=("clip", "blend", "reconstruct"),
        default="clip",
        help="仅 LibRaw：高光处理 clip/blend/reconstruct；RAW 9 固定使用 Apple 高光重建",
    )
    parser.add_argument(
        "--grade",
        choices=grade_choices(),
        default="none",
        help="可选内置色彩风格；本地 LUT 仅在文件存在时显示",
    )
    parser.add_argument(
        "--grade-strength",
        type=float,
        default=1.0,
        help="成片风格强度 0-1.5（默认 1.0；0=关闭效果）",
    )
    parser.add_argument(
        "--scene-transform",
        choices=SCENE_TRANSFORM_CHOICES,
        default="none",
        help="AgX 前 scene-linear Rec.2020 前馈变换；none=关闭，arri_skin_d55=demo ARRI 式肤色前馈",
    )
    parser.add_argument(
        "--scene-transform-strength",
        type=float,
        default=1.0,
        help="scene transform 强度 0-3（默认 1.0；0=关闭效果；>1 用于诊断/强化 A/B）",
    )
    parser.add_argument(
        "--punch",
        type=float,
        default=1.0,
        help="AgX 纯度补偿倍率 0-1.5（默认 1.0=场景自动值；0=关闭；夜景自动为 0）",
    )
    # Bounded post-plan biases, identical in meaning and range to the GUI sliders, so a
    # render dialled in there can be reproduced from the command line. 0 is exact
    # identity; the automatic endpoints and RAW evidence decisions stay authoritative.
    for _flag, _help in (
        ("midtone-brightness", "中间调亮度偏置 -1..1（显示端内部提升，不改曝光与端点）"),
        ("midtone-contrast", "中间调对比偏置 -1..1"),
        ("shadow-transition", "暗部过渡 -1..1（正=趾部更开）"),
        ("highlight-transition", "高光过渡 -1..1（正=肩部更柔）"),
        ("highlight-fade", "高光褪色 -1..1（显示端色度退让）"),
    ):
        parser.add_argument(f"--{_flag}", type=float, default=0.0, help=_help)
    parser.add_argument(
        "--agx-primaries",
        choices=AGX_PRIMARIES_CLI_CHOICES,
        default="base",
        help="仅 tone-core=agx 的 AgX 原色几何：base=固定版本 darktable scene 默认；smooth=darktable smooth；punchy/muted=纯度变化参考",
    )
    parser.add_argument(
        "--tone-core",
        choices=TONE_CORE_CHOICES,
        default="agx",
        help="tone 核: agx=默认全图 AgX；gated=RAW 门控实验；lum=对照·场景 C1 仅亮度；neutral=诊断·固定 Y 比例曲线",
    )
    parser.add_argument(
        "--lum-norm",
        choices=LUM_NORM_CHOICES,
        default="y",
        help="lum 核 norm: y=Rec.2020 Y；power=power norm；max=max RGB",
    )
    parser.add_argument(
        "--wb",
        choices=WB_CHOICES,
        default="camera",
        help="白平衡: camera=相机 AsShot（默认）；daylight=固定日光配平（胶片式，整卷一致，AsShot 仅作现场光源证词）",
    )
    parser.add_argument(
        "--demosaic",
        choices=DEMOSAIC_CHOICES,
        default="auto",
        help="仅 LibRaw：去马赛克插值算法。RAW 9 使用 Apple 的 CoreML 去马赛克+降噪模型",
    )
    parser.add_argument(
        "--decoder",
        choices=DECODER_CHOICES,
        default="libraw",
        help="scene-linear RGB 解码器: libraw=默认；coreimage=macOS CIRAWFilter（证据层仍为 LibRaw；与 --wb daylight 不兼容）",
    )
    parser.add_argument(
        "--coreimage-version",
        choices=COREIMAGE_VERSION_CHOICES,
        default="auto",
        help="仅 --decoder coreimage：auto=选文件支持的最高版本(优先9)；显式 9/8/7 在不支持时直接报错",
    )
    parser.add_argument(
        "--coreimage-scale",
        choices=COREIMAGE_SCALE_CHOICES,
        default=None,
        help=(
            "仅 --decoder coreimage：scene-linear 尺度策略。"
            "aligned=逐文件对齐 LibRaw 解码绿色中位（默认，非自动曝光）；"
            "unity=保留 Core Image 原生单位；"
            f"measured=旧版固定倍率 1/{COREIMAGE_SCALE_MEASURED_RATIO:.4f}，仅供复现"
        ),
    )
    parser.add_argument(
        "--output-gamut",
        choices=("srgb", "p3"),
        default="srgb",
        help="JPEG 输出色彩空间: srgb=兼容优先；p3=Display P3 并嵌入 ICC",
    )
    args = parser.parse_args(argv)
    if args.coreimage_scale is not None and args.decoder != "coreimage":
        parser.error(
            f"--coreimage-scale {args.coreimage_scale} 仅作用于 --decoder coreimage，"
            f"当前解码器是 {args.decoder}"
        )
    if args.coreimage_scale is None:
        args.coreimage_scale = COREIMAGE_SCALE_DEFAULT_MODE
    args.agx_primaries = resolve_agx_primaries(args.agx_primaries)
    if args.margin < 0:
        parser.error("--margin must be >= 0")
    if not 1 <= args.jpeg_quality <= 100:
        parser.error("--jpeg-quality must be between 1 and 100")
    if not 0 <= args.hdr_headroom <= MAX_HDR_HEADROOM_EV + 1e-9:
        parser.error(
            f"--hdr-headroom must be between 0 and {MAX_HDR_HEADROOM_EV:.6f} EV "
            "(4000 nit @ 100 nit reference white)"
        )
    if not 0.0 <= args.grade_strength <= 1.5:
        parser.error("--grade-strength must be between 0 and 1.5")
    if not 0.0 <= args.scene_transform_strength <= 3.0:
        parser.error("--scene-transform-strength must be between 0 and 3")
    if not 0.0 <= args.punch <= 1.5:
        parser.error("--punch must be between 0 and 1.5")
    for _name in (
        "midtone_brightness", "midtone_contrast", "shadow_transition",
        "highlight_transition", "highlight_fade",
    ):
        if not -1.0 <= getattr(args, _name) <= 1.0:
            parser.error(f"--{_name.replace('_', '-')} must be between -1 and 1")
    if args.output_format == "ultrahdr" and args.chroma != "444":
        parser.error("Apple HDR gain-map JPEG 固定使用 4:4:4；请移除 --chroma 或设为 444")
    if args.output_format == "ultrahdr" and args.jpeg_quality != 100:
        parser.error("Apple HDR gain-map JPEG 固定使用 quality 100")
    if args.output_format == "ultrahdr" and args.grade != "none":
        parser.error(
            "Ultrahdr 第一版不支持 display look/filter；请使用 --grade none"
        )
    if args.decoder == "coreimage" and args.tone_core == "gated":
        # gated is defined as "RAW evidence gates the colour path"; the Core Image
        # pipeline has no per-pixel CFA evidence, so the combination is meaningless
        # rather than merely degraded.
        parser.error(
            "--tone-core gated 需要逐像素 CFA 证据，而 --decoder coreimage 是独立管线"
            "（Core Image 执行 DNG opcode，几何与 LibRaw 不可对齐）。"
            "请改用 --tone-core agx/lum/neutral，或改回 --decoder libraw"
        )
    if args.decoder == "coreimage":
        # CIRAWFilter exposes one calibrated reconstruction path, not LibRaw's three
        # highlight policies. Keep cache keys and reports honest about what was run.
        args.highlight_mode = "reconstruct"
        args.demosaic = "auto"
    return args


def main(argv: list[str]) -> int:
    try:
        args = parse_args(argv)
        if not args.path.exists():
            raise FileNotFoundError(f"Input file does not exist: {args.path}")
        if not args.path.is_file():
            raise FileNotFoundError(f"Input path is not a file: {args.path}")
        require_dependencies()
        if args.output_format == "ultrahdr":
            from .gainmap import apple_gainmap_backend_status

            available, reason = apple_gainmap_backend_status()
            if not available:
                raise RuntimeError(reason)
        if args.decoder == "coreimage":
            from . import coreimage_decode

            probe = coreimage_decode.probe_raw9_support(args.path)
            if not probe["coreimage_available"]:
                raise RuntimeError("Apple Core Image RAW 解码器在此系统不可用")
            if probe["error"]:
                raise RuntimeError(f"Apple RAW 无法探测这个文件：{probe['error']}")
            if not probe["raw9_supported"]:
                fallback = probe["fallback_version"]
                offered = ", ".join(str(value) for value in probe["versions_offered"]) or "none"
                if args.coreimage_version == "9":
                    raise RuntimeError(
                        f"此文件不支持 Apple RAW 9（系统报告版本：{offered}）；"
                        "请改用 --decoder libraw，或显式选择可用的 --coreimage-version"
                    )
                if args.coreimage_version == "auto":
                    if fallback is None:
                        raise RuntimeError(
                            f"此文件不支持 Apple RAW 9，且没有 RAW 8/7 降级路径"
                            f"（系统报告版本：{offered}）"
                        )
                    print(
                        f"warning: 此文件不支持 Apple RAW 9；将明确降级到 Apple RAW {fallback}。"
                        f"可用 --coreimage-version 9 禁止降级，或改用 --decoder libraw。",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"warning: 此文件不支持 Apple RAW 9；当前显式使用 Apple RAW "
                        f"{args.coreimage_version}。",
                        file=sys.stderr,
                    )
        scan_requested = bool(args.scan or args.out is not None or (args.jpeg is None and args.csv is None))
        out_path = args.out if args.out is not None else (default_png_path(args.path) if scan_requested else None)

        bundle = load_raw(
            args.path,
            args.highlight_mode,
            demosaic=args.demosaic,
            wb_mode=args.wb,
            decoder=args.decoder,
            coreimage_version=args.coreimage_version,
            coreimage_scale=args.coreimage_scale,
        )
        diagnostics_requested = bool(scan_requested or args.csv is not None)
        analysis, y, ev = analyze(
            bundle,
            args.margin,
            diagnostics=diagnostics_requested,
            gamut_names=None
            if diagnostics_requested
            else (output_gamut_space("p3" if args.output_format == "ultrahdr" else args.output_gamut),),
        )
        look, look_strength, display_filter, filter_strength = resolve_grade(
            args.grade, args.grade_strength
        )

        ev_input = parse_ev_value(args.ev)
        auto_ev_result: AutoEvResult | None = None
        jpeg_output_gamut = "p3" if args.output_format == "ultrahdr" else args.output_gamut
        if is_ev_auto(ev_input):
            if args.jpeg is None and not scan_requested:
                raise ValueError("--ev auto 需要同时导出 JPEG（--jpeg）或诊断图（--scan / --out）")
            resolved_ev, auto_ev_result = resolve_export_ev(
                ev_input,
                bundle,
                analysis,
                jpeg_output_gamut,
                look,
                look_strength,
                display_filter,
                filter_strength,
                args.scene_transform,
                args.scene_transform_strength,
                args.punch,
                args.tone_core,
                args.lum_norm,
                args.agx_primaries,
            )
        else:
            resolved_ev = float(ev_input)

        bundle = with_intent_exposure(
            bundle, user_ev=resolved_ev, tone_core=args.tone_core
        )
        if out_path is not None:
            plot_dashboard(bundle, analysis, y, ev, out_path, auto_ev=auto_ev_result)

        jpeg_path = args.jpeg
        jpeg_icc_embedded = False
        render_plan = (
            build_render_plan(
                bundle,
                analysis,
                RENDER_MODE,
                jpeg_output_gamut,
                args.scene_transform,
                args.scene_transform_strength,
                args.punch,
                args.tone_core,
                args.lum_norm,
                agx_primaries=args.agx_primaries,
            )
            if jpeg_path is not None
            else None
        )
        if render_plan is not None:
            render_plan = apply_render_adjustments(
                render_plan,
                RenderAdjustments(
                    midtone_brightness=args.midtone_brightness,
                    midtone_contrast=args.midtone_contrast,
                    shadow_transition=args.shadow_transition,
                    highlight_transition=args.highlight_transition,
                    highlight_fade=args.highlight_fade,
                ),
            )
        if jpeg_path is not None:
            export_result = export_jpeg(
                path=args.path,
                out_path=jpeg_path,
                quality=args.jpeg_quality,
                bundle=bundle,
                analysis=analysis,
                tone_plan=render_plan,
                output_gamut=jpeg_output_gamut,
                output_format=args.output_format,
                hdr_headroom=args.hdr_headroom,
                hdr_drt=args.hdr_drt,
                subsampling=chroma_to_subsampling(args.chroma),
                look=look,
                look_strength=look_strength,
                display_filter=display_filter,
                filter_strength=filter_strength,
                scene_transform=args.scene_transform,
                scene_transform_strength=args.scene_transform_strength,
            )
            jpeg_icc_embedded = (
                str(export_result.get("profile", "")) == "Display P3"
                if isinstance(export_result, dict)
                else bool(export_result)
            )

        if args.csv is not None:
            # Only built on demand: without --scan/--csv the analysis deliberately
            # computes a gamut subset, which the full CSV schema must not read.
            row = csv_row(
                bundle,
                analysis,
                out_path,
                jpeg_path,
                args.jpeg_quality if jpeg_path is not None else None,
                RENDER_MODE if jpeg_path is not None else "",
                jpeg_icc_embedded,
                resolved_ev,
                render_plan.tone if render_plan is not None else None,
                jpeg_output_gamut,
                auto_ev_result,
                args.grade,
                args.grade_strength,
                args.scene_transform,
                args.scene_transform_strength,
            )
            write_csv(args.csv, row)
        print_report(
            bundle,
            analysis,
            out_path,
            args.csv,
            jpeg_path,
            args.jpeg_quality,
            RENDER_MODE if jpeg_path is not None else "",
            jpeg_icc_embedded,
            resolved_ev,
            render_plan.tone if render_plan is not None else None,
            jpeg_output_gamut,
            auto_ev_result,
            args.grade,
            args.grade_strength,
            args.scene_transform,
            args.scene_transform_strength,
        )
        if jpeg_path is not None and args.output_format == "ultrahdr":
            print(
                f"JPEG HDR: Apple Core Image ISO 21496-1；Display P3 SDR 底图；"
                f"ACES 2-derived RGB gain map；capacity=+{args.hdr_headroom:.2f}EV"
            )
        return 0
    except Exception as exc:
        maybe_print_exc()
        print(f"error: {exc}", file=sys.stderr)
        return 1
