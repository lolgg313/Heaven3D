# @heaven3d-part importers/glb_classic.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# #############################################################################
# SECTION 2  —  GLB IMPORTER  (from scratch — pure Python, no deps)
# #############################################################################

class GlbImporter:
    """
    glTF/GLB model import — backed by **trimesh**.

    The previous hand-rolled parser (chunk reader, accessor decoding,
    stride handling, material/extension traversal) was ripped out entirely:
    trimesh now does all glTF-spec plumbing and hands us numpy geometry
    plus PIL textures.  This class is a thin ADAPTER that converts
    trimesh's output into the engine's long-standing MeshData/NodeData
    contract (numpy arrays in, straight into a moderngl.Buffer), so the
    renderer, scene attachment and EGG writer are completely unchanged.

    Scope: meshes, normals, UVs, materials (base colour /
    metallic-roughness / normal / emissive / occlusion), textures, node
    world transforms.  Skinning / morph / animations are ignored.
    """

    @dataclass
    class MeshData:
        name: str
        positions: np.ndarray
        normals: Optional[np.ndarray]
        uvs: Optional[np.ndarray]
        tangents: Optional[np.ndarray]
        indices: Optional[np.ndarray]
        material_info: Optional[dict]

    @dataclass
    class NodeData:
        name: str
        matrix: Optional[List[float]]
        translation: Optional[List[float]]
        rotation: Optional[List[float]]
        scale: Optional[List[float]]
        meshes: List["GlbImporter.MeshData"]
        children: List["GlbImporter.NodeData"]

    # ------------------------------------------------------------- loading
    @classmethod
    def build_nodes(cls, path: str) -> List["GlbImporter.NodeData"]:
        """Load a .glb/.gltf with trimesh and adapt the result into the
        engine's NodeData contract.  trimesh does ALL the glTF plumbing
        (buffers, accessors, strides, extensions, embedded + external
        textures); this method only flattens the scene graph into root
        nodes.

        IMPORTANT — transforms are BAKED INTO THE VERTEX DATA, not stored
        as node matrices.  The old approach kept each mesh's world matrix
        and let _apply_glb_transform decompose it into TRS on a SceneNode.
        That decomposition is mathematically impossible whenever the glTF
        hierarchy combines non-uniform scale with a rotated child (the
        composed world matrix then contains SHEAR, which no TRS can
        represent) — the result was sub-meshes landing in wrong positions,
        rotations and scales on exactly those models.  It also DOUBLE-
        APPLIED the decomposed scale on duplicate/load: the serializer
        restores the node's scale first, then load_model_into multiplied
        the GLB's scale on top again.  Baking the world transform into
        positions/normals is exact for ANY matrix and keeps the node
        transform identity, so both bugs are eliminated at the root."""
        cls._require_trimesh()
        path = os.fspath(path)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Model file not found: {path}")
        try:
            import trimesh
            loaded = trimesh.load_scene(path, resolver=cls._make_resolver(path))
        except Exception as ex:
            raise ValueError(
                f"trimesh could not load '{os.path.basename(path)}': {ex}")
        roots: List[GlbImporter.NodeData] = []
        for name, world_T, mesh in cls._iter_scene_meshes(loaded):
            md = cls._meshdata_from_trimesh(mesh, name)
            if md is None:
                continue
            # Bake the node's WORLD transform into the geometry itself
            # (positions, normals, winding) — see _bake_world_transform.
            cls._bake_world_transform(
                md, np.asarray(world_T, dtype=np.float64))
            roots.append(cls.NodeData(
                name=name, matrix=None,
                translation=None, rotation=None, scale=None,
                meshes=[md], children=[]))
        if not roots:
            Log.warn(f"glTF import: no triangle meshes in {os.path.basename(path)}")
        else:
            Log.info(f"glTF imported (trimesh): {os.path.basename(path)} "
                     f"({len(roots)} mesh parts)")
        return roots

    @staticmethod
    def _require_trimesh():
        try:
            import trimesh  # noqa: F401
        except ImportError:
            raise RuntimeError(
                "Model import requires the 'trimesh' package — "
                "install it with:  pip install trimesh")

    @staticmethod
    def _make_resolver(path: str):
        """trimesh looks external resources up by their RAW glTF URI — a
        URL-encoded name like "my%20tex.png" is never found on disk.  This
        resolver retries the lookup with the URI percent-decoded (glTF URIs
        are URL-encoded by spec), which makes textures/buffers with spaces
        or special characters in their filenames load correctly."""
        from trimesh import resolvers

        class _UrlDecodeResolver(resolvers.FilePathResolver):
            def get(self, name):
                try:
                    return super().get(name)
                except Exception:
                    from urllib.parse import unquote
                    return super().get(unquote(name))

        return _UrlDecodeResolver(path)

    @classmethod
    def _iter_scene_meshes(cls, loaded):
        """Return [(name, world_transform_4x4, trimesh.Trimesh)] for every
        triangle mesh in whatever load_scene produced (a full Scene, or a
        bare Trimesh for trivial files).  Falls back to a flattened dump()
        if the scene-graph walk fails, so geometry is never dropped."""
        import trimesh
        if isinstance(loaded, trimesh.Trimesh):
            try:
                name = str(loaded.metadata.get("name") or "mesh")
            except Exception:
                name = "mesh"
            return [(name, np.eye(4), loaded)]
        out = []
        try:
            for node_name in loaded.graph.nodes_geometry:
                T, geom_name = loaded.graph.get(node_name)
                geom = loaded.geometry.get(geom_name)
                if isinstance(geom, trimesh.Trimesh):
                    out.append((str(node_name),
                                np.asarray(T, dtype=np.float64), geom))
        except Exception as ex:
            Log.warn(f"trimesh scene-graph walk failed ({ex}); flattened dump().")
            out = []
        if not out:
            try:
                dumped = loaded.dump(concatenate=False)
                geoms = dumped.values() if isinstance(dumped, dict) else dumped
                for i, geom in enumerate(geoms):
                    if isinstance(geom, trimesh.Trimesh):
                        out.append((f"mesh_{i}", np.eye(4), geom))
            except Exception as ex:
                Log.warn(f"trimesh dump failed: {ex}")
        return out

    @classmethod
    def _meshdata_from_trimesh(cls, mesh, name: str) -> Optional["GlbImporter.MeshData"]:
        positions = np.asarray(mesh.vertices, dtype=np.float32)
        if positions.size == 0:
            return None
        faces = getattr(mesh, "faces", None)
        indices = None
        if faces is not None and len(faces):
            indices = np.asarray(faces, dtype=np.uint32).ravel()
        # Normals — trimesh computes smooth vertex normals on demand; if
        # they come back degenerate/NaN, fall back to our own generator.
        normals = None
        try:
            vn = np.asarray(mesh.vertex_normals, dtype=np.float32)
            if (vn.shape == positions.shape and np.isfinite(vn).all()
                    and float(np.abs(vn).sum()) > 0.0):
                normals = vn
        except Exception:
            normals = None
        if normals is None:
            normals = cls._gen_smooth_normals(positions, indices)
        # UVs (only TextureVisuals carry them)
        uvs = None
        visual = getattr(mesh, "visual", None)
        uv = getattr(visual, "uv", None)
        if uv is not None:
            uv = np.asarray(uv, dtype=np.float32)
            if uv.shape == (len(positions), 2) and np.isfinite(uv).all():
                uvs = uv
        # Tangents: trimesh does not reliably expose glTF tangent data; the
        # engine's mikktspace-style generator in Mesh.upload builds them
        # from positions/normals/UVs, which is visually equivalent.
        mat_info = cls._material_info_from_trimesh(getattr(visual, "material", None))
        return cls.MeshData(
            name=name,
            positions=positions,
            normals=normals,
            uvs=uvs,
            tangents=None,
            indices=indices,
            material_info=mat_info,
        )

    @classmethod
    def _bake_world_transform(cls, md: "GlbImporter.MeshData",
                              T: np.ndarray):
        """Apply a 4x4 world transform to a MeshData IN PLACE.

        This replaces matrix-decomposition-at-attach entirely:

          * positions  p' = M3 · p + t          (exact for any matrix,
                                                  including shear/mirror)
          * normals    n' = normalize(inv(M3)ᵀ · n)   (the correct normal
                                                  transform under non-uniform
                                                  scale/shear)
          * winding    if det(M3) < 0 the node is MIRRORED; every triangle's
                                                  vertex order is swapped so
                                                  front faces stay front faces
                                                  and computed tangents keep
                                                  the right handedness.

        All arrays are REBUILT (never written through trimesh's buffers),
        so a geometry shared by several scene nodes stays intact for the
        other nodes.  Identity transforms return immediately."""
        T = np.asarray(T, dtype=np.float64)
        if np.allclose(T, np.eye(4), atol=1e-12):
            return
        M3 = T[:3, :3]
        t = T[:3, 3]

        # --- positions (row-vector form: p' = p · M3ᵀ + t) ---
        p = np.asarray(md.positions, dtype=np.float64)
        md.positions = (p @ M3.T + t).astype(np.float32)

        # --- normals: inverse-transpose of the linear part ---
        if md.normals is not None and len(md.normals):
            try:
                N = np.linalg.inv(M3).T
            except np.linalg.LinAlgError:
                # Singular matrix (zero scale on an axis) — fall back to
                # the linear part itself; normals are renormalised anyway.
                N = M3
            n = np.asarray(md.normals, dtype=np.float64) @ N.T
            ln = np.linalg.norm(n, axis=1, keepdims=True)
            ln[ln < 1e-12] = 1.0
            md.normals = (n / ln).astype(np.float32)

        # --- mirrored nodes: flip triangle winding ---
        if float(np.linalg.det(M3)) < 0.0:
            if md.indices is not None and len(md.indices) >= 3:
                idx = np.asarray(md.indices, dtype=np.uint32).reshape(-1, 3)
                idx = idx[:, [0, 2, 1]].copy()
                md.indices = idx.ravel()
            elif md.indices is None and len(md.positions) % 3 == 0:
                # Triangle soup: swap the 2nd/3rd vertex of every triangle
                # in every per-vertex attribute.
                for attr in ("positions", "normals", "uvs"):
                    arr = getattr(md, attr)
                    if arr is not None and len(arr) == len(md.positions):
                        a = np.asarray(arr).reshape(-1, 3, arr.shape[1])
                        a[:, [1, 2]] = a[:, [2, 1]]

    @classmethod
    def _material_info_from_trimesh(cls, mat) -> Optional[dict]:
        """Adapt a trimesh PBRMaterial to the material_info dict consumed by
        Material.from_glb_info.  Textures arrive as PIL images (trimesh has
        already resolved embedded buffers AND external files) and are
        re-encoded to PNG bytes to keep the ('bytes', payload) descriptor
        contract."""
        if mat is None:
            return None

        def _factor(attr, default):
            v = getattr(mat, attr, None)
            if v is None:
                return list(default)
            try:
                vals = [float(x) for x in v]
            except TypeError:
                return list(default)
            # glTF factors are 0..1 floats, but trimesh stores colours as
            # uint8 (0..255) — normalise or every tinted material blows out.
            if vals and max(vals) > 1.0:
                vals = [x / 255.0 for x in vals]
            return vals

        def _scalar(attr, default):
            v = getattr(mat, attr, None)
            if v is None:
                return default
            try:
                return float(v)
            except (TypeError, ValueError):
                return default

        def _tex(attr):
            img = getattr(mat, attr, None)
            if img is None:
                return None
            try:
                return ("bytes", cls._pil_to_png_bytes(img))
            except Exception as ex:
                Log.warn(f"glTF texture '{attr}' re-encode failed: {ex}")
                return None

        return {
            "name": str(getattr(mat, "name", None) or "material"),
            "base_color": _factor("baseColorFactor", [1.0, 1.0, 1.0, 1.0]),
            # glTF defaults when the factor is absent: metallic=1, rough=1.
            "metallic": _scalar("metallicFactor", 1.0),
            "roughness": _scalar("roughnessFactor", 1.0),
            "base_color_tex": _tex("baseColorTexture"),
            "metallic_roughness_tex": _tex("metallicRoughnessTexture"),
            "normal_tex": _tex("normalTexture"),
            "emissive": _factor("emissiveFactor", [0.0, 0.0, 0.0]),
            "emissive_intensity": 1.0,
            "emissive_tex": _tex("emissiveTexture"),
            "occlusion_tex": _tex("occlusionTexture"),
            "double_sided": bool(getattr(mat, "doubleSided", False)),
            "alpha_mode": str(getattr(mat, "alphaMode", None) or "OPAQUE"),
            # glTF alphaCutoff (default 0.5 per spec) — survives the import
            # so MASK foliage keeps the exporter's tuned edge.
            "alpha_cutoff": _scalar("alphaCutoff", 0.5),
        }

    @staticmethod
    def _pil_to_png_bytes(img) -> bytes:
        """Re-encode any PIL image (PNG/JPEG/WebP/TGA/palette…) as PNG
        bytes — a format the renderer's texture chain decodes universally."""
        import io
        if getattr(img, "mode", None) == "P":
            img = img.convert("RGBA")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    @staticmethod
    def _gen_smooth_normals(positions: np.ndarray,
                            indices: Optional[np.ndarray]) -> np.ndarray:
        """Area-weighted smooth vertex normals.  Indexed meshes share normals
        across vertices (smooth shading); non-indexed triangle soup gets flat
        per-face normals."""
        n = len(positions)
        if indices is None or len(indices) < 3:
            tris = positions.reshape(-1, 3, 3)
            fn = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
            ln = np.linalg.norm(fn, axis=1, keepdims=True)
            ln[ln < 1e-12] = 1.0
            fn = fn / ln
            return np.repeat(fn, 3, axis=0).astype(np.float32)
        tris = indices.reshape(-1, 3)
        a = positions[tris[:, 0]]
        b = positions[tris[:, 1]]
        c = positions[tris[:, 2]]
        fn = np.cross(b - a, c - a)   # magnitude = 2× triangle area
        normals = np.zeros((n, 3), dtype=np.float32)
        np.add.at(normals, tris[:, 0], fn)
        np.add.at(normals, tris[:, 1], fn)
        np.add.at(normals, tris[:, 2], fn)
        ln = np.linalg.norm(normals, axis=1, keepdims=True)
        ln[ln < 1e-12] = 1.0
        return (normals / ln).astype(np.float32)

    # ##############################################################
    #  EGG WRITER  (glb → .egg text converter, built into the engine)
    # ##############################################################
    @classmethod
    def glb_to_egg(cls, glb_path: str, egg_path: str) -> bool:
        try:
            roots = cls.build_nodes(glb_path)
            lines: List[str] = []
            lines.append("<CoordinateSystem> { Z-Up }")
            lines.append("")
            def _emit(nd, indent):
                pad = "  " * indent
                lines.append(f"{pad}<Group> {cls._safe_name(nd.name)} {{")
                if nd.matrix:
                    m = nd.matrix
                    lines.append(f"{pad}  <Transform> {{ <Matrix4> {{")
                    for r in range(4):
                        lines.append(f"{pad}      " + " ".join(f"{m[r*4+c]:.6f}" for c in range(4)))
                    lines.append(f"{pad}    }} }}")
                else:
                    if nd.translation or nd.rotation or nd.scale:
                        lines.append(f"{pad}  <Transform> {{")
                        if nd.translation:
                            t = nd.translation
                            lines.append(f"{pad}    <Translate> {{ {t[0]:.6f} {t[1]:.6f} {t[2]:.6f} }}")
                        if nd.rotation:
                            q = nd.rotation
                            quat = glm.quat(glm.vec3(0))
                            try:
                                qg = glm.quat(q[3], q[0], q[1], q[2])
                                hpr = glm.degrees(glm.eulerAngles(qg))
                                lines.append(f"{pad}    <Rotation> {{ {hpr[0]:.6f} {hpr[1]:.6f} {hpr[2]:.6f} }}")
                            except Exception:
                                pass
                        if nd.scale:
                            s = nd.scale
                            lines.append(f"{pad}    <Scale> {{ {s[0]:.6f} {s[1]:.6f} {s[2]:.6f} }}")
                        lines.append(f"{pad}  }}")
                for mi, mesh in enumerate(nd.meshes):
                    cls._mesh_to_egg(mesh, lines, pad + "  ", f"{cls._safe_name(nd.name)}_m{mi}")
                for c in nd.children:
                    _emit(c, indent + 1)
                lines.append(f"{pad}}}")
                lines.append("")
            for r in roots:
                _emit(r, 0)
            with open(egg_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            Log.info(f"GLB → EGG: {glb_path} → {egg_path}")
            return True
        except Exception as ex:
            Log.error(f"GLB→EGG conversion failed: {ex}")
            return False

    @classmethod
    def _mesh_to_egg(cls, mesh, lines, pad, pool_base):
        positions = mesh.positions
        normals = mesh.normals
        uvs = mesh.uvs
        n = len(positions)
        pool_name = f"{pool_base}_vpool"
        lines.append(f"{pad}<VertexPool> {pool_name} {{")
        for i in range(n):
            x, y, z = float(positions[i,0]), float(positions[i,1]), float(positions[i,2])
            ln = f"{pad}  {i} <Vertex> {x:.6f} {y:.6f} {z:.6f} {{"
            if normals is not None:
                ln += f" <Normal> {{ {float(normals[i,0]):.6f} {float(normals[i,1]):.6f} {float(normals[i,2]):.6f} }}"
            if uvs is not None:
                ln += f" <UV> {{ {float(uvs[i,0]):.6f} {float(uvs[i,1]):.6f} }}"
            ln += " }"
            lines.append(ln)
        lines.append(f"{pad}}}")
        indices = mesh.indices
        if indices is None:
            indices = np.arange(n, dtype=np.uint32)
        for ti in range(0, len(indices) - 2, 3):
            a, b, c = int(indices[ti]), int(indices[ti+1]), int(indices[ti+2])
            lines.append(f"{pad}<Polygon> {{ <VertexRef> {{ {a} {b} {c} <Ref> {{ {pool_name} }} }} }}")

    @staticmethod
    def _safe_name(n):
        return "".join(c if c.isalnum() or c == "_" else "_" for c in str(n))



# [OBJPATCH:obj_importer_begin]