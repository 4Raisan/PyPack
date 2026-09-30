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


def has_net(timeout=6):
    try:  # tiny read + close: avoids hanging on throttled simple index
        r = urlreq.urlopen("https://pypi.org/simple/", timeout=timeout)
        r.read(64)
        r.close()
        return True
    except Exception:
        return False


def py_ver():
    return _TARGET_INFO["version"] if _TARGET_INFO else platform.python_version()


def pip_ver(refresh=False):
    """pip version, cached so each GUI log line doesn't spawn a subprocess."""
    global _PIPVER
    if _PIPVER is not None and not refresh:
        return _PIPVER
    ok, out = run(pip_cmd("--version"), timeout=15)
    if ok and out:
        try:
            _PIPVER = out.split()[1]
        except Exception:
            _PIPVER = out.strip()[:40]
    else:
        _PIPVER = "missing"
    return _PIPVER


def can_modify_environment(log=print):
    """Respect OS-managed Python; do not override its package protection."""
    import sysconfig
    marker = os.path.join(sysconfig.get_path("stdlib"), "EXTERNALLY-MANAGED")
    managed = _TARGET_INFO["managed"] if _TARGET_INFO else (sys.prefix == sys.base_prefix and os.path.isfile(marker))
    if managed:
        log("!! This Python is managed by the operating system. Create and activate a virtual environment, then run PyPack there.")
        log(f'   "{PYS}" -m venv .venv')
        return False
    return True

