"""Draws the FlipFormat icon (original artwork) and writes assets/*.ico/png.

Run from the project folder:  python tools/make_icon.py
"""
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

S = 2048  # drawn large, then scaled down for crisp edges
OUT = Path(__file__).resolve().parent.parent / "assets"


def gradient(size, stops, angle_deg=135):
    """Linear multi-stop colour gradient. stops = [(pos, (r, g, b)), ...]."""
    w, h = size
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    a = math.radians(angle_deg)
    t = (xs * math.cos(a) + ys * math.sin(a))
    t = (t - t.min()) / (t.max() - t.min())
    out = np.zeros((h, w, 3), np.float32)
    for c in range(3):
        out[..., c] = np.interp(t, [p for p, _ in stops], [col[c] for _, col in stops])
    return Image.fromarray(out.astype(np.uint8), "RGB")


def rounded_mask(size, radius):
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius, fill=255)
    return m


def layer_rotated(draw_fn, angle, center):
    """Draw on a transparent layer, rotate about `center`, return the layer."""
    layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    draw_fn(layer)
    return layer.rotate(angle, resample=Image.Resampling.BICUBIC, center=center)


def card(layer, box, radius, fill):
    ImageDraw.Draw(layer).rounded_rectangle(box, radius, fill=fill)


def arrow_arc(draw, c, r, start, end, width, color):
    cx, cy = c
    draw.arc((cx - r, cy - r, cx + r, cy + r), start, end, fill=color, width=width)
    t = math.radians(end)
    px, py = cx + r * math.cos(t), cy + r * math.sin(t)
    tx, ty = -math.sin(t), math.cos(t)       # clockwise tangent
    nx, ny = math.cos(t), math.sin(t)        # radial
    L, W = 105, 95
    draw.polygon([(px + tx * L, py + ty * L),
                  (px + nx * W, py + ny * W),
                  (px - nx * W, py - ny * W)], fill=color)


def main():
    OUT.mkdir(exist_ok=True)
    # --- background tile: sunset gradient in a rounded square
    tile = gradient((S, S), [(0, (255, 150, 30)), (0.45, (255, 46, 147)), (1, (110, 60, 255))])
    base = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    base.paste(tile, (0, 0), rounded_mask((S, S), int(S * 0.225)))

    # soft glow in the corner for depth
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((-500, -600, 1300, 900), fill=(255, 255, 255, 70))
    glow = glow.filter(ImageFilter.GaussianBlur(160))
    clip = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    clip.paste(glow, (0, 0), rounded_mask((S, S), int(S * 0.225)))
    base = Image.alpha_composite(base, clip)

    centre = (S // 2, S // 2 - 40)
    # --- back card (translucent), tilted one way
    back = layer_rotated(lambda l: card(l, (330, 330, 1530, 1230), 120, (255, 255, 255, 105)),
                         10, centre)
    base = Image.alpha_composite(base, back)

    # --- front card with shadow, tilted the other way
    def front_card(l):
        card(l, (440, 520, 1640, 1420), 120, (255, 255, 255, 255))
        # mini landscape: sky, sun, hills (clipped to the card)
        sky = gradient((1200, 900), [(0, (255, 232, 190)), (1, (255, 190, 225))], 90)
        scene = Image.new("RGBA", (1200, 900), (0, 0, 0, 0))
        d = ImageDraw.Draw(scene)
        d.ellipse((760, 110, 980, 330), fill=(255, 200, 20, 255))               # sun
        d.ellipse((-240, 470, 760, 1250), fill=(0, 196, 170, 255))              # hill 1
        d.ellipse((330, 560, 1480, 1400), fill=(110, 76, 255, 255))             # hill 2
        photo = Image.new("RGBA", (1200, 900), (0, 0, 0, 0))
        photo.paste(sky, (0, 0))
        photo = Image.alpha_composite(photo, scene)
        l.paste(photo, (440, 520), rounded_mask((1200, 900), 120))

    front = layer_rotated(front_card, -7, centre)
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    shadow.paste((60, 0, 90, 120), (0, 40), front.split()[3])
    shadow = shadow.filter(ImageFilter.GaussianBlur(40))
    base = Image.alpha_composite(base, shadow)
    base = Image.alpha_composite(base, front)

    # --- convert badge: cyan-blue disc with two circular arrows
    bc, br = (1480, 1480), 360
    badge = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    bd = ImageDraw.Draw(badge)
    bd.ellipse((bc[0] - br - 40, bc[1] - br - 40, bc[0] + br + 40, bc[1] + br + 40),
               fill=(255, 255, 255, 255))                                      # white ring
    disc = gradient((2 * br, 2 * br), [(0, (0, 225, 255)), (1, (20, 90, 255))], 120)
    badge.paste(disc, (bc[0] - br, bc[1] - br), Image.new("L", (2 * br, 2 * br), 0))
    dm = Image.new("L", (2 * br, 2 * br), 0)
    ImageDraw.Draw(dm).ellipse((0, 0, 2 * br - 1, 2 * br - 1), fill=255)
    badge.paste(disc, (bc[0] - br, bc[1] - br), dm)
    ad = ImageDraw.Draw(badge)
    arrow_arc(ad, bc, 200, 205, 325, 58, (255, 255, 255, 255))
    arrow_arc(ad, bc, 200, 25, 145, 58, (255, 255, 255, 255))
    bshadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    bshadow.paste((40, 0, 80, 110), (0, 30), badge.split()[3])
    bshadow = bshadow.filter(ImageFilter.GaussianBlur(30))
    base = Image.alpha_composite(base, bshadow)
    base = Image.alpha_composite(base, badge)

    icon = base.resize((1024, 1024), Image.Resampling.LANCZOS)
    icon.resize((512, 512), Image.Resampling.LANCZOS).save(OUT / "icon.png")
    icon.save(OUT / "flipformat.ico",
              sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)])
    print("wrote", OUT / "icon.png", "and", OUT / "flipformat.ico")


if __name__ == "__main__":
    main()
