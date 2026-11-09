# @heaven3d-part importers/xfile.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# ---------------------------------------------------------------------------
# [X IMPORTER]  DirectX .x models (GameGuru Classic, DirectX 9 era) -> parts
#
#   Standard library + NumPy only.  Reads the text ("txt"), binary ("bin")
#   and MSZIP-compressed ("tzip" / "bzip") variants, 32- or 64-bit floats.
#   Frames (with their FrameTransformMatrix hierarchy), Meshes with
#   MeshNormals, MeshTextureCoords, MeshVertexColors and MeshMaterialList
#   (inline or referenced Materials with TextureFilename).  DirectX is
#   left-handed: converted to Heaven3D's right-handed, Y-up space.
#   Skinning and animation sets are skipped (static model, like .glb).
# ---------------------------------------------------------------------------
class XData:
    __slots__ = ("type", "name", "values", "children", "ref")

    def __init__(self, type_, name=None):
        self.type = type_
        self.name = name
        self.values = []           # flat numbers / strings, in file order
        self.children = []
        self.ref = None            # {Name} reference

    def __repr__(self):
        return "<X %s %s %d values %d children>" % (self.type, self.name, len(self.values), len(self.children))


def _x_decompress(data):
    """MSZIP: after the 16-byte header, u32 total size, then blocks of
    (u16 uncompressed, u16 compressed incl. 'CK', 'CK' + raw deflate); each
    block may use the previous 32 KB as its dictionary."""
    import zlib
    out = bytearray(data[:16])
    off = 20
    prev = b""
    while off + 4 <= len(data):
        _usize, csize = struct.unpack_from("<HH", data, off)
        off += 4
        blk = data[off:off + csize]
        off += csize
        if blk[:2] == b"CK":
            blk = blk[2:]
        d = zlib.decompressobj(-15, zdict=prev[-32768:]) if prev else zlib.decompressobj(-15)
        chunk = d.decompress(blk) + d.flush()
        out += chunk
        prev = (prev + chunk)[-32768:]
    out[8:12] = b"txt " if data[8:12] == b"tzip" else b"bin "
    return bytes(out)


def _x_tokens_text(text):
    import re
    # strip comments (// and #), keep strings
    tok_re = re.compile(r'"[^"]*"|<[^>]*>|//[^\n]*|#[^\n]*|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?|[A-Za-z_][\w\-.]*|[{};,\[\]]')
    for t in tok_re.findall(text):
        if t.startswith("//") or t.startswith("#"):
            continue
        if t[0] == '"':
            yield ("str", t[1:-1])
        elif t[0] == "<":
            yield ("guid", t)
        elif t in "{}":
            yield (t, t)
        elif t in ";,[]":
            continue
        elif t[0].isdigit() or t[0] in "+-.":
            yield ("num", float(t) if any(c in t for c in ".eE") else int(t))
        else:
            yield ("name", t)


def _x_tokens_bin(data, float64):
    import numpy as _np
    off = 16
    n = len(data)
    fdt = "<f8" if float64 else "<f4"
    fsz = 8 if float64 else 4
    while off + 2 <= n:
        tok = struct.unpack_from("<H", data, off)[0]
        off += 2
        if tok == 1:                                   # NAME
            ln = struct.unpack_from("<I", data, off)[0]
            off += 4
            yield ("name", data[off:off + ln].decode("latin-1"))
            off += ln
        elif tok == 2:                                 # STRING (+ terminator token)
            ln = struct.unpack_from("<I", data, off)[0]
            off += 4
            yield ("str", data[off:off + ln].decode("latin-1"))
            off += ln + 2
        elif tok == 3:                                 # INTEGER
            yield ("num", struct.unpack_from("<I", data, off)[0])
            off += 4
        elif tok == 5:                                 # GUID
            off += 16
            yield ("guid", "")
        elif tok == 6:                                 # INTEGER_LIST
            cnt = struct.unpack_from("<I", data, off)[0]
            off += 4
            arr = _np.frombuffer(data, "<u4", cnt, off)
            off += 4 * cnt
            yield ("list", arr.astype(_np.int64))
        elif tok == 7:                                 # FLOAT_LIST
            cnt = struct.unpack_from("<I", data, off)[0]
            off += 4
            arr = _np.frombuffer(data, fdt, cnt, off)
            off += fsz * cnt
            yield ("list", arr.astype(_np.float64))
        elif tok == 10:
            yield ("{", "{")
        elif tok == 11:
            yield ("}", "}")
        elif tok in (12, 13, 14, 15, 16, 17, 18, 19, 20):
            continue                                   # ( ) [ ] < > . , ;
        elif tok == 31:                                # TEMPLATE keyword
            yield ("name", "template")
        elif 40 <= tok <= 53:                          # type keywords (only inside templates)
            yield ("name", "#type%d" % tok)
        else:
            continue


def _x_tree(tokens):
    """Tokens -> list of top-level XData objects (templates skipped)."""
    root = XData("__root__")
    stack = [root]
    pending = []                                       # names seen before a "{"
    skip = 0
    for kind, val in tokens:
        if skip:
            if kind == "{":
                skip += 1
            elif kind == "}":
                skip -= 1
            continue
        if kind == "{":
            if pending and pending[0] == "template":
                skip = 1
                pending = []
                continue
            if not pending:
                o = XData("__ref__")
                stack[-1].children.append(o)
                stack.append(o)
                continue
            o = XData(pending[0], pending[1] if len(pending) > 1 else None)
            stack[-1].children.append(o)
            stack.append(o)
            pending = []
        elif kind == "}":
            if pending and stack[-1].type == "__ref__":
                stack[-1].ref = pending[0]
            elif pending and stack[-1].values == [] and len(pending) == 1:
                # "{ Name }" reference inside a list (e.g. MeshMaterialList)
                r = XData("__ref__")
                r.ref = pending[0]
                stack[-1].children.append(r)
            pending = []
            if len(stack) > 1:
                o = stack.pop()
                if o.type == "__ref__" and o.ref is None and o.values:
                    o.ref = str(o.values[0])
        elif kind == "name":
            if stack[-1] is not root and stack[-1].type == "__ref__":
                stack[-1].ref = val
                continue
            pending.append(val)
            if len(pending) > 2:
                pending = pending[-2:]
        elif kind == "guid":
            continue
        else:
            if pending and stack[-1] is not root:
                pending = []
            if kind == "list":
                stack[-1].values.extend(val.tolist())
            else:
                stack[-1].values.append(val)
    return root.children


def _x_reader(vals):
    it = iter(vals)

    def nxt():
        return next(it)
    return nxt


def load_x_parts(path, log=None):
    """Read a DirectX .x file -> (parts, lights) like the glTF importer."""
    import numpy as _np
    with open(path, "rb") as fh:
        data = fh.read()
    if not data.startswith(b"xof "):
        raise ValueError("not a DirectX .x file")
    fmt = data[8:12]
    if fmt in (b"tzip", b"bzip"):
        data = _x_decompress(data)
        fmt = data[8:12]
    float64 = data[12:16] == b"0064"
    if fmt == b"bin ":
        objs = _x_tree(_x_tokens_bin(data, float64))
    else:
        objs = _x_tree(_x_tokens_text(data[16:].decode("latin-1")))

    named_mats = {}

    def collect(objs_):
        for o in objs_:
            if o.type == "Material" and o.name:
                named_mats[o.name] = o
            collect(o.children)
    collect(objs)
    base_dir = os.path.dirname(os.path.abspath(path))

    def tex(fname):
        fname = fname.replace("\\\\", "/").replace("\\", "/")
        hit = model_find_file(base_dir, fname, ("textures", "Textures"))
        if hit and not hit.lower().endswith(".dds"):
            return ("path", hit)
        # GameGuru ships .dds; use a .png/.jpg/.tga/.bmp of the same name if there is one
        stem = os.path.splitext(fname.split("/")[-1])[0]
        for ext in (".png", ".jpg", ".jpeg", ".tga", ".bmp"):
            hit = model_find_file(base_dir, stem + ext, ("textures", "Textures"))
            if hit:
                return ("path", hit)
        return None

    def mat_info(m):
        v = [float(x) for x in m.values if isinstance(x, (int, float))] if m is not None else []
        col = v[0:4] if len(v) >= 4 else [0.8, 0.8, 0.8, 1.0]
        power = v[4] if len(v) >= 5 else 20.0
        emis = v[8:11] if len(v) >= 11 else [0.0, 0.0, 0.0]
        slots = {}
        if m is not None:
            for c in m.children:
                if c.type in ("TextureFilename", "TextureFileName") and c.values:
                    d = tex(str(c.values[0]))
                    if d is not None:
                        slots["base_color"] = {"desc": d, "uvset": 0, "xf": None, "wrap": 0, "wrap_t": 0}
                        if max(col[:3]) < 0.02:
                            col = [1.0, 1.0, 1.0, col[3]]
        alpha = max(0.0, min(1.0, col[3]))
        if "base_color" in slots and max(emis) > 1e-4:
            # Direct3D's fixed-function stage modulates the emissive term by
            # the texture, so the glow takes the texture's colours too
            slots["emissive"] = dict(slots["base_color"])
        return {"name": (m.name if m is not None and m.name else "x_material"),
                "base_color": [col[0], col[1], col[2], alpha], "metallic": 0.0,
                "roughness": float(max(0.05, min(1.0, math.sqrt(2.0 / (max(power, 0.0) + 2.0))))),
                "emissive": list(emis), "emissive_intensity": 1.0,
                "base_color_tex": None, "metallic_roughness_tex": None, "normal_tex": None,
                "emissive_tex": None, "occlusion_tex": None, "double_sided": False,
                "alpha_mode": "BLEND" if alpha < 0.999 else "OPAQUE", "alpha_cutoff": 0.5,
                "_h3d": {"vertex_colors": False, "slots": slots}}

    LH = _np.diag([1.0, 1.0, -1.0, 1.0])               # left-handed -> right-handed
    parts = []

    def mesh_parts(mesh, M):
        r = _x_reader(mesh.values)
        try:
            nv = int(r())
            verts = _np.array([r() for _ in range(nv * 3)], _np.float64).reshape(nv, 3)
            nf = int(r())
            faces = []
            for _ in range(nf):
                k = int(r())
                faces.append([int(r()) for _ in range(k)])
        except StopIteration:
            return
        if nv == 0 or nf == 0:
            return
        normals = None
        nfaces = None
        uvs = None
        colors = None
        face_mat = None
        mats = []
        for c in mesh.children:
            rr = _x_reader(c.values)
            try:
                if c.type == "MeshNormals":
                    nn = int(rr())
                    normals = _np.array([rr() for _ in range(nn * 3)], _np.float64).reshape(nn, 3)
                    nnf = int(rr())
                    nfaces = []
                    for _ in range(nnf):
                        k = int(rr())
                        nfaces.append([int(rr()) for _ in range(k)])
                elif c.type == "MeshTextureCoords":
                    nt = int(rr())
                    uvs = _np.array([rr() for _ in range(nt * 2)], _np.float64).reshape(nt, 2)
                elif c.type == "MeshVertexColors":
                    nc = int(rr())
                    colors = _np.ones((nv, 4))
                    for _ in range(nc):
                        idx = int(rr())
                        rgba = [rr() for _ in range(4)]
                        if 0 <= idx < nv:
                            colors[idx] = rgba
                elif c.type == "MeshMaterialList":
                    nm = int(rr())
                    nfi = int(rr())
                    face_mat = [int(rr()) for _ in range(nfi)]
                    for mc in c.children:
                        if mc.type == "Material":
                            mats.append(mc)
                        elif mc.type == "__ref__" and mc.ref in named_mats:
                            mats.append(named_mats[mc.ref])
                    _ = nm
            except StopIteration:
                continue
        # corners: one per (face, corner), triangulated as fans
        corner_v, corner_n, tris, tri_mat = [], [], [], []
        for fi, f in enumerate(faces):
            base = len(corner_v)
            corner_v.extend(f)
            if nfaces is not None and fi < len(nfaces) and len(nfaces[fi]) == len(f):
                corner_n.extend(nfaces[fi])
            else:
                corner_n.extend(f if (normals is not None and len(normals) == nv) else [-1] * len(f))
            m = face_mat[fi] if face_mat is not None and len(face_mat) else 0
            if face_mat is not None and len(face_mat) == 1:
                m = face_mat[0]
            for k in range(1, len(f) - 1):
                tris.append((base, base + k + 1, base + k))     # reversed: LH -> RH winding
                tri_mat.append(m)
        if not tris:
            return
        cv = _np.clip(_np.asarray(corner_v, _np.int64), 0, nv - 1)
        tris = _np.asarray(tris, _np.int64)
        tri_mat = _np.asarray(tri_mat, _np.int64)
        W = LH @ M @ LH                                  # (also flips the matrix's handedness)
        P = verts[cv] @ (LH[:3, :3].T)
        P = P @ W[:3, :3].T + W[:3, 3]
        Nw = None
        if normals is not None:
            cn = _np.asarray(corner_n, _np.int64)
            if (cn >= 0).all():
                Nw = normals[_np.clip(cn, 0, len(normals) - 1)] @ LH[:3, :3].T
                Nw = Nw @ _np.linalg.inv(W[:3, :3]).T
                Nw /= _np.maximum(_np.linalg.norm(Nw, axis=1, keepdims=True), 1e-12)
        UV = uvs[_np.clip(cv, 0, len(uvs) - 1)] if (uvs is not None and len(uvs) == nv) else None
        COL = colors[cv] if colors is not None else None
        name = mesh.name or "x_mesh"
        for mi in sorted(set(tri_mat.tolist())):
            tsel = tris[tri_mat == mi]
            corners = _np.unique(tsel.ravel())
            remap = _np.full(len(cv), -1, _np.int64)
            remap[corners] = _np.arange(len(corners))
            p_pos = P[corners].astype(_np.float32)
            p_idx = remap[tsel].ravel().astype(_np.uint32)
            p_nrm = Nw[corners].astype(_np.float32) if Nw is not None else _fbx_flat_normals(p_pos, p_idx)
            info = mat_info(mats[mi] if 0 <= mi < len(mats) else (mats[0] if mats else None))
            p_col = None
            if COL is not None:
                p_col = _np.clip(COL[corners], 0, 1).astype(_np.float32)
                info["_h3d"]["vertex_colors"] = True
            parts.append({"name": name if not parts else "%s_%d" % (name, len(parts)),
                          "positions": p_pos, "normals": p_nrm,
                          "uv0": UV[corners].astype(_np.float32) if UV is not None else None,  # DirectX v already points down
                          "uv1": None, "colors": p_col, "tangents": None, "indices": p_idx,
                          "material_info": info})

    def walk(objs_, M):
        for o in objs_:
            if o.type == "Frame":
                L = _np.eye(4)
                for c in o.children:
                    if c.type == "FrameTransformMatrix" and len(c.values) >= 16:
                        # DirectX row-vector matrix -> column-vector convention
                        L = _np.array([float(x) for x in c.values[:16]], _np.float64).reshape(4, 4).T
                walk(o.children, M @ L)
            elif o.type == "Mesh":
                mesh_parts(o, M)
    walk(objs, _np.eye(4))
    if log is not None:
        try:
            log.info(".x: %d mesh part(s)" % len(parts))
        except Exception:
            pass
    return parts, []


def model_texture_files(path):
    """Texture files an .fbx / .x model uses from disk (for export /
    portable projects): [(path relative to the model, absolute path)]."""
    ext = os.path.splitext(path)[1].lower()
    try:
        parts, _l = (load_fbx_parts if ext == ".fbx" else load_x_parts)(path)
    except Exception:
        return []
    d = os.path.dirname(os.path.abspath(path))
    out, seen = [], set()
    for p in parts:
        for s in (p["material_info"].get("_h3d", {}).get("slots", {}) or {}).values():
            desc = s.get("desc")
            if desc and desc[0] == "path" and desc[1] not in seen:
                seen.add(desc[1])
                rel = os.path.relpath(desc[1], d) if os.path.abspath(desc[1]).startswith(d) else os.path.basename(desc[1])
                out.append((rel.replace(os.sep, "/"), desc[1]))
    return out
