"""HQ FPS Weapons package (needs a GPU / llvmpipe):

    python tests/test_hqfps_weapons.py

Boots a real engine, equips the player and drives the weapon system: skinned
bare-skin arms plus the weapon on screen, the animation state machine
(draw / idle / fire / reload / holster), ammo and reloading, hitscan damage
reaching an enemy, buckshot pellets, melee swings, throwables, and switching
across all twenty viewmodels.

The script is a generator stepped one engine frame at a time and it waits on
state rather than on frame numbers, so it does not care how fast the machine
renders.

Screenshots land in $H3D_SHOTS (default: a temp dir, printed at the end).
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ENGINE = os.path.join(ROOT, "Heaven3D.py")
PKG = os.path.join(ROOT, "packages", "hqfps_weapons")
OUT = os.environ.get("H3D_SHOTS") or tempfile.mkdtemp(prefix="h3dhqfps_shots_")
os.makedirs(OUT, exist_ok=True)
res = []
W, HH = 1280, 760


def check(n, c, d=""):
    res.append(bool(c))
    print(("PASS " if c else "FAIL ") + n + ("  [%s]" % d if d else ""), flush=True)


def boot(project, mode):
    import importlib.util
    spec = importlib.util.spec_from_file_location("Heaven3D", ENGINE)
    H = importlib.util.module_from_spec(spec)
    sys.modules["Heaven3D"] = H
    spec.loader.exec_module(H)
    H._h3d_set_splash_mode("off")
    eng = H.HeavenEngine(mode=mode, width=W, height=HH,
                         project_path=os.path.join(project, "main.heaven3d"))
    return H, eng


def shot(name):
    import moderngl
    import pygame
    data = moderngl.get_context().screen.read(components=3)
    pygame.image.save(
        pygame.transform.flip(pygame.image.frombuffer(data, (W, HH), "RGB"), False, True),
        os.path.join(OUT, name + ".png"))


def run(project):
    import numpy as np
    import pygame
    H, eng = boot(project, "runtime")
    import glm

    mod = sys.modules.get("h3dpkg_hqfps_weapons")
    check("package loaded", mod is not None)
    if mod is None:
        print("\n%d/%d passed" % (sum(res), len(res)), flush=True)
        os._exit(1)

    check("skinning hooks in the geometry + shadow shaders",
          "mh_skin_matrix" in H.GEOMETRY_VERT and "mh_skin_matrix" in H.SHADOW_VERT)
    cat = mod.catalogue()
    check("all 20 viewmodels found", len(cat) == 20, "%d" % len(cat))
    guns = [k for k, v in cat.items() if v["kind"] == "gun"]
    melee = [k for k, v in cat.items() if v["kind"] == "melee"]
    throw = [k for k, v in cat.items() if v["kind"] == "throw"]
    check("weapons classified", len(guns) == 11 and len(melee) == 4 and len(throw) == 2,
          "%d guns, %d melee, %d throwables" % (len(guns), len(melee), len(throw)))
    check("stats are the Unity ones",
          cat["M1911"]["damage"] == 40.0 and cat["M1911"]["magazine"] == 7
          and cat["AKM"]["magazine"] == 30 and cat["R870"]["pellets"] == 8
          and cat["HuntingRifle"]["damage"] == 100.0,
          "M1911 %gdmg/%drnd, AKM %drnd, R870 %d pellets, HuntingRifle %gdmg"
          % (cat["M1911"]["damage"], cat["M1911"]["magazine"], cat["AKM"]["magazine"],
             cat["R870"]["pellets"], cat["HuntingRifle"]["damage"]))
    check("AKM is full auto and the pump R870 is not",
          cat["AKM"]["auto"] == 1 and cat["R870"]["auto"] == 0
          and abs(cat["AKM"]["fire_interval"] - 60.0 / 530.0) < 1e-3,
          "AKM %.3fs/shot, R870 %.3fs/shot" % (cat["AKM"]["fire_interval"],
                                               cat["R870"]["fire_interval"]))

    # ---- arena ---------------------------------------------------------
    for n in eng.scene.root.traverse():
        if n.is_ocean:
            n.ocean_settings.enabled = False
    ground = eng.add_plane(size=80.0, name="Ground")
    ground.material.base_color = (0.30, 0.33, 0.30, 1.0)
    for i, x in enumerate((-6.0, 0.0, 6.0)):
        w = eng.add_cube(size=1.0, name="Wall%d" % i)
        w.scale = glm.vec3(3.0, 3.0, 0.4)
        w.position = glm.vec3(x, 1.5, -12.0)
        w.collider = H.BoxCollider(size=(1.0, 1.0, 1.0))

    target = eng.add_cube(size=1.0, name="Target")
    target.scale = glm.vec3(1.4, 2.2, 1.0)
    target.position = glm.vec3(0.0, 1.1, -7.0)
    target.collider = H.BoxCollider(size=(1.0, 1.0, 1.0))
    hp = eng.attach_blueprint(target, H.Health, {"max_health": 5000.0, "hide_on_death": False})

    zombie = eng.add_cube(size=1.0, name="Zombie")
    zombie.scale = glm.vec3(0.9, 1.8, 0.9)
    zombie.position = glm.vec3(3.5, 1.0, -7.0)
    zombie.collider = H.BoxCollider(size=(1.0, 1.0, 1.0))
    zhp = eng.attach_blueprint(zombie, H.Health, {"max_health": 120.0, "hide_on_death": False})
    eng.attach_blueprint(zombie, H.EnemyAI, {"move_speed": 0.0, "detect_range": 0.0,
                                             "attack_range": 0.0, "max_health": 120.0})

    player = H.SceneNode("Player", parent=eng.scene.root)
    player.position = glm.vec3(0.0, 1.0, 0.0)
    eng.attach_blueprint(player, H.FPSController)
    bp = mod.give_weapons(eng, player, weapons=",".join(mod.DEFAULT_ORDER), start=0)
    check("arsenal attached to the player", bp is not None)
    eng._begin_play()

    cam = eng.camera

    def aim_at(node):
        import math
        pc = eng.first_blueprint("FPSController")
        d = glm.normalize(glm.vec3(node.world_position()) - glm.vec3(cam.position))
        yaw = math.degrees(math.atan2(-d.x, -d.z))
        pitch = math.degrees(math.asin(max(-1.0, min(1.0, d.y))))
        if pc is not None:
            pc.yaw, pc.pitch = yaw, pitch
        cam.yaw, cam.pitch = yaw, pitch

    def hand_view_z():
        """View-space Z of the posed right hand.

        Negative means in front of the eye.  This is the check that catches
        a viewmodel rotated or placed behind the camera -- it renders to
        nothing on screen but every other assertion still passes.
        """
        vm = bp.vm
        names = [x.get("name", "") for x in vm.arms.model.glb.nodes]
        G = vm.arms.model.global_matrices(vm.arms.clip(vm.clip), vm.t)
        i = names.index("Hand.R")
        lp = G[i][:3, 3]
        wp = vm.root.world_matrix() * glm.vec4(float(lp[0]), float(lp[1]), float(lp[2]), 1.0)
        return (cam.view() * wp).z

    # ---- the script ----------------------------------------------------
    def wait(pred, label, limit=400, detail=lambda: ""):
        for _ in range(limit):
            if pred():
                check(label, True, detail())
                return True
            yield
        check(label, False, "timed out after %d frames; %s" % (limit, detail()))
        return False

    def idle():
        return bp.vm is not None and bp.vm.clip in ("Idle", "Hold") and bp.vm.loop

    def switch_to(name):
        bp._begin_switch(mod.DEFAULT_ORDER.index(name))
        yield from wait(lambda: bp.name == name and idle(),
                        "switched to %s" % name, 400, lambda: "%s / %s" % (bp.name, bp.vm.clip))

    # The engine clears the one-shot input flags in end_frame(), which runs
    # immediately after display.flip() -- that is, right after this script
    # gets its turn.  So a flag set here would be wiped before the next
    # tick ever sees it.  Queue them instead and re-apply after the clear.
    pending = {"lmb": False, "keys": set()}
    _real_end = eng.input.end_frame

    def end_frame():
        _real_end()
        if pending["lmb"]:
            eng.input.lmb = True
            eng.input.lmb_just_pressed = True
            pending["lmb"] = False
        for k in pending["keys"]:
            eng.input._keys.add(k)
            eng.input._pressed.add(k)
        pending["keys"].clear()

    eng.input.end_frame = end_frame

    def click():
        pending["lmb"] = True
        yield                     # the tick after this one sees the press
        eng.input.lmb = False

    def press(key):
        pending["keys"].add(key)
        yield
        eng.input._keys.discard(key)

    def script():
        vm = bp.vm
        check("viewmodel built: arms + weapon",
              len(vm.arms.nodes) == 2 and len(vm.gun.nodes) >= 1,
              "%d arm meshes, %d gun meshes" % (len(vm.arms.nodes), len(vm.gun.nodes)))
        check("arms are the bare-skin set (Arm_Standard, no gloves)",
              all("Arm_Standard" in n.name for n in vm.arms.nodes))
        check("arm texture wired", os.path.isfile(str(vm.arms.nodes[0].material.base_color_tex)),
              os.path.basename(str(vm.arms.nodes[0].material.base_color_tex)))
        check("weapon texture wired", os.path.isfile(str(vm.gun.nodes[0].material.base_color_tex)),
              os.path.basename(str(vm.gun.nodes[0].material.base_color_tex)))
        check("starts on the draw animation", vm.clip == "Draw", vm.clip)
        aim_at(target)
        yield
        span = float(np.abs(vm.arms.model.palette(vm.arms.clip(vm.clip), vm.t) - np.eye(4)).max())
        check("arms are posed, not left in their bind pose", span > 0.2,
              "max |palette - I| = %.2f" % span)
        shot("01_draw")

        yield from wait(idle, "draw settles into the idle loop", 300, lambda: bp.vm.clip)
        aim_at(target)
        yield
        # Checked on the settled idle pose: during the draw the hands are
        # still swinging up and legitimately sit near the eye.
        z = hand_view_z()
        check("the hands are IN FRONT of the eye", z < -0.15, "view-space z = %.2f" % z)
        shot("02_idle_m1911")

        # ---- firing ----
        hp0, ammo0 = hp.hp, bp.ammo["M1911"]
        yield from click()
        check("firing plays the fire clip", bp.vm.clip in ("Fire", "AimFire"), bp.vm.clip)
        check("firing spends a round", bp.ammo["M1911"] == ammo0 - 1,
              "%d -> %d" % (ammo0, bp.ammo["M1911"]))
        check("hitscan damaged the target", hp.hp == hp0 - 40.0,
              "%.0f -> %.0f hp (M1911 does 40)" % (hp0, hp.hp))
        shot("03_fire")

        # semi-auto must not keep firing while the button is held down
        held = bp.ammo["M1911"]
        eng.input.lmb = True
        for _ in range(30):
            yield
        eng.input.lmb = False
        check("semi-auto needs a fresh click", bp.ammo["M1911"] == held,
              "still %d rounds after holding 30 frames" % bp.ammo["M1911"])

        # ---- reloading ----
        yield from wait(idle, "back to idle after firing", 200)
        bp.ammo["M1911"] = 0
        res0 = bp.reserve["M1911"]
        yield from press("r")
        check("empty magazine plays the empty-reload clip",
              bp.vm.clip in ("ReloadEmpty", "Reload"), bp.vm.clip)
        yield from wait(lambda: bp.ammo["M1911"] > 0, "reload refills the magazine", 400,
                        lambda: "%d rounds" % bp.ammo["M1911"])
        check("rounds came out of the reserve",
              bp.ammo["M1911"] == 7 and bp.reserve["M1911"] == res0 - 7,
              "%d loaded, reserve %d -> %d" % (bp.ammo["M1911"], res0, bp.reserve["M1911"]))
        shot("04_after_reload")

        # ---- buckshot ----
        yield from switch_to("R870")
        check("the shotgun's own mesh loaded",
              any("R870" in (n.material.name or "") for n in bp.vm.gun.nodes),
              ", ".join(n.material.name for n in bp.vm.gun.nodes))
        aim_at(target)
        yield
        shot("05_r870")
        hp1 = hp.hp
        yield from click()
        lost = hp1 - hp.hp
        check("buckshot fires a spread of pellets", lost >= 30.0,
              "%.0f damage from 8 x 15 pellets" % lost)
        shot("06_r870_fire")

        # ---- melee ----
        yield from switch_to("CombatKnife")
        # The knife reaches 1.2 m (HQFPS MaxHitDistance), so step up to the
        # target -- at range it is supposed to hit nothing.
        home = glm.vec3(player.position)
        player.position = glm.vec3(0.0, 1.0, -5.6)
        aim_at(target)
        yield
        shot("07_knife")
        hp2 = hp.hp
        yield from click()
        check("melee plays a swing", bp.vm.clip in ("Stab1", "Stab2", "ComboAttack", "Fire"),
              bp.vm.clip)
        check("the swing has not landed yet (cast delay)", hp.hp == hp2, "%.0f hp" % hp.hp)
        yield from wait(lambda: hp.hp < hp2, "melee lands its hit after the cast delay", 200,
                        lambda: "%.0f -> %.0f hp" % (hp2, hp.hp))
        shot("08_knife_hit")
        check("the knife did its Unity damage (25 or 27)", (hp2 - hp.hp) in (25.0, 27.0),
              "%.0f damage" % (hp2 - hp.hp))
        player.position = home

        # ---- throwables ----
        yield from switch_to("FragGrenade")
        shot("09_grenade")
        yield from click()
        yield from wait(lambda: any("thrown" in n.name for n in eng.scene.root.traverse()),
                        "throwing spawns a live projectile", 300)
        shot("10_grenade_thrown")

        # ---- every viewmodel ----
        bad = []
        for name in mod.DEFAULT_ORDER:
            try:
                v = mod.Viewmodel(eng, name)
                ok = len(v.arms.nodes) == 2 and v.arms.tex is not None
                if name != "Unarmed":
                    ok = ok and len(v.gun.nodes) >= 1 and v.gun.tex is not None
                # every model must at least have something to hold.  Draw and
                # Holster are not universal: the Syringe ships only Hold+Use.
                ok = ok and v.duration("Idle") > 0.0
                if not ok:
                    bad.append(name)
                v.destroy()
            except Exception as ex:
                bad.append("%s(%s)" % (name, ex))
        check("all %d viewmodels build, texture and animate" % len(mod.DEFAULT_ORDER),
              not bad, ", ".join(bad) or "no failures")

        # ---- full auto on an enemy ----
        yield from switch_to("AKM")
        zhp.hp = 120.0
        aim_at(zombie)
        yield
        z0 = zhp.hp
        eng.input.lmb = True
        yield from wait(lambda: bp.ammo["AKM"] <= 24, "full auto keeps firing while held", 300,
                        lambda: "%d of 30 rounds left" % bp.ammo["AKM"])
        shot("11_akm_full_auto")
        yield from wait(lambda: zhp.hp <= 0.0, "the enemy is killed", 400,
                        lambda: "zombie %.0f -> %.0f hp" % (z0, zhp.hp))
        eng.input.lmb = False
        yield
        shot("12_enemy_down")

        print("\n%d/%d passed" % (sum(res), len(res)), flush=True)
        print("SCREENSHOTS", OUT, flush=True)
        sys.stdout.flush()
        os._exit(0 if all(res) else 1)

    gen = script()
    budget = [6000]

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
    proj = tempfile.mkdtemp(prefix="h3dhqfps_")
    shutil.copytree(os.path.join(ROOT, "templates", "empty"), proj, dirs_exist_ok=True)
    os.makedirs(os.path.join(proj, "packages"), exist_ok=True)
    shutil.copytree(PKG, os.path.join(proj, "packages", "hqfps_weapons"),
                    ignore=shutil.ignore_patterns("__pycache__", "tools", "tests"))
    rc = subprocess.run([sys.executable, __file__, proj]).returncode
    print("ALL PASSED" if rc == 0 else "FAILED")
    sys.exit(rc)
