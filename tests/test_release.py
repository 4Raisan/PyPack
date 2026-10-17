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
        build.build_source(self.output)
        version = (ROOT / "VERSION").read_text().strip()
        with zipfile.ZipFile(self.output / f"PyPack-{version}-source.zip") as archive:
            names = archive.namelist()
            prefix = f"PyPack-{version}-source/"
            self.assertEqual(archive.read(prefix + "pypack_setup.py"), (ROOT / "pypack_setup.py").read_bytes())
            self.assertFalse(any(name.startswith(prefix + folder) for name in names
                                 for folder in ("releases/", "dist/", "build/", ".git/")))

    @unittest.skipIf(os.name == "nt" or not shutil.which("sh"), "Requires a native POSIX shell")
    def test_launchers_preserve_arguments_exit_codes_and_cleanup(self):
        fixture = self.root / "source with spaces"
        (fixture / "assets").mkdir(parents=True)
        (fixture / "pypack-setup.sh").write_bytes((ROOT / "pypack-setup.sh").read_bytes())
        (fixture / "LICENSE").write_bytes((ROOT / "LICENSE").read_bytes())
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
        extraction += '\n    extracted = {name: (root / name).read_bytes() for name in ("pypack_setup.py", "assets/pypack-icon.png", "LICENSE")}\n'
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
