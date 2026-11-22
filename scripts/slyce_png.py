"""SLYCE image helpers: PNG read/write and small RGBA operations, Python standard library only.

An image is an `Img` holding width, height and a bytearray of RGBA pixels (8 bits per channel,
row by row, top to bottom). Fine for UI sprites; slow for very large art, which SLYCE seldom resamples.
"""
import hashlib
import struct
import zlib

PNG_SIG = b"\x89PNG\r\n\x1a\n"


class Img(object):
    __slots__ = ("w", "h", "px")

    def __init__(self, w, h, px=None):
        self.w = w
        self.h = h
        self.px = px if px is not None else bytearray(w * h * 4)

    def row(self, y):
        s = self.w * 4
        return self.px[y * s:(y + 1) * s]

    def channel_col(self, x, c):
        """Channel c (0..3) of column x, as bytes (top to bottom)."""
        return bytes(self.px[x * 4 + c::self.w * 4])

    def col(self, x):
        """Column x as RGBA bytes (top to bottom)."""
        out = bytearray(self.h * 4)
        for c in range(4):
            out[c::4] = self.px[x * 4 + c::self.w * 4]
        return out

    def alpha(self):
        return self.px[3::4]

    def crop(self, x, y, w, h):
        out = Img(w, h)
        s = self.w * 4
        for r in range(h):
            start = (y + r) * s + x * 4
            out.px[r * w * 4:(r + 1) * w * 4] = self.px[start:start + w * 4]
        return out

    def paste(self, src, x, y):
        s = self.w * 4
        for r in range(src.h):
            start = (y + r) * s + x * 4
            self.px[start:start + src.w * 4] = src.px[r * src.w * 4:(r + 1) * src.w * 4]

    def digest(self):
        return hashlib.sha1(struct.pack(">II", self.w, self.h) + bytes(self.px)).hexdigest()


# ---------- PNG ----------

def _paeth(a, b, c):
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _unfilter(raw, h, stride, bpp):
    out = bytearray(h * stride)
    prev = bytearray(stride)
    pos = 0
    for y in range(h):
        ft = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + stride])
        pos += stride
        if ft == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 255
        elif ft == 2:
            line = bytearray((a + b) & 255 for a, b in zip(line, prev))
        elif ft == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 255
        elif ft == 4:
            for i in range(stride):
                if i >= bpp:
                    line[i] = (line[i] + _paeth(line[i - bpp], prev[i], prev[i - bpp])) & 255
                else:
                    line[i] = (line[i] + prev[i]) & 255
        elif ft != 0:
            raise ValueError("bad PNG filter %d" % ft)
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return out


def read_png(path):
    with open(path, "rb") as f:
        data = f.read()
    if data[:8] != PNG_SIG:
        raise ValueError("not a PNG: %s" % path)
    pos = 8
    idat = []
    palette = None
    trns = None
    w = h = depth = ctype = interlace = None
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            w, h, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", body)
        elif kind == b"PLTE":
            palette = body
        elif kind == b"tRNS":
            trns = body
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
    if depth != 8 or interlace:
        raise ValueError("%s: only 8-bit, non-interlaced PNGs are supported" % path)
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    raw = _unfilter(zlib.decompress(b"".join(idat)), h, w * channels, channels)
    img = Img(w, h)
    px = img.px
    n = w * h
    if ctype == 6:
        px[:] = raw
    elif ctype == 2:
        for c in range(3):
            px[c::4] = raw[c::3]
        px[3::4] = b"\xff" * n
    elif ctype == 0:
        for c in range(3):
            px[c::4] = raw
        px[3::4] = b"\xff" * n
    elif ctype == 4:
        for c in range(3):
            px[c::4] = raw[0::2]
        px[3::4] = raw[1::2]
    else:
        alphas = trns or b""
        for i in range(n):
            k = raw[i]
            px[i * 4:i * 4 + 3] = palette[k * 3:k * 3 + 3]
            px[i * 4 + 3] = alphas[k] if k < len(alphas) else 255
    return img


def _chunk(kind, body):
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)


def write_png(path, img):
    stride = img.w * 4
    raw = bytearray()
    for y in range(img.h):
        raw.append(0)
        raw += img.px[y * stride:(y + 1) * stride]
    data = PNG_SIG + _chunk(b"IHDR", struct.pack(">IIBBBBB", img.w, img.h, 8, 6, 0, 0, 0))
    data += _chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + _chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(data)


# ---------- transforms ----------

def trim(img, threshold=1):
    """Crops to pixels with alpha >= threshold. Returns (img, x, y); (None, 0, 0) when empty."""
    a = img.alpha()
    w, h = img.w, img.h
    rows = [y for y in range(h) if max(a[y * w:(y + 1) * w]) >= threshold]
    if not rows:
        return None, 0, 0
    y0, y1 = rows[0], rows[-1] + 1
    x0, x1 = w, 0
    for y in range(y0, y1):
        line = a[y * w:(y + 1) * w]
        if max(line) < threshold:
            continue
        for x in range(w):
            if line[x] >= threshold:
                x0 = min(x0, x)
                break
        for x in range(w - 1, -1, -1):
            if line[x] >= threshold:
                x1 = max(x1, x + 1)
                break
    if x0 == 0 and y0 == 0 and x1 == w and y1 == h:
        return img, 0, 0
    return img.crop(x0, y0, x1 - x0, y1 - y0), x0, y0


def flip_h(img):
    out = Img(img.w, img.h)
    for y in range(img.h):
        line = img.row(y)
        rev = bytearray(len(line))
        for c in range(4):
            rev[c::4] = line[c::4][::-1]
        out.px[y * img.w * 4:(y + 1) * img.w * 4] = rev
    return out


def flip_v(img):
    out = Img(img.w, img.h)
    s = img.w * 4
    for y in range(img.h):
        out.px[(img.h - 1 - y) * s:(img.h - y) * s] = img.row(y)
    return out


def transpose(img):
    out = Img(img.h, img.w)
    for x in range(img.w):
        out.px[x * img.h * 4:(x + 1) * img.h * 4] = img.col(x)
    return out


def orientations(img):
    """The 8 rotations/mirrors of img with a label each."""
    r = []
    t = transpose(img)
    for label, im in (("id", img), ("flipH", flip_h(img)), ("flipV", flip_v(img)), ("rot180", flip_v(flip_h(img))),
                      ("transpose", t), ("rot90", flip_h(t)), ("rot270", flip_v(t)), ("transverse", flip_v(flip_h(t)))):
        r.append((label, im))
    return r


def _weights(src, dst):
    """Area-average weights: for each destination index, a list of (source index, weight)."""
    scale = src / float(dst)
    table = []
    for i in range(dst):
        a = i * scale
        b = a + scale
        items = []
        j = int(a)
        while j < b and j < src:
            lo = max(a, j)
            hi = min(b, j + 1)
            if hi > lo:
                items.append((j, (hi - lo) / scale))
            j += 1
        table.append(items)
    return table


def resize_area(img, nw, nh):
    """Downscale with area averaging on premultiplied alpha (no dark fringes)."""
    if nw == img.w and nh == img.h:
        return img
    w, h = img.w, img.h
    src = img.px
    pre = [0.0] * (w * h * 4)
    for i in range(w * h):
        a = src[i * 4 + 3]
        f = a / 255.0
        pre[i * 4] = src[i * 4] * f
        pre[i * 4 + 1] = src[i * 4 + 1] * f
        pre[i * 4 + 2] = src[i * 4 + 2] * f
        pre[i * 4 + 3] = float(a)
    wx = _weights(w, nw)
    tmp = [0.0] * (nw * h * 4)
    for y in range(h):
        base = y * w * 4
        obase = y * nw * 4
        for x in range(nw):
            r = g = b = a = 0.0
            for j, wt in wx[x]:
                k = base + j * 4
                r += pre[k] * wt
                g += pre[k + 1] * wt
                b += pre[k + 2] * wt
                a += pre[k + 3] * wt
            o = obase + x * 4
            tmp[o] = r
            tmp[o + 1] = g
            tmp[o + 2] = b
            tmp[o + 3] = a
    wy = _weights(h, nh)
    out = Img(nw, nh)
    dst = out.px
    for y in range(nh):
        for x in range(nw):
            r = g = b = a = 0.0
            for j, wt in wy[y]:
                k = (j * nw + x) * 4
                r += tmp[k] * wt
                g += tmp[k + 1] * wt
                b += tmp[k + 2] * wt
                a += tmp[k + 3] * wt
            o = (y * nw + x) * 4
            if a > 0.5:
                f = 255.0 / a
                dst[o] = min(255, int(r * f + 0.5))
                dst[o + 1] = min(255, int(g * f + 0.5))
                dst[o + 2] = min(255, int(b * f + 0.5))
                dst[o + 3] = min(255, int(a + 0.5))
    return out


def pad_center(img, size_w, size_h):
    out = Img(size_w, size_h)
    out.paste(img, (size_w - img.w) // 2, (size_h - img.h) // 2)
    return out


def lines_equal(a, b, tol):
    if a == b:
        return True
    if tol <= 0:
        return False
    return max(abs(x - y) for x, y in zip(a, b)) <= tol
