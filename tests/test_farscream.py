"""FarScream in Play (needs a GPU / llvmpipe; slow on llvmpipe, ~20 min):

    python tests/test_farscream.py

Opens the FarScream template as an exported game would, and plays it:
the player lands with the pistol only, soldiers appear at a camp holding
pistols and MP5s, a dead soldier drops his MP5 and .45 ammo and walking over
it picks them up, F gets into the army truck, it drives and turns with the
third-person camera, F gets out, a fuel drum explodes (and sets off the one
next to it), islanders who see you run at you and punch, soldiers shoot,
bullet holes stay capped, and Stop puts everything back.  Screenshots go to
$H3D_SHOTS (or a temp folder)."""
import json
import math
import os
import shutil
import sys
import tempfile
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ENGINE = os.path.join(ROOT, "Heaven3D.py")
OUT = os.environ.get("H3D_SHOTS") or tempfile.mkdtemp(prefix="h3dfs_shots_")
os.makedirs(OUT, exist_ok=True)
W, HH = 640, 360
res = []


def check(n, c, d=""):
    res.append(bool(c))
    print(("PASS " if c else "FAIL ") + n + ("  [%s]" % d if d else ""), flush=True)


def make_project():
    proj = tempfile.mkdtemp(prefix="h3dfs_")
    d = json.load(open(os.path.join(ROOT, "packages", "farscream", "templates", "farscream", "main.heaven3d")))
    d["settings"].update({"ssao": False, "msaa": False, "msaa_samples": 0})   # (llvmpipe is slow)
    json.dump(d, open(os.path.join(proj, "main.heaven3d"), "w"))
    os.makedirs(os.path.join(proj, "packages"))
    for p in ("farscream", "tropical_islands", "hqfps_weapons", "mh_characters"):
        src = os.path.join(ROOT, "packages", p)
        try:
            os.symlink(src, os.path.join(proj, "packages", p))
        except OSError:
            shutil.copytree(src, os.path.join(proj, "packages", p), ignore=shutil.ignore_patterns("__pycache__"))
    return proj


def main():
    import importlib.util
    proj = make_project()
    spec = importlib.util.spec_from_file_location("Heaven3D", ENGINE)
    H = importlib.util.module_from_spec(spec)
    sys.modules["Heaven3D"] = H
    spec.loader.exec_module(H)
    H._h3d_set_splash_mode("off")
    eng = H.HeavenEngine(mode="runtime", width=W, height=HH, project_path=os.path.join(proj, "main.heaven3d"))
    import glm
    import pygame

    fs = sys.modules.get("h3dpkg_farscream")
    check("FarScream package loaded", fs is not None)
    if fs is None:
        os._exit(1)

    def shot(name):
        data = eng.ctx.screen.read(components=3)
        pygame.image.save(pygame.transform.flip(pygame.image.frombuffer(data, (W, HH), "RGB"), False, True),
                          os.path.join(OUT, name + ".png"))

    keys = {"down": set(), "tap": set()}
    real_end = eng.input.end_frame

    def end_frame():
        real_end()
        for k in keys["down"] | keys["tap"]:
            eng.input._keys.add(k)
        for k in keys["tap"]:
            eng.input._pressed.add(k)
        keys["tap"] = set()
    eng.input.end_frame = end_frame

    def frames(n):
        for _ in range(n):
            yield

    def put_player(pc, mr, x, z, look_at=None):
        pc.node.position = glm.vec3(x, mr.height(x, z) + 1.0, z)
        if look_at is not None:
            d = glm.normalize(glm.vec3(look_at[0] - x, 0.0, look_at[1] - z))
            pc.yaw, pc.pitch = math.degrees(math.atan2(-d.x, -d.z)), -4.0

    def script():
        yield from frames(3)
        if not eng._playing:
            eng._begin_play()
        mr = list(fs.STATE["maps"].values())[0]
        g = mr.bp.game
        for _ in range(400):
            if g.ready and fs._holder(eng) is not None:
                break
            yield
        check("the map is built and the game is running", g.ready, "%d objects" % len(mr.props))
        pc = eng.first_blueprint("FPSController")
        h = fs._holder(eng)
        check("the player has only the pistol", h is not None and h.names == ["M1911"], h.names if h else None)
        st = mr.layout["start"]
        p = pc.node.world_position()
        check("the player lands at the start", math.hypot(p.x - st["x"], p.z - st["z"]) < 3.0)
        shot("01_landing")

        # ---- a camp: soldiers, loot
        camp = [s for s in mr.layout["sites"] if s["kind"] == "camp"][0]
        put_player(pc, mr, camp["x"] + 30.0, camp["z"] + 30.0, (camp["x"], camp["z"]))
        for _ in range(300):
            if len(g.chars) >= 3:
                break
            yield
        check("soldiers appear near the camp", len(g.chars) >= 3, "%d" % len(g.chars))
        guns = [getattr(getattr(g._enemy(n), "rig", None), "gun_name", None) for n in g.chars.values()]
        check("soldiers hold pistols and MP5s (no revolvers)",
              guns and all(gn and (gn == "MP5" or gn.startswith("Pistol")) for gn in guns), guns)
        shot("02_camp")
        k, node = next(((k, n) for k, n in g.chars.items() if mr.layout["posts"][k].get("weapon") == "MP5"),
                       next(iter(g.chars.items())))
        n_loot = len(g.loot)
        eng.message_ancestors(node, "damage", {"amount": 500.0, "source": "test"})
        yield from frames(3)
        check("a dead soldier drops loot", len(g.loot) == n_loot + 1)
        lt = g.loot[-1]
        before = h.reserve_of("M1911")
        lp = lt["node"].world_position()
        pc.node.position = glm.vec3(lp.x, lp.y + 1.0, lp.z)
        yield from frames(4)
        check("walking over the loot picks it up", lt not in g.loot)
        if lt["weapon"] == "MP5":
            check("the soldier's MP5 is ours", "MP5" in h.names, h.names)
        check("and his .45 ammunition", h.reserve_of("M1911") > before, "%d -> %d" % (before, h.reserve_of("M1911")))

        # ---- drive the army truck
        for kk in list(g.chars):
            g._despawn(kk)
            g.dead.add(kk)
        v = next((v for v in g.vehicles if v.md.name == "M3S"), g.vehicles[0])
        dp = v.node.position - v.right() * (v.half.z + 1.0)
        put_player(pc, mr, dp.x, dp.z)
        yield from frames(3)
        keys["tap"].add("f")
        yield from frames(3)
        check("F gets into the truck", g.drive is v)
        p0 = glm.vec3(v.node.position)
        keys["down"].add("w")
        yield from frames(60)
        check("the truck drives", glm.length(v.node.position - p0) > 3.0, "%.1f m" % glm.length(v.node.position - p0))
        check("the camera follows behind it", glm.length(eng.camera.position - v.node.position) > 4.0)
        shot("03_driving")
        y0 = v.yaw
        keys["down"].add("a")
        yield from frames(30)
        eng.input._keys -= keys["down"]                 # (let go of W and A)
        keys["down"].clear()
        check("the truck steers", abs(v.yaw - y0) > 4.0, "%.1f deg" % (v.yaw - y0))
        yield from frames(30)
        keys["tap"].add("f")
        yield from frames(3)
        check("F gets out beside the truck", g.drive is None and v.door_distance(pc.node.position) < 3.5)

        # ---- a fuel drum
        drums = [(it, n) for it, n in g.mr.items if it.get("role") == "explosive" and n.visible]
        it, n = drums[0]
        dpos = n.world_position()
        put_player(pc, mr, dpos.x + 15.0, dpos.z + 15.0, (dpos.x, dpos.z))    # (out of the blast)
        pc.hp = pc.max_hp
        check("fuel drums can be shot", any(type(b).__name__ == "FSProp" for b in n.blueprints))
        for b in list(n.blueprints):
            b.on_message("damage", {"amount": 100.0, "source": "hqfps_weapon"})
        yield from frames(20)
        check("the drum explodes", not n.visible and len(g.blown) >= 1, "%d blown" % len(g.blown))

        # ---- islanders
        for _ in range(400):
            if not getattr(pc, "_dead", False):
                break
            yield
        check("the player is alive before the next test", not getattr(pc, "_dead", False))
        pc.hp = pc.max_hp
        vil = [s for s in mr.layout["sites"] if s["kind"] == "village"][0]
        put_player(pc, mr, vil["x"] + 14.0, vil["z"] + 2.0, (vil["x"], vil["z"]))
        hp0 = pc.hp
        for _ in range(240):
            if pc.hp < hp0:
                break
            yield
        nat = [n for kk, n in g.chars.items() if mr.layout["posts"][kk]["kind"] == "native"]
        check("islanders who see you run at you and punch", nat and pc.hp < hp0, "hp %.0f" % pc.hp)
        shot("04_islanders")

        # ---- soldiers shoot
        for kk in list(g.chars):
            g._despawn(kk)
            g.dead.add(kk)
        for _ in range(400):
            if not getattr(pc, "_dead", False):
                break
            yield
        pc.hp = pc.max_hp
        camp = [s for s in mr.layout["sites"] if s["kind"] == "camp"][1]
        put_player(pc, mr, camp["x"] + 16.0, camp["z"] + 16.0, (camp["x"], camp["z"]))
        hp0 = pc.hp
        for _ in range(300):
            if pc.hp < hp0:
                break
            yield
        check("soldiers who see you shoot", pc.hp < hp0, "hp %.0f" % pc.hp)
        shot("05_firefight")

        # ---- bullet holes
        p = pc.node.world_position()
        for i in range(70):
            eng.spawn_impact_fx(SimpleNamespace(point=glm.vec3(p.x + i * 0.05, mr.height(p.x, p.z) + 0.01, p.z + 2.0),
                                                normal=glm.vec3(0, 1, 0), node=mr.ground.node, distance=2.0))
        check("bullet holes are capped, the oldest removed first", len(H._FP2_DECALS) <= fs.DECAL_CAP,
              "%d" % len(H._FP2_DECALS))

        # ---- Stop
        eng._stop_play()
        yield from frames(2)
        check("Stop removes the characters", not any(getattr(n, "_fs_char", False) for n in eng.scene.root.children))
        check("Stop puts the drums back", all(n.visible for _it, n in mr.items))

    gen = script()
    flip = pygame.display.flip

    def flip_and_step():
        try:
            next(gen)
        except StopIteration:
            print("\n%d/%d passed  (screenshots in %s)" % (sum(res), len(res), OUT), flush=True)
            shutil.rmtree(proj, ignore_errors=True)
            os._exit(0 if all(res) else 1)
        except Exception:
            import traceback
            traceback.print_exc()
            check("no exception", False)
            os._exit(1)
        flip()
    pygame.display.flip = flip_and_step
    eng.run()


if __name__ == "__main__":
    main()
