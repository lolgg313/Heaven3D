#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
  Heaven3D Hub — project manager + offline learning centre
================================================================================

  Like Unity Hub, for Heaven3D:

      python Heaven3D_Hub.py

  * PROJECTS  — create projects from templates, open them in the editor,
                play them, add existing project folders, remove / locate.
  * LEARN     — offline step-by-step tutorials (the tutorials/ folder),
                progress tracking and one-click practice projects.
  * TEMPLATES — Empty 3D, First-Person Arena, Coin Collector (scripted),
                Outdoor Terrain (the templates/ folder).
  * SETTINGS  — engine file, Python interpreter, default project folder,
                editor window size, dependency check, engine logs.

  Standard library only (tkinter).  The Hub never imports the engine — it
  launches it as a separate process:

      python Heaven3D.py --project <project>/main.heaven3d            (editor)
      python Heaven3D.py --runtime --project <project>/main.heaven3d  (play)

  Command line (no window):
      python Heaven3D_Hub.py --list-templates
      python Heaven3D_Hub.py --create "My Game" --template fps_arena [--location DIR]
      python Heaven3D_Hub.py --list
================================================================================
"""

import argparse
import datetime
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time

HUB_VERSION = "1.4.0"
HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(HERE, "templates")
BRANDING_DIR = os.path.join(HERE, "branding")
TUTORIALS_DIR = os.path.join(HERE, "tutorials")
CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".heaven3d")
CONFIG_FILE = os.path.join(CONFIG_DIR, "hub.json")
PROJECT_FILE = "main.heaven3d"
PROJECT_META = "project.json"
THUMB_FILE = ".heaven3d_thumb.png"
LOG_DIR = ".heaven3d_logs"
IS_WIN = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"

TEMPLATE_INFO = {
    "empty": {
        "title": "Empty 3D",
        "desc": "A fresh scene with sun, sky, clouds and ocean. Start from nothing.",
        "order": 1},
    "fps_arena": {
        "title": "First-Person Arena",
        "desc": "Walled arena with crates, a player spawn, flying enemies, health and "
                "ammo pickups and a finish trigger. Press Play and shoot.",
        "order": 2},
    "coin_collector": {
        "title": "Coin Collector (scripted)",
        "desc": "Collect every coin before the timer runs out. Shows custom Python "
                "blueprints in scripts/coins.py.",
        "order": 3},
    "outdoor_terrain": {
        "title": "Outdoor Terrain",
        "desc": "Grassy 3 x 3 km landscape with sculpted hills, ready for the "
                "Terrain editor.",
        "order": 4},
}


# =============================================================================
#  Config / project registry (no GUI)
# =============================================================================
def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _default_projects_root():
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    base = docs if os.path.isdir(docs) else os.path.expanduser("~")
    return os.path.join(base, "Heaven3D Projects")


def _detect_engine():
    for name in ("Heaven3D.py",):
        p = os.path.join(HERE, name)
        if os.path.isfile(p):
            return p
    return ""


def _bundled_python():
    """The Python shipped inside the Heaven3D standalone edition (runtime/python)."""
    for rel in (("runtime", "python", "python.exe"), ("runtime", "python", "bin", "python3")):
        p = os.path.join(HERE, *rel)
        if os.path.isfile(p):
            return p
    return ""


def _platform_id():
    return "windows" if IS_WIN else ("macos" if IS_MAC else "linux")


def export_players():
    """{platform: folder} of the game players this install can export with."""
    out = {}
    rt = os.path.join(HERE, "runtime")
    for cand in (os.path.join(rt, "python"), os.path.join(rt, "players", "windows"),
                 os.path.join(rt, "players", "linux"), os.path.join(rt, "players", "macos")):
        try:
            with open(os.path.join(cand, "HEAVEN3D_PLATFORM")) as f:
                out.setdefault(f.read().strip(), cand)
        except OSError:
            pass
    if "windows" in out and not os.path.isfile(os.path.join(rt, "launchers", "heaven3d-game.exe")):
        out.pop("windows")
    return out


def _detect_python():
    b = _bundled_python()
    if b:
        return b
    for rel in ((".venv-heaven3d", "Scripts", "python.exe"),
                (".venv-heaven3d", "bin", "python")):
        p = os.path.join(HERE, *rel)
        if os.path.isfile(p):
            return p
    exe = sys.executable or "python"
    if IS_WIN and exe.lower().endswith("pythonw.exe"):
        cand = exe[:-len("pythonw.exe")] + "python.exe"
        if os.path.isfile(cand):
            exe = cand
    return exe


class Config:
    def __init__(self, path=CONFIG_FILE):
        self.path = path
        self.data = {"projects": [], "tutorials_done": [], "settings": {}}
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                d = json.load(fh)
            if isinstance(d, dict):
                self.data.update(d)
        except (OSError, ValueError):
            pass
        s = self.data.setdefault("settings", {})
        s.setdefault("engine_path", _detect_engine())
        s.setdefault("python_path", _detect_python())
        s.setdefault("projects_root", _default_projects_root())
        s.setdefault("editor_width", 1690)
        s.setdefault("editor_height", 950)
        if not s.get("engine_path") or not os.path.isfile(s["engine_path"]):
            s["engine_path"] = _detect_engine()
        if not s.get("python_path") or not os.path.isfile(s["python_path"]):
            s["python_path"] = _detect_python()
        # The standalone edition always uses its own engine + Python unless
        # the user picked different ones in Settings.
        if _bundled_python():
            if not s.get("custom_python"):
                s["python_path"] = _bundled_python()
            if not s.get("custom_engine") and _detect_engine():
                s["engine_path"] = _detect_engine()

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, indent=2)
            os.replace(tmp, self.path)
        except OSError as ex:
            print(f"[Hub] could not save settings: {ex}")

    @property
    def settings(self):
        return self.data["settings"]

    @property
    def projects(self):
        return self.data["projects"]

    def find(self, path):
        np_ = os.path.normcase(os.path.abspath(path))
        for p in self.projects:
            if os.path.normcase(os.path.abspath(p["path"])) == np_:
                return p
        return None

    def add_project(self, path, name=None, template=None):
        path = os.path.abspath(path)
        p = self.find(path)
        if p is None:
            p = {"path": path, "name": name or os.path.basename(path),
                 "added": _now(), "last_opened": None, "template": template}
            self.projects.append(p)
        elif name:
            p["name"] = name
        self.save()
        return p

    def remove_project(self, path):
        p = self.find(path)
        if p is not None:
            self.projects.remove(p)
            self.save()


def list_templates():
    out = []
    if os.path.isdir(TEMPLATES_DIR):
        for tid in os.listdir(TEMPLATES_DIR):
            d = os.path.join(TEMPLATES_DIR, tid)
            if os.path.isfile(os.path.join(d, PROJECT_FILE)):
                info = dict(TEMPLATE_INFO.get(tid, {"title": tid.replace("_", " ").title(),
                                                    "desc": "", "order": 99}))
                meta = os.path.join(d, "template.json")
                if os.path.isfile(meta):
                    try:
                        info.update(json.load(open(meta, encoding="utf-8")))
                    except ValueError:
                        pass
                info["id"] = tid
                info["dir"] = d
                info["thumb"] = os.path.join(d, THUMB_FILE)
                out.append(info)
    try:
        for tid, d, m in package_templates():
            info = {"title": tid.split(":", 1)[1].replace("_", " ").title(), "desc": "", "order": 50}
            meta = os.path.join(d, "template.json")
            if os.path.isfile(meta):
                try:
                    info.update(json.load(open(meta, encoding="utf-8")))
                except ValueError:
                    pass
            info.update(id=tid, dir=d, thumb=os.path.join(d, THUMB_FILE), package=m["name"])
            out.append(info)
    except Exception:
        pass
    out.sort(key=lambda t: (t.get("order", 99), t["title"]))
    return out


_BAD_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def validate_project_name(name):
    name = (name or "").strip()
    if not name:
        return "Please type a project name."
    if _BAD_NAME.search(name):
        return 'A project name can\'t contain any of  < > : " / \\ | ? *'
    if name in (".", "..") or name.endswith(".") or name.endswith(" "):
        return "A project name can't end with a dot or a space."
    if len(name) > 80:
        return "Please use a shorter name (80 characters max)."
    return None


def create_project(cfg, name, template_id, location):
    """Copy a template into <location>/<name>, write project.json and
    register it.  Returns the project dict."""
    err = validate_project_name(name)
    if err:
        raise ValueError(err)
    tpl = next((t for t in list_templates() if t["id"] == template_id), None)
    if tpl is None:
        raise ValueError(f"Unknown template '{template_id}'.")
    dest = os.path.join(os.path.abspath(location), name.strip())
    if os.path.exists(dest) and os.listdir(dest):
        raise ValueError(f"The folder already exists and isn't empty:\n{dest}")
    os.makedirs(location, exist_ok=True)
    shutil.copytree(tpl["dir"], dest, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "template.json"))
    for sub in ("assets", "scripts"):
        os.makedirs(os.path.join(dest, sub), exist_ok=True)
    readme = os.path.join(dest, "assets", "README.txt")
    if not os.path.exists(readme):
        with open(readme, "w", encoding="utf-8") as fh:
            fh.write("Put your models (.glb .gltf .blend .obj), textures and sounds here.\n"
                     "Files inside the project folder are saved with RELATIVE paths, so\n"
                     "the project keeps working when you move or share the folder.\n")
    for pid in list(tpl.get("packages", []) or []):          # packages the template needs
        if os.path.isdir(os.path.join(dest, "packages", pid)):
            continue
        if find_package(pid) is None:
            raise ValueError(f"This template needs the package '{pid}'.")
        if not os.path.isdir(os.path.join(PKG_LIB_DIR, pid)):
            install_package(find_package(pid)["dir"])
        add_package_to_project(dest, pid)
    meta = {"name": name.strip(), "template": template_id, "created": _now(),
            "engine_version": engine_version(cfg.settings.get("engine_path", "")),
            "hub_version": HUB_VERSION}
    with open(os.path.join(dest, PROJECT_META), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)
    return cfg.add_project(dest, name.strip(), template_id)



# =============================================================================
#  Packages (no GUI) - pure-Python add-ons for the Hub, the editor and games
# =============================================================================
PKG_LIB_DIR = os.path.join(CONFIG_DIR, "packages")          # the user's library
PKG_BUNDLED_DIR = os.path.join(HERE, "packages")             # shipped with Heaven3D
PKG_STATE_FILE = os.path.join(CONFIG_DIR, "packages.json")   # {"enabled": {id: bool}}
PKG_TARGETS = ("hub", "editor", "game")
_PKG_ID = re.compile(r"^[a-z][a-z0-9_]{1,40}$")


def _version_tuple(v):
    return tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[.\-+]", str(v))[:3])


def read_manifest(folder):
    try:
        with open(os.path.join(folder, "package.json"), encoding="utf-8") as fh:
            m = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(m, dict) or not _PKG_ID.match(str(m.get("id", ""))):
        return None
    m.setdefault("name", m["id"])
    m.setdefault("version", "0.0.0")
    m.setdefault("targets", [])
    m.setdefault("description", "")
    m["dir"] = folder
    return m


def validate_package(folder):
    """Return (manifest, None) or (None, "what is wrong")."""
    m = read_manifest(folder)
    if m is None:
        return None, 'package.json is missing or invalid (it needs an "id" like my_package).'
    bad = [t for t in m["targets"] if t not in PKG_TARGETS]
    if bad or not m["targets"]:
        return None, '"targets" must list one or more of ' + ", ".join(PKG_TARGETS) + "."
    for key in ("hub", "engine"):
        if m.get(key) and not os.path.isfile(os.path.join(folder, m[key])):
            return None, f'"{key}" entry file {m[key]} is missing.'
    if "hub" in m["targets"] and not m.get("hub") and not os.path.isdir(os.path.join(folder, "templates")):
        return None, 'A "hub" package needs a "hub" entry file (e.g. hub.py).'
    if ("editor" in m["targets"] or "game" in m["targets"]) and not m.get("engine"):
        return None, 'An "editor" or "game" package needs an "engine" entry file (e.g. engine.py).'
    req = str(m.get("heaven3d", "")).strip()
    if req.startswith(">="):
        ev = engine_version(os.path.join(HERE, "Heaven3D.py"))
        if ev != "?" and _version_tuple(ev) < _version_tuple(req[2:]):
            return None, f"Needs Heaven3D {req[2:]} or newer (this is {ev})."
    return m, None


def _pkg_state():
    try:
        with open(PKG_STATE_FILE, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_pkg_state(d):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = PKG_STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, indent=2)
    os.replace(tmp, PKG_STATE_FILE)


def is_enabled(pid):
    return bool(_pkg_state().get("enabled", {}).get(pid, False))


def set_enabled(pid, on):
    d = _pkg_state()
    d.setdefault("enabled", {})[pid] = bool(on)
    _save_pkg_state(d)


def _list_dir(root):
    out = []
    if os.path.isdir(root):
        for d in sorted(os.listdir(root)):
            m = read_manifest(os.path.join(root, d))
            if m:
                out.append(m)
    return out


def library_packages():
    return _list_dir(PKG_LIB_DIR)


def bundled_packages():
    return _list_dir(PKG_BUNDLED_DIR)


def find_package(pid):
    """Library copy first, then the copy shipped with Heaven3D."""
    for m in library_packages() + bundled_packages():
        if m["id"] == pid:
            return m
    return None


def install_package(source, enable=True):
    """Install from a folder, a .zip or a .h3dpkg file into the library.
    Returns the manifest.  Replaces an older copy (its settings are kept)."""
    import tempfile
    import zipfile
    source = os.path.abspath(os.path.expanduser(source))
    tmp = None
    try:
        if os.path.isfile(source):
            tmp = tempfile.mkdtemp(prefix="h3dpkg_")
            with zipfile.ZipFile(source) as z:
                root = os.path.abspath(tmp)
                for n in z.namelist():
                    target = os.path.abspath(os.path.join(tmp, n))
                    if not (target == root or target.startswith(root + os.sep)):
                        raise ValueError("unsafe path inside the package file")
                z.extractall(tmp)
            folder = tmp
            if not os.path.isfile(os.path.join(folder, "package.json")):
                subs = [os.path.join(tmp, d) for d in os.listdir(tmp) if os.path.isdir(os.path.join(tmp, d))]
                if len(subs) == 1:
                    folder = subs[0]
        elif os.path.isdir(source):
            folder = source
        else:
            raise ValueError(f"Not found: {source}")
        m, err = validate_package(folder)
        if err:
            raise ValueError(err)
        dest = os.path.join(PKG_LIB_DIR, m["id"])
        if os.path.normcase(os.path.abspath(folder)) != os.path.normcase(os.path.abspath(dest)):
            keep = None
            if os.path.isfile(os.path.join(dest, "settings.json")):
                with open(os.path.join(dest, "settings.json"), "rb") as fh:
                    keep = fh.read()
            if os.path.isdir(dest):
                shutil.rmtree(dest)
            os.makedirs(PKG_LIB_DIR, exist_ok=True)
            shutil.copytree(folder, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            if keep:
                with open(os.path.join(dest, "settings.json"), "wb") as fh:
                    fh.write(keep)
        for dep in m.get("requires", []) or []:          # install what it needs
            if not os.path.isdir(os.path.join(PKG_LIB_DIR, dep)):
                b = next((x for x in bundled_packages() if x["id"] == dep), None)
                if b:
                    install_package(b["dir"], enable)
        if enable:
            set_enabled(m["id"], True)
        return read_manifest(dest)
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def uninstall_package(pid):
    d = os.path.join(PKG_LIB_DIR, pid)
    if os.path.isdir(d):
        shutil.rmtree(d)
    st = _pkg_state()
    st.get("enabled", {}).pop(pid, None)
    _save_pkg_state(st)


def export_package(pid, dest_file):
    """Write a shareable .h3dpkg (a zip with package.json at the top)."""
    import zipfile
    m = find_package(pid)
    if m is None:
        raise ValueError(f"unknown package {pid}")
    with zipfile.ZipFile(dest_file, "w", zipfile.ZIP_DEFLATED) as z:
        for dp, dns, fns in os.walk(m["dir"]):
            dns[:] = [d for d in dns if d != "__pycache__"]
            for fn in fns:
                if fn.endswith(".pyc") or fn == "settings.json":
                    continue
                full = os.path.join(dp, fn)
                z.write(full, os.path.relpath(full, m["dir"]).replace(os.sep, "/"))
    return dest_file


def project_packages(project_dir):
    return _list_dir(os.path.join(project_dir, "packages"))


def add_package_to_project(project_dir, pid, _seen=None):
    """Copy a package (and the packages it requires) into <project>/packages so
    the project - and every game exported from it - carries it."""
    _seen = _seen if _seen is not None else set()
    if pid in _seen:
        return []
    _seen.add(pid)
    m = find_package(pid)
    if m is None:
        raise ValueError(f"Package '{pid}' isn't installed.")
    added = []
    for dep in m.get("requires", []) or []:
        added += add_package_to_project(project_dir, dep, _seen)
    dest = os.path.join(project_dir, "packages", pid)
    keep = None
    if os.path.isfile(os.path.join(dest, "settings.json")):
        with open(os.path.join(dest, "settings.json"), "rb") as fh:
            keep = fh.read()
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copytree(m["dir"], dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "settings.json"))
    if keep:
        with open(os.path.join(dest, "settings.json"), "wb") as fh:
            fh.write(keep)
    return added + [pid]


def remove_package_from_project(project_dir, pid):
    d = os.path.join(project_dir, "packages", pid)
    if os.path.isdir(d):
        shutil.rmtree(d)


def package_templates():
    """Project templates shipped inside installed, enabled packages."""
    out = []
    for m in library_packages():
        if not is_enabled(m["id"]):
            continue
        root = os.path.join(m["dir"], "templates")
        for tid in sorted(os.listdir(root)) if os.path.isdir(root) else []:
            d = os.path.join(root, tid)
            if os.path.isfile(os.path.join(d, PROJECT_FILE)):
                out.append((f"{m['id']}:{tid}", d, m))
    return out


def create_package(name, author="", description="", targets=("editor", "game"), location=None):
    """Scaffold a new package in the library with working example code."""
    name = (name or "").strip() or "My Package"
    pid = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_") or "my_package"
    if not pid[0].isalpha():
        pid = "pkg_" + pid
    pid = pid[:40]
    dest = os.path.join(location or PKG_LIB_DIR, pid)
    if os.path.exists(dest):
        raise ValueError(f"A package called '{pid}' already exists.")
    os.makedirs(dest)
    targets = [t for t in PKG_TARGETS if t in targets] or ["editor", "game"]
    manifest = {"id": pid, "name": name, "version": "0.1.0", "author": author,
                "category": "Tools", "description": description or "My Heaven3D package.",
                "heaven3d": ">=2.7.0", "targets": targets}
    if "editor" in targets or "game" in targets:
        manifest["engine"] = "engine.py"
        with open(os.path.join(dest, "engine.py"), "w", encoding="utf-8") as fh:
            fh.write(PACKAGE_ENGINE_EXAMPLE.replace("{name}", name))
    if "hub" in targets:
        manifest["hub"] = "hub.py"
        with open(os.path.join(dest, "hub.py"), "w", encoding="utf-8") as fh:
            fh.write(PACKAGE_HUB_EXAMPLE.replace("{name}", name))
    with open(os.path.join(dest, "package.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    with open(os.path.join(dest, "README.md"), "w", encoding="utf-8") as fh:
        fh.write(f"# {name}\n\n{manifest['description']}\n\n"
                 "Edit engine.py / hub.py, then restart the editor (or the Hub) to see changes.\n"
                 "The full API is in docs/PACKAGES.md in your Heaven3D folder.\n")
    if location is None:
        set_enabled(pid, True)
    return read_manifest(dest)


PACKAGE_ENGINE_EXAMPLE = r'''"""{name} - runs inside the editor and (when "game" is a target) in games.

register(h3d) is called once at start-up.  h3d.module is the Heaven3D module
(Blueprint, SceneNode, Mesh, Material, glm, ...).  See docs/PACKAGES.md.
"""


def register(h3d):
    m = h3d.module

    # 1) A blueprint you can attach to objects (also works in exported games)
    @h3d.blueprint
    class Spinner(m.Blueprint):
        def __init__(self):
            super().__init__()
            self.properties.setdefault("speed", 90.0)     # editable in the Inspector

        def on_tick(self, dt):
            self.node.rotation_euler.y += float(self.properties["speed"]) * dt

    # 2) A button in the editor's Packages menu
    def add_spinning_cube(engine):
        cube = engine.add_cube(name="Spinning Cube")
        cube.position = engine.camera.position + engine.camera.forward() * 6.0
        engine.attach_blueprint(cube, Spinner)
        engine.select_node(cube)
        h3d.log("added a spinning cube - press Play")

    h3d.add_tool("{name}: add spinning cube", add_spinning_cube)

    # 3) Events: init, update, play_start, play_tick, play_stop, render, key, ...
    @h3d.on("play_start")
    def hello(engine):
        h3d.log("Play started!")
'''

PACKAGE_HUB_EXAMPLE = r'''"""{name} - runs inside the Heaven3D Hub.  See docs/PACKAGES.md."""


def register(hub):
    def build(page, ui):
        ui.label(page, "{name}", 20, "bold").pack(anchor="w", padx=32, pady=(28, 6))
        ui.label(page, "This page was added by a package.", 10, fg=ui.C["muted"]).pack(anchor="w", padx=32)
        ui.Btn(page, "Say hello", lambda: ui.toast("Hello from {name}!"), kind="primary").pack(
            anchor="w", padx=32, pady=16)

    hub.add_page("{name}", build, icon="templates")
    hub.add_project_action("Say hello", lambda project: hub.toast("Hello " + project["name"]))
'''

_VERSION_CACHE = {}


def engine_version(engine_path):
    if not engine_path or not os.path.isfile(engine_path):
        return "?"
    key = (engine_path, os.path.getmtime(engine_path))
    if key in _VERSION_CACHE:
        return _VERSION_CACHE[key]
    ver = "?"
    try:
        with open(engine_path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = re.match(r'\s*HEAVEN_VERSION\s*=\s*"([^"]+)"', line)
                if m:
                    ver = m.group(1)          # the LAST assignment wins (patches)
    except OSError:
        pass
    _VERSION_CACHE[key] = ver
    return ver


def project_meta(path):
    meta = {}
    mp = os.path.join(path, PROJECT_META)
    if os.path.isfile(mp):
        try:
            meta = json.load(open(mp, encoding="utf-8"))
        except ValueError:
            meta = {}
    return meta


def project_mtime(path):
    f = os.path.join(path, PROJECT_FILE)
    try:
        return os.path.getmtime(f)
    except OSError:
        return None


def human_time(ts):
    if not ts:
        return "never"
    if isinstance(ts, str):
        try:
            ts = datetime.datetime.fromisoformat(ts).timestamp()
        except ValueError:
            return ts
    d = time.time() - ts
    if d < 60:
        return "just now"
    if d < 3600:
        return f"{int(d // 60)} min ago"
    if d < 86400:
        return f"{int(d // 3600)} h ago"
    if d < 86400 * 30:
        n = int(d // 86400)
        return f"{n} day{'s' if n != 1 else ''} ago"
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


# =============================================================================
#  Launching the engine
# =============================================================================
class Launcher:
    def __init__(self, cfg):
        self.cfg = cfg
        self.running = {}            # project path -> (Popen, mode, log path)

    def _cmd(self, project_path, mode):
        s = self.cfg.settings
        engine = s.get("engine_path") or ""
        if not os.path.isfile(engine):
            raise RuntimeError("The Heaven3D engine file wasn't found.\n"
                               "Set it in Settings > Engine file (Heaven3D.py).")
        py = s.get("python_path") or _detect_python()
        # -u: unbuffered, so the log is complete even when Stop ends the engine
        iso = (["-E", "-s"] if py == _bundled_python() else []) + ["-u"]
        cmd = [py] + iso + [engine, "--project", os.path.join(project_path, PROJECT_FILE),
               "--width", str(int(s.get("editor_width", 1690))),
               "--height", str(int(s.get("editor_height", 950))), "--no-splash"]
        if mode == "play":
            cmd.insert(cmd.index(engine) + 1, "--runtime")
        return cmd

    def launch(self, project, mode="editor"):
        path = project["path"]
        if not os.path.isdir(path):
            raise RuntimeError(f"Project folder not found:\n{path}")
        cur = self.running.get(path)
        if cur and cur[0].poll() is None:
            raise RuntimeError("This project is already open. Close the engine "
                               "window first (or press Stop in the Hub).")
        cmd = self._cmd(path, mode)
        logd = os.path.join(path, LOG_DIR)
        os.makedirs(logd, exist_ok=True)
        log_path = os.path.join(logd, f"{mode}.log")
        logf = open(log_path, "w", encoding="utf-8", errors="replace")
        logf.write(f"# Heaven3D Hub {HUB_VERSION} — {_now()}\n# {' '.join(cmd)}\n\n")
        logf.flush()
        flags = 0
        if IS_WIN:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        env.setdefault("PYTHONIOENCODING", "utf-8")
        proc = subprocess.Popen(cmd, cwd=path, stdout=logf, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, creationflags=flags, env=env)
        self.running[path] = (proc, mode, log_path)
        project["last_opened"] = _now()
        self.cfg.save()
        return proc

    def status(self, path):
        cur = self.running.get(path)
        if not cur:
            return None
        if cur[0].poll() is None:
            return cur[1]
        return None

    def stop(self, path, wait=5.0):
        """Close the engine window for this project (waits for it to exit,
        so the project can be reopened straight away)."""
        cur = self.running.get(path)
        if cur and cur[0].poll() is None:
            cur[0].terminate()
            try:
                cur[0].wait(timeout=wait)
            except subprocess.TimeoutExpired:
                cur[0].kill()
                try:
                    cur[0].wait(timeout=wait)
                except subprocess.TimeoutExpired:
                    pass

    def last_log(self, path):
        for mode in ("editor", "play"):
            p = os.path.join(path, LOG_DIR, f"{mode}.log")
            if os.path.isfile(p):
                return p
        return None


def open_in_file_manager(path):
    try:
        if IS_WIN:
            os.startfile(path)                       # noqa: E1101 (Windows only)
        elif IS_MAC:
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as ex:
        raise RuntimeError(f"Could not open the folder: {ex}")


# =============================================================================
#  Tutorials (offline markdown)
# =============================================================================
def load_tutorials():
    out = []
    if not os.path.isdir(TUTORIALS_DIR):
        return out
    for fn in sorted(os.listdir(TUTORIALS_DIR)):
        if not fn.lower().endswith(".md"):
            continue
        p = os.path.join(TUTORIALS_DIR, fn)
        try:
            text = open(p, encoding="utf-8").read()
        except OSError:
            continue
        meta = {"id": os.path.splitext(fn)[0], "file": p, "title": fn, "level": "",
                "minutes": "", "summary": "", "template": ""}
        body = text
        if text.startswith("---"):
            end = text.find("\n---", 3)
            if end > 0:
                for line in text[3:end].splitlines():
                    if ":" in line:
                        k, v = line.split(":", 1)
                        meta[k.strip()] = v.strip()
                body = text[end + 4:].lstrip("\n")
        meta["body"] = body
        out.append(meta)
    return out


# =============================================================================
#  GUI
# =============================================================================
def _win_dpi_aware():
    """Windows: render at the real screen DPI instead of being bitmap-stretched
    (the stretched mode is what makes Tk text look blurry at 125-200 % scaling)."""
    if not IS_WIN:
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)      # per-monitor
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _win_app_id(app_id="Heaven3D.Hub"):
    """Windows: give the process its own taskbar identity so the taskbar
    shows the Heaven3D logo instead of the Python icon."""
    if not IS_WIN:
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass


def _win_style_titlebar(win, caption_rgb=None, dark=True):
    """Windows 10/11: dark title bar (and caption colour on Windows 11)."""
    if not IS_WIN:
        return
    try:
        import ctypes
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
        on = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):                                   # DWMWA_USE_IMMERSIVE_DARK_MODE
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(on), 4) == 0:
                break
        if caption_rgb:
            r, g, b = caption_rgb
            col = ctypes.c_int(r | (g << 8) | (b << 16))
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(col), 4)   # caption colour
        corner = ctypes.c_int(2)                                # rounded window corners (Win 11)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(corner), 4)
    except Exception:
        pass


# ---------------------------------------------------------------------------
#  Anti-aliased drawing (pure Python -> tk.PhotoImage).  Tk's own canvas has no
#  anti-aliasing on Windows/X11, so every rounded shape and icon is rasterised
#  here with signed-distance functions and cached.
# ---------------------------------------------------------------------------
def _rgb(c):
    c = c.lstrip("#")
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))


def _mix(a, b, t):
    if t <= 0.0:
        return a
    if t >= 1.0:
        return b
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)


def _hexc(c):
    return "#%02x%02x%02x" % (int(c[0] + 0.5), int(c[1] + 0.5), int(c[2] + 0.5))


def _cov(d):
    """distance (pixels, negative inside) -> coverage 0..1"""
    return 0.0 if d >= 0.5 else (1.0 if d <= -0.5 else 0.5 - d)


def _sd_box(x, y, cx, cy, hx, hy, r):
    qx = abs(x - cx) - (hx - r)
    qy = abs(y - cy) - (hy - r)
    ox = qx if qx > 0.0 else 0.0
    oy = qy if qy > 0.0 else 0.0
    return math.hypot(ox, oy) + min(max(qx, qy), 0.0) - r


def _sd_seg(x, y, ax, ay, bx, by):
    px_, py_ = x - ax, y - ay
    dx, dy = bx - ax, by - ay
    h = max(0.0, min(1.0, (px_ * dx + py_ * dy) / (dx * dx + dy * dy or 1.0)))
    return math.hypot(px_ - dx * h, py_ - dy * h)


def _raster_rows(w, h, fn):
    """fn(x, y) -> rgb tuple at pixel centre; returns Tk photo data."""
    rows = []
    for y in range(h):
        cy = y + 0.5
        rows.append("{" + " ".join(_hexc(fn(x + 0.5, cy)) for x in range(w)) + "}")
    return " ".join(rows)


def run_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox
    import tkinter.font as tkfont

    _win_dpi_aware()
    _win_app_id()

    C = {"bg": "#16181d", "side": "#111317", "panel": "#1d2026", "panel_hi": "#232730",
         "raised": "#2a2e37", "raised_hi": "#343944", "field": "#252932", "field_hi": "#2c313b",
         "line": "#2a2e36", "text": "#e9ebef", "muted": "#9199a6", "faint": "#5c6370",
         "accent": "#4c7dff", "accent_hi": "#6890ff", "accent_soft": "#1f2b47", "accent_text": "#a9c0ff",
         "violet": "#8b5cf6", "ok": "#3ccf8e", "ok_soft": "#173628", "warn": "#f0b44c",
         "warn_soft": "#3a2f17", "danger": "#ff6b6b", "danger_soft": "#3d1e22", "danger_soft_hi": "#4d252a",
         "code": "#121419", "code_text": "#d6dae2", "sel": "#212c45", "sel_hi": "#26334f",
         "dark": True, "body": "#d3d7de", "icode": "#ffb3d1", "key_bg": "#343944", "key_fg": "#ffffff",
         "tip_bg": "#15281f", "warn_bg": "#2b2415", "note_bg": "#1a2236", "callout": "#dfe3ea",
         "dim_title": "#c9ced6"}

    cfg = Config()
    launcher = Launcher(cfg)

    # ------------------------------------------------------------ packages (Hub side)
    # Enabled library packages with the "hub" target are loaded BEFORE the
    # window is built, so a theme package can recolour everything.
    import types as _types
    import importlib.util as _ilu
    HUB_EXT = {"pages": [], "actions": [], "handlers": {}, "loaded": [], "errors": {}, "restart": False}
    SAFE_MODE = "--safe-mode" in sys.argv or os.environ.get("HEAVEN3D_SAFE_MODE") == "1"

    class HubAPI:
        """What a package's hub.py receives in register(hub).

        hub.set_theme({...})                       replace Hub colours (see the light_theme package)
        hub.add_page(title, build, icon=)          new sidebar page; build(page_frame, ui)
        hub.add_project_action(label, fn)          item in each project's ... menu; fn(project)
        hub.on(event, fn)                          ready(hub) | project_created(project) |
                                                   project_opened(project, mode) | export_finished(project)
        hub.toast(msg) / hub.log(msg) / hub.config / hub.projects / hub.path / hub.settings
        """
        EVENTS = ("ready", "project_created", "project_opened", "export_finished")

        def __init__(self, m):
            self.manifest = m
            self.id, self.name, self.path = m["id"], m["name"], m["dir"]
            self.config = cfg
            self.root = None
            self.settings = dict(m.get("default_settings", {}) or {})
            self._settings_file = os.path.join(CONFIG_DIR, "package_settings", self.id + ".json")
            try:
                with open(self._settings_file, encoding="utf-8") as fh:
                    self.settings.update(json.load(fh))
            except (OSError, ValueError):
                pass

        @property
        def projects(self):
            return cfg.projects

        def set_theme(self, colors):
            for k, v in dict(colors).items():
                C[k] = v

        def add_page(self, title, build, icon="templates"):
            pid = f"ext_{self.id}_{len(HUB_EXT['pages'])}"
            HUB_EXT["pages"].append({"id": pid, "title": title, "build": build, "icon": icon, "pkg": self})
            return pid

        def add_project_action(self, label, fn):
            HUB_EXT["actions"].append((label, fn, self))

        def on(self, event, fn=None):
            if event not in self.EVENTS:
                raise ValueError(f"unknown hub event {event!r}")
            if fn is None:
                return lambda f: (HUB_EXT["handlers"].setdefault(event, []).append((f, self)), f)[1]
            HUB_EXT["handlers"].setdefault(event, []).append((fn, self))
            return fn

        def save_settings(self):
            os.makedirs(os.path.dirname(self._settings_file), exist_ok=True)
            with open(self._settings_file, "w", encoding="utf-8") as fh:
                json.dump(self.settings, fh, indent=2)

        def toast(self, msg, kind="ok"):
            toast(msg, kind)

        def log(self, msg):
            print(f"[{self.name}] {msg}")

    def hub_emit(event, *args):
        for fn, api in list(HUB_EXT["handlers"].get(event, [])):
            try:
                fn(*args)
            except Exception as ex:
                HUB_EXT["errors"][api.id] = f"{event}: {ex}"
                print(f"[Hub package {api.id}] {event} failed: {ex}")

    if not SAFE_MODE:
        for _m in library_packages():
            if not (is_enabled(_m["id"]) and "hub" in _m["targets"] and _m.get("hub")):
                continue
            _api = HubAPI(_m)
            try:
                _spec = _ilu.spec_from_file_location(f"h3dhub_{_m['id']}", os.path.join(_m["dir"], _m["hub"]))
                _mod = _ilu.module_from_spec(_spec)
                sys.modules[_spec.name] = _mod
                _spec.loader.exec_module(_mod)
                if hasattr(_mod, "register"):
                    _mod.register(_api)
                HUB_EXT["loaded"].append(_api)
            except Exception as ex:
                HUB_EXT["errors"][_m["id"]] = str(ex)
                print(f"[Hub] package {_m['id']} failed to load: {ex}")

    root = tk.Tk()
    root.withdraw()
    root.title("Heaven3D Hub")
    root.configure(bg=C["bg"])
    S = max(1.0, root.winfo_fpixels("1i") / 96.0)

    def px(v):
        return int(round(v * S))

    root.minsize(px(980), px(620))
    try:
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        w, h = min(px(1260), sw - 60), min(px(800), sh - 80)
        root.geometry(f"{w}x{h}+{(sw - w) // 2}+{max(0, (sh - h) // 2 - 20)}")
    except Exception:
        root.geometry("1200x760")

    # ------------------------------------------------------------ fonts
    fams = set(tkfont.families(root))

    def pick(*names, default="TkDefaultFont"):
        return next((f for f in names if f in fams), default)
    base_family = pick("Segoe UI", "SF Pro Text", ".AppleSystemUIFont", "Helvetica Neue", "Inter",
                       "Noto Sans", "Ubuntu", "Cantarell", "Roboto", "DejaVu Sans", "Arial")
    semi_family = "Segoe UI Semibold" if (IS_WIN and "Segoe UI Semibold" in fams) else None
    mono_family = pick("Cascadia Mono", "Cascadia Code", "Consolas", "SF Mono", "Menlo", "JetBrains Mono",
                       "Fira Code", "Ubuntu Mono", "DejaVu Sans Mono", "Liberation Mono", "Courier New",
                       default="TkFixedFont")
    _fonts = {}

    # macOS Tk measures fonts at 72 dpi (1 pt = 1 px): scale up to match
    # how the Hub looks on Windows/Linux (96 dpi)
    FS = 96.0 / 72.0 if IS_MAC else 1.0

    def F(size=10, weight="normal", mono=False):
        size = int(round(size * FS))
        if mono:
            return (mono_family, size, "bold" if weight in ("bold", "semibold") else "normal")
        if weight == "semibold":
            return (semi_family, size, "normal") if semi_family else (base_family, size, "bold")
        return (base_family, size, weight)

    def font_obj(f):
        if f not in _fonts:
            _fonts[f] = tkfont.Font(root=root, font=f)
        return _fonts[f]

    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
        try:
            tkfont.nametofont(name).configure(family=base_family, size=int(round(10 * FS)))
        except tk.TclError:
            pass

    # ------------------------------------------------------------ images
    _imgs = {}

    def photo(key, w, h, fn):
        img = _imgs.get(key)
        if img is None:
            img = tk.PhotoImage(master=root, width=w, height=h)
            img.put(_raster_rows(w, h, fn))
            _imgs[key] = img
        return img

    def rrect(w, h, r, fill, outer, border=None, bw=1.0):
        fo, ff = _rgb(outer), _rgb(fill)
        fb = _rgb(border) if border else None
        r = min(r, w / 2.0, h / 2.0)
        hw, hh = w / 2.0, h / 2.0

        def fn(x, y):
            a = _cov(_sd_box(x, y, hw, hh, hw, hh, r))
            if fb is None:
                return _mix(fo, ff, a)
            ai = _cov(_sd_box(x, y, hw, hh, hw - bw, hh - bw, max(0.0, r - bw)))
            return _mix(_mix(fo, fb, a), ff, ai)
        return photo(("rr", w, h, r, fill, outer, border, bw), w, h, fn)

    def corner(r, fill, outer, which):
        fo, ff = _rgb(outer), _rgb(fill)
        ox = 0 if which in ("nw", "sw") else -r
        oy = 0 if which in ("nw", "ne") else -r

        def fn(x, y):
            return _mix(fo, ff, _cov(_sd_box(x - ox, y - oy, r, r, r, r, r)))
        return photo(("corner", r, fill, outer, which), r, r, fn)

    # vector icons (drawn in a 20 x 20 design grid, scaled)
    def icon(name, color, outer, size=20):
        n = px(size)
        k = size / float(n)
        fc, fo = _rgb(color), _rgb(outer)
        st = 1.6

        def ring(d, width=st):
            return abs(d) - width / 2.0

        def d_folder(x, y):
            body = _sd_box(x, y, 10, 11.2, 7.6, 5.6, 1.8)
            tab = _sd_box(x, y, 6.2, 5.8, 3.8, 1.8, 1.2)
            return ring(min(body, tab if y < 6.4 else 99))

        def d_book(x, y):
            left = _sd_box(x, y, 6.2, 10, 4.2, 6.2, 1.4)
            right = _sd_box(x, y, 13.8, 10, 4.2, 6.2, 1.4)
            return min(ring(left), ring(right))

        def d_grid(x, y):
            return min(ring(_sd_box(x, y, cx, cy, 3.2, 3.2, 1.2))
                       for cx, cy in ((6, 6), (14, 6), (6, 14), (14, 14)))

        def d_gear(x, y):
            dx, dy = x - 10, y - 10
            rad = math.hypot(dx, dy)
            body = rad - 5.4
            ang = math.atan2(dy, dx)
            best = 99.0
            for i in range(8):
                a = i * math.pi / 4
                ca, sa = math.cos(a), math.sin(a)
                lx, ly = dx * ca + dy * sa, -dx * sa + dy * ca
                best = min(best, _sd_box(lx, ly, 6.6, 0, 1.9, 1.5, 0.5))
            solid = min(body, best)
            return max(solid, -(rad - 2.4))

        def d_search(x, y):
            return min(ring(math.hypot(x - 8.5, y - 8.5) - 5.2, 1.8), _sd_seg(x, y, 12.6, 12.6, 16.5, 16.5) - 1.0)

        def d_plus(x, y):
            return min(_sd_seg(x, y, 10, 4.5, 10, 15.5), _sd_seg(x, y, 4.5, 10, 15.5, 10)) - 1.0

        def d_dots(x, y):
            return min(math.hypot(x - cx, y - 10) - 1.6 for cx in (4.5, 10, 15.5))

        def d_check(x, y):
            return min(_sd_seg(x, y, 4.5, 10.5, 8.3, 14.2), _sd_seg(x, y, 8.3, 14.2, 15.8, 6)) - 1.0

        def d_folder_open(x, y):
            return d_folder(x, y)

        def d_copy(x, y):
            a = ring(_sd_box(x, y, 11.5, 11.5, 5, 5, 1.3))
            b = ring(_sd_box(x, y, 8, 8, 5, 5, 1.3))
            return min(a, b if not (x > 6.2 and y > 6.2) else 99)

        def d_box(x, y):
            body = ring(_sd_box(x, y, 10, 11.3, 6.6, 5.2, 1.4))
            lid = ring(_sd_box(x, y, 10, 5.2, 7.6, 1.6, 0.9))
            return min(body, lid, _sd_seg(x, y, 8.0, 9.2, 12.0, 9.2) - 0.8)

        fns = {"projects": d_folder, "learn": d_book, "templates": d_grid, "settings": d_gear,
               "packages": d_box,
               "search": d_search, "plus": d_plus, "more": d_dots,
               "check": d_check, "folder": d_folder_open, "copy": d_copy}
        sd = fns.get(name, d_grid)

        def fn(x, y):
            return _mix(fo, fc, _cov(sd(x * k, y * k) / k))
        return photo(("icon", name, color, outer, n), n, n, fn)

    def logo(size=36):
        n = px(size)
        a, b, white = _rgb(C["accent"]), _rgb(C["violet"]), (255, 255, 255)
        fo = _rgb(C["side"])
        k = size / float(n)

        def fn(x, y):
            u, v = x * k, y * k
            tile = _sd_box(u, v, size / 2, size / 2, size / 2, size / 2, size * 0.28)
            col = _mix(a, b, max(0.0, min(1.0, (u + v) / (2.0 * size))))
            s = size / 36.0
            cloud = min(math.hypot(u - 14 * s, v - 19.5 * s) - 5.2 * s,
                        math.hypot(u - 20.5 * s, v - 16 * s) - 6.6 * s,
                        math.hypot(u - 25.5 * s, v - 20.5 * s) - 4.4 * s,
                        _sd_box(u, v, 19.5 * s, 22.5 * s, 9.5 * s, 2.8 * s, 2.6 * s))
            col = _mix(col, white, _cov(cloud / k))
            return _mix(fo, col, _cov(tile / k))
        return photo(("logo", n), n, n, fn)

    _brand_cache = {}

    def brand(size):
        """The Heaven3D logo (branding/icons/logo_<n>.png) at the closest
        pre-rendered size, so it stays smooth without any image library."""
        want = px(size)
        try:
            sizes = sorted(int(f[5:-4]) for f in os.listdir(os.path.join(BRANDING_DIR, "icons"))
                           if f.startswith("logo_") and f.endswith(".png"))
        except OSError:
            sizes = []
        if not sizes:
            return logo(size)
        n = min(sizes, key=lambda v: (abs(v - want), -v))
        if n not in _brand_cache:
            try:
                _brand_cache[n] = tk.PhotoImage(master=root, file=os.path.join(BRANDING_DIR, "icons", f"logo_{n}.png"))
            except tk.TclError:
                return logo(size)
        return _brand_cache[n]

    def switch_img(on, outer):
        w, h = px(38), px(22)
        track = _rgb(C["accent"] if on else C["raised_hi"])
        knob = (255, 255, 255) if on else _rgb(C["muted"])
        fo = _rgb(outer)
        r = h / 2.0
        kx = w - r if on else r

        def fn(x, y):
            col = _mix(fo, track, _cov(_sd_box(x, y, w / 2.0, r, w / 2.0, r, r)))
            return _mix(col, knob, _cov(math.hypot(x - kx, y - r) - (r - px(3))))
        return photo(("switch", on, outer, w, h), w, h, fn)

    def progress_img(frac, w, outer):
        h = px(6)
        fo, tr, fl = _rgb(outer), _rgb(C["raised"]), _rgb(C["ok"])
        cut = frac * w

        def fn(x, y):
            col = fl if x < cut else tr
            return _mix(fo, col, _cov(_sd_box(x, y, w / 2.0, h / 2.0, w / 2.0, h / 2.0, h / 2.0)))
        return photo(("prog", round(frac, 3), w, outer), w, h, fn)

    # ------------------------------------------------------------ thumbnails
    _thumb_px = {}
    try:
        from PIL import Image as _PILImage
    except Exception:
        _PILImage = None

    def _thumb_pixels(path, w):
        """-> (w, h, rows of rgb tuples) smoothly downscaled to width w."""
        key = (path, os.path.getmtime(path), w)
        if key in _thumb_px:
            return _thumb_px[key]
        if _PILImage is not None:
            im = _PILImage.open(path).convert("RGB")
            h = max(1, round(im.height * w / im.width))
            im = im.resize((w, h), getattr(_PILImage, "LANCZOS", 1))
            data = im.tobytes()
            rows = [[tuple(data[(y * w + x) * 3:(y * w + x) * 3 + 3]) for x in range(w)] for y in range(h)]
        else:
            src = tk.PhotoImage(master=root, file=path)
            W, H = src.width(), src.height()
            raw = root.tk.splitlist(root.tk.call(src, "data"))
            pix = [[_rgb(c) for c in root.tk.splitlist(r)] for r in raw]
            h = max(1, round(H * w / W))
            if W <= w:
                rows, w, h = pix, W, H
            else:
                xs = [(int(i * W / w), max(int(i * W / w) + 1, int((i + 1) * W / w))) for i in range(w)]
                ys = [(int(j * H / h), max(int(j * H / h) + 1, int((j + 1) * H / h))) for j in range(h)]
                hrows = []
                for r in pix:
                    out = []
                    for a, b in xs:
                        n = b - a
                        sr = sg = sb = 0
                        for c in r[a:b]:
                            sr += c[0]; sg += c[1]; sb += c[2]
                        out.append((sr / n, sg / n, sb / n))
                    hrows.append(out)
                rows = []
                for a, b in ys:
                    n = b - a
                    block = hrows[a:b]
                    rows.append([(sum(rr[i][0] for rr in block) / n, sum(rr[i][1] for rr in block) / n,
                                  sum(rr[i][2] for rr in block) / n) for i in range(w)])
        _thumb_px[key] = (w, h, rows)
        return _thumb_px[key]

    def load_thumb(path, max_w, outer=None, radius=8):
        if not path or not os.path.isfile(path):
            return None
        outer = outer or C["panel"]
        try:
            key = ("thumb", path, os.path.getmtime(path), px(max_w), outer)
            if key in _imgs:
                return _imgs[key]
            import hashlib
            cache_file = os.path.join(CONFIG_DIR, "thumbs", hashlib.sha1(
                repr(key[1:] + (radius, 2)).encode("utf-8")).hexdigest() + ".png")
            if os.path.isfile(cache_file):
                try:
                    img = tk.PhotoImage(master=root, file=cache_file)
                    _imgs[key] = img
                    return img
                except tk.TclError:
                    pass
            w, h, rows = _thumb_pixels(path, px(max_w))
            fo = _rgb(outer)
            r = px(radius)
            hw, hh = w / 2.0, h / 2.0
            lines = []
            for y in range(h):
                cy = y + 0.5
                edge_y = y < r or y >= h - r
                row = rows[y]
                out = []
                for x in range(w):
                    c = row[x]
                    if edge_y and (x < r or x >= w - r):
                        c = _mix(fo, c, _cov(_sd_box(x + 0.5, cy, hw, hh, hw, hh, r)))
                    out.append(_hexc(c))
                lines.append("{" + " ".join(out) + "}")
            img = tk.PhotoImage(master=root, width=w, height=h)
            img.put(" ".join(lines))
            _imgs[key] = img
            try:
                os.makedirs(os.path.dirname(cache_file), exist_ok=True)
                img.write(cache_file, format="png")
            except (OSError, tk.TclError):
                pass
            return img
        except Exception:
            return None

    # ------------------------------------------------------------ widgets
    def recolor(w, old, new):
        for c in w.winfo_children():
            if getattr(c, "_corner", False):
                continue
            if hasattr(c, "_regen"):
                if c._outer == old:
                    c._regen(new)
                continue
            if isinstance(c, (Btn, Pill, Thumb, Switch, IconLabel)):
                if c._outer == old:
                    c.set_outer(new)
                continue
            if isinstance(c, Card):
                if c._outer == old:
                    c.set_outer(new)
                continue                                  # a card keeps its own colour
            try:
                if c.cget("bg") == old:
                    c.configure(bg=new)
            except tk.TclError:
                pass
            recolor(c, old, new)

    class Card(tk.Frame):
        """Frame with anti-aliased rounded corners."""

        def __init__(self, parent, bg, outer=None, radius=10, **kw):
            super().__init__(parent, bg=bg, bd=0, highlightthickness=0, **kw)
            self._outer = outer or parent.cget("bg")
            self._r = px(radius)
            self._corners = []
            for which, relx, rely in (("nw", 0, 0), ("ne", 1, 0), ("sw", 0, 1), ("se", 1, 1)):
                lab = tk.Label(self, bd=0, highlightthickness=0, padx=0, pady=0, bg=bg)
                lab._corner = True
                lab.place(relx=relx, rely=rely, anchor=which)
                self._corners.append((which, lab))
            self._paint()
            self.bind("<Map>", lambda e: self.lift_corners(), add="+")

        def lift_corners(self):
            for _w, lab in self._corners:
                lab.lift()

        def _paint(self):
            bg = self.cget("bg")
            for which, lab in self._corners:
                lab.configure(image=corner(self._r, bg, self._outer, which), bg=bg)

        def set_bg(self, new):
            old = self.cget("bg")
            if old == new:
                return
            self.configure(bg=new)
            self._paint()
            recolor(self, old, new)

        def set_outer(self, new):
            self._outer = new
            self._paint()

    class Btn(tk.Label):
        """Rounded, anti-aliased button."""

        def __init__(self, parent, text, cmd=None, kind="normal", size=10, padx=14, pady=7, width=None,
                     outer=None, radius=8):
            super().__init__(parent, bd=0, highlightthickness=0, compound="center", padx=0, pady=0,
                             cursor="hand2")
            self._kind, self._size, self._padx, self._pady = kind, size, padx, pady
            self._minw, self._r = width, radius
            self._outer = outer or parent.cget("bg")
            self._cmd, self._enabled, self._hot, self._text = cmd, True, False, text
            self._fg_override = None
            self._render()
            self.bind("<Enter>", lambda e: self._hover(True))
            self.bind("<Leave>", lambda e: self._hover(False))
            self.bind("<ButtonRelease-1>", self._release)

        def _colors(self):
            k = self._kind
            if not self._enabled:
                return (C["raised"] if k != "ghost" else self._outer), C["faint"], None
            if k == "primary":
                return C["accent"], "#ffffff", C["accent_hi"]
            if k == "ghost":
                return self._outer, C["muted"], C["raised"]
            if k == "danger":
                return C["danger_soft"], C["danger"], C["danger_soft_hi"]
            if k == "success":
                return C["ok_soft"], C["ok"], C["ok_soft"]
            return C["raised"], C["text"], C["raised_hi"]

        def _render(self):
            bg, fg, hot = self._colors()
            fg = self._fg_override or fg
            f = F(self._size, "semibold")
            fo = font_obj(f)
            w = max(px(self._minw or 0), fo.measure(self._text) + 2 * px(self._padx))
            h = fo.metrics("linespace") + 2 * px(self._pady)
            fill = hot if (self._hot and hot) else bg
            img = rrect(w, h, px(self._r), fill, self._outer)
            self.configure(image=img, text=self._text, fg=fg, bg=self._outer, font=f,
                           activebackground=self._outer, activeforeground=fg,
                           cursor="hand2" if self._enabled else "arrow")

        def _hover(self, on):
            self._hot = on
            self._render()

        def _release(self, e):
            if 0 <= e.x < self.winfo_width() and 0 <= e.y < self.winfo_height():
                self._click()

        def _click(self, _e=None):
            if self._enabled and self._cmd:
                self._cmd()

        def set_enabled(self, on):
            self._enabled = bool(on)
            self._render()

        def set_text(self, text, fg=None):
            self._text = text
            self._fg_override = fg
            self._render()

        def set_outer(self, new):
            self._outer = new
            self._render()

    class Pill(tk.Label):
        """Small rounded badge."""

        def __init__(self, parent, text, fg, bg, outer=None, size=8):
            super().__init__(parent, bd=0, highlightthickness=0, compound="center", padx=0, pady=0)
            self._text, self._fg, self._fill, self._size = text, fg, bg, size
            self._outer = outer or parent.cget("bg")
            self.set_outer(self._outer)

        def set_outer(self, new):
            self._outer = new
            f = F(self._size, "semibold")
            fo = font_obj(f)
            w, h = fo.measure(self._text) + px(16), fo.metrics("linespace") + px(6)
            self.configure(image=rrect(w, h, h / 2.0, self._fill, new), text=self._text,
                           fg=self._fg, bg=new, font=f)

    class Thumb(tk.Label):
        def __init__(self, parent, path, width, outer=None, radius=8):
            super().__init__(parent, bd=0, highlightthickness=0, padx=0, pady=0)
            self._path, self._tw, self._radius = path, width, radius
            self.set_outer(outer or parent.cget("bg"))

        def set_outer(self, new):
            self._outer = new
            img = load_thumb(self._path, self._tw, new, self._radius)
            self.configure(image=img, bg=new)
            self.image = img

    class IconLabel(tk.Label):
        def __init__(self, parent, name, color, size=20, outer=None):
            super().__init__(parent, bd=0, highlightthickness=0, padx=0, pady=0)
            self._name, self._color, self._size = name, color, size
            self.set_outer(outer or parent.cget("bg"))

        def set_color(self, color):
            self._color = color
            self.set_outer(self._outer)

        def set_outer(self, new):
            self._outer = new
            self.configure(image=icon(self._name, self._color, new, self._size), bg=new)

    class Switch(tk.Label):
        def __init__(self, parent, var, outer=None):
            super().__init__(parent, bd=0, highlightthickness=0, padx=0, pady=0, cursor="hand2")
            self._var = var
            self.set_outer(outer or parent.cget("bg"))
            self.bind("<Button-1>", lambda e: (var.set(not var.get()), self.set_outer(self._outer)))

        def set_outer(self, new):
            self._outer = new
            self.configure(image=switch_img(bool(self._var.get()), new), bg=new)

    class SlimScroll(tk.Canvas):
        """Thin overlay-style scrollbar that hides itself when nothing scrolls."""

        def __init__(self, parent, command, bg):
            super().__init__(parent, width=px(12), bg=bg, highlightthickness=0, bd=0)
            self._cmd, self._lo, self._hi, self._hot, self._drag = command, 0.0, 1.0, False, None
            self.bind("<Configure>", lambda e: self._draw())
            self.bind("<Enter>", lambda e: self._set_hot(True))
            self.bind("<Leave>", lambda e: self._set_hot(False))
            self.bind("<Button-1>", self._press)
            self.bind("<B1-Motion>", self._move)
            self.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag", None))

        def set(self, lo, hi):
            self._lo, self._hi = float(lo), float(hi)
            self._draw()

        def _set_hot(self, on):
            self._hot = on
            self._draw()

        def _draw(self):
            self.delete("all")
            if self._hi - self._lo >= 0.999:
                return
            h, w = self.winfo_height(), self.winfo_width()
            y0 = self._lo * h
            y1 = max(y0 + px(28), self._hi * h)
            t = px(6) if (self._hot or self._drag) else px(4)
            x = w / 2.0
            self.create_line(x, y0 + t / 2 + 2, x, y1 - t / 2 - 2, width=t, capstyle="round",
                             fill=C["raised_hi"] if (self._hot or self._drag) else C["raised"])

        def _press(self, e):
            h = max(1, self.winfo_height())
            pos = e.y / h
            if not (self._lo <= pos <= self._hi):
                self._cmd("moveto", pos - (self._hi - self._lo) / 2)
            self._drag = (e.y, self._lo)

        def _move(self, e):
            if self._drag:
                y0, lo0 = self._drag
                self._cmd("moveto", lo0 + (e.y - y0) / max(1, self.winfo_height()))

    def hoverable(card, normal, hot):
        """Hover colour for a whole card, without flicker when crossing children."""
        nf = normal if callable(normal) else (lambda: normal)
        hf = hot if callable(hot) else (lambda: hot)

        def check():
            try:
                x, y = card.winfo_pointerxy()
                w = card.winfo_containing(x, y)
            except (KeyError, tk.TclError):
                w = None
            inside = False
            while w is not None:
                if w is card:
                    inside = True
                    break
                w = w.master
            try:
                card.set_bg(hf() if inside else nf())
            except tk.TclError:
                pass

        def bind(w):
            w.bind("<Enter>", lambda e: card.after_idle(check), add="+")
            w.bind("<Leave>", lambda e: card.after(15, check), add="+")
            for c in w.winfo_children():
                if not getattr(c, "_corner", False):
                    bind(c)
        bind(card)

    def entry(parent, var, width=None, outer=None, placeholder=None, font_size=10, icon_name=None):
        """Filled rounded text field.  Returns (card, tk.Entry)."""
        box = Card(parent, C["field"], outer=outer, radius=8)
        if icon_name:
            IconLabel(box, icon_name, C["muted"], 18).pack(side="left", padx=(px(10), 0))
        e = tk.Entry(box, textvariable=var, bg=C["field"], fg=C["text"], insertbackground=C["text"],
                     relief="flat", bd=0, highlightthickness=0, font=F(font_size),
                     selectbackground=C["accent"], selectforeground="#ffffff",
                     disabledbackground=C["field"], width=width or 20)
        e.pack(side="left", fill="x", expand=True, padx=px(10), pady=px(8))
        if placeholder:
            state = {"ph": False}

            def show_ph(_e=None):
                if not var.get():
                    state["ph"] = True
                    e.configure(fg=C["faint"])
                    e.insert(0, placeholder)

            def hide_ph(_e=None):
                if state["ph"]:
                    state["ph"] = False
                    e.delete(0, "end")
                    e.configure(fg=C["text"])
            e.bind("<FocusIn>", hide_ph, add="+")
            e.bind("<FocusOut>", show_ph, add="+")
            e._ph_state = state
            show_ph()
        e.bind("<FocusIn>", lambda _e: box.set_bg(C["field_hi"]), add="+")
        e.bind("<FocusOut>", lambda _e: box.set_bg(C["field"]), add="+")
        box.bind("<Button-1>", lambda _e: e.focus_set())
        return box, e

    def scrollable(parent, bg):
        outer = tk.Frame(parent, bg=bg)
        canvas = tk.Canvas(outer, bg=bg, highlightthickness=0, bd=0, yscrollincrement=px(24))
        sb = SlimScroll(outer, canvas.yview, bg)
        inner = tk.Frame(canvas, bg=bg)
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=(0, 0, e.width, e.height)))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        def wheel(e):
            if canvas.yview() == (0.0, 1.0):
                return
            if e.num == 4:
                canvas.yview_scroll(-2, "units")
            elif e.num == 5:
                canvas.yview_scroll(2, "units")
            else:
                d = e.delta
                canvas.yview_scroll(int(-d / 120) * 2 if abs(d) >= 120 else (-1 if d > 0 else 1), "units")

        def bind_wheel(_e):
            canvas.bind_all("<MouseWheel>", wheel)
            canvas.bind_all("<Button-4>", wheel)
            canvas.bind_all("<Button-5>", wheel)

        def unbind_wheel(_e):
            canvas.unbind_all("<MouseWheel>")
            canvas.unbind_all("<Button-4>")
            canvas.unbind_all("<Button-5>")
        outer.bind("<Enter>", bind_wheel)
        outer.bind("<Leave>", unbind_wheel)
        return outer, inner, canvas

    def placeholder(parent, w, h, text="No preview", outer=None):
        ph = Card(parent, C["raised"], outer=outer, radius=8, width=px(w), height=px(h))
        ph.pack_propagate(False)
        tk.Label(ph, text=text, bg=C["raised"], fg=C["muted"], font=F(9)).pack(expand=True)
        return ph

    _toasts = []

    def toast(msg, kind="ok"):
        for t in _toasts:
            try:
                t.destroy()
            except tk.TclError:
                pass
        fill, fg = {"ok": (C["ok"], "#06140d"), "warn": (C["warn"], "#1d1404"),
                    "err": (C["danger"], "#ffffff")}[kind]
        f = F(10, "semibold")
        fo = font_obj(f)
        w, h = fo.measure(msg) + px(36), fo.metrics("linespace") + px(18)
        t = tk.Label(root, image=rrect(w, h, h / 2.0, fill, C["bg"]), text=msg, compound="center",
                     fg=fg, bg=C["bg"], font=f, bd=0, padx=0, pady=0)
        t.place(relx=0.5, rely=1.0, y=-px(26), x=px(116), anchor="s")
        _toasts[:] = [t]
        root.after(2600, lambda: t.destroy() if t.winfo_exists() else None)

    def error(title, msg):
        messagebox.showerror(title, msg, parent=root)

    def label(parent, text, size=10, weight="normal", fg=None, **kw):
        return tk.Label(parent, text=text, bg=parent.cget("bg"), fg=fg or C["text"],
                        font=F(size, weight), bd=0, **kw)

    # ------------------------------------------------------------ layout
    side = tk.Frame(root, bg=C["side"], width=px(236))
    side.pack(side="left", fill="y")
    side.pack_propagate(False)
    tk.Frame(root, bg=C["line"], width=1).pack(side="left", fill="y")
    content = tk.Frame(root, bg=C["bg"])
    content.pack(side="left", fill="both", expand=True)

    brand_row = tk.Frame(side, bg=C["side"])
    brand_row.pack(fill="x", padx=px(20), pady=(px(24), px(26)))
    tk.Label(brand_row, image=brand(44), bg=C["side"], bd=0).pack(side="left")
    bt = tk.Frame(brand_row, bg=C["side"])
    bt.pack(side="left", padx=px(12))
    label(bt, "Heaven3D", 14, "bold").pack(anchor="w")
    label(bt, "Hub", 9, fg=C["muted"]).pack(anchor="w")

    label(side, "MENU", 8, "semibold", fg=C["faint"]).pack(anchor="w", padx=px(26), pady=(0, px(6)))

    pages = {}
    nav_items = {}
    current = {"page": None}

    def show(name):
        current["page"] = name
        for n, (card, ic, lab) in nav_items.items():
            on = n == name
            card.set_bg(C["sel"] if on else C["side"])
            ic.set_color(C["accent_text"] if on else C["muted"])
            lab.configure(fg=C["text"] if on else C["muted"])
        for p in pages.values():
            p.pack_forget()
        pages[name].pack(fill="both", expand=True)
        refresh = getattr(pages[name], "refresh", None)
        if refresh:
            refresh()

    def nav(name, text, icon_name=None):
        card = Card(side, C["side"], radius=9, cursor="hand2")
        card.pack(fill="x", padx=px(12), pady=px(2))
        ic = IconLabel(card, icon_name or name, C["muted"], 20)
        ic.pack(side="left", padx=(px(14), px(12)), pady=px(10))
        lab = tk.Label(card, text=text, bg=C["side"], fg=C["muted"], font=F(10, "semibold"), anchor="w")
        lab.pack(side="left", fill="x", expand=True)
        for w in (card, ic, lab):
            w.bind("<Button-1>", lambda e: show(name))
        hoverable(card, lambda: C["sel"] if current["page"] == name else C["side"],
                  lambda: C["sel_hi"] if current["page"] == name else C["panel_hi"])
        nav_items[name] = (card, ic, lab)

    nav("projects", "Projects")
    nav("learn", "Learn")
    nav("templates", "Templates")
    for _ext in HUB_EXT["pages"]:
        nav(_ext["id"], _ext["title"][:22], _ext.get("icon") or "templates")
    nav("packages", "Packages")
    nav("settings", "Settings")

    foot = tk.Frame(side, bg=C["side"])
    foot.pack(side="bottom", fill="x", padx=px(20), pady=px(18))
    eng_card = Card(foot, C["panel"], radius=10)
    eng_card.pack(fill="x")
    eng_dot = tk.Label(eng_card, text="●", bg=C["panel"], fg=C["ok"], font=F(9))
    eng_dot.pack(side="left", anchor="n", padx=(px(14), px(8)), pady=px(10))
    eng_lbl = tk.Label(eng_card, text="", bg=C["panel"], fg=C["muted"], font=F(9), justify="left", anchor="w")
    eng_lbl.pack(side="left", fill="x", pady=px(10))

    def update_footer():
        ev = engine_version(cfg.settings.get("engine_path", ""))
        ok = ev != "?"
        eng_dot.configure(fg=C["ok"] if ok else C["danger"])
        eng_lbl.configure(text=(f"Engine v{ev}" if ok else "Engine not found") + f"\nHub v{HUB_VERSION}",
                          fg=C["muted"] if ok else C["danger"])

    def page_header(parent, title, subtitle=""):
        h = tk.Frame(parent, bg=C["bg"])
        h.pack(fill="x", padx=px(32), pady=(px(28), px(4) if subtitle else px(14)))
        label(h, title, 21, "bold").pack(side="left", anchor="s")
        if subtitle:
            label(parent, subtitle, 10, fg=C["muted"]).pack(anchor="w", padx=px(33), pady=(0, px(16)))
        return h

    # ================================================================ PROJECTS
    pp = tk.Frame(content, bg=C["bg"])
    pages["projects"] = pp
    head = page_header(pp, "Projects")
    search_var = tk.StringVar()
    actions = tk.Frame(head, bg=C["bg"])
    actions.pack(side="right")
    sbox, sent = entry(actions, search_var, width=24, placeholder="Search projects", icon_name="search")
    sbox.pack(side="left", padx=(0, px(10)))
    Btn(actions, "Add existing", lambda: add_existing()).pack(side="left", padx=(0, px(8)))
    Btn(actions, "+   New project", lambda: new_project_dialog(), kind="primary").pack(side="left")

    COLS = [("NAME", 1, 0), ("VERSION", 0, 90), ("MODIFIED", 0, 120), ("", 0, 340)]
    THUMB_W = 136

    def grid_cols(frame):
        frame.grid_columnconfigure(0, minsize=px(THUMB_W + 16))
        for i, (_t, weight, minsize) in enumerate(COLS):
            frame.grid_columnconfigure(i + 1, weight=weight, minsize=px(minsize))

    hdr = tk.Frame(pp, bg=C["bg"])
    hdr.pack(fill="x", padx=(px(44), px(38)), pady=(px(6), px(6)))
    grid_cols(hdr)
    for i, (txt, _w, _m) in enumerate(COLS):
        label(hdr, txt, 8, "semibold", fg=C["faint"], anchor="w").grid(row=0, column=i + 1, sticky="w",
                                                                         padx=(px(4) if i == 0 else 0, 0))
    plist_outer, plist, plist_canvas = scrollable(pp, C["bg"])
    plist_outer.pack(fill="both", expand=True, padx=(px(20), px(8)), pady=(0, px(16)))

    def on_search(*_):
        render_projects()
    search_var.trace_add("write", on_search)

    def search_text():
        if getattr(sent, "_ph_state", {}).get("ph"):
            return ""
        return search_var.get().strip().lower()

    def project_row(p):
        path = p["path"]
        exists = os.path.isfile(os.path.join(path, PROJECT_FILE))
        card = Card(plist, C["panel"], outer=C["bg"], radius=12, cursor="hand2")
        card.pack(fill="x", pady=px(5), padx=(px(12), px(6)))
        body = tk.Frame(card, bg=C["panel"])
        body.pack(fill="x", padx=px(12), pady=px(12))
        grid_cols(body)
        thumb_path = os.path.join(path, THUMB_FILE)
        if exists and os.path.isfile(thumb_path) and load_thumb(thumb_path, THUMB_W) is not None:
            Thumb(body, thumb_path, THUMB_W).grid(row=0, column=0, sticky="w")
        else:
            placeholder(body, THUMB_W, int(THUMB_W * 9 / 16), "No preview" if exists else "Missing").grid(
                row=0, column=0, sticky="w")
        txt = tk.Frame(body, bg=C["panel"])
        txt.grid(row=0, column=1, sticky="ew", padx=(px(4), px(10)))
        label(txt, p.get("name") or os.path.basename(path), 12, "semibold", anchor="w", width=1).pack(fill="x")
        path_lbl = label(txt, path, 9, fg=C["muted"], anchor="w", width=1)
        path_lbl.pack(fill="x", pady=(px(2), px(8)))
        txt.bind("<Configure>", lambda e, l=path_lbl, t=path: l.configure(text=elide(t, F(9), e.width)), add="+")
        status = launcher.status(path)
        meta = project_meta(path) if exists else {}
        tpl = TEMPLATE_INFO.get(meta.get("template") or p.get("template") or "", {}).get("title", "")
        badges = tk.Frame(txt, bg=C["panel"])
        badges.pack(fill="x")
        if not exists:
            Pill(badges, "Folder or main.heaven3d not found", C["danger"], C["danger_soft"]).pack(side="left")
        elif status:
            Pill(badges, "●  Running in editor" if status == "editor" else "●  Playing",
                 C["ok"], C["ok_soft"]).pack(side="left")
        if exists and tpl:
            Pill(badges, tpl, C["accent_text"], C["accent_soft"]).pack(side="left", padx=(px(6) if status else 0, 0))
        ver = ("v" + (meta.get("engine_version") or engine_version(cfg.settings.get("engine_path", "")))) if exists else ""
        label(body, ver, 10, fg=C["muted"], anchor="w").grid(row=0, column=2, sticky="w")
        label(body, human_time(project_mtime(path)) if exists else "—", 10, fg=C["muted"], anchor="w").grid(
            row=0, column=3, sticky="w")
        right = tk.Frame(body, bg=C["panel"])
        right.grid(row=0, column=4, sticky="e")
        if exists:
            if status:
                Btn(right, "■   Stop", lambda: (launcher.stop(path), root.after(400, render_projects)),
                    kind="danger").pack(side="left", padx=px(3))
            else:
                Btn(right, "Export", lambda: export_dialog(p), kind="ghost").pack(side="left", padx=px(3))
                Btn(right, "▶   Play", lambda: launch(p, "play")).pack(side="left", padx=px(3))
                Btn(right, "Open", lambda: launch(p, "editor"), kind="primary", width=84).pack(side="left", padx=px(3))
        else:
            Btn(right, "Locate…", lambda: locate(p)).pack(side="left", padx=px(3))
        more = Btn(right, "•••", None, kind="ghost", padx=10, size=9)
        more.pack(side="left", padx=(px(3), 0))
        menu = tk.Menu(root, tearoff=0, bg=C["raised"], fg=C["text"], activebackground=C["accent"],
                       activeforeground="#ffffff", bd=0, relief="flat", font=F(10))
        menu.add_command(label="  Export game…", command=lambda: export_dialog(p))
        menu.add_command(label="  Packages…", command=lambda: project_packages_dialog(p))
        for _lab, _fn, _api in HUB_EXT["actions"]:
            def _run(fn=_fn, api=_api):
                try:
                    fn(p)
                except Exception as ex:
                    error(api.name, str(ex))
            menu.add_command(label="  " + _lab, command=_run)
        menu.add_separator()
        menu.add_command(label="  Show in folder", command=lambda: safe(open_in_file_manager, path))
        menu.add_command(label="  Open last engine log", command=lambda: show_log(p))
        menu.add_command(label="  Rename in Hub…", command=lambda: rename(p))
        menu.add_separator()
        menu.add_command(label="  Remove from list", command=lambda: remove(p))
        more._cmd = lambda: menu.tk_popup(more.winfo_rootx(), more.winfo_rooty() + more.winfo_height() + px(4))
        hoverable(card, C["panel"], C["panel_hi"])

        def bind_dbl(w):
            if not isinstance(w, Btn):
                w.bind("<Double-Button-1>", lambda e: launch(p, "editor"))
                for c in w.winfo_children():
                    bind_dbl(c)
        if exists:
            bind_dbl(card)

    def elide(text, f, width):
        fo = font_obj(f)
        if fo.measure(text) <= width:
            return text
        lo, hi = 0, len(text)
        while lo < hi:                      # keep the end of the path, it is the useful part
            mid = (lo + hi) // 2
            if fo.measure("…" + text[mid:]) <= width:
                hi = mid
            else:
                lo = mid + 1
        return "…" + text[lo:]

    def render_projects():
        for w in plist.winfo_children():
            w.destroy()
        q = search_text()
        items = [p for p in cfg.projects if not q or q in (p.get("name", "") + p["path"]).lower()]
        items.sort(key=lambda p: (-(project_mtime(p["path"]) or 0)))
        if not cfg.projects:
            hdr.pack_forget()
            box = tk.Frame(plist, bg=C["bg"])
            box.pack(fill="both", expand=True, pady=px(90))
            tk.Label(box, image=brand(112), bg=C["bg"], bd=0).pack()
            label(box, "No projects yet", 17, "bold").pack(pady=(px(16), px(6)))
            label(box, "Create your first game from a template. It takes ten seconds.", 10,
                  fg=C["muted"]).pack(pady=(0, px(20)))
            Btn(box, "+   New project", lambda: new_project_dialog(), kind="primary", size=11).pack()
            Btn(box, "Start the beginner tutorial", lambda: show("learn"), kind="ghost").pack(pady=px(8))
            return
        if not hdr.winfo_ismapped():
            hdr.pack(fill="x", padx=(px(44), px(38)), pady=(px(6), px(6)), before=plist_outer)
        if not items:
            label(plist, "No project matches your search.", 10, fg=C["muted"]).pack(pady=px(40))
        for p in items:
            project_row(p)
        plist_canvas.yview_moveto(0)

    pp.refresh = render_projects

    def safe(fn, *a):
        try:
            fn(*a)
        except Exception as ex:
            error("Heaven3D Hub", str(ex))

    def launch(p, mode):
        hub_emit("project_opened", p, mode)
        try:
            launcher.launch(p, mode)
            toast(("Opening " if mode == "editor" else "Playing ") + (p.get("name") or ""), "ok")
        except Exception as ex:
            error("Can't start the engine", str(ex))
        render_projects()
        watch_process(p)

    def watch_process(p):
        path = p["path"]

        def check():
            cur = launcher.running.get(path)
            if not cur:
                return
            rc = cur[0].poll()
            if rc is None:
                root.after(1000, check)
                return
            render_projects() if current["page"] == "projects" else None
            if rc not in (0, None) and rc > 0:
                if messagebox.askyesno("The engine closed with an error",
                                       f"Heaven3D exited with code {rc}.\n\nShow the log?", parent=root):
                    show_log(p)
        root.after(1500, check)

    def add_existing():
        d = filedialog.askdirectory(title="Choose a Heaven3D project folder", parent=root,
                                    initialdir=cfg.settings.get("projects_root"))
        if not d:
            return
        if not os.path.isfile(os.path.join(d, PROJECT_FILE)):
            cands = [f for f in os.listdir(d) if f.endswith(".heaven3d")]
            if not cands:
                error("Not a Heaven3D project",
                      f"That folder has no {PROJECT_FILE}.\n\nA Heaven3D project is a folder "
                      f"containing a main.heaven3d scene file.")
                return
            if messagebox.askyesno("Use this scene?",
                                   f"No main.heaven3d here, but '{cands[0]}' was found.\n\n"
                                   f"Make a copy named main.heaven3d so the Hub can open it?",
                                   parent=root):
                shutil.copy(os.path.join(d, cands[0]), os.path.join(d, PROJECT_FILE))
            else:
                return
        cfg.add_project(d, project_meta(d).get("name") or os.path.basename(d))
        render_projects()
        toast("Project added")

    def locate(p):
        d = filedialog.askdirectory(title=f"Where is '{p.get('name')}' now?", parent=root)
        if d and os.path.isfile(os.path.join(d, PROJECT_FILE)):
            p["path"] = os.path.abspath(d)
            cfg.save()
            render_projects()
        elif d:
            error("Not a Heaven3D project", f"That folder has no {PROJECT_FILE}.")

    def remove(p):
        if messagebox.askyesno("Remove from list",
                               f"Remove '{p.get('name')}' from the Hub?\n\n"
                               f"Your files are NOT deleted. Use Add existing to bring it back.", parent=root):
            cfg.remove_project(p["path"])
            render_projects()

    def dialog(title, w, h):
        win = tk.Toplevel(root, bg=C["bg"])
        win.withdraw()
        win.title(title)
        win.transient(root)
        win.configure(bg=C["bg"])
        root.update_idletasks()
        x = root.winfo_rootx() + (root.winfo_width() - px(w)) // 2
        y = root.winfo_rooty() + (root.winfo_height() - px(h)) // 3
        win.geometry(f"{px(w)}x{px(h)}+{max(0, x)}+{max(0, y)}")
        _win_style_titlebar(win, _rgb(C["bg"]), C.get("dark", True))
        win.deiconify()
        return win

    def rename(p):
        win = dialog("Rename", 440, 200)
        win.resizable(False, False)
        label(win, "Name shown in the Hub", 10, "semibold").pack(padx=px(24), pady=(px(22), px(8)), anchor="w")
        v = tk.StringVar(value=p.get("name", ""))
        box, e = entry(win, v, width=34, font_size=11)
        box.pack(padx=px(24), fill="x")
        e.focus_set()
        e.select_range(0, "end")

        def ok(_e=None):
            if v.get().strip():
                p["name"] = v.get().strip()
                cfg.save()
                render_projects()
            win.destroy()
        e.bind("<Return>", ok)
        e.bind("<Escape>", lambda _e: win.destroy())
        row = tk.Frame(win, bg=C["bg"])
        row.pack(fill="x", padx=px(24), pady=px(20))
        Btn(row, "Rename", ok, kind="primary").pack(side="right")
        Btn(row, "Cancel", win.destroy).pack(side="right", padx=px(8))

    def show_log(p):
        lp_ = launcher.last_log(p["path"])
        if not lp_:
            error("No log yet", "This project hasn't been opened from the Hub yet.")
            return
        win = dialog(f"Engine log · {p.get('name')}", 920, 540)
        box = Card(win, C["code"], radius=10)
        box.pack(fill="both", expand=True, padx=px(14), pady=px(14))
        t = tk.Text(box, bg=C["code"], fg=C["code_text"], font=F(9, mono=True), relief="flat", wrap="none",
                    bd=0, highlightthickness=0, padx=px(14), pady=px(12), selectbackground=C["accent"])
        sb = SlimScroll(box, t.yview, C["code"])
        t.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y", pady=px(8))
        t.pack(fill="both", expand=True, padx=px(4), pady=px(4))
        try:
            t.insert("1.0", open(lp_, encoding="utf-8", errors="replace").read()[-200000:])
        except OSError as ex:
            t.insert("1.0", str(ex))
        for tag, pat, col in (("err", "ERROR", C["danger"]), ("warn", "WARN", C["warn"]),
                              ("tb", "Traceback", C["danger"])):
            t.tag_configure(tag, foreground=col)
            start = "1.0"
            while True:
                pos = t.search(pat, start, stopindex="end")
                if not pos:
                    break
                t.tag_add(tag, f"{pos} linestart", f"{pos} lineend")
                start = f"{pos} lineend"
        t.see("end")
        t.config(state="disabled")

    # ---------------------------------------------------- export dialog
    def export_dialog(p):
        path = p["path"]
        players = export_players()
        win = dialog(f"Export · {p.get('name')}", 660, 820)
        win.minsize(px(560), px(600))
        pad = px(26)
        head = tk.Frame(win, bg=C["bg"])
        head.pack(fill="x", padx=pad, pady=(px(22), px(4)))
        tk.Label(head, image=brand(40), bg=C["bg"], bd=0).pack(side="left")
        ht = tk.Frame(head, bg=C["bg"])
        ht.pack(side="left", padx=px(12))
        label(ht, "Export game", 17, "bold").pack(anchor="w")
        label(ht, "A stand-alone copy anyone can play. No Python or install needed.", 10,
              fg=C["muted"]).pack(anchor="w")
        body = Card(win, C["panel"], radius=14)
        body.pack(fill="both", expand=True, padx=pad, pady=(px(14), 0))
        inner = tk.Frame(body, bg=C["panel"])
        inner.pack(fill="both", expand=True, padx=px(22), pady=px(18))

        def caption(t):
            label(inner, t, 8, "semibold", fg=C["muted"], anchor="w").pack(fill="x", pady=(0, px(6)))
        caption("GAME NAME")
        name_var = tk.StringVar(value=p.get("name") or os.path.basename(path))
        nb, ne = entry(inner, name_var, width=30)
        nb.pack(fill="x", pady=(0, px(14)))
        caption("PLATFORMS")
        plat_vars = {}
        prow = tk.Frame(inner, bg=C["panel"])
        prow.pack(fill="x", pady=(0, px(4)))
        missing = []
        for plat, title in (("windows", "Windows  (.exe)"), ("linux", "Linux"), ("macos", "macOS  (.app)")):
            v = tk.BooleanVar(value=plat in players)
            plat_vars[plat] = v
            if plat in players:
                cell = tk.Frame(prow, bg=C["panel"])
                cell.pack(side="left", padx=(0, px(26)))
                Switch(cell, v).pack(side="left")
                label(cell, title, 10).pack(side="left", padx=px(10))
            else:
                missing.append(title.split("  ")[0])
        if missing:
            if "macos" in players:              # macOS edition without the add-on
                note = (f"{' and '.join(missing)}: add the Export-Players download "
                        f"(see runtime/players in your Heaven3D folder).")
            else:
                note = (f"{' and '.join(missing)} export needs the "
                        f"{'macOS edition' if missing == ['macOS'] else 'standalone editions'} of Heaven3D.")
            label(inner, note, 9, fg=C["faint"], anchor="w").pack(fill="x", pady=(px(4), 0))
        tk.Frame(inner, bg=C["panel"], height=px(10)).pack(fill="x")
        caption("WINDOW SIZE")
        srow = tk.Frame(inner, bg=C["panel"])
        srow.pack(fill="x", pady=(0, px(14)))
        w_var, h_var = tk.StringVar(value="1600"), tk.StringVar(value="900")
        b1, _e1 = entry(srow, w_var, width=6)
        b1.pack(side="left")
        label(srow, "×", 11, fg=C["muted"]).pack(side="left", padx=px(8))
        b2, _e2 = entry(srow, h_var, width=6)
        b2.pack(side="left")
        caption("GAME ICON")
        irow = tk.Frame(inner, bg=C["panel"])
        irow.pack(fill="x", pady=(0, px(14)))
        icon_state = {"path": None}
        irow.columnconfigure(0, weight=0)
        _gi = project_meta(path).get("game_icon")
        if _gi and os.path.isfile(os.path.join(path, *_gi.split("/"))):
            icon_state["path"] = os.path.join(path, *_gi.split("/"))
        icon_prev = tk.Frame(irow, bg=C["panel"])
        icon_prev.pack(side="left", anchor="n")
        icon_txt = tk.Frame(irow, bg=C["panel"])
        icon_txt.pack(side="left", padx=px(12))
        icon_name = label(icon_txt, "", 10, "semibold", anchor="w")
        icon_name.pack(anchor="w")
        label(icon_txt, "Used for the .exe, the Mac app, the window and the taskbar.", 9,
              fg=C["muted"], anchor="w").pack(anchor="w")

        def show_icon():
            for w in icon_prev.winfo_children():
                w.destroy()
            pth = icon_state["path"]
            img = load_thumb(pth, 44, C["panel"], radius=9) if pth else None
            if pth is None:
                tk.Label(icon_prev, image=brand(44), bg=C["panel"], bd=0).pack()
            elif img is not None:
                lab = tk.Label(icon_prev, image=img, bg=C["panel"], bd=0)
                lab.image = img
                lab.pack()
            else:                                    # .jpg/.bmp without Pillow: no preview
                n = px(44)
                tk.Label(icon_prev, image=rrect(n, n, px(9), C["raised"], C["panel"]),
                         text=os.path.splitext(pth)[1][1:].upper(), compound="center", fg=C["muted"],
                         font=F(9, "bold"), bg=C["panel"], bd=0).pack()
            icon_name.configure(text=os.path.basename(pth) if pth else "Heaven3D logo (default)")

        def _set_meta_icon(rel):
            mp = os.path.join(path, PROJECT_META)
            try:
                meta = json.load(open(mp, encoding="utf-8")) if os.path.isfile(mp) else {}
            except ValueError:
                meta = {}
            if rel:
                meta["game_icon"] = rel
            else:
                meta.pop("game_icon", None)
            with open(mp, "w", encoding="utf-8") as fh:
                json.dump(meta, fh, indent=2)

        def choose_icon():
            f = filedialog.askopenfilename(parent=win, title="Choose your game's icon",
                                           filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp"), ("PNG", "*.png"),
                                                      ("JPEG", "*.jpg *.jpeg"), ("Bitmap", "*.bmp")])
            if not f:
                return
            ext = os.path.splitext(f)[1].lower()
            if ext not in (".png", ".jpg", ".jpeg", ".bmp"):
                error("Game icon", "Please choose a .png, .jpg or .bmp image.")
                return
            adir = os.path.join(path, "assets")
            os.makedirs(adir, exist_ok=True)
            for old in os.listdir(adir):
                if old.lower().startswith("game_icon."):
                    try:
                        os.remove(os.path.join(adir, old))
                    except OSError:
                        pass
            dest = os.path.join(adir, "game_icon" + (".jpg" if ext == ".jpeg" else ext))
            shutil.copy2(f, dest)                    # travels with the project
            _set_meta_icon("assets/" + os.path.basename(dest))
            icon_state["path"] = dest
            show_icon()

        def reset_icon():
            _set_meta_icon(None)
            icon_state["path"] = None
            show_icon()
        ibtns = tk.Frame(icon_txt, bg=C["panel"])
        ibtns.pack(anchor="w", pady=(px(6), 0))
        Btn(ibtns, "Choose image…", choose_icon, size=9, padx=10, pady=5).pack(side="left")
        Btn(ibtns, "Use Heaven3D logo", reset_icon, kind="ghost", size=9, padx=10, pady=5).pack(side="left", padx=px(4))
        show_icon()
        caption("SAVE TO")
        out_var = tk.StringVar(value=cfg.settings.get("export_root") or os.path.join(
            os.path.dirname(cfg.settings.get("projects_root") or _default_projects_root()), "Heaven3D Builds"))
        orow = tk.Frame(inner, bg=C["panel"])
        orow.pack(fill="x", pady=(0, px(12)))
        Btn(orow, "Browse", lambda: out_var.set(filedialog.askdirectory(parent=win, initialdir=out_var.get())
                                                 or out_var.get())).pack(side="right", padx=(px(8), 0))
        ob, _oe = entry(orow, out_var, width=20)
        ob.pack(side="left", fill="x", expand=True)
        zip_var = tk.BooleanVar(value=True)
        zrow = tk.Frame(inner, bg=C["panel"])
        zrow.pack(fill="x", pady=(px(2), px(12)))
        Switch(zrow, zip_var).pack(side="left")
        label(zrow, "Also make a .zip of each build (ready to share)", 10).pack(side="left", padx=px(10))
        logbox = tk.Text(inner, height=7, bg=C["code"], fg=C["code_text"], font=F(9, mono=True), relief="flat",
                         bd=0, highlightthickness=0, padx=px(10), pady=px(8), wrap="word", state="disabled")
        logbox.pack(fill="both", expand=True)
        foot = tk.Frame(win, bg=C["bg"])
        foot.pack(fill="x", padx=pad, pady=px(18))
        status = label(foot, "", 10, fg=C["muted"], anchor="w")
        status.pack(side="left", fill="x", expand=True)
        go = Btn(foot, "Export", None, kind="primary", size=11, padx=22, pady=9)
        go.pack(side="right")
        close = Btn(foot, "Close", win.destroy, pady=9)
        close.pack(side="right", padx=px(8))
        state = {"proc": None, "out": None}

        def log(line):
            logbox.configure(state="normal")
            logbox.insert("end", line + "\n")
            logbox.see("end")
            logbox.configure(state="disabled")

        def finished(rc, results):
            state["proc"] = None
            go.set_enabled(True)
            if rc == 0:
                status.configure(text="Done! Your game is ready.", fg=C["ok"])
                hub_emit("export_finished", p)
                go.set_text("Show folder")
                go._cmd = lambda: safe(open_in_file_manager, state["out"])
                toast("Export finished")
            else:
                status.configure(text="Export failed - see the messages above.", fg=C["danger"])

        def run():
            targets = [k for k, v in plat_vars.items() if v.get()]
            if not targets:
                status.configure(text="Pick at least one platform.", fg=C["warn"])
                return
            try:
                wv, hv = max(320, int(w_var.get())), max(240, int(h_var.get()))
            except ValueError:
                status.configure(text="Window size must be numbers.", fg=C["warn"])
                return
            err = validate_project_name(name_var.get())
            if err:
                status.configure(text=err, fg=C["warn"])
                return
            out = out_var.get().strip()
            cfg.settings["export_root"] = out
            cfg.save()
            state["out"] = out
            engine = cfg.settings.get("engine_path") or ""
            py = cfg.settings.get("python_path") or _detect_python()
            iso = (["-E", "-s"] if py == _bundled_python() else []) + ["-u"]
            cmd = [py] + iso + [engine, "--export-game", os.path.join(path, PROJECT_FILE),
                                "--targets", ",".join(targets), "--out", out, "--name", name_var.get().strip(),
                                "--width", str(wv), "--height", str(hv)] + ([] if zip_var.get() else ["--no-zip"]) + \
                (["--icon", icon_state["path"]] if icon_state["path"] else [])
            go.set_enabled(False)
            status.configure(text="Exporting… this can take a minute.", fg=C["muted"])
            log("Exporting " + ", ".join(t.title() for t in targets) + " build(s)…")
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                        encoding="utf-8", errors="replace", cwd=path,
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WIN else 0)
            except Exception as ex:
                log(str(ex))
                finished(1, [])
                return
            state["proc"] = proc

            def reader():
                results = []
                for line in proc.stdout:
                    line = line.rstrip()
                    if line.startswith("[Export]") or line.startswith("[Portable]"):
                        if " RESULT " in line:
                            results.append(line)
                            continue
                        root.after(0, lambda l=line: log(l.replace("[Export] ", "").replace("[Portable] ", "  ")))
                rc = proc.wait()
                root.after(0, lambda: finished(rc, results))
            threading.Thread(target=reader, daemon=True).start()
        go._cmd = run

        def on_close():
            if state["proc"] is not None:
                if not messagebox.askyesno("Export running", "Stop the export?", parent=win):
                    return
                try:
                    state["proc"].kill()
                except Exception:
                    pass
            win.destroy()
        close._cmd = on_close
        win.protocol("WM_DELETE_WINDOW", on_close)

    # ---------------------------------------------------- new project dialog
    def new_project_dialog(preselect=None, preset_name=None, on_created=None):
        tpls = list_templates()
        if not tpls:
            error("No templates", f"The templates folder is missing:\n{TEMPLATES_DIR}")
            return
        win = dialog("New project", 1020, 640)
        win.minsize(px(880), px(560))
        win.grab_set()
        top = tk.Frame(win, bg=C["bg"])
        top.pack(fill="x", padx=px(28), pady=(px(22), px(2)))
        label(top, "New project", 18, "bold").pack(side="left")
        label(win, "Pick a template. You can change everything later in the editor.", 10,
              fg=C["muted"]).pack(anchor="w", padx=px(29))
        body = tk.Frame(win, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=px(20), pady=px(16))
        right = Card(body, C["panel"], radius=14, width=px(350))
        right.pack(side="right", fill="y", padx=(px(12), px(8)))
        right.pack_propagate(False)
        left_o, left, _c = scrollable(body, C["bg"])
        left_o.pack(side="left", fill="both", expand=True)
        sel = {"id": preselect or tpls[0]["id"]}
        cards = {}

        def card_bg(tid):
            return C["sel"] if tid == sel["id"] else C["panel"]

        def paint():
            for tid, (card, title_lbl, check) in cards.items():
                on = tid == sel["id"]
                card.set_bg(card_bg(tid))
                title_lbl.configure(fg=C["text"])
                if on:
                    check.pack(side="right")
                else:
                    check.pack_forget()
            t = next(t for t in tpls if t["id"] == sel["id"])
            d_title.configure(text=t["title"])
            d_desc.configure(text=t.get("desc", ""))

        grid = tk.Frame(left, bg=C["bg"])
        grid.pack(fill="both", expand=True)
        for i, t in enumerate(tpls):
            card = Card(grid, C["panel"], outer=C["bg"], radius=12, cursor="hand2")
            card.grid(row=i // 2, column=i % 2, padx=px(6), pady=px(6), sticky="nsew")
            inner = tk.Frame(card, bg=C["panel"])
            inner.pack(fill="both", expand=True, padx=px(10), pady=px(10))
            if load_thumb(t["thumb"], 268) is not None:
                Thumb(inner, t["thumb"], 268).pack()
            else:
                placeholder(inner, 268, 150).pack()
            row = tk.Frame(inner, bg=C["panel"])
            row.pack(fill="x", pady=(px(10), px(2)))
            lt = label(row, t["title"], 11, "semibold", anchor="w")
            lt.pack(side="left", padx=px(2))
            chk = IconLabel(row, "check", C["accent_text"], 18)
            cards[t["id"]] = (card, lt, chk)

            def bind(w, tid=t["id"]):
                w.bind("<Button-1>", lambda e: (sel.update(id=tid), paint()))
                w.bind("<Double-Button-1>", lambda e: (sel.update(id=tid), paint(), create()))
                for c in w.winfo_children():
                    if not getattr(c, "_corner", False):
                        bind(c, tid)
            bind(card)
            hoverable(card, lambda tid=t["id"]: card_bg(tid),
                      lambda tid=t["id"]: C["sel_hi"] if tid == sel["id"] else C["panel_hi"])
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)

        pad = px(22)
        d_title = label(right, "", 14, "bold", anchor="w", wraplength=px(300), justify="left")
        d_title.pack(fill="x", padx=pad, pady=(pad, px(6)))
        d_desc = label(right, "", 10, fg=C["muted"], anchor="w", wraplength=px(300), justify="left")
        d_desc.pack(fill="x", padx=pad)
        tk.Frame(right, bg=C["line"], height=1).pack(fill="x", padx=pad, pady=px(18))

        def field(caption, var):
            label(right, caption, 8, "semibold", fg=C["muted"], anchor="w").pack(fill="x", padx=pad)
            box, e = entry(right, var, width=20)
            box.pack(fill="x", padx=pad, pady=(px(6), px(14)))
            return box, e
        root_dir = cfg.settings.get("projects_root") or _default_projects_root()
        base = preset_name or "My Game"
        name = base
        k = 2
        while os.path.exists(os.path.join(root_dir, name)):
            name = f"{base} {k}"
            k += 1
        name_var = tk.StringVar(value=name)
        loc_var = tk.StringVar(value=root_dir)
        _nb, ne = field("PROJECT NAME", name_var)
        label(right, "LOCATION", 8, "semibold", fg=C["muted"], anchor="w").pack(fill="x", padx=pad)
        loc_row = tk.Frame(right, bg=C["panel"])
        loc_row.pack(fill="x", padx=pad, pady=(px(6), px(14)))
        Btn(loc_row, "Browse",
            lambda: loc_var.set(filedialog.askdirectory(parent=win, initialdir=loc_var.get()) or loc_var.get()),
            padx=12).pack(side="right", padx=(px(8), 0))
        lb, _le = entry(loc_row, loc_var, width=14)
        lb.pack(side="left", fill="x", expand=True)
        open_var = tk.BooleanVar(value=True)
        sw_row = tk.Frame(right, bg=C["panel"])
        sw_row.pack(fill="x", padx=pad, pady=(px(2), 0))
        Switch(sw_row, open_var).pack(side="left")
        lab_open = label(sw_row, "Open in the editor after creating", 10, cursor="hand2")
        lab_open.pack(side="left", padx=px(10))
        msg = label(right, "", 9, fg=C["danger"], wraplength=px(300), justify="left", anchor="w")
        msg.pack(fill="x", padx=pad, pady=(px(14), 0))
        path_lbl = label(right, "", 9, fg=C["faint"], wraplength=px(300), justify="left", anchor="w")
        path_lbl.pack(fill="x", padx=pad)
        sw_widget = sw_row.winfo_children()[0]
        lab_open.bind("<Button-1>", lambda e: (open_var.set(not open_var.get()), sw_widget.set_outer(C["panel"])))

        def upd(*_):
            err = validate_project_name(name_var.get())
            dest = os.path.join(loc_var.get(), name_var.get().strip())
            if not err and os.path.exists(dest) and os.listdir(dest):
                err = "A folder with that name already exists here."
            msg.configure(text=err or "")
            path_lbl.configure(text="" if err else f"Will be created at\n{dest}")
            go.set_enabled(not err)
        bottom = tk.Frame(right, bg=C["panel"])
        bottom.pack(side="bottom", fill="x", padx=pad, pady=pad)
        go = Btn(bottom, "Create project", lambda: create(), kind="primary", size=11, padx=18, pady=9)
        go.pack(side="right")
        Btn(bottom, "Cancel", win.destroy, pady=9).pack(side="right", padx=px(8))
        name_var.trace_add("write", upd)
        loc_var.trace_add("write", upd)

        def create(_e=None):
            upd()
            if not go._enabled:
                return
            try:
                p = create_project(cfg, name_var.get(), sel["id"], loc_var.get())
                cfg.settings["projects_root"] = loc_var.get()
                cfg.save()
            except Exception as ex:
                msg.configure(text=str(ex))
                return
            win.grab_release()
            win.destroy()
            show("projects")
            toast(f"Created '{p['name']}'")
            hub_emit("project_created", p)
            if on_created:
                on_created(p)
            if open_var.get():
                launch(p, "editor")
        ne.bind("<Return>", create)
        win.bind("<Escape>", lambda e: win.destroy())
        ne.focus_set()
        ne.select_range(0, "end")
        paint()
        upd()

    # ================================================================ LEARN
    lp = tk.Frame(content, bg=C["bg"])
    pages["learn"] = lp
    page_header(lp, "Learn", "Offline step-by-step tutorials. Work through them in order; each one takes 5 to 20 minutes.")
    learn_body = tk.Frame(lp, bg=C["bg"])
    learn_body.pack(fill="both", expand=True, padx=(px(20), px(28)), pady=(0, px(20)))
    tl_outer, tl_list, _tlc = scrollable(learn_body, C["bg"])
    tl_outer.configure(width=px(316))
    tl_outer.pack(side="left", fill="y")
    tl_outer.pack_propagate(False)
    reader_box = Card(learn_body, C["panel"], outer=C["bg"], radius=14)
    reader_box.pack(side="left", fill="both", expand=True, padx=(px(12), 0))
    rbar = tk.Frame(reader_box, bg=C["panel"])
    rbar.pack(fill="x", padx=px(22), pady=(px(16), 0))
    r_meta = tk.Frame(rbar, bg=C["panel"])
    r_meta.pack(side="left")
    r_done = Btn(rbar, "Mark as done", None, padx=12, pady=6, size=9)
    r_done.pack(side="right")
    r_practice = Btn(rbar, "Create practice project", None, kind="primary", padx=12, pady=6, size=9)
    r_practice.pack(side="right", padx=px(8))
    tk.Frame(reader_box, bg=C["line"], height=1).pack(fill="x", padx=px(22), pady=(px(14), 0))
    txt_frame = tk.Frame(reader_box, bg=C["panel"])
    txt_frame.pack(fill="both", expand=True, padx=px(8), pady=(px(4), px(14)))
    reader = tk.Text(txt_frame, bg=C["panel"], fg=C["text"], wrap="word", relief="flat", bd=0,
                     padx=px(28), pady=px(14), font=F(11), spacing1=px(2), spacing2=px(3), spacing3=px(4),
                     cursor="arrow", insertwidth=0, highlightthickness=0, selectbackground=C["accent_soft"],
                     selectforeground=C["text"])
    rsb = SlimScroll(txt_frame, reader.yview, C["panel"])
    reader.configure(yscrollcommand=rsb.set)
    rsb.pack(side="right", fill="y")
    reader.pack(side="left", fill="both", expand=True)
    reader.tag_configure("h1", font=F(20, "bold"), spacing1=px(4), spacing3=px(12))
    reader.tag_configure("h2", font=F(14, "semibold"), foreground=C["accent_text"], spacing1=px(18), spacing3=px(8))
    reader.tag_configure("h3", font=F(12, "semibold"), spacing1=px(12), spacing3=px(4))
    reader.tag_configure("p", spacing3=px(10), foreground=C["body"])
    reader.tag_configure("bold", font=F(11, "semibold"), foreground=C["text"])
    reader.tag_configure("ital", font=(base_family, 11, "italic"))
    reader.tag_configure("icode", font=F(10, mono=True), foreground=C["icode"], background=C["code"])
    reader.tag_configure("code", font=F(10, mono=True), foreground=C["code_text"], background=C["code"],
                         lmargin1=px(40), lmargin2=px(40), rmargin=px(30), spacing1=0, spacing2=px(2), spacing3=0)
    reader.tag_configure("codepad", font=F(5), background=C["code"], lmargin1=px(40), rmargin=px(30))
    reader.tag_configure("bullet", lmargin1=px(28), lmargin2=px(46), spacing3=px(5), foreground=C["body"])
    reader.tag_configure("bdot", foreground=C["accent_text"])
    reader.tag_configure("num", lmargin1=px(28), lmargin2=px(52), spacing3=px(6), foreground=C["body"])
    reader.tag_configure("numlab", foreground=C["accent_text"], font=F(11, "semibold"))
    reader.tag_configure("key", font=F(9, "bold", mono=True), background=C["key_bg"], foreground=C["key_fg"])
    for k, col, soft in (("tip", C["ok"], C["tip_bg"]), ("warn", C["warn"], C["warn_bg"]), ("note", C["accent_text"], C["note_bg"])):
        reader.tag_configure(k, lmargin1=px(40), lmargin2=px(40), rmargin=px(36), background=soft,
                             spacing1=px(10), spacing3=px(10), foreground=C["callout"])
        reader.tag_configure(k + "_label", foreground=col, font=F(9, "bold"))
    reader.tag_configure("hr", font=F(3), background=C["line"], spacing1=px(8), spacing3=px(8))

    tutorials = []
    t_state = {"current": None}

    INLINE = re.compile(r"(`[^`]+`|\*\*[^*]+\*\*|\[\[[^\]]+\]\]|\*[^*\s][^*]*\*)")

    def emit_inline(text, base_tags):
        for part in INLINE.split(text):
            if not part:
                continue
            if part.startswith("`") and part.endswith("`"):
                reader.insert("end", " " + part[1:-1] + " ", base_tags + ("icode",))
            elif part.startswith("**") and part.endswith("**"):
                reader.insert("end", part[2:-2], base_tags + ("bold",))
            elif part.startswith("[[") and part.endswith("]]"):
                reader.insert("end", f" {part[2:-2]} ", base_tags + ("key",))
            elif part.startswith("*") and part.endswith("*") and len(part) > 2:
                reader.insert("end", part[1:-1], base_tags + ("ital",))
            else:
                reader.insert("end", part, base_tags)

    def copy_text(s):
        root.clipboard_clear()
        root.clipboard_append(s)
        toast("Copied to clipboard")

    def render_markdown(md):
        reader.config(state="normal")
        reader.delete("1.0", "end")
        lines = md.splitlines()
        i = 0
        num = 0
        while i < len(lines):
            ln = lines[i]
            s = ln.strip()
            if s.startswith("```"):
                code = []
                i += 1
                while i < len(lines) and not lines[i].strip().startswith("```"):
                    code.append(lines[i])
                    i += 1
                i += 1
                block = "\n".join(code)
                b = Btn(reader, "Copy", lambda t=block: copy_text(t), size=8, padx=10, pady=3, outer=C["panel"])
                reader.window_create("end", window=b, padx=px(40), pady=px(4))
                reader.insert("end", "\n")
                reader.insert("end", " \n", ("codepad",))
                reader.insert("end", block + "\n", ("code",))
                reader.insert("end", " \n", ("codepad",))
                reader.insert("end", "\n")
                continue
            if s.startswith("> "):
                kind, body = "note", s[2:]
                for k, lab in (("tip", "TIP"), ("warn", "WARNING"), ("warn", "WATCH OUT"), ("note", "NOTE")):
                    if body.upper().startswith(lab + ":"):
                        kind, body = k, body[len(lab) + 1:].strip()
                        label_ = {"tip": "TIP   ", "warn": "WATCH OUT   ", "note": "GOOD TO KNOW   "}[k]
                        break
                else:
                    label_ = ""
                while i + 1 < len(lines) and lines[i + 1].strip().startswith("> "):
                    i += 1
                    body += " " + lines[i].strip()[2:]
                if label_:
                    reader.insert("end", label_, (kind, kind + "_label"))
                emit_inline(body, (kind,))
                reader.insert("end", "\n", (kind,))
                reader.insert("end", "\n")
                i += 1
                continue
            if s.startswith("### "):
                reader.insert("end", s[4:] + "\n", ("h3",))
            elif s.startswith("## "):
                reader.insert("end", s[3:] + "\n", ("h2",))
                num = 0
            elif s.startswith("# "):
                reader.insert("end", s[2:] + "\n", ("h1",))
            elif s in ("---", "***"):
                reader.insert("end", " \n", ("hr",))
            elif re.match(r"^[-*] ", s):
                reader.insert("end", "•   ", ("bullet", "bdot"))
                emit_inline(s[2:], ("bullet",))
                reader.insert("end", "\n", ("bullet",))
            elif re.match(r"^\d+\. ", s):
                num += 1
                n, rest = s.split(". ", 1)
                reader.insert("end", f"{n}.   ", ("num", "numlab"))
                emit_inline(rest, ("num",))
                reader.insert("end", "\n", ("num",))
            elif not s:
                reader.insert("end", "\n")
            else:
                para = s
                while i + 1 < len(lines) and lines[i + 1].strip() and not re.match(
                        r"^(#|```|> |[-*] |\d+\. |---)", lines[i + 1].strip()):
                    i += 1
                    para += " " + lines[i].strip()
                emit_inline(para, ("p",))
                reader.insert("end", "\n", ("p",))
            i += 1
        reader.config(state="disabled")
        reader.yview_moveto(0)

    def open_tutorial(t):
        t_state["current"] = t
        render_markdown(t["body"])
        done = t["id"] in cfg.data.get("tutorials_done", [])
        for w in r_meta.winfo_children():
            w.destroy()
        if t.get("level"):
            Pill(r_meta, t["level"], C["accent_text"], C["accent_soft"], size=9).pack(side="left", padx=(0, px(6)))
        if t.get("minutes"):
            Pill(r_meta, t["minutes"] + " min", C["muted"], C["raised"], size=9).pack(side="left")
        r_done.set_text("✓  Completed" if done else "Mark as done", fg=C["ok"] if done else None)
        r_done._cmd = lambda: toggle_done(t)
        if t.get("template"):
            r_practice._cmd = lambda: new_project_dialog(t["template"], "Practice - " + t["title"][:40])
            r_practice.pack(side="right", padx=px(8))
        else:
            r_practice.pack_forget()
        render_tutorial_list()

    def toggle_done(t):
        done = cfg.data.setdefault("tutorials_done", [])
        if t["id"] in done:
            done.remove(t["id"])
        else:
            done.append(t["id"])
            toast("Nice work! Tutorial completed")
        cfg.save()
        open_tutorial(t)

    def num_badge(parent, text, done, on):
        n = px(26)
        f = F(9, "bold")
        fill = C["ok"] if done else (C["accent"] if on else C["raised"])
        fg = "#06140d" if done else ("#ffffff" if on else C["muted"])
        lab = tk.Label(parent, text=text, compound="center", fg=fg, font=f, bd=0, padx=0, pady=0)

        def regen(outer):
            lab._outer = outer
            lab.configure(image=rrect(n, n, n / 2.0, fill, outer), bg=outer)
        lab._regen = regen
        regen(parent.cget("bg"))
        return lab

    def render_tutorial_list():
        for w in tl_list.winfo_children():
            w.destroy()
        done = set(cfg.data.get("tutorials_done", []))
        if tutorials:
            n = sum(1 for t in tutorials if t["id"] in done)
            pr = tk.Frame(tl_list, bg=C["bg"])
            pr.pack(fill="x", padx=(px(12), px(10)), pady=(0, px(12)))
            top = tk.Frame(pr, bg=C["bg"])
            top.pack(fill="x")
            label(top, "Your progress", 9, "semibold", fg=C["muted"]).pack(side="left")
            label(top, f"{n} / {len(tutorials)}", 9, "semibold", fg=C["ok"] if n else C["muted"]).pack(side="right")
            tk.Label(pr, image=progress_img(n / max(1, len(tutorials)), px(276), C["bg"]), bg=C["bg"],
                     bd=0).pack(anchor="w", pady=(px(8), 0))
        else:
            label(tl_list, "No tutorials found in\n" + TUTORIALS_DIR, 9, fg=C["muted"], justify="left").pack(
                padx=px(8), pady=px(20))
        for idx, t in enumerate(tutorials):
            on = t_state["current"] is t
            bg = C["sel"] if on else C["bg"]
            card = Card(tl_list, bg, outer=C["bg"], radius=10, cursor="hand2")
            card.pack(fill="x", padx=(px(6), px(4)), pady=px(2))
            row = tk.Frame(card, bg=bg)
            row.pack(fill="x", padx=px(10), pady=px(10))
            is_done = t["id"] in done
            num_badge(row, "✓" if is_done else str(idx + 1), is_done, on).pack(side="left", anchor="n")
            txt = tk.Frame(row, bg=bg)
            txt.pack(side="left", fill="x", expand=True, padx=(px(12), 0))
            label(txt, t["title"], 10, "semibold", fg=C["text"] if on or not is_done else C["dim_title"],
                  anchor="w", wraplength=px(210), justify="left").pack(fill="x")
            info = "  ·  ".join(x for x in (t.get("level"), (t.get("minutes") + " min") if t.get("minutes") else "") if x)
            label(txt, info, 8, fg=C["muted"], anchor="w").pack(fill="x", pady=(px(2), 0))

            def bind(w, tt=t):
                w.bind("<Button-1>", lambda e: open_tutorial(tt))
                for c in w.winfo_children():
                    if not getattr(c, "_corner", False):
                        bind(c, tt)
            bind(card)
            hoverable(card, lambda o=on: C["sel"] if o else C["bg"],
                      lambda o=on: C["sel_hi"] if o else C["panel"])

    def refresh_learn():
        tutorials[:] = load_tutorials()
        if t_state["current"] is None and tutorials:
            done = set(cfg.data.get("tutorials_done", []))
            nxt = next((t for t in tutorials if t["id"] not in done), tutorials[0])
            open_tutorial(nxt)
        else:
            render_tutorial_list()
    lp.refresh = refresh_learn

    # ================================================================ TEMPLATES
    tp = tk.Frame(content, bg=C["bg"])
    pages["templates"] = tp
    page_header(tp, "Templates", "Starting points for new projects. Pick one to create a project from it.")
    tg_outer, tg, _tgc = scrollable(tp, C["bg"])
    tg_outer.pack(fill="both", expand=True, padx=(px(22), px(8)), pady=(0, px(16)))

    def refresh_templates():
        for w in tg.winfo_children():
            w.destroy()
        for i, t in enumerate(list_templates()):
            card = Card(tg, C["panel"], outer=C["bg"], radius=14)
            card.grid(row=i // 2, column=i % 2, padx=px(8), pady=px(8), sticky="nsew")
            inner = tk.Frame(card, bg=C["panel"])
            inner.pack(fill="both", expand=True, padx=px(14), pady=px(14))
            if load_thumb(t["thumb"], 372, radius=10) is not None:
                Thumb(inner, t["thumb"], 372, radius=10).pack()
            label(inner, t["title"], 13, "semibold", anchor="w").pack(fill="x", pady=(px(14), px(4)))
            label(inner, t.get("desc", ""), 10, fg=C["muted"], anchor="w", wraplength=px(370),
                  justify="left").pack(fill="x", pady=(0, px(14)))
            Btn(inner, "Use this template", lambda tid=t["id"], tt=t["title"]: new_project_dialog(tid, tt),
                kind="primary").pack(anchor="w")
            hoverable(card, C["panel"], C["panel_hi"])
        tg.columnconfigure(0, weight=1)
        tg.columnconfigure(1, weight=1)
    tp.refresh = refresh_templates


    # ================================================================ PACKAGES
    CAT_COLORS = {"Theme": "#8b5cf6", "Physics": "#14b8a6", "Terrain": "#22a565", "Gameplay": "#e0533d",
                  "Tools": "#3b82f6", "Audio": "#d946ef", "Templates": "#f59e0b"}
    kp = tk.Frame(content, bg=C["bg"])
    pages["packages"] = kp
    khead = page_header(kp, "Packages", "Add features to the Hub, the editor and your games. Packages are plain Python.")
    kact = tk.Frame(khead, bg=C["bg"])
    kact.pack(side="right")
    Btn(kact, "Install from file", lambda: install_from_file()).pack(side="left", padx=(0, px(8)))
    Btn(kact, "+   Create package", lambda: create_package_dialog(), kind="primary").pack(side="left")
    restart_bar = Card(kp, C["accent_soft"], outer=C["bg"], radius=10)
    rb_in = tk.Frame(restart_bar, bg=C["accent_soft"])
    rb_in.pack(fill="x", padx=px(16), pady=px(10))
    label(rb_in, "Restart the Hub to apply the package changes.", 10, "semibold", fg=C["accent_text"]).pack(side="left")
    Btn(rb_in, "Restart now", lambda: restart_hub(), kind="primary", size=9, pady=5).pack(side="right")
    trust = Card(kp, C["warn_soft"], outer=C["bg"], radius=10)
    tr_in = tk.Frame(trust, bg=C["warn_soft"])
    tr_in.pack(fill="x", padx=px(16), pady=px(9))
    label(tr_in, "Packages run Python code on your computer. Only install packages from people you trust."
          + ("   (Safe mode: packages are switched off.)" if SAFE_MODE else ""), 9, fg=C["warn"]).pack(side="left")
    trust.pack(fill="x", padx=(px(32), px(40)), pady=(0, px(8)))
    klist_o, klist, _kc = scrollable(kp, C["bg"])
    klist_o.pack(fill="both", expand=True, padx=(px(20), px(8)), pady=(0, px(16)))

    def restart_hub():
        args = [sys.executable] + (["-E", "-s"] if sys.flags.ignore_environment else []) + \
               [os.path.abspath(__file__)] + [a for a in sys.argv[1:] if a == "--safe-mode"]
        try:
            subprocess.Popen(args, cwd=HERE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WIN else 0)
        except Exception as ex:
            error("Restart failed", str(ex))
            return
        root.after(200, root.destroy)

    def need_restart():
        HUB_EXT["restart"] = True
        if not restart_bar.winfo_ismapped():
            restart_bar.pack(fill="x", padx=(px(32), px(40)), pady=(0, px(8)), before=trust)

    def pkg_tile(parent, m, size=46):
        ip = os.path.join(m["dir"], m.get("icon", "icon.png"))
        if os.path.isfile(ip):
            img = load_thumb(ip, size, parent.cget("bg"), radius=10)
            if img is not None:
                lab = tk.Label(parent, image=img, bg=parent.cget("bg"), bd=0)
                lab.image = img
                return lab
        col = CAT_COLORS.get(m.get("category", ""), C["accent"])
        n = px(size)
        return tk.Label(parent, image=rrect(n, n, px(12), col, parent.cget("bg")), text=m["name"][:1].upper(),
                        compound="center", fg="#ffffff", font=F(16, "bold"), bg=parent.cget("bg"), bd=0)

    def target_pills(parent, m):
        row = tk.Frame(parent, bg=parent.cget("bg"))
        for t in PKG_TARGETS:
            if t in m["targets"]:
                Pill(row, {"hub": "Hub", "editor": "Editor", "game": "Game"}[t],
                     C["accent_text"], C["accent_soft"]).pack(side="left", padx=(0, px(6)))
        return row

    def package_card(m, installed):
        card = Card(klist, C["panel"], outer=C["bg"], radius=12)
        card.pack(fill="x", pady=px(5), padx=(px(12), px(6)))
        body = tk.Frame(card, bg=C["panel"])
        body.pack(fill="x", padx=px(16), pady=px(14))
        pkg_tile(body, m).pack(side="left", anchor="n")
        mid = tk.Frame(body, bg=C["panel"])
        mid.pack(side="left", fill="x", expand=True, padx=(px(14), px(10)))
        top = tk.Frame(mid, bg=C["panel"])
        top.pack(fill="x")
        label(top, m["name"], 12, "semibold").pack(side="left")
        label(top, "  v" + str(m.get("version", "")), 9, fg=C["faint"]).pack(side="left", pady=(px(3), 0))
        meta = "  ·  ".join(x for x in (("by " + m["author"]) if m.get("author") else "", m.get("category", "")) if x)
        if meta:
            label(mid, meta, 9, fg=C["muted"], anchor="w").pack(fill="x")
        label(mid, m.get("description", ""), 10, fg=C["body"], anchor="w", justify="left",
              wraplength=px(560)).pack(fill="x", pady=(px(5), px(7)))
        pills = target_pills(mid, m)
        pills.pack(fill="x")
        if m.get("requires"):
            label(mid, "Needs: " + ", ".join(m["requires"]), 9, fg=C["faint"], anchor="w").pack(fill="x", pady=(px(5), 0))
        if installed and m["id"] in HUB_EXT["errors"]:
            label(mid, "Problem: " + HUB_EXT["errors"][m["id"]], 9, fg=C["danger"], anchor="w",
                  wraplength=px(560), justify="left").pack(fill="x", pady=(px(5), 0))
        right = tk.Frame(body, bg=C["panel"])
        right.pack(side="right", anchor="n")
        if installed:
            sw_row = tk.Frame(right, bg=C["panel"])
            sw_row.pack(anchor="e")
            var = tk.BooleanVar(value=is_enabled(m["id"]))
            state_lbl = label(sw_row, "On" if var.get() else "Off", 9, "semibold",
                              fg=C["ok"] if var.get() else C["faint"])

            def toggled(*_a, m=m, var=var, state_lbl=state_lbl):
                set_enabled(m["id"], var.get())
                state_lbl.configure(text="On" if var.get() else "Off", fg=C["ok"] if var.get() else C["faint"])
                if "hub" in m["targets"]:
                    need_restart()
                elif var.get():
                    toast(f"{m['name']} is on: it loads the next time you open the editor")
            var.trace_add("write", toggled)
            state_lbl.pack(side="left", padx=(0, px(8)))
            Switch(sw_row, var).pack(side="left")
            btns = tk.Frame(right, bg=C["panel"])
            btns.pack(anchor="e", pady=(px(10), 0))
            if "editor" in m["targets"] or "game" in m["targets"]:
                ab = Btn(btns, "Add to project", None, size=9, padx=10, pady=5)
                ab.pack(side="left", padx=(0, px(4)))
                ab._cmd = lambda m=m, ab=ab: project_picker(m, ab)
            more = Btn(btns, "•••", None, kind="ghost", padx=10, size=9)
            more.pack(side="left")
            menu = tk.Menu(root, tearoff=0, bg=C["raised"], fg=C["text"], activebackground=C["accent"],
                           activeforeground="#ffffff", bd=0, relief="flat", font=F(10))
            menu.add_command(label="  Show folder", command=lambda m=m: safe(open_in_file_manager, m["dir"]))
            menu.add_command(label="  Export as .h3dpkg…", command=lambda m=m: export_pkg(m))
            menu.add_separator()
            menu.add_command(label="  Uninstall", command=lambda m=m: uninstall(m))
            more._cmd = lambda menu=menu, more=more: menu.tk_popup(more.winfo_rootx(),
                                                                    more.winfo_rooty() + more.winfo_height() + px(4))
        else:
            Btn(right, "Install", lambda m=m: install_bundled(m), kind="primary", size=10).pack(anchor="e")
        hoverable(card, C["panel"], C["panel_hi"])

    def section_title(text):
        label(klist, text, 8, "semibold", fg=C["faint"], anchor="w").pack(fill="x", padx=px(16), pady=(px(14), px(4)))

    def render_packages():
        for w in klist.winfo_children():
            w.destroy()
        lib = library_packages()
        have = {m["id"] for m in lib}
        section_title(f"INSTALLED  ({len(lib)})")
        if not lib:
            label(klist, "Nothing installed yet. Install one of the packages below, or create your own.",
                  10, fg=C["muted"], anchor="w").pack(fill="x", padx=px(16), pady=px(6))
        for m in lib:
            package_card(m, True)
        avail = [m for m in bundled_packages() if m["id"] not in have]
        if avail:
            section_title("INCLUDED WITH HEAVEN3D")
            for m in avail:
                package_card(m, False)
        if HUB_EXT["restart"]:
            need_restart()
    kp.refresh = render_packages

    def install_bundled(m):
        try:
            got = install_package(m["dir"])
        except Exception as ex:
            error("Install failed", str(ex))
            return
        render_packages()
        toast(f"Installed {got['name']}")
        if "hub" in got["targets"]:
            need_restart()

    def _peek_manifest(path):
        import zipfile
        try:
            with zipfile.ZipFile(path) as z:
                names = [n for n in z.namelist() if n.endswith("package.json")]
                names.sort(key=lambda n: n.count("/"))
                return json.loads(z.read(names[0]).decode("utf-8")) if names else None
        except Exception:
            return None

    def install_from_file():
        path = filedialog.askopenfilename(parent=root, title="Install a Heaven3D package",
                                          filetypes=[("Heaven3D package", "*.h3dpkg *.zip"), ("All files", "*.*")])
        if not path:
            return
        mm = _peek_manifest(path) or {}
        who = mm.get("author") or "an unknown author"
        if not messagebox.askyesno(
                "Install package?",
                f"{mm.get('name', os.path.basename(path))}  {mm.get('version', '')}\nby {who}\n\n"
                f"Runs in: {', '.join(mm.get('targets', [])) or '?'}\n\n"
                "Packages run Python code on your computer.\nOnly install packages from people you trust.",
                parent=root):
            return
        try:
            got = install_package(path)
        except Exception as ex:
            error("Install failed", str(ex))
            return
        render_packages()
        toast(f"Installed {got['name']}")
        if "hub" in got["targets"]:
            need_restart()

    def uninstall(m):
        if not messagebox.askyesno("Uninstall", f"Uninstall {m['name']}?\n\nProjects that already use it keep "
                                                f"their own copy.", parent=root):
            return
        uninstall_package(m["id"])
        if "hub" in m["targets"]:
            need_restart()
        render_packages()

    def export_pkg(m):
        dest = filedialog.asksaveasfilename(parent=root, title="Export package", defaultextension=".h3dpkg",
                                            initialfile=f"{m['id']}-{m.get('version', '1.0')}.h3dpkg",
                                            filetypes=[("Heaven3D package", "*.h3dpkg")])
        if dest:
            try:
                export_package(m["id"], dest)
                toast("Package exported")
            except Exception as ex:
                error("Export failed", str(ex))

    def project_picker(m, anchor_btn):
        menu = tk.Menu(root, tearoff=0, bg=C["raised"], fg=C["text"], activebackground=C["accent"],
                       activeforeground="#ffffff", bd=0, relief="flat", font=F(10))
        if not cfg.projects:
            menu.add_command(label="  (no projects yet)", state="disabled")
        for p in cfg.projects:
            if os.path.isfile(os.path.join(p["path"], PROJECT_FILE)):
                menu.add_command(label="  " + (p.get("name") or os.path.basename(p["path"])),
                                 command=lambda p=p: add_to(p, m))
        menu.tk_popup(anchor_btn.winfo_rootx(), anchor_btn.winfo_rooty() + anchor_btn.winfo_height() + px(4))

    def add_to(p, m):
        try:
            added = add_package_to_project(p["path"], m["id"])
        except Exception as ex:
            error("Couldn't add the package", str(ex))
            return
        extra = [a for a in added if a != m["id"]]
        toast(f"{m['name']} added to {p.get('name')}" + (f" (+ {', '.join(extra)})" if extra else ""))

    def project_packages_dialog(p):
        win = dialog(f"Packages · {p.get('name')}", 620, 560)
        pad = px(26)
        label(win, "Packages in this project", 16, "bold").pack(anchor="w", padx=pad, pady=(px(22), px(2)))
        label(win, "They are copied into the project's packages folder and exported with your game.",
              10, fg=C["muted"]).pack(anchor="w", padx=pad)
        box_o, box, _c = scrollable(win, C["bg"])
        box_o.pack(fill="both", expand=True, padx=(px(18), px(6)), pady=px(12))
        in_proj = {m["id"]: m for m in project_packages(p["path"])}
        seen = {}
        for m in library_packages() + bundled_packages() + list(in_proj.values()):
            if m["id"] not in seen and ("editor" in m["targets"] or "game" in m["targets"]):
                seen[m["id"]] = m
        if not seen:
            label(box, "No editor or game packages installed.", 10, fg=C["muted"]).pack(anchor="w", padx=px(10))
        for pid, m in sorted(seen.items(), key=lambda kv: kv[1]["name"]):
            card = Card(box, C["panel"], outer=C["bg"], radius=10)
            card.pack(fill="x", pady=px(4), padx=px(6))
            row = tk.Frame(card, bg=C["panel"])
            row.pack(fill="x", padx=px(14), pady=px(10))
            pkg_tile(row, m, 36).pack(side="left")
            txt = tk.Frame(row, bg=C["panel"])
            txt.pack(side="left", fill="x", expand=True, padx=px(12))
            label(txt, m["name"], 11, "semibold", anchor="w").pack(fill="x")
            target_pills(txt, m).pack(fill="x", pady=(px(4), 0))
            var = tk.BooleanVar(value=pid in in_proj)

            def flip(*_a, pid=pid, var=var):
                try:
                    if var.get():
                        added = add_package_to_project(p["path"], pid)
                        if len(added) > 1:
                            toast("Also added: " + ", ".join(a for a in added if a != pid))
                    else:
                        remove_package_from_project(p["path"], pid)
                except Exception as ex:
                    error("Packages", str(ex))
            var.trace_add("write", flip)
            Switch(row, var).pack(side="right")
        foot = tk.Frame(win, bg=C["bg"])
        foot.pack(fill="x", padx=pad, pady=(0, px(18)))
        Btn(foot, "Done", win.destroy, kind="primary").pack(side="right")

    def create_package_dialog():
        win = dialog("Create package", 560, 600)
        pad = px(26)
        label(win, "Create a package", 16, "bold").pack(anchor="w", padx=pad, pady=(px(22), px(2)))
        label(win, "Makes a working example you can edit (a blueprint, an editor tool and a Hub page).",
              10, fg=C["muted"], wraplength=px(500), justify="left").pack(anchor="w", padx=pad)
        form = tk.Frame(win, bg=C["bg"])
        form.pack(fill="x", padx=pad, pady=px(14))
        vals = {}
        for key, cap, default in (("name", "NAME", "My Package"), ("author", "AUTHOR", ""),
                                  ("desc", "WHAT IT DOES", "")):
            label(form, cap, 8, "semibold", fg=C["muted"], anchor="w").pack(fill="x")
            v = tk.StringVar(value=default)
            b, _e = entry(form, v, width=40)
            b.pack(fill="x", pady=(px(5), px(12)))
            vals[key] = v
        label(form, "RUNS IN", 8, "semibold", fg=C["muted"], anchor="w").pack(fill="x")
        trow = tk.Frame(form, bg=C["bg"])
        trow.pack(fill="x", pady=(px(6), 0))
        tvars = {}
        for t, cap in (("hub", "Hub"), ("editor", "Editor"), ("game", "Games")):
            v = tk.BooleanVar(value=t != "hub")
            tvars[t] = v
            Switch(trow, v).pack(side="left")
            label(trow, cap, 10).pack(side="left", padx=(px(8), px(22)))
        msg = label(win, "", 9, fg=C["danger"], anchor="w")
        msg.pack(fill="x", padx=pad)
        foot = tk.Frame(win, bg=C["bg"])
        foot.pack(side="bottom", fill="x", padx=pad, pady=px(20))

        def go():
            targets = [t for t, v in tvars.items() if v.get()]
            if not targets:
                msg.configure(text="Pick at least one place for it to run.")
                return
            try:
                m = create_package(vals["name"].get(), vals["author"].get(), vals["desc"].get(), targets)
            except Exception as ex:
                msg.configure(text=str(ex))
                return
            win.destroy()
            render_packages()
            if "hub" in m["targets"]:
                need_restart()
            toast(f"Created {m['name']}")
            safe(open_in_file_manager, m["dir"])
        Btn(foot, "Create", go, kind="primary", padx=20, pady=9).pack(side="right")
        Btn(foot, "Cancel", win.destroy, pady=9).pack(side="right", padx=px(8))

    # ================================================================ SETTINGS
    sp = tk.Frame(content, bg=C["bg"])
    pages["settings"] = sp
    page_header(sp, "Settings", "Where the Hub finds the engine, and how it starts it.")
    so, sbody, _sc = scrollable(sp, C["bg"])
    so.pack(fill="both", expand=True, padx=(px(22), px(8)), pady=(0, px(16)))

    def section(parent, title, help_=""):
        box = Card(parent, C["panel"], outer=C["bg"], radius=14)
        box.pack(fill="x", padx=(px(8), px(14)), pady=px(6))
        inner = tk.Frame(box, bg=C["panel"])
        inner.pack(fill="x", padx=px(22), pady=px(18))
        label(inner, title, 11, "semibold", anchor="w").pack(fill="x")
        if help_:
            label(inner, help_, 9, fg=C["muted"], anchor="w", justify="left", wraplength=px(760)).pack(
                fill="x", pady=(px(3), 0))
        return inner

    def setting_row(parent, title, help_, key, browse=None, numeric=False):
        inner = section(parent, title, help_)
        line = tk.Frame(inner, bg=C["panel"])
        line.pack(fill="x", pady=(px(12), 0))
        var = tk.StringVar(value=str(cfg.settings.get(key, "")))
        box, _e = entry(line, var, width=8 if numeric else 60)
        box.pack(side="left", fill="x" if not numeric else None, expand=not numeric)

        def commit(*_):
            v = var.get().strip()
            if numeric:
                try:
                    v = max(320, min(7680, int(v)))
                except ValueError:
                    return
            cfg.settings[key] = v
            if key in ("engine_path", "python_path"):
                cfg.settings["custom_" + key.split("_")[0]] = True
            cfg.save()
            update_footer()
        var.trace_add("write", commit)
        if browse == "file":
            Btn(line, "Browse", lambda: var.set(filedialog.askopenfilename(
                parent=root, initialdir=os.path.dirname(var.get() or HERE)) or var.get())).pack(side="left", padx=px(8))
        elif browse == "dir":
            Btn(line, "Browse", lambda: var.set(filedialog.askdirectory(
                parent=root, initialdir=var.get() or HERE) or var.get())).pack(side="left", padx=px(8))
        return var

    def refresh_settings():
        for w in sbody.winfo_children():
            w.destroy()
        setting_row(sbody, "Engine file", "The Heaven3D.py the Hub launches for every project.", "engine_path", "file")
        setting_row(sbody, "Python interpreter",
                    "Python used to run the engine. If install.py created .venv-heaven3d next to the "
                    "Hub, it is picked automatically.", "python_path", "file")
        setting_row(sbody, "Default project folder", "Where new projects are created.", "projects_root", "dir")
        size = tk.Frame(sbody, bg=C["bg"])
        size.pack(fill="x")
        ws = section(size, "Editor window size", "Width and height in pixels.")
        line = tk.Frame(ws, bg=C["panel"])
        line.pack(fill="x", pady=(px(12), 0))
        for key, cap in (("editor_width", "Width"), ("editor_height", "Height")):
            label(line, cap, 10, fg=C["muted"]).pack(side="left", padx=(0, px(8)))
            var = tk.StringVar(value=str(cfg.settings.get(key, "")))
            b, _e = entry(line, var, width=7)
            b.pack(side="left", padx=(0, px(20)))

            def commit(*_a, key=key, var=var):
                try:
                    cfg.settings[key] = max(320, min(7680, int(var.get().strip())))
                    cfg.save()
                except ValueError:
                    pass
            var.trace_add("write", commit)
        inner = section(sbody, "Engine requirements", "Checks that the Python above can run Heaven3D.")
        out = label(inner, "", 9, anchor="w", justify="left")
        out.configure(font=F(9, mono=True))
        Btn(inner, "Run check", lambda: run_check(out)).pack(anchor="w", pady=(px(12), px(8)))
        out.pack(fill="x")
        about = section(sbody, "About")
        label(about, f"Heaven3D Hub {HUB_VERSION}   ·   engine {engine_version(cfg.settings.get('engine_path', ''))}"
                     f"   ·   Python {platform.python_version()}   ·   {platform.system()}\n"
                     f"Settings file: {CONFIG_FILE}\nApache License 2.0   ·   github.com/lolgg313/Heaven3D",
              9, fg=C["muted"], anchor="w", justify="left").pack(fill="x", pady=(px(6), 0))
    sp.refresh = refresh_settings

    CHECK = ("import importlib, platform\n"
             "req = ['numpy', 'glm', 'moderngl', 'pygame']\n"
             "opt = [('numba', 'faster physics'), ('trimesh', '.obj import'), ('PIL', 'texture formats'),"
             " ('zstandard', 'compressed .blend (Python < 3.14)'), ('DracoPy', 'Draco-compressed glTF'),"
             " ('tkinter', 'file dialogs')]\n"
             "print('Python', platform.python_version())\n"
             "for m in req:\n"
             "    try:\n"
             "        mod = importlib.import_module(m); print('  OK      ', m, getattr(mod, '__version__', getattr(mod, 'version', '')))\n"
             "    except Exception as ex: print('  MISSING ', m, '->  pip install', {'glm': 'pyglm'}.get(m, m))\n"
             "for m, why in opt:\n"
             "    try: importlib.import_module(m); print('  ok      ', m, '(optional)')\n"
             "    except Exception: print('  -       ', m, '(optional:', why + ')')\n")

    def run_check(lbl):
        lbl.configure(text="Checking…", fg=C["muted"])
        py = cfg.settings.get("python_path") or _detect_python()

        def work():
            try:
                r = subprocess.run([py, "-c", CHECK], capture_output=True, text=True, timeout=120,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WIN else 0)
                txt = (r.stdout or "") + (r.stderr or "")
                ok = "MISSING" not in txt and r.returncode == 0
            except Exception as ex:
                txt, ok = f"Could not run {py}:\n{ex}", False
            root.after(0, lambda: lbl.configure(text=txt.strip(), fg=C["ok"] if ok else C["danger"]))
        threading.Thread(target=work, daemon=True).start()


    # ================================================================ PAGES ADDED BY HUB PACKAGES
    ui_kit = _types.SimpleNamespace(C=C, F=F, px=px, Card=Card, Btn=Btn, Pill=Pill, Switch=Switch, label=label,
                                    entry=entry, scrollable=scrollable, toast=toast, brand=brand, root=root,
                                    dialog=dialog, error=error, tk=tk, page_header=page_header, cfg=cfg)
    for _ext in HUB_EXT["pages"]:
        _pg = tk.Frame(content, bg=C["bg"])
        pages[_ext["id"]] = _pg

        def _build_ext(pg=_pg, ext=_ext):
            for w in pg.winfo_children():
                w.destroy()
            try:
                ext["build"](pg, ui_kit)
            except Exception as ex:
                HUB_EXT["errors"][ext["pkg"].id] = f"page: {ex}"
                label(pg, f"This page (from the {ext['pkg'].name} package) failed:\n{ex}", 10,
                      fg=C["danger"], justify="left").pack(anchor="w", padx=px(32), pady=px(32))
        _pg.refresh = _build_ext
    for _api in HUB_EXT["loaded"]:
        _api.root = root

    # ------------------------------------------------------------ start
    update_footer()
    show("projects")
    globals()["HUB_UI"] = {"root": root, "show": show, "new_project": new_project_dialog,
                           "open_tutorial": open_tutorial, "tutorials": tutorials,
                           "launch": launch, "cfg": cfg, "launcher": launcher, "export": export_dialog,
                           "packages": render_packages, "project_packages": project_packages_dialog,
                           "create_package": create_package_dialog, "hub_ext": HUB_EXT,
                           "render_projects": render_projects}
    if not cfg.projects and not os.path.isfile(CONFIG_FILE):
        cfg.save()
    try:
        icons = [brand(n / S) for n in (16, 32, 48, 64, 128, 256)]
        root.iconphoto(True, *[i for i in icons if i is not None])
    except tk.TclError:
        pass
    root.deiconify()
    _win_style_titlebar(root, _rgb(C["side"]), C.get("dark", True))
    for fn, api in list(HUB_EXT["handlers"].get("ready", [])):
        try:
            fn(api)
        except Exception as ex:
            HUB_EXT["errors"][api.id] = f"ready: {ex}"

    def prewarm(jobs):
        if jobs:
            path_, w_, r_ = jobs.pop(0)
            load_thumb(path_, w_, C["panel"], r_)
            root.after(30, lambda: prewarm(jobs))
    root.after(800, lambda: prewarm([(t["thumb"], w_, r_) for w_, r_ in ((268, 8), (372, 10))
                                     for t in list_templates() if t.get("thumb")]))
    root.mainloop()


# =============================================================================
#  CLI
# =============================================================================
def main(argv=None):
    ap = argparse.ArgumentParser(description="Heaven3D Hub — projects + offline tutorials")
    ap.add_argument("--list-templates", action="store_true")
    ap.add_argument("--list", action="store_true", help="list registered projects")
    ap.add_argument("--create", metavar="NAME", help="create a project without opening the window")
    ap.add_argument("--template", default="empty")
    ap.add_argument("--location", default=None)
    ap.add_argument("--open", metavar="PROJECT_DIR", help="open a project folder in the editor")
    ap.add_argument("--version", action="store_true")
    ap.add_argument("--list-packages", action="store_true", help="list installed and included packages")
    ap.add_argument("--install-package", metavar="PATH", help="install a package folder / .zip / .h3dpkg")
    ap.add_argument("--safe-mode", action="store_true", help="start without any packages")
    a = ap.parse_args(argv)
    if a.version:
        print(f"Heaven3D Hub {HUB_VERSION}")
        return 0
    if a.list_packages:
        have = {m["id"] for m in library_packages()}
        for m in library_packages():
            print(f"{'on ' if is_enabled(m['id']) else 'off'}  {m['id']:18s} {m['version']:8s} {', '.join(m['targets'])}")
        for m in bundled_packages():
            if m["id"] not in have:
                print(f"     {m['id']:18s} {m['version']:8s} (included, not installed)")
        return 0
    if a.install_package:
        m = install_package(a.install_package)
        print(f"Installed {m['name']} {m['version']}")
        return 0
    if a.list_templates:
        for t in list_templates():
            print(f"{t['id']:18s} {t['title']}  —  {t.get('desc', '')}")
        return 0
    cfg = Config()
    if a.list:
        for p in cfg.projects:
            print(f"{p.get('name', ''):30s} {p['path']}")
        return 0
    if a.create:
        loc = os.path.expanduser(a.location) if a.location else cfg.settings["projects_root"]
        p = create_project(cfg, a.create, a.template, loc)
        print(f"Created {p['path']}")
        return 0
    if a.open:
        p = cfg.add_project(os.path.abspath(os.path.expanduser(a.open)))
        Launcher(cfg).launch(p, "editor")
        return 0
    try:
        run_gui()
    except ImportError as ex:
        print(f"The Hub needs tkinter ({ex}).\n  Linux:  sudo apt install python3-tk\n"
              f"  Windows/macOS: reinstall Python from python.org (tkinter is included).")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
