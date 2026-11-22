"""SLYCE contact sheets: numbered grids of images that the planner and reviewer agents can look at in one go.

Each cell shows one image on a checkerboard with its number in the top-left corner; the matching JSON lists
what each number is (name, role, treatment, path). Pure Python (a tiny built-in digit font).
"""
import json

import slyce_imaging as I
import slyce_png as P

# 3x5 digits, one string of 15 bits per digit (rows top to bottom).
DIGITS = {
    "0": "111101101101111", "1": "010110010010111", "2": "111001111100111", "3": "111001111001111",
    "4": "101101111001001", "5": "111100111001111", "6": "111100111101111", "7": "111001010010010",
    "8": "111101111101111", "9": "111101111001111",
}


def _rect(img, x, y, w, h, rgba):
    for yy in range(max(0, y), min(img.h, y + h)):
        o = (yy * img.w + max(0, x)) * 4
        for xx in range(max(0, x), min(img.w, x + w)):
            img.px[o:o + 4] = rgba
            o += 4


def _label(img, x, y, text, scale=3):
    w = len(text) * 4 * scale + scale
    _rect(img, x, y, w, 5 * scale + 2 * scale, bytes((20, 20, 20, 230)))
    cx = x + scale
    for ch in text:
        bits = DIGITS.get(ch)
        if bits:
            for r in range(5):
                for c in range(3):
                    if bits[r * 3 + c] == "1":
                        _rect(img, cx + c * scale, y + scale + r * scale, scale, scale, bytes((255, 220, 60, 255)))
        cx += 4 * scale


def make_sheet(items, path, cell=180, cols=6):
    """items: list of (Img or None, info dict). Writes path (PNG) and path + '.json'; returns the count."""
    rows = max(1, (len(items) + cols - 1) // cols)
    sheet = P.Img(cols * cell, rows * cell)
    for y in range(sheet.h):
        for x in range(sheet.w):
            o = (y * sheet.w + x) * 4
            v = 150 if ((x // 12) + (y // 12)) % 2 else 110
            sheet.px[o:o + 4] = bytes((v, v, v, 255))
    index = []
    for i, (img, info) in enumerate(items):
        cx, cy = (i % cols) * cell, (i // cols) * cell
        _rect(sheet, cx, cy, cell, 1, bytes((60, 60, 60, 255)))
        _rect(sheet, cx, cy, 1, cell, bytes((60, 60, 60, 255)))
        if img is not None:
            f = min(1.0, (cell - 8) / float(img.w), (cell - 8) / float(img.h))
            if f < 1:
                img = P.resize_area(img, max(1, int(img.w * f)), max(1, int(img.h * f)))
            I.alpha_over(sheet, img, cx + (cell - img.w) // 2, cy + (cell - img.h) // 2)
        _label(sheet, cx + 3, cy + 3, str(i + 1))
        entry = {"n": i + 1}
        entry.update(info)
        index.append(entry)
    P.write_png(path, sheet)
    with open(path + ".json", "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2)
    return len(items)
