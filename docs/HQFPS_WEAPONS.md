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
python Play_HQFPS_Arena.py --no-zombies
```

`HQFPS_Arena.heaven3d` is the walled 40 x 40 arena from the *fps_arena*
template. The launcher opens it in runtime mode, hands the player the whole
arsenal and puts eight MakeHuman zombies in it that come after you. ESC
releases the mouse.

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

**Placement is derived, not dialled in.** The rigs are authored around a
standing figure, with the hands about 1.4 m up, so the viewmodel root is
solved from `T + R·anchor = eye + offset`. One offset therefore places all
twenty weapons. The rigs also keep Unity's +Z-forward, +X-left convention
while Heaven3D nodes face −Z, so the root is turned 180° about Y and pitch
and roll are negated to match.

**Aiming** is measured per weapon: the posed weapon mesh gives its own
centreline and sight height, which puts each gun's sights on the crosshair
without hand-tuning eleven aim poses.

---

## Tests

```
python packages/hqfps_weapons/tests/test_skin.py      # 57 checks, no GPU needed
python tests/test_hqfps_weapons.py                    # 42 checks, needs a GPU
python tests/test_hqfps_with_characters.py            # 14 checks, needs a GPU
```

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
