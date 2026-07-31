# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional C++ AgX core: import, dispatch policy, and fallback."""

from __future__ import annotations

import logging
import os
from typing import Any

from ._deps import np
from .fast_plan import NATIVE_ABI_VERSION
from .models import ToneCompressionPlan

_LOG = logging.getLogger(__name__)

_extension: Any | None = None
_extension_error: str | None = None


class NativeKernelError(RuntimeError):
    """Raised when DNGSCAN_FAST=1 and the native kernel fails."""


def _load_extension() -> Any | None:
    global _extension, _extension_error
    if _extension is not None:
        return _extension
    if _extension_error is not None:
        return None
    try:
        from . import _dngscan_fast as ext
    except ImportError as exc:
        _extension_error = str(exc)
        return None
    if int(ext.native_abi_version()) != int(NATIVE_ABI_VERSION):
        _extension_error = "native ABI mismatch"
        return None
    if not bool(ext.self_test()):
        _extension_error = "native self_test failed"
        return None
    _extension = ext
    return _extension


def _require_extension() -> Any:
    ext = _load_extension()
    if ext is None:
        raise NativeKernelError(_extension_error or "native extension unavailable")
    return ext


def _fast_mode() -> str:
    raw = os.environ.get("DNGSCAN_FAST", "auto").strip().lower()
    if raw in {"0", "false", "off", "numpy"}:
        return "off"
    if raw in {"1", "true", "on", "cpp", "native"}:
        return "strict"
    return "auto"


def strict_requested() -> bool:
    return _fast_mode() == "strict"


def available() -> bool:
    if _fast_mode() == "off":
        return False
    return _load_extension() is not None


def backend_name() -> str:
    return "cpp" if available() else "numpy"


def supports_agx(plan: ToneCompressionPlan) -> bool:
    if str(getattr(plan, "tone_core", "agx")) != "agx":
        return False
    if not bool(getattr(plan, "use_c1_endpoints", False)):
        return False
    if str(getattr(plan, "film_mode", "observe")) == "full" and str(
        getattr(plan, "curve_preset", "none")
    ) != "none":
        # Film takeover renders through film_develop, not the AgX kernel. In the
        # default "observe" mode a film preset is just curve parameters — the
        # native kernel handles it at full speed.
        return False
    if _fast_mode() == "off":
        return False
    if _load_extension() is None:
        return False
    try:
        from .fast_plan import compile_agx_plan

        compile_agx_plan(plan)
    except Exception:
        return False
    return True


def can_use_agx(rgb: Any, plan: ToneCompressionPlan) -> bool:
    if not supports_agx(plan):
        return False
    arr = np.asarray(rgb)
    if arr.ndim != 2 or arr.shape[1] != 3:
        return False
    if arr.dtype != np.float32:
        return False
    if not arr.flags["C_CONTIGUOUS"]:
        return False
    if not np.isfinite(arr).all():
        return False
    return True


def compile_agx_plan(plan: ToneCompressionPlan) -> Any:
    from .fast_plan import compile_agx_plan as _compile

    return _compile(plan)


def apply_agx_core_f32(rgb: np.ndarray, plan: Any) -> np.ndarray:
    ext = _require_extension()
    arr = np.ascontiguousarray(rgb, dtype=np.float32)
    if arr.ndim != 2 or arr.shape[1] != 3:
        raise ValueError("rgb must be (N, 3) float32")
    return ext.apply_agx_core_f32(arr, plan)
