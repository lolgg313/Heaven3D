"""Regenerates templates/tropical_island (needs a GPU):  python tools/make_template.py

A ready-to-play tropical island: the player starts on the beach at the end of
a dirt road; change the island's shape, size and densities in the Inspector."""
import importlib.util
import os
import shutil
import sys
import tempfile

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(PKG))
DST = os.path.join(PKG, "templates", "tropical_island")

proj = tempfile.mkdtemp(prefix="h3disland_")
shutil.copytree(os.path.join(ROOT, "templates", "empty"), proj, dirs_exist_ok=True)
os.makedirs(os.path.join(proj, "packages"))
os.symlink(PKG, os.path.join(proj, "packages", "tropical_islands"))
spec = importlib.util.spec_from_file_location("Heaven3D", os.path.join(ROOT, "Heaven3D.py"))
H = importlib.util.module_from_spec(spec)
sys.modules["Heaven3D"] = H
spec.loader.exec_module(H)
H._h3d_set_splash_mode("off")
import glm
import moderngl
import pygame

W, Hh = 1280, 720
eng = H.HeavenEngine(mode="editor", width=W, height=Hh, project_path=os.path.join(proj, "main.heaven3d"))
mod = sys.modules["h3dpkg_tropical_islands"]
if eng.find_player_spawn() is None:
    eng.add_player_spawn()
node = mod.new_island(eng, {"seed": 20261101, "size": 650, "topology": "classic", "roads": 3}, select=False)
rt = mod.runtime_of(node)
e = rt.isl.extent
state = {"f": 0}
_flip = pygame.display.flip


def frame():
    state["f"] += 1
    f = state["f"]
    if f == 2:
        eng.camera.position = glm.vec3(e * 0.05, e * 0.22, e * 0.48)
        eng.camera.yaw, eng.camera.pitch = 4.0, -24.0
    if f == 40:
        eng.save_project(os.path.join(proj, "main.heaven3d"))
        os.makedirs(DST, exist_ok=True)
        shutil.copy(os.path.join(proj, "main.heaven3d"), os.path.join(DST, "main.heaven3d"))
        data = moderngl.get_context().screen.read(components=3)
        img = pygame.transform.flip(pygame.image.frombuffer(data, (W, Hh), "RGB"), False, True)
        sub = img.subsurface((int(W * 0.18), int(Hh * 0.09), int(W * 0.64), int(Hh * 0.8))).copy()
        out = pygame.transform.smoothscale(sub, (480, 270))
        pygame.image.save(out, os.path.join(DST, ".heaven3d_thumb.png"))
        print("template written to", DST)
        os._exit(0)
    _flip()


pygame.display.flip = frame
eng.run()
