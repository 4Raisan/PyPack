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

def log_fn(msg, widget=None):
    print(msg, flush=True)
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

def search_pypi(name, limit=20):
    """Light PyPI search via stdlib only. Returns [names] (best first)."""
    name = (name or "").strip()
    if not name or limit <= 0:
        return []
    # Fast lane: PyPI simple index, cached locally (refetched every 12h),
    # then substring match -> real multi-result search.
    try:
        names = _pypi_names()
        if names:
            q = name.lower()
            starts, contains = [], []
            for n in names:
                ln = n.lower()
                if ln.startswith(q):
                    starts.append(n)
                elif q in ln:
                    contains.append(n)
            if not starts and not contains:
                return []
            pop = _pypi_popularity() or []
            rank = {n.lower(): i for i, n in enumerate(pop)}
            starts.sort(key=lambda n: (n.lower() != q, rank.get(n.lower(), 10**9), len(n), n.lower()))
            contains.sort(key=lambda n: (rank.get(n.lower(), 10**9), len(n), n.lower()))
            out = (starts + contains)[:limit]
            if out:
                return out
    except Exception:
        pass
    # Slow lanes: exact name via pip, then JSON, then installed-fuzzy.
    ok, outp = run(pip_cmd("index", "versions", name,
                           "--disable-pip-version-check"), timeout=30)
    if ok and outp and outp.splitlines():
        for line in outp.splitlines():
            match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s+\([^)]+\)", line)
            if match:
                return [match.group(1)]
    try:
        req = urlreq.Request(f"https://pypi.org/pypi/{name}/json",
                             headers={"User-Agent": "PyPack/1.0"})
        with urlreq.urlopen(req, timeout=8) as r:
            return [json.load(r)["info"]["name"]]
    except Exception:
        pass
    import difflib
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

def latest_ver(pkg):
    ok, out = run(pip_cmd("index", "versions", pkg.strip(),
                          "--disable-pip-version-check"), timeout=30)
    if ok and out:
        for line in out.splitlines():
            if "LATEST:" in line.upper():
                return line.split(":")[-1].strip()
        first = out.splitlines()[0]
        if "(" in first and ")" in first:
            return first.split("(")[1].split(")")[0].strip()
    try:
        req = urlreq.Request(f"https://pypi.org/pypi/{pkg}/json",
                             headers={"User-Agent": "PyPack/1.0"})
        with urlreq.urlopen(req, timeout=8) as r:
            return json.load(r)["info"]["version"]
    except Exception:
        return None

def install_pkgs(pkgs, log=print, upgrade=True, progress=None):
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
    return True

def update_all(log=print, progress=None):
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
    return not bad

def open_python_download(log=print):
    url = "https://www.python.org/downloads/"
    if OS == "Darwin":
        url = "https://www.python.org/downloads/macos/"
    log(f">> Get/Update Python: {url}")
    log(">> Win: winget install Python.Python.3.12 | Mac: brew install python | Linux: sudo apt install python3 python3-pip")
    webbrowser.open(url)

def launch_gui(auto_update=False):
    """Optional compact package browser with an always-visible activity terminal."""
    import tkinter as tk
    from tkinter import ttk, messagebox
    import re

    root = tk.Tk()
    root.title(APP)
    icon = resource_path("assets/pypack-icon.png")
    if icon.exists():
        root._app_icon = tk.PhotoImage(file=str(icon))
        root.iconphoto(True, root._app_icon)
    root.minsize(480, 1)
    BG, CARD, FG, SUB, AC = "#0e1117", "#161b26", "#e8eaf0", "#8b90a5", "#6c5ce7"
    root.configure(background=BG)
    root.columnconfigure(0, weight=1)
    root.rowconfigure(4, weight=1)
    st = ttk.Style(root)
    try:
        st.theme_use("clam")
    except tk.TclError:
        pass
    st.configure(".", background=BG, foreground=FG, font=("Segoe UI", 10))
    st.configure("TFrame", background=BG)
    st.configure("Card.TFrame", background=CARD)
    st.configure("TLabel", background=BG, foreground=FG)
    st.configure("Sub.TLabel", foreground=SUB)
    st.configure("Title.TLabel", font=("Segoe UI Semibold", 18), foreground="#a29bfe")
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
    ttk.Label(head, textvariable=info, style="Sub.TLabel", font=("Segoe UI", 9)).pack(side="right")

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
    tree = ttk.Treeview(results, columns=("name",), show="tree headings", selectmode="browse", height=3)
    tree.heading("#0", text="")
    tree.column("#0", width=32, minwidth=32, stretch=False, anchor="center")
    tree.heading("name", text="Package")
    tree.column("name", width=320, minwidth=120, anchor="w")
    tree.tag_configure("inst", foreground="#7ee787")
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
    progressbar = ttk.Progressbar(activitybar, mode="indeterminate", length=100)
    progressbar.pack(side="right")
    progressbar.pack_forget()
    activity = ttk.Frame(root, padding=(14, 0, 14, 0))
    activity.grid(row=4, column=0, sticky="nsew")
    activity.columnconfigure(0, weight=1)
    activity.rowconfigure(0, weight=1)
    logbox = tk.Text(activity, width=60, height=4, state="disabled", wrap="word", font=("Consolas", 9),
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
        busy(True, message)
        def worker():
            try:
                ok = fn(log, progress)
                result = done_message if ok else "Operation failed. See Activity for details."
            except Exception as error:
                log(f"!! {error}")
                result = "Operation failed. See Activity for details."
            schedule(lambda: busy(False, result))
            refresh_info()
        threading.Thread(target=worker, daemon=True).start()
    def confirm_update():
        if state["busy"]:
            return
        if messagebox.askyesno(APP, "Update every installed package to latest?\nThis can take a while."):
            start("Updating packages…", update_all, "Packages updated.")
    def normalized(name):
        return re.sub(r"[-_.]+", "-", name).lower()
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
            for name in names:
                tree.insert("", "end", iid=name, text="☐", values=(name,))
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
            installed = {normalized(name) for name in installed}
            for name in names:
                if tree.exists(name) and normalized(name) in installed:
                    tree.item(name, values=(name + "  (installed)",), tags=("inst",))
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

def resource_path(name):
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / name


def select_python(path):
    """Validate a real interpreter and cache its environment, never this EXE."""
    global PYS, _TARGET_INFO, _PIPVER
    if not path or (FROZEN and os.path.normcase(os.path.abspath(path)) == os.path.normcase(sys.executable)):
        return False
    probe = ("import sys,os,json,platform,sysconfig; "
             "print(json.dumps({'path':sys.executable,'version':platform.python_version(),"
             "'supported':sys.version_info >= (3,10),'managed':sys.prefix == sys.base_prefix and "
             "os.path.isfile(os.path.join(sysconfig.get_path('stdlib'),'EXTERNALLY-MANAGED'))}))")
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
        for base in (Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Programs/Python",
                     Path(os.environ.get("ProgramFiles", "C:/Program Files"))):
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


def main(argv):
    """Return a process status; dispatch only the first argument as a command."""
    if sys.version_info < (3, 10):
        print(f"!! Python {py_ver()} too old (need 3.10+). Opening download page...")
        open_python_download()
        return 1
    args = argv[1:]
    if len(args) == 2 and args[0] == "--self-test":
        report = {"frozen": FROZEN, "ok": False}
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
                              self_recursion=os.path.normcase(PYS) == os.path.normcase(sys.executable))
                report["ok"] = report["pip"] != "missing" and not (FROZEN and report["self_recursion"])
            finally:
                test_root.destroy()
        except Exception as error:
            report["error"] = str(error)
        Path(args[1]).write_text(json.dumps(report, indent=2), encoding="utf-8")
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
        print(search_pypi(args[1]))
        return 0
    if command == "install":
        if len(args) < 2:
            print("Usage: pypack_setup.py install <package> [more packages]")
            return 2
        return 0 if install_pkgs(args[1:]) else 1
    print("Usage: pypack_setup.py [--gui] [--update] | fix | update-all | search <query> | install <packages> | get-python")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
