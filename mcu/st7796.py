# Support for the ST7796S 480x320 FSMC TFT (Mingda Magician Max mod)
#
# The panel is driven through the `fsmc_tft.c` MCU commands: the host keeps a
# character grid and only sends the cells that changed, so very little traffic
# goes over the serial link.
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import glob
import logging
import os
import re
import socket
import subprocess
from . import tft_icons

TextGlyphs = {'right_arrow': b'\x1a', 'degrees': b'\xf8'}

# KlipperScreen theme (values converted to RGB565)
COL_BG     = 0x10C3   # #13181C
COL_ACTIVE = 0x426A   # #404E57
COL_C1     = 0xEB20   # #ED6500  (orange)
COL_C2     = 0xB010   # #B10080  (magenta)
COL_C3     = 0x0490   # #009384  (teal)
COL_C4     = 0xA700   # #A7E100  (lime)
COL_WARN   = 0xFD44   # #f9a825
COL_ERR    = 0x98E3   # #981E1F
COL_WHITE  = 0xFFFF
# Top-bar heater state colours
COL_HEAT   = 0xEB20   # #ED6500  orange  -> heating towards the target
COL_OK     = 0x2DE9   # #2DBF4A  green   -> at/over the target (-3C)
COL_COOL   = 0x2DBE   # #29B6F6  blue    -> cooling (target 0)
COL_DIM    = 0x39E7   # #3A3A3A  grey    -> disabled rail icons

GUI_COLORS = (COL_C1, COL_C2, COL_C3, COL_C4)

# Panels: list of (label, icon, action). action is gcode, or ('scr', name),
# ('mv', axis, sign), or None.
GUI_SCREENS = {
    'main': [
        ("Mover", "move", ('scr', 'move')),
        ("Temperatura", "heatup", ('scr', 'temperature')),
        ("Extruder", "extrude", ('scr', 'extrude')),
        ("Imprimir", "print", ('scr', 'print')),
        ("Nivelar", "bedmesh", 'G29'),
        ("Z Calibrar", "zoffset", ('scr', 'probe')),
        ("Mais", "settings", ('scr', 'more')),
        ("Home", "home", 'G28'),
    ],
    'move': [
        ("Home All", "home", 'G28'),
        ("Y -", "up", ('mv', 'Y', -1)),
        ("Home XY", "home", 'G28 X Y'),
        ("Z +", "up", ('mv', 'Z', 1)),
        ("X -", "left", ('mv', 'X', -1)),
        ("Y +", "down", ('mv', 'Y', 1)),
        ("X +", "right", ('mv', 'X', 1)),
        ("Z -", "down", ('mv', 'Z', -1)),
    ],
    'temperature': [
        ("PLA", "heatup", 'PREHEAT_PLA'),
        ("PETG", "heatup", 'PREHEAT_PETG'),
        ("ABS", "heatup", 'PREHEAT_ABS'),
        ("TPU", "heatup", 'PREHEAT_TPU'),
        ("Arrefecer", "cool", 'M104 S0\nM140 S0'),
        ("Bico 200", "fire", 'M104 S200'),
        ("Cama 60", "bed", 'M140 S60'),
        ("Voltar", "back", ('scr', 'main')),
    ],
    'extrude': [
        ("Carregar", "load", 'LOAD_FILAMENT'),
        ("Descarga", "unload", 'UNLOAD_FILAMENT'),
        ("E +", "edown", ('ex', 1)),
        ("E -", "eup", ('ex', -1)),
        ("Bico 200", "fire", 'M104 S200'),
        ("Bico 240", "fire", 'M104 S240'),
        ("E 1mm", "extrude", ('ext',)),
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
    # 'print' is built dynamically from the virtual SD files (see _print_items)
    'print': [],
    'ptune': [],
    'ptemp': [],
    'network': [],
    'more': [
        ("PID hot", "fire", 'PID_CALIBRATE HEATER=extruder TARGET=200'),
        ("PID bed", "bed", 'PID_CALIBRATE HEATER=heater_bed TARGET=60'),
        ("Guardar", "save", 'SAVE_CONFIG'),
        ("Filam", "extrude", ('fsens',)),
        ("Rede", "network", ('scr', 'network')),
        ("Beep", "alert", ('beep',)),
        ("Motores OFF", "power", 'M84'),
        ("Voltar", "back", ('scr', 'main')),
    ],
}

# Bottom strip: buttons per panel. Value is the step (mm) for step panels, or a
# gcode string for panels with actions.
GUI_STRIP = {
    'move': [('0.1', 0.1), ('0.5', 0.5), ('1', 1.0), ('5', 5.0),
             ('10', 10.0), ('25', 25.0), ('50', 50.0)],
}

# Sentinel stored in the grid for a cell holding an icon (drawn by write_glyph).
ICON = 0x100


def _mk_bmp(n, fn):
    # n x n 1-bit bitmap (MSB first, row-major) from a predicate fn(i, j, n).
    rb = (n + 7) // 8
    out = bytearray()
    for j in range(n):
        bits = 0
        for i in range(n):
            bits = (bits << 1) | (1 if fn(i, j, n) else 0)
        bits <<= (rb * 8 - n)
        for k in range(rb):
            out.append((bits >> ((rb - 1 - k) * 8)) & 0xFF)
    return (n, n, bytes(out))


def _pause_fn(i, j, n):
    if not (0.18 * n <= j < 0.82 * n):
        return False
    return (0.30 * n <= i < 0.42 * n) or (0.58 * n <= i < 0.70 * n)


def _play_fn(i, j, n):
    h = abs(j - 0.5 * n)
    if h >= 0.32 * n:
        return False
    return 0.26 * n <= i < 0.30 * n + 0.44 * n * (1.0 - h / (0.32 * n))


def _x_fn(i, j, n):
    if not (0.16 * n <= i < 0.84 * n and 0.16 * n <= j < 0.84 * n):
        return False
    return abs(i - j) <= 0.08 * n or abs(i - (n - 1 - j)) <= 0.08 * n


# Icons we generate locally (not part of the MDI set).
EXTRA_ICONS = {
    'pause48': _mk_bmp(48, _pause_fn),
    'play48': _mk_bmp(48, _play_fn),
    'x48': _mk_bmp(48, _x_fn),
}


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
        gcode.register_command('TFT_PANEL', self.cmd_TFT_PANEL,
                               desc="Write ST7796 registers live (DIC/VCOM/INVON)")
        gcode.register_command('TFT_SPLIT', self.cmd_TFT_SPLIT,
                               desc="Compare big-rect vs narrow-strip fills")
        gcode.register_command('TFT_GUI', self.cmd_TFT_GUI,
                               desc="Force a full GUI redraw")
        gcode.register_command('TFT_NET', self.cmd_TFT_NET,
                               desc="Show network info / send SSDP announce")
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
        # Button icons are drawn from the 64px bitmaps; scale them down for a
        # lighter look (and much less serial traffic).
        self.gui_icon_scale = config.getfloat('gui_icon_scale', 0.4,
                                             minval=0.15, maxval=1.0)
        self.gui_btn_gap = config.getint('gui_btn_gap', 8, minval=0, maxval=40)
        self._icon_cache = {}
        # Temperature history for the printing screen graph (sampled in the bars)
        self._t_hist = []
        self._t_last = 0.
        self._last_ps_state = 'ready'
        self._zmax_v = 0.
        self._net_cache = None
        self._net_t = 0.
        self._ptune = 'flow'
        self._card_cols = {}
        self._corners = (_make_corner(self.gui_radius, False, False),
                         _make_corner(self.gui_radius, True, False),
                         _make_corner(self.gui_radius, False, True),
                         _make_corner(self.gui_radius, True, True))
        self.gui_screen = 'main'
        self._gui_steps = (0.1, 0.5, 1.0, 5.0, 10.0, 25.0, 50.0)
        self._gui_step = 1.0
        self._ext_steps = (1.0, 5.0, 10.0)
        self._ext_step = 1.0
        self.gui_beep = config.getboolean('gui_beep', True)
        # Refuse filament moves below this nozzle temperature (0 disables).
        self.extrude_min_temp = config.getfloat('extrude_min_temp', 180.,
                                                minval=0.)
        self.filament_sensor = config.get('filament_sensor', 'filament_sensor')
        self._gui_msg = None
        self._gui_msg_until = 0.
        self._gui_action_active = False
        self._gui_ops = None
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
        # Panel tuning (sent by the host, so no reflash needed to experiment)
        self.init_invert = config.getboolean('init_invert', False)
        self.init_dic = int(config.get('init_dic', '0x00'), 0) & 0xFF
        self.init_vcom = int(config.get('init_vcom', '0x22'), 0) & 0xFF
        self.init_vcomofs = int(config.get('init_vcomofs', '0x00'), 0) & 0xFF
        self.init_gamma = config.getboolean('init_gamma', True)
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
        # Virtual SD file browser (Print panel)
        self._sd_page = 0
        self._sd_files_cache = []
        self._sd_cache_t = 0.
        self._sd_per_page = 6
        self._sd_sel = 0
        self._sd_top = 0
        self._sd_vis = 12
        # Track real homing/probing so the display stays off the serial link.
        # (Must not rely on print_stats: a print file starts with G28.)
        self._homing = 0
        self._homing_t = 0.
        # After any GUI action that may move (print/move/home) keep the display
        # silent for a short grace window: the first moves of a print start are
        # short-buffered and a 7 KB redraw on the wire delays them ~15 ms.
        self._gui_grace_until = 0.
        self.printer.register_event_handler("homing:homing_move_begin",
                                            self._homing_begin)
        self.printer.register_event_handler("homing:homing_move_end",
                                            self._homing_end)
        self.printer.register_event_handler("klippy:connect", self._on_connect)
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
        seq = [
            (0xF0, [0xC3]), (0xF0, [0x96]),
            (0x3A, [0x55]),
            (0xB4, [self.init_dic]),
            (0xB7, [0xC6]),
            (0xC1, [0x15]),
            (0xC2, [0xAF]),
            (0xC5, [self.init_vcom]),
            (0xC6, [self.init_vcomofs]),
        ]
        if self.init_gamma:
            seq += [
                (0xE8, [0x40, 0x8A, 0x00, 0x00, 0x29, 0x19, 0xA5, 0x33]),
                (0xE0, [0xF0, 0x04, 0x08, 0x09, 0x08, 0x15, 0x2F, 0x42,
                        0x46, 0x28, 0x15, 0x16, 0x29, 0x2D]),
                (0xE1, [0xF0, 0x04, 0x09, 0x09, 0x08, 0x15, 0x2E, 0x46,
                        0x46, 0x28, 0x15, 0x15, 0x29, 0x2D]),
            ]
        seq += [(0x13, []), (0x53, [0x24]), (0xF0, [0x3C]), (0xF0, [0x69])]
        if self.init_invert:
            seq.append((0x21, []))     # INVON
        seq.append((0x29, []))         # DISPON
        for reg, data in seq:
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
        color = self._rgb565(r, g, b)
        self.cmd_rect.send([0, 0, self.width, self.height, color])
        msg = gcmd.get('MSG', '')
        if msg:
            self._gui_text_center(0, self.width, self.height // 2, msg[:56],
                                  0xFFFF, color, 1)
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
    # --- panel/scan-line diagnostics (host only, no reflash) ---
    def cmd_TFT_PANEL(self, gcmd):
        # Write ST7796 display registers live, to hunt panel banding/stripes.
        if not self.enabled:
            raise gcmd.error("st7796 display not enabled")
        out = []
        dic = gcmd.get_int('DIC', None)
        if dic is not None:
            self.cmd_cmd.send([0xB4])
            self.cmd_data.send([dic & 0xFF])
            out.append("DIC=0x%02X" % (dic & 0xFF))
        vcom = gcmd.get_int('VCOM', None)
        if vcom is not None:
            self.cmd_cmd.send([0xC5])
            self.cmd_data.send([vcom & 0xFF])
            out.append("VCOM=0x%02X" % (vcom & 0xFF))
        ofs = gcmd.get_int('VCOM_OFS', None)
        if ofs is not None:
            self.cmd_cmd.send([0xC6])
            self.cmd_data.send([ofs & 0xFF])
            out.append("VCM_OFS=0x%02X" % (ofs & 0xFF))
        invon = gcmd.get_int('INVON', None)
        if invon is not None:
            self.cmd_cmd.send([0x21 if invon else 0x20])
            out.append("INVON=%d" % (1 if invon else 0))
        gcmd.respond_info("tft panel: " + (", ".join(out) if out else "no change"))
    def cmd_TFT_SPLIT(self, gcmd):
        # Left half = one big rect, right half = many narrow rects, same colour.
        # If only the left half shows the lines, the fill method is to blame;
        # if both do, it is the panel.
        if not self.enabled:
            raise gcmd.error("st7796 display not enabled")
        c = gcmd.get_int('C', 0x1418, minval=0, maxval=0xFFFF)
        w = gcmd.get_int('W', 1, minval=1, maxval=self.width)
        self.cmd_rect.send([0, 0, self.width, self.height, 0x0000])
        half = self.width // 2
        self.cmd_rect.send([0, 0, half, self.height, c])
        x = half
        while x < self.width:
            n = min(w, self.width - x)
            self.cmd_rect.send([x, 0, n, self.height, c])
            x += n
        self._gui_text(6, 6, "1 RECT", 0xFFFF, c)
        self._gui_text(half + 6, 6, "STRIPS W=%d" % w, 0xFFFF, c)
    def cmd_TFT_NET(self, gcmd):
        if gcmd.get_int('ANNOUNCE', 0):
            info = self._net_info()
            ip = self._net_announce()
            gcmd.respond_info("announce (SSDP) -> %s" % ip)
        i = self._net_info()
        gcmd.respond_info("net: ssid=%s ip=%s sinal=%s host=%s"
                          % (i['ssid'], i['ip'], i['qual'], i['host']))
    def cmd_TFT_GUI(self, gcmd):
        if not self.gui:
            return
        scr = gcmd.get('SCREEN', None)
        if scr:
            self.gui_screen = scr
        self._gui_pending = self.printer.get_reactor().monotonic()
    # --- touch (XPT2046 -> Klipper menu gestures) ---
    def _get_menu(self):
        if self.menu is None:
            disp = self.printer.lookup_object('display', None)
            if disp is not None:
                self.menu = getattr(disp, 'menu', None)
        return self.menu
    def _touch_event(self, eventtime):
        if self.gui and self._disp_quiet(eventtime):
            # Printer is homing/moving: keep the serial link completely clear.
            return eventtime + 0.1
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
        if self.touch_debug:
            self._dbg_i = (getattr(self, '_dbg_i', 0) + 1) % 4
            if self._dbg_i == 0:
                self.gcode.respond_info("raw x=%d y=%d z=%d" % (x, y, z))
        if self._mon is not None:
            m = self._mon
            m['zmin'] = min(m['zmin'], z)
            if z >= 40:  # only sample x/y while actually touching
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
            try:
                st = self.printer.lookup_object('print_stats').get_status(
                    eventtime)['state']
            except Exception:
                st = 'ready'
            if st != self._last_ps_state:
                self._last_ps_state = st
                if st in ('printing', 'paused'):
                    # A print just started: jump to the printing screen.
                    self.gui_screen = 'print'
                    self._gui_defer(eventtime, 0.05)
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
    # --- KlipperScreen-like GUI ---
    TOP_H = 30
    RAIL_W = 36
    STRIP_H = 34
    def _gc(self, i):
        return GUI_COLORS[i % len(GUI_COLORS)]
    def _gui_text(self, x, y, text, fg, bg, scale=1):
        for i, ch in enumerate(text.encode('latin-1')):
            self.cmd_char.send([x + i * 8 * scale, y, scale, ch, fg, bg])
    def _gui_text_center(self, x0, w, y, text, fg, bg, scale=1):
        tw = len(text) * 8 * scale
        self._gui_text(x0 + max(0, (w - tw) // 2), y, text, fg, bg, scale)
    @staticmethod
    def _resample(data, w, h, nw, nh):
        # Box-filter downscale of a 1-bit bitmap (MSB first, row-major).
        rowbytes = (w + 7) // 8
        nrb = (nw + 7) // 8
        out = bytearray()
        for j in range(nh):
            j0 = j * h // nh
            j1 = max(j0 + 1, (j + 1) * h // nh)
            bits = 0
            for i in range(nw):
                i0 = i * w // nw
                i1 = max(i0 + 1, (i + 1) * w // nw)
                on = tot = 0
                for sj in range(j0, j1):
                    base = sj * rowbytes
                    for si in range(i0, i1):
                        tot += 1
                        idx = base + (si >> 3)
                        if idx < len(data) and data[idx] & (0x80 >> (si & 7)):
                            on += 1
                bits = (bits << 1) | (1 if tot and on * 2 >= tot else 0)
            bits <<= (nrb * 8 - nw)
            for k in range(nrb):
                out.append((bits >> ((nrb - 1 - k) * 8)) & 0xFF)
        return bytes(out)
    def _scaled_icon(self, name):
        ent = EXTRA_ICONS.get(name)
        if ent is not None:
            return ent            # locally generated, already the right size
        ent = tft_icons.ICONS.get(name)
        if ent is None:
            return None
        s = self.gui_icon_scale
        if s >= 0.999:
            return ent
        key = (name, s)
        res = self._icon_cache.get(key)
        if res is not None:
            return res
        w, h, data = ent
        nw = max(4, int(round(w * s)))
        nh = max(4, int(round(h * s)))
        res = (nw, nh, self._resample(data, w, h, nw, nh))
        self._icon_cache[key] = res
        return res
    def _icon_at(self, name, size):
        ent = EXTRA_ICONS.get(name) or tft_icons.ICONS.get(name)
        if ent is None:
            return None
        w, h, data = ent
        if w == size and h == size:
            return ent
        key = ('@', name, size)
        res = self._icon_cache.get(key)
        if res is not None:
            return res
        res = (size, size, self._resample(data, w, h, size, size))
        self._icon_cache[key] = res
        return res
    def _draw_bmp(self, icon, x, y, fg, bg):
        if icon is None or self.cmd_bmp is None:
            return
        w, h, data = icon
        rowbytes = (w + 7) // 8
        rows_per = max(1, 40 // rowbytes)
        for off in range(0, h, rows_per):
            rows = min(rows_per, h - off)
            chunk = data[off * rowbytes:(off + rows) * rowbytes]
            self.cmd_bmp.send([x, y, w, rows, off, fg, bg, bytes(chunk)])
    def _gui_icon(self, name, x, y, fg, bg):
        self._draw_bmp(self._scaled_icon(name), x, y, fg, bg)
    def _gui_icon_at(self, name, x, y, fg, bg, size):
        self._draw_bmp(self._icon_at(name, size), x, y, fg, bg)
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
    # --- virtual SD file browser ---
    def _sd_dir(self):
        try:
            vsd = self.printer.lookup_object('virtual_sdcard')
            return getattr(vsd, 'sdcard_dirname', None)
        except Exception:
            return None
    def _sd_files(self):
        # [(name, mtime)] newest first.
        now = self.printer.get_reactor().monotonic()
        if now - self._sd_cache_t < 3.:
            return self._sd_files_cache
        self._sd_cache_t = now
        d = self._sd_dir()
        entries = []
        if d and os.path.isdir(d):
            try:
                for n in os.listdir(d):
                    if n.lower().rsplit('.', 1)[-1] not in ('gcode', 'gco',
                                                            'g'):
                        continue
                    try:
                        m = os.stat(os.path.join(d, n)).st_mtime
                    except OSError:
                        m = 0.
                    entries.append((n, m))
            except OSError:
                entries = []
        entries.sort(key=lambda e: -e[1])
        self._sd_files_cache = entries
        return entries
    # Vertical list geometry: the list fills the button area, with up/down/print
    # buttons in a strip on the right.
    RIGHT_W = 80
    LIST_PITCH = 20
    def _list_area(self):
        return (self.RAIL_W, self.TOP_H, self.width - self.RIGHT_W,
                self.height - self.STRIP_H)
    def _list_rows(self):
        x0, y0, x1, y1 = self._list_area()
        pitch = self.LIST_PITCH
        n = max(1, (y1 - y0) // pitch)
        files = self._sd_files()
        self._sd_vis = n
        self._sd_sel = max(0, min(max(0, len(files) - 1), self._sd_sel))
        if self._sd_sel < self._sd_top:
            self._sd_top = self._sd_sel
        if self._sd_sel >= self._sd_top + n:
            self._sd_top = self._sd_sel - n + 1
        self._sd_top = max(0, min(self._sd_top, max(0, len(files) - n)))
        out = []
        for i in range(n):
            k = self._sd_top + i
            if k >= len(files):
                break
            out.append((x0, y0 + i * pitch, x1 - x0, pitch, k, files[k][0]))
        return out
    def _list_btns(self):
        x0, y0, x1, y1 = self._list_area()
        bw = bh = 64
        bx = x1 + (self.RIGHT_W - bw) // 2
        gap = 6
        total = 3 * bh + 2 * gap
        by = y0 + max(0, (y1 - y0 - total) // 2)
        kinds = ('up', 'down', 'print')
        return [(bx, by + i * (bh + gap), bw, bh, k)
                for i, k in enumerate(kinds)]
    def _screen_items(self):
        if self.gui_screen == 'print':
            return []
        return GUI_SCREENS.get(self.gui_screen, [])
    def _gui_buttons(self):
        cols, rows = 4, 2
        x0, y0 = self.RAIL_W, self.TOP_H
        x1, y1 = self.width, self.height - self.STRIP_H
        bw = (x1 - x0) // cols
        bh = (y1 - y0) // rows
        screen = self._screen_items()
        out = []
        for idx in range(cols * rows):
            item = screen[idx] if idx < len(screen) else None
            out.append((x0 + (idx % cols) * bw, y0 + (idx // cols) * bh,
                        bw, bh, idx, item))
        return out
    def _gui_rail(self):
        back = ('scr', 'print') if self.gui_screen in ('ptune', 'ptemp') \
            else ('scr', 'main')
        items = [('back24', back), ('home24', ('scr', 'main')),
                 ('files24', ('scr', 'print')), ('alert24', None)]
        n = len(items)
        area = self.height - self.STRIP_H - self.TOP_H
        step = min(56, (area - 40) // max(1, n - 1))
        total = (n - 1) * step + 24
        y0 = self.TOP_H + (area - total) // 2
        return [(6, y0 + i * step, name, action,
                 # Back/Home are meaningless on the main screen: dim + inert.
                 self.gui_screen == 'main' and action == ('scr', 'main'))
                for i, (name, action) in enumerate(items)]
    def _gui_strip(self):
        items = GUI_STRIP.get(self.gui_screen)
        if not items:
            return []
        n = len(items)
        x0 = self.RAIL_W
        w = (self.width - x0) // n
        y = self.height - self.STRIP_H
        return [(x0 + i * w + 2, y + 3, w - 5, self.STRIP_H - 6, lab, val)
                for i, (lab, val) in enumerate(items)]
    def _temp_color(self, cur, tgt):
        # orange while heating, green once at target, blue when cooling/off
        if tgt <= 0.:
            return COL_COOL
        if cur >= tgt - 3.:
            return COL_OK
        return COL_HEAT
    def _gui_heater(self, icon, iy, tx, st):
        cur = st['temperature']
        tgt = st['target']
        col = self._temp_color(cur, tgt)
        self._gui_icon_at(icon, tx - 28, iy, col, self.gui_bar_bg, 20)
        self._gui_text(tx, 7, ("%d>%d" % (cur, tgt)).ljust(7), col,
                       self.gui_bar_bg, 1)
    def _filament_status(self, eventtime):
        try:
            st = self.printer.lookup_object(
                'filament_switch_sensor ' + self.filament_sensor).get_status(
                    eventtime)
            return bool(st.get('enabled')), bool(st.get('filament_detected'))
        except Exception:
            return None, None
    def _gui_topbar(self, eventtime, fill=True):
        bg = self.gui_bar_bg
        if fill:
            self.cmd_rect.send([0, 0, self.width, self.TOP_H, bg])
        ps = self._print_status(eventtime)
        printing = ps is not None and ps['state'] in ('printing', 'paused')
        if self.gui_screen == 'print' and printing:
            # While printing the top bar shows the file name instead of the
            # temperatures (those are in the info panel).
            fname = os.path.basename(ps.get('filename') or '') or '-'
            self._gui_text(6, 7, (fname + ' ' * 26)[:26], COL_WHITE, bg, 1)
        else:
            try:
                nb = self.printer.lookup_object('extruder').get_status(
                    eventtime)
                bd = self.printer.lookup_object('heater_bed').get_status(
                    eventtime)
            except Exception:
                return
            self._gui_heater('nozzle', 9, 34, nb)
            self._gui_heater('bed', 7, 120, bd)
        if self.gui_screen != 'print':
            self._gui_text_center(0, self.width, 7,
                                  (self.gui_screen.capitalize()
                                   + ' ' * 14)[:14], COL_WHITE, bg, 1)
        try:
            dur = int(self.printer.lookup_object('print_stats').get_status(
                eventtime)['print_duration'])
        except Exception:
            dur = 0
        self._gui_text(self.width - 52, 7,
                       "%02d:%02d" % (dur // 3600, (dur // 60) % 60),
                       COL_WHITE, bg, 1)
        # Filament runout indicator: green = filament ok, red = runout,
        # grey = sensor disabled/unavailable.
        en, det = self._filament_status(eventtime)
        fcol = COL_DIM
        if en and det:
            fcol = COL_OK
        elif en and not det:
            fcol = COL_ERR
        self._gui_icon_at('extrude', self.width - 78, 8, fcol, bg, 18)
    def _gui_bottombar(self, eventtime, fill=True):
        bg = self.gui_bar_bg
        y = self.height - self.STRIP_H
        if fill:
            self.cmd_rect.send([0, y, self.width, self.STRIP_H, bg])
        strip = self._gui_strip()
        if strip:
            for (x, yy, w, h, lab, val) in strip:
                sel = isinstance(val, float) and abs(val - self._gui_step) < 1e-6
                bg = COL_ACTIVE if sel else self.gui_btn_bg
                self._gui_round(x, yy, w, h, bg)
                self._gui_text_center(x, w, yy + (h - 16) // 2, lab,
                                      COL_WHITE, bg, 1)
        else:
            msg = ''
            fg = COL_WHITE
            now = self.printer.get_reactor().monotonic()
            if self._gui_msg and now < self._gui_msg_until:
                msg, fg = self._gui_msg, COL_WARN
            else:
                ds = self.printer.lookup_object('display_status', None)
                if ds is not None:
                    msg = ds.get_status(eventtime).get('message', '') or ''
                if not msg:
                    try:
                        ps = self.printer.lookup_object('print_stats', None)
                        if ps is not None:
                            msg = str(ps.get_status(eventtime).get('state',
                                                                   ''))
                    except Exception:
                        msg = ''
            self._gui_text_center(0, self.width, y + 9,
                                  (str(msg)[:60] + ' ' * 60)[:60], fg,
                                  bg, 1)
    def _gui_bars(self, eventtime):
        sampled = False
        now = self.printer.get_reactor().monotonic()
        if now - self._t_last >= 2.:
            self._t_last = now
            try:
                e = self.printer.lookup_object('extruder').get_status(
                    eventtime)['temperature']
                b = self.printer.lookup_object('heater_bed').get_status(
                    eventtime)['temperature']
            except Exception:
                e = b = 0.
            self._t_hist.append((e, b))
            if len(self._t_hist) > 60:
                self._t_hist.pop(0)
            sampled = True
        if self._gui_ops:
            return
        if (sampled and self.gui_screen == 'print'
                and self._print_view(eventtime)):
            # Refresh just the info panel (icons/values), not the whole screen.
            pop = []
            self._print_view_ops(pop, eventtime, values_only=True)
            self._gui_enqueue(pop)
            return
        self._gui_enqueue([lambda et=eventtime: self._gui_topbar(et, False),
                           lambda et=eventtime: self._gui_bottombar(et,
                                                                    False)])
    def _gui_enqueue(self, ops):
        # Drain the draw commands a few at a time (see _gui_drain): sending a
        # whole redraw synchronously blocks the reactor for ~50ms, which starves
        # the move queue and makes the MCU shut down with "Timer too close".
        if not ops:
            return
        self._gui_ops = ops
        self.printer.get_reactor().register_callback(self._gui_drain)
    def _gui_drain(self, eventtime):
        reactor = self.printer.get_reactor()
        ops = self._gui_ops
        if not ops:
            self._gui_ops = None
            return reactor.NEVER
        if self._disp_quiet(eventtime):
            # Homing/moving started mid-redraw: drop it and redraw when idle.
            self._gui_ops = None
            self._gui_defer(eventtime, 0.4)
            return reactor.NEVER
        t0 = reactor.monotonic()
        while ops:
            ops.pop(0)()
            if reactor.monotonic() - t0 >= 0.003:
                break
        if ops:
            reactor.register_callback(self._gui_drain)
        else:
            self._gui_ops = None
        return reactor.NEVER
    def _gui_defer(self, eventtime, delay=0.4):
        t = eventtime + delay
        if self._gui_pending is None or t < self._gui_pending:
            self._gui_pending = t
    def _homing_begin(self, hmove):
        self._homing += 1
        self._homing_t = self.printer.get_reactor().monotonic()
        logging.info("st7796: homing begin - display muted (depth %d)",
                     self._homing)
    def _homing_end(self, hmove):
        self._homing = max(0, self._homing - 1)
        self._homing_t = self.printer.get_reactor().monotonic()
        logging.info("st7796: homing end - display resumed (depth %d)",
                     self._homing)
    def _on_connect(self):
        self._homing = 0
    def _disp_quiet(self, eventtime):
        # True => do not talk to the MCU at all (no touch poll, no redraw). While
        # the printer runs short-buffered motion (homing / probing / manual move)
        # display traffic delays the trsync_state replies and the MCU shuts down
        # with "Timer too close". Printing has a deep move buffer, so redraws are
        # allowed then - but a G28/G29 inside a print still sets _homing.
        if self._gui_action_active:
            return True
        if self._homing:
            if eventtime - self._homing_t > 300.:   # safety: stale counter
                self._homing = 0
            else:
                return True
        if eventtime < self._gui_grace_until:
            return True                 # just after a GUI action that may move
        try:
            ps = self.printer.lookup_object('print_stats').get_status(eventtime)
            printing = ps['state'] in ('printing', 'paused')
        except Exception:
            printing = False
        try:
            th = self.printer.lookup_object('toolhead')
            st = th.get_status(eventtime)
            buf = st['print_time'] - st['estimated_print_time']
        except Exception:
            return False
        if buf <= 0.001:
            return False                # idle: safe to draw
        # Moves pending. A steady print keeps a deep (1-2 s) buffer, so a redraw
        # is harmless there; a shallow buffer (print start, manual moves) is not.
        return (not printing) or buf < 1.0
    def _gui_list_ops(self, ops):
        x0, y0, x1, y1 = self._list_area()
        ops.append(lambda: self.cmd_rect.send([x0, y0, x1 - x0, y1 - y0,
                                               COL_BG]))
        for (rx, ry, rw, rh, k, name) in self._list_rows():
            bg = COL_ACTIVE if k == self._sd_sel else COL_BG
            ops.append(lambda rx=rx, ry=ry, rw=rw, rh=rh, bg=bg:
                       self.cmd_rect.send([rx, ry, rw, rh, bg]))
            if k == self._sd_sel:
                ops.append(lambda rx=rx, ry=ry, rh=rh:
                           self.cmd_rect.send([rx, ry, 4, rh, COL_C1]))
            ops.append(lambda rx=rx, ry=ry, name=name, bg=bg:
                       self._gui_text(rx + 12, ry + 2, name[:44], COL_WHITE,
                                      bg, 1))
        for (bx, by, bw, bh, kind) in self._list_btns():
            ops.append(lambda bx=bx, by=by, bw=bw, bh=bh:
                       self._gui_round(bx, by, bw, bh, self.gui_btn_bg))
            ops.append(lambda bx=bx, by=by, kind=kind:
                       self._gui_icon(kind, bx, by, COL_WHITE, self.gui_btn_bg))
    def _list_press(self, kind, eventtime):
        if kind == 'up':
            self._sd_sel = max(0, self._sd_sel - 1)
        elif kind == 'down':
            n = len(self._sd_files())
            self._sd_sel = min(max(0, n - 1), self._sd_sel + 1)
        elif kind == 'print':
            files = self._sd_files()
            if files:
                self._gui_do(('sd', files[self._sd_sel][0]), eventtime)
            return
        self._gui_render(eventtime)
        self._gui_run_action("M300 S1000 P60" if self.gui_beep else None)
    # --- printing screen: progress + temperature graphs + pause/stop ---
    def _print_status(self, eventtime):
        try:
            return self.printer.lookup_object('print_stats').get_status(
                eventtime)
        except Exception:
            return None
    def _print_view(self, eventtime):
        ps = self._print_status(eventtime)
        return ps is not None and ps['state'] in ('printing', 'paused')
    def _print_btns(self):
        # The four grid slots on the right hand side (2x2), as (x,y,w,h,idx).
        out = []
        for (x, y, w, h, idx, item) in self._gui_buttons():
            if idx in (2, 3, 6, 7):
                out.append((x, y, w, h, idx))
        return out
    def _graph_ops(self, ops, gx, gy, gw, gh, series, tgt, color, label, cur):
        ops.append(lambda gx=gx, gy=gy, gw=gw, gh=gh:
                   self.cmd_rect.send([gx, gy, gw, gh, self.gui_bar_bg]))
        mx = max(120.0, tgt + 20.0, (max(series) + 20.0) if series else 120.0)
        if tgt > 0:
            ty = gy + gh - 1 - int(min(1.0, tgt / mx) * (gh - 2))
            ops.append(lambda ty=ty: self.cmd_rect.send([gx, ty, gw, 1,
                                                         COL_ACTIVE]))
        n = len(series)
        if n:
            cw = max(2, gw // n)
            for i, v in enumerate(series):
                h = int(min(1.0, v / mx) * (gh - 2))
                if h <= 0:
                    continue
                x = gx + i * gw // n
                yy = gy + gh - 1 - h
                ops.append(lambda x=x, yy=yy, h=h, cw=cw:
                           self.cmd_rect.send([x, yy, cw, h, color]))
        ops.append(lambda gx=gx, gy=gy, label=label, cur=cur, tgt=tgt:
                   self._gui_text(gx + 4, gy + 2, "%s %d>%d" % (label, cur,
                                                                tgt),
                                  COL_WHITE, self.gui_bar_bg, 1))
    def _zmax(self):
        if self._zmax_v:
            return self._zmax_v
        try:
            s = self.printer.lookup_object('configfile').get_status(0)[
                'settings']
            self._zmax_v = float(s['stepper_z']['position_max'])
        except Exception:
            self._zmax_v = 0.
        return self._zmax_v
    def _info_line(self, ops, x, y, xr, lab, val, col):
        # Vertical colour bar, label, right aligned value.
        ops.append(lambda x=x, y=y, col=col: self.cmd_rect.send(
            [x, y + 6, 5, 22, col]))
        ops.append(lambda x=x, y=y, lab=lab: self._gui_text(
            x + 14, y + 9, lab, COL_WHITE, COL_BG, 1))
        if val:
            vw = len(val) * 8
            ops.append(lambda xr=xr, y=y, val=val, vw=vw, col=col:
                       self._gui_text(xr - vw, y + 9, val, col, COL_BG, 1))
    def _card_ops(self, ops, cx, cy, cw, ch, icon, val, col, draw_icon=True):
        # Small coloured icon with the value centred underneath (no label).
        if draw_icon:
            sc = self._icon_at(icon, 22)
            iw = sc[0] if sc else 0
            ops.append(lambda cx=cx, cy=cy, cw=cw, icon=icon, iw=iw, col=col:
                       self._gui_icon_at(icon, cx + (cw - iw) // 2, cy + 5,
                                         col, COL_BG, 22))
        if val:
            ops.append(lambda cx=cx, cy=cy, cw=cw, val=val, col=col:
                       self._gui_text_center(cx, cw, cy + ch - 21,
                                             str(val).center(9), col, COL_BG,
                                             1))
    def _print_view_ops(self, ops, eventtime, values_only=False):
        ps = self._print_status(eventtime)
        if ps is None:
            return
        bw = (self.width - self.RAIL_W) // 4
        try:
            ex = self.printer.lookup_object('extruder').get_status(eventtime)
            bd = self.printer.lookup_object('heater_bed').get_status(eventtime)
            gm = self.printer.lookup_object('gcode_move').get_status(eventtime)
            fn = self.printer.lookup_object('fan').get_status(eventtime)
        except Exception:
            return
        prog = 0.
        try:
            prog = float(self.printer.lookup_object('virtual_sdcard')
                         .get_status(eventtime).get('progress') or 0.)
        except Exception:
            prog = 0.
        if not prog:
            prog = float(ps.get('progress') or 0.)
        prog = min(1., max(0., prog))
        try:
            zc = float(gm['gcode_position'][2])
        except Exception:
            zc = 0.
        speed = float(gm.get('speed_factor') or 1.) * 100.
        flow = float(gm.get('extrude_factor') or 1.) * 100.
        fansp = float(fn.get('speed') or 0.) * 100.
        # Info panel: 2x4 grid of small coloured icons with the value below.
        # No background/outline fill (that flickered) and the progress bar is
        # drawn as text blocks - far fewer MCU commands.
        px0, py0 = self.RAIL_W + 2, self.TOP_H + 2
        pw = 2 * bw - 4
        ph = self.height - self.STRIP_H - self.TOP_H - 4
        bar_h = 24
        ch = (ph - bar_h) // 4
        cw = pw // 2
        cards = [
            ('fire', "%d/%d" % (ex['temperature'], ex['target']),
             self._temp_color(ex['temperature'], ex['target'])),
            ('bed', "%d/%d" % (bd['temperature'], bd['target']),
             self._temp_color(bd['temperature'], bd['target'])),
            ('fan', "%d%%" % int(round(fansp)), COL_C3),
            ('extrude', "%d%%" % int(round(flow)), COL_C4),
            ('speed', "%d%%" % int(round(speed)), COL_C2),
            ('zoffset', "%.1f/%.0f" % (zc, self._zmax()), COL_C1),
            ('print', "%d%%" % int(round(prog * 100)), COL_WHITE),
        ]
        for i, (icon, val, col) in enumerate(cards):
            cx = px0 + (i % 2) * cw
            cy = py0 + (i // 2) * ch
            draw = (not values_only) or (self._card_cols.get(i) != col)
            self._card_cols[i] = col
            self._card_ops(ops, cx, cy, cw, ch, icon, val, col, draw)
        # Progress bar as text: full blocks then light-shade blocks.
        n = max(4, pw // 8)
        f = int(round(prog * n))
        bx = px0 + (pw - n * 8) // 2
        by = py0 + 4 * ch + 4
        if f > 0:
            ops.append(lambda bx=bx, by=by, f=f: self._gui_text(
                bx, by, "\xdb" * f, COL_C1, COL_BG, 1))
        if n - f > 0:
            ops.append(lambda bx=bx, by=by, f=f, n=n: self._gui_text(
                bx + f * 8, by, "\xb0" * (n - f), COL_ACTIVE, COL_BG, 1))
        # Right hand buttons: pause/resume, cancel, tune, temperatures
        paused = ps['state'] == 'paused'
        for (x, y, w, h, idx) in self._print_btns():
            if idx == 2:
                self._btn_ops(ops, x, y, w, h,
                              'play48' if paused else 'pause48',
                              "Retomar" if paused else "Pausa")
            elif idx == 3:
                self._btn_ops(ops, x, y, w, h, 'x48', "Parar", COL_ERR)
            elif idx == 6:
                self._btn_ops(ops, x, y, w, h, 'settings', "Tune")
            elif idx == 7:
                self._btn_ops(ops, x, y, w, h, 'thermo', "Temps")
    def _gui_render(self, eventtime):
        if self._disp_quiet(eventtime) or self._gui_ops:
            self._gui_defer(eventtime)
            return
        # Split the background into strips: a single full-screen tft_rect blocks
        # the MCU for ~30ms and delays the trsync replies.
        ops = []
        for y in range(0, self.height, 20):
            h = min(20, self.height - y)
            ops.append(lambda y=y, h=h: self.cmd_rect.send(
                [0, y, self.width, h, COL_BG]))
        ops.append(lambda: self.cmd_rect.send([0, self.TOP_H, self.RAIL_W,
                                               self.height - self.TOP_H,
                                               self.gui_bar_bg]))
        for (x, y, name, action, dim) in self._gui_rail():
            ops.append(lambda x=x, y=y, name=name, dim=dim: self._gui_icon_at(
                name, x, y, COL_DIM if dim else COL_WHITE, self.gui_bar_bg, 24))
        if self.gui_screen == 'print':
            if self._print_view(eventtime):
                self._print_view_ops(ops, eventtime)
            else:
                self._gui_list_ops(ops)
        elif self.gui_screen == 'network':
            self._net_view_ops(ops, eventtime)
        elif self.gui_screen == 'ptune':
            self._ptune_view_ops(ops, eventtime)
        elif self.gui_screen == 'ptemp':
            self._ptemp_view_ops(ops, eventtime)
        g = self.gui_btn_gap
        i2 = g // 2
        for (x, y, w, h, idx, item) in self._gui_buttons():
            if item is None:
                continue
            label, icon, action = item
            if isinstance(action, tuple) and action[0] == 'beep':
                label = "Beep ON" if self.gui_beep else "Beep OFF"
            elif isinstance(action, tuple) and action[0] == 'ext':
                label = "E %gmm" % self._ext_step
            elif isinstance(action, tuple) and action[0] == 'fsens':
                en, det = self._filament_status(eventtime)
                label = "Filam ON" if en else "Filam OFF"
            sc = self._scaled_icon(icon)
            iw, ih = (sc[0], sc[1]) if sc else (0, 0)
            bx, by = x + i2, y + i2
            bw, bh = w - g, h - g
            iy = by + max(4, (bh - (ih + 8 + 16)) // 2)
            ty = iy + ih + 8
            ops.append(lambda bx=bx, by=by, bw=bw, bh=bh: self._gui_round(
                bx, by, bw, bh, self.gui_btn_bg))
            ops.append(lambda bx=bx, bw=bw, iy=iy, icon=icon, iw=iw:
                       self._gui_icon(icon, bx + (bw - iw) // 2, iy,
                                      COL_WHITE, self.gui_btn_bg))
            ops.append(lambda bx=bx, bw=bw, ty=ty, label=label:
                       self._gui_text_center(bx, bw, ty, label, COL_WHITE,
                                             self.gui_btn_bg, 1))
            ops.append(lambda bx=bx, by=by, bw=bw, bh=bh, idx=idx:
                       self.cmd_rect.send([bx + 10, by + bh - 13, bw - 20, 5,
                                           self._gc(idx)]))
        ops.append(lambda et=eventtime: self._gui_topbar(et))
        ops.append(lambda et=eventtime: self._gui_bottombar(et))
        self._gui_enqueue(ops)
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
    # Z stays >= 0 (the IR trigger) so the endstop never starts triggered.
    AXIS_LIMITS = {'X': (0.0, 320.0), 'Y': (0.0, 320.0), 'Z': (0.0, 400.0)}
    def _move_gcode(self, axis, sign):
        # Use a *relative* move: absolute gcode Z differs from the toolhead Z by
        # the bed-mesh offset. Clamp in toolhead (kinematic) coordinates.
        th = self.printer.lookup_object('toolhead')
        amt = self._gui_step * sign
        if axis in self.AXIS_LIMITS:
            lo, hi = self.AXIS_LIMITS[axis]
            if axis.lower() not in th.get_status(0).get('homed_axes', ''):
                return "G28 %s" % axis
            cur = th.get_position()[{'X': 0, 'Y': 1, 'Z': 2}[axis]]
            amt = max(lo, min(hi, cur + amt)) - cur
            feed = 600 if axis == 'Z' else 3000
            return "G91\nG1 %s%.3f F%d\nG90" % (axis, amt, feed)
        return "G91\nG1 %s%.3f F300\nG90" % (axis, amt)
    _MOVE_TOKENS = ('G28', 'G29', 'G1', 'G2', 'G3', 'SDCARD_PRINT_FILE',
                    'PROBE_CALIBRATE', 'BED_MESH_CALIBRATE', 'TESTZ',
                    'LOAD_FILAMENT', 'UNLOAD_FILAMENT', 'RESTART',
                    'CANCEL_PRINT', 'SET_GCODE_OFFSET')
    def _gui_run_action(self, script):
        # Run GUI gcode in one serialised greenlet (beep + action together), so
        # two actions never interleave, and mark the display busy meanwhile.
        if not script:
            return
        reactor = self.printer.get_reactor()
        if any(t in script for t in self._MOVE_TOKENS):
            # Moving: stay off the wire for a moment after dispatching.
            self._gui_grace_until = reactor.monotonic() + 6.
        self._gui_action_active = True
        def _run(et, s=script):
            try:
                self.gcode.run_script_from_command(s)
            except Exception:
                logging.exception("st7796: GUI action failed")
            finally:
                self._gui_action_active = False
                self._gui_defer(reactor.monotonic(), 0.2)
        reactor.register_async_callback(_run)
    # --- hot-end safeguard -------------------------------------------------
    _E_WORD_RE = re.compile(r"E-?[0-9.]")
    _FEED_MACROS = ('LOAD_FILAMENT', 'UNLOAD_FILAMENT')
    def _nozzle_temp(self, eventtime):
        try:
            return self.printer.lookup_object('extruder').get_status(
                eventtime)['temperature']
        except Exception:
            return None
    def _needs_hot_end(self, script):
        # True for anything that pushes/pulls filament (E moves, load/unload).
        if not script:
            return False
        if any(m in script for m in self._FEED_MACROS):
            return True
        for line in script.split('\n'):
            line = line.split(';')[0].upper().strip()
            if line.startswith('G1') and self._E_WORD_RE.search(line):
                return True
        return False
    def _gui_warn(self, text):
        logging.info("st7796: %s", text)
        try:
            self.gcode.respond_info("st7796: " + text)
        except Exception:
            pass
        self._gui_msg = text
        self._gui_msg_until = self.printer.get_reactor().monotonic() + 4.
        self._gui_next = 0.          # refresh the bottom bar on the next poll
        if self.gui_beep and not self._gui_action_active:
            self._gui_run_action("M300 S400 P150")
    # --- tune / network helpers -------------------------------------------
    def _tune_gcode(self, what, sign, eventtime):
        gm = self.printer.lookup_object('gcode_move').get_status(eventtime)
        if what == 'fan':
            s = float(self.printer.lookup_object('fan').get_status(
                eventtime).get('speed') or 0.)
            s = max(0., min(1., s + sign * 0.2))
            return "M106 S%d" % int(round(s * 255))
        if what == 'flow':
            f = float(gm.get('extrude_factor') or 1.) + sign * 0.05
            return "M221 S%d" % int(round(max(0.5, min(2.0, f)) * 100))
        if what == 'speed':
            f = float(gm.get('speed_factor') or 1.) + sign * 0.05
            return "M220 S%d" % int(round(max(0.25, min(2.0, f)) * 100))
        if what == 'etemp':
            t = self.printer.lookup_object('extruder').get_status(
                eventtime)['target'] + sign * 5.
            return "M104 S%d" % int(max(0., min(270., t)))
        if what == 'btemp':
            t = self.printer.lookup_object('heater_bed').get_status(
                eventtime)['target'] + sign * 5.
            return "M140 S%d" % int(max(0., min(120., t)))
        return None
    def _net_info(self):
        now = self.printer.get_reactor().monotonic()
        if self._net_cache is not None and now - self._net_t < 10.:
            return self._net_cache
        self._net_t = now
        info = {'ssid': '-', 'ip': '-', 'qual': -1,
                'host': socket.gethostname() + '.local'}
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect(('8.8.8.8', 80))
                info['ip'] = s.getsockname()[0]
            finally:
                s.close()
        except Exception:
            pass
        try:
            with open('/proc/net/wireless') as f:
                lines = f.read().splitlines()[2:]
            for line in lines:
                p = line.replace(':', ' ').split()
                if len(p) >= 4:
                    try:
                        info['qual'] = min(100, int(
                            float(p[2].rstrip('.')) * 100 / 70))
                    except Exception:
                        pass
                    ssid = self._ssid()
                    if ssid:
                        info['ssid'] = ssid
                    break
        except Exception:
            pass
        self._net_cache = info
        return info
    def _ssid(self):
        # NetworkManager first (the usual source on current Raspberry Pi OS).
        try:
            out = subprocess.run(['nmcli', '-t', '-f', 'active,ssid', 'dev',
                                  'wifi'], capture_output=True, timeout=3)
            for line in out.stdout.decode(errors='ignore').splitlines():
                if line.startswith('yes:'):
                    return line.split(':', 1)[1].strip()
        except Exception:
            pass
        try:
            for p in glob.glob('/etc/NetworkManager/system-connections/*'):
                with open(p) as f:
                    m = re.search(r'^ssid=(.+)$', f.read(), re.M)
                if m:
                    return m.group(1).strip()
        except Exception:
            pass
        try:
            with open('/etc/wpa_supplicant/wpa_supplicant.conf') as f:
                m = re.search(r'ssid="([^"]+)"', f.read())
            if m:
                return m.group(1)
        except Exception:
            pass
        return None
    def _net_announce(self):
        # SSDP NOTIFY - announce this printer to UPnP/SSDP browsers on the LAN.
        try:
            info = self._net_info()
            ip = info.get('ip') or '0.0.0.0'
            uid = 'uuid:4b6c6970-7065-7200-0000-4d696e676461'
            msgs = []
            for nt in ('upnp:rootdevice', uid,
                       'urn:schemas-upnp-org:device:basic:1'):
                msgs.append(
                    'NOTIFY * HTTP/1.1\r\n'
                    'HOST: 239.255.255.250:1900\r\n'
                    'CACHE-CONTROL: max-age=1800\r\n'
                    'LOCATION: http://%s:7125/\r\n'
                    'SERVER: Klipper/Moonraker UPnP/1.0\r\n'
                    'NT: %s\r\nNTS: ssdp:alive\r\n'
                    'USN: %s::%s\r\n\r\n' % (ip, nt, uid, nt))
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
                for m in msgs:
                    s.sendto(m.encode(), ('239.255.255.250', 1900))
            finally:
                s.close()
            return ip
        except Exception:
            logging.exception("st7796: network announce failed")
            return None
    def _btn_ops(self, ops, x, y, w, h, icon, label, col=COL_WHITE):
        g = self.gui_btn_gap
        i2 = g // 2
        bx, by, bw2, bh2 = x + i2, y + i2, w - g, h - g
        sc = self._scaled_icon(icon)
        iw, ih = (sc[0], sc[1]) if sc else (0, 0)
        iy = by + max(4, (bh2 - (ih + 8 + 16)) // 2)
        ty = iy + ih + 8
        ops.append(lambda bx=bx, by=by, bw2=bw2, bh2=bh2:
                   self._gui_round(bx, by, bw2, bh2, self.gui_btn_bg))
        ops.append(lambda bx=bx, bw2=bw2, iy=iy, icon=icon, iw=iw, col=col:
                   self._gui_icon(icon, bx + (bw2 - iw) // 2, iy, col,
                                  self.gui_btn_bg))
        ops.append(lambda bx=bx, bw2=bw2, ty=ty, label=label:
                   self._gui_text_center(bx, bw2, ty, label, COL_WHITE,
                                         self.gui_btn_bg, 1))
    def _net_view_ops(self, ops, eventtime):
        info = self._net_info()
        bw = (self.width - self.RAIL_W) // 4
        ix0 = self.RAIL_W + 2
        ix1 = self.RAIL_W + 2 * bw - 6
        iy0 = self.TOP_H
        q = info['qual']
        lines = [
            ("SSID", str(info['ssid'])[:18], COL_C1),
            ("IP", str(info['ip']), COL_C3),
            ("Sinal", ("%d%%" % q) if q >= 0 else "-", COL_C4),
            ("Host", str(info['host']), COL_C2),
            ("Porta", "7125 (Moonraker)", COL_ACTIVE),
        ]
        line_h = max(26, (self.height - self.STRIP_H - iy0 - 30)
                     // max(1, len(lines)))
        for i, (lab, val, col) in enumerate(lines):
            self._info_line(ops, ix0, iy0 + i * line_h, ix1, lab, val, col)
        for (x, y, w, h, idx, item) in self._gui_buttons():
            if idx == 2:
                self._btn_ops(ops, x, y, w, h, 'network', "Anunciar")
            elif idx == 3:
                self._btn_ops(ops, x, y, w, h, 'back', "Voltar")
    _PTUNE = ('flow', 'speed', 'fan')
    def _ptune_value(self, eventtime):
        try:
            if self._ptune == 'fan':
                v = float(self.printer.lookup_object('fan').get_status(
                    eventtime).get('speed') or 0.)
                return "%d%%" % int(round(v * 100))
            gm = self.printer.lookup_object('gcode_move').get_status(eventtime)
            if self._ptune == 'speed':
                v = float(gm.get('speed_factor') or 1.)
            else:
                v = float(gm.get('extrude_factor') or 1.)
            return "%d%%" % int(round(v * 100))
        except Exception:
            return "--"
    def _ptune_view_ops(self, ops, eventtime):
        ix0 = self.RAIL_W
        ix1 = self.RAIL_W + 2 * ((self.width - self.RAIL_W) // 4)
        iy0 = self.TOP_H
        ih = self.height - self.STRIP_H - iy0
        names = {'flow': 'FLOW', 'speed': 'SPEED', 'fan': 'FAN'}
        name = names.get(self._ptune, 'FLOW')
        val = self._ptune_value(eventtime)
        ty = iy0 + (ih - (48 + 16 + 80)) // 2
        ops.append(lambda ty=ty, name=name: self._gui_text_center(
            ix0, ix1 - ix0, ty, name, COL_WHITE, COL_BG, 3))
        ops.append(lambda ty=ty, val=val: self._gui_text_center(
            ix0, ix1 - ix0, ty + 64, val, COL_C1, COL_BG, 5))
        for (x, y, w, h, idx, item) in self._gui_buttons():
            if idx == 2:
                self._btn_ops(ops, x, y, w, h, 'settings', "Trocar")
            elif idx == 6:
                self._btn_ops(ops, x, y, w, h, 'back', "Voltar")
            elif idx == 3:
                self._btn_ops(ops, x, y, w, h, 'up', "+")
            elif idx == 7:
                self._btn_ops(ops, x, y, w, h, 'down', "-")
    def _ptemp_view_ops(self, ops, eventtime):
        try:
            ex = self.printer.lookup_object('extruder').get_status(eventtime)
            bd = self.printer.lookup_object('heater_bed').get_status(eventtime)
        except Exception:
            return
        bw = (self.width - self.RAIL_W) // 4
        iy0 = self.TOP_H
        for lab, st, cx in (("Cama", bd, self.RAIL_W),
                            ("Bico", ex, self.RAIL_W + 2 * bw)):
            col = self._temp_color(st['temperature'], st['target'])
            val = "%d/%d" % (st['temperature'], st['target'])
            ops.append(lambda cx=cx, bw=bw, iy0=iy0, lab=lab: self._gui_text_center(
                cx, 2 * bw, iy0 + 22, lab, COL_WHITE, COL_BG, 2))
            ops.append(lambda cx=cx, bw=bw, iy0=iy0, val=val, col=col:
                       self._gui_text_center(cx, 2 * bw, iy0 + 62, val, col,
                                             COL_BG, 3))
        for (x, y, w, h, idx, item) in self._gui_buttons():
            if idx == 4:
                self._btn_ops(ops, x, y, w, h, 'bed', "Cama +")
            elif idx == 5:
                self._btn_ops(ops, x, y, w, h, 'bed', "Cama -")
            elif idx == 6:
                self._btn_ops(ops, x, y, w, h, 'fire', "Bico +")
            elif idx == 7:
                self._btn_ops(ops, x, y, w, h, 'fire', "Bico -")
    def _gui_do(self, action, eventtime):
        script = None
        if isinstance(action, tuple) and action[0] == 'scr':
            self.gui_screen = action[1]
            self._gui_render(eventtime)
        elif isinstance(action, tuple) and action[0] == 'step':
            i = self._gui_steps.index(self._gui_step)
            self._gui_step = self._gui_steps[(i + 1) % len(self._gui_steps)]
            self._gui_render(eventtime)
        elif isinstance(action, tuple) and action[0] == 'beep':
            self.gui_beep = not self.gui_beep
            self._gui_render(eventtime)
        elif isinstance(action, tuple) and action[0] == 'ext':
            i = self._ext_steps.index(self._ext_step)
            self._ext_step = self._ext_steps[(i + 1) % len(self._ext_steps)]
            self._gui_render(eventtime)
        elif isinstance(action, tuple) and action[0] == 'ptv':
            i = self._PTUNE.index(self._ptune)
            self._ptune = self._PTUNE[(i + 1) % len(self._PTUNE)]
            self.gcode.respond_info("GUI tune -> %s" % self._ptune)
            self._gui_render(eventtime)
        elif isinstance(action, tuple) and action[0] == 'tune':
            script = self._tune_gcode(action[1], action[2], eventtime)
            if script:
                self.gcode.respond_info("GUI tune: %s" % script)
        elif isinstance(action, tuple) and action[0] == 'net':
            ip = self._net_announce()
            if ip:
                self.gcode.respond_info(
                    "st7796: anuncio SSDP enviado na rede (%s)" % ip)
            else:
                self._gui_warn("Falha ao anunciar na rede")
        elif isinstance(action, tuple) and action[0] == 'fsens':
            en, det = self._filament_status(eventtime)
            new = 0 if en else 1
            script = "SET_FILAMENT_SENSOR SENSOR=%s ENABLE=%d" % (
                self.filament_sensor, new)
            self.gcode.respond_info("GUI filamento: sensor %s"
                                    % ("ON" if new else "OFF"))
            self._gui_defer(eventtime, 0.3)     # refresh the label
        elif isinstance(action, tuple) and action[0] == 'ex':
            script = "G91\nG1 E%.3f F300\nG90" % (self._ext_step * action[1])
            self.gcode.respond_info("GUI extrude: %g mm"
                                    % (self._ext_step * action[1]))
        elif isinstance(action, tuple) and action[0] == 'sd':
            try:
                ps = self.printer.lookup_object('print_stats').get_status(
                    eventtime)
                state = ps['state']
            except Exception:
                state = 'ready'
            if state in ('printing', 'paused'):
                self._gui_warn("Impressao em curso - cancelar primeiro")
                return
            fname = str(action[1]).replace('"', '')
            self.gcode.respond_info("GUI print: %s" % fname)
            # Clear any stale file first: a cancelled print can leave the
            # virtual sdcard busy ("SD busy" on the next print).
            script = ('SDCARD_RESET_FILE\n'
                      'SDCARD_PRINT_FILE FILENAME="%s"' % fname)
        elif isinstance(action, tuple) and action[0] == 'mv':
            script = self._move_gcode(action[1], action[2])
            self.gcode.respond_info("GUI move: %s" % script.replace('\n', ' '))
        elif action:
            script = action
        if isinstance(script, str) and script.strip().upper().startswith(
                'M84'):
            try:
                st = self.printer.lookup_object('print_stats').get_status(
                    eventtime)['state']
            except Exception:
                st = 'ready'
            if st in ('printing', 'paused'):
                self._gui_warn("Impressao em curso - M84 bloqueado")
                return
        if (isinstance(script, str) and self.extrude_min_temp > 0
                and self._needs_hot_end(script)):
            temp = self._nozzle_temp(eventtime)
            if temp is not None and temp < self.extrude_min_temp:
                self._gui_warn("AVISO: bico %.0fC (min %.0fC) - extrusao"
                               " bloqueada" % (temp, self.extrude_min_temp))
                return
        if self.gui_beep:
            script = "M300 S1000 P60\n" + (script or "")
        self._gui_run_action(script)
    def _gui_tap(self, rx, ry, eventtime):
        sx, sy = self._raw_to_screen(rx, ry)
        self.gcode.respond_info("GUI tap raw=(%d,%d) -> (%d,%d)"
                                % (rx, ry, sx, sy))
        for (x, y, w, h, lab, val) in self._gui_strip():
            if x <= sx < x + w and y <= sy < y + h:
                if isinstance(val, str):
                    self.gcode.respond_info("GUI: %s" % lab)
                    script = val
                    if self.gui_beep:
                        script = "M300 S1000 P60\n" + script
                    self._gui_run_action(script)
                else:
                    self._gui_step = val
                    self.gcode.respond_info("GUI step: %g mm" % val)
                    self._gui_render(eventtime)
                    self._gui_run_action(
                        "M300 S1000 P60" if self.gui_beep else None)
                return
        if self.gui_screen in ('ptune', 'ptemp'):
            for (x, y, w, h, idx, item) in self._gui_buttons():
                if x <= sx < x + w and y <= sy < y + h:
                    if self.gui_screen == 'ptune':
                        if idx == 2:
                            self._gui_do(('ptv',), eventtime)
                        elif idx == 3:
                            self._gui_do(('tune', self._ptune, 1), eventtime)
                        elif idx == 7:
                            self._gui_do(('tune', self._ptune, -1), eventtime)
                        elif idx == 6:
                            self._gui_do(('scr', 'print'), eventtime)
                    else:
                        if idx == 4:
                            self._gui_do(('tune', 'btemp', 1), eventtime)
                        elif idx == 5:
                            self._gui_do(('tune', 'btemp', -1), eventtime)
                        elif idx == 6:
                            self._gui_do(('tune', 'etemp', 1), eventtime)
                        elif idx == 7:
                            self._gui_do(('tune', 'etemp', -1), eventtime)
                    return
        if self.gui_screen == 'network':
            for (x, y, w, h, idx, item) in self._gui_buttons():
                if x <= sx < x + w and y <= sy < y + h:
                    if idx == 2:
                        self.gcode.respond_info("GUI: Anunciar")
                        self._gui_do(('net', 'announce'), eventtime)
                    elif idx == 3:
                        self._gui_do(('scr', 'more'), eventtime)
                    return
        if self.gui_screen == 'print':
            if self._print_view(eventtime):
                ps = self._print_status(eventtime) or {}
                paused = ps.get('state') == 'paused'
                for (x, y, w, h, idx) in self._print_btns():
                    if x <= sx < x + w and y <= sy < y + h:
                        if idx == 2:
                            self.gcode.respond_info(
                                "GUI: %s" % ("Retomar" if paused else "Pausa"))
                            self._gui_do('RESUME' if paused else 'PAUSE',
                                         eventtime)
                        elif idx == 3:
                            self.gcode.respond_info("GUI: Parar")
                            self._gui_do('CANCEL_PRINT', eventtime)
                        elif idx == 6:
                            self.gcode.respond_info("GUI: Tune")
                            self._gui_do(('scr', 'ptune'), eventtime)
                        elif idx == 7:
                            self.gcode.respond_info("GUI: Temperaturas")
                            self._gui_do(('scr', 'ptemp'), eventtime)
                        return
            else:
                for (bx, by, bw, bh, kind) in self._list_btns():
                    if bx <= sx < bx + bw and by <= sy < by + bh:
                        self.gcode.respond_info("GUI file: %s" % kind)
                        self._list_press(kind, eventtime)
                        return
                for (rx, ry, rw, rh, k, name) in self._list_rows():
                    if rx <= sx < rx + rw and ry <= sy < ry + rh:
                        self._sd_sel = k
                        self.gcode.respond_info("GUI file sel: %s" % name)
                        self._gui_render(eventtime)
                        self._gui_run_action("M300 S1000 P60"
                                             if self.gui_beep else None)
                        return
        for (x, y, name, action, dim) in self._gui_rail():
            if x - 4 <= sx < x + 28 and y - 4 <= sy < y + 28:
                if action and not dim:
                    self._gui_do(action, eventtime)
                return
        for (x, y, w, h, idx, item) in self._gui_buttons():
            if item is None:
                continue
            if x <= sx < x + w and y <= sy < y + h:
                label, icon, action = item
                self.gcode.respond_info("GUI: %s" % label)
                self._gui_do(action, eventtime)
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
