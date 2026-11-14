# Installation

There are three ways to run Heaven3D: a **standalone edition** (nothing to install), **from source** (Python 3.10+), or **with pip**.

## Option 1 — Standalone editions (recommended for most people)

Unzip and double-click. Python and every library are inside the folder, so there is nothing else to install. You can even run it from a USB stick.

| Download | Contents | Start |
| --- | --- | --- |
| `Heaven3D-<version>-Windows.zip` | Hub, editor and a bundled Windows Python | double-click **Heaven3D Hub.exe** |
| `Heaven3D-<version>-Linux.zip` | Hub, editor and a bundled Linux Python (glibc 2.17+) | run **./Heaven3D-Hub** (`./install-desktop-shortcuts.sh` adds menu entries) |
| `Heaven3D-<version>-Content-Packs.zip` | the asset packages (characters, islands, weapons) | unzip next to the edition |
| `Heaven3D-<version>-macOS-AppleSilicon.zip` / `-macOS-Intel.zip` | the same for Macs, when a release includes them | unlock once (below), then double-click **Heaven3D Hub.app** |
| `Heaven3D-<version>-Export-Players.zip` | Add-on for the macOS editions: the Windows and Linux game players | move its `windows` and `linux` folders into `runtime/players` |

Take them from the newest release on the [Releases page](https://github.com/lolgg313/Heaven3D/releases); not every release has every edition. Changes made after a release (see the [[Changelog]]) are in the source: use Option 2 or 3 for those.

- The **Windows** and **Linux** editions each contain both game players, so either one can export Windows **and** Linux games.
- The **macOS** editions export macOS games. Add the *Export-Players* download to export Windows and Linux games from a Mac too.

### macOS: first start

Heaven3D isn't from the App Store, so macOS blocks it until you unlock it once:

- right-click **Fix macOS security warning.command** in the Heaven3D folder and choose **Open**, **or**
- run `xattr -dr com.apple.quarantine <Heaven3D folder>` in Terminal.

On a trackpad, right-click is a two-finger click (or Control-click).

## Option 2 — Running from source

### Requirements

- **Python 3.10+**
- A GPU and driver supporting **OpenGL 3.3 Core** or newer (macOS uses its built-in OpenGL 4.1 core profile)
- `tkinter` for the Hub (included with Python on Windows and macOS; `sudo apt install python3-tk` on Debian/Ubuntu)

### Get the code

```bash
git clone https://github.com/lolgg313/Heaven3D.git
cd Heaven3D
```

### The easy way: `install.py`

```bash
python install.py
```

The installer:

1. checks your Python version (3.10+),
2. on Linux, installs OS packages with your distro's package manager (apt / dnf / pacman / zypper): Mesa OpenGL, tkinter, venv support and the SDL/X11 libraries pygame needs,
3. creates an isolated virtual environment, `.venv-heaven3d`, so nothing collides with the system Python (no PEP 668 "externally-managed-environment" errors),
4. installs a **pinned, mutually compatible** set of packages,
5. verifies every import and checks for a working OpenGL 3.3+ context, then prints the exact launch command.

It is safe to re-run. Tested on Ubuntu 20.04+, Debian 11+, Fedora 38+, Arch, openSUSE, Mint, Pop!_OS and Raspberry Pi OS (desktop).

### The manual way

```bash
pip install moderngl pygame pyglm numpy numba zstandard
```

| Package | Why |
| --- | --- |
| `moderngl` | OpenGL Core access (renderer) |
| `pygame` | window, input, audio |
| `pyglm` | vector / matrix math (`import glm`) |
| `numpy` | mesh data, terrain, physics arrays |
| `numba` | optional JIT acceleration of hot paths (falls back to plain Python, bit-identical) |
| `zstandard` | reads compressed `.blend` files from Blender 3.0+ (not needed on Python 3.14+) |

Optional extras:

```bash
pip install trimesh    # .obj import
pip install DracoPy    # Draco-compressed glTF / GLB
```

## Verify the install

```bash
python Heaven3D.py --version        # prints the engine version
python Heaven3D_Hub.py --version    # prints the Hub version
python Heaven3D_Hub.py              # opens the Hub
```

On Windows you can also double-click `Heaven3D_Hub.pyw` (no console window).

Next: [[Getting Started]].

## Option 3 — pip

```bash
pip install git+https://github.com/lolgg313/Heaven3D
```

This installs Heaven3D with the libraries it needs and three commands:

| Command | Starts |
| --- | --- |
| `heaven3d` | the editor (same options as `python Heaven3D.py`) |
| `heaven3d-hub` | the Hub |
| `heaven3d-runtime --project game.heaven3d` | a project without the editor |

```python
import heaven3d
engine = heaven3d.HeavenEngine(mode="runtime", project_path="game.heaven3d")
engine.run()
```

- It is the same engine, editor, templates and packages as the repository, kept together inside site-packages (`heaven3d.engine_dir()` tells you where).
- `HEAVEN3D_PIP_LITE=1 pip install git+https://github.com/lolgg313/Heaven3D` leaves the asset packages (about 350 MB) out.
- Update with `pip install --upgrade --force-reinstall git+https://github.com/lolgg313/Heaven3D`.
- The Hub and the file dialogs need tkinter, which pip can't install (`sudo apt install python3-tk` on Debian / Ubuntu). A virtual environment keeps Heaven3D's libraries apart from your other projects.
