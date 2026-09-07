#!/usr/bin/env bash
set -e
python -m pip install --upgrade -r requirements.txt
exec python main.py
