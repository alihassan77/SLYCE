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

TOL = 2               # uniformity tolerance per channel (9.2)
KEEP = 4              # px kept from a collapsed run (minCollapsedPx)
FLAT_TOL = 6          # flat colour tolerance (9.10)
DUP_AUTO = 0.985      # duplicate reused without review (10.3)
DUP_REVIEW = 0.96     # duplicate reused but listed for review
PROFILE_DISTANCE = 0.35
MAX_DUP_COLOUR_MAE = 12.0   # mean colour difference allowed for a duplicate (SSIM alone ignores colour)
PART_MIN_SSIM = 0.70  # parts are kept only when drawn together they look like the whole (14.3)
TITLE_MAX = 512       # the main title image is never larger (12.3)


# ---------- silhouette fitting (SLYCE: pixel layers without a live shape) ----------

def _row_insets(img, thr):
    w = img.w
    a = img.alpha()
    out = []
    for y in range(img.h):
        line = a[y * w:(y + 1) * w]
        l = 0
        while l < w and line[l] < thr:
            l += 1
        if l == w:
            out.append(None)
            continue
        r = 0
        while line[w - 1 - r] < thr:
            r += 1
        out.append((l, r, min(line[l:w - r]) >= thr))
    return out


def _ideal_rounded(w, h, r, y):
    if r <= 0:
        return 0
    yy = y + 0.5
    if yy < r:
        d = r - yy
    elif yy > h - r:
        d = yy - (h - r)
    else:
        return 0
    if d >= r:
        return int(math.ceil(r - 0.5))
    return max(0, int(math.ceil(r - math.sqrt(r * r - d * d) - 0.5)))


def _ideal_ellipse(w, h, y):
    a, b = w / 2.0, h / 2.0
    t = (y + 0.5 - b) / b
    if abs(t) >= 1:
        return int(a)
    return max(0, int(math.ceil(a - a * math.sqrt(1 - t * t) - 0.5)))


def _fit_error(insets, ideal):
    errs = []
    for y, row in enumerate(insets):
        e = ideal(y)
        errs.append(abs(row[0] - e))
        errs.append(abs(row[1] - e))
    return max(errs), sum(errs) / float(len(errs))


def fit_shape(img, thr=128):
    """('rect', 0) / ('roundedRect', r) / ('ellipse', None) when the alpha mask is that shape within pixel
    error, else None. Photoshop's rounded corners are not exact arcs, so the radius fits within ~2 px."""
    insets = _row_insets(img, thr)
    while insets and insets[0] is None:
        insets.pop(0)
    while insets and insets[-1] is None:
        insets.pop()
    if len(insets) < 2 or any(r is None or not r[2] for r in insets):
        return None
    ml = min(r[0] for r in insets)
    mr = min(r[1] for r in insets)
    insets = [(r[0] - ml, r[1] - mr) for r in insets]
    w, h = img.w - ml - mr, len(insets)
    if w < 2:
        return None
    e = _fit_error(insets, lambda y: 0)
    if e[0] <= 1 and e[1] <= 0.2:
        return "rect", 0
    rmax = min(w, h) / 2.0
    best = None
    r = 1.0
    while r <= rmax + 0.01:
        e = _fit_error(insets, lambda y, r=r: _ideal_rounded(w, h, r, y))
        if best is None or e[1] < best[1][1]:
            best = (r, e)
        r += 0.5 if r < 40 else 1.0
    ell = _fit_error(insets, lambda y: _ideal_ellipse(w, h, y))
    if ell[0] <= 2 and ell[1] <= 0.5 and (best is None or ell[1] <= best[1][1]):
        return "ellipse", None
    if best and best[1][0] <= max(2, 0.05 * best[0]) and best[1][1] <= 0.6:
        return "roundedRect", round(best[0], 1)
    return None


def soft_fx(img):
    """SLYCE: glow-like pixels (soft glows, bokeh, feathered shadows): almost nothing opaque and a low mean
    alpha, or mostly semi-transparent with a mean alpha below 170. Solid art with a soft shadow is kept."""
    vis = [v for v in img.alpha() if v >= 8]
    if not vis:
        return True
    n = float(len(vis))
    strong = sum(1 for v in vis if v >= 230) / n
    partial = sum(1 for v in vis if v < 200) / n
    mean = sum(vis) / n
    return (strong < 0.08 and mean < 140) or (partial >= 0.45 and mean < 170)


# ---------- gradient box (FUZIO GradientMapper, GradientBoxCheck; spec 9.4) ----------

def _hex(c):
    return "#%02X%02X%02X%02X" % tuple(c)


def _parse(h):
    if not h or len(h) != 7 or h[0] != "#":
        return None
    v = int(h[1:], 16)
    return (v >> 16) & 255, (v >> 8) & 255, v & 255


def _mix(a, b, u, t):
    return [t] + [int(round(a[i] + (b[i] - a[i]) * u)) for i in range(1, 5)]


def map_gradient(g, fill_color=None):
    """Photoshop gradient -> Gradient2 keys. Linear or reflected at 90 / -90 / 0 / 180 degrees (+-1) only."""
    if not g:
        return {"supported": False, "reason": "no gradient"}
    gtype = g.get("type") or "linear"
    if gtype not in ("linear", "reflected"):
        return {"supported": False, "reason": "gradient type '%s' (a radial look: soft circles in FUZIO 9.5)" % gtype}
    if (g.get("form") or "customStops") != "customStops":
        return {"supported": False, "reason": "gradient form '%s'" % g.get("form")}
    if (g.get("blendMode") or "normal") != "normal":
        return {"supported": False, "reason": "overlay blend mode '%s'" % g.get("blendMode")}
    angle = float(g.get("angle", 90))
    while angle > 180:
        angle -= 360
    while angle <= -180:
        angle += 360

    def near(t):
        return abs(angle - t) <= 1

    if near(90):
        gt, flip = "Vertical", False
    elif near(-90):
        gt, flip = "Vertical", True
    elif near(0):
        gt, flip = "Horizontal", False
    elif near(180) or near(-180):
        gt, flip = "Horizontal", True
    else:
        return {"supported": False, "reason": "angle %.4g is not a fixed orientation" % angle}
    colors = []
    for i, st in enumerate(sorted(g.get("colorStops") or [], key=lambda s: s["location"])):
        c = _parse(st.get("color"))
        if c is None:
            return {"supported": False, "reason": "a colour stop uses the foreground/background colour"}
        key = [st["location"], c[0], c[1], c[2], 255]
        if i > 0 and abs(st.get("midpoint", 0.5) - 0.5) >= 0.005:
            prev = colors[-1]
            colors.append(_mix(prev, key, 0.5, prev[0] + (key[0] - prev[0]) * st["midpoint"]))
        colors.append(key)
    if not colors:
        return {"supported": False, "reason": "no colour stops"}
    alphas = []
    for i, st in enumerate(sorted(g.get("alphaStops") or [], key=lambda s: s["location"])):
        key = [st["location"], 255, 255, 255, int(round(max(0, min(100, st["opacity"])) * 2.55))]
        if i > 0 and abs(st.get("midpoint", 0.5) - 0.5) >= 0.005:
            prev = alphas[-1]
            alphas.append(_mix(prev, key, 0.5, prev[0] + (key[0] - prev[0]) * st["midpoint"]))
        alphas.append(key)
    if not alphas:
        alphas = [[0, 255, 255, 255, 255]]
    opacity = float(g.get("opacity", 100))
    if opacity < 99.5:
        f = _parse(fill_color) if fill_color else None
        if f is None:
            return {"supported": False, "reason": "overlay opacity %g%% without a flat fill colour" % opacity}
        o = opacity / 100.0
        colors = [[k[0]] + [int(round(f[i] + (k[i + 1] - f[i]) * o)) for i in range(3)] + [255] for k in colors]

    def flipk(keys):
        return sorted([[1 - k[0]] + k[1:] for k in keys], key=lambda k: k[0])

    def reflect(keys):
        out = []
        for k in keys:
            out.append([0.5 - k[0] / 2.0] + k[1:])
            if k[0] > 0:
                out.append([0.5 + k[0] / 2.0] + k[1:])
        return sorted(out, key=lambda k: k[0])

    if g.get("reverse"):
        colors, alphas = flipk(colors), flipk(alphas)
    if gtype == "reflected":
        colors, alphas = reflect(colors), reflect(alphas)
    if flip:
        colors, alphas = flipk(colors), flipk(alphas)
    merged = 0
    for keys in (colors, alphas):
        while len(keys) > 8:
            best = min(range(1, len(keys)), key=lambda i: keys[i][0] - keys[i - 1][0])
            a, b = keys[best - 1], keys[best]
            keys[best - 1] = _mix(a, b, 0.5, (a[0] + b[0]) / 2.0)
            del keys[best]
            merged += 1
    if len(colors) == 1:
        colors.append([1] + colors[0][1:])
    if len(alphas) == 1:
        alphas.append([1] + alphas[0][1:])
    return {"supported": True, "gradientType": gt, "zoom": max(0.1, min(10.0, float(g.get("scale", 100)) / 100.0)),
            "colorKeys": [{"time": round(k[0], 4), "color": "#%02X%02X%02X" % (k[1], k[2], k[3])} for k in colors],
            "alphaKeys": [{"time": round(k[0], 4), "alpha": k[4]} for k in alphas],
            "mergedKeys": merged, "_c": colors, "_a": alphas}


def _sample(keys, t):
    if t <= keys[0][0]:
        return keys[0]
    for i in range(1, len(keys)):
        if t > keys[i][0]:
            continue
        a, b = keys[i - 1], keys[i]
        span = b[0] - a[0]
        u = 1 if span <= 0 else (t - a[0]) / span
        return _mix(a, b, u, t)
    return keys[-1]


def rebuild_gradient_box(m, full, fx, fy, mask, mx, my, frame, frx, fry, shape):
    """The gradient drawn inside the fill mask (Gradient2: Vertical bottom 0 -> top 1, Horizontal left -> right),
    with the frame on top; all in the full render's frame (document positions)."""
    out = P.Img(full.w, full.h)
    vertical = m["gradientType"] == "Vertical"
    sx, sy, sw, sh = shape["x"], shape["y"], max(1, shape["width"]), max(1, shape["height"])
    for y in range(full.h):
        dy = fy + y + 0.5
        my_ = int(math.floor(dy - my))
        if my_ < 0 or my_ >= mask.h:
            continue
        for x in range(full.w):
            dx = fx + x + 0.5
            mx_ = int(math.floor(dx - mx))
            if mx_ < 0 or mx_ >= mask.w:
                continue
            cov = mask.px[(my_ * mask.w + mx_) * 4 + 3]
            if cov == 0:
                continue
            t = (sy + sh - dy) / sh if vertical else (dx - sx) / sw
            t = min(1.0, max(0.0, 0.5 + (t - 0.5) / m["zoom"]))
            c = _sample(m["_c"], t)
            a = _sample(m["_a"], t)[4]
            o = (y * full.w + x) * 4
            out.px[o:o + 4] = bytes((c[1], c[2], c[3], int(round(a * cov / 255.0))))
    if frame is not None:
        I.alpha_over(out, frame, frx - fx, fry - fy)
    return out


# ---------- sizing (FUZIO Sizing; spec 9.8) ----------

def decide_size(max_side, sliced, keep_detail, min_size, max_size, cap, soft=False):
    if max_side <= 0:
        return {"size": min_size, "scale": 1.0, "reason": "empty"}
    lg = math.log(max_side, 2)
    lower = int(math.floor(lg))
    boundary = math.sqrt(2) * 2 ** lower
    band = abs(max_side - boundary) / boundary <= 0.05
    if soft and not sliced:
        # Soft shadows and glows tolerate a little more blur: never pushed up to the next size (Ali).
        exp = lower
        reason = "soft (shadow/glow): rounded down, 2^floor(log2(%d))" % max_side
    elif band and keep_detail:
        exp = lower + 1
        reason = "within 5%% of the rounding boundary %d px; rounded up for text or thin strokes" % round(boundary)
    else:
        exp = int(round(lg))
        reason = "2^round(log2(%d))" % max_side
    size = max(min_size, min(max_size, 2 ** max(0, exp)))
    if size < max_side and max_side <= 64 and not sliced:
        # Tiny sprites (a bulb, a small glow) lose too much when shrunk: go up a size instead (Ali).
        size = 2 ** int(math.ceil(lg))
        reason += "; small sprite: rounded up to %d instead of shrinking" % size
    if size < max_side and sliced:
        while size < max_side:
            size *= 2
        reason += "; sliced sprites are not resampled, so rounded up to %d" % size
    if size > cap and not sliced:
        size = cap
        reason += "; capped at %d px" % cap
    scale = 1.0
    if max_side > size:
        scale = size / float(max_side)
        reason += "; downscaled to fit"
    elif size > max_side:
        reason += "; padded, not upscaled"
    return {"size": size, "scale": scale, "reason": reason, "oversized": max_side > max_size and not sliced}


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


# ---------- duplicates (FUZIO DuplicateFinder, DuplicateMatcher; spec 10.3) ----------

def _luma(px, k):
    return 0.299 * px[k] + 0.587 * px[k + 1] + 0.114 * px[k + 2]


def _alpha_error(a, b):
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


def mean_colour(img):
    """Alpha-weighted mean RGB and the share of strongly coloured pixels (for the cheap colour gate)."""
    px = img.px
    r = g = b = wsum = 0.0
    coloured = visible = 0
    for k in range(0, len(px), 4):
        a = px[k + 3]
        if a < 128:
            continue
        visible += 1
        if max(px[k], px[k + 1], px[k + 2]) - min(px[k], px[k + 1], px[k + 2]) > 40:
            coloured += 1
        r += px[k]
        g += px[k + 1]
        b += px[k + 2]
        wsum += 1
    if wsum == 0:
        return (0, 0, 0), False
    return (r / wsum, g / wsum, b / wsum), coloured <= visible / 10


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


def process(plan, renders, out_dir, rules, log=print, clean=False):
    """renders: key -> {'file', 'bounds'} or {'error'}; extra renders are '<key>:<variant>'."""
    min_size = int(rules.get("minSize"))
    max_size = int(rules.get("maxSize"))
    prev = _load_prev(os.path.join(out_dir, "manifest.json"))
    doc = plan["document"]
    canvases = [Canvas(a["rect"]) for a in plan.get("artboards") or []]
    doc_canvas = Canvas({"x": 0, "y": 0, "width": doc["width"], "height": doc["height"]})
    result = {"elements": [], "sprites": [], "duplicates": [], "kit": [], "gradients": [],
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
            if s["kind"] == "sliced9":
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
            if not el.get("noRoundEnds"):
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


def round_both_ends(sl, c):
    """A fill bar is often cut by a mask in the PSD: rounded at one end, straight at the other. Unity needs it
    rounded at both ends (sliced or filled along a rounded bar background), so the rounded cap is mirrored
    onto the cut end. Works on a 3-slice result; returns it unchanged when both ends already match."""
    axis, b, img = sl["axis"], sl["borders"], sl["img"]
    lo_key, hi_key = ("left", "right") if axis == "x" else ("top", "bottom")
    lo_cover, hi_cover = _edge_cover(img, axis, False), _edge_cover(img, axis, True)
    if hi_cover >= 0.9 and lo_cover < 0.6:
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
    c["decisions"].append("fill bar cut straight at the %s end (a mask in the PSD): the rounded end is mirrored so both ends are round" % (
        {"x": {"lo": "left", "hi": "right"}, "y": {"lo": "top", "hi": "bottom"}}[axis][cut]))
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
    # Sources first, so a copy can use its source's upright rect.
    cands = sorted(cands, key=lambda c: 1 if c["dup"] else 0)
    for c in cands:
        el = c["el"]
        entry = {"name": el["name"], "layerId": el["layerId"], "path": el["path"], "role": c["role"], "treatment": c["treatment"],
                 "placed": c["placed"], "decisions": c["decisions"] + el.get("notes", []), "review": c["review"], "sprites": []}
        if c.get("kit"):
            result["kit"].append(c["kit"])
            result["skipped"].append({"layerId": el["layerId"], "name": el["name"], "path": el["path"], "kind": el["kind"],
                                      "category": "kit", "reason": "flat %s %s: tinted %s" % (c["kit"]["color"], c["kit"]["shape"], c["kit"]["sprite"]),
                                      "bounds": el.get("bounds")})
        if c.get("gradient"):
            if c["dup"] and c["gradient"].get("frame"):
                c["gradient"]["frame"] = c["dup"]["source"] + "-Frame"   # merged gradient boxes share the source's frame
                c["gradient"]["duplicateOf"] = c["dup"]["source"]
            result["gradients"].append(c["gradient"])
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
