# Development and releases

Use Python 3.10 or newer. The application and offline tests use the standard library; tkinter is needed for the GUI.

```sh
python -m unittest discover -s tests -v
python scripts/build_release.py --allow-dirty
```

The builder writes self-contained `PyPack.sh` and `PyPack.command`, a source ZIP and `SHA256SUMS.txt` to ignored `dist/`. Both Unix launchers embed the current application, icon, `VERSION` and MIT license. They forward arguments without shell evaluation, return the application's exit status, check for tkinter before opening the GUI, and remove their temporary files on exit. Their interpreter discovery follows `pypack-setup.sh`.

`build_source` refuses a dirty working tree, because `git ls-files` reads the index and the ZIP would otherwise differ from the commit the release points at. Use `--allow-dirty` for local iteration; commit before building anything you intend to publish.

The source ZIP includes `SOURCE_FILES.json`, so an extracted source download can run the same tests and builder without Git or a `.git` directory. Keep that manifest with the extracted sources. A Git checkout uses its tracked file list instead.

The GUI tests need a display. They skip automatically without one; on headless Linux run them under `xvfb-run -a`, which is what CI does.

On Windows, install the build tools into a dedicated environment:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-build.txt
.venv\Scripts\python scripts/build_release.py --windows
$env:PYPACK_PYTHON = (Get-Command python).Source
$process = Start-Process -FilePath "$PWD\dist\PyPack.exe" -ArgumentList '--self-test', 'build/runtime-check.json' -WindowStyle Hidden -Wait -PassThru
if ($process.ExitCode -ne 0) { throw 'Packaged runtime check failed' }
Get-Content build/runtime-check.json
```

PyInstaller bundles the icon, Python-install helper, `VERSION`, MIT license and Tcl/Tk runtime. This builds an unsigned Windows executable. `--self-test` checks the GUI runtime, image loading, selected external Python and pip availability; it does not install or upgrade packages.

## Publish a release

1. Update `VERSION` and add `docs/releases/VERSION.md`. Commit the source, tests and build configuration before building; the source ZIP includes tracked files only.
2. Push to `main` and wait for **Check PyPack** to pass on Windows, Linux and macOS with Python 3.10 and 3.14.
3. Run **Build PyPack downloads** from GitHub Actions on `main`. It builds on Windows, checks the packaged runtime, attests build provenance for every asset, verifies the Unix launchers on Linux, and checks all download hashes.
4. Inspect the resulting draft, its source commit and workflow results, then publish it. The workflow refuses to reuse an existing release version.

Downloads belong in GitHub Releases. Keep `dist/`, `build/` and `releases/` out of source commits. The older v0.1.0 files remain downloadable from their existing release; their historical Git objects are retained. Removing files from the current tree does not rewrite history.

Automated checks now cover the GUI on all three platforms, including Linux under a virtual display. A native desktop session, real package installation, and clean-machine Python installation still require manual validation. Dependabot proposes weekly updates for GitHub Actions and the pinned build dependency; review its pull requests rather than letting action tags drift unattended.
