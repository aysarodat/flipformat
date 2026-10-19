"""FlipFormat background remover.

Everything runs offline with a small AI model (U2-Net, Apache-2.0, human
segmentation variant). The raw AI mask is only the starting point. Passport
and visa photos need clean edges, so the pipeline also:

  1. averages the model's prediction with a mirrored pass (steadier mask),
  2. drops stray blobs and fills holes (e.g. a white shirt on a white wall),
  3. snaps the mask to real image edges at full resolution (guided filter),
  4. removes the old background colour from hair/shoulder edges
     (foreground colour estimation, "blur fusion"),
  5. composites onto exact (255, 255, 255) white, or another colour.

No GUI code lives here.
"""
import hashlib
import os
import sys
import urllib.request
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

RESAMPLE = getattr(Image, "Resampling", Image).LANCZOS
BILINEAR = getattr(Image, "Resampling", Image).BILINEAR

MODEL_NAME = "u2net_human_seg.onnx"
MODEL_URL = ("https://github.com/danielgatis/rembg/releases/download/"
             "v0.0.0/u2net_human_seg.onnx")
MODEL_SHA256 = "01eb6a29a5c4d8edb30b56adad9bb3a2a0535338e480724a213e0acfd2d1c73c"
MODEL_SIZE = 175997641
MODEL_SIDE = 320
MAX_SIDE = 2400  # working resolution cap (keeps memory and time reasonable)

BACKGROUNDS = {
    "White": (255, 255, 255),
    "Light gray": (235, 235, 235),
    "Light blue": (190, 215, 235),
    "Transparent": None,
}


# ------------------------------------------------------------------ model

def app_dir():
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def user_model_dir():
    base = os.environ.get("LOCALAPPDATA") or (Path.home() / ".local" / "share")
    return Path(base) / "FlipFormat" / "models"


def find_model():
    """Bundled copy (installer) first, then the one-time download folder."""
    for folder in (app_dir() / "models", user_model_dir()):
        path = folder / MODEL_NAME
        if path.exists() and path.stat().st_size == MODEL_SIZE:
            return path
    return None


def download_model(progress=None):
    """One-time download for people running from source. Verified by SHA-256."""
    folder = user_model_dir()
    folder.mkdir(parents=True, exist_ok=True)
    dest, part = folder / MODEL_NAME, folder / (MODEL_NAME + ".part")
    digest = hashlib.sha256()
    done = 0
    with urllib.request.urlopen(MODEL_URL, timeout=30) as resp, open(part, "wb") as out:
        total = int(resp.headers.get("Content-Length") or MODEL_SIZE)
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
            digest.update(chunk)
            done += len(chunk)
            if progress:
                progress(done / total)
    if digest.hexdigest() != MODEL_SHA256:
        part.unlink(missing_ok=True)
        raise RuntimeError("The model download was corrupted. Please try again.")
    part.replace(dest)
    return dest


class Matter:
    """Runs the segmentation model and returns a soft low-resolution mask."""

    MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

    def __init__(self, model_path):
        import onnxruntime as ort
        options = ort.SessionOptions()
        options.log_severity_level = 3
        self.session = ort.InferenceSession(
            str(model_path), options, providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name

    def _run(self, rgb):
        a = np.asarray(rgb, dtype=np.float32)
        a = a / max(float(a.max()), 1e-6)
        a = (a - self.MEAN) / self.STD
        x = np.ascontiguousarray(a.transpose(2, 0, 1)[None], dtype=np.float32)
        out = self.session.run(None, {self.input_name: x})[0][0, 0]
        lo, hi = float(out.min()), float(out.max())
        return (out - lo) / (hi - lo + 1e-8)

    def predict(self, im):
        small = im.resize((MODEL_SIDE, MODEL_SIDE), RESAMPLE)
        flipped = small.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        first = self._run(small)
        second = self._run(flipped)[:, ::-1]
        return (first + second) / 2


# ---------------------------------------------------------- image helpers

def _flat_rgb(im):
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        base = Image.new("RGB", im.size, "white")
        base.paste(im, mask=im.split()[-1])
        return base
    return im.convert("RGB")


def _blur_axis(x, r, axis):
    pads = [(0, 0)] * x.ndim
    pads[axis] = (r, r)
    x = np.pad(x, pads, mode="edge")
    c = np.cumsum(x, axis=axis, dtype=np.float64)
    zero_shape = list(c.shape)
    zero_shape[axis] = 1
    c = np.concatenate([np.zeros(zero_shape), c], axis=axis)
    k = 2 * r + 1
    hi = [slice(None)] * x.ndim
    lo = [slice(None)] * x.ndim
    hi[axis], lo[axis] = slice(k, None), slice(0, -k)
    return ((c[tuple(hi)] - c[tuple(lo)]) / k).astype(np.float32)


def box_blur(a, r):
    """Fast box blur (window 2r+1) for 2-D or H x W x C float arrays."""
    if r <= 0:
        return a
    if a.ndim == 3:
        return np.dstack([box_blur(a[..., i], r) for i in range(a.shape[2])])
    return _blur_axis(_blur_axis(a, r, 0), r, 1)


def _resize_float(arr, size):
    """Resize a float32 H x W array to size=(w, h)."""
    return np.asarray(Image.fromarray(arr.astype(np.float32), "F")
                      .resize(size, BILINEAR), dtype=np.float32)


# ------------------------------------------------------------ mask cleanup

def cleanup_region(alpha_lr, threshold=0.5, keep_ratio=0.12):
    """Work out which parts of the mask are really the subject.

    Returns (kept, holes): kept = the subject (stray blobs removed),
    holes = enclosed areas that must be solid (e.g. a white shirt the model
    mistook for wall).
    """
    mask = (alpha_lr > threshold).tolist()
    h, w = len(mask), len(mask[0])
    labels = [[0] * w for _ in range(h)]
    sizes = [0]
    for y in range(h):
        for x in range(w):
            if mask[y][x] and not labels[y][x]:
                n = len(sizes)
                labels[y][x] = n
                queue, count = deque([(y, x)]), 0
                while queue:
                    cy, cx = queue.popleft()
                    count += 1
                    for ny, nx in ((cy + 1, cx), (cy - 1, cx), (cy, cx + 1), (cy, cx - 1)):
                        if 0 <= ny < h and 0 <= nx < w and mask[ny][nx] and not labels[ny][nx]:
                            labels[ny][nx] = n
                            queue.append((ny, nx))
                sizes.append(count)
    if len(sizes) == 1:
        raise RuntimeError("No person was found in this photo.")
    biggest = max(sizes[1:])
    keep = {i for i, s in enumerate(sizes) if i and s >= keep_ratio * biggest}
    kept = np.array([[labels[y][x] in keep for x in range(w)] for y in range(h)])

    # Background = empty pixels connected to the border; the rest are holes.
    outside = [[False] * w for _ in range(h)]
    queue = deque()
    for y in range(h):
        for x in range(w):
            if (y in (0, h - 1) or x in (0, w - 1)) and not kept[y][x]:
                outside[y][x] = True
                queue.append((y, x))
    while queue:
        cy, cx = queue.popleft()
        for ny, nx in ((cy + 1, cx), (cy - 1, cx), (cy, cx + 1), (cy, cx - 1)):
            if 0 <= ny < h and 0 <= nx < w and not outside[ny][nx] and not kept[ny][nx]:
                outside[ny][nx] = True
                queue.append((ny, nx))
    holes = ~kept & ~np.array(outside)
    return kept, holes


# --------------------------------------------------------------- refinement

def guided_filter(guide, p, r, eps):
    """Edge-aware smoothing of p that follows the edges in guide."""
    mean_i, mean_p = box_blur(guide, r), box_blur(p, r)
    cov = box_blur(guide * p, r) - mean_i * mean_p
    var = box_blur(guide * guide, r) - mean_i * mean_i
    a = cov / (var + eps)
    b = mean_p - a * mean_i
    return box_blur(a, r) * guide + box_blur(b, r)


def _fb_fusion(img, fg, bg, a3, a2, r):
    blurred_a = box_blur(a2, r)[..., None]
    blur_f = box_blur(fg * a3, r) / (blurred_a + 1e-5)
    blur_b = box_blur(bg * (1 - a3), r) / ((1 - blurred_a) + 1e-5)
    out = blur_f + a3 * (img - a3 * blur_f - (1 - a3) * blur_b)
    return np.clip(out, 0, 1), blur_b


def estimate_foreground(img, alpha):
    """Replace edge pixels contaminated by the old background colour with the
    colour the subject really has there (no coloured halo on white)."""
    long_side = max(alpha.shape)
    a3 = alpha[..., None]
    fg, blur_b = _fb_fusion(img, img, img, a3, alpha, max(7, int(long_side * 0.045)))
    fg, _ = _fb_fusion(img, fg, blur_b, a3, alpha, max(2, int(long_side * 0.003)))
    return fg


def adjust_alpha(alpha, edge=0, soft=1, long_side=None):
    """User edge controls. edge < 0 shrinks the cut-out, > 0 grows it;
    soft feathers the edge. One unit is 0.05% of the long side."""
    long_side = long_side or max(alpha.shape)
    unit = long_side / 2000
    a8 = Image.fromarray(np.rint(alpha * 255).astype(np.uint8), "L")
    steps = int(round(abs(edge) * unit))
    if edge and steps >= 1:
        size = 2 * steps + 1
        a8 = a8.filter(ImageFilter.MaxFilter(size) if edge > 0 else ImageFilter.MinFilter(size))
    radius = soft * unit
    if radius >= 0.4:
        a8 = a8.filter(ImageFilter.GaussianBlur(radius))
    return np.asarray(a8, dtype=np.float32) / 255


def composite(fg, alpha, color):
    """Blend onto a flat colour (exact RGB), or keep transparency if None."""
    a = alpha[..., None]
    if color is None:
        rgba = np.dstack([fg, alpha])
        return Image.fromarray(np.rint(rgba * 255).astype(np.uint8), "RGBA")
    bg = np.array(color, dtype=np.float32) / 255
    out = fg * a + bg * (1 - a)
    return Image.fromarray(np.rint(out * 255).astype(np.uint8), "RGB")


class Cutout:
    """A finished cut-out. Edge/colour changes re-render instantly."""

    def __init__(self, fg, alpha):
        self.fg, self.alpha = fg, alpha
        h, w = alpha.shape
        self.long_side = max(h, w)
        self.scale = min(1.0, 640 / self.long_side)
        if self.scale < 1:
            size = (max(1, round(w * self.scale)), max(1, round(h * self.scale)))
            self.fg_small = np.dstack([_resize_float(fg[..., i], size) for i in range(3)])
            self.alpha_small = _resize_float(alpha, size)
        else:
            self.fg_small, self.alpha_small = fg, alpha

    @property
    def size(self):
        return self.alpha.shape[1], self.alpha.shape[0]

    def render(self, color, edge=0, soft=1, preview=False):
        if preview:
            fg, alpha, long_side = self.fg_small, self.alpha_small, self.long_side * self.scale
        else:
            fg, alpha, long_side = self.fg, self.alpha, self.long_side
        return composite(fg, adjust_alpha(alpha, edge, soft, long_side), color)


def cut_out(matter, im, progress=None):
    """Full pipeline: PIL image in, Cutout out."""
    def step(text):
        if progress:
            progress(text)

    im = _flat_rgb(im)
    if max(im.size) > MAX_SIDE:
        scale = MAX_SIDE / max(im.size)
        im = im.resize((round(im.width * scale), round(im.height * scale)), RESAMPLE)
    w, h = im.size

    step("Finding the person...")
    alpha_lr = matter.predict(im)

    step("Cleaning the mask...")
    kept, holes = cleanup_region(alpha_lr)
    grown = Image.fromarray(kept.astype(np.uint8) * 255, "L").filter(ImageFilter.MaxFilter(5))
    region = _resize_float(np.asarray(grown, dtype=np.float32) / 255, (w, h))
    solid = _resize_float(holes.astype(np.float32), (w, h))
    alpha = np.clip(_resize_float(alpha_lr, (w, h)) * region, 0, 1)
    alpha = np.maximum(alpha, np.clip(solid * 2, 0, 1))

    step("Refining the edges...")
    img = np.asarray(im, dtype=np.float32) / 255
    guide = img @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    radius = max(4, max(w, h) // 200)
    refined = np.clip(guided_filter(guide, alpha, radius, 1e-4), 0, 1)
    unknown = ((alpha > 0.02) & (alpha < 0.98)).astype(np.float32)
    weight = np.clip(box_blur(unknown, radius) * 4, 0, 1)
    alpha = weight * refined + (1 - weight) * alpha
    t = np.clip((alpha - 0.08) / 0.84, 0, 1)  # gentle S-curve: crisper edge
    alpha = (t * t * (3 - 2 * t)).astype(np.float32)

    step("Removing old background colour...")
    fg = estimate_foreground(img, alpha)
    return Cutout(fg.astype(np.float32), alpha)
