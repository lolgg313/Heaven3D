# MakeHuman Characters

The **MakeHuman Characters** package (`packages/mh_characters`) puts realistic, fully rigged
humans into Heaven3D: the body, morphs, clothes and skeleton of **MPFB 2 / MakeHuman**,
built inside the engine (no Blender, no export step), GPU-skinned, and animated with
**Mixamo** clips or with the animations that come built in.

On top of that it adds gameplay: **NPCs that walk paths** and **enemies that patrol, notice
the player, chase, attack, react to hits and die**, all with **foot IK** so feet stay planted
on slopes and stairs.

## Licence: nothing to worry about

Everything shipped in `packages/mh_characters/assets/` is **CC0** (public domain dedication):
the MakeHuman base mesh, morph targets, the MPFB Mixamo-compatible rig and its weights, and
every skin, eye, hair, eyebrow, eyelash and garment. You can use the characters in personal
and **commercial, closed-source** games, change them, and sell what you make, with no credit
or royalty required.

When the library was built, assets that were **not** CC0 (CC-BY packs) were left out, and so
were costumes and textures named after trademarked characters (superhero suits and the
like), so nothing in the box carries someone else's brand. The package's code is part of
Heaven3D (Apache 2.0). See `packages/mh_characters/ASSETS_LICENSE.md` for the full list of
sources.

Mixamo animations are free from Adobe for use in your games, but you download them yourself
(they can't be redistributed as files), so none are included. Without them, every character
still moves using the built-in procedural clips.

## What's in the library

| | |
| --- | --- |
| Body | MakeHuman base mesh (14.5k vertices) or 7 MPFB proxies, from 741 to 16k vertices (low-poly ones are great for crowds) |
| Macro morphs | gender, age (25-90), muscle, weight, height, proportions, breast size / firmness, African / Asian / Caucasian blend |
| Detail morphs | 186 sliders: torso, hips, stomach, arms, legs, hands, feet, neck, head, face, nose, mouth, eyes, ears, chin, cheeks |
| Skins | 66 (young / middle-aged / old in three ethnicities, freckles, make-up, tattoos, zombies, aliens, ...) plus an RGB tint |
| Clothes | 175 CC0 garments: suits, dresses, casual wear, shirts, trousers, skirts, shoes and boots, hats and helmets, gloves, glasses, jewellery, socks, sci-fi armour, robes, viking gear ... Many have extra looks (colour variants) |
| Hair | 34 styles (+ colour variants and a tint), 26 eyebrows, 9 eyelashes, 9 eye colours |
| Skeleton | MPFB's Mixamo rig: 52 bones named `mixamorig:Hips`, `mixamorig:LeftUpLeg`, ... so Mixamo animations drop straight on |

Clothes fit any body: they are refitted to the morphed mesh every time it changes, and the
skin under them is hidden so it never pokes through.

## Getting started

1. In the Hub, open **Packages** and **Install** *MakeHuman Characters*, then **Add to project**
   on your project (or create a project from the **Character Town** template).
2. Open the project. The **Packages** menu (Hierarchy header) now has:

| Tool | What it does |
| --- | --- |
| Characters: new character | a random person in front of the camera |
| Characters: new NPC walker (+ path) | a person + a 4-point path they walk in Play |
| Characters: new enemy (foot IK) | a random soldier / zombie / viking / sci-fi enemy |
| Characters: crowd of 6 on a path | six random walkers sharing one path |
| Characters: use project 'animations' folder | assigns your Mixamo files to every character by file name |
| NPC path: new path / add point | build and extend paths |

3. Click a character. The **Character Creator** opens next to the Inspector.

## The Character Creator

| Tab | |
| --- | --- |
| **Body** | macro sliders, ethnicity blend, body mesh (full / low-poly proxies), body detail sliders by region |
| **Face** | face detail sliders (head, forehead, brows, eyes, nose, mouth, cheeks, chin, ears), *Random face*, *Reset face* |
| **Clothes** | what's worn (switch **look** for colour variants, brightness, remove), *Random outfit*, *Take all off*, and the whole catalogue by slot (full outfits, tops, bottoms, feet, head, hands, eyewear, jewellery, ...). By default only clothes made for the body's gender are listed; *show everything* lists all of them. Picking a top replaces the current outfit's top, a hat replaces a hat, and so on |
| **Hair** | eye colour, hair colour tint, hair style + look, eyebrows, eyelashes |
| **Skin** | skin texture and RGB tint |
| **Anim** | preview any slot in the viewport, load an animation file per slot, foot IK on/off |
| **AI** | the path to walk / patrol, walker mode and speeds, enemy health, damage, ranges, field of view, melee or hitscan |

The top row has **Random**, **Male**, **Female** and a **Style** switch (civilian, soldier,
viking, zombie, scifi) for the random buttons. Drag a slider or click anywhere on it; the
character rebuilds live, and its feet stay on the ground when you change its height.

Everything is saved in the project as plain values (no meshes), and rebuilt when the project
opens, in the editor and in exported games.

## Mixamo animations

1. Go to [mixamo.com](https://www.mixamo.com) (free Adobe account) and pick an animation. The
   default character is fine, you don't have to upload yours.
2. **Download** with *Format: FBX Binary (.fbx)*, *Skin: Without Skin* (smaller files),
   *Frames per second: 30*. For walk / run / strafe clips tick **In Place**.
3. Either put the files in an **`animations` folder next to your project** and use
   **Packages > Characters: use project 'animations' folder** (names decide the slot:
   `Walking.fbx` → walk, `Running.fbx` → run, `Punching.fbx` / `Sword Slash.fbx` → attack,
   `Firing Rifle.fbx` → shoot, `Hit Reaction.fbx` → hit, `Dying.fbx` → death,
   `Waving.fbx` → wave, `Breathing Idle.fbx` → idle), or select a character, open
   **Anim** and press **Load...** next to a slot. Loaded files are copied into
   `<project>/animations/`, so the project and its exported games carry them.

Slots: `idle`, `walk`, `run` (looping, blended by speed), `attack`, `shoot`, `hit`,
`death` (holds its last frame), `wave` (NPC greeting). Empty slots use the built-in
procedural clips.

Also supported: **.glb / .gltf** (any skinned humanoid glTF animation; a file with several
animations gets a **clip k/n** button to choose one) and **.bvh** motion capture (CMU and
similar). Bones are matched by name, ignoring namespaces (`mixamorig:`, `mixamorig1:`,
`Armature|`) and common naming schemes of other rigs.

**How the retargeting works.** Mixamo rigs stand in a T-pose, MakeHuman characters in an
A-pose, and their bones point in different directions. Each bone of the clip is transferred
in world space: the target bone gets the source bone's rotation relative to its rest pose,
after aligning the two rest directions. So arms swing, legs step and the spine and head turn
exactly like the source, whatever the proportions. The hips' motion is scaled by the hip
height ratio, and loops are made in place (forward drift removed) because NPCs move
themselves. The test suite checks that the target thigh follows the source thigh within
3 degrees.

## NPC paths

A path is an empty **NPC Path** node plus blue **waypoint** spheres (`NPC Path P1`,
`P2`, ...). Move the points with the gizmo; **NPC path: add point** extends the selected path.
Waypoints are hidden while playing. A waypoint's `wait` property (seconds) overrides the
walker's wait time there (`-1` = use the walker's).

**NPCWalker** properties: `path`, `mode` (`loop`, `pingpong`, `random`), `speed`, `run`,
`wait_time`, `wander_radius` (no path: wander around the start point), `greet_player`
(stop, turn and wave when the player comes close) and `greet_range`. Walkers use a capsule
character controller: they collide, slide along walls, climb steps and slopes, and turn
smoothly; the walk animation speeds up and slows down with how fast they actually move. A
walker that gets blocked for a few seconds gives up and heads for its next point.

## Enemies

**MHEnemy**: patrols its `path` (or wanders), notices the player inside `detect_range`
within its `fov` (or always within `hearing_range`), optionally needing line of sight
(`require_los`), and keeps chasing for `lose_interest` seconds after losing sight.

- `attack_style` **melee**: plays *attack*; the damage lands on the animation's hit frame if the
  player is still within `attack_range`.
- `attack_style` **hitscan**: plays *shoot* and fires a ray at the player (within
  `hitscan_range`).
- Takes `damage` messages (the built-in **Weapon** works out of the box), plays *hit* and
  staggers, dies at 0 `max_health`: plays *death* and stays down, its collider is removed and
  a `died` message is sent (for scores, doors, spawners ...).

## Foot IK

On by default (Inspector / **Anim** tab: `foot_ik`). In Play, every character within about
28 m raycasts the ground under each foot: the pelvis drops for the lower foot, each leg is
solved as a two-bone chain that keeps the knee's bend plane, and planted feet tilt to the
slope (up to 35°). Lifted feet keep their animated height above the ground, so walking up
stairs or a ramp just works. On level ground the solve is skipped.

## Performance

- Skinning runs on the GPU (bone matrices in a small float texture, added to the engine's
  geometry and shadow shaders). Identical characters share their GPU buffers.
- Animation is level-of-detail'ed by distance (every frame up close, then every 2nd, 4th
  and 8th frame), foot IK only runs near the camera, and its raycasts use a cached broad phase.
- Use the low-poly proxy bodies (`M low-poly`, `F low-poly`, `Lowest 741`) for big crowds.
- Building a character takes about 0.2-0.3 s (morph, fit, rig and the built-in clips).

## Scripting

```python
mh = sys.modules["h3dpkg_mh_characters"]          # the package module
core = sys.modules["h3dpkg_mh_characters_mh_core"]

spec = core.random_spec(mh._lib(), seed=42, gender="female", style="civilian")
spec["macro"]["height"] = 0.6
spec["clothes"].append({"id": "clothes/fedora01"})
npc = mh.place_character(engine, spec, pos=glm.vec3(0, 0, 0), name="Clerk")
engine.attach_blueprint(npc, mh.CLS["NPCWalker"]).properties["path"] = "NPC Path"

rig = mh.rig_of(npc)          # runtime: rig.animator.play("wave"), rig.animator.set_speed(1.3), rig.height
```

A character's look is the `character` property of its **MHCharacter** blueprint: a JSON
dictionary (`macro`, `modifiers`, `body`, `skin`, `skin_color`, `eyes`, `eyebrows`,
`eyelashes`, `hair`, `hair_material`, `hair_color`, `clothes`), so you can also copy looks
between projects or generate them.

## Rebuilding the asset library

Only needed if you want to add more CC0 MakeHuman assets:

```bash
python packages/mh_characters/tools/build_assets.py --mpfb <mpfb2>/src/mpfb/data \
    --packs <makehuman_system_assets> <more unzipped CC0 asset packs> --out packages/mh_characters/assets
```

It reads MPFB 2's data folder and MakeHuman asset packs, keeps only CC0 assets, skips
trademark-named ones, shrinks textures (1024 px skins, 768/512 px clothes, palette PNGs for
hair) and packs meshes, morph targets, fitting data and weights into compact `.npz` files.
`tools/make_template.py` regenerates the *Character Town* template.
