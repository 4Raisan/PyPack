#!/usr/bin/env python3
"""PyPack - pip/pip-essentials fixer + any-library installer.
Fix pip, update everything, search + install any PyPI lib.
Only stdlib + tkinter. Win/Mac/Linux. Bare-PC boot via PyPack-Setup launchers.
"""
import sys, os, subprocess, threading, platform, webbrowser, json, queue, tempfile, re, shutil
import difflib, gzip, time
from pathlib import Path
import urllib.request as urlreq

APP = "PyPack"
FROZEN = getattr(sys, "frozen", False)
PYS = None if FROZEN else (sys.executable or "python3")
_TARGET_INFO = None
_RUN_LOCK = threading.RLock()
OS = platform.system()  # Windows, Darwin, Linux
_PIPVER = None  # cache: pip version changes only after ensure_essentials
_ACTIVE = None  # child process that cancel_current() can kill
_ACTIVE_LOCK = threading.Lock()
_CANCEL = threading.Event()  # also stops multi-package loops between subprocesses
_INDEX_TTL = 43200  # 12h; an older index is still served when PyPI is unreachable
_VERSION = None
_INDEX_NAMES = None
_POPULAR = None
_LOGGER = None


def resource_path(name):
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / name


def app_version():
    global _VERSION
    if _VERSION is None:
        try:
            _VERSION = resource_path("VERSION").read_text(encoding="utf-8").strip() or "0.0.0"
        except OSError:
            _VERSION = "0.0.0"
    return _VERSION


def user_agent():
    return f"{APP}/{app_version()}"


def data_dir():
    """Per-user application directory, for things that are not a disposable cache."""
    if OS == "Windows":
        return Path(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")) / "PyPack"
    if OS == "Darwin":
        return Path.home() / "Library" / "Application Support" / "PyPack"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "pypack"


def cache_dir():
    if OS == "Windows":
        return data_dir() / "cache"
    if OS == "Darwin":
        return Path.home() / "Library" / "Caches" / "PyPack"
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "pypack"


def log_dir():
    return data_dir() / "logs"


def legacy_cache(name):
    """Caches written before 0.1.2 lived loose in LOCALAPPDATA/home."""
    return Path(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")) / name


_NORMALIZE = re.compile(r"[-_.]+")


def normalize(name):
    """PEP 503 project-name comparison key."""
    return _NORMALIZE.sub("-", name).lower()


def run(cmd, timeout=180):
    """Run cmd list, return (ok, out+err). Uses current Python when pip."""
    global _ACTIVE
    if _CANCEL.is_set():
        return False, "Cancelled."
    try:
        options = {}
        if OS == "Windows":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            options["start_new_session"] = True  # own process group, so Stop kills pip's children too
        environment = os.environ.copy()
        if FROZEN:
            environment.pop("PYTHONHOME", None)
            environment.pop("PYTHONPATH", None)
        with _RUN_LOCK:
            if FROZEN and OS == "Windows":
                import ctypes
                ctypes.windll.kernel32.SetDllDirectoryW(None)
            try:
                process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           text=True, encoding="utf-8", errors="replace",
                                           env=environment, **options)
                with _ACTIVE_LOCK:
                    _ACTIVE = process
                try:
                    out, err = process.communicate(timeout=timeout)
                except subprocess.TimeoutExpired:
                    kill_tree(process)
                    out, err = process.communicate()
                    return False, f"timed out after {timeout}s\n{(out or '') + (err or '')}"
                return process.returncode == 0, (out or "") + (err or "")
            finally:
                with _ACTIVE_LOCK:
                    _ACTIVE = None
                if FROZEN and OS == "Windows":
                    ctypes.windll.kernel32.SetDllDirectoryW(str(getattr(sys, "_MEIPASS", "")))
    except Exception as e:
        return False, str(e)


def kill_tree(process):
    """Kill a child and its descendants; pip spawns build backends of its own."""
    if process.poll() is not None:
        return
    try:
        if OS == "Windows":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                           capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            import signal
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


def cancel_current():
    """Stop the running package operation. Returns True if a child was killed."""
    _CANCEL.set()
    with _ACTIVE_LOCK:
        process = _ACTIVE
    if process is None or process.poll() is not None:
        return False
    kill_tree(process)
    return True

def pip_cmd(*args):
    if not PYS:
        raise RuntimeError("Choose a Python interpreter first.")
    return [PYS, "-m", "pip", "--disable-pip-version-check", "--no-input", *args]

def index_url():
    """pip's configured index, so a private mirror is not reported as offline."""
    return (os.environ.get("PIP_INDEX_URL") or "https://pypi.org/simple/").rstrip("/") + "/"

def has_net(timeout=6):
    try:  # tiny read + close: avoids hanging on throttled simple index
        r = urlreq.urlopen(index_url(), timeout=timeout)
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

def _file_log(message):
    """The frozen exe hides its console, so this is the only durable record of a failure."""
    global _LOGGER
    if _LOGGER is None:
        import logging.handlers
        try:
            directory = log_dir()
            directory.mkdir(parents=True, exist_ok=True)
            handler = logging.handlers.RotatingFileHandler(
                directory / "pypack.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
            logger = logging.getLogger("pypack")
            logger.setLevel(logging.INFO)
            logger.addHandler(handler)
            logger.propagate = False
            _LOGGER = logger
        except OSError:
            _LOGGER = False  # tried and failed; do not retry on every line
    if _LOGGER:
        _LOGGER.info(message)

def log_fn(msg, widget=None):
    msg = str(msg)
    print(msg, flush=True)
    _file_log(msg)
    if widget is not None:
        try:
            widget.after(0, lambda: (_w(widget, msg)))
        except Exception:
            pass

def _w(widget, msg):
    widget.configure(state="normal")
    widget.insert("end", msg + "\n")
    widget.see("end")
    widget.configure(state="disabled")

def can_modify_environment(log=log_fn):
    """Respect OS-managed Python and read-only site-packages; override neither."""
    import sysconfig
    if _TARGET_INFO:
        managed = _TARGET_INFO["managed"]
        writable = _TARGET_INFO.get("writable", True)
    else:
        marker = os.path.join(sysconfig.get_path("stdlib"), "EXTERNALLY-MANAGED")
        managed = sys.prefix == sys.base_prefix and os.path.isfile(marker)
        site = sysconfig.get_path("purelib")
        writable = not os.path.isdir(site) or os.access(site, os.W_OK)
    if managed:
        log("!! This Python is managed by the operating system; packages cannot go into it.")
    elif not writable:
        log("!! site-packages is not writable for this Python (often an all-users install).")
    else:
        return True
    log("   Use New venv in the PyPack window, or run:")
    log(f'   "{PYS}" -m venv .venv')
    log("   then activate that environment and run PyPack from it.")
    return False

def check_dependencies(log=log_fn):
    """Surface broken requirements after an install or bulk upgrade."""
    ok, out = run(pip_cmd("check"), timeout=120)
    if ok:
        return True
    log("!! Dependency conflicts reported by pip check:")
    log(out.strip()[-1500:] or "   (no detail)")
    return False

def ensure_essentials(log=log_fn, progress=None):
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

def _read_lines(path, ttl):
    """Cached lines, or None. ttl=None accepts any age, for the offline fallback."""
    try:
        if ttl is not None and time.time() - path.stat().st_mtime >= ttl:
            return None
        with open(path, encoding="utf-8") as handle:
            lines = [line.strip() for line in handle if line.strip()]
        return lines or None
    except OSError:
        return None


def _write_lines(path, lines):
    """Atomic: a kill mid-write must not leave a truncated index that still looks valid."""
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write("\n".join(lines))
        temporary.replace(path)
    except OSError:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _fetch_index_names():
    request = urlreq.Request("https://pypi.org/simple/", headers={
        "User-Agent": user_agent(),
        "Accept": "application/vnd.pypi.simple.v1+json",
        "Accept-Encoding": "gzip"})
    with urlreq.urlopen(request, timeout=90) as response:
        data = response.read()
        encoding = response.headers.get("Content-Encoding", "")
    if encoding.lower() == "gzip" or data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return [project["name"] for project in json.loads(data)["projects"]]


def _pypi_names():
    """All PyPI project names (~926k), loaded once per process and cached on disk."""
    global _INDEX_NAMES
    if _INDEX_NAMES is not None:
        return _INDEX_NAMES
    path = cache_dir() / "pypi-index.txt"
    legacy = legacy_cache(".pypack_pypi_index.txt")
    # A fresh pre-0.1.2 cache is still fresh: upgrading must not re-download 14 MB.
    names = _read_lines(path, _INDEX_TTL) or _read_lines(legacy, _INDEX_TTL)
    if names is None:
        try:
            names = _fetch_index_names()
            _write_lines(path, names)
        except Exception:
            # A day-old index still answers searches; an empty result set does not.
            names = _read_lines(path, None) or _read_lines(legacy, None)
    if names:
        _INDEX_NAMES = names
    return names


def _pypi_popularity():
    """Top projects by recent downloads (hugovk top-pypi-packages), as an ordered
    mapping of name -> 30-day download count. Iteration order is the rank."""
    global _POPULAR
    if _POPULAR is not None:
        return _POPULAR
    path = cache_dir() / "pypi-popular.txt"
    legacy = legacy_cache(".pypack_pypi_popular.txt")
    lines = _read_lines(path, _INDEX_TTL) or _read_lines(legacy, _INDEX_TTL)
    if lines is None:
        try:
            request = urlreq.Request(
                "https://hugovk.dev/top-pypi-packages/top-pypi-packages.min.json",
                headers={"User-Agent": user_agent()})
            with urlreq.urlopen(request, timeout=60) as response:
                data = json.load(response)
            lines = [f"{row['project']}\t{int(row['download_count'])}"
                     if row.get("download_count") is not None else row["project"]
                     for row in data.get("rows", [])]
            _write_lines(path, lines)
        except Exception:
            lines = _read_lines(path, None)
    if lines is None:  # pre-0.1.2 cache stored names only, without counts
        lines = _read_lines(legacy, None) or []
    counts = {}
    for line in lines:
        name, _, count = line.partition("\t")
        if name:
            counts[name] = int(count) if count.isdigit() else None
    _POPULAR = counts
    return _POPULAR


_RANK_SOURCE = None
_RANK = None


def _popularity_rank(popular):
    """Normalized rank lookup, built once per index rather than once per search."""
    global _RANK_SOURCE, _RANK
    if _RANK_SOURCE is not popular:
        _RANK = {normalize(n): i for i, n in enumerate(popular)}
        _RANK_SOURCE = popular
    return _RANK


def download_count(name):
    """30-day downloads, or None when the project is outside the published top list."""
    return (_pypi_popularity() or {}).get(normalize(name))


def human_downloads(count):
    if count is None:
        return "—"
    for unit, size in (("B", 10**9), ("M", 10**6), ("k", 10**3)):
        if count >= size:
            return f"{count / size:.1f}{unit}"
    return str(count)


_SCAN_SOURCE = None
_HAYSTACK = None
_OFFSETS = None


def _scan_index(query, names):
    """Exact / prefix / substring tiers in one C-level pass over a joined haystack.

    Scanning 926k names in Python costs ~400ms per search; this keeps it near 1ms.
    """
    global _SCAN_SOURCE, _HAYSTACK, _OFFSETS
    if _SCAN_SOURCE is not names:
        from array import array
        offsets = array("i")
        position = 1
        lowered = []
        for name in names:
            offsets.append(position)
            low = name.lower()
            lowered.append(low)
            position += len(low) + 1
        _HAYSTACK = "\n" + "\n".join(lowered) + "\n"
        _OFFSETS = offsets
        _SCAN_SOURCE = names
    import bisect
    exact, starts, contains = [], [], []
    seen = set()
    position = _HAYSTACK.find(query)
    while position != -1:
        line_start = _HAYSTACK.rfind("\n", 0, position) + 1
        index = bisect.bisect_left(_OFFSETS, line_start)
        if index not in seen:  # first occurrence classifies the name, as an elif chain would
            seen.add(index)
            name = names[index]
            if position != line_start:
                contains.append(name)
            elif _HAYSTACK[position + len(query)] == "\n":
                exact.append(name)
            else:
                starts.append(name)
        position = _HAYSTACK.find(query, position + 1)
    return exact, starts, contains


_BUCKET_SOURCE = None
_BUCKETS = None


def _buckets(names):
    """First-two-character buckets so fuzzy matching scans thousands, not 926k."""
    global _BUCKET_SOURCE, _BUCKETS
    if _BUCKET_SOURCE is not names:
        buckets = {}
        for name in names:
            buckets.setdefault(name[:2].lower(), []).append(name)
        _BUCKET_SOURCE, _BUCKETS = names, buckets
    return _BUCKETS


def _fuzzy(query, names, limit):
    """Close spellings of query. Length-scoped so a transposition still matches."""
    if len(query) < 4 or not names:
        return []
    span = 1 if len(query) < 7 else 2
    candidates = [n for n in names if abs(len(n) - len(query)) <= span]
    return difflib.get_close_matches(query, candidates, n=limit, cutoff=0.75)


def search_pypi(name, limit=20):
    """Light PyPI search via stdlib only. Returns [names] (best first).

    A close spelling of a widely downloaded project outranks an exact substring
    hit on an obscure one, so `numpi` finds numpy and `reqeusts` finds requests
    rather than a name-squatter that happens to contain the typo.
    """
    name = (name or "").strip()
    if not name or limit <= 0 or "\n" in name:
        return []
    try:
        names = _pypi_names()
        if names:
            query = name.lower()
            popular = _pypi_popularity() or {}
            rank = _popularity_rank(popular)
            exact, starts, contains = _scan_index(query, names)
            fuzzy = []
            # Fuzzy is a rescue for when nothing well-downloaded matched by name.
            if not any(normalize(n) in rank for n in exact + starts):
                fuzzy = _fuzzy(query, list(popular), limit)
                # Only scan the whole index when nothing else matched at all;
                # bucketing 926k names is a one-off cost worth paying for a suggestion.
                if not fuzzy and not (exact or starts or contains):
                    fuzzy = _fuzzy(query, _buckets(names).get(query[:2], []), limit)
            # (in top-download list, not in it) per tier: exact, prefix, fuzzy, substring.
            # An exact name keeps privilege over prefix and substring hits, but a widely
            # downloaded close spelling beats an obscure exact namesake — that is what
            # separates numpy from NuMPI for the query "numpi".
            weights = ((0, 2), (4, 22), (1, 20), (6, 24))
            scored, seen = [], set()
            for quality, tier in enumerate((exact, starts, fuzzy, contains)):
                listed, obscure = weights[quality]
                for candidate in tier:
                    key = normalize(candidate)
                    if key in seen:
                        continue
                    seen.add(key)
                    scored.append((listed if key in rank else obscure,
                                   rank.get(key, 10**9), len(candidate), candidate.lower(), candidate))
            scored.sort()
            return [item[4] for item in scored[:limit]]
    except Exception:
        pass
    # Slow lanes: exact name via pip, then JSON, then installed-fuzzy.
    ok, outp = run(pip_cmd("index", "versions", name), timeout=30)
    if ok and outp and outp.splitlines():
        for line in outp.splitlines():
            match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s+\([^)]+\)", line)
            if match:
                return [match.group(1)]
    try:
        req = urlreq.Request(f"https://pypi.org/pypi/{name}/json",
                             headers={"User-Agent": user_agent()})
        with urlreq.urlopen(req, timeout=8) as r:
            return [json.load(r)["info"]["name"]]
    except Exception:
        pass
    cands = []
    try:
        ok2, lst = run(pip_cmd("list", "--format=freeze"), timeout=60)
        if ok2:
            installed = [l.split("==")[0] for l in lst.splitlines() if "==" in l]
            cands = difflib.get_close_matches(name, installed, n=limit, cutoff=0.6)
    except Exception:
        pass
    return cands or []

def installed_set():
    """Lowercase names of all installed packages (one pip call)."""
    ok, out = run(pip_cmd("list", "--format=freeze",
                          "--disable-pip-version-check"), timeout=30)
    if not ok:
        return set()
    return {l.split("==")[0].lower() for l in out.splitlines() if "==" in l}

def installed_ver(pkg):
    ok, out = run(pip_cmd("show", pkg), timeout=30)
    if not ok:
        return None
    for line in out.splitlines():
        if line.lower().startswith("version:"):
            return line.split(":", 1)[1].strip()
    return "?"

def install_pkgs(pkgs, log=log_fn, upgrade=True, progress=None):
    pkgs = [(p or "").strip() for p in (pkgs or [])]
    pkgs = [p for p in pkgs if p]
    if not pkgs:
        log("!! nothing to install (empty name).")
        return False
    if not can_modify_environment(log):
        return False
    if any(p.startswith("-") for p in pkgs):
        log("!! Pass package names or requirements, not pip options.")
        return False
    args = ["install", "-q"] + (["--upgrade"] if upgrade else []) + ["--"] + pkgs
    log(f"Installing {', '.join(pkgs)} ...")
    if progress:
        try:
            progress("Installing " + ", ".join(pkgs))
        except Exception:
            pass
    ok, out = run(pip_cmd(*args), timeout=900)
    if not ok:  # quiet hid the reason, show tail only on failure
        log(out[-1500:] if len(out) > 1500 else out)
        log(f"!! install failed for: {', '.join(pkgs)} (name wrong? no net? no compiler?)")
        return False
    for p in pkgs:
        log(f"Done: {p} {installed_ver(p) or ''}".strip())
    check_dependencies(log)
    return True

def update_all(log=log_fn, progress=None):
    """Update essentials + all installed pkgs to latest on every run."""
    def say(m, p=None):
        log(m)
        if progress:
            try:
                progress(p or m)
            except Exception:
                pass
    say("Updating everything to latest...", "Updating everything")
    if not ensure_essentials(log, progress):
        return False
    ok, out = run(pip_cmd("list", "--format=freeze"), timeout=60)
    if not ok:
        say("!! could not list packages.")
        return False
    pkgs = [l.split("==")[0] for l in out.splitlines() if "==" in l
            and not l.lower().startswith(("pip==", "setuptools==", "wheel=="))]
    if not pkgs:
        say("Nothing else to update.")
        return True
    say(f"Found {len(pkgs)} packages. Updating one by one...", f"Updating {len(pkgs)} packages")
    good, bad = 0, []
    for i, p in enumerate(pkgs, 1):
        if _CANCEL.is_set():
            say(f"Cancelled. {good} updated before stopping.")
            return False
        if progress:
            try:
                progress(f"Updating {i}/{len(pkgs)}: {p}")
            except Exception:
                pass
        okp, outp = run(pip_cmd("install", "--upgrade", "-q", p,
                                "--disable-pip-version-check"), timeout=600)
        if okp:
            good += 1
        else:
            bad.append(p)
            say(f"Skipped {p}: {outp[-1500:]}")
    say(f"Done: {good} updated" + (f", {len(bad)} skipped: {', '.join(bad)}" if bad else "."))
    check_dependencies(log)
    return not bad

def open_python_download(log=log_fn):
    url = "https://www.python.org/downloads/"
    if OS == "Darwin":
        url = "https://www.python.org/downloads/macos/"
    log(f">> Get/Update Python: {url}")
    log(">> Win: winget install Python.Python.3.12 | Mac: brew install python | Linux: sudo apt install python3 python3-pip")
    webbrowser.open(url)

def launch_gui(auto_update=False):
    """Optional compact package browser with an always-visible activity terminal."""
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
    from tkinter import font as tkfont

    root = tk.Tk()
    root.title(f"{APP} {app_version()}")
    icon = resource_path("assets/pypack-icon.png")
    if icon.exists():
        root._app_icon = tk.PhotoImage(file=str(icon))
        root.iconphoto(True, root._app_icon)
    root.minsize(480, 1)
    BG, CARD, FG, SUB, AC = "#0e1117", "#161b26", "#e8eaf0", "#8b90a5", "#6c5ce7"
    root.configure(background=BG)
    root.columnconfigure(0, weight=1)
    root.rowconfigure(4, weight=1)
    # Segoe UI and Consolas are Windows-only; pick the first family actually present.
    present = set(tkfont.families(root))
    def family(*choices):
        return next((c for c in choices if c in present), "Helvetica")
    BODY = family("Segoe UI", "SF Pro Text", "Helvetica Neue", "Ubuntu", "Noto Sans", "DejaVu Sans")
    MONO = family("Consolas", "SF Mono", "Menlo", "Ubuntu Mono", "DejaVu Sans Mono", "Courier New")
    st = ttk.Style(root)
    try:
        st.theme_use("clam")
    except tk.TclError:
        pass
    st.configure(".", background=BG, foreground=FG, font=(BODY, 10))
    st.configure("TFrame", background=BG)
    st.configure("Card.TFrame", background=CARD)
    st.configure("TLabel", background=BG, foreground=FG)
    st.configure("Sub.TLabel", foreground=SUB)
    st.configure("Title.TLabel", font=(BODY, 18, "bold"), foreground="#a29bfe")
    st.configure("TButton", background="#232a3b", foreground=FG, padding=(10, 5), borderwidth=0)
    st.map("TButton", background=[("active", "#2e3750")], foreground=[("disabled", "#656b80")])
    st.configure("Accent.TButton", background=AC, foreground="white")
    st.map("Accent.TButton", background=[("disabled", "#232a3b"), ("active", "#7f71f0")])
    st.configure("TEntry", fieldbackground="#1a2030", foreground=FG, insertcolor=FG, padding=6)
    st.configure("Treeview", background="#1a2030", fieldbackground="#1a2030", foreground=FG,
                 rowheight=26, borderwidth=0)
    st.configure("Treeview.Heading", background="#232a3b", foreground=SUB, padding=(8, 5))
    st.map("Treeview", background=[("selected", AC)], foreground=[("selected", "white")])
    st.configure("TScrollbar", background="#2e3750", troughcolor=CARD, arrowcolor=SUB, borderwidth=0)

    status = tk.StringVar(value="Ready. Search for packages, fix pip, or update packages.")
    info = tk.StringVar(value=f"Python {py_ver()}  |  checking pip…  |  {OS}")
    count = tk.StringVar(value="Search results")
    selection = tk.StringVar(value="Select packages, then Install.")
    head = ttk.Frame(root, padding=(14, 10, 14, 4))
    head.grid(row=0, column=0, sticky="ew")
    ttk.Label(head, text=APP, style="Title.TLabel").pack(side="left")
    ttk.Label(head, textvariable=info, style="Sub.TLabel", font=(BODY, 9)).pack(side="right")

    toolbar = ttk.Frame(root, padding=(14, 4, 14, 8))
    toolbar.grid(row=1, column=0, sticky="ew")
    controls = []
    def button(parent, text, command, accent=False):
        widget = ttk.Button(parent, text=text, command=command,
                            style="Accent.TButton" if accent else "TButton")
        controls.append(widget)
        return widget
    button(toolbar, "Fix pip", lambda: start("Fixing pip", ensure_essentials)).pack(side="left", padx=(0, 6))
    button(toolbar, "Update all", lambda: confirm_update()).pack(side="left", padx=(0, 6))
    button(toolbar, "New venv", lambda: new_venv()).pack(side="left", padx=(0, 6))
    button(toolbar, "Get Python", lambda: open_python_download(log)).pack(side="left")

    browser = ttk.Frame(root, padding=(14, 0, 14, 0))
    browser.grid(row=2, column=0, sticky="nsew")
    browser.columnconfigure(0, weight=1)
    searchrow = ttk.Frame(browser)
    searchrow.grid(row=0, column=0, sticky="ew", pady=(0, 6))
    entry = ttk.Entry(searchrow)
    entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
    controls.append(entry)
    button(searchrow, "Search", lambda: do_search(), True).pack(side="right")
    ttk.Label(browser, textvariable=count, style="Sub.TLabel").grid(row=1, column=0, sticky="w", pady=(0, 5))
    results = ttk.Frame(browser)
    results.grid(row=2, column=0, sticky="nsew")
    results.rowconfigure(0, weight=1)
    results.columnconfigure(0, weight=1)
    tree = ttk.Treeview(results, columns=("name", "downloads"), show="tree headings",
                        selectmode="browse", height=3)
    tree.heading("#0", text="")
    tree.column("#0", width=32, minwidth=32, stretch=False, anchor="center")
    tree.heading("name", text="Package")
    tree.column("name", width=270, minwidth=120, anchor="w")
    tree.heading("downloads", text="Downloads/30d")
    tree.column("downloads", width=110, minwidth=90, stretch=False, anchor="e")
    tree.tag_configure("inst", foreground="#7ee787")
    tree.tag_configure("rare", foreground="#e3b341")
    tree.grid(row=0, column=0, sticky="nsew")
    scrollbar = ttk.Scrollbar(results, orient="vertical", command=tree.yview)
    scrollbar.grid(row=0, column=1, sticky="ns")
    tree.configure(yscrollcommand=scrollbar.set)
    results.grid_remove()
    installrow = ttk.Frame(browser)
    installrow.grid(row=3, column=0, sticky="ew", pady=(4, 0))
    installrow.grid_remove()
    install_button = button(installrow, "Install", lambda: do_install_at(), True)
    install_button.pack(side="right")
    ttk.Label(installrow, textvariable=selection, style="Sub.TLabel").pack(side="left", fill="x", expand=True)

    activitybar = ttk.Frame(root, padding=(14, 8, 14, 4))
    activitybar.grid(row=3, column=0, sticky="ew")
    ttk.Label(activitybar, text="Activity", style="Sub.TLabel").pack(side="left")
    # Stop sits beside the activity it cancels, keeping the toolbar inside the
    # compact minimum width. Outside `controls`: it enables while they disable.
    stop_button = ttk.Button(activitybar, text="Stop", command=lambda: stop(), state="disabled")
    stop_button.pack(side="right")
    progressbar = ttk.Progressbar(activitybar, mode="indeterminate", length=100)
    progressbar.pack(side="right")
    progressbar.pack_forget()
    activity = ttk.Frame(root, padding=(14, 0, 14, 0))
    activity.grid(row=4, column=0, sticky="nsew")
    activity.columnconfigure(0, weight=1)
    activity.rowconfigure(0, weight=1)
    logbox = tk.Text(activity, width=60, height=4, state="disabled", wrap="word", font=(MONO, 9),
                     relief="flat", bg="#1a2030", fg="#c9d1e3", insertbackground=FG)
    logbox.grid(row=0, column=0, sticky="nsew")
    logscroll = ttk.Scrollbar(activity, orient="vertical", command=logbox.yview)
    logscroll.grid(row=0, column=1, sticky="ns")
    logbox.configure(yscrollcommand=logscroll.set)
    status_label = ttk.Label(root, textvariable=status, style="Sub.TLabel", padding=(14, 5, 14, 8), wraplength=600)
    status_label.grid(row=5, column=0, sticky="ew")

    state = {"busy": False, "closed": False, "search": 0, "poll": None}
    callbacks = queue.Queue()
    def schedule(fn):
        if not state["closed"]:
            callbacks.put(fn)
    def log(message):
        message = str(message)
        print(message, flush=True)
        _file_log(message)
        schedule(lambda: _w(logbox, message))
    def poll():
        for _ in range(100):
            try:
                fn = callbacks.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception as error:
                # One failed callback must not stop every future GUI update.
                log(f"!! Interface error: {error}")
        if not state["closed"]:
            state["poll"] = root.after(30, poll)
    checked = set()
    def resize(event):
        if event.widget is root:
            status_label.configure(wraplength=max(200, event.width - 28))
    root.bind("<Configure>", resize)
    def update_selection():
        n = len(checked)
        selection.set(f"{n} checked" if n else "Check packages to install.")
        install_button.configure(text=f"Install ({n})" if n else "Install")
        install_button.configure(state="disabled" if state["busy"] or (tree.get_children() and not n) else "normal")
    def toggle_package(event):
        if state["busy"]:
            return "break"
        item = tree.focus() if event.keysym == "space" else tree.identify_row(event.y)
        if not item:
            return
        if item in checked:
            checked.remove(item)
        else:
            checked.add(item)
        tree.item(item, text="☑" if item in checked else "☐")
        tree.focus_set()
        tree.focus(item)
        tree.selection_set(item)
        update_selection()
        return "break"
    tree.bind("<Button-1>", toggle_package)
    tree.bind("<space>", toggle_package)
    def busy(value, message=""):
        state["busy"] = value
        for widget in controls:
            widget.configure(state="disabled" if value else "normal")
        stop_button.configure(state="normal" if value else "disabled")
        if value:
            progressbar.pack(side="right")
            progressbar.start(12)
        else:
            progressbar.stop()
            progressbar.pack_forget()
        update_selection()
        if message:
            status.set(message)
    def progress(message):
        schedule(lambda: status.set(str(message)))
    def refresh_info():
        version = pip_ver()
        schedule(lambda: info.set(f"Python {py_ver()}  |  pip {version}  |  {OS}"))
    def start(message, fn, done_message="Done."):
        if state["busy"]:
            return
        _CANCEL.clear()
        busy(True, message)
        def worker():
            try:
                ok = fn(log, progress)
                if _CANCEL.is_set():
                    result = "Cancelled."
                else:
                    result = done_message if ok else "Operation failed. See Activity for details."
            except Exception as error:
                log(f"!! {error}")
                result = "Operation failed. See Activity for details."
            schedule(lambda: busy(False, result))
            refresh_info()
        threading.Thread(target=worker, daemon=True).start()
    def stop():
        if not state["busy"]:
            return
        log("Stopping the current operation…")
        if not cancel_current():
            log("No package command is running right now; stopping before the next one.")
        status.set("Stopping…")
    def new_venv():
        if state["busy"]:
            return
        directory = filedialog.askdirectory(
            parent=root, mustexist=False,
            title="Choose a project folder — a .venv is created inside it")
        if not directory:
            return
        target = Path(directory) / ".venv"
        start("Creating virtual environment…",
              lambda log, progress: create_venv(target, log, progress),
              "Virtual environment ready. Packages install there now.")
    def confirm_update():
        if state["busy"]:
            return
        if messagebox.askyesno(APP, "Update every installed package to latest?\nThis can take a while."):
            start("Updating packages…", update_all, "Packages updated.")
    def do_search():
        if state["busy"]:
            return
        query = entry.get().strip()
        if not query:
            status.set("Type a package name first, e.g. pandas or pillow.")
            return
        state["search"] += 1
        token = state["search"]
        busy(True, f"Searching for {query}…")
        count.set(f"Searching for {query}…")
        def fill(names, error):
            if token != state["search"]:
                return
            children = tree.get_children()
            if children:
                tree.delete(*children)
            checked.clear()
            tree.configure(height=max(1, min(5, len(names))))
            installrow.grid()
            popular = _pypi_popularity() or {}
            rank = _popularity_rank(popular)
            for name in names:
                downloads = download_count(name)
                # Outside the top-download list, or barely downloaded, is the tell
                # for a name-squatter. Unknown counts alone are not evidence.
                rare = bool(rank) and (normalize(name) not in rank
                                       or (downloads is not None and downloads < 1000))
                tree.insert("", "end", iid=name, text="☐",
                            values=(name, human_downloads(downloads)),
                            tags=("rare",) if rare else ())
            if names:
                results.grid()
                tree.see(names[0])
            if not names:
                results.grid_remove()
            count.set(f"{len(names)} results for ‘{query}’")
            message = error or (f"Found {len(names)} packages. Check one or more to install." if names
                               else f"No match for ‘{query}’. Install can try the exact name.")
            update_selection()
            busy(False, message)
        def annotate(names, installed):
            if token != state["search"]:
                return
            installed = {normalize(name) for name in installed}
            for name in names:
                if tree.exists(name) and normalize(name) in installed:
                    values = list(tree.item(name, "values"))
                    values[0] = name + "  (installed)"
                    tree.item(name, values=values, tags=("inst",))
        def worker():
            log(f"Searching '{query}' …")
            try:
                names = list(dict.fromkeys(search_pypi(query)))
                error = None
                log(f"Search done: {len(names)} candidates")
            except Exception as exc:
                names, error = [], f"Search failed: {exc}"
                log(error)
            schedule(lambda: fill(names, error))
            if names:
                try:
                    installed = installed_set()
                    schedule(lambda: annotate(names, installed))
                except Exception as exc:
                    log(f"Could not check installed packages: {exc}")
        threading.Thread(target=worker, daemon=True).start()
    def do_install_at(event=None):
        if state["busy"]:
            return
        names = [name for name in tree.get_children() if name in checked]
        if not names and tree.get_children():
            status.set("Check one or more packages first.")
            return
        if not names:
            names = [entry.get().strip()]
        names = [name for name in names if name]
        if not names:
            status.set("Select a package or type its exact name first.")
            return
        start("Installing " + ", ".join(names),
              lambda log, progress: install_pkgs(names, log, True, progress), "Installation complete.")
    def close():
        state["closed"] = True
        # Never leave pip writing to site-packages after the window is gone.
        cancel_current()
        progressbar.stop()  # its animation timer would otherwise fire after destroy
        if state["poll"] is not None:
            root.after_cancel(state["poll"])
        root.destroy()
    root.protocol("WM_DELETE_WINDOW", close)
    entry.bind("<Return>", lambda event: do_search())
    entry.focus_set()
    state["poll"] = root.after(30, poll)
    log(f"{APP} ready. Python {py_ver()}.")
    log(f"Package target: {PYS}")
    threading.Thread(target=refresh_info, daemon=True).start()
    if auto_update:
        start("Updating packages…", update_all, "Packages updated.")
    root.mainloop()


def venv_python(directory):
    return Path(directory) / ("Scripts/python.exe" if OS == "Windows" else "bin/python")


def create_venv(directory, log=log_fn, progress=None):
    """Create a virtual environment and retarget PyPack at it.

    Managed and read-only interpreters otherwise dead-end a GUI user into a
    terminal command they cannot run from a double-clicked executable.
    """
    directory = Path(directory).expanduser()
    if directory.exists() and any(directory.iterdir()):
        log(f"!! {directory} already exists and is not empty.")
        return False
    log(f"Creating virtual environment in {directory} ...")
    if progress:
        try:
            progress("Creating virtual environment")
        except Exception:
            pass
    ok, out = run([PYS, "-m", "venv", str(directory)], timeout=300)
    if not ok:
        log(out[-1500:] or "!! could not create it (on Debian/Ubuntu, install python3-venv).")
        return False
    if not select_python(str(venv_python(directory))):
        log("!! The new environment did not provide a usable Python.")
        return False
    log(f"Ready. Packages now install into {directory}")
    return True


def select_python(path):
    """Validate a real interpreter and cache its environment, never this EXE."""
    global PYS, _TARGET_INFO, _PIPVER
    if not path or (FROZEN and os.path.normcase(os.path.abspath(path)) == os.path.normcase(sys.executable)):
        return False
    # os.access only. A real create-and-delete probe can block indefinitely on
    # security software that hooks writes to site-packages, which would make
    # interpreter discovery time out and reject every Python on the machine.
    probe = (
        "import sys,os,json,platform,sysconfig\n"
        "site=sysconfig.get_path('purelib')\n"
        "print(json.dumps({'path':sys.executable,'version':platform.python_version(),"
        "'supported':sys.version_info >= (3,10),"
        "'managed':sys.prefix == sys.base_prefix and "
        "os.path.isfile(os.path.join(sysconfig.get_path('stdlib'),'EXTERNALLY-MANAGED')),"
        "'writable':not os.path.isdir(site) or os.access(site,os.W_OK)}))\n")
    ok, output = run([str(path), "-c", probe], timeout=15)
    if not ok:
        return False
    try:
        info = json.loads(output.strip())
        if not info["supported"]:
            return False
        PYS, _TARGET_INFO, _PIPVER = info["path"], info, None
        return True
    except (ValueError, KeyError):
        return False


def discover_python():
    candidates = []
    if os.environ.get("PYPACK_PYTHON"):
        candidates.append(os.environ["PYPACK_PYTHON"])
    if os.environ.get("VIRTUAL_ENV"):
        candidates.append(str(Path(os.environ["VIRTUAL_ENV"]) / ("Scripts/python.exe" if OS == "Windows" else "bin/python")))
    if not FROZEN:
        candidates.append(sys.executable)
    if OS == "Windows":
        # Prefer actual executables over Store execution aliases on PATH.
        bases = [Path(os.environ.get("ProgramFiles") or "C:/Program Files")]
        local = os.environ.get("LOCALAPPDATA")
        if local:  # Path.home() raises when the profile environment is absent
            bases.insert(0, Path(local) / "Programs/Python")
        for base in bases:
            candidates.extend(str(item) for item in sorted(base.glob("Python*/python.exe"), reverse=True))
        launcher = shutil.which("py")
        if launcher:
            ok, output = run([launcher, "-3", "-c", "import sys; print(sys.executable)"], timeout=15)
            if ok:
                candidates.append(output.strip())
    candidates.extend(shutil.which(name) for name in ("python3", "python"))
    for candidate in dict.fromkeys(candidates):
        if candidate and select_python(candidate):
            return True
    return False


def prepare_python(gui):
    if discover_python():
        return True
    if not gui:
        print("No Python 3.10+ found. Set PYPACK_PYTHON to a real interpreter path.")
        return False
    import tkinter as tk
    from tkinter import filedialog, messagebox
    dialog = tk.Tk()
    dialog.withdraw()
    try:
        if OS == "Windows":
            choice = messagebox.askyesnocancel(APP, "Python 3.10+ is required to install packages.\n\nYes: install Python\nNo: choose an existing Python\nCancel: close", parent=dialog)
            if choice is None:
                return False
            if choice:
                messagebox.showinfo(APP, "Python will now install. This can take a few minutes.\nPyPack will open when installation finishes.", parent=dialog)
                ok, output = run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(resource_path("pypack-bootstrap.ps1"))], timeout=900)
                if ok and discover_python():
                    return True
                messagebox.showerror(APP, "Python installation did not finish successfully.\n" + output[-1000:], parent=dialog)
                return False
        path = filedialog.askopenfilename(parent=dialog, title="Choose Python 3.10+", filetypes=[("Python executable", "*.exe"), ("All files", "*")])
        if path and select_python(path):
            return True
        if path:
            messagebox.showerror(APP, "That file is not a supported Python interpreter.", parent=dialog)
        return False
    finally:
        dialog.destroy()


def usage():
    return ("Usage: pypack_setup.py [--gui] [--update] | fix | update-all | "
            "search <query> | install <packages> | get-python\n"
            "       pypack_setup.py --help | --version\n"
            "Exit codes: 0 success, 1 operational failure, 2 invalid arguments.")


def main(argv):
    """Return a process status; dispatch only the first argument as a command."""
    args = argv[1:]
    if args and args[0].lower() in ("--help", "-h", "help"):
        print(usage())
        return 0
    if args and args[0].lower() in ("--version", "-v"):
        print(f"{APP} {app_version()}")
        return 0
    if sys.version_info < (3, 10):
        print(f"!! Python {py_ver()} too old (need 3.10+). Opening download page...")
        open_python_download()
        return 1
    if len(args) == 2 and args[0] == "--self-test":
        report = {"frozen": FROZEN, "ok": False, "app": app_version()}
        try:
            if not prepare_python(False):
                raise RuntimeError("No supported target Python found")
            import tkinter as tk
            test_root = tk.Tk()
            test_root.withdraw()
            try:
                image = tk.PhotoImage(file=str(resource_path("assets/pypack-icon.png")))
                test_root.iconphoto(True, image)
                report.update(python=PYS, version=py_ver(), pip=pip_ver(),
                              icon_size=[image.width(), image.height()],
                              tcl=test_root.tk.call("info", "patchlevel"),
                              writable=_TARGET_INFO.get("writable", True),
                              self_recursion=os.path.normcase(PYS) == os.path.normcase(sys.executable))
                report["ok"] = report["pip"] != "missing" and not (FROZEN and report["self_recursion"])
            finally:
                test_root.destroy()
        except Exception as error:
            report["error"] = str(error)
        destination = Path(args[1])
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return 0 if report["ok"] else 1
    if FROZEN or os.environ.get("PYPACK_PYTHON"):
        if not prepare_python(not args or args[0].lower() in ("--gui", "--update", "--auto-update")):
            return 1
    command = args[0].lower() if args else "--gui"
    if command == "--gui" or (len(args) == 1 and command in ("--update", "--auto-update")):
        if any(arg.lower() not in ("--gui", "--update", "--auto-update") for arg in args):
            print("!! Unknown GUI option.")
            return 2
        try:
            launch_gui(auto_update=any(arg.lower() in ("--update", "--auto-update") for arg in args))
            return 0
        except Exception as error:
            print(f"GUI unavailable ({error}). Install tkinter or use the CLI:")
            print(f"  {PYS} pypack_setup.py fix | update-all | search <query> | install <packages>")
            return 1
    if command in ("fix", "essentials", "update-all", "update", "get-python"):
        if len(args) != 1:
            print(f"!! {command} does not accept extra arguments.")
            return 2
        if command == "get-python":
            open_python_download()
            return 0
        operation = ensure_essentials if command in ("fix", "essentials") else update_all
        return 0 if operation() else 1
    if command == "search":
        if len(args) != 2 or not args[1].strip():
            print("Usage: pypack_setup.py search <query>")
            return 2
        print(json.dumps(search_pypi(args[1])))
        return 0
    if command == "install":
        if len(args) < 2:
            print("Usage: pypack_setup.py install <package> [more packages]")
            return 2
        return 0 if install_pkgs(args[1:]) else 1
    print(usage())
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
