"""Esc pause menu in a running game (needs a GPU):  python tests/test_pause_menu.py"""
import os, sys, importlib.util, json, shutil, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
ENGINE = os.path.join(ROOT, 'Heaven3D.py'); OUT = tempfile.mkdtemp(prefix='h3dshots_')
proj = tempfile.mkdtemp(prefix='h3dpause_')
shutil.copytree(os.path.join(ROOT, 'templates', 'coin_collector'), proj, dirs_exist_ok=True)
spec = importlib.util.spec_from_file_location("Heaven3D", ENGINE)
H = importlib.util.module_from_spec(spec); sys.modules["Heaven3D"] = H; spec.loader.exec_module(H)
H._h3d_set_splash_mode("off")
import pygame, moderngl
eng = H.HeavenEngine(mode="runtime", width=1280, height=720, project_path=os.path.join(proj, "main.heaven3d"))
W, Hh = 1280, 720
state = {"f": 0, "log": []}
def shot(name):
    data = moderngl.get_context().screen.read(components=3)
    img = pygame.transform.flip(pygame.image.frombuffer(data, (W, Hh), "RGB"), False, True)
    pygame.image.save(img, os.path.join(OUT, name + ".png"))
def click(fx, fy):
    pos = (int(fx * W), int(fy * Hh))
    pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=pos))
    pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=pos))
def key(k):
    pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=k, mod=0, unicode='', scancode=0))
    pygame.event.post(pygame.event.Event(pygame.KEYUP, key=k, mod=0, unicode='', scancode=0))
_flip = pygame.display.flip
def flip():
    f = state["f"] = state["f"] + 1
    if f == 40: state["log"].append(("playing before Esc", bool(eng._playing))); key(pygame.K_ESCAPE)
    if f == 60:
        state["log"].append(("paused after Esc", bool(getattr(eng, "_fp2_paused", False)) and not eng._playing)); shot("pause_menu")
    if f == 62: click(0.50, 0.4275)      # RESUME
    if f == 80: state["log"].append(("resumed after RESUME", not getattr(eng, "_fp2_paused", False) and bool(eng._playing)))
    if f == 82: key(pygame.K_ESCAPE)
    if f == 100: click(0.50, 0.6475)    # EXIT
    if f > 400: state["log"].append(("EXIT quits", False)); finish()
    _flip()
def finish():
    ok = all(v for _, v in state["log"])
    for n, v in state["log"]: print(("PASS " if v else "FAIL ") + n, flush=True)
    os._exit(0 if ok else 1)
pygame.display.flip = flip
eng.run()
state["log"].append(("EXIT quits", state["f"] < 400))
finish()
