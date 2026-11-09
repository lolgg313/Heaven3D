#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
  Heaven3D — install.py   (Linux, Windows and macOS)
================================================================================

  One command:

      python3 install.py        (Windows: py install.py)

  …and a user goes from a fresh OS to a running Heaven3D engine with
  ZERO manual steps.  On Windows and macOS step 2 is skipped (python.org
  Python already has tkinter + venv, OpenGL comes with the driver) and the
  launcher is run_heaven3d.bat on Windows.  The script:

    1. Verifies the Python version (3.10+).
    2. Installs OS-level packages with the distro's own package manager
       (apt / dnf / pacman / zypper) — OpenGL (Mesa), tkinter, venv support,
       SDL/X11 libraries pygame needs.
    3. Creates an isolated virtual environment (.venv-heaven3d) so pip
       packages can NEVER collide with the system Python or hit
       "externally-managed-environment" (PEP 668) errors on Debian 12+ /
       Ubuntu 23.04+ / Fedora.
    4. Installs a PINNED, mutually-compatible set of Python packages
       (the pins are chosen so numba / numpy / moderngl / pygame / pyglm /
       trimesh / Pillow / zstandard always agree with each other).
    5. Verifies every import the engine performs at startup, checks for a
       working OpenGL 3.3+ context, and prints an exact launch command.

  Safe to re-run: every step is idempotent.

  Tested target distros: Ubuntu 20.04+, Debian 11+, Fedora 38+,
  Arch, openSUSE, Mint, Pop!_OS, Raspberry Pi OS (desktop).
================================================================================
"""

import os
import platform
import shutil
import subprocess
import sys
import venv

# ----------------------------------------------------------------------------
# Pinned package set — DO NOT loosen these without re-testing the matrix.
# The critical pairs are:
#   numba 0.61.x  <->  numpy 2.2.x   (numba is strict about numpy versions)
#   moderngl 5.12 <->  pyglm 2.8.x   (matrix upload path used by the engine)
#   trimesh 4.x   <->  numpy 2.2.x   (geometry importer)
#   pygame 2.5.x  bundles SDL2 — no system SDL2 build needed.
# ----------------------------------------------------------------------------
PIP_PACKAGES = [
    ("numpy",               "numpy==2.2.6"),
    ("pyglm",               "pyglm==2.8.2"),
    ("moderngl",            "moderngl==5.12.0"),
    ("pygame",              "pygame==2.6.1"),
    ("numba",               "numba==0.61.2"),
    ("trimesh",             "trimesh==4.6.8"),
    ("Pillow",              "Pillow==11.3.0"),
    # 2.7: Blender 3.0+ "Compress"ed .blend files are zstd streams
    ("zstandard",           "zstandard==0.25.0"),
]

VENV_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        ".venv-heaven3d")
IS_WIN = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"

# A Windows console or a redirected log may not be UTF-8: never crash on ✓ / …
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

# ----------------------------------------------------------------------------
# small helpers
# ----------------------------------------------------------------------------

class Step:
    """Coloured, silent-failure-proof console output."""
    GREEN, YELLOW, RED, BLUE, RESET = ("", "", "", "", "")
    # (the classic Windows console prints ANSI codes as garbage)
    if sys.stdout.isatty() and (not IS_WIN or os.environ.get("WT_SESSION")):
        GREEN, YELLOW, RED, BLUE, RESET = (
            "\033[92m", "\033[93m", "\033[91m", "\033[94m", "\033[0m")

    @classmethod
    def info(cls, msg):    print(f"{cls.BLUE}:: {msg}{cls.RESET}", flush=True)
    @classmethod
    def ok(cls, msg):      print(f"{cls.GREEN}✓ {msg}{cls.RESET}", flush=True)
    @classmethod
    def warn(cls, msg):    print(f"{cls.YELLOW}! {msg}{cls.RESET}", flush=True)
    @classmethod
    def fail(cls, msg):    print(f"{cls.RED}✗ {msg}{cls.RESET}", flush=True)
    @classmethod
    def die(cls, msg):
        cls.fail(msg)
        print(f"\n{cls.RED}Installation aborted.{cls.RESET}")
        sys.exit(1)


def have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def run(cmd, *, as_root=False, capture=False, check=True, env=None):
    """Run a command, optionally through sudo.  Never raises unless asked."""
    full = list(cmd)
    if as_root and os.geteuid() != 0:
        if not have("sudo"):
            Step.die("This step needs root privileges but 'sudo' is not "
                     "installed.\n  Re-run as root, or install sudo.")
        # In a terminal, let sudo ask for the password (with -n it failed at
        # once for every normal desktop user, so nothing was ever installed).
        # Without a terminal, -n fails fast instead of hanging.
        interactive = sys.stdin is not None and sys.stdin.isatty()
        full = ["sudo"] + ([] if interactive else ["-n"]) + full
    if capture:
        # read child output as UTF-8 (and have Python children write it):
        # a Windows pipe would otherwise use the ANSI code page
        env = dict(env or os.environ, PYTHONIOENCODING="utf-8")
    try:
        return subprocess.run(full, check=check, capture_output=capture,
                              text=True, env=env,
                              **({"encoding": "utf-8", "errors": "replace"} if capture else {}))
    except subprocess.CalledProcessError as ex:
        if check:
            out = (ex.stderr or ex.stdout or "").strip()
            Step.fail(f"command failed: {' '.join(full)}\n{out[:800]}")
            raise
        return ex


def which_pkg_manager():
    for pm, _ in (("apt-get", "deb"), ("dnf", "rpm"), ("pacman", "arch"),
                  ("zypper", "rpm")):
        if have(pm):
            return pm
    return None


# ----------------------------------------------------------------------------
# 1. Python version gate
# ----------------------------------------------------------------------------

def check_python():
    Step.info("Checking Python version …")
    if sys.version_info < (3, 10):
        Step.die(f"Heaven3D needs Python 3.10 or newer — you have "
                 f"{platform.python_version()}.\n"
                 f"  Install a newer python3 (e.g. 'sudo apt install "
                 f"python3.12') and re-run this script with it.")
    Step.ok(f"Python {platform.python_version()}")


# ----------------------------------------------------------------------------
# 2. System packages (OpenGL / tkinter / SDL / build helpers)
# ----------------------------------------------------------------------------

def install_system_packages():
    pm = which_pkg_manager()
    if pm is None:
        Step.warn("No supported package manager found — skipping system "
                  "packages.\n  Make sure OpenGL (Mesa) and tkinter are "
                  "installed or the engine will not open a window.")
        return

    jobs = {
        "apt-get": {
            "update": [["apt-get", "update", "-qq"]],
            "install": [
                "apt-get", "install", "-y", "--no-install-recommends",
                # OpenGL + windowing stack used by moderngl/pygame
                "libgl1", "libegl1", "libgles2",
                "mesa-utils",                     # glxinfo → GL verification
                "libxkbcommon0", "libdbus-1-3",
                # tkinter for FILEBURNER.py
                "python3-tk",
                # toolchain for any source fallback builds
                "python3-dev", "build-essential",
            ],
        },
        "dnf": {
            "update": [],
            "install": [
                "dnf", "install", "-y",
                "mesa-libGL", "mesa-libEGL", "mesa-libGLES",
                "mesa-demos",                     # glxinfo
                "libxkbcommon", "dbus-libs",
                "python3-tkinter",
                "python3-devel", "gcc", "gcc-c++", "make",
            ],
        },
        "pacman": {
            "update": [],
            "install": [
                "pacman", "-S", "--needed", "--noconfirm",
                "mesa", "mesa-utils",
                "libxkbcommon", "dbus",
                "tk",                             # python-tk equivalent
                "base-devel", "python-pip",
            ],
        },
        "zypper": {
            "update": [["zypper", "refresh"]],
            "install": [
                "zypper", "install", "-y",
                "Mesa-libGL1", "Mesa-libEGL1", "Mesa-libGLESv2-2",
                "Mesa-demo-x",                    # glxinfo
                "libxkbcommon0", "dbus-1",
                "python3-tk",
                "python3-devel", "gcc", "gcc-c++", "make",
            ],
        },
    }[pm]

    Step.info(f"Installing system packages via {pm} …")
    for cmd in jobs["update"]:
        try:
            run(cmd, as_root=True, check=True)
        except subprocess.CalledProcessError:
            Step.warn(f"'{cmd[0]} update' failed — continuing anyway "
                      f"(cached indexes may be enough).")

    # Ensure the OS python3-venv / ensurepip bits exist (Debian splits it).
    if pm == "apt-get":
        try:
            run(["apt-get", "install", "-y", "--no-install-recommends",
                 f"python3.{sys.version_info.minor}-venv"],
                as_root=True, check=True)
        except subprocess.CalledProcessError:
            try:
                run(["apt-get", "install", "-y", "--no-install-recommends",
                     "python3-venv"], as_root=True, check=True)
            except subprocess.CalledProcessError:
                Step.warn("Could not install python3-venv — if the venv "
                          "step below fails, run: sudo apt install python3-venv")

    try:
        run(jobs["install"], as_root=True, check=True)
    except subprocess.CalledProcessError:
        Step.warn("Some system packages failed to install.  The engine may "
                  "still work if OpenGL + tkinter are already present — "
                  "continuing.")

    Step.ok("System packages handled.")


# ----------------------------------------------------------------------------
# 3. Virtual environment (isolation = no PEP-668 / distro-pip conflicts)
# ----------------------------------------------------------------------------

def create_venv():
    Step.info(f"Creating virtual environment in {VENV_DIR} …")
    if os.path.isdir(VENV_DIR) and os.path.isfile(
            os.path.join(VENV_DIR, "pyvenv.cfg")):
        Step.ok("Virtual environment already exists — reusing it.")
        return
    # Windows: symlinks need admin rights / developer mode, so copy instead.
    builder = venv.EnvBuilder(with_pip=True, clear=False, symlinks=not IS_WIN)
    try:
        builder.create(VENV_DIR)
    except Exception as ex:
        Step.die(f"Could not create the virtual environment: {ex}\n"
                 f"  On Debian/Ubuntu: sudo apt install python3-venv")
    Step.ok("Virtual environment created.")


def venv_python():
    if IS_WIN:
        return os.path.join(VENV_DIR, "Scripts", "python.exe")
    return os.path.join(VENV_DIR, "bin", "python")


def venv_pip():
    return [venv_python(), "-m", "pip"]


# ----------------------------------------------------------------------------
# 4. Pinned Python packages
# ----------------------------------------------------------------------------

def install_python_packages():
    Step.info("Installing pinned Python packages (this can take a minute) …")

    # Make sure pip itself is fresh enough for modern manylinux wheels.
    run(venv_pip() + ["install", "--upgrade",
                      "pip==25.1.1", "setuptools==80.9.0", "wheel==0.45.1"],
        check=True)

    try:
        run(venv_pip() + ["install", "--upgrade", "--only-binary", ":all:"] +
            [spec for _, spec in PIP_PACKAGES], check=True)
    except subprocess.CalledProcessError:
        # The pins only have wheels for the Python versions that existed when
        # they were chosen (numba 0.61 / numpy 2.2 stop at 3.13, pygame 2.6.1
        # too), so a newer Python used to abort the whole install here.  Fall
        # back to the newest releases that have wheels for THIS Python.
        Step.warn("The pinned versions have no wheels for Python "
                  f"{platform.python_version()} - installing the newest "
                  "compatible releases instead …")
        run(venv_pip() + ["install", "--upgrade", "--only-binary", ":all:"] +
            [name for name, _ in PIP_PACKAGES], check=True)

    Step.ok("Python packages installed.")


# ----------------------------------------------------------------------------
# 5. Verification — every import the engine performs at startup
# ----------------------------------------------------------------------------

def verify_installation():
    Step.info("Verifying the installation …")
    code = r"""
import sys
failures = []

mods = {
    "numpy":    "numpy",
    "glm":      "pyglm",
    "pygame":   "pygame",
    "moderngl": "moderngl",
    "numba":    "numba",
    "trimesh":  "trimesh",
    "PIL":      "Pillow",
    "zstandard": "zstandard",
}
for module, label in mods.items():
    try:
        m = __import__(module)
        ver = getattr(m, "__version__", "?")
        print(f"  [OK] {label:10s} {ver}")
    except Exception as ex:
        failures.append(f"{label}: {ex}")
        print(f"  [FAIL] {label}: {ex}")
# tkinter is only used by the Hub and FILE BURNER windows - the engine and
# the editor run without it, so its absence is a warning, not a failure.
try:
    import tkinter
    have_tk = True
    print(f"  [OK] tkinter    {tkinter.TkVersion}")
except Exception as ex:
    have_tk = False
    print(f"  [WARN] tkinter: {ex}  (needed for the Hub only: install your "
          f"distro's python3-tk for this Python; the editor works without it)")

# Import the engine modules themselves (syntax + import-time checks).
import os, importlib.util
# argv[1] IS the Heaven3D folder (taking its dirname looked one level up,
# so the engine files were never found and never checked)
here = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.getcwd()
for fname in ("Heaven3D.py", "Heaven3D_runtime.py", "FILEBURNER.py", "Heaven3D_Hub.py"):
    path = os.path.join(here, fname)
    if not os.path.isfile(path):
        print(f"  [SKIP] {fname} not found next to install.py")
        continue
    if fname == "FILEBURNER.py" and not have_tk:
        print(f"  [SKIP] {fname} (needs tkinter)")
        continue
    try:
        spec = importlib.util.spec_from_file_location(fname[:-3], path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        print(f"  [OK] {fname} imports cleanly")
    except SystemExit as ex:
        # the engine exits with 1 after "FATAL: numpy not installed" etc.
        if ex.code in (0, None):
            print(f"  [OK] {fname} imports cleanly")
        else:
            failures.append(f"{fname}: exited with {ex.code}")
            print(f"  [FAIL] {fname}: exited with {ex.code}")
    except Exception as ex:
        failures.append(f"{fname}: {ex}")
        print(f"  [FAIL] {fname}: {ex}")

if failures:
    print("IMPORT FAILURES:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
"""
    res = run([venv_python(), "-c", code, os.path.dirname(os.path.abspath(__file__))],
              capture=True, check=False)
    print(res.stdout or "")
    if res.returncode != 0 and res.stderr:
        print(res.stderr[-3000:])
    if res.returncode != 0:
        Step.die("Verification failed — see the import errors above.")

    Step.ok("All imports verified.")


def check_opengl():
    Step.info("Checking OpenGL 3.3+ availability …")
    if IS_WIN or IS_MAC:
        Step.ok("OpenGL comes with the graphics driver (macOS: built in) - it is "
                "checked the first time the engine opens a window.")
        return
    glxinfo = shutil.which("glxinfo")
    if glxinfo is None:
        Step.warn("glxinfo not available — cannot verify the GPU/driver. "
                  "The engine needs OpenGL 3.3+ (any Mesa 20+ or GPU driver).")
        return
    res = run([glxinfo], capture=True, check=False)
    text = (res.stdout or "") + (res.stderr or "")
    if res.returncode != 0:
        Step.warn("glxinfo could not contact a display (headless session?).")
        Step.warn("OpenGL will be verified the first time the engine "
                  "opens a window on a desktop session.")
        return
    ver = None
    for line in text.splitlines():
        if "OpenGL core profile version" in line or \
           "OpenGL version string" in line:
            ver = line.split(":")[-1].strip()
            break
    if not ver:
        Step.warn("Could not parse the GL version; continuing anyway.")
        return
    major = 0
    try:
        major = int(ver.split()[0].split(".")[0])
    except Exception:
        pass
    if major >= 3:
        Step.ok(f"OpenGL: {ver}")
    else:
        Step.warn(f"OpenGL version looks too old ({ver}). Heaven3D needs "
                  f"3.3+. Try: sudo apt install mesa-utils libgl1-mesa-dri")


# ----------------------------------------------------------------------------
# 6. Launcher helper
# ----------------------------------------------------------------------------

LAUNCHER = "run_heaven3d.bat" if IS_WIN else "run_heaven3d.sh"


def write_launcher():
    """A tiny convenience script so users never have to type the venv path."""
    launcher = os.path.join(os.path.dirname(os.path.abspath(__file__)), LAUNCHER)
    venv_name = os.path.basename(VENV_DIR)
    if IS_WIN:
        body = f"""@echo off
rem Auto-generated by install.py - launches Heaven3D inside its venv.
cd /d "%~dp0"
if not exist "%~dp0{venv_name}\\Scripts\\python.exe" (
    echo Virtual environment missing - run: python install.py
    exit /b 1
)
"%~dp0{venv_name}\\Scripts\\python.exe" %*
"""
    else:
        body = f"""#!/usr/bin/env bash
# Auto-generated by install.py — launches Heaven3D inside its venv.
cd "$(dirname "$0")"
if [ ! -x "./{venv_name}/bin/python" ]; then
    echo "Virtual environment missing — run: python3 install.py"
    exit 1
fi
exec "./{venv_name}/bin/python" "$@"
"""
    try:
        with open(launcher, "w", encoding="utf-8", newline="\r\n" if IS_WIN else "\n") as fh:
            fh.write(body)
        os.chmod(launcher, 0o755)
        Step.ok(f"Launcher written: {launcher}")
    except OSError:
        Step.warn("Could not write the launcher script (permissions?).")


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------

def main():
    system = "Windows" if IS_WIN else ("macOS" if IS_MAC else platform.system())
    print("=" * 70)
    print(f"  Heaven3D — one-shot installer ({system})")
    print("=" * 70)

    check_python()
    if IS_WIN or IS_MAC:
        # Python from python.org already has tkinter and venv; OpenGL comes
        # with the graphics driver.  Only the pip packages are needed.
        Step.ok("No system packages needed on " + system + ".")
    else:
        install_system_packages()
    create_venv()
    install_python_packages()
    verify_installation()
    check_opengl()
    write_launcher()

    here = os.path.dirname(os.path.abspath(__file__))
    run_ = LAUNCHER if IS_WIN else "./" + LAUNCHER
    activate = (f"{VENV_DIR}\\Scripts\\activate" if IS_WIN
                else f"source {VENV_DIR}/bin/activate")
    print()
    print("=" * 70)
    Step.ok("INSTALLATION COMPLETE — your engine is ready.")
    print("=" * 70)
    print(f"""
  Open the Heaven3D Hub (projects + offline tutorials):
      cd "{here}"
      {run_} Heaven3D_Hub.py

  Run the editor directly:
      {run_} Heaven3D.py

  Run a game (standalone runtime, no editor UI):
      {run_} Heaven3D.py --runtime

  Burn a project portable (FILE BURNER):
      {run_} FILEBURNER.py

  …or activate the venv yourself and use python directly:
      {activate}
      python Heaven3D.py --runtime
""")


if __name__ == "__main__":
    main()
