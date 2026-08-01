# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from dngscan._deps import np
from dngscan.gui.preview_cache import (
    MAX_FRAME_CACHE_ITEMS,
    PreviewEntry,
    _cache_identity,
    _evidence_cache_identity,
    _read_disk_entry,
    _write_disk_entry,
    build_proxy_entry,
    downsample_mean,
)
from dngscan.models import Analysis, RawBundle, RawGuidanceMaps


def _analysis() -> Analysis:
    channels = [0, 1, 2, 3]
    labels = {0: "R", 1: "G", 2: "B", 3: "G"}
    return Analysis(
        channel_ids=channels,
        labels=labels,
        ceilings={key: 1000 for key in channels},
        ceil_spike_counts={key: 0 for key in channels},
        ceil_near_counts={key: 0 for key in channels},
        ceil_spike_ok={key: False for key in channels},
        fullwell_channel_ids=channels,
        fullwell_note="test",
        saturation_levels={key: 1000 for key in channels},
        channel_fullwell={key: 1000 for key in channels},
        channel_thresholds={key: 996 for key in channels},
        fullwell=1000,
        threshold=996,
        clip_pct={key: 0.0 for key in channels},
        cfa_cell_supported=True,
        cell_union_pct=0.0,
        cell_ge2_of_clipped_pct=0.0,
        cell_k_of_clipped_pct={key: 0.0 for key in range(1, 5)},
        cell_k_of_all_pct={key: 0.0 for key in range(1, 5)},
        ev_p1=-6.0,
        ev_raw_p1=-6.0,
        ev_median=-1.0,
        ev_p99=2.0,
        ev_p999=3.0,
        ev_dr_p1_p999=9.0,
        ev_floor_hit_pct=0.0,
        median_vs_gray_ev=-1.0,
        median_y=0.1,
        noise_floor=0.001,
        usable_dr_ev=9.0,
        snr_curves={},
        snr1_dr={},
        snr1_stop={},
        gamut_out_pct={"sRGB": 0.1, "P3": 0.0, "Rec2020": 0.0},
        bright_pixel_pct=50.0,
        survivor_channel="G",
        container_bits_est=10,
    )


def _bundle() -> RawBundle:
    raw = np.arange(64, dtype=np.uint16).reshape(8, 8)
    colors = np.tile(np.asarray([[0, 1], [3, 2]], dtype=np.uint8), (4, 4))
    scene = np.arange(8 * 8 * 3, dtype=np.uint16).reshape(8, 8, 3)
    return RawBundle(
        path=Path("synthetic.dng"),
        raw_image=raw,
        raw_colors=colors,
        xyz_render=scene.copy(),
        render_scale=65535.0,
        scene_rec2020_render=scene,
        scene_scale=65535.0,
        white_level=1000,
        black_levels=[0.0] * 4,
        camera_wb=[2.0, 1.0, 1.5, 1.0],
        color_desc="RGBG",
        raw_pattern=[[0, 1], [3, 2]],
        camera_white_levels=[1000.0] * 4,
        clip_masks=np.linspace(0.0, 1.0, 8 * 8 * 3, dtype=np.float16).reshape(8, 8, 3),
        scene_scale_mode="measured",
        baseline_exposure=0.75,
        scene_decoder_runtime="Version 27.0 (Build TEST)",
        scene_align_factor=0.875,
        scene_opcode_names=("WarpRectilinear", "GainMap"),
        evidence_provider_version="rawpy 0.27.0/LibRaw 0.22.0",
    )


class PreviewCacheTest(unittest.TestCase):
    def test_realtime_proxy_has_one_fixed_1980px_long_edge(self) -> None:
        image = np.zeros((600, 2400, 3), dtype=np.float32)
        proxy = downsample_mean(image)
        self.assertEqual(proxy.shape, (495, 1980, 3))

    def test_runtime_plan_and_frame_caches_are_bounded_lrus(self) -> None:
        entry = PreviewEntry(bundle=_bundle(), analysis=_analysis())
        builds = 0

        def build() -> object:
            nonlocal builds
            builds += 1
            return object()

        first = entry.get_or_build_plan(("agx", "srgb"), build)
        self.assertIs(entry.get_or_build_plan(("agx", "srgb"), build), first)
        self.assertEqual(builds, 1)

        for index in range(MAX_FRAME_CACHE_ITEMS + 2):
            entry.put_frame((index,), {"preview": str(index), "metrics": {"v": index}})
        self.assertIsNone(entry.get_frame((0,)))
        latest = entry.get_frame((MAX_FRAME_CACHE_ITEMS + 1,))
        self.assertIsNotNone(latest)
        assert latest is not None
        latest["metrics"]["v"] = -1
        self.assertEqual(
            entry.get_frame((MAX_FRAME_CACHE_ITEMS + 1,))["metrics"]["v"],
            MAX_FRAME_CACHE_ITEMS + 1,
        )

    def test_evidence_identity_is_scene_decoder_independent(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".dng") as source:
            path = Path(source.name)
            evidence_key = _evidence_cache_identity(path)
            libraw_key, _ = _cache_identity(path, "clip", "camera", "libraw", "auto")
            apple_key, _ = _cache_identity(
                path, "reconstruct", "camera", "coreimage", "9"
            )
        self.assertEqual(libraw_key[:4], evidence_key)
        self.assertEqual(apple_key[:4], evidence_key)
        self.assertNotEqual(libraw_key, apple_key)

    def test_round_trip_keeps_compact_proxy_and_guidance(self) -> None:
        entry = build_proxy_entry(_bundle(), _analysis())
        entry.bundle.raw_guidance = RawGuidanceMaps(
            headroom=np.full((8, 8, 3), 0.8, dtype=np.float16),
            clip_class=np.full((8, 8), 3, dtype=np.uint8),
            snr_confidence=np.full((8, 8), 0.7, dtype=np.float16),
        )
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "preview.npz"
            _write_disk_entry(cache_path, entry)
            restored = _read_disk_entry(cache_path, Path("synthetic.dng"), require_guidance=True)

        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertIsNone(restored.bundle.raw_image)
        self.assertIsNone(restored.bundle.raw_colors)
        np.testing.assert_array_equal(
            restored.bundle.scene_rec2020_render,
            entry.bundle.scene_rec2020_render,
        )
        np.testing.assert_array_equal(restored.bundle.clip_masks, entry.bundle.clip_masks)
        self.assertEqual(restored.analysis.labels, entry.analysis.labels)
        self.assertEqual(restored.analysis.channel_thresholds, entry.analysis.channel_thresholds)
        self.assertEqual(restored.bundle.scene_scale_mode, "measured")
        self.assertEqual(restored.bundle.baseline_exposure, 0.75)
        self.assertEqual(
            restored.bundle.scene_decoder_runtime, "Version 27.0 (Build TEST)"
        )
        self.assertEqual(restored.bundle.scene_align_factor, 0.875)
        self.assertEqual(
            restored.bundle.scene_opcode_names, ("WarpRectilinear", "GainMap")
        )
        self.assertEqual(restored.bundle.evidence_provider, "libraw")
        self.assertEqual(
            restored.bundle.evidence_provider_version,
            "rawpy 0.27.0/LibRaw 0.22.0",
        )
        assert restored.bundle.raw_guidance is not None
        np.testing.assert_array_equal(
            restored.bundle.raw_guidance.clip_class,
            entry.bundle.raw_guidance.clip_class,
        )

    def test_raw9_signed_half_proxy_round_trip(self) -> None:
        bundle = _bundle()
        bundle.scene_decoder = "coreimage"
        bundle.scene_scale = 1.0
        bundle.render_scale = 1.0
        bundle.clip_masks = None
        bundle.scene_rec2020_render = np.linspace(
            -0.25, 2.5, 8 * 8 * 3, dtype=np.float16
        ).reshape(8, 8, 3)
        entry = build_proxy_entry(bundle, _analysis())

        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "raw9.npz"
            _write_disk_entry(cache_path, entry)
            restored = _read_disk_entry(
                cache_path, Path("synthetic.dng"), require_guidance=False
            )

        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertEqual(restored.bundle.scene_rec2020_render.dtype, np.float16)
        self.assertEqual(restored.bundle.scene_scale, 1.0)
        self.assertLess(float(restored.bundle.scene_rec2020_render.min()), 0.0)
        self.assertGreater(float(restored.bundle.scene_rec2020_render.max()), 1.0)


if __name__ == "__main__":
    unittest.main()
