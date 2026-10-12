# Support for the ST7796S 480x320 FSMC TFT (Mingda Magician Max mod)
#
# The panel is driven through the `fsmc_tft.c` MCU commands: the host keeps a
# character grid and only sends the cells that changed, so very little traffic
# goes over the serial link.
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging

TextGlyphs = {'right_arrow': b'\x1a', 'degrees': b'\xf8'}

# Sentinel stored in the grid for a cell holding an icon (drawn by write_glyph).
ICON = 0x100

# ST7796S init sequence (register, [data...]) - from Marlin st7796s.h
INIT_SEQ = [
    (0xF0, [0xC3]), (0xF0, [0x96]),
    (0x3A, [0x55]),
    (0xB4, [0x01]),
    (0xB7, [0xC6]),
    (0xC1, [0x15]),
    (0xC2, [0xAF]),
    (0xC5, [0x22]),
    (0xC6, [0x00]),
    (0xE8, [0x40, 0x8A, 0x00, 0x00, 0x29, 0x19, 0xA5, 0x33]),
    (0xE0, [0xF0, 0x04, 0x08, 0x09, 0x08, 0x15, 0x2F, 0x42,
            0x46, 0x28, 0x15, 0x16, 0x29, 0x2D]),
    (0xE1, [0xF0, 0x04, 0x09, 0x09, 0x08, 0x15, 0x2E, 0x46,
            0x46, 0x28, 0x15, 0x15, 0x29, 0x2D]),
    (0x13, []),
    (0x53, [0x24]),
    (0xF0, [0x3C]), (0xF0, [0x69]),
    (0x29, []),
]


class ST7796:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.mcu = self.printer.lookup_object('mcu')
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command('TFT_FILL', self.cmd_TFT_FILL,
                               desc="Fill the TFT with a solid R,G,B colour")
        gcode.register_command('TFT_TEST', self.cmd_TFT_TEST,
                               desc="Draw a TFT diagnostic pattern")
        gcode.register_command('TFT_LIMITS', self.cmd_TFT_LIMITS,
                               desc="Draw edge/padding test frames")
        gcode.register_command('TFT_LINES', self.cmd_TFT_LINES,
                               desc="Draw alternating vertical b/w stripes")
        gcode.register_command('TFT_FILLV', self.cmd_TFT_FILLV,
                               desc="Fill the TFT with a colour as vertical strips")
        gcode.register_command('TFT_TOUCH', self.cmd_TFT_TOUCH,
                               desc="Read the XPT2046 touch raw values")
        gcode.register_command('TFT_TOUCH_DEBUG', self.cmd_TFT_TOUCH_DEBUG,
                               desc="Toggle logging of touch raw values")
        # Touch (XPT2046)
        self.gcode = gcode
        self.touch_enable = config.getboolean('touch', True)
        self.touch_threshold = config.getint('touch_threshold', 200, minval=0)
        self.touch_swap = config.getboolean('touch_swap_xy', False)
        self.touch_inv_x = config.getboolean('touch_invert_x', False)
        self.touch_inv_y = config.getboolean('touch_invert_y', False)
        self.touch_swipe = config.getint('touch_swipe_min', 50, minval=1)
        self.touch_long = config.getfloat('touch_long_ms', 700., above=0.) / 1000.
        self.query_touch = None
        self.menu = None
        self.touch_debug = False
        self._mon = None
        gcode.register_command('TFT_TOUCH_MON', self.cmd_TFT_TOUCH_MON,
                               desc="Sample touch raw values for T seconds")
        self._touched = False
        self._t0 = 0.
        self._x0 = self._y0 = self._xl = self._yl = 0
        self.touch_timer = self.printer.get_reactor().register_timer(
            self._touch_event)
        self.cols = config.getint('cols', 20, minval=4, maxval=60)
        self.rows = config.getint('rows', 4, minval=1, maxval=20)
        self.scale = config.getint('scale', 3, minval=1, maxval=8)
        self.fg = int(config.get('fg_color', '0xFFFF'), 0) & 0xFFFF
        self.bg = int(config.get('bg_color', '0x0000'), 0) & 0xFFFF
        # MADCTL: MV(0x20)|RGB. Try 0xA0 / 0x68 / 0x28 if colours/orientation
        # look wrong.
        self.madctl = int(config.get('madctl', '0x28'), 0) & 0xFF
        self.width = config.getint('width', 480, minval=1)
        self.height = config.getint('height', 320, minval=1)
        # Command lookups happen in init(): the MCU command dictionary is only
        # available after the klippy:connect stage.
        self.cmd_cmd = self.cmd_data = self.cmd_rect = self.cmd_char = None
        self.cmd_icon = None
        self.enabled = False
        self.cell_w = self.width // self.cols
        self.cell_h = self.height // self.rows
        self.vpad = max(0, (self.cell_h - 16 * self.scale) // 2)
        self.icons = {}
        self.grid = [[ord(' ')] * self.cols for _ in range(self.rows)]
        self.shown = [[ord(' ')] * self.cols for _ in range(self.rows)]
    # --- display framework interface ---
    def set_glyphs(self, glyphs):
        self.icons = glyphs
    def get_dimensions(self):
        return (self.cols, self.rows)
    def init(self):
        self.cmd_cmd = self.mcu.try_lookup_command("tft_cmd reg=%c")
        self.cmd_data = self.mcu.try_lookup_command("tft_data val=%hu")
        self.cmd_rect = self.mcu.try_lookup_command(
            "tft_rect x=%hu y=%hu w=%hu h=%hu color=%hu")
        self.cmd_char = self.mcu.try_lookup_command(
            "tft_char x=%hu y=%hu scale=%c ch=%c fg=%hu bg=%hu")
        self.cmd_icon = self.mcu.try_lookup_command(
            "tft_icon x=%hu y=%hu w=%hu h=%hu fg=%hu bg=%hu data=%*s")
        if None in (self.cmd_cmd, self.cmd_data, self.cmd_rect, self.cmd_char):
            logging.info("st7796: MCU has no tft_* commands (flash the display"
                         " firmware); display disabled for now")
            return
        self.enabled = True
        reactor = self.printer.get_reactor()
        mcu = self.mcu
        print_time = mcu.estimated_print_time(reactor.monotonic())
        c1 = mcu.print_time_to_clock(print_time + 0.12)
        c2 = mcu.print_time_to_clock(print_time + 0.24)
        self.cmd_cmd.send([0x01])                 # SWRESET (now)
        self.cmd_cmd.send([0x11], minclock=c1)    # SLPOUT (+120ms)
        self.cmd_cmd.send([0x36], minclock=c2)    # MADCTL
        self.cmd_data.send([self.madctl], minclock=c2)
        for reg, data in INIT_SEQ:
            self.cmd_cmd.send([reg], minclock=c2)
            for d in data:
                self.cmd_data.send([d], minclock=c2)
        self.cmd_rect.send([0, 0, self.width, self.height, self.bg],
                           minclock=c2)
        self.grid = [[ord(' ')] * self.cols for _ in range(self.rows)]
        self.shown = [[ord(' ')] * self.cols for _ in range(self.rows)]
        if self.touch_enable:
            try:
                self.query_touch = self.mcu.lookup_query_command(
                    "tft_touch",
                    "tft_touch_result x=%hu y=%hu z=%hu xr=%hu yr=%hu")
            except Exception:
                self.query_touch = None
                logging.info("st7796: touch query not available")
            if self.query_touch is not None:
                reactor.update_timer(self.touch_timer, reactor.NOW)
    def clear(self):
        self.grid = [[ord(' ')] * self.cols for _ in range(self.rows)]
    def flush(self):
        if not self.enabled:
            return
        for r in range(self.rows):
            grow = self.grid[r]
            srow = self.shown[r]
            for c in range(self.cols):
                if grow[c] != srow[c]:
                    if grow[c] != ICON:
                        self._draw_cell(c, r, grow[c])
                    # else: icon already drawn by write_glyph this cycle
                    srow[c] = grow[c]
    def write_text(self, x, y, data):
        if y < 0 or y >= self.rows:
            return
        for i, ch in enumerate(bytearray(data)):
            col = x + i
            if 0 <= col < self.cols:
                self.grid[y][col] = ch
    def write_glyph(self, x, y, glyph_name):
        ch = TextGlyphs.get(glyph_name)
        if ch is not None:
            self.write_text(x, y, ch)
            return 1
        gd = self.icons.get(glyph_name)
        if gd is not None:
            icon = gd.get('icon16x16')
            if icon is not None and self.cmd_icon is not None and self.enabled:
                high, low = icon
                buf = bytearray()
                for i in range(16):
                    row = (high[i] << 8) | low[i]
                    buf.append((row >> 8) & 0xFF)
                    buf.append(row & 0xFF)
                size = min(self.cell_w, 16 * self.scale)
                px = (x * self.cell_w
                      + max(0, (self.cell_w - size) // 2))
                py = (y * self.cell_h + self.vpad
                      + max(0, (16 * self.scale - size) // 2))
                self._cover_cell(x, y)
                self.cmd_icon.send([px, py, size, size, self.fg, self.bg,
                                    bytes(buf)])
                if 0 <= y < self.rows and 0 <= x < self.cols:
                    self.grid[y][x] = ICON
            else:
                self.write_text(x, y, b' ')
            return 1
        return 0
    def write_graphics(self, x, y, data):
        # Progress bar cell - not rendered yet.
        return
    # --- diagnostics ---
    def _rgb565(self, r, g, b):
        return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | ((b & 0xF8) >> 3)
    def cmd_TFT_FILL(self, gcmd):
        if not self.enabled:
            raise gcmd.error("st7796 display not enabled")
        r = gcmd.get_int('R', 0, minval=0, maxval=255)
        g = gcmd.get_int('G', 0, minval=0, maxval=255)
        b = gcmd.get_int('B', 0, minval=0, maxval=255)
        self.cmd_rect.send([0, 0, self.width, self.height,
                            self._rgb565(r, g, b)])
    def cmd_TFT_TEST(self, gcmd):
        if not self.enabled:
            raise gcmd.error("st7796 display not enabled")
        w2 = self.width // 2
        h2 = self.height // 2
        self.cmd_rect.send([0, 0, w2, h2, 0xF800])    # red
        self.cmd_rect.send([w2, 0, w2, h2, 0x07E0])   # green
        self.cmd_rect.send([0, h2, w2, h2, 0x001F])   # blue
        self.cmd_rect.send([w2, h2, w2, h2, 0xFFFF])  # white
        for i, ch in enumerate(b'ABCDEFGHIJ'):
            self.cmd_char.send([i * self.cell_w, h2 + self.vpad,
                                self.scale, ch, 0x0000, 0xFFFF])
    def cmd_TFT_LIMITS(self, gcmd):
        if not self.enabled:
            raise gcmd.error("st7796 display not enabled")
        w, h = self.width, self.height
        W, K = 0xFFFF, 0x0000
        self.cmd_rect.send([0, 0, w, h, K])            # clear black
        # outer 1px frame, touching the panel edges
        self.cmd_rect.send([0, 0, w, 1, W])
        self.cmd_rect.send([0, h - 1, w, 1, W])
        self.cmd_rect.send([0, 0, 1, h, W])
        self.cmd_rect.send([w - 1, 0, 1, h, W])
        # inner 1px frame, 5px padding
        self.cmd_rect.send([5, 5, w - 10, 1, W])
        self.cmd_rect.send([5, h - 6, w - 10, 1, W])
        self.cmd_rect.send([5, 5, 1, h - 10, W])
        self.cmd_rect.send([w - 6, 5, 1, h - 10, W])
    def cmd_TFT_LINES(self, gcmd):
        if not self.enabled:
            raise gcmd.error("st7796 display not enabled")
        w, h = self.width, self.height
        period = gcmd.get_int('W', 20, minval=1, maxval=w)
        for i, x in enumerate(range(0, w, period)):
            color = 0xFFFF if (i & 1) == 0 else 0x0000
            self.cmd_rect.send([x, 0, min(period, w - x), h, color])
    def cmd_TFT_FILLV(self, gcmd):
        if not self.enabled:
            raise gcmd.error("st7796 display not enabled")
        w, h = self.width, self.height
        step = gcmd.get_int('W', 20, minval=1, maxval=w)
        r = gcmd.get_int('R', 0, minval=0, maxval=255)
        g = gcmd.get_int('G', 0, minval=0, maxval=255)
        b = gcmd.get_int('B', 0, minval=0, maxval=255)
        color = self._rgb565(r, g, b)
        for x in range(0, w, step):
            self.cmd_rect.send([x, 0, min(step, w - x), h, color])
    # --- touch (XPT2046 -> Klipper menu gestures) ---
    def _get_menu(self):
        if self.menu is None:
            disp = self.printer.lookup_object('display', None)
            if disp is not None:
                self.menu = getattr(disp, 'menu', None)
        return self.menu
    def _touch_event(self, eventtime):
        try:
            resp = self.query_touch.send([])
        except Exception:
            return eventtime + 0.1
        x = resp.get('x', 0)
        y = resp.get('y', 0)
        z = resp.get('z', 0)
        if self._mon is not None:
            m = self._mon
            m['zmin'] = min(m['zmin'], z)
            if z >= 100:  # only sample x/y while actually touching
                m['xmin'] = min(m['xmin'], x); m['xmax'] = max(m['xmax'], x)
                m['ymin'] = min(m['ymin'], y); m['ymax'] = max(m['ymax'], y)
                if z > m['zmax']:
                    m['zmax'] = z; m['xz'] = x; m['yz'] = y
            if eventtime >= m['end']:
                self.gcode.respond_info(
                    "touch mon: z %d..%d | x %d..%d | y %d..%d | at max z"
                    " x=%d y=%d" % (m['zmin'], m['zmax'], m['xmin'],
                                    m['xmax'], m['ymin'], m['ymax'],
                                    m['xz'], m['yz']))
                self._mon = None
            return eventtime + 0.04
        if z >= self.touch_threshold:
            if self.touch_debug:
                self.gcode.respond_info("touch raw x=%d y=%d z=%d" % (x, y, z))
            if not self._touched:
                self._touched = True
                self._t0 = eventtime
                self._x0, self._y0 = x, y
            self._xl, self._yl = x, y
        elif self._touched:
            self._touched = False
            self._handle_gesture(eventtime)
        return eventtime + 0.04
    def _handle_gesture(self, eventtime):
        dt = eventtime - self._t0
        dx = self._xl - self._x0
        dy = self._yl - self._y0
        if self.touch_swap:
            dx, dy = dy, dx
        if self.touch_inv_x:
            dx = -dx
        if self.touch_inv_y:
            dy = -dy
        sw = self.touch_swipe
        key = None
        if abs(dx) < sw and abs(dy) < sw:
            key = 'long_click' if dt >= self.touch_long else 'click'
        elif abs(dy) >= abs(dx):
            if dy > 0:
                key = 'fast_down' if abs(dy) > 2 * sw else 'down'
            else:
                key = 'fast_up' if abs(dy) > 2 * sw else 'up'
        elif dx < 0:
            key = 'back'
        if key is None:
            return
        menu = self._get_menu()
        if menu is not None:
            logging.info("st7796: touch gesture -> %s", key)
            menu.key_event(key, eventtime)
    def cmd_TFT_TOUCH(self, gcmd):
        if self.query_touch is None:
            raise gcmd.error("st7796 touch not available")
        r = self.query_touch.send([])
        gcmd.respond_info("touch x=%d y=%d z=%d (raw xr=%d yr=%d)"
                          % (r.get('x', 0), r.get('y', 0), r.get('z', 0),
                             r.get('xr', 0), r.get('yr', 0)))
    def cmd_TFT_TOUCH_DEBUG(self, gcmd):
        self.touch_debug = not self.touch_debug
        gcmd.respond_info("touch debug %s"
                          % ("on" if self.touch_debug else "off"))
    def cmd_TFT_TOUCH_MON(self, gcmd):
        if self.query_touch is None:
            raise gcmd.error("st7796 touch not available")
        t = gcmd.get_float('T', 5., above=0.)
        now = self.printer.get_reactor().monotonic()
        self._mon = {'end': now + t, 'zmin': 9999, 'zmax': 0,
                     'xmin': 9999, 'xmax': 0, 'ymin': 9999, 'ymax': 0,
                     'xz': 0, 'yz': 0}
        gcmd.respond_info("touch mon: sampling %.0fs - touch the screen now" % t)
    # --- helpers ---
    def _wcmd(self, v):
        self.cmd_cmd.send([v & 0xFF])
    def _wdata(self, v):
        self.cmd_data.send([v & 0xFFFF])
    def _cover_cell(self, col, row):
        self.cmd_rect.send([col * self.cell_w, row * self.cell_h,
                            self.cell_w, self.cell_h, self.bg])
    def _draw_cell(self, col, row, ch):
        self._cover_cell(col, row)
        x = col * self.cell_w + max(0, (self.cell_w - 8 * self.scale) // 2)
        y = row * self.cell_h + self.vpad
        self.cmd_char.send([x, y, self.scale, ch, self.fg, self.bg])


def load_config(config):
    return ST7796(config)
