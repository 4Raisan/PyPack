# PyPack

## For users

- Windows: open **PyPack.exe**. The GUI and square logo are bundled. Python 3.10+ is detected automatically; if missing, PyPack offers to install it or select an existing interpreter.
- Linux/macOS: run **sh PyPack.sh**. This is a single-file launcher containing the application and icon. Requires Python 3.10+ and tkinter. On macOS, **PyPack.command** can also launch it after `chmod +x PyPack.command`.

The ready-to-use files are in `releases`: `PyPack.exe` for Windows, `PyPack.sh` for Linux/macOS, and `PyPack.command` for macOS.

Search for a package, check one or several results, then click Install. The window starts compact. Activity shows progress and the exact Python path receiving packages. Startup does not install or upgrade packages automatically.

Activate your virtual environment before launching PyPack to use it. OS-managed Python must use a virtual environment. Advanced users can set `PYPACK_PYTHON` to a Python executable path.

## Source launchers

Keep `pypack_setup.py`, `PyPack-Setup.bat`, `pypack-bootstrap.ps1`, `pypack-setup.sh`, and `assets` together. Double-click the batch launcher on Windows, or run `sh pypack-setup.sh` on Linux/macOS.

```text
python pypack_setup.py --gui
python pypack_setup.py search panda
python pypack_setup.py install pandas pillow
python pypack_setup.py fix
python pypack_setup.py update-all
```

Exit codes: 0 success, 1 operational failure, 2 invalid arguments. `--gui --update` explicitly upgrades packages on startup. Installing/upgrading packages can change the selected environment's dependencies.

## Release notes

The Windows build is a 64-bit executable with bundled Tcl/Tk. Package operations use an external Python interpreter, never the executable itself. The executable is unsigned. The Unix shell launcher has been checked under WSL; native Linux/macOS GUI and clean-machine Python installation still need release testing.

Square PNG and multi-size Windows ICO assets are in `assets`; the original wordmark is retained. The license is MIT.

Built with [PyInstaller](https://pyinstaller.org/en/stable/usage.html). No releases have been committed or pushed automatically.

