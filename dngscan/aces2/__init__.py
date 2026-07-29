# SPDX-License-Identifier: GPL-3.0-or-later
"""ACES 2-derived HDR NumPy kernel.

The implementation baseline is ACES ``v2-dev-release-2`` (2024), not the
substantially revised formal 2025 ACES 2 release. See ``REFERENCE.md``.
"""
from __future__ import annotations

from .output_transform import render_aces2_hdr_p3_linear

__all__ = ["render_aces2_hdr_p3_linear"]
