#!/usr/bin/env bash
set -e

# 1) Start the bgutil PO Token provider (HTTP server on localhost:4416).
#    yt-dlp's plugin auto-discovers it at http://127.0.0.1:4416.
node /opt/bgutil-pot/server/build/main.js -H 127.0.0.1 -p 4416 &
PROVIDER_PID=$!

# 2) Give it a moment to boot, then start the bot.
sleep 3
echo "[start.sh] PO token provider running (pid $PROVIDER_PID) on 127.0.0.1:4416"

python bot.py
