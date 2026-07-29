# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from dngscan._deps import np
from dngscan.color import srgb_decode
from dngscan.gainmap import (
    HDR_DIFFUSE_WHITE_EV,
    apple_gainmap_backend_status,
    build_hdr_alternate_rgba_half,
    gain_stops_for_scene_ev,
    inspect_gainmap_jpeg,
    write_apple_gainmap_jpeg,
)
from dngscan.cli import parse_args


class GainCurveTests(unittest.TestCase):
    def test_cli_ultrahdr_rejects_display_look(self) -> None:
        with self.assertRaises(SystemExit):
            parse_args(
                [
                    "photo.dng",
                    "--output-format",
                    "ultrahdr",
                    "--grade",
                    "look:optic_warm_cyan",
                ]
            )

    def test_cli_hdr_keeps_fixed_delivery_defaults(self) -> None:
        args = parse_args(
            [
                "photo.dng",
                "--output-format",
                "ultrahdr",
            ]
        )
        self.assertEqual(args.grade, "none")
        self.assertEqual(args.jpeg_quality, 100)
        self.assertEqual(args.chroma, "444")
        self.assertEqual(args.hdr_drt, "aces2")

    def test_gain_is_c1_monotone_and_bounded(self) -> None:
        start = HDR_DIFFUSE_WHITE_EV
        ev = np.linspace(start - 1.0, start + 5.0, 6001, dtype=np.float32)
        gain = gain_stops_for_scene_ev(ev, start + 5.0, 3.0)
        self.assertTrue(np.all(np.diff(gain) >= -1e-7))
        self.assertTrue(np.all(gain[ev <= start] == 0.0))
        self.assertTrue(np.all(gain[ev >= start + 3.5] == 3.0))
        # smoothstep has zero slope on both sides of each endpoint.
        self.assertLess(float(gain[np.searchsorted(ev, start + 0.001)]), 1e-5)
        self.assertLess(3.0 - float(gain[np.searchsorted(ev, start + 3.499)]), 1e-5)

    def test_requested_headroom_is_a_cap_not_a_normalized_target(self) -> None:
        start = HDR_DIFFUSE_WHITE_EV
        ev = np.array([start, start + 0.5, start + 1.0], dtype=np.float32)
        gain = gain_stops_for_scene_ev(ev, start + 1.0, 3.0)
        np.testing.assert_allclose(gain, [0.0, 0.5, 1.0], atol=1e-6)

    def test_hdr_alternate_keeps_sdr_chromaticity(self) -> None:
        start = HDR_DIFFUSE_WHITE_EV
        scene_ev = np.array(
            [start - 1.0, start, start + 0.25, start + 1.0, start + 4.0],
            dtype=np.float32,
        )
        scene_y = np.float32(0.18) * np.exp2(scene_ev)
        scene = np.repeat(scene_y[None, :, None], 3, axis=2)
        base = np.repeat(np.array([[[120, 80, 40]]], dtype=np.uint8), 5, axis=1)
        bundle = SimpleNamespace(
            scene_rec2020_render=scene,
            scene_scale=1.0,
            exposure_gain=1.0,
            wb_mode="camera",
            camera_wb=[1.0, 1.0, 1.0],
            daylight_wb=[1.0, 1.0, 1.0],
        )
        plan = SimpleNamespace(white_ev=start + 4.0)
        hdr = build_hdr_alternate_rgba_half(base, bundle, plan, 3.0)
        base_linear = srgb_decode(base.astype(np.float32) / 255.0)

        np.testing.assert_allclose(hdr[0, 0, :3], base_linear[0, 0], rtol=2e-3, atol=2e-4)
        np.testing.assert_allclose(hdr[0, 1, :3], base_linear[0, 1], rtol=2e-3, atol=2e-4)
        np.testing.assert_allclose(hdr[0, 4, :3], base_linear[0, 4] * 8.0, rtol=2e-3, atol=2e-4)
        for x in range(5):
            channel_gain = hdr[0, x, :3].astype(np.float32) / base_linear[0, x]
            self.assertLess(float(np.max(channel_gain) - np.min(channel_gain)), 5e-3)
        self.assertTrue(np.all(hdr[:, :, 3] == np.float16(1.0)))


class AppleGainMapWriterTests(unittest.TestCase):
    def test_public_backend_is_paused_until_hdr_agx_exists(self) -> None:
        available, reason = apple_gainmap_backend_status()
        self.assertFalse(available)
        self.assertIn("HDR AgX", reason)

    def test_public_writer_rejects_unverified_backend(self) -> None:
        base = np.full((4, 4, 3), 128, dtype=np.uint8)
        hdr = np.ones((4, 4, 4), dtype=np.float16)
        with mock.patch(
            "dngscan.gainmap.apple_gainmap_backend_status",
            return_value=(False, "round-trip not verified"),
        ):
            with self.assertRaisesRegex(RuntimeError, "round-trip not verified"):
                write_apple_gainmap_jpeg(base, hdr, Path("unused.jpg"), 100, 3.0)

    def test_declared_headroom_tracks_actual_rendition_peak(self) -> None:
        available, reason = apple_gainmap_backend_status()
        if not available:
            self.skipTest(reason)

        h, w = 16, 32
        ramp = np.linspace(64, 255, w, dtype=np.uint8)
        base = np.repeat(ramp[None, :, None], h, axis=0)
        base = np.repeat(base, 3, axis=2)
        linear = srgb_decode(base.astype(np.float32) / 255.0)
        gain = np.linspace(1.0, 2.0, w, dtype=np.float32)[None, :, None]
        hdr = np.empty((h, w, 4), dtype=np.float16)
        hdr[:, :, :3] = (linear * gain).astype(np.float16)
        hdr[:, :, 3] = np.float16(1.0)

        with tempfile.TemporaryDirectory() as td:
            info = write_apple_gainmap_jpeg(
                base, hdr, Path(td) / "scene_headroom.jpg", 100, 3.0
            )
            self.assertAlmostEqual(info["headroom"], 2.0, delta=0.02)

    def test_writes_iso_gainmap_display_p3_jpeg(self) -> None:
        available, reason = apple_gainmap_backend_status()
        if not available:
            self.skipTest(reason)

        h, w = 32, 64
        ramp = np.linspace(0, 255, w, dtype=np.uint8)
        base = np.empty((h, w, 3), dtype=np.uint8)
        base[:, :, 0] = ramp[None, :]
        base[:, :, 1] = np.minimum(ramp[None, :], 220)
        base[:, :, 2] = np.minimum(ramp[None, :], 180)
        linear = srgb_decode(base.astype(np.float32) / 255.0)
        gain = np.exp2(np.linspace(0.0, 3.0, w, dtype=np.float32))[None, :, None]
        hdr = np.empty((h, w, 4), dtype=np.float16)
        hdr[:, :, :3] = np.clip(linear * gain, 0.0, 8.0).astype(np.float16)
        hdr[:, :, 3] = np.float16(1.0)

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "native_iso_gainmap.jpg"
            written = write_apple_gainmap_jpeg(base, hdr, path, 100, 3.0)
            inspected = inspect_gainmap_jpeg(path)
            from PIL import Image

            with Image.open(path) as image:
                decoded_primary = np.asarray(image.convert("RGB"), dtype=np.uint8)
            self.assertTrue(path.is_file())
            self.assertTrue(written["has_iso_gainmap"])
            self.assertTrue(inspected["has_iso_gainmap"])
            self.assertEqual(inspected["profile"], "Display P3")
            self.assertEqual(inspected["chroma_subsampling"], "4:4:4")
            self.assertNotEqual(inspected["gainmap_pixel_format"], "L008")
            self.assertTrue(inspected["gainmap_pixel_format"])
            self.assertEqual(
                (inspected["gainmap_width"], inspected["gainmap_height"]), (w, h)
            )
            self.assertGreater(inspected["headroom"], 1.0)
            self.assertEqual((inspected["width"], inspected["height"]), (w, h))
            primary_error = np.abs(decoded_primary.astype(np.int16) - base.astype(np.int16))
            self.assertLess(float(np.mean(primary_error)), 1.0)


if __name__ == "__main__":
    unittest.main()
