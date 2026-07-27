# SPDX-License-Identifier: GPL-3.0-or-later
"""Compile ToneCompressionPlan values into immutable native AgX parameters."""

from __future__ import annotations

import math
from typing import Any

from ._deps import np
from . import agx as agx_engine
from . import drt as drt_engine
from .constants import OKLAB_M1, OKLAB_M1_INV, OKLAB_M2, OKLAB_M2_INV, RGB_TO_XYZ, XYZ_TO_RGB
from .models import ToneCompressionPlan

NATIVE_ABI_VERSION = 2

# Compiled plans are tiny, but every distinct scene compiles a distinct plan, so an
# unbounded dict grows for the lifetime of a GUI server session. FIFO-evict beyond this.
_PLAN_CACHE_MAX = 64
_plan_cache: dict[tuple[Any, ...], Any] = {}


def _flat_matrix(matrix: Any) -> tuple[float, ...]:
    return tuple(float(matrix[i, j]) for i in range(3) for j in range(3))


def _curve_key(params: dict[str, float | bool]) -> tuple[Any, ...]:
    return tuple(params[k] for k in sorted(params))


def _plan_cache_key(plan: ToneCompressionPlan) -> tuple[Any, ...]:
    inset, outset = agx_engine.formation_matrices(plan)
    curve = drt_engine.curve_params_from_plan(plan)
    return (
        _flat_matrix(inset),
        _flat_matrix(outset),
        _curve_key(curve),
        float(plan.hue_restore),
        float(plan.view_brightness),
        float(plan.punch_strength),
        _flat_matrix(RGB_TO_XYZ["Rec2020"]),
        _flat_matrix(XYZ_TO_RGB["Rec2020"]),
        _flat_matrix(OKLAB_M1),
        _flat_matrix(OKLAB_M2),
        _flat_matrix(OKLAB_M1_INV),
        _flat_matrix(OKLAB_M2_INV),
    )


def _finite_plan(plan: ToneCompressionPlan) -> bool:
    for value in _plan_cache_key(plan):
        if isinstance(value, tuple):
            for item in value:
                if isinstance(item, float) and not math.isfinite(item):
                    return False
                if isinstance(item, bool):
                    continue
        elif isinstance(value, float) and not math.isfinite(value):
            return False
    return True


def _build_native_plan(plan: ToneCompressionPlan) -> Any:
    from types import SimpleNamespace

    try:
        from . import _dngscan_fast as ext
    except ImportError as exc:
        raise RuntimeError(str(exc)) from exc
    inset, outset = agx_engine.formation_matrices(plan)
    curve_py = drt_engine.curve_params_from_plan(plan)
    return SimpleNamespace(
        inset=_flat_matrix(inset),
        outset=_flat_matrix(outset),
        curve=SimpleNamespace(**curve_py),
        hue_restore=float(plan.hue_restore),
        view_brightness=max(1e-12, float(plan.view_brightness)),
        punch_strength=float(plan.punch_strength),
        rec2020_to_xyz=_flat_matrix(RGB_TO_XYZ["Rec2020"]),
        xyz_to_rec2020=_flat_matrix(XYZ_TO_RGB["Rec2020"]),
        oklab_m1=_flat_matrix(OKLAB_M1),
        oklab_m2=_flat_matrix(OKLAB_M2),
        oklab_m1_inv=_flat_matrix(OKLAB_M1_INV),
        oklab_m2_inv=_flat_matrix(OKLAB_M2_INV),
    )


def compile_agx_plan(plan: ToneCompressionPlan) -> Any:
    """Return a cached immutable native plan for one tone plan."""
    if not _finite_plan(plan):
        raise ValueError("tone plan contains non-finite parameters")
    key = _plan_cache_key(plan)
    cached = _plan_cache.get(key)
    if cached is None:
        if len(_plan_cache) >= _PLAN_CACHE_MAX:
            _plan_cache.pop(next(iter(_plan_cache)))
        cached = _build_native_plan(plan)
        _plan_cache[key] = cached
    return cached
