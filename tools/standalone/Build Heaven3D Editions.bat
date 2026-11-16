@echo off
rem ==========================================================================
rem  Build Heaven3D Editions.bat
rem
rem  Makes the two standalone editions of Heaven3D (no Python needed to run them):
rem     Heaven3D-<version>-Windows   (+ .zip)   Heaven3D Hub.exe, Heaven3D Editor.exe
rem     Heaven3D-<version>-Linux     (+ .zip)   Heaven3D-Hub, Heaven3D-Editor
rem  both with all of Heaven3D's own packages, and each able to export games
rem  for Windows AND Linux.
rem
rem  How to use:
rem    1. Download the engine as one .zip (GitHub: Code > Download ZIP) and unzip it.
rem    2. Put the unzipped engine folder in the SAME folder as this .bat
rem          some folder\
rem             Build Heaven3D Editions.bat
rem             Heaven3D-main\          <- the engine (Heaven3D.py is inside)
rem    3. Double-click this .bat.  Results:  Heaven3D-Editions\
rem
rem  Needs: Windows 10 (version 1803 / 2018 or newer, 64-bit), internet, ~4 GB free.
rem  Nothing is installed on this PC: a private Python 3.12 is unpacked into
rem  _heaven3d_build\ next to this file (delete that folder to start fresh).
rem  Only the engine's own files, its 8 own packages and the libraries the engine
rem  uses go into the editions; anything else in the engine folder is left out.
rem ==========================================================================
setlocal EnableExtensions DisableDelayedExpansion
title Heaven3D - building the Windows and Linux editions
cd /d "%~dp0"
set "BASE=%~dp0"
set "BASE=%BASE:~0,-1%"
set "WORK=%BASE%\_heaven3d_build"
set "BOOT=%WORK%\python"
set "DL=%WORK%\dl"
set "OUT=%BASE%\Heaven3D-Editions"
set "PYV=3.12.10"

echo.
echo  Heaven3D editions builder
echo  -------------------------
if /i not "%PROCESSOR_ARCHITECTURE%"=="AMD64" if /i not "%PROCESSOR_ARCHITEW6432%"=="AMD64" (
    echo  This needs 64-bit Windows.
    goto :fail
)
where curl.exe >nul 2>nul || (
    echo  curl.exe was not found. It comes with Windows 10 version 1803 and newer:
    echo  please update Windows, then run this again.
    goto :fail
)

rem ---- 1. find the engine folder next to this .bat --------------------------
set "ENGINE="
for /d %%D in ("%BASE%\*") do (
    if not defined ENGINE if exist "%%~fD\Heaven3D.py" if exist "%%~fD\Heaven3D_Hub.py" if exist "%%~fD\Heaven3D_runtime.py" set "ENGINE=%%~fD"
)
if not defined ENGINE (
    echo  No Heaven3D engine folder was found next to this .bat.
    echo  Unzip the engine and put its folder here:  %BASE%
    echo  ^(the folder that has Heaven3D.py, Heaven3D_Hub.py and Heaven3D_runtime.py in it^)
    goto :fail
)
echo  Engine:  %ENGINE%
echo  Output:  %OUT%
echo.

rem ---- 2. a private Python 3.12 (python.org) in _heaven3d_build\python ------
if exist "%BOOT%\python.exe" if exist "%BOOT%\Lib\site-packages\pip\__init__.py" goto :have_python
echo  [setup] Getting Python %PYV% from python.org (private copy, nothing is installed)
if exist "%BOOT%" rmdir /s /q "%BOOT%"
if not exist "%DL%" mkdir "%DL%"
for %%M in (core exe lib tcltk) do (
    if not exist "%DL%\%%M.msi" (
        echo    downloading %%M.msi
        curl.exe -fL --retry 3 --silent --show-error -o "%DL%\%%M.msi.part" "https://www.python.org/ftp/python/%PYV%/amd64/%%M.msi" || goto :fail_net
        move /y "%DL%\%%M.msi.part" "%DL%\%%M.msi" >nul
    )
    echo    unpacking %%M.msi
    start "" /wait msiexec.exe /a "%DL%\%%M.msi" /qn TARGETDIR="%BOOT%" /l*v "%WORK%\msi-%%M.log"
    if errorlevel 1 goto :fail_msi
)
del /q "%BOOT%\*.msi" >nul 2>nul
if not exist "%BOOT%\python.exe" goto :fail_msi
echo    setting up pip
"%BOOT%\python.exe" -m ensurepip --default-pip >"%WORK%\pip-setup.log" 2>&1
if errorlevel 1 (
    curl.exe -fL --retry 3 --silent --show-error -o "%DL%\get-pip.py" "https://bootstrap.pypa.io/get-pip.py" || goto :fail_net
    "%BOOT%\python.exe" "%DL%\get-pip.py" --no-warn-script-location >>"%WORK%\pip-setup.log" 2>&1 || goto :fail
)
:have_python

rem ---- 3. the builder (the Python part at the end of this file) -------------
set "H3D_BAT=%~f0"
set "H3D_PY=%WORK%\build_editions.py"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$t=[IO.File]::ReadAllText($env:H3D_BAT); $m='##HEAVEN3D-'+'BUILDER##'; $i=$t.IndexOf($m); if($i -lt 0){exit 1}; [IO.File]::WriteAllText($env:H3D_PY, $t.Substring($i))"
if errorlevel 1 goto :fail
"%BOOT%\python.exe" -X utf8 -E -s "%H3D_PY%" --engine "%ENGINE%" --work "%WORK%" --out "%OUT%" --boot "%BOOT%"
if errorlevel 1 goto :fail

echo.
echo  ==========================================================
echo   Finished.  Your editions are in:
echo     %OUT%
echo  ==========================================================
start "" explorer.exe "%OUT%"
pause
exit /b 0

:fail_net
echo.
echo  A download failed. Check the internet connection and run this again
echo  ^(finished downloads are kept in %DL%^).
goto :fail
:fail_msi
echo.
echo  Python could not be unpacked. See the logs in %WORK%
echo  ^(msi-*.log^). Delete the folder %BOOT% and try again.
:fail
echo.
echo  *** The build did not finish. ***
pause
exit /b 1

##HEAVEN3D-BUILDER## (everything from here on is Python, run by the .bat above)
r"""Builds the Heaven3D standalone editions for Windows and Linux, on Windows.

Called by Build Heaven3D Editions.bat with a private Python 3.12 it set up.
Nothing is installed on the PC: everything happens in the _heaven3d_build
folder next to the .bat, the results go to Heaven3D-Editions.

  Heaven3D-<ver>-Windows\   Heaven3D Hub.exe, Heaven3D Editor.exe, runtime\python (Windows)
  Heaven3D-<ver>-Linux/     Heaven3D-Hub, Heaven3D-Editor,           runtime/python (Linux)

Each edition carries the other's game player (runtime/players), so games can
be exported for Windows and Linux from either.  Only the engine's own files
and its own packages go in (a fixed list), and only the libraries the engine
uses (plus what they need); anything else in the engine folder is left out.
"""
import argparse
import compileall
import hashlib
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
import zlib

# ---------------------------------------------------------------- what goes in
SHIP_FILES = ("Heaven3D.py", "Heaven3D_runtime.py", "Heaven3D_Hub.py", "FILEBURNER.py", "install.py",
              "LICENSE", "README.md")
SHIP_DIRS = ("templates", "tutorials", "branding", "docs", "vitrail", "h3d_editor", "packages")
# the packages that belong to Heaven3D; any other folder in packages\ is left out
ENGINE_PACKAGES = ("car_race_kit", "farscream", "hqfps_weapons", "light_theme", "mh_characters",
                   "physics_plus", "spline_roads", "tropical_islands")
# the libraries the engine imports (pip adds only what these need: glcontext, llvmlite)
LIBS = ["numpy", "pygame", "moderngl", "pyglm", "numba", "zstandard", "pillow", "trimesh", "DracoPy", "pip"]
JUNK_DIRS = {"__pycache__", ".git", ".github", ".idea", ".vscode", ".heaven3d_logs", ".island_cache",
             ".pytest_cache", "dist", "build"}
JUNK_FILES = re.compile(r"(.*\.pyc$|.*\.pyo$|.*\.heaven3d\.bak$|^Thumbs\.db$|^desktop\.ini$|^\.DS_Store$)", re.I)

LINUX_PY_URL = ("https://github.com/astral-sh/python-build-standalone/releases/download/20251014/"
                "cpython-3.12.12+20251014-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz")
LINUX_PY_SHA256 = "c74addcd1b033a6e4d60ead3ab47fcc995569027e01d3061c4a934f363c4a0cf"


def log(msg):
    print(msg, flush=True)


def run(*cmd):
    log("  > " + " ".join(str(c) for c in cmd))
    subprocess.run([str(c) for c in cmd], check=True)


def rmtree(p):
    def onerr(func, path, _exc):
        os.chmod(path, stat.S_IWRITE)
        func(path)
    if os.path.isdir(p):
        shutil.rmtree(p, onerror=onerr)


def mark(folder, platform):
    with open(os.path.join(folder, "HEAVEN3D_PLATFORM"), "w") as f:
        f.write(platform)


def copy_clean(src, dst):
    def ignore(d, names):
        return {n for n in names if (n in JUNK_DIRS and os.path.isdir(os.path.join(d, n)))
                or (JUNK_FILES.match(n) and os.path.isfile(os.path.join(d, n)))}
    shutil.copytree(src, dst, ignore=ignore)


def engine_version(engine):
    ver = "0.0.0"
    with open(os.path.join(engine, "Heaven3D.py"), encoding="utf-8") as fh:
        for line in fh:
            m = re.match(r'\s*HEAVEN_VERSION\s*=\s*"([^"]+)"', line)
            if m:
                ver = m.group(1)
    return ver


def pip_target(boot_py, target, platform_args):
    os.makedirs(target, exist_ok=True)
    run(boot_py, "-m", "pip", "install", "--disable-pip-version-check", "--no-warn-script-location",
        "--upgrade", "--target", target, "--only-binary=:all:", "--python-version", "3.12",
        "--implementation", "cp", *platform_args, *LIBS)
    for junk in ("bin", "Scripts", "lib", "lib64", "share", "include"):   # console-script stubs, DracoPy static libs
        rmtree(os.path.join(target, junk))


# ---------------------------------------------------------------- Windows runtime
def build_windows_python(boot, boot_py, work):
    dst = os.path.join(work, "winpy")
    if os.path.isfile(os.path.join(dst, "HEAVEN3D_PLATFORM")):
        return dst
    log("\n[1/5] Windows Python + libraries")
    rmtree(dst)

    def ignore(d, names):
        out = {n for n in names if n.lower().endswith(".msi") or n in ("Scripts", "__pycache__")}
        if os.path.normcase(d) == os.path.normcase(os.path.join(boot, "Lib")):
            out.add("site-packages")                 # the builder's own pip stays out
        return out
    shutil.copytree(boot, dst, ignore=ignore)
    pip_target(boot_py, os.path.join(dst, "Lib", "site-packages"), ["--platform", "win_amd64"])
    site = os.path.join(dst, "Lib", "site-packages")
    for libs in (site, os.path.join(site, "numpy.libs")):   # MSVCP140.dll for pyglm / numba on a clean Windows
        msvcp = next((f for f in (os.listdir(libs) if os.path.isdir(libs) else [])
                      if f.lower().startswith("msvcp140") and f.lower().endswith(".dll")), None)
        if msvcp:
            shutil.copy2(os.path.join(libs, msvcp), os.path.join(dst, "MSVCP140.dll"))
            break
    mark(dst, "windows")
    return dst


# ---------------------------------------------------------------- Linux runtime
LINUX_SKIP = re.compile(r"^python/(share|include|lib/pkgconfig|lib/python3\.12/(test|idlelib|turtledemo|lib2to3|"
                        r"ensurepip|config-3\.12-x86_64-linux-gnu|site-packages/[^/]+)|lib/libpython3[^/]*|"
                        r"bin/(?!python3\.12$)[^/]+)(/|$)")


def build_linux_python(boot_py, work):
    dst = os.path.join(work, "linpy")
    if os.path.isfile(os.path.join(dst, "HEAVEN3D_PLATFORM")):
        return dst
    log("\n[2/5] Linux Python + libraries")
    dl = os.path.join(work, "dl")
    os.makedirs(dl, exist_ok=True)
    tgz = os.path.join(dl, "linux-python-3.12.tar.gz")
    if not os.path.isfile(tgz) or sha256(tgz) != LINUX_PY_SHA256:
        log("  downloading the Linux Python (python-build-standalone, ~31 MB)")
        tmp = tgz + ".part"
        with urllib.request.urlopen(LINUX_PY_URL, timeout=120) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f, 1 << 20)
        if sha256(tmp) != LINUX_PY_SHA256:
            raise SystemExit("the Linux Python download is damaged (checksum mismatch) - run the .bat again")
        os.replace(tmp, tgz)
    rmtree(dst)
    root = os.path.abspath(dst)
    with tarfile.open(tgz, "r:gz") as tar:
        for m in tar:
            if not (m.isfile() or m.isdir()) or LINUX_SKIP.match(m.name):
                continue                             # (symlinks: the launchers call bin/python3 directly)
            rel = m.name.split("/", 1)[1] if "/" in m.name else ""
            if not rel:
                continue
            out = os.path.join(dst, *rel.split("/"))
            if not os.path.abspath(out).startswith(root + os.sep):
                continue
            if m.isdir():
                os.makedirs(out, exist_ok=True)
            else:
                os.makedirs(os.path.dirname(out), exist_ok=True)
                with tar.extractfile(m) as s, open(out, "wb") as f:
                    shutil.copyfileobj(s, f, 1 << 20)
    os.replace(os.path.join(dst, "bin", "python3.12"), os.path.join(dst, "bin", "python3"))
    pip_target(boot_py, os.path.join(dst, "lib", "python3.12", "site-packages"),
               ["--platform", "manylinux2014_x86_64"])     # glibc 2.17+ (pip also takes older manylinux tags)
    mark(dst, "linux")
    return dst


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


# ---------------------------------------------------------------- Windows launchers (.exe)
LAUNCHER_CS = r"""
using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Windows.Forms;
[assembly: AssemblyTitle("%DESC%")]
[assembly: AssemblyProduct("Heaven3D")]
[assembly: AssemblyCompany("Heaven3D")]
[assembly: AssemblyCopyright("Apache License 2.0")]
[assembly: AssemblyVersion("%VER4%")]
[assembly: AssemblyFileVersion("%VER4%")]
[assembly: AssemblyInformationalVersion("%VER%")]
static class Launcher {
    static void Fail(string msg, string path) {
        MessageBox.Show(msg + "\n\n" + path + "\n\nPlease extract the whole folder (all files) and run it again.",
                        "Heaven3D", MessageBoxButtons.OK, MessageBoxIcon.Error);
    }
    static string Quote(string a) {
        if (a.Length > 0 && a.IndexOfAny(new char[] {' ', '\t', '"'}) < 0) return a;
        string s = "\"";
        int bs = 0;
        foreach (char c in a) {
            if (c == '\\') { bs++; continue; }
            if (c == '"') { s += new string('\\', bs * 2 + 1) + "\""; bs = 0; continue; }
            s += new string('\\', bs) + c; bs = 0;
        }
        return s + new string('\\', bs * 2) + "\"";
    }
    [STAThread]
    static int Main(string[] args) {
        string dir = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
        string py = Path.Combine(dir, "%PYREL%");
        string script = Path.Combine(dir, "%SCRIPT%");
        if (!File.Exists(py)) { Fail("The bundled Python runtime is missing:", py); return 2; }
        if (!File.Exists(script)) { Fail("The %WHAT% files are missing:", script); return 3; }
        string cmd = "-X utf8 -E -s " + Quote(script);
        if (%GAME%) cmd += " --game " + Quote(dir);
        foreach (string a in args) cmd += " " + Quote(a);
        ProcessStartInfo si = new ProcessStartInfo(py, cmd);
        si.UseShellExecute = false;
        si.WorkingDirectory = dir;
        try { Process.Start(si); } catch (Exception) { Fail("Could not start:", py); return 4; }
        return 0;
    }
}
"""
ICON_SLOTS = ((16, 4096), (20, 6144), (24, 8192), (32, 12288), (40, 16384), (48, 20480),
              (64, 32768), (128, 98304), (256, 327680))


def padded_ico(engine, out_path):
    """The game launcher's icon: PNG images with room to spare, so the Hub's
    exporter can write a game's own icon over each of them in place."""
    images = []
    for size, cap in ICON_SLOTS:
        png = open(os.path.join(engine, "branding", "icons", "logo_%d.png" % size), "rb").read()
        need = cap - len(png) - 12
        if need < 0:
            raise SystemExit("logo_%d.png is larger than its icon slot" % size)
        body = b"\0" * need
        chunk = struct.pack(">I", len(body)) + b"hpAd" + body + struct.pack(">I", zlib.crc32(b"hpAd" + body) & 0xffffffff)
        iend = png.rindex(b"IEND") - 4
        images.append((size, png[:iend] + chunk + png[iend:]))
    head = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for size, data in images:
        d = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", d, d, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
        blobs += data
    with open(out_path, "wb") as f:
        f.write(head + entries + blobs)
    return out_path


def find_csc():
    windir = os.environ.get("WINDIR", r"C:\Windows")
    for fw in ("Framework64", "Framework"):
        p = os.path.join(windir, "Microsoft.NET", fw, "v4.0.30319", "csc.exe")
        if os.path.isfile(p):
            return p
    raise SystemExit("The C# compiler of the .NET Framework 4 was not found (csc.exe). It is part of every "
                     "Windows 10; turn on '.NET Framework 4.8 Advanced Services' in Windows Features.")


def build_launchers(engine, work, version):
    log("\n[3/5] Windows launchers (.exe with the Heaven3D logo)")
    out = os.path.join(work, "launchers")
    rmtree(out)
    os.makedirs(out)
    csc = find_csc()
    nums = [int(x) for x in re.findall(r"\d+", version)[:4]]
    nums += [0] * (4 - len(nums))
    ver4 = ".".join(str(n) for n in nums)
    plain_ico = os.path.join(engine, "branding", "heaven3d.ico")
    game_ico = padded_ico(engine, os.path.join(out, "game.ico"))
    for name, desc, pyrel, script, what, game in (
            ("hub", "Heaven3D Hub", r"runtime\\python\\pythonw.exe", "Heaven3D_Hub.py", "Heaven3D Hub", False),
            ("editor", "Heaven3D Editor", r"runtime\\python\\pythonw.exe", "Heaven3D.py", "Heaven3D editor", False),
            ("game", "Made with Heaven3D", r"engine\\python\\pythonw.exe", r"engine\\Heaven3D.py", "game", True)):
        src = (LAUNCHER_CS.replace("%DESC%", desc).replace("%VER4%", ver4).replace("%VER%", version)
               .replace("%PYREL%", pyrel).replace("%SCRIPT%", script).replace("%WHAT%", what)
               .replace("%GAME%", "true" if game else "false"))
        cs = os.path.join(out, name + ".cs")
        with open(cs, "w", encoding="utf-8") as f:
            f.write(src)
        run(csc, "/nologo", "/target:winexe", "/optimize+", "/platform:anycpu",
            "/r:System.Windows.Forms.dll", "/win32icon:" + (game_ico if game else plain_ico),
            "/out:" + os.path.join(out, name + ".exe"), cs)
    return out


# ---------------------------------------------------------------- assembling
def prune_player(src, dst):
    """A game player = the Python runtime without Tk, pip, tests, headers."""
    prune_d = {"__pycache__", "test", "tests", "idlelib", "turtledemo", "ensurepip", "lib2to3",
               "pydoc_data", "tkinter", "tcl", "tcl8", "tcl8.6", "tk8.6", "itcl4.2.4", "thread2.8.9",
               "include", "Tools", "Doc", "man", "pkgconfig", "pip", "setuptools", "wheel",
               "_distutils_hack", "venv", "Scripts"}
    prune_f = re.compile(r"(^tcl.*\.dll$|^tk.*\.dll$|^_tkinter.*|^libtcl.*|^libtk.*|.*\.a$|.*\.lib$|.*\.pdb$"
                         r"|^distutils-precedence\.pth$|^pip-.*|^HEAVEN3D_PLATFORM$)", re.I)

    def ignore(d, names):
        return {n for n in names if (os.path.isdir(os.path.join(d, n)) and (n in prune_d or n.startswith("pip-")))
                or (os.path.isfile(os.path.join(d, n)) and prune_f.match(n))}
    shutil.copytree(src, dst, ignore=ignore)


LINUX_HUB = """#!/bin/sh
# Heaven3D Hub (standalone, no install needed)
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
exec "$HERE/runtime/python/bin/python3" -X utf8 -E -s "$HERE/Heaven3D_Hub.py" "$@"
"""
LINUX_EDITOR = """#!/bin/sh
# Heaven3D editor (standalone, no install needed)
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
exec "$HERE/runtime/python/bin/python3" -X utf8 -E -s "$HERE/Heaven3D.py" "$@"
"""
LINUX_SHORTCUTS = """#!/bin/sh
# Adds Heaven3D Hub and Heaven3D Editor to your applications menu (with the logo).
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$APPS"
for app in Hub Editor; do
cat > "$APPS/heaven3d-$(echo $app | tr A-Z a-z).desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Heaven3D $app
Comment=Heaven3D game engine
Exec="$HERE/Heaven3D-$app"
Path=$HERE
Icon=$HERE/branding/heaven3d_logo.png
Terminal=false
Categories=Development;
StartupWMClass=Heaven3D
EOF
chmod +x "$APPS/heaven3d-$(echo $app | tr A-Z a-z).desktop"
done
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" >/dev/null 2>&1
echo "Heaven3D Hub and Heaven3D Editor were added to your applications menu."
"""
LINUX_EXEC = {"Heaven3D-Hub", "Heaven3D-Editor", "install-desktop-shortcuts.sh"}


def copy_engine(engine, root):
    for f in SHIP_FILES:
        if os.path.isfile(os.path.join(engine, f)):
            shutil.copy2(os.path.join(engine, f), root)
    for d in SHIP_DIRS:
        src = os.path.join(engine, d)
        if not os.path.isdir(src):
            continue
        if d != "packages":
            copy_clean(src, os.path.join(root, d))
            continue
        os.makedirs(os.path.join(root, d))
        for pid in sorted(os.listdir(src)):
            p = os.path.join(src, pid)
            if not os.path.isdir(p):
                continue
            if pid not in ENGINE_PACKAGES or not os.path.isfile(os.path.join(p, "package.json")):
                log("  left out packages\\%s (not one of Heaven3D's own packages)" % pid)
                continue
            copy_clean(p, os.path.join(root, d, pid))
    missing = [p for p in ENGINE_PACKAGES if not os.path.isdir(os.path.join(root, "packages", p))]
    if missing:
        log("  note: these Heaven3D packages are not in your engine folder: " + ", ".join(missing))


def assemble(platform, engine, version, out, winpy, linpy, launchers):
    name = "Heaven3D-%s-%s" % (version, platform.title())
    root = os.path.join(out, name)
    rmtree(root)
    os.makedirs(root)
    copy_engine(engine, root)
    rt = os.path.join(root, "runtime")
    os.makedirs(os.path.join(rt, "players"))
    os.makedirs(os.path.join(rt, "launchers"))
    shutil.copy2(os.path.join(launchers, "game.exe"), os.path.join(rt, "launchers", "heaven3d-game.exe"))
    if platform == "windows":
        shutil.copytree(winpy, os.path.join(rt, "python"))
        prune_player(linpy, os.path.join(rt, "players", "linux"))
        mark(os.path.join(rt, "players", "linux"), "linux")
        shutil.copy2(os.path.join(launchers, "hub.exe"), os.path.join(root, "Heaven3D Hub.exe"))
        shutil.copy2(os.path.join(launchers, "editor.exe"), os.path.join(root, "Heaven3D Editor.exe"))
        text = ("Heaven3D %s for Windows (standalone)\r\n\r\n"
                "1. Extract this whole folder anywhere (Desktop, Documents, a USB stick).\r\n"
                "2. Double-click  Heaven3D Hub.exe\r\n\r\n"
                "Nothing else to install: Python and every library are inside runtime\\.\r\n"
                "Heaven3D Editor.exe opens the editor directly.\r\n"
                "Export your games for Windows and Linux from the Hub (Export).\r\n" % version)
        with open(os.path.join(root, "START HERE.txt"), "w", newline="") as f:
            f.write(text)
    else:
        shutil.copytree(linpy, os.path.join(rt, "python"))
        prune_player(winpy, os.path.join(rt, "players", "windows"))
        mark(os.path.join(rt, "players", "windows"), "windows")
        for fn, txt in (("Heaven3D-Hub", LINUX_HUB), ("Heaven3D-Editor", LINUX_EDITOR),
                        ("install-desktop-shortcuts.sh", LINUX_SHORTCUTS)):
            with open(os.path.join(root, fn), "w", newline="\n") as f:
                f.write(txt)
        with open(os.path.join(root, "START HERE.txt"), "w", newline="\n") as f:
            f.write("Heaven3D %s for Linux (standalone)\n\n"
                    "1. Extract this folder anywhere (keep the file permissions: use unzip,\n"
                    "   your file manager, or  tar  - not a Windows copy of the folder).\n"
                    "2. Run  ./Heaven3D-Hub   (or double-click it)\n"
                    "3. Optional: ./install-desktop-shortcuts.sh adds Hub + Editor to your app menu.\n\n"
                    "Nothing else to install: Python and every library are inside runtime/.\n"
                    "Needs a desktop with OpenGL 3.3+ (glibc 2.17+, any distro of the last ~10 years).\n"
                    "Export your games for Linux and Windows from the Hub (Export).\n" % version)
    return root


def byte_compile(folder, boot_py):
    """Pre-compiled runtimes start faster (the .pyc files only depend on Python 3.12)."""
    for dp, dns, _f in os.walk(folder):
        for d in list(dns):
            if d == "__pycache__":
                rmtree(os.path.join(dp, d))
                dns.remove(d)
    subprocess.run([boot_py, "-m", "compileall", "-q", "-j", "0", "--invalidation-mode", "unchecked-hash",
                    folder], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def zip_tree(folder, zip_path, unix):
    top = os.path.basename(folder)
    tmp = zip_path + ".part"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for dp, dns, fns in os.walk(folder):
            dns.sort()
            for fn in sorted(fns):
                full = os.path.join(dp, fn)
                rel = os.path.relpath(full, folder).replace(os.sep, "/")
                zi = zipfile.ZipInfo.from_file(full, top + "/" + rel)
                zi.compress_type = zipfile.ZIP_DEFLATED
                if unix:                             # Linux needs the "executable" bits Windows doesn't keep
                    exe = ("/bin/" in "/" + rel or ".so" in fn or fn in LINUX_EXEC
                           or fn.endswith(".sh"))
                    zi.external_attr = (stat.S_IFREG | (0o755 if exe else 0o644)) << 16
                    zi.create_system = 3
                with open(full, "rb") as src, z.open(zi, "w") as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
    os.replace(tmp, zip_path)
    return zip_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--boot", required=True)
    a = ap.parse_args()
    engine = os.path.abspath(a.engine)
    boot_py = os.path.join(a.boot, "python.exe")
    version = engine_version(engine)
    log("Heaven3D %s from  %s" % (version, engine))
    for f in ("Heaven3D.py", "Heaven3D_runtime.py", "Heaven3D_Hub.py"):
        if not os.path.isfile(os.path.join(engine, f)):
            raise SystemExit("The engine folder is incomplete: %s is missing" % f)
    os.makedirs(a.out, exist_ok=True)
    winpy = build_windows_python(a.boot, boot_py, a.work)
    linpy = build_linux_python(boot_py, a.work)
    launchers = build_launchers(engine, a.work, version)
    log("\n[4/5] Putting the editions together")
    made = []
    for plat in ("windows", "linux"):
        root = assemble(plat, engine, version, a.out, winpy, linpy, launchers)
        for sub in ("python", os.path.join("players", "windows"), os.path.join("players", "linux")):
            if os.path.isdir(os.path.join(root, "runtime", sub)):
                byte_compile(os.path.join(root, "runtime", sub), boot_py)
        made.append((plat, root))
        log("  " + root)
    log("\n[5/5] Zipping")
    for plat, root in made:
        z = zip_tree(root, root + ".zip", unix=(plat == "linux"))
        log("  %s  (%d MB)" % (z, os.path.getsize(z) >> 20))
    log("\nDone.")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as ex:
        print("\nFAILED: a step returned an error (%s)" % ex)
        sys.exit(1)
