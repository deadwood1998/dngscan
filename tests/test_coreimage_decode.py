# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the optional Core Image (CIRAWFilter) scene decoder."""
from __future__ import annotations

import unittest
from pathlib import Path

import numpy as np

from dngscan import coreimage_decode
from dngscan.models import RawBundle
from dngscan.retreat import clip_masks_for_shape, resize_clip_masks

PICTURES = Path("/Users/itoshikigen/Pictures")
SIGMA_DNG = PICTURES / "_SDI0150.DNG"
FUJI_RAF = PICTURES / "DSCF0614.RAF"


def _skip_unless_available() -> None:
    if not coreimage_decode.available():
        raise unittest.SkipTest("Core Image / CIRAWFilter unavailable")


class CoreImageDecodeImportTests(unittest.TestCase):
    def test_available_never_raises(self) -> None:
        # Must be False-safe on any platform.
        value = coreimage_decode.available()
        self.assertIsInstance(value, bool)

    def test_import_is_side_effect_free(self) -> None:
        # Re-importing must not raise even when Quartz is absent.
        import importlib

        importlib.reload(coreimage_decode)
        self.assertIsInstance(coreimage_decode.available(), bool)


class CoreImageVersionTests(unittest.TestCase):
    def test_auto_prefers_nine(self) -> None:
        chosen = coreimage_decode.resolve_decoder_version(
            "auto", ("6.dng", "7", "7.dng", "8", "8.dng", "9", "9.dng")
        )
        self.assertEqual(chosen, "9")

    def test_auto_falls_back(self) -> None:
        chosen = coreimage_decode.resolve_decoder_version("auto", ("7", "8"))
        self.assertEqual(chosen, "8")

    def test_explicit_unsupported_raises(self) -> None:
        with self.assertRaises(RuntimeError):
            coreimage_decode.resolve_decoder_version("9", ("7", "8"))


@unittest.skipUnless(coreimage_decode.available(), "Core Image unavailable")
class CoreImageLiveTests(unittest.TestCase):
    def test_color_noise_default_cleared(self) -> None:
        _skip_unless_available()
        if not SIGMA_DNG.is_file():
            raise unittest.SkipTest(f"missing {SIGMA_DNG}")
        from Foundation import NSURL
        import Quartz

        filt = Quartz.CIRAWFilter.alloc().initWithImageURL_(
            NSURL.fileURLWithPath_(str(SIGMA_DNG))
        )
        self.assertGreater(float(filt.colorNoiseReductionAmount()), 0.0)
        coreimage_decode.configure_linear_filter(filt, version="9", scale_factor=0.1)
        self.assertAlmostEqual(float(filt.colorNoiseReductionAmount()), 0.0, places=6)

    def test_linearity_contract(self) -> None:
        _skip_unless_available()
        if not SIGMA_DNG.is_file():
            raise unittest.SkipTest(f"missing {SIGMA_DNG}")
        a, _ = coreimage_decode.decode_scene_rec2020(
            SIGMA_DNG, half_size=True, version="auto", exposure=0.0, scale_compensation=1.0
        )
        b, _ = coreimage_decode.decode_scene_rec2020(
            SIGMA_DNG, half_size=True, version="auto", exposure=1.0, scale_compensation=1.0
        )
        mask = a.mean(axis=2) > 0.01
        if not np.any(mask):
            raise unittest.SkipTest("no midtone pixels for linearity check")
        ratio = float(np.median(b[mask] / np.maximum(a[mask], 1e-8)))
        self.assertAlmostEqual(ratio, 2.0, delta=0.02)

    def test_separate_pipeline_drops_cfa_masks_and_scales(self) -> None:
        """Strict Core Image pipeline: no per-pixel CFA evidence, correct scale.

        Core Image executes the file's DNG opcodes (WarpRectilinear here), so its frame
        is a nonlinear warp of LibRaw's — corners measured ~70 px away on this capture.
        Reusing LibRaw masks would put clip retreat on the wrong pixels, so the bundle
        must carry none, while the aggregate LibRaw facts stay available.
        """
        _skip_unless_available()
        if not SIGMA_DNG.is_file():
            raise unittest.SkipTest(f"missing {SIGMA_DNG}")
        from dngscan.raw_io import load_raw

        libraw = load_raw(SIGMA_DNG, scene_half_size=True, decoder="libraw")
        ci_bundle = load_raw(SIGMA_DNG, scene_half_size=True, decoder="coreimage")
        self.assertIsNotNone(libraw.clip_masks)
        self.assertIsNone(ci_bundle.clip_masks)
        self.assertEqual(ci_bundle.scene_decoder, "coreimage")
        self.assertIn("WarpRectilinear", ci_bundle.scene_opcode_names)
        # Aggregate (geometry-free) RAW facts survive: same mosaic, same levels.
        self.assertEqual(int(ci_bundle.white_level), int(libraw.white_level))
        self.assertEqual(list(ci_bundle.black_levels), list(libraw.black_levels))
        # Scale compensation keeps both decoders on the same exposure anchor.
        import numpy as np

        def mid_median(bundle):
            arr = np.asarray(bundle.scene_rec2020_render, dtype=np.float32) / float(bundle.scene_scale)
            y = 0.2627 * arr[:, :, 0] + 0.6780 * arr[:, :, 1] + 0.0593 * arr[:, :, 2]
            return float(np.median(y[(y > 0.01) & (y < 0.5)]))

        self.assertAlmostEqual(mid_median(ci_bundle) / mid_median(libraw), 1.0, delta=0.10)

    def test_full_resolution_production_path_renders(self) -> None:
        """Exercise the resolution the exporter actually uses.

        The earlier gate passed at half size and rejected every full-size export; any
        future check must be verified where production runs.
        """
        _skip_unless_available()
        if not SIGMA_DNG.is_file():
            raise unittest.SkipTest(f"missing {SIGMA_DNG}")
        import numpy as np

        from dngscan.analysis import analyze
        from dngscan.raw_io import load_raw
        from dngscan.render import render_output_u8
        from dngscan.tone import build_render_plan

        bundle = load_raw(SIGMA_DNG, scene_half_size=False, decoder="coreimage")
        analysis, _, _ = analyze(bundle, 4, diagnostics=False, gamut_names=("P3",))
        plan = build_render_plan(bundle, analysis, "agx", "p3")
        rgb = render_output_u8(bundle, analysis, "p3", plan)
        self.assertEqual(rgb.shape[:2], bundle.scene_rec2020_render.shape[:2])
        self.assertGreater(int(np.asarray(rgb).max()), 32)


    def test_fuji_resolves_without_claiming_v9(self) -> None:
        _skip_unless_available()
        if not FUJI_RAF.is_file():
            raise unittest.SkipTest(f"missing {FUJI_RAF}")
        offered = coreimage_decode.supported_versions(FUJI_RAF)
        self.assertNotIn("9", {_normalize(v) for v in offered})
        with self.assertRaises(RuntimeError):
            coreimage_decode.resolve_decoder_version("9", offered)
        _, info = coreimage_decode.decode_scene_rec2020(
            FUJI_RAF, half_size=True, version="auto"
        )
        self.assertEqual(coreimage_decode._normalize_version_token(info["version"]), "8")


def _normalize(token: str) -> str:
    return coreimage_decode._normalize_version_token(token)


class LoadRawDecoderGuardTests(unittest.TestCase):
    def test_daylight_rejected_for_coreimage(self) -> None:
        from dngscan.raw_io import load_raw

        if not coreimage_decode.available():
            raise unittest.SkipTest("Core Image unavailable")
        if not SIGMA_DNG.is_file():
            raise unittest.SkipTest(f"missing {SIGMA_DNG}")
        with self.assertRaises(ValueError):
            load_raw(SIGMA_DNG, scene_half_size=True, decoder="coreimage", wb_mode="daylight")


if __name__ == "__main__":
    unittest.main()


class DecoderGuardTests(unittest.TestCase):
    def test_gated_core_rejected_with_coreimage(self) -> None:
        """gated means "RAW evidence gates the colour path"; that evidence does not
        exist on the Core Image pipeline, so the combination must be refused rather
        than silently degraded."""
        from dngscan.cli import parse_args

        with self.assertRaises(SystemExit):
            parse_args(["photo.dng", "--jpeg", "out.jpg",
                        "--decoder", "coreimage", "--tone-core", "gated"])

    def test_opcode_reader_is_best_effort(self) -> None:
        """Never fatal: a non-TIFF container just reports nothing."""
        result = coreimage_decode.read_dng_opcodes(Path("/nonexistent/x.raf"))
        self.assertFalse(result["geometry"])
        self.assertEqual(result["names"], ())

    def test_opcode_reader_finds_dng_geometry_opcodes(self) -> None:
        if not SIGMA_DNG.is_file():
            raise unittest.SkipTest(f"missing {SIGMA_DNG}")
        result = coreimage_decode.read_dng_opcodes(SIGMA_DNG)
        self.assertTrue(result["parsed"])
        self.assertIn("WarpRectilinear", result["names"])
        self.assertTrue(result["geometry"])
