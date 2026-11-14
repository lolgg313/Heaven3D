# Packages

Packages add features to **the Hub**, **the editor** and **your games**. They are plain Python folders, so anyone can write one. Examples: a light theme, a spline-road tool, physics presets, a complete racing kit, realistic people, procedural islands, an HQ weapon arsenal.

> ⚠️ Packages run Python code on your computer. Only install packages from people you trust.

## Included packages

| Package | Version | Runs in | Adds | Page |
| --- | --- | --- | --- | --- |
| **Light Theme** | 1.0.0 | Hub, Editor | a light look | [[Physics Plus and Light Theme]] |
| **Physics Plus** | 1.0.0 | Editor, Game | physics quality presets (120 Hz, more solver iterations) and gravity presets | [[Physics Plus and Light Theme]] |
| **Spline Roads** | 1.0.0 | Editor, Game | roads drawn with control points that follow the terrain | [[Car Race Kit and Spline Roads]] |
| **Car Race Kit** | 1.1.0 | Editor, Game | arcade car (your own body and wheel models), checkpoints, race manager, *Race Track* template | [[Car Race Kit and Spline Roads]] |
| **MakeHuman Characters** | 1.2.2 | Editor, Game | CC0 MakeHuman people, Character Creator, mocap animations, Mixamo retargeting, NPCs, enemies, foot IK, *Character Town* template | [[MakeHuman Characters]] |
| **Tropical Islands** | 1.1.1 | Editor, Game | one-click procedural tropical islands with LOD vegetation, *Tropical Island* template | [[Tropical Islands]] |
| **HQ FPS Weapons** | 0.4.0 | Editor, Game | 20 GPU-skinned first-person weapons with animations, sounds and stats, *HQ FPS Arena* template | [[HQ FPS Weapons]] |

**Asset licences.** Package code is Apache 2.0 like the engine. Assets: MakeHuman Characters and Tropical Islands are **CC0**; HQ FPS Weapons assets are by lolgg313, **free in Heaven3D games only, no redistribution of the raw files** ([licence](https://github.com/lolgg313/Heaven3D/blob/main/packages/hqfps_weapons/ASSETS_LICENSE.md)).

## Installing and using packages

1. In the Hub, open **Packages**.
2. Under **Included with Heaven3D**, press **Install**. Required packages come along automatically (installing *Car Race Kit* also installs *Spline Roads*).
3. Switch a package **On** or **Off** on its card. Hub packages apply after **Restart now**; editor and game packages load the next time the editor or game starts.
4. To use a package in a game, press **Add to project** on its card (or **⋯ > Packages…** on a project).
5. Open the project. Package tools appear in the **Packages** menu in the menu bar; package blueprints show their settings in the Inspector.

**Install from file** installs a `.h3dpkg` someone shared.

## Where packages live

| Location | Used by |
| --- | --- |
| `~/.heaven3d/packages/<id>` — your **library** | the Hub (`hub` target) and the editor (`editor`/`game` targets) when the package is **On** |
| `<project>/packages/<id>` — **project packages** | that project in the editor **and every game exported from it** |
| `<Heaven3D>/packages/<id>` | packages included with Heaven3D, offered on the Packages page |

- **Games only use project packages.** That keeps exported games self-contained.
- A project's copy wins over the library copy, so a project keeps the version it was built with — except that when Heaven3D ships a **newer** version of an included package, older copies (library and projects) are updated automatically when the Hub or editor opens, keeping your package settings.
- Templates can list the packages they need; those are added to new projects automatically.

## Safe mode

If a package breaks the Hub or editor, start without packages:

```bash
python Heaven3D_Hub.py --safe-mode
python Heaven3D.py --safe-mode
# or
HEAVEN3D_SAFE_MODE=1 python Heaven3D_Hub.py
```

Then switch the package off. Errors in packages never stop the engine anyway: they're logged in the editor console and on the package card, and a per-frame handler that keeps failing is switched off.

## Command line

```bash
python Heaven3D_Hub.py --list-packages
python Heaven3D_Hub.py --install-package my_package.h3dpkg   # also a folder or .zip
```

## Writing your own

See [[Writing Packages]]. The included packages' source in `packages/` is a good place to learn from — *Physics Plus* shows the whole API in about 40 lines.
