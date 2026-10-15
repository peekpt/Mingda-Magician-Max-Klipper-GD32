#!/usr/bin/env python3
"""Slicer thumbnail -> 1-bit bitmap for the ST7796 panel.

Shipped next to st7796.py on the printer (klippy/extras/display/).

Pure stdlib (base64 + zlib) - no Pillow on the printer. Used by the Klipper
display chip (st7796.py) to show the object being printed.

The bitmap format matches the MCU `tft_idef`/`tft_bmp` commands: rows of
(w+7)//8 bytes, MSB first.

"""
import base64
import os
import sys
import zlib

# Panel background (COL_BG #13181C) so transparent thumbnails sit on it.
BG_LUMA = 0x13


def png_decode(raw):
    """Minimal PNG reader -> (w, h, gray bytes, alpha bytes) or None."""
    if raw[:8] != b'\x89PNG\r\n\x1a\n':
        return None
    pos = 8
    w = h = bd = ct = None
    idat = bytearray()
    pal = trns = None
    while pos + 8 <= len(raw):
        ln = int.from_bytes(raw[pos:pos + 4], 'big')
        typ = raw[pos + 4:pos + 8]
        data = raw[pos + 8:pos + 8 + ln]
        pos += 12 + ln
        if typ == b'IHDR':
            w = int.from_bytes(data[0:4], 'big')
            h = int.from_bytes(data[4:8], 'big')
            bd, ct = data[8], data[9]
            if data[12] != 0:
                return None                 # interlaced: unsupported
        elif typ == b'PLTE':
            pal = data
        elif typ == b'tRNS':
            trns = data
        elif typ == b'IDAT':
            idat += data
        elif typ == b'IEND':
            break
    if not w or not h or bd != 8:
        return None
    nch = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(ct)
    if nch is None:
        return None
    try:
        rawpx = zlib.decompress(bytes(idat))
    except Exception:
        return None
    stride = w * nch
    if len(rawpx) < (stride + 1) * h:
        return None
    out = bytearray(stride * h)
    prev = bytearray(stride)
    p = 0
    for row in range(h):
        ft = rawpx[p]
        p += 1
        line = bytearray(rawpx[p:p + stride])
        p += stride
        if ft == 1:
            for i in range(nch, stride):
                line[i] = (line[i] + line[i - nch]) & 0xFF
        elif ft == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ft == 3:
            for i in range(stride):
                a = line[i - nch] if i >= nch else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif ft == 4:
            for i in range(stride):
                a = line[i - nch] if i >= nch else 0
                b = prev[i]
                c = prev[i - nch] if i >= nch else 0
                pp = a + b - c
                pa, pb, pc = abs(pp - a), abs(pp - b), abs(pp - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        out[row * stride:(row + 1) * stride] = line
        prev = line
    gray = bytearray(w * h)
    alpha = bytearray(w * h)
    for y in range(h):
        base = y * stride
        for x in range(w):
            o = base + x * nch
            if ct == 0:
                g, a = out[o], 255
            elif ct == 4:
                g, a = out[o], out[o + 1]
            elif ct == 2:
                g = (out[o] * 30 + out[o + 1] * 59 + out[o + 2] * 11) // 100
                a = 255
            elif ct == 6:
                g = (out[o] * 30 + out[o + 1] * 59 + out[o + 2] * 11) // 100
                a = out[o + 3]
            else:
                idx = out[o]
                g = pal[idx * 3] if pal else 0
                a = trns[idx] if trns and idx < len(trns) else 255
            gray[y * w + x] = g
            alpha[y * w + x] = a
    return w, h, gray, alpha


def read_thumbnail(path, max_lines=400):
    """Return the raw bytes of the largest thumbnail block, or None."""
    best, best_area, blk = None, -1, None
    with open(path, 'rb') as f:
        for n, line in enumerate(f):
            if n > max_lines:
                break
            s = line.decode('ascii', 'ignore').strip()
            if s.startswith('; thumbnail begin'):
                parts = s.split()
                area = 0
                if len(parts) > 3 and 'x' in parts[3]:
                    try:
                        a, b = parts[3].split('x')
                        area = int(a) * int(b)
                    except Exception:
                        area = 0
                blk = [area, []]
                continue
            if s.startswith('; thumbnail end'):
                if blk and blk[0] >= best_area and blk[1]:
                    try:
                        best = base64.b64decode(''.join(blk[1]))
                        best_area = blk[0]
                    except Exception:
                        pass
                blk = None
                continue
            if blk is not None:
                blk[1].append(s.lstrip('; ').strip())
    return best


def coverage(w, h, gray, alpha):
    """Per pixel "ink" 0..255: the alpha when the image has a real alpha
    channel, otherwise a luma threshold against the border background.

    Slicer thumbnails are a light model on a transparent (or dark) background,
    so a plain dither of the luma turns into noise - a silhouette with soft
    edges reads far better on a 1-bit panel.
    """
    a_lo = min(alpha)
    if a_lo < 250:
        return alpha, stretch_luma(w, h, gray, [a > 8 for a in alpha])
    # opaque image: estimate the background from the border and cut it out
    n = border = 0
    for x in range(w):
        border += gray[x] + gray[(h - 1) * w + x]
        n += 2
    for y in range(h):
        border += gray[y * w] + gray[y * w + w - 1]
        n += 2
    bg = border // max(1, n)
    thr = bg + max(24, (255 - bg) // 5)
    out = bytearray(w * h)
    inside = [g > thr for g in gray]
    for i in range(w * h):
        if inside[i]:
            out[i] = 255
    return out, stretch_luma(w, h, gray, inside)


def stretch_luma(w, h, gray, inside):
    """Contrast stretch the luma of the model pixels to 0..255 (2%..98%)."""
    vals = sorted(gray[i] for i in range(w * h) if inside[i])
    if len(vals) < 16:
        return bytearray(b'\xff' * (w * h))
    lo = vals[int(len(vals) * 0.02)]
    hi = vals[min(len(vals) - 1, int(len(vals) * 0.98))]
    span = max(1, hi - lo)
    out = bytearray(w * h)
    for i in range(w * h):
        if inside[i]:
            out[i] = min(255, max(0, (gray[i] - lo) * 255 // span))
    return out


def scale_cov(w, h, cov, size):
    """Box-average down to size x size."""
    out = bytearray(size * size)
    for y in range(size):
        y0 = y * h // size
        y1 = max(y0 + 1, (y + 1) * h // size)
        for x in range(size):
            x0 = x * w // size
            x1 = max(x0 + 1, (x + 1) * w // size)
            acc = n = 0
            for yy in range(y0, y1):
                base = yy * w
                for xx in range(x0, x1):
                    acc += cov[base + xx]
                    n += 1
            out[y * size + x] = acc // max(1, n)
    return out


def dither_1bit(luma, size):
    """Floyd-Steinberg to 1 bit (255 = on).  Returns (w, h, packed bytes)."""
    buf = [float(v) for v in luma]
    w = h = size
    rowbytes = (w + 7) // 8
    out = bytearray(rowbytes * h)
    for y in range(h):
        for x in range(w):
            i = y * w + x
            old = buf[i]
            new = 255.0 if old > 127 else 0.0
            if new > 0:
                out[y * rowbytes + (x >> 3)] |= 0x80 >> (x & 7)
            err = old - new
            if x + 1 < w:
                buf[i + 1] += err * 7 / 16
            if y + 1 < h:
                if x > 0:
                    buf[i + w - 1] += err * 3 / 16
                buf[i + w] += err * 5 / 16
                if x + 1 < w:
                    buf[i + w + 1] += err * 1 / 16
    return w, h, bytes(out)


def thumbnail_bitmap(path, size=168):
    """-> (size, size, 1-bit bytes) or (None, 0, 0)."""
    raw = read_thumbnail(path)
    if not raw:
        return None, 0, 0
    dec = png_decode(raw)
    if dec is None:
        return None, 0, 0
    w, h, gray, alpha = dec
    cov, shade = coverage(w, h, gray, alpha)
    # silhouette * shading: the darkest face keeps a floor so it stays visible
    ink = bytearray(w * h)
    for i in range(w * h):
        ink[i] = cov[i] * (120 + 135 * shade[i] // 255) // 255
    ink = scale_cov(w, h, ink, size)
    return dither_1bit(ink, size)
