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


def ensure_essentials(log=print, progress=None):
    """Replaces 'after python': ensure pip + upgrade pip/setuptools/wheel."""
    def say(m, p=None):
        log(m)
        if progress:
            try:
                progress(p or m)
            except Exception:
                pass
    if not can_modify_environment(log):
        return False
    say("Checking internet...", "Checking internet")
    if not has_net():
        say("!! OFFLINE - connect to internet and retry.")
        return False
    say("Internet OK. Checking pip...", "Checking pip")
    run([PYS, "-m", "ensurepip", "--upgrade"], timeout=120)
    # bootstrap via get-pip.py if still no pip
    ok, _ = run(pip_cmd("--version"), timeout=30)
    if not ok:
        say("pip missing, installing it...", "Installing pip")
        try:
            with tempfile.TemporaryDirectory(prefix="pypack-pip-") as directory:
                script = os.path.join(directory, "get-pip.py")
                urlreq.urlretrieve("https://bootstrap.pypa.io/get-pip.py", script)
                ok, out = run([PYS, script], timeout=180)
                if not ok:
                    say(f"!! pip bootstrap failed: {out[-1500:]}")
                    return False
        except Exception as e:
            say(f"!! get-pip failed: {e}")
            return False
    say("Updating pip + essentials...", "Updating pip")
    ok, out = run(pip_cmd("install", "--upgrade", "-q",
                          "pip", "setuptools", "wheel"), timeout=300)
    if not ok:
        say(out[-1500:] if len(out) > 1500 else out)
        say("!! essentials update failed.")
        return False
    say(f"Ready. pip {pip_ver(refresh=True)}")
    return True


def _pypi_names():
    """All PyPI project names, cached on disk and refreshed every 12h."""
    import time, gzip
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    path = os.path.join(base, ".pypack_pypi_index.txt")
    try:
        if os.path.exists(path) and time.time() - os.path.getmtime(path) < 43200:
            with open(path, encoding="utf-8") as f:
                ns = [l.strip() for l in f if l.strip()]
            if ns:
                return ns
    except Exception:
        pass
    req = urlreq.Request("https://pypi.org/simple/", headers={
        "User-Agent": "PyPack/1.0",
        "Accept": "application/vnd.pypi.simple.v1+json",
        "Accept-Encoding": "gzip"})
    with urlreq.urlopen(req, timeout=90) as r:
        data = r.read()
        enc = r.headers.get("Content-Encoding", "")
    if enc.lower() == "gzip" or data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    names = [p["name"] for p in json.loads(data)["projects"]]
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(names))
    except Exception:
        pass
    return names


def _pypi_popularity():
    """Project names ordered by recent downloads (hugovk top-pypi-packages,
    ~0.8MB), cached on disk and refreshed every 12h."""
    import time
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    path = os.path.join(base, ".pypack_pypi_popular.txt")
    try:
        if os.path.exists(path) and time.time() - os.path.getmtime(path) < 43200:
            with open(path, encoding="utf-8") as f:
                ns = [l.strip() for l in f if l.strip()]
            if ns:
                return ns
    except Exception:
        pass
    try:
        req = urlreq.Request(
            "https://hugovk.dev/top-pypi-packages/top-pypi-packages.min.json",
            headers={"User-Agent": "PyPack/1.0"})
        with urlreq.urlopen(req, timeout=60) as r:
            data = json.load(r)
        names = [row["project"] for row in data.get("rows", [])]
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(names))
        except Exception:
            pass
        return names
    except Exception:
        return None

