# FlipFormat

A small, offline image converter, resizer and cropper for Windows.
Everything runs locally; nothing is uploaded.

Not affiliated with any other converter app. Original code.

## Features

- **Convert** between JPG, PNG, WEBP, HEIC, TIFF, BMP, AVIF and PDF.
- **Shift-drag menu:** select images in Explorer or on the Desktop, hold
  Shift, drag, and drop onto a format (or Crop / White BG) in the menu.
- **Resize** by percent, longest side, width or height.
- **Max file size (KB):** lowers quality first, then dimensions if needed.
- **Crop** freely or with presets (US, Schengen/EU, UK, Canada, India, China,
  Japan, Australia photo sizes and common ratios), with **rotate, flip and
  straighten**.
- **White background:** offline AI cut-out with edge clean-up (stray blobs
  and holes fixed, edges snapped to the photo, old background colour removed
  from hair edges) onto exact white, light gray, light blue or transparent,
  with edge shrink/grow and softness controls. Works up to 2400 px on the
  long side.
- **Remove metadata:** strips GPS location, camera details and other hidden
  data (colours are converted to sRGB first so nothing looks dull).
- Originals are never modified; converted copies are saved beside them.

> Photo preset sizes are the commonly published ones. Rules differ by
> embassy and change over time, so always check the official requirements.

## Run

Requires Python 3.10+.

```
pip install -r requirements.txt
python flipformat.py
```

Keep the window open (minimising is fine) to use the Shift-drag menu.
Select the file(s) first, then Shift-drag them.

## Install (no Python needed)

Download `FlipFormat-Setup.exe` from the Releases page and run it. Windows
may show a SmartScreen warning because the installer is not code-signed:
choose "More info" then "Run anyway". The installer can optionally start the
app when you sign in, which keeps the Shift-drag menu ready.

## Build it yourself

- Locally: run `build.bat` (needs Python), then compile `installer.iss` with
  Inno Setup 6.
- On GitHub: push a tag such as `v1.0.0`; the workflow in
  `.github/workflows/build.yml` builds the installer and attaches it to a
  release.

## Files

- `flipformat.py`: window, Crop tab and Shift-drag menu
- `flipformat_core.py`: conversion, resizing, size fitting, metadata, crop maths
- `flipformat_bg.py`: background removal
- `tools/make_icon.py`, `assets/`: the app icon
- `installer.iss`, `build.bat`, `.github/workflows/build.yml`: packaging

## Credits

The background remover uses the U2-Net human segmentation model
(Apache-2.0, by Xuebin Qin et al.), run locally with ONNX Runtime. The model
is not stored in this repo; the build downloads and verifies it. When running
from source, the app downloads it once on first use (about 176 MB).
