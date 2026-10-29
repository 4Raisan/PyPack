"""Drive the real tkinter window offline: no packages installed, no network.

Skipped automatically where no display is available (headless Linux CI).
"""
import contextlib
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

try:
    import tkinter as tk
    from tkinter import ttk
except ImportError:  # headless build without python3-tk
    tk = ttk = None

import pypack_setup as app


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


class GuiTests(unittest.TestCase):
    def setUp(self):
        if tk is None:
            self.skipTest("tkinter is not installed")
        try:
            probe = tk.Tk()
        except tk.TclError:
            self.skipTest("No display available for tkinter")
        probe.destroy()

        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = TemporaryDirectory(prefix="pypack-gui-test-")
        self.addCleanup(self.temp.cleanup)
        self.root_dir = Path(self.temp.name)
        self.cancel = threading.Event()
        self.roots = []

        for target, value in (
            ("PYS", "target-python"),
            ("FROZEN", False),
            ("_TARGET_INFO", {"version": "3.12.0", "managed": False, "writable": True}),
            ("_CANCEL", self.cancel),
            ("_POPULAR", None),
            ("_RANK_SOURCE", None),
            ("_RANK", None),
        ):
            self.stack.enter_context(patch.object(app, target, value))
        self.stack.enter_context(patch.object(app, "cache_dir", return_value=self.root_dir / "cache"))
        self.stack.enter_context(patch.object(app, "log_dir", return_value=self.root_dir / "logs"))
        self.stack.enter_context(patch.object(app, "_file_log"))
        self.stack.enter_context(patch.object(app, "pip_ver", return_value="24.0"))
        self.stack.enter_context(patch.object(app, "installed_set", return_value=set()))
        self.stack.enter_context(patch.object(app, "legacy_cache",
                                              side_effect=lambda name: self.root_dir / name))
        self.popular = self.stack.enter_context(
            patch.object(app, "_pypi_popularity", return_value={"numpy": 801_552_837}))
        self.search = self.stack.enter_context(
            patch.object(app, "search_pypi", return_value=["numpy", "NuMPI"]))
        self.install = self.stack.enter_context(patch.object(app, "install_pkgs", return_value=True))

    def build(self, auto_update=False):
        captured = {}

        def fake_mainloop(root, *args):
            captured["root"] = root

        self.stack.enter_context(patch.object(tk.Tk, "mainloop", fake_mainloop))
        app.launch_gui(auto_update=auto_update)
        self.window = captured["root"]
        self.roots.append(self.window)
        self.stack.callback(self.destroy_windows)
        widgets = list(descendants(self.window))
        self.tree = next(w for w in widgets if isinstance(w, ttk.Treeview))
        self.entry = next(w for w in widgets if isinstance(w, ttk.Entry))
        self.buttons = {}
        for widget in widgets:
            if isinstance(widget, ttk.Button):
                self.buttons[str(widget.cget("text"))] = widget
        self.status_label = next(w for w in widgets if isinstance(w, ttk.Label)
                                 and w.grid_info().get("row") == 5)
        return self.window

    def destroy_windows(self):
        for window in self.roots:
            with contextlib.suppress(tk.TclError):
                # Go through the real close handler so timers are cancelled too.
                command = window.protocol("WM_DELETE_WINDOW")
                window.tk.call(command) if command else window.destroy()

    def pump(self, predicate, timeout=5.0):
        """Run the Tk event loop until predicate holds; after() needs real updates."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.window.update()
            if predicate():
                return True
            time.sleep(0.005)
        return False

    def value(self, widget):
        name = str(widget.cget("textvariable"))
        return self.window.tk.call("set", name) if name else str(widget.cget("text"))

    def disabled(self, text):
        return self.buttons[text].instate(["disabled"])

    def run_search(self, query="numpi"):
        self.entry.delete(0, "end")
        self.entry.insert(0, query)
        self.buttons["Search"].invoke()
        return self.pump(lambda: not self.disabled("Search"))

    def check_first_row(self):
        """Click the checkbox column. Focus-independent, so it is stable headless."""
        self.window.update()
        box = self.tree.bbox(self.tree.get_children()[0])
        self.tree.event_generate("<Button-1>", x=box[0] + 5, y=box[1] + box[3] // 2, when="now")
        self.window.update()

    # --- layout ----------------------------------------------------------

    def test_window_shows_version_and_a_download_column(self):
        self.build()
        self.assertIn(app.app_version(), self.window.title())
        self.assertEqual(tuple(self.tree.cget("columns")), ("name", "downloads"))
        for text in ("Fix pip", "Update all", "New venv", "Get Python", "Search", "Stop", "Install"):
            self.assertIn(text, self.buttons)
        self.assertTrue(self.disabled("Stop"), "Stop must be inert while idle")

    def test_results_table_starts_hidden(self):
        self.build()
        self.assertFalse(self.tree.master.winfo_ismapped())

    # --- search ----------------------------------------------------------

    def test_search_lists_downloads_and_flags_unpopular_names(self):
        self.build()
        self.assertTrue(self.run_search())
        self.assertEqual(self.tree.get_children(), ("numpy", "NuMPI"))
        self.assertEqual(self.tree.item("numpy", "values"), ("numpy", "801.6M"))
        self.assertFalse(self.tree.item("numpy", "tags"))
        self.assertEqual(self.tree.item("NuMPI", "values"), ("NuMPI", "—"))
        self.assertIn("rare", self.tree.item("NuMPI", "tags"))
        self.search.assert_called_once_with("numpi")

    def test_installed_marker_keeps_the_download_column(self):
        self.stack.enter_context(patch.object(app, "installed_set", return_value={"numpy"}))
        self.build()
        self.assertTrue(self.run_search())
        self.assertTrue(self.pump(lambda: "installed" in self.tree.item("numpy", "values")[0]))
        self.assertEqual(self.tree.item("numpy", "values"), ("numpy  (installed)", "801.6M"))
        self.assertEqual(self.tree.item("numpy", "tags"), ("inst",))

    def test_empty_query_asks_for_a_name_without_searching(self):
        self.build()
        self.buttons["Search"].invoke()
        self.window.update()
        self.search.assert_not_called()
        self.assertIn("Type a package name", self.value(self.status_label))

    def test_no_results_hides_the_table(self):
        self.search.return_value = []
        self.build()
        self.assertTrue(self.run_search("definitely-not-a-package"))
        self.assertEqual(self.tree.get_children(), ())
        self.assertFalse(self.tree.master.winfo_ismapped())
        self.assertIn("No match", self.value(self.status_label))

    def test_failed_search_reports_the_error(self):
        self.search.side_effect = RuntimeError("index unreachable")
        self.build()
        self.assertTrue(self.run_search())
        self.assertIn("Search failed", self.value(self.status_label))

    # --- selection and install -------------------------------------------

    def test_space_and_click_toggle_the_checkbox(self):
        self.build()
        self.assertTrue(self.run_search())
        self.check_first_row()
        self.assertEqual(self.buttons["Install"].cget("text"), "Install (1)")
        self.assertEqual(self.tree.item("numpy", "text"), "☑")
        # Space is the keyboard path; force focus so key delivery does not depend
        # on which of the several Tk roots in this process last held it.
        self.window.focus_force()
        self.tree.focus_set()
        self.tree.focus("numpy")
        self.window.update()
        self.tree.event_generate("<space>", when="now")
        self.window.update()
        self.assertEqual(self.buttons["Install"].cget("text"), "Install")
        self.assertEqual(self.tree.item("numpy", "text"), "☐")

    def test_install_sends_only_checked_names(self):
        self.build()
        self.assertTrue(self.run_search())
        self.check_first_row()
        self.buttons["Install"].invoke()
        self.assertTrue(self.pump(lambda: self.install.called))
        self.assertEqual(self.install.call_args[0][0], ["numpy"])

    def test_install_without_a_selection_is_refused(self):
        self.build()
        self.assertTrue(self.run_search())
        self.buttons["Install"].invoke()
        self.window.update()
        self.install.assert_not_called()
        self.assertIn("Check one or more", self.value(self.status_label))

    def test_typed_name_installs_when_nothing_is_listed(self):
        self.search.return_value = []
        self.build()
        self.assertTrue(self.run_search("some-exact-name"))
        self.buttons["Install"].invoke()
        self.assertTrue(self.pump(lambda: self.install.called))
        self.assertEqual(self.install.call_args[0][0], ["some-exact-name"])

    # --- busy state and cancellation -------------------------------------

    def test_stop_enables_while_busy_and_disables_after(self):
        gate = threading.Event()
        self.install.side_effect = lambda *a: (gate.wait(5), True)[1]
        self.build()
        self.assertTrue(self.run_search())
        self.check_first_row()
        self.buttons["Install"].invoke()
        self.assertTrue(self.pump(lambda: not self.disabled("Stop")), "Stop never enabled")
        self.assertTrue(self.disabled("Search"))
        self.assertTrue(self.disabled("Fix pip"))
        gate.set()
        self.assertTrue(self.pump(lambda: self.disabled("Stop")), "Stop never reset")
        self.assertFalse(self.disabled("Search"))

    def test_stop_cancels_the_running_operation(self):
        gate = threading.Event()
        self.install.side_effect = lambda *a: (gate.wait(5), not self.cancel.is_set())[1]
        self.build()
        self.assertTrue(self.run_search())
        self.check_first_row()
        self.buttons["Install"].invoke()
        self.assertTrue(self.pump(lambda: not self.disabled("Stop")))
        self.buttons["Stop"].invoke()
        self.assertTrue(self.cancel.is_set(), "Stop did not request cancellation")
        gate.set()
        self.assertTrue(self.pump(lambda: self.disabled("Stop")))
        self.assertEqual(self.value(self.status_label), "Cancelled.")

    def test_closing_the_window_cancels_the_active_child(self):
        self.build()
        with patch.object(app, "cancel_current") as cancel:
            command = self.window.protocol("WM_DELETE_WINDOW")
            self.window.tk.call(command)
        cancel.assert_called_once()
        self.roots.clear()  # already destroyed by close()

    def test_update_all_asks_before_starting(self):
        self.build()
        with patch("tkinter.messagebox.askyesno", return_value=False) as ask:
            self.buttons["Update all"].invoke()
        ask.assert_called_once()
        self.assertTrue(self.disabled("Stop"), "declining must not start an update")

    # --- virtual environments --------------------------------------------

    def test_new_venv_creates_inside_the_chosen_folder(self):
        create = self.stack.enter_context(patch.object(app, "create_venv", return_value=True))
        self.build()
        with patch("tkinter.filedialog.askdirectory", return_value=str(self.root_dir)):
            self.buttons["New venv"].invoke()
        self.assertTrue(self.pump(lambda: create.called))
        self.assertEqual(create.call_args[0][0], self.root_dir / ".venv")

    def test_new_venv_cancelled_dialog_does_nothing(self):
        create = self.stack.enter_context(patch.object(app, "create_venv"))
        self.build()
        with patch("tkinter.filedialog.askdirectory", return_value=""):
            self.buttons["New venv"].invoke()
        self.window.update()
        create.assert_not_called()


if __name__ == "__main__":
    unittest.main()