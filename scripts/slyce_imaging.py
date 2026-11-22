"""SLYCE image metrics and drawing, ported from FUZIO Imaging (Similarity, Compose, Transform2D, FlatShape,
Uniformity, SliceDetector). Pure Python on slyce_png.Img (RGBA, rows top-down, y down)."""
import math

import slyce_fast as F
from slyce_png import Img, resize_area

GREY = (128, 128, 128)
VISIBLE_ALPHA = 8     # FUZIO Coverage.VisibleAlpha
OPAQUE_ALPHA = 250    # FUZIO Coverage.OpaqueAlpha


# ---------- comparison (FUZIO Similarity: both images flattened over mid-grey) ----------

def flatten(img, bg=GREY):
    px = img.px
    out = []
    for i in range(0, len(px), 4):
        a = px[i + 3] / 255.0
        out.append((px[i] * a + bg[0] * (1 - a), px[i + 1] * a + bg[1] * (1 - a), px[i + 2] * a + bg[2] * (1 - a)))
    return out


def mae(a, b):
    """Mean absolute error over R, G, B in 0-255 units."""
    if F.ENABLED:
        return F.mae(a, b)
    fa, fb = flatten(a), flatten(b)
    if not fa:
        return 0.0
    s = 0.0
    for p, q in zip(fa, fb):
        s += abs(p[0] - q[0]) + abs(p[1] - q[1]) + abs(p[2] - q[2])
    return s / (len(fa) * 3.0)


def ssim(a, b, max_side=384):
    """Mean SSIM on luminance over 8x8 windows with a 4 px step. Larger images are compared on an
    area-averaged copy of at most max_side px (pure Python speed)."""
    if max(a.w, a.h) > max_side:
        f = max_side / float(max(a.w, a.h))
        nw, nh = max(1, int(round(a.w * f))), max(1, int(round(a.h * f)))
        a, b = resize_area(a, nw, nh), resize_area(b, nw, nh)
    if F.ENABLED:
        return F.ssim_luma(a, b)
    w, h = a.w, a.h
    la = [0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2] for p in flatten(a)]
    lb = [0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2] for p in flatten(b)]
    c1, c2 = 6.5025, 58.5225
    win = min(8, w, h)
    if win <= 1:
        return 1.0
    step = max(1, win // 2)
    n = win * win
    total = 0.0
    count = 0
    for y in range(0, h - win + 1, step):
        for x in range(0, w - win + 1, step):
            idx = [(y + j) * w + x + i for j in range(win) for i in range(win)]
            ma = sum(la[k] for k in idx) / n
            mb = sum(lb[k] for k in idx) / n
            va = vb = cov = 0.0
            for k in idx:
                da, db = la[k] - ma, lb[k] - mb
                va += da * da
                vb += db * db
                cov += da * db
            va /= n - 1
            vb /= n - 1
            cov /= n - 1
            total += ((2 * ma * mb + c1) * (2 * cov + c2)) / ((ma * ma + mb * mb + c1) * (va + vb + c2))
            count += 1
    return total / count if count else 1.0


def passes(rebuilt, render, min_ssim=0.98, max_mae=3.0):
    """FUZIO element thresholds (13): SSIM >= 0.98 and MAE <= 3."""
    m = mae(rebuilt, render)
    if m > max_mae:
        return False, 0.0, m
    s = ssim(rebuilt, render)
    return s >= min_ssim, s, m


# ---------- composing ----------

def alpha_over(dst, src, ox, oy):
    """Draws src over dst at (ox, oy), straight alpha."""
    for y in range(src.h):
        dy = oy + y
        if dy < 0 or dy >= dst.h:
            continue
        for x in range(src.w):
            dx = ox + x
            if dx < 0 or dx >= dst.w:
                continue
            s = (y * src.w + x) * 4
            sa = src.px[s + 3]
            if sa == 0:
                continue
            d = (dy * dst.w + dx) * 4
            if sa == 255:
                dst.px[d:d + 4] = src.px[s:s + 4]
                continue
            fa = sa / 255.0
            fd = dst.px[d + 3] / 255.0 * (1 - fa)
            oa = fa + fd
            for c in range(3):
                dst.px[d + c] = min(255, int(round((src.px[s + c] * fa + dst.px[d + c] * fd) / oa)))
            dst.px[d + 3] = min(255, int(round(oa * 255)))


# ---------- flat shapes (FUZIO FlatShape, spec 9.10) ----------

def is_flat(img, tol=6):
    """(True, (r,g,b,a)) when every visible pixel is one colour within tol (0.2% stray pixels allowed) and at
    least half of them share the inside alpha (a feathered edge or soft shadow is not flat)."""
    px = img.px
    best = -1
    ba = 0
    for i in range(3, len(px), 4):
        if px[i] > ba:
            ba = px[i]
            best = i - 3
    if best < 0:
        return False, None
    col = (px[best], px[best + 1], px[best + 2], px[best + 3])
    visible = off = solid = 0
    for i in range(0, len(px), 4):
        a = px[i + 3]
        if a < VISIBLE_ALPHA:
            continue
        visible += 1
        if abs(px[i] - col[0]) > tol or abs(px[i + 1] - col[1]) > tol or abs(px[i + 2] - col[2]) > tol:
            off += 1
        if abs(a - col[3]) <= tol:
            solid += 1
    return (visible > 0 and off <= visible * 0.002 and solid >= visible / 2.0), col


def draw_shape(shape, w, h, radius, col):
    """The shape filling w x h, anti-aliased at pixel centres, as the tinted kit sprite draws it."""
    out = Img(w, h)
    hw, hh = w / 2.0, h / 2.0
    radius = min(radius or 0, hw, hh)
    for y in range(h):
        py = y + 0.5
        for x in range(w):
            px = x + 0.5
            if shape == "ellipse":
                nx, ny = (px - hw) / hw, (py - hh) / hh
                ln = math.sqrt(nx * nx + ny * ny)
                edge = -min(hw, hh) if ln <= 0 else (ln - 1) * math.sqrt(nx * nx * hw * hw + ny * ny * hh * hh) / ln
                cover = min(1.0, max(0.0, 0.5 - edge))
            elif shape == "roundedRect" and radius > 0:
                dx = max(0.0, abs(px - hw) - (hw - radius))
                dy = max(0.0, abs(py - hh) - (hh - radius))
                cover = min(1.0, max(0.0, radius + 0.5 - math.sqrt(dx * dx + dy * dy)))
            else:
                cover = 1.0
            if cover > 0:
                o = (y * w + x) * 4
                out.px[o:o + 4] = bytes((col[0], col[1], col[2], int(round(col[3] * cover))))
    return out


# ---------- uniformity and slicing (FUZIO Uniformity, SliceDetector, SliceDecider; spec 9.2, 9.3) ----------

FAINT_ALPHA = 64      # pixels this faint (anti-aliased edges, soft shadow tails) ...
FAINT_TOL = 24        # ... may differ this much in alpha and still count as uniform (invisible)


LENIENT = [True]   # header_split turns it off: a soft shadow's fading body must not read as a header + flat body


def _px_diff(p, q):
    """Largest channel difference, with faint pixels compared premultiplied and their alpha tolerance folded
    in: a soft edge whose alpha wobbles by up to FAINT_TOL must not block a slice (the difference is invisible)."""
    if p[3] == 0 and q[3] == 0:
        return 0
    if not LENIENT[0]:
        return max(abs(p[0] - q[0]), abs(p[1] - q[1]), abs(p[2] - q[2]), abs(p[3] - q[3]))
    if p[3] <= FAINT_ALPHA and q[3] <= FAINT_ALPHA:
        da = abs(p[3] - q[3])
        dc = max(abs(p[c] * p[3] - q[c] * q[3]) for c in range(3)) // 255
        return max(dc, 0 if da <= FAINT_TOL else da)
    # Alpha gets 2 more: an anti-aliased edge wobbles by a few levels along a straight run (invisible).
    return max(abs(p[0] - q[0]), abs(p[1] - q[1]), abs(p[2] - q[2]), abs(p[3] - q[3]) - 2)


def _px_equal(p, q, tol):
    # Fully transparent pixels match whatever their colour.
    return _px_diff(p, q) <= tol


def _line_equal(a, b, tol):
    if a == b:
        return True
    for i in range(0, len(a), 4):
        if not _px_equal(a[i:i + 4], b[i:i + 4], tol):
            return False
    return True


def _line_step(a, b):
    m = 0
    for i in range(0, len(a), 4):
        d = _px_diff(a[i:i + 4], b[i:i + 4])
        if d > m:
            m = d
    return m


def longest_run(n, line, tol):
    """Longest run of lines equal to the run's first line, strictly inside (not touching either edge)."""
    best = (0, 0)
    start = 1
    while start <= n - 2:
        ref = line(start)
        end = start
        while end + 1 <= n - 2 and _line_equal(ref, line(end + 1), tol):
            end += 1
        if end - start + 1 > best[1]:
            best = (start, end - start + 1)
        start = end + 1
    return best


def is_flat_run(n, line, run, tol):
    """Flat fill, not 8-bit banding in a gradient: covers a quarter of the axis, or both ends meet a sharp
    change (more than 3 x tol) such as an edge or a stroke."""
    start, length = run
    if length <= 0:
        return False
    if length * 4 >= n:
        return True
    sharp = 3 * tol
    return _line_step(line(start - 1), line(start)) > sharp and _line_step(line(start + length - 1), line(start + length)) > sharp


def collapse_cols(img, start, length, keep):
    keep_cols = list(range(0, start)) + [start] * keep + list(range(start + length, img.w))
    out = Img(len(keep_cols), img.h)
    for y in range(img.h):
        row = img.row(y)
        line = bytearray(len(keep_cols) * 4)
        for i, x in enumerate(keep_cols):
            line[i * 4:i * 4 + 4] = row[x * 4:x * 4 + 4]
        out.px[y * out.w * 4:(y + 1) * out.w * 4] = line
    return out


def collapse_rows(img, start, length, keep):
    keep_rows = list(range(0, start)) + [start] * keep + list(range(start + length, img.h))
    out = Img(img.w, len(keep_rows))
    for i, y in enumerate(keep_rows):
        out.px[i * img.w * 4:(i + 1) * img.w * 4] = img.row(y)
    return out


def expand_cols(img, centre, keep, target_w):
    extra = target_w - img.w
    cols = list(range(0, centre)) + [centre] * (keep + extra) + list(range(centre + keep, img.w))
    out = Img(target_w, img.h)
    for y in range(img.h):
        row = img.row(y)
        line = bytearray(target_w * 4)
        for i, x in enumerate(cols):
            line[i * 4:i * 4 + 4] = row[x * 4:x * 4 + 4]
        out.px[y * target_w * 4:(y + 1) * target_w * 4] = line
    return out


def expand_rows(img, centre, keep, target_h):
    extra = target_h - img.h
    rows = list(range(0, centre)) + [centre] * (keep + extra) + list(range(centre + keep, img.h))
    out = Img(img.w, target_h)
    for i, y in enumerate(rows):
        out.px[i * img.w * 4:(i + 1) * img.w * 4] = img.row(y)
    return out


def max_difference(a, b):
    m = 0
    pa, pb = a.px, b.px
    for i in range(0, len(pa), 4):
        d = _px_diff(pa[i:i + 4], pb[i:i + 4])
        if d > m:
            m = d
    return m


def decide_slice(img, tol=2, keep=4):
    """FUZIO SliceDecider: collapse the longest flat interior column run and row run to `keep` px; both axes
    give a 9-slice, one a 3-slice. Used only when expanding back reproduces the image within tol.
    Returns dict(kind, img, borders{left,right,top,bottom}, axis, columnRun, rowRun, maxError)."""
    res = {"kind": None, "img": img, "borders": None, "axis": None, "maxError": 0}
    cols = longest_run(img.w, img.col, tol)
    rows = longest_run(img.h, img.row, tol)
    res["columnRun"], res["rowRun"] = cols[1], rows[1]
    # An axis is sliced only when it saves at least `keep` px (a 5 px run collapsed to 4 gains nothing).
    cx = cols[1] >= 2 * keep and is_flat_run(img.w, img.col, cols, tol)
    cy = rows[1] >= 2 * keep and is_flat_run(img.h, img.row, rows, tol)
    if not cx and not cy:
        return res
    cur = img
    b = {"left": 0, "right": 0, "top": 0, "bottom": 0}
    if cx:
        cur = collapse_cols(cur, cols[0], cols[1], keep)
        b["left"], b["right"] = cols[0], img.w - (cols[0] + cols[1])
    if cy:
        cur = collapse_rows(cur, rows[0], rows[1], keep)
        b["top"], b["bottom"] = rows[0], img.h - (rows[0] + rows[1])
    exp = cur
    if cy:
        exp = expand_rows(exp, rows[0], keep, img.h)
    if cx:
        exp = expand_cols(exp, cols[0], keep, img.w)
    res["maxError"] = max_difference(exp, img)
    if res["maxError"] > tol:
        return res
    res.update(img=cur, borders=b, kind="sliced9" if (cx and cy) else "sliced3")
    if not (cx and cy):
        res["axis"] = "x" if cx else "y"
    return res


def header_split(img, tol=2, keep=4, min_run_fraction=0.25):
    """FUZIO FindHeaderSplit (9.7): the smallest top band below which the rest has a collapsible column run of
    at least a quarter of the width. 0 when none is needed or possible (band at most half the image)."""
    LENIENT[0] = False
    try:
        return _header_split(img, tol, keep, min_run_fraction)
    finally:
        LENIENT[0] = True


def _header_split(img, tol, keep, min_run_fraction):
    min_run = max(keep + 1, int(math.ceil(img.w * min_run_fraction)))

    def body_has_run(band):
        body = img.crop(0, band, img.w, img.h - band)
        return longest_run(body.w, body.col, tol)[1] >= min_run

    if body_has_run(0):
        return 0
    hi = img.h // 2
    if hi < 1 or not body_has_run(hi):
        return 0
    lo = 1
    while lo < hi:
        mid = (lo + hi) // 2
        if body_has_run(mid):
            hi = mid
        else:
            lo = mid + 1
    return lo


# ---------- shape moments and transforms (FUZIO Transform2D, spec 10.3) ----------

def moments(img):
    """Alpha mass, centroid relative to the image centre (y down), principal axis in degrees, anisotropy 0..1."""
    w, h = img.w, img.h
    a = img.alpha()
    m = sx = sy = 0.0
    for y in range(h):
        row = y * w
        for x in range(w):
            v = a[row + x]
            if v:
                f = v / 255.0
                m += f
                sx += f * (x + 0.5)
                sy += f * (y + 0.5)
    if m <= 0:
        return 0.0, 0.0, 0.0, 0.0, 0.0
    mx, my = sx / m, sy / m
    u20 = u02 = u11 = 0.0
    for y in range(h):
        row = y * w
        dy = y + 0.5 - my
        for x in range(w):
            v = a[row + x]
            if v:
                f = v / 255.0
                dx = x + 0.5 - mx
                u20 += f * dx * dx
                u02 += f * dy * dy
                u11 += f * dx * dy
    axis = 0.5 * math.degrees(math.atan2(2 * u11, u20 - u02))
    spread = u20 + u02
    aniso = 0.0 if spread <= 0 else math.sqrt((u20 - u02) ** 2 + 4 * u11 * u11) / spread
    return m, mx - w / 2.0, my - h / 2.0, axis, aniso


def radial_profile(img):
    """Rotation- and scale-invariant shape signature: alpha mass in 8 rings around the centroid, out to twice
    the radius of gyration, as fractions of the mass."""
    m, cx, cy, _, _ = moments(img)
    bins = [0.0] * 8
    if m <= 0:
        return bins
    w, h = img.w, img.h
    a = img.alpha()
    mx, my = cx + w / 2.0, cy + h / 2.0
    g = 0.0
    pts = []
    for y in range(h):
        for x in range(w):
            v = a[y * w + x]
            if v:
                dx, dy = x + 0.5 - mx, y + 0.5 - my
                d2 = dx * dx + dy * dy
                g += v / 255.0 * d2
                pts.append((v / 255.0, d2))
    radius = max(0.5, math.sqrt(g / m) * 2)
    for f, d2 in pts:
        bins[min(7, int(math.sqrt(d2) / radius * 8))] += f / m
    return bins


def draw_transformed(src, angle, scale, flip, w, h, ox, oy):
    """Draws src mirrored (flip), scaled and turned by `angle` degrees (clockwise on screen, y down) about its
    centre, placed at the canvas centre plus (ox, oy). Bilinear on premultiplied alpha."""
    if F.ENABLED:
        return F.draw_transformed(src, angle, scale, flip, w, h, ox, oy, Img)
    out = Img(w, h)
    rad = -math.radians(angle)
    cos, sin = math.cos(rad), math.sin(rad)
    tcx, tcy = w / 2.0 + ox, h / 2.0 + oy
    scx, scy = src.w / 2.0, src.h / 2.0
    sp, sw, sh = src.px, src.w, src.h
    for y in range(h):
        for x in range(w):
            dx, dy = x + 0.5 - tcx, y + 0.5 - tcy
            ux = (dx * cos - dy * sin) / scale
            uy = (dx * sin + dy * cos) / scale
            if flip:
                ux = -ux
            fx_, fy_ = ux + scx - 0.5, uy + scy - 0.5
            if fx_ < -1 or fy_ < -1 or fx_ > sw or fy_ > sh:
                continue
            x0, y0 = int(math.floor(fx_)), int(math.floor(fy_))
            fx, fy = fx_ - x0, fy_ - y0
            r = g = b = al = 0.0
            for j in (0, 1):
                py = y0 + j
                if py < 0 or py >= sh:
                    continue
                wy = (1 - fy) if j == 0 else fy
                for i in (0, 1):
                    qx = x0 + i
                    if qx < 0 or qx >= sw:
                        continue
                    k = (py * sw + qx) * 4
                    pa = sp[k + 3] * wy * ((1 - fx) if i == 0 else fx)
                    if pa:
                        r += sp[k] * pa
                        g += sp[k + 1] * pa
                        b += sp[k + 2] * pa
                        al += pa
            if al > 0.001:
                o = (y * w + x) * 4
                out.px[o] = min(255, int(round(r / al)))
                out.px[o + 1] = min(255, int(round(g / al)))
                out.px[o + 2] = min(255, int(round(b / al)))
                out.px[o + 3] = min(255, int(round(al)))
    return out
