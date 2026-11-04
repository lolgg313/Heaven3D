# scripts/hqfps_arena.py — the "HQ FPS Arena" template.
#
# One blueprint, attached to the Player Spawn: it hands the player the HQ
# FPS arsenal and sets a horde of MakeHuman zombies on them.  Heaven3D
# imports every .py in a project's scripts/ folder, so HQFPSArena shows up
# in the Inspector like any built-in blueprint.
#
# Both packages are optional at runtime: without hqfps_weapons you get the
# arena and the stock weapon, without mh_characters you get the arena with
# no horde, and either way the scene still plays.
import math
import random
import sys

import glm

from Heaven3D import Blueprint, Log, blueprint

WEAPONS_PKG = "h3dpkg_hqfps_weapons"
CHARACTERS_PKG = "h3dpkg_mh_characters"


@blueprint
class HQFPSArena(Blueprint):
    """Arms the player and fills the arena with zombies.

    Attach to the Player Spawn.  The horde spawns in a ring around the
    arena centre, out of the player's lap, and is cleaned up on Stop so
    the scene you saved is the scene you get back.
    """

    DEFAULTS = {
        "weapons": "",              # comma separated; empty = every weapon
        "start_weapon": 0,
        "zombies": 8,
        "zombie_health": 60.0,
        "zombie_speed": 2.1,
        "spawn_radius": 13.0,
        "keep_clear": 7.0,          # no zombie closer than this to the player
        "seed": 4100,
    }

    def __init__(self):
        super().__init__()
        for k, v in self.DEFAULTS.items():
            self.properties.setdefault(k, v)
        self.horde = []

    def p(self, k):
        return self.properties.get(k, self.DEFAULTS.get(k))

    # ------------------------------------------------------------------
    def on_begin_play(self):
        self.horde = []
        self._arm()
        self._spawn_horde()

    def on_end_play(self):
        for z in self.horde:
            try:
                z.remove()
            except Exception:
                pass
        self.horde = []

    # ------------------------------------------------------------------
    def _arm(self):
        wep = sys.modules.get(WEAPONS_PKG)
        if wep is None:
            Log.warn("HQ FPS Arena: the hqfps_weapons package is not loaded, "
                     "so the player keeps the stock weapon")
            return
        wep.give_weapons(self.engine, self.node,
                         weapons=str(self.p("weapons") or ""),
                         start=int(self.p("start_weapon")))

    def _spawn_horde(self):
        count = max(0, int(self.p("zombies")))
        if count <= 0:
            return
        chars = sys.modules.get(CHARACTERS_PKG)
        if chars is None:
            Log.warn("HQ FPS Arena: the mh_characters package is not loaded, "
                     "so the arena is empty")
            return
        rng = random.Random(int(self.p("seed")))
        here = glm.vec3(self.node.position)
        radius = float(self.p("spawn_radius"))
        clear = float(self.p("keep_clear"))
        for i in range(count):
            # Spread them round the arena centre, never on top of the
            # player: a zombie in your face on frame one is not a fight.
            for _ in range(24):
                x = rng.uniform(-radius, radius)
                z = rng.uniform(-radius, radius)
                if glm.length(glm.vec2(x - here.x, z - here.z)) >= clear:
                    break
            spec = chars.CORE.random_spec(chars._lib(),
                                          seed=int(self.p("seed")) + i,
                                          style="zombie")
            yaw = math.degrees(math.atan2(-(here.x - x), -(here.z - z)))
            node = chars.place_character(self.engine, spec, pos=glm.vec3(x, 0.0, z),
                                         name="Zombie%d" % (i + 1), yaw=yaw,
                                         select=False)
            self.engine.attach_blueprint(node, chars.CLS["MHEnemy"], {
                "chase_speed": float(self.p("zombie_speed")),
                "patrol_speed": float(self.p("zombie_speed")) * 0.45,
                "detect_range": 40.0,
                "attack_range": 1.5,
                "attack_damage": 9.0,
                "require_los": False,
                "max_health": float(self.p("zombie_health")),
            })
            self.horde.append(node)
        Log.info("HQ FPS Arena: %d zombies are hunting you" % len(self.horde))
