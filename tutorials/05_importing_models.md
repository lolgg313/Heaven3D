---
title: Import models (.glb .gltf .blend)
level: Beginner
minutes: 15
template: empty
summary: Bring in models from Blender and the web — they arrive as one object.
---
# Import models

Click **Import** and pick a `.glb`, `.gltf`, `.blend` or `.obj` file. The model appears at the centre of the world.

## One model = one object

However many parts and materials a model has, it is imported as **one object**:

- the Hierarchy shows a single row for it
- clicking any part selects the whole model
- moving, rotating, scaling, duplicating and deleting always act on the whole model
- the parts can't be pulled apart (that keeps models intact)

## What gets imported

**glTF / GLB** — meshes, the full node hierarchy, materials and textures (base colour, metal/rough, normal, emissive, occlusion), vertex colours, second UV sets, texture transforms and wrap modes, alpha (opaque / cut-out / blend), double-sided materials, skinned characters in their rest pose, morph targets, GPU instancing, lights, and compressed files (meshopt; Draco with `pip install DracoPy`).

**Blender .blend** — no Blender needed. Works with files from Blender 2.4x up to 5.x, including compressed files. Objects, parents, collection instances, materials (Principled BSDF and older materials), packed and external textures, UV maps, colour attributes, smooth/sharp shading, lights, and these modifiers: **Mirror, Array, Subdivision Surface, Solidify, Weld, Edge Split, Smooth by Angle**.

> WATCH OUT: Other modifiers (Boolean, Bevel, Geometry Nodes…) and curve/text objects can't be evaluated without Blender. In Blender, apply them first (Ctrl+A → Apply modifiers, or Object → Convert → Mesh) and save.

> TIP: Compressed .blend files from Blender 3.0+ need `pip install zstandard` (or Python 3.14+).

## Colliders for imported models

Select the model and use the **Physics** section of the Inspector:

- **+ Box** fits a box around the **whole** model automatically.
- **+ Mesh (exact triangles)** collides with every triangle of every part — perfect for buildings you walk into.

## Where models come from

Free sources: Kenney.nl, Quaternius, Poly Pizza, Sketchfab (download as glTF/GLB), and anything you make in Blender.
