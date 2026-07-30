# dngscan User Guide

This is the manual without the math. It answers three questions: which cameras this tool
can handle, what every number on screen means, and what to pick when exporting. For the
pipeline internals and technical detail, see the [README](../README.md).
[中文版使用说明在这里](USER_GUIDE.zh-CN.md).

dngscan does one thing: it turns RAW photos into faithful JPEGs — ordinary JPEGs, or HDR
photos that genuinely light up on capable screens (iPhone / Mac / recent Android photo
apps all display them natively).

---

## 1. Supported cameras

**The default decoder (LibRaw)** covers the vast majority of RAW formats on the market,
including but not limited to:

| Brand | Formats |
|---|---|
| Canon | CR2 / CR3 |
| Nikon | NEF / NRW |
| Sony | ARW |
| Fujifilm | RAF (including X-Trans sensors) |
| Panasonic / OM System / Leica / Pentax | RW2 / ORF / DNG / PEF and others |
| Sigma, iPhone (ProRAW), and anything that writes DNG | DNG |

It works out of the box — no per-camera plugins. Development and calibration happened
primarily on Sigma fp DNGs and iPhone ProRAW, so those are the most thoroughly
validated; other mainstream cameras decode and convert normally, and sample files for
anything misbehaving are welcome.

**The Apple RAW decoder (optional — set "解码器 / Decoder" to Apple RAW)** uses the RAW
engine built into macOS. Its coverage follows Apple's official camera RAW compatibility
list — mainstream Canon, Nikon, Sony, Fujifilm, Panasonic and Sigma bodies plus iPhone
are on it, but **the newest decode model (RAW 9) is only open to the more recent bodies
among them**. You never need to look this up yourself: after you pick a file the tool
probes what that exact file supports, and if RAW 9 is unavailable it tells you plainly
and lets you choose an older version or fall back to LibRaw — it never switches
algorithms silently.

The two decoders have slightly different color personalities (Apple's denoising and
highlight reconstruction feel more "camera-manufacturer"); both produce SDR and HDR
normally. When in doubt, use the default LibRaw.

---

## 2. Basic workflow

1. **Pick a file** — the RAW file selector at the top;
2. **Read the Detected Parameters card** — the tool has already analyzed the photo;
   look here before touching anything (next section explains each number);
3. **Adjust exposure and tone** — if needed;
4. **Choose the output** — ordinary JPEG or HDR, for sharing or for archiving
   (section 5);
5. **Update the preview to confirm, then export.**

---

## 3. What the Detected Parameters mean

This card shows the tool's measurements of your photo — the same analysis the final
render will use.

**First, EV**: EV means "stops", photography's exposure unit. +1 EV = twice as bright,
−1 EV = half. Every EV in this tool is anchored at **middle gray** (the 18% gray of a
correct exposure — roughly the midtone brightness of a properly exposed face): +3 EV is
three stops brighter than middle gray, −5 EV five stops darker.

| Field | Meaning | Why it matters |
|---|---|---|
| **RAW clipping** | Share of sensor pixels that blew out ("dead white") | High values mean burned highlights; the colors there are guesses, and the tool automatically trusts them less |
| **Reliable highlight tail** | The brightest content that was **genuinely measured without clipping** (clipped pixels excluded — they don't count) | This is the sole budget for HDR brightness: if a neon sign measured +6 EV, HDR can honestly light it to +6 |
| **Earned HDR headroom** | The part of the reliable tail above "paper white" (a normal white on screen) | Exactly how many stops brighter than an ordinary photo this HDR can go; 0 means there is nothing worth HDR in this shot |
| **Subject median** | Roughly which stop the scene's body sits at | Strongly negative = a dark scene (night); near 0 = standard exposure |
| **Scene type** | Normal / sparse emitters | "Sparse emitters" = mostly-dark frames with small very-bright sources (night lights, stages); the tool automatically switches to a highlight policy suited to them |
| **Compiled curve** | The dark-to-bright range and contrast this render actually adopted | Reference values, so you know what the tool decided for this image |

**A typical read of this card**: earned headroom +1.5 EV → worth exporting HDR; RAW
clipping 8% → the sky may be burned, try the "reconstruct" highlight mode; subject
median −3 EV → this is a night scene, don't force the exposure up.

---

## 3.5 White balance: As Shot vs fixed Kelvin

Besides "As Shot", the white balance selector offers a set of **fixed color
temperatures**. These are not for balancing by eye — eyeballing neutrality on a screen
never beats the camera's metering (your eyes chromatically adapt while you look). They
are **declared standard references**: the values come from industry calibrations, and
the multipliers are solved precisely from the photo file's own color calibration, with
no eye in the loop.

| Option | What it is | When |
|---|---|---|
| **As Shot** | The balance the camera metered at capture | Default; everyday output |
| **6500K · D65** | The standard white point of sRGB/Rec.709 displays | Aligning with display-industry standards |
| **5500K · photographic daylight** | The calibration temperature of daylight-balanced film | **The correct starting point for film simulation**: one fixed 5500K for the whole roll, letting the actual light's warmth or coolness pass through — tungsten light *should* look orange, exactly as real film behaves |
| **3400K · Type A** | Type A tungsten film (photoflood lamps) | Tungsten film simulation |
| **3200K · Type B** | Type B tungsten film (3200K studio tungsten/halogen) | Same, the more common tungsten calibration |
| **9300K · Japanese broadcast white** | The traditional white point of Japanese television (cool blue) | The "old Japanese TV" cool look — for fun |

Fixed Kelvin works on both decoders. A visible color cast after choosing one is
**expected behavior** — it is precisely how film sees the world, not a malfunction.

## 3.7 Film observation positions (Portra 400 / Superia X-TRA 400)

The **film observation position** selector (imaging card) sets three independent
declarations at once: white balance locked to 5500K (film's calibration temperature),
the spectral prefeed (how that stock's layers separated colour, from manufacturer
datasheets), and the curve preset (that stock plus its paired paper, as a fitted
coordinate). The three underlying controls visibly update when you pick one — **nothing
is baked**; adjust any layer individually at any time.

- **Kodak Portra 400** — the Western memory portrait negative, famous for its skin
  separation;
- **Fujifilm Superia X-TRA 400** — the Eastern memory everyday negative (the classic
  recipe discontinued in 2024).

It adds no grain and no vignette — it changes *how the camera saw the world*: how
colours separate, how highlights roll off, where shadows rest against the paper's
deepest black. **HDR keeps working**: a film body plus genuinely measured highlight
headroom, a combination no other film tool offers.

**Lens filters** (RAW decode card) are the companion control: Wratten conversion
glass simulated from Kodak's published parameters (85B daylight-to-tungsten, 80A the
reverse, and others), for recreating historical workflows like "tungsten film + 85B in
daylight". There is no strength slider — glass has no half-installed state.

## 4. The other EVs in the interface

- **Exposure EV** (the slider): brightens/darkens everything, +1 = one stop up. 0 keeps
  the brightness relationships from capture.
- **Brightness reference** (button): the tool measures the subject and aligns it to a
  standard exposure while limiting highlight overflow. "Expose this for me, once" — the
  result is written back to the slider and can be adjusted further.
- **HDR headroom ceiling** (output card): how many stops above paper white your screen
  or use case allows at most. It is a **ceiling, not a target** — actual usage is
  decided by the earned headroom, whichever is smaller. The default +3.0 (roughly an
  800-nit screen) rarely needs changing.

---

## 5. The compression cores, and which to pick

RAW records a far wider brightness range than any screen can show; the "compression
core" is how the former is fitted into the latter. It decides the overall look —
especially how highlights behave in color.

| Option | One line | When |
|---|---|---|
| **AgX · default** | Film-style highlight handling: bright areas fade naturally toward white instead of staying garishly saturated | **Use this when unsure** — 95% of the time |
| **RAW-gated · fidelity** | Same brightness as AgX, but the color processing strength is decided per pixel by RAW evidence: colors the sensor genuinely measured are preserved harder | When you want colors more "faithful to the sensor"; LibRaw decoding only |
| **Scene C1 · luminance only** | Compresses brightness only, never touches color ratios | A control group: switch here to see what AgX's color handling is actually doing |
| **Fixed curve · diagnostic** | A fixed curve that ignores the scene | For troubleshooting, not for daily use |

The last two are comparison/diagnostic tools, not finishing tools.

---

## 6. Output: formats and delivery profiles

**Formats**:
- **SDR JPEG** — an ordinary photo, viewable everywhere;
- **HDR gain-map · JPEG** — the recommended HDR format. The file carries both a normal
  rendition and the highlight-boost information; on capable screens (iPhone, Mac,
  Android 15+) highlights genuinely light up, everywhere else it gracefully shows the
  normal version — nothing breaks;
- **HDR gain-map · HEIC** — the same content in a HEIC container. **Measured on this
  machine it is neither smaller nor better** — choose it only when a downstream
  requires HEIC.

**Delivery profiles**:
- **Archive · fidelity** — maximum quality (~60 MB per frame), for high-quality
  keeping;
- **Share · streaming** — for publishing (~11–27 MB per frame): visually
  near-identical, HDR information fully preserved, and **even the largest frames stay
  inside WeChat's 25 MB original-image limit**.

**WeChat notes**: in chats you must tick **原图 (original image)** for HDR to survive
to the recipient (iPhone WeChat shows full HDR when viewing the original); **Moments
always recompresses to an ordinary photo** — that is WeChat's behavior and no tool can
bypass it. For deliveries that matter, sending as a "file" is the safest path.

**After exporting, read the Delivery Report** — the collapsible panel above the
preview. It states what actually happened: file size, how many stops of HDR were really
used, how small the compression error measured. Every exported file passed an automatic
verification; files that fail it are never kept.

---

## 7. FAQ

**HDR export fails with "the reliable highlight tail supports no HDR headroom"?**
The photo has no genuinely measured highlight content (its earned headroom is 0). This
is not a malfunction — an HDR of a photo with no bright content would look identical
anyway. Export SDR instead.

**Chose Apple RAW and got told the file only supports RAW 8?**
Your camera/file is outside RAW 9's coverage. Continue with the older version as
prompted, or switch back to LibRaw; both produce normal output.

**Will the preview differ from the export?**
The preview uses a low-resolution proxy for speed; framing and tone match. Judge
sharpness and noise from the full-size export. Numbers labeled "full-resolution truth"
in the export status are the final ones.

**Where can I see the HDR effect?**
Open the exported file in macOS Preview/Finder, iPhone Photos, an Android 15+ gallery,
or Chrome. On ordinary monitors or older systems it is simply a normal JPEG.

**Why are quality and chroma subsampling sometimes locked?**
The archive profile pins maximum quality by definition; in HDR formats the chroma
subsampling is decided by the system encoder from the quality setting — the tool shows
the truth rather than pretending it is adjustable.
