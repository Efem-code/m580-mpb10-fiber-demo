#!/bin/sh
# Machine Health demo dashboard, for Linux and Mac. Add --live to read the M580, --lan to show it on TVs/iPads.
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
    echo "We need Python 3. On Ubuntu or Debian: sudo apt install python3"
    exit 1
fi
exec python3 server.py "$@"
