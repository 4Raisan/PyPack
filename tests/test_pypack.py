"""Exercise package operations without installing packages or contacting PyPI."""
import contextlib
import io
import json
import unittest
from unittest.mock import patch

import pypack_setup as app


class PyPackTests(unittest.TestCase):
    def setUp(self):
        self.state = patch.multiple(app, PYS="target-python", FROZEN=False,
                                    _TARGET_INFO=None, _PIPVER=None)
        self.state.start()
        self.addCleanup(self.state.stop)

    def dispatch(self, *args):
        with contextlib.redirect_stdout(io.StringIO()):
            return app.main(["pypack_setup.py", *args])

    @patch.dict(app.os.environ, {}, clear=True)
    def test_invalid_commands_and_missing_arguments_never_run_pip(self):
        with patch.object(app, "run") as run:
            for args in (("unknown",), ("search",), ("search", ""),
                         ("install",), ("fix", "extra"), ("--gui", "wrong")):
                with self.subTest(args=args):
                    self.assertEqual(self.dispatch(*args), 2)
            run.assert_not_called()

    @patch.dict(app.os.environ, {}, clear=True)
    def test_gui_does_not_update_unless_requested(self):
        with patch.object(app, "launch_gui") as gui:
            self.assertEqual(self.dispatch(), 0)
            gui.assert_called_once_with(auto_update=False)
            gui.reset_mock()
            self.assertEqual(self.dispatch("--gui", "--update"), 0)
            gui.assert_called_once_with(auto_update=True)

    @patch.dict(app.os.environ, {}, clear=True)
    def test_cli_propagates_operational_failures(self):
        for command, operation in (("fix", "ensure_essentials"),
                                   ("update-all", "update_all")):
            with self.subTest(command=command), patch.object(app, operation, return_value=False):
                self.assertEqual(self.dispatch(command), 1)
        with patch.object(app, "install_pkgs", return_value=False):
            self.assertEqual(self.dispatch("install", "example"), 1)

    def test_managed_python_blocks_changes_before_network_or_pip(self):
        app._TARGET_INFO = {"managed": True}
        with patch.object(app, "run") as run, patch.object(app, "has_net") as net:
            self.assertFalse(app.install_pkgs(["example"], log=lambda _: None))
            self.assertFalse(app.ensure_essentials(log=lambda _: None))
            run.assert_not_called()
            net.assert_not_called()

    def test_install_rejects_empty_names_and_pip_options(self):
        with patch.object(app, "can_modify_environment", return_value=True), patch.object(app, "run") as run:
            for names in ([], [" "], ["--target=elsewhere"]):
                self.assertFalse(app.install_pkgs(names, log=lambda _: None))
            run.assert_not_called()

    def test_multiple_requirements_are_separate_arguments(self):
        with patch.object(app, "can_modify_environment", return_value=True), \
             patch.object(app, "installed_ver", return_value="1.0"), \
             patch.object(app, "run", return_value=(True, "")) as run:
            self.assertTrue(app.install_pkgs(["first>=1", "second[extra]"], log=lambda _: None))
            run.assert_called_once_with(
                ["target-python", "-m", "pip", "--disable-pip-version-check", "--no-input",
                 "install", "-q", "--upgrade", "--", "first>=1", "second[extra]"], timeout=900)

    def test_failed_install_returns_failure_and_keeps_error(self):
        messages = []
        with patch.object(app, "can_modify_environment", return_value=True), \
             patch.object(app, "run", return_value=(False, "dependency conflict")):
            self.assertFalse(app.install_pkgs(["example"], log=messages.append))
        self.assertIn("dependency conflict", messages)

    def test_failed_essentials_prevents_bulk_update(self):
        with patch.object(app, "ensure_essentials", return_value=False), patch.object(app, "run") as run:
            self.assertFalse(app.update_all(log=lambda _: None))
            run.assert_not_called()

    def test_bulk_update_reports_partial_failure(self):
        responses = [(True, "pip==1\nsetuptools==1\nwheel==1\nfirst==1\nsecond==1\n"),
                     (True, ""), (False, "resolution failed")]
        messages = []
        with patch.object(app, "ensure_essentials", return_value=True), \
             patch.object(app, "run", side_effect=responses) as run:
            self.assertFalse(app.update_all(log=messages.append))
            self.assertEqual(run.call_count, 3)
        self.assertTrue(any("second" in message and "skipped" in message for message in messages))

    def test_offline_fix_does_not_start_bootstrap(self):
        with patch.object(app, "can_modify_environment", return_value=True), \
             patch.object(app, "has_net", return_value=False), patch.object(app, "run") as run:
            self.assertFalse(app.ensure_essentials(log=lambda _: None))
            run.assert_not_called()

    def test_failed_pip_bootstrap_stops_before_upgrade(self):
        with patch.object(app, "can_modify_environment", return_value=True), \
             patch.object(app, "has_net", return_value=True), \
             patch.object(app.urlreq, "urlretrieve"), \
             patch.object(app, "run", side_effect=[(False, ""), (False, ""), (False, "failed")]) as run:
            self.assertFalse(app.ensure_essentials(log=lambda _: None))
            self.assertEqual(run.call_count, 3)

    def test_interpreter_probe_requires_supported_python(self):
        for output in ("not json", json.dumps({"supported": False})):
            with patch.object(app, "run", return_value=(True, output)):
                self.assertFalse(app.select_python("other-python"))
                self.assertEqual(app.PYS, "target-python")

    def test_interpreter_selection_resets_pip_cache(self):
        info = {"supported": True, "path": "chosen-python", "version": "3.12.0", "managed": False}
        app._PIPVER = "stale"
        with patch.object(app, "run", return_value=(True, json.dumps(info))):
            self.assertTrue(app.select_python("other-python"))
        self.assertEqual(app.PYS, "chosen-python")
        self.assertIsNone(app._PIPVER)
        self.assertEqual(app.py_ver(), "3.12.0")

    def test_frozen_application_cannot_select_itself_as_python(self):
        app.FROZEN = True
        with patch.object(app, "run") as run:
            self.assertFalse(app.select_python(app.sys.executable))
            run.assert_not_called()

    def test_subprocess_timeout_returns_failure(self):
        with patch.object(app.subprocess, "run", side_effect=app.subprocess.TimeoutExpired("pip", 1)):
            ok, error = app.run(["target-python", "-m", "pip"], timeout=1)
        self.assertFalse(ok)
        self.assertIn("timed out", error)

    def test_search_prefers_exact_match_and_respects_limit(self):
        with patch.object(app, "_pypi_names", return_value=["pandas-extra", "pandas", "geopandas"]), \
             patch.object(app, "_pypi_popularity", return_value=["geopandas", "pandas-extra"]), \
             patch.object(app, "run") as run:
            self.assertEqual(app.search_pypi("pandas", limit=2), ["pandas", "pandas-extra"])
            self.assertEqual(app.search_pypi("", limit=2), [])
            self.assertEqual(app.search_pypi("pandas", limit=0), [])
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
