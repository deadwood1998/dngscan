# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared constants for the local web GUI."""

from __future__ import annotations

RAW_EXTS = {
    ".dng", ".arw", ".cr2", ".cr3", ".nef", ".nrw", ".raf", ".rw2", ".orf",
    ".raw", ".pef", ".srw", ".x3f", ".iiq", ".3fr", ".mrw", ".dcr", ".kdc",
}

# Realtime preview is a product invariant, not a quality knob.  Keeping one
# geometry prevents sharpness and pacing changes while the user drags controls.
REALTIME_PREVIEW_LONG_EDGE = 1980
PROXY_LONG_EDGE = REALTIME_PREVIEW_LONG_EDGE
