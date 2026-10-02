#!/bin/sh
# PyPack source launcher. Preserves arguments; requires Python 3.10+.
set -u
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1
SCRIPT="$HERE/pypack_setup.py"
if [ ! -f "$SCRIPT" ]; then
    echo "[PyPack] Missing $SCRIPT (keep the application next to this launcher)." >&2
    exit 1
fi
find_python() {
    PY=""
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1 &&
           "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
            PY="$candidate"
            return 0
        fi
    done
    return 1
}
if ! find_python; then
    echo "[PyPack] Python 3.10+ not found. Installing Python..."
    if [ "$(uname)" = "Darwin" ]; then
        if command -v brew >/dev/null 2>&1; then
            brew install python || exit 1
        else
            echo "[PyPack] Install Python from https://www.python.org/downloads/macos/" >&2
            exit 1
        fi
    elif command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update && sudo apt-get install -y python3 python3-pip python3-tk python3-venv || exit 1
    elif command -v dnf >/dev/null 2>&1; then
        sudo dnf install -y python3 python3-pip python3-tkinter || exit 1
    elif command -v pacman >/dev/null 2>&1; then
        sudo pacman -S --noconfirm python python-pip tk || exit 1
    else
        echo "[PyPack] Install Python 3.10+ and tkinter, then retry." >&2
        exit 1
    fi
    if ! find_python; then
        echo "[PyPack] Installation did not provide Python 3.10+. Restart your terminal and retry." >&2
        exit 1
    fi
fi
if [ "$#" -eq 0 ]; then
    set -- --gui
fi
echo "[PyPack] Using $PY ($("$PY" --version 2>&1))"
exec "$PY" "$SCRIPT" "$@"
