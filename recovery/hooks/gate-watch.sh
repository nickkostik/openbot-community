#!/bin/bash
# Re-applies the local-turn gate patch if the host bundle gets rewritten.
HOOK=/home/box/sand-data/openbot-hooks/local-turn-gate.sh
LOG=/home/box/sand-data/local-turn-gate-watch.log
while true; do
  bash "$HOOK" >> "$LOG" 2>&1
  sleep 20
done
