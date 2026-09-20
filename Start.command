#!/bin/bash
cd -- "$(dirname -- "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  echo "Install Python 3.12 or newer from https://www.python.org/downloads/, then open Start.command again."
  read -r -p "Press Return to close. "
  exit 1
fi
python3 launch.py "$@"
result=$?
if [ "$result" -ne 0 ]; then read -r -p "Press Return to close. "; fi
exit "$result"
