#!/bin/bash
# Very last step before halt (installed to
# /usr/lib/systemd/system-shutdown/zz-printer-beep.sh).
# Fires ~30 s after the shutdown tune started, then the Pi powers off.
WAV=/home/mingda/printer_data/config/beep.wav
TSFILE=/home/mingda/printer_data/config/.shutdown_ts
DELAY=30
[ -f "$WAV" ] || exit 0
TS=$(cat "$TSFILE" 2>/dev/null)
if [ -n "$TS" ]; then
    NOW=$(date +%s)
    REM=$(( DELAY - (NOW - TS) ))
    [ "$REM" -gt 0 ] && sleep "$REM"
else
    sleep "$DELAY"
fi
# Prefer a USB audio card, otherwise the first playback card.
list=$(aplay -l 2>/dev/null | grep "^card")
CARD=$(echo "$list" | grep -i usb | sed "s/^card \([0-9]*\):.*/\1/" | head -1)
[ -z "$CARD" ] && CARD=$(echo "$list" | sed "s/^card \([0-9]*\):.*/\1/" | head -1)
[ -z "$CARD" ] && exit 0
aplay -q -D "plughw:$CARD,0" "$WAV" 2>/dev/null
rm -f "$TSFILE" 2>/dev/null
