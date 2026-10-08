# PyPack

![PyPack](assets/pypack-logo.png)

A compact Python package manager. Search PyPI, check one or several packages, then install them. Fix pip or update installed packages from the same window.

## Download

Choose **one** file for your operating system from the [latest release](https://github.com/4Raisan/PyPack/releases/latest):

| Platform | Download | Start |
| --- | --- | --- |
| Windows 64-bit | [PyPack.exe](https://github.com/4Raisan/PyPack/releases/latest/download/PyPack.exe) | Double-click the executable. |
| Linux or macOS | [PyPack.sh](https://github.com/4Raisan/PyPack/releases/latest/download/PyPack.sh) | Run `sh PyPack.sh` in a terminal. |
| macOS alternative | [PyPack.command](https://github.com/4Raisan/PyPack/releases/latest/download/PyPack.command) | Run `chmod +x PyPack.command`, then double-click. |

Each launcher contains the application and square icon. You do not need the source files, a separate logo, or the `.bat`/`.ps1` helpers to use these downloads. Windows bundles its Python-install helper and Tcl/Tk GUI runtime; it detects Python 3.10+ or offers to install/select it when missing. Linux/macOS require Python 3.10+ and tkinter.

## Use

1. Open PyPack and search for a package, such as `panda`.
2. Check the packages you want to install.
3. Click **Install** and follow the Activity log.

The window starts compact; the empty results table takes no space. Activity shows the exact Python interpreter receiving packages. Opening PyPack does not upgrade packages automatically.

Activate a virtual environment before launching to use it. OS-managed Python requires a virtual environment. You can also set `PYPACK_PYTHON` to a Python executable path. Package installs/upgrades can change that environment's dependencies.

## Run from source

Keep `pypack_setup.py`, `PyPack-Setup.bat`, `pypack-bootstrap.ps1`, `pypack-setup.sh`, and `assets` together. On Windows, double-click `PyPack-Setup.bat`; on Linux/macOS, run `sh pypack-setup.sh`.

```text
python pypack_setup.py --gui
python pypack_setup.py search panda
python pypack_setup.py install pandas pillow
python pypack_setup.py fix
python pypack_setup.py update-all
```

Exit codes: 0 success, 1 operational failure, 2 invalid arguments. `--gui --update` explicitly upgrades packages at startup.

## Verification

The Windows executable passed an isolated runtime check with its bundled icon, Tcl/Tk, and bootstrap helper. GUI checks covered the compact layout, result checkboxes, and simulated single/multiple installs. Both Unix launchers passed syntax, argument forwarding, and exit-code checks under WSL.

Native Linux/macOS desktop checks and clean-machine Python installation remain unverified. The Windows executable is unsigned. Script files use the operating system's default file icon; the running app displays the PyPack logo.

Built with [PyInstaller](https://pyinstaller.org/en/stable/usage.html). [MIT licensed](LICENSE).
