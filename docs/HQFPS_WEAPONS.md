# HQ FPS Weapons

The HQ FPS Template weapon system, ported into Heaven3D: twenty GPU-skinned
first-person viewmodels with their original animations, sounds and stats.

Eleven firearms, four melee weapons, two throwables and three tools, each one
the bare-skin `Arm_Standard` arm rig (no gloves) plus its own weapon mesh.

---

## Playing it right now

```
python Play_HQFPS_Arena.py            # or double-click "Play HQFPS Arena.bat"
python Play_HQFPS_Arena.py 1920 1080
python Play_HQFPS_Arena.py --where    # print the project folder
```

**HQ FPS Arena is its own template** (`templates/hqfps_arena`), listed in
the Hub beside Tropical Island, Character Town and Race Track. It changes
no existing scene and conflicts with no other kit. The first run makes a
project from it in your Heaven3D Projects folder, with `hqfps_weapons` and
`mh_characters` copied in the way the Hub does it, so it opens in the
editor like any other project and exports like any other game.

The scene is the walled 40 x 40 arena with its crates and pickups, and one
blueprint — `HQFPSArena`, in the template's own `scripts/` — hands the
player the arsenal and sets eight MakeHuman zombies on them. Its count,
speed, health, spawn radius and loadout are Inspector properties. Without
either package it still plays: you get the arena, and a warning saying
what is missing.

---

## Using it in your own scene

Attach **HQWeapons** to the player — the node that carries `FPSController` —
or use the Packages menu: *HQ FPS: give the player every weapon*.

A Player Spawn grows Heaven3D's own `Weapon` blueprint when play starts.
That one fires on the same LMB and draws its own crosshair, so `HQWeapons`
stands it down on begin-play rather than letting every click fire twice.

From a script:

```python
import sys
mod = sys.modules["h3dpkg_hqfps_weapons"]
mod.give_weapons(engine)                                  # every weapon
mod.give_weapons(engine, weapons="M1911,AKM,CombatKnife")  # or a loadout
```

### Controls

| | |
|---|---|
| **LMB** | fire · swing · throw |
| **RMB** | aim down sights |
| **R** | reload |
| **1 – 0** | pick a weapon |
| **wheel**, **Q** / **E** | previous / next weapon |

### Properties

| property | default | |
|---|---|---|
| `weapons` | *(empty)* | comma-separated loadout; empty means all of them |
| `start_index` | `0` | which one is drawn first |
| `reserve_magazines` | `4` | spare magazines per weapon |
| `infinite_reserve` | `False` | never run down the reserve |
| `camera_shake` | `1.0` | recoil kick on the camera; `0` disables it |
| `show_hud` | `True` | crosshair, ammo counter, reload bar |
| `volume` | `0.7` | |

Weapons damage anything that answers the `damage` message, so `Health`,
`EnemyAI` and the `mh_characters` enemies all take hits with no extra wiring.

---

## The weapons

Damage, magazine size, cadence and reload times are read out of the Unity
`Info_*.asset` files rather than retyped, so they are the project's own
numbers.

| weapon | damage | mag | rate | notes |
|---|---|---|---|---|
| M1911 | 40 | 7 | 6.7/s | semi-auto |
| Revolver | 60 | 6 | 5.0/s | has a dry-fire animation |
| MP5 | 25 | 30 | 10.0/s | full auto |
| AKM | 40 | 30 | 8.8/s | full auto |
| M1A | 55 | 15 | 5.7/s | semi-auto |
| F1 | 30 | 24 | 2.2/s | 3-round burst |
| R870 | 15 × 8 | 7 | 1.3/s | pump, three-part reload |
| Double Barrel Shotgun | 25 × 6 | 2 | 3.3/s | |
| Hunting Rifle | 100 | 5 | 0.5/s | bolt action |
| Flare Gun · Crossbow | — | 1 | | projectile weapons |
| Combat Knife | 25 / 27 | — | | two alternating stabs, 1.2 m |
| Baseball Bat | 30 / 40 | — | | combo + strong swing, 1.3 m |
| Fire Axe | 45 | — | | 1.15 m |
| Frag Grenade | 90 | — | | 10 m blast |
| Molotov Cocktail | 15 | — | | fire pool |
| Flashlight · Compass · Syringe | — | — | | tools |

### A note on fire rate

HQFPS reads `RoundsPerMinute` **only** in full-auto; semi-auto uses
`FireDuration` and burst uses `BurstDuration + BurstPause`
(`ProjectileWeapon.UpdateFireModeSettings`). This port follows the same rule,
which is why the pump-action R870 fires at 1.3/s and not at the nonsense
15 RPM its asset carries — Unity never reads that field for it either.

---

## How it works

**Two halves, one clip.** In HQFPS the arms and the weapon are separate
skinned meshes, each with its own armature and its own copy of every
animation, played in sync. The port keeps that: `assets/models/<name>.glb`
is the arms plus the 44-bone arm rig, `assets/models_gun/<name>.glb` is the
weapon plus its moving parts (slide, hammer, trigger, magazine, cylinder),
and one clip name drives both.

**Skinning** reuses the `u_mh_*` shader hooks that `mh_characters` installs,
verbatim and idempotently. Whichever package loads first patches the
shaders; the other sees the hooks already there and leaves them alone. Both
can be in one scene.

**Placement is HQFPS's own.** In `Player.prefab` the weapon rig hangs off
the camera at `View_Root(y 1.6) / Camera_Root / … / FP_Equipment(0, −1.6,
0.33)`, so the rig origin sits 1.6 m below the eye — cancelling the
standing figure the rigs are authored around — and 0.33 m in front. That
is the offset this port uses, which is why every weapon lands where its
author put it and the animations are not re-anchored underneath. The rigs
also keep Unity's +Z-forward, +X-left convention while Heaven3D nodes face
−Z, so the root is turned 180° about Y and pitch and roll are negated.

**Aiming** is measured per weapon: on the posed weapon mesh, the top
surface nearest the shooter is the rear sight, and that is the point put on
the crosshair — no hand-tuned aim pose for each of the eleven guns.

**Ammunition is per calibre**, out of the HQFPS Item Database: the M1911
and the MP5 share one 45ACP pouch, the R870 and the double barrel share 12
gauge, each rifle has its own, and every pouch stops at the carry limit the
database gives it. Ammo pickups (`add_ammo`) feed the round in hand, or a
named calibre if the pickup says so.

**Shots leave the engine's own bullet holes.** A Player Spawn's built-in
`Weapon` makes decals itself, so standing it down took them away with it;
`HeavenEngine.spawn_impact_fx(hit)` now exposes that same hole, spark and
flash to any package. Scenery gets a hole and a surface sound; a body gets
a wet thud and no hole.

**A thrown grenade is the grenade.** The throwables spawn their own model,
centred on itself and textured, with the Molotov's lighter left in the
hand — not a stand-in sphere.

---

## Tests

```
python packages/hqfps_weapons/tests/test_skin.py      # 62 checks, no GPU needed
python tests/test_hqfps_weapons.py                    # 56 checks, needs a GPU
python tests/test_hqfps_with_characters.py            # 14 checks, needs a GPU
python tests/test_hqfps_arms.py                       # 20 weapons, no GPU needed
python tests/test_hqfps_arena_fixes.py                # 14 checks, needs a GPU
```

`test_hqfps_arms.py` skins the arms through every clip of every weapon
(16 moments each) exactly as the viewmodel shows them, and fails if an open
edge of the arm mesh, or the camera's near plane cutting through it, is ever
inside the view -- the two ways you could look into the hollow arm. The arms
are cut off above the elbow; `hq_arms.py` continues each cut to where a
shoulder would be (beside, below and behind the eye), bound to the upper arm
at the cut and to the arms' root at the shoulder, and closes it there.

`test_hqfps_arena_fixes.py` plays the HQ FPS Arena template: zombie reach,
hit-then-death with the M1911, bullet holes (also on an engine without
`spawn_impact_fx`), the right mouse button and the pointer lock, the closed
arms and the 3 cm near plane.

The first also pins the UV convention — glTF's origin is top-left and
Heaven3D samples bottom-left, so V must be flipped exactly as the engine's
own glTF importer flips it. Getting that wrong does not look like a bug in
the loader: the model is perfectly shaped and perfectly lit, it just wears
the wrong half of its own atlas.

The first validates the glTF skinning runtime: the bind pose must rebuild to
identity (which checks the inverse-bind matrices, the hierarchy walk, TRS
composition and forward kinematics all at once), clips must not snap on their
first frame, idle loops must be seamless, and every weapon's arm clips must
match its gun clips.

The second drives a real engine frame by frame, waiting on state rather than
frame counts, and screenshots each step. Among other things it checks that
the hands are *in front of the eye* — a viewmodel placed behind the camera
renders to nothing while every other assertion still passes.

The second also guards the audio, which is easy to break silently: every
sound a weapon can ask for must resolve to a real file (a glob like
`*Reload*` matches the `Gun_Reloading` *folder* just as happily as a clip),
every gun must have a shot and every blade a swing, firing must leave a
playable sound registered, and nothing the run played may have failed to
load.

The third puts both skinning packages in one scene and shoots `mh_characters`
zombies with the rifle and stabs one with the knife. It asserts the shader
hooks end up installed exactly once, that both packages share the bone-palette
texture unit, and that the viewmodel and the characters are posed in the same
frame.

---

## Assets

Converted from the HQ FPS Template with Blender 4.3 (`FBX → glTF`), textures
recompressed from 1.83 GB to 121 MB. 183 original sound effects. The assets
are the HQ FPS Template's and stay under its licence; this package is the
code that runs them.
