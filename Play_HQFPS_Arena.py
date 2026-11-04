"""Play the HQ FPS arsenal in the walled arena.

    python Play_HQFPS_Arena.py                 # 1600x900
    python Play_HQFPS_Arena.py 1920 1080
    python Play_HQFPS_Arena.py --no-zombies    # arena and guns only

Loads HQFPS_Arena.heaven3d in runtime mode (so play starts immediately),
hands the player the whole arsenal and fills the arena with MakeHuman
zombies that come after you.

Controls
    WASD / Shift / Space    move, sprint, jump
    mouse                   look          ESC   release the mouse
    LMB  fire / swing / throw             RMB   aim down sights
    R    reload              1-0 / wheel / Q-E   switch weapon
"""
import importlib.util
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCENE = os.path.join(HERE, "HQFPS_Arena.heaven3d")

# Where the zombies stand: in front of the player spawn at (0, -14), spread
# across the arena and clear of the crates.
ZOMBIES = [(-3.0, 4.0), (3.5, 6.0), (-9.0, 11.0), (0.0, 12.0), (8.0, 9.0),
           (-14.0, 6.0), (13.0, 15.0), (-5.0, 17.0)]


def main(argv):
    w, h = 1600, 900
    nums = [a for a in argv if a.isdigit()]
    if len(nums) >= 2:
        w, h = int(nums[0]), int(nums[1])
    want_zombies = "--no-zombies" not in argv

    spec = importlib.util.spec_from_file_location("Heaven3D",
                                                  os.path.join(HERE, "Heaven3D.py"))
    H = importlib.util.module_from_spec(spec)
    sys.modules["Heaven3D"] = H
    spec.loader.exec_module(H)
    H._h3d_set_splash_mode("off")

    # Runtime mode loads the scene and begins play inside the constructor,
    # which is why everything below can attach straight to a live game.
    eng = H.HeavenEngine(mode="runtime", width=w, height=h, project_path=SCENE)
    import glm

    wep = sys.modules.get("h3dpkg_hqfps_weapons")
    if wep is None:
        print("The hqfps_weapons package did not load - nothing to shoot with.")
        return 1

    player = eng.find_player_spawn()
    if player is None:
        print("HQFPS_Arena.heaven3d has no Player Spawn.")
        return 1
    wep.give_weapons(eng, player)

    chars = sys.modules.get("h3dpkg_mh_characters")
    if want_zombies and chars is not None:
        p = glm.vec3(player.position)
        for i, (x, z) in enumerate(ZOMBIES):
            spec_z = chars.CORE.random_spec(chars._lib(), seed=4100 + i, style="zombie")
            yaw = math.degrees(math.atan2(-(p.x - x), -(p.z - z)))
            z_node = chars.place_character(eng, spec_z, pos=glm.vec3(x, 0.0, z),
                                           name="Zombie%d" % (i + 1), yaw=yaw,
                                           select=False)
            eng.attach_blueprint(z_node, chars.CLS["MHEnemy"], {
                "chase_speed": 2.1, "patrol_speed": 0.9, "detect_range": 30.0,
                "attack_range": 1.5, "attack_damage": 9.0, "require_los": False,
                "max_health": 60.0,
            })
        print("%d zombies are hunting you." % len(ZOMBIES))
    elif want_zombies:
        print("mh_characters did not load - the arena is empty.")

    print(__doc__.split("Controls")[1].rstrip())
    eng.run()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
