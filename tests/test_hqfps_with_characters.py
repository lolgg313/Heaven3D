"""HQ FPS Weapons + MakeHuman Characters in one scene (needs a GPU):

    python tests/test_hqfps_with_characters.py

Both packages GPU-skin their meshes through the same `u_mh_*` shader hooks,
so the thing worth proving is that they coexist: the hooks get installed
once, both the viewmodel and the characters animate, and the weapons
actually kill the zombies.
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ENGINE = os.path.join(ROOT, "Heaven3D.py")
OUT = os.environ.get("H3D_SHOTS") or tempfile.mkdtemp(prefix="h3dboth_shots_")
os.makedirs(OUT, exist_ok=True)
res = []
W, HH = 1280, 760


def check(n, c, d=""):
    res.append(bool(c))
    print(("PASS " if c else "FAIL ") + n + ("  [%s]" % d if d else ""), flush=True)


def shot(name):
    import moderngl
    import pygame
    data = moderngl.get_context().screen.read(components=3)
    pygame.image.save(
        pygame.transform.flip(pygame.image.frombuffer(data, (W, HH), "RGB"), False, True),
        os.path.join(OUT, name + ".png"))


def run(project):
    import importlib.util
    spec = importlib.util.spec_from_file_location("Heaven3D", ENGINE)
    H = importlib.util.module_from_spec(spec)
    sys.modules["Heaven3D"] = H
    spec.loader.exec_module(H)
    H._h3d_set_splash_mode("off")
    eng = H.HeavenEngine(mode="runtime", width=W, height=HH,
                         project_path=os.path.join(project, "main.heaven3d"))
    import glm
    import pygame

    wep = sys.modules.get("h3dpkg_hqfps_weapons")
    chars = sys.modules.get("h3dpkg_mh_characters")
    core = sys.modules.get("h3dpkg_mh_characters_mh_core")
    check("both packages loaded", wep is not None and chars is not None)
    if wep is None or chars is None:
        print("\n%d/%d passed" % (sum(res), len(res)), flush=True)
        sys.stdout.flush()
        os._exit(1)

    # The hooks are textually identical and patching is idempotent, so the
    # shader must end up with exactly one copy no matter which loaded first.
    check("skinning hooks installed exactly once (not twice)",
          H.GEOMETRY_VERT.count("mat4 mh_skin_matrix()") == 1
          and H.FORWARD_VERT.count("mat4 mh_skin_matrix()") == 1
          and H.SHADOW_VERT.count("mat4 mh_skin_matrix()") == 1,
          "geometry x%d, forward x%d, shadow x%d"
          % (H.GEOMETRY_VERT.count("mat4 mh_skin_matrix()"),
             H.FORWARD_VERT.count("mat4 mh_skin_matrix()"),
             H.SHADOW_VERT.count("mat4 mh_skin_matrix()")))
    check("both packages use the same bone-palette texture unit",
          wep.UNIT == chars.UNIT, "unit %d" % wep.UNIT)

    for n in eng.scene.root.traverse():
        if n.is_ocean:
            n.ocean_settings.enabled = False
    ground = eng.add_plane(size=80.0, name="Ground")
    ground.material.base_color = (0.30, 0.33, 0.30, 1.0)

    player = H.SceneNode("Player", parent=eng.scene.root)
    player.position = glm.vec3(0.0, 1.0, 0.0)
    eng.attach_blueprint(player, H.FPSController)
    bp = wep.give_weapons(eng, player, weapons="AKM,M1911,CombatKnife")

    # three zombies in a row downrange
    zombies = []
    for i, x in enumerate((-2.0, 0.0, 2.0)):
        spec = core.random_spec(chars._lib(), seed=1000 + i, style="zombie")
        z = chars.place_character(eng, spec, pos=glm.vec3(x, 0.0, -7.0),
                                  name="Zombie%d" % i, yaw=0.0, select=False)
        eng.attach_blueprint(z, chars.CLS["MHEnemy"])
        z.blueprints[-1].properties.update({"chase_speed": 0.0, "patrol_speed": 0.0,
                                            "detect_range": 0.0, "max_health": 60.0})
        zombies.append(z)
    check("zombies built", all(chars.rig_of(z) is not None for z in zombies),
          "%d characters" % len(zombies))

    eng._begin_play()
    cam = eng.camera

    def enemy_of(z):
        return next((b for b in z.blueprints if b.__class__.__name__ == "MHEnemy"), None)

    def aim_at(node, dy=0.0):
        import math
        pc = eng.first_blueprint("FPSController")
        p = glm.vec3(node.world_position()) + glm.vec3(0.0, dy, 0.0)
        d = glm.normalize(p - glm.vec3(cam.position))
        yaw = math.degrees(math.atan2(-d.x, -d.z))
        pitch = math.degrees(math.asin(max(-1.0, min(1.0, d.y))))
        if pc is not None:
            pc.yaw, pc.pitch = yaw, pitch
        cam.yaw, cam.pitch = yaw, pitch

    pending = {"lmb": False}
    _real_end = eng.input.end_frame

    def end_frame():
        _real_end()
        if pending["lmb"]:
            eng.input.lmb = True
            eng.input.lmb_just_pressed = True
            pending["lmb"] = False

    eng.input.end_frame = end_frame

    def wait(pred, label, limit=500, detail=lambda: ""):
        for _ in range(limit):
            if pred():
                check(label, True, detail())
                return True
            yield
        check(label, False, "timed out; %s" % detail())
        return False

    def script():
        yield from wait(lambda: bp.vm is not None and bp.vm.clip in ("Idle", "Hold"),
                        "viewmodel drawn and idling", 400)
        aim_at(zombies[1])
        yield
        shot("01_zombies_and_viewmodel")

        # The character node sits at the CENTRE of its capsule, so confirm
        # the shot is actually on the zombie before blaming the damage path
        # when nothing dies.
        hit = eng.physics.raycast(cam.position, cam.forward(), 50.0, ignore=bp._ignore)
        on_target = hit is not None and any(
            hit.node is n for n in zombies[1].traverse())
        check("the crosshair is on a zombie's collider", on_target,
              "ray hit %s" % (hit.node.name if hit else "nothing"))

        # both skinned systems must be posed at the same time
        import numpy as np
        vm_span = float(np.abs(bp.vm.arms.model.palette(
            bp.vm.arms.clip(bp.vm.clip), bp.vm.t) - np.eye(4)).max())
        r = chars.rig_of(zombies[1])
        ch_span = float(np.abs(r.pose.skin() - np.eye(4)).max())
        check("viewmodel and characters are both posed in the same frame",
              vm_span > 0.2 and ch_span > 0.2,
              "viewmodel %.2f, character %.2f" % (vm_span, ch_span))

        alive0 = sum(1 for z in zombies if getattr(enemy_of(z), "alive", False))
        check("zombies start alive", alive0 == 3, "%d alive" % alive0)

        # ---- shoot one with the rifle ----
        eng.input.lmb = True
        yield from wait(lambda: not getattr(enemy_of(zombies[1]), "alive", True),
                        "a zombie is killed by gunfire", 600,
                        lambda: "AKM ammo %d" % bp.ammo["AKM"])
        eng.input.lmb = False
        yield
        shot("02_zombie_killed")

        # ---- and the one next to it, to show it was not a fluke ----
        aim_at(zombies[0])
        for _ in range(5):
            yield
        eng.input.lmb = True
        yield from wait(lambda: not getattr(enemy_of(zombies[0]), "alive", True),
                        "a second zombie is killed", 600)
        eng.input.lmb = False
        yield

        left = [i for i, z in enumerate(zombies) if getattr(enemy_of(z), "alive", False)]
        check("the zombie that was not shot is still alive", left == [2], "alive: %s" % left)
        shot("03_two_down")

        # ---- melee the last one ----
        bp._begin_switch(2)
        yield from wait(lambda: bp.name == "CombatKnife" and bp.vm.clip in ("Idle", "Hold"),
                        "switched to the knife beside live characters", 500)
        # Step inside the knife's 1.2 m reach, measured from the EYE -- the
        # camera sits ~0.6 m above the player node, so standing 1.1 m away
        # on the ground still leaves the zombie 1.34 m from the muzzle.
        player.position = glm.vec3(2.0, 1.0, -6.6)
        aim_at(zombies[2])
        for _ in range(5):
            yield
        d = glm.length(glm.vec3(zombies[2].world_position()) - glm.vec3(cam.position))
        check("within the knife's reach", d < 1.2, "%.2f m from the eye" % d)
        shot("04_knife_vs_zombie")
        # 60 hp against a 25/27 stab: it takes three swings
        killed = False
        for _ in range(240):
            if not getattr(enemy_of(zombies[2]), "alive", True):
                killed = True
                break
            if bp._cool <= 0.0 and bp._busy <= 0.0:
                pending["lmb"] = True
            yield
        check("a zombie is killed with the knife", killed,
              "hp %.0f" % getattr(enemy_of(zombies[2]), "hp", -1))
        yield
        shot("05_all_down")

        print("\n%d/%d passed" % (sum(res), len(res)), flush=True)
        print("SCREENSHOTS", OUT, flush=True)
        sys.stdout.flush()
        os._exit(0 if all(res) else 1)

    gen = script()
    budget = [7000]

    def frame():
        budget[0] -= 1
        if budget[0] <= 0:
            check("script finished within its frame budget", False)
            print("\n%d/%d passed" % (sum(res), len(res)), flush=True)
            sys.stdout.flush()
            os._exit(1)
        try:
            next(gen)
        except StopIteration:
            pass
        except Exception:
            import traceback
            traceback.print_exc()
            sys.stdout.flush()
            os._exit(1)
        _flip()

    _flip = pygame.display.flip
    pygame.display.flip = frame
    eng.run()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        run(sys.argv[1])
        sys.exit(0)
    proj = tempfile.mkdtemp(prefix="h3dboth_")
    shutil.copytree(os.path.join(ROOT, "templates", "empty"), proj, dirs_exist_ok=True)
    os.makedirs(os.path.join(proj, "packages"), exist_ok=True)
    for pkg in ("hqfps_weapons", "mh_characters"):
        shutil.copytree(os.path.join(ROOT, "packages", pkg),
                        os.path.join(proj, "packages", pkg),
                        ignore=shutil.ignore_patterns("__pycache__", "tools", "tests"))
    rc = subprocess.run([sys.executable, __file__, proj]).returncode
    print("ALL PASSED" if rc == 0 else "FAILED")
    sys.exit(rc)
