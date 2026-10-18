"""FlipFormat core: conversion, resizing, file-size fitting and crop helpers.

No GUI code lives here, so it can be tested or reused on its own.
"""
import io
from pathlib import Path

from PIL import Image, ImageOps

# Optional codecs: HEIC and AVIF come from these plugins.
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass
try:
    import pillow_avif  # noqa: F401
except ImportError:
    pass

RESAMPLE = getattr(Image, "Resampling", Image).LANCZOS

# name -> (extension, Pillow format, save options, needs flat RGB)
FORMATS = {
    "JPG":  (".jpg",  "JPEG", {"quality": 92}, True),
    "PNG":  (".png",  "PNG",  {}, False),
    "WEBP": (".webp", "WEBP", {"quality": 90}, False),
    "HEIC": (".heic", "HEIF", {"quality": 90}, False),
    "TIFF": (".tiff", "TIFF", {"compression": "tiff_lzw"}, False),
    "BMP":  (".bmp",  "BMP",  {}, True),
    "AVIF": (".avif", "AVIF", {"quality": 85}, False),
    "PDF":  (".pdf",  "PDF",  {"resolution": 100.0}, True),
}
LOSSY = {"JPG", "WEBP", "HEIC", "AVIF", "PDF"}  # "quality" shrinks the file

INPUT_EXTS = {
    ".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif",
    ".tif", ".tiff", ".bmp", ".avif", ".gif",
}

RESIZE_MODES = [
    "Original size", "Scale %", "Longest side (px)", "Width (px)", "Height (px)",
]


def _mm(millimetres, dpi=300):
    return round(millimetres / 25.4 * dpi)


# Photo sizes are the commonly published ones, but rules change and differ by
# embassy: always check the official requirements for your application.
# "px" = exact output size, "ratio" = locked aspect only, "max_kb" = typical
# upload limit that gets pre-filled in the size box.
CROP_PRESETS = [
    {"name": "Free crop"},
    {"name": "US Visa / Passport (2x2 in)", "px": (600, 600), "max_kb": 240},
    {"name": "Schengen / EU Visa (35x45 mm)", "px": (_mm(35), _mm(45))},
    {"name": "UK Passport (35x45 mm)", "px": (700, 900)},
    {"name": "Canada Passport (50x70 mm)", "px": (_mm(50), _mm(70))},
    {"name": "India Passport / Visa (2x2 in)", "px": (600, 600)},
    {"name": "China Visa (33x48 mm)", "px": (_mm(33), _mm(48))},
    {"name": "Japan Visa (45x45 mm)", "px": (_mm(45), _mm(45))},
    {"name": "Australia Passport (35x45 mm)", "px": (_mm(35), _mm(45))},
    {"name": "Square 1:1", "ratio": (1, 1)},
    {"name": "Portrait 4:5", "ratio": (4, 5)},
    {"name": "Photo 3:2", "ratio": (3, 2)},
    {"name": "Photo 4:3", "ratio": (4, 3)},
    {"name": "Wide 16:9", "ratio": (16, 9)},
]


def preset_ratio(preset):
    """Width / height the crop box must keep, or None for free crop."""
    if "px" in preset:
        w, h = preset["px"]
        return w / h
    if "ratio" in preset:
        w, h = preset["ratio"]
        return w / h
    return None


def available_formats():
    """Formats this install can actually write (HEIC/AVIF need plugins)."""
    Image.init()
    return [n for n, (_, pil_fmt, _, _) in FORMATS.items() if pil_fmt in Image.SAVE]


def load_image(path):
    """Open an image fully into memory, honouring phone-camera rotation."""
    with Image.open(path) as im:
        im.load()
        return ImageOps.exif_transpose(im)


def to_flat_rgb(im):
    """Flatten transparency onto white, for formats without alpha."""
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        background = Image.new("RGB", im.size, "white")
        background.paste(im, mask=im.split()[-1])
        return background
    return im.convert("RGB")


def unique_path(path):
    """photo.jpg -> photo (1).jpg -> photo (2).jpg ... never overwrite."""
    path = Path(path)
    if not path.exists():
        return path
    i = 1
    while True:
        candidate = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not candidate.exists():
            return candidate
        i += 1


def resize_image(im, mode, value):
    """Resize by percent or by one side, keeping the aspect ratio."""
    if mode == "Original size" or not value:
        return im
    w, h = im.size
    if mode == "Scale %":
        factor = value / 100
    elif mode == "Longest side (px)":
        factor = value / max(w, h)
    elif mode == "Width (px)":
        factor = value / w
    elif mode == "Height (px)":
        factor = value / h
    else:
        return im
    size = (max(1, round(w * factor)), max(1, round(h * factor)))
    return im.resize(size, RESAMPLE)


def encode(im, fmt, quality=None):
    """Encode to bytes in memory (needed to measure the file size)."""
    _, pil_fmt, options, flat = FORMATS[fmt]
    if flat:
        im = to_flat_rgb(im)
    options = dict(options)
    if quality is not None and fmt in LOSSY:
        options["quality"] = quality
    buf = io.BytesIO()
    im.save(buf, pil_fmt, **options)
    return buf.getvalue()


def fit_to_size(im, fmt, max_kb, allow_downscale=True):
    """Encode so the file is at most max_kb.

    Returns (bytes, (width, height), note). Lossy formats lower the quality
    first. If that is not enough (or the format is lossless) the image is
    scaled down, unless allow_downscale is False (exact-pixel presets).
    """
    limit = int(max_kb * 1024)
    data = encode(im, fmt)
    if len(data) <= limit:
        return data, im.size, None

    lossy = fmt in LOSSY
    if lossy:
        lo, hi, best = 15, 95, None
        while lo <= hi:
            mid = (lo + hi) // 2
            candidate = encode(im, fmt, mid)
            if len(candidate) <= limit:
                best, lo = candidate, mid + 1
            else:
                hi = mid - 1
        if best is not None:
            return best, im.size, None

    if not allow_downscale:
        smallest = encode(im, fmt, 15) if lossy else data
        return smallest, im.size, (
            f"could not reach {max_kb} KB without changing the pixel size")

    quality = 80 if lossy else None
    candidate, small = data, im
    for step in range(1, 31):
        scale = 0.9 ** step
        small = im.resize(
            (max(1, round(im.width * scale)), max(1, round(im.height * scale))),
            RESAMPLE)
        candidate = encode(small, fmt, quality)
        if len(candidate) <= limit:
            return candidate, small.size, (
                f"scaled to {small.width}x{small.height} to fit")
    return candidate, small.size, f"could not reach {max_kb} KB"


def save_image(im, src, fmt, max_kb=0, suffix="", allow_downscale=True):
    """Write im beside src in the chosen format. Returns (path, size, note)."""
    src = Path(src)
    if max_kb:
        data, size, note = fit_to_size(im, fmt, max_kb, allow_downscale)
    else:
        data, size, note = encode(im, fmt), im.size, None
    out = unique_path(src.with_name(src.stem + suffix + FORMATS[fmt][0]))
    out.write_bytes(data)
    return out, size, note


def convert(src, fmt, resize_mode="Original size", resize_value=0, max_kb=0):
    """Convert one file. The original is never modified."""
    im = load_image(src)
    im = resize_image(im, resize_mode, resize_value)
    return save_image(im, src, fmt, max_kb)


# ---------------------------------------------------------------- cropping

def centered_box(width, height, ratio, fill=0.9):
    """Largest box (x0, y0, x1, y1) with the given ratio, centred in the image."""
    w, h = width * fill, height * fill
    if ratio:
        if w / h > ratio:
            w = h * ratio
        else:
            h = w / ratio
    return ((width - w) / 2, (height - h) / 2,
            (width + w) / 2, (height + h) / 2)


def anchored_box(ax, ay, mx, my, width, height, ratio, min_size=8):
    """Box with one corner fixed at (ax, ay) and the opposite one following
    the mouse (mx, my), clamped to the image and to the ratio if locked."""
    sx = 1 if mx >= ax else -1
    sy = 1 if my >= ay else -1
    w, h = abs(mx - ax), abs(my - ay)
    max_w = (width - ax) if sx > 0 else ax
    max_h = (height - ay) if sy > 0 else ay
    if ratio:
        w = max(w, h * ratio)
        w = min(w, max_w, max_h * ratio)
        w = max(w, min_size)
        h = w / ratio
    else:
        w = max(min(w, max_w), min_size)
        h = max(min(h, max_h), min_size)
    bx, by = ax + sx * w, ay + sy * h
    bx, by = min(max(bx, 0), width), min(max(by, 0), height)
    return (min(ax, bx), min(ay, by), max(ax, bx), max(ay, by))


def crop_and_resize(im, box, px=None):
    """Crop to box (image coordinates) and optionally resize to exact px."""
    x0, y0, x1, y1 = (round(v) for v in box)
    x1, y1 = max(x1, x0 + 1), max(y1, y0 + 1)
    cropped = im.crop((x0, y0, x1, y1))
    if px:
        cropped = cropped.resize(px, RESAMPLE)
    return cropped
