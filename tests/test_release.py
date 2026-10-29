"""Verify generated payloads and real Unix argument/exit-code forwarding."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_release", ROOT / "scripts/build_release.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pypack-build-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / "downloads"
        self.output.mkdir()

    def test_source_zip_excludes_generated_outputs_and_preserves_source(self):
        build.build_source(self.output, allow_dirty=True)
        version = (ROOT / "VERSION").read_text().strip()
        with zipfile.ZipFile(self.output / f"PyPack-{version}-source.zip") as archive:
            names = archive.namelist()
            prefix = f"PyPack-{version}-source/"
            self.assertEqual(archive.read(prefix + "pypack_setup.py"), (ROOT / "pypack_setup.py").read_bytes())
            self.assertFalse(any(name.startswith(prefix + folder) for name in names
                                 for folder in ("releases/", "dist/", "build/", ".git/")))

    def test_extracted_source_rebuilds_without_git_metadata(self):
        build.build_source(self.output, allow_dirty=True)
        version = (ROOT / "VERSION").read_text().strip()
        with zipfile.ZipFile(self.output / f"PyPack-{version}-source.zip") as archive:
            archive.extractall(self.root / "extracted")
        extracted = self.root / "extracted" / f"PyPack-{version}-source"
        rebuilt = self.root / "rebuilt"
        rebuilt.mkdir()
        with patch.object(build, "ROOT", extracted), patch.object(build.subprocess, "run") as run:
            build.build_unix(rebuilt)
            build.build_source(rebuilt, allow_dirty=True)
            build.write_checksums(rebuilt)
            run.assert_not_called()
        self.assertEqual((rebuilt / "PyPack.sh").read_bytes(), self._launcher_bytes())
        self.assertEqual((rebuilt / f"PyPack-{version}-source.zip").read_bytes(),
                         (self.output / f"PyPack-{version}-source.zip").read_bytes())

    def _launcher_bytes(self):
        build.build_unix(self.output)
        return (self.output / "PyPack.sh").read_bytes()

    def test_source_manifest_cannot_read_outside_the_project(self):
        fixture = self.root / "source"
        fixture.mkdir()
        (fixture / "VERSION").write_text("0.1.1")
        (fixture / "SOURCE_FILES.json").write_text(json.dumps(["../private.txt"]))
        (self.root / "private.txt").write_text("private")
        with patch.object(build, "ROOT", fixture), self.assertRaisesRegex(ValueError, "escapes"):
            build.build_source(self.output, allow_dirty=True)

    def _git_fixture(self):
        fixture = self.root / "repo"
        fixture.mkdir()
        (fixture / "VERSION").write_text("0.1.1")
        (fixture / "LICENSE").write_text("MIT")
        (fixture / ".git").mkdir()
        return fixture

    def test_dirty_working_tree_is_refused_so_the_zip_matches_the_release_commit(self):
        fixture = self._git_fixture()
        dirty = subprocess.CompletedProcess([], 0, stdout=" M pypack_setup.py\n", stderr="")
        with patch.object(build, "ROOT", fixture), \
             patch.object(build.subprocess, "run", return_value=dirty) as run:
            with self.assertRaisesRegex(RuntimeError, "uncommitted changes"):
                build.build_source(self.output)
        self.assertEqual(run.call_args[0][0], ["git", "status", "--porcelain"])

    def test_allow_dirty_skips_the_cleanliness_check(self):
        fixture = self._git_fixture()
        listing = subprocess.CompletedProcess([], 0, stdout=b"LICENSE\0VERSION\0", stderr=b"")
        with patch.object(build, "ROOT", fixture), \
             patch.object(build.subprocess, "run", return_value=listing) as run:
            build.build_source(self.output, allow_dirty=True)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args[0][0], ["git", "ls-files", "-z"])

    def test_stale_source_zips_are_removed_so_dist_only_ships_this_version(self):
        version = (build.ROOT / "VERSION").read_text().strip()
        (self.output / "PyPack-0.0.1-source.zip").write_bytes(b"old")
        build.build_source(self.output, allow_dirty=True)
        names = [p.name for p in self.output.glob("PyPack-*-source.zip")]
        self.assertEqual(names, [f"PyPack-{version}-source.zip"])

    def test_checksums_use_lf_so_the_linux_publish_job_can_verify_them(self):
        (self.output / "PyPack.sh").write_bytes(b"#!/bin/sh\necho hi\n")
        build.write_checksums(self.output)
        data = (self.output / "SHA256SUMS.txt").read_bytes()
        self.assertNotIn(b"\r", data, "a trailing CR becomes part of the filename on Linux")
        self.assertTrue(data.endswith(b"\n"))
        self.assertIn(b"  PyPack.sh\n", data)

    def test_generated_launcher_guards_the_gui_but_not_the_cli(self):
        build.build_unix(self.output)
        text = (self.output / "PyPack.sh").read_text()
        self.assertIn("require_tk() {", text)
        self.assertIn('require_tk "${1:-}" || exit 1', text)
        self.assertIn("python3-tk", text)

    @unittest.skipIf(os.name == "nt" or not shutil.which("sh"), "Requires a native POSIX shell")
    def test_launchers_preserve_arguments_exit_codes_and_cleanup(self):
        fixture = self.root / "source with spaces"
        (fixture / "assets").mkdir(parents=True)
        (fixture / "pypack-setup.sh").write_bytes((ROOT / "pypack-setup.sh").read_bytes())
        (fixture / "LICENSE").write_bytes((ROOT / "LICENSE").read_bytes())
        (fixture / "VERSION").write_bytes((ROOT / "VERSION").read_bytes())
        (fixture / "assets/pypack-icon.png").write_bytes((ROOT / "assets/pypack-icon.png").read_bytes())
        probe = '''import json, pathlib, sys
pathlib.Path(sys.argv[1]).write_text(json.dumps({"args":sys.argv[2:], "path":__file__}))
sys.exit(7)
'''
        (fixture / "pypack_setup.py").write_text(probe)
        with patch.object(build, "ROOT", fixture):
            build.build_unix(self.output)
        for name in ("PyPack.sh", "PyPack.command"):
            with self.subTest(name=name):
                launcher = self.output / name
                subprocess.run(["sh", "-n", str(launcher)], check=True)
                report = self.root / "argument report.json"
                result = subprocess.run(["sh", str(launcher), str(report), "two words", "a;$(echo nope)", "--flag"],
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 7, result.stderr)
                received = json.loads(report.read_text())
                self.assertEqual(received["args"], ["two words", "a;$(echo nope)", "--flag"])
                self.assertFalse(Path(received["path"]).parent.exists(), "Temporary payload was not removed")

    def test_generated_payload_contains_exact_source_icon_and_license(self):
        build.build_unix(self.output)
        text = (self.output / "PyPack.sh").read_text()
        payload = text.split("<<'PYPACK_ASSETS'\n", 1)[1].rsplit("PYPACK_ASSETS\n", 1)[0]
        # Execute only the extraction portion; never start the GUI or install packages.
        extraction = payload.split('    args = sys.argv[1:] or ["--gui"]', 1)[0]
        extraction += '\n    extracted = {name: (root / name).read_bytes() for name in ("pypack_setup.py", "assets/pypack-icon.png", "LICENSE", "VERSION")}\n'
        namespace = {"__name__": "test_payload"}
        # The extraction registers signal handlers; restore the process handlers afterward.
        import signal
        with patch.object(signal, "signal"):
            exec(extraction, namespace)
        for name, value in namespace["extracted"].items():
            self.assertEqual(value, (ROOT / name).read_bytes())
        self.assertEqual((self.output / "PyPack.sh").read_bytes(), (self.output / "PyPack.command").read_bytes())


if __name__ == "__main__":
    unittest.main()
