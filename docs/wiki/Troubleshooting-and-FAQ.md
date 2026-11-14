# Troubleshooting and FAQ

## Start-up problems

**The window doesn't open / "OpenGL 3.3 not supported".**
Heaven3D needs OpenGL **3.3 Core** or newer. Update your GPU driver. On Linux, make sure Mesa is installed (`python install.py` does this and checks for a working 3.3 context). Virtual machines and remote desktops often expose only old OpenGL versions.

**`ModuleNotFoundError: moderngl` / `glm` / `pygame`.**
Install the dependencies: `python install.py` (recommended) or `pip install moderngl pygame pyglm numpy numba zstandard`. Note that the PyPI package is **`pyglm`**, imported as `glm`.

**The Hub won't start: `No module named tkinter`.**
On Debian/Ubuntu: `sudo apt install python3-tk`. Fedora: `sudo dnf install python3-tkinter`. Windows and macOS Python include it.

**`externally-managed-environment` when running pip.**
Your distro protects the system Python (PEP 668). Use `python install.py`, which creates `.venv-heaven3d`, or make your own virtual environment.

**macOS says the app "is damaged" or "can't be opened".**
It isn't from the App Store. Right-click **Fix macOS security warning.command** → **Open**, or run `xattr -dr com.apple.quarantine` on the Heaven3D folder. Players of your exported Mac game do the same with **right-click → Open → Open**.

**The Hub or editor crashes after installing a package.**
Start in safe mode — `python Heaven3D_Hub.py --safe-mode` (or `HEAVEN3D_SAFE_MODE=1`) — and switch the package off on the Packages page.

## Editor

**I'm lost in the scene.** Select something in the Outliner and press **F**.

**Keys don't do anything.** While playing, the editor is locked; only **Ctrl+P** (stop) works. When not playing, camera fly keys need the **right mouse button** held.

**My changes disappeared after Stop.** That's by design: Play is a test run and everything returns to where it was. Edit while stopped, then **Save** (there's no auto-save).

**I lost my last save.** The previous version is kept as `main.heaven3d.bak` next to `main.heaven3d`.

**I can't select one part of an imported model.** Imported models are one object on purpose. Split the model in Blender if you need separate parts.

**I can't add a second Player Spawn.** A scene can have only one.

## Models

**A `.blend` file imports without some geometry.** Boolean, Bevel, Geometry Nodes and other unsupported modifiers, and curve/text objects, need Blender to evaluate. Apply them (Ctrl+A → Apply / Object → Convert → Mesh) and save.

**`zstandard` error on a `.blend`.** Blender 3.0+ compresses with zstd: `pip install zstandard` (not needed on Python 3.14+).

**Draco-compressed glTF doesn't load.** `pip install DracoPy`.

**`.obj` doesn't load.** `pip install trimesh`.

**Textures are missing after moving the project.** Save the project once in 2.7+ so outside files are copied into `assets/imported/`. For very old projects run `FILEBURNER.py`.

## Physics

**The player falls through the floor.** The floor needs a collider (Cubes, Planes and Spheres get one automatically; imported models don't). Add **+ Box** or **+ Mesh** in the Inspector.

**A moving object with a Mesh collider behaves like a ball.** Mesh colliders are for static/kinematic objects; a dynamic body falls back to a bounding sphere. Use Box/Sphere/Capsule.

**Fast objects pass through thin walls.** Make walls thicker, or use *Physics Plus* → **Physics quality: High/Ultra** (120 Hz).

**Something stutters the first time an enemy sees me.** Install `numba`; its kernels compile in the background during loading.

## Scripting

**"Blueprint 'X' not registered; not restored."** Put `@blueprint` above the class and make sure the file is in the project's `scripts/` folder (and doesn't start with `_`).

**My script's error stops nothing but the behaviour doesn't work.** Errors are logged to the console with a traceback; run the editor from a terminal, or check `<project>/.heaven3d_logs/` when started from the Hub.

**HUD text flickers or vanishes.** HUD calls last one frame — call them every frame in `on_tick`. In a package's `render` handler, call `engine.hud.render()` at the end.

## Exporting

**The Export button says the players are missing.** Exporting needs a standalone edition (which carries `runtime/players`). On macOS, add the *Export-Players* download to export Windows/Linux games.

**The exported game is missing a package feature.** Games only use **project** packages. Press **Add to project** on the package, then export again.

**Where are my players' saves?** In the game's `saves/` folder; on macOS in `~/Library/Application Support/Heaven3D/<Game>`. See [[Portable Projects and Save Games]].

## FAQ

**Is Heaven3D really pure Python?** The engine code is Python; rendering goes through OpenGL (ModernGL), window/input/audio through pygame, and hot loops can be JIT-compiled by Numba. There's no other engine underneath.

**Can I sell games made with Heaven3D?** The engine is Apache 2.0. The MakeHuman Characters and Tropical Islands assets are CC0. The HQ FPS Weapons assets were made by lolgg313 and are free to use in games made with Heaven3D (personal or commercial), but the raw files may not be redistributed or used outside Heaven3D — see their [assets licence](https://github.com/lolgg313/Heaven3D/blob/main/packages/hqfps_weapons/ASSETS_LICENSE.md).

**Does it support multiplayer / VR / mobile?** Not built in. Windows, Linux and macOS desktop are the supported targets.

**Can I use C#/Lua/visual scripting?** Scripting is Python. Many games need no code at all thanks to the [[Built-in Blueprints]].

**How do I report a bug?** Open an issue with your OS, Python version, GPU and driver version, and the console output or traceback. See [[Contributing]].
