# Third-party notices

## darktable AgX (GPL-3.0-or-later)

The `agx` tone-mapping mode in `dngscan.core` ports portions of the AgX view-transform
implementation from darktable:

- https://github.com/darktable-org/darktable/blob/cf5e698c1a5afac52de785c3bf63fcbcb71707d3/src/iop/agx.c
- https://github.com/darktable-org/darktable/blob/cf5e698c1a5afac52de785c3bf63fcbcb71707d3/data/kernels/agx.cl

darktable is licensed under GPL-3.0-or-later. Because this project incorporates that
code, the combined work is distributed under **GPL-3.0-or-later** as well.
Reference copies of `agx.c` and `agx.cl` are included under `dngscan_assets/` with
their original GPL notices intact. The exact upstream commit is recorded in
`dngscan_assets/README.md` so changes in darktable `master` cannot silently redefine
dngscan's rendering baseline.

The AgX inset/outset primaries derive from Troy Sobotka's AgX family of view
transforms. Optional Blender-reference geometries follow the published construction
used by Eary Chow's AgX LUT generator:

- https://github.com/EaryChow/AgX_LUT_Gen

No third-party display or camera LUT is distributed with dngscan.

## ACES (Apache-2.0)

The ACES 2-derived HDR reference kernel under `dngscan/aces2/` ports algorithms
from the Academy Software Foundation's ACES project:

- https://github.com/AcademySoftwareFoundation/aces
- https://github.com/AcademySoftwareFoundation/aces-core
- https://github.com/AcademySoftwareFoundation/aces-output

That source code is licensed under Apache-2.0. Pinned release commits and the
Python-to-CTL function map are recorded in `dngscan/aces2/REFERENCE.md`.
Local reference checkouts under `_ref/` are gitignored and not distributed.

## RAW to ACES spectral data (Apache-2.0)

Selected camera sensitivities and training reflectances under
`dngscan_assets/spectral/` come from the Academy Software Foundation's
`rawtoaces-data` repository:

- https://github.com/AcademySoftwareFoundation/rawtoaces-data

That source repository is licensed under Apache-2.0. Derived CSV files retain
source and measurement notes in `dngscan_assets/spectral/README.md`.
