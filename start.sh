#!/usr/bin/env bash
set -e

node /opt/bgutil-pot/server/build/main.js -H 127.0.0.1 -p 4416 &
PROVIDER_PID=$!

sleep 3
echo "[start.sh] PO token provider running (pid $PROVIDER_PID) on 127.0.0.1:4416"

python bot.py
