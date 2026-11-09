# @heaven3d-part importers/fbx.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# ---------------------------------------------------------------------------
# [FBX IMPORTER]  Autodesk .fbx (binary and ASCII, FBX 7.x) -> model parts
#
#   Written for Heaven3D with only the standard library + NumPy (no FBX
#   SDK).  Produces the same "parts" the glTF / .blend importers produce,
#   so an .fbx becomes one locked, selectable object exactly like a .glb:
#
#     * every Model's mesh, with its full FBX transform (translation,
#       pre/post rotation, pivots, offsets, rotation order, geometric
#       transform) baked in, hierarchy included
#     * the file's axis system and unit converted to Heaven3D (Y up, metres)
#     * polygons triangulated; normals / UVs / vertex colours with every
#       mapping mode (by vertex, by polygon vertex, by polygon, all same;
#       direct or indexed); one part per material
#     * materials: Lambert / Phong (diffuse, emissive, opacity, shininess
#       -> roughness) and 3ds Max PBR (base colour, metalness, roughness),
#       with diffuse / normal / emissive / opacity textures, embedded in
#       the file or found next to it (also in <name>.fbm/ and textures/)
#
#   Not imported: skinning and animation (static model, like .glb).
# ---------------------------------------------------------------------------
class FbxNode:
    __slots__ = ("name", "props", "children")

    def __init__(self, name, props=None, children=None):
        self.name = name
        self.props = props if props is not None else []
        self.children = children if children is not None else []

    def find(self, name):
        for c in self.children:
            if c.name == name:
                return c
        return None

    def find_all(self, name):
        return [c for c in self.children if c.name == name]

    def __repr__(self):
        return "<FbxNode %s %d props %d children>" % (self.name, len(self.props), len(self.children))


_FBX_MAGIC = b"Kaydara FBX Binary  \x00"


def fbx_parse_binary(data):
    import zlib
    import numpy as _np
    if not data.startswith(_FBX_MAGIC):
        raise ValueError("not a binary FBX file (or a damaged one)")
    version = struct.unpack_from("<I", data, 23)[0]
    wide = version >= 7500
    hdr = struct.Struct("<QQQ" if wide else "<III")
    arr_kinds = {ord("f"): ("<f4", 4), ord("d"): ("<f8", 8), ord("l"): ("<i8", 8),
                 ord("i"): ("<i4", 4), ord("b"): ("u1", 1), ord("c"): ("u1", 1)}

    def read_prop(off):
        t = data[off]
        off += 1
        if t == ord("Y"):
            return struct.unpack_from("<h", data, off)[0], off + 2
        if t == ord("C"):
            return bool(data[off]), off + 1
        if t == ord("I"):
            return struct.unpack_from("<i", data, off)[0], off + 4
        if t == ord("F"):
            return struct.unpack_from("<f", data, off)[0], off + 4
        if t == ord("D"):
            return struct.unpack_from("<d", data, off)[0], off + 8
        if t == ord("L"):
            return struct.unpack_from("<q", data, off)[0], off + 8
        if t in arr_kinds:
            n, enc, clen = struct.unpack_from("<III", data, off)
            off += 12
            raw = data[off:off + clen]
            off += clen
            if enc == 1:
                raw = zlib.decompress(raw)
            dt, sz = arr_kinds[t]
            return _np.frombuffer(raw, dtype=dt, count=n).copy(), off
        if t in (ord("S"), ord("R")):
            n = struct.unpack_from("<I", data, off)[0]
            off += 4
            b = data[off:off + n]
            return (b.decode("utf-8", "replace") if t == ord("S") else bytes(b)), off + n
        raise ValueError("FBX: unknown property type %r at %d" % (chr(t), off - 1))

    def read_node(off):
        end, nprops, _plen = hdr.unpack_from(data, off)
        off += hdr.size
        nlen = data[off]
        off += 1
        if end == 0:
            return None, off
        name = data[off:off + nlen].decode("ascii", "replace")
        off += nlen
        props = []
        for _ in range(nprops):
            v, off = read_prop(off)
            props.append(v)
        children = []
        while off < end:
            # a nested list ends with a zeroed "null record"
            if data[off:off + hdr.size + 1] == b"\x00" * (hdr.size + 1):
                off += hdr.size + 1
                break
            c, off = read_node(off)
            if c is None:
                break
            children.append(c)
        return FbxNode(name, props, children), end

    root = FbxNode("__root__")
    off = 27
    n = len(data)
    while off < n - hdr.size:
        node, off2 = read_node(off)
        if node is None:
            break
        root.children.append(node)
        off = off2
    return root, version


def fbx_parse_ascii(text):
    """FBX ASCII: `Name: v, v, ... { children }` with `*N { a: ... }` arrays."""
    import re
    import numpy as _np
    tok_re = re.compile(r'"(?:[^"\\]|\\.)*"|;[^\n]*|[A-Za-z_][\w|\-]*\s*:|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?|[{}*,]|\S')
    toks = [t for t in tok_re.findall(text) if not t.startswith(";")]
    i = 0
    n = len(toks)

    def value(t):
        if t.startswith('"'):
            return t[1:-1].replace("&quot;", '"')
        try:
            if any(c in t for c in ".eE") and not t.isalpha():
                return float(t)
            return int(t)
        except ValueError:
            return t

    def parse_body():
        nonlocal i
        children = []
        while i < n and toks[i] != "}":
            t = toks[i]
            if not t.endswith(":"):
                i += 1
                continue
            name = t[:-1].strip()
            i += 1
            props = []
            node_children = []
            while i < n:
                t = toks[i]
                if t.endswith(":") or t == "}":
                    break
                if t == "{":
                    i += 1
                    node_children = parse_body()
                    i += 1                     # "}"
                    break
                if t == "*":                   # *count { a: 1,2,3 }
                    i += 2                     # "*", count
                    if i < n and toks[i] == "{":
                        i += 1
                        vals = []
                        while i < n and toks[i] != "}":
                            tt = toks[i]
                            if not tt.endswith(":") and tt != ",":
                                vals.append(value(tt))
                            i += 1
                        i += 1                 # "}"
                        arr = _np.asarray(vals, dtype=_np.float64)
                        if arr.size and _np.all(_np.mod(arr, 1) == 0) and name in (
                                "PolygonVertexIndex", "Edges", "NormalsIndex", "NormalIndex", "UVIndex",
                                "Materials", "ColorIndex", "Indexes", "KeyTime"):
                            arr = arr.astype(_np.int64)
                        props.append(arr)
                        break
                    continue
                if t != ",":
                    props.append(value(t))
                i += 1
            children.append(FbxNode(name, props, node_children))
        return children

    root = FbxNode("__root__", [], parse_body())
    ver = 7000
    hdr = root.find("FBXHeaderExtension")
    if hdr is not None:
        v = hdr.find("FBXVersion")
        if v is not None and v.props:
            ver = int(v.props[0])
    return root, ver


def _fbx_p70(node):
    """Properties70 / Properties60 of a node -> {name: [values...]}"""
    out = {}
    if node is None:
        return out
    for key in ("Properties70", "Properties60"):
        p = node.find(key)
        if p is None:
            continue
        for c in p.children:
            if len(c.props) >= 4:
                # P: "name", "type", "label", "flags", values...
                out[str(c.props[0])] = c.props[4:] if c.name == "P" else c.props[3:]
            elif c.props:
                out[str(c.props[0])] = c.props[1:]
    return out


def _fbx_vec(p, key, default):
    v = p.get(key)
    if not v or len(v) < len(default):
        return list(default)
    try:
        return [float(x) for x in v[:len(default)]]
    except (TypeError, ValueError):
        return list(default)


def _fbx_name(prop):
    s = str(prop)
    for sep in ("\x00\x01", "::"):
        if sep in s:
            a, b = s.split(sep, 1)
            return a if sep == "\x00\x01" else b
    return s


def _fbx_euler(deg, order):
    """FBX euler (degrees) -> 3x3.  order 0..5 = XYZ, XZY, YZX, YXZ, ZXY, ZYX:
    the first letter is applied first."""
    import numpy as _np
    x, y, z = [math.radians(a) for a in deg]
    cx, sx, cy, sy, cz, sz = math.cos(x), math.sin(x), math.cos(y), math.sin(y), math.cos(z), math.sin(z)
    RX = _np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    RY = _np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    RZ = _np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    m = {"X": RX, "Y": RY, "Z": RZ}
    seq = ("XYZ", "XZY", "YZX", "YXZ", "ZXY", "ZYX")[int(order) if 0 <= int(order) <= 5 else 0]
    R = _np.eye(3)
    for ax in seq:                  # applied first = rightmost in the product
        R = m[ax] @ R
    return R


def _fbx_m4(R=None, t=None, s=None):
    import numpy as _np
    M = _np.eye(4)
    if R is not None:
        M[:3, :3] = R
    if s is not None:
        M[:3, :3] = M[:3, :3] @ _np.diag(s)
    if t is not None:
        M[:3, 3] = t
    return M


def _fbx_local_matrix(p):
    """The full FBX node transform:
    T * Roff * Rp * Rpre * R * Rpost^-1 * Rp^-1 * Soff * Sp * S * Sp^-1"""
    import numpy as _np
    order = int((p.get("RotationOrder") or [0])[0] or 0)
    T = _fbx_m4(t=_fbx_vec(p, "Lcl Translation", (0, 0, 0)))
    R = _fbx_m4(R=_fbx_euler(_fbx_vec(p, "Lcl Rotation", (0, 0, 0)), order))
    S = _fbx_m4(s=_fbx_vec(p, "Lcl Scaling", (1, 1, 1)))
    Rpre = _fbx_m4(R=_fbx_euler(_fbx_vec(p, "PreRotation", (0, 0, 0)), 0))
    Rpost = _fbx_m4(R=_fbx_euler(_fbx_vec(p, "PostRotation", (0, 0, 0)), 0))
    Roff = _fbx_m4(t=_fbx_vec(p, "RotationOffset", (0, 0, 0)))
    Rp = _fbx_m4(t=_fbx_vec(p, "RotationPivot", (0, 0, 0)))
    Soff = _fbx_m4(t=_fbx_vec(p, "ScalingOffset", (0, 0, 0)))
    Sp = _fbx_m4(t=_fbx_vec(p, "ScalingPivot", (0, 0, 0)))
    return (T @ Roff @ Rp @ Rpre @ R @ _np.linalg.inv(Rpost) @ _np.linalg.inv(Rp)
            @ Soff @ Sp @ S @ _np.linalg.inv(Sp))


def _fbx_geometric_matrix(p):
    return (_fbx_m4(t=_fbx_vec(p, "GeometricTranslation", (0, 0, 0)))
            @ _fbx_m4(R=_fbx_euler(_fbx_vec(p, "GeometricRotation", (0, 0, 0)), 0))
            @ _fbx_m4(s=_fbx_vec(p, "GeometricScaling", (1, 1, 1))))


def _fbx_layer(geom, name, data_key, index_keys):
    """-> (values ndarray (n, k), mapping, per-reference indices or None)"""
    import numpy as _np
    el = geom.find(name)
    if el is None:
        return None
    mapping = "ByPolygonVertex"
    ref = "Direct"
    m = el.find("MappingInformationType")
    if m is not None and m.props:
        mapping = str(m.props[0])
    r = el.find("ReferenceInformationType")
    if r is not None and r.props:
        ref = str(r.props[0])
    d = el.find(data_key)
    if d is None or not d.props:
        return None
    vals = _np.asarray(d.props[0], dtype=_np.float64)
    idx = None
    if ref in ("IndexToDirect", "Index"):
        for k in index_keys:
            ie = el.find(k)
            if ie is not None and ie.props:
                idx = _np.asarray(ie.props[0], dtype=_np.int64)
                break
    return vals, mapping, idx


def _fbx_expand(vals, k, mapping, idx, poly_vi, poly_of_corner):
    """Per-corner values (n_corners, k) from an FBX layer."""
    import numpy as _np
    vals = vals.reshape(-1, k)
    n = len(poly_vi)
    if mapping in ("ByPolygonVertex",):
        sel = _np.arange(n)
    elif mapping in ("ByVertice", "ByVertex", "ByControlPoint"):
        sel = poly_vi
    elif mapping == "ByPolygon":
        sel = poly_of_corner
    elif mapping == "AllSame":
        sel = _np.zeros(n, _np.int64)
    else:
        return None
    if idx is not None:
        if len(idx) == 0:
            return None
        sel = idx[_np.clip(sel, 0, len(idx) - 1)]
    if len(vals) == 0:
        return None
    sel = _np.clip(sel, 0, len(vals) - 1)
    return vals[sel]


def load_fbx_parts(path, log=None):
    """Read an .fbx -> (parts, lights) in the format of the glTF importer."""
    import numpy as _np
    with open(path, "rb") as fh:
        data = fh.read()
    if data.startswith(b"Kaydara FBX Binary"):
        root, version = fbx_parse_binary(data)
    else:
        root, version = fbx_parse_ascii(data.decode("utf-8", "replace"))
    if version < 7000:
        raise ValueError("FBX %d.%d files aren't supported (FBX 2011 / 7.0 or newer is needed; "
                         "re-export from your 3D app)" % (version // 1000, version % 1000 // 100))
    gs = _fbx_p70(root.find("GlobalSettings"))

    def gi(key, default):
        v = gs.get(key)
        try:
            return int(v[0]) if v else default
        except (TypeError, ValueError):
            return default
    unit = 1.0
    try:
        unit = float((gs.get("UnitScaleFactor") or [1.0])[0]) / 100.0      # file units -> metres
    except (TypeError, ValueError):
        pass
    if unit <= 0:
        unit = 0.01
    axes = _np.eye(3)
    up = axes[gi("UpAxis", 1)] * gi("UpAxisSign", 1)
    front = axes[gi("FrontAxis", 2)] * gi("FrontAxisSign", 1)
    coord = axes[gi("CoordAxis", 0)] * gi("CoordAxisSign", 1)
    B = _np.vstack([coord, up, front])            # file axes -> (right, up, front) = Heaven3D x, y, z
    flip = _np.linalg.det(B) < 0                  # mirrored (left-handed) source: fix the winding too
    C = _np.eye(4)
    C[:3, :3] = B * unit

    objs = root.find("Objects")
    if objs is None:
        return [], []
    by_id = {}
    for o in objs.children:
        if o.props:
            by_id[o.props[0]] = o
    children_of = {}           # parent id -> [(child id, prop name or None)]
    parent_of = {}             # child id -> parent model id (OO)
    conns = root.find("Connections")
    for c in (conns.children if conns is not None else ()):
        if len(c.props) < 3:
            continue
        kind, child, parent = str(c.props[0]), c.props[1], c.props[2]
        pname = str(c.props[3]) if len(c.props) > 3 else None
        children_of.setdefault(parent, []).append((child, pname if kind == "OP" else None))
        if kind == "OO":
            co, po = by_id.get(child), by_id.get(parent)
            if co is not None and co.name == "Model" and (po is None or po.name == "Model"):
                parent_of[child] = parent

    world_cache = {}

    def world(mid):
        if mid in world_cache:
            return world_cache[mid]
        node = by_id.get(mid)
        M = _fbx_local_matrix(_fbx_p70(node)) if node is not None else _np.eye(4)
        par = parent_of.get(mid)
        if par is not None and par in by_id and par != mid:
            M = world(par) @ M
        world_cache[mid] = M
        return M

    base_dir = os.path.dirname(os.path.abspath(path))
    stem = os.path.splitext(os.path.basename(path))[0]

    def tex_desc(tex_id):
        tex = by_id.get(tex_id)
        if tex is None:
            return None
        names = []
        for key in ("RelativeFilename", "FileName", "Filename"):
            e = tex.find(key)
            if e is not None and e.props and str(e.props[0]).strip():
                names.append(str(e.props[0]))
        # embedded image: a Video connected to this Texture with Content
        for cid, _pn in children_of.get(tex_id, ()):
            vid = by_id.get(cid)
            if vid is not None and vid.name == "Video":
                cont = vid.find("Content")
                raw = None
                if cont is not None and cont.props:
                    raw = cont.props[-1]
                    if isinstance(raw, str):                 # ASCII FBX: base64 text
                        try:
                            import base64
                            raw = base64.b64decode(raw)
                        except Exception:
                            raw = None
                if isinstance(raw, (bytes, bytearray)) and len(raw) > 16:
                    return ("bytes", bytes(raw))
                for key in ("RelativeFilename", "FileName", "Filename"):
                    e = vid.find(key)
                    if e is not None and e.props and str(e.props[0]).strip():
                        names.append(str(e.props[0]))
        for nm in names:
            hit = model_find_file(base_dir, nm, (stem + ".fbm", "textures", "Textures",
                                                 os.path.join("..", "textures")))
            if hit:
                return ("path", hit)
        return None

    def material_info(mat_id):
        mat = by_id.get(mat_id)
        p = _fbx_p70(mat)
        name = _fbx_name(mat.props[1]) if mat is not None and len(mat.props) > 1 else "fbx_material"
        diff = _fbx_vec(p, "DiffuseColor", (0.8, 0.8, 0.8))
        if "Diffuse" in p and "DiffuseColor" not in p:
            diff = _fbx_vec(p, "Diffuse", (0.8, 0.8, 0.8))
        dfac = _fbx_vec(p, "DiffuseFactor", (1.0,))[0]
        emis = _fbx_vec(p, "EmissiveColor", (0.0, 0.0, 0.0))
        efac = _fbx_vec(p, "EmissiveFactor", (1.0,))[0]
        opacity = 1.0
        if "Opacity" in p:
            opacity = _fbx_vec(p, "Opacity", (1.0,))[0]
        elif "TransparencyFactor" in p:
            tf = _fbx_vec(p, "TransparencyFactor", (0.0,))[0]
            tc = _fbx_vec(p, "TransparentColor", (1.0, 1.0, 1.0))
            opacity = 1.0 - tf * (sum(tc) / 3.0)
        shin = _fbx_vec(p, "ShininessExponent", _fbx_vec(p, "Shininess", (20.0,)))[0]
        rough = max(0.05, min(1.0, math.sqrt(2.0 / (max(shin, 0.0) + 2.0))))
        metal = 0.0
        base = [diff[0] * dfac, diff[1] * dfac, diff[2] * dfac]
        pbr_emis, pbr_ew = None, 1.0
        # 3ds Max Physical / PBR materials
        for key, val in p.items():
            low = key.lower()
            try:
                if low.endswith("|base_color") or low.endswith("|basecolor"):
                    base = [float(x) for x in val[:3]]
                elif low.endswith("|metalness") or low.endswith("|metallic"):
                    metal = float(val[0])
                elif low.endswith("|roughness"):
                    rough = float(val[0])
                elif low.endswith("|glossiness"):
                    rough = 1.0 - float(val[0])
                elif low.endswith("|emit_color") or low.endswith("|emission_color"):
                    # Max writes the legacy EmissiveColor pre-multiplied by
                    # pi; the PBR key carries the colour the artist chose
                    pbr_emis = [float(x) for x in val[:3]]
                elif low.endswith("|emission"):
                    pbr_ew = float(val[0])
            except (TypeError, ValueError, IndexError):
                pass
        if pbr_emis is not None:
            emis, efac = pbr_emis, pbr_ew
        slots = {}
        for cid, pname in children_of.get(mat_id, ()):
            if pname is None or by_id.get(cid) is None or by_id[cid].name != "Texture":
                continue
            low = pname.lower()
            d = tex_desc(cid)
            if d is None:
                continue
            ref = {"desc": d, "uvset": 0, "xf": None, "wrap": 0, "wrap_t": 0}
            if ("diffuse" in low or "base_color" in low or "basecolor" in low) and "factor" not in low:
                slots.setdefault("base_color", ref)
            elif "normal" in low or "bump" in low:
                slots.setdefault("normal", dict(ref, scale=1.0))
            elif "emissive" in low and "factor" not in low:
                slots.setdefault("emissive", ref)
        if "base_color" in slots:
            base = [1.0, 1.0, 1.0] if max(base) < 0.02 else base
        info = {"name": name, "base_color": [base[0], base[1], base[2], max(0.0, min(1.0, opacity))],
                "metallic": float(max(0.0, min(1.0, metal))), "roughness": float(max(0.04, min(1.0, rough))),
                "emissive": [emis[0] * efac, emis[1] * efac, emis[2] * efac], "emissive_intensity": 1.0,
                "base_color_tex": None, "metallic_roughness_tex": None, "normal_tex": None,
                "emissive_tex": None, "occlusion_tex": None, "double_sided": False,
                "alpha_mode": "BLEND" if opacity < 0.999 else "OPAQUE", "alpha_cutoff": 0.5,
                "_h3d": {"vertex_colors": False, "slots": slots}}
        if "emissive" in slots and max(info["emissive"]) < 1e-4:
            info["emissive"] = [1.0, 1.0, 1.0]
        return info

    parts = []
    for mid, model in list(by_id.items()):
        if model.name != "Model":
            continue
        geoms = [cid for cid, _pn in children_of.get(mid, ())
                 if by_id.get(cid) is not None and by_id[cid].name == "Geometry"
                 and (len(by_id[cid].props) < 3 or str(by_id[cid].props[2]) == "Mesh")]
        if not geoms:
            continue
        mats = [cid for cid, _pn in children_of.get(mid, ())
                if by_id.get(cid) is not None and by_id[cid].name == "Material"]
        mp = _fbx_p70(model)
        if mp.get("Visibility") and float(mp["Visibility"][0] or 1) == 0.0:
            continue
        M = C @ world(mid) @ _fbx_geometric_matrix(mp)
        Nm = _np.linalg.inv(M[:3, :3]).T
        mname = _fbx_name(model.props[1]) if len(model.props) > 1 else "fbx_mesh"
        for gid in geoms:
            g = by_id[gid]
            ve = g.find("Vertices")
            pe = g.find("PolygonVertexIndex")
            if ve is None or pe is None or not ve.props or not pe.props:
                continue
            verts = _np.asarray(ve.props[0], _np.float64).reshape(-1, 3)
            pvi = _np.asarray(pe.props[0], _np.int64)
            if len(pvi) == 0 or len(verts) == 0:
                continue
            ends = pvi < 0
            vi = _np.where(ends, -pvi - 1, pvi)
            # polygon of every corner, and fan triangulation
            pid = _np.concatenate([[0], _np.cumsum(ends[:-1])]).astype(_np.int64)
            starts = _np.concatenate([[0], _np.nonzero(ends)[0][:-1] + 1]) if ends.any() else _np.array([0])
            tri = []
            end_idx = _np.nonzero(ends)[0]
            for s0, e0 in zip(starts, end_idx):
                for k in range(s0 + 1, e0):
                    tri.append((s0, k, k + 1))
            if not tri:
                continue
            tri = _np.asarray(tri, _np.int64)
            if flip:
                tri = tri[:, [0, 2, 1]]
            vi = _np.clip(vi, 0, len(verts) - 1)
            pos = verts[vi]
            nrm = None
            ln = _fbx_layer(g, "LayerElementNormal", "Normals", ("NormalsIndex", "NormalIndex"))
            if ln is not None:
                nrm = _fbx_expand(ln[0], 3, ln[1], ln[2], vi, pid)
            uv = None
            lu = _fbx_layer(g, "LayerElementUV", "UV", ("UVIndex",))
            if lu is not None:
                uv = _fbx_expand(lu[0], 2, lu[1], lu[2], vi, pid)
            col = None
            lc = _fbx_layer(g, "LayerElementColor", "Colors", ("ColorIndex", "ColorsIndex"))
            if lc is not None:
                col = _fbx_expand(lc[0], 4, lc[1], lc[2], vi, pid)
            # material of each triangle
            tri_mat = _np.zeros(len(tri), _np.int64)
            lm = g.find("LayerElementMaterial")
            if lm is not None:
                me = lm.find("Materials")
                mapping = lm.find("MappingInformationType")
                mapping = str(mapping.props[0]) if mapping is not None and mapping.props else "AllSame"
                if me is not None and me.props and len(me.props[0]):
                    mi = _np.asarray(me.props[0], _np.int64)
                    if mapping == "ByPolygon":
                        tri_mat = mi[_np.clip(pid[tri[:, 0]], 0, len(mi) - 1)]
                    else:
                        tri_mat = _np.full(len(tri), int(mi[0]), _np.int64)
            # to world (Heaven3D) space
            P = pos @ M[:3, :3].T + M[:3, 3]
            if nrm is not None:
                Nw = nrm @ Nm.T
                Nw /= _np.maximum(_np.linalg.norm(Nw, axis=1, keepdims=True), 1e-12)
            else:
                Nw = None
            for m_index in sorted(set(tri_mat.tolist())):
                tsel = tri[tri_mat == m_index]
                corners = _np.unique(tsel.ravel())
                remap = _np.full(len(vi), -1, _np.int64)
                remap[corners] = _np.arange(len(corners))
                p_pos = P[corners].astype(_np.float32)
                p_idx = remap[tsel].ravel().astype(_np.uint32)
                if Nw is not None:
                    p_nrm = Nw[corners].astype(_np.float32)
                else:
                    p_nrm = _fbx_flat_normals(p_pos, p_idx)
                p_uv = None
                if uv is not None:
                    p_uv = uv[corners].astype(_np.float32).copy()
                    p_uv[:, 1] = 1.0 - p_uv[:, 1]                 # FBX v points up, glTF down
                p_col = None
                if col is not None:
                    p_col = _np.clip(col[corners], 0, 1).astype(_np.float32)
                mat_id = mats[m_index] if 0 <= m_index < len(mats) else (mats[0] if mats else None)
                info = material_info(mat_id) if mat_id is not None else material_info(None)
                if p_col is not None:
                    info["_h3d"]["vertex_colors"] = True
                parts.append({"name": mname if len(parts) == 0 else "%s_%d" % (mname, len(parts)),
                              "positions": p_pos, "normals": p_nrm, "uv0": p_uv, "uv1": None,
                              "colors": p_col, "tangents": None, "indices": p_idx,
                              "material_info": info})
    if log is not None:
        try:
            log.info("FBX %d.%d: %d mesh part(s)" % (version // 1000, version % 1000 // 100, len(parts)))
        except Exception:
            pass
    return parts, []


def model_find_file(base_dir, name, subdirs=()):
    """Find a texture a model refers to: the path as written, next to the
    model, or in its texture folders; letter case is ignored (models made
    on Windows often say MP5SIL.BMP for mp5sil.bmp)."""
    name = str(name).replace("\\", "/").strip()
    if not name:
        return None
    base = name.split("/")[-1]
    dirs = [base_dir] + [os.path.join(base_dir, d) for d in subdirs]
    cands = [name, os.path.join(base_dir, name)] + [os.path.join(d, base) for d in dirs]
    for c in cands:
        if os.path.isfile(c):
            return os.path.abspath(c)
    low = base.lower()
    for d in dirs:
        try:
            for f in os.listdir(d):
                if f.lower() == low and os.path.isfile(os.path.join(d, f)):
                    return os.path.abspath(os.path.join(d, f))
        except OSError:
            continue
    return None


def _fbx_flat_normals(pos, idx):
    """Smooth-ish normals for meshes stored without any (area-weighted)."""
    import numpy as _np
    t = idx.reshape(-1, 3)
    a, b, c = pos[t[:, 0]], pos[t[:, 1]], pos[t[:, 2]]
    fn = _np.cross(b - a, c - a)
    n = _np.zeros_like(pos)
    for k in range(3):
        _np.add.at(n, t[:, k], fn)
    n /= _np.maximum(_np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    return n.astype(_np.float32)
