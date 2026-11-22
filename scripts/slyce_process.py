"""SLYCE process step: turns the renders of plan.json into sprites and manifest.json, following FUZIO's
slicing rules (FUZIO_SPEC sections 8, 9, 10.3, 12.3, 14.3). Pure Python on the renders.

Per element, top of the PSD first, on its own artboard:
  skipped   stray pixels; full-screen mockup under a dim overlay; big images behind the background;
            covered by opaque layers above (8.2; pixels off the artboard count as covered); soft glows,
            shadows and light art (SLYCE: rebuilt in engine, not exported)
  title     the main title with a style TMP cannot draw: whole image, at most 512 px (12.3)
  composed  a styled or composed group / smart object: whole image, plus its parts when they redraw it (14.3)
  background full-artboard art: tinted fill if flat, 9-slice if it slices, else whole at native size (9.9)
  art       whole (9.1 step 2a)
  box-like  (shape/fill layers, UI names, or art whose silhouette is a box or rounded box), 9.1 order:
            1 tinted fill: one flat colour in a box / rounded box / ellipse, checked by redrawing it (9.10)
            2 9-slice of the whole element (9.2)
            3 gradient box: linear gradient in a fixed orientation -> frame sprite + Gradient2 keys, checked
              by rebuilding it (9.4)
            4 3-slice along the flat axis (9.3)
            5 header split: a header band whole and the body sliced (9.7)
            6 whole
Then duplicates, largest first (10.3), and square power-of-two sizing (9.8).
"""
import json
import math
import os
import re
import time

import slyce_imaging as I
import slyce_png as P

from slyce_shapes import soft_fx  # noqa: F401
from slyce_dupes import _dedupe  # noqa: F401
from slyce_decide import _decide  # noqa: F401
from slyce_write import _write  # noqa: F401


# ---------- covered layers (FUZIO Coverage; spec 8.2) ----------

class Canvas(object):
    """One artboard (or the document): opaque mask, dim overlay and background seen so far."""

    def __init__(self, rect):
        self.x, self.y, self.w, self.h = rect["x"], rect["y"], rect["width"], rect["height"]
        self.m = bytearray(self.w * self.h)
        self.dimmed = None
        self.background = None

    def covered_fraction(self, img, x0, y0):
        total = under = 0
        a = img.alpha()
        for y in range(img.h):
            dy = y0 + y - self.y
            row = a[y * img.w:(y + 1) * img.w]
            for x in range(img.w):
                if row[x] < I.VISIBLE_ALPHA:
                    continue
                total += 1
                dx = x0 + x - self.x
                if dx < 0 or dx >= self.w or dy < 0 or dy >= self.h or self.m[dy * self.w + dx]:
                    under += 1   # off the artboard is never seen either
        return (under / float(total)) if total else 1.0

    def add(self, img, x0, y0):
        a = img.alpha()
        for y in range(img.h):
            dy = y0 + y - self.y
            if dy < 0 or dy >= self.h:
                continue
            row = a[y * img.w:(y + 1) * img.w]
            base = dy * self.w
            for x in range(img.w):
                if row[x] >= I.OPAQUE_ALPHA:
                    dx = x0 + x - self.x
                    if 0 <= dx < self.w:
                        self.m[base + dx] = 1


# ---------- main ----------

def _load_prev(manifest_path):
    if not os.path.exists(manifest_path):
        return {}
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            m = json.load(f)
    except (OSError, ValueError):
        return {}
    known = dict((f, None) for f in m.get("owned", []))
    known.update((s["file"], s.get("digest")) for s in m.get("sprites", []))
    return known


def _clip(img, x, y, rect):
    """Crops a render placed at (x, y) to the artboard rectangle; artboards clip what lies outside them."""
    x0, y0 = max(x, rect["x"]), max(y, rect["y"])
    x1, y1 = min(x + img.w, rect["x"] + rect["width"]), min(y + img.h, rect["y"] + rect["height"])
    if x1 <= x0 or y1 <= y0:
        return None, x, y
    if (x0, y0, x1, y1) == (x, y, x + img.w, y + img.h):
        return img, x, y
    out, tx, ty = P.trim(img.crop(x0 - x, y0 - y, x1 - x0, y1 - y0))
    return out, x0 + tx, y0 + ty


def process(plan, renders, out_dir, rules, log=print, clean=False):
    """renders: key -> {'file', 'bounds'} or {'error'}; extra renders are '<key>:<variant>'."""
    min_size = int(rules.get("minSize"))
    max_size = int(rules.get("maxSize"))
    prev = _load_prev(os.path.join(out_dir, "manifest.json"))
    doc = plan["document"]
    canvases = [Canvas(a["rect"]) for a in plan.get("artboards") or []]
    doc_canvas = Canvas({"x": 0, "y": 0, "width": doc["width"], "height": doc["height"]})
    result = {"elements": [], "sprites": [], "duplicates": [], "tintedFills": [], "gradients": [],
              "skipped": list(plan["skipped"]), "texts": plan["texts"], "warnings": list(plan.get("warnings", []))}

    def skip(el, category, reason):
        result["skipped"].append({"layerId": el["layerId"], "name": el["name"], "path": el["path"], "kind": el["kind"],
                                  "category": category, "reason": reason, "bounds": el.get("bounds")})

    def load(key):
        r = renders.get(key)
        if not r or r.get("error"):
            return None, 0, 0
        # A large faint halo (alpha 1-7, e.g. a smart object's stray soft pixels) must not stretch the bounds,
        # but a small glow's faint edge belongs to it: trim at alpha 8 only when that removes a lot of area.
        raw = P.read_png(r["file"])
        full, fx_, fy_ = P.trim(raw, 1)
        if full is None:
            return None, 0, 0
        vis, vx, vy = P.trim(raw, I.VISIBLE_ALPHA)
        if vis is not None and vis.w * vis.h < 0.6 * full.w * full.h:
            img, tx, ty = vis, vx, vy
        else:
            img, tx, ty = full, fx_, fy_
        # Colour under fully transparent pixels is invisible: clear it, so it never blocks a merge or bloats a PNG.
        px = img.px
        for k in range(3, len(px), 4):
            if px[k] == 0:
                px[k - 3] = px[k - 2] = px[k - 1] = 0
        sc = float(r["bounds"].get("scale") or 1.0)
        if sc != 1.0:
            return img, int(round(r["bounds"]["x"] + tx / sc)), int(round(r["bounds"]["y"] + ty / sc))
        if img is None:
            return None, 0, 0
        return img, r["bounds"]["x"] + tx, r["bounds"]["y"] + ty

    cands = []
    t_start = time.time()
    for el in plan["elements"]:
        if len(cands) and len(cands) % 20 == 0:
            log("SLYCE: deciding... %d elements, %.0f s" % (len(cands), time.time() - t_start))
        r = renders.get(el["key"])
        if not r or r.get("error"):
            skip(el, "error", "render failed: %s" % (r or {}).get("error", "no render"))
            continue
        img, bx, by = load(el["key"])
        ab = el.get("artboard")
        cv = canvases[ab] if ab is not None and ab < len(canvases) else doc_canvas
        if img is not None:
            img, bx, by = _clip(img, bx, by, {"x": cv.x, "y": cv.y, "width": cv.w, "height": cv.h})
        if img is None:
            skip(el, "covered", "nothing visible on its artboard")
            continue
        if max(img.alpha()) < 16 or img.w * img.h <= 4:
            skip(el, "empty", "stray pixels (%dx%d px, nearly invisible)" % (img.w, img.h))
            continue
        placed = {"x": bx, "y": by, "width": img.w, "height": img.h}
        keep = el.get("keep")
        role = el["role"]
        full_canvas = img.w >= 0.9 * cv.w and img.h >= 0.9 * cv.h
        if not keep and cv.dimmed and full_canvas:
            skip(el, "mockup", "full-screen image under the dim overlay %s: a mockup of the screen behind the popup" % cv.dimmed)
            continue
        if not keep and cv.background and role != "background" and img.w * img.h >= 0.5 * cv.w * cv.h:
            skip(el, "behind", "large image behind the background %s (a reference or an alternative background)" % cv.background)
            continue
        if not keep and cv.covered_fraction(img, bx, by) >= 0.995:
            skip(el, "covered", "fully covered by opaque layers above it (or off its artboard)")
            continue
        if not el.get("show"):
            cv.add(img, bx, by)   # hidden alternative states never cover what is visible below them
        if not keep and role not in ("composed", "title", "text", "background") and soft_fx(img):
            skip(el, "fx", "soft translucent pixels only (glow, light or shadow: rebuilt in engine)")
            continue

        c = {"el": el, "img": img, "placed": placed, "sprites": [], "decisions": [], "review": [], "treatment": None,
             "role": role, "dup": None, "keepDetail": role in ("title", "text") or bool(el.get("thinStroke")),
             "bgSize": int(rules.get("backgroundSize"))}
        _decide(c, el, img, placed, cv, keep, full_canvas, load)
        cands.append(c)

    t0 = time.time()
    log("SLYCE: %d elements decided; looking for duplicates" % len(cands))
    _dedupe(cands)
    t1 = time.time()
    log("SLYCE: duplicates done in %.0f s; writing sprites" % (t1 - t0))
    _write(cands, result, out_dir, prev, min_size, max_size, load, log, plan.get("artboards"), rules.get("artboardFolders"))
    log("SLYCE: sprites written in %.0f s" % (time.time() - t1))

    files = set(s["file"] for s in result["sprites"])
    stale = sorted(f for f in prev if f not in files and os.path.exists(os.path.join(out_dir, f)))
    result["removed"] = []
    if clean:
        # Only files SLYCE itself wrote (listed in the previous manifest) are ever deleted.
        for f in stale:
            os.remove(os.path.join(out_dir, f))
            result["removed"].append(f)
            folder = os.path.dirname(os.path.join(out_dir, f))
            if os.path.isdir(folder) and not os.listdir(folder) and os.path.basename(folder) != "Sprites":
                os.rmdir(folder)
        stale = []
    result["stale"] = stale
    result["owned"] = sorted(files | set(stale))
    return result
