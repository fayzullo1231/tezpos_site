"""Blend kassa_img white stage into landing paper so it doesn't float as a card."""
from pathlib import Path

from PIL import Image

SRC = Path(__file__).resolve().parents[1] / "image" / "kassa_img.png"
OUT = Path(__file__).resolve().parents[1] / "static" / "img" / "kassa_img.png"
PAPER = (244, 247, 251, 255)


def main():
    im = Image.open(SRC).convert("RGBA")
    px = im.load()
    w, h = im.size

    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a < 12:
                px[x, y] = PAPER
                continue
            # near-white fills (big stage + card faces) → paper
            if r >= 242 and g >= 242 and b >= 242:
                t = min(a, 255) / 255.0
                # fully replace bright white; soft-blend faint whites
                if r >= 250 and g >= 250 and b >= 250:
                    px[x, y] = PAPER
                else:
                    px[x, y] = (
                        int(r * (1 - t) + PAPER[0] * t),
                        int(g * (1 - t) + PAPER[1] * t),
                        int(b * (1 - t) + PAPER[2] * t),
                        255,
                    )
            elif a < 255:
                t = a / 255.0
                px[x, y] = (
                    int(PAPER[0] * (1 - t) + r * t),
                    int(PAPER[1] * (1 - t) + g * t),
                    int(PAPER[2] * (1 - t) + b * t),
                    255,
                )

    im.save(OUT, optimize=True)
    print("saved", OUT, im.size, "corner", im.getpixel((0, 0)))


if __name__ == "__main__":
    main()
