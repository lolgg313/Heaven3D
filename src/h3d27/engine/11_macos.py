# @heaven3d-part h3d27/engine/11_macos.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
    # ---------------------------------------------------------------
    # 11. macOS
    #     * OpenGL 4.1 core / forward-compatible context (the only modern
    #       GL macOS offers; every Heaven3D shader is GLSL 330 core).
    #     * Tk can't share the Cocoa main thread with SDL, so on macOS no
    #       Tk window lives in the engine process: askfloat / askstring /
    #       askcolor / file dialogs / message boxes run in a tiny helper
    #       process, and the Library popup becomes an in-editor dropdown.
    #     HEAVEN3D_MAC_COMPAT=1 turns the same code on elsewhere (tests).
    # ---------------------------------------------------------------
    import subprocess as _h3d27_sp
    H3D_MAC = sys.platform == "darwin"
    H3D_MAC_COMPAT = H3D_MAC or os.environ.get("HEAVEN3D_MAC_COMPAT") == "1"

    if H3D_MAC:
        os.environ.setdefault("SDL_VIDEO_HIGHDPI_DISABLED", "1")   # 1 pixel = 1 point; no quarter-size viewport on Retina
        os.environ.setdefault("SDL_MAC_CTRL_CLICK_EMULATE_RIGHT_CLICK", "1")

    if H3D_MAC_COMPAT:
        _H3D27_REAL_SET_MODE = pygame.display.set_mode

        def _h3d27_mac_set_mode(*a, **kw):
            flags = kw.get("flags", a[1] if len(a) > 1 else 0)
            if flags & OPENGL:
                try:
                    pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MAJOR_VERSION, 4)
                    pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MINOR_VERSION, 1)
                    pygame.display.gl_set_attribute(pygame.GL_CONTEXT_PROFILE_MASK, pygame.GL_CONTEXT_PROFILE_CORE)
                    pygame.display.gl_set_attribute(pygame.GL_CONTEXT_FLAGS, pygame.GL_CONTEXT_FORWARD_COMPATIBLE_FLAG)
                    pygame.display.gl_set_attribute(pygame.GL_DEPTH_SIZE, 24)
                except Exception as ex:
                    Log.warn(f"[macOS] GL attributes: {ex}")
            return _H3D27_REAL_SET_MODE(*a, **kw)

        pygame.display.set_mode = _h3d27_mac_set_mode

    _H3D27_DIALOG_CHILD = r'''
import sys, json
import tkinter as tk
from tkinter import simpledialog, colorchooser, filedialog, messagebox
req = json.loads(sys.argv[1])
mod = {"simpledialog": simpledialog, "colorchooser": colorchooser,
       "filedialog": filedialog, "messagebox": messagebox}[req["module"]]
kw = req["kw"]
if "filetypes" in kw:
    kw["filetypes"] = [tuple(t) for t in kw["filetypes"]]
if isinstance(kw.get("color"), list):
    kw["color"] = tuple(kw["color"])
root = tk.Tk()
root.withdraw()
try:
    root.attributes("-topmost", True)
    root.lift(); root.focus_force()
except Exception:
    pass
res = getattr(mod, req["name"])(*req["args"], parent=root, **kw)
if isinstance(res, tuple):
    res = list(res)
sys.stdout.write("H3DRESULT" + json.dumps(res))
'''

    def _h3d27_jsonable(v):
        if isinstance(v, (list, tuple)):
            return [_h3d27_jsonable(x) for x in v]
        if isinstance(v, (str, int, float, bool)) or v is None:
            return v
        return str(v)

    def _h3d27_remote_dialog(module, name, args, kw):
        """Run one tkinter dialog in a helper process, keeping the game
        window responsive (events pumped) until the player answers."""
        kw = {k: _h3d27_jsonable(v) for k, v in kw.items() if k not in ("parent", "master")}
        req = json.dumps({"module": module, "name": name, "args": _h3d27_jsonable(list(args)), "kw": kw})
        try:
            p = _h3d27_sp.Popen([sys.executable, "-E", "-s", "-c", _H3D27_DIALOG_CHILD, req],
                                stdout=_h3d27_sp.PIPE, stderr=_h3d27_sp.PIPE, text=True)
        except Exception as ex:
            Log.error(f"[macOS] dialog helper: {ex}")
            return None
        while p.poll() is None:
            try:
                pygame.event.pump()
            except Exception:
                pass
            time.sleep(0.03)
        out, err = p.communicate()
        if "H3DRESULT" not in (out or ""):
            if err:
                Log.warn(f"[macOS] dialog: {err.strip()[-300:]}")
            return None
        res = json.loads(out.split("H3DRESULT", 1)[1])
        if module == "colorchooser" and isinstance(res, list):
            rgb = tuple(res[0]) if isinstance(res[0], list) else res[0]
            return (rgb, res[1])
        if module == "filedialog" and isinstance(res, list):
            return tuple(res)
        return res

    class _H3D27StubTk:
        """Stands in for tkinter.Tk / Toplevel in the engine process."""
        def __init__(self, *a, **kw):
            self._alive = True

        def winfo_exists(self):
            return self._alive

        def destroy(self):
            self._alive = False

        def __getattr__(self, name):
            if name.startswith("_"):
                raise AttributeError(name)
            if name.startswith("winfo_"):
                return lambda *a, **kw: 1
            return lambda *a, **kw: None

    def _h3d27_install_mac_dialogs():
        try:
            import tkinter
            from tkinter import simpledialog, colorchooser, filedialog, messagebox
        except Exception:
            return              # no tkinter at all: dialogs are skipped as before
        if getattr(tkinter, "_h3d27_mac", False):
            return
        tkinter._h3d27_mac = True
        tkinter.Tk = _H3D27StubTk

        def make(module, name):
            return lambda *a, **kw: _h3d27_remote_dialog(module, name, a, kw)
        for mod, names in ((simpledialog, ("askfloat", "askstring", "askinteger")),
                           (colorchooser, ("askcolor",)),
                           (filedialog, ("askopenfilename", "asksaveasfilename", "askdirectory", "askopenfilenames")),
                           (messagebox, ("showinfo", "showwarning", "showerror", "askyesno",
                                         "askokcancel", "askyesnocancel", "askquestion", "askretrycancel"))):
            for n in names:
                setattr(mod, n, make(mod.__name__.split(".")[-1], n))

    # ---- Library popup -> in-editor dropdown -------------------------
    class _H3D27LibRecorder:
        """A fake `tkinter` module: running the real open_library_menu (and
        any wrappers that add buttons, e.g. Flying Enemy) against it records
        the sections and buttons instead of opening a Tk window."""
        def __init__(self):
            self.items = []
            rec = self

            class Widget(_H3D27StubTk):
                def __init__(self, master=None, *a, **kw):
                    super().__init__()
                    text = kw.get("text", "")
                    if isinstance(self, Button):
                        rec.items.append(("button", text, kw.get("command")))
                    elif isinstance(self, Label) and text:
                        rec.items.append(("label", text, None))

            class Button(Widget):
                pass

            class Label(Widget):
                pass
            self.Toplevel = Widget
            self.Tk = _H3D27StubTk
            self.Frame = Widget
            self.Button = Button
            self.Label = Label
            self.Canvas = Widget
            self.Scrollbar = Widget

        def __getattr__(self, name):
            if name.isupper():
                return name.lower()
            return _H3D27StubTk

    _H3D27_LIB_STATE = {"open": False, "items": []}

    def _h3d27_capture_library(self, real):
        rec = _H3D27LibRecorder()
        saved = sys.modules.get("tkinter")
        sys.modules["tkinter"] = rec
        try:
            self._library_window = None
            real(self)
        except Exception as ex:
            Log.warn(f"[macOS] library: {ex}")
        finally:
            if saved is not None:
                sys.modules["tkinter"] = saved
            else:
                sys.modules.pop("tkinter", None)
            self._library_window = None
        return rec.items

    if H3D_MAC_COMPAT:
        _H3D27_LIB_STATE["real"] = HeavenEngine.open_library_menu

        def _h3d27_lib_cmd(cmd):
            def run():
                _H3D27_LIB_STATE["open"] = False
                try:
                    cmd()
                except Exception as ex:
                    Log.warn(f"[Library] {ex}")
            return run

        _H3D27_LIB_STATE["base"] = _H3D27_LIB_STATE["real"]

        def _h3d27_mac_library(self):
            if _H3D27_LIB_STATE.get("capturing"):
                # a wrapper installed after us called down into us: act as
                # the original Tk popup (recorded by the fake tkinter)
                return _H3D27_LIB_STATE["base"](self)
            if _H3D27_LIB_STATE["open"]:
                _H3D27_LIB_STATE["open"] = False
                return
            _H3D27_LIB_STATE["capturing"] = True
            try:
                items = _h3d27_capture_library(self, _H3D27_LIB_STATE["real"])
            finally:
                _H3D27_LIB_STATE["capturing"] = False
            # keep section titles + buttons; the popup's title/subtitle go
            items = [(k, t, _h3d27_lib_cmd(c) if k == "button" else None)
                     for k, t, c in items if k == "button" or t.isupper()]
            _H3D27_LIB_STATE.update(open=bool(items), items=items)

        def _h3d27_claim_library():
            # other patches may wrap open_library_menu later (Flying Enemy
            # adds its button that way): fold them into what we record.
            cur = HeavenEngine.__dict__.get("open_library_menu")
            if cur is not _h3d27_mac_library:
                _H3D27_LIB_STATE["real"] = cur
                HeavenEngine.open_library_menu = _h3d27_mac_library

        HeavenEngine.open_library_menu = _h3d27_mac_library
        _H3D27_PREV_BUILD_UI3 = HeavenEngine._build_ui

        def _h3d27_build_ui3(self):
            _h3d27_claim_library()
            _H3D27_PREV_BUILD_UI3(self)
            try:
                if (self.ui is None or self.mode != "editor" or self._playing
                        or not _H3D27_LIB_STATE["open"]):
                    return
                T = THEME
                items = _H3D27_LIB_STATE["items"]
                h, lh = 0.032, 0.026
                height = sum(h + 0.004 if k == "button" else lh for k, _t, _c in items) + 0.014
                x, w, top = 0.659, 0.16, 0.926       # under Library, inside the viewport
                self.ui.panel(UIRect(x, top - height, w, height), T["panel"])
                yy = top - 0.007
                for kind, text, cmd in items:
                    if kind == "label":
                        yy -= lh
                        self.ui.button(UIRect(x + 0.004, yy, w - 0.008, lh), text, lambda: None, T["panel"],
                                       align="left", text_color=T["accent"], pad_x_px=8, pad_y_px=5)
                        continue
                    yy -= h
                    self.ui.button(UIRect(x + 0.006, yy, w - 0.012, h), text, cmd, T["button"], align="left", pad_x_px=10)
                    yy -= 0.004
            except Exception as ex:
                _h3d27_log_once("mac_library", f"[macOS] library menu: {ex}")

        HeavenEngine._build_ui = _h3d27_build_ui3
        _h3d27_install_mac_dialogs()
        Log.info("[macOS] GL 4.1 core context, out-of-process dialogs, in-editor Library menu")