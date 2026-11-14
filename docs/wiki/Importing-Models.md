# Importing Models

Click **Import** in the toolbar and pick a `.glb`, `.gltf`, `.blend` or `.obj` file. The model appears at the centre of the world. All importers are written for Heaven3D — no Blender, Assimp or external converter needed.

## One model = one object

However many parts and materials a model has, it is imported as **one object**:

- the Outliner shows a single row for it
- clicking any part selects the whole model
- moving, rotating, scaling, duplicating and deleting always act on the whole model
- children and material parts are locked to it, so they can't be pulled apart
- colliders cover the whole model

## Supported formats

### glTF 2.0 / GLB

Full glTF 2.0:

- every accessor type (sparse, strided, normalized) and all 7 primitive modes
- the full node hierarchy, skins (bind pose), morph targets
- PBR materials: base colour, metal/rough, normal, emissive (with `KHR_materials_emissive_strength`), occlusion
- `KHR_texture_transform`, texture wrap modes, vertex colours, a second UV set
- alpha modes (opaque / cut-out / blend), double-sided materials
- `KHR_materials_unlit`, spec-gloss, transmission
- `KHR_lights_punctual`
- `EXT_mesh_gpu_instancing`
- `KHR_meshopt_compression`, and `KHR_draco_mesh_compression` with `pip install DracoPy`

### Blender `.blend` (read directly)

No Blender install needed. Works with:

- files from Blender **2.5x up to 5.0**, including gzip- and zstd-compressed files (zstd needs `pip install zstandard` on Python < 3.14), 32-bit and big-endian files
- objects, parents, linked duplicates and collection instances
- materials (Principled BSDF and older materials), packed and external textures
- 2 UV maps, colour attributes, custom and sharp normals, smooth/sharp shading
- lights
- modifiers: **Mirror, Array, Subdivision Surface, Solidify, Weld, Edge Split, Triangulate, Smooth by Angle** and **Multires** (levels)

> ⚠️ Other modifiers (Boolean, Bevel, Geometry Nodes…) and curve / text objects can't be evaluated without Blender. Apply them first in Blender (Ctrl+A → Apply, or Object → Convert → Mesh) and save.

### OBJ + MTL

Wavefront OBJ with its MTL materials and texture resolution. Needs `pip install trimesh`.

## Colliders for imported models

Select the model, then in the Inspector's **Physics** section:

- **+ Box** fits a box around the **whole** model.
- **+ Mesh (exact triangles)** collides with every triangle of every part — ideal for buildings you walk into.

See [[Physics]].

## Where imported files go

When you save, any model imported from **outside** the project folder is copied into `assets/imported/` together with its side files (`.gltf` buffers and textures, `.obj` + `.mtl`, `.blend` external images). Paths are stored relative to the project. See [[Portable Projects and Save Games]].

> 💡 Put models in the project's `assets/` folder **before** importing them and nothing needs copying.

## From code

```python
node = engine.load_model("assets/models/tree.glb")          # new node at the origin
engine.load_model_into(existing_node, "assets/models/crate.blend")
```

## Free model sources

[Kenney](https://kenney.nl), [Quaternius](https://quaternius.com), [Poly Pizza](https://poly.pizza), [Poly Haven](https://polyhaven.com), Sketchfab (download as glTF / GLB), and anything you make in Blender.

## Importer test suite

The importers are tested against Blender and the Khronos sample models with 8 hard test files per format (31 / 31 pass). Run them without a GPU:

```bash
python tests/importers/run_importer_tests.py
```

See [[Testing]].
