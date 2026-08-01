#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Measure the fixed-resolution interactive preview path on one RAW file."""

from __future__ import annotations

import argparse
import base64
import io
import json
import math
import os
import sys
import tempfile
import time
import uuid
from collections import defaultdict
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dngscan.gui.preview_scheduler import PREVIEW_COORDINATOR
from dngscan.gui import preview_cache as preview_cache_module
from dngscan.gui import service as service_module
from dngscan.gui.constants import REALTIME_PREVIEW_LONG_EDGE


prepare_preview = service_module.prepare_preview
run_preview = service_module.run_preview

SCENARIOS: dict[str, dict[str, object]] = {
    "baseline": {},
    "look": {"grade": "look:optic_warm_cyan", "gradeStrength": 1.0},
    "scene-transform": {
        "sceneTransform": "portra400_d55",
        "sceneTransformStrength": 1.0,
    },
    "raw-gated": {"toneCore": "gated"},
}


def _elapsed_ms(callable_) -> tuple[object, float]:
    started = time.perf_counter()
    result = callable_()
    return result, (time.perf_counter() - started) * 1000.0


def _percentile(samples: list[float], percentile: float) -> float:
    ordered = sorted(samples)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _dimensions(data_url: str) -> list[int]:
    encoded = data_url.split(",", 1)[-1]
    with Image.open(io.BytesIO(base64.b64decode(encoded))) as image:
        return [image.width, image.height]


class _StageRecorder:
    def __init__(self) -> None:
        self.samples: dict[str, list[float]] = defaultdict(list)

    def clear(self) -> None:
        self.samples.clear()

    def wrapper(self, stage: str, callable_):
        def measured(*args, **kwargs):
            started = time.perf_counter()
            try:
                return callable_(*args, **kwargs)
            finally:
                self.samples[stage].append((time.perf_counter() - started) * 1000.0)

        return measured

    def report(self) -> dict[str, dict[str, float]]:
        return {
            stage: {
                "p50_ms": round(_percentile(samples, 0.50), 3),
                "p95_ms": round(_percentile(samples, 0.95), 3),
            }
            for stage, samples in sorted(self.samples.items())
            if samples
        }


@contextmanager
def _record_stages():
    """Instrument preview stages without adding timers to the production path."""
    from dngscan import render as render_module

    recorder = _StageRecorder()
    targets = (
        (service_module.PREVIEW_STORE, "get", "session_lookup"),
        (preview_cache_module.PreviewEntry, "get_frame", "frame_cache_lookup"),
        (service_module, "_cached_render_plan", "plan_lookup"),
        (service_module.dg, "render_output_u8", "pixel_pipeline_total"),
        (render_module, "scene_intent_rec2020", "scene_intent"),
        (
            render_module.scene_transform_engine,
            "apply_scene_transform_rec2020",
            "scene_transform",
        ),
        (render_module.retreat_engine, "apply_clip_retreat_rec2020", "clip_retreat"),
        (render_module, "apply_tone_core", "tone_core"),
        (render_module, "rec2020_to_output", "output_matrix"),
        (render_module, "finalize_output_linear", "gamut_finalize"),
        (render_module, "encode_display_linear", "transfer_encode"),
        (render_module, "dither_quantize_u8", "dither_quantize"),
        (service_module, "preview_metrics_from_u8", "preview_metrics"),
        (service_module, "preview_b64_from_u8", "jpeg_base64"),
    )
    with ExitStack() as stack:
        for owner, name, stage in targets:
            original = getattr(owner, name)
            stack.enter_context(patch.object(owner, name, recorder.wrapper(stage, original)))
        yield recorder


@contextmanager
def _preview_geometry(long_edge: int | None):
    """Use an isolated cache when profiling a non-product candidate geometry."""
    if long_edge is None or long_edge == REALTIME_PREVIEW_LONG_EDGE:
        yield REALTIME_PREVIEW_LONG_EDGE
        return
    if long_edge < 320:
        raise ValueError("--long-edge must be at least 320")
    old_edge = preview_cache_module.PROXY_LONG_EDGE
    old_cache_dir = os.environ.get("DNGSCAN_PREVIEW_CACHE_DIR")
    with tempfile.TemporaryDirectory(prefix="dngscan-preview-profile-") as cache_dir:
        try:
            preview_cache_module.PROXY_LONG_EDGE = int(long_edge)
            os.environ["DNGSCAN_PREVIEW_CACHE_DIR"] = cache_dir
            preview_cache_module.PREVIEW_STORE.clear_memory()
            yield int(long_edge)
        finally:
            preview_cache_module.PREVIEW_STORE.clear_memory()
            preview_cache_module.PROXY_LONG_EDGE = old_edge
            if old_cache_dir is None:
                os.environ.pop("DNGSCAN_PREVIEW_CACHE_DIR", None)
            else:
                os.environ["DNGSCAN_PREVIEW_CACHE_DIR"] = old_cache_dir


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--iterations", type=int, default=30)
    parser.add_argument(
        "--long-edge",
        type=int,
        help="profile an isolated candidate size without changing the product constant",
    )
    parser.add_argument(
        "--scenario",
        choices=tuple(SCENARIOS),
        default="baseline",
        help="representative preview mode to measure",
    )
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("--iterations must be at least 1")

    input_path = args.input.expanduser().resolve()
    with _preview_geometry(args.long_edge) as long_edge:
        session = f"benchmark:{uuid.uuid4()}"
        common = {
            "input": str(input_path),
            "previewSession": session,
            "includeMetrics": False,
            **SCENARIOS[args.scenario],
        }
        try:
            prepared, prepare_ms = _elapsed_ms(lambda: prepare_preview(common))
            if not prepared.get("ok"):
                raise RuntimeError(prepared)

            with _record_stages() as stages:
                first, first_frame_ms = _elapsed_ms(
                    lambda: run_preview({**common, "generation": 1, "ev": 0.0})
                )
                if not first.get("ok") or first.get("superseded"):
                    raise RuntimeError(first)
                stages.clear()

                continuous_ms: list[float] = []
                last_params: dict[str, object] | None = None
                for index in range(args.iterations):
                    step = (index // 2 + 1) * 0.05
                    ev = -step if index % 2 == 0 else step
                    last_params = {
                        **common,
                        "generation": index + 2,
                        "ev": ev,
                    }
                    result, elapsed_ms = _elapsed_ms(lambda p=last_params: run_preview(p))
                    if not result.get("ok") or result.get("superseded"):
                        raise RuntimeError(result)
                    continuous_ms.append(elapsed_ms)

                stage_report = stages.report()

            assert last_params is not None
            repeated, cache_hit_ms = _elapsed_ms(
                lambda: run_preview(
                    {**last_params, "generation": args.iterations + 2}
                )
            )
            report = {
                "input": input_path.name,
                "scenario": args.scenario,
                "long_edge": long_edge,
                "dimensions": _dimensions(first["preview"]),
                "prepare_ms": round(prepare_ms, 2),
                "first_frame_ms": round(first_frame_ms, 2),
                "continuous_iterations": len(continuous_ms),
                "continuous_p50_ms": round(_percentile(continuous_ms, 0.50), 2),
                "continuous_p95_ms": round(_percentile(continuous_ms, 0.95), 2),
                "continuous_p99_ms": round(_percentile(continuous_ms, 0.99), 2),
                "continuous_max_ms": round(max(continuous_ms), 2),
                "continuous_stage_ms": stage_report,
                "revisit_cache_hit": bool(repeated.get("cache_hit")),
                "revisit_ms": round(cache_hit_ms, 2),
            }
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        finally:
            PREVIEW_COORDINATOR.clear()


if __name__ == "__main__":
    raise SystemExit(main())
