#!/bin/bash
# Compatibility entrypoint for the old watcher. One checked recovery transaction
# owns patching, syntax checks, durable pending reloads and idle process signals.
set -eu
RECOVERY=/home/box/sand-data/openbot-hooks/computer-recovery.py
if [ ! -f "$RECOVERY" ]; then
  echo "OpenBot computer recovery helper is missing: $RECOVERY" >&2
  exit 1
fi
exec /usr/bin/python3 "$RECOVERY"
