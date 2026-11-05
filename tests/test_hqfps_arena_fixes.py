"""HQ FPS Arena, the fixes players reported (needs a GPU):

    python tests/test_hqfps_arena_fixes.py

The project is made the way a player makes it: the Hub's create_project
from the HQ FPS Arena template, in a fresh home folder.

  * the template has a Hub preview image
  * the guns and the ammo boxes are scene objects with their 3D models in
    the EDITOR, saved with the project (their models are not)
  * in play you start empty-handed and pick the guns up off the floor

  * zombies only hurt you at arm's length, never from across the arena
  * a shot that doesn't kill plays the hit reaction and stuns; the killing
    shot plays the death, once, and the zombie stays on its feet until then
  * HQ FPS handguns leave bullet holes on walls (also on an engine without
    spawn_impact_fx), none floating where a zombie stood
  * aiming with the right mouse button never loses mouse-look
  * the arms are closed (no hollow inside) and the near plane is 3 cm in play
"""
import math
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HOME = tempfile.mkdtemp(prefix="h3dhq_home_")
os.environ["HOME"] = HOME                      # never touch the user's own projects
os.environ["USERPROFILE"] = HOME
OUT = os.environ.get("H3D_SHOTS") or tempfile.mkdtemp(prefix="h3dhq_shots_")
os.makedirs(OUT, exist_ok=True)
W, HH = 1280, 720
res = []


def check(n, c, d=""):
    res.append(bool(c))
    print(("PASS " if c else "FAIL ") + n + ("  [%s]" % d if d else ""), flush=True)


def main():
    import importlib.util
    spec = importlib.util.spec_from_file_location("Heaven3D_Hub", os.path.join(ROOT, "Heaven3D_Hub.py"))
    hub = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hub)
    cfg = hub.Config()
    tpl = next(t for t in hub.list_templates() if t["id"] == "hqfps_arena")
    check("the HQ FPS Arena template has a Hub preview image", os.path.isfile(tpl["thumb"]), tpl["thumb"])
    proj = hub.create_project(cfg, "HQ FPS Arena", "hqfps_arena", os.path.join(HOME, "Projects"))
    import subprocess
    r = subprocess.run([sys.executable, os.path.abspath(__file__), "editor", proj["path"], OUT],
                       env=dict(os.environ), capture_output=True, text=True)
    for line in r.stdout.splitlines():
        if line.startswith(("PASS ", "FAIL ")):
            res.append(line.startswith("PASS "))
            print(line, flush=True)
    if r.returncode != 0 and not any(l.startswith(("PASS", "FAIL")) for l in r.stdout.splitlines()):
        check("editor phase ran", False, r.stderr[-800:])
    spec = importlib.util.spec_from_file_location("Heaven3D", os.path.join(ROOT, "Heaven3D.py"))
    H = importlib.util.module_from_spec(spec)
    sys.modules["Heaven3D"] = H
    spec.loader.exec_module(H)
    H._h3d_set_splash_mode("off")
    import glm
    import moderngl
    import pygame
    eng = H.HeavenEngine(mode="runtime", width=W, height=HH,
                         project_path=os.path.join(proj["path"], "main.heaven3d"))
    wep = sys.modules["h3dpkg_hqfps_weapons"]

    def shot(name):
        data = moderngl.get_context().screen.read(components=3)
        pygame.image.save(pygame.transform.flip(pygame.image.frombuffer(data, (W, HH), "RGB"), False, True),
                          os.path.join(OUT, name + ".png"))

    def bps(cls):
        return [b for n in eng.scene.root.traverse() for b in n.blueprints if b.__class__.__name__ == cls]

    pending = {"lmb": False}
    real_end = eng.input.end_frame

    def end_frame():
        real_end()
        if pending["lmb"]:
            eng.input.lmb = True
            eng.input.lmb_just_pressed = True
            pending["lmb"] = False
    eng.input.end_frame = end_frame

    def aim(p):
        pc = eng.first_blueprint("FPSController")
        d = glm.normalize(glm.vec3(p) - glm.vec3(eng.camera.position))
        pc.yaw = math.degrees(math.atan2(-d.x, -d.z))
        pc.pitch = math.degrees(math.asin(max(-1.0, min(1.0, d.y))))

    def frames(n):
        for _ in range(n):
            yield

    def fire():
        bp = bps("HQWeapons")[0]
        bp._cool = 0.0
        bp.ammo[bp.name] = max(bp.ammo.get(bp.name, 0), 5)   # never test a dry gun
        pending["lmb"] = True
        yield
        yield
        eng.input.lmb = False

    def script():
        bp = None
        while bp is None:
            found = bps("HQWeapons")
            bp = found[0] if found else None
            yield
        yield from frames(20)
        zs = bps("MHEnemy")
        pc = eng.first_blueprint("FPSController")
        player = pc.node
        picks = bps("HQWeaponPickup")
        boxes = bps("HQAmmoPickup")
        check("play starts empty-handed, with guns and ammo lying in the arena",
              bp.names == [] and bp.vm is None and len(picks) >= 10 and len(boxes) >= 10,
              "%d weapons on the floor, %d ammo boxes, carrying %s" % (len(picks), len(boxes), bp.names))
        check("every pickup shows its HQ FPS model",
              all(p.parts and all(c.mesh is not None and c.visible for c in p.parts) for p in picks + boxes))
        for z in zs:                          # keep the horde away while shopping
            z.properties["detect_range"] = 0.0
        # walk onto the M1911 on the floor
        m1911 = next(p for p in picks if p.p("weapon") == "M1911")
        player.position = glm.vec3(m1911.node.world_position().x, 1.0, m1911.node.world_position().z)
        for _ in range(60):
            yield
            if bp.vm is not None and bp.name == "M1911":
                break
        check("walking onto the M1911 picks it up and draws it",
              bp.names == ["M1911"] and bp.vm is not None and not any(c.visible for c in m1911.parts),
              "carrying %s, ammo %s / %s" % (bp.names, bp.ammo.get("M1911"), bp.reserve.get(bp.ammo_type("M1911"))))
        shot("hq_01_m1911_picked_up")
        for p in picks:                       # and the rest of the arsenal
            if p is m1911:
                continue
            player.position = glm.vec3(p.node.world_position().x, 1.0, p.node.world_position().z)
            yield from frames(8)
        check("every weapon on the floor can be picked up",
              sorted(bp.names) == sorted(p.p("weapon") for p in picks), "%d carried" % len(bp.names))
        for _ in range(200):
            yield
            if bp.vm is not None and bp.vm.clip in ("Idle", "Hold") and bp._busy <= 0.0:
                break
        check("near plane is %.2f m while the viewmodel is out" % wep.VIEWMODEL_NEAR,
              abs(eng.camera.near - wep.VIEWMODEL_NEAR) < 1e-6, "near %.3f" % eng.camera.near)
        arms = bp.vm.arms
        check("arms closed: continued to the shoulder, one extra bone row",
              arms.root_node is not None and arms.tex.width == (len(arms.model.joints) + 1) * 4,
              "root %s, palette texels %d" % (arms.root_node, arms.tex.width))
        reach = sorted(round(z._reach(), 2) for z in zs)
        check("every arena zombie has an arm's-length reach (<= 2 m)", max(reach) <= 2.0, "%s" % reach)

        # park the horde far away and out of the fight
        for i, z in enumerate(zs):
            z.properties["detect_range"] = 0.0
            z.state = "patrol"
            z.points, z._wander = [], None
            z.node.position = glm.vec3(-15.0 + i * 2.0, z.node.position.y, 16.0)
            z._home = glm.vec3(z.node.position)
            z.properties["patrol_speed"] = 0.0
        yield from frames(5)

        # ---- reach: a zombie 3 m away that keeps attacking must never hurt you
        z = zs[0]
        player.position = glm.vec3(0.0, 1.0, -10.0)
        yield from frames(3)
        hp0 = pc.hp if hasattr(pc, "hp") else None
        z.properties.update({"detect_range": 40.0, "chase_speed": 0.0, "attack_range": 9.0})   # a bad value on purpose
        z.node.position = glm.vec3(0.0, z.node.position.y, -13.0)
        z.cool = 0.0
        yield from frames(150)
        hp1 = pc.hp
        check("a zombie 3 m away never hurts you, even with attack_range 9", hp1 == hp0,
              "hp %s -> %s, its reach %.2f m" % (hp0, hp1, z._reach()))
        z.node.position = glm.vec3(0.0, z.node.position.y, -11.25)
        yield from frames(150)
        check("...but it does at arm's length", pc.hp < hp1, "hp %s -> %s" % (hp1, pc.hp))
        pc.hp = hp0
        z.properties.update({"detect_range": 0.0, "attack_range": 1.4})
        z.state = "patrol"
        z.node.position = glm.vec3(-15.0, z.node.position.y, 16.0)

        # ---- handgun: hit + stun first, death once on the killing shot
        bp._begin_switch(bp.names.index("M1911"))
        for _ in range(300):
            yield
            if bp.name == "M1911" and bp.vm.clip in ("Idle", "Hold"):
                break
        target = zs[1]
        target.properties["max_health"] = 60.0
        target.hp = 60.0
        target.node.position = glm.vec3(0.0, target.node.position.y, -16.0)
        yield from frames(5)
        rig = sys.modules["h3dpkg_mh_characters"].rig_of(target.node)
        hips = rig.model.bone("mixamorig:Hips")
        aim(target.node.world_position() + glm.vec3(0.0, 0.15, 0.0))
        yield from frames(3)
        stand = float(rig.pose.G[hips][1, 3])
        log = []
        stuns = []
        real_msg = target.on_message

        def on_message(event, data):          # the stun as set by the hit itself
            real_msg(event, data)
            if event == "damage" and target.alive:
                stuns.append(target.stagger)
        target.on_message = on_message

        def watch(n):
            for _ in range(n):
                yield
                an = rig.animator
                log.append((an.action["slot"] if an.action else None, float(rig.pose.G[hips][1, 3]),
                            target.alive, target.stagger))
        yield from fire()
        yield from watch(4)
        hit_ok = any(s == "hit" for s, _h, _a, _st in log) and target.alive
        stun = max(stuns) if stuns else 0.0
        shot("hq_10_zombie_hit")
        check("a non-lethal handgun shot plays the hit reaction and stuns",
              hit_ok and stun >= 0.35, "hp %.0f, stun %.2f s" % (target.hp, stun))
        yield from watch(40)
        low = min(h for s, h, a, st in log if a)
        check("the zombie stays on its feet while hit (no knock-down)", low > 0.7 * stand,
              "hips %.2f m standing, lowest %.2f m" % (stand, low))
        shots = 0
        while target.alive and shots < 8:
            aim(target.node.world_position() + glm.vec3(0.0, 0.15, 0.0))
            yield from fire()
            shots += 1
            yield from watch(20)
        yield from watch(90)
        starts = sum(1 for a, b in zip(log, log[1:]) if b[0] == "death" and a[0] != "death")
        falls = sum(1 for a, b in zip(log, log[1:]) if a[1] > 0.45 * stand >= b[1])
        shot("hq_11_zombie_dead")
        check("the killing shot plays the death exactly once",
              not target.alive and starts == 1 and falls == 1,
              "death clips started %d, falls to the floor %d" % (starts, falls))

        # ---- bullet holes on the wall with the handgun
        player.position = glm.vec3(0.0, 1.0, -16.0)
        yield from frames(3)
        before = len(H._FP2_DECALS)
        for k in range(4):
            aim(glm.vec3(-0.6 + 0.4 * k, 1.6, -19.5))
            yield
            yield from fire()
            yield from frames(15)
        check("M1911 shots leave bullet holes on the wall", len(H._FP2_DECALS) - before == 4,
              "%d new holes" % (len(H._FP2_DECALS) - before))
        shot("hq_12_m1911_holes")
        # an engine without spawn_impact_fx (old Heaven3D_runtime / editions)
        api = H.HeavenEngine.spawn_impact_fx
        del H.HeavenEngine.spawn_impact_fx
        n0 = len(wep._HOLES)
        for k in range(3):
            aim(glm.vec3(-0.4 + 0.4 * k, 1.2, -19.5))
            yield
            yield from fire()
            yield from frames(15)
        H.HeavenEngine.spawn_impact_fx = api
        check("...and on an engine without spawn_impact_fx too", len(wep._HOLES) - n0 == 3,
              "%d own holes" % (len(wep._HOLES) - n0))
        # no hole on a zombie
        z2 = zs[2]
        z2.node.position = glm.vec3(1.2, z2.node.position.y, -18.0)
        yield from frames(3)
        aim(z2.node.world_position() + glm.vec3(0.0, 0.15, 0.0))
        yield
        d0, o0 = len(H._FP2_DECALS), len(wep._HOLES)
        yield from fire()
        yield from frames(10)
        check("no bullet hole left hanging where a zombie was shot",
              len(H._FP2_DECALS) == d0 and len(wep._HOLES) == o0)

        # ---- right mouse button: aim, release, still locked
        yield from frames(5)
        ok = True
        for k in range(3):
            pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=3, pos=(640, 360)))
            yield from frames(10)
            held = pygame.event.get_grab() and not pygame.mouse.get_visible() and eng.input.rmb
            pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONUP, button=3, pos=(640, 360)))
            yield from frames(5)
            after = pygame.event.get_grab() and not pygame.mouse.get_visible()
            ok = ok and held and after
        check("right mouse button (aim) never releases the pointer", ok)
        pygame.event.set_grab(False)            # the OS takes the pointer away
        pygame.mouse.set_visible(True)
        yield from frames(2)
        check("a lost pointer lock is taken back on the next frame",
              pygame.event.get_grab() and not pygame.mouse.get_visible())

        # ---- arms on screen: the AKM reload and the knife holster
        for name, clip in (("AKM", "EmptyReload"), ("CombatKnife", "Holster")):
            bp._begin_switch(bp.names.index(name))
            for _ in range(300):
                yield
                if bp.name == name and bp.vm.clip in ("Idle", "Hold"):
                    break
            pc.yaw, pc.pitch = 180.0, 0.0
            bp._busy = 999.0
            for i, frac in enumerate((0.2, 0.45, 0.7)):
                bp.vm.play(clip, loop=True)
                bp.vm.t = bp.vm.duration(clip) * frac
                bp.vm.speed = 0.0
                yield from frames(2)
                shot("hq_2%d_%s_%s" % (i, name, clip))
            bp._busy = 0.0
        eng._stop_play()
        yield
        check("near plane restored after Stop", abs(eng.camera.near - 0.1) < 1e-6, "%.3f" % eng.camera.near)
        print("\n%d/%d passed   (screenshots in %s)" % (sum(res), len(res), OUT), flush=True)
        shutil.rmtree(HOME, ignore_errors=True)
        os._exit(0 if all(res) else 1)

    gen = script()
    flip = pygame.display.flip

    def driver():
        next(gen)
        flip()
    pygame.display.flip = driver
    eng.run()


def editor_phase(project, out):
    """Open the project in the EDITOR, as the Hub's Open button does."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("Heaven3D", os.path.join(ROOT, "Heaven3D.py"))
    H = importlib.util.module_from_spec(spec)
    sys.modules["Heaven3D"] = H
    spec.loader.exec_module(H)
    H._h3d_set_splash_mode("off")
    import glm
    import moderngl
    import pygame
    import json
    eng = H.HeavenEngine(mode="editor", width=W, height=HH, project_path=os.path.join(project, "main.heaven3d"))

    def bps(cls):
        return [b for n in eng.scene.root.traverse() for b in n.blueprints if b.__class__.__name__ == cls]
    st = {"f": 0}
    flip = pygame.display.flip

    def driver():
        st["f"] += 1
        if st["f"] == 3:
            eng.camera.position = glm.vec3(3.5, 2.2, -18.8)
            eng.camera.yaw, eng.camera.pitch = 155.0, -18.0
        if st["f"] == 30:
            picks, boxes = bps("HQWeaponPickup"), bps("HQAmmoPickup")
            shown = [p for p in picks + boxes if p.parts and all(c.mesh is not None for c in p.parts)]
            check("EDITOR: the guns and ammo boxes show their 3D models",
                  len(picks) >= 10 and len(boxes) >= 10 and len(shown) == len(picks) + len(boxes),
                  "%d guns, %d boxes, %d with models" % (len(picks), len(boxes), len(shown)))
            data = moderngl.get_context().screen.read(components=3)
            pygame.image.save(pygame.transform.flip(pygame.image.frombuffer(data, (W, HH), "RGB"), False, True),
                              os.path.join(out, "hq_00_editor.png"))
            eng.save_project()
            raw = open(os.path.join(project, "main.heaven3d"), encoding="utf-8").read()
            check("EDITOR: saving keeps the pickups and not their generated models",
                  sum(1 for c in json.loads(raw)["scene"]["children"] if any(b.get("class") == "HQWeaponPickup" for b in c.get("blueprints", []))) == len(picks) and "weapon part" not in raw and "ammo part" not in raw)
            os._exit(0)
        flip()
    pygame.display.flip = driver
    eng.run()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "editor":
        editor_phase(sys.argv[2], sys.argv[3])
    else:
        main()
