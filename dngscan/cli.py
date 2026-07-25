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
    CHROMA_CHOICES, DEFAULT_GAINMAP_SCALE, DEFAULT_HDR_HEADROOM_EV, DEMOSAIC_CHOICES,
    JPEG_OUTPUT_FORMATS, WB_CHOICES,
)
from .export import chroma_to_subsampling, export_jpeg
from .grade import RENDER_MODE, grade_choices, resolve_grade
from .plot import default_png_path, plot_dashboard
from .raw_io import load_raw
from .report import csv_row, print_report, write_csv
from .scene_transform import SCENE_TRANSFORM_CHOICES
from .models import RenderAdjustments
from .tone import (
    LUM_NORM_CHOICES, TONE_CORE_CHOICES, apply_render_adjustments, compute_exposure_gain,
    exposure_mode_for_tone_core, build_render_plan,
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
        help="JPEG 输出格式: sdr=普通 JPEG；ultrahdr=ISO 21496-1 gain-map HDR JPEG（强制 Display P3 底图）",
    )
    parser.add_argument(
        "--hdr-headroom",
        type=float,
        default=DEFAULT_HDR_HEADROOM_EV,
        help="Ultra HDR gain map 的 HDR headroom（档），默认 +3EV",
    )
    parser.add_argument(
        "--hdr-gainmap-scale",
        type=int,
        choices=(1, 2, 4),
        default=DEFAULT_GAINMAP_SCALE,
        help="gain map 降采样倍率；1=全分辨率，2/4=更小体积，默认 2",
    )
    parser.add_argument(
        "--ev",
        default="0",
        help="手动曝光补偿（档），或 auto=按全图中位亮度计算 18%% 灰参考值（高光保护；仅显式指定时应用）",
    )
    parser.add_argument(
        "--highlight-mode",
        choices=("clip", "blend", "reconstruct"),
        default="clip",
        help="JPEG 导出缓存的高光处理: clip=硬剪切；blend=libraw 高光混合；reconstruct=libraw 默认高光重建",
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
        default="smooth",
        help="仅 tone-core=agx 的 AgX 高光原色几何：smooth=darktable 默认；base/punchy/muted=Blender 参考组。gated 固定使用 darktable smooth；别名 agx_blender_strong、agx_dt_smooth 等",
    )
    parser.add_argument(
        "--tone-core",
        choices=TONE_CORE_CHOICES,
        default="agx",
        help="tone 核: agx=默认全图 AgX；gated=RAW 门控；lum=对照·场景 C1 仅亮度；neutral=对照·固定通用导出曲线",
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
        help="去马赛克插值算法（画质，非降噪；本工具不做任何降噪。仅全分辨率导出生效): auto=自动选最佳可用(DHT优先，非Bayer走原生)；其余为手动指定",
    )
    parser.add_argument(
        "--output-gamut",
        choices=("srgb", "p3"),
        default="srgb",
        help="JPEG 输出色彩空间: srgb=兼容优先；p3=Display P3 并嵌入 ICC",
    )
    args = parser.parse_args(argv)
    args.agx_primaries = resolve_agx_primaries(args.agx_primaries)
    if args.margin < 0:
        parser.error("--margin must be >= 0")
    if not 1 <= args.jpeg_quality <= 100:
        parser.error("--jpeg-quality must be between 1 and 100")
    if args.hdr_headroom <= 0:
        parser.error("--hdr-headroom must be > 0")
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
    if args.grade != "none" and args.output_format == "ultrahdr":
        parser.error("成片风格暂不支持 Ultra HDR 输出")
    return args


def main(argv: list[str]) -> int:
    try:
        args = parse_args(argv)
        if not args.path.exists():
            raise FileNotFoundError(f"Input file does not exist: {args.path}")
        if not args.path.is_file():
            raise FileNotFoundError(f"Input path is not a file: {args.path}")
        require_dependencies()
        scan_requested = bool(args.scan or args.out is not None or (args.jpeg is None and args.csv is None))
        out_path = args.out if args.out is not None else (default_png_path(args.path) if scan_requested else None)

        bundle = load_raw(args.path, args.highlight_mode, demosaic=args.demosaic, wb_mode=args.wb)
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

        bundle.exposure_gain = compute_exposure_gain(
            exposure_mode_for_tone_core(args.tone_core), resolved_ev
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
            jpeg_icc_embedded = export_jpeg(
                args.path,
                jpeg_path,
                args.jpeg_quality,
                bundle,
                analysis,
                render_plan,
                jpeg_output_gamut,
                args.output_format,
                args.hdr_headroom,
                args.hdr_gainmap_scale,
                chroma_to_subsampling(args.chroma),
                look,
                look_strength,
                display_filter,
                filter_strength,
                args.scene_transform,
                args.scene_transform_strength,
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
            print(f"JPEG HDR: ISO 21496-1 gain-map；headroom=+{args.hdr_headroom:.2f}EV；gain map scale=1/{args.hdr_gainmap_scale}")
        return 0
    except Exception as exc:
        maybe_print_exc()
        print(f"error: {exc}", file=sys.stderr)
        return 1
