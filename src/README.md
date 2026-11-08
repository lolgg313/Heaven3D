# Heaven3D engine source

`Heaven3D.py` (the single file players run and games ship with) is **built**
from the files in this folder. Work here, then build:

```bash
python build.py              # writes Heaven3D.py and Heaven3D_runtime.py
python build.py --check      # fails if they are out of date (use it before committing)
python build.py --where 25120   # a traceback says Heaven3D.py:25120? this finds the file here
```

`Heaven3D_runtime.py` is the same engine, started in play mode
(`python Heaven3D_runtime.py --project game.heaven3d`). Don't edit either
built file by hand: the next build overwrites it.

## How the engine is organised

The engine is one **linear** program: the core sections come first, and
each later block extends or patches what came before it (it captures the
previous function, wraps it, and puts the wrapper back). So the order of
the parts matters, and `BUILD_ORDER` lists it. A part can use every name
defined in the parts above it. That is also why a part on its own isn't
importable, and why your editor may underline names that come from
earlier parts.

| Folder | What's in it |
|---|---|
| `core/` | file header, imports and the logger, the scene graph, the camera, project save/load |
| `importers/` | the classic GLB, OBJ and `.blend` importers |
| `rendering/` | meshes, materials, lights, every GLSL shader, the deferred renderer, instancing, water and reflections |
| `physics/` | the CPU physics engine, its later overhaul (islands, sweep-and-prune), the Numba kernels |
| `audio/` | the audio system and the sound points |
| `gameplay/` | blueprints, the HUD, the FPS kit, weapon FX, particles and flying AI, the pause menu |
| `world/` | terrain, the terrain editor, the procedural building system |
| `editor/` | gizmos, the editor grid, the collider editor, the view gizmo, the classic UI renderer, selection outlines |
| `engine/` | `HeavenEngine` (window, main loop, editor and runtime modes), `main()`, the `__main__` guard |
| `branding/` | the embedded logo and the boot splash |
| `patches/` | quality fixes and optimisations that touch several areas |
| `h3d27/` | the 2.7 importers and engine features: `modules/` are the glTF, `.blend`, meshopt and portable-file-system modules (whole Python modules, stored without the indentation they get inside the engine's importer factory); `engine/` are the numbered engine hooks (shaders, mesh, materials, import, colliders, projects, portable files, packages, view menu, macOS) |

Each file starts with a `# @heaven3d-part` label line; the build drops it.
`BUILD_ORDER` entries marked `indent=N` are stored dedented and indented
back by N spaces when building.

## Adding a part

1. Create the file here, starting with a label line
   (`# @heaven3d-part <path>  -- ...`).
2. Add it to `BUILD_ORDER` after every part it depends on.
3. `python build.py`, then run the tests.

## Tests

`python tests/test_build.py` checks that the built files are up to date,
that the build is lossless, and that both files compile.
