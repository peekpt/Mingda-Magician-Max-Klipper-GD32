#!/usr/bin/env python3
"""Off-line renderer for the Klipper ST7796 GUI (klipper_printer/mcu/st7796.py).

Runs the host code with stubbed MCU commands and rasterises the recorded
cmd_rect / cmd_char / cmd_bmp calls into a PNG, so a screen/layout can be
checked without the printer.

Usage:  python3 tools/tft_sim.py [screen]
        screen: print | list | main | tune | ptemp | power   (default: print)
"""
import importlib.util
import os
import re
import sys

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
MCU = os.path.normpath(os.path.join(HERE, '..', 'klipper_printer', 'mcu'))
W, H = 480, 320


def rgb565(c):
    r, g, b = (c >> 11) & 0x1F, (c >> 5) & 0x3F, c & 0x1F
    return (r * 255 // 31, g * 255 // 63, b * 255 // 31)


def load(name, path, patch=None):
    src = open(path).read()
    if patch:
        src = patch(src)
    mod = importlib.util.module_from_spec(
        importlib.util.spec_from_loader(name, loader=None))
    exec(compile(src, path, 'exec'), mod.__dict__)
    sys.modules[name] = mod
    return mod


# --- font (256 glyphs x 16 bytes) -------------------------------------------
_fsrc = open(os.path.join(MCU, 'font8x14.h')).read()
FONT = [int(x, 16) for x in re.findall(r'0x([0-9a-fA-F]{2})', _fsrc)]


class Sim:
    def __init__(self):
        self.img = Image.new('RGB', (W, H), (0, 0, 0))
        self.d = ImageDraw.Draw(self.img)

    def rect(self, x, y, w, h, col):
        if w <= 0 or h <= 0:
            return
        self.d.rectangle([x, y, x + w - 1, y + h - 1], fill=rgb565(col))

    def char(self, x, y, scale, ch, fg, bg):
        if not scale:
            return
        g = FONT[(ch & 0xFF) * 16:(ch & 0xFF) * 16 + 16]
        for row in range(16):
            bits = g[row] if len(g) > row else 0
            for ry in range(scale):
                for col in range(8):
                    c = fg if (bits << col) & 0x80 else bg
                    for rx in range(scale):
                        px, py = x + col * scale + rx, y + row * scale + ry
                        if 0 <= px < W and 0 <= py < H:
                            self.img.putpixel((px, py), rgb565(c))

    def bmp(self, x, y, w, rows, off, fg, bg, data):
        rb = (w + 7) // 8
        if not w or not rows or len(data) < rb * rows:
            return
        for row in range(rows):
            rp = data[row * rb:(row + 1) * rb]
            for col in range(w):
                bit = (rp[col >> 3] << (col & 7)) & 0x80
                c = fg if bit else bg
                px, py = x + col, y + off + row
                if 0 <= px < W and 0 <= py < H:
                    self.img.putpixel((px, py), rgb565(c))


class Cmd:
    """Stand-in for a Klipper mcu command wrapper."""

    def __init__(self, sim, fn):
        self.sim, self.fn = sim, fn

    def send(self, args, **kw):
        self.fn(*args)


class Obj:
    def __init__(self, data):
        self.data = data

    def get_status(self, eventtime=None):
        return self.data


class Reactor:
    NOW, NEVER = 0.0, 1e18

    def monotonic(self):
        return 0.0

    def register_callback(self, cb, waketime=None):
        return None


class Printer:
    def __init__(self, printing, paused=False):
        self.printing = printing
        self.paused = paused

    def get_reactor(self):
        return Reactor()

    def lookup_object(self, name, default=None):
        if name == 'print_stats':
            if self.paused:
                st = 'paused'
            else:
                st = 'printing' if self.printing else 'ready'
            return Obj({'state': st, 'filename': 'Voron_Design_Cube_v8_PETG.gcode',
                        'progress': 0.42, 'print_duration': 1234,
                        'info': {'current_layer': 12, 'total_layer': 50}})
        if name == 'virtual_sdcard':
            return Obj({'progress': 0.42, 'is_active': self.printing})
        if name == 'extruder':
            return Obj({'temperature': 214.3, 'target': 215.0,
                        'pressure_advance': 0.045, 'smooth_time': 0.040})
        if name == 'heater_bed':
            return Obj({'temperature': 59.7, 'target': 60.0})
        if name == 'gcode_move':
            return Obj({'speed_factor': 1.0, 'extrude_factor': 0.98,
                        'gcode_position': [123.4, 45.6, 7.8, 0.0]})
        if name == 'fan':
            return Obj({'speed': 1.0})
        if name == 'display_status':
            return Obj({'message': 'M117 Printing layer 12 of 50 - 42% ETA 00:23:45 remaining'})
        if name.startswith('filament_switch_sensor'):
            return Obj({'enabled': True, 'filament_detected': True})
        if name == 'configfile':
            return Obj({'settings': {
                'stepper_z': {'position_max': 400.0},
                'printer': {'kinematics': 'cartesian'},
                'bed_mesh': {'mesh_min': [20.0, 20.0],
                             'mesh_max': [300.0, 300.0],
                             'probe_count': [4, 4]}}})
        if name == 'input_shaper':
            return Obj({'shaper_type_x': 'mzv', 'shaper_freq_x': 43.2,
                        'shaper_type_y': 'ei', 'shaper_freq_y': 38.5})
        if name == 'toolhead':
            return Obj({'max_velocity': 150.0, 'max_accel': 3000.0,
                        'square_corner_velocity': 5.0,
                        'minimum_cruise_ratio': 0.5,
                        'print_time': 10.0, 'estimated_print_time': 9.0,
                        'homed_axes': 'xyz', 'axis_maximum': [320, 320, 400, 0],
                        'position': [113.0, 206.0, 0, 0]})
        if default is not None:
            return default
        raise KeyError(name)


def build(printing, paused=False):
    ic = load('tft_icons', os.path.join(MCU, 'tft_icons.py'))
    th = load('tft_thumb', os.path.join(MCU, 'tft_thumb.py'))
    st = load('st7796', os.path.join(MCU, 'st7796.py'),
              patch=lambda s: s.replace('from . import tft_icons',
                                        'import tft_icons')
              .replace('from . import tft_thumb', 'import tft_thumb')
)
    sys.modules['tft_icons'] = ic
    sys.modules['tft_thumb'] = th
    sim = Sim()
    o = st.ST7796.__new__(st.ST7796)
    o.width, o.height = W, H
    o.printer = Printer(printing, paused)
    o.gui_btn_gap, o.gui_radius, o.gui_icon_scale = 16, 14, 0.8
    o.gui_bar_bg, o.gui_btn_bg, o.gui_bg = 0x2124, 0x3186, 0x10C3
    o._icon_cache, o._card_cols = {}, {}
    o._corners = (st._make_corner(14, False, False),
                  st._make_corner(14, True, False),
                  st._make_corner(14, False, True),
                  st._make_corner(14, True, True))
    o._sd_cache_t = 0.0
    o._sd_page = 0
    o._sd_per_page = 6
    o._gui_steps, o._gui_step = (0.1, 1.0, 10.0), 1.0
    o._ext_steps, o._ext_step = (1.0, 5.0), 1.0
    o._gui_msg, o._gui_msg_until = None, 0.0
    o._t_hist = [(200, 60)] * 20
    o._sd_files_cache = [
        ('Voron_Design_Cube_v8_PETG_50m10s.gcode', 1),
        ('ringing_tower_PETG_52m23s.gcode', 2),
        ('cube_PLA_18m28s.gcode', 3),
        ('fast_tower_test_PETG_21m51s.gcode', 4),
        ('rpi.gcode', 5),
        ('teste final.gcode', 6)]
    o._sd_sel, o._sd_top, o._sd_vis = 0, 0, 12
    o._zmax_v = 0.0
    o._net_cache = None
    o.gui = True
    o.gui_beep = True
    o.gui_version = 'R1'
    o._ui_live_t = 0.0
    o._gui_beat = 0.0
    o._gui_err, o._gui_err_until = None, 0.0
    o._fil_unloaded = False
    o._BSTEPS, o._bstep_i = (0.005, 0.01, 0.1, 0.5), 1
    o._baby_dirty = False
    o._popup = None
    o._popup_val = None
    o._popup_step = 5.0
    o._popup_save = False
    o._popup_applied = 0.0
    o._baby_base = 0.0
    o._baby_saved = 0.0
    o._mesh_active = True
    o._mesh_done = 5
    o._mesh_prev_screen = 'print'
    o._mesh_arm = 0.
    o._g29_base = None
    o._mesh_run = 0
    o._mesh_t = 0.0
    o._adj_orig = [[-7.31, -7.13, -6.98, -7.00],
                   [-7.11, -7.01, -6.95, -6.98],
                   [-6.99, -6.95, -6.99, -7.14],
                   [-6.85, -6.95, -7.09, -7.21]]
    o._adj_cur = [list(r) for r in o._adj_orig]
    o._adj_i, o._adj_j = 1, 2
    o._adj_delta = 0.035
    o._adj_cur[2][1] += 0.035
    o._adj_cur[0][3] -= 0.02
    o._adj_at = True
    o._adj_step_i = 0
    o._adj_lstate = o._adj_lval = o._adj_lstep = o._adj_lsave = None
    o._eta_hist = []
    o._prev_bits = None
    o._prev_key = 0
    o._prev_file = None
    o._prev_until = 0.0
    o._prev_toggle = False
    o._prev_pending = False
    o._mesh_lmk = 0x7FFFFFFF
    o._mesh_ldone = 0x7FFFFFFF
    o._mesh_ltv = None
    o._mesh_lbr = None
    o._home_icon_scale = 1
    o.cmd_rect = Cmd(sim, sim.rect)
    o.cmd_char = Cmd(sim, sim.char)
    o.cmd_bmp = Cmd(sim, lambda x, y, w, rows, off, fg, bg, data:
                    sim.bmp(x, y, w, rows, off, fg, bg, data))
    o.cmd_cmd = o.cmd_data = o.cmd_icon = Cmd(sim, lambda *a, **k: None)
    o.cmd_idef = o.cmd_idraw = o.cmd_str = o.cmd_tcfg = None
    o._ic_ids, o._ic_bytes = {}, 0
    o._touch_evt = False
    o._gui_ops = None
    o._disp_quiet = lambda et: False
    return st, o, sim


SCREENS = ('print', 'printp', 'printprev', 'list', 'main', 'ptune', 'ptemp', 'power',
           'estop', 'more', 'move', 'temperature', 'extrude', 'probe', 'tune',
           'network', 'modify', 'filament', 'popup', 'popupbaby', 'mesh', 'meshidle', 'meshadj',
           'advanced', 'limits', 'more2', 'extrude2')


def render(which):
    printing = which in ('print', 'printp', 'printprev', 'ptune', 'ptemp',
                         'popup', 'popupbaby')
    st, o, sim = build(printing, paused=(which == 'printp'))
    o.gui_screen = 'print' if which in ('print', 'list', 'printp',
                                        'printprev', 'popup',
                                        'popupbaby') else which
    if which in ('mesh', 'meshidle'):
        o.gui_screen = 'mesh'
    if which == 'popup':
        o._popup, o._popup_step = 'etemp', 5.0
    if which == 'popupbaby':
        o._popup, o._popup_step = 'baby', 0.01
        o._popup_save = True
    if which == 'printprev':
        import importlib.util as _iu
        _spec = _iu.spec_from_file_location(
            'tft_thumb', os.path.join(MCU, 'tft_thumb.py'))
        _tt = _iu.module_from_spec(_spec)
        _spec.loader.exec_module(_tt)
        _bw, _bh, _bits = _tt.thumbnail_bitmap('/tmp/test.gcode', 168)
        if _bits:
            o._prev_bits = (_bits, _bw, _bh)
            o._prev_key = 1
            o._prev_until = 0.0
            o._prev_toggle = True
        else:
            print('aviso: /tmp/test.gcode sem thumbnail')
    if which == 'meshidle':
        o._mesh_active = False
        o._mesh_run = 0
        o._mesh_done = -1
    if which == 'mesh':
        o._mesh_run = 1
    st.GUI_SCREENS['print'] = []
    o._gui_render(0.0)
    ops = o._gui_ops or []
    for op in ops:
        op()
    return sim.img


if __name__ == '__main__':
    which = sys.argv[1] if len(sys.argv) > 1 else 'print'
    if which not in SCREENS:
        print('unknown screen', which, '->', SCREENS)
        sys.exit(1)
    img = render(which)
    out = os.path.join(HERE, 'sim_%s.png' % which)
    img.save(out)
    print('wrote', out)
