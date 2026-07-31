#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# Rebuild rawpy from source against a pinned LibRaw *master* snapshot.
#
# Why: rawpy wheels bundle the newest LibRaw *release* (0.22.x), whose camera
# colour tables lag ~1-2 years behind master. Sony A7 V, for example, decodes
# with an empty colour matrix on the wheel build (see docs/SENSOR_SUPPORT.zh-CN.md).
# Master's soname bump (25 -> 26) makes a dylib
# swap unsafe, so the supported path is this source rebuild: rawpy's own sdist
# build machinery compiles and bundles whatever sits in external/LibRaw.
#
# The fallback matrix table (dngscan/camera_matrices.py) stays useful either
# way: it covers bodies missing even from master (A7R VI as of the pin below).
#
# Usage:  sh tools/build_libraw_master.sh [work_dir]
set -e

ROOT=$(cd "$(dirname "$0")/.." && pwd)
. "$ROOT/tools/libraw-pin.env"
# Move the pin deliberately: rerun the full test suite and re-baseline the
# SDR freeze/golden fixtures if decode output shifts.
PIP="$ROOT/.venv/bin/pip"
WORK=${1:-"$ROOT/build/libraw-master"}
mkdir -p "$WORK"
cd "$WORK"

"$PIP" install cmake >/dev/null
"$PIP" download "rawpy==$RAWPY_VERSION" --no-binary rawpy --no-deps -d .
rm -rf "rawpy-$RAWPY_VERSION"
tar xzf "rawpy-$RAWPY_VERSION.tar.gz"
cd "rawpy-$RAWPY_VERSION"

# The sdist VENDORS the release LibRaw at external/LibRaw — it must be
# force-replaced, not created-if-missing (the trap that silently rebuilds the
# old release: the install "succeeds" with the stale camera table).
rm -rf external/LibRaw external/LibRaw-cmake/build build
git clone https://github.com/LibRaw/LibRaw.git external/LibRaw
git -C external/LibRaw checkout "$LIBRAW_COMMIT"
test "$(git -C external/LibRaw rev-parse HEAD)" = "$LIBRAW_COMMIT"

PATH="$ROOT/.venv/bin:$PATH" "$PIP" install . --force-reinstall --no-deps

cd "$ROOT"
"$ROOT/.venv/bin/python" - <<'EOF'
import rawpy
from dngscan.libraw_policy import MIN_RAWPY_VERSION, PINNED_LIBRAW_COMMIT

assert rawpy.__version__ == MIN_RAWPY_VERSION, (rawpy.__version__, MIN_RAWPY_VERSION)
print(
    "rawpy:", rawpy.__version__,
    "libraw:", rawpy.libraw_version,
    "source-pin:", PINNED_LIBRAW_COMMIT,
)
EOF
echo "Done. Run the full test suite; expect SDR freeze/golden drift if decode changed."
