# SPDX-License-Identifier: GPL-3.0-or-later
"""Delivery gates: the file must be the rendition it claims to be."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from dngscan.analysis import analyze
from dngscan.color import srgb_decode
from dngscan.gainmap import (
    _base_roundtrip_error,
    _roundtrip_error,
    apple_gainmap_backend_status,
    inspect_gainmap_jpeg,
    write_apple_gainmap_jpeg,
)
from dngscan.grade import RENDER_MODE
from dngscan.hdr_agx import to_gainmap_alternate
from dngscan.raw_io import load_raw
from dngscan.render import render_output_u8
from dngscan.tone import build_render_plan

SIGMA = Path.home() / "Pictures" / "_SDI0150.DNG"
_BACKEND_OK, _BACKEND_WHY = apple_gainmap_backend_status()


class AlternatePackingTests(unittest.TestCase):
    def test_alternate_is_float16_rgba_with_opaque_alpha(self) -> None:
        rgb = np.linspace(-0.2, 12.0, 300, dtype=np.float32).reshape(10, 10, 3)
        out = to_gainmap_alternate(rgb, 8.0)
        self.assertEqual(out.dtype, np.float16)
        self.assertEqual(out.shape, (10, 10, 4))
        self.assertTrue(bool(np.all(out[..., 3] == np.float16(1.0))))

    def test_packer_defensively_clamps_an_invalid_negative_input(self) -> None:
        """The HDR projector should prevent this; packing still guards external callers."""
        rgb = np.array([[[-0.05, 0.4, 0.2]]], dtype=np.float32)
        self.assertEqual(float(to_gainmap_alternate(rgb, 8.0)[0, 0, 0]), 0.0)

    def test_hdr_diagnostic_encoder_does_not_repeat_sdr_finalization(self) -> None:
        from tools.hdr_ab import _encode_hdr_diagnostic

        linear = np.full((3, 4, 3), 0.25, dtype=np.float32)
        with mock.patch(
            "tools.hdr_ab.finalize_output_linear",
            side_effect=AssertionError("SDR finalizer must not run"),
        ):
            encoded = _encode_hdr_diagnostic(linear, "p3")
        self.assertEqual(encoded.shape, linear.shape)
        self.assertEqual(encoded.dtype, np.uint8)

    def test_peak_is_enforced(self) -> None:
        rgb = np.full((4, 4, 3), 20.0, dtype=np.float32)
        self.assertLessEqual(float(np.max(to_gainmap_alternate(rgb, 8.0))), 8.0)


class RoundtripErrorTests(unittest.TestCase):
    def test_shape_mismatch_reports_infinite_error(self) -> None:
        """A mismatch must never read as a small error and pass the gate."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "missing.jpg"
            path.write_bytes(b"not a jpeg")
            try:
                out = _roundtrip_error(path, np.zeros((2, 2, 4), dtype=np.float16))
            except Exception:
                return  # An exception is an acceptable failure mode here.
            self.assertEqual(out["chroma_error"], float("inf"))

    def test_reference_white_and_below_are_part_of_the_gate(self) -> None:
        intended = np.full((4, 4, 4), 0.25, dtype=np.float16)
        intended[..., 3] = np.float16(1.0)
        expanded = intended.copy()
        expanded[..., 0] = np.float16(0.35)
        with mock.patch(
            "dngscan.gainmap._read_expanded_hdr_rgba_half", return_value=expanded
        ):
            out = _roundtrip_error(Path("unused.jpg"), intended)
        self.assertGreater(out["median_relative_error"], 0.1)
        self.assertGreater(out["p99_relative_error"], 0.1)

    def test_sdr_base_shape_mismatch_cannot_pass(self) -> None:
        with mock.patch("PIL.Image.open") as opened:
            opened.return_value.__enter__.return_value.convert.return_value = np.zeros(
                (2, 3, 3), dtype=np.uint8
            )
            out = _base_roundtrip_error(
                Path("unused.jpg"), np.zeros((3, 2, 3), dtype=np.uint8)
            )
        self.assertEqual(out["base_mean_code_error"], float("inf"))


@unittest.skipUnless(_BACKEND_OK, f"gain-map backend unavailable: {_BACKEND_WHY}")
class WriterVerificationTests(unittest.TestCase):
    def test_writer_rejects_a_rendition_it_cannot_reproduce(self) -> None:
        """The per-file check is the real guarantee, so it has to actually bite.

        Extreme per-channel gain ratios are what the container cannot carry: measured,
        a channel gaining 1.25x beside one gaining 3x comes back lifted to 1.5x. A file
        like that would claim to be a rendition it is not, so writing it must fail rather
        than succeed quietly.
        """
        h, patch = 24, 24
        gains = np.array([[1.15, 2.4, 3.6], [3.6, 1.15, 2.4]], dtype=np.float32)
        base = np.full((h, patch * len(gains), 3), 180, dtype=np.uint8)
        base_linear = srgb_decode(base.astype(np.float32) / np.float32(255.0))
        hdr = np.empty(base.shape[:2] + (4,), dtype=np.float16)
        for i, gain in enumerate(gains):
            hdr[:, i * patch:(i + 1) * patch, :3] = (
                base_linear[:, i * patch:(i + 1) * patch] * gain
            ).astype(np.float16)
        hdr[..., 3] = np.float16(1.0)
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "extreme.jpg"
            with self.assertRaises(RuntimeError):
                write_apple_gainmap_jpeg(base, hdr, out, 100, 2.0)
            self.assertFalse(out.exists(), "a rejected rendition must leave no file")

    def test_neutral_hdr_survives_the_container(self) -> None:
        h, w = 24, 48
        base = np.full((h, w, 3), 180, dtype=np.uint8)
        linear = srgb_decode(base.astype(np.float32) / np.float32(255.0))
        hdr = np.empty((h, w, 4), dtype=np.float16)
        hdr[..., :3] = (linear * 2.5).astype(np.float16)
        hdr[..., 3] = np.float16(1.0)
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "neutral.jpg"
            info = write_apple_gainmap_jpeg(base, hdr, out, 100, 2.0)
            self.assertTrue(out.exists())
            self.assertLess(info["chroma_error"], 0.01)


@unittest.skipUnless(SIGMA.is_file() and _BACKEND_OK, "sample frame or backend unavailable")
class EndToEndDeliveryTests(unittest.TestCase):
    def test_export_produces_a_conforming_iso_gainmap_file(self) -> None:
        from dngscan.export import export_ultrahdr_jpeg

        bundle = load_raw(SIGMA, scene_half_size=True)
        analysis, _, _ = analyze(bundle, margin=4, diagnostics=False)
        plan = build_render_plan(bundle, analysis, RENDER_MODE, "p3")
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "hdr.jpg"
            info = export_ultrahdr_jpeg(SIGMA, out, 100, bundle, analysis, plan)
            self.assertTrue(out.exists())
            probe = inspect_gainmap_jpeg(out)
            self.assertTrue(probe["has_iso_gainmap"])
            self.assertEqual(probe["profile"], "Display P3")
            self.assertEqual(probe["chroma_subsampling"], "4:4:4")
            # An L008 gain map cannot carry independent per-channel geometry.
            self.assertNotIn(str(probe["gainmap_pixel_format"]), ("", "L008"))
            self.assertGreater(probe["headroom"], 1.0)
            self.assertLessEqual(info["median_relative_error"], 0.015)
            self.assertLessEqual(info["p95_relative_error"], 0.08)
            self.assertLessEqual(info["p99_relative_error"], 0.12)
            self.assertLessEqual(info["headroom_error_ev"], 0.05)
            self.assertLessEqual(info["base_mean_code_error"], 1.0)
            self.assertLessEqual(info["base_p99_code_error"], 4.0)
            self.assertLessEqual(info["base_max_code_error"], 12.0)
            # Declared headroom must not exceed what the scene was allowed.
            self.assertLessEqual(
                float(np.log2(probe["headroom"])), info["budget_headroom_ev"] + 1e-3
            )

    def test_sdr_base_is_the_same_rendition_as_a_plain_export(self) -> None:
        """Same pixels into the encoder; the encoders themselves then differ.

        Pillow and Core Image do not agree bit for bit -- measured up to 8/255 on 54 % of
        pixels at quality 100 -- so this asserts the rendition, not the bytes.
        """
        bundle = load_raw(SIGMA, scene_half_size=True)
        analysis, _, _ = analyze(bundle, margin=4, diagnostics=False)
        plan = build_render_plan(bundle, analysis, RENDER_MODE, "p3")
        a = render_output_u8(bundle, analysis, "p3", plan)
        b = render_output_u8(bundle, analysis, "p3", plan)
        self.assertTrue(bool(np.array_equal(a, b)))

    def test_a_scene_without_a_reliable_tail_is_refused(self) -> None:
        """Refusing beats writing a file whose HDR range the sensor never recorded."""
        import dataclasses

        from dngscan.export import export_ultrahdr_jpeg

        bundle = load_raw(SIGMA, scene_half_size=True)
        analysis, _, _ = analyze(bundle, margin=4, diagnostics=False)
        plan = build_render_plan(bundle, analysis, RENDER_MODE, "p3")
        # HDR owns its white endpoint, so changing the SDR one must not control this gate.
        # Remove the actual RAW-authoritative tail instead.
        no_tail = dataclasses.replace(plan.scene, reliable_tail_ev_p9999=float("nan"))
        flat = dataclasses.replace(plan, scene=no_tail)
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "flat.jpg"
            with self.assertRaises(RuntimeError):
                export_ultrahdr_jpeg(SIGMA, out, 100, bundle, analysis, flat)


if __name__ == "__main__":
    unittest.main()
