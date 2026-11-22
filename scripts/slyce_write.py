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
            if sliced and sp["purpose"] == "main" and canvas_fams.get(el.get("canvasFamily")):
                img, sp = _onto_family_canvas(img, sp, canvas_fams[el["canvasFamily"]], entry, el["canvasFamily"])
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
                    if side < 0.9 * target:
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
                sz = decide_size(max(img.w, img.h), sliced, c["keepDetail"], min_size, max_size, cap, soft)
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


def _body_top(img):
    """First row of the solid body (alpha >= 200 on a tenth of the row): soft shadows and glows are left out."""
    need = max(1, img.w // 10)
    for y in range(img.h):
        if sum(1 for v in img.row(y)[3::4] if v >= 200) >= need:
            return y
    return 0


def _body_left(img):
    need = max(1, img.h // 10)
    for x in range(img.w):
        if sum(1 for v in img.channel_col(x, 3) if v >= 200) >= need:
            return x
    return 0


def _canvas_targets(cands):
    """sameCanvas families (Ali, Daily Login): swappable sliced pieces share one canvas. Along a sliced axis the
    slice lines line up (each border padded out to the family's widest); along an unsliced axis the solid bodies
    line up (shadows and strokes grow outward), and every member is padded to the family's biggest extent."""
    fams = {}
    for c in cands:
        fam = c["el"].get("canvasFamily")
        if not fam or c["dup"]:
            continue
        for sp in c["sprites"]:
            if sp["purpose"] == "main" and sp["borders"] is not None:
                b, img = sp["borders"], sp["img"]
                f = fams.setdefault(fam, {"left": 0, "right": 0, "top": 0, "bottom": 0, "midW": 0, "midH": 0,
                                          "bodyTop": 0, "belowTop": 0, "bodyLeft": 0, "rightOfLeft": 0, "members": 0})
                f["members"] += 1
                for k in ("left", "right", "top", "bottom"):
                    f[k] = max(f[k], b[k])
                f["midW"] = max(f["midW"], img.w - b["left"] - b["right"])
                f["midH"] = max(f["midH"], img.h - b["top"] - b["bottom"])
                if b["top"] == 0 and b["bottom"] == 0:   # rows not sliced: line up the bodies' tops
                    t = _body_top(img)
                    f["bodyTop"], f["belowTop"] = max(f["bodyTop"], t), max(f["belowTop"], img.h - t)
                if b["left"] == 0 and b["right"] == 0:
                    l_ = _body_left(img)
                    f["bodyLeft"], f["rightOfLeft"] = max(f["bodyLeft"], l_), max(f["rightOfLeft"], img.w - l_)
    return dict((k, v) for k, v in fams.items() if v["members"] > 1)


def _onto_family_canvas(img, sp, f, entry, fam):
    b = sp["borders"]
    if b["top"] == 0 and b["bottom"] == 0:
        t = _body_top(img)
        pt = f["bodyTop"] - t
        pb = f["bodyTop"] + f["belowTop"] - pt - img.h
    else:
        pt = f["top"] - b["top"]
        pb = f["bottom"] - b["bottom"] + (f["midH"] - (img.h - b["top"] - b["bottom"]))
    if b["left"] == 0 and b["right"] == 0:
        l_ = _body_left(img)
        pl = f["bodyLeft"] - l_
        pr = f["bodyLeft"] + f["rightOfLeft"] - pl - img.w
    else:
        pl = f["left"] - b["left"]
        pr = f["right"] - b["right"] + (f["midW"] - (img.w - b["left"] - b["right"]))
    pl, pt, pr, pb = max(0, pl), max(0, pt), max(0, pr), max(0, pb)
    if not (pl or pt or pr or pb):
        return img, sp
    out = P.Img(img.w + pl + pr, img.h + pt + pb)
    out.paste(img, pl, pt)
    nb = {"left": b["left"] + pl if b["left"] or b["right"] else 0, "right": b["right"] + pr if b["left"] or b["right"] else 0,
          "top": b["top"] + pt if b["top"] or b["bottom"] else 0, "bottom": b["bottom"] + pb if b["top"] or b["bottom"] else 0}
    entry["decisions"].append("family %s (sameCanvas): padded %d/%d/%d/%d px (left/top/right/bottom) to the family's canvas" % (fam, pl, pt, pr, pb))
    return out, dict(sp, img=out, borders=nb)


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
