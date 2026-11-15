# FarScream

Open-island shooter map generator for Heaven3D. Guide: [docs/FARSCREAM.md](../../docs/FARSCREAM.md).

| File | |
| --- | --- |
| `engine.py` | the package: model library (shared meshes, LODs, texture streaming, collision copies), the map (built from a seed on a Tropical Island), the game (characters, loot, crates, fuel drums, trucks, objectives, HUD) |
| `fs_layout.py` | where everything goes: sites, props, characters, loot (pure numpy, deterministic) |
| `templates/farscream/` | the *FarScream* project template |
| `assets/models/` | the 19 optimized models, their lighter levels of detail and `manifest.json` |
| `tools/optimize_models.py` | optimizes the source models without visible change and makes the levels of detail |
| `tools/strip_lod_textures.py` | copies the result into `assets/models` (levels of detail without texture copies, small levels left out) |
| `tools/texel_density.py` | reports (or applies) texture sizes larger than any screen can show |

Rebuilding the models from the originals:

    python tools/optimize_models.py <folder with the original .glb files> /tmp/fs_opt
    python tools/strip_lod_textures.py /tmp/fs_opt assets/models
