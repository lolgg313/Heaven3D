<div align="center">

# ☁️ Heaven3D Wiki

**A complete 3D game engine built from the metal up — in Python.**

Deferred PBR rendering · CPU physics · Built-in editor · Hub launcher · Packages · One-click export for Windows, Linux and macOS

![Version](https://img.shields.io/badge/version-3.1.4-blue)
![Python](https://img.shields.io/badge/python-3.10%2B-yellow)
![OpenGL](https://img.shields.io/badge/OpenGL-3.3%2B%20Core-green)
![License](https://img.shields.io/badge/license-Apache%202.0-orange)

</div>

---

## What is Heaven3D?

Heaven3D is a from-scratch 3D game engine written in Python on top of [ModernGL](https://github.com/moderngl/moderngl), which gives it direct access to the OpenGL Core profile. There is no Panda3D, Unity, Unreal or GameGuru underneath: the renderer, physics, editor, model importers, audio and gameplay systems are all written specifically for Heaven3D.

The whole engine is a single file, `Heaven3D.py`, so it is easy to read, hack on and bundle into your own projects. Around it sit:

| Part | File | What it does |
| --- | --- | --- |
| **Engine** | `Heaven3D.py` | Renderer, physics, importers, blueprints, runtime: one file, built from the modular source in `src/` by `build.py` |
| **Editor interface** | `h3d_editor/` + `vitrail/` | The editor's windows (since 3.1.3), loaded in editor mode only |
| **Runtime** | `Heaven3D_runtime.py` | The same engine, started in play mode (built from the same source) |
| **Hub** | `Heaven3D_Hub.py` / `.pyw` | Launcher like Unity Hub: projects, templates, tutorials, packages, export |
| **FILE BURNER** | `FILEBURNER.py` | Legacy tool that makes old projects portable (now built into saving) |
| **Installer** | `install.py` | Creates a virtual environment and installs/checks dependencies (Linux, Windows, macOS) |
| **pip package** | `pyproject.toml`, `heaven3d/` | `pip install git+https://github.com/lolgg313/Heaven3D` gives the commands `heaven3d` and `heaven3d-hub` |

## Start here

| I want to… | Read |
| --- | --- |
| Install and run Heaven3D | [[Installation]] → [[Getting Started]] |
| Learn the editor | [[Editor Guide]] |
| Build a level without code | [[Built-in Blueprints]] · [[Templates]] |
| Write game logic in Python | [[Scripting Guide]] → [[Scripting API Reference]] |
| Bring in models from Blender or the web | [[Importing Models]] |
| Make things solid, bouncy or physical | [[Physics]] |
| Sculpt terrain, generate buildings, add effects | [[World Tools]] |
| Add features with packages, or write one | [[Packages]] → [[Writing Packages]] |
| Ship my game to players | [[Exporting Games]] · [[Portable Projects and Save Games]] |
| Fix a problem | [[Troubleshooting and FAQ]] |

## Feature overview

### Rendering — see [[Rendering]]
- Deferred renderer with a G-buffer (positions, normals, albedo, material, full-colour emissive)
- HDR float framebuffers with ACES filmic tone mapping
- PBR materials (Cook-Torrance GGX, metallic-roughness) with editable emission
- Forward transparency pass, SSAO, screen-space reflections, bloom, PCF soft shadows
- FXAA 3.11 stacked with 4× hardware MSAA
- Procedural day/night sky, animated clouds and a Gerstner-wave ocean in every scene

### Physics — see [[Physics]]
- Box, sphere, capsule, plane, heightfield and exact triangle-mesh colliders
- Sequential-impulse rigid bodies with friction and restitution, sweep-and-prune broadphase, persistent manifolds and simulation islands
- Raycasts, overlap queries, triggers, collision events and a kinematic character controller
- Pure CPU; with Numba the pair search, box contacts and the contact solver are compiled (same results as the Python fallback); bodies at rest sleep and cost nothing

### Editor — see [[Editor Guide]]
- Menu bar, toolbar, Outliner, live Inspector, undo / redo; transform / light / spawn / view gizmos and a visual collider editor
- Script editor with hot reload, Python console, visual scripts, keyframe animation, node materials, prefabs, asset browser
- Lit, Lit + Wireframe and Wireframe (x-ray) views
- Terrain sculpting and painting, the Building Maker (painted buildings, generated houses, villages and whole towns drawn in districts with levels of detail), a Particle Studio

### Gameplay — see [[Built-in Blueprints]] and [[Scripting Guide]]
- Blueprints: Python behaviours you attach to any object
- Ready-made: `FPSController`, `Weapon`, `Projectile`, `Health`, `EnemyAI`, `EnemyFlyShooter`, `Pickup`, `Spawner`, `MovingPlatform`, `TriggerZone`, `LevelEndTrigger`
- 13 synthesised sounds, your own `.wav` / `.ogg`, 3D positional audio, music
- HUD text, bars, crosshair and damage vignette; save games

### Asset import — see [[Importing Models]]
- Full glTF 2.0 / GLB, Blender `.blend` read directly (2.5x to 5.0, no Blender needed), OBJ + MTL, FBX, DirectX `.x`
- One model = one object in the Outliner

### Packages — see [[Packages]]
Light Theme · Physics Plus · Spline Roads · Car Race Kit · [[MakeHuman Characters]] · [[Tropical Islands]] · [[HQ FPS Weapons]]

## Current version

**Engine 3.1.4**, plus the changes on `main` since then (towns, view-mode buttons, faster physics, pip install). See the [[Changelog]].

## License

Heaven3D is licensed under the **Apache License 2.0**. Assets inside packages have their own licences (most are CC0); see each package page.
