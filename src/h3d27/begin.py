# @heaven3d-part h3d27/begin.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# #############################################################################
# [H3D27:importers BEGIN]
#  Heaven3D 2.7 — rewritten model importers + engine hooks (append-only patch
#  module, same capture-previous + wrap + reassign idiom as the other
#  [TRIPLEA]/[FINALPATCH]/[GREATUPDATE] blocks; double-install guarded).
#
#  .blend  : Blender 2.4x -> 5.x, gzip/zstd compressed, 32/64-bit pointers,
#            little AND big endian, Blender 5 "BLENDER17" headers + per-ID
#            address scopes.  Meshes from every storage generation (MFace,
#            MPoly/MLoop, generic attributes, 5.x AttributeStorage), split
#            normals (sharp edges / auto smooth / custom), n-gon ear clipping,
#            UV maps x2, colour attributes, collection instances, parenting,
#            lights, Principled/legacy materials, packed + external images,
#            and a modifier evaluator: Mirror, Array, Subdivision Surface
#            (OpenSubdiv-matching Catmull-Clark incl. limit surface),
#            Solidify, Weld, Edge Split, Smooth-by-Angle, Multires(levels),
#            Simple Deform, Displace (no texture), Smooth.  Also curves and
#            NURBS surfaces (bevel / extrude / caps / 2D fills), shape keys,
#            packed custom split normals, Mix Shader blending, collections
#            switched off, and zstd "Compress" files without extra packages.
#  .gltf / .glb : full glTF 2.0 — every accessor form (strided, sparse,
#            normalized, no-bufferView), all 7 primitive modes (lines/points
#            become thin geometry), skins in their default pose, morph
#            weights, EXT_mesh_gpu_instancing, KHR_node_visibility, multiple
#            scenes, KHR_lights_punctual, data/percent-encoded/unicode URIs,
#            EXT/KHR_meshopt_compression (pure-Python port of meshoptimizer,
#            byte-identical), KHR_draco_mesh_compression (via DracoPy if
#            installed), KHR_texture_transform, KHR_materials_unlit /
#            emissive_strength / pbrSpecularGlossiness / transmission,
#            per-texture UV set + sampler wrap modes, vertex colours.
#  ONE OBJECT: every import is a single scene object; extra material parts
#  are locked children (never listed, never selectable, collide as one).
# #############################################################################
if not globals().get("_H3D27_IMPORTERS_INSTALLED"):
    _H3D27_IMPORTERS_INSTALLED = True
    import types as _h3d27_types

    def _h3d27_build_importers():
        """Importer modules, scoped inside one factory so their helper
        names never collide with the engine's globals."""