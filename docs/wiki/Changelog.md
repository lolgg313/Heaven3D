# Changelog

Engine **3.2.0** is the current version. The repository's README has the full notes of every version.

## 3.2.0

**FarScream map generator (new demo package)**
- one seed makes a furnished island: 3 military camps, a radio outpost, 2 islander villages, a plane crash, a shipwreck, road wrecks; *FarScream* template
- pistol at the start; soldiers (pistol / MP5) drop their gun and .45 ammo; islanders run at you and punch; exploding fuel drums; supply crates; objectives with markers
- both trucks drive in third person (weight, handbrake drifts, suspension, orbit camera that recentres), F to get in / out
- the 19 models: invisible geometry optimization, sub-pixel LODs, shared meshes, low-poly collision copies, distance-based texture streaming (≈5.7 GB → a few hundred MB of textures), characters prepared on a worker thread
- at most 40 bullet holes on the map, the oldest goes first

**Towns of painted buildings**
- buildings that stand still are drawn in 48 m districts: one vertex buffer, one draw call per material, one shadow caster (a 62-building town: 1,297 draw calls → 96, same picture)
- three levels of detail by distance; far districts keep only their shell in video memory and detail streams in as the camera approaches
- identical house designs are baked once; a Town generator in the Buildings panel and `engine.add_town()`, `set_building_detail()`, `building_stats()`
- window glass no longer casts a solid shadow

**Editor**
- the *Lit / Lit + Wire / Wire* view modes are buttons on the viewport again, F4 cycles them; the wireframe is not drawn over a running game
- hiding an object hides the locked parts it is made of; fixes for the building paint tool with the grid hidden

**Physics**
- compiled pair search, box contacts and contact solver with the same results as the Python code; no second contact pass for pairs without scripts
- 400 boxes at rest: 12.9 → about 1.5 ms a step; a moving pile of 300 bodies: 132 → 37 ms
- mesh-collider trees build about 4× faster (Play starts sooner)
- `on_collision_*` receive a contact that works as `contact.point` and as `contact["point"]`

**Install**
- `pip install heaven3d` from PyPI (engine, editor, Hub, small packages); `heaven3d-packages install all` gets the four asset packs (release `.h3dpkg` files, or the repository archive)
- or everything from GitHub: `pip install git+https://github.com/lolgg313/Heaven3D`; commands `heaven3d`, `heaven3d-hub`, `heaven3d-runtime`, `heaven3d-packages`
- new releases go to PyPI by themselves (`.github/workflows/publish.yml`, trusted publishing)

## 3.1.4 — bug-fix release

- Windows: project and game names in any language export correctly, no silent crashes without a console, games on a USB stick root, reserved names refused
- Linux: exported games' `install-shortcut.sh` escapes names properly; `install.py` asks for sudo and works on Windows and macOS too
- Engine: no black specks on normal-mapped models, no false mesh-collider warning for roads, a closed log pipe no longer crashes a game
- Hub: survives a damaged `hub.json`

## 3.1.3

- a **new editor interface** (Vitrail): menu bar, toolbar, Outliner, live Inspector, undo / redo, asset browser
- **coding inside the editor**: script editor with hot reload, Python console, API reference
- **visual scripting** and **keyframe animation**, both running in exported games
- node materials, prefabs (apply / revert / unlink), FBX and DirectX `.x` import
- Building Maker: buildings painted on a grid, each one object
- fixed particle and transparency edges; the view gizmo's letters are back
- modular engine source in `src/`, built into the single file by `build.py`

## HQ FPS Arena fixes — HQ FPS Weapons 0.4.0, MakeHuman Characters 1.2.2

- **Everything works from the Hub**: New project → *HQ FPS Arena* → Open / Play, no separate launcher. The Hub always gives a project the newest copy of a package.
- **Guns and ammo are real scene objects** you can see, move and save. You start empty-handed and pick guns up off the floor. Older arena projects get them when opened.
- **Zombies only hit at arm's length**: melee reach never exceeds 2 m and needs you in front, at about its height and not behind a wall; the claw lands only if you're still there.
- **Hit, then death, once**: non-lethal shots play a hit reaction that keeps the character on its feet; the killing shot plays the death.
- **Bullet holes with every HQ FPS gun** on any engine build; zombies get a blood puff.
- **Aiming never loses mouse-look**; a dropped pointer lock is retaken next frame.
- **No more hollow arms**: arm cuts are closed to the shoulder and the near plane is 3 cm while a weapon is out (`tests/test_hqfps_arms.py` checks all 20 weapons).

## 2.7.3

**MakeHuman Characters 1.2 — lifelike animation**
- 38 CC0 motion-captured clips (Quaternius *Universal Animation Library*) retargeted onto every body
- natural posture (no shrug, upright chest and head)
- movesets: *civilian*, *pistol*, *brawler*, *zombie*, *sword*
- real CC0 pistols and revolvers with a two-handed aim (`gun` property)
- layered animation: speed-blended locomotion, upper-body aim and actions
- a proper hello wave; crowds that keep personal space and lanes; smooth stairs with foot IK
- animations work in Play from the Hub and in exported games
- packages update themselves when Heaven3D ships a newer version

**Optimisation update**
- one scene walk per frame, non-recursive world matrices, skipped redundant material binds, shadow-caster culling: 25–50 % less CPU per frame in large scenes, pixel-identical (`HEAVEN3D_NO_PERF=1` to compare)
- Tropical Islands 1.1: octahedral impostors (64 views), dithered LOD cross-fade, vectorised LOD selection, per-tree colour variation, `lod_crossfade`
- fixed walking on islands in exported games

**Clean shadows and smooth slopes**
- 32-bit world positions in the G-buffer: no more striped shadows or AO grid lines (automatic fallback)
- world-unit shadow bias, soft shadow-edge fade, texel-snapped shadow window
- uphill speed equals flat speed; downhill stays glued to the ground
- Tropical Islands 1.1.1: texel-snapped island shadows
- terrain collision uses the real lowest point (no more falling through the sea floor)

**Physics performance**
- characters up to 15× cheaper (12-character crowd: 52 ms → 3.4 ms)
- heightfield raycasts as a Numba kernel (~4 ms → ~0.01 ms per ray)
- Numba kernels warm up in a background thread
- all verified bit-identical, with and without Numba

## 2.7.1

- **macOS support**: standalone editions for Apple Silicon and Intel, `.app` export with your game icon, OpenGL 4.1 core profile, Mac-style dialogs
- **View menu**: Lit, Lit + Wireframe, Wireframe (x-ray), grid toggle; selection drawn in orange
- **Car Race Kit 1.1**: use your own body and wheel models

## New packages

- **Tropical Islands**: one-click procedural islands, 9 shapes, 150 m – 3 km, AAA 4-layer terrain material, CC0 vegetation with LODs and wind
- **MakeHuman Characters**: MPFB 2 / MakeHuman bodies, 175 clothes, 34 hair styles, 66 skins, Character Creator, Mixamo retargeting, NPCs, enemies, foot IK, *Character Town*

## 2.7.0

- **Standalone editions** for Windows and Linux (bundled Python)
- **Export your game** for Windows (`.exe`) and Linux from either system, with your own icon
- **Portable projects** (FILE BURNER built into saving)
- **Save games**: `engine.save_game()` / `engine.load_game()`
- **Packages** with a Hub page; included: Light Theme, Physics Plus, Spline Roads, Car Race Kit
- **In-game pause menu** (Esc: Resume, Sound volume, Exit)
- **Heaven3D Hub**: projects, templates, tutorials
- **New `.blend`, `.gltf` and `.glb` importers** (31/31 hard test files pass)
- **Imported models are one object**

For full details see the [README](https://github.com/lolgg313/Heaven3D#readme) and the [commit history](https://github.com/lolgg313/Heaven3D/commits).
