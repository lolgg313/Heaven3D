# @heaven3d-part editor/vitrail_hook.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# ---------------------------------------------------------------------------
# [VITRAIL EDITOR]  The 2.8 editor UI (h3d_editor/, built with Vitrail)
#
#   The editor's windows -- menu bar, toolbar, outliner, inspector, asset
#   browser, console, code editor, visual scripts, animation -- live in the
#   h3d_editor/ package next to this file and are drawn by Vitrail (also
#   next to this file).  They are loaded ONLY in editor mode: a game run or
#   exported with this engine never imports either of them.
#
#   python Heaven3D.py --classic-ui   (or HEAVEN3D_CLASSIC_UI=1) keeps the
#   classic in-engine UI.  If the editor can't start for any reason, the
#   classic UI is used and the reason is logged.
# ---------------------------------------------------------------------------
_H3D_EDITOR_STATE = {"error": None, "module": None, "editor": None}

# Packages add Vitrail panels to the editor's Inspector here:
#   fn(inspector, node) -> None, or an object with .sync() (called while shown)
VT_INSPECTOR_PANELS = []


def _h3d_editor_wanted(engine):
    if getattr(engine, "mode", None) != "editor":
        return False
    if "--classic-ui" in sys.argv or os.environ.get("HEAVEN3D_CLASSIC_UI", "") in ("1", "true", "yes"):
        return False
    return True


def _h3d_this_module():
    """The engine module object (whatever name it was loaded under)."""
    g = globals()
    mod = sys.modules.get(__name__)
    if getattr(mod, "__dict__", None) is g:
        return mod
    for mod in list(sys.modules.values()):
        if getattr(mod, "__dict__", None) is g:
            return mod
    return None


def _h3d_editor_attach(engine):
    """Load h3d_editor and give this engine its Vitrail editor.  Returns the
    editor, or None (the classic UI then stays in charge)."""
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        import h3d_editor
    except Exception as ex:
        _H3D_EDITOR_STATE["error"] = "h3d_editor could not be loaded (%s)" % ex
        Log.warn("[Editor] %s - using the classic UI" % _H3D_EDITOR_STATE["error"])
        return None
    _H3D_EDITOR_STATE["module"] = h3d_editor
    try:
        return h3d_editor.attach(_h3d_this_module(), engine)
    except Exception as ex:
        import traceback as _tb
        _H3D_EDITOR_STATE["error"] = "%s: %s" % (type(ex).__name__, ex)
        Log.warn("[Editor] the Vitrail editor failed to start (%s) - using the classic UI\n%s"
                 % (_H3D_EDITOR_STATE["error"], _tb.format_exc()))
        try:
            h3d_editor.detach(engine)
        except Exception:
            pass
        return None


_H3D_EDITOR_PREV_INIT = HeavenEngine.__init__


def _h3d_editor_close():
    """Take the editor off the engine it was attached to (it wraps
    pygame.event.get, so it must not outlive its engine)."""
    ed = _H3D_EDITOR_STATE.get("editor")
    _H3D_EDITOR_STATE["editor"] = None
    if ed is not None:
        try:
            ed.uninstall()
        except Exception:
            pass


def _h3d_editor_engine_init(self, *a, **kw):
    _h3d_editor_close()                      # a new engine (window) replaces the old one
    _H3D_EDITOR_PREV_INIT(self, *a, **kw)
    self._vt_editor = None
    if _h3d_editor_wanted(self):
        self._vt_editor = _h3d_editor_attach(self)
        _H3D_EDITOR_STATE["editor"] = self._vt_editor


HeavenEngine.__init__ = _h3d_editor_engine_init

_H3D_EDITOR_PREV_RELEASE = HeavenEngine.release


def _h3d_editor_release(self):
    if _H3D_EDITOR_STATE.get("editor") is not None and getattr(self, "_vt_editor", None) is _H3D_EDITOR_STATE["editor"]:
        _h3d_editor_close()
    return _H3D_EDITOR_PREV_RELEASE(self)


HeavenEngine.release = _h3d_editor_release
