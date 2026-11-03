<div align="center">

# ☁️ Heaven3D

<img src="branding/heaven3d_logo.png" width="140" alt="Heaven3D logo" />

<img width="1690" height="979" alt="gameeglory" src="https://github.com/user-attachments/assets/f4865664-c6f7-41f7-b977-88398f1833ea" />

**A complete 3D game engine built from the metal up — in Python.**

Deferred PBR rendering · CPU physics · Built-in editor · Standalone runtime

![Version](https://img.shields.io/badge/version-2.7.3-blue)
![Python](https://img.shields.io/badge/python-3.10%2B-yellow)
![OpenGL](https://img.shields.io/badge/OpenGL-3.3%2B%20Core-green)
![License](https://img.shields.io/badge/license-Apache%202.0-orange)

</div>

---

## About

Heaven3D is a from-scratch 3D game engine written in Python on top of [ModernGL](https://github.com/moderngl/moderngl), giving direct access to the OpenGL Core profile. No Panda3D, Unity, Unreal, or GameGuru under the hood: the renderer, physics, editor, importers, audio, and gameplay systems are all written specifically for Heaven3D.

The whole engine ships as a single file, `Heaven3D.py`, so it is easy to read, hack on, and bundle into your own projects.

## 🆕 What's new in 2.7.3

**MakeHuman Characters 1.2: lifelike animation.**

- **Motion-captured animations built in**: 38 CC0 clips from Quaternius' *Universal Animation Library* replace the old procedural ones: natural idles, talking, walk, jog, sprint, punches, hit reactions, death, zombie moves, pistol aim / shoot / reload. They're retargeted onto every body (male, female, heavy, any proportions), with no hands sinking into the body and no foot sliding.
- **Natural posture**: shoulders stay relaxed (no shrug), and the chest and head stand upright instead of pushing forward.
- **Movesets**: *civilian*, *pistol*, *brawler*, *zombie* and *sword*, picked automatically (zombie skins shamble, ranged enemies carry a gun, melee enemies punch) or chosen per character.
- **Real guns**: pistol enemies hold one of 10 CC0 pistols and revolvers (Quaternius *Ultimate Gun Pack*) in a two-handed aim, and shots leave the barrel. Pick one with the new `gun` property.
- **Layered animation**: walk speed blends with the feet in phase, the gun aim plays over the walk, and hits, shots and talking play on the upper body only, so legs keep walking.
- **A proper hello wave**: the arm swings out and away from the body; NPCs wave, then chat.
- **Crowds that flow**: walkers keep personal space, follow instead of bumping, step aside for oncoming people and walk in their own lanes, with no jittery turning.
- **Smooth stairs**: the body glides up and down steps with foot IK, no stutter.
- **Animations work everywhere**: characters animate in Play from the Hub and in exported games, not only in the editor.
- **Packages update themselves**: when Heaven3D ships a newer version of a package, older copies in your library and in your projects are replaced automatically when the Hub or editor opens (your package settings are kept).

**Optimization update (the techniques of Unity's best-known optimizer assets, built in):**

- **Faster engine, identical picture**: the scene graph is walked once per frame instead of once per pass, world matrices are computed without recursion, repeated material binds are skipped, and objects outside the sun's shadow view are no longer drawn into the shadow map. Every frame is pixel-for-pixel identical to before (verified on test scenes); CPU time per frame drops roughly 25-50% in large scenes. Set the environment variable `HEAVEN3D_NO_PERF=1` to switch these off.
- **Tropical Islands 1.1**: octahedral impostors for far trees (as in *Amplify Impostors*): 64 baked views blended per pixel with normals, correct from any angle including from above, with proper lighting and shadows. Smooth dithered LOD cross-fades instead of popping (as in Unity's LOD Group / *GPU Instancer*), vectorized LOD selection that only runs when the camera moves, and natural per-tree colour variation. New `lod_crossfade` property.
- **Fixed: walking on islands in exported games**: the player no longer falls through the island in exported games, and palm trunks, trees and boulders block the player in Play as intended (both were broken in the first 2.7.3 build).

**Fixes: clean shadows and smooth slopes.**

- **No more striped / blocky shadows or line grids**: the G-buffer now stores world positions at full 32-bit precision. Half floats only had 12-25 cm steps away from the world origin, which drew staircase shadows on sand, diagonal stripes on terrain and rocks, and a grid of lines in the ambient occlusion on every surface. Falls back to the old format automatically on drivers that can't render it.
- **Shadows stick to their objects**: the shadow bias is now measured in world units from the real shadow-texel size (it was ~0.7 m on islands, so shadows floated away from rocks and trunks), shadows fade out softly at the edge of the shadow area instead of ending on a hard line, and the shadow window snaps to whole texels so edges no longer crawl or shimmer while you walk or turn.
- **Walking up ramps and slopes no longer speeds you up**: the character controller re-applied its slide after resolving a contact, so the player moved up to 2x faster up slopes and ramps (and along walls), with jerky bursts on bumps and step-assist teleports on steep ramps. Uphill speed now equals flat-ground speed on terrain, boxes and meshes; walking downhill stays glued to the ground instead of hopping.
- **Tropical Islands 1.1.1**: stable (texel-snapped) island shadows. Existing projects update when opened in the editor.
- **No more falling through the sea floor**: terrain collision stopped 1 m below the terrain's origin, so wading into water deeper than ~3 m dropped the player through the world (and raycasts under water missed the ground). Terrain bounds now use the real lowest point.

**Physics performance (same results, much less CPU):**

- Characters and enemies are up to **15x cheaper** to move: node transforms and collider shapes are cached instead of being rebuilt thousands of times per frame, and each character checks the colliders around it once per move instead of up to nine times. A 12-character crowd went from 52 ms to 3.4 ms of physics per frame.
- **Heightfield raycasts** (enemy line of sight, bullets hitting terrain, step probes) run as a numba kernel: ~4 ms to ~0.01 ms per ray.
- **No first-use hitches**: numba kernels compile / load in a background thread while the game loads, instead of freezing the game the first time an enemy looks for you.
- Every change is verified bit-identical to the previous physics (crowds, slopes, meshes, thousands of random rays), with and without numba installed; without numba the engine still runs the caches (~6x faster).

## What's new in 2.7.1

- **macOS support**: standalone editions for **Apple Silicon** and **Intel** Macs (`Heaven3D Hub.app`, `Heaven3D Editor.app`), and **macOS `.app` export** for your games (with your game icon). The editor runs on Apple's OpenGL 4.1 core profile; dialogs and the Library open the Mac way.
- **Wireframe view**: a **View** menu in the top-left corner of the viewport (not in the crowded toolbar) switches between *Lit*, *Lit + Wireframe* and *Wireframe* (x-ray), and shows or hides the grid. The selected object is drawn in orange.
- **Car Race Kit 1.1**: replace the dummy car's **body and wheels with your own 3D models** (`.glb`, `.gltf`, `.blend`, `.obj`). They're fitted automatically; the wheels spin and steer.

## 🏝️ New package: Tropical Islands

- **One click, a whole island**: *Packages > Islands: new tropical island* generates terrain, beaches, jungle, meadows, flowers, rocks and dirt roads in the engine's ocean (turquoise lagoon water included). Press Play and walk it.
- **Endless variety**: 9 shapes (*classic, volcano, mountains, hills, atoll, twin peaks, plateau, archipelago, flat*), any size from 150 m to 3 km, a seed, coastline roughness, beach width, erosion (river valleys), number of roads and density sliders for jungle, trees, palms, grass, flowers and rocks. Change a setting in the Inspector and the island rebuilds.
- **AAA terrain material**: 4 CC0 PBR layers (beach sand, jungle floor, dirt, mossy rock) blended by height, with per-pixel **slope auto-material** (steep ground turns to rock), triplanar cliffs, anti-tiling and wet sand at the waterline.
- **Realistic CC0 vegetation**: 5 trees (coconut palm in 5 shapes, royal palm, island tree, flowering jacaranda, acacia), 2 grasses, 2 flowers and 2 rock kinds, built from Poly Haven scans and Nobiax palms into game-ready LODs (leaf-cluster cards, impostors far away), swaying in the wind, with alpha-tested shadows and trunk collision in Play.
- Saved as a few numbers; rebuilt identically (and cached) when the project opens. Guide: [docs/ISLANDS.md](docs/ISLANDS.md).

## 🧍 New package: MakeHuman Characters

- **Realistic people, built in the engine**: the MPFB 2 / MakeHuman body with gender, age, muscle, weight, height, proportion and ethnicity morphs plus 186 detail sliders, **175 clothes**, 34 hair styles, 66 skins, eyes and brows, all edited live in a new **Character Creator** panel.
- **Mixamo-ready**: every character gets MPFB's Mixamo skeleton and GPU skinning. Load Mixamo `.fbx` (or `.glb` / `.bvh`) animations per slot and they're retargeted automatically; CC0 motion-captured idle, walk, run, attack, shoot, hit, death and wave clips are built in (see 2.7.3).
- **NPCs and enemies**: NPCs walk paths (loop, ping-pong, random), wait and wave at you; enemies patrol, see and hear you, chase, attack (melee or hitscan), react to hits and die. **Foot IK** keeps feet planted on stairs and slopes.
- **100% CC0 assets**: free for commercial games, no credit, no royalties. New *Character Town* template. Guide: [docs/CHARACTERS.md](docs/CHARACTERS.md).

## What's new in 2.7.0

- **Standalone editions for Windows and Linux**: unzip and double-click. Python and every library are inside, so there's nothing to install.
- **Export your game** for **Windows (.exe)** and **Linux** from either system, with the Heaven3D logo as its icon. Your players install nothing either.
- **Portable projects** (FILE BURNER built in): every save stores asset paths inside the project folder, and anything imported from outside is copied in. Projects open on any PC and either OS.
- **Save games** for your scripts: `engine.save_game()` / `engine.load_game()`, crash-safe, and they travel with the exported game.
- **Packages**: pure-Python add-ons for the Hub, the editor and games, managed from the Hub's new **Packages** page. Included: *Light Theme*, *Physics Plus*, *Spline Roads* and *Car Race Kit* (with a *Race Track* template). See [docs/PACKAGES.md](docs/PACKAGES.md).
- **Your own game icon** (`.png`, `.jpg` or `.bmp`) on exported games: the `.exe` icon, the window, the taskbar and the Linux menu.
- **In-game pause menu** (Esc: Resume, Sound volume, Exit) in Play and in every exported game.
- **Heaven3D Hub** (`Heaven3D_Hub.py`): a launcher like Unity Hub. Create projects from templates, open them in the editor or play them, export them, and follow offline tutorials.
- **New `.blend`, `.gltf` and `.glb` importers**, tested against Blender and the Khronos samples with 8 hard test files per format (31/31 pass, see [tests/importers](tests/importers/README.md)).
- **Imported models are one object**: children and material parts are locked to the main object, so you can't select, move or delete them separately.

## ✨ Features

### Rendering
- **Deferred renderer** with a G-buffer (positions, normals, albedo, material, full-colour emissive)
- **HDR float framebuffers** with **ACES filmic tone mapping**
- **PBR materials** (Cook-Torrance GGX, metallic-roughness workflow) with editable emission
- **Forward transparency pass** for true alpha blending
- **SSAO**, **screen-space reflections**, **bloom**, and **PCF soft shadows**
- **FXAA 3.11** (full Quality path) stacked with **4x hardware MSAA**
- **Procedural day/night sky** and animated volumetric-style clouds, on by default in every scene
- **Procedural ocean** using Gerstner waves, with Beer-Lambert absorption, fresnel sky reflection, sun glint, foam, and CPU-mirrored wave heights for buoyancy
- Sun, point, spot, and ambient lights
- Geometry instancing for shared meshes and materials

### Physics (pure CPU, no extra dependencies)
- Rigid bodies with **box, sphere, capsule, plane, heightfield, and triangle-mesh** colliders (mesh colliders use the node's exact triangles, so low-poly buildings with interiors just work)
- Sequential-impulse solver with friction and restitution
- Broadphase sweep-and-prune, persistent contact manifolds, and simulation islands
- Raycasts, overlap queries, triggers, and collision events
- Fixed-step simulation
- Kinematic **CharacterController** with sliding, slopes, step-offset, and jumping
- Colliders and rigid bodies can be assigned to any mesh straight from the Inspector

### Editor
- Built-in scene editor with Inspector / properties panel
- Transform gizmos, light gizmos, spawn gizmos, view gizmo, and a visual collider shape editor
- **View menu** (top-left of the viewport): Lit, Lit + Wireframe and Wireframe (x-ray) shading, grid on/off
- Procedural terrain generation
- **Building editor**: generate, paint, or manually place modular building sectors on a 2 m grid with a snapped ghost preview, across 5 biomes
- Save and load projects as JSON (`.heaven3d`)

### Gameplay
- **Blueprint system**: attach reusable behaviours to any scene node
- Ready-made blueprints you can attach and play: `FPSController`, `Weapon`, `Projectile`, `Health`, `EnemyAI`, `EnemyFlyShooter`, `Pickup`, `Spawner`, `MovingPlatform`, `TriggerZone`, `LevelEndTrigger`
- **Audio**: 13 built-in synthesised sounds, your own `.wav`/`.ogg` files, 3D positional audio, and music; silent-safe on machines with no audio device
- **HUD**: crosshair, bars, text, and vignette for in-game UI

### Asset import
- **glTF / GLB**, full glTF 2.0: every accessor type (sparse, strided, normalized), all 7 primitive modes, skins (bind pose), morph targets, `EXT_mesh_gpu_instancing`, `KHR_meshopt_compression`, `KHR_draco_mesh_compression` (optional `DracoPy`), `KHR_texture_transform`, unlit, emissive strength, spec-gloss, transmission, punctual lights, texture wrap modes, vertex colours and a second UV set
- **Blender `.blend`** read directly, no Blender install needed: Blender 2.5x up to **5.0**, gzip/zstd compressed, 32-bit and big-endian files, parented objects, linked duplicates, collection instances, lights, materials and textures (packed or external), colour attributes, 2 UV maps, custom/sharp normals and the modifiers Mirror, Array, Subdivision Surface, Solidify, Weld, Edge Split, Triangulate, Smooth by Angle and Multires (levels)
- **OBJ + MTL** with texture resolution
- **One model = one object**: every imported model shows as a single object in the Hierarchy; its children and material parts are locked to it, and colliders cover the whole model

### Performance
- Optional **Numba** acceleration for hot paths, with automatic, bit-identical fallback to plain Python if Numba is unavailable

## 💾 Standalone editions (no install)

| Download | Contents | Start |
| --- | --- | --- |
| `Heaven3D-2.7.3-Windows.zip` | Hub, editor and a bundled Windows Python | double-click **Heaven3D Hub.exe** |
| `Heaven3D-2.7.3-Linux.zip` | Hub, editor and a bundled Linux Python (glibc 2.17+) | run **./Heaven3D-Hub** (`./install-desktop-shortcuts.sh` adds menu entries) |
| `Heaven3D-2.7.3-macOS-AppleSilicon.zip` | Hub, editor and a bundled macOS Python for M1/M2/M3/M4 Macs (macOS 11+) | unlock once (below), then double-click **Heaven3D Hub.app** |
| `Heaven3D-2.7.3-macOS-Intel.zip` | the same for Intel Macs | the same |

| `Heaven3D-2.7.3-Export-Players.zip` | add-on for the macOS editions: the Windows and Linux game players | move its `windows` and `linux` folders into `runtime/players` |

Unzip anywhere, even a USB stick. The Windows and Linux editions each contain both game players, so you can export Windows **and** Linux games from either one. The macOS editions export macOS games; add the Export-Players download to export Windows and Linux games from a Mac too (it's separate to keep the Mac downloads small). The editions are built by `tools/standalone/build_standalone.py`.

**macOS, first start.** Heaven3D isn't from the App Store, so macOS blocks it until you unlock it once: right-click **Fix macOS security warning.command** in the Heaven3D folder and choose **Open**, or run `xattr -dr com.apple.quarantine` on the folder in Terminal. On a trackpad, right-click is a two-finger click (or Control-click).

## 🧩 Packages

Packages add features to the Hub, the editor and your games, and they're plain Python. Open **Packages** in the Hub to install the included ones, **Create package** to start your own (a working example is generated), or **Install from file** for a `.h3dpkg` someone shared.

| Included package | Runs in | Adds |
| --- | --- | --- |
| Light Theme | Hub, Editor | a light look |
| Physics Plus | Editor, Game | physics quality presets (120 Hz, more solver iterations) and gravity presets |
| Spline Roads | Editor, Game | roads drawn with control points that follow the terrain |
| Car Race Kit | Editor, Game | arcade car (use your own body and wheel models), checkpoints, race manager, and a *Race Track* template |
| MakeHuman Characters | Editor, Game | CC0 MakeHuman people with a Character Creator, Mixamo animations, path-walking NPCs, enemies, foot IK and a *Character Town* template |
| Tropical Islands | Editor, Game | one-click procedural tropical islands (9 shapes, any size) with beaches, jungle, flowers, rocks and dirt roads, slope-aware terrain materials, LOD vegetation with wind, and a *Tropical Island* template |

```python
# engine.py of a package
def register(h3d):
    @h3d.on("play_tick")
    def tick(engine, dt):
        engine.hud.text(0.02, 0.95, "Hello from my package", size=18)
    h3d.add_tool("My tool", lambda engine: engine.add_cube())
```

A game uses the packages inside its project folder (**Add to project**), so exported games carry everything they need. Full API: [docs/PACKAGES.md](docs/PACKAGES.md).

## 🚢 Exporting a game

In the Hub, press **Export** on a project (or press the **Export** button in the editor's toolbar). Choose Windows, Linux and/or macOS, a window size and, if you like, your own **game icon** (`.png`, `.jpg` or `.bmp`). It becomes the `.exe` icon, the Mac app icon, the window and taskbar icon and the Linux menu icon. You get:

```
My Game-Windows/                     My Game-Linux/
├── My Game.exe   ← double-click     ├── My Game   ← ./My Game
├── game/         your project       ├── game/
├── engine/       Heaven3D + Python  ├── engine/
└── saves/        player saves       ├── saves/
                                     ├── icon.png + install-shortcut.sh

My Game-macOS/
└── My Game.app   ← double-click (game, engine and Python are inside the app)
```

…and a `.zip` of each, ready to upload. From a terminal:

```bash
python Heaven3D.py --export-game "path/to/My Game" --targets windows,linux,macos --out builds --icon my_logo.png
```

Exporting needs a standalone edition (it carries the game players in `runtime/`); `macos` needs a macOS edition, and `windows`/`linux` from a Mac need the Export-Players add-on. A game built with the Apple Silicon edition runs on Apple Silicon Macs; one built with the Intel edition runs on every Mac (through Rosetta 2 on Apple Silicon). Mac players unlock the game once, like Heaven3D itself (the game's `README.txt` explains how).

## 📂 Portable projects and save games

- **Saving a project** stores every model, texture and sound path relative to the project (`assets/...`, `/` separators). Files imported from outside the project are copied into `assets/imported/` with their side files (`.gltf` buffers and textures, `.obj` + `.mtl`, `.blend` external images). File names are made safe for Windows and Linux, and the previous save is kept as `main.heaven3d.bak`.
- **Loading** resolves paths from older versions and other machines: either slash style, different letter case, home-relative and absolute paths, and FILE BURNER `media/` output.
- **Save games** in scripts:

```python
self.engine.save_game("slot1", {"coins": 7, "level": 2})
progress = self.engine.load_game("slot1", default={})
path = self.engine.asset_path("assets/music/theme.ogg")   # finds project files anywhere
```

Saves are written atomically (a crash can't corrupt them) with an automatic backup. They're stored in the game's `saves/` folder, so they move with the game. If that folder can't be written to, they go to `%APPDATA%\Heaven3D\<Game>` on Windows or `~/.local/share/heaven3d/<Game>` on Linux. macOS games always save to `~/Library/Application Support/Heaven3D/<Game>` (an app should never write inside itself).

## 📦 Requirements (running from source)

- Python **3.10+**
- A GPU and driver supporting **OpenGL 3.3 Core** or newer (macOS: the built-in OpenGL 4.1)

The easiest way is the installer, which creates a virtual environment and checks everything:

```bash
python install.py
```

Or install the core dependencies by hand:

```bash
pip install moderngl pygame pyglm numpy numba zstandard
```

- `zstandard` reads compressed `.blend` files saved by Blender 3.0 and newer (not needed on Python 3.14+).
- `tkinter` (included with Python on Windows and macOS; `sudo apt install python3-tk` on Linux) is needed for the Hub.

Optional:

```bash
pip install trimesh    # .obj import
pip install DracoPy    # Draco-compressed glTF / GLB
```

## 🚀 Getting Started

Clone the repository:

```bash
git clone https://github.com/lolgg313/Heaven3D.git
cd Heaven3D
```

### Heaven3D Hub (recommended)

```bash
python Heaven3D_Hub.py
```

On Windows you can also double-click `Heaven3D_Hub.pyw`.

- **Projects**: all your projects with thumbnails. Open them in the editor, press Play to run the game, add an existing project, rename or remove from the list.
- **New project**: pick a template (Empty, FPS Arena, Coin Collector, Outdoor Terrain), a name and a folder.
- **Learn**: 9 offline step-by-step tutorials, from the first launch to sharing your game. Each one can create its own practice project.
- **Templates** and **Settings**: engine file, Python interpreter, project folder, window size, dependency check.

The Hub also works from the command line:

```bash
python Heaven3D_Hub.py --list-templates
python Heaven3D_Hub.py --create MyGame --template fps_arena --location "~/Heaven3D Projects"
python Heaven3D_Hub.py --list
```

### Editor mode

```bash
python Heaven3D.py
```

### Play mode

```bash
python Heaven3D_runtime.py
```

### Standalone runtime

Runs `game.heaven3d` from the current folder with no editor UI and no gizmos:

```bash
python Heaven3D.py --runtime
```

### Command-line options

| Option | Description |
| --- | --- |
| `--runtime` | Run in standalone runtime mode (no editor UI) |
| `--project PATH` | Load a specific `.heaven3d` project file |
| `--width N` / `--height N` | Window size (default `1690` x `950`) |
| `--no-splash` | Skip the boot splash |
| `--splash` | Force the full first-run welcome splash |
| `--reset-welcome` | Show the welcome splash again on next launch |
| `--version` | Print the engine version |

## 🎮 Minimal FPS Example

```python
import glm
from Heaven3D import HeavenEngine, SceneNode, FPSController, Weapon

eng = HeavenEngine(mode="runtime")

ground = eng.add_cube(size=1.0, name="Ground")   # solid by default
ground.scale = glm.vec3(50, 1, 50)               # collider scales with the node

player = SceneNode("player", parent=eng.scene.root)
player.position = glm.vec3(0, 1, 0)
eng.attach_blueprint(player, FPSController)
eng.attach_blueprint(player, Weapon)

eng._begin_play()
eng.run()
```

Playing a sound:

```python
eng.audio.play("shoot")          # built-in synthesised sound
eng.audio.play("explosion.wav")  # your own file
```

## 📁 Project Structure

```
Heaven3D/
├── Heaven3D.py    # The engine: renderer, physics, editor, importers, runtime
├── Heaven3D_runtime.py    # The Standalone Game Out Put that only runs on main.heaven3d save file.
├── FILEBURNER.py    # Fixing file path for in game used assets and media compatible with linux and windows.  
├── Heaven3D_Hub.py    # The Hub: projects, templates and offline tutorials
├── Heaven3D_Hub.pyw   # Double-click launcher for the Hub on Windows (no console window)
├── branding/          # Heaven3D logo (PNG sizes + .ico) used by the Hub, editor, launchers and exported games
├── packages/          # Packages included with Heaven3D (light_theme, physics_plus, spline_roads, car_race_kit)
├── docs/PACKAGES.md   # How to write packages (engine + Hub API)
├── install.py         # Creates a virtual environment and installs/checks dependencies
├── templates/         # Starter projects used by the Hub (main.heaven3d + thumbnail + scripts)
├── tutorials/         # Offline tutorials shown in the Hub (Markdown)
├── tools/standalone/  # Builds the Windows/Linux standalone editions + the logo-stamped .exe launchers
├── tests/
│   ├── test_one_object.py        # Imported model = one locked object (editor checks)
│   ├── test_portable_projects.py # Portable paths, outside assets, moved projects, save games
│   ├── test_portable_editor.py   # Same, end to end in the real editor (needs a GPU)
│   ├── test_pause_menu.py        # Esc pause menu (needs a GPU)
│   ├── test_packages.py          # Package system + included packages (needs a GPU)
│   └── importers/                # 8 hard .blend / .gltf / .glb files each + test runner
├── LICENSE        # Apache License 2.0
└── README.md
```

### Running the tests

```bash
python tests/importers/run_importer_tests.py   # 31 importer files, no GPU needed
python tests/test_portable_projects.py         # portable projects + save games, no GPU needed
python tests/test_one_object.py                # editor checks, needs a GPU
python tests/test_pause_menu.py                # Esc pause menu in a running game, needs a GPU
python tests/test_packages.py                  # packages: load order, tools, spline road, race kit, needs a GPU
```

## 🛠️ Building the standalone editions

```bash
# on Linux, with curl, msitools, mingw-w64, binutils, uv and python3.12
python3.12 tools/standalone/build_standalone.py --out dist
```

This downloads the official python.org Windows Python and a conda-forge Linux Python, installs the engine's libraries into each, compiles the logo-stamped Windows launchers and writes `dist/Heaven3D-<version>-Windows.zip` and `dist/Heaven3D-<version>-Linux.zip`.

## 🤝 Contributing

Contributions are very welcome, whether that's bug reports, feature ideas, documentation, sample scenes, or code.

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Commit your changes: `git commit -m "Add my feature"`
4. Push to the branch: `git push origin feature/my-feature`
5. Open a Pull Request

When reporting a bug, please include your OS, Python version, GPU and driver version, and the console output or traceback.

## 📄 License

Heaven3D is licensed under the **Apache License, Version 2.0**. See the [LICENSE](LICENSE) file for the full text.

```
Copyright 2026 Heaven3D Contributors

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```

## 💖 Support Development

Heaven3D is free and open source, built with a lot of late nights and passion. If the engine has helped you, or you'd like to see it keep growing, please consider supporting development with a Bitcoin donation. Every contribution, big or small, helps us keep building new features, fixing bugs, and improving the engine for everyone.

**Bitcoin (BTC):**

```
bitcoin:bc1q83ks2w5t8uy9r5gg47dhdx4ugeswdlmjlp9a0m?message=GOD%21
```

Thank you for your support! 🙏

<div align="center">

**Made with ❤️ by the Heaven3D community**

⭐ If you like Heaven3D, give the repo a star!

</div>
