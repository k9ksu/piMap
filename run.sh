#!/bin/sh
# Run piMap on the Pi's screen (display :0) in the FOREGROUND so you can
# stop it with Ctrl-C in this terminal. Re-run this script to start it again.
#
#   ./run.sh        start piMap (Ctrl-C here stops it; Esc on the Pi also quits)
#
set -u

export DISPLAY="${DISPLAY:-:0}"
export XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

# Kill any piMap already running (e.g. a stray background copy) so we don't
# stack two windows on the screen.
pkill -f 'piMap/piMap\.py' 2>/dev/null && sleep 1

cd "$(dirname "$0")"
echo "Starting piMap on $DISPLAY  — press Ctrl-C here (or Esc on the Pi) to stop."
exec python3 piMap.py
