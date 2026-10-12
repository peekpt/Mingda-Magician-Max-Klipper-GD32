# Support for the ST7796S 480x320 FSMC TFT (Mingda Magician Max mod)
#
# The panel is driven through the `fsmc_tft.c` MCU commands: the host keeps a
# character grid and only sends the cells that changed, so very little traffic
# goes over the serial link.
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
from . import tft_icons

TextGlyphs = {'right_arrow': b'\x1a', 'degrees': b'\xf8'}

# Marlin-like menus: screen -> list of (label, icon, action).
# action is a gcode string, or ('scr', name) to switch screen, or None (empty).
GUI_SCREENS = {
    'main': [
        ("Preheat", "fire", ('scr', 'preheat')),
        ("Mover", "move", ('scr', 'move')),
        ("Extruder", "load", ('scr', 'extrude')),
        ("Home", "home", 'G28'),
        ("Nivelar", "mesh", 'G29'),
        ("Probe", "probe", ('scr', 'probe')),
        ("Arrefecer", "cool", 'M104 S0\nM140 S0'),
        ("Config", "cog", ('scr', 'config')),
    ],
    'preheat': [
        ("PLA", "thermo", 'PREHEAT_PLA'),
        ("PETG", "thermo", 'PREHEAT_PETG'),
        ("ABS", "thermo", 'PREHEAT_ABS'),
        ("TPU", "thermo", 'PREHEAT_TPU'),
        ("Temp 200", "fire", 'M104 S200'),
        ("Temp 240", "fire", 'M104 S240'),
        ("Cama 60", "bed", 'M140 S60'),
        ("Voltar", "back", ('scr', 'main')),
    ],
    'move': [
        ("Z +", "up", ('mv', 'Z', 1)),
        ("Y +", "down", ('mv', 'Y', 1)),
        ("Z -", "down", ('mv', 'Z', -1)),
        ("1", "speed", ('step',)),
        ("X -", "left", ('mv', 'X', -1)),
        ("Y -", "up", ('mv', 'Y', -1)),
        ("X +", "right", ('mv', 'X', 1)),
        ("Voltar", "back", ('scr', 'main')),
    ],
    'extrude': [
        ("Carregar", "load", 'LOAD_FILAMENT'),
        ("Descarga", "unload", 'UNLOAD_FILAMENT'),
        ("E +10", "right", 'G91\nG1 E10 F300\nG90'),
        ("E -10", "left", 'G91\nG1 E-10 F300\nG90'),
        ("Temp 200", "fire", 'M104 S200'),
        ("Temp 240", "fire", 'M104 S240'),
        ("", "", None),
        ("Voltar", "back", ('scr', 'main')),
    ],
    'probe': [
        ("Calibrar", "probe", 'PROBE_CALIBRATE'),
        ("Z -1", "down", 'TESTZ Z=-1'),
        ("Z -0.1", "down", 'TESTZ Z=-0.1'),
        ("Z +0.1", "up", 'TESTZ Z=+0.1'),
        ("Z +1", "up", 'TESTZ Z=+1'),
        ("Aceitar", "check", 'ACCEPT'),
        ("Guardar", "save", 'SAVE_CONFIG'),
        ("Voltar", "back", ('scr', 'main')),
    ],
    'config': [
        ("PID hot", "fire", 'PID_CALIBRATE HEATER=extruder TARGET=200'),
        ("PID bed", "bed", 'PID_CALIBRATE HEATER=heater_bed TARGET=60'),
        ("Guardar", "save", 'SAVE_CONFIG'),
        ("Info", "info", 'M117 Klipper OK'),
        ("", "", None),
        ("", "", None),
        ("Reiniciar", "cog", 'RESTART'),
        ("Voltar", "back", ('scr', 'main')),
    ],
}

# Sentinel stored in the grid for a cell holding an icon (drawn by write_glyph).
ICON = 0x100


def _make_corner(r, fx, fy):
    # r x r quarter-disk bitmap (1 = inside), MSB-first, row-major.
    rowbytes = (r + 7) // 8
    data = bytearray()
    for j in range(r):
        val = 0
        for i in range(r):
            ii = (r - 1 - i) if fx else i
            jj = (r - 1 - j) if fy else j
            dx = ii - (r - 0.5)
            dy = jj - (r - 0.5)
            val = (val << 1) | (1 if dx * dx + dy * dy <= (r - 0.5) ** 2 else 0)
        val <<= (rowbytes * 8 - r)
        for k in range(rowbytes):
            data.append((val >> ((rowbytes - 1 - k) * 8)) & 0xFF)
    return (r, r, bytes(data))

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
        self.touch_short_tap = config.getboolean('touch_short_tap', False)
        # KlipperScreen-like GUI mode
        self.gui = config.getboolean('gui', False)
        self.gui_bg = int(config.get('gui_bg', '0x1082'), 0) & 0xFFFF
        self.gui_bar_bg = int(config.get('gui_bar_bg', '0x2124'), 0) & 0xFFFF
        self.gui_btn_bg = int(config.get('gui_btn_bg', '0x3186'), 0) & 0xFFFF
        self.gui_accent = int(config.get('gui_accent', '0x07FF'), 0) & 0xFFFF
        self.gui_label = int(config.get('gui_label', '0xFFFF'), 0) & 0xFFFF
        self.gui_icon = int(config.get('gui_icon', '0xFFFF'), 0) & 0xFFFF
        self.raw_x_min = config.getint('raw_x_min', 200)
        self.raw_x_max = config.getint('raw_x_max', 1900)
        self.raw_y_min = config.getint('raw_y_min', 200)
        self.raw_y_max = config.getint('raw_y_max', 1900)
        self.gui_status_fg = int(config.get('gui_status_fg', '0xFFFF'), 0) & 0xFFFF
        self.gui_radius = config.getint('gui_radius', 8, minval=0, maxval=16)
        self._corners = (_make_corner(self.gui_radius, False, False),
                         _make_corner(self.gui_radius, True, False),
                         _make_corner(self.gui_radius, False, True),
                         _make_corner(self.gui_radius, True, True))
        self.gui_screen = 'main'
        self._gui_steps = (0.1, 1.0, 10.0)
        self._gui_step_i = 1
        self.cmd_bmp = None
        self.cmd_rrect = None
        self._gui_next = 0.
        self._gui_pending = None
        self._gui_last = None
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
        self.icon_color = int(config.get('icon_color', '0x07FF'), 0) & 0xFFFF
        self.bar_color = int(config.get('bar_color', '0x07E0'), 0) & 0xFFFF
        self.bar_bg = int(config.get('bar_bg', '0x0000'), 0) & 0xFFFF
        rc = config.get('row_colors', '').strip()
        self.row_colors = ([int(v, 0) & 0xFFFF for v in rc.split(',')
                            if v.strip()] if rc else None)
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
        try:
            self.query_touch = self.mcu.lookup_query_command(
                "tft_touch",
                "tft_touch_result x=%hu y=%hu z=%hu xr=%hu yr=%hu")
        except Exception:
            self.query_touch = None
            logging.info("st7796: touch query not available")
        self.cmd_bmp = self.mcu.try_lookup_command(
            "tft_bmp x=%hu y=%hu w=%hu rows=%c rowoff=%c fg=%hu bg=%hu"
            " data=%*s")
        self.cmd_rrect = self.mcu.try_lookup_command(
            "tft_rrect x=%hu y=%hu w=%hu h=%hu r=%c color=%hu")
        if self.gui and self.cmd_bmp is not None:
            # Draw after the panel init commands (which are scheduled ~0.24 s
            # out) so the init doesn't wipe the GUI.
            self._gui_pending = reactor.monotonic() + 0.30
        if self.gui or (self.query_touch is not None and self.touch_enable):
            reactor.update_timer(self.touch_timer, reactor.NOW)
    def clear(self):
        if self.gui:
            return
        self.grid = [[ord(' ')] * self.cols for _ in range(self.rows)]
    def flush(self):
        if not self.enabled or self.gui:
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
        if self.gui:
            return
        if y < 0 or y >= self.rows:
            return
        for i, ch in enumerate(bytearray(data)):
            col = x + i
            if 0 <= col < self.cols:
                self.grid[y][col] = ch
    def write_glyph(self, x, y, glyph_name):
        if self.gui:
            return 0
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
                isz = min(self.cell_w, self.cell_h)
                self._cover_cell(x, y)
                self.cmd_icon.send([x * self.cell_w,
                                    y * self.cell_h + (self.cell_h - isz) // 2,
                                    isz, isz, self.icon_color, self.bg,
                                    bytes(buf)])
                if 0 <= y < self.rows and 0 <= x < self.cols:
                    self.grid[y][x] = ICON
            else:
                self.write_text(x, y, b' ')
            return 1
        return 0
    def write_graphics(self, x, y, data):
        # Progress bar: data = [0xff] + [byte]*14 + [0xff], the byte holds the
        # 8 horizontal pixels of this bar slice.
        if (not self.enabled or self.gui
                or not (0 <= y < self.rows and 0 <= x < self.cols)):
            return
        b = data[1] & 0xFF if len(data) > 1 else 0
        bar_h = max(4, self.cell_h // 2)
        px = x * self.cell_w
        py = y * self.cell_h + (self.cell_h - bar_h) // 2
        filled = (self.cell_w * bin(b).count('1') + 4) // 8
        self.cmd_rect.send([px, py, self.cell_w, bar_h, self.bar_bg])
        if filled > 0:
            self.cmd_rect.send([px, py, filled, bar_h, self.bar_color])
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
        if self._gui_pending is not None:
            if eventtime >= self._gui_pending:
                self._gui_pending = None
                self._gui_render(eventtime)
        if self.query_touch is None:
            return eventtime + 0.2
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
        if self.gui:
            if eventtime >= self._gui_next:
                self._gui_next = eventtime + 0.5
                self._gui_bars(eventtime)
            if z >= self.touch_threshold:
                if not self._touched:
                    self._touched = True
                    self._x0, self._y0 = x, y
                self._xl, self._yl = x, y
            elif self._touched:
                self._touched = False
                if (abs(self._xl - self._x0) < self.touch_swipe
                        and abs(self._yl - self._y0) < self.touch_swipe):
                    self._gui_tap((self._x0 + self._xl) // 2,
                                  (self._y0 + self._yl) // 2, eventtime)
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
            if dt >= self.touch_long:
                key = 'click'            # long press selects
            elif self.touch_short_tap:
                key = 'click'            # (optional) short tap also selects
            else:
                key = None               # ignore short taps (resistive noise)
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
    # --- Marlin-like GUI (4x2 rounded buttons, bottom bar, tap only) ---
    def _gui_buttons(self):
        cols, rows = 4, 2
        bw = self.width // cols
        bh = (self.height - 60) // rows
        screen = GUI_SCREENS.get(self.gui_screen, [])
        out = []
        for idx in range(cols * rows):
            item = screen[idx] if idx < len(screen) else None
            out.append(((idx % cols) * bw + 4, 30 + (idx // cols) * bh + 4,
                        bw - 8, bh - 8, item))
        return out
    def _gui_text(self, x, y, text, fg, bg, scale=1):
        for i, ch in enumerate(text.encode('latin-1')):
            self.cmd_char.send([x + i * 8 * scale, y, scale, ch, fg, bg])
    def _gui_text_center(self, x0, w, y, text, fg, bg, scale=1):
        tw = len(text) * 8 * scale
        self._gui_text(x0 + max(0, (w - tw) // 2), y, text, fg, bg, scale)
    def _gui_icon(self, name, x, y, fg, bg):
        icon = tft_icons.ICONS.get(name)
        if icon is None or self.cmd_bmp is None:
            return
        w, h, data = icon
        rowbytes = (w + 7) // 8
        rows_per = max(1, 40 // rowbytes)
        for off in range(0, h, rows_per):
            rows = min(rows_per, h - off)
            chunk = data[off * rowbytes:(off + rows) * rowbytes]
            self.cmd_bmp.send([x, y, w, rows, off, fg, bg, bytes(chunk)])
    def _gui_statusbar(self, eventtime):
        y = self.height - 30
        self.cmd_rect.send([0, y, self.width, 30, self.gui_bar_bg])
        msg = ''
        ds = self.printer.lookup_object('display_status', None)
        if ds is not None:
            msg = ds.get_status(eventtime).get('message', '') or ''
        if not msg:
            try:
                nb = self.printer.lookup_object('extruder').get_status(eventtime)
                bd = self.printer.lookup_object(
                    'heater_bed').get_status(eventtime)
                msg = "N %d   B %d" % (nb['temperature'], bd['temperature'])
                ps = self.printer.lookup_object('print_stats', None)
                if ps is not None:
                    msg += "   " + str(ps.get_status(eventtime).get('state', ''))
            except Exception:
                msg = ''
        self._gui_text(4, y + 7, str(msg)[:56], self.gui_status_fg,
                       self.gui_bar_bg, 1)
    def _bmp_at(self, bmp, x, y, fg, bg):
        w, h, data = bmp
        if w <= 0 or h <= 0 or self.cmd_bmp is None:
            return
        self.cmd_bmp.send([x, y, w, h, 0, fg, bg, data])
    def _gui_round(self, x, y, w, h, color):
        self.cmd_rect.send([x, y, w, h, color])
        r = self.gui_radius
        if r <= 0 or self.cmd_bmp is None:
            return
        tl, tr, bl, br = self._corners
        self._bmp_at(tl, x, y, color, self.gui_bg)
        self._bmp_at(tr, x + w - r, y, color, self.gui_bg)
        self._bmp_at(bl, x, y + h - r, color, self.gui_bg)
        self._bmp_at(br, x + w - r, y + h - r, color, self.gui_bg)
    def _icon16(self, name, x, y, fg, bg):
        icon = tft_icons.ICONS.get(name)
        if icon is None or self.cmd_icon is None:
            return
        self.cmd_icon.send([x, y, 16, 16, fg, bg, icon[2]])
    def _gui_topbar(self, eventtime):
        self.cmd_rect.send([0, 0, self.width, 30, self.gui_bar_bg])
        try:
            nb = self.printer.lookup_object('extruder').get_status(eventtime)
            bd = self.printer.lookup_object(
                'heater_bed').get_status(eventtime)['temperature']
        except Exception:
            return
        self._gui_icon('nozzle20', 6, 5, self.gui_accent, self.gui_bar_bg)
        self._gui_text(30, 7, "%d" % nb['temperature'], self.gui_status_fg,
                       self.gui_bar_bg, 1)
        self._gui_icon('bed20', 96, 5, self.gui_accent, self.gui_bar_bg)
        self._gui_text(120, 7, "%d" % bd, self.gui_status_fg, self.gui_bar_bg, 1)
        self._gui_icon('clock20', 196, 5, self.gui_accent, self.gui_bar_bg)
        try:
            dur = int(self.printer.lookup_object('print_stats').get_status(
                eventtime)['print_duration'])
        except Exception:
            dur = 0
        self._gui_text(216, 7, "%02d:%02d" % (dur // 3600, (dur // 60) % 60),
                       self.gui_status_fg, self.gui_bar_bg, 1)
    def _gui_bars(self, eventtime):
        self._gui_topbar(eventtime)
        self._gui_statusbar(eventtime)
    def _gui_render(self, eventtime):
        self.cmd_rect.send([0, 0, self.width, self.height, self.gui_bg])
        for (x, y, w, h, item) in self._gui_buttons():
            if item is None:
                continue
            label, icon, action = item
            if isinstance(action, tuple) and action[0] == 'step':
                label = "%g" % self._gui_steps[self._gui_step_i]
            self._gui_round(x, y, w, h, self.gui_btn_bg)
            iw, ih = tft_icons.ICONS.get(icon, (0, 0, b''))[:2]
            self._gui_icon(icon, x + (w - iw) // 2, y + 10, self.gui_icon,
                           self.gui_btn_bg)
            self._gui_text_center(x, w, y + 10 + ih + 4, label,
                                  self.gui_label, self.gui_btn_bg, 1)
        self._gui_bars(eventtime)
    def _raw_to_screen(self, rx, ry):
        if self.touch_swap:
            rx, ry = ry, rx
        nx = (rx - self.raw_x_min) / float(self.raw_x_max - self.raw_x_min)
        ny = (ry - self.raw_y_min) / float(self.raw_y_max - self.raw_y_min)
        if self.touch_inv_x:
            nx = 1.0 - nx
        if self.touch_inv_y:
            ny = 1.0 - ny
        return int(nx * self.width), int(ny * self.height)
    def _move_gcode(self, axis, sign):
        amt = self._gui_steps[self._gui_step_i] * sign
        feed = 300 if axis == 'E' else (600 if axis == 'Z' else 3000)
        return "G91\nG1 %s%.3f F%d\nG90" % (axis, amt, feed)
    def _gui_tap(self, rx, ry, eventtime):
        sx, sy = self._raw_to_screen(rx, ry)
        self.gcode.respond_info("GUI tap raw=(%d,%d) -> (%d,%d)"
                                % (rx, ry, sx, sy))
        for (x, y, w, h, item) in self._gui_buttons():
            if item is None:
                continue
            if x <= sx < x + w and y <= sy < y + h:
                label, icon, action = item
                self.gcode.respond_info("GUI: %s" % label)
                if isinstance(action, tuple) and action[0] == 'scr':
                    self.gui_screen = action[1]
                    self._gui_render(eventtime)
                elif isinstance(action, tuple) and action[0] == 'step':
                    self._gui_step_i = ((self._gui_step_i + 1)
                                        % len(self._gui_steps))
                    self._gui_render(eventtime)
                elif isinstance(action, tuple) and action[0] == 'mv':
                    self.printer.get_reactor().register_async_callback(
                        lambda et, ax=action[1], d=action[2]:
                        self.gcode.run_script_from_command(
                            self._move_gcode(ax, d)))
                elif action:
                    # Run outside this timer callback so blocking macros (e.g.
                    # a filament load that waits for the hotend) don't freeze
                    # the UI.
                    self.printer.get_reactor().register_async_callback(
                        lambda et, a=action:
                        self.gcode.run_script_from_command(a))
                return
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
        fg = self.fg
        if self.row_colors:
            fg = self.row_colors[row % len(self.row_colors)]
        x = col * self.cell_w + max(0, (self.cell_w - 8 * self.scale) // 2)
        y = row * self.cell_h + self.vpad
        self.cmd_char.send([x, y, self.scale, ch, fg, self.bg])


def load_config(config):
    return ST7796(config)
