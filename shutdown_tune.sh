#!/bin/bash
# Play PLAY_SHUTDOWN_TUNE on the printer buzzer via Moonraker, then wait so the
# melody finishes BEFORE Klipper/Moonraker are stopped.
# Called by printer-shutdown-tune.service (ExecStop) on a graceful Pi shutdown.
# Records the start time so the final beep fires ~30 s after the tune.
date +%s > /home/mingda/printer_data/config/.shutdown_ts 2>/dev/null
curl -s -X POST --max-time 5 \
  "http://127.0.0.1:7125/printer/gcode/script?script=PLAY_SHUTDOWN_TUNE" \
  >/dev/null 2>&1
sleep 8
