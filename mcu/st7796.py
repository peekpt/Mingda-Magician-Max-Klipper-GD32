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
import threading
import urllib.request
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

# Accent colours for the button underlines: the 4 theme accents plus 4 more, so
# each of the 8 grid buttons gets its own colour.
GUI_COLORS = (COL_C1,   # orange   #ED6500
              COL_C2,   # magenta  #B10080
              COL_C3,   # teal     #009384
              COL_C4,   # lime     #A7E100
              0xFF00,   # yellow   #FFE300
              0xF800,   # red      #FF0000
              0x2BDF,   # blue     #2979FF
              0xDEDC)   # light grey / near white #D8DCE0

# Panels: list of (label, icon, action). action is gcode, or ('scr', name),
# ('mv', axis, sign), or None.
GUI_SCREENS = {
    'main': [
        ("Mover", "move", ('scr', 'move')),
        ("Temperatura", "heatup", ('scr', 'temperature')),
        ("Extruder", "extrude", ('scr', 'extrude')),
        ("Imprimir", "print", ('scr', 'print')),
        ("Nivelar", "bedmesh", ('scr', 'mesh')),
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
    'filament': [
        ("Ejetar", "unload", ('fil', 'unload')),
        ("Baby", "zoffset", ('baby', 0)),
        ("0.01", "cog", ('bstep',)),
        ("Save", "save", ('bsave',)),
        ("Inserir", "load", ('fil', 'load')),
        ("Z +", "up", ('baby', 1)),
        ("Z -", "down", ('baby', -1)),
        ("", "", None),
    ],
    'ptune': [],
    'estop': [],
    'power': [
        ("Emergency", "alert", 'M112'),
        ("Klipper", "refresh", 'RESTART'),
        ("Firmware", "console", 'FIRMWARE_RESTART'),
        ("Reboot", "power", ('host', 'reboot')),
        ("Desligar", "power", ('host', 'shutdown')),
        ("Voltar", "back", ('scr', 'main')),
    ],
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

# Screens whose button grid is not the default 4 columns x 2 rows
GUI_ROWS = {'print': 2}

# Generic adjustment popups (one implementation for every info icon).
#   kind: 'temp' reads <src>.target, 'factor' reads gcode_move.<key>,
#         'fan' reads fan.speed, 'baby' is the live Z offset.
POPUP_ITEMS = {
    'etemp': dict(icon='fire', name='Bico', units='C', kind='temp',
                  src='extruder', steps=(1., 5., 10.), default=5.,
                  lo=0., hi=270.),
    'btemp': dict(icon='bed', name='Cama', units='C', kind='temp',
                  src='heater_bed', steps=(1., 5., 10.), default=5.,
                  lo=0., hi=120.),
    'fan': dict(icon='fan', name='Fan', units='%', kind='fan',
                steps=(10., 25., 50.), default=25., lo=0., hi=100.),
    'flow': dict(icon='extrude', name='Flow', units='%', kind='factor',
                 key='extrude_factor', steps=(1., 5.), default=1.,
                 lo=50., hi=200.),
    'speed': dict(icon='speed', name='Speed', units='%', kind='factor',
                  key='speed_factor', steps=(5., 10.), default=5.,
                  lo=25., hi=200.),
    'baby': dict(icon='zoffset', name='Baby', units='mm', kind='baby',
                 steps=(0.005, 0.01, 0.1, 0.5), default=0.01,
                 lo=-5., hi=5., live=True, save=True),
}

# Friendly titles for the top bar (otherwise the screen id is shown)
GUI_TITLES = {'ptune': 'Tune', 'filament': 'Filamento',
              'estop': 'Confirmar', 'mesh': 'Nivelar'}

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


def _dot_fn(i, j, n):
    c = (n - 1) / 2.0
    dx, dy = i - c, j - c
    return dx * dx + dy * dy <= (c + 0.35) ** 2


def _ring_fn(i, j, n):
    c = (n - 1) / 2.0
    dx, dy = i - c, j - c
    r2 = dx * dx + dy * dy
    return (c - 1.7) ** 2 <= r2 <= (c + 0.35) ** 2


def _hourglass_fn(i, j, n):
    # Simple hourglass: solid shape that narrows towards the middle.
    m = 0.12 * n
    if not (m <= i < n - m):
        return False
    if j < 0.13 * n or j >= 0.87 * n:
        return True
    c = 0.5 * n
    d = abs(j - c) / (0.37 * n)
    hw = (n * 0.44) * d + 1.0
    return abs(i - c) <= hw


# Icons we generate locally (not part of the MDI set).
EXTRA_ICONS = {
    'pause48': _mk_bmp(48, _pause_fn),
    'play48': _mk_bmp(48, _play_fn),
    'x48': _mk_bmp(48, _x_fn),
    'hourglass20': _mk_bmp(20, _hourglass_fn),
    'dot10': _mk_bmp(10, _dot_fn),
    'dot12': _mk_bmp(12, _dot_fn),
    'dot22': _mk_bmp(22, _dot_fn),
    'dot16': _mk_bmp(16, _dot_fn),
    'ring16': _mk_bmp(16, _ring_fn),
    'dot18': _mk_bmp(18, _dot_fn),
    'dot30': _mk_bmp(30, _dot_fn),
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
        # Wrap BED_MESH_CALIBRATE / G29 so the display can show the probing map.
        self._mesh_base = None
        self._g29_base = None
        self._mesh_hooked = False
        self._mesh_arm = 0.
        self._mesh_run = 0
        self._mesh_t = 0.
        self._mesh_lmk = 0x7FFFFFFF     # state currently drawn on the panel
        self._mesh_ldone = 0x7FFFFFFF
        self._mesh_ltv = None
        self._mesh_lbr = None
        self._mesh_hook()
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
        self._ui_live_t = 0.
        self._gui_beat = 0.
        self._gui_err = None
        self._gui_err_until = 0.
        self._ic_ids = {}
        self._ic_bytes = 0
        # Only cache icons up to this many bytes (the first firmware build
        # truncated the length to 8 bits, so >255 byte icons were not drawn).
        self._ic_max = config.getint('icon_cache_max', 250, minval=0)
        self._touch_evt = False
        self.cmd_idef = self.cmd_idraw = self.cmd_str = self.cmd_tcfg = None
        gcode.register_output_handler(self._gui_output)
        self._ptune = 'flow'
        self._card_cols = {}
        self._fil_unloaded = False
        self._BSTEPS = (0.005, 0.01, 0.1, 0.5)
        self._bstep_i = 1
        self._baby_dirty = False
        self._popup = None
        self._popup_val = None
        self._popup_step = 1.
        self._popup_save = False
        self._popup_applied = 0.
        self._baby_base = 0.
        # Baby step persisted outside printer.cfg: saved to a small file and
        # re-applied at the start of every print (no SAVE_CONFIG restart).
        self._baby_saved = 0.
        self._baby_file = os.path.expanduser(
            '~/printer_data/config/baby_z.txt')
        # Bed mesh probing map
        self._mesh_active = False
        self._mesh_done = -1
        self._mesh_prev = None
        self._mesh_prev_screen = 'print'
        try:
            old = gcode.ready_gcode_handlers  # noqa
        except Exception:
            old = None
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
        # Optional version stamp drawn in the top bar (debug aid)
        self.gui_version = config.get('gui_version', '').strip()
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
        # Optional fast paths (new firmware): icon cache, string draws and
        # event driven touch.
        self.cmd_idef = self.mcu.try_lookup_command(
            "tft_idef id=%c w=%hu h=%hu off=%hu data=%*s")
        self.cmd_idraw = self.mcu.try_lookup_command(
            "tft_idraw id=%c x=%hu y=%hu fg=%hu bg=%hu")
        self.cmd_str = self.mcu.try_lookup_command(
            "tft_str x=%hu y=%hu scale=%c text=%*s fg=%hu bg=%hu")
        self.cmd_tcfg = self.mcu.try_lookup_command(
            "tft_touch_cfg threshold=%hu period=%hu")
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
        # Event driven touch: the MCU samples the panel and reports changes, so
        # the host never has to poll (no blocking queries on the serial link).
        if self.cmd_tcfg is not None and self.query_touch is not None:
            try:
                self.cmd_tcfg.send([int(self.touch_threshold), 25000])
                self.mcu.register_serial_response(
                    self._touch_report,
                    "tft_touch_result x=%hu y=%hu z=%hu xr=%hu yr=%hu")
                self._touch_evt = True
                logging.info("st7796: event driven touch enabled")
            except Exception:
                self._touch_evt = False
                logging.exception("st7796: event driven touch setup failed")
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
            # Printer is homing/moving: stay off the link. During homing/probing
            # stay completely silent (trsync timing), otherwise send a tiny
            # heartbeat (clock + hourglass) so the screen doesn't look frozen.
            if (self._mesh_run > 0 and not self._homing
                    and not self._gui_ops and eventtime >= self._gui_beat):
                # Leveling: keep the screen alive - temperatures while heating,
                # the map while travelling between probe points. Never during a
                # probe move (_homing): that is when trsync timing is critical.
                self._gui_beat = eventtime + 0.3
                try:
                    self._mesh_update(eventtime)
                except Exception:
                    logging.exception("st7796: mesh update failed")
                return eventtime + 0.1
            if not self._homing and eventtime >= self._gui_beat:
                self._gui_beat = eventtime + 1.0
                try:
                    self._gui_clock(eventtime)
                except Exception:
                    pass
            return eventtime + 0.06
        if self._gui_pending is not None:
            if eventtime >= self._gui_pending:
                self._gui_pending = None
                self._gui_render(eventtime)
        if self.query_touch is None:
            return eventtime + 0.2
        if self._touch_evt:
            # The MCU samples the panel itself (tft_touch_cfg) and only reports
            # changes - nothing to poll, so no blocking query. The periodic GUI
            # work (bars, print-start detection) still has to run here.
            if self.gui:
                self._gui_periodic(eventtime)
            return eventtime + 0.05
        try:
            resp = self.query_touch.send([])
        except Exception:
            return eventtime + 0.1
        self._touch_sample(resp.get('x', 0), resp.get('y', 0),
                           resp.get('z', 0), eventtime)
        return eventtime + (0.03 if self.gui else 0.04)
    def _touch_report(self, params):
        # Unsolicited tft_touch_result from the MCU (event driven touch).
        try:
            et = self.printer.get_reactor().monotonic()
            self._touch_sample(params.get('x', 0), params.get('y', 0),
                               params.get('z', 0), et)
        except Exception:
            logging.exception("st7796: touch report failed")
    def _gui_periodic(self, eventtime):
        # Print-start detection + periodic bars (runs from the touch timer and
        # from touch events).
        try:
            st = self.printer.lookup_object('print_stats').get_status(
                eventtime)['state']
        except Exception:
            st = 'ready'
        if st != self._last_ps_state:
            self._last_ps_state = st
            if st in ('printing', 'paused'):
                # A print just started: jump to the printing screen and apply
                # the saved baby step.
                self.gui_screen = 'print'
                self._gui_defer(eventtime, 0.05)
                self._baby_apply_saved()
        if eventtime >= self._gui_next:
            self._gui_next = eventtime + 0.5
            self._gui_bars(eventtime)
        if (self.gui and self.gui_screen == 'mesh' and not self._gui_ops
                and eventtime >= self._mesh_t):
            # Leveling screen: only redraw what changed (temperatures / probe
            # map) - a full repaint every second flickers.
            self._mesh_t = eventtime + (0.3 if self._mesh_run > 0 else 1.0)
            try:
                self._mesh_update(eventtime)
            except Exception:
                logging.exception("st7796: mesh update failed")
    def _touch_sample(self, x, y, z, eventtime):
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
            return
        if self.gui:
            self._gui_periodic(eventtime)
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
            return
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
    MESH_X0 = RAIL_W + 14
    MESH_Y0 = TOP_H + 16
    def _gc(self, i):
        return GUI_COLORS[i % len(GUI_COLORS)]
    def _gui_text(self, x, y, text, fg, bg, scale=1):
        data = text.encode('latin-1')
        if self.cmd_str is not None:
            # One command per chunk of text (far less serial overhead).
            for i in range(0, len(data), 32):
                self.cmd_str.send([x + i * 8 * scale, y, scale,
                                   bytes(data[i:i + 32]), fg, bg])
            return
        for i, ch in enumerate(data):
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
    def _icon_id(self, key, icon):
        # Cache a bitmap on the MCU (if supported) and return its id.
        if self.cmd_idraw is None or self.cmd_idef is None or key is None:
            return None
        i = self._ic_ids.get(key)
        if i is not None:
            return i if i >= 0 else None
        w, h, data = icon
        size = ((w + 7) // 8) * h
        if (size < 24 or size > self._ic_max or len(self._ic_ids) >= 60
                or self._ic_bytes + size > 22000):
            self._ic_ids[key] = -1
            return None
        i = len(self._ic_ids)
        try:
            for off in range(0, size, 32):
                self.cmd_idef.send([i, w, h, off,
                                    bytes(data[off:off + 32])])
        except Exception:
            logging.exception("st7796: icon upload failed")
            self._ic_ids[key] = -1
            return None
        self._ic_ids[key] = i
        self._ic_bytes += size
        return i
    def _draw_bmp(self, icon, x, y, fg, bg, key=None):
        if icon is None or self.cmd_bmp is None:
            return
        if key is not None and self.cmd_idraw is not None:
            i = self._icon_id(key, icon)
            if i is not None:
                self.cmd_idraw.send([i, x, y, fg, bg])
                return
        w, h, data = icon
        rowbytes = (w + 7) // 8
        rows_per = max(1, 40 // rowbytes)
        for off in range(0, h, rows_per):
            rows = min(rows_per, h - off)
            chunk = data[off * rowbytes:(off + rows) * rowbytes]
            self.cmd_bmp.send([x, y, w, rows, off, fg, bg, bytes(chunk)])
    def _gui_icon(self, name, x, y, fg, bg):
        self._draw_bmp(self._scaled_icon(name), x, y, fg, bg, ('s', name))
    def _gui_icon_at(self, name, x, y, fg, bg, size):
        self._draw_bmp(self._icon_at(name, size), x, y, fg, bg, (name, size))
    def _bmp_at(self, bmp, x, y, fg, bg, key=None):
        w, h, data = bmp
        if w <= 0 or h <= 0 or self.cmd_bmp is None:
            return
        self._draw_bmp(bmp, x, y, fg, bg, key)
    def _gui_round(self, x, y, w, h, color):
        self.cmd_rect.send([x, y, w, h, color])
        r = self.gui_radius
        if r <= 0 or self.cmd_bmp is None:
            return
        tl, tr, bl, br = self._corners
        self._bmp_at(tl, x, y, color, self.gui_bg, ('corner', 0))
        self._bmp_at(tr, x + w - r, y, color, self.gui_bg, ('corner', 1))
        self._bmp_at(bl, x, y + h - r, color, self.gui_bg, ('corner', 2))
        self._bmp_at(br, x + w - r, y + h - r, color, self.gui_bg,
                     ('corner', 3))
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
        cols, rows = 4, GUI_ROWS.get(self.gui_screen, 2)
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
        back = ('scr', 'print') if self.gui_screen in ('ptune', 'filament') \
            else ('scr', 'main')
        items = [('back24', back), ('home24', ('scr', 'main')),
                 ('files24', ('scr', 'print')), ('alert24', ('scr', 'power'))]
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
                                  (GUI_TITLES.get(
                                      self.gui_screen,
                                      self.gui_screen.capitalize())
                                   + ' ' * 14)[:14], COL_WHITE, bg, 1)
        try:
            dur = int(self.printer.lookup_object('print_stats').get_status(
                eventtime)['print_duration'])
        except Exception:
            dur = 0
        self._gui_text(self.width - 96, 7,
                       "%02d:%02d" % (dur // 3600, (dur // 60) % 60),
                       COL_WHITE, bg, 1)
        if self.gui_version:
            self._gui_text(self.width - 170, 7,
                           (self.gui_version + ' ' * 8)[:8], COL_DIM, bg, 1)
        # Filament runout indicator: green = filament ok, red = runout,
        # grey = sensor disabled/unavailable.
        en, det = self._filament_status(eventtime)
        fcol = COL_DIM
        if en and det:
            fcol = COL_OK
        elif en and not det:
            fcol = COL_ERR
        self._gui_icon_at('extrude', self.width - 124, 8, fcol, bg, 18)
        # Hourglass: dark grey normally, WHITE while the touch will not respond
        # (display muted during homing/moves or while a GUI action runs).
        self._draw_hourglass(self._disp_quiet(eventtime))
    def _draw_hourglass(self, dead):
        self._gui_icon_at('hourglass20', self.width - 22, 11,
                          COL_WHITE if dead else COL_DIM, self.gui_bar_bg,
                          11)
    def _gui_clock(self, eventtime):
        # Minimal heartbeat while the display is muted: just the clock + the
        # hourglass (~60 bytes), so the screen does not look frozen.
        try:
            dur = int(self.printer.lookup_object('print_stats').get_status(
                eventtime)['print_duration'])
        except Exception:
            dur = 0
        self._gui_text(self.width - 96, 7,
                       "%02d:%02d" % (dur // 3600, (dur // 60) % 60),
                       COL_WHITE, self.gui_bar_bg, 1)
        self._draw_hourglass(True)
    # --- bed mesh probing map ---------------------------------------------
    def _mesh_hook(self):
        # bed_mesh (and the BED_MESH_CALIBRATE / G29 macros) may be configured
        # after this section, so also retry once the printer is fully built.
        if self._mesh_base is None:
            try:
                base = self.gcode.register_command('BED_MESH_CALIBRATE', None)
            except Exception:
                base = None
            if base is not None:
                self._mesh_base = base
                try:
                    self.gcode.register_command('BED_MESH_CALIBRATE',
                                                self._cmd_mesh_calibrate)
                    logging.info("st7796: BED_MESH_CALIBRATE hooked (probe map)")
                except Exception:
                    logging.exception("st7796: cannot hook BED_MESH_CALIBRATE")
        if self._g29_base is None:
            try:
                base = self.gcode.register_command('G29', None)
            except Exception:
                base = None
            if base is not None:
                self._g29_base = base
                try:
                    self.gcode.register_command('G29', self._cmd_g29)
                    logging.info("st7796: G29 hooked (leveling notice)")
                except Exception:
                    logging.exception("st7796: cannot hook G29")

    def _mesh_notice(self, secs=1800.):
        # Warn the user that leveling heats the bed and the hotend.
        self._gui_msg = ("Nivelamento: aquece cama e bico. Limpa o bico"
                         " antes de continuar!")
        self._gui_msg_until = (
            self.printer.get_reactor().monotonic() + secs)

    def _cmd_g29(self, gcmd):
        if self._g29_base is None:
            return
        reactor = self.printer.get_reactor()
        prev = self.gui_screen
        self._mesh_run += 1
        try:
            self._mesh_notice()
            self._mesh_forget()
            mn, mx, cx, cy = self._mesh_geo()
            logging.info("st7796: nivelamento %dx%d de %s a %s",
                         cx, cy, tuple(mn), tuple(mx))
            if self.gui:
                self.gui_screen = 'mesh'
                self._gui_render(reactor.monotonic())
        except Exception:
            logging.exception("st7796: g29 notice failed")
        try:
            self._g29_base(gcmd)
        finally:
            self._mesh_run = max(0, self._mesh_run - 1)
            if self.gui:
                self.gui_screen = prev or 'print'
                self._gui_defer(reactor.monotonic(), 0.3)
            self._gui_msg = None
            self._gui_msg_until = 0.

    def _cmd_mesh_calibrate(self, gcmd):
        if self._mesh_base is None:
            return
        reactor = self.printer.get_reactor()
        prev = self.gui_screen
        self._mesh_active = True
        self._mesh_run += 1
        self._mesh_done = -1
        self._mesh_arm = 0.
        self._mesh_forget()
        mn, mx, cx, cy = self._mesh_geo()
        logging.info("st7796: nivelamento %dx%d de %s a %s",
                     cx, cy, tuple(mn), tuple(mx))
        try:
            self._mesh_notice(240.)
        except Exception:
            pass
        if self.gui:
            self.gui_screen = 'mesh'
            self._gui_render(reactor.monotonic())
        try:
            self._mesh_base(gcmd)
        finally:
            self._mesh_active = False
            self._mesh_run = max(0, self._mesh_run - 1)
            if self.gui:
                self.gui_screen = prev or 'print'
                self._gui_defer(reactor.monotonic(), 0.3)

    def _mesh_geo(self):
        # (mesh_min, mesh_max, cols, rows) of the configured bed mesh. The
        # bed_mesh status only reports mesh_min/mesh_max once a mesh is loaded
        # (it returns 0,0 before that), so always take them from the config -
        # those are the tool coordinates the probe will actually visit.
        mn, mx, cx, cy = (0., 0.), (300., 300.), 4, 4
        try:
            cfg = self.printer.lookup_object('configfile').get_status(0)[
                'settings'].get('bed_mesh', {})
            mn = tuple(float(v) for v in cfg.get('mesh_min', mn))
            mx = tuple(float(v) for v in cfg.get('mesh_max', mx))
            pc = cfg.get('probe_count', (4, 4))
            cx, cy = max(2, int(pc[0])), max(2, int(pc[1]))
        except Exception:
            pass
        return mn, mx, cx, cy

    def _mesh_cur(self, eventtime):
        try:
            st = self.printer.lookup_object('toolhead').get_status(eventtime)
            pos = st['position']
        except Exception:
            return None
        return float(pos[0]), float(pos[1])

    def _mesh_pts(self, mn, mx, cx, cy):
        # [(screen_x, screen_y, probe index, col, row)] - the probe order is
        # serpentine, the on-screen layout is the plain grid.
        x0, y0 = self.MESH_X0, self.MESH_Y0
        span = 60 * (max(cx, cy) - 1) + 16
        out = []
        for j in range(cy):
            for m in range(cx):
                i = m if j % 2 == 0 else cx - 1 - m
                sx = x0 + int(i * (span - 16) / (cx - 1))
                sy = y0 + int((cy - 1 - j) * (span - 16) / (cy - 1))
                out.append((sx, sy, j * cx + m, i, j))
        return out

    def _mesh_marker(self, pts, mn, mx, cx, cy, eventtime):
        # index of the point the head is closest to (-1 when not probing)
        if not self._mesh_active:
            return -1
        pos = self._mesh_cur(eventtime)
        if pos is None:
            return -1
        hx, hy = pos
        best = None
        for (sx, sy, idx, i, j) in pts:
            px = mn[0] + (mx[0] - mn[0]) * i / max(1, cx - 1)
            py = mn[1] + (mx[1] - mn[1]) * j / max(1, cy - 1)
            d = (px - hx) ** 2 + (py - hy) ** 2
            if best is None or d < best[0]:
                best = (d, idx)
        return best[1] if best else -1

    def _mesh_point_ops(self, ops, sx, sy, idx, mk, done):
        # clear the cell first (the marker is bigger than a dot)
        ops.append(lambda sx=sx, sy=sy: self.cmd_rect.send(
            [sx - 13, sy - 13, 26, 26, COL_BG]))
        if idx == mk:
            # point being probed: white circle with a black square in it
            ops.append(lambda sx=sx, sy=sy: self._bmp_at(
                EXTRA_ICONS['dot22'], sx - 11, sy - 11, COL_WHITE, COL_BG,
                ('dot22', 0)))
            ops.append(lambda sx=sx, sy=sy: self.cmd_rect.send(
                [sx - 5, sy - 5, 10, 10, 0x0000]))
        elif idx <= done:
            ops.append(lambda sx=sx, sy=sy: self._bmp_at(
                EXTRA_ICONS['dot16'], sx - 8, sy - 8, COL_ERR, COL_BG,
                ('dot16', 0)))
        else:
            ops.append(lambda sx=sx, sy=sy: self._bmp_at(
                EXTRA_ICONS['ring16'], sx - 8, sy - 8, COL_ACTIVE, COL_BG,
                ('ring16', 0)))

    def _mesh_progress_ops(self, ops, done, total):
        y = self.MESH_Y0 + 60 * 3 + 24
        ops.append(lambda y=y: self.cmd_rect.send(
            [self.RAIL_W + 16, y, 168, 16, COL_BG]))
        lab = "Ponto %d / %d" % (done + 1, total)
        ops.append(lambda y=y, lab=lab: self._gui_text(
            self.RAIL_W + 16, y, lab, COL_WHITE, COL_BG, 1))

    def _mesh_temp_state(self, eventtime):
        try:
            ex = self.printer.lookup_object('extruder').get_status(eventtime)
            bd = self.printer.lookup_object('heater_bed').get_status(eventtime)
        except Exception:
            return None
        return tuple(("%d/%d" % (st['temperature'], st['target']),
                      self._temp_color(st['temperature'], st['target']))
                     for st in (ex, bd))

    def _mesh_temp_ops(self, ops, eventtime):
        tv = self._mesh_temp_state(eventtime)
        if tv is None:
            return
        tx = self.width - 202
        for k, (icon, (lbl, col)) in enumerate(zip(('nozzle', 'bed'), tv)):
            ty = self.TOP_H + 58 + k * 44
            ico = self._icon_at(icon, 22)
            ops.append(lambda tx=tx, ty=ty: self.cmd_rect.send(
                [tx, ty, 104, 24, COL_BG]))
            ops.append(lambda i=ico, tx=tx, ty=ty, c=col, ic=icon:
                       self._draw_bmp(i, tx, ty, c, COL_BG, ('mticon', ic, 22)))
            ops.append(lambda tx=tx, ty=ty, c=col, s=lbl: self._gui_text(
                tx + 30, ty + 3, s, c, COL_BG, 1))
        self._mesh_ltv = tv

    def _mesh_btns(self):
        bw, bh = 104, 88
        y = self.TOP_H + 128
        x0 = self.width - 2 * bw - 20
        return [(x0, y, bw, bh, 'start'), (x0 + bw + 12, y, bw, bh, 'cancel')]

    def _mesh_btn_ops(self, ops, eventtime):
        running = self._mesh_run > 0
        armed = running and eventtime < self._mesh_arm
        for (x, y, w, h, kind) in self._mesh_btns():
            if kind == 'start':
                icon = 'play48'
                if running:
                    lab, fg, bg = "INICIAR", COL_DIM, self.gui_btn_bg
                else:
                    lab, fg, bg = "INICIAR", COL_WHITE, COL_OK
            else:
                icon = 'x48'
                if running:
                    lab = "CONFIRMAR" if armed else "ABORTAR"
                    fg, bg = COL_WHITE, COL_ERR
                else:
                    lab, fg, bg = "CANCELAR", COL_WHITE, self.gui_btn_bg
            ops.append(lambda x=x, y=y, w=w, h=h, bg=bg: self._gui_round(
                x, y, w, h, bg))
            ops.append(lambda x=x, y=y, w=w, icon=icon, fg=fg, bg=bg:
                       self._gui_icon_at(icon, x + (w - 44) // 2, y + 8,
                                         fg, bg, 44))
            ops.append(lambda x=x, y=y, w=w, h=h, lab=lab, fg=fg, bg=bg:
                       self._gui_text_center(x, w, y + h - 26, lab, fg, bg, 1))
        self._mesh_lbr = (running, armed)

    def _mesh_view_ops(self, ops, eventtime):
        # Full draw of the leveling screen.
        mn, mx, cx, cy = self._mesh_geo()
        pts = self._mesh_pts(mn, mx, cx, cy)
        mk = self._mesh_marker(pts, mn, mx, cx, cy, eventtime)
        if mk > self._mesh_done:
            self._mesh_done = mk
        done = self._mesh_done
        ops.append(lambda: self._gui_text_center(
            self.width - 224, 216, self.TOP_H + 12, "VERIFICAR SE O BICO",
            COL_WARN, COL_BG, 1))
        ops.append(lambda: self._gui_text_center(
            self.width - 224, 216, self.TOP_H + 30, "ESTA SUJO (LIMPAR ANTES)",
            COL_WARN, COL_BG, 1))
        for (sx, sy, idx, i, j) in pts:
            self._mesh_point_ops(ops, sx, sy, idx, mk, done)
        self._mesh_progress_ops(ops, done, len(pts))
        self._mesh_temp_ops(ops, eventtime)
        self._mesh_btn_ops(ops, eventtime)
        self._mesh_lmk, self._mesh_ldone = mk, done

    def _mesh_forget(self):
        # The last redraw was dropped halfway: next update redraws everything.
        self._mesh_lmk = 0x7FFFFFFF
        self._mesh_ldone = 0x7FFFFFFF
        self._mesh_ltv = None
        self._mesh_lbr = None

    def _mesh_update(self, eventtime):
        # Incremental redraw: only the points/temps that actually changed, so
        # the panel does not flicker while leveling.
        mn, mx, cx, cy = self._mesh_geo()
        pts = self._mesh_pts(mn, mx, cx, cy)
        mk = self._mesh_marker(pts, mn, mx, cx, cy, eventtime)
        done = self._mesh_done
        if mk > done:
            done = mk
        ops = []
        lmk, ldone = self._mesh_lmk, self._mesh_ldone
        changed = set()
        if lmk != mk:
            if 0 <= lmk:
                changed.add(lmk)
            if 0 <= mk:
                changed.add(mk)
        if done > ldone:
            for k in range(max(0, ldone + 1), done + 1):
                changed.add(k)
        elif done < ldone:
            for k in range(max(0, done + 1), ldone + 1):
                changed.add(k)
        for (sx, sy, idx, i, j) in pts:
            if idx in changed:
                self._mesh_point_ops(ops, sx, sy, idx, mk, done)
        if done != ldone:
            self._mesh_progress_ops(ops, done, len(pts))
        tv = self._mesh_temp_state(eventtime)
        if tv is not None and tv != self._mesh_ltv:
            self._mesh_temp_ops(ops, eventtime)
        running = self._mesh_run > 0
        if (running, running and eventtime < self._mesh_arm) != self._mesh_lbr:
            self._mesh_btn_ops(ops, eventtime)
        self._mesh_done = done
        self._mesh_lmk, self._mesh_ldone = mk, done
        if ops:
            self._gui_enqueue(ops)

    # --- inline adjustment panel (drawn where the info tiles are) --------
    def _baby_load(self):
        try:
            with open(self._baby_file) as f:
                self._baby_saved = float(f.read().strip() or 0.)
        except Exception:
            self._baby_saved = 0.
        return self._baby_saved

    def _baby_store(self, val):
        try:
            with open(self._baby_file, 'w') as f:
                f.write("%.4f\n" % val)
            self._baby_saved = val
            return True
        except Exception:
            logging.exception("st7796: could not save baby step")
            return False

    def _baby_apply_saved(self):
        if abs(self._baby_saved) < 0.0005:
            return
        script = "SET_GCODE_OFFSET Z=%.4f" % self._baby_saved
        self.gcode.respond_info("GUI: aplicar baby step guardado (%s)"
                                % script)
        self._gui_run_action(script)

    def _popup_state(self):
        return POPUP_ITEMS.get(self._popup)

    def _popup_area(self):
        x0 = self.RAIL_W + 2
        y0 = self.TOP_H + 2
        return x0, y0, (self.width - 100) - x0, 2 * 88

    def _popup_read(self, eventtime):
        it = self._popup_state()
        if it is None:
            return None
        try:
            k = it['kind']
            if k == 'temp':
                return float(self.printer.lookup_object(
                    it['src']).get_status(eventtime)['target'])
            if k == 'factor':
                gm = self.printer.lookup_object(
                    'gcode_move').get_status(eventtime)
                return float(gm.get(it['key']) or 1.) * 100.
            if k == 'fan':
                return float(self.printer.lookup_object('fan').get_status(
                    eventtime).get('speed') or 0.) * 100.
            if k == 'baby':
                gp = self.printer.lookup_object(
                    'gcode_move').get_status(eventtime)['gcode_position']
                return float(gp[2]) - self._baby_base
        except Exception:
            return None
        return None

    def _popup_fmt(self, val):
        it = self._popup_state()
        if it is None or val is None:
            return "-"
        if it['kind'] == 'baby':
            return "%+.3f" % val
        if it['kind'] == 'temp':
            return "%d" % round(val)
        return "%d%%" % round(val)

    def _popup_apply(self, val):
        it = self._popup_state()
        if it is None or val is None:
            return None
        k = it['kind']
        if k == 'temp':
            return ("M104 S%d" if it['src'] == 'extruder'
                    else "M140 S%d") % round(val)
        if k == 'factor':
            return ("M221 S%d" if it['key'] == 'extrude_factor'
                    else "M220 S%d") % round(val)
        if k == 'fan':
            return "M106 S%d" % int(round(val * 255. / 100.))
        if k == 'baby':
            return "SET_GCODE_OFFSET Z_ADJUST=%+.3f MOVE=1" % val
        return None

    def _popup_btns(self):
        # [-][+][step][OK|Save] across the lower half of the panel area
        x0, y0, w, h = self._popup_area()
        bw = (w - 5 * 8) // 4
        bh = 54
        by = y0 + h - bh - 8
        kinds = ('minus', 'plus', 'step', 'save' if self._popup_save
                 else 'ok')
        return [(x0 + 8 + i * (bw + 8), by, bw, bh, k)
                for i, k in enumerate(kinds)]

    def _popup_view_ops(self, ops, eventtime):
        it = self._popup_state()
        if it is None:
            return
        x0, y0, w, h = self._popup_area()
        iw = (self._icon_at(it['icon'], 44) or (0, 0))[0]
        ops.append(lambda iw=iw: self._gui_icon_at(
            it['icon'], x0 + 14, y0 + 14, COL_WHITE, COL_BG, 44))
        ops.append(lambda: self._gui_text(x0 + 74, y0 + 18, it['name'],
                                          COL_WHITE, COL_BG, 1))
        val = self._popup_val if self._popup_val is not None \
            else self._popup_read(eventtime)
        ops.append(lambda val=val: self._gui_text(x0 + 74, y0 + 38,
                                                  self._popup_fmt(val),
                                                  COL_C1, COL_BG, 3))
        if it.get('live'):
            ops.append(lambda: self._gui_text(
                x0 + 74, y0 + 92, "(live - guardado %+.3f)"
                % self._baby_saved, COL_DIM, COL_BG, 1))
        for (bx, by, bw, bh, what) in self._popup_btns():
            if what in ('minus', 'plus'):
                self._sign_btn(ops, bx, by, bw, bh,
                               "+" if what == 'plus' else "-",
                               self._gc(0 if what == 'plus' else 1))
            elif what == 'step':
                bg = self.gui_btn_bg
                ops.append(lambda bx=bx, by=by, bw=bw, bh=bh, bg=bg:
                           self._gui_round(bx, by, bw, bh, bg))
                ops.append(lambda bx=bx, bw=bw, by=by, bh=bh, bg=bg:
                           self._gui_text_center(
                               bx, bw, by + (bh - 16) // 2,
                               "%g" % self._popup_step, COL_WHITE, bg, 1))
            else:
                # OK -> check mark (with a green line), Save -> floppy disk
                if what == 'save':
                    icon, bg, acc = 'save', COL_OK, None
                else:
                    icon, bg, acc = 'check', self.gui_btn_bg, COL_OK
                isz = 30
                iw = (self._icon_at(icon, isz) or (0, 0))[0]
                ops.append(lambda bx=bx, by=by, bw=bw, bh=bh, bg=bg:
                           self._gui_round(bx, by, bw, bh, bg))
                ops.append(lambda bx=bx, by=by, bw=bw, bh=bh, icon=icon,
                           bg=bg, isz=isz, iw=iw: self._gui_icon_at(
                               icon, bx + (bw - iw) // 2,
                               by + (bh - isz) // 2, COL_WHITE, bg, isz))
                if acc is not None:
                    ops.append(lambda bx=bx, by=by, bw=bw, bh=bh, acc=acc:
                               self.cmd_rect.send([bx + 10, by + bh - 11,
                                                   bw - 20, 5, acc]))

    def _popup_open(self, key, eventtime):
        it = POPUP_ITEMS.get(key)
        if it is None:
            return
        self._popup = key
        self._popup_step = it['default']
        self._popup_val = None
        self._popup_save = bool(it.get('save'))
        self._popup_applied = 0.
        if it.get('live'):
            try:
                gp = self.printer.lookup_object(
                    'gcode_move').get_status(0)['gcode_position']
                self._baby_base = float(gp[2])
            except Exception:
                self._baby_base = 0.
        self._gui_render(eventtime)

    def _popup_close(self, eventtime, cancel=False):
        it = self._popup_state()
        if cancel and it is not None and it.get('live') and self._popup_applied:
            script = "SET_GCODE_OFFSET Z_ADJUST=%+.3f MOVE=1" % (
                -self._popup_applied)
            self.gcode.respond_info("GUI: cancelar baby step (%s)" % script)
            self._gui_run_action(script)
        self._popup = None
        self._popup_val = None
        self._popup_applied = 0.
        self._gui_render(eventtime)

    def _popup_tap(self, sx, sy, eventtime):
        it = self._popup_state()
        if it is None:
            return False
        for (bx, by, bw, bh, what) in self._popup_btns():
            if not (bx <= sx < bx + bw and by <= sy < by + bh):
                continue
            if what in ('minus', 'plus'):
                sign = 1. if what == 'plus' else -1.
                if it.get('live'):
                    amt = self._popup_step * sign
                    cur = self._popup_read(eventtime)
                    if cur is None or cur + amt < it['lo'] \
                            or cur + amt > it['hi']:
                        return True
                    script = self._popup_apply(amt)
                    self._popup_applied += amt
                    self.gcode.respond_info("GUI %s %+.3f" % (it['name'], amt))
                    self._gui_run_action(script)
                    self._gui_render(eventtime)
                else:
                    cur = self._popup_val
                    if cur is None:
                        cur = self._popup_read(eventtime)
                    if cur is None:
                        return True
                    self._popup_val = max(
                        it['lo'], min(it['hi'], cur + self._popup_step * sign))
                    self._gui_render(eventtime)
                return True
            if what == 'step':
                vals = it['steps']
                if len(vals) > 1:
                    i = vals.index(self._popup_step) \
                        if self._popup_step in vals else 0
                    self._popup_step = vals[(i + 1) % len(vals)]
                    self._gui_render(eventtime)
                return True
            if what == 'ok':
                val = self._popup_val
                if val is not None:
                    script = self._popup_apply(val)
                    if script:
                        self.gcode.respond_info("GUI %s: %s" % (it['name'],
                                                                script))
                        self._gui_run_action(script)
                self._popup_close(eventtime)
                return True
            if what == 'save':
                total = self._baby_saved + self._popup_applied
                ok = self._baby_store(total)
                self._popup_applied = 0.
                self.gcode.respond_info(
                    "GUI: baby step guardado %+.3f (%s) - aplica-se no inicio de"
                    " cada impressao" % (total,
                                         "ok" if ok else "ERRO a gravar"))
                self._gui_msg = "Baby step guardado %+.3f" % total
                self._gui_msg_until = (
                    self.printer.get_reactor().monotonic() + 6.)
                self._popup_close(eventtime)
                return True
        return False

    def _gui_output(self, msg):
        # Klipper prefixes gcode errors with "!!" - keep the last one so it can
        # be shown on the panel.
        try:
            if msg.startswith('!!'):
                self._gui_err = msg[2:].strip()[:56]
                self._gui_err_until = (
                    self.printer.get_reactor().monotonic() + 15.)
                self._gui_next = 0.
        except Exception:
            pass
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
            if self._gui_err and now < self._gui_err_until:
                msg, fg = "ERRO: " + self._gui_err, COL_ERR
            elif self._gui_msg and now < self._gui_msg_until:
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
            m = str(msg)[:56]
            self._bot_max = max(getattr(self, '_bot_max', 0), len(m))
            self._gui_text_center(0, self.width, y + 9,
                                  (' ' + m.center(self._bot_max + 2) + ' ')
                                  [:(self._bot_max + 4)], fg, bg, 1)
    def _gui_bars(self, eventtime):
        if self._popup is not None:
            return              # modal popup covers everything: leave it alone
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
        if (sampled and self._popup is None and self.gui_screen == 'print'
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
        if self._disp_quiet(eventtime) and not self._mesh_can_draw():
            # Homing/moving started mid-redraw: drop it and redraw when idle.
            self._gui_ops = None
            self._mesh_forget()
            self._gui_defer(eventtime, 0.4)
            return reactor.NEVER
        t0 = reactor.monotonic()
        while ops:
            try:
                ops.pop(0)()
            except Exception:
                # e.g. the serial link is closing (shutdown/restart): drop the
                # rest of the redraw instead of taking klippy down.
                logging.exception("st7796: display draw failed")
                self._gui_ops = None
                return reactor.NEVER
            if reactor.monotonic() - t0 >= 0.010:
                break
            if self.gui_screen == 'mesh' and self._mesh_run > 0 \
                    and self._homing:
                # a probe move just started: stop the map update instantly
                self._gui_ops = None
                self._mesh_forget()
                return reactor.NEVER
        if ops:
            reactor.register_callback(self._gui_drain)
        else:
            self._gui_ops = None
            self._ui_live_t = reactor.monotonic()
        return reactor.NEVER
    def _mesh_can_draw(self):
        # The leveling screen keeps refreshing while G29 runs (heating, and the
        # travels between probe points). It still goes completely silent for the
        # probe moves themselves (_homing) - that is where trsync is critical.
        return (bool(self.gui) and self.gui_screen == 'mesh'
                and self._mesh_run > 0 and not self._homing)

    def _gui_defer(self, eventtime, delay=0.2):
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
        self._mesh_hook()
        self._homing = 0
        self._baby_load()
        if self._baby_saved:
            logging.info("st7796: baby step guardado %+.3f", self._baby_saved)
    def _disp_quiet(self, eventtime):
        # True => do not talk to the MCU at all (no touch poll, no redraw). While
        # the printer runs short-buffered motion (homing / probing / manual move)
        # display traffic delays the trsync_state replies and the MCU shuts down
        # with "Timer too close". Printing has a deep move buffer, so redraws are
        # allowed then - but a G28/G29 inside a print still sets _homing.
        if self._gui_action_active:
            return not self._mesh_can_draw()
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
            isz = 34                    # smaller icon, centred in the square
            ops.append(lambda bx=bx, by=by, bw=bw, bh=bh:
                       self._gui_round(bx, by, bw, bh, self.gui_btn_bg))
            ops.append(lambda bx=bx, by=by, bw=bw, bh=bh, kind=kind, isz=isz:
                       self._gui_icon_at(kind, bx + (bw - isz) // 2,
                                         by + (bh - isz) // 2, COL_WHITE,
                                         self.gui_btn_bg, isz))
            if kind == 'print':
                lcol = COL_OK              # green accent line for print
            elif kind == 'down':
                lcol = COL_C3
            else:
                lcol = COL_C1
            ops.append(lambda bx=bx, by=by, bw=bw, bh=bh, lcol=lcol:
                       self.cmd_rect.send([bx + 10, by + bh - 9, bw - 20, 4,
                                           lcol]))
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
        # 1 x 2 column on the right: Pause/Continue (idx 2) + Cancel (idx 3).
        bw = 92
        x = self.width - bw - 6
        y0 = self.TOP_H + 4
        y1 = self.height - self.STRIP_H - 6
        h = (y1 - y0 - 8) // 2
        return [(x, y0, bw, h, 2), (x, y0 + h + 8, bw, h, 3)]
    _PRINT_KEYS = ('etemp', 'btemp', 'fan', 'flow', 'speed', 'baby')
    def _print_panel(self):
        # (x, y, w, h, index, popup key) for the 3x2 tappable info tiles.
        x0 = self.RAIL_W + 2
        x1 = self.width - 100
        y0 = self.TOP_H + 2
        tw = (x1 - x0) // 3
        th = 88
        return [(x0 + (i % 3) * tw, y0 + (i // 3) * th, tw, th, i, k)
                for i, k in enumerate(self._PRINT_KEYS)]
    def _panel_value(self, key, eventtime):
        it = POPUP_ITEMS[key]
        try:
            if it['kind'] == 'temp':
                st = self.printer.lookup_object(
                    it['src']).get_status(eventtime)
                return ("%d/%d" % (st['temperature'], st['target']),
                        self._temp_color(st['temperature'], st['target']))
            if it['kind'] == 'factor':
                gm = self.printer.lookup_object(
                    'gcode_move').get_status(eventtime)
                return ("%d%%" % int(round(float(
                    gm.get(it['key']) or 1.) * 100)), COL_C4)
            if it['kind'] == 'fan':
                v = float(self.printer.lookup_object('fan').get_status(
                    eventtime).get('speed') or 0.) * 100.
                return ("%d%%" % int(round(v)), COL_C3)
            if it['kind'] == 'baby':
                gp = self.printer.lookup_object(
                    'gcode_move').get_status(eventtime)['gcode_position']
                return ("%.2f" % float(gp[2]), COL_C1)
        except Exception:
            pass
        return ("-", COL_DIM)
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
        # Small coloured icon, the value centred underneath and a coloured
        # accent line - same style as the menu buttons.
        top = cy + max(0, (ch - 50) // 2)
        if draw_icon:
            sc = self._icon_at(icon, 22)
            iw = sc[0] if sc else 0
            ops.append(lambda cx=cx, top=top, cw=cw, icon=icon, iw=iw, col=col:
                       self._gui_icon_at(icon, cx + (cw - iw) // 2, top, col,
                                         COL_BG, 22))
        if val:
            ops.append(lambda cx=cx, top=top, cw=cw, val=val, col=col:
                       self._gui_text_center(cx, cw, top + 26,
                                             str(val).center(9), col, COL_BG,
                                             1))
    def _print_view_ops(self, ops, eventtime, values_only=False):
        ps = self._print_status(eventtime)
        if ps is None:
            return
        if self._popup is not None:
            # adjustment panel drawn where the info tiles normally are
            self._popup_view_ops(ops, eventtime)
        else:
            for (tx, ty, tw, th, i, k) in self._print_panel():
                val, col = self._panel_value(k, eventtime)
                it = POPUP_ITEMS[k]
                draw = (not values_only) or (self._card_cols.get(i) != col)
                self._card_cols[i] = col
                self._card_ops(ops, tx, ty, tw, th, it['icon'], val, col,
                               draw)
        # X / Y / Z line
        try:
            gp = self.printer.lookup_object(
                'gcode_move').get_status(eventtime)['gcode_position']
            xyz = "X%.1f Y%.1f Z%.1f" % (float(gp[0]), float(gp[1]),
                                         float(gp[2]))
        except Exception:
            xyz = "X- Y- Z-"
        py = self.TOP_H + 2 + 2 * 88
        ops.append(lambda xyz=xyz, py=py: self._gui_text_center(
            self.RAIL_W, self.width - 100 - self.RAIL_W, py + 4, xyz,
            COL_WHITE, COL_BG, 1))
        # progress bar (with side padding) and its percentage
        prog = 0.
        try:
            prog = float(self.printer.lookup_object('virtual_sdcard')
                         .get_status(eventtime).get('progress') or 0.)
        except Exception:
            prog = 0.
        if not prog:
            prog = float(ps.get('progress') or 0.)
        prog = min(1., max(0., prog))
        px0 = self.RAIL_W + 18
        bwp = self.width - 100 - self.RAIL_W - 36
        n = max(4, bwp // 8)
        f = int(round(prog * n))
        bx = px0 + (bwp - n * 8) // 2
        by = py + 24
        if f > 0:
            ops.append(lambda bx=bx, by=by, f=f: self._gui_text(
                bx, by, "\xdb" * f, COL_C1, COL_BG, 1))
        if n - f > 0:
            ops.append(lambda bx=bx, by=by, f=f, n=n: self._gui_text(
                bx + f * 8, by, "\xb0" * (n - f), COL_ACTIVE, COL_BG, 1))
        pct = "%d%%" % int(round(prog * 100))
        tx = bx + max(0, (n * 8 - len(pct) * 8) // 2)
        ops.append(lambda tx=tx, by=by, pct=pct: self._gui_text(
            tx, by, pct, COL_WHITE, COL_BG, 1))
        # Right column: Pause/Continue (idx 2) + Cancel (idx 3)
        paused = ps['state'] == 'paused'
        for (x, y, w, h, idx) in self._print_btns():
            acc = self._gc(idx)
            if idx == 2:
                self._btn_ops(ops, x, y, w, h,
                              'play48' if paused else 'pause48',
                              "Continuar" if paused else "Pausa", accent=acc)
            else:
                self._btn_ops(ops, x, y, w, h, 'x48', "Cancelar", COL_ERR,
                              accent=acc)
    def _gui_render(self, eventtime):
        if self._gui_ops:
            return
        if self._disp_quiet(eventtime) and not self._mesh_can_draw():
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
            col = COL_DIM if dim else COL_WHITE
            if isinstance(action, tuple) and (action[0] == 'estop' or
                                              action[0] == 'host' or
                                              action == ('scr', 'power') or
                                              action == ('scr', 'estop')):
                col = COL_ERR                 # power menu: always red
            ops.append(lambda x=x, y=y, name=name, col=col: self._gui_icon_at(
                name, x, y, col, self.gui_bar_bg, 24))
        if self.gui_screen == 'print':
            if self._print_view(eventtime):
                self._print_view_ops(ops, eventtime)
            else:
                self._gui_list_ops(ops)
        elif self.gui_screen == 'network':
            self._net_view_ops(ops, eventtime)
        elif self.gui_screen == 'ptune':
            self._ptune_view_ops(ops, eventtime)
        elif self.gui_screen == 'estop':
            self._estop_view_ops(ops, eventtime)
        elif self.gui_screen == 'mesh':
            self._mesh_view_ops(ops, eventtime)
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
            elif isinstance(action, tuple) and action[0] == 'bstep':
                label = "%g" % self._BSTEPS[self._bstep_i]
            elif isinstance(action, tuple) and action[0] == 'baby' \
                    and action[1] == 0:
                try:
                    gz = self.printer.lookup_object(
                        'gcode_move').get_status(eventtime)['gcode_position']
                    label = "Baby %.3f" % float(gz[2])
                except Exception:
                    label = "Baby -"
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
                    'CANCEL_PRINT', 'SET_GCODE_OFFSET', 'SAVE_CONFIG',
                    'Z_OFFSET_APPLY_PROBE')
    def _gui_run_action(self, script):
        # Run GUI gcode in one serialised greenlet (beep + action together), so
        # two actions never interleave, and mark the display busy meanwhile.
        if not script:
            return
        reactor = self.printer.get_reactor()
        if any(t in script for t in self._MOVE_TOKENS):
            # Moving: stay off the wire for a moment after dispatching.
            self._gui_grace_until = reactor.monotonic() + 2.5
        self._gui_action_active = True
        # Tell the user straight away that taps will be ignored from now on.
        try:
            self._draw_hourglass(True)
        except Exception:
            pass
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
            f = float(gm.get('extrude_factor') or 1.) + sign * 0.01
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
    def _btn_ops(self, ops, x, y, w, h, icon, label, col=COL_WHITE,
                 accent=None):
        g = self.gui_btn_gap
        i2 = g // 2
        bx, by, bw2, bh2 = x + i2, y + i2, w - g, h - g
        sz = 0
        sc = self._scaled_icon(icon)
        iw, ih = (sc[0], sc[1]) if sc else (0, 0)
        if ih + 42 > bh2:                     # short button: shrink the icon
            sz = max(16, bh2 - 42)
            iw = ih = sz
        iy = by + max(4, (bh2 - (ih + 8 + 16)) // 2)
        ty = iy + ih + 8
        ops.append(lambda bx=bx, by=by, bw2=bw2, bh2=bh2:
                   self._gui_round(bx, by, bw2, bh2, self.gui_btn_bg))
        if sz:
            ops.append(lambda bx=bx, bw2=bw2, iy=iy, icon=icon, iw=iw,
                       col=col, sz=sz: self._gui_icon_at(
                           icon, bx + (bw2 - iw) // 2, iy, col,
                           self.gui_btn_bg, sz))
        else:
            ops.append(lambda bx=bx, bw2=bw2, iy=iy, icon=icon, iw=iw,
                       col=col: self._gui_icon(
                           icon, bx + (bw2 - iw) // 2, iy, col,
                           self.gui_btn_bg))
        ops.append(lambda bx=bx, bw2=bw2, ty=ty, label=label:
                   self._gui_text_center(bx, bw2, ty, label, COL_WHITE,
                                         self.gui_btn_bg, 1))
        if accent is not None:
            ops.append(lambda bx=bx, by=by, bw2=bw2, bh2=bh2, accent=accent:
                       self.cmd_rect.send([bx + 10, by + bh2 - 13, bw2 - 20, 5,
                                           accent]))
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
    _TUNE_ROWS = (('Bico', 'etemp', 5.), ('Cama', 'btemp', 5.),
                  ('Flow', 'flow', 1.))
    def _tune_row_btns(self):
        # (label, value, colour, key, row_y, row_h, plus_rect, minus_rect)
        try:
            ex = self.printer.lookup_object('extruder').get_status(0)
            bd = self.printer.lookup_object('heater_bed').get_status(0)
            gm = self.printer.lookup_object('gcode_move').get_status(0)
        except Exception:
            return []
        vals = {
            'etemp': ("%d/%d" % (ex['temperature'], ex['target']),
                      self._temp_color(ex['temperature'], ex['target'])),
            'btemp': ("%d/%d" % (bd['temperature'], bd['target']),
                      self._temp_color(bd['temperature'], bd['target'])),
            'flow': ("%d%%" % int(round(float(
                gm.get('extrude_factor') or 1.) * 100)), COL_C4),
        }
        x1 = self.width
        y0, y1 = self.TOP_H, self.height - self.STRIP_H
        rh = (y1 - y0) // len(self._TUNE_ROWS)
        bw = 84
        bh = min(64, rh - 14)
        out = []
        for i, (lab, key, step) in enumerate(self._TUNE_ROWS):
            val, col = vals[key]
            ry = y0 + i * rh
            by = ry + (rh - bh) // 2
            plus = (x1 - 2 * bw - 22, by, bw, bh, key, step)
            minus = (x1 - bw - 10, by, bw, bh, key, -step)
            out.append((lab, val, col, key, ry, rh, plus, minus))
        return out
    def _sign_btn(self, ops, x, y, w, h, sign, accent):
        ops.append(lambda x=x, y=y, w=w, h=h: self._gui_round(
            x, y, w, h, self.gui_btn_bg))
        tw = len(sign) * 8 * 3
        ops.append(lambda x=x, y=y, w=w, h=h, sign=sign, tw=tw:
                   self._gui_text(x + max(0, (w - tw) // 2),
                                  y + max(0, (h - 48) // 2), sign,
                                  COL_WHITE, self.gui_btn_bg, 3))
        if accent is not None:
            ops.append(lambda x=x, y=y, w=w, h=h, accent=accent:
                       self.cmd_rect.send([x + 10, y + h - 11, w - 20, 5,
                                           accent]))
    def _ptune_view_ops(self, ops, eventtime):
        # Table: name + current/target value on the left, +/- buttons on the
        # right (fixed step per row).
        for (lab, val, col, key, ry, rh, plus, minus) in self._tune_row_btns():
            ops.append(lambda ry=ry, lab=lab: self._gui_text(
                self.RAIL_W + 10, ry + 8, lab, COL_WHITE, COL_BG, 1))
            ops.append(lambda ry=ry, val=val, col=col: self._gui_text(
                self.RAIL_W + 10, ry + 30, val, col, COL_BG, 2))
            p = plus
            self._sign_btn(ops, p[0], p[1], p[2], p[3], "+", self._gc(0))
            m = minus
            self._sign_btn(ops, m[0], m[1], m[2], m[3], "-", self._gc(1))
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
    def _big_btn(self, ops, x, y, w, h, label, bg, fg):
        ops.append(lambda x=x, y=y, w=w, h=h, bg=bg: self._gui_round(
            x, y, w, h, bg))
        tw = len(label) * 32
        tx = x + max(0, (w - tw) // 2)
        ty = y + max(0, (h - 64) // 2)
        ops.append(lambda tx=tx, ty=ty, label=label, bg=bg, fg=fg:
                   self._gui_text(tx, ty, label, fg, bg, 4))
    def _estop_btns(self):
        bw = (self.width - self.RAIL_W) // 4
        y = self.TOP_H + 104
        h = self.height - self.STRIP_H - y - 8
        return [(self.RAIL_W + 6, y, 2 * bw - 12, h, 'no'),
                (self.RAIL_W + 2 * bw + 6, y, 2 * bw - 12, h, 'yes')]
    def _estop_view_ops(self, ops, eventtime):
        ops.append(lambda: self._gui_text_center(
            0, self.width, self.TOP_H + 26, "CONFIRMAR?", COL_WARN, COL_BG, 3))
        for (x, y, w, h, kind) in self._estop_btns():
            if kind == 'yes':
                self._big_btn(ops, x, y, w, h, "SIM", COL_ERR, COL_WHITE)
            else:
                self._big_btn(ops, x, y, w, h, "NAO", self.gui_btn_bg,
                              COL_WHITE)
    def _host_power(self, what):
        # Runs in a worker thread: ask Moonraker (it holds the privileges).
        try:
            req = urllib.request.Request(
                'http://127.0.0.1:7125/machine/%s' % what, data=b'',
                method='POST')
            with urllib.request.urlopen(req, timeout=8) as r:
                r.read()
            logging.info("st7796: host %s ok", what)
            return True
        except Exception:
            logging.exception("st7796: host %s failed", what)
            return False
    def _gui_do(self, action, eventtime):
        script = None
        if isinstance(action, tuple) and action[0] == 'scr':
            if self._popup is not None:
                # rail back / other navigation: ignore the pending change
                self._popup_close(eventtime, cancel=True)
                if action[1] == self.gui_screen:
                    return
            self.gui_screen = action[1]
            if action[1] == 'mesh':
                self._mesh_done = -1
                self._mesh_arm = 0.
                self._mesh_forget()
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
        elif isinstance(action, tuple) and action[0] == 'baby':
            amt = self._BSTEPS[self._bstep_i] * action[1]
            self.gcode.respond_info("GUI baby step: %+.3f" % amt)
            self._gui_msg = "Baby step %+.3f (Save p/ fixar)" % amt
            self._gui_msg_until = (
                self.printer.get_reactor().monotonic() + 4.)
            self._baby_dirty = True
            script = ('SET_GCODE_OFFSET Z_ADJUST=%.3f MOVE=1' % amt)
        elif isinstance(action, tuple) and action[0] == 'bstep':
            self._bstep_i = (self._bstep_i + 1) % len(self._BSTEPS)
            self._gui_render(eventtime)
        elif isinstance(action, tuple) and action[0] == 'bsave':
            if not self._baby_dirty:
                # Nothing changed - just leave the menu.
                self.gui_screen = 'print'
                self._gui_render(eventtime)
                return
            self.gcode.respond_info("GUI: gravar o baby step na probe")
            self._gui_msg = "A gravar o baby step (SAVE_CONFIG)..."
            self._gui_msg_until = (
                self.printer.get_reactor().monotonic() + 20.)
            self._baby_dirty = False
            script = 'Z_OFFSET_APPLY_PROBE\nSAVE_CONFIG'
        elif isinstance(action, tuple) and action[0] == 'fil':
            try:
                st = self.printer.lookup_object('print_stats').get_status(
                    eventtime)['state']
            except Exception:
                st = 'ready'
            if action[1] == 'load':
                self._gui_msg = "A introduzir o filamento (~100mm)..."
                script = 'LOAD_FILAMENT'
            else:
                self._gui_msg = "A retirar o filamento..."
                script = ('PAUSE\n' if st == 'printing' else '')
                script += 'UNLOAD_FILAMENT'
            try:
                self._gui_msg_until = (
                    self.printer.get_reactor().monotonic() + 20.)
            except Exception:
                pass
            self._gui_defer(eventtime, 1.0)
        elif isinstance(action, tuple) and action[0] == 'estop':
            if action[1] == 'yes':
                self.gcode.respond_info("GUI: EMERGENCY STOP (M112)")
                self._gui_msg = "EMERGENCY STOP !"
                self._gui_msg_until = (
                    self.printer.get_reactor().monotonic() + 6.)
                script = 'M112'
            else:
                self.gui_screen = 'power'
                self._gui_render(eventtime)
                return
        elif isinstance(action, tuple) and action[0] == 'host':
            what = 'reboot' if action[1] == 'reboot' else 'shutdown'
            self.gcode.respond_info("GUI: host %s" % what)
            try:
                self._gui_msg = ("A reiniciar o host..." if what == 'reboot'
                                 else "A desligar o host...")
                self._gui_msg_until = (
                    self.printer.get_reactor().monotonic() + 15.)
            except Exception:
                pass
            self._gui_next = 0.
            try:
                threading.Thread(target=self._host_power, args=(what,),
                                 daemon=True).start()
            except Exception:
                logging.exception("st7796: host %s thread failed", what)
            return
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
        if self._popup is not None:
            # A popup is modal: taps outside are ignored.
            self._popup_tap(sx, sy, eventtime)
            return
        # top bar: the filament sensor icon opens the filament menu
        if (self.width - 132) <= sx < (self.width - 100) and 2 <= sy < 28:
            self.gcode.respond_info("GUI: Filamento")
            self._gui_do(('scr', 'filament'), eventtime)
            return
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
        if self.gui_screen == 'estop':
            for (x, y, w, h, kind) in self._estop_btns():
                if x <= sx < x + w and y <= sy < y + h:
                    self.gcode.respond_info("GUI: %s" % kind)
                    self._gui_do(('estop', kind), eventtime)
                    return
        if self.gui_screen == 'ptune':
            for (lab, val, col, key, ry, rh, plus, minus) in \
                    self._tune_row_btns():
                for (bx, by, bw, bh, k, step) in (plus, minus):
                    if bx <= sx < bx + bw and by <= sy < by + bh:
                        self.gcode.respond_info("GUI tune %s %+g" % (k, step))
                        self._gui_do(('tune', k, 1 if step > 0 else -1),
                                     eventtime)
                        return
        if self.gui_screen == 'mesh':
            for (x, y, w, h, kind) in self._mesh_btns():
                if not (x <= sx < x + w and y <= sy < y + h):
                    continue
                running = self._mesh_run > 0
                now = self.printer.get_reactor().monotonic()
                if kind == 'start':
                    if running:
                        self.gcode.respond_info(
                            "GUI: nivelamento em curso - INICIAR desligado")
                        return
                    self.gcode.respond_info("GUI nivelar: INICIAR")
                    self._gui_do('G29', eventtime)
                    return
                if running:
                    # The printer has no native abort: two taps -> M112.
                    if now < self._mesh_arm:
                        self.gcode.respond_info(
                            "GUI: ABORTAR nivelamento (M112 - FIRMWARE_RESTART)")
                        self._gui_msg = "Nivelamento abortado (M112)"
                        self._gui_msg_until = now + 15.
                        self._mesh_arm = 0.
                        self._gui_run_action("M112")
                    else:
                        self._mesh_arm = now + 5.
                        self.gcode.respond_info("GUI: ABORTAR? toca outra vez")
                        self._gui_msg = "Toca outra vez para ABORTAR (M112)"
                        self._gui_msg_until = now + 6.
                        self._gui_render(now)
                else:
                    self.gcode.respond_info("GUI nivelar: CANCELAR")
                    self._gui_do(('scr', 'print'), eventtime)
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
                if self._popup is not None:
                    if self._popup_tap(sx, sy, eventtime):
                        return
                else:
                    for (tx, ty, tw, th, i, k) in self._print_panel():
                        if tx <= sx < tx + tw and ty <= sy < ty + th:
                            self.gcode.respond_info("GUI: ajuste %s" % k)
                            self._popup_open(k, eventtime)
                            return
                for (x, y, w, h, idx) in self._print_btns():
                    if x <= sx < x + w and y <= sy < y + h:
                        if idx == 2:
                            self.gcode.respond_info(
                                "GUI: %s" % ("Continuar" if paused
                                             else "Pausa"))
                            self._gui_do('RESUME' if paused else 'PAUSE',
                                         eventtime)
                            self._gui_defer(eventtime, 0.8)
                        else:
                            self.gcode.respond_info("GUI: Cancelar")
                            self._gui_do('CANCEL_PRINT', eventtime)
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
