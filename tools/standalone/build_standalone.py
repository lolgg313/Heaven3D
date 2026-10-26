#!/usr/bin/env python3
"""
Build the Heaven3D STANDALONE editions (no Python install needed):

    Heaven3D-<ver>-Windows/   Heaven3D Hub.exe, Heaven3D Editor.exe, runtime/python (Windows)
    Heaven3D-<ver>-Linux/     Heaven3D-Hub, Heaven3D-Editor,           runtime/python (Linux)

Each edition also carries the OTHER platform's game player in
runtime/players/<platform>, so games can be exported for Windows AND Linux
from either system (Hub > Export game).

Runs on Linux.  Needs: curl, msitools (msiextract), mingw-w64
(x86_64-w64-mingw32-gcc/windres), binutils (strip, readelf), uv, and a
Python 3.12 for byte-compiling.  Downloads:
  * Windows: the official python.org 3.12 MSI components (core/exe/lib/tcltk)
  * Linux:   conda-forge python 3.12 + Tk 8.6 (Xft fonts), glibc >= 2.17
  * wheels:  numpy pygame moderngl pyglm numba zstandard pillow trimesh DracoPy pip

    python tools/standalone/build_standalone.py --work /tmp/h3d-build --out dist
"""
import argparse
import compileall
import os
import re
import shutil
import stat
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
PY_WIN = "3.12.10"                         # last 3.12 release with Windows binaries
PY_LIN = "3.12"
PKGS = ["numpy", "pygame", "moderngl", "pyglm", "numba", "zstandard", "pillow", "trimesh", "DracoPy", "pip"]
MICROMAMBA = "https://conda.anaconda.org/conda-forge/linux-64/micromamba-2.9.0-0.tar.bz2"
SHIP = ["Heaven3D.py", "Heaven3D_runtime.py", "Heaven3D_Hub.py", "FILEBURNER.py", "install.py",
        "LICENSE", "README.md", "templates", "tutorials", "branding"]


def sh(*cmd, **kw):
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def mark(folder, platform):
    with open(os.path.join(folder, "HEAVEN3D_PLATFORM"), "w") as f:
        f.write(platform)


# ----------------------------------------------------------------- Windows
def build_windows_python(work):
    dst = os.path.join(work, "winpy")
    if os.path.isfile(os.path.join(dst, "HEAVEN3D_PLATFORM")):
        return dst
    dl = os.path.join(work, "dl", "win")
    os.makedirs(dl, exist_ok=True)
    shutil.rmtree(dst, ignore_errors=True)
    os.makedirs(dst)
    for m in ("core", "exe", "lib", "tcltk"):
        msi = os.path.join(dl, m + ".msi")
        if not os.path.isfile(msi):
            sh("curl", "-sSfL", f"https://www.python.org/ftp/python/{PY_WIN}/amd64/{m}.msi", "-o", msi)
        sh("msiextract", "-C", dst, msi, stdout=subprocess.DEVNULL)
    site = os.path.join(dst, "Lib", "site-packages")
    sh("uv", "pip", "install", "--target", site, "--python-platform", "x86_64-pc-windows-msvc",
       "--python-version", "3.12", "--only-binary", ":all:", *PKGS)
    # pyglm / numba need MSVCP140.dll, which a clean Windows may not have;
    # numpy ships the (redistributable) Microsoft C++ runtime - use it.
    libs = os.path.join(site, "numpy.libs")
    msvcp = next((f for f in os.listdir(libs) if f.lower().startswith("msvcp140")), None)
    if msvcp:
        shutil.copy2(os.path.join(libs, msvcp), os.path.join(dst, "MSVCP140.dll"))
    for junk in ("lib", "bin", "share", "include"):            # DracoPy static libs etc.
        shutil.rmtree(os.path.join(site, junk), ignore_errors=True)
    mark(dst, "windows")
    return dst


# ----------------------------------------------------------------- Linux
def _elf_needed(root):
    needed = set()
    for dp, _d, fns in os.walk(root):
        for fn in fns:
            p = os.path.join(dp, fn)
            if os.path.islink(p) or not (".so" in fn or os.access(p, os.X_OK)):
                continue
            try:
                with open(p, "rb") as f:
                    if f.read(4) != b"\x7fELF":
                        continue
            except OSError:
                continue
            out = subprocess.run(["readelf", "-d", p], capture_output=True, text=True).stdout
            needed.update(re.findall(r"\(NEEDED\)\s+Shared library: \[(.+?)\]", out))
    return needed


def build_linux_python(work):
    dst = os.path.join(work, "linpy")
    if os.path.isfile(os.path.join(dst, "HEAVEN3D_PLATFORM")):
        return dst
    mm = os.path.join(work, "dl", "mm")
    os.makedirs(mm, exist_ok=True)
    mbin = os.path.join(mm, "bin", "micromamba")
    if not os.path.isfile(mbin):
        sh("curl", "-sSfL", MICROMAMBA, "-o", os.path.join(mm, "mm.tar.bz2"))
        sh("tar", "xjf", os.path.join(mm, "mm.tar.bz2"), "-C", mm, "bin/micromamba")
    env = dict(os.environ, MAMBA_ROOT_PREFIX=os.path.join(work, "mamba-root"))
    raw = os.path.join(work, "linpy-raw")
    if not os.path.isdir(raw):
        sh(mbin, "create", "-y", "-q", "-p", raw, "-c", "conda-forge", "--override-channels",
           f"python={PY_LIN}", "tk=8.6.13=xft*", env=env)
        sh("uv", "pip", "install", "--python", os.path.join(raw, "bin", "python3"), "--only-binary", ":all:", *PKGS)
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(raw, dst, symlinks=True)
    # -- relocatable + lean ------------------------------------------------
    for d in ("conda-meta", "include", "man", "share/man", "share/doc", "share/info", "share/terminfo",
              "ssl/man", "lib/pkgconfig", "lib/cmake", "compiler_compat", "x86_64-conda-linux-gnu",
              "lib/python3.12/test", "lib/python3.12/idlelib", "lib/python3.12/turtledemo",
              "lib/python3.12/lib2to3", "lib/python3.12/ensurepip",
              "lib/python3.12/site-packages/setuptools", "lib/python3.12/site-packages/_distutils_hack",
              "lib/python3.12/site-packages/wheel", "lib/python3.12/site-packages/lib"):
        shutil.rmtree(os.path.join(dst, *d.split("/")), ignore_errors=True)
    for pth in ("distutils-precedence.pth",):
        try:
            os.remove(os.path.join(dst, "lib", "python3.12", "site-packages", pth))
        except OSError:
            pass
    bindir = os.path.join(dst, "bin")
    real = os.path.join(bindir, "python3.12")
    for f in os.listdir(bindir):
        p = os.path.join(bindir, f)
        if p != real:
            os.remove(p) if (os.path.islink(p) or os.path.isfile(p)) else shutil.rmtree(p)
    os.rename(real, os.path.join(bindir, "python3"))
    libdir = os.path.join(dst, "lib")
    for f in os.listdir(libdir):
        p = os.path.join(libdir, f)
        if f.endswith(".a") or f.startswith("libpython3"):
            os.remove(p)
    needed = _elf_needed(dst)
    for f in sorted(os.listdir(libdir)):
        p = os.path.join(libdir, f)
        if os.path.islink(p):
            target = os.path.realpath(p)
            os.remove(p)
            if f in needed and os.path.isfile(target):
                shutil.copy2(target, p)
    for f in sorted(os.listdir(libdir)):                    # drop versioned originals nobody needs
        p = os.path.join(libdir, f)
        if os.path.isfile(p) and re.search(r"\.so\.\d+\.\d+", f) and f not in needed:
            base = f.split(".so.")[0] + ".so."
            if any(n.startswith(base) and os.path.isfile(os.path.join(libdir, n)) for n in needed):
                os.remove(p)
    for dp, dns, fns in os.walk(dst):                        # any remaining symlink -> real copy
        for n in dns + fns:
            p = os.path.join(dp, n)
            if os.path.islink(p):
                t = os.path.realpath(p)
                os.remove(p)
                if os.path.isdir(t):
                    shutil.copytree(t, p)
                elif os.path.isfile(t):
                    shutil.copy2(t, p)
    for dp, _d, fns in os.walk(dst):                         # strip debug symbols
        for fn in fns:
            if ".so" in fn:
                subprocess.run(["strip", "--strip-unneeded", os.path.join(dp, fn)],
                               stderr=subprocess.DEVNULL)
    mark(dst, "linux")
    return dst


# ----------------------------------------------------------------- common
def byte_compile(folder, python312):
    """Pre-compile every .py so the first start is fast (works for both
    platforms: .pyc only depends on the Python version)."""
    for dp, dns, _f in os.walk(folder):
        for d in list(dns):
            if d == "__pycache__":
                shutil.rmtree(os.path.join(dp, d))
                dns.remove(d)
    subprocess.run([python312, "-m", "compileall", "-q", "-j", "0", "--invalidation-mode", "unchecked-hash",
                    folder], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def build_launchers(work):
    out = os.path.join(work, "launchers")
    os.makedirs(out, exist_ok=True)
    shutil.copy2(os.path.join(REPO, "branding", "heaven3d.ico"), out)
    for define, desc, name in (("HUB", "Heaven3D Hub", "hub"), ("EDITOR", "Heaven3D Editor", "editor"),
                               ("GAME", "Made with Heaven3D", "game")):
        obj = os.path.join(out, name + ".o")
        sh("x86_64-w64-mingw32-windres", f'-DH3D_DESC=\\"{desc}\\"', "-I", HERE, "-I", out,
           os.path.join(HERE, "launcher.rc"), "-O", "coff", "-o", obj)
        sh("x86_64-w64-mingw32-gcc", f"-DH3D_{define}", "-municode", "-mwindows", "-Os", "-s",
           os.path.join(HERE, "launcher.c"), obj, "-o", os.path.join(out, name + ".exe"))
    return out


def prune_player(src, dst):
    """A game player = the Python runtime without Tk, pip, tests, headers."""
    prune_d = {"__pycache__", "test", "tests", "idlelib", "turtledemo", "ensurepip", "lib2to3",
               "pydoc_data", "tkinter", "tcl", "tcl8", "tcl8.6", "tk8.6", "include", "Tools", "Doc",
               "man", "conda-meta", "pkgconfig", "pip", "setuptools", "wheel", "_distutils_hack", "venv",
               "Scripts"}
    prune_f = re.compile(r"(^tcl.*\.dll$|^tk.*\.dll$|^_tkinter.*|^libtcl.*|^libtk.*|.*\.a$|.*\.lib$|.*\.pdb$"
                         r"|^distutils-precedence\.pth$)", re.I)

    def ignore(d, names):
        return {n for n in names if (os.path.isdir(os.path.join(d, n)) and n in prune_d)
                or (os.path.isfile(os.path.join(d, n)) and prune_f.match(n))}
    shutil.copytree(src, dst, ignore=ignore)


LINUX_HUB = """#!/bin/sh
# Heaven3D Hub (standalone, no install needed)
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
exec "$HERE/runtime/python/bin/python3" -E -s "$HERE/Heaven3D_Hub.py" "$@"
"""
LINUX_EDITOR = """#!/bin/sh
# Heaven3D editor (standalone, no install needed)
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
exec "$HERE/runtime/python/bin/python3" -E -s "$HERE/Heaven3D.py" "$@"
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
Categories=Development;Game;
StartupWMClass=Heaven3D
EOF
chmod +x "$APPS/heaven3d-$(echo $app | tr A-Z a-z).desktop"
done
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" >/dev/null 2>&1
echo "Heaven3D Hub and Heaven3D Editor were added to your applications menu."
"""


def assemble(platform, version, work, out, winpy, linpy, launchers):
    name = f"Heaven3D-{version}-{platform.title()}"
    root = os.path.join(out, name)
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root)
    for item in SHIP:
        s = os.path.join(REPO, item)
        if os.path.isdir(s):
            shutil.copytree(s, os.path.join(root, item), ignore=shutil.ignore_patterns("__pycache__"))
        elif os.path.isfile(s):
            shutil.copy2(s, root)
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
    else:
        shutil.copytree(linpy, os.path.join(rt, "python"))
        prune_player(winpy, os.path.join(rt, "players", "windows"))
        mark(os.path.join(rt, "players", "windows"), "windows")
        for fn, txt in (("Heaven3D-Hub", LINUX_HUB), ("Heaven3D-Editor", LINUX_EDITOR),
                        ("install-desktop-shortcuts.sh", LINUX_SHORTCUTS)):
            p = os.path.join(root, fn)
            with open(p, "w", newline="\n") as f:
                f.write(txt)
            os.chmod(p, 0o755)
    with open(os.path.join(root, "START HERE.txt"), "w", newline="\r\n" if platform == "windows" else "\n") as f:
        if platform == "windows":
            f.write(f"Heaven3D {version} for Windows (standalone)\n\n"
                    "1. Extract this whole folder anywhere (Desktop, Documents, a USB stick).\n"
                    "2. Double-click  Heaven3D Hub.exe\n\n"
                    "Nothing else to install: Python and every library are inside runtime\\.\n"
                    "Heaven3D Editor.exe opens the editor directly.\n"
                    "Export your games for Windows and Linux from the Hub (Export).\n")
        else:
            f.write(f"Heaven3D {version} for Linux (standalone)\n\n"
                    "1. Extract this folder anywhere.\n"
                    "2. Run  ./Heaven3D-Hub   (or double-click it)\n"
                    "3. Optional: ./install-desktop-shortcuts.sh adds Hub + Editor to your app menu.\n\n"
                    "Nothing else to install: Python and every library are inside runtime/.\n"
                    "Needs a desktop with OpenGL 3.3+ (any distro from the last ~10 years, glibc 2.17+).\n"
                    "Export your games for Linux and Windows from the Hub (Export).\n")
    return root


def zip_tree(folder, zip_path, unix):
    top = os.path.basename(folder)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for dp, dns, fns in os.walk(folder):
            dns.sort()
            if not dns and not fns:
                zd = zipfile.ZipInfo(os.path.join(top, os.path.relpath(dp, folder)).replace(os.sep, "/") + "/")
                zd.external_attr = ((stat.S_IFDIR | 0o755) << 16) | 0x10
                z.writestr(zd, b"")
            for fn in sorted(fns):
                full = os.path.join(dp, fn)
                arc = os.path.join(top, os.path.relpath(full, folder)).replace(os.sep, "/")
                zi = zipfile.ZipInfo.from_file(full, arc)
                zi.compress_type = zipfile.ZIP_DEFLATED
                if unix:
                    mode = os.stat(full).st_mode & 0o777
                    rel = os.path.relpath(full, folder).replace(os.sep, "/")
                    if "/bin/" in "/" + rel or ".so" in fn or rel.startswith("runtime/players/linux/bin/"):
                        mode |= 0o755
                    zi.external_attr = (stat.S_IFREG | mode) << 16
                    zi.create_system = 3
                with open(full, "rb") as src, z.open(zi, "w") as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
    return zip_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", default="/tmp/heaven3d-standalone")
    ap.add_argument("--out", default=os.path.join(REPO, "dist"))
    ap.add_argument("--version", default="2.7.0")
    ap.add_argument("--python312", default=shutil.which("python3.12") or sys.executable)
    ap.add_argument("--platforms", default="windows,linux")
    ap.add_argument("--no-zip", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.work, exist_ok=True)
    os.makedirs(a.out, exist_ok=True)
    winpy = build_windows_python(a.work)
    linpy = build_linux_python(a.work)
    for py in (winpy, linpy):
        byte_compile(py, a.python312)
    launchers = build_launchers(a.work)
    for plat in a.platforms.split(","):
        root = assemble(plat, a.version, a.work, a.out, winpy, linpy, launchers)
        # Only the bundled runtimes are pre-compiled (unchecked pycs are
        # never re-validated - right for a frozen runtime, wrong for
        # Heaven3D.py & co. which users are free to edit).
        for sub in ("python", os.path.join("players", "windows"), os.path.join("players", "linux")):
            if os.path.isdir(os.path.join(root, "runtime", sub)):
                byte_compile(os.path.join(root, "runtime", sub), a.python312)
        print("assembled", root, flush=True)
        if not a.no_zip:
            z = zip_tree(root, root + ".zip", unix=(plat == "linux"))
            print("zipped", z, os.path.getsize(z) // (1 << 20), "MB", flush=True)


if __name__ == "__main__":
    main()
