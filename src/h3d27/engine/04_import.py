# @heaven3d-part h3d27/engine/04_import.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# ---------------------------------------------------------------
# 4. Import entry points
# ---------------------------------------------------------------
def _h3d27_load_parts(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".blend":
        return H3DImporters.load_blend(path, _H3D27Log)
    if ext == ".fbx":
        return load_fbx_parts(path, _H3D27Log)
    if ext == ".x":
        return load_x_parts(path, _H3D27Log)
    return H3DImporters.load_gltf(path, _H3D27Log)

def _h3d27_nodes_from_parts(parts):
    roots = []
    for p in parts:
        md = GlbImporter.MeshData(name=p["name"], positions=p["positions"], normals=p["normals"],
                                  uvs=p.get("uv0"), tangents=p.get("tangents"),
                                  indices=p["indices"], material_info=p["material_info"])
        md.uv1 = p.get("uv1")
        md.colors = p.get("colors")
        roots.append(GlbImporter.NodeData(name=p["name"], matrix=None, translation=None,
                                          rotation=None, scale=None, meshes=[md], children=[]))
    return roots

def _h3d27_glb_build_nodes(cls, path):
    parts, _lights = H3DImporters.load_gltf(os.fspath(path), _H3D27Log)
    return _h3d27_nodes_from_parts(parts)

def _h3d27_blend_build_nodes(cls, path):
    parts, lights = H3DImporters.load_blend(os.fspath(path), _H3D27Log)
    cls._pending_lights = list(lights)
    return _h3d27_nodes_from_parts(parts)

GlbImporter.build_nodes = classmethod(_h3d27_glb_build_nodes)
BlendImporter.build_nodes = classmethod(_h3d27_blend_build_nodes)

_H3D27_PREV_LOAD_INTO = HeavenEngine.load_model_into

def _h3d27_load_model_into(self, node, path):
    """.blend / .gltf / .glb through the Heaven3D 2.7 importers.  The
    model is ONE object: part 0 lives on `node`, every other material
    part is a locked child (hidden in the Hierarchy, never selectable,
    moved/deleted/duplicated/saved together with the root)."""
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".blend", ".gltf", ".glb", ".fbx", ".x"):
        return _H3D27_PREV_LOAD_INTO(self, node, path)
    try:
        parts, lights = _h3d27_load_parts(path)
    except Exception as ex:
        Log.error(f"Model load failed '{os.path.basename(path)}': {ex}")
        return False
    if not parts:
        Log.warn(f"'{os.path.basename(path)}' contains no visible geometry.")
        return False
    for c in list(node.children):
        if getattr(c, "_heaven3d_imported_part", False):
            c.remove()
    try:
        for i, part in enumerate(parts):
            mesh = _h3d27_mesh_from_part(self.ctx, part)
            mat = _h3d27_material_from_info(part["material_info"])
            mesh._h3d_cull = not bool(mat.double_sided)
            if i == 0:
                node.mesh = mesh
                node.material = mat
            else:
                child = SceneNode(f"{node.name}_part{i}", parent=node)
                child._heaven3d_imported_part = ext
                child._h3d_locked = True
                child.mesh = mesh
                child.material = mat
    except Exception as ex:
        Log.error(f"Model upload failed '{os.path.basename(path)}': {ex}")
        traceback.print_exc()
        return False
    node.source_path = path
    node._h3d_collision_proxy = None
    lights = [l for l in lights if not (ext != ".blend" and l.get("kind") == "sun")]
    if lights:
        try:
            BlendImporter._pending_lights = list(lights)
            BlendImporter.attach_imported_lights(self, node)
            for c in node.children:
                if getattr(c, "_heaven3d_imported_part", False):
                    c._heaven3d_imported_part = ext
                    c._h3d_locked = True
        except Exception as ex:
            Log.warn(f"Imported lights skipped: {ex}")
    tris = sum(len(p["indices"]) // 3 for p in parts)
    Log.info(f"Imported '{os.path.basename(path)}' as one object: {len(parts)} material "
             f"part(s), {tris:,} triangles, {len(lights)} light(s)")
    return True

HeavenEngine.load_model_into = _h3d27_load_model_into
