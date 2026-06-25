#!/usr/bin/env python3
"""Generate the social-share card at docs/og.png.

There was no source for the card, so it drifted (stale tool count + price).
This regenerates it from data, on the blue anchor palette. Re-run after a
price/count change:  .venv/bin/python scripts/gen_og.py

Fonts are macOS system fonts; adjust FONT_* if running elsewhere.
"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 630
NAVY = (11, 18, 32, 255)        # #0b1220
BLUE = (125, 211, 252, 255)     # #7dd3fc
WHITE = (236, 233, 226, 255)    # #ece9e2
GRAY = (122, 124, 132, 255)     # #7a7c84

MARGIN = 72

FONT_BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
FONT_REG = "/System/Library/Fonts/Supplemental/Arial.ttf"
FONT_MONO = "/System/Library/Fonts/Menlo.ttc"

# (price, name) — representative endpoints; prices must match the catalogue.
CHIPS = [
    ("$0.001", "screen"),
    ("$0.005", "intel"),
    ("$0.01", "aura"),
    ("$0.05", "oracle"),
    ("$1.77", "investigate"),
]
TOOL_COUNT = 16
FOOTER = "chat.anchor-x402.com"


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def _tracked(draw, xy, text, font, fill, tracking):
    """Draw text with extra per-character spacing (letter-spacing)."""
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + tracking


def main() -> None:
    img = Image.new("RGBA", (W, H), NAVY)
    d = ImageDraw.Draw(img)

    f_eyebrow = _font(FONT_BOLD, 24)
    f_head = _font(FONT_BOLD, 104)
    f_sub = _font(FONT_REG, 44)
    f_foot = _font(FONT_MONO, 24)

    # eyebrow
    _tracked(d, (MARGIN, 96), "AGENT / TERMINAL", f_eyebrow, GRAY, 4)

    # headline: "anchor" blue + "-x402" white
    hy = 150
    d.text((MARGIN, hy), "anchor", font=f_head, fill=BLUE)
    ax = MARGIN + d.textlength("anchor", font=f_head)
    d.text((ax, hy), "-x402", font=f_head, fill=WHITE)

    # subheads
    d.text((MARGIN, 290), f"{TOOL_COUNT} paid AI tools.", font=f_sub, fill=WHITE)
    d.text((MARGIN, 346), "Pay per call. From your wallet.", font=f_sub, fill=GRAY)

    # chips — auto-shrink the font so all chips fit one row within the margins
    cy, ch_h, gap, pad = 432, 56, 16, 18
    labels = [f"{price}  {name}" for price, name in CHIPS]
    avail = W - 2 * MARGIN
    for size in range(24, 13, -1):
        f_chip = _font(FONT_MONO, size)
        widths = [d.textlength(s, font=f_chip) + pad * 2 for s in labels]
        if sum(widths) + gap * (len(labels) - 1) <= avail:
            break
    cx = MARGIN
    for label, cw in zip(labels, widths):
        d.rounded_rectangle([cx, cy, cx + cw, cy + ch_h], radius=10, outline=BLUE, width=2)
        bb = f_chip.getbbox(label)
        ty = cy + (ch_h - (bb[3] - bb[1])) / 2 - bb[1]
        d.text((cx + pad, ty), label, font=f_chip, fill=BLUE)
        cx += cw + gap

    # footer
    d.text((MARGIN, H - 64), FOOTER, font=f_foot, fill=GRAY)

    out = os.path.join(os.path.dirname(__file__), "..", "docs", "og.png")
    img.save(os.path.abspath(out))
    print("wrote", os.path.abspath(out))


if __name__ == "__main__":
    main()
