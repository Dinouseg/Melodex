#!/usr/bin/env bash
# Start Melodex from source.
cd "$(dirname "$0")"
exec python3 app.py "$@"
