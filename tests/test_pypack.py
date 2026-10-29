"""Exercise package operations without installing packages or contacting PyPI."""
import contextlib
import io
import json
import sys
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, call, patch

import pypack_setup as app


class PyPackTests(unittest.TestCase):
    def setUp(self):
        self.state = patch.multiple(app, PYS="target-python", FROZEN=False,
                                    _TARGET_INFO=None, _PIPVER=None,
                                    _INDEX_NAMES=None, _POPULAR=None,
                                    _SCAN_SOURCE=None, _HAYSTACK=None, _OFFSETS=None,
                                    _BUCKET_SOURCE=None, _BUCKETS=None,
                                    _RANK_SOURCE=None, _RANK=None,
                                    _VERSION=None, _LOGGER=None,
                                    _ACTIVE=None, _CANCEL=threading.Event())
        self.state.start()
        self.addCleanup(self.state.stop)
        # Keep every test off the real user cache and log directory.
        self.real_cache_dir = app.cache_dir
        self.real_log_dir = app.log_dir
        self.temp = TemporaryDirectory(prefix="pypack-test-")
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.close_log_handlers)
        self.root = Path(self.temp.name)
        self.cache = self.root / "cache"
        self.enter(patch.object(app, "cache_dir", return_value=self.cache))
        self.enter(patch.object(app, "log_dir", return_value=self.root / "logs"))
        self.enter(patch.object(app, "legacy_cache",
                                side_effect=lambda name: self.root / "legacy" / name))

    @staticmethod
    def close_log_handlers():
        """Release the log file before the temporary directory is removed."""
        import logging
        logger = logging.getLogger("pypack")
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)

    def enter(self, context):
        """TestCase.enterContext is 3.11+; PyPack still supports 3.10."""
        value = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        return value

    def dispatch(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            status = app.main(["pypack_setup.py", *args])
        return status, output.getvalue()

    # --- command routing -------------------------------------------------

    @patch.dict(app.os.environ, {}, clear=True)
    def test_invalid_commands_and_missing_arguments_never_run_pip(self):
        with patch.object(app, "run") as run:
            for args in (("unknown",), ("search",), ("search", ""),
                         ("install",), ("fix", "extra"), ("--gui", "wrong")):
                with self.subTest(args=args):
                    self.assertEqual(self.dispatch(*args)[0], 2)
            run.assert_not_called()

    @patch.dict(app.os.environ, {}, clear=True)
    def test_help_and_version_succeed_without_touching_pip(self):
        with patch.object(app, "run") as run:
            for flag in ("--help", "-h", "help"):
                status, output = self.dispatch(flag)
                self.assertEqual(status, 0)
                self.assertIn("Usage: pypack_setup.py", output)
            for flag in ("--version", "-v"):
                status, output = self.dispatch(flag)
                self.assertEqual(status, 0)
                self.assertIn(app.app_version(), output)
            run.assert_not_called()

    def test_unknown_command_prints_usage_and_exit_code_contract(self):
        status, output = self.dispatch("unknown")
        self.assertEqual(status, 2)
        self.assertIn("Exit codes: 0 success", output)

    @patch.dict(app.os.environ, {}, clear=True)
    def test_gui_does_not_update_unless_requested(self):
        with patch.object(app, "launch_gui") as gui:
            self.assertEqual(self.dispatch()[0], 0)
            gui.assert_called_once_with(auto_update=False)
            gui.reset_mock()
            self.assertEqual(self.dispatch("--gui", "--update")[0], 0)
            gui.assert_called_once_with(auto_update=True)

    @patch.dict(app.os.environ, {}, clear=True)
    def test_cli_propagates_operational_failures(self):
        for command, operation in (("fix", "ensure_essentials"),
                                   ("update-all", "update_all")):
            with self.subTest(command=command), patch.object(app, operation, return_value=False):
                self.assertEqual(self.dispatch(command)[0], 1)
        with patch.object(app, "install_pkgs", return_value=False):
            self.assertEqual(self.dispatch("install", "example")[0], 1)

    @patch.dict(app.os.environ, {}, clear=True)
    def test_search_prints_json_not_a_python_repr(self):
        with patch.object(app, "search_pypi", return_value=["pandas", "pandas-stubs"]):
            status, output = self.dispatch("search", "panda")
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output), ["pandas", "pandas-stubs"])

    # --- environment protection ------------------------------------------

    def test_managed_python_blocks_changes_before_network_or_pip(self):
        app._TARGET_INFO = {"managed": True}
        with patch.object(app, "run") as run, patch.object(app, "has_net") as net:
            self.assertFalse(app.install_pkgs(["example"], log=lambda _: None))
            self.assertFalse(app.ensure_essentials(log=lambda _: None))
            run.assert_not_called()
            net.assert_not_called()

    def test_unwritable_site_packages_blocks_changes(self):
        app._TARGET_INFO = {"managed": False, "writable": False}
        messages = []
        with patch.object(app, "run") as run:
            self.assertFalse(app.install_pkgs(["example"], log=messages.append))
        run.assert_not_called()
        self.assertTrue(any("not writable" in m for m in messages))
        self.assertTrue(any("New venv" in m for m in messages))

    def test_writable_unmanaged_environment_is_allowed(self):
        app._TARGET_INFO = {"managed": False, "writable": True}
        self.assertTrue(app.can_modify_environment(log=lambda _: None))

    def test_install_rejects_empty_names_and_pip_options(self):
        with patch.object(app, "can_modify_environment", return_value=True), patch.object(app, "run") as run:
            for names in ([], [" "], ["--target=elsewhere"]):
                self.assertFalse(app.install_pkgs(names, log=lambda _: None))
            run.assert_not_called()

    # --- package operations ----------------------------------------------

    def test_multiple_requirements_are_separate_arguments(self):
        with patch.object(app, "can_modify_environment", return_value=True), \
             patch.object(app, "installed_ver", return_value="1.0"), \
             patch.object(app, "check_dependencies", return_value=True), \
             patch.object(app, "run", return_value=(True, "")) as run:
            self.assertTrue(app.install_pkgs(["first>=1", "second[extra]"], log=lambda _: None))
        self.assertEqual(run.call_args_list[0], call(
            ["target-python", "-m", "pip", "--disable-pip-version-check", "--no-input",
             "install", "-q", "--upgrade", "--", "first>=1", "second[extra]"], timeout=900))

    def test_install_reports_dependency_conflicts(self):
        with patch.object(app, "can_modify_environment", return_value=True), \
             patch.object(app, "installed_ver", return_value="1.0"), \
             patch.object(app, "run", side_effect=[(True, ""), (False, "broken requirement")]) as run:
            messages = []
            self.assertTrue(app.install_pkgs(["example"], log=messages.append))
        self.assertEqual(run.call_args_list[-1][0][0][-1], "check")
        self.assertTrue(any("Dependency conflicts" in m for m in messages))
        self.assertTrue(any("broken requirement" in m for m in messages))

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
                     (True, ""), (False, "resolution failed"), (True, "")]
        messages = []
        with patch.object(app, "ensure_essentials", return_value=True), \
             patch.object(app, "run", side_effect=responses) as run:
            self.assertFalse(app.update_all(log=messages.append))
            self.assertEqual(run.call_count, 4)
        self.assertTrue(any("second" in message and "skipped" in message for message in messages))

    def test_bulk_update_checks_dependencies_after_upgrading(self):
        responses = [(True, "first==1\n"), (True, ""), (False, "conflict here")]
        messages = []
        with patch.object(app, "ensure_essentials", return_value=True), \
             patch.object(app, "run", side_effect=responses):
            self.assertTrue(app.update_all(log=messages.append))
        self.assertTrue(any("conflict here" in m for m in messages))

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

    def test_connectivity_probe_honours_a_private_index(self):
        with patch.dict(app.os.environ, {"PIP_INDEX_URL": "https://mirror.internal/pypi/simple"}), \
             patch.object(app.urlreq, "urlopen") as urlopen:
            urlopen.return_value.read.return_value = b"ok"
            self.assertTrue(app.has_net())
        self.assertEqual(urlopen.call_args[0][0], "https://mirror.internal/pypi/simple/")

    # --- uninstall and installed listing ---------------------------------

    def test_uninstall_passes_names_after_a_separator_with_yes(self):
        with patch.object(app, "can_modify_environment", return_value=True), \
             patch.object(app, "check_dependencies", return_value=True), \
             patch.object(app, "run", return_value=(True, "")) as run:
            self.assertTrue(app.uninstall_pkgs(["first", "second"], log=lambda _: None))
        run.assert_called_once_with(
            ["target-python", "-m", "pip", "--disable-pip-version-check", "--no-input",
             "uninstall", "-y", "-q", "--", "first", "second"], timeout=600)

    def test_uninstall_refuses_pip_setuptools_and_wheel(self):
        for name in ("pip", "Setuptools", "WHEEL"):
            with self.subTest(name=name), \
                 patch.object(app, "can_modify_environment", return_value=True), \
                 patch.object(app, "run") as run:
                messages = []
                self.assertFalse(app.uninstall_pkgs([name], log=messages.append))
                run.assert_not_called()
                self.assertTrue(any("Refusing" in m for m in messages))

    def test_uninstall_refuses_a_batch_containing_a_protected_name(self):
        with patch.object(app, "can_modify_environment", return_value=True), \
             patch.object(app, "run") as run:
            self.assertFalse(app.uninstall_pkgs(["numpy", "pip"], log=lambda _: None))
        run.assert_not_called()

    def test_uninstall_rejects_empty_names_and_options(self):
        with patch.object(app, "can_modify_environment", return_value=True), \
             patch.object(app, "run") as run:
            for names in ([], [" "], ["--yes"]):
                self.assertFalse(app.uninstall_pkgs(names, log=lambda _: None))
        run.assert_not_called()

    def test_uninstall_reports_failure_and_keeps_error(self):
        messages = []
        with patch.object(app, "can_modify_environment", return_value=True), \
             patch.object(app, "run", return_value=(False, "permission denied")):
            self.assertFalse(app.uninstall_pkgs(["example"], log=messages.append))
        self.assertIn("permission denied", messages)

    def test_uninstall_blocks_on_a_managed_environment(self):
        app._TARGET_INFO = {"managed": True}
        with patch.object(app, "run") as run:
            self.assertFalse(app.uninstall_pkgs(["example"], log=lambda _: None))
        run.assert_not_called()

    def test_installed_packages_parses_names_and_versions(self):
        with patch.object(app, "run", return_value=(True, "NumPy==2.1.0\nFlask==3.0.1\n")):
            self.assertEqual(app.installed_packages(), [("NumPy", "2.1.0"), ("Flask", "3.0.1")])
            self.assertEqual(app.installed_set(), {"numpy", "flask"})

    def test_installed_packages_ignores_editable_and_direct_lines(self):
        with patch.object(app, "run", return_value=(True, "-e git+https://x/y#egg=z\nfoo @ file:///tmp/foo\nok==1\n")):
            self.assertEqual(app.installed_packages(), [("ok", "1")])
        with patch.object(app, "run", return_value=(False, "")):
            self.assertEqual(app.installed_packages(), [])

    @patch.dict(app.os.environ, {}, clear=True)
    def test_cli_list_prints_json(self):
        with patch.object(app, "installed_packages", return_value=[("numpy", "2.1.0")]):
            status, output = self.dispatch("list")
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output), [{"name": "numpy", "version": "2.1.0"}])
        self.assertEqual(self.dispatch("list", "extra")[0], 2)

    @patch.dict(app.os.environ, {}, clear=True)
    def test_cli_uninstall_routes_and_propagates_failure(self):
        with patch.object(app, "uninstall_pkgs", return_value=True) as uninstall:
            self.assertEqual(self.dispatch("uninstall", "example")[0], 0)
        uninstall.assert_called_once_with(["example"])
        with patch.object(app, "uninstall_pkgs", return_value=False):
            self.assertEqual(self.dispatch("uninstall", "example")[0], 1)
        self.assertEqual(self.dispatch("uninstall")[0], 2)

    # --- cancellation -----------------------------------------------------

    def test_subprocess_timeout_returns_failure_and_kills_the_tree(self):
        process = MagicMock()
        process.poll.return_value = None
        process.communicate.side_effect = [app.subprocess.TimeoutExpired("pip", 1), ("", "")]
        with patch.object(app.subprocess, "Popen", return_value=process), \
             patch.object(app, "kill_tree") as kill:
            ok, error = app.run(["target-python", "-m", "pip"], timeout=1)
        self.assertFalse(ok)
        self.assertIn("timed out", error)
        kill.assert_called_once_with(process)

    def test_cancelled_run_never_starts_a_child(self):
        app._CANCEL.set()
        with patch.object(app.subprocess, "Popen") as popen:
            ok, message = app.run(["target-python", "-m", "pip"])
        self.assertFalse(ok)
        self.assertIn("Cancelled", message)
        popen.assert_not_called()

    def test_cancel_stops_a_real_running_child(self):
        result = {}
        target = threading.Thread(
            target=lambda: result.update(value=app.run(
                [sys.executable, "-c", "import time; time.sleep(30)"], timeout=30)),
            daemon=True)
        target.start()
        deadline = time.time() + 10
        while time.time() < deadline:
            with app._ACTIVE_LOCK:
                if app._ACTIVE is not None:
                    break
            time.sleep(0.01)
        self.assertTrue(app.cancel_current(), "no child was registered to cancel")
        target.join(15)
        self.assertFalse(target.is_alive(), "run() did not return after the child was killed")
        self.assertFalse(result["value"][0])

    def test_update_all_stops_between_packages_when_cancelled(self):
        def responses(cmd, timeout=180):
            if "list" in cmd:
                return True, "first==1\nsecond==1\nthird==1\n"
            app._CANCEL.set()  # user presses Stop during the first upgrade
            return False, "Cancelled."
        with patch.object(app, "ensure_essentials", return_value=True), \
             patch.object(app, "check_dependencies", return_value=True), \
             patch.object(app, "run", side_effect=responses) as run:
            self.assertFalse(app.update_all(log=lambda _: None))
        self.assertEqual(run.call_count, 2, "packages after the cancel must not start")

    def test_cancel_is_idempotent_without_a_child(self):
        self.assertFalse(app.cancel_current())
        self.assertTrue(app._CANCEL.is_set())

    # --- interpreter selection -------------------------------------------

    def test_interpreter_probe_requires_supported_python(self):
        for output in ("not json", json.dumps({"supported": False})):
            with patch.object(app, "run", return_value=(True, output)):
                self.assertFalse(app.select_python("other-python"))
                self.assertEqual(app.PYS, "target-python")

    def test_interpreter_selection_resets_pip_cache(self):
        info = {"supported": True, "path": "chosen-python", "version": "3.12.0",
                "managed": False, "writable": True}
        app._PIPVER = "stale"
        with patch.object(app, "run", return_value=(True, json.dumps(info))):
            self.assertTrue(app.select_python("other-python"))
        self.assertEqual(app.PYS, "chosen-python")
        self.assertIsNone(app._PIPVER)
        self.assertEqual(app.py_ver(), "3.12.0")

    def test_interpreter_probe_never_writes_to_site_packages(self):
        # A blocking write here can hang discovery on machines whose security
        # software intercepts writes to site-packages.
        with patch.object(app, "run", return_value=(True, "ignored")) as run:
            app.select_python("other-python")
        probe = run.call_args[0][0][2]
        self.assertIn("os.access(site,os.W_OK)", probe)
        self.assertNotIn("tempfile", probe)
        self.assertNotIn("NamedTemporaryFile", probe)

    def test_frozen_application_cannot_select_itself_as_python(self):
        app.FROZEN = True
        with patch.object(app, "run") as run:
            self.assertFalse(app.select_python(app.sys.executable))
            run.assert_not_called()

    def test_discover_python_prefers_the_override_then_virtualenv(self):
        with patch.dict(app.os.environ, {"PYPACK_PYTHON": "override-python"}, clear=True), \
             patch.object(app, "select_python", side_effect=lambda p: p == "override-python") as select:
            self.assertTrue(app.discover_python())
        self.assertEqual(select.call_args_list[0][0][0], "override-python")

    # --- virtual environments --------------------------------------------

    def test_create_venv_refuses_a_non_empty_directory(self):
        (self.root / "occupied").mkdir()
        (self.root / "occupied" / "keep.txt").write_text("data")
        messages = []
        with patch.object(app, "run") as run:
            self.assertFalse(app.create_venv(self.root / "occupied", log=messages.append))
        run.assert_not_called()
        self.assertTrue(any("not empty" in m for m in messages))

    def test_create_venv_retargets_the_new_interpreter(self):
        target = self.root / "fresh"
        info = {"supported": True, "path": str(app.venv_python(target)),
                "version": "3.12.0", "managed": False, "writable": True}
        with patch.object(app, "run", return_value=(True, "")) as run, \
             patch.object(app, "select_python", return_value=True) as select:
            self.assertTrue(app.create_venv(target, log=lambda _: None))
        self.assertEqual(run.call_args[0][0][:3], ["target-python", "-m", "venv"])
        select.assert_called_once_with(str(app.venv_python(target)))

    def test_create_venv_reports_a_failed_creation(self):
        with patch.object(app, "run", return_value=(False, "venv module missing")), \
             patch.object(app, "select_python") as select:
            messages = []
            self.assertFalse(app.create_venv(self.root / "fresh", log=messages.append))
        select.assert_not_called()
        self.assertTrue(any("venv module missing" in m for m in messages))

    # --- search and caching ----------------------------------------------

    def test_search_prefers_exact_match_and_respects_limit(self):
        with patch.object(app, "_pypi_names", return_value=["pandas-extra", "pandas", "geopandas"]), \
             patch.object(app, "_pypi_popularity", return_value={"geopandas": 30, "pandas-extra": 20}), \
             patch.object(app, "run") as run:
            self.assertEqual(app.search_pypi("pandas", limit=2), ["pandas", "pandas-extra"])
            self.assertEqual(app.search_pypi("", limit=2), [])
            self.assertEqual(app.search_pypi("pandas", limit=0), [])
            run.assert_not_called()

    def test_typo_ranks_the_real_package_above_a_substring_squatter(self):
        names = ["easy-reqeusts", "requests", "grequests", "requests-toolbelt"]
        popular = {"requests": 1_200_000_000, "grequests": 299_400, "requests-toolbelt": 197_400_000}
        with patch.object(app, "_pypi_names", return_value=names), \
             patch.object(app, "_pypi_popularity", return_value=popular):
            self.assertEqual(app.search_pypi("reqeusts", limit=3)[0], "requests")
            self.assertEqual(app.search_pypi("reqeusts", limit=4)[-1], "easy-reqeusts")

    def test_transposition_finds_the_popular_package_not_the_obscure_namesake(self):
        names = ["NuMPI", "numpix", "numpiii", "numpy", "numpydoc"]
        popular = {"numpy": 801_552_837, "numpydoc": 1_300_000}
        with patch.object(app, "_pypi_names", return_value=names), \
             patch.object(app, "_pypi_popularity", return_value=popular):
            self.assertEqual(app.search_pypi("numpi", limit=3), ["numpy", "NuMPI", "numpix"])

    def test_exact_obscure_name_still_beats_its_popular_extensions(self):
        names = ["django-rest", "django-rest-knox", "django-rest-polymorphic"]
        popular = {"django-rest-knox": 750_400, "django-rest-polymorphic": 1_900_000}
        with patch.object(app, "_pypi_names", return_value=names), \
             patch.object(app, "_pypi_popularity", return_value=popular):
            self.assertEqual(app.search_pypi("django-rest", limit=3)[0], "django-rest")

    def test_popularity_lookup_uses_pep503_normalization(self):
        with patch.object(app, "_pypi_names", return_value=["python_dateutil"]), \
             patch.object(app, "_pypi_popularity", return_value={"python-dateutil": 90_000_000}):
            self.assertEqual(app.search_pypi("python_dateutil"), ["python_dateutil"])
            self.assertEqual(app.download_count("python_dateutil"), 90_000_000)

    def test_search_rejects_a_newline_query(self):
        with patch.object(app, "_pypi_names") as names:
            self.assertEqual(app.search_pypi("bad\nquery"), [])
        names.assert_not_called()

    def test_haystack_scan_matches_a_naive_scan_exactly(self):
        import random
        rng = random.Random(20261029)
        alphabet = "abcXY-_.012"
        names = list(dict.fromkeys(
            "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 9)))
            for _ in range(600)))
        for _ in range(80):
            query = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 4))).lower()
            with self.subTest(query=query):
                exact, starts, contains = app._scan_index(query, names)
                lowered = [n.lower() for n in names]
                self.assertEqual(exact, [n for n, low in zip(names, lowered) if low == query])
                self.assertEqual(starts, [n for n, low in zip(names, lowered)
                                          if low.startswith(query) and low != query])
                self.assertEqual(contains, [n for n, low in zip(names, lowered)
                                            if query in low and not low.startswith(query)])

    def test_index_is_read_once_per_process(self):
        self.cache.mkdir(parents=True)
        (self.cache / "pypi-index.txt").write_text("alpha\nbeta\n", encoding="utf-8")
        with patch.object(app, "_fetch_index_names") as fetch:
            first = app._pypi_names()
            second = app._pypi_names()
        fetch.assert_not_called()
        self.assertIs(first, second)
        self.assertEqual(first, ["alpha", "beta"])

    def test_stale_index_is_served_when_pypi_is_unreachable(self):
        self.cache.mkdir(parents=True)
        path = self.cache / "pypi-index.txt"
        path.write_text("alpha\nbeta\n", encoding="utf-8")
        old = time.time() - 20 * 86400
        import os
        os.utime(path, (old, old))
        with patch.object(app, "_fetch_index_names", side_effect=OSError("offline")):
            self.assertEqual(app._pypi_names(), ["alpha", "beta"])

    def test_fresh_legacy_cache_is_reused_without_downloading(self):
        legacy = self.root / "legacy"
        legacy.mkdir(parents=True)
        (legacy / ".pypack_pypi_index.txt").write_text("legacy-name\n", encoding="utf-8")
        with patch.object(app, "_fetch_index_names") as fetch:
            self.assertEqual(app._pypi_names(), ["legacy-name"])
        fetch.assert_not_called()

    def test_cache_write_is_atomic_and_leaves_no_partial_file(self):
        app._write_lines(self.cache / "index.txt", ["one", "two"])
        self.assertEqual((self.cache / "index.txt").read_text(encoding="utf-8"), "one\ntwo")
        self.assertEqual([p.name for p in self.cache.iterdir()], ["index.txt"])

    def test_failed_cache_write_preserves_the_previous_index(self):
        path = self.cache / "index.txt"
        app._write_lines(path, ["good"])
        with patch.object(app.Path, "replace", side_effect=OSError("disk full")):
            app._write_lines(path, ["truncated"])
        self.assertEqual(path.read_text(encoding="utf-8"), "good")
        self.assertEqual([p.name for p in self.cache.iterdir()], ["index.txt"])

    def test_popularity_keeps_counts_and_order(self):
        self.cache.mkdir(parents=True)
        (self.cache / "pypi-popular.txt").write_text("numpy\t801552837\nrequests\t1208056002\n",
                                                     encoding="utf-8")
        popular = app._pypi_popularity()
        self.assertEqual(list(popular), ["numpy", "requests"])
        self.assertEqual(popular["numpy"], 801552837)
        self.assertEqual(app.download_count("numpy"), 801552837)
        self.assertIsNone(app.download_count("obscure-package"))

    def test_legacy_popularity_without_counts_reports_unknown_not_zero(self):
        legacy = self.root / "legacy"
        legacy.mkdir(parents=True)
        (legacy / ".pypack_pypi_popular.txt").write_text("numpy\nrequests\n", encoding="utf-8")
        popular = app._pypi_popularity()
        self.assertEqual(list(popular), ["numpy", "requests"])
        self.assertIsNone(popular["numpy"])
        self.assertEqual(app.human_downloads(None), "—")

    def test_human_downloads_scales(self):
        self.assertEqual(app.human_downloads(1_208_056_002), "1.2B")
        self.assertEqual(app.human_downloads(801_552_837), "801.6M")
        self.assertEqual(app.human_downloads(299_400), "299.4k")
        self.assertEqual(app.human_downloads(12), "12")
        self.assertEqual(app.human_downloads(None), "—")

    def test_cache_location_follows_the_platform(self):
        with patch.object(app, "OS", "Windows"), \
             patch.dict(app.os.environ, {"LOCALAPPDATA": "C:/Users/x/AppData/Local"}):
            self.assertEqual(self.real_cache_dir(), Path("C:/Users/x/AppData/Local/PyPack/cache"))
        with patch.object(app, "OS", "Linux"), \
             patch.dict(app.os.environ, {"XDG_CACHE_HOME": "/tmp/xdg"}, clear=True):
            self.assertEqual(self.real_cache_dir(), Path("/tmp/xdg/pypack"))

    # --- logging and versioning ------------------------------------------

    def test_log_fn_records_to_the_durable_log(self):
        with patch.object(app, "_LOGGER") as logger:
            app.log_fn("installing example")
        logger.info.assert_called_once_with("installing example")

    def test_file_log_creates_a_rotating_log(self):
        import logging
        with patch.object(app, "_LOGGER", None):
            app._file_log("first line")
            app._file_log("second line")
            logger = logging.getLogger("pypack")
            for handler in list(logger.handlers):
                handler.close()
                logger.removeHandler(handler)
        text = (self.root / "logs" / "pypack.log").read_text(encoding="utf-8")
        self.assertIn("first line", text)
        self.assertIn("second line", text)

    def test_logs_stay_inside_the_application_directory(self):
        # cache_dir().parent is a shared folder on macOS and Linux, so logs must
        # not be derived from it.
        with patch.object(app, "OS", "Linux"), \
             patch.dict(app.os.environ, {"XDG_CACHE_HOME": "/tmp/xdg", "XDG_DATA_HOME": "/tmp/data"},
                        clear=True):
            self.assertEqual(self.real_cache_dir(), Path("/tmp/xdg/pypack"))
            self.assertEqual(self.real_log_dir(), Path("/tmp/data/pypack/logs"))
        with patch.object(app, "OS", "Darwin"), \
             patch.object(app.Path, "home", return_value=Path("/Users/x")):
            self.assertEqual(self.real_log_dir(), Path("/Users/x/Library/Application Support/PyPack/logs"))
            self.assertEqual(self.real_cache_dir(), Path("/Users/x/Library/Caches/PyPack"))

    def test_file_log_survives_an_unwritable_location(self):
        with patch.object(app, "_LOGGER", None), \
             patch.object(app, "log_dir", side_effect=OSError("no storage")):
            app._file_log("message")  # must not raise
            self.assertIs(app._LOGGER, False)

    def test_version_and_user_agent_come_from_the_version_file(self):
        with patch.object(app, "resource_path", return_value=self.root / "VERSION"):
            (self.root / "VERSION").write_text("9.9.9\n", encoding="utf-8")
            self.assertEqual(app.app_version(), "9.9.9")
            self.assertEqual(app.user_agent(), "PyPack/9.9.9")

    def test_missing_version_file_does_not_crash(self):
        with patch.object(app, "resource_path", return_value=self.root / "absent"):
            self.assertEqual(app.app_version(), "0.0.0")


    # --- pip output parsing ----------------------------------------------

    def test_pip_version_is_parsed_and_cached(self):
        with patch.object(app, "run", return_value=(True, "pip 24.0 from /x (python 3.12)")) as run:
            self.assertEqual(app.pip_ver(), "24.0")
            self.assertEqual(app.pip_ver(), "24.0")
        self.assertEqual(run.call_count, 1, "each log line must not spawn a subprocess")
        with patch.object(app, "run", return_value=(True, "pip 25.0 from /x")):
            self.assertEqual(app.pip_ver(refresh=True), "25.0")

    def test_missing_pip_is_reported_as_missing(self):
        with patch.object(app, "run", return_value=(False, "")):
            self.assertEqual(app.pip_ver(), "missing")

    def test_installed_set_lowercases_names(self):
        with patch.object(app, "run", return_value=(True, "NumPy==2.0\nFlask==3.0\n")):
            self.assertEqual(app.installed_set(), {"numpy", "flask"})
        with patch.object(app, "run", return_value=(False, "")):
            self.assertEqual(app.installed_set(), set())

    def test_installed_ver_reads_pip_show(self):
        with patch.object(app, "run", return_value=(True, "Name: numpy\nVersion: 2.1.0\n")):
            self.assertEqual(app.installed_ver("numpy"), "2.1.0")
        with patch.object(app, "run", return_value=(False, "")):
            self.assertIsNone(app.installed_ver("numpy"))

    def test_index_fetch_decodes_gzip_and_plain_json(self):
        import gzip as gzip_module
        payload = json.dumps({"projects": [{"name": "alpha"}, {"name": "beta"}]}).encode("utf-8")
        for body, headers in ((payload, {}),
                              (gzip_module.compress(payload), {"Content-Encoding": "gzip"})):
            response = MagicMock()
            response.read.return_value = body
            response.headers = headers
            response.__enter__.return_value = response
            with self.subTest(encoding=headers), patch.object(app.urlreq, "urlopen", return_value=response):
                self.assertEqual(app._fetch_index_names(), ["alpha", "beta"])

    def test_buckets_group_by_prefix_and_are_reused(self):
        names = ["numpy", "numpix", "flask"]
        buckets = app._buckets(names)
        self.assertEqual(buckets["nu"], ["numpy", "numpix"])
        self.assertEqual(buckets["fl"], ["flask"])
        self.assertIs(app._buckets(names), buckets)

    def test_fuzzy_ignores_queries_too_short_to_guess(self):
        self.assertEqual(app._fuzzy("np", ["numpy"], 5), [])
        self.assertEqual(app._fuzzy("numpi", [], 5), [])
        self.assertEqual(app._fuzzy("numpi", ["numpy"], 5), ["numpy"])

    def test_get_python_opens_the_platform_download_page(self):
        with patch.object(app, "OS", "Darwin"), patch.object(app.webbrowser, "open") as opened:
            self.assertEqual(self.dispatch("get-python")[0], 0)
        opened.assert_called_once_with("https://www.python.org/downloads/macos/")

    def test_prepare_python_without_a_gui_explains_the_override(self):
        with patch.object(app, "discover_python", return_value=False), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertFalse(app.prepare_python(False))
        self.assertIn("PYPACK_PYTHON", output.getvalue())


if __name__ == "__main__":
    unittest.main()