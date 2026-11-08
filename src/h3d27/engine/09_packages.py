# @heaven3d-part h3d27/engine/09_packages.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# ---------------------------------------------------------------
# 9. PACKAGES — pure-Python add-ons for the editor and for games
#    (the Hub manages them; see docs/PACKAGES.md).
#
#    <package>/package.json   {"id", "name", "version", "targets":
#                               ["hub","editor","game"], "engine": "engine.py",
#                               "hub": "hub.py", ...}
#    engine.py:  def register(h3d): ...   (h3d = H3DPackageAPI below)
#
#    Where packages come from:
#      ~/.heaven3d/packages/<id>     the user's library (enabled in the Hub)
#      <project>/packages/<id>      packages used by THIS game - they are
#                                   exported with it
#    Editor : enabled library packages (editor/game) + project packages
#    Game   : project packages with the "game" target only
#    --safe-mode or HEAVEN3D_SAFE_MODE=1 skips every package.
# ---------------------------------------------------------------
import importlib.util as _h3d27_ilu
_H3D27_PKG_STATE = {"apis": [], "tools": [], "menu_open": False, "loaded_for": None, "errors": {}}
H3DPackages = _H3D27_PKG_STATE["apis"]

def _h3d27_lib_root():
    return os.path.join(os.path.expanduser("~"), ".heaven3d", "packages")

def _h3d27_read_manifest(folder):
    try:
        with open(os.path.join(folder, "package.json"), encoding="utf-8") as f:
            m = json.load(f)
        if not isinstance(m, dict) or not m.get("id"):
            return None
        m.setdefault("name", m["id"])
        m.setdefault("version", "0.0.0")
        m.setdefault("targets", [])
        return m
    except (OSError, ValueError):
        return None

def _h3d27_enabled_ids():
    try:
        with open(os.path.join(os.path.expanduser("~"), ".heaven3d", "packages.json"), encoding="utf-8") as f:
            d = json.load(f)
        return set(i for i, on in d.get("enabled", {}).items() if on)
    except (OSError, ValueError):
        return set()

def _h3d27_ver(v):
    out = []
    for part in str(v).split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out + [0] * (3 - len(out)))

def _h3d27_sync_bundled(project_dir):
    """A newer Heaven3D ships newer packages next to Heaven3D.py.  Replace
    OLDER copies in the user's library and in the project with them (their
    settings.json is kept), so updating Heaven3D really updates packages."""
    bundled = os.path.join(os.path.dirname(os.path.abspath(__file__)), "packages")
    if not os.path.isdir(bundled):
        return []
    roots = [_h3d27_lib_root()]
    if project_dir:
        roots.append(os.path.join(project_dir, "packages"))
    updated = []
    for d in sorted(os.listdir(bundled)):
        src = os.path.join(bundled, d)
        mb = _h3d27_read_manifest(src)
        if not mb:
            continue
        for root in roots:
            dest = os.path.join(root, d)
            if os.path.normcase(os.path.abspath(dest)) == os.path.normcase(os.path.abspath(src)):
                continue
            mo = _h3d27_read_manifest(dest)
            if not mo or mo.get("id") != mb["id"] or _h3d27_ver(mo["version"]) >= _h3d27_ver(mb["version"]):
                continue
            try:
                keep = None
                sf = os.path.join(dest, "settings.json")
                if os.path.isfile(sf):
                    with open(sf, "rb") as fh:
                        keep = fh.read()
                tmp = dest + ".h3dnew"
                if os.path.isdir(tmp):
                    shutil.rmtree(tmp)
                shutil.copytree(src, tmp, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "settings.json"))
                if keep:
                    with open(os.path.join(tmp, "settings.json"), "wb") as fh:
                        fh.write(keep)
                shutil.rmtree(dest)
                os.replace(tmp, dest)
                updated.append((mb["id"], mo["version"], mb["version"], dest))
                Log.info(f"Package {mb['id']} updated {mo['version']} -> {mb['version']} ({dest})")
            except Exception as ex:
                Log.warn(f"Couldn't update package {mb['id']} in {root}: {ex}")
    return updated

def _h3d27_discover(mode, project_dir):
    wanted = {"editor", "game"} if mode == "editor" else {"game"}
    found = {}
    if mode == "editor" and not _H3D27_STATE.get("game"):
        try:
            _h3d27_sync_bundled(project_dir)
        except Exception as ex:
            Log.warn(f"package update check failed: {ex}")
    if mode == "editor" and not _H3D27_STATE.get("game"):
        enabled = _h3d27_enabled_ids()
        lib = _h3d27_lib_root()
        for d in sorted(os.listdir(lib)) if os.path.isdir(lib) else []:
            m = _h3d27_read_manifest(os.path.join(lib, d))
            if m and m["id"] in enabled and wanted & set(m["targets"]) and m.get("engine"):
                found[m["id"]] = (m, os.path.join(lib, d), "library")
    pk = os.path.join(project_dir, "packages") if project_dir else None
    for d in sorted(os.listdir(pk)) if pk and os.path.isdir(pk) else []:
        m = _h3d27_read_manifest(os.path.join(pk, d))
        if m and wanted & set(m["targets"]) and m.get("engine"):
            found[m["id"]] = (m, os.path.join(pk, d), "project")      # project copy wins
    # dependency order: a package loads after the packages it requires
    order, seen = [], set()

    def visit(pid, stack=()):
        if pid in seen or pid not in found or pid in stack:
            return
        for dep in found[pid][0].get("requires", []) or []:
            visit(dep, stack + (pid,))
        seen.add(pid)
        order.append(found[pid])
    for pid in sorted(found):
        visit(pid)
    return order

class H3DPackageAPI:
    """What a package's engine.py receives in register(h3d).

    h3d.engine        the HeavenEngine (None until the "init" event)
    h3d.module        the Heaven3D module (Blueprint, SceneNode, Mesh, THEME, glm, ...)
    h3d.mode          "editor" or "runtime"
    h3d.path          this package's folder;  h3d.asset("x.png") -> full path
    h3d.settings      dict saved with h3d.save_settings()
    h3d.on(event, fn) events: init, update(dt), play_start, play_tick(dt), play_stop,
                      render, key(key) -> True to consume, project_loaded(path),
                      project_saved(path)
    h3d.add_tool(label, fn)   button in the editor's Packages menu
    h3d.wrap(owner, "method", wrapper)   wrapper(original, *args, **kw)
    h3d.theme(**hex_colors)   change editor colours (THEME keys)
    h3d.blueprint             decorator to register Blueprint classes
    h3d.log / h3d.warn
    """

    EVENTS = ("init", "update", "play_start", "play_tick", "play_stop", "render", "key",
              "project_loaded", "project_saved")

    def __init__(self, manifest, folder, origin, mode):
        self.manifest = manifest
        self.id = manifest["id"]
        self.name = manifest.get("name", self.id)
        self.version = manifest.get("version", "")
        self.path = folder
        self.origin = origin
        self.mode = mode
        self.engine = None
        self.module = sys.modules[__name__]
        self.blueprint = blueprint
        self._handlers = {e: [] for e in self.EVENTS}
        self._settings_file = (os.path.join(folder, "settings.json") if origin == "project" else
                               os.path.join(os.path.expanduser("~"), ".heaven3d", "package_settings",
                                            self.id + ".json"))
        self.settings = dict(manifest.get("default_settings", {}) or {})
        try:
            with open(self._settings_file, encoding="utf-8") as f:
                self.settings.update(json.load(f))
        except (OSError, ValueError):
            pass

    # -- events / tools -------------------------------------------
    def on(self, event, fn=None):
        if event not in self._handlers:
            raise ValueError(f"unknown event {event!r}; use one of {', '.join(self.EVENTS)}")
        if fn is None:                       # decorator form: @h3d.on("update")
            return lambda f: (self._handlers[event].append(f), f)[1]
        self._handlers[event].append(fn)
        return fn

    def add_tool(self, label, fn, color=None):
        """Adds a button to the editor's Packages menu.  fn(engine) runs on
        click.  Returns the tool dict - change tool["label"] any time."""
        tool = {"label": label, "fn": fn, "color": color, "pkg": self}
        _H3D27_PKG_STATE["tools"].append(tool)
        return tool

    # -- helpers ----------------------------------------------------
    def wrap(self, owner, name, wrapper):
        """Replace owner.<name> with wrapper(original, *args, **kw).  If the
        wrapper raises, the original runs instead (the engine never breaks)."""
        original = getattr(owner, name)
        api = self

        def patched(*a, **kw):
            try:
                return wrapper(original, *a, **kw)
            except Exception as ex:
                api._error(f"wrap {name}", ex)
                return original(*a, **kw)
        patched.__wrapped__ = original
        setattr(owner, name, patched)
        return original

    def theme(self, **colors):
        for k, v in colors.items():
            THEME[k] = _hex(v) if isinstance(v, str) else tuple(v)

    def asset(self, rel):
        return os.path.join(self.path, *rel.replace("\\", "/").split("/"))

    def load(self, filename):
        """Import another .py file of this package as a module."""
        name = f"h3dpkg_{self.id}_{os.path.splitext(os.path.basename(filename))[0]}"
        spec = _h3d27_ilu.spec_from_file_location(name, self.asset(filename))
        mod = _h3d27_ilu.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod

    def save_settings(self):
        try:
            H3DPortable.atomic_write_json(self._settings_file, self.settings, backup=False)
        except Exception as ex:
            self.warn(f"settings not saved: {ex}")

    def log(self, msg):
        Log.info(f"[{self.name}] {msg}")

    def warn(self, msg):
        Log.warn(f"[{self.name}] {msg}")

    def _error(self, where, ex):
        key = (self.id, where)
        n = _H3D27_PKG_STATE["errors"].get(key, 0) + 1
        _H3D27_PKG_STATE["errors"][key] = n
        if n <= 3:
            Log.error(f"[Package {self.id}] {where}: {ex}")
            if n == 1:
                traceback.print_exc()
        return n

def _h3d27_emit(event, *args):
    result = None
    for api in list(H3DPackages):
        for fn in list(api._handlers.get(event, ())):
            try:
                r = fn(*args)
                if event == "key" and r:
                    result = True
            except Exception as ex:
                if api._error(event, ex) >= 5 and event in ("update", "play_tick", "render"):
                    api._handlers[event].remove(fn)      # stop a per-frame handler that keeps failing
                    Log.error(f"[Package {api.id}] '{event}' handler disabled after repeated errors")
    return result

def _h3d27_load_packages(mode, project_dir):
    if _H3D27_PKG_STATE["loaded_for"] == (mode, project_dir):
        return
    if "--safe-mode" in sys.argv or os.environ.get("HEAVEN3D_SAFE_MODE") == "1":
        Log.info("[Packages] safe mode: packages are not loaded")
        _H3D27_PKG_STATE["loaded_for"] = (mode, project_dir)
        return
    _H3D27_PKG_STATE["loaded_for"] = (mode, project_dir)
    for manifest, folder, origin in _h3d27_discover(mode, project_dir):
        api = H3DPackageAPI(manifest, folder, origin, mode)
        try:
            entry = os.path.join(folder, manifest["engine"])
            spec = _h3d27_ilu.spec_from_file_location(f"h3dpkg_{api.id}", entry)
            mod = _h3d27_ilu.module_from_spec(spec)
            mod.h3d = api
            sys.modules[spec.name] = mod
            spec.loader.exec_module(mod)
            if hasattr(mod, "register"):
                mod.register(api)
            H3DPackages.append(api)
            Log.info(f"[Packages] {api.name} {api.version} loaded ({origin})")
        except Exception as ex:
            Log.error(f"[Packages] {manifest.get('id')} failed to load: {ex}")
            traceback.print_exc()

# -- engine hooks -------------------------------------------------
_H3D27_PREV_INIT3 = HeavenEngine.__init__

def _h3d27_engine_init3(self, *args, **kwargs):
    names = ("mode", "width", "height", "project_path")
    bound = dict(zip(names, args)); bound.update(kwargs)
    mode = bound.get("mode", "editor")
    pp = bound.get("project_path")
    pdir = os.path.dirname(os.path.abspath(pp)) if pp else os.getcwd()
    try:
        _h3d27_load_packages(mode, pdir)        # before the project loads: blueprints exist
    except Exception as ex:
        Log.error(f"[Packages] {ex}")
    self._h3d27_last_t = None
    _H3D27_PREV_INIT3(self, *args, **kwargs)
    for api in H3DPackages:
        api.engine = self
    _h3d27_emit("init", self)

HeavenEngine.__init__ = _h3d27_engine_init3

_H3D27_PREV_CAPTURE = HeavenEngine._apply_mouse_capture

def _h3d27_capture(self):
    if H3DPackages:
        t = float(getattr(self, "time", 0.0))
        last = getattr(self, "_h3d27_last_t", None)
        self._h3d27_last_t = t
        _h3d27_emit("update", self, 0.0 if last is None else max(0.0, t - last))
    return _H3D27_PREV_CAPTURE(self)

HeavenEngine._apply_mouse_capture = _h3d27_capture

_H3D27_PREV_SCHED = HeavenEngine._process_scheduler

def _h3d27_sched(self):
    r = _H3D27_PREV_SCHED(self)
    if H3DPackages:
        t = float(getattr(self, "time", 0.0))
        last = getattr(self, "_h3d27_last_pt", None)
        self._h3d27_last_pt = t
        _h3d27_emit("play_tick", self, 0.0 if last is None else min(0.1, max(0.0, t - last)))
    return r

HeavenEngine._process_scheduler = _h3d27_sched

_H3D27_PREV_BEGIN = HeavenEngine._begin_play
_H3D27_PREV_STOP = HeavenEngine._stop_play

def _h3d27_begin(self, *a, **kw):
    r = _H3D27_PREV_BEGIN(self, *a, **kw)
    self._h3d27_last_pt = None
    _h3d27_emit("play_start", self)
    return r

def _h3d27_stop(self, *a, **kw):
    _h3d27_emit("play_stop", self)
    return _H3D27_PREV_STOP(self, *a, **kw)

HeavenEngine._begin_play = _h3d27_begin
HeavenEngine._stop_play = _h3d27_stop

_H3D27_PREV_OUTLINE = HeavenEngine._render_selection_outline

def _h3d27_outline(self):
    r = _H3D27_PREV_OUTLINE(self)
    if H3DPackages:
        _h3d27_emit("render", self)
    return r

HeavenEngine._render_selection_outline = _h3d27_outline

_H3D27_PREV_KEY = HeavenEngine._on_key

def _h3d27_key(self, key):
    if H3DPackages and _h3d27_emit("key", self, key):
        return None
    return _H3D27_PREV_KEY(self, key)

HeavenEngine._on_key = _h3d27_key

_H3D27_PREV_LOADP = HeavenEngine.load_project
_H3D27_PREV_SAVEP = HeavenEngine.save_project

def _h3d27_loadp(self, path=None):
    r = _H3D27_PREV_LOADP(self, path)
    _h3d27_emit("project_loaded", self, path or getattr(self, "project_path", None))
    return r

def _h3d27_savep(self, path=None):
    r = _H3D27_PREV_SAVEP(self, path)
    _h3d27_emit("project_saved", self, path or getattr(self, "project_path", None))
    return r

HeavenEngine.load_project = _h3d27_loadp
HeavenEngine.save_project = _h3d27_savep

# runtime-mode projects load inside __init__ (before "init"): announce it
_H3D27_PREV_PSLOAD2 = ProjectSerializer.load.__func__

def _h3d27_psload2(cls, path, scene, engine):
    ok = _H3D27_PREV_PSLOAD2(cls, path, scene, engine)
    if ok and getattr(engine, "mode", None) == "runtime":
        engine._h3d27_runtime_loaded = path
    return ok

ProjectSerializer.load = classmethod(_h3d27_psload2)

def _h3d27_runtime_announce(engine):
    p = getattr(engine, "_h3d27_runtime_loaded", None)
    if p:
        _h3d27_emit("project_loaded", engine, p)
if True:
    _H3D27_PREV_INIT4 = HeavenEngine.__init__

    def _h3d27_engine_init4(self, *a, **kw):
        _H3D27_PREV_INIT4(self, *a, **kw)
        _h3d27_runtime_announce(self)
    HeavenEngine.__init__ = _h3d27_engine_init4

# -- the editor's "Packages" menu (Hierarchy header) ---------------
_H3D27_PREV_BUILD_UI = HeavenEngine._build_ui

def _h3d27_build_ui(self):
    _H3D27_PREV_BUILD_UI(self)
    try:
        if self.ui is None or self.mode != "editor" or self._playing:
            return
        tools = _H3D27_PKG_STATE["tools"]
        T = THEME
        label = f"Packages ({len(H3DPackages)})"
        self.ui.button(UIRect(0.098, 0.879, 0.075, 0.030), label,
                       lambda: _H3D27_PKG_STATE.update(menu_open=not _H3D27_PKG_STATE["menu_open"]),
                       T["accent_dim"] if _H3D27_PKG_STATE["menu_open"] else T["button"])
        if not _H3D27_PKG_STATE["menu_open"]:
            return
        rows = tools or [{"label": "No package tools - add packages in the Hub", "fn": None, "color": None}]
        h = 0.034
        top = 0.872
        height = len(rows) * (h + 0.006) + 0.014
        self.ui.panel(UIRect(0.182, top - height, 0.20, height), T["panel"])
        y = top - 0.007 - h
        for t in rows:
            def run(t=t):
                _H3D27_PKG_STATE["menu_open"] = False
                if t["fn"] is None:
                    return
                try:
                    t["fn"](self)
                except TypeError:
                    try:
                        t["fn"]()
                    except Exception as ex:
                        t["pkg"]._error(f"tool {t['label']}", ex)
                except Exception as ex:
                    t["pkg"]._error(f"tool {t['label']}", ex)
            col = _hex(t["color"]) if isinstance(t.get("color"), str) else (t.get("color") or T["button"])
            self.ui.button(UIRect(0.188, y, 0.188, h), t["label"], run, col, align="left", pad_x_px=12)
            y -= h + 0.006
    except Exception as ex:
        _h3d27_log_once("pkg_menu", f"[Packages] menu: {ex}")

HeavenEngine._build_ui = _h3d27_build_ui
