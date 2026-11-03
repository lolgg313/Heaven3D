"""macOS code paths, forced on with HEAVEN3D_MAC_COMPAT=1 so they can be
tested anywhere: GL 4.1 core forward-compatible context, every shader
compiles, Library becomes an in-editor dropdown, dialogs run in a helper
process (typed into with xdotool when it's installed).
    python tests/test_mac_mode.py"""
import os, sys, shutil, tempfile, threading, time, subprocess, importlib.util
os.environ["HEAVEN3D_MAC_COMPAT"] = "1"
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
OUT = os.environ.get("H3D_SHOTS") or tempfile.mkdtemp(prefix="h3dmac_")
proj = tempfile.mkdtemp(prefix="h3dmacproj_")
shutil.copytree(os.path.join(ROOT, "templates", "empty"), proj, dirs_exist_ok=True)
spec = importlib.util.spec_from_file_location("Heaven3D", os.path.join(ROOT, "Heaven3D.py"))
H = importlib.util.module_from_spec(spec); sys.modules["Heaven3D"] = H; spec.loader.exec_module(H)
H._h3d_set_splash_mode("off")
import pygame, moderngl, glm, tkinter
from tkinter import simpledialog
res = []


def check(n, c, d=""):
    res.append(bool(c)); print(("PASS " if c else "FAIL ") + n + (f"  [{d}]" if d else ""), flush=True)


errors = []
_err = H.Log.error
H.Log.error = lambda m, *a, **k: (errors.append(str(m)), _err(m, *a, **k))
W, Hh = 1400, 860
eng = H.HeavenEngine(mode="editor", width=W, height=Hh, project_path=os.path.join(proj, "main.heaven3d"))
info = eng.ctx.info
prof = eng.ctx.version_code
check("OpenGL core context >= 4.1", prof >= 410, f"{info.get('GL_VERSION')}")
flags = pygame.display.gl_get_attribute(pygame.GL_CONTEXT_FLAGS)
check("forward-compatible flag requested", flags & pygame.GL_CONTEXT_FORWARD_COMPATIBLE_FLAG, flags)
check("profile = core", pygame.display.gl_get_attribute(pygame.GL_CONTEXT_PROFILE_MASK) == pygame.GL_CONTEXT_PROFILE_CORE)
check("no Tk window in the engine process", tkinter.Tk is H._H3D27StubTk)
eng.add_cube(); eng.camera.position = glm.vec3(4, 3, 6); eng.camera.yaw = -35; eng.camera.pitch = -20


def shot(name):
    data = moderngl.get_context().screen.read(components=3)
    pygame.image.save(pygame.transform.flip(pygame.image.frombuffer(data, (W, Hh), "RGB"), False, True), os.path.join(OUT, name + ".png"))


def typer(title, text):
    for _ in range(100):
        r = subprocess.run(["xdotool", "search", "--name", title], capture_output=True, text=True)
        if r.stdout.strip():
            wid = r.stdout.split()[0]
            time.sleep(0.6)
            subprocess.run(["xdotool", "windowfocus", "--sync", wid], capture_output=True)
            subprocess.run(["xdotool", "type", text]); time.sleep(0.2)
            subprocess.run(["xdotool", "key", "Return"])
            return
        time.sleep(0.1)


st = {"f": 0}
_flip = pygame.display.flip


def flip():
    st["f"] += 1
    f = st["f"]
    if f == 2:
        eng.open_library_menu()
        st["items"] = items = H._H3D27_LIB_STATE["items"]
        labels = [t for k, t, c in items if k == "button"]
        check("Library opens as in-editor dropdown", H._H3D27_LIB_STATE["open"] and "Point Light" in labels, labels)
        check("Library keeps extra buttons from other patches", any("Flying Enemy" in t for t in labels), labels)
    if f == 6:
        items = st["items"]
        shot("mac_library_dropdown")
        n0 = len(eng.scene.root.children)
        run = next(c for k, t, c in items if k == "button" and t == "Point Light")
        run()
        check("Library button adds the object and closes", len(eng.scene.root.children) == n0 + 1 and not H._H3D27_LIB_STATE["open"])
    if f == 10:
        H._h3d27_view(eng)["shading"] = "lit_wire"
    if f == 14:
        shot("mac_wire")
        check("no GL / shader errors so far", not errors, errors[:3])
        if shutil.which("xdotool"):
            threading.Thread(target=typer, args=("Heaven3D test", "42.5"), daemon=True).start()
            v = simpledialog.askfloat("Heaven3D test", "Value:", parent=eng._tk_root_window(), minvalue=0.0)
            check("askfloat runs in a helper process and returns the value", v == 42.5, v)
            threading.Thread(target=typer, args=("Rename test", "My Car"), daemon=True).start()
            s = simpledialog.askstring("Rename test", "Name:", parent=eng._tk_root_window())
            check("askstring works too", s == "My Car", s)
        else:
            print("SKIP dialog typing (install xdotool)")
        print("RESULT", "PASS" if all(res) else "FAIL", f"{sum(res)}/{len(res)}", "shots in", OUT, flush=True)
        os._exit(0 if all(res) else 1)
    _flip()


pygame.display.flip = flip
eng.run()
