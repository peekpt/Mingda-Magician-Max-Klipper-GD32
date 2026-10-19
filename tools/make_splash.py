#!/usr/bin/env python3
"""Generate the boot splash for the Mingda Magician Max ST7796 panel.

Builds a 480x320 image with the same 8x14 font and the same palette colours
the Klipper GUI uses, quantises it to 16 colours and run-length encodes it
into `klipper_printer/mcu/splash.h`, which the MCU driver (fsmc_tft.c) draws
at power-on (and on request, via the `tft_splash` command).

Usage:  python3 tools/make_splash.py            # write splash.h + preview
        python3 tools/make_splash.py --preview  # preview only
"""
import os
import re
import sys

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..'))
MCU = os.path.join(ROOT, 'klipper_printer', 'mcu')

W, H = 480, 320

# Colours used by the GUI (COL_* in klipper_printer/mcu/st7796.py)
BG = (0x13, 0x18, 0x1C)
WHITE = (0xFF, 0xFF, 0xFF)
ORANGE = (0xED, 0x65, 0x00)
GREY = (0x40, 0x4E, 0x57)
DIM = (0x5A, 0x5A, 0x5A)

VERSION = 'R2'

# --- font (8x14, 256 glyphs x 16 bytes, MSB = leftmost) ----------------------
_fsrc = open(os.path.join(MCU, 'font8x14.h')).read()
FONT = [int(x, 16) for x in re.findall(r'0x([0-9a-fA-F]{2})', _fsrc)]
FONT = [FONT[i * 16:(i + 1) * 16] for i in range(256)]


def text_w(s, scale):
    return len(s) * 8 * scale


def draw_text(d, x, y, s, scale, col):
    """Same glyph layout as the MCU tft_char: 8 wide, 16 tall, MSB first."""
    for ch in s:
        g = FONT[ord(ch) & 0xFF]
        for row in range(16):
            bits = g[row]
            for c in range(8):
                if (bits << c) & 0x80:
                    d.rectangle([x + c * scale, y + row * scale,
                                 x + c * scale + scale - 1,
                                 y + row * scale + scale - 1], fill=col)
        x += 8 * scale


def draw_text_c(d, cx, y, s, scale, col):
    draw_text(d, cx - text_w(s, scale) // 2, y, s, scale, col)


def load_extra_icon(name):
    """Locally generated icons (EXTRA_ICONS in st7796.py)."""
    import types
    sys.path.insert(0, MCU)
    src = open(os.path.join(MCU, 'st7796.py')).read()
    src = src.replace('from . import tft_icons', 'import tft_icons')
    src = src.replace('from . import tft_thumb', 'import tft_thumb')
    mod = types.ModuleType('st_icons')
    exec(compile(src, 'st7796.py', 'exec'), mod.__dict__)
    return mod.EXTRA_ICONS[name]


def load_icon(name):
    """Load a 1-bit icon: locally generated ones win over the MDI set."""
    try:
        return load_extra_icon(name)
    except Exception:
        pass
    src = open(os.path.join(MCU, 'tft_icons.py')).read()
    m = re.search(r'^ICONS\s*=\s*\{(.*?)^\}', src, re.S | re.M)
    if m:
        blob = m.group(1)
        # entries look like: 'name': (w, h, b'...'),
        for em in re.finditer(
                r"'%s':\s*\((\d+),\s*(\d+),\s*(b(?:'[^']*'|\"[^\"]*\"))" % name,
                blob, re.S):
            w, h = int(em.group(1)), int(em.group(2))
            data = eval(em.group(3))
            return w, h, data
    # fall back to importing the module
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'tft_icons', os.path.join(MCU, 'tft_icons.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.ICONS[name]


def draw_icon(img, name, cx, cy, size, col):
    w, h, data = load_icon(name)
    rb = (w + 7) // 8
    px = img.load()
    for y in range(size):
        sy = y * h // size
        for x in range(size):
            sx = x * w // size
            if (data[sy * rb + (sx >> 3)] << (sx & 7)) & 0x80:
                px[cx - size // 2 + x, cy - size // 2 + y] = col


def build():
    img = Image.new('RGB', (W, H), BG)
    d = ImageDraw.Draw(img)

    # logo: the Mingda brand mark, orange
    draw_icon(img, 'mingda', W // 2, 70, 108, ORANGE)

    # title
    draw_text_c(d, W // 2, 136, "MINGDA", 4, WHITE)
    draw_text_c(d, W // 2, 208, "MAGICIAN MAX", 2, ORANGE)

    # divider
    d.rectangle([132, 250, 347, 251], fill=GREY)

    # footer
    draw_text_c(d, W // 2, 262, "Klipper  -  GUI %s" % VERSION, 1, DIM)

    return img


def quantize(img, ncol=16):
    q = img.quantize(colors=ncol, method=Image.MEDIANCUT, dither=Image.NONE)
    q.load()
    pal = q.getpalette()[:ncol * 3]
    pal = [tuple(pal[i * 3:i * 3 + 3]) for i in range(len(pal) // 3)]
    return q, pal


def rgb565(c):
    r, g, b = c
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def rle(q):
    px = list(q.getdata())
    out = []
    i = 0
    n = len(px)
    while i < n:
        v = px[i]
        run = 1
        while i + run < n and px[i + run] == v and run < 255:
            run += 1
        out.append((run, v))
        i += run
    return out


def bg_index(pal):
    best = 0
    bd = None
    for i, c in enumerate(pal):
        d = sum((a - b) ** 2 for a, b in zip(c, BG))
        if bd is None or d < bd:
            bd, best = d, i
    return best


def write_header(path, pal, runs):
    with open(path, 'w') as f:
        f.write("// Auto-generated by tools/make_splash.py - do not edit.\n")
        f.write("// Branding splash drawn by the MCU at power-on.\n")
        f.write("#pragma once\n#include <stdint.h>\n\n")
        f.write("#define SPLASH_W %d\n#define SPLASH_H %d\n" % (W, H))
        f.write("#define SPLASH_NCOL %d\n" % len(pal))
        f.write("#define SPLASH_BG %d\n\n" % bg_index(pal))
        f.write("static const uint16_t splash_pal[%d] = {\n" % len(pal))
        for i in range(0, len(pal), 8):
            f.write("    " + ", ".join("0x%04X" % rgb565(c)
                                       for c in pal[i:i + 8]) + ",\n")
        f.write("};\n\n")
        f.write("// run-length encoded 4-bit palette indices: pairs of\n")
        f.write("// (count, index), count 1..255\n")
        f.write("#define SPLASH_RLE_LEN %d\n" % (len(runs) * 2))
        f.write("static const uint8_t splash_rle[%d] = {\n" % (len(runs) * 2))
        line = []
        for (cnt, idx) in runs:
            line.append("%d,%d" % (cnt, idx))
            if len(line) >= 16:
                f.write("    " + ",".join(line) + ",\n")
                line = []
        if line:
            f.write("    " + ",".join(line) + ",\n")
        f.write("};\n")
    return len(runs) * 2


if __name__ == '__main__':
    img = build()
    prev = os.path.join(HERE, 'splash_preview.png')
    img.save(prev)
    print('preview ->', prev)
    if '--preview' in sys.argv:
        sys.exit(0)
    q, pal = quantize(img)
    runs = rle(q)
    out = os.path.join(MCU, 'splash.h')
    n = write_header(out, pal, runs)
    print('palette:', ['#%02X%02X%02X' % c for c in pal])
    print('rle bytes:', n, '-> %s (%.1f KB)' % (out, os.path.getsize(out) / 1024))
