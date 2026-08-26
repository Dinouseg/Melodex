#!/usr/bin/env bash
# Start WaveQueen Downloader from source.
cd "$(dirname "$0")"
exec python3 app.py "$@"
