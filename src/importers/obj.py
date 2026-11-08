# @heaven3d-part importers/obj.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# #############################################################################
# SECTION 2A  —  OBJ IMPORTER  (Wavefront .obj + .mtl — from scratch,
#                pure Python + numpy, ZERO extra dependencies)
# #############################################################################
#
# [OBJPATCH]  The old code sent .obj files through trimesh, which silently
# dropped MTL materials and textures.  This importer implements the complete
# Wavefront OBJ/MTL grammar itself (stdlib + numpy only) and emits the
# engine's long-standing GlbImporter.MeshData / NodeData contract, so the
# renderer, scene attachment, physics and project save/load code paths are
# completely untouched.

class ObjImporter:
    """
    AAA-quality Wavefront OBJ importer — pure Python + numpy.  No trimesh,
    no PIL, no new dependencies of any kind.

    OBJ support
      v / vn / vt (2 or 3 components) - f in v, v/vt, v//vn and v/vt/vn
      forms - negative (relative) indices - N-gon fan triangulation -
      backslash line continuations - comments - o/g groups (each group
      becomes a scene node) - usemtl (every material becomes its own
      indexed, vertex-deduplicated sub-mesh with its own Material) -
      mtllib (multiple library files, filenames containing spaces) -
      s smoothing groups (off = per-face flat shading, numbered groups
      smooth only within their group).

    MTL support
      newmtl - Kd/Ks/Ke/Tf - Ns (converted to GGX roughness through the
      standard sqrt(2/(Ns+2)) mapping) - d / Tr (including the -halo
      form) - illum - the PBR extensions Pr / Pm - map_Kd / map_Ke /
      map_Ka (as occlusion) / map_d (forces BLEND alpha) / map_Bump /
      bump / map_bump / norm (normal maps; an explicit norm map always
      wins over a height bump map) - every standard map option
      (-s -o -t -bm -mm -clamp -blendu -blendv -cc -texres -imfchan
      -type -boost) parsed with the correct arity, so texture filenames
      that contain spaces resolve correctly.

    Textures are emitted as ("path", absolute_path) descriptors, which
    the renderer's existing pygame/PIL decode chain already understands
    (PNG/JPEG/BMP/GIF/TGA/WebP/TIFF).  Lookup order: the .mtl folder,
    the .obj folder, basename-only in both, then a case-insensitive
    directory scan (survives Texture.PNG vs texture.png mismatches from
    sloppy exporters).  Missing files log a warning and simply leave
    that slot empty — the mesh still imports with its colour factors.

    UV convention: the engine uploads textures in OpenGL bottom-left
    origin (see Renderer.get_texture), which is exactly the Wavefront
    vt convention, so texture coordinates are used AS-IS (no V flip).
    """

    # map_* option arities: an int consumes exactly N following tokens;
    # "f3" consumes up to 3 following float tokens (the -o/-s/-t forms).
    _MAP_OPT_ARITY = {
        "-blendu": 1, "-blendv": 1, "-boost": 1, "-mm": 2,
        "-texres": 1, "-clamp": 1, "-bm": 1, "-imfchan": 1,
        "-type": 1, "-cc": 1,
        "-o": "f3", "-s": "f3", "-t": "f3",
    }

    # ------------------------------------------------------------ loading
    @classmethod
    def build_nodes(cls, path: str) -> List["GlbImporter.NodeData"]:
        """Parse a .obj (plus its .mtl libraries) into engine NodeData
        roots: one root per o/g group, each carrying one indexed MeshData
        per material used inside that group.  Node transforms stay
        identity — OBJ has no scene graph, exactly like a flat glTF
        import, so the attach code treats both identically."""
        path = os.fspath(path)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Model file not found: {path}")
        obj_dir = os.path.dirname(os.path.abspath(path))
        stem = os.path.splitext(os.path.basename(path))[0]

        # ---------------- Phase 1: tokenise the OBJ ----------------
        v_list: List[Tuple[float, float, float]] = []
        vt_list: List[Tuple[float, float]] = []
        vn_list: List[Tuple[float, float, float]] = []
        # group name -> material name (None allowed) -> [(tri, sg)]
        # tri = ((vi, ti, ni), (vi, ti, ni), (vi, ti, ni)) with fully
        # resolved 0-based indices; -1 = attribute absent on that corner.
        groups: Dict[str, Dict[Optional[str], list]] = {}
        cur_group: Optional[str] = None
        cur_mat: Optional[str] = None
        # Smoothing group; 0 = flat.  Defaults to smooth so normal-less
        # files shade exactly like the previous importer's smooth output;
        # an explicit "s off" in the file restores faceted shading.
        cur_sg = 1
        mtl_refs: List[str] = []
        n_faces = 0
        n_skipped = 0

        for line in cls._logical_lines(path):
            parts = line.split()
            kw = parts[0].lower()
            args = parts[1:]
            try:
                if kw == "v":
                    if len(args) < 3:
                        continue
                    x, y, z = float(args[0]), float(args[1]), float(args[2])
                    if math.isfinite(x) and math.isfinite(y) and math.isfinite(z):
                        v_list.append((x, y, z))
                elif kw == "vt":
                    if len(args) < 2:
                        continue
                    u, vv = float(args[0]), float(args[1])
                    if not (math.isfinite(u) and math.isfinite(vv)):
                        u, vv = 0.0, 0.0
                    vt_list.append((u, vv))
                elif kw == "vn":
                    if len(args) < 3:
                        continue
                    nx, ny, nz = float(args[0]), float(args[1]), float(args[2])
                    if math.isfinite(nx) and math.isfinite(ny) and math.isfinite(nz):
                        vn_list.append((nx, ny, nz))
                elif kw in ("o", "g"):
                    name = " ".join(args).strip()
                    if name:
                        cur_group = name
                        groups.setdefault(cur_group, {})
                elif kw == "usemtl":
                    cur_mat = " ".join(args).strip() or None
                elif kw == "mtllib":
                    cls._collect_mtllib(args, obj_dir, mtl_refs)
                elif kw == "s":
                    cur_sg = cls._parse_smoothing(args)
                elif kw == "f":
                    face = cls._parse_face(args, len(v_list),
                                           len(vt_list), len(vn_list))
                    if face is None:
                        n_skipped += 1
                        continue
                    gname = cur_group or stem
                    bucket = groups.setdefault(gname, {}).setdefault(cur_mat, [])
                    for tri in cls._fan_triangulate(face):
                        bucket.append((tri, cur_sg))
                        n_faces += 1
                # l / p / vp / free-form surface statements are ignored
                # by design (the engine renders triangles only).
            except (ValueError, IndexError, OverflowError):
                n_skipped += 1

        # ---------------- Phase 2: parse the .mtl libraries ----------------
        materials: Dict[str, dict] = {}
        for ref in mtl_refs:
            mtl_path = cls._resolve_file(ref, [obj_dir])
            if mtl_path is None:
                Log.warn(f"OBJ material library not found: '{ref}' "
                         f"(referenced by {os.path.basename(path)})")
                continue
            cls._parse_mtl(mtl_path, obj_dir, materials)

        # ------------- Phase 3: build one mesh per material -------------
        roots: List[GlbImporter.NodeData] = []
        n_meshes = 0
        for gname, gmap in groups.items():
            meshes: List[GlbImporter.MeshData] = []
            for mat_name, tris in gmap.items():
                md = cls._build_mesh(gname, mat_name, tris,
                                     v_list, vt_list, vn_list, materials)
                if md is not None:
                    meshes.append(md)
                    n_meshes += 1
            if meshes:
                roots.append(GlbImporter.NodeData(
                    name=gname, matrix=None, translation=None,
                    rotation=None, scale=None, meshes=meshes, children=[]))

        if not roots:
            Log.warn(f"OBJ import: no usable faces in {os.path.basename(path)}")
        else:
            msg = (f"OBJ imported: {os.path.basename(path)} - {n_faces} "
                   f"triangles, {n_meshes} meshes, {len(roots)} node(s), "
                   f"{len(materials)} materials")
            if n_skipped:
                msg += f" ({n_skipped} bad statement(s) skipped)"
            Log.info(msg)
        return roots

    # ------------------------------------------------------- OBJ lexing
    @staticmethod
    def _logical_lines(path: str):
        """Yield logical lines: comments stripped, backslash continuations
        joined, blank lines dropped.  Tolerant decoding (utf-8 with
        replacement) so one stray byte never aborts a whole import."""
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            buf = ""
            for raw in fh:
                line = raw.rstrip("\r\n")
                hash_pos = line.find("#")
                if hash_pos != -1:
                    line = line[:hash_pos]
                if line.endswith("\\"):
                    buf += line[:-1] + " "
                    continue
                line = (buf + line).strip()
                buf = ""
                if line:
                    yield line
            if buf.strip():
                yield buf.strip()

    @staticmethod
    def _is_float(tok: str) -> bool:
        try:
            float(tok)
            return True
        except (ValueError, TypeError):
            return False

    @staticmethod
    def _resolve_index(tok: str, count: int) -> int:
        """OBJ indices are 1-based; negative values are relative to the
        end of the list (-1 = most recently defined).  Returns a 0-based
        index; the illegal value 0 returns a hugely negative sentinel so
        the caller's range check rejects the face (an EMPTY slot, which
        is legitimate, never reaches this function)."""
        idx = int(float(tok))
        if idx > 0:
            return idx - 1
        if idx < 0:
            return count + idx
        return -10 ** 9

    @classmethod
    def _parse_face(cls, args, nv, nt, nn):
        """Parse one f statement into a list of (vi, ti, ni) corners with
        fully resolved 0-based indices (-1 = absent).  Returns None when
        the face is malformed or references out-of-range attributes — the
        caller skips it and keeps importing the rest of the model."""
        if len(args) < 3:
            return None
        corners = []
        for tok in args:
            fields = tok.split("/")
            vi = cls._resolve_index(fields[0], nv) if fields[0] else -1
            ti = -1
            ni = -1
            if len(fields) >= 2 and fields[1] != "":
                ti = cls._resolve_index(fields[1], nt)
            if len(fields) >= 3 and fields[2] != "":
                ni = cls._resolve_index(fields[2], nn)
            if vi < 0 or vi >= nv:
                return None
            # -1 is legitimate ONLY for an empty slot (handled above, this
            # code never sees it); anything below -1 or past the list end
            # is a corrupt reference — reject the whole face.
            if ti < -1 or ti >= nt or ni < -1 or ni >= nn:
                return None
            corners.append((vi, ti, ni))
        return corners

    @staticmethod
    def _fan_triangulate(face):
        """Fan-triangulate an N-gon.  Triangles that repeat a vertex
        index (degenerate) are dropped; everything else is preserved in
        the file's original winding order."""
        for i in range(1, len(face) - 1):
            tri = (face[0], face[i], face[i + 1])
            if (tri[0][0] == tri[1][0] or tri[1][0] == tri[2][0]
                    or tri[2][0] == tri[0][0]):
                continue
            yield tri

    @staticmethod
    def _parse_smoothing(args) -> int:
        """s off / s 0  ->  0 (flat shading);   s N  ->  group N."""
        if not args:
            return 0
        tok = args[0].strip().lower()
        if tok in ("off", "0"):
            return 0
        try:
            return max(1, int(float(tok)))
        except ValueError:
            return 0

    @classmethod
    def _collect_mtllib(cls, args, obj_dir, out):
        """mtllib may name several libraries on one line, and a library
        filename may itself contain spaces.  Try the full remainder as a
        single name first; otherwise treat each token as its own file."""
        if not args:
            return
        joined = " ".join(args)
        if cls._resolve_file(joined, [obj_dir]) is not None:
            out.append(joined)
            return
        out.extend(args)

    # ------------------------------------------------------- MTL parsing
    @staticmethod
    def _default_mtl(name: str) -> dict:
        return {"name": name,
                "kd": [0.8, 0.8, 0.8], "ka": [0.0, 0.0, 0.0],
                "ks": [0.0, 0.0, 0.0], "ke": [0.0, 0.0, 0.0],
                "tf": None, "ns": None, "alpha": 1.0, "illum": 2,
                "pr": None, "pm": None,
                "base_color_tex": None, "emissive_tex": None,
                "occlusion_tex": None, "normal_tex": None,
                "has_map_d": False}

    @classmethod
    def _parse_mtl(cls, mtl_path: str, obj_dir: str, materials: Dict[str, dict]):
        """Parse one .mtl library into the shared materials dict.  Texture
        paths resolve against the .mtl folder first, then the .obj folder."""
        mtl_dir = os.path.dirname(os.path.abspath(mtl_path))
        search_dirs = [mtl_dir]
        if obj_dir not in search_dirs:
            search_dirs.append(obj_dir)
        cur: Optional[dict] = None
        for line in cls._logical_lines(mtl_path):
            parts = line.split()
            kw = parts[0].lower()
            args = parts[1:]
            if kw == "newmtl":
                name = " ".join(args).strip()
                if name:
                    cur = cls._default_mtl(name)
                    materials[name] = cur
                else:
                    cur = None
                continue
            if cur is None:
                continue
            try:
                if kw in ("kd", "ka", "ks", "ke", "tf"):
                    vals = [float(a) for a in args[:3] if cls._is_float(a)]
                    if not vals:
                        continue
                    while len(vals) < 3:
                        vals.append(vals[-1])
                    cur[kw] = vals[:3]
                elif kw == "ns":
                    cur["ns"] = float(args[0])
                elif kw == "d":
                    cur["alpha"] = cls._parse_dissolve(args)
                elif kw == "tr":
                    cur["alpha"] = 1.0 - cls._parse_dissolve(args)
                elif kw == "illum":
                    cur["illum"] = int(float(args[0]))
                elif kw == "pr":
                    cur["pr"] = float(args[0])
                elif kw == "pm":
                    cur["pm"] = float(args[0])
                elif kw in ("map_kd", "map_ke", "map_ka", "map_d",
                            "map_bump", "bump", "norm",
                            "map_ks", "map_ns", "map_pr", "map_pm",
                            "decal", "disp", "refl"):
                    tex = cls._parse_map(args, search_dirs)
                    if tex is not None:
                        cls._assign_texture(cur, kw, tex)
                # ps / pcr / aniso / anisor / sharpness and any unknown
                # statement are parsed-and-ignored by design.
            except (ValueError, IndexError, OverflowError):
                continue

    @classmethod
    def _parse_dissolve(cls, args) -> float:
        """d / Tr value; takes the LAST float on the line so the
        'd -halo 0.5' form parses correctly.  Clamped to [0, 1]."""
        val = 1.0
        for tok in args:
            if cls._is_float(tok):
                val = float(tok)
        return _clamp(val, 0.0, 1.0)

    @classmethod
    def _parse_map(cls, args, search_dirs) -> Optional[str]:
        """Parse a map_* statement: skip leading -options using the spec
        arities, then treat everything that remains as the filename
        (re-joined with spaces).  Returns an absolute path or None."""
        i = 0
        name_tokens: List[str] = []
        while i < len(args):
            tok = args[i]
            if tok.startswith("-") and not cls._is_float(tok):
                arity = cls._MAP_OPT_ARITY.get(tok.lower())
                if arity is None:
                    # Unknown option: consume it plus any bare float args.
                    i += 1
                    while i < len(args) and cls._is_float(args[i]):
                        i += 1
                elif arity == "f3":
                    i += 1
                    consumed = 0
                    while consumed < 3 and i < len(args) and cls._is_float(args[i]):
                        i += 1
                        consumed += 1
                else:
                    i += 1 + arity
            else:
                name_tokens = args[i:]
                break
        if not name_tokens:
            return None
        name = " ".join(name_tokens).strip().strip('"').strip("'")
        if not name:
            return None
        resolved = cls._resolve_file(name, search_dirs)
        if resolved is None:
            Log.warn(f"OBJ texture not found: '{name}'")
        return resolved

    @staticmethod
    def _assign_texture(mat: dict, kw: str, path: str):
        if kw == "map_d":
            mat["has_map_d"] = True
            return
        if kw == "map_kd":
            mat["base_color_tex"] = path
        elif kw == "map_ke":
            mat["emissive_tex"] = path
        elif kw == "map_ka":
            mat["occlusion_tex"] = path
        elif kw == "norm":
            # A true tangent-space normal map always wins.
            mat["normal_tex"] = path
        elif kw in ("map_bump", "bump"):
            # Height/bump map: used as the normal source only when no
            # explicit norm map exists.
            if mat["normal_tex"] is None:
                mat["normal_tex"] = path
        # map_ks / map_ns / map_pr / map_pm / decal / disp / refl have no
        # matching engine slot (the engine uses packed glTF-style
        # metallic-roughness textures); the scalar factors carry those
        # values instead.

    @staticmethod
    def _ci_join(root: str, parts) -> Optional[str]:
        """Walk path components below root case-INSENSITIVELY.  Returns
        the on-disk path if it resolves to a file, else None."""
        cur = root
        for part in parts:
            if not os.path.isdir(cur):
                return None
            try:
                entries = os.listdir(cur)
            except OSError:
                return None
            low = part.lower()
            match = None
            for entry in entries:
                if entry.lower() == low:
                    match = entry
                    break
            if match is None:
                return None
            cur = os.path.join(cur, match)
        return cur if os.path.isfile(cur) else None

    @classmethod
    def _resolve_file(cls, name: str, search_dirs) -> Optional[str]:
        """Locate a referenced file: as given (absolute, or relative to
        each search dir), backslash-normalised, basename-only, then a
        case-insensitive resolution (path components first, then a plain
        basename scan) — so "textures/Wood_Normal.PNG" referenced as
        "Textures\\wood_normal.png" still loads."""
        name = name.strip()
        if not name:
            return None
        if os.path.isabs(name) and os.path.isfile(name):
            return os.path.abspath(name)
        norm = name.replace("\\", "/")
        base = os.path.basename(norm)
        for d in search_dirs:
            for cand in (os.path.join(d, name), os.path.join(d, norm),
                         os.path.join(d, base)):
                if os.path.isfile(cand):
                    return os.path.abspath(cand)
        rel_parts = [p for p in norm.split("/") if p not in ("", ".")]
        for d in search_dirs:
            hit = cls._ci_join(d, rel_parts)
            if hit is not None:
                return os.path.abspath(hit)
            hit = cls._ci_join(d, [base])
            if hit is not None:
                return os.path.abspath(hit)
        return None

    # ------------------------------------------------------- mesh building
    @classmethod
    def _build_mesh(cls, gname, mat_name, tris, v_list, vt_list, vn_list,
                    materials) -> Optional["GlbImporter.MeshData"]:
        """Assemble one indexed MeshData from every triangle of one
        (group, material) bucket.

        Vertex de-duplication keys on the full attribute tuple:
          * corners with file normals merge on (v, vt, vn);
          * normal-less corners inside a smoothing group merge on
            (v, vt, group) and receive area-weighted smooth normals;
          * normal-less corners with "s off" get one vertex PER FACE —
            exactly what flat shading requires.
        """
        vmap: Dict[tuple, int] = {}
        meta: List[Tuple[int, int, int, int]] = []   # vi, ti, ni, sg
        indices: List[int] = []
        for tri, sg in tris:
            for (vi, ti, ni) in tri:
                if ni >= 0:
                    key = (vi, ti, ni)
                elif sg != 0:
                    key = (vi, ti, -1, sg)
                else:
                    # Flat shading: a unique vertex for this face corner.
                    key = (vi, ti, -2, len(indices))
                idx = vmap.get(key)
                if idx is None:
                    idx = len(meta)
                    vmap[key] = idx
                    meta.append((vi, ti, ni, sg))
                indices.append(idx)
        if not meta:
            return None

        n = len(meta)
        positions = np.empty((n, 3), dtype=np.float32)
        uvs = np.zeros((n, 2), dtype=np.float32)
        normals = np.zeros((n, 3), dtype=np.float32)
        needs_normal = np.zeros(n, dtype=bool)
        has_uv = False
        for i, (vi, ti, ni, sg) in enumerate(meta):
            positions[i] = v_list[vi]
            if ti >= 0:
                uvs[i] = vt_list[ti]
                has_uv = True
            if ni >= 0:
                normals[i] = vn_list[ni]
            else:
                needs_normal[i] = True
        if needs_normal.any():
            cls._fill_missing_normals(positions, meta, tris, indices,
                                      normals, needs_normal)
        # Normalise everything (file normals are not guaranteed to be
        # unit length); degenerate normals fall back to +Z so the shader
        # never receives a zero vector.
        ln = np.linalg.norm(normals, axis=1, keepdims=True)
        bad = ln[:, 0] < 1e-12
        ln[bad] = 1.0
        normals = (normals / ln).astype(np.float32)
        if bad.any():
            normals[bad] = (0.0, 0.0, 1.0)

        if gname and mat_name and gname != mat_name:
            mesh_name = f"{gname}.{mat_name}"
        else:
            mesh_name = mat_name or gname or "mesh"

        mat_info = None
        if mat_name is not None:
            mtl = materials.get(mat_name)
            if mtl is None:
                # Case-insensitive fallback — exporters disagree on case.
                low = mat_name.lower()
                for k, v in materials.items():
                    if k.lower() == low:
                        mtl = v
                        break
            if mtl is None:
                Log.warn(f"OBJ material '{mat_name}' is not defined in any "
                         f".mtl library - default PBR values used.")
                mtl = cls._default_mtl(mat_name)
            mat_info = cls._material_info(mtl)

        return GlbImporter.MeshData(
            name=mesh_name,
            positions=positions,
            normals=normals,
            uvs=uvs if has_uv else None,
            tangents=None,
            indices=np.asarray(indices, dtype=np.uint32),
            material_info=mat_info,
        )

    @staticmethod
    def _fill_missing_normals(positions, meta, tris, indices, normals,
                              needs_normal):
        """Compute normals for corners the file left bare.
        Smoothing-group corners accumulate area-weighted face normals per
        (vertex position, group); s-off corners take their own face
        normal (their vertices are unique per face, so nothing blends)."""
        smooth_acc: Dict[Tuple[int, int], np.ndarray] = {}
        for k, (tri, sg) in enumerate(tris):
            a, b, c = indices[3 * k], indices[3 * k + 1], indices[3 * k + 2]
            pa, pb, pc = positions[a], positions[b], positions[c]
            fn = np.cross(pb - pa, pc - pa)   # magnitude = 2x area
            if not (needs_normal[a] or needs_normal[b] or needs_normal[c]):
                continue
            for corner, (vi, ti, ni) in zip((a, b, c), tri):
                if not needs_normal[corner]:
                    continue
                if sg == 0:
                    normals[corner] += fn
                else:
                    key = (vi, sg)
                    acc = smooth_acc.get(key)
                    if acc is None:
                        smooth_acc[key] = fn.astype(np.float64).copy()
                    else:
                        acc += fn
        for i, (vi, ti, ni, sg) in enumerate(meta):
            if needs_normal[i] and sg != 0:
                acc = smooth_acc.get((vi, sg))
                if acc is not None:
                    normals[i] = acc

    # ------------------------------------------------------- MTL -> PBR
    @classmethod
    def _material_info(cls, m: dict) -> dict:
        """Adapt one parsed MTL material to the material_info dict consumed
        by Material.from_glb_info — the exact contract the glTF importer
        produces, so every downstream system treats both identically."""
        kd = m["kd"]
        alpha = _clamp(m["alpha"], 0.0, 1.0)
        base = [_clamp(float(kd[0]), 0.0, 1.0),
                _clamp(float(kd[1]), 0.0, 1.0),
                _clamp(float(kd[2]), 0.0, 1.0),
                alpha]
        if m["pr"] is not None:
            roughness = _clamp(float(m["pr"]), 0.0, 1.0)
        else:
            # Blinn-Phong exponent -> GGX roughness (the standard
            # sqrt(2/(Ns+2)) mapping).  No Ns -> 1.0, matching the glTF
            # default the engine already uses for factor-less materials.
            ns = _clamp(float(m["ns"]) if m["ns"] is not None else 0.0,
                        0.0, 10000.0)
            roughness = math.sqrt(2.0 / (ns + 2.0))
        roughness = _clamp(roughness, 0.045, 1.0)
        metallic = (_clamp(float(m["pm"]), 0.0, 1.0)
                    if m["pm"] is not None else 0.0)
        emissive = [_clamp(float(c), 0.0, 64.0) for c in m["ke"]]
        alpha_mode = "BLEND" if (alpha < 0.999 or m["has_map_d"]) else "OPAQUE"

        def _tex(path):
            return ("path", path) if path else None

        return {
            "name": m["name"],
            "base_color": base,
            "metallic": metallic,
            "roughness": roughness,
            "base_color_tex": _tex(m["base_color_tex"]),
            # OBJ carries roughness/metallic as separate greyscale maps;
            # merging them into the engine's packed glTF-style texture
            # would require an imaging library, so the scalar factors
            # (Pr/Pm, or Ns converted) carry those values instead.
            "metallic_roughness_tex": None,
            "normal_tex": _tex(m["normal_tex"]),
            "emissive": emissive,
            "emissive_intensity": 1.0,
            "emissive_tex": _tex(m["emissive_tex"]),
            "occlusion_tex": _tex(m["occlusion_tex"]),
            "double_sided": False,
            "alpha_mode": alpha_mode,
            "alpha_cutoff": 0.5,
        }
# [OBJPATCH:obj_importer_end]


# [PATCHB:blend_importer_begin]