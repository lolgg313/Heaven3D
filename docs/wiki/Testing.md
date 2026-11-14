# Testing

Heaven3D's tests are plain Python scripts — no test framework needed. Some need a GPU (they open a real engine window; Mesa's llvmpipe software renderer works too); others run anywhere.

## Engine tests (`tests/`)

The folder holds more test files than this table (prefabs, visual scripts, node materials, drag and drop, FBX / .x import, exporting...): every `tests/test_*.py` starts with a description of what it checks and whether it needs a GPU.

| Command | GPU | Checks |
| --- | --- | --- |
| `python tests/importers/run_importer_tests.py` | no | 31 `.blend` / `.gltf` / `.glb` files against expected results |
| `python tests/test_build.py` | no | `Heaven3D.py` is up to date with `src/`, the build is lossless, both files compile |
| `python tests/test_pip_package.py` | no | the pip package: version, lazy import, commands, files |
| `python tests/test_vitrail_editor.py` | yes | the editor interface: menus, outliner, inspector, undo, console, script editor |
| `python tests/test_building_maker.py` | yes | painting buildings with the mouse, collision, save / load, speed |
| `python tests/test_building_city.py` | yes | towns in districts: same picture as part-by-part drawing, levels of detail, streaming, view-mode buttons |
| `python tests/test_physics_fast_paths.py` | yes | compiled pair search and the event shortcut against the old code, step by step |
| `python tests/test_physics_kernels.py` | yes | compiled contacts and contact solver: bit-identical to the Python code |
| `python tests/test_portable_projects.py` | no | portable paths, outside assets, moved projects, save games |
| `python tests/test_one_object.py` | yes | an imported model is one locked object in the editor |
| `python tests/test_portable_editor.py` | yes | portability end to end in the real editor |
| `python tests/test_pause_menu.py` | yes | the Esc pause menu in a running game |
| `python tests/test_packages.py` | yes | package load order, tools, spline road, race kit |
| `python tests/test_mac_mode.py` | yes | macOS code paths forced on with `HEAVEN3D_MAC_COMPAT=1`: GL 4.1 context, every shader compiles, the classic Library dropdown, dialogs |
| `python tests/test_car_models.py` | yes | custom car body / wheel models: fitting, save → move → load, spinning and steering wheels |
| `python tests/test_characters.py` | yes | MakeHuman characters: generation, Creator panel, GPU skinning, FBX/BVH retargeting, NPCs, enemies, foot IK, save/reload, runtime |
| `python tests/test_islands.py` | yes | Tropical Islands: every topology, LOD vegetation, live rebuild, walking in Play, cache reload, runtime |
| `python tests/test_hqfps_weapons.py` | yes | 56 checks on the HQ FPS viewmodels, audio and pickups |
| `python tests/test_hqfps_with_characters.py` | yes | 14 checks: both skinning packages in one scene |
| `python tests/test_hqfps_arms.py` | no | every clip of all 20 weapons: no hollow arms |
| `python tests/test_hqfps_arena_fixes.py` | yes | 14 checks: zombie reach, hit/death, bullet holes, mouse-look, near plane |
| `python tests/test_hqfps_old_arena.py <old main.heaven3d>` | yes | an older arena project gets its weapon and ammo pickups when opened |

## Package tests

```bash
python packages/hqfps_weapons/tests/test_skin.py      # 62 checks, no GPU: glTF skinning runtime, UV convention, clip sync
python packages/mh_characters/tools/anim_qa.py        # contact sheets + hand/body penetration, joint angles, foot slip
```

## Importer test assets

`tests/importers/assets/` holds 8 hard files per format:

- **`.blend`**: hierarchy + multi-material, modifiers, packed textures, colour attributes + 2 UVs, a big zstd-compressed file, instances + lights, a gzip Suzanne with subsurf (Blender 2.52), and a 32-bit big-endian file (2.60)
- **glTF / GLB**: Khronos samples such as CesiumMan, MeshPrimitiveModes, MultiUVTest, NegativeScaleTest, SimpleSparseAccessor, TextureTransformMultiTest, a Unicode file name, a file name with spaces, and meshopt compression

`expected.json` holds the ground truth; `tools/truth_gltf.py` and `tools/gen_blend.py` regenerate it. Licences for every sample are in `tests/importers/assets/licenses/`.

## Writing a test

Follow the existing style: build an engine, drive it frame by frame, **wait on state rather than frame counts**, and assert on what the player would see. For example, the HQ FPS tests check that the hands are *in front of the eye* — a viewmodel behind the camera renders nothing while every other assertion still passes.

```python
from Heaven3D import HeavenEngine
eng = HeavenEngine(mode="runtime", project_path="templates/fps_arena/main.heaven3d")
# ... step frames, inspect eng.scene / eng.physics, take screenshots ...
```

Before sending a pull request, run at least the no-GPU suites and the suites for the area you touched.
