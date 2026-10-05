#!/bin/bash
# Play PLAY_SHUTDOWN_TUNE on the printer buzzer via Moonraker, then wait so the
# melody finishes BEFORE Klipper/Moonraker are stopped.
# Called by printer-shutdown-tune.service (ExecStop) on a graceful Pi shutdown.
curl -s -X POST --max-time 5 \
  "http://127.0.0.1:7125/printer/gcode/script?script=PLAY_SHUTDOWN_TUNE" \
  >/dev/null 2>&1
sleep 8
