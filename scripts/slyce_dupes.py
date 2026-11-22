"""SLYCE duplicates (FUZIO 10.3): exact copies in 8 orientations, transformed copies (moments / principal
axis), tinted copies of grey sources, plus SLYCE's merges of part sets, gradient boxes and sliced boxes.
"""
import json
import math
import os
import re
import time

import slyce_fast as F
import slyce_imaging as I
import slyce_png as P

from slyce_shapes import DUP_AUTO, DUP_REVIEW, KEEP, MAX_DUP_COLOUR_MAE, PROFILE_DISTANCE, _hex, mean_colour  # noqa: F401


# ---------- duplicates (FUZIO DuplicateFinder, DuplicateMatcher; spec 10.3) ----------

def _luma(px, k):
    return 0.299 * px[k] + 0.587 * px[k + 1] + 0.114 * px[k + 2]


def _alpha_error(a, b):
    if F.ENABLED:
        return F.alpha_error(a, b)
    s = 0.0
    pa, pb = a.px, b.px
    for k in range(0, len(pa), 4):
        s += abs(pa[k + 3] - pb[k + 3]) + abs(_luma(pa, k) * pa[k + 3] - _luma(pb, k) * pb[k + 3]) / 255.0
    return s / (len(pa) // 4 or 1)


def _exact_equal(a, b):
    off = 0
    limit = len(a.px) // 4 // 1000
    pa, pb = a.px, b.px
    for k in range(0, len(pa), 4):
        if pa[k + 3] == 0 and pb[k + 3] == 0:
            continue
        if (abs(pa[k + 3] - pb[k + 3]) > 2 or abs(pa[k] - pb[k]) > 2 or abs(pa[k + 1] - pb[k + 1]) > 2
                or abs(pa[k + 2] - pb[k + 2]) > 2):
            off += 1
            if off > limit:
                return False
    return True


def _offset(sc, tc, angle, scale, flip):
    x = (-sc[0] if flip else sc[0]) * scale
    y = sc[1] * scale
    r = math.radians(angle)
    return tc[0] - (x * math.cos(r) - y * math.sin(r)), tc[1] - (x * math.sin(r) + y * math.cos(r))


def _tint_for(img, target):
    """For a white/grey image, the tint that gives the target's average colour; None when it is coloured."""
    ir = ig = ib = ia = tr = tg = tb = ta = 0.0
    coloured = visible = 0
    p, q = img.px, target.px
    for k in range(0, len(p), 4):
        if p[k + 3] >= 128:
            visible += 1
            if max(p[k], p[k + 1], p[k + 2]) - min(p[k], p[k + 1], p[k + 2]) > 40:
                coloured += 1
        w = p[k + 3] / 255.0
        ir += p[k] * w
        ig += p[k + 1] * w
        ib += p[k + 2] * w
        ia += p[k + 3]
        v = q[k + 3] / 255.0
        tr += q[k] * v
        tg += q[k + 1] * v
        tb += q[k + 2] * v
        ta += q[k + 3]
    if visible == 0 or coloured > visible / 10 or ia <= 0 or ta <= 0:
        return None
    iw, tw = ia / 255.0, ta / 255.0
    rgb = tuple(int(max(0, min(255, t / tw / max(1.0, i / iw) * 255))) for t, i in ((tr, ir), (tg, ig), (tb, ib)))
    return rgb + (int(max(0, min(255, ta / ia * 255))),)


def _tinted(img, t):
    out = P.Img(img.w, img.h)
    for k in range(0, len(img.px), 4):
        out.px[k] = img.px[k] * t[0] // 255
        out.px[k + 1] = img.px[k + 1] * t[1] // 255
        out.px[k + 2] = img.px[k + 2] * t[2] // 255
        out.px[k + 3] = img.px[k + 3] * t[3] // 255
    return out


def match_duplicate(src, tgt, min_sim, ms_src=None, ms_tgt=None):
    """Is tgt a copy of the (larger) src: exact in 8 orientations, or resized / freely rotated / mirrored
    (scale from alpha mass, angle from principal axes or every 10 deg, refined +-3 deg), or a tinted grey copy."""
    ms, scx, scy, saxis, saniso = ms_src or I.moments(src)
    mt, tcx, tcy, taxis, taniso = ms_tgt or I.moments(tgt)
    if ms <= 0 or mt <= 0:
        return None
    scale = math.sqrt(mt / ms)
    if scale > 1.02:
        return None
    scale = min(1.0, scale)
    if scale > 0.99:
        for label, v in P.orientations(src):
            if v.w == tgt.w and v.h == tgt.h and _exact_equal(v, tgt):
                return {"match": "exact", "transform": label, "angle": 0, "scale": 1.0, "flip": False, "similarity": 1.0}
    quick = min(1.0, 48.0 / max(tgt.w, tgt.h))
    qtw, qth = max(4, int(round(tgt.w * quick))), max(4, int(round(tgt.h * quick)))
    ssw = min(src.w, max(4, int(round(src.w * quick / scale))))
    ssh = min(src.h, max(4, int(round(src.h * quick / scale))))
    small_t = P.resize_area(tgt, qtw, qth)
    small_s = P.resize_area(src, ssw, ssh)
    small_scale = scale * quick * src.w / float(ssw)
    cands = []
    for flip in (False, True):
        s = -saxis if flip else saxis
        if saniso >= 0.15 and taniso >= 0.15:
            cands += [(taxis - s, flip), (taxis - s + 180, flip)]
        else:
            cands += [(a, flip) for a in range(0, 360, 10)]

    def err(angle, sc_small, sc_full, flip):
        ox, oy = _offset((scx, scy), (tcx, tcy), angle, sc_full, flip)
        return _alpha_error(I.draw_transformed(small_s, angle, sc_small, flip, qtw, qth, ox * quick, oy * quick), small_t)

    best = None
    for angle, flip in cands:
        e = err(angle, small_scale, scale, flip)
        if best is None or e < best[0]:
            best = (e, angle, scale, flip)
    _, coarse, _, bflip = best
    a = coarse - 3
    while a <= coarse + 3.001:
        for f in (0.98, 1.0, 1.02):
            s = min(1.0, scale * f)
            e = err(a, small_scale * s / scale, s, bflip)
            if e < best[0]:
                best = (e, a, s, bflip)
        a += 0.5
    _, angle, bscale, flip = best
    ox, oy = _offset((scx, scy), (tcx, tcy), angle, bscale, flip)
    drawn = I.draw_transformed(src, angle, bscale, flip, tgt.w, tgt.h, ox, oy)
    sim = I.ssim(drawn, tgt)
    res = {"match": "transformed", "angle": round(angle % 360, 1), "scale": round(bscale, 4), "flip": flip,
           "transform": ("mirrored, " if flip else "") + "rotated %.1f deg" % (angle % 360), "similarity": round(sim, 4)}
    tint = _tint_for(drawn, tgt)
    if tint and (tint[0] < 250 or tint[1] < 250 or tint[2] < 250):
        ts = I.ssim(_tinted(drawn, tint), tgt)
        if ts > sim:
            res.update(similarity=round(ts, 4), match="tinted", tint=_hex(tint))
            drawn = _tinted(drawn, tint)
    # SSIM sees brightness only: colour variants (a grey locked charm and its coloured version) are never
    # merged (10.3 rule 4), so the colour must match too.
    res["colourMae"] = round(I.mae(drawn, tgt), 2)
    if res["colourMae"] > MAX_DUP_COLOUR_MAE:
        return None
    return res if res["similarity"] >= min_sim else None


def _expand_like(sp, w, h):
    """Draws a sliced sprite at w x h the way a sliced Image stretches its centre (repeating the run)."""
    img, b = sp["img"], sp["borders"]
    if sp["kind"] == "sliced9" or sp.get("axis") == "x":
        if w < b["left"] + b["right"] + 1:
            return None
        img = I.expand_cols(img, b["left"], KEEP, w)
    if sp["kind"] == "sliced9" or sp.get("axis") == "y":
        if h < b["top"] + b["bottom"] + 1:
            return None
        img = I.expand_rows(img, b["top"], KEEP, h)
    return img if (img.w, img.h) == (w, h) else None


def _dedupe(cands, rules=None):
    # Copies of one smart object exported in parts (a title on two artboards): the largest keeps its parts,
    # the others reuse them (10.3 rule 1, transform from the PSD).
    groups = {}
    loose = []
    for c in cands:
        so = c["el"].get("smartObject") or {}
        if c["el"].get("parts") and so.get("placedId"):
            groups.setdefault(so["placedId"], []).append(c)
        elif c["el"].get("parts"):
            loose.append(c)
    # Separate smart objects or groups that look the same (a title placed on each artboard of one feature):
    # merged when their whole renders match (10.3 rule 3), so their parts export once.
    keys = list(groups.keys())
    for i, ka in enumerate(keys):
        for kb in keys[i + 1:]:
            if not groups.get(ka) or not groups.get(kb):
                continue
            a0, b0 = groups[ka][0], groups[kb][0]
            big, small = (a0, b0) if a0["img"].w * a0["img"].h >= b0["img"].w * b0["img"].h else (b0, a0)
            if match_duplicate(big["img"], small["img"], DUP_AUTO):
                groups[ka] = groups[ka] + groups.pop(kb)
    for group in groups.values():
        if len(group) < 2:
            continue
        # The copy whose parts the planner named keeps them; otherwise the largest.
        src = max(group, key=lambda c: (sum(1 for q in c["el"]["parts"] if q.get("renamed")), c["img"].w * c["img"].h))
        a = src["el"].get("smartObject") or {}
        for c in group:
            if c is src:
                continue
            b = c["el"].get("smartObject") or {}
            angle = ((b.get("rotation") or 0) - (a.get("rotation") or 0)) % 360
            c["dup"] = {"source": src["el"]["name"], "match": "smartObject (parts)", "angle": round(angle, 2),
                        "scale": round((b.get("scaleX") or 1) / float(a.get("scaleX") or 1), 4),
                        "flip": bool(a.get("flipped")) != bool(b.get("flipped")), "similarity": 1.0,
                        "transform": "reuses the parts of %s" % src["el"]["name"], "review": False}
    """10.3: whole images of art and box elements that copy a larger one reuse its sprite. Copies of one
    smart object are always compared; other pairs are shortlisted by their radial shape profile.
    SLYCE also merges sliced sprites whose collapsed pixels are identical."""
    items = []
    for c in cands:
        if c["treatment"] == "whole" and len(c["sprites"]) == 1 and c["role"] in ("art", "box", "composed") \
                and not c["el"].get("parts") and not c["sprites"][0]["background"]:
            img = c["sprites"][0]["img"]
            c["_moments"] = I.moments(img)
            c["_mass"] = c["_moments"][0]
            c["_profile"] = I.radial_profile(img)
            c["_colour"], c["_grey"] = mean_colour(img)
            vis = [v for v in img.alpha() if v >= 8]
            c["_soft"] = bool(vis) and sum(1 for v in vis if v < 200) / float(len(vis)) >= 0.45
            items.append(c)
    sources = []
    for c in sorted(items, key=lambda c: -c["_mass"]):
        so = (c["el"].get("smartObject") or {}).get("placedId")
        found = None
        for s in sources:
            same = so is not None and so == (s["el"].get("smartObject") or {}).get("placedId")
            if same:
                # 10.3 rule 1: copies of one smart object are certain; the transform comes from the PSD's matrices.
                a, b = s["el"]["smartObject"], c["el"]["smartObject"]
                flip = bool(a.get("flipped")) != bool(b.get("flipped"))
                base = 0 if s["el"].get("upright") else (a.get("rotation") or 0)   # an upright source is drawn straight
                angle = ((b.get("rotation") or 0) - base) % 360
                found = (s, {"match": "smartObject", "scale": round((b.get("scaleX") or 1) / float(a.get("scaleX") or 1), 4),
                             "angle": round(angle, 2), "flip": flip,
                             "transform": ("mirrored, " if flip else "") + "rotated %.1f deg" % angle, "similarity": 1.0})
                break
            if c["el"].get("noReuse") or s["el"].get("noReuse") or c["_soft"] or s["_soft"]:
                continue   # rule noReuse; soft blurred blobs fool SSIM, so they only merge as smart object copies
            if not same:
                if sum(abs(a - b) for a, b in zip(s["_profile"], c["_profile"])) > PROFILE_DISTANCE:
                    continue
                # Colour gate: a copy has about the same mean colour, unless the source is grey (a tint).
                if not s["_grey"] and max(abs(a - b) for a, b in zip(s["_colour"], c["_colour"])) > 3 * MAX_DUP_COLOUR_MAE:
                    continue
                if s["_mass"] < c["_mass"] * 0.98:
                    continue
            m = match_duplicate(s["sprites"][0]["img"], c["sprites"][0]["img"], DUP_REVIEW, s["_moments"], c["_moments"])
            if m:
                found = (s, m)
                break
        if not found:
            sources.append(c)
            continue
        s, m = found
        m["source"] = s["el"]["name"]
        m["review"] = m["similarity"] < DUP_AUTO
        c["dup"] = m
    # Identical gradient boxes (same frame pixels and borders, same Gradient2 keys) share one frame sprite.
    gb = {}
    for c in cands:
        if c["dup"] or c["treatment"] != "gradientBox":
            continue
        g = (c.get("gradient") or {}).get("fill", {}).get("gradient2")
        fr = c["sprites"][0] if c["sprites"] else None
        key = (json.dumps(g, sort_keys=True), fr["img"].digest() if fr else None, json.dumps(fr["borders"], sort_keys=True) if fr else None)
        if key in gb:
            c["dup"] = {"source": gb[key], "match": "gradient box", "transform": "id", "scale": 1.0, "similarity": 1.0, "review": False}
        else:
            gb[key] = c["el"]["name"]
    seen = {}
    sliced = []
    for c in cands:
        if c["dup"] or c["treatment"] not in ("sliced9", "sliced3") or len(c["sprites"]) != 1:
            continue
        sp = c["sprites"][0]
        key = (sp["img"].digest(), json.dumps(sp["borders"], sort_keys=True))
        if key in seen:
            c["dup"] = {"source": seen[key], "match": "sliced", "transform": "id", "scale": 1.0, "similarity": 1.0, "review": False}
        else:
            seen[key] = c["el"]["name"]
            sliced.append(c)
    # A whole box that a sliced sprite draws exactly at its size (the same pill at another width) reuses it.
    for c in cands:
        if c["dup"] or c["treatment"] != "whole" or len(c["sprites"]) != 1 or c["role"] not in ("box", "art"):
            continue
        img = c["sprites"][0]["img"]
        for s in sliced:
            drawn = _expand_like(s["sprites"][0], img.w, img.h)
            if drawn is not None and I.max_difference(drawn, img) <= 8:
                c["dup"] = {"source": s["el"]["name"], "match": "sliced", "transform": "drawn sliced at %dx%d" % (img.w, img.h),
                            "scale": 1.0, "similarity": 1.0, "review": False}
                break
    # Rule `reuse`: the person decided these look the same; use the best transform found, whatever its score.
    by_name = dict((c["el"]["name"].lower(), c) for c in cands)
    for c in cands:
        target = c["el"].get("reuse")
        if not target or c["dup"]:
            continue
        src = by_name.get(target.lower())
        if src is not None and src["treatment"] == c["treatment"] == "gradient":
            continue   # both rebuilt as gradients in Unity: nothing to reuse, nothing to warn about
        if src is None or src is c or not src["sprites"] or src["dup"]:
            c["review"].append("rule reuse names '%s', which is not an exported sprite of this PSD" % target)
            continue
        a, b = src["sprites"][0]["img"], c["sprites"][0]["img"] if c["sprites"] else None
        if b is None:
            continue
        if a.w * a.h < b.w * b.h:
            c["review"].append("rule reuse: %s is smaller than this element; it is drawn scaled up" % target)
        m = None
        ssp = src["sprites"][0]
        if ssp["borders"] is not None:
            # A sliced target is drawn sliced at this element's own size, upright: no rotation search.
            drawn = _expand_like(ssp, b.w, b.h)
            m = {"match": "sliced", "transform": "drawn sliced at %dx%d" % (b.w, b.h), "scale": 1.0, "angle": 0, "flip": False,
                 "similarity": round(I.ssim(drawn, b), 4) if drawn is not None else None}
        elif a.w * a.h >= b.w * b.h:
            # Prefer the plain match (same orientation, resized) when it is as good as any turned one.
            plain = I.ssim(P.resize_area(a, b.w, b.h), b) if (a.w, a.h) != (b.w, b.h) else I.ssim(a, b)
            m = match_duplicate(a, b, 0.0)
            if m is None or plain >= m["similarity"] - 0.005:
                m = {"match": "resized", "transform": "id", "scale": round(b.w / float(a.w), 4), "angle": 0, "flip": False,
                     "similarity": round(plain, 4)}
        m = m or {"match": "rule", "transform": "id", "scale": round(b.w / float(a.w), 4), "angle": 0, "flip": False, "similarity": None}
        m.update(source=src["el"]["name"], review=False, rule=True)
        m["match"] = "rule (" + m["match"] + ")"
        c["dup"] = m
