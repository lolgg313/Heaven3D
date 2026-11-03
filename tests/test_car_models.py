"""Car Race Kit: replace the dummy car's body and wheels with your own models.
Fits them, survives save -> move project -> load without duplicates, wheels
spin and steer while driving (needs a GPU):  python tests/test_car_models.py"""
import os, sys, shutil, tempfile, subprocess, math, json
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
ENGINE = os.path.join(ROOT, 'Heaven3D.py')
TRUCK = os.path.join(HERE, 'importers', 'assets', 'glb', 'CesiumMilkTruck.glb')


def make_wheel_obj(path, seg=24, r=0.5, w=0.32):
    """A tyre: cylinder whose axle is the X axis."""
    v, f = [], []
    for i in range(seg):
        a = 2 * math.pi * i / seg
        y, z = r * math.cos(a), r * math.sin(a)
        v += [(-w / 2, y, z), (w / 2, y, z)]
    v += [(-w / 2, 0, 0), (w / 2, 0, 0)]
    cl, cr = len(v) - 1, len(v)
    for i in range(seg):
        a, b = 2 * i + 1, 2 * ((i + 1) % seg) + 1
        f += [(a, b, b + 1, a + 1), (cl, b, a), (cr, a + 1, b + 1)]
    with open(path, 'w') as fh:
        fh.writelines(f"v {x:.4f} {y:.4f} {z:.4f}\n" for x, y, z in v)
        fh.writelines("f " + " ".join(map(str, t)) + "\n" for t in f)


def boot(project, mode):
    import importlib.util
    spec = importlib.util.spec_from_file_location("Heaven3D", ENGINE)
    H = importlib.util.module_from_spec(spec); sys.modules["Heaven3D"] = H; spec.loader.exec_module(H)
    H._h3d_set_splash_mode("off")
    eng = H.HeavenEngine(mode=mode, width=1280, height=720, project_path=os.path.join(project, "main.heaven3d"))
    return H, eng


res = []


def check(n, c, d=""):
    res.append(bool(c)); print(("PASS " if c else "FAIL ") + n + (f"  [{d}]" if d else ""), flush=True)


def summary(eng, H, car):
    kids = [c.name for c in car.children]
    body = [c for c in car.children if c.name == "Custom Body"]
    piv = [c for c in car.children if c.name.startswith("Wheel Pivot")]
    return kids, body, piv


def phase1(project, outside, shots):
    H, eng = boot(project, "editor")
    import glm, pygame, moderngl
    for n in eng.scene.root.traverse():
        if n.is_ocean: n.ocean_settings.enabled = False
    eng.add_plane(size=200.0, name="Ground")
    crk = sys.modules["h3dpkg_car_race_kit"]
    car = crk.build_car(eng, glm.vec3(0, 1.0, 0), 0.0)
    bp = next(b for b in car.blueprints if b.__class__.__name__ == "ArcadeCar")
    check("new properties in Inspector", all(k in bp.properties for k in ("body_model", "wheel_model", "body_scale", "wheel_turn")))
    bp.properties["body_model"] = os.path.join(outside, "CesiumMilkTruck.glb")
    bp.properties["wheel_model"] = os.path.join(outside, "tyre.obj")
    H._h3d27_emit("update", eng, 0.3)
    kids, body, piv = summary(eng, H, car)
    check("custom body loaded as a car child", len(body) == 1, kids)
    check("built-in body parts hidden", all(not c.visible for c in car.children if c.name in ("Body", "Cabin", "Spoiler")))
    check("built-in wheels hidden", all(not c.visible for c in car.children if c.name.startswith("Wheel ") and "Pivot" not in c.name))
    check("4 wheel pivots with a tyre each", len(piv) == 4 and all(len(p.children) == 1 for p in piv))
    lo, hi = crk._bounds(eng, body[0], car); ext = hi - lo
    check("body auto-turned lengthwise and fitted to 4.3 m", abs(ext[2] - 4.3) < 0.05 and ext[0] < ext[2], f"{ext}")
    check("built-in lights hidden too (all copies)", all(not c.visible for c in car.children if c.name in ("Headlight", "Taillight")))
    lo, hi = crk._bounds(eng, piv[0].children[0], piv[0]); ws = hi - lo
    check("wheel auto-fitted to 0.74 m, axle along X", abs(max(ws[1], ws[2]) - 0.74) < 0.02 and ws[0] < 0.4, f"{ws}")
    check("wheel centred on its pivot", abs((lo[1] + hi[1]) / 2) < 1e-3 and abs((lo[2] + hi[2]) / 2) < 1e-3)
    wl = eng._spawn_mesh_local_aabb(car)
    check("body sits on the wheels (bottom near wheel bottom)", abs(float(wl[0].y) - (-0.55)) < 0.1, f"{wl[0].y:.2f}")
    # Inspector edit: scale + re-run must not duplicate
    bp.properties["wheel_scale"] = 1.2
    H._h3d27_emit("update", eng, 0.3)
    kids, body, piv = summary(eng, H, car)
    lo, hi = crk._bounds(eng, piv[0].children[0], piv[0]); ws = hi - lo
    check("wheel_scale re-fits live", abs(max(ws[1], ws[2]) - 0.888) < 0.02 and len(piv) == 4 and len(body) == 1, f"{ws}")
    bp.properties["wheel_scale"] = 1.0
    bp.properties["wheel_base"] = 1.55; bp.properties["wheel_track"] = 1.0
    H._h3d27_emit("update", eng, 0.3)
    kids, body, piv = summary(eng, H, car)
    check("wheel_base / wheel_track move the wheels", abs(abs(piv[0].position.z) - 1.55) < 1e-4 and abs(abs(piv[0].position.x) - 1.0) < 1e-4)
    bp.properties["body_turn"] = 180.0
    H._h3d27_emit("update", eng, 0.3)
    lo, hi = crk._bounds(eng, body[0], car); ext = hi - lo
    check("body_turn 180 keeps the fit", abs(ext[2] - 4.3) < 0.05 and len(summary(eng, H, car)[1]) == 1, f"{ext}")
    bp.properties["body_turn"] = 0.0
    H._h3d27_emit("update", eng, 0.3)
    eng.camera.position = glm.vec3(5.5, 2.5, 6.5); eng.camera.yaw = 40; eng.camera.pitch = -14
    eng.deselect()
    st = {"f": 0}
    W, Hh = 1280, 720
    _flip = pygame.display.flip

    def flip():
        st["f"] += 1
        if st["f"] == 6:
            data = moderngl.get_context().screen.read(components=3)
            pygame.image.save(pygame.transform.flip(pygame.image.frombuffer(data, (W, Hh), "RGB"), False, True), os.path.join(shots, "car_custom_editor.png"))
            eng.save_project()
            raise SystemExit
        _flip()
    pygame.display.flip = flip
    try:
        eng.run()
    except SystemExit:
        pass
    raw = open(os.path.join(project, "main.heaven3d")).read()
    check("model paths saved project-relative", "assets/imported/CesiumMilkTruck.glb" in raw and "assets/imported/tyre.obj" in raw and outside not in raw)
    check("models copied into the project", os.path.isfile(os.path.join(project, "assets", "imported", "CesiumMilkTruck.glb")))
    print("PHASE1", sum(res), len(res), flush=True)
    os._exit(0 if all(res) else 1)


def phase2(project, shots):
    H, eng = boot(project, "runtime")
    import glm, pygame, moderngl
    car = eng.first_blueprint("ArcadeCar")
    kids, body, piv = summary(eng, H, car.node)
    check("after move + load: exactly one custom body", len(body) == 1, kids)
    check("after move + load: 4 pivots, one tyre each", len(piv) == 4 and all(len(p.children) == 1 for p in piv), [len(p.children) for p in piv])
    check("built-in parts still hidden after load", all(not c.visible for c in car.node.children if c.name in ("Body", "Cabin")))
    W, Hh = 1280, 720
    st = {"f": 0}
    _flip = pygame.display.flip

    def flip():
        st["f"] += 1
        if st["f"] == 5:
            st["p0"] = glm.vec3(car.node.position); eng.input.set_key("w", True); eng.input.set_key("a", True)
        if st["f"] == 140:
            data = moderngl.get_context().screen.read(components=3)
            pygame.image.save(pygame.transform.flip(pygame.image.frombuffer(data, (W, Hh), "RGB"), False, True), os.path.join(shots, "car_custom_drive.png"))
            front = next(p for p in piv if p.position.z < 0)
            back = next(p for p in piv if p.position.z > 0)
            moved = glm.length(car.node.position - st["p0"])
            check("car drives with custom models", moved > 5.0, f"{moved:.1f} m")
            check("wheels spin", abs(back.rotation_euler.x) > 1.0, f"{back.rotation_euler.x:.0f}")
            check("front wheels steer, rear don't", abs(front.rotation_euler.y) > 5 and abs(back.rotation_euler.y) < 1e-6, f"{front.rotation_euler.y:.0f}")
            print("PHASE2", sum(res), len(res), flush=True)
            os._exit(0 if all(res) else 1)
        _flip()
    pygame.display.flip = flip
    eng.run()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "phase1":
        phase1(*sys.argv[2:5])
    elif len(sys.argv) > 1 and sys.argv[1] == "phase2":
        phase2(*sys.argv[2:4])
    else:
        tmp = tempfile.mkdtemp(prefix="h3dcar_")
        shots = os.environ.get("H3D_SHOTS") or tmp
        proj, outside = os.path.join(tmp, "proj"), os.path.join(tmp, "Downloads")
        os.makedirs(outside)
        shutil.copytree(os.path.join(ROOT, 'templates', 'empty'), proj)
        for p in ('spline_roads', 'car_race_kit'):
            shutil.copytree(os.path.join(ROOT, 'packages', p), os.path.join(proj, 'packages', p))
        shutil.copy(TRUCK, outside)
        make_wheel_obj(os.path.join(outside, "tyre.obj"))
        ok1 = subprocess.call([sys.executable, __file__, "phase1", proj, outside, shots]) == 0
        moved = os.path.join(tmp, "moved project")
        shutil.move(proj, moved); shutil.rmtree(outside)
        ok2 = subprocess.call([sys.executable, __file__, "phase2", moved, shots]) == 0
        print("RESULT", "PASS" if ok1 and ok2 else "FAIL", "shots in", shots)
        sys.exit(0 if ok1 and ok2 else 1)
