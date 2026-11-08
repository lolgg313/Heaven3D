# @heaven3d-part core/project_io.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# #############################################################################
# SECTION 8  —  PROJECT SAVE / LOAD  (JSON, .heaven3d)
# #############################################################################

# ---- Portable-path helpers ----------------------------------------------
# Asset references inside a project file must be PORTABLE — i.e. they must
# survive the project being shared to another machine or another user.
# We achieve this by stripping the current user's home-directory prefix
# off every absolute asset path before writing it to JSON:
#
#     C:\Users\Hamid IS Legend\Downloads\foo.glb   →   Downloads\foo.glb
#     C:\Users\Bob\Pictures\bar.glb                →   Pictures\bar.glb
#
# On load we re-prefix the path with whatever user home the engine is
# currently running under, so the project resolves correctly on any
# machine / any Windows user account.


def _to_portable_path(path: Optional[str], project_dir: str = "") -> Optional[str]:
    """Strip the current user's home-directory prefix from an absolute asset
    path so it can be stored portably in the project JSON.

    Examples (Windows):
        C:\\Users\\<me>\\Downloads\\foo.glb   →  Downloads\\foo.glb
        C:\\Users\\<me>\\Desktop\\x\\y.glb    →  Desktop\\x\\y.glb
    The path is normalised to use the OS separator and trimmed of any
    leading separator so it reads as a clean relative path.  If the path
    is already relative, or doesn't live under the user home, it is
    returned unchanged (after a project-relative fallback attempt)."""
    if not path:
        return None
    try:
        from os.path import abspath, normpath, expanduser, relpath, basename
        abs_path = normpath(abspath(path))
        home = normpath(expanduser("~"))
        # Append a separator so we only match whole directory components
        # (avoids e.g. "C:\\Users\\Bob2" being treated as inside "C:\\Users\\Bob").
        if abs_path.lower().startswith(home.lower() + os.sep) or \
           abs_path.lower() == home.lower():
            rel = abs_path[len(home):].lstrip(os.sep)
            return rel if rel else basename(abs_path)
        # Not under the user home: try relative to the project dir, otherwise
        # keep the absolute path (still works on this machine, and is better
        # than dropping the reference entirely).
        try:
            return relpath(abs_path, abspath(project_dir)) if project_dir else abs_path
        except ValueError:
            return abs_path
    except Exception:
        return path


def _from_portable_path(path: Optional[str], project_dir: str = "") -> Optional[str]:
    """Resolve a portable stored path back into an absolute filesystem path
    at load time.  Strategy (in order):
        1. If it's already absolute and exists, use it as-is (legacy projects).
        2. Prepend the current user's home dir (the inverse of save).
        3. Resolve relative to the project file's directory.
        4. Fall back to step 2's path even if missing (so the engine can log
           a clear 'asset not found' message instead of silently dropping)."""
    if not path:
        return None
    from os.path import isabs, join, abspath, exists, expanduser, normpath
    # 1. Already absolute
    if isabs(path):
        ap = normpath(path)
        return ap
    home = normpath(expanduser("~"))
    # 2. Re-prefix with the current user's home (the portable case)
    under_home = normpath(join(home, path))
    if exists(under_home):
        return under_home
    # 3. Project-relative
    if project_dir:
        rel = normpath(join(project_dir, path))
        if exists(rel):
            return rel
    # 4. Best guess — prefer the home-relative form so the missing-asset
    # message is meaningful.
    return under_home


class ProjectSerializer:
    @classmethod
    def save(cls, scene: Scene, path: str,
             camera_state: Optional[dict] = None,
             settings: Optional[dict] = None) -> bool:
        try:
            # Directory the project file lives in — every asset path is
            # stored relative to this so the project is fully portable.
            project_dir = os.path.dirname(os.path.abspath(path))
            data = {
                "version": HEAVEN_VERSION,
                "type": "heaven3d_project",
                "scene": cls._serialize_node(scene.root, project_dir),
                "camera": camera_state or {},
                "settings": settings or {},
                "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            Log.info(f"Project saved: {path}")
            return True
        except Exception as ex:
            Log.error(f"Save failed: {ex}")
            traceback.print_exc()
            return False

    @classmethod
    def _serialize_node(cls, node: SceneNode, project_dir: str) -> dict:
        return {
            "name": node.name,
            "position": [round(v, 6) for v in node.position],
            "rotation_euler": [round(v, 6) for v in node.rotation_euler],
            "scale": [round(v, 6) for v in node.scale],
            # Store as a PORTABLE relative path (never absolute) so the
            # project survives being moved or shared to another machine.
            "source_path": _to_portable_path(node.source_path, project_dir),
            "mesh_type": node.mesh_type,
            "mesh_params": node.mesh_params if node.mesh_type else None,
            "material": node.material.to_dict() if node.material else None,
            "blueprint": node.blueprint.to_dict() if node.blueprint else None,
            "blueprints": [bp.to_dict() for bp in node.blueprints
                           if not getattr(bp, "_runtime_spawn", False)],
            "visible": node.visible,
            "tag": node.tag,
            "collider": node.collider.to_dict() if node.collider else None,
            "rigidbody": node.rigidbody.to_dict() if node.rigidbody else None,
            # Spawn points (Player / Enemy / pickups) round-trip exactly like
            # lights: a kind tag + the Inspector-editable config block.
            "spawn_kind": node.spawn_kind,
            "spawn_config": (node.spawn_config if node.spawn_kind else None),
            "is_sun": node.is_sun,
            "sun_light": {
                "direction": list(node.sun_light.direction),
                "color": list(node.sun_light.color),
                "intensity": node.sun_light.intensity,
                "shadow_map_size": node.sun_light.shadow_map_size,
                "shadow_bias": node.sun_light.shadow_bias,
            } if (node.is_sun and node.sun_light) else None,
            "is_point_light": node.is_point_light,
            "point_light": node.point_light.to_dict()
                if (node.is_point_light and node.point_light) else None,
            "is_spot_light": node.is_spot_light,
            "spot_light": node.spot_light.to_dict()
                if (node.is_spot_light and node.spot_light) else None,
            "is_clouds": node.is_clouds,
            "cloud_settings": node.cloud_settings.to_dict()
                if (node.is_clouds and node.cloud_settings) else None,
            "is_ocean": node.is_ocean,  # [TRIPLEA:ocean_serialize]
            "ocean_settings": node.ocean_settings.to_dict()
                if (node.is_ocean and node.ocean_settings) else None,
            "is_terrain": node.is_terrain,
            "terrain_config": node.user_data.get("terrain_config") if node.is_terrain else None,
            # "_runtime_" nodes (enemy bullets, flashes, ...) only exist
            # during Play — they are never written into the project file.
            "children": [cls._serialize_node(c, project_dir)
                         for c in node.children
                         if not c.name.startswith("_runtime_")
                         and not getattr(c, "_heaven3d_imported_part", False)],
        }

    @classmethod
    def load(cls, path: str, scene: Scene, engine) -> bool:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("type") != "heaven3d_project":
                Log.error(f"Not a Heaven3D project file: {path}")
                return False
            # Directory the project lives in — used to resolve portable
            # relative asset paths back to absolute ones at load time.
            project_dir = os.path.dirname(os.path.abspath(path))
            # Clear scene children
            for c in list(scene.root.children):
                c.remove()
            for child_data in data.get("scene", {}).get("children", []):
                cls._deserialize_node(child_data, scene.root, engine, project_dir)
            # Backward-compat: old projects had a top-level "sun" key.
            # If no sun node was deserialized, create a default one.
            if scene._sun_node is None:
                scene._create_default_sun()
                if data.get("sun"):
                    sd = data["sun"]
                    scene._sun_node.sun_light.direction = glm.vec3(*sd.get("direction",[-0.5,-1.0,-0.3]))
                    scene._sun_node.sun_light.color = glm.vec3(*sd.get("color",[1,0.96,0.9]))
                    scene._sun_node.sun_light.intensity = sd.get("intensity", 3.0)
            # Backward-compat: projects saved before clouds existed get the
            # default cloud layer injected on load — exactly like the Sun.
            if scene._clouds_node is None:
                scene._create_default_clouds()
            # Backward-compat: projects saved before the ocean existed get
            # the default ocean injected on load — exactly like the Clouds.
            # [TRIPLEA:ocean_load_backcompat]
            if scene._ocean_node is None:
                scene._create_default_ocean()
            # Hard rule: a scene may hold AT MOST ONE Player Spawn.  If a
            # hand-edited / corrupted project file contains several, keep
            # the first and drop the rest.
            seen_player_spawn = False
            for n in list(scene.root.traverse()):
                if getattr(n, "spawn_kind", None) == "player":
                    if seen_player_spawn:
                        Log.warn(f"Extra Player Spawn '{n.name}' removed — "
                                 f"only ONE is allowed per scene.")
                        n.remove()
                    else:
                        seen_player_spawn = True
            Log.info(f"Project loaded: {path}")
            return True
        except Exception as ex:
            Log.error(f"Load failed: {ex}")
            traceback.print_exc()
            return False

    @classmethod
    def _deserialize_node(cls, data: dict, parent: SceneNode, engine,
                          project_dir: str):
        node = SceneNode(name=data.get("name", "object"), parent=parent)
        node.position = glm.vec3(*data.get("position", [0,0,0]))
        node.rotation_euler = glm.vec3(*data.get("rotation_euler", [0,0,0]))
        node.scale = glm.vec3(*data.get("scale", [1,1,1]))
        # Resolve the portable stored path back to absolute, using the
        # project's directory as the base.
        node.source_path = _from_portable_path(data.get("source_path"), project_dir)
        node.is_terrain = data.get("is_terrain", False)
        node.is_sun = data.get("is_sun", False)
        if node.is_sun:
            sld = data.get("sun_light") or {}
            node.sun_light = SunLight(
                direction=glm.vec3(*sld.get("direction", [-0.5, -1.0, -0.3])),
                color=glm.vec3(*sld.get("color", [1.0, 0.96, 0.9])),
                intensity=sld.get("intensity", 3.0),
                shadow_map_size=sld.get("shadow_map_size", 2048),
                shadow_bias=sld.get("shadow_bias", 0.001),
            )
            engine.scene._sun_node = node
        node.is_point_light = data.get("is_point_light", False)
        if node.is_point_light:
            node.point_light = PointLight.from_dict(data.get("point_light") or {})
        node.is_spot_light = data.get("is_spot_light", False)
        if node.is_spot_light:
            node.spot_light = SpotLight.from_dict(data.get("spot_light") or {})
        node.is_clouds = data.get("is_clouds", False)
        if node.is_clouds:
            node.cloud_settings = CloudSettings.from_dict(
                data.get("cloud_settings") or {})
            engine.scene._clouds_node = node
        node.is_ocean = data.get("is_ocean", False)  # [TRIPLEA:ocean_deserialize]
        if node.is_ocean:
            node.ocean_settings = OceanSettings.from_dict(
                data.get("ocean_settings") or {})
            engine.scene._ocean_node = node
        # Spawn points (Player / Enemy / pickups).
        node.spawn_kind = data.get("spawn_kind")
        node.spawn_config = data.get("spawn_config") or {}

        if node.is_terrain and data.get("terrain_config"):
            engine.create_terrain_node(node, data["terrain_config"])
        elif node.source_path:
            engine.load_model_into(node, node.source_path)
        elif data.get("mesh_type"):
            # Rebuild an editor primitive (cube/plane/sphere) that has no
            # source file on disk.  Without this the node would load as an
            # invisible empty transform.
            mt = data["mesh_type"]
            params = data.get("mesh_params") or {}
            if mt == "cube":
                node.mesh = Mesh.make_cube(engine.ctx, params.get("size", 1.0))
            elif mt == "plane":
                node.mesh = Mesh.make_plane(engine.ctx, params.get("size", 10.0), 1)
            elif mt == "sphere":
                node.mesh = Mesh.make_sphere(engine.ctx, params.get("radius", 1.0))
            elif mt == "capsule":
                node.mesh = Mesh.make_capsule(
                    engine.ctx, params.get("radius", 0.35),
                    params.get("height", 1.8))
            node.mesh_type = mt
            node.mesh_params = params

        mat_data = data.get("material")
        if mat_data:
            # After load_model_into() ran above, the node already has a
            # material whose embedded texture descriptors were re-resolved
            # from the source GLB (because the saved form was the
            # "__embedded__" sentinel and can't carry the bytes themselves).
            # Merge instead of overwriting: keep any texture descriptors
            # already on the node's material, but apply the saved scalar
            # params (base_color, metallic, roughness, alpha_mode, …) so
            # in-editor material tweaks survive the round-trip.
            if node.material is None:
                node.material = Material.from_dict(mat_data)
            else:
                m = node.material
                m.name = mat_data.get("name", m.name)
                m.base_color = tuple(mat_data.get("base_color", m.base_color))
                m.metallic = mat_data.get("metallic", m.metallic)
                m.roughness = mat_data.get("roughness", m.roughness)
                m.emissive = tuple(mat_data.get("emissive", m.emissive))
                m.emissive_intensity = mat_data.get("emissive_intensity",
                                                    m.emissive_intensity)
                m.double_sided = mat_data.get("double_sided", m.double_sided)
                m.alpha_mode = mat_data.get("alpha_mode", m.alpha_mode)
                m.alpha_cutoff = mat_data.get("alpha_cutoff", m.alpha_cutoff)
                # For texture slots: prefer whatever was resolved during
                # load_model_into (re-extracted from the GLB).  Only fall
                # back to the saved value if the resolved slot is empty
                # AND the saved value isn't the embedded sentinel.
                for slot in ("base_color_tex", "metallic_roughness_tex",
                             "normal_tex", "emissive_tex"):
                    saved = _tex_descriptor_from_json(mat_data.get(slot))
                    current = getattr(m, slot)
                    if current is None and saved is not None:
                        setattr(m, slot, saved)

        node.visible = data.get("visible", True)
        node.tag = data.get("tag", "")
        # Physics components (restored before blueprints so on_begin_play
        # can rely on them if the project saved them explicitly).
        col_data = data.get("collider")
        if col_data:
            node.collider = Collider.from_dict(col_data)
        rb_data = data.get("rigidbody")
        if rb_data:
            node.rigidbody = RigidBody.from_dict(rb_data)

        bp_datas = data.get("blueprints")
        if not bp_datas:
            # Backward compatibility: single "blueprint" key.
            single = data.get("blueprint")
            bp_datas = [single] if single else []
        for bp_data in bp_datas:
            if not bp_data:
                continue
            bp = Blueprint.from_dict(bp_data)
            if bp:
                bp.properties = bp_data.get("properties", {})
                engine.attach_blueprint(node, bp)

        imported_part_names = {c.name for c in node.children
                               if getattr(c, "_heaven3d_imported_part", False)}
        imported_ext = os.path.splitext(str(node.source_path or ""))[1].lower()
        for child_data in data.get("children", []):
            child_count_before = len(node.children)
            cls._deserialize_node(child_data, node, engine, project_dir)
            if (node.source_path and len(node.children) > child_count_before
                    and child_data.get("name") in imported_part_names):
                node.children[-1]._heaven3d_imported_part = imported_ext

