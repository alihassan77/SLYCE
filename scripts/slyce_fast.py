"""SLYCE fast paths: numpy versions of the hottest pixel loops (transformed drawing for duplicates, alpha error,
area resize, MAE / SSIM, PNG unfiltering). Used automatically when numpy is installed (`install_skill.py` installs
it); without numpy SLYCE runs the pure-Python versions, which give the same sprites, only slower.

Every function mirrors its pure-Python twin operation for operation, so results match (floating point sums may
differ in the last bits, which no threshold notices; the regression test `slyce.py test` checks it).
"""
import math
import os

try:
    import numpy as np
except ImportError:   # pragma: no cover - the pure-Python path is used instead
    np = None

ENABLED = np is not None and not os.environ.get("SLYCE_PURE")   # SLYCE_PURE=1 forces the pure-Python path


def _arr(img):
    return np.frombuffer(bytes(img.px), dtype=np.uint8).reshape(img.h, img.w, 4)


def _img(arr, Img):
    out = Img(arr.shape[1], arr.shape[0])
    out.px[:] = np.ascontiguousarray(arr, dtype=np.uint8).tobytes()
    return out


def draw_transformed(src, angle, scale, flip, w, h, ox, oy, Img):
    """See slyce_imaging.draw_transformed: bilinear on premultiplied alpha, same arithmetic per pixel."""
    rad = -math.radians(angle)
    cos, sin = math.cos(rad), math.sin(rad)
    tcx, tcy = w / 2.0 + ox, h / 2.0 + oy
    scx, scy = src.w / 2.0, src.h / 2.0
    sw, sh = src.w, src.h
    S = _arr(src).astype(np.float64)
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float64)
    dx, dy = xs + 0.5 - tcx, ys + 0.5 - tcy
    ux = (dx * cos - dy * sin) / scale
    uy = (dx * sin + dy * cos) / scale
    if flip:
        ux = -ux
    fx_, fy_ = ux + scx - 0.5, uy + scy - 0.5
    valid = ~((fx_ < -1) | (fy_ < -1) | (fx_ > sw) | (fy_ > sh))
    x0, y0 = np.floor(fx_), np.floor(fy_)
    fx, fy = fx_ - x0, fy_ - y0
    x0, y0 = x0.astype(np.int64), y0.astype(np.int64)
    r = np.zeros((h, w))
    g = np.zeros((h, w))
    b = np.zeros((h, w))
    al = np.zeros((h, w))
    for j in (0, 1):
        py = y0 + j
        oky = valid & (py >= 0) & (py < sh)
        wy = (1 - fy) if j == 0 else fy
        pyc = np.clip(py, 0, sh - 1)
        for i in (0, 1):
            qx = x0 + i
            ok = oky & (qx >= 0) & (qx < sw)
            wx = (1 - fx) if i == 0 else fx
            s = S[pyc, np.clip(qx, 0, sw - 1)]
            pa = np.where(ok, s[..., 3] * wy * wx, 0.0)
            r += s[..., 0] * pa
            g += s[..., 1] * pa
            b += s[..., 2] * pa
            al += pa
    out = np.zeros((h, w, 4), dtype=np.uint8)
    m = al > 0.001
    safe = np.where(m, al, 1.0)
    for c, v in enumerate((r, g, b)):
        out[..., c] = np.where(m, np.minimum(255, np.round(v / safe)), 0)
    out[..., 3] = np.where(m, np.minimum(255, np.round(al)), 0)
    return _img(out, Img)


def alpha_error(a, b):
    """See slyce_dupes._alpha_error."""
    A, B = _arr(a).astype(np.float64), _arr(b).astype(np.float64)
    la = 0.299 * A[..., 0] + 0.587 * A[..., 1] + 0.114 * A[..., 2]
    lb = 0.299 * B[..., 0] + 0.587 * B[..., 1] + 0.114 * B[..., 2]
    s = np.abs(A[..., 3] - B[..., 3]) + np.abs(la * A[..., 3] - lb * B[..., 3]) / 255.0
    n = a.w * a.h or 1
    return float(s.sum()) / n


def _weights(src, dst):
    W = np.zeros((dst, src))
    scale = src / float(dst)
    for i in range(dst):
        a_ = i * scale
        b_ = a_ + scale
        j = int(a_)
        while j < b_ and j < src:
            lo, hi = max(a_, j), min(b_, j + 1)
            if hi > lo:
                W[i, j] = (hi - lo) / scale
            j += 1
    return W


def resize_area(img, nw, nh, Img):
    """See slyce_png.resize_area: area averaging on premultiplied alpha."""
    A = _arr(img).astype(np.float64)
    f = A[..., 3:4] / 255.0
    pre = np.concatenate([A[..., :3] * f, A[..., 3:4]], axis=2)
    tmp = np.einsum("xj,yjc->yxc", _weights(img.w, nw), pre)
    res = np.einsum("yj,jxc->yxc", _weights(img.h, nh), tmp)
    a = res[..., 3]
    m = a > 0.5
    k = np.where(m, 255.0 / np.where(m, a, 1.0), 0.0)
    out = np.zeros((nh, nw, 4), dtype=np.uint8)
    for c in range(3):
        out[..., c] = np.where(m, np.minimum(255, np.floor(res[..., c] * k + 0.5)), 0)
    out[..., 3] = np.where(m, np.minimum(255, np.floor(a + 0.5)), 0)
    return _img(out, Img)


def _flat(img, bg=(128, 128, 128)):
    A = _arr(img).astype(np.float64)
    a = A[..., 3:4] / 255.0
    return A[..., :3] * a + np.array(bg, dtype=np.float64) * (1 - a)


def mae(a, b):
    """See slyce_imaging.mae."""
    if a.w * a.h == 0:
        return 0.0
    return float(np.abs(_flat(a) - _flat(b)).sum()) / (a.w * a.h * 3.0)


def ssim_luma(a, b):
    """See slyce_imaging.ssim (after its resize): mean SSIM over 8x8 windows, 4 px step."""
    w, h = a.w, a.h
    k = np.array([0.299, 0.587, 0.114])
    la = (_flat(a) * k).sum(axis=2)
    lb = (_flat(b) * k).sum(axis=2)
    c1, c2 = 6.5025, 58.5225
    win = min(8, w, h)
    if win <= 1:
        return 1.0
    step = max(1, win // 2)
    n = win * win
    total, count = 0.0, 0
    from numpy.lib.stride_tricks import sliding_window_view
    wa = sliding_window_view(la, (win, win))[::step, ::step]
    wb = sliding_window_view(lb, (win, win))[::step, ::step]
    if wa.size == 0:
        return 1.0
    ma = wa.mean(axis=(2, 3))
    mb = wb.mean(axis=(2, 3))
    da = wa - ma[..., None, None]
    db = wb - mb[..., None, None]
    va = (da * da).sum(axis=(2, 3)) / (n - 1)
    vb = (db * db).sum(axis=(2, 3)) / (n - 1)
    cov = (da * db).sum(axis=(2, 3)) / (n - 1)
    v = ((2 * ma * mb + c1) * (2 * cov + c2)) / ((ma * ma + mb * mb + c1) * (va + vb + c2))
    total, count = float(v.sum()), v.size
    return total / count if count else 1.0


def unfilter_rows(raw, h, stride, bpp, paeth_row):
    """See slyce_png._unfilter: None/Sub/Up rows vectorised; Average and Paeth rows fall back to the pure loop."""
    out = bytearray(h * stride)
    prev = np.zeros(stride, dtype=np.uint8)
    data = np.frombuffer(raw, dtype=np.uint8)
    pos = 0
    for y in range(h):
        ft = data[pos]
        line = data[pos + 1:pos + 1 + stride]
        pos += stride + 1
        if ft == 0:
            cur = line.copy()
        elif ft == 1:
            cur = (np.cumsum(line.reshape(-1, bpp).astype(np.uint32), axis=0) & 255).astype(np.uint8).reshape(-1)
        elif ft == 2:
            cur = (line.astype(np.uint16) + prev).astype(np.uint8)
        elif ft in (3, 4):
            cur = np.frombuffer(bytes(paeth_row(ft, bytearray(line.tobytes()), bytearray(prev.tobytes()), stride, bpp)), dtype=np.uint8)
        else:
            raise ValueError("bad PNG filter %d" % ft)
        out[y * stride:(y + 1) * stride] = cur.tobytes()
        prev = cur
    return out
