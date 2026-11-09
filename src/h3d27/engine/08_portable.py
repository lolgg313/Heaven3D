# @heaven3d-part h3d27/engine/08_portable.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# ---------------------------------------------------------------
# 8. PORTABLE FILE SYSTEM  (FILE BURNER built in)
#    save : every asset path -> "assets/..." relative, '/' separators;
#           outside assets are copied into assets/imported/ (+ side files)
#    load : any old/foreign path is resolved (separators, letter case,
#           home-relative, absolute, FILE BURNER media/, name search)
#    + game save data (eng.saves / eng.save_game / eng.load_game),
#      eng.asset_path(), audio that resolves project files,
#      `--game DIR` (exported games) and `--export-game` (Hub export).
# ---------------------------------------------------------------
import json as _h3d27_json
import shutil
_H3D27_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
_H3D27_STATE = {"project_dir": None, "game": None, "copy_cache": {}}

def _h3d27_project_dir():
    return _H3D27_STATE["project_dir"] or os.getcwd()

def _h3d27_resolve(ref):
    if not isinstance(ref, str) or not ref:
        return None
    if os.path.isfile(ref):
        return os.path.abspath(ref)
    if not H3DPortable.is_media_ref(ref):
        return None
    return H3DPortable.Resolver(_h3d27_project_dir(), [_H3D27_ENGINE_DIR]).resolve(ref)

def _h3d27_one_shot(module, name, transform):
    """Temporarily wrap module.<name> so the FIRST project dict that
    passes through it is transformed (json.dump / json.load)."""
    real = getattr(module, name)
    state = {"done": False}

    def wrapper(*a, **kw):
        if name == "dump":
            obj = a[0]
            if not state["done"] and isinstance(obj, dict) and obj.get("type") == "heaven3d_project":
                state["done"] = True
                transform(obj)
            return real(*a, **kw)
        obj = real(*a, **kw)
        if not state["done"] and isinstance(obj, dict) and obj.get("type") == "heaven3d_project":
            state["done"] = True
            transform(obj)
        return obj
    setattr(module, name, wrapper)
    return lambda: setattr(module, name, real)

_H3D27_PREV_PS_SAVE = ProjectSerializer.save.__func__
_H3D27_PREV_PS_LOAD = ProjectSerializer.load.__func__

def _h3d27_ps_save(cls, scene, path, *args, **kwargs):
    path = os.path.abspath(path)
    pdir = os.path.dirname(path)
    _H3D27_STATE["project_dir"] = pdir
    if os.path.isfile(path):                       # keep the previous save
        try:
            shutil.copy2(path, path + ".bak")
        except OSError:
            pass
    collector = H3DPortable.Collector(pdir, Log.info)
    collector.done = _H3D27_STATE["copy_cache"].setdefault(os.path.normcase(pdir), {})
    report = {}

    def portable(obj):
        try:
            report.update(H3DPortable.make_portable(obj, pdir, Log.info, collect=True,
                                                    collector=collector))
        except Exception as ex:
            Log.warn(f"[Portable] save pass failed (project saved unchanged): {ex}")
    restore = _h3d27_one_shot(json, "dump", portable)
    try:
        ok = _H3D27_PREV_PS_SAVE(cls, scene, path, *args, **kwargs)
    finally:
        restore()
    # Point the scene at the project copies so the next save is instant.
    try:
        moved = {k[0]: os.path.join(pdir, *v.split("/")) for k, v in collector.done.items()}
        for n in scene.root.traverse():
            sp = getattr(n, "source_path", None)
            if sp and os.path.normcase(os.path.abspath(sp)) in moved:
                n.source_path = moved[os.path.normcase(os.path.abspath(sp))]
    except Exception:
        pass
    if report.get("copied"):
        Log.info(f"[Portable] {report['copied']} file(s) copied into {H3DPortable.IMPORT_DIR}/ "
                 f"- the project folder now holds everything it needs.")
    return ok

def _h3d27_ps_load(cls, path, scene, engine):
    path = os.path.abspath(path)
    pdir = os.path.dirname(path)
    _H3D27_STATE["project_dir"] = pdir
    restore = _h3d27_one_shot(json, "load", lambda obj: H3DPortable.resolve_project_data(
        obj, pdir, Log.warn, extra_dirs=[_H3D27_ENGINE_DIR]))
    try:
        return _H3D27_PREV_PS_LOAD(cls, path, scene, engine)
    finally:
        restore()

ProjectSerializer.save = classmethod(_h3d27_ps_save)
ProjectSerializer.load = classmethod(_h3d27_ps_load)

# -- audio finds project files wherever the project was moved -----
_H3D27_PREV_LOAD_SOUND = AudioSystem.load_sound
_H3D27_PREV_PLAY = AudioSystem.play
_H3D27_PREV_MUSIC = AudioSystem.play_music

def _h3d27_load_sound(self, path_or_name, name=None):
    p = str(path_or_name)
    if not os.path.isfile(p):
        hit = _h3d27_resolve(p)
        if hit:
            name = name or os.path.splitext(os.path.basename(p.replace("\\", "/")))[0]
            p = hit
    return _H3D27_PREV_LOAD_SOUND(self, p, name)

def _h3d27_play(self, name, *a, **kw):
    key = str(name)
    if (getattr(self, "enabled", False) and key not in self._sounds
            and not os.path.isfile(key) and H3DPortable.is_media_ref(key)):
        hit = _h3d27_resolve(key)
        if hit:
            _H3D27_PREV_LOAD_SOUND(self, hit, key)
    return _H3D27_PREV_PLAY(self, name, *a, **kw)

def _h3d27_play_music(self, path, *a, **kw):
    return _H3D27_PREV_MUSIC(self, _h3d27_resolve(path) or path, *a, **kw)

AudioSystem.load_sound = _h3d27_load_sound
AudioSystem.play = _h3d27_play
AudioSystem.play_music = _h3d27_play_music

# -- game save data + asset lookup for scripts --------------------
def _h3d27_game_name(self):
    g = _H3D27_STATE.get("game")
    if g and g.get("name"):
        return g["name"]
    pdir = _H3D27_STATE["project_dir"] or (os.path.dirname(os.path.abspath(self.project_path))
                                           if getattr(self, "project_path", None) else os.getcwd())
    try:
        with open(os.path.join(pdir, "project.json"), encoding="utf-8") as f:
            return json.load(f).get("name") or os.path.basename(pdir)
    except (OSError, ValueError):
        return os.path.basename(pdir)

def _h3d27_saves(self):
    sd = getattr(self, "_h3d27_savedata", None)
    if sd is None:
        g = _H3D27_STATE.get("game")
        root = g["root"] if g else (_H3D27_STATE["project_dir"] or os.getcwd())
        if g and not g.get("portable", True):
            root = None                     # inside a macOS .app: saves go to Application Support
        sd = H3DPortable.SaveData(_h3d27_game_name(self), portable_root=root)
        self._h3d27_savedata = sd
        Log.info(f"Game saves folder: {sd.folder}")
    return sd

HeavenEngine.saves = property(_h3d27_saves)
HeavenEngine.save_game = lambda self, slot="save", data=None: self.saves.save(slot, data)
HeavenEngine.load_game = lambda self, slot="save", default=None: self.saves.load(slot, default)
HeavenEngine.asset_path = lambda self, ref: _h3d27_resolve(ref) or ref

# -- window title / taskbar identity -----------------------------
_H3D27_PREV_INIT2 = HeavenEngine.__init__

def _h3d27_engine_init2(self, *args, **kwargs):
    names = ("mode", "width", "height", "project_path")
    bound = dict(zip(names, args)); bound.update(kwargs)
    pp = bound.get("project_path")
    if pp:
        _H3D27_STATE["project_dir"] = os.path.dirname(os.path.abspath(pp))
    g = _H3D27_STATE.get("game")
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                ("Heaven3D.Game." + H3DPortable.safe_name(g["name"])) if g else "Heaven3D.Engine")
        except Exception:
            pass
    _H3D27_PREV_INIT2(self, *args, **kwargs)
    try:
        if g:
            pygame.display.set_caption(g["name"])
        elif bound.get("mode") == "runtime" and pp:
            pygame.display.set_caption(_h3d27_game_name(self))
    except Exception:
        pass

HeavenEngine.__init__ = _h3d27_engine_init2

# -- command line: exported games and the Hub's export ------------
def _h3d27_project_icon(project_file):
    """The game icon chosen for a project (project.json "game_icon")."""
    try:
        pdir = os.path.dirname(os.path.abspath(project_file))
        with open(os.path.join(pdir, "project.json"), encoding="utf-8") as f:
            rel = json.load(f).get("game_icon")
        if rel:
            path = os.path.join(pdir, *rel.split("/"))
            return path if os.path.isfile(path) else None
    except (OSError, ValueError):
        pass
    return None

def _h3d27_cli():
    argv = sys.argv[1:]
    if "--export-game" in argv:
        # Game names and paths can be in any script; a Windows pipe or an
        # old console code page must not end the export on a log line.
        for _s in (sys.stdout, sys.stderr):
            try:
                _s.reconfigure(errors="replace")
            except (AttributeError, ValueError):
                pass
        import argparse
        ap = argparse.ArgumentParser(prog="Heaven3D.py --export-game")
        ap.add_argument("--export-game", dest="project", required=True)
        ap.add_argument("--targets", default=H3DPortable.current_platform())
        ap.add_argument("--out", required=True)
        ap.add_argument("--name", default=None)
        ap.add_argument("--width", type=int, default=1600)
        ap.add_argument("--height", type=int, default=900)
        ap.add_argument("--no-zip", action="store_true")
        ap.add_argument("--icon", default=None, help="game icon (.png .jpg .bmp)")
        a = ap.parse_args(argv)
        proj = a.project
        if os.path.isdir(proj):
            proj = os.path.join(proj, "main.heaven3d")
        try:
            res = H3DPortable.export_game(
                proj, a.out, [t.strip() for t in a.targets.split(",") if t.strip()],
                _H3D27_ENGINE_DIR, a.name, a.width, a.height, not a.no_zip,
                log=lambda m: print(m, flush=True), icon=a.icon or _h3d27_project_icon(proj))
            for plat, folder, z in res:
                print(f"[Export] RESULT {plat} {folder} {z or ''}", flush=True)
            sys.exit(0)
        except Exception as ex:
            print(f"[Export] ERROR {ex}", flush=True)
            sys.exit(2)
    if "--game" in argv:
        i = argv.index("--game")
        root = os.path.abspath(argv[i + 1] if i + 1 < len(argv) else os.getcwd())
        cfg = {}
        try:
            with open(os.path.join(root, "engine", "game.json"), encoding="utf-8") as f:
                cfg = json.load(f)
        except (OSError, ValueError):
            pass
        name = cfg.get("name") or os.path.basename(root)
        portable = bool(cfg.get("portable_saves", True))       # False for macOS .app games
        _H3D27_STATE["game"] = {"name": name, "root": root, "portable": portable}
        # pythonw (Windows) has no console: keep a log next to the saves
        logdirs = [os.path.join(H3DPortable.user_data_root(), H3DPortable.safe_name(name))]
        if portable:
            logdirs.insert(0, os.path.join(root, "saves"))
        for logdir in logdirs:
            try:
                os.makedirs(logdir, exist_ok=True)
                fh = open(os.path.join(logdir, "last_run.log"), "w", encoding="utf-8", buffering=1)
                if sys.stdout is None or not getattr(sys.stdout, "isatty", lambda: False)():
                    sys.stdout = fh
                    sys.stderr = fh
                break
            except OSError:
                continue
        project = os.path.join(root, *cfg.get("project", "game/main.heaven3d").split("/"))
        os.chdir(os.path.dirname(project))
        sys.argv = [sys.argv[0], "--runtime", "--project", project,
                    "--width", str(cfg.get("width", 1600)), "--height", str(cfg.get("height", 900))]
        # A game always shows the short "made with Heaven3D" splash.
        globals()["_h3d_is_first_run"] = lambda: False
        icon_file = os.path.join(root, *cfg["icon"].split("/")) if cfg.get("icon") else None
        if icon_file and os.path.isfile(icon_file):
            def _game_icon(path=icon_file):
                try:
                    pygame.display.set_icon(pygame.transform.smoothscale(pygame.image.load(path), (64, 64)))
                except Exception:
                    pass
            globals()["_h3d_set_window_icon"] = _game_icon      # used before the window opens

if __name__ == "__main__":
    _h3d27_cli()

# -- the editor's Export button builds real stand-alone games ------
_H3D27_PREV_EXPORT_DLG = HeavenEngine.export_runtime_dialog

def _h3d27_export_dialog(self):
    players = [p for p in ("windows", "linux", "macos") if H3DPortable.find_player(_H3D27_ENGINE_DIR, p)]
    proj = getattr(self, "project_path", None)
    if not players:
        Log.info("[Export] Stand-alone game export needs the Heaven3D standalone edition "
                 "(it ships runtime/players). Saving a .heaven3d copy instead.")
        return _H3D27_PREV_EXPORT_DLG(self)
    if not proj:
        Log.warn("[Export] Save the project first (Save), then press Export again.")
        return _H3D27_PREV_EXPORT_DLG(self)
    if getattr(self, "_h3d27_exporting", False):
        Log.info("[Export] An export is already running...")
        return
    self.save_project(proj)
    pdir = os.path.dirname(os.path.abspath(proj))
    out = os.path.join(os.path.dirname(os.path.dirname(pdir)), "Heaven3D Builds")
    self._h3d27_exporting = True
    Log.info(f"[Export] Building {' + '.join(p.title() for p in players)} into {out} ...")

    def work():
        try:
            res = H3DPortable.export_game(proj, out, players, _H3D27_ENGINE_DIR,
                                          width=self.width, height=self.height, make_zip=True,
                                          log=lambda m: Log.info(m), icon=_h3d27_project_icon(proj))
            for plat, folder, z in res:
                Log.info(f"[Export] {plat.title()} ready: {z or folder}")
        except Exception as ex:
            Log.error(f"[Export] failed: {ex}")
        finally:
            self._h3d27_exporting = False
    import threading
    threading.Thread(target=work, daemon=True).start()

HeavenEngine.export_runtime_dialog = _h3d27_export_dialog
