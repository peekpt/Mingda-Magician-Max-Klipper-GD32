#!/usr/bin/env python3
"""Telegram bot for the Mingda Magician Max panel.

Reads TOKEN/CHAT from ~/printer_data/config/telegram.conf (never in git) and
talks to the local Moonraker on 127.0.0.1:7125. Only the configured chat is
answered.
"""
import json
import os
import socket
import subprocess
import time
import urllib.parse
import urllib.request

CFG = os.path.expanduser('~/printer_data/config/telegram.conf')
OFF = os.path.expanduser('~/.tg_bot_offset')
MR = 'http://127.0.0.1:7125'

HELP = ("Mingda Magician Max - commands\n"
        "/status  - state, progress, temperatures, ETA\n"
        "/pause /resume /cancel\n"
        "/home    - G28\n"
        "/files   - newest gcode files\n"
        "/print <name>  - start a file\n"
        "/unload  - pause + eject filament\n"
        "/load    - insert filament\n"
        "/beep    - short beep\n"
        "/ip  /host  - network info\n"
        "/help")

CMDS = [
    ('status', 'Printer status'), ('pause', 'Pause the print'),
    ('resume', 'Resume the print'), ('cancel', 'Cancel the print'),
    ('home', 'Home all axes'), ('files', 'List gcode files'),
    ('print', 'Start a file: /print name.gcode'),
    ('unload', 'Pause and eject the filament'),
    ('load', 'Insert the filament'), ('beep', 'Short beep'),
    ('ip', 'IP address'), ('host', 'Hostname'), ('help', 'Commands'),
]


def conf():
    d = {}
    try:
        with open(CFG) as f:
            for line in f:
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    k, v = line.split('=', 1)
                    d[k.strip()] = v.strip()
    except OSError:
        pass
    return d


def api(method, timeout=40, **params):
    token = conf().get('TOKEN', '')
    if not token:
        return {}
    url = 'https://api.telegram.org/bot%s/%s' % (token, method)
    data = urllib.parse.urlencode(params).encode()
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, data=data), timeout=timeout) as r:
            return json.load(r)
    except Exception:
        return {}


def send(text):
    chat = conf().get('CHAT', '')
    if chat:
        api('sendMessage', chat_id=chat, text=text[:4000],
            disable_web_page_preview='true')


def moon(path, data=None):
    url = MR + path
    try:
        body = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(
            url, data=body, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)
    except Exception as exc:
        return {'error': str(exc)}


def gcode(script):
    return moon('/printer/gcode/script', {'script': script})


def objects():
    d = moon('/printer/objects/query?print_stats&virtual_sdcard'
             '&heater_bed&extruder')
    return ((d.get('result') or {}).get('status') or {})


def state():
    return (objects().get('print_stats') or {}).get('state', '?')


def hostname():
    return socket.gethostname()


def ipaddr():
    try:
        out = subprocess.check_output(['hostname', '-I'], text=True,
                                      timeout=3).split()
        out = [a for a in out if ':' not in a and not a.startswith('127.')]
        return out[0] if out else '-'
    except Exception:
        return '-'


def status_text():
    st = objects()
    ps = st.get('print_stats') or {}
    vs = st.get('virtual_sdcard') or {}
    ex = st.get('extruder') or {}
    bd = st.get('heater_bed') or {}
    state = ps.get('state', '?')
    prog = float(vs.get('progress') or 0.) * 100.
    dur = float(ps.get('print_duration') or 0.)
    lines = ['%s%s' % (state, (' %.0f%%' % prog) if state == 'printing' else '')]
    if ps.get('filename'):
        lines.append(os.path.basename(ps['filename']))
    if state in ('printing', 'paused') and prog > 2 and dur > 30:
        left = int(dur * (100. - prog) / prog)
        lines.append('left %dh%02dm' % (left // 3600, (left // 60) % 60))
    lines.append('nozzle %d/%d  bed %d/%d'
                 % (ex.get('temperature', 0), ex.get('target', 0),
                    bd.get('temperature', 0), bd.get('target', 0)))
    return '\n'.join(lines)


def files_text():
    d = moon('/server/files/list?root=gcodes')
    items = d.get('result') or []
    names = [i['path'] for i in items if i.get('path', '').endswith('.gcode')]
    if not names:
        return 'no gcode files'
    return ('newest:\n' + '\n'.join(names[:12]) +
            '\n\nstart with: /print <name>')


def handle(text):
    parts = text.split()
    if not parts:
        return
    cmd = parts[0].lower().split('@')[0]
    arg = ' '.join(parts[1:]).strip()
    if cmd in ('/start', '/help'):
        send(HELP)
    elif cmd == '/status':
        send(status_text())
    elif cmd == '/pause':
        gcode('PAUSE')
        send('paused')
    elif cmd == '/resume':
        gcode('RESUME')
        send('resumed')
    elif cmd == '/cancel':
        gcode('CANCEL_PRINT')
        send('cancelled')
    elif cmd == '/home':
        gcode('G28')
        send('homing')
    elif cmd == '/files':
        send(files_text())
    elif cmd == '/print':
        if not arg:
            send('use /print <name>  (see /files)')
            return
        gcode('SDCARD_RESET_FILE\nSDCARD_PRINT_FILE FILENAME="%s"'
              % arg.replace('"', ''))
        send('starting %s' % arg)
    elif cmd == '/unload':
        st = state()
        send('pausing and ejecting...')
        gcode(('PAUSE\n' if st in ('printing', 'paused') else '')
              + 'UNLOAD_FILAMENT')
        send('eject started - when it stops use /load')
    elif cmd == '/load':
        send('inserting filament (heats first)...')
        gcode('LOAD_FILAMENT')
    elif cmd == '/beep':
        gcode('BEEP')
        send('beep')
    elif cmd == '/ip':
        send('%s  (%s)' % (ipaddr(), hostname()))
    elif cmd == '/host':
        send(hostname())
    else:
        send('unknown command - /help')


def main():
    c = conf()
    if not c.get('TOKEN') or not c.get('CHAT'):
        raise SystemExit('telegram.conf missing TOKEN/CHAT')
    api('setMyCommands',
        commands=json.dumps([{'command': k, 'description': d}
                             for k, d in CMDS]))
    offset = 0
    try:
        offset = int(open(OFF).read().strip() or 0)
    except Exception:
        pass
    while True:
        try:
            token = c.get('TOKEN', '')
            url = ('https://api.telegram.org/bot%s/getUpdates?offset=%d'
                   '&timeout=25' % (token, offset))
            with urllib.request.urlopen(url, timeout=40) as resp:
                r = json.load(resp)
        except Exception:
            time.sleep(5)
            continue
        for u in r.get('result') or []:
            offset = u.get('update_id', offset) + 1
            try:
                open(OFF, 'w').write(str(offset))
            except OSError:
                pass
            msg = u.get('message') or {}
            chat = str((msg.get('chat') or {}).get('id', ''))
            if chat != str(c.get('CHAT')):
                continue
            try:
                handle((msg.get('text') or '').strip())
            except Exception as exc:
                send('error: %s' % exc)
        time.sleep(0.2)


if __name__ == '__main__':
    main()
