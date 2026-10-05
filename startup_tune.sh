#!/bin/bash
# Wait until Klipper reports "ready", then play the Windows startup melody on the
# printer buzzer via Moonraker.  Called by printer-startup-tune.service on boot.
for i in $(seq 1 60); do
  if curl -s --max-time 2 http://127.0.0.1:7125/printer/info \
     | python3 -c "import sys,json;sys.exit(0 if json.load(sys.stdin).get(\"result\",{}).get(\"state\")==\"ready\" else 1)" 2>/dev/null; then
    break
  fi
  sleep 2
done
curl -s -X POST --max-time 5 \
  "http://127.0.0.1:7125/printer/gcode/script?script=PLAY_STARTUP_TUNE" \
  >/dev/null 2>&1
