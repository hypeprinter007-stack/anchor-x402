"""Generate a per-price variant of the signing screenshot.

Input: docs/screenshots/04-signing.png (the real Coinbase keys.coinbase.com sign
view, captured by the user — shows "−0.01 USDC" + EIP-712 typed-data JSON).
Output: a copy with the "−0.01 USDC" string replaced by the requested price.

Usage:
    python scripts/demo/signing.py <price> <out.png>

Example:
    python scripts/demo/signing.py 0.001 /tmp/signing-screen-0.001.png
"""
from __future__ import annotations

import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

SRC = Path(__file__).resolve().parents[2] / "docs" / "screenshots" / "04-signing.png"

# Bounding box of "−0.01 USDC" in the source image, with padding.
# Found empirically — pure-white pixels live in x[63..382] y[703..745]; we pad
# vertically to catch antialiasing fringes and horizontally to leave room for
# longer prices like "−7.770 USDC".
MASK_BOX = (50, 678, 720, 772)

# Block background color — sampled from the asset-changes panel between
# the price text and the USDC logo (uniform across the block region).
BLOCK_BG = (20, 22, 25)

# Text baseline + size, tuned to match the source rendering.
TEXT_XY = (63, 685)
FONT_SIZE = 80

# Helvetica.ttc index 1 = Bold; closest local proxy for SF Pro Display Bold,
# which is what keys.coinbase.com uses for the asset-change amount.
FONT_PATH = "/System/Library/Fonts/Helvetica.ttc"
FONT_INDEX = 1


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    if Path(FONT_PATH).exists():
        try:
            return ImageFont.truetype(FONT_PATH, size, index=FONT_INDEX)
        except OSError:
            pass
    return ImageFont.load_default()


def generate(price: str, out_path: Path) -> None:
    """Render a signing-screen variant with `−{price} USDC` in the asset-changes row.

    `price` is a decimal string like "0.001" or "7.770" — no leading minus, no $.
    """
    im = Image.open(SRC).convert("RGB")
    draw = ImageDraw.Draw(im)
    draw.rectangle(MASK_BOX, fill=BLOCK_BG)

    text = f"−{price} USDC"  # U+2212 minus sign matches the original
    font = _load_font(FONT_SIZE)
    draw.text(TEXT_XY, text, font=font, fill=(255, 255, 255))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    im.save(out_path, "PNG", optimize=True)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: signing.py <price> <out.png>", file=sys.stderr)
        sys.exit(2)
    generate(sys.argv[1], Path(sys.argv[2]))
    print(f"wrote {sys.argv[2]}")
