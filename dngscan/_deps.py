# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional runtime dependencies (numpy, rawpy, matplotlib)."""
from __future__ import annotations

IMPORT_ERRORS: list[str] = []

try:
    import numpy as np
except Exception as exc:  # pragma: no cover - exercised only on missing deps
    np = None  # type: ignore[assignment]
    IMPORT_ERRORS.append(f"numpy: {exc}")

try:
    import rawpy
except Exception as exc:  # pragma: no cover - exercised only on missing deps
    rawpy = None  # type: ignore[assignment]
    IMPORT_ERRORS.append(f"rawpy: {exc}")
else:
    from .libraw_policy import rawpy_runtime_problem

    if problem := rawpy_runtime_problem(rawpy):
        IMPORT_ERRORS.append(f"rawpy: {problem}")

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.image as mpimg
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from matplotlib.colors import ListedColormap
    from matplotlib.patches import Patch
except Exception as exc:  # pragma: no cover - exercised only on missing deps
    matplotlib = None  # type: ignore[assignment]
    mpimg = None  # type: ignore[assignment]
    plt = None  # type: ignore[assignment]
    font_manager = None  # type: ignore[assignment]
    ListedColormap = None  # type: ignore[assignment]
    Patch = None  # type: ignore[assignment]
    IMPORT_ERRORS.append(f"matplotlib: {exc}")
