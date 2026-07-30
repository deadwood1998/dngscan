#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# Rebuild rawpy from source against a pinned LibRaw *master* snapshot.
#
# Why: rawpy wheels bundle the newest LibRaw *release* (0.22.x), whose camera
# colour tables lag ~1-2 years behind master. Bodies like the Nikon Zf or Sony
# A7 V decode with an empty colour matrix on the wheel build (see
# docs/SENSOR_SUPPORT.zh-CN.md). Master's soname bump (25 -> 26) makes a dylib
# swap unsafe, so the supported path is this source rebuild: rawpy's own sdist
# build machinery compiles and bundles whatever sits in external/LibRaw.
#
# The fallback matrix table (dngscan/camera_matrices.py) stays useful either
# way: it covers bodies missing even from master (A7R VI as of the pin below).
#
# Usage:  sh tools/build_libraw_master.sh [work_dir]
set -e

RAWPY_VERSION=0.27.0
# LibRaw master snapshot this procedure was validated against (2026-07-18).
# Move the pin deliberately: rerun the full test suite and re-baseline the
# SDR freeze/golden fixtures if decode output shifts.
LIBRAW_COMMIT=e419de08001de28ae6988ecb22df47e52b9c5eaa

ROOT=$(cd "$(dirname "$0")/.." && pwd)
PIP="$ROOT/.venv/bin/pip"
WORK=${1:-"$ROOT/build/libraw-master"}
mkdir -p "$WORK"
cd "$WORK"

"$PIP" install cmake >/dev/null
"$PIP" download "rawpy==$RAWPY_VERSION" --no-binary rawpy --no-deps -d .
tar xzf "rawpy-$RAWPY_VERSION.tar.gz"
cd "rawpy-$RAWPY_VERSION"

# The sdist VENDORS the release LibRaw at external/LibRaw — it must be
# force-replaced, not created-if-missing (the trap that silently rebuilds the
# old release: the install "succeeds" with the stale camera table).
rm -rf external/LibRaw external/LibRaw-cmake/build build
git clone https://github.com/LibRaw/LibRaw.git external/LibRaw
git -C external/LibRaw checkout "$LIBRAW_COMMIT"

PATH="$ROOT/.venv/bin:$PATH" "$PIP" install . --force-reinstall --no-deps

"$ROOT/.venv/bin/python" - <<'EOF'
import rawpy
print("rawpy:", rawpy.__version__, "libraw:", rawpy.libraw_version)
EOF
echo "Done. Run the full test suite; expect SDR freeze/golden drift if decode changed."
