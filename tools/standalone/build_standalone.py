#!/usr/bin/env python3
"""
Build the Heaven3D STANDALONE editions (no Python install needed):

    Heaven3D-<ver>-Windows/   Heaven3D Hub.exe, Heaven3D Editor.exe, runtime/python (Windows)
    Heaven3D-<ver>-Linux/     Heaven3D-Hub, Heaven3D-Editor,           runtime/python (Linux)
    Heaven3D-<ver>-macOS-AppleSilicon/  Heaven3D Hub.app, Heaven3D Editor.app, runtime/python (macOS arm64)
    Heaven3D-<ver>-macOS-Intel/         the same for Intel Macs (x86_64)

The Windows and Linux editions carry each other's game player in
runtime/players/<platform>, so games can be exported for Windows AND Linux
from either one.  The macOS editions export macOS .app games; to keep their
download small, the Windows + Linux players come as a separate add-on
(Heaven3D-<ver>-Export-Players.zip: move its windows/ and linux/ folders
into runtime/players/).

Runs on Linux.  Needs: curl, msitools (msiextract), mingw-w64
(x86_64-w64-mingw32-gcc/windres), binutils (strip, readelf), zstd, uv, and
a Python 3.12 for byte-compiling.  Downloads:
  * Windows: the official python.org 3.12 MSI components (core/exe/lib/tcltk)
  * Linux:   conda-forge python 3.12 + Tk 8.6 (Xft fonts), glibc >= 2.17
  * macOS:   conda-forge python 3.12 + Tk 8.6 (Aqua) for osx-arm64 / osx-64,
             unpacked as-is (no prefix rewriting) so Apple's code signatures
             of every binary stay valid; macOS 11+
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
        "LICENSE", "README.md", "templates", "tutorials", "branding", "packages", "docs"]


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


# ----------------------------------------------------------------- macOS
MAC_ARCHES = {"arm64": ("osx-arm64", "aarch64-apple-darwin", "AppleSilicon"),
              "x86_64": ("osx-64", "x86_64-apple-darwin", "Intel")}


def _micromamba(work):
    mm = os.path.join(work, "dl", "mm")
    os.makedirs(mm, exist_ok=True)
    mbin = os.path.join(mm, "bin", "micromamba")
    if not os.path.isfile(mbin):
        sh("curl", "-sSfL", MICROMAMBA, "-o", os.path.join(mm, "mm.tar.bz2"))
        sh("tar", "xjf", os.path.join(mm, "mm.tar.bz2"), "-C", mm, "bin/micromamba")
    return mbin


def _extract_conda_pkg(path, dst):
    """Unpack a .conda / .tar.bz2 package into dst exactly as shipped."""
    os.makedirs(dst, exist_ok=True)
    if path.endswith(".tar.bz2"):
        sh("tar", "xjf", path, "-C", dst, "--exclude", "info")
        return
    with zipfile.ZipFile(path) as z:
        for n in z.namelist():
            if n.startswith("pkg-") and n.endswith(".tar.zst"):
                tmp = os.path.join(dst, "_pkg.tar.zst")
                with open(tmp, "wb") as f:
                    f.write(z.read(n))
                sh("tar", "--zstd", "-xf", tmp, "-C", dst)
                os.remove(tmp)


def build_mac_python(work, arch):
    subdir, triple, _label = MAC_ARCHES[arch]
    dst = os.path.join(work, f"macpy-{arch}")
    if os.path.isfile(os.path.join(dst, "HEAVEN3D_PLATFORM")):
        return dst
    mbin = _micromamba(work)
    env = dict(os.environ, MAMBA_ROOT_PREFIX=os.path.join(work, "mamba-root"), CONDA_OVERRIDE_OSX="11.0")
    plan = subprocess.run([mbin, "create", "--dry-run", "--json", "-p", os.path.join(work, "mac-plan"),
                           "-c", "conda-forge", "--override-channels", "--platform", subdir,
                           f"python={PY_LIN}", "tk"], env=env, capture_output=True, text=True, check=True)
    import json
    links = json.loads(plan.stdout)["actions"]["LINK"]
    dl = os.path.join(work, "dl", subdir)
    os.makedirs(dl, exist_ok=True)
    shutil.rmtree(dst, ignore_errors=True)
    for pkg in links:
        url = pkg["url"]
        fn = url.rsplit("/", 1)[1]
        if "/noarch/" in url and not fn.startswith("ca-certificates"):
            continue                                  # pip/setuptools/tzdata: not needed
        local = os.path.join(dl, fn)
        if not os.path.isfile(local):
            sh("curl", "-sSfL", url, "-o", local)
        _extract_conda_pkg(local, dst)
    site = os.path.join(dst, "lib", "python3.12", "site-packages")
    os.makedirs(site, exist_ok=True)
    sh("uv", "pip", "install", "--target", site, "--python-platform", triple, "--python-version", "3.12",
       "--only-binary", ":all:", *PKGS, env=dict(os.environ, MACOSX_DEPLOYMENT_TARGET="11.0"))
    for d in ("conda-meta", "include", "man", "share", "lib/pkgconfig", "lib/cmake",
              "lib/python3.12/test", "lib/python3.12/idlelib", "lib/python3.12/turtledemo",
              "lib/python3.12/lib2to3", "lib/python3.12/ensurepip", "lib/python3.12/config-3.12-darwin",
              "lib/python3.12/site-packages/lib", "lib/python3.12/site-packages/bin",
              "lib/python3.12/site-packages/include", "lib/python3.12/site-packages/share"):
        shutil.rmtree(os.path.join(dst, *d.split("/")), ignore_errors=True)
    bindir = os.path.join(dst, "bin")
    real = os.path.join(bindir, "python3.12")
    for f in os.listdir(bindir):
        p = os.path.join(bindir, f)
        if p != real:
            os.remove(p) if (os.path.islink(p) or os.path.isfile(p)) else shutil.rmtree(p)
    os.rename(real, os.path.join(bindir, "python3"))
    for dp, _d, fns in os.walk(dst):
        for fn in fns:
            if fn.endswith(".a"):
                os.remove(os.path.join(dp, fn))
    for dp, dns, fns in os.walk(dst):                        # zip-friendly: symlinks -> copies
        for n in dns + fns:
            p = os.path.join(dp, n)
            if os.path.islink(p):
                t = os.path.realpath(p)
                os.remove(p)
                if os.path.isdir(t):
                    shutil.copytree(t, p)
                elif os.path.isfile(t):
                    shutil.copy2(t, p)
    mark(dst, "macos")
    with open(os.path.join(dst, "HEAVEN3D_ARCH"), "w") as f:
        f.write(arch)
    return dst


def icns_from_branding(out_path):
    """Heaven3D logo as an Apple .icns, from the ready-made PNG sizes."""
    import struct
    b = os.path.join(REPO, "branding")
    parts = b""
    for code, rel in ((b"ic11", "icons/logo_32.png"), (b"ic12", "icons/logo_64.png"),
                      (b"ic07", "icons/logo_128.png"), (b"ic08", "icons/logo_256.png"),
                      (b"ic13", "icons/logo_256.png"), (b"ic09", "heaven3d_logo.png"),
                      (b"ic14", "heaven3d_logo.png")):
        data = open(os.path.join(b, *rel.split("/")), "rb").read()
        parts += code + struct.pack(">I", len(data) + 8) + data
    with open(out_path, "wb") as f:
        f.write(b"icns" + struct.pack(">I", len(parts) + 8) + parts)
    return out_path


MAC_APP_SCRIPT = """#!/bin/sh
# {name} (standalone, no install needed)
APP="$(cd "$(dirname "$0")/../.." && pwd)"
ROOT="$(dirname "$APP")"
case "$APP" in
  */AppTranslocation/*)
    /usr/bin/osascript -e 'display alert "Heaven3D needs one more step" message "macOS started Heaven3D from a temporary read-only copy, so it cannot find its files.\n\nOpen the Heaven3D folder, right-click  Fix macOS security warning.command  and choose Open (or follow START HERE.txt). Then start {name} again."' >/dev/null 2>&1
    exit 1;;
esac
PY="$ROOT/runtime/python"
export TCL_LIBRARY="$PY/lib/tcl8.6" TK_LIBRARY="$PY/lib/tk8.6" PYTHONNOUSERSITE=1
cd "$ROOT"
exec "$PY/bin/python3" -E -s "$ROOT/{script}" "$@"
"""
MAC_FIX = """#!/bin/sh
# Removes the "downloaded from the internet" flag from this Heaven3D folder,
# so macOS lets Heaven3D Hub / Editor (and their bundled Python) run.
cd "$(dirname "$0")"
echo "Unlocking Heaven3D in: $(pwd)"
/usr/bin/xattr -dr com.apple.quarantine . 2>/dev/null
echo "Done. You can close this window and open Heaven3D Hub."
"""


def make_mac_app(root, name, script, version):
    app = os.path.join(root, f"{name}.app", "Contents")
    os.makedirs(os.path.join(app, "MacOS"))
    os.makedirs(os.path.join(app, "Resources"))
    exe = os.path.join(app, "MacOS", name)
    with open(exe, "w", newline="\n") as f:
        f.write(MAC_APP_SCRIPT.format(name=name, script=script))
    os.chmod(exe, 0o755)
    icns_from_branding(os.path.join(app, "Resources", "icon.icns"))
    bid = "org.heaven3d." + name.split()[-1].lower()
    with open(os.path.join(app, "Info.plist"), "w", newline="\n") as f:
        f.write(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>{name}</string>
  <key>CFBundleDisplayName</key><string>{name}</string>
  <key>CFBundleExecutable</key><string>{name}</string>
  <key>CFBundleIdentifier</key><string>{bid}</string>
  <key>CFBundleIconFile</key><string>icon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>{version}</string>
  <key>CFBundleVersion</key><string>{version}</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><false/>
</dict>
</plist>
""")
    with open(os.path.join(app, "PkgInfo"), "w") as f:
        f.write("APPL????")


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


ICON_SLOTS = ((16, 4096), (20, 6144), (24, 8192), (32, 12288), (40, 16384), (48, 20480),
              (64, 32768), (128, 98304), (256, 327680))


def padded_ico(out_path):
    """An .ico whose PNG images carry a private padding chunk, so every icon
    slot in the compiled .exe has room for a game's own (bigger) icon -
    Heaven3D's exporter rewrites them in place (tools: h3d_portable.set_exe_icon)."""
    import struct
    import zlib
    images = []
    for size, cap in ICON_SLOTS:
        png = open(os.path.join(REPO, "branding", "icons", f"logo_{size}.png"), "rb").read()
        need = cap - len(png) - 12
        if need < 0:
            raise ValueError(f"logo_{size}.png is larger than its slot")
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


def build_launchers(work):
    out = os.path.join(work, "launchers")
    os.makedirs(out, exist_ok=True)
    for define, desc, name in (("HUB", "Heaven3D Hub", "hub"), ("EDITOR", "Heaven3D Editor", "editor"),
                               ("GAME", "Made with Heaven3D", "game")):
        # the game launcher gets roomy icon slots (custom game icons on export)
        if name == "game":
            padded_ico(os.path.join(out, "heaven3d.ico"))
        else:
            shutil.copy2(os.path.join(REPO, "branding", "heaven3d.ico"), out)
        obj = os.path.join(out, name + ".o")
        sh("x86_64-w64-mingw32-windres", f'-DH3D_DESC=\\"{desc}\\"', "-I", out,
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


def assemble(platform, version, work, out, winpy, linpy, launchers, macpy=None):
    name = f"Heaven3D-{version}-{platform.title()}"
    if platform.startswith("macos"):
        name = f"Heaven3D-{version}-macOS-{MAC_ARCHES[platform.split('-', 1)[1]][2]}"
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
    elif platform.startswith("macos"):
        shutil.copytree(macpy, os.path.join(rt, "python"))
        with open(os.path.join(rt, "players", "PUT WINDOWS AND LINUX PLAYERS HERE.txt"), "w") as f:
            f.write("To export games for Windows and Linux too, download\n"
                    f"Heaven3D-{version}-Export-Players.zip and move its  windows  and  linux\n"
                    "folders into this folder. Then restart the Hub.\n")
        make_mac_app(root, "Heaven3D Hub", "Heaven3D_Hub.py", version)
        make_mac_app(root, "Heaven3D Editor", "Heaven3D.py", version)
        p = os.path.join(root, "Fix macOS security warning.command")
        with open(p, "w", newline="\n") as f:
            f.write(MAC_FIX)
        os.chmod(p, 0o755)
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
        if platform.startswith("macos"):
            chip = "Apple Silicon (M1, M2, M3, M4 ...)" if platform.endswith("arm64") else "Intel"
            f.write(f"Heaven3D {version} for macOS - {chip} Macs (standalone)\n\n"
                    "1. Double-click the zip to extract it, and move the Heaven3D folder where you\n"
                    "   want to keep it (Applications, Documents ...). Keep the folder together.\n"
                    "2. Heaven3D isn't from the App Store, so unlock it once:\n"
                    "   right-click  Fix macOS security warning.command  > Open > Open.\n"
                    "   (Or in Terminal:  xattr -dr com.apple.quarantine  and drag this folder in.)\n"
                    "3. Double-click  Heaven3D Hub.app\n\n"
                    "Nothing else to install: Python and every library are inside runtime/.\n"
                    "Needs macOS 11 Big Sur or newer.\n"
                    "Export your games for macOS from the Hub (Export). For Windows and Linux\n"
                    f"games too, add Heaven3D-{version}-Export-Players.zip (see runtime/players).\n"
                    "Games built here run on " + ("Apple Silicon Macs.\n" if platform.endswith("arm64") else
                                                  "Intel Macs, and on Apple Silicon through Rosetta 2.\n")
                    + "\nRight-click on a trackpad = two-finger click (or Control-click).\n")
        elif platform == "windows":
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


def assemble_players(version, out, winpy, linpy):
    """Add-on for the macOS editions: the Windows + Linux game players."""
    root = os.path.join(out, f"Heaven3D-{version}-Export-Players")
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root)
    for plat, src in (("windows", winpy), ("linux", linpy)):
        prune_player(src, os.path.join(root, plat))
        mark(os.path.join(root, plat), plat)
    with open(os.path.join(root, "README.txt"), "w") as f:
        f.write(f"Heaven3D {version} export players (for the macOS editions)\n\n"
                "Move the  windows  and  linux  folders into  runtime/players  inside your\n"
                "Heaven3D folder, then restart the Hub. Export will offer Windows and Linux.\n"
                "(The Windows and Linux editions already include them.)\n")
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
                    if ("/bin/" in "/" + rel or ".so" in fn or "/Contents/MacOS/" in "/" + rel
                            or fn.endswith(".command")):
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
    ap.add_argument("--version", default="2.7.3")
    ap.add_argument("--python312", default=shutil.which("python3.12") or sys.executable)
    ap.add_argument("--platforms", default="windows,linux,macos-arm64,macos-x86_64")
    ap.add_argument("--no-zip", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.work, exist_ok=True)
    os.makedirs(a.out, exist_ok=True)
    winpy = build_windows_python(a.work)
    linpy = build_linux_python(a.work)
    plats = [p.strip() for p in a.platforms.split(",") if p.strip()]
    macpys = {p: build_mac_python(a.work, p.split("-", 1)[1]) for p in plats if p.startswith("macos")}
    for py in (winpy, linpy, *macpys.values()):
        byte_compile(py, a.python312)
    launchers = build_launchers(a.work)
    for plat in plats:
        root = assemble(plat, a.version, a.work, a.out, winpy, linpy, launchers, macpys.get(plat))
        # Only the bundled runtimes are pre-compiled (unchecked pycs are
        # never re-validated - right for a frozen runtime, wrong for
        # Heaven3D.py & co. which users are free to edit).
        for sub in ("python", os.path.join("players", "windows"), os.path.join("players", "linux")):
            if os.path.isdir(os.path.join(root, "runtime", sub)):
                byte_compile(os.path.join(root, "runtime", sub), a.python312)
        print("assembled", root, flush=True)
        if not a.no_zip:
            z = zip_tree(root, root + ".zip", unix=(plat != "windows"))
            print("zipped", z, os.path.getsize(z) // (1 << 20), "MB", flush=True)
    if any(p.startswith("macos") for p in plats):
        root = assemble_players(a.version, a.out, winpy, linpy)
        for sub in ("windows", "linux"):
            byte_compile(os.path.join(root, sub), a.python312)
        if not a.no_zip:
            z = zip_tree(root, root + ".zip", unix=True)
            print("zipped", z, os.path.getsize(z) // (1 << 20), "MB", flush=True)


if __name__ == "__main__":
    main()
