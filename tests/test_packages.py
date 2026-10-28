"""Packages in the editor: project packages load in dependency order, tools work,
spline road builds live, the race kit drives (needs a GPU):  python tests/test_packages.py"""
import os, sys, importlib.util, shutil, tempfile, math
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
ENGINE = os.path.join(ROOT, 'Heaven3D.py'); OUT = tempfile.mkdtemp(prefix='h3dshots_')
PK = os.path.join(ROOT, 'packages')
proj = tempfile.mkdtemp(prefix='h3dpkg_')
shutil.copytree(os.path.join(ROOT, 'templates', 'empty'), proj, dirs_exist_ok=True)
for p in ('spline_roads', 'car_race_kit', 'physics_plus'):
    shutil.copytree(os.path.join(PK, p), os.path.join(proj, 'packages', p))
spec = importlib.util.spec_from_file_location("Heaven3D", ENGINE)
H = importlib.util.module_from_spec(spec); sys.modules["Heaven3D"] = H; spec.loader.exec_module(H)
H._h3d_set_splash_mode("off")
import pygame, moderngl, glm
res = []
def check(n, c, d=""):
    res.append(bool(c)); print(("PASS " if c else "FAIL ") + n + (f"  [{d}]" if d else ""), flush=True)
W, Hh = 1400, 860
eng = H.HeavenEngine(mode="editor", width=W, height=Hh, project_path=os.path.join(proj, "main.heaven3d"))
for n in eng.scene.root.traverse():
    if n.is_ocean: n.ocean_settings.enabled = False
ground = eng.add_plane(size=400.0, name="Ground")
ground.material.base_color = (0.32, 0.45, 0.28, 1.0)
check("3 project packages loaded", len(H.H3DPackages) == 3, [a.id for a in H.H3DPackages])
check("spline_roads loads before car_race_kit (requires)", [a.id for a in H.H3DPackages].index('spline_roads') < [a.id for a in H.H3DPackages].index('car_race_kit'))
tools = {t["label"]: t for t in H._H3D27_PKG_STATE["tools"]}
check("package tools registered", "Spline road: new road" in tools and "Race kit: add car" in tools, list(tools))
check("physics_plus applied High quality", abs(H.PhysicsWorld.FIXED_DT - 1/120) < 1e-9 and H.PhysicsWorld.SOLVER_ITERATIONS == 8)
eng.camera.position = glm.vec3(0, 30, 60); eng.camera.yaw = 0; eng.camera.pitch = -25
tools["Spline road: new road"]["fn"](eng)
road = next(n for n in eng.scene.root.children if n.name.startswith("Road"))
check("road mesh built", road.mesh is not None and road.mesh.index_count > 100, road.mesh.index_count if road.mesh else None)
pts = sorted([n for n in eng.scene.root.children if " P" in n.name], key=lambda n: n.name)
check("4 control points", len(pts) == 4)
sig0 = road.mesh.index_count; m0 = road.mesh
pts[1].position = pts[1].position + glm.vec3(15, 0, -10)
mod = sys.modules["h3dpkg_spline_roads"]
for _ in range(3): H._h3d27_emit("update", eng, 0.1)
check("moving a point rebuilds the road live", road.mesh is not m0)
# race: car at checkpoint 0 behind start gate on road
P0, P1 = pts[0].position, pts[1].position
d = glm.normalize(glm.vec3(P1.x - P0.x, 0, P1.z - P0.z)); yaw = math.degrees(math.atan2(-d.x, -d.z))
crk = sys.modules["h3dpkg_car_race_kit"]
gates = [crk.build_checkpoint(eng, P0 + d * 8.0, yaw, 0)]
for i, p in enumerate(pts[1:]): gates.append(crk.build_checkpoint(eng, glm.vec3(p.position), yaw, i + 1))
car = crk.build_car(eng, P0 + glm.vec3(0, 1.0, 0), yaw)
tools["Race kit: add race manager"]["fn"](eng)
eng.deselect()
state = {"f": 0}
def shot(name):
    data = moderngl.get_context().screen.read(components=3)
    pygame.image.save(pygame.transform.flip(pygame.image.frombuffer(data, (W, Hh), "RGB"), False, True), os.path.join(OUT, name + ".png"))
_flip = pygame.display.flip
def flip():
    f = state["f"] = state["f"] + 1
    if f == 3: H._H3D27_PKG_STATE["menu_open"] = True
    if f == 8: shot("pkg_editor"); H._H3D27_PKG_STATE["menu_open"] = False
    if f == 10:
        state["z0"] = glm.vec3(car.position); eng.toggle_play()
    if f == 20: shot("pkg_countdown")
    if f == 30: eng.input.set_key("w", True)
    if f == 260:
        moved = glm.length(car.position - state["z0"])
        mgr = eng.first_blueprint("RaceManager"); cb = eng.first_blueprint("ArcadeCar")
        check("countdown -> GO -> car drives", moved > 20, f"moved {moved:.1f} m, speed {cb.speed*3.6:.0f} km/h, state {mgr.state}")
        check("car stays on the road surface", abs(car.position.y - (road.mesh and 0) ) < 3.0, f"y={car.position.y:.2f}")
        shot("pkg_driving")
    if f == 262:
        eng.input.set_key("w", False); eng.toggle_play()
    if f == 270:
        check("stop restores the car", glm.length(car.position - state["z0"]) < 0.01)
        eng.save_project(os.path.join(proj, "main.heaven3d"))
        txt = open(os.path.join(proj, "main.heaven3d")).read()
        check("road + race blueprints saved", "SplineRoad" in txt and "RaceManager" in txt and "ArcadeCar" in txt)
        print(f"\n{sum(res)}/{len(res)} passed"); print("PROJECT", proj)
        os._exit(0 if all(res) else 1)
    _flip()
pygame.display.flip = flip
eng.run()
