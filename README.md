# FlipFormat

A small, offline image converter, resizer and cropper for Windows.
Everything runs locally; nothing is uploaded.

Not affiliated with any other converter app. Original code.

## Features

- **Convert** between JPG, PNG, WEBP, HEIC, TIFF, BMP, AVIF and PDF.
- **Shift-drag menu:** select images in Explorer or on the Desktop, hold
  Shift, drag, and drop onto a format in the menu that appears.
- **Resize** by percent, longest side, width or height.
- **Max file size (KB):** lowers quality first, then dimensions if needed.
- **Crop** freely or with presets (US, Schengen/EU, UK, Canada, India, China,
  Japan, Australia photo sizes and common ratios).
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
- `flipformat_core.py`: conversion, resizing, size fitting, crop maths
- `installer.iss`, `build.bat`, `.github/workflows/build.yml`: packaging
