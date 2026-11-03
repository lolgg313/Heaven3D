"""Regenerates templates/character_town (needs a GPU):  python tools/make_template.py

A small town square: a crowd of NPCs walking a loop, a couple waiting at a
bench path, zombies patrolling the alley behind the houses, a soldier with a
rifle (hitscan) and a ramp + stairs that show off the foot IK."""
import math
import os
import random
import shutil
import sys
import tempfile
import importlib.util

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(PKG))
DST = os.path.join(PKG, "templates", "character_town")

proj = tempfile.mkdtemp(prefix="h3dtown_")
shutil.copytree(os.path.join(ROOT, "templates", "empty"), proj, dirs_exist_ok=True)
os.makedirs(os.path.join(proj, "packages"))
os.symlink(PKG, os.path.join(proj, "packages", "mh_characters"))
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
for n in eng.scene.root.traverse():
    if n.is_ocean:
        n.ocean_settings.enabled = False
mod = sys.modules["h3dpkg_mh_characters"]
core = sys.modules["h3dpkg_mh_characters_mh_core"]
lib = mod._lib()
rng = random.Random(7)

g = eng.add_plane(size=160.0, name="Ground")
g.material.base_color = (0.42, 0.44, 0.40, 1.0)
g.material.roughness = 0.95
plaza = eng.add_plane(size=26.0, name="Plaza")
plaza.position = glm.vec3(0, 0.01, 0)
plaza.material.base_color = (0.62, 0.58, 0.52, 1.0)


def box(name, pos, size, color, rot=0.0):
    b = eng.add_cube(size=1.0, name=name)
    b.position = glm.vec3(*pos)
    b.scale = glm.vec3(*size)
    b.rotation_euler.y = rot
    b.material.base_color = color
    b.material.roughness = 0.85
    return b


cols = [(0.78, 0.70, 0.58, 1), (0.66, 0.42, 0.36, 1), (0.55, 0.60, 0.66, 1), (0.82, 0.78, 0.68, 1)]
for i, (x, z, w, d, h) in enumerate([(-17, -8, 7, 9, 7), (-17, 6, 7, 8, 9), (17, -6, 7, 10, 8), (17, 8, 7, 7, 6),
                                       (-4, -19, 10, 6, 10), (8, -19, 8, 6, 7)]):
    box(f"House {i + 1}", (x, h / 2, z), (w, h, d), cols[i % len(cols)])
fountain = box("Fountain", (0, 0.4, 0), (3.0, 0.8, 3.0), (0.6, 0.62, 0.66, 1))
# ramp + stairs for foot IK
ramp = box("Ramp", (6.0, 0.75, 16.0), (6.0, 0.5, 8.0), (0.5, 0.48, 0.45, 1))
ramp.rotation_euler.x = 14.0
for k in range(6):
    h = 0.18 * (k + 1)
    box(f"Step {k + 1}", (-7.0, h / 2, 14.0 - 0.45 * k), (5.0, h, 0.45), (0.58, 0.55, 0.5, 1))
for b in [n for n in eng.scene.root.children if n.name.startswith(("Ramp", "Step"))]:
    b.collider = H.BoxCollider(size=(1.0, 1.0, 1.0))

spawn = eng.add_player_spawn()
spawn.position = glm.vec3(0.0, 1.0, 20.0)

# crowd loop around the fountain
square = mod.new_path(eng, glm.vec3(0, 0, 0), radius=7.0, count=8, select=False)
pts = mod._waypoints(eng, square.name)
for i in range(7):
    a = pts[i % len(pts)][1].position
    b = pts[(i + 1) % len(pts)][1].position
    pos = a + (b - a) * rng.uniform(0.2, 0.8)
    n = mod.place_character(eng, core.random_spec(lib, seed=100 + i), pos=glm.vec3(pos), name="Townsperson", select=False)
    eng.attach_blueprint(n, mod.CLS["NPCWalker"])
    n.blueprints[-1].properties.update({"path": square.name, "mode": "loop" if i % 3 else "random",
                                        "speed": round(rng.uniform(1.0, 1.45), 2),
                                        "wait_time": round(rng.uniform(0.3, 2.5), 1)})
# walkers on the stairs and the ramp (foot IK showcase)
for nm, a, b, seed, gender in (("Stairs", (-7.0, 0.0, 16.6), (-7.0, 1.1, 11.9), 42, "female"),
                               ("Ramp", (6.0, 0.0, 21.0), (6.0, 2.0, 12.8), 43, "male")):
    pth = mod.new_path(eng, glm.vec3(*a), radius=1.0, count=2, select=False)
    wp = mod._waypoints(eng, pth.name)
    wp[0][1].position, wp[1][1].position = glm.vec3(*a), glm.vec3(*b)
    wp[0][2].properties["wait"] = wp[1][2].properties["wait"] = 1.0
    c = mod.place_character(eng, core.random_spec(lib, seed=seed, gender=gender), pos=glm.vec3(*a),
                            name=f"{nm} Walker", select=False)
    eng.attach_blueprint(c, mod.CLS["NPCWalker"])
    c.blueprints[-1].properties.update({"path": pth.name, "mode": "pingpong", "speed": 1.1, "greet_player": False})
# zombies in the alley behind the west houses, soldier on the east side
alley = mod.new_path(eng, glm.vec3(-26, 0, 0), radius=6.0, count=4, select=False)
for i in range(2):
    z = mod.place_character(eng, core.random_spec(lib, seed=300 + i, style="zombie"), pos=glm.vec3(-26, 0, -4 + 8 * i),
                            name="Zombie", select=False)
    eng.attach_blueprint(z, mod.CLS["MHEnemy"])
    z.blueprints[-1].properties.update({"path": alley.name, "patrol_speed": 0.8, "chase_speed": 2.0,
                                        "attack_range": 1.4, "require_los": True, "max_health": 60.0})
guard = mod.new_path(eng, glm.vec3(26, 0, 0), radius=5.0, count=4, select=False)
s = mod.place_character(eng, core.random_spec(lib, seed=501, gender="male", style="soldier"), pos=glm.vec3(26, 0, 5),
                        name="Soldier", select=False)
eng.attach_blueprint(s, mod.CLS["MHEnemy"])
s.blueprints[-1].properties.update({"path": guard.name, "attack_style": "hitscan", "attack_damage": 6.0,
                                    "attack_cooldown": 1.2, "detect_range": 22.0, "max_health": 120.0})
eng.camera.position = glm.vec3(7.0, 3.6, 15.5)
eng.camera.yaw, eng.camera.pitch = 22.0, -12.0
eng.deselect()
state = {"f": 0}
_flip = pygame.display.flip


def frame():
    f = state["f"] = state["f"] + 1
    if f == 6:
        eng.save_project(os.path.join(proj, "main.heaven3d"))
        os.makedirs(DST, exist_ok=True)
        shutil.copy(os.path.join(proj, "main.heaven3d"), os.path.join(DST, "main.heaven3d"))
        # thumbnail: the 3D viewport, without editor panels
        data = moderngl.get_context().screen.read(components=3)
        img = pygame.transform.flip(pygame.image.frombuffer(data, (W, Hh), "RGB"), False, True)
        x0, x1 = int(W * 0.19), int(W * 0.77)
        y0, y1 = int(Hh * 0.14), int(Hh * 0.86)
        crop = img.subsurface((x0, y0, x1 - x0, y1 - y0))
        th = pygame.transform.smoothscale(crop, (384, int(384 * (y1 - y0) / (x1 - x0))))
        out = pygame.Surface((384, 216))
        out.blit(th, (0, (216 - th.get_height()) // 2))
        pygame.image.save(out, os.path.join(DST, ".heaven3d_thumb.png"))
        print("template written to", DST)
        os._exit(0)
    _flip()


pygame.display.flip = frame
eng.run()
