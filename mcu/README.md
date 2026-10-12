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
scale: 2
cols: 30
rows: 10
display_group: my_30x10
madctl: 0xE8     # MV|MX|MY|BGR for this panel (tweak if colours/flip are odd)
```

The **30x10** grid gives 16x32 px cells that fill the 480x320 panel exactly
(no gaps). The status layout is a custom `[display_data my_30x10 ...]` group in
`printer.cfg`.

Restart the **klipper service** (a plain `RESTART` does not reload Python modules).

## Diagnostics (gcode)

| Command | Effect |
|---|---|
| `TFT_FILL R= G= B=` | fill the screen with a solid colour |
| `TFT_TEST` | 4 colour quadrants + a row of letters |
| `TFT_LIMITS` | edge frame + 5px-padding frame |
| `TFT_LINES W=20` | alternating vertical b/w stripes |
| `TFT_FILLV R= G= B= W=` | fill a colour as vertical strips |
| `TFT_TOUCH` | read the XPT2046 raw values once |
| `TFT_TOUCH_MON T=6` | sample min/max of x/y/z for N seconds |
| `TFT_TOUCH_DEBUG` | toggle per-poll logging of raw touch values |

## Touch (XPT2046)

The resistive touch controller is on a software SPI bus
(`CS=PB6, SCK=PB3, MOSI=PB5, MISO=PB4`); the MCU slows the bit-bang down so the
XPT2046 can keep up. The host polls it (~25 Hz), turns touches into gestures and
feeds them to the Klipper menu:

- **tap** -> `click` (select/open)
- **swipe up/down** -> `up`/`down` (navigate)
- **swipe left** -> `back`
- **long press** -> `long_click`

Config options (in `[display]`):

```ini
touch: True
touch_threshold: 250      # z above this = touching
touch_swipe_min: 120      # raw movement to count as a swipe
#touch_swap_xy: False
#touch_invert_x: False
#touch_invert_y: False
#touch_long_ms: 700
```

Use `TFT_TOUCH_MON` to calibrate `touch_threshold` and the invert/swap options.

## GUI (Marlin-like, tap only)

`gui: True` in `[display]` switches the chip to a touch GUI instead of the
Klipper text menu:

- **Top + bottom status bars** (nozzle/bed temps, elapsed time, `M117` message).
- **Main menu**: 4x2 rounded buttons with MDI icons (from KlipperScreen's icon
  font): Preheat, Mover, Extruder, Home, Nivelar, Probe, Arrefecer, Config.
- **Mover**: Z+/Y+/Z- plus a distance button (0.1/1/10 mm - tap to cycle),
  X-/Y-/X+/Voltar.
- **Extruder**: load/unload, E +/-.
- **Probe**: PROBE_CALIBRATE, TESTZ steps, ACCEPT, SAVE_CONFIG.
- **Config**: PID, SAVE_CONFIG, restart.

Icons are 1-bit MDI bitmaps rendered off-line into `tft_icons.py`. Touch
calibration: `raw_x_min/max`, `raw_y_min/max`, `touch_swap_xy`,
`touch_invert_x/y` (measure with `TFT_TOUCH_MON T=12`).

## Status / TODO

- Text, colours, orientation, native icons and **touch menu navigation** work.
- Icons are 16x16 Klipper glyphs drawn at one cell; making them larger and/or
  loading them from the W25 flash is future work.
- Some cheap panels show faint vertical lines on large uniform black areas
  (a panel/backlight artefact, not a driver bug).
