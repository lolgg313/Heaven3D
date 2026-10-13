<div align="center">

# ☁️ Heaven3D

<img width="1690" height="979" alt="gameeglory" src="https://github.com/user-attachments/assets/f4865664-c6f7-41f7-b977-88398f1833ea" />

**A complete 3D game engine built from the metal up — in Python.**

Deferred PBR rendering · CPU physics · Built-in editor · Standalone runtime

![Version](https://img.shields.io/badge/version-2.6.0-blue)
![Python](https://img.shields.io/badge/python-3.10%2B-yellow)
![OpenGL](https://img.shields.io/badge/OpenGL-4.6%20Core-green)
![License](https://img.shields.io/badge/license-Apache%202.0-orange)

</div>

---

## About

Heaven3D is a from-scratch 3D game engine written in Python on top of [ModernGL](https://github.com/moderngl/moderngl), giving direct access to the OpenGL Core profile. No Panda3D, Unity, Unreal, or GameGuru under the hood: the renderer, physics, editor, importers, audio, and gameplay systems are all written specifically for Heaven3D.

The whole engine ships as a single file, `Heaven3D.py`, so it is easy to read, hack on, and bundle into your own projects.

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
- Procedural terrain generation
- **Building editor**: generate, paint, or manually place modular building sectors on a 2 m grid with a snapped ghost preview, across 5 biomes
- Save and load projects as JSON (`.heaven3d`)

### Gameplay
- **Blueprint system**: attach reusable behaviours to any scene node
- Ready-made blueprints you can attach and play: `FPSController`, `Weapon`, `Projectile`, `Health`, `EnemyAI`, `EnemyFlyShooter`, `Pickup`, `Spawner`, `MovingPlatform`, `TriggerZone`, `LevelEndTrigger`
- **Audio**: 13 built-in synthesised sounds, your own `.wav`/`.ogg` files, 3D positional audio, and music; silent-safe on machines with no audio device
- **HUD**: crosshair, bars, text, and vignette for in-game UI

### Asset import
- **glTF / GLB** (via `trimesh`)
- **OBJ + MTL** with texture resolution
- **Blender `.blend`** files read directly by parsing the Blender file format (no Blender install required)

### Performance
- Optional **Numba** acceleration for hot paths, with automatic, bit-identical fallback to plain Python if Numba is unavailable

## 📦 Requirements

- Python **3.10+**
- A GPU and driver supporting **OpenGL 4.6 Core**

Install the core dependencies:

```bash
pip install moderngl pygame pyglm numpy numba
```

Optional, for GLB/glTF import:

```bash
pip install trimesh
```

## 🚀 Getting Started

Clone the repository:

```bash
git clone https://github.com/lolgg313/Heaven3D.git
cd Heaven3D
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
├── LICENSE        # Apache License 2.0
└── README.md
```

## 🛠️ Building an Executable

You can package a game into a single executable with [PyInstaller](https://pyinstaller.org/):

```bash
pip install pyinstaller
pyinstaller --onefile Heaven3D.py
```

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
bc1qxsjfny55sfy3qujl8ejafskfhn47vanuc7rvdd
```

Thank you for your support! 🙏

<div align="center">

**Made with ❤️ by the Heaven3D community**

⭐ If you like Heaven3D, give the repo a star!

</div>
