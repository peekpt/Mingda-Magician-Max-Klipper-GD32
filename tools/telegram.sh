#!/bin/sh
# Send one Telegram message: telegram.sh "text"
# Config (root-only, 600): ~/printer_data/config/telegram.conf
#   TOKEN=123456:ABC...
#   CHAT=123456789
CFG=/home/mingda/printer_data/config/telegram.conf
[ -f "$CFG" ] || exit 0
. "$CFG"
[ -n "${TOKEN:-}" ] && [ -n "${CHAT:-}" ] || exit 0
exec curl -s -m 12 -X POST \
     "https://api.telegram.org/bot$TOKEN/sendMessage" \
     --data-urlencode "chat_id=$CHAT" \
     --data-urlencode "text=$1" \
     --data-urlencode "disable_web_page_preview=true" \
     -o /dev/null
