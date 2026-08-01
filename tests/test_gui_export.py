# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for GUI export naming."""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from dngscan._deps import np
from dngscan.gui.page import render_page
from dngscan.gui.server import store_upload
from dngscan.gui.service import downsample_mean
from dngscan.gui.service import export_suffix_parts


class ExportSuffixTests(unittest.TestCase):
    def test_proxy_downsample_reaches_requested_long_edge(self) -> None:
        source = np.zeros((303, 202, 3), dtype=np.uint16)
        proxy = downsample_mean(source, 128)
        self.assertEqual(proxy.shape, (128, 85, 3))

    def test_public_gui_is_concise_and_has_no_vendor_luts(self) -> None:
        html = render_page("/tmp").decode("utf-8")
        self.assertNotIn("更新预览", html)
        self.assertIn('<button class="go" id="go">导出</button>', html)
        self.assertNotIn(">导出 JPEG</button>", html)
        self.assertIn("前馈校正", html)
        self.assertIn("中间调亮度", html)
        self.assertIn("中间调对比", html)
        self.assertIn("暗部过渡", html)
        self.assertIn("高光过渡", html)
        self.assertIn("高光褪白", html)
        self.assertIn("中频纯度", html)
        self.assertIn("HDR gain-map · JPEG", html)
        self.assertIn("HDR gain-map · HEIC", html)
        self.assertIn("只恢复漫反射白以上的真实亮度档数", html)
        self.assertIn("/raw9-support", html)
        self.assertIn("此文件不支持 RAW 9", html)
        self.assertIn('type="file" id="filePicker"', html)
        self.assertIn('accept=".3fr,.arw,.cr2,.cr3,.dcr,.dng', html)
        self.assertIn('fetch("/upload?name="', html)
        self.assertNotIn("文件只传给本机", html)
        self.assertNotIn("filePickerHint", html)
        self.assertNotIn('id="browseBtn"', html)
        self.assertNotIn('id="browser"', html)
        self.assertNotIn('optgroup label="本地 LUT"', html)
        # Vendor display LUTs must never leak into the public GUI. Named film
        # observation presets ("Kodak Portra 400", "Fujifilm Superia X-TRA 400") are
        # NOT vendor LUTs — they are dngscan's own calibrated declarations fitted from
        # published datasheet data — so the guard targets LUT product names, not the
        # manufacturers whose stocks the film feature legitimately names.
        for vendor_lut in ("ARRI Classic", "ARRI Reveal", "RED IPP2", "LC-709", "2383", ".cube"):
            self.assertNotIn(vendor_lut, html)
        self.assertIn("Kodak Portra 400", html)
        self.assertIn("Fujifilm Superia X-TRA 400", html)

    def test_default_agx_only(self) -> None:
        self.assertEqual(export_suffix_parts("clip", "srgb", "sdr"), "agx")

    def test_nondefault_primaries_path_is_named(self) -> None:
        self.assertEqual(
            export_suffix_parts("clip", "srgb", "sdr", agx_primaries="smooth"),
            "agx_smooth",
        )
        self.assertEqual(
            export_suffix_parts("clip", "srgb", "sdr", tone_core="gated", agx_primaries="base"),
            "gated",
        )

    def test_includes_grade(self) -> None:
        self.assertEqual(
            export_suffix_parts("clip", "srgb", "sdr", "look:optic_warm_cyan", 1.0),
            "agx_look_optic_warm_cyan",
        )
        self.assertEqual(
            export_suffix_parts("clip", "srgb", "sdr", "filter:kodak_2383_d65", 1.0),
            "agx_filter_kodak_2383_d65",
        )

    def test_includes_grade_strength_when_not_one(self) -> None:
        self.assertEqual(
            export_suffix_parts("clip", "srgb", "sdr", "look:optic_warm_cyan", 0.8),
            "agx_look_optic_warm_cyan_gs0.8",
        )

    def test_includes_scene_transform(self) -> None:
        self.assertEqual(
            export_suffix_parts("clip", "p3", "sdr", "none", 1.0, "arri_skin_d55", 0.75),
            "agx_p3_arri_skin_d55_st0.75",
        )

    def test_neutral_export_suffix(self) -> None:
        self.assertEqual(
            export_suffix_parts("clip", "srgb", "sdr", tone_core="neutral"),
            "neutral",
        )
        self.assertEqual(
            export_suffix_parts("clip", "srgb", "sdr", tone_core="lum"),
            "lum",
        )
        self.assertEqual(
            export_suffix_parts("clip", "srgb", "sdr", tone_core="lum", lum_norm="power"),
            "lum_power",
        )


class BrowserUploadTests(unittest.TestCase):
    def test_store_upload_keeps_only_safe_filename_and_exact_bytes(self) -> None:
        payload = b"example raw bytes"
        with tempfile.TemporaryDirectory() as temp:
            saved = store_upload(
                "../../folder/My Photo.DNG",
                io.BytesIO(payload),
                len(payload),
                Path(temp),
            )
            self.assertEqual(saved.name, "My_Photo.dng")
            self.assertEqual(saved.read_bytes(), payload)
            self.assertEqual(saved.parent.parent, Path(temp))

    def test_store_upload_rejects_non_raw_extension(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "不支持的 RAW"):
                store_upload("photo.jpg", io.BytesIO(b"jpeg"), 4, Path(temp))

    def test_store_upload_rejects_incomplete_body(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "传输未完成"):
                store_upload("photo.dng", io.BytesIO(b"short"), 10, Path(temp))


if __name__ == "__main__":
    unittest.main()
