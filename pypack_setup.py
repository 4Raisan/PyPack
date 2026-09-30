#!/usr/bin/env python3
"""PyPack - pip/pip-essentials fixer + any-library installer.
Fix pip, update everything, search + install any PyPI lib.
Only stdlib + tkinter. Win/Mac/Linux. Bare-PC boot via PyPack-Setup launchers.
"""
import sys, os, subprocess, threading, platform, webbrowser, json, queue, tempfile, re, shutil
from pathlib import Path
import urllib.request as urlreq

APP = "PyPack"
FROZEN = getattr(sys, "frozen", False)
PYS = None if FROZEN else (sys.executable or "python3")
_TARGET_INFO = None
_RUN_LOCK = threading.RLock()
OS = platform.system()  # Windows, Darwin, Linux
_PIPVER = None  # cache: pip version changes only after ensure_essentials


def run(cmd, timeout=180):
    """Run cmd list, return (ok, out+err). Uses current Python when pip."""
    try:
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if OS == "Windows" else {}
        environment = os.environ.copy()
        if FROZEN:
            environment.pop("PYTHONHOME", None)
            environment.pop("PYTHONPATH", None)
        with _RUN_LOCK:
            if FROZEN and OS == "Windows":
                import ctypes
                ctypes.windll.kernel32.SetDllDirectoryW(None)
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", timeout=timeout, env=environment, **options)
            finally:
                if FROZEN and OS == "Windows":
                    ctypes.windll.kernel32.SetDllDirectoryW(str(getattr(sys, "_MEIPASS", "")))
        return r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return False, str(e)


def pip_cmd(*args):
    if not PYS:
        raise RuntimeError("Choose a Python interpreter first.")
    return [PYS, "-m", "pip", "--disable-pip-version-check", "--no-input", *args]

