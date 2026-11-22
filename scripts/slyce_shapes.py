"""SLYCE process helpers: thresholds, silhouette fitting, soft-fx detection, gradient mapping and
rebuilding, and square power-of-two sizing (FUZIO 9.4, 9.8, 9.10).
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


def plain_gradient(img, max_side=128):
    """SLYCE (Ali, Daily Login): a background that is only a linear gradient (no artwork) is rebuilt in Unity with a
    gradient component, not exported as a 2048 texture. Returns {'gradientType', 'colorKeys', 'alphaKeys', 'ssim', 'mae'}
    when a vertical or horizontal gradient redraws the image (9.10 thresholds), else None."""
    a = img.alpha()
    if sum(1 for v in a if v >= 250) < 0.98 * len(a):
        return None
    k = min(1.0, float(max_side) / max(img.w, img.h))
    small = P.resize_area(img, max(1, int(round(img.w * k))), max(1, int(round(img.h * k))))
    best = None
    for vertical in (True, False):
        n = small.h if vertical else small.w
        m = small.w if vertical else small.h
        prof = []
        for i in range(n):
            line = small.row(i) if vertical else small.col(i)
            prof.append([sum(line[j * 4 + ch] for j in range(m)) / float(m) for ch in range(4)])
        out = P.Img(small.w, small.h)
        px = out.px
        for y in range(small.h):
            for x in range(small.w):
                c = prof[y if vertical else x]
                o = (y * small.w + x) * 4
                for ch in range(4):
                    px[o + ch] = int(round(c[ch]))
        ok, ss, mm = I.passes(out, small)
        if ok and (best is None or mm < best[2]):
            best = (vertical, prof, mm, ss)
    if best is None:
        return None
    vertical, prof, mm, ss = best
    keys = _simplify(prof, 2.0)
    n = len(prof) - 1 or 1
    # Unity's Gradient keys run bottom (0) to top (1) for a vertical Gradient2; PSD rows run top to bottom.
    ck, ak = [], []
    for i in keys:
        t = (1.0 - i / float(n)) if vertical else i / float(n)
        c = prof[i]
        ck.append({"time": round(t, 3), "color": _hex([int(round(v)) for v in c[:3]] + [255])[:7]})
        ak.append({"time": round(t, 3), "alpha": int(round(c[3]))})
    ck.sort(key=lambda d: d["time"])
    ak.sort(key=lambda d: d["time"])
    if all(d["alpha"] >= 254 for d in ak):
        ak = [{"time": 0, "alpha": 255}, {"time": 1, "alpha": 255}]
    return {"gradientType": "Vertical" if vertical else "Horizontal", "colorKeys": ck, "alphaKeys": ak,
            "ssim": round(ss, 4), "mae": round(mm, 2)}


def _simplify(prof, tol):
    """Indices of the fewest stops whose linear interpolation stays within tol of the colour profile (Douglas-Peucker)."""
    keep = {0, len(prof) - 1}

    def rec(a, b):
        worst, wi = 0.0, None
        for i in range(a + 1, b):
            u = (i - a) / float(b - a)
            d = max(abs(prof[i][ch] - (prof[a][ch] + (prof[b][ch] - prof[a][ch]) * u)) for ch in range(4))
            if d > worst:
                worst, wi = d, i
        if wi is not None and worst > tol:
            keep.add(wi)
            rec(a, wi)
            rec(wi, b)

    rec(0, len(prof) - 1)
    return sorted(keep)
