#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Look at what the HDR AgX allocation did, on a screen that cannot show HDR.

Phase 2 produces an extended-linear P3 rendition with values above 1.0, and Phase 6/7
have not built the delivery that would let a display receive it. So this renders the
comparison instead of exporting it, three panels per frame:

    SDR            what ships today
    HDR -H_act     the HDR rendition exposed down by its own achieved headroom, which
                   brings the extra highlight range into an SDR screen's range
    lift map       where the allocation spent stops, in EV

The middle panel is the useful one. If the allocation is behaving, its shadows and
midtones look *darker* than the SDR panel by exactly the exposure applied, while its
highlights hold detail the SDR panel has already compressed. Highlights that look the
same in both mean the scene had no reliable tail to spend, which is a correct outcome,
not a broken render.

Nothing here writes a deliverable file: an HDR rendition that no verified pipeline can
package is a diagnostic, and treating a viewable JPEG of one as a product is exactly the
confusion the suspended export gate exists to prevent.

Usage:
    python tools/hdr_ab.py photo.dng [more.dng ...] --out out/
    python tools/hdr_ab.py photo.dng --peak-nits 1000 --full
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np

from dngscan.analysis import analyze
from dngscan.grade import RENDER_MODE
from dngscan.hdr_agx import achieved_headroom, hdr_lift_factor, scene_render_to_hdr_display_linear
from dngscan.hdr_agx_plan import compile_hdr_agx_plan, describe_hdr_plan
from dngscan.models import HdrDisplayTarget
from dngscan.raw_io import load_raw
from dngscan.render import quantize_final_output_linear_to_u8, scene_render_to_display_linear
from dngscan.tone import build_render_plan

LUMA = np.array([0.2627, 0.6780, 0.0593], dtype=np.float32)


def _encode(linear: np.ndarray, gamut: str = "p3") -> np.ndarray:
    """Display-linear -> 8-bit through the pipeline's own encoder, dither included."""
    return quantize_final_output_linear_to_u8(np.clip(linear, 0.0, 1.0), gamut)


def _lift_map_u8(lift: np.ndarray, budget_ev: float) -> np.ndarray:
    """Grey ramp of applied stops, black = none, white = the whole budget."""
    if budget_ev <= 0.0:
        return np.zeros(lift.shape + (3,), dtype=np.uint8)
    stops = np.log2(np.maximum(lift, 1e-6))
    norm = np.clip(stops / budget_ev, 0.0, 1.0)
    return (norm[..., None] * np.uint8(255)).astype(np.uint8).repeat(3, axis=2)


def _panel(images: list[tuple[str, np.ndarray]], width: int = 720) -> np.ndarray:
    from PIL import Image, ImageDraw

    tiles = []
    for label, arr in images:
        im = Image.fromarray(arr)
        h = int(width * im.size[1] / im.size[0])
        tiles.append((label, im.resize((width, h))))
    w, h = tiles[0][1].size
    sheet = Image.new("RGB", (w * len(tiles), h + 24), (16, 16, 16))
    draw = ImageDraw.Draw(sheet)
    for i, (label, im) in enumerate(tiles):
        sheet.paste(im, (i * w, 24))
        draw.text((i * w + 6, 6), label, fill=(240, 240, 240))
    return np.asarray(sheet)


def process(path: Path, out_dir: Path, target: HdrDisplayTarget, half: bool) -> dict:
    bundle = load_raw(path, scene_half_size=half)
    analysis, _, _ = analyze(bundle, margin=4, diagnostics=False)
    plan = build_render_plan(bundle, analysis, RENDER_MODE, "p3")
    hdr_plan = compile_hdr_agx_plan(plan, target, analysis=analysis,
                                    scene_decoder=str(bundle.scene_decoder))

    sdr = scene_render_to_display_linear(bundle, plan, "p3")
    hdr = scene_render_to_hdr_display_linear(bundle, plan, hdr_plan, "p3")
    actual = achieved_headroom(hdr)

    # Expose the HDR rendition down by what it actually reached, not by the budget: the
    # point is to see the range the render used, and dividing by an unused allowance
    # would just darken everything and hide it.
    exposure = 2.0 ** -actual if actual > 0.0 else 1.0
    # The lift map re-derives the gain from the scene buffer. That matches the render
    # only because this tool never applies a scene transform; adding one as an option
    # means routing it here too, or the map will describe a render that did not happen.
    lift = hdr_lift_factor(
        np.asarray(bundle.scene_rec2020_render, dtype=np.float32)[:, :, :3]
        / np.float32(bundle.scene_scale)
        * np.float32(bundle.exposure_gain),
        hdr_plan,
    )

    y_sdr, y_hdr = sdr @ LUMA, hdr @ LUMA
    body = (y_sdr > 0.02) & (y_sdr < 0.5)
    body_delta = (
        float(np.log2(np.median(y_hdr[body]) / np.median(y_sdr[body]))) if np.any(body) else 0.0
    )

    sheet = _panel(
        [
            ("SDR", _encode(sdr)),
            (f"HDR  -{actual:.2f}EV", _encode(hdr * np.float32(exposure))),
            (f"lift map  0..{hdr_plan.tone.budget_headroom_ev:.2f}EV", _lift_map_u8(lift, hdr_plan.tone.budget_headroom_ev)),
        ]
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{path.stem}_hdr_ab.jpg"
    from PIL import Image

    Image.fromarray(sheet).save(out_path, quality=92)

    return {
        "name": path.stem,
        "budget": hdr_plan.tone.budget_headroom_ev,
        "actual": actual,
        "window": hdr_plan.tone.white_ev - hdr_plan.tone.knee_ev,
        "tail": hdr_plan.tone.reliable_tail_ev,
        "body_delta_ev": body_delta,
        "above_one_pct": 100.0 * float(np.count_nonzero(hdr > 1.0)) / hdr.size,
        "plan": describe_hdr_plan(hdr_plan),
        "path": out_path,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", type=Path, nargs="+")
    parser.add_argument("--out", type=Path, default=Path("hdr_ab"))
    parser.add_argument("--peak-nits", type=float, default=800.0)
    parser.add_argument("--reference-white-nits", type=float, default=100.0)
    parser.add_argument("--full", action="store_true", help="full resolution (default: half)")
    args = parser.parse_args(argv)

    target = HdrDisplayTarget(
        reference_white_nits=args.reference_white_nits, peak_nits=args.peak_nits
    )
    print(
        f"display target: {args.peak_nits:.0f}/{args.reference_white_nits:.0f} nit "
        f"= +{target.display_headroom_ev:.2f} EV capacity"
    )
    print(
        f"\n  {'frame':16s} {'window':>7s} {'tail':>7s} {'budget':>7s} {'actual':>7s} "
        f"{'body dEV':>9s} {'>1.0':>7s}"
    )
    rows = []
    for path in args.paths:
        if not path.is_file():
            print(f"  {path}: not a file", file=sys.stderr)
            continue
        row = process(path, args.out, target, half=not args.full)
        rows.append(row)
        print(
            f"  {row['name']:16s} {row['window']:7.2f} {row['tail']:+7.2f} "
            f"{row['budget']:7.2f} {row['actual']:7.2f} {row['body_delta_ev']:+9.5f} "
            f"{row['above_one_pct']:6.2f}%"
        )
    if rows:
        worst = max(abs(r["body_delta_ev"]) for r in rows)
        print(f"\n  largest scene-body shift across frames: {worst:+.6f} EV (must be ~0)")
        print(f"  panels written to {args.out}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
