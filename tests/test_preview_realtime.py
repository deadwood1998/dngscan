# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import dngscan as dg
from dngscan.gui.constants import REALTIME_PREVIEW_LONG_EDGE
from dngscan.gui.preview_cache import PreviewEntry
from dngscan.gui.preview_scheduler import PREVIEW_COORDINATOR, PreviewCoordinator
from dngscan.gui.service import _cached_render_plan, run_preview


class PreviewCoordinatorTests(unittest.TestCase):
    def test_newest_generation_supersedes_older_requests(self) -> None:
        coordinator = PreviewCoordinator()
        self.assertTrue(coordinator.register("session", 1))
        self.assertTrue(coordinator.is_current("session", 1))
        self.assertTrue(coordinator.register("session", 3))
        self.assertFalse(coordinator.is_current("session", 1))
        self.assertFalse(coordinator.register("session", 2))
        self.assertTrue(coordinator.is_current("session", 3))

    def test_legacy_generation_zero_does_not_change_session_state(self) -> None:
        coordinator = PreviewCoordinator()
        self.assertTrue(coordinator.register("session", 0))
        self.assertTrue(coordinator.is_current("session", 0))
        self.assertTrue(coordinator.register("session", 4))
        self.assertTrue(coordinator.is_current("session", 0))
        self.assertTrue(coordinator.is_current("session", 4))

    def test_run_preview_rejects_stale_generation_before_decode(self) -> None:
        PREVIEW_COORDINATOR.clear()
        with tempfile.NamedTemporaryFile(suffix=".dng") as source:
            PREVIEW_COORDINATOR.register("browser-session", 2)
            result = run_preview(
                {
                    "input": source.name,
                    "previewSession": "browser-session",
                    "generation": 1,
                }
            )
        self.assertEqual(
            result, {"ok": True, "superseded": True, "generation": 1}
        )


class PreviewPlanCacheTests(unittest.TestCase):
    def test_base_plan_is_reused_across_interactive_adjustments(self) -> None:
        bundle = MagicMock()
        bundle.lens_filter = "none"
        entry = PreviewEntry(bundle=bundle, analysis=MagicMock())
        base_plan = object()
        adjusted_plan = object()

        def compile_plan(adjustments: dg.RenderAdjustments | None) -> object:
            return _cached_render_plan(
                entry,
                bundle,
                "srgb",
                "none",
                1.0,
                1.0,
                "agx",
                "y",
                "base",
                "none",
                adjustments,
            )

        with patch(
            "dngscan.gui.service.dg.build_render_plan", return_value=base_plan
        ) as build, patch(
            "dngscan.gui.service.dg.apply_render_adjustments",
            return_value=adjusted_plan,
        ) as apply:
            self.assertIs(compile_plan(None), adjusted_plan)
            self.assertIs(
                compile_plan(dg.RenderAdjustments(midtone_brightness=0.5)),
                adjusted_plan,
            )

        build.assert_called_once()
        self.assertEqual(apply.call_count, 2)

    def test_fixed_resolution_constant_is_960(self) -> None:
        self.assertEqual(REALTIME_PREVIEW_LONG_EDGE, 960)


if __name__ == "__main__":
    unittest.main()
