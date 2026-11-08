# @heaven3d-part h3d27/engine/06_projects.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# ---------------------------------------------------------------
# 6. Projects (Heaven3D Hub): `--project` opens in the EDITOR too, and
#    every project's scripts/*.py blueprints are registered first.
# ---------------------------------------------------------------
# When the engine runs as a script (python Heaven3D.py / Heaven3D_runtime.py)
# it is the module "__main__".  Project scripts do `from Heaven3D import
# Blueprint, blueprint` — without this alias Python would load a SECOND
# copy of the engine and register their blueprints there, so saved
# blueprints would never be found ("not registered; not restored").
if __name__ == "__main__" and "Heaven3D" not in sys.modules:
    sys.modules["Heaven3D"] = sys.modules["__main__"]

_H3D27_PREV_INIT = HeavenEngine.__init__

def _h3d27_import_project_scripts(project_dir):
    sdir = os.path.join(project_dir, "scripts")
    if not os.path.isdir(sdir):
        return
    if project_dir not in sys.path:
        sys.path.insert(0, project_dir)
    if sdir not in sys.path:
        sys.path.insert(0, sdir)
    import importlib
    for fn in sorted(os.listdir(sdir)):
        if not fn.endswith(".py") or fn.startswith("_"):
            continue
        mod = os.path.splitext(fn)[0]
        try:
            if mod in sys.modules:
                importlib.reload(sys.modules[mod])
            else:
                importlib.import_module(mod)
            Log.info(f"Project script loaded: scripts/{fn}")
        except Exception as ex:
            Log.error(f"Project script scripts/{fn} failed: {ex}")
            traceback.print_exc()

def _h3d27_engine_init(self, *args, **kwargs):
    names = ("mode", "width", "height", "project_path")
    bound = dict(zip(names, args)); bound.update(kwargs)
    mode = bound.get("mode", "editor")
    project_path = bound.get("project_path")
    proj_dir = os.path.dirname(os.path.abspath(project_path)) if project_path else os.getcwd()
    try:
        _h3d27_import_project_scripts(proj_dir)
    except Exception as ex:
        Log.warn(f"Project scripts not loaded: {ex}")
    _H3D27_PREV_INIT(self, *args, **kwargs)
    if mode == "editor" and project_path and os.path.isfile(project_path):
        try:
            self.load_project(project_path)
            self.project_path = project_path
            pygame.display.set_caption(
                f"Heaven3D v{HEAVEN_VERSION}  —  {os.path.splitext(os.path.basename(project_path))[0]}")
            Log.info(f"Project opened: {project_path}")
        except Exception as ex:
            Log.error(f"Could not open project '{project_path}': {ex}")

HeavenEngine.__init__ = _h3d27_engine_init
