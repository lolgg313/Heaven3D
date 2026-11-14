# Command Line Reference

## `Heaven3D.py` — engine and editor

Installed with pip, `heaven3d` takes the same options as `python Heaven3D.py`, `heaven3d-runtime` the ones of `Heaven3D_runtime.py` and `heaven3d-hub` the ones of `Heaven3D_Hub.py`. `--classic-ui` starts the editor with the interface from before 3.1.3.

```bash
python Heaven3D.py [options]
```

| Option | Description |
| --- | --- |
| *(none)* | open the editor |
| `--project PATH` | load a specific `.heaven3d` file |
| `--runtime` | standalone runtime mode: no editor UI, no gizmos (loads `game.heaven3d` from the current folder unless `--project` is given) |
| `--width N` / `--height N` | window size (default `1690` × `950`) |
| `--no-splash` | skip the boot splash |
| `--splash` | force the full first-run welcome splash |
| `--reset-welcome` | show the welcome splash again on next launch |
| `--safe-mode` | start without loading any package |
| `--version` | print the engine version |

### Exporting

```bash
python Heaven3D.py --export-game "path/to/My Game" --targets windows,linux,macos --out builds [--name "My Game"] [--width 1600 --height 900] [--icon logo.png] [--no-zip]
```

See [[Exporting Games]].

## `Heaven3D_runtime.py` — game player

```bash
python Heaven3D_runtime.py [--project PATH] [--width 1408 --height 792] [--version]
```

The runtime used by exported games: it plays a project's `main.heaven3d` with no editor. It accepts the same `--export-game` options as the engine.

## `Heaven3D_Hub.py` — Hub

```bash
python Heaven3D_Hub.py [options]
```

| Option | Description |
| --- | --- |
| *(none)* | open the Hub window |
| `--list-templates` | list available templates |
| `--list` | list registered projects |
| `--create NAME` | create a project without opening the window |
| `--template ID` | template for `--create` (default `empty`) |
| `--location DIR` | parent folder for `--create` |
| `--open PROJECT_DIR` | open a project folder in the editor |
| `--list-packages` | list installed and included packages |
| `--install-package PATH` | install a package folder, `.zip` or `.h3dpkg` |
| `--safe-mode` | start without any packages |
| `--version` | print the Hub version |

Examples:

```bash
python Heaven3D_Hub.py --create MyGame --template fps_arena --location "~/Heaven3D Projects"
python Heaven3D_Hub.py --open "~/Heaven3D Projects/MyGame"
```

## Other scripts

| Command | |
| --- | --- |
| `python install.py` | create `.venv-heaven3d` and install / verify dependencies |
| `python FILEBURNER.py` | legacy portability tool (GUI) |
| `python Play_HQFPS_Arena.py` | shortcut that plays the HQ FPS Arena (not needed any more — use the Hub) |
| `python3.12 tools/standalone/build_standalone.py --out dist` | build the standalone editions (see [[Contributing]]) |

## Environment variables

| Variable | Effect |
| --- | --- |
| `HEAVEN3D_SAFE_MODE=1` | same as `--safe-mode` |
| `HEAVEN3D_NO_PERF=1` | switch off the 2.7.3 render optimisations |
| `HEAVEN3D_NO_WARMUP=1` | skip the background Numba warm-up |
| `HEAVEN3D_NO_PHYS_KERNELS=1` | use the Python contact and solver code instead of the compiled kernels |
| `HEAVEN3D_NO_CITY=1` | draw every painted building part by part instead of in districts |
| `HEAVEN3D_CLASSIC_UI=1` | same as `--classic-ui`: the editor interface from before 3.1.3 |
| `HEAVEN3D_PIP_LITE=1` | (while running `pip install`) leave the asset packages out |
| `HEAVEN3D_MAC_COMPAT=1` | use the macOS code paths on another OS (tests) |
