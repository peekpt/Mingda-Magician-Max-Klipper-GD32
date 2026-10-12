# Stock ST7796 TFT support (experimental)

Adds a Klipper `[display]` driver for the stock 480x320 ST7796 panel of the
Mingda Magician Max (16-bit FSMC/8080 parallel bus wired to the printer MCU).

The host keeps a character grid and only sends the cells that changed; the MCU
draws the glyphs from a built-in 8x14 font, so very little goes over the serial
link (this matters at 250000 baud).

## Files

- `fsmc_tft.c`  — MCU driver: FSMC bus setup at boot plus the commands
                  `tft_cmd`, `tft_data`, `tft_rect`, `tft_char`, `tft_icon`.
                  `build_klipper_fw.sh` copies it into `src/stm32/`.
- `font8x14.h`  — 8x14 VGA font used by `tft_char` (generated from
                  `klippy/extras/display/font8x14.py`).
- `st7796.py`   — host side display chip (`klippy/extras/display/st7796.py`).

## Build the MCU firmware

`./build_klipper_fw.sh` injects the driver + font into the Klipper tree and
produces `firmware.bin`. Flash it via SD as usual.

## Install the host side (Raspberry Pi)

```
cp st7796.py ~/klipper/klippy/extras/display/st7796.py
```

Then register it in `~/klipper/klippy/extras/display/display.py`:

```python
from . import aip31068_spi, hd44780, hd44780_spi, st7920, uc1701, menu, st7796

LCD_chips = {
    ...
    'st7796': st7796.ST7796,
}
```

And add to `printer.cfg`:

```ini
[display]
lcd_type: st7796
scale: 3
cols: 20
rows: 4
madctl: 0xE8     # MV|MX|MY|BGR for this panel (tweak if colours/flip are odd)
```

Restart the **klipper service** (a plain `RESTART` does not reload Python modules).

## Diagnostics (gcode)

| Command | Effect |
|---|---|
| `TFT_FILL R= G= B=` | fill the screen with a solid colour |
| `TFT_TEST` | 4 colour quadrants + a row of letters |
| `TFT_LIMITS` | edge frame + 5px-padding frame |
| `TFT_LINES W=20` | alternating vertical b/w stripes |
| `TFT_FILLV R= G= B= W=` | fill a colour as vertical strips |

## Status / TODO

- Text, colours, orientation and native Klipper glyph icons work.
- The `[menu]` framework is loaded but needs touch input to navigate
  (XPT2046 on SPI, PE/… — not done yet).
- Some cheap panels show faint vertical lines on large uniform black areas
  (a panel/backlight artefact, not a driver bug).
