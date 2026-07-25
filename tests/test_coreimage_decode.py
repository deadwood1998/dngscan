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

    def test_geometry_gate_and_scale(self) -> None:
        _skip_unless_available()
        if not SIGMA_DNG.is_file():
            raise unittest.SkipTest(f"missing {SIGMA_DNG}")
        from dngscan.raw_io import load_raw

        libraw = load_raw(SIGMA_DNG, scene_half_size=True, decoder="libraw")
        ci_float, info = coreimage_decode.decode_scene_rec2020(
            SIGMA_DNG, half_size=True, version="auto"
        )
        corr = coreimage_decode.verify_geometry_alignment(ci_float, libraw.scene_rec2020_render)
        self.assertGreaterEqual(corr, coreimage_decode.GEOMETRY_CORR_MIN)
        # Deliberately corrupted mapping must raise.
        with self.assertRaises(RuntimeError):
            coreimage_decode.verify_geometry_alignment(
                np.flipud(ci_float), libraw.scene_rec2020_render
            )
        # Scale compensation brings median ratio near 1.
        lr = np.asarray(libraw.scene_rec2020_render, dtype=np.float32)
        if np.issubdtype(lr.dtype, np.integer):
            lr = lr / float(libraw.scene_scale)
        from PIL import Image

        def luma(rgb: np.ndarray) -> np.ndarray:
            return 0.2627 * rgb[:, :, 0] + 0.6780 * rgb[:, :, 1] + 0.0593 * rgb[:, :, 2]

        mapped = np.asarray(
            Image.fromarray(luma(lr), mode="F").resize(
                (ci_float.shape[1], ci_float.shape[0]), Image.Resampling.BILINEAR
            ),
            dtype=np.float32,
        )
        mid = (luma(ci_float) > 0.01) & (mapped > 0.01) & (luma(ci_float) < 0.5) & (mapped < 0.5)
        if np.any(mid):
            ratio = float(np.median(luma(ci_float)[mid] / mapped[mid]))
            self.assertAlmostEqual(ratio, 1.0, delta=0.03)
        self.assertTrue(info["color_noise_cleared"])

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


class ClipMaskGeometryTests(unittest.TestCase):
    def test_crop_mapping_keeps_clipped_region(self) -> None:
        # Evidence 100x80 with a bright clipped block at (20:40, 30:50).
        masks = np.zeros((100, 80, 3), dtype=np.float32)
        masks[20:40, 30:50, :] = 1.0
        # Scene is a pure scale of the full evidence frame → crop is full frame.
        crop = (0.0, 0.0, 100.0, 80.0)
        resized = resize_clip_masks(masks, (50, 40), crop=crop)
        self.assertEqual(resized.shape[:2], (50, 40))
        peak = np.unravel_index(int(np.argmax(resized[:, :, 0])), resized.shape[:2])
        # Half-scale maps evidence (20:40, 30:50) → scene (~10:20, ~15:25).
        self.assertTrue(8 <= peak[0] <= 22)
        self.assertTrue(12 <= peak[1] <= 28)

        bundle = RawBundle(
            path=Path("synthetic.dng"),
            raw_image=np.zeros((2, 2), dtype=np.uint16),
            raw_colors=np.zeros((2, 2), dtype=np.uint8),
            xyz_render=np.zeros((50, 40, 3), dtype=np.uint16),
            render_scale=65535.0,
            scene_rec2020_render=np.zeros((50, 40, 3), dtype=np.uint16),
            scene_scale=65535.0,
            white_level=65535,
            black_levels=[0.0, 0.0, 0.0, 0.0],
            camera_wb=[1.0, 1.0, 1.0, 1.0],
            color_desc="RGBG",
            raw_pattern=[[0, 1], [3, 2]],
            camera_white_levels=[65535.0] * 4,
            clip_masks=masks.astype(np.float16),
            evidence_shape=(100, 80),
            scene_geometry_crop=crop,
            scene_decoder="coreimage",
        )
        mapped = clip_masks_for_shape(bundle, (50, 40))
        peak2 = np.unravel_index(int(np.argmax(mapped[:, :, 0])), mapped.shape[:2])
        self.assertTrue(8 <= peak2[0] <= 22)
        self.assertTrue(12 <= peak2[1] <= 28)

    def test_corrupted_crop_moves_peak(self) -> None:
        masks = np.zeros((100, 80, 3), dtype=np.float32)
        masks[20:40, 30:50, :] = 1.0
        # Deliberate wrong crop: take only the bottom-right quadrant.
        bad = resize_clip_masks(masks, (50, 40), crop=(50.0, 40.0, 100.0, 80.0))
        peak = np.unravel_index(int(np.argmax(bad[:, :, 0])), bad.shape[:2])
        # The clipped block is outside this crop, so the peak should not sit in the
        # expected half-scale window — or the max should be near zero.
        if float(bad.max()) < 0.1:
            return
        self.assertFalse(8 <= peak[0] <= 22 and 12 <= peak[1] <= 28)


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
