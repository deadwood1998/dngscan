# dngscan

This is a small RAW-to-JPEG tool I wrote for myself.

I like what AgX does to digital images, especially its highlights and highly saturated
colors, but I usually only want to develop one RAW through AgX rather than open a full
photo editor. darktable's scene-linear pipeline is the foundation of this project; it
simply contains far more than I need for this particular job.

dngscan follows that one path: read the RAW, analyse the signal the sensor actually
recorded, form the image in scene-linear Rec.2020, compile a tone plan from the RAW
analysis, and compress it through AgX into an sRGB or Display P3 JPEG. It is not a photo
editor. I think of it as a very narrow digital developer, or a small signal-and-algorithm
toy.

The repository is public mainly so friends can use it too. Anyone interested can tinker
with the code, parameters, and data from other cameras.

[中文说明](README.zh-CN.md) · [License](LICENSE) · [Third-party notices](NOTICE.md)

## Why I made a separate pipeline

I have always thought of darktable's scene-referred pipeline as a signal-processing
laboratory, where much of the pleasure comes from understanding what every module does
to the signal. dngscan takes out the path I use most: LibRaw interpretation,
scene-linear Rec.2020, and the curve construction and primary geometry from darktable's
GPL `agx` module. AgX originated with Troy Sobotka and developed through the Blender /
EaryChow ecosystem; this project mainly inherits it through darktable's photographic
implementation.

There would not be much point in merely extracting darktable's AgX module. The useful
part of dngscan is carrying information from RAW capture all the way into the final
display transform.

darktable's AgX module receives a floating-point image after demosaic, white balance,
and exposure. It sees the image, but not the original CFA: it cannot know which channel
really clipped on the sensor, or whether a smooth highlight contains measured signal or
values invented by highlight reconstruction. A small integrated pipeline can preserve
that evidence before demosaic, then use it to distinguish the reliable scene body, the
sensor tail, and highlights whose original information has already been lost.

That is also what “automatic” means here. It is not an attempt to make aesthetic choices
for a photograph. It assigns measurable questions to measurements: black and white
levels, per-channel CFA clipping, noise floor, usable dynamic range, the luminance body,
and the highlight tail. These can decide how much scene EV the curve must contain, when
chroma may retreat toward white, and when a reconstructed pixel should not be trusted.

Exposure compensation, white balance, looks, and LUTs are different. They express
capture intent or taste, so they remain explicit choices outside the automatic AgX
analysis. I do not require exposure and white balance to remain untouched; I only do
not want a content-adaptive algorithm silently turning a night scene gray or removing
the color of its original light.

## Pipeline

```text
RAW / DNG
  |
  +-- Capture
  |     black / white level
  |     per-channel CFA clipping and headroom
  |     noise confidence and usable dynamic range
  |     demosaic, highlight handling, camera interpretation
  |
scene-linear Rec.2020
  |
  +-- optional camera-response prefeed
  |
  +-- Tone
  |     black point / white point / pivot
  |     contrast / toe / shoulder / view brightness
  |
  +-- Color geometry
  |     AgX inset / outset / hue path
  |     RAW clip retreat / punch / gamut fit
  |     optional look or local LUT
  |
  +-- Delivery
        sRGB / Display P3
        8-bit TPDF dither
        JPEG quality and chroma sampling
```

These layers are deliberately separate. Tone controls luminance relationships and the
display dynamic range. Color geometry controls hue paths, chroma compression, and the
path to white. Capture supplies evidence without directly deciding taste. When one
stage changes the image, its reason should remain identifiable.

## Capture: where the RAW evidence comes from

### Black, white, and per-channel clipping

dngscan reads the pre-demosaic CFA from `raw_image_visible` and
`raw_colors_visible`. Black level comes from metadata. Full well first looks for a
credible saturation pile at the top of each channel and uses that measured ceiling
when present; otherwise it falls back to per-channel metadata white levels. The values
are never collapsed into one scalar for all R/G/B, so clipping is a threshold map
indexed by CFA color. If no channel has a reliable pile, the report labels full well as
a metadata fallback rather than presenting the estimate as a measurement.

This affects more than the clip percentage in a report. Spatial clip maps, 2x2 cell
metrics, highlight classes, and render-time clip masks use the same thresholds. If a
camera's green channel reaches full well before red, the rest of the pipeline should
know that green information was lost first rather than treating all three channels as
simultaneously clipped.

Highlight reconstruction can create continuous luminance and plausible color, but it
cannot recover signal the sensor never recorded. Clipping evidence is saved before
reconstruction, so a repaired pixel can never feed back and define the global white
endpoint.

### Demosaic

Full-resolution `auto` export tries DHT, DCB, then AHD according to what the local
rawpy/LibRaw build actually supports. Non-Bayer data such as X-Trans stays on the
corresponding LibRaw path. Preview uses half-size 2x2 superpixel binning, so it is useful
for exposure, color, and highlight decisions but not for judging final texture.

dngscan performs no denoising, which makes demosaic the main texture choice. DHT suits
clean low-ISO signal; DCB, AAHD, VNG, or PPG can look more natural on noisy night files.
Standard rawpy wheels do not necessarily include GPL demosaic-pack algorithms such as
AMaZE, LMMSE, VCD, or AFD, so the available set depends on the local LibRaw build. The
GUI/CLI can select `dht / dcb / ahd / aahd / vng / ppg` manually; an algorithm supplied
by another LibRaw build only needs an entry in `DEMOSAIC_CHOICES` to use the existing
availability check and fallback logic.

### Optional Core Image pipeline

`--decoder coreimage` is a fifth control path in the same spirit as the `lum` /
`neutral` tone cores: an alternate *interpretation* of the capture, not a quality
upgrade and never the default. It uses `CIRAWFilter` (RAW 9 where the file offers it,
otherwise the highest supported version — some Fujifilm RAF files stop at 8 and are not
labelled 9), rendered with every subjective control zeroed into linear Rec.2020, so it
hands AgX the same working space the LibRaw path does.

It is a **separate pipeline, not a LibRaw back end.** Core Image executes the DNG
opcodes a file carries; on a Sigma fp DNG that means a per-plane `WarpRectilinear` plus
a lens-shading `GainMap`. The warp moves corners by tens of pixels (measured ~70 px on a
24 MP frame), so LibRaw's per-pixel CFA masks describe different pixels and are dropped
rather than re-mapped — carrying them over would put clip retreat on the wrong part of
the image. Consequently this path has no per-pixel CFA evidence: `--tone-core gated` is
refused, clip retreat does not run, and `--highlight-mode` does not apply because Core
Image performs its own highlight handling. Aggregate RAW facts (levels, clipping
percentages, SNR, noise floor, white-balance testimony) are distributions rather than
pixel positions, so they remain valid and still come from LibRaw. The report names the
decoder, its version, and the opcodes that were executed.

After the fixed `1/0.9314` scale compensation the two pipelines agree on the midtones
(measured 0.01 EV apart on an ISO 2500 frame) and differ mainly in camera
interpretation — warmer skin and a different highlight rendering on the Sigma fp
samples. Two behavioural differences follow from the decoders themselves rather than
from taste, and are worth knowing before reading an A/B:

- **`--ev auto` can choose a different exposure on each path.** Apple keeps detail above
  diffuse white, so the reference's highlight growth budget sees more near-white pixels
  and stops the boost earlier. On one ISO 2500 frame the LibRaw path took +0.73 EV and
  the Core Image path +0.44 EV. Compare at a fixed `--ev` when the decoder itself is the
  question.
- **Apple's buffer carries genuine specular headroom** — 2.7 % of pixels above diffuse
  white on one frame, up to 2.06 linear. dngscan reserves quantisation room for it, so
  the compiled white endpoint can rise above its +3.00 EV floor (measured +3.67 EV on
  that frame) and the shoulder rolls those highlights off instead of clipping them.

RAW 9 was also observed smoothing high-frequency energy with noise reduction forced off
(~9 % less on one high-ISO night frame), though a second ISO 2500 capture showed no such
difference — treat it as a per-scene observation, not a fixed property. `--wb daylight`
is rejected until a validated temperature/tint mapping exists.

### White balance

`camera` uses the file's AsShot measurement. `daylight` uses LibRaw's calibrated
daylight multipliers and is useful when a group of images under the same light should
keep a fixed balance.

Sun, overcast, and shade lie roughly on a predictable daylight locus, where the camera
measurement is usually useful. Mixed light, narrow-band LED, fluorescent, and sodium
light are not a simple color-temperature problem. Some changes that look like incorrect
white balance also come from a tone curve redistributing luminance and purity, which is
why WB and the DRT remain separate stages. The AsShot deviation from the daylight
multipliers is also written into the analysis: it is both WB data and evidence about the
light at capture.

I do not treat an adapted eye in front of a display as an absolute white-point meter.
Hunt, Stevens, Abney, and Bezold-Brücke appearance effects can make changes in luminance
and purity look like changes in hue or warmth, while memory colors such as skin, sky,
and foliage are not simple colorimetric targets. When something looks “off,” separating
the illuminant, camera balance, tone, and color geometry is more useful than immediately
turning the temperature control.

### Highlight handling

LibRaw's three choices affect the appearance after reconstruction:

- `clip` cuts at saturation. It is closest to sensor state, but staggered channel
  clipping can leave colored borders.
- `blend` feathers the clipping boundary.
- `reconstruct` estimates missing channels from surviving ones. It can recover
  continuous structure, but its chroma is inferred.
- Its hue often leans toward the surviving channel, so continuity is not color truth.

I generally use `reconstruct` for photographs and `clip` when inspecting the sensor or
the algorithm itself. The saved RAW clipping evidence is unchanged in every case.

## Tone: exposure and curve construction

### Fixed exposure anchor

The pipeline uses scene-linear `0.18` as nominal middle gray. Its exposure baseline is
a fixed camera constant plus manual EV, not an operation that forces every image median
to 18% gray. Constant scaling preserves scene intent: a dark scene remains dark before
AgX, a bright scene remains bright, and content-adaptive exposure does not reorder the
relationship between photographs.

The GUI's **brightness reference**, also available as `--ev auto`, is an explicitly
requested alternate reading. It tries to place the global median at 18% gray while
respecting a budget for newly created highlight clipping. Lights already clipped in the
CFA do not consume that budget; they were emitters in the scene already. Only areas that
still contained information but are pushed into the display ceiling limit the increase.
The global median can still be misled by a background, so this remains a reference and
not the default exposure.

### Scene statistics are not simple min/max

The tone plan separates the reliable body from the highlight tail. Body statistics
exclude CFA-clipped and low-confidence areas and estimate black, pivot, contrast, and
the useful mid-frequency range. The tail only reserves space for the shoulder. Sparse
emitters and large bright surfaces are also different: letting a few lamps define white
EV makes the highlights harsh while the rest of the image remains dark.

The controls in the tone plan therefore have different evidence:

- `black point` and `toe` follow the noise floor, usable shadows, and target display black.
- `white point` and `shoulder` follow the reliable luminance tail, display headroom, and emitter topology.
- `pivot` and `contrast` follow the subject midtones rather than a few extreme pixels.
- `view brightness` raises only the curve interior while preserving true black and the target white endpoint.

### The four GUI tone adjustments

The GUI does not expose the automatic pivot, black EV, or white EV directly. Instead,
it adds four bounded biases to the compiled tone plan. The center **Auto** value is the
analysed result, not another preset. With all four at zero, the original render plan is
used directly and the output is unchanged.

| Control | Move left | Move right | What stays fixed |
| --- | --- | --- | --- |
| **Midtone brightness** | Makes the subject darker and more restrained | Raises the subject and visible shadows | Scene exposure, black point, and white point |
| **Midtone contrast** | Softens midtone separation | Increases separation across the automatic pivot | The pivot position itself |
| **Shadow transition** | Deepens the toe and reaches black sooner | Opens the toe and reveals more shadow separation | Black point; it cannot create low-SNR information |
| **Highlight transition** | Makes the shoulder more direct and highlights more forceful | Softens the shoulder and preserves bright detail earlier | White point and RAW clipping position |

**Midtone brightness** is not exposure compensation. Exposure EV scales the
scene-linear signal, changes where content enters the shoulder, and consumes highlight
headroom. Midtone brightness reshapes only the display-referred curve interior while
true black and target white remain fixed. **Midtone contrast** is not another brightness
control either: it changes slope around the automatic pivot, separating values within
the subject instead of moving the subject as a whole.

In practice, set subject placement with **midtone brightness**, shape it with **midtone
contrast**, then tune the two ends with **shadow transition** and **highlight
transition**. Opening shadows only reveals what the sensor recorded; in a low-SNR scene
it also reveals read and chroma noise. **Highlight fade** is separate from these four
luminance controls and changes only the chroma path near display white.

### The darktable-style C1 curve

The main curve follows darktable AgX's C1 construction. Toe, linear latitude, and
shoulder meet with both value and first derivative continuous. The tone plan supplies
black/white EV, contrast, toe and shoulder powers, and latitude, while the calibrated
EV 0 to 18% anchor remains stable.

Feeding scene min/max directly into a generic sigmoid lets a handful of lamps define
white EV, producing bright, sharp highlights over a dark body. C1 endpoints plus the
body/tail split make “how wide the scene is” and “where its important content should
sit” two different questions.

## Color geometry: what AgX actually changes

A bare per-channel S-curve sends R, G, and B into the toe and shoulder at different
rates, so highly saturated colors change hue with brightness. AgX is not only a
sigmoid; its defining structure is the primary geometry around that curve.

The pre-curve `inset` contracts the working primaries toward the neutral axis and adds
a small rotation. Extreme colors do not hit a single channel ceiling directly and gain
a smoother path to white. The post-curve `outset` restores purity, but is deliberately
not the exact inverse of the inset. The difference between the two, plus optional hue
restoration, is part of AgX's color character. This also addresses the notorious six of
bare per-channel curves, such as pure red moving toward orange-yellow and pure blue
toward cyan as they brighten; the inset rotation carries some Abney-style perceptual hue
compensation as well.

dngscan defaults to darktable's `smooth` primaries. `base`, `punchy`, and `muted` remain
as geometric references from the wider AgX ecosystem; they do not participate in RAW
analysis or change the exposure algorithm.

AgX pays for this behavior through the same structure. The inset removes purity before
the curve, and content largely earns it back through per-channel expansion in the toe.
This is why high-ISO night images can look rich while bright wide-DR daylight images can
look comparatively flat. Blender's common Base-plus-Punchy pairing addresses the same
fact. Chroma is also coupled to where content lands on the curve: the same object can
render at a different purity after a change in framing or exposure. `punch`, `gated`,
and `lum` exist to separate and inspect these effects, not to reject AgX.

### Four compression cores

All four share the same exposure anchor, CFA evidence, and delivery safeguards so they
can be compared at the same EV:

| Core | Underlying difference |
| --- | --- |
| `agx` | Complete inset -> per-channel C1 curve -> hue path -> outset. The default render. |
| `gated` | Computes both AgX-color and luminance-preserving candidates, then mixes them per pixel from RAW clipping, headroom, and noise confidence. |
| `lum` | Applies the same scene-compiled C1 curve to a luminance norm while preserving RGB ratios; no AgX inset/outset. |
| `neutral` | A fixed conventional shoulder without scene-compiled AgX geometry. |

`gated` is not another exposure curve. It first normalizes the AgX candidate to the same
Rec.2020 luminance as the lum candidate, then chooses how much chromatic path to mix.
There is one luminance authority, so confidence-mask boundaries cannot create brightness
seams. It uses CFA information that a mid-pipeline darktable module cannot see: whether
a color change came from valid channels or from an area already clipped and rebuilt.

`lum` deliberately preserves RGB ratios. It retains mid-frequency purity, but bright
saturated colors can look neon because they do not retreat toward white as AgX colors
do. The `y`, `max`, and `power` norms trade colorimetric luminance, loudest-channel
protection, and a compromise between the two.

### RAW clip retreat, punch, and gamut fit

RAW clip retreat only engages where CFA evidence says channel information was lost. It
moves the color toward the neutral axis at the same luminance before the curve. This is
different from AgX's global inset: one is driven by actual sensor clipping, while the
other is the color geometry of the display transform itself.

`punch` (labelled **mid-frequency purity** in the GUI) compensates for the broad loss
of purity caused by the AgX inset in bright, wide-dynamic-range scenes. It works in
Oklab and its automatic strength is gated by
subject brightness, usable DR, and tone-window width. It fades on the neutral axis, in
deep shadows, in highlights, on already vivid colors, and in the skin band. Every
weight multiplies the gain increment, so gain is always >= 1: it only restores purity
and never reverses into local desaturation. Night or high-ISO scenes can gate exactly to
zero and short-circuit the operator so shadow chroma noise is not amplified. The GUI
strength is a multiplier on the analysed value: `1` uses it and `0` disables it. This is
still a global policy tuned on a limited image set, not a sensor measurement itself.

**Highlight fade** is a separate, restrained display-side chroma bias. It does not alter
the luminance shoulder or pretend to reconstruct clipped RAW data. Moving it right sends
colors near display white toward the neutral axis earlier; moving it left retains more
highlight chroma under the protection of the final gamut fit.

Final gamut fitting occurs after tone and looks. It pushes colors that do not fit the
target sRGB/P3 gamut back along Oklab chroma rather than clipping each RGB channel. This
keeps highlight colors retained by AgX or P3 from collapsing into hard primaries at the
last step.

## The prefeed experiment I am keeping

I like the idea of compensating repeatable camera defects from measurements before the
image reaches AgX. If two sensor and filter-stack responses are measured well enough,
the same layer can also approximate some response relationships of another camera,
within the information the original sensor actually recorded.

The included ARRI-like prefeed came from a personal goal: I wanted to see whether the
Sigma fp could move a little toward the skin I like in ARRI footage, with blood warmth
set against a cooler cyan field. My original suspicion involved the ALEV filter stack's
red/near-IR behavior and the different filter and magenta behavior of the fp/IMX410.

The current implementation integrates public camera SSFs, illuminant SPDs, and material
reflectance spectra. It fits constrained 3x3 mappings for skin, foliage, cyan, neutral,
and magenta classes, then limits every mapping with a soft window in the `(R/G, B/G)`
chromaticity plane. The windows move with selected white balance through von Kries
scaling. A neutral-axis constraint prevents it from becoming hidden white balance,
while per-class residual and cross-class leakage enter its confidence.

The ALEV III SSF was digitized from Leonhardt & Brendel's CIC23 paper. ARRI averaged
measurements from five ALEXA bodies because interference patterns in the sensor stack
vary between units. The Sigma fp side currently uses the full-camera Sony A7 III SSF
measured by Weta Digital in AMPAS `rawtoaces-data`; it shares the IMX410 sensor but is
not the same complete filter stack as the fp. The camera-to-Rec.2020 profile is fitted
on AMPAS's 190 training reflectances. The calibration files keep these sources and
substitutions explicit rather than treating “same CMOS” as “same camera.”

There is a firm physical limit. If two materials have already become metameric on the
fp, a per-pixel matrix cannot recreate the distinction they would have shown on ALEV.
Sensor stacks also vary between individual bodies, so serious calibration should target
the exact camera in hand. I do not have controlled illuminants, targets, or spectral
equipment, and the present result is closer to a restrained geometric color mapping
than the ARRI skin response I originally wanted. Sources, assumptions, CSV data, and
fit reports are in [`dngscan_assets/spectral/`](dngscan_assets/spectral/).

## Looks and LUTs

The repository includes one look I wrote, `optic_warm_cyan`, because I actually use it.
It is an Oklab chroma field after AgX, not a vendor LUT and not a camera prefeed.

The code also keeps optional `.cube` slots for Kodak 2383, RED IPP2, and Sony
LC-709TypeA. Legally obtained LUTs can be placed in the corresponding paths under
`dngscan_assets/vendor_luts/`, where the GUI discovers them automatically. The files
themselves are not distributed here. Prefeed, AgX geometry, and a display-side LUT sit
at three different points in the pipeline even when some of their visual effects look
similar.

## Output

SDR output is an 8-bit JPEG with deterministic TPDF dither, quality 100 and 4:4:4 by
default. Dither is applied before quantization to reduce banding in smooth gradients; it
does not alter the tone plan. 4:2:2 and 4:2:0 are available when smaller files matter at
the cost of chroma resolution. Display P3 embeds an ICC profile and export stops if that
profile is unavailable rather than writing untagged wide-gamut values.

An ISO 21496-1 gain-map HDR JPEG path also exists. It uses a P3 SDR base as the
compatibility image and attaches a luminance gain map. `--output-format ultrahdr`
selects it and `--hdr-headroom` sets its gain ceiling in EV. This path remains
experimental.

## Quick start

Python 3.10 or newer is required.

```bash
git clone https://github.com/Gen-416/dngscan.git
cd dngscan
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m dngscan.gui
```

Open the localhost address printed in the terminal. The GUI runs entirely on the local
machine and uploads nothing. The first open decodes and analyses the file and builds a
1280px proxy; later previews reuse memory and disk caches, while full export always
returns to the full-resolution scene buffer. Full export runs in a short-lived worker
process so its large arrays leave with that process instead of remaining in the GUI
server.

On macOS the cache defaults to `~/Library/Caches/dngscan/preview-v1`, is limited to
768 MB, and evicts older entries automatically.

I normally start at EV 0 with `AgX`, `smooth` primaries, camera WB, and highlight
reconstruction, then adjust from the photograph itself. Quality 100 and 4:4:4 are the
default output settings.

### CLI

```bash
# Default AgX JPEG
python -m dngscan photo.dng --jpeg photo.jpg

# Highlight reconstruction and Display P3
python -m dngscan photo.dng --jpeg photo_p3.jpg \
  --highlight-mode reconstruct --output-gamut p3

# RAW analysis dashboard and CSV
python -m dngscan photo.dng --jpeg photo.jpg --scan --csv photo.csv

# Compare another core at the same EV
python -m dngscan photo.dng --jpeg gated.jpg --tone-core gated

# Optional Core Image scene buffer (macOS; evidence stays on LibRaw)
python -m dngscan photo.dng --jpeg ci.jpg --decoder coreimage
python -m dngscan photo.dng --jpeg ci8.jpg --decoder coreimage --coreimage-version 8

# Deliberately use the brightness reference
python -m dngscan photo.dng --jpeg reference.jpg --ev auto
```

Run `python -m dngscan --help` for the complete list.

### Optional C++ acceleration

NumPy is the reference implementation and works without a native build. The pybind11
C++ kernel accelerates only the normal AgX hot path: formation, C1 curve, hue restoration,
and punch. RAW analysis, tone-plan compilation, and fallback policy remain in Python.

```bash
pip install pybind11 cmake
tools/build_native.sh
```

`DNGSCAN_FAST=auto` is the default; `0` forces NumPy; `1` requires the native kernel and
raises if it cannot be used. The kernel releases the GIL and works with the existing
chunked export path; the AgX hot stage measures at roughly 2x. Import checks its ABI and
runs a self-test. Current real-scene linear differences are around `2e-6`, with final
8-bit differences within one dither step. Its job is only to reduce export time, not to
change the imaging decisions.

## RAW reports

`--scan` writes a six-panel report with SNR versus stops, separate R/G/B RAW
distributions, exposure and gamut pressure, spatial exposure zones, clipped-channel
maps, and per-channel full-well, clip, black-level, and WB readouts. RAW distributions
use stops from clipping on the horizontal axis and peak-normalized linear density on the
vertical axis. Density curves may be lightly smoothed for display; clip percentages,
medians, percentiles, and all other statistics always come from the unsmoothed samples.
SNR and dynamic range are single-frame estimates, not full photon-transfer measurements;
container bit depth is not the same as usable dynamic range.

## License and sources

dngscan is GPL-3.0-or-later because its AgX curve and primary-geometry implementation
derives from darktable's GPL `agx` code. See [NOTICE.md](NOTICE.md) for code, spectral
data, and optional dependency attribution.

AgX was created by Troy Sobotka and developed through the Blender/EaryChow ecosystem;
this project mainly follows darktable's photographic implementation. ARRI, ALEXA, ALEV,
Sony, Sigma, RED, Kodak, darktable, Blender, and other names belong to their respective
owners and are used here only to describe sources, compatibility, and comparisons in
the pipeline.
