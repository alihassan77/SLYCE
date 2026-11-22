"""SLYCE per-element decision (FUZIO 9.1 order): tinted fill, 9-slice, gradient box, 3-slice, header split,
whole; plus fill bars rounded at both ends and squarer 3-slice centres.
"""
import json
import math
import os
import re
import time

import slyce_imaging as I
import slyce_png as P

from slyce_shapes import plain_gradient, FLAT_TOL, KEEP, TITLE_MAX, TOL, _hex, fit_shape, map_gradient, rebuild_gradient_box  # noqa: F401


def _box_shape(el, img):
    """The kit shape for a flat fill: the live shape's origination (rect, rounded rect with equal radii,
    ellipse; FUZIO 9.10), or for pixel layers and smart objects the silhouette fitted within pixel error."""
    shape = el.get("shape") or {}
    origin = shape.get("origination")
    if origin == "ellipse":
        return "ellipse", None
    if origin == "roundedRect":
        r = shape.get("radii") or {}
        vals = [r.get(k, 0) or 0 for k in ("topLeft", "topRight", "bottomRight", "bottomLeft")]
        if max(vals) - min(vals) >= 0.5:
            return None
        return ("roundedRect", vals[0]) if vals[0] > 0 else ("rect", 0)
    if origin == "rect":
        return "rect", 0
    return fit_shape(img, max(1, max(img.alpha()) // 2))


def _decide(c, el, img, placed, cv, keep, full_canvas, load):
    role = el["role"]

    def add_sprite(purpose, image, kind, borders=None, axis=None, at=None, background=False, cap=None, suffix=""):
        c["sprites"].append({"purpose": purpose, "img": image, "kind": kind, "borders": borders, "axis": axis,
                             "placed": at or placed, "background": background, "cap": cap, "suffix": suffix})

    def tinted_fill():
        flat, col = I.is_flat(img, FLAT_TOL)
        if not flat:
            return False
        sh = _box_shape(el, img)
        if not sh:
            return False
        shape, radius = sh
        ok, s, m = I.passes(I.draw_shape(shape, img.w, img.h, radius, col), img)
        if not ok:
            c["decisions"].append("flat %s %s does not match when redrawn (SSIM %.4f, MAE %.2f); next option" % (shape, _hex(col), s, m))
            return False
        sprite = "white circle" if shape in ("ellipse", "roundedRect") else "white box"
        c["treatment"] = "tintedFill"
        c["kit"] = {"name": el["name"], "layerId": el["layerId"], "path": el["path"], "shape": shape, "radius": radius,
                    "color": _hex(col), "sprite": sprite, "imageType": "simple" if shape == "ellipse" else "sliced",
                    "placed": placed, "ssim": round(s, 4), "mae": round(m, 2)}
        c["decisions"].append("flat %s in %s: tinted %s%s, no sprite (9.10)" % (
            shape, _hex(col), sprite, ", corner radius %g px" % radius if shape == "roundedRect" else ""))
        if full_canvas and shape == "rect" and col[3] < 250:
            cv.dimmed = cv.dimmed or el["name"]
        return True

    is_bg = role == "background" or (full_canvas and role in ("box", "art") and not el.get("noSlice"))
    box_like = role == "box"
    if role == "art" and not is_bg and not el.get("noSlice"):
        sil = fit_shape(img, max(1, max(img.alpha()) // 2))
        if sil and sil[0] in ("rect", "roundedRect"):
            box_like = True
            c["decisions"].append("art whose silhouette is a %s: treated as box-like" % sil[0])
        else:
            trial = I.decide_slice(img, TOL, KEEP)
            if trial["kind"] and (trial["img"].w <= 0.6 * img.w or trial["img"].h <= 0.6 * img.h):
                box_like = True
                c["decisions"].append("art that slices losslessly to %dx%d (from %dx%d): treated as a UI box" % (
                    trial["img"].w, trial["img"].h, img.w, img.h))

    if role in ("title", "text"):
        c["treatment"] = "whole"
        c["decisions"].append("title image, at most %d px (12.3)" % TITLE_MAX if role == "title" else "text as an image (rule textAsImage)")
        add_sprite("main", img, "whole", cap=TITLE_MAX if role == "title" else None)
        return
    if role == "composed":
        c["treatment"] = "whole"
        add_sprite("main", img, "whole")
        return
    if is_bg:
        opaque = sum(1 for v in img.alpha() if v >= 230)
        if el.get("overlays") is not None or el["kind"] == "group":
            # The background group as a whole (one painting): never a tinted fill or a slice.
            c["treatment"] = "whole"
            c["decisions"].append("background as a whole: fitted and padded into a %d px square" % c["bgSize"])
            add_sprite("main", img, "whole", background=True)
        elif keep or not tinted_fill():
            s = I.decide_slice(img, TOL, KEEP) if not el.get("noSlice") else {"kind": None}
            g = None if keep or s["kind"] == "sliced9" else plain_gradient(img)
            if g:
                # A gradient with no artwork: Unity's gradient component (or a 32 x 32 texture), never a 2048 image.
                c["treatment"] = "gradient"
                c["decisions"].append("background that is only a %s gradient (%d keys, SSIM %.4f, MAE %.2f): not exported, "
                                      "rebuilt with a gradient component" % (g["gradientType"].lower(), len(g["colorKeys"]), g["ssim"], g["mae"]))
                c["gradient"] = {"name": el["name"], "layerId": el["layerId"], "path": el["path"], "source": "background gradient",
                                 "fill": {"sprite": "none: a gradient component, or a 32 x 32 gradient texture at most", "rect": placed,
                                          "gradient2": {"supported": True, "gradientType": g["gradientType"], "zoom": 1.0,
                                                        "colorKeys": g["colorKeys"], "alphaKeys": g["alphaKeys"]}},
                                 "placed": placed, "ssim": g["ssim"], "mae": g["mae"]}
            elif s["kind"] == "sliced9":
                c["treatment"] = "sliced9"
                c["decisions"].append("flat background: 9-slice (9.9)")
                add_sprite("main", s["img"], "sliced9", s["borders"], background=True)
            else:
                c["treatment"] = "whole"
                c["decisions"].append("background art: whole, fitted and padded into a %d px square" % c["bgSize"])
                add_sprite("main", img, "whole", background=True)
        dim = c.get("kit") and int(c["kit"]["color"][7:9], 16) < 250
        if opaque >= 0.5 * cv.w * cv.h and not dim:
            cv.background = cv.background or el["name"]
        return
    if not box_like:
        c["treatment"] = "whole"
        c["decisions"].append("art: exported whole (9.1 step 2a)")
        add_sprite("main", img, "whole")
        return

    if not keep and tinted_fill():
        return
    s = None
    if not el.get("noSlice"):
        s = I.decide_slice(img, TOL, KEEP)
        if s["kind"] == "sliced9":
            c["treatment"] = "sliced9"
            c["decisions"].append("flat fill and uniform style: one 9-slice of the whole element (9.1 step 3)")
            add_sprite("main", s["img"], "sliced9", s["borders"])
            return
        c["decisions"].append("uniformity: longest column run %d px, row run %d px" % (s["columnRun"], s["rowRun"]))
        if el.get("gradient") and _gradient_box(c, el, img, placed, load, add_sprite):
            return
        if el.get("gradient") and (el["gradient"].get("type") or "linear") not in ("linear", "reflected"):
            c["review"].append("radial or shaded gradient: FUZIO builds it from soft circles (9.5); SLYCE exports it")
        if s["kind"] == "sliced3":
            c["treatment"] = "sliced3"
            c["decisions"].append("only %s are uniform: 3-slice (9.3)" % ("columns" if s["axis"] == "x" else "rows"))
            s = squarer_slice(img, s, c)
            if el.get("symmetric"):
                s = round_both_ends(s, c, el["symmetric"])
            elif not el.get("noRoundEnds"):
                s = round_both_ends(s, c)
            add_sprite("main", s["img"], "sliced3", s["borders"], axis=s["axis"])
            return
        band = I.header_split(img, TOL, KEEP)
        if band > 0:
            body = img.crop(0, band, img.w, img.h - band)
            bs = I.decide_slice(body, TOL, KEEP)
            if bs["kind"]:
                c["treatment"] = "headerSplit"
                c["decisions"].append("header on the box: split %d px from the top; header whole, body %s (9.7)" % (band, bs["kind"]))
                add_sprite("header", img.crop(0, 0, img.w, band), "whole", at=dict(placed, height=band), suffix="-Header")
                add_sprite("body", bs["img"], bs["kind"], bs["borders"], axis=bs["axis"],
                           at=dict(placed, y=placed["y"] + band, height=img.h - band), suffix="-Body")
                return
            c["decisions"].append("header split at %d px rejected: the body does not slice losslessly" % band)
    c["treatment"] = "whole"
    c["decisions"].append("no lossless slice or gradient box: exported whole (9.1 step 8)")
    add_sprite("main", img, "whole")


def _edge_cover(img, axis, last):
    """Share of the edge line (first/last column for axis x, row for axis y) that is nearly opaque."""
    if axis == "x":
        line = img.channel_col(img.w - 1 if last else 0, 3)
    else:
        line = img.row(img.h - 1 if last else 0)[3::4]
    return sum(1 for v in line if v >= 200) / float(len(line) or 1)


def _sharpness(img):
    """Mean neighbour difference over the visible pixels: higher is crisper (used to pick the clearer cap)."""
    px, w, tot, n = img.px, img.w, 0, 0
    for y in range(img.h):
        for x in range(1, w):
            o = (y * w + x) * 4
            if px[o + 3] < 8 and px[o - 1] < 8:
                continue
            tot += sum(abs(px[o + c] - px[o - 4 + c]) for c in range(4))
            n += 1
    return tot / float(n) if n else 0.0


def round_both_ends(sl, c, symmetric=None):
    """A fill bar is often cut by a mask in the PSD: rounded at one end, straight at the other. Unity needs it
    rounded at both ends (sliced or filled along a rounded bar background), so the rounded cap is mirrored
    onto the cut end. Works on a 3-slice result; returns it unchanged when both ends already match."""
    axis, b, img = sl["axis"], sl["borders"], sl["img"]
    lo_key, hi_key = ("left", "right") if axis == "x" else ("top", "bottom")
    lo_cover, hi_cover = _edge_cover(img, axis, False), _edge_cover(img, axis, True)
    if symmetric:
        # Rule `symmetric` (Ali, Daily Login): the piece looks the same at both ends, so both ends get one cap.
        # "auto" keeps the bigger cap (the made one; a dragged copy is usually the stub).
        lo_side = "left" if axis == "x" else "top"
        hi_side = "right" if axis == "x" else "bottom"
        if symmetric == lo_side:
            keep_cap = "lo"
        elif symmetric == hi_side:
            keep_cap = "hi"
        else:
            # Ali: the cap with the clearest pixels (sharpest edges) wins; a tie keeps the bigger cap.
            n_ = img.w if axis == "x" else img.h
            lo_img = img.crop(0, 0, b[lo_key], img.h) if axis == "x" else img.crop(0, 0, img.w, b[lo_key])
            hi_img = img.crop(n_ - b[hi_key], 0, b[hi_key], img.h) if axis == "x" else img.crop(0, n_ - b[hi_key], img.w, b[hi_key])
            sl_, sh_ = _sharpness(lo_img), _sharpness(hi_img)
            if abs(sl_ - sh_) > 0.05 * max(sl_, sh_, 1e-6):
                keep_cap = "lo" if sl_ > sh_ else "hi"
            else:
                keep_cap = "lo" if b[lo_key] >= b[hi_key] else "hi"
        cut = "hi" if keep_cap == "lo" else "lo"
        if b[lo_key] == b[hi_key] and symmetric == "auto":
            return sl
    elif hi_cover >= 0.9 and lo_cover < 0.6:
        keep_cap, cut = "lo", "hi"
    elif lo_cover >= 0.9 and hi_cover < 0.6:
        keep_cap, cut = "hi", "lo"
    else:
        return sl
    L, R = b[lo_key], b[hi_key]
    n = img.w if axis == "x" else img.h
    mid = n - L - R
    if axis == "x":
        cap = img.crop(0, 0, L, img.h) if keep_cap == "lo" else img.crop(n - R, 0, R, img.h)
        mirrored = P.flip_h(cap)
        middle = img.crop(L, 0, mid, img.h)
        parts = [cap, middle, mirrored] if keep_cap == "lo" else [mirrored, middle, cap]
        out = P.Img(sum(q.w for q in parts), img.h)
        x = 0
        for q in parts:
            out.paste(q, x, 0)
            x += q.w
    else:
        cap = img.crop(0, 0, img.w, L) if keep_cap == "lo" else img.crop(0, n - R, img.w, R)
        mirrored = P.flip_v(cap)
        middle = img.crop(0, L, img.w, mid)
        parts = [cap, middle, mirrored] if keep_cap == "lo" else [mirrored, middle, cap]
        out = P.Img(img.w, sum(q.h for q in parts))
        y = 0
        for q in parts:
            out.paste(q, 0, y)
            y += q.h
    size = L if keep_cap == "lo" else R
    nb = dict(b)
    nb[lo_key] = nb[hi_key] = size
    end = {"x": {"lo": "left", "hi": "right"}, "y": {"lo": "top", "hi": "bottom"}}[axis]
    if symmetric:
        c["decisions"].append("symmetric (rule): the %s cap is mirrored onto the %s end" % (end[keep_cap], end[cut]))
    else:
        c["decisions"].append("fill bar cut straight at the %s end (a mask in the PSD): the rounded end is mirrored so both ends are round" % end[cut])
    return dict(sl, img=out, borders=nb, roundedBothEnds=True)


def squarer_slice(img, sl, c):
    """A 3-slice collapsed to a 4 px middle can look like a sliver (a button whose shine sits on top and a
    hair-thin centre column): when the sprite's texture has the room, keep more of the flat run so the
    content is about square. Still lossless (the run is flat); the borders stay where they are."""
    axis, b = sl["axis"], sl["borders"]
    run = sl["columnRun"] if axis == "x" else sl["rowRun"]
    if axis == "x":
        want = img.h - b["left"] - b["right"]
    else:
        want = img.w - b["top"] - b["bottom"]
    keep = min(want, run - 1)
    if keep <= KEEP:
        return sl
    wider = I.decide_slice(img, TOL, keep)
    if wider["kind"] != "sliced3" or wider["axis"] != axis:
        return sl
    c["decisions"].append("3-slice centre kept at %d px (not %d) so the sprite is about square and easy to slice" % (keep, KEEP))
    return wider


def _gradient_box(c, el, img, placed, load, add_sprite):
    """FUZIO 9.4: frame (9-sliced) + a Gradient2 fill in the white box/circle, used when its rebuild matches."""
    m = map_gradient(el["gradient"], el.get("fillColor"))
    if not m["supported"]:
        c["decisions"].append("gradient box not possible: %s" % m["reason"])
        return False
    origin = (el.get("shape") or {}).get("origination")
    radius = 0
    if origin == "roundedRect":
        r = el["shape"].get("radii") or {}
        vals = [r.get(k, 0) or 0 for k in ("topLeft", "topRight", "bottomRight", "bottomLeft")]
        if max(vals) - min(vals) >= 0.5 or vals[0] <= 0:
            c["decisions"].append("gradient box not possible: uneven corner radii (9.6)")
            return False
        radius = vals[0]
    elif origin != "rect":
        c["decisions"].append("gradient box not possible: the shape is '%s', not a rectangle or rounded rectangle" % (origin or el["kind"]))
        return False
    mask, mx, my = load(el["key"] + ":fill")
    if mask is None:
        c["decisions"].append("gradient box not possible: no fill render")
        return False
    frame, frx, fry, fs = None, 0, 0, None
    for v in ("frame", "style"):
        if v in el.get("extras", []):
            frame, frx, fry = load(el["key"] + ":" + v)
    if frame is not None:
        fs = I.decide_slice(frame, TOL, KEEP)
        if fs["kind"] != "sliced9":
            c["decisions"].append("gradient box not possible: the style frame is not uniform")
            return False
    rebuilt = rebuild_gradient_box(m, img, placed["x"], placed["y"], mask, mx, my, frame, frx, fry, el["shapeBounds"])
    ok, s, mae = I.passes(rebuilt, img)
    if not ok:
        c["decisions"].append("gradient box rebuild failed the element thresholds (SSIM %.4f, MAE %.2f); next option" % (s, mae))
        return False
    c["treatment"] = "gradientBox"
    info = dict((k, v) for k, v in m.items() if not k.startswith("_"))
    c["gradient"] = {"name": el["name"], "layerId": el["layerId"], "path": el["path"], "source": "layer style",
                     "fill": {"sprite": "white circle" if radius > 0 else "white box", "rect": el["shapeBounds"],
                              "cornerRadius": radius, "gradient2": info},
                     "frame": (el["name"] + "-Frame") if frame is not None else None, "placed": placed,
                     "ssim": round(s, 4), "mae": round(mae, 2)}
    c["decisions"].append("linear gradient in a fixed orientation: gradient box, %s Gradient2 with %d colour keys; rebuild matches (SSIM %.4f, MAE %.2f)"
                          % (m["gradientType"], len(m["colorKeys"]), s, mae))
    if frame is not None:
        add_sprite("frame", fs["img"], "sliced9", fs["borders"], at={"x": frx, "y": fry, "width": frame.w, "height": frame.h}, suffix="-Frame")
    else:
        c["decisions"].append("no style besides the gradient: no frame sprite, nothing exported")
    return True
