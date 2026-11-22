"""SLYCE writing: sizes and writes every sprite, its states and parts, the duplicates' placement, and the
manifest rows.
"""
import json
import math
import os
import re
import time

import slyce_imaging as I
import slyce_png as P

from slyce_shapes import PART_MIN_SSIM, decide_size  # noqa: F401


def _write(cands, result, out_dir, prev, min_size, max_size, load, log, artboards=None, artboard_folders=True):
    used = sorted(set(c["el"].get("artboard") for c in cands if not c["dup"] and c["sprites"]), key=lambda v: (v is None, v))
    multi = len([a for a in used if a is not None]) > 1 and bool(artboard_folders)

    def folder(el):
        ab = el.get("artboard")
        if multi and ab is not None and artboards and ab < len(artboards):
            return "Sprites/%s/" % re.sub(r"[^A-Za-z0-9]+", "-", artboards[ab]["name"]).strip("-")
        return "Sprites/"

    def write_sprite(rel, img):
        path = os.path.join(out_dir, rel)
        digest = img.digest()
        if prev.get(rel) == digest and os.path.exists(path):
            return digest, "same"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        P.write_png(path, img)
        return digest, ("changed" if rel in prev else "new")

    by_name = dict((c["el"]["name"], c) for c in cands)
    families = _family_targets(cands, min_size, max_size)
    canvas_fams = _canvas_targets(cands)
    # Sources first, so a copy can use its source's upright rect.
    cands = sorted(cands, key=lambda c: 1 if c["dup"] else 0)
    for c in cands:
        el = c["el"]
        entry = {"name": el["name"], "layerId": el["layerId"], "path": el["path"], "role": c["role"], "treatment": c["treatment"],
                 "placed": c["placed"], "decisions": c["decisions"] + el.get("notes", []), "review": c["review"], "sprites": []}
        if c.get("kit"):
            result["tintedFills"].append(c["kit"])
            result["skipped"].append({"layerId": el["layerId"], "name": el["name"], "path": el["path"], "kind": el["kind"],
                                      "category": "tintedFill", "reason": "flat %s %s: tinted %s" % (c["kit"]["color"], c["kit"]["shape"], c["kit"]["sprite"]),
                                      "bounds": el.get("bounds")})
        if c.get("gradient"):
            if c["dup"] and c["gradient"].get("frame"):
                c["gradient"]["frame"] = c["dup"]["source"] + "-Frame"   # merged gradient boxes share the source's frame
                c["gradient"]["duplicateOf"] = c["dup"]["source"]
            result["gradients"].append(c["gradient"])
            if c["gradient"].get("source") == "background gradient":
                g2 = c["gradient"]["fill"]["gradient2"]
                result["skipped"].append({"layerId": el["layerId"], "name": el["name"], "path": el["path"], "kind": el["kind"],
                                          "category": "gradient", "reason": "background that is only a %s gradient %s: rebuild in Unity" % (
                                              g2["gradientType"].lower(), " -> ".join(k["color"] for k in g2["colorKeys"])),
                                          "bounds": el.get("bounds")})
        if c["dup"]:
            d = dict(name=el["name"], layerId=el["layerId"], path=el["path"], placed=c["placed"])
            d.update(c["dup"])
            if d.get("angle") is not None:
                a = d["angle"] % 360
                d["rotation"] = round(a - 360 if a > 180 else a, 2)   # signed, like sprites' `rotation`
            src = by_name.get(d.get("source"))
            so = el.get("smartObject") or {}
            if src is not None and src.get("uprightPlaced") and so.get("centre"):
                sp_, sc_ = src["uprightPlaced"]
                k = d.get("scale") or 1.0
                cx, cy = so["centre"]
                d["pivot"] = [round(cx, 2), round(cy, 2)]
                d["placed"] = {"x": int(round(cx + (sp_["x"] - sc_[0]) * k)), "y": int(round(cy + (sp_["y"] - sc_[1]) * k)),
                               "width": int(round(sp_["width"] * k)), "height": int(round(sp_["height"] * k))}
            result["duplicates"].append(d)
            entry["treatment"] = "duplicate"
            entry["duplicate"] = c["dup"]
            if d.get("review"):
                result["warnings"].append("%s reuses %s's sprite at similarity %.3f: check it looks the same" % (el["name"], d["source"], d["similarity"]))
            result["elements"].append(entry)
            log("  %-32s duplicate of %s (%s)" % (el["name"], d["source"], d["match"]))
            continue
        whole_scale = 1.0
        for sp in c["sprites"]:
            name = el["name"] + sp["suffix"]
            img, sliced = sp["img"], sp["borders"] is not None
            placed_w = float(img.w)
            if id(sp) in canvas_fams:
                fimg, fb, note = canvas_fams[id(sp)]
                entry["decisions"].append(note)
                sp = dict(sp, img=fimg, borders=fb)
                img = fimg
            alt = ":hires" if el.get("hires") else (":upright" if el.get("uprightRender") else None)
            if sp["purpose"] == "main" and not sliced and alt and c["treatment"] == "whole":
                hi, hx, hy = load(el["key"] + alt)
                hx, hy = hx or 0, hy or 0
                if hi is not None:
                    img = hi
                    placed_w = hi.w / float((el.get("hires") or {}).get("scale") or 1.0)
                    if ((el.get("hires") or {}).get("scale") or 1.0) > 1.05:
                        entry["decisions"].append("headroom: pixels from the smart object's contents at %dx%d (placed %dx%d)" % (
                            hi.w, hi.h, c["placed"]["width"], c["placed"]["height"]))
                else:
                    entry["review"].append("%s render failed: exported as placed instead" % alt.strip(":"))
            rel = folder(el) + "%s.png" % name
            info = {"scale": 1.0}
            if sp["background"] and sliced:
                final = img
                info.update(texture={"width": img.w, "height": img.h}, content={"x": 0, "y": 0, "width": img.w, "height": img.h},
                            background=True, borders=sp["borders"], slice=sp["borders"])
            elif sp["background"]:
                # Backgrounds as a whole: fitted (aspect kept) and padded into one square, e.g. a 1870x2436 tablet
                # background -> 1572x2048 inside 2048x2048. The Image keeps the placed size.
                size = c["bgSize"]
                f = min(1.0, size / float(max(img.w, img.h)))
                work = img if f >= 0.999 else P.resize_area(img, max(1, int(round(img.w * f))), max(1, int(round(img.h * f))))
                final = P.pad_center(work, size, size)
                cx, cy = (size - work.w) // 2, (size - work.h) // 2
                info.update(texture={"width": size, "height": size}, content={"x": cx, "y": cy, "width": work.w, "height": work.h},
                            scale=round(work.w / float(img.w), 4), background=True,
                            sizing="background as a whole: fitted and padded into %d x %d" % (size, size))
            elif families.get(el.get("family")) and sp["purpose"] == "main" and not sliced:
                # sameSize family (Ali, Daily Login): interchangeable items share the biggest slot's size, so a
                # booster placed small still looks right when the developer moves it into the big card.
                size, target = families[el["family"]]
                side = max(img.w, img.h)
                if side > target:
                    f = target / float(side)
                    work = P.resize_area(img, max(1, int(round(img.w * f))), max(1, int(round(img.h * f))))
                else:
                    work = img
                    if side < 0.95 * target:
                        c["review"].append("family %s: source is %d px, smaller than the family's %d px; padded, not upscaled "
                                           "(ask the artist for a bigger source)" % (el["family"], side, target))
                final = P.pad_center(work, size, size)
                cx, cy = (size - work.w) // 2, (size - work.h) // 2
                info.update(texture={"width": size, "height": size}, content={"x": cx, "y": cy, "width": work.w, "height": work.h},
                            scale=round(work.w / placed_w, 4), family=el["family"],
                            sizing="family %s: %d px content in %d, like the biggest slot" % (el["family"], target, size))
            else:
                cap = sp["cap"] or max_size
                vis = [v for v in img.alpha() if v >= 8]
                soft = bool(vis) and sum(1 for v in vis if v < 200) / float(len(vis)) >= 0.45
                sz = decide_size(max(img.w, img.h), sliced, c["keepDetail"], min_size, max_size, cap, soft, c.get("stroked"))
                work = img
                if sz["scale"] < 1 and not sliced:
                    work = P.resize_area(img, max(1, int(round(img.w * sz["scale"]))), max(1, int(round(img.h * sz["scale"]))))
                size = max(sz["size"], work.w, work.h)
                final = P.pad_center(work, size, size)
                cx, cy = (size - work.w) // 2, (size - work.h) // 2
                info.update(texture={"width": size, "height": size}, content={"x": cx, "y": cy, "width": work.w, "height": work.h},
                            scale=round(work.w / placed_w, 4), sizing=sz["reason"])
                if sliced:
                    b = sp["borders"]
                    info["borders"] = {"left": b["left"] + cx, "right": b["right"] + (size - cx - work.w),
                                       "top": b["top"] + cy, "bottom": b["bottom"] + (size - cy - work.h)}
                    info["slice"] = b
                if sz.get("oversized"):
                    c["review"].append("%s is over %d px and could not be sliced" % (name, max_size))
            if sp["purpose"] == "main":
                whole_scale = info["scale"]
            if sp["purpose"] == "main" and el.get("parts") and not c.get("_partsDone"):
                # An object exported in parts ships only as its parts (Ali): the whole is not written when they redraw it.
                c["_partsDone"] = True
                if _parts(c, el, entry, result, info["scale"], min_size, max_size, load, write_sprite, folder(el)):
                    entry["decisions"].append("whole not exported: its parts replace it")
                    c["review"] = [r for r in c["review"] if "could not be sliced" not in r]
                    entry["review"] = c["review"]
                    continue
            digest, status = write_sprite(rel, final)
            s = {"name": name, "file": rel, "element": el["name"], "layerId": el["layerId"], "path": el["path"], "role": c["role"],
                 "purpose": sp["purpose"], "kind": sp["kind"], "axis": sp["axis"], "placed": sp["placed"], "status": status,
                 "digest": digest, "mergeWith": el.get("mergeWith"), "notes": entry["decisions"]}
            if el.get("gradient") and sp["purpose"] == "frame":
                s["gradient"] = el["gradient"]
            if el.get("upright") and sp["purpose"] == "main" and img is not sp["img"]:
                # The sprite is drawn straight: placed is its upright rect in PSD pixels; Unity rotates it by
                # `rotation` (degrees, clockwise on screen) around `pivot` (the smart object's centre).
                u = el["upright"]
                hsc = (el.get("hires") or {}).get("scale") or 1.0
                s["rotation"] = u["rotation"]
                s["pivot"] = [round(u["centre"][0], 2), round(u["centre"][1], 2)]
                s["placed"] = {"x": hx, "y": hy, "width": int(round(img.w / hsc)), "height": int(round(img.h / hsc))}
                c["uprightPlaced"] = (s["placed"], u["centre"])
            s.update(info)
            result["sprites"].append(s)
            entry["sprites"].append(name)
        if not c.get("_partsDone"):
            _parts(c, el, entry, result, whole_scale, min_size, max_size, load, write_sprite, folder(el))
        _states(c, el, entry, result, min_size, max_size, load, write_sprite, folder(el))
        result["elements"].append(entry)
        if entry["review"]:
            result["warnings"].extend("%s: %s" % (el["name"], r) for r in entry["review"])
        log("  %-32s %s" % (el["name"], entry["treatment"]))


def _inner_span(img, axis, at, along):
    """Inner area along one axis: from `along` (inside the uniform centre) outwards while the pixels keep the
    centre's colour (within 40) and stay opaque. Strokes, shadows and bevels outside it are left out, so a
    variant with a stroke or shadow lines up with one without (Ali: they grow outward)."""
    n = img.w if axis == "x" else img.h

    def px(i):
        o = ((at * img.w + i) if axis == "x" else (i * img.w + at)) * 4
        return img.px[o:o + 4]

    ref = px(along)

    def same(q):
        return q[3] >= 200 and max(abs(q[0] - ref[0]), abs(q[1] - ref[1]), abs(q[2] - ref[2])) <= 40

    lo = along
    while lo - 1 >= 0 and same(px(lo - 1)):
        lo -= 1
    hi = along + 1
    while hi < n and same(px(hi)):
        hi += 1
    return lo, hi


def _body_span(c, sp, axis, img, b, lo_k):
    """The layer's own body (its bounds without effects: strokes, shadows and glows grow outward from it) along
    one axis, in the sliced sprite's pixels. (None, None) when the layer has no such bounds."""
    bb = c["el"].get("shapeBounds")
    p = sp.get("placed") or c["placed"]
    if not bb or not bb.get("width") or not bb.get("height"):
        return None, None
    pos, size = ("x", "width") if axis == "x" else ("y", "height")
    s0 = max(0, bb[pos] - p[pos])
    e0 = min(p[size], bb[pos] + bb[size] - p[pos])
    removed = p[size] - (img.w if axis == "x" else img.h)   # the collapsed centre run (0 on an unsliced axis)
    if e0 > b[lo_k]:
        e0 -= removed
    if e0 - s0 < 1:
        return None, None
    return s0, e0


def _expand(img, axis, start, width, extra):
    if extra <= 0:
        return img
    if axis == "x":
        return I.expand_cols(img, start, width, img.w + extra)
    return I.expand_rows(img, start, width, img.h + extra)


def _canvas_targets(cands, keep=4):
    """sameCanvas families (Ali, Daily Login): swappable sliced pieces share one canvas. Every member's body
    (its layer bounds without effects; else the fill inside strokes and shadows) is lined up at the same place; along a sliced axis the uniform
    centre is stretched (lossless) so every inner area has the same length and the slice lines are shared;
    strokes and shadows grow outward. Returns {id(sprite): (img, borders, note)}."""
    fams, c_of = {}, {}
    for c in cands:
        fam = c["el"].get("canvasFamily")
        if not fam or c["dup"]:
            continue
        for sp in c["sprites"]:
            if sp["purpose"] == "main" and sp["borders"] is not None:
                fams.setdefault(fam, []).append(sp)
                c_of[id(sp)] = c
    out = {}
    for fam, members in fams.items():
        if len(members) < 2:
            continue
        info = []
        for sp in members:
            img, b = sp["img"], sp["borders"]
            m = {"sp": sp, "img": img, "b": dict(b)}
            for axis, (lo_k, hi_k, n) in (("x", ("left", "right", img.w)), ("y", ("top", "bottom", img.h))):
                sliced = bool(b[lo_k] or b[hi_k])
                cw = n - b[lo_k] - b[hi_k] if sliced else 0
                along = b[lo_k] + cw // 2 if sliced else n // 2
                o_lo, o_hi = ("top", "bottom") if axis == "x" else ("left", "right")
                o_n = img.h if axis == "x" else img.w
                o_sl = bool(b[o_lo] or b[o_hi])
                at = b[o_lo] + (o_n - b[o_lo] - b[o_hi]) // 2 if o_sl else o_n // 2
                s0, e0 = _body_span(c_of[id(sp)], sp, axis, img, b, lo_k)
                if s0 is None:
                    s0, e0 = _inner_span(img, axis, at, along)
                m[axis] = {"sliced": sliced, "a": b[lo_k], "cw": cw, "n": n, "s": s0, "len": e0 - s0}
            info.append(m)
        for axis in ("x", "y"):
            sliced = all(m[axis]["sliced"] for m in info)
            S = max(m[axis]["s"] for m in info)
            Lb = max(m[axis]["len"] for m in info)
            for m in info:
                d = m[axis]
                d["o"] = S - d["s"]
                d["d"] = (Lb - d["len"]) if sliced else 0
            if sliced:
                A = max(d["a"] + d["o"] for d in (m[axis] for m in info))
                Z = min(d["a"] + d["cw"] + d["o"] + d["d"] for d in (m[axis] for m in info))
                if Z - A < keep:
                    for m in info:
                        m[axis]["d"] += keep - (Z - A)
                    Z = A + keep
            W = max(m[axis]["o"] + m[axis]["n"] + m[axis]["d"] for m in info)
            for m in info:
                m[axis]["W"] = W
                if sliced:
                    m[axis]["A"], m[axis]["Z"] = A, Z
        for m in info:
            img = m["img"]
            for axis in ("x", "y"):
                d = m[axis]
                if d["sliced"] and d["d"]:
                    img = _expand(img, axis, d["a"], d["cw"], d["d"])
            X, Y = m["x"], m["y"]
            canvas = P.Img(X["W"], Y["W"])
            canvas.paste(img, X["o"], Y["o"])
            nb = {"left": X["A"] if "A" in X else 0, "right": X["W"] - X["Z"] if "Z" in X else 0,
                  "top": Y["A"] if "A" in Y else 0, "bottom": Y["W"] - Y["Z"] if "Z" in Y else 0}
            note = ("family %s (sameCanvas): inner area lined up at %d,%d (%dx%d) on a %dx%d canvas; strokes and shadows "
                    "grow outward" % (fam, max(x["s"] + x["o"] for x in [X]), Y["s"] + Y["o"], X["len"] + X["d"], Y["len"] + Y["d"],
                                      X["W"], Y["W"]))
            out[id(m["sp"])] = (canvas, nb, note)
    return out


def _family_targets(cands, min_size, max_size):
    """sameSize families: family -> (texture size, content side), from the biggest placed member (FUZIO 9.8 sizing)."""
    biggest = {}
    for c in cands:
        fam = c["el"].get("family")
        if not fam or c["dup"]:
            continue
        # A member with headroom (it zooms bigger) asks for its headroom size: the family follows it.
        k = float(c["el"].get("headroomScale") or 1.0)
        for sp in c["sprites"]:
            if sp["purpose"] == "main" and sp["borders"] is None and not sp["background"]:
                biggest[fam] = max(biggest.get(fam, 0), int(round(max(sp["img"].w, sp["img"].h) * k)))
    # Copies placed bigger than their source count too: the biggest slot any member uses.
    for c in cands:
        fam = c["el"].get("family")
        if fam and c["dup"] and fam in biggest:
            biggest[fam] = max(biggest[fam], c["placed"]["width"], c["placed"]["height"]) if not c["dup"].get("angle") else biggest[fam]
    out = {}
    for fam, side in biggest.items():
        sz = decide_size(side, False, False, min_size, max_size, max_size)
        out[fam] = (sz["size"], max(1, int(round(side * min(1.0, sz["scale"])))))
    return out


def _states(c, el, entry, result, min_size, max_size, load, write_sprite, folder="Sprites/"):
    """States hidden inside a smart object (rule `states`), each its own sprite at the element's resolution."""
    for st in el.get("states") or []:
        img, x, y = load(st["key"])
        if img is None:
            entry["review"].append("state %s: render failed or empty" % st["state"])
            continue
        scale = st["soRender"].get("scale") or 1.0
        # A state is an animation sprite (a glow that pulses): never shrunk below its own pixels; padded up to
        # the next power of two instead (downscaled only beyond maxSize).
        side = max(img.w, img.h)
        size = max(min_size, 1 << max(0, int(math.ceil(math.log(side, 2)))))
        sz = {"reason": "state: padded up to %d px, never shrunk" % size}
        work = img
        if size > max_size:
            f = max_size / float(side)
            work = P.resize_area(img, max(1, int(round(img.w * f))), max(1, int(round(img.h * f))))
            size = max_size
        final = P.pad_center(work, size, size)
        rel = folder + "%s.png" % st["name"]
        digest, status = write_sprite(rel, final)
        placed = {"x": x, "y": y, "width": int(round(img.w / scale)), "height": int(round(img.h / scale))}
        result["sprites"].append({
            "name": st["name"], "file": rel, "element": el["name"], "parent": el["name"], "layerId": el["layerId"],
            "path": el["path"] + " > state " + st["state"], "role": "state", "purpose": "state", "kind": "state",
            "placed": placed, "status": status, "digest": digest, "texture": {"width": size, "height": size},
            "content": {"x": (size - work.w) // 2, "y": (size - work.h) // 2, "width": work.w, "height": work.h},
            "scale": round(work.w / float(placed["width"] or 1), 4), "sizing": sz["reason"]})
        entry["sprites"].append(st["name"])
        entry["decisions"].append("state %s exported as %s" % (st["state"], st["name"]))


def _parts(c, el, entry, result, whole_scale, min_size, max_size, load, write_sprite, folder="Sprites/"):
    """14.3: the parts drawn together must look like the whole (SSIM >= 0.70) to be used."""
    parts = []
    for part in el.get("parts") or []:
        img, x, y = load(part["key"])
        if img is not None:
            parts.append((part, img, x, y))
    if len(parts) < 2:
        return False
    whole, wx, wy = c["img"], c["placed"]["x"], c["placed"]["y"]
    canvas = P.Img(whole.w, whole.h)
    for part, img, x, y in reversed(parts):   # bottom part first
        I.alpha_over(canvas, img, x - wx, y - wy)
    sim = I.ssim(canvas, whole)
    if sim < PART_MIN_SSIM:
        entry["decisions"].append("parts dropped: drawn together they do not look like the whole (SSIM %.3f < 0.70)" % sim)
        return False
    entry["decisions"].append("split into %d parts; drawn together SSIM %.3f" % (len(parts), sim))
    # Every part at exactly the whole's scale (they animate together, so their sharpness must match): the
    # texture is the next power of two that holds the scaled part (padded, never shrunk further), unless
    # that exceeds maxSize, in which case all parts share the smaller common scale.
    common = whole_scale
    biggest = max(max(img.w, img.h) for _, img, _, _ in parts)
    if biggest * common > max_size:
        common = max_size / float(biggest)
        entry["review"].append("parts scaled to %.3f (not the whole's %.3f) so the biggest fits %d px" % (common, whole_scale, max_size))
    for part, img, x, y in parts:
        work = img
        if common < 0.999:
            work = P.resize_area(img, max(1, int(round(img.w * common))), max(1, int(round(img.h * common))))
        size = max(min_size, 1 << max(0, int(math.ceil(math.log(max(work.w, work.h), 2)))))
        final = P.pad_center(work, size, size)
        rel = folder + "%s.png" % part["name"]
        digest, status = write_sprite(rel, final)
        result["sprites"].append({
            "name": part["name"], "file": rel, "element": el["name"], "parent": el["name"], "layerId": el["layerId"],
            "path": el["path"] + " > " + part["partName"], "role": "part", "purpose": "part", "kind": "part",
            "placed": {"x": x, "y": y, "width": img.w, "height": img.h}, "status": status, "digest": digest,
            "texture": {"width": size, "height": size},
            "content": {"x": (size - work.w) // 2, "y": (size - work.h) // 2, "width": work.w, "height": work.h},
            "scale": round(work.w / float(img.w), 4)})
        entry["sprites"].append(part["name"])
    return True
