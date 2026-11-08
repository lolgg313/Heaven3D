# @heaven3d-part importers/blend_classic.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# #############################################################################
# SECTION 2B  —  BLEND IMPORTER  (from scratch — pure Python, no deps)
# #############################################################################


class BlendImporter:
    """
    Blender binary (.blend) importer — from scratch, pure Python
    (stdlib + numpy ONLY: no trimesh, no PIL, no bpy, no subprocess,
    no Blender installation required).

    Supported files: Blender 2.80 - 4.x, 32/64-bit pointers,
    little/big endian.  The parser NEVER hardcodes byte offsets: every
    field is resolved BY NAME through the file's own SDNA registry (the
    DNA1 block), so DNA layout changes between Blender versions are
    handled automatically.

    Format structures relied on (Blender blenloader readfile.cc,
    makesdna DNA_*.h):
      Header : "BLENDER" ptrsize('_'/'-') endian('v'/'V') version(3s)
      BHead  : code(4s) size(u32) old_addr(ptr) sdna_index(u32) count(u32)
      ID.name char[66]  (2-letter prefix stripped: OB/ME/MA/IM/LA/SC...)
      ListBase { *first, *last }
      Scene.view_layers -> ViewLayer.object_bases -> Base.object
      Object : type, data, parent, parentinv[4][4], loc[3], rot[3],
               quat[4] (w,x,y,z), rotmode, scale[3] (legacy: size[3]),
               modifiers (ListBase — NOT evaluated, warn only)
      Mesh   : totvert/totloop/totpoly, vdata/ldata/pdata (embedded
               CustomData), mat (Material**), totcol, legacy direct
               mvert/mloop/mpoly/mloopuv/mloopcol/mface, 4.5+ face
               storage poly_offset_indices (int32 offset table,
               totpoly+1 entries, no MPoly), and 4.x name pointers
               active/default_color_attribute + active/default_
               uv_map_attribute.  4.x generic attributes are matched by
               NAME first (".corner_vert", "position",
               "material_index", "sharp_face", UV/colour names), then by
               known type ids, always with a block-size plausibility
               check — 4.x reused CustomData type ids between releases.
      CustomData      : layers, totlayer, maxlayer, typemap
      CustomDataLayer : type, flag/active/active_rnd/active_clone/
                        active_mask, name[64], data (void* — 2.8x-3.x;
                        4.x files use order-based block pairing instead)
      MVert.co/no, MLoop.v/e, MPoly.loopstart/totloop/mat_nr/flag,
      MFace.v1..v4/mat_nr, MLoopUV.uv, MLoopCol.r/g/b/a
      Material : r/g/b/a, metallic, roughness, alpha_threshold,
                 use_nodes, nodetree, blend_method (2.8-4.1),
                 surface_render_method (4.2+), use_backface_culling
      bNodeTree.nodes/links, bNode.idname/name/inputs/outputs/id,
      bNodeSocket.name/identifier/default_value,
      bNodeLink.fromnode/fromsock/tonode/tosock,
      bNodeSocketValueFloat/RGBA/Vector/Int.value
      Image : filepath[1024] ("//" = relative to the .blend),
              packedfile (PackedFile.size/data), packedfiles (3.x)
      Light/Lamp : type, r/g/b, energy, spotsize, spotblend, dist, mode

    Behaviour contract — IDENTICAL to GlbImporter.build_nodes:
      * one root NodeData per mesh OBJECT; matrix/translation/rotation/
        scale are all None and children=[] because the world transform
        (including Blender Z-up -> engine Y-up conversion
        engine=(bx,bz,-by)) is BAKED into the vertex data via
        GlbImporter._bake_world_transform.
      * one MeshData per material slot of the object (GLB-primitive
        parity): load_model_into keeps part 0 on the root node and turns
        the rest into hidden child part nodes — no matter how many
        sub-meshes, it stays ONE object.
      * output geometry is per-corner TRIANGLE SOUP (fan triangulation),
        indices=None — Mesh.upload handles non-indexed meshes.

    Limitations:
      * modifiers are NOT evaluated — apply them in Blender first.
      * the engine has no vertex-colour vertex channel: when a colour
        layer exists, its average linear RGB is multiplied into the
        material base_color (only if the material has no base-colour
        texture); otherwise the vertex colours are skipped (info log).
      * cameras are skipped (info log); other non-mesh, non-lamp objects
        are skipped (debug log).
      * AREA lights are mapped to point lights (warn).
      * watts -> engine-intensity conversions are documented heuristics
        (see attach_imported_lights).

    Robustness: ANY hard failure raises ValueError from build_nodes();
    load_model_into catches it and returns False, exactly like a failed
    GLB.  Corrupt sub-structures degrade with Log.warn + fallbacks where
    possible; garbage counts are clamped, never trusted.
    """

    # Reuse the GlbImporter data contract verbatim.
    MeshData = GlbImporter.MeshData
    NodeData = GlbImporter.NodeData

    # Light descriptors collected by build_nodes(), consumed by
    # attach_imported_lights() from load_model_into.
    _pending_lights: List[dict] = []

    # ---- CustomData layer type ids (classic + 4.x generic attributes) ----
    _CD_MVERT = 0
    _CD_MFACE = 4
    _CD_MLOOP = 6
    _CD_NORMAL = 8
    _CD_MPOLY = 10
    _CD_MLOOPUV = 11
    _CD_MLOOPCOL = 12
    _CD_CUSTOMLOOPNORMAL = 26
    _CD_PROP_BOOL = 30
    _CD_PROP_FLOAT2 = 33
    _CD_PROP_FLOAT3 = 34
    _CD_PROP_INT32 = 35
    _CD_PROP_COLOR = 36
    _CD_PROP_BYTE_COLOR = 38

    _OB_MESH = 1
    _OB_LAMP = 10
    _OB_CAMERA = 11

    _ME_SMOOTH = 1

    # Blender Z-up -> engine Y-up right-handed: engine = (bx, bz, -by).
    _ZUP_TO_YUP = np.array([[1.0, 0.0, 0.0, 0.0],
                            [0.0, 0.0, 1.0, 0.0],
                            [0.0, -1.0, 0.0, 0.0],
                            [0.0, 0.0, 0.0, 1.0]], dtype=np.float64)

    _ID_PREFIXES = ("OB", "ME", "MA", "IM", "LA", "SC", "WO", "CA", "GR",
                    "NT", "BR", "MC", "AC", "AR", "CU", "KE", "LI", "LS",
                    "MS", "PA", "PK", "PT", "SO", "SP", "TE", "TX", "VF",
                    "WM", "WV")

    # =================================================================
    #  low-level binary plumbing
    # =================================================================
    class _Block:
        __slots__ = ("code", "size", "old_addr", "sdna_index", "count",
                     "data")

        def __init__(self, code, size, old_addr, sdna_index, count, data):
            self.code = code
            self.size = size
            self.old_addr = old_addr
            self.sdna_index = sdna_index
            self.count = count
            self.data = data

    class _Field:
        __slots__ = ("type_name", "name", "size", "is_pointer",
                     "array_dims", "offset")

    class _SDNA:
        def __init__(self):
            self.names = []
            self.types = []
            self.tlens = []
            self.struct_names = []      # sdna_index -> struct name
            self.structs = {}           # struct name -> [_Field]
            self.struct_sizes = {}      # struct name -> total bytes

    @staticmethod
    def _decode_dna_name(raw):
        """Decode an SDNA field name: '*' -> pointer (function pointers
        '(*x)()' count as ONE pointer), '[n][m]' -> array dims.
        Returns (clean_name, is_pointer, dims)."""
        is_ptr = "*" in raw
        dims = []
        clean = []
        i = 0
        while i < len(raw):
            ch = raw[i]
            if ch == "*":
                i += 1
            elif ch == "[":
                j = raw.find("]", i)
                if j < 0:
                    break
                try:
                    dims.append(int(raw[i + 1:j]))
                except ValueError:
                    pass
                i = j + 1
            elif ch in "()":
                i += 1
            else:
                clean.append(ch)
                i += 1
        return "".join(clean), is_ptr, dims

    # -----------------------------------------------------------------
    #  struct instance cursor — all reads go through the file's own SDNA
    # -----------------------------------------------------------------
    class _StructView:
        __slots__ = ("blend", "block", "off", "sname", "fields")

        def __init__(self, blend, block, off, sname):
            self.blend = blend
            self.block = block
            self.off = off
            self.sname = sname
            self.fields = blend.sdna.structs.get(sname)
            if self.fields is None:
                raise ValueError(
                    f"SDNA has no struct '{sname}' (unsupported DNA)")

        def _f(self, name):
            for fld in self.fields:
                if fld.name == name:
                    return fld
            return None

        def has(self, name):
            return self._f(name) is not None

        def _at(self, fld):
            pos = self.off + fld.offset
            if pos < 0 or pos + fld.size > len(self.block.data):
                raise ValueError(
                    f"truncated/corrupt .blend: field of struct "
                    f"'{self.sname}' lies outside its block")
            return pos

        def _unpack(self, fmt, pos):
            return struct.unpack_from(self.blend.endian + fmt,
                                      self.block.data, pos)[0]

        def get_int(self, name, default=None):
            fld = self._f(name)
            if fld is None or fld.is_pointer:
                return default
            tn = fld.type_name
            unsigned = tn.startswith("u") or tn == "bool"
            fmt = {1: "B" if unsigned else "b",
                   2: "H" if unsigned else "h",
                   4: "I" if unsigned else "i",
                   8: "Q" if unsigned else "q"}.get(fld.size)
            if fmt is None:
                return default
            try:
                return int(self._unpack(fmt, self._at(fld)))
            except struct.error:
                raise ValueError(
                    f"truncated/corrupt .blend: reading '{self.sname}."
                    f"{name}'")

        def get_float(self, name, default=None):
            fld = self._f(name)
            if fld is None or fld.is_pointer:
                return default
            fmt = {4: "f", 8: "d"}.get(fld.size)
            if fmt is None:
                return default
            try:
                return float(self._unpack(fmt, self._at(fld)))
            except struct.error:
                raise ValueError(
                    f"truncated/corrupt .blend: reading '{self.sname}."
                    f"{name}'")

        get_short = get_int
        get_char = get_int

        def get_array(self, name, kind, default=None):
            """Read a fixed-size array field as a numpy array.
            kind: 'f4','f8','i1','u1','i2','u2','i4','u4','i8','u8'."""
            fld = self._f(name)
            if fld is None or fld.is_pointer or not fld.array_dims:
                return default
            count = 1
            for d in fld.array_dims:
                count *= d
            item = np.dtype(kind).itemsize
            if item * count > fld.size:
                return default
            try:
                pos = self._at(fld)
                dt = np.dtype(kind).newbyteorder(self.blend.endian)
                return np.frombuffer(self.block.data, dtype=dt,
                                     count=count, offset=pos)
            except (ValueError, struct.error):
                raise ValueError(
                    f"truncated/corrupt .blend: reading '{self.sname}."
                    f"{name}'")

        def get_ptr(self, name):
            fld = self._f(name)
            if fld is None:
                return 0
            try:
                pos = self._at(fld)
            except ValueError:
                return 0
            fmt = "I" if self.blend.ptrsize == 4 else "Q"
            try:
                return int(self._unpack(fmt, pos))
            except struct.error:
                return 0

        def resolve(self, name):
            """Pointer field -> (block, offset) or None."""
            return self.blend.resolve_ptr(self.get_ptr(name))

        def get_string(self, name, default=""):
            fld = self._f(name)
            if fld is None or fld.is_pointer:
                return default
            try:
                pos = self._at(fld)
            except ValueError:
                return default
            raw = self.block.data[pos:pos + fld.size]
            nul = raw.find(b"\x00")
            if nul >= 0:
                raw = raw[:nul]
            return raw.decode("utf-8", "replace")

        def get_struct(self, name):
            """Embedded (non-pointer) struct field -> _StructView."""
            fld = self._f(name)
            if fld is None or fld.is_pointer:
                return None
            return BlendImporter._StructView(
                self.blend, self.block, self.off + fld.offset,
                fld.type_name)

    # -----------------------------------------------------------------
    #  the parsed .blend file
    # -----------------------------------------------------------------
    class _File:
        def __init__(self):
            self.path = ""
            self.ptrsize = 8
            self.endian = "<"
            self.version = ""
            self.blocks = []          # file order
            self._sorted = []         # sorted by old_addr
            self.sdna = None

        # ---------------------------- loading ------------------------
        @classmethod
        def load(cls, path):
            self = cls()
            self.path = path
            try:
                with open(path, "rb") as fh:
                    data = fh.read()
            except OSError as ex:
                raise ValueError(f"cannot read .blend file: {ex}")
            if len(data) < 12 or data[:7] != b"BLENDER":
                raise ValueError(
                    f"not a Blender .blend file (bad magic): "
                    f"{os.path.basename(path)}")
            ps = data[7:8]
            if ps == b"_":
                self.ptrsize = 4
            elif ps == b"-":
                self.ptrsize = 8
            else:
                raise ValueError(
                    "corrupt .blend header (unknown pointer-size byte)")
            en = data[8:9]
            if en == b"v":
                self.endian = "<"
            elif en == b"V":
                self.endian = ">"
            else:
                raise ValueError(
                    "corrupt .blend header (unknown endianness byte)")
            self.version = data[9:12].decode("ascii", "replace")

            pos = 12
            hdr = 16 + self.ptrsize
            while True:
                if pos + hdr > len(data):
                    raise ValueError(
                        "truncated/corrupt .blend: block header runs "
                        "past end of file")
                code = data[pos:pos + 4]
                size = struct.unpack_from(self.endian + "I", data,
                                          pos + 4)[0]
                off = pos + 8
                if self.ptrsize == 8:
                    old_addr = struct.unpack_from(self.endian + "Q",
                                                  data, off)[0]
                    off += 8
                else:
                    old_addr = struct.unpack_from(self.endian + "I",
                                                  data, off)[0]
                    off += 4
                sdna_index, count = struct.unpack_from(
                    self.endian + "II", data, off)
                pos += hdr
                if pos + size > len(data):
                    raise ValueError(
                        "truncated/corrupt .blend: block data runs past "
                        "end of file")
                self.blocks.append(BlendImporter._Block(
                    code, size, old_addr, sdna_index, count,
                    data[pos:pos + size]))
                pos += size
                if code == b"ENDB":
                    break

            dna_block = None
            for b in self.blocks:
                if b.code == b"DNA1":
                    dna_block = b
                    break
            if dna_block is None:
                raise ValueError("corrupt .blend: no DNA1 block found")
            self.sdna = self._parse_dna(dna_block.data)
            self._sorted = sorted(
                (b for b in self.blocks if b.old_addr),
                key=lambda b: b.old_addr)
            return self

        # ----------------------------- SDNA --------------------------
        def _parse_dna(self, d):
            endian = self.endian
            n = len(d)
            p = 0

            def _tag():
                nonlocal p
                if p + 4 > n:
                    raise ValueError("truncated/corrupt DNA1 block")
                t = d[p:p + 4]
                p += 4
                return t

            def _u32():
                nonlocal p
                if p + 4 > n:
                    raise ValueError("truncated/corrupt DNA1 block")
                v = struct.unpack_from(endian + "I", d, p)[0]
                p += 4
                return v

            def _align4():
                nonlocal p
                p = (p + 3) & ~3

            def _names(cnt):
                nonlocal p
                out = []
                for _ in range(cnt):
                    e = d.find(b"\x00", p)
                    if e < 0:
                        raise ValueError("truncated/corrupt DNA1 names")
                    out.append(d[p:e].decode("latin-1"))
                    p = e + 1
                return out

            if _tag() != b"SDNA":
                raise ValueError("corrupt DNA1 block (missing SDNA tag)")
            if _tag() != b"NAME":
                raise ValueError("corrupt DNA1 block (missing NAME tag)")
            names = _names(_u32())
            _align4()
            if _tag() != b"TYPE":
                raise ValueError("corrupt DNA1 block (missing TYPE tag)")
            types = _names(_u32())
            _align4()
            if _tag() != b"TLEN":
                raise ValueError("corrupt DNA1 block (missing TLEN tag)")
            if p + 2 * len(types) > n:
                raise ValueError("truncated/corrupt DNA1 TLEN")
            tlens = list(struct.unpack_from(
                endian + "%dH" % len(types), d, p))
            p += 2 * len(types)
            _align4()
            if _tag() != b"STRC":
                raise ValueError("corrupt DNA1 block (missing STRC tag)")
            nstructs = _u32()
            raw = []
            for _ in range(nstructs):
                if p + 4 > n:
                    raise ValueError("truncated/corrupt DNA1 structs")
                ti, fc = struct.unpack_from(endian + "HH", d, p)
                p += 4
                flds = []
                for _ in range(fc):
                    if p + 4 > n:
                        raise ValueError(
                            "truncated/corrupt DNA1 struct fields")
                    fti, fni = struct.unpack_from(endian + "HH", d, p)
                    p += 4
                    flds.append((fti, fni))
                raw.append((ti, flds))

            sdna = BlendImporter._SDNA()
            sdna.names = names
            sdna.types = types
            sdna.tlens = tlens
            for ti, flds in raw:
                if ti >= len(types):
                    raise ValueError("corrupt DNA1 (type index)")
                sname = types[ti]
                sdna.struct_names.append(sname)
                flist = []
                off = 0
                for fti, fni in flds:
                    if fti >= len(types) or fni >= len(names):
                        raise ValueError("corrupt DNA1 (field index)")
                    tname = types[fti]
                    clean, is_ptr, dims = \
                        BlendImporter._decode_dna_name(names[fni])
                    prod = 1
                    for dd in dims:
                        prod *= dd
                    if is_ptr:
                        size = self.ptrsize * max(1, prod)
                    else:
                        tlen = tlens[fti] if fti < len(tlens) else 0
                        size = tlen * max(1, prod)
                    f = BlendImporter._Field()
                    f.type_name = tname
                    f.name = clean
                    f.size = size
                    f.is_pointer = is_ptr
                    f.array_dims = dims
                    f.offset = off
                    flist.append(f)
                    off += size
                sdna.structs[sname] = flist
                sdna.struct_sizes[sname] = off
            return sdna

        # ------------------------- block access ----------------------
        def blocks_by_code(self, code2):
            """ID blocks: 2-letter code ('OB','ME','MA','IM','LA','SC')."""
            return [b for b in self.blocks if b.code[:2] == code2]

        def resolve_ptr(self, ptr):
            """old_addr pointer -> (block, offset) or None."""
            if not ptr:
                return None
            for b in self._sorted:
                if b.old_addr <= ptr < b.old_addr + max(b.size, 1):
                    return (b, ptr - b.old_addr)
            return None

        def struct_view(self, block, off, sname=None):
            if sname is None:
                if not (0 <= block.sdna_index < len(self.sdna.struct_names)):
                    raise ValueError("corrupt .blend: bad sdna_index")
                sname = self.sdna.struct_names[block.sdna_index]
            return BlendImporter._StructView(self, block, off, sname)

        def read_cstring(self, ptr):
            """Read a NUL-terminated string at a resolved pointer."""
            r = self.resolve_ptr(ptr)
            if r is None:
                return ""
            blk, off = r
            raw = blk.data[off:]
            nul = raw.find(b"\x00")
            if nul >= 0:
                raw = raw[:nul]
            return raw.decode("utf-8", "replace")

    # =================================================================
    #  transforms (Blender Z-up, column-vector convention, row-major
    #  [row][col] storage exactly like Blender's float[4][4])
    # =================================================================
    @staticmethod
    def _quat_mat3(q):
        w, x, y, z = (float(q[0]), float(q[1]), float(q[2]), float(q[3]))
        n = math.sqrt(w * w + x * x + y * y + z * z)
        if n < 1e-12:
            return np.eye(3)
        w, x, y, z = w / n, x / n, y / n, z / n
        return np.array([
            [1.0 - 2.0 * (y * y + z * z), 2.0 * (x * y - z * w),
             2.0 * (x * z + y * w)],
            [2.0 * (x * y + z * w), 1.0 - 2.0 * (x * x + z * z),
             2.0 * (y * z - x * w)],
            [2.0 * (x * z - y * w), 2.0 * (y * z + x * w),
             1.0 - 2.0 * (x * x + y * y)]])

    @staticmethod
    def _euler_mat3(ex, ey, ez, mode):
        # Blender rotmode: 1=XYZ 2=XZY 3=YXZ 4=YZX 5=ZXY 6=ZYX
        orders = {1: "XYZ", 2: "XZY", 3: "YXZ", 4: "YZX", 5: "ZXY",
                  6: "ZYX"}
        order = orders.get(mode, "XYZ")

        def _rx(a):
            c, s = math.cos(a), math.sin(a)
            return np.array([[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]])

        def _ry(a):
            c, s = math.cos(a), math.sin(a)
            return np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])

        def _rz(a):
            c, s = math.cos(a), math.sin(a)
            return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])

        mats = {"X": _rx(ex), "Y": _ry(ey), "Z": _rz(ez)}
        R = np.eye(3)
        # Blender applies the first-listed euler first: M = Rz @ Ry @ Rx
        # for XYZ order.
        for axis in reversed(order):
            R = R @ mats[axis]
        return R

    @classmethod
    def _local_matrix(cls, view):
        loc = view.get_array("loc", "f4")
        if loc is None or len(loc) < 3:
            loc = np.zeros(3)
        scale = view.get_array("scale", "f4")
        if scale is None or len(scale) < 3:
            scale = view.get_array("size", "f4")   # ancient DNA
        if scale is None or len(scale) < 3:
            scale = np.ones(3)
        rotmode = view.get_int("rotmode", 1)
        if rotmode is None:
            rotmode = 1
        if rotmode == 0 and view.has("quat"):
            q = view.get_array("quat", "f4")
            R = (cls._quat_mat3(q) if (q is not None and len(q) >= 4)
                 else np.eye(3))
        else:
            e = view.get_array("rot", "f4")
            if e is None or len(e) < 3:
                e = np.zeros(3)
            R = cls._euler_mat3(float(e[0]), float(e[1]), float(e[2]),
                                int(rotmode))
        M = np.eye(4)
        M[:3, :3] = R * np.asarray(scale[:3], dtype=np.float64)[None, :]
        M[:3, 3] = np.asarray(loc[:3], dtype=np.float64)
        return M

    @classmethod
    def _world_matrix(cls, blend, view, cache, stack):
        """W = (parent ? W_parent · parentinv : I) · L, recursive with
        memoization and cycle protection."""
        key = (view.block.old_addr, view.off)
        if key in cache:
            return cache[key]
        if key in stack:
            Log.warn(".blend: parent cycle detected — using local matrix")
            return cls._local_matrix(view)
        stack.add(key)
        L = cls._local_matrix(view)
        W = L
        pptr = view.get_ptr("parent")
        if pptr:
            r = blend.resolve_ptr(pptr)
            if r is not None:
                try:
                    pview = blend.struct_view(r[0], r[1], "Object")
                    Wp = cls._world_matrix(blend, pview, cache, stack)
                    pinv = view.get_array("parentinv", "f4")
                    if pinv is not None and len(pinv) >= 16:
                        base = Wp @ np.asarray(
                            pinv[:16], dtype=np.float64).reshape(4, 4)
                    else:
                        base = Wp
                    W = base @ L
                except Exception as ex:
                    Log.warn(f".blend: parent chain broken ({ex})")
                    W = L
        stack.discard(key)
        cache[key] = W
        return W

    # =================================================================
    #  object enumeration
    # =================================================================
    @classmethod
    def _walk_listbase(cls, blend, lb_view, sname, cap=1000000):
        """Walk a ListBase of `sname` items; yields (ptr, view)."""
        if lb_view is None:
            return
        ptr = lb_view.get_ptr("first")
        guard = 0
        while ptr and guard < cap:
            guard += 1
            r = blend.resolve_ptr(ptr)
            if r is None:
                break
            try:
                v = blend.struct_view(r[0], r[1], sname)
            except ValueError:
                break
            yield ptr, v
            ptr = v.get_ptr("next")

    @classmethod
    def _enumerate_objects(cls, blend):
        """Primary route: SC -> Scene.view_layers -> ViewLayer.
        object_bases -> Base.object.  Fallback: all OB blocks."""
        objs = []
        seen = set()
        for scb in blend.blocks_by_code(b"SC"):
            try:
                sc = blend.struct_view(scb, 0, "Scene")
            except ValueError as ex:
                Log.warn(f".blend: Scene struct unreadable ({ex})")
                continue
            for _p, vl in cls._walk_listbase(
                    blend, sc.get_struct("view_layers"), "ViewLayer"):
                for _bp, base in cls._walk_listbase(
                        blend, vl.get_struct("object_bases"), "Base"):
                    r = blend.resolve_ptr(base.get_ptr("object"))
                    if r is None:
                        continue
                    key = (r[0].old_addr, r[1])
                    if key not in seen:
                        seen.add(key)
                        objs.append(r)
        if objs:
            Log.info(f".blend: objects via Scene->ViewLayer->Base "
                     f"({len(objs)} found)")
            return objs
        objs = [(b, 0) for b in blend.blocks_by_code(b"OB")]
        Log.info(f".blend: objects via OB block scan, fallback "
                 f"({len(objs)} found)")
        return objs

    # =================================================================
    #  CustomData layers
    # =================================================================
    @classmethod
    def _customdata_layers(cls, blend, cd_view, domain_count):
        """-> [{name,type,active,active_rnd,block,off}].  Uses the
        layer's 'data' pointer when the DNA has it (2.8x-3.x); otherwise
        (4.x) pairs data blocks by file order."""
        out = []
        if cd_view is None:
            return out
        r = blend.resolve_ptr(cd_view.get_ptr("layers"))
        if r is None:
            return out
        lblk, loff = r
        if "CustomDataLayer" not in blend.sdna.structs:
            return out
        ssize = blend.sdna.struct_sizes.get("CustomDataLayer", 0)
        if ssize <= 0:
            return out
        tot = cd_view.get_int("totlayer", 0) or 0
        avail = (lblk.size - loff) // ssize
        if avail <= 0:
            return out
        n = min(tot, avail) if tot > 0 else avail
        cdl_fields = blend.sdna.structs["CustomDataLayer"]
        has_data_field = any(f.name == "data" for f in cdl_fields)
        pending = []
        for i in range(n):
            lv = blend.struct_view(lblk, loff + i * ssize,
                                   "CustomDataLayer")
            info = {
                "name": lv.get_string("name", ""),
                "type": lv.get_int("type", -1),
                "active": lv.get_int("active", 0) or 0,
                "active_rnd": lv.get_int("active_rnd", 0) or 0,
                "block": None,
                "off": 0,
            }
            if has_data_field:
                rr = blend.resolve_ptr(lv.get_ptr("data"))
                if rr is not None:
                    info["block"], info["off"] = rr
            if info["block"] is None:
                pending.append(i)
            out.append(info)
        if pending and not has_data_field:
            cls._assign_layers_by_order(blend, lblk, out, pending,
                                        domain_count)
        return out

    @classmethod
    def _layer_elem_size(cls, blend, type_id):
        """Best-known per-element byte size for plausibility checks."""
        classic = {cls._CD_MVERT: "MVert", cls._CD_MFACE: "MFace",
                   cls._CD_MLOOP: "MLoop", cls._CD_MPOLY: "MPoly",
                   cls._CD_MLOOPUV: "MLoopUV",
                   cls._CD_MLOOPCOL: "MLoopCol"}
        if type_id in classic:
            return blend.sdna.struct_sizes.get(classic[type_id], 0)
        return {cls._CD_PROP_BOOL: 1, cls._CD_PROP_FLOAT2: 8,
                cls._CD_PROP_FLOAT3: 12, cls._CD_PROP_INT32: 4,
                cls._CD_PROP_COLOR: 16, cls._CD_PROP_BYTE_COLOR: 4,
                cls._CD_NORMAL: 12, cls._CD_CUSTOMLOOPNORMAL: 12
                }.get(type_id, 0)

    @classmethod
    def _assign_layers_by_order(cls, blend, layers_block, layers, pending,
                                domain_count):
        """Blender 4.x writes layer data blocks immediately after the
        layers-array block, one per layer that has data, in layer order.
        Pair them sequentially with a size plausibility check."""
        Log.warn(".blend: CustomDataLayer has no 'data' field (Blender "
                 "4.x layout) — pairing layer data blocks by file order "
                 "(heuristic)")
        try:
            start = blend.blocks.index(layers_block)
        except ValueError:
            return
        cand = blend.blocks[start + 1:]
        ci = 0
        for i in pending:
            info = layers[i]
            elem = cls._layer_elem_size(blend, info["type"])
            need = elem * max(1, domain_count)
            while ci < len(cand):
                b = cand[ci]
                ci += 1
                if b.code == b"ENDB" or b.code == b"DNA1":
                    continue
                if elem <= 0 or b.size >= need:
                    info["block"] = b
                    info["off"] = 0
                    break
            if info["block"] is None:
                Log.warn(f".blend: no data block found for layer "
                         f"'{info['name']}' (type {info['type']})")

    # -----------------------------------------------------------------
    #  layer data decoding
    # -----------------------------------------------------------------
    @classmethod
    def _read_layer_array(cls, blend, info, count, struct_name=None,
                          field_name=None, np_kind=None, np_shape=()):
        """Decode `count` elements out of a layer data block.

        Two modes:
          * struct mode: SDNA struct `struct_name`, field `field_name`
            (stride = SDNA struct size — never a hardcoded offset);
          * raw mode: fixed numpy dtype `np_kind` with `np_shape`
            components (4.x generic attribute blocks).
        Always validates block size vs count*elemsize; clamps + warns on
        garbage."""
        blk = info.get("block") if info else None
        if blk is None or count <= 0:
            return None
        base = info.get("off", 0)
        if base < 0 or base >= blk.size:
            Log.warn(".blend: layer data offset outside block")
            return None
        try:
            if struct_name is not None and field_name is not None:
                fields = blend.sdna.structs.get(struct_name)
                ssize = blend.sdna.struct_sizes.get(struct_name, 0)
                if not fields or ssize <= 0:
                    return None
                fld = None
                for f in fields:
                    if f.name == field_name:
                        fld = f
                        break
                if fld is None or fld.is_pointer:
                    return None
                item = np.dtype(np_kind).itemsize
                if item <= 0 or fld.size % item:
                    return None
                ncomp = fld.size // item
                nmax = (blk.size - base) // ssize
                n = min(count, nmax)
                if n < count:
                    Log.warn(f".blend: {struct_name} layer truncated "
                             f"({n}/{count} elements) — clamping")
                if n <= 0:
                    return None
                raw = np.frombuffer(
                    blk.data, dtype=np.uint8,
                    count=(n - 1) * ssize + fld.size,
                    offset=base + fld.offset)
                view = np.lib.stride_tricks.as_strided(
                    raw, shape=(n, fld.size), strides=(ssize, 1))
                dt = np.dtype(np_kind).newbyteorder(blend.endian)
                arr = view.copy().view(dt).reshape(n, ncomp)
                return arr
            if np_kind is not None:
                dt = np.dtype(np_kind).newbyteorder(blend.endian)
                comps = 1
                for d0 in np_shape:
                    comps *= d0
                stride = dt.itemsize * comps
                nmax = (blk.size - base) // stride
                n = min(count, nmax)
                if n < count:
                    Log.warn(f".blend: attribute layer truncated "
                             f"({n}/{count} elements) — clamping")
                if n <= 0:
                    return None
                arr = np.frombuffer(blk.data, dtype=dt, count=n * comps,
                                    offset=base)
                if np_shape:
                    arr = arr.reshape((n,) + tuple(np_shape))
                return arr
        except Exception as ex:
            Log.warn(f".blend: layer decode failed ({ex})")
            return None
        return None

    @staticmethod
    def _find_layer(layers, type_id):
        for l in layers:
            if l["type"] == type_id and l.get("block") is not None:
                return l
        return None

    @staticmethod
    def _find_layers(layers, type_id):
        return [l for l in layers
                if l["type"] == type_id and l.get("block") is not None]

    @staticmethod
    def _find_named_layer(layers, type_id, name):
        name = (name or "").lower()
        for l in layers:
            if (l["type"] == type_id and l.get("block") is not None
                    and (l["name"] or "").lower() == name):
                return l
        return None

    @staticmethod
    def _find_generic_layer(layers, names, bytes_per_elem, count,
                            type_ids=()):
        """4.x generic attribute lookup: NAME first, then known type ids
        — always with a block-size plausibility check against the domain
        count (bytes_per_elem * count), because 4.x reused type ids."""
        fallback = None
        for l in layers:
            blk = l.get("block")
            if blk is None or count <= 0:
                continue
            avail = blk.size - l.get("off", 0)
            if avail < bytes_per_elem * count:
                continue
            if names and (l["name"] or "") in names:
                return l
            if fallback is None and l["type"] in type_ids:
                fallback = l
        return fallback

    @staticmethod
    def _srgb_to_linear01(u8):
        c = u8.astype(np.float32) / 255.0
        return np.where(c <= 0.04045, c / 12.92,
                        ((c + 0.055) / 1.055) ** 2.4)

    # =================================================================
    #  mesh decode -> per-material triangle-soup MeshData parts
    # =================================================================
    @classmethod
    def _decode_mesh(cls, blend, mview, obj_name, blend_dir):
        totvert = mview.get_int("totvert", 0) or 0
        totloop = mview.get_int("totloop", 0) or 0
        totpoly = mview.get_int("totpoly", 0) or 0
        if totvert < 0 or totloop < 0 or totpoly < 0:
            Log.warn(f".blend: mesh '{obj_name}' has negative counts — "
                     f"clamped")
            totvert = max(0, totvert)
            totloop = max(0, totloop)
            totpoly = max(0, totpoly)

        vlayers = cls._customdata_layers(
            blend, mview.get_struct("vdata"), totvert)
        llayers = cls._customdata_layers(
            blend, mview.get_struct("ldata"), totloop)
        players = cls._customdata_layers(
            blend, mview.get_struct("pdata"), totpoly)

        # ----------------------- vertices -----------------------
        positions = None
        vnormals = None
        mv = cls._find_layer(vlayers, cls._CD_MVERT)
        if mv is not None:
            positions = cls._read_layer_array(
                blend, mv, totvert, "MVert", "co", "f4")
            no = cls._read_layer_array(
                blend, mv, totvert, "MVert", "no", "i2")
            if no is not None and positions is not None and len(no) == len(positions):
                vnormals = no.astype(np.float32) / 32767.0
        if positions is None:
            # 4.x: generic vertex-domain FLOAT3 attribute named
            # "position" (name first, then known type ids; the block
            # size must match totvert*12 — never trust the type alone).
            pl = cls._find_generic_layer(vlayers, ("position",),
                                         12, totvert,
                                         (cls._CD_PROP_FLOAT3, 48))
            if pl is not None:
                positions = cls._read_layer_array(
                    blend, pl, totvert, None, None, "f4", (3,))
        if positions is None and totvert > 0:
            # legacy direct pointer fallback (only if it resolves)
            r = mview.resolve("mvert")
            if r is not None:
                fake = {"block": r[0], "off": r[1]}
                positions = cls._read_layer_array(
                    blend, fake, totvert, "MVert", "co", "f4")
                no = cls._read_layer_array(
                    blend, fake, totvert, "MVert", "no", "i2")
                if no is not None and positions is not None and len(no) == len(positions):
                    vnormals = no.astype(np.float32) / 32767.0
        if positions is None or len(positions) == 0:
            Log.warn(f".blend: mesh '{obj_name}' has no vertex data")
            return []
        positions = np.ascontiguousarray(positions,
                                         dtype=np.float32).reshape(-1, 3)
        nverts = len(positions)

        # --------------------- loops + polys ---------------------
        loops_v = None
        ml = cls._find_layer(llayers, cls._CD_MLOOP)
        if ml is not None:
            loops_v = cls._read_layer_array(
                blend, ml, totloop, "MLoop", "v", "i4")
        if loops_v is None and totloop > 0:
            r = mview.resolve("mloop")
            if r is not None:
                loops_v = cls._read_layer_array(
                    blend, {"block": r[0], "off": r[1]},
                    totloop, "MLoop", "v", "i4")
        if loops_v is None and totloop > 0:
            # 4.1+: loop->vertex is the generic corner-domain INT32
            # attribute ".corner_vert".
            cl = cls._find_generic_layer(llayers, (".corner_vert",),
                                         4, totloop,
                                         (cls._CD_PROP_INT32,))
            if cl is not None:
                loops_v = cls._read_layer_array(
                    blend, cl, totloop, None, None, "i4")
        if loops_v is not None:
            loops_v = np.asarray(loops_v, dtype=np.int64).reshape(-1)

        # polys: list of (corner_vert_idx[int64[k]], corner_loop_idx or
        # None, mat_nr, smooth)
        polys = []
        # face-domain generic attributes shared by all poly routes:
        # "material_index" (INT32) and "sharp_face" (BOOL, inverted).
        midx_arr = None
        midx = cls._find_generic_layer(players, ("material_index",),
                                       4, totpoly, (cls._CD_PROP_INT32,
                                                    11))
        if midx is not None:
            midx_arr = cls._read_layer_array(
                blend, midx, totpoly, None, None, "i4")
            if midx_arr is not None and len(midx_arr) != totpoly:
                midx_arr = None
        sharp_arr = None
        sharp = cls._find_generic_layer(players, ("sharp_face",),
                                        1, totpoly, (cls._CD_PROP_BOOL,
                                                     50))
        if sharp is not None:
            sharp_arr = cls._read_layer_array(
                blend, sharp, totpoly, None, None, "u1")
            if sharp_arr is not None and len(sharp_arr) != totpoly:
                sharp_arr = None

        def _poly_smooth(pi, flag):
            if sharp_arr is not None and pi < len(sharp_arr):
                return not bool(sharp_arr[pi])
            if flag is not None and pi < len(flag):
                return bool(int(flag[pi]) & cls._ME_SMOOTH)
            return True

        mp = cls._find_layer(players, cls._CD_MPOLY)
        if mp is None and totpoly > 0:
            r = mview.resolve("mpoly")
            if r is not None:
                mp = {"block": r[0], "off": r[1]}
        if mp is not None and loops_v is not None and totpoly > 0:
            loopstart = cls._read_layer_array(
                blend, mp, totpoly, "MPoly", "loopstart", "i4")
            ptot = cls._read_layer_array(
                blend, mp, totpoly, "MPoly", "totloop", "i4")
            mat_nr = cls._read_layer_array(
                blend, mp, totpoly, "MPoly", "mat_nr", "i2")
            flag = cls._read_layer_array(
                blend, mp, totpoly, "MPoly", "flag", "u1")
            # 4.x generic "material_index" overrides MPoly.mat_nr
            if midx_arr is not None:
                mat_nr = midx_arr
            nloops = len(loops_v)
            for pi in range(totpoly):
                try:
                    ls = int(loopstart[pi]) if loopstart is not None else 0
                    tl = int(ptot[pi]) if ptot is not None else 0
                except Exception:
                    continue
                if tl < 3:
                    if tl != 0:
                        Log.warn(f".blend: degenerate poly {pi} in "
                                 f"'{obj_name}' (totloop={tl}) — skipped")
                    continue
                if ls < 0 or ls + tl > nloops:
                    Log.warn(f".blend: poly {pi} loop range out of bounds "
                             f"in '{obj_name}' — clamped/skipped")
                    if ls < 0 or ls >= nloops:
                        continue
                    tl = nloops - ls
                    if tl < 3:
                        continue
                corner_loops = np.arange(ls, ls + tl, dtype=np.int64)
                cv = loops_v[ls:ls + tl]
                if cv.min(initial=1) < 0 or cv.max(initial=0) >= nverts:
                    Log.warn(f".blend: poly {pi} references bad vertices "
                             f"in '{obj_name}' — skipped")
                    continue
                mn = int(mat_nr[pi]) if mat_nr is not None else 0
                polys.append((cv, corner_loops, mn,
                              _poly_smooth(pi, flag)))
        elif loops_v is not None and totpoly > 0 and \
                mview.has("poly_offset_indices"):
            # Blender 4.5+ face storage: Mesh.poly_offset_indices is an
            # int32 offset table with totpoly+1 entries; face i owns
            # loops[off[i] : off[i+1]] (no MPoly any more).
            r = mview.resolve("poly_offset_indices")
            if r is not None:
                blk, off = r
                n = totpoly + 1
                if blk.size - off < n * 4:
                    Log.warn(".blend: poly_offset_indices truncated — "
                             "clamping")
                    n = (blk.size - off) // 4
                if n >= 2:
                    dt = np.dtype("i4").newbyteorder(blend.endian)
                    offs = np.frombuffer(blk.data, dtype=dt, count=n,
                                         offset=off).astype(np.int64)
                    nloops = len(loops_v)
                    for pi in range(n - 1):
                        ls, le = int(offs[pi]), int(offs[pi + 1])
                        tl = le - ls
                        if tl < 3:
                            if tl != 0:
                                Log.warn(f".blend: degenerate poly {pi} "
                                         f"in '{obj_name}' — skipped")
                            continue
                        if ls < 0 or le > nloops:
                            Log.warn(f".blend: poly {pi} loop range out "
                                     f"of bounds in '{obj_name}' — "
                                     f"clamped/skipped")
                            if ls < 0 or ls >= nloops:
                                continue
                            tl = nloops - ls
                            if tl < 3:
                                continue
                        corner_loops = np.arange(ls, ls + tl,
                                                 dtype=np.int64)
                        cv = loops_v[ls:ls + tl]
                        if cv.min(initial=1) < 0 or \
                                cv.max(initial=0) >= nverts:
                            Log.warn(f".blend: poly {pi} references bad "
                                     f"vertices in '{obj_name}' — "
                                     f"skipped")
                            continue
                        mn = (int(midx_arr[pi])
                              if midx_arr is not None else 0)
                        polys.append((cv, corner_loops, mn,
                                      _poly_smooth(pi, None)))
        else:
            # legacy MFace path (triangles/quads, no loop attributes)
            mf = cls._find_layer(players, cls._CD_MFACE)
            if mf is None:
                for cand in (vlayers, llayers):
                    mf = cls._find_layer(cand, cls._CD_MFACE)
                    if mf is not None:
                        break
            if mf is None:
                r = mview.resolve("mface")
                if r is not None:
                    mf = {"block": r[0], "off": r[1]}
            if mf is not None:
                nface = max(totpoly, mf["block"].count or 0)
                v1 = cls._read_layer_array(blend, mf, nface, "MFace",
                                           "v1", "u4")
                v2 = cls._read_layer_array(blend, mf, nface, "MFace",
                                           "v2", "u4")
                v3 = cls._read_layer_array(blend, mf, nface, "MFace",
                                           "v3", "u4")
                v4 = cls._read_layer_array(blend, mf, nface, "MFace",
                                           "v4", "u4")
                mnr = cls._read_layer_array(blend, mf, nface, "MFace",
                                            "mat_nr", "i2")
                if v1 is not None and v2 is not None and v3 is not None:
                    nf = len(v1)
                    for fi in range(nf):
                        a, b, c = int(v1[fi]), int(v2[fi]), int(v3[fi])
                        d = int(v4[fi]) if v4 is not None else 0
                        if max(a, b, c, d) >= nverts:
                            Log.warn(f".blend: MFace {fi} references bad "
                                     f"vertices — skipped")
                            continue
                        idx = [a, b, c]
                        if d != 0 and d != c:
                            idx.append(d)
                        mn = int(mnr[fi]) if mnr is not None else 0
                        polys.append((np.asarray(idx, dtype=np.int64),
                                      None, mn, False))
        if not polys:
            Log.warn(f".blend: mesh '{obj_name}' has no polygons")
            return []

        # ------------------------- UVs ---------------------------
        # Classic: CD_MLOOPUV layers with an MLoopUV struct (choose the
        # layer whose active_rnd/active flag is set, else first).
        # 4.x: corner-domain FLOAT2 generic attributes (choose by the
        # mesh's active/default UV attribute name, else active flag,
        # else first).  Type-id clashes (4.x reused ids) are settled by
        # the per-element size check.
        uv_data = None
        mloopuv_ssize = blend.sdna.struct_sizes.get("MLoopUV", 0)
        classic_uvs = []
        for l in cls._find_layers(llayers, cls._CD_MLOOPUV):
            avail = l["block"].size - l.get("off", 0)
            if mloopuv_ssize and totloop > 0 \
                    and avail >= totloop * mloopuv_ssize:
                classic_uvs.append(l)
        if not classic_uvs:
            r = mview.resolve("mloopuv")
            if r is not None and totloop > 0 and mloopuv_ssize \
                    and r[0].size - r[1] >= totloop * mloopuv_ssize:
                classic_uvs = [{"block": r[0], "off": r[1],
                                "name": "mloopuv",
                                "type": cls._CD_MLOOPUV,
                                "active": 1, "active_rnd": 1}]
        if classic_uvs:
            chosen = None
            for l in classic_uvs:
                if l.get("active_rnd") or l.get("active"):
                    chosen = l
                    break
            if chosen is None:
                chosen = classic_uvs[0]
            uv_data = cls._read_layer_array(
                blend, chosen, totloop, "MLoopUV", "uv", "f4")
        if uv_data is None:
            want = blend.read_cstring(
                mview.get_ptr("active_uv_map_attribute")) or \
                blend.read_cstring(
                    mview.get_ptr("default_uv_map_attribute"))
            # Any named, non-internal corner-domain layer with >= 8
            # bytes per element is a UV candidate (type ids for UV
            # layers differ across Blender generations — name-driven).
            f2 = []
            for l in llayers:
                if l.get("block") is None or totloop <= 0:
                    continue
                nm = l["name"] or ""
                if not nm or nm.startswith("."):
                    continue
                avail = l["block"].size - l.get("off", 0)
                if avail < totloop * 8:
                    continue
                f2.append(l)
            if f2:
                chosen = None
                if want:
                    for l in f2:
                        if l["name"] == want:
                            chosen = l
                            break
                if chosen is None:
                    for l in f2:
                        if l.get("active_rnd") or l.get("active"):
                            chosen = l
                            break
                if chosen is None:
                    chosen = f2[0]
                avail = chosen["block"].size - chosen.get("off", 0)
                if mloopuv_ssize and avail >= totloop * mloopuv_ssize:
                    # MLoopUV struct payload (uv + flag), any type id
                    uv_data = cls._read_layer_array(
                        blend, chosen, totloop, "MLoopUV", "uv", "f4")
                else:
                    # raw float2 generic attribute
                    uv_data = cls._read_layer_array(
                        blend, chosen, totloop, None, None, "f4", (2,))
        if uv_data is not None:
            uv_data = np.asarray(uv_data, dtype=np.float32).reshape(-1, 2)

        # --------------------- loop normals ----------------------
        loop_normals = None
        for tid in (cls._CD_NORMAL, cls._CD_CUSTOMLOOPNORMAL):
            ln = cls._find_layer(llayers, tid)
            if ln is None:
                continue
            arr = cls._read_layer_array(blend, ln, totloop, None, None,
                                        "f4", (3,))
            if arr is not None and len(arr) >= totloop > 0:
                loop_normals = np.asarray(arr, dtype=np.float32)
                break
            # 3.x custom normals are stored as short2 — unsupported;
            # fall through to computed normals.
            Log.warn(f".blend: loop-normal layer (type {tid}) not "
                     f"float3 data — using computed normals instead")

        # -------------------- vertex colours ---------------------
        vcol_linear = None     # (n, 4) linear floats, corner domain
        vcol_point = None      # point domain
        mlc = cls._find_layers(llayers, cls._CD_MLOOPCOL)
        if not mlc:
            r = mview.resolve("mloopcol")
            if r is not None and totloop > 0:
                mlc = [{"block": r[0], "off": r[1], "name": "mloopcol",
                        "type": cls._CD_MLOOPCOL, "active": 1,
                        "active_rnd": 1}]
        if mlc:
            l0 = mlc[0]
            cr = cls._read_layer_array(blend, l0, totloop, "MLoopCol",
                                       "r", "u1")
            cg = cls._read_layer_array(blend, l0, totloop, "MLoopCol",
                                       "g", "u1")
            cb = cls._read_layer_array(blend, l0, totloop, "MLoopCol",
                                       "b", "u1")
            ca = cls._read_layer_array(blend, l0, totloop, "MLoopCol",
                                       "a", "u1")
            if cr is not None and cg is not None and cb is not None:
                if ca is None:
                    ca = np.full_like(cr, 255)
                rgba = np.stack([cr, cg, cb, ca], axis=-1).reshape(-1, 4)
                vcol_linear = cls._srgb_to_linear01(rgba)
        if vcol_linear is None:
            # 4.x generic colour attributes, chosen by the mesh's
            # active/default colour attribute name when available;
            # element size decides the encoding (16B = float linear
            # COLOR, 4B = sRGB BYTE_COLOR).
            aca = blend.read_cstring(
                mview.get_ptr("active_color_attribute"))
            dca = blend.read_cstring(
                mview.get_ptr("default_color_attribute"))
            cand_layers = []
            for domain_layers, cnt in ((llayers, totloop),
                                       (vlayers, totvert)):
                for l in domain_layers:
                    if l.get("block") is None or cnt <= 0:
                        continue
                    avail = l["block"].size - l.get("off", 0)
                    if avail >= cnt * 16:
                        enc = "float"
                    elif avail >= cnt * 4:
                        enc = "byte"
                    else:
                        continue
                    named = (l["name"] or "") in (aca, dca) and \
                        bool(aca or dca)
                    colish = "col" in (l["name"] or "").lower() and \
                        not (l["name"] or "").startswith(".")
                    if l["type"] in (cls._CD_PROP_COLOR, 36) \
                            and enc == "float":
                        cand_layers.append((named, domain_layers is
                                            llayers, cnt, l, enc))
                    elif l["type"] in (cls._CD_PROP_BYTE_COLOR, 38) \
                            and enc == "byte":
                        cand_layers.append((named, domain_layers is
                                            llayers, cnt, l, enc))
                    elif named or colish:
                        cand_layers.append((named, domain_layers is
                                            llayers, cnt, l, enc))
            cand_layers.sort(key=lambda t: not t[0])   # named first
            for _named, corner_domain, cnt, l, enc in cand_layers:
                if enc == "float":
                    arr = cls._read_layer_array(blend, l, cnt,
                                                None, None, "f4", (4,))
                else:
                    arr = cls._read_layer_array(blend, l, cnt,
                                                None, None, "u1", (4,))
                    if arr is not None:
                        arr = cls._srgb_to_linear01(arr)
                if arr is not None and len(arr):
                    if corner_domain:
                        vcol_linear = np.asarray(arr, np.float32)
                    else:
                        vcol_point = np.asarray(arr, np.float32)
                    break

        # average colour (linear) — the engine has no vertex-colour
        # channel, so it is folded into the material base colour.
        vcol_avg = None
        if vcol_linear is not None and len(vcol_linear):
            vcol_avg = vcol_linear[:, :3].mean(axis=0)
        elif vcol_point is not None and len(vcol_point):
            vcol_avg = vcol_point[:, :3].mean(axis=0)

        # ---------------------- materials ------------------------
        materials = cls._decode_materials(blend, mview, blend_dir)

        # ----------------- triangulation (fan) -------------------
        # groups: mat_index -> dict(corners=[(3 loop/vert pairs)...])
        groups = {}
        nmat = len(materials)
        skipped_degenerate = 0
        for cv, cloops, mn, smooth in polys:
            mi = mn
            if mi < 0 or (nmat and mi >= nmat):
                Log.warn(f".blend: poly mat_nr {mn} out of range "
                         f"(0..{max(0, nmat - 1)}) in '{obj_name}' — "
                         f"clamped")
                mi = min(max(mi, 0), max(0, nmat - 1))
            elif not nmat:
                mi = 0
            g = groups.setdefault(mi, [])
            k = len(cv)
            for i in range(1, k - 1):
                tri_v = (int(cv[0]), int(cv[i]), int(cv[i + 1]))
                tri_l = (None if cloops is None else
                         (int(cloops[0]), int(cloops[i]),
                          int(cloops[i + 1])))
                if tri_v[0] == tri_v[1] or tri_v[1] == tri_v[2] \
                        or tri_v[0] == tri_v[2]:
                    skipped_degenerate += 1
                    continue
                g.append((tri_v, tri_l, smooth))
        if skipped_degenerate:
            Log.warn(f".blend: {skipped_degenerate} degenerate triangles "
                     f"skipped in '{obj_name}'")

        # computed smooth vertex normals as fallback
        if vnormals is None:
            vnormals = GlbImporter._gen_smooth_normals(
                positions,
                np.concatenate([
                    np.asarray([t[0] for t in g], dtype=np.uint32).ravel()
                    for g in groups.values() if g]) if any(groups.values())
                else None)

        parts = []
        for mi in sorted(groups.keys()):
            tris = groups[mi]
            if not tris:
                Log.warn(f".blend: empty material group {mi} in "
                         f"'{obj_name}' — skipped")
                continue
            ntri = len(tris)
            vidx = np.asarray([t[0] for t in tris],
                              dtype=np.int64).ravel()
            pos = positions[vidx].astype(np.float32)
            # normals
            nor = None
            have_loop_attr = all(t[1] is not None for t in tris)
            if have_loop_attr:
                lidx = np.asarray([t[1] for t in tris],
                                  dtype=np.int64).ravel()
            else:
                lidx = None
            if loop_normals is not None and lidx is not None \
                    and lidx.max(initial=0) < len(loop_normals):
                nor = loop_normals[lidx].astype(np.float32)
            else:
                nor = np.empty((ntri * 3, 3), dtype=np.float32)
                fn = None
                flat_mask = np.asarray([not t[2] for t in tris],
                                       dtype=bool)
                if flat_mask.any():
                    tr = pos.reshape(-1, 3, 3)
                    fn_all = np.cross(tr[:, 1] - tr[:, 0],
                                      tr[:, 2] - tr[:, 0])
                    ln = np.linalg.norm(fn_all, axis=1, keepdims=True)
                    ln[ln < 1e-12] = 1.0
                    fn_all = fn_all / ln
                    nor[:] = np.repeat(fn_all, 3, axis=0)
                if (~flat_mask).any():
                    sm = vnormals[vidx].astype(np.float32)
                    sm[~np.repeat(~flat_mask, 3)] = 0.0
                    # merge: where flat rows were written keep them
                    m = np.repeat(flat_mask, 3)
                    nor = np.where(m[:, None], nor, sm)
            # uvs
            uvs = None
            if uv_data is not None and lidx is not None \
                    and len(uv_data) and lidx.max(initial=0) < len(uv_data):
                uvs = uv_data[lidx].astype(np.float32)
            info = materials[mi] if mi < len(materials) else None
            if vcol_avg is not None:
                if info is not None and info.get("base_color_tex") is None:
                    bc = list(info.get("base_color",
                                       [1.0, 1.0, 1.0, 1.0]))
                    bc[0] = float(bc[0]) * float(vcol_avg[0])
                    bc[1] = float(bc[1]) * float(vcol_avg[1])
                    bc[2] = float(bc[2]) * float(vcol_avg[2])
                    info["base_color"] = bc
                else:
                    Log.info(f".blend: vertex colours on '{obj_name}' "
                             f"skipped (material has a base-colour "
                             f"texture or no material)")
            if len(groups) == 1 and not info:
                mname = obj_name
            elif info:
                mname = f"{obj_name}.{info.get('name', 'mat')}"
            else:
                mname = f"{obj_name}.mat{mi}"
            parts.append(cls.MeshData(
                name=mname,
                positions=pos,
                normals=nor,
                uvs=uvs,
                tangents=None,
                indices=None,
                material_info=info,
            ))
        return parts

    # =================================================================
    #  materials
    # =================================================================
    _SOCKET_CANDIDATES = {
        "base_color": ("Base Color",),
        "metallic": ("Metallic",),
        "roughness": ("Roughness",),
        "specular": ("Specular IOR Level", "Specular"),
        "alpha": ("Alpha",),
        "normal": ("Normal",),
        "emission_color": ("Emission Color", "Emission"),
        "emission_strength": ("Emission Strength",),
    }

    @classmethod
    def _decode_materials(cls, blend, mview, blend_dir):
        infos = []
        totcol = mview.get_int("totcol", 0) or 0
        mp = mview.get_ptr("mat")
        if totcol <= 0 or not mp:
            return infos
        r = blend.resolve_ptr(mp)
        if r is None:
            Log.warn(".blend: material pointer array does not resolve")
            return infos
        blk, off = r
        fmt = "I" if blend.ptrsize == 4 else "Q"
        maxn = (blk.size - off) // blend.ptrsize
        n = min(totcol, maxn)
        if n < totcol:
            Log.warn(f".blend: material array truncated "
                     f"({n}/{totcol}) — clamped")
        for i in range(n):
            try:
                ptr = struct.unpack_from(blend.endian + fmt, blk.data,
                                         off + i * blend.ptrsize)[0]
            except struct.error:
                infos.append(None)
                continue
            rr = blend.resolve_ptr(ptr)
            if rr is None:
                infos.append(None)
                continue
            try:
                mv2 = blend.struct_view(rr[0], rr[1], "Material")
                infos.append(cls._decode_material(blend, mv2, blend_dir))
            except Exception as ex:
                Log.warn(f".blend: material decode failed ({ex})")
                infos.append(None)
        return infos

    @classmethod
    def _decode_material(cls, blend, mview, blend_dir):
        name = cls._id_name(mview) or "material"
        r = mview.get_float("r", 0.8)
        g = mview.get_float("g", 0.8)
        b = mview.get_float("b", 0.8)
        a = mview.get_float("a", 1.0)
        r = 0.8 if r is None else r
        g = 0.8 if g is None else g
        b = 0.8 if b is None else b
        a = 1.0 if a is None else a
        metallic = mview.get_float("metallic", None)
        roughness = mview.get_float("roughness", None)
        alpha_cutoff = mview.get_float("alpha_threshold", 0.5)
        if not alpha_cutoff or alpha_cutoff <= 0.0:
            alpha_cutoff = 0.5
        if mview.has("blend_method"):
            bm = mview.get_int("blend_method", 0) or 0
            # 2.8-4.1: 0=OPAQUE 1=CLIP 2=HASHED 3=BLEND
            alpha_mode = {0: "OPAQUE", 1: "MASK", 2: "BLEND",
                          3: "BLEND"}.get(bm, "OPAQUE")
        elif mview.has("surface_render_method"):
            srm = mview.get_int("surface_render_method", 0) or 0
            # 4.2+: 0=DITHERED 1=BLENDED — both map to BLEND
            alpha_mode = {0: "BLEND", 1: "BLEND"}.get(srm, "OPAQUE")
        else:
            alpha_mode = "OPAQUE"
        if mview.has("use_backface_culling"):
            double_sided = not bool(
                mview.get_int("use_backface_culling", 0))
        else:
            double_sided = False
        info = {
            "name": name,
            "base_color": [float(r), float(g), float(b), float(a)],
            # engine Material defaults: metallic 0.0, roughness 0.5
            "metallic": float(metallic) if metallic is not None else 0.0,
            "roughness": (float(roughness) if roughness is not None
                          else 0.5),
            "base_color_tex": None,
            "metallic_roughness_tex": None,
            "normal_tex": None,
            "emissive": [0.0, 0.0, 0.0],
            "emissive_intensity": 1.0,
            "emissive_tex": None,
            "occlusion_tex": None,
            "double_sided": double_sided,
            "alpha_mode": alpha_mode,
            "alpha_cutoff": float(alpha_cutoff),
        }
        use_nodes = mview.get_int("use_nodes", 0) or 0
        ntp = mview.get_ptr("nodetree")
        if use_nodes and ntp:
            try:
                cls._material_from_nodes(blend, info, ntp, blend_dir)
            except Exception as ex:
                Log.warn(f".blend: node material '{name}' partially "
                         f"decoded ({ex}) — using fallbacks")
        return info

    # ---------------------------- node route -------------------------
    @classmethod
    def _node_sockets(cls, blend, node_view, list_field):
        """Walk a bNode's 'inputs'/'outputs' ListBase -> [(addr, view)]."""
        return list(cls._walk_listbase(
            blend, node_view.get_struct(list_field), "bNodeSocket",
            cap=10000))

    @classmethod
    def _match_socket_slot(cls, sock_view):
        ident = sock_view.get_string("identifier", "")
        nm = sock_view.get_string("name", "")
        for slot, cands in cls._SOCKET_CANDIDATES.items():
            if ident in cands or nm in cands:
                return slot
        return None

    @classmethod
    def _socket_default(cls, blend, sock_view):
        """Read a bNodeSocket default_value via SDNA (by field name)."""
        rr = blend.resolve_ptr(sock_view.get_ptr("default_value"))
        if rr is None:
            return None
        blk, off = rr
        try:
            dv = blend.struct_view(blk, off)
        except ValueError:
            return None
        if not dv.has("value"):
            return None
        arr = dv.get_array("value", "f4")
        if arr is not None and len(arr):
            return [float(x) for x in arr]
        fv = dv.get_float("value", None)
        if fv is not None:
            return float(fv)
        iv = dv.get_int("value", None)
        if iv is not None:
            return float(iv)
        return None

    @classmethod
    def _teximage_from_node(cls, blend, node_view, blend_dir):
        if node_view is None:
            return None
        if node_view.get_string("idname", "") != "ShaderNodeTexImage":
            return None
        return cls._image_descriptor(blend, node_view.get_ptr("id"),
                                     blend_dir)

    @classmethod
    def _material_from_nodes(cls, blend, info, ntree_ptr, blend_dir,
                             _depth=0, _seen=None):
        if _seen is None:
            _seen = set()
        if _depth > 4 or ntree_ptr in _seen:
            return
        _seen.add(ntree_ptr)
        rr = blend.resolve_ptr(ntree_ptr)
        if rr is None:
            return
        nt = blend.struct_view(rr[0], rr[1], "bNodeTree")
        nodes = {}    # addr -> view
        for ptr, nv in cls._walk_listbase(blend, nt.get_struct("nodes"),
                                          "bNode"):
            nodes[ptr] = nv
        if not nodes:
            return
        # tosock addr -> fromnode addr
        tosock_map = {}
        for _p, lv in cls._walk_listbase(blend, nt.get_struct("links"),
                                         "bNodeLink"):
            tosock_map[lv.get_ptr("tosock")] = lv.get_ptr("fromnode")
        def _follow_image(sock_addr, expect_normal_map=False):
            from_addr = tosock_map.get(sock_addr)
            if not from_addr:
                return None
            fnode = nodes.get(from_addr)
            if fnode is None:
                rr2 = blend.resolve_ptr(from_addr)
                if rr2 is None:
                    return None
                try:
                    fnode = blend.struct_view(rr2[0], rr2[1], "bNode")
                except ValueError:
                    return None
            idname = fnode.get_string("idname", "")
            if idname == "ShaderNodeTexImage":
                return cls._teximage_from_node(blend, fnode, blend_dir)
            if idname == "ShaderNodeNormalMap":
                # follow ITS "Color" input link to the TexImage
                for sa, sv in cls._node_sockets(blend, fnode, "inputs"):
                    nm = sv.get_string("identifier", "") or \
                        sv.get_string("name", "")
                    if nm == "Color":
                        return _follow_image(sa)
                return None
            return None

        principled = None
        diffuse_fallback = None
        for addr, nv in nodes.items():
            idn = nv.get_string("idname", "")
            if idn == "ShaderNodeBsdfPrincipled":
                principled = nv
                break
            if idn == "ShaderNodeBsdfDiffuse" and diffuse_fallback is None:
                diffuse_fallback = nv
        if principled is None:
            # Materials wrapped in a node GROUP (common in exporter-
            # generated scenes, e.g. armory): descend into the group's
            # own node tree (depth- and cycle-guarded).
            _before_group = dict(info)
            for addr, nv in nodes.items():
                if nv.get_string("idname", "") == "ShaderNodeGroup":
                    sub = nv.get_ptr("id")
                    if sub:
                        cls._material_from_nodes(blend, info, sub,
                                                 blend_dir,
                                                 _depth + 1, _seen)
            if info == _before_group and diffuse_fallback is not None:
                # No Principled BSDF anywhere (legacy/exporter scenes,
                # e.g. armory's Diffuse-based materials): Diffuse BSDF
                # gives base colour ("Color" socket — default RGBA or a
                # linked Image Texture) and, in 4.x, a "Roughness" socket.
                for sock_addr, sv in cls._node_sockets(
                        blend, diffuse_fallback, "inputs"):
                    snm = sv.get_string("identifier", "") or \
                        sv.get_string("name", "")
                    if snm == "Color":
                        if sock_addr in tosock_map:
                            desc = _follow_image(sock_addr)
                            if desc is not None:
                                info["base_color_tex"] = desc
                                # Linked socket: default colour is ignored
                                # by Blender — white factor (see below).
                                info["base_color"] = [
                                    1.0, 1.0, 1.0, info["base_color"][3]]
                        else:
                            val = cls._socket_default(blend, sv)
                            if isinstance(val, list):
                                vals = [float(x) for x in val] + [1.0] * 4
                                info["base_color"] = vals[:4]
                    elif snm == "Roughness" and sock_addr not in tosock_map:
                        val = cls._socket_default(blend, sv)
                        if val is not None:
                            info["roughness"] = float(
                                val[0] if isinstance(val, list) else val)
            return

        for sock_addr, sv in cls._node_sockets(blend, principled,
                                               "inputs"):
            slot = cls._match_socket_slot(sv)
            if slot is None:
                continue
            linked = sock_addr in tosock_map
            if linked:
                desc = _follow_image(sock_addr)
                if desc is not None:
                    if slot == "base_color":
                        info["base_color_tex"] = desc
                        # Blender IGNORES the socket default (and the legacy
                        # Material.r/g/b viewport colour) when the socket is
                        # linked — the texture alone defines the surface
                        # colour.  Reset the factor to white (glTF
                        # baseColorFactor=[1,1,1] with texture) or the
                        # texture would be double-tinted.  Alpha preserved.
                        info["base_color"] = [1.0, 1.0, 1.0,
                                              info["base_color"][3]]
                    elif slot == "normal":
                        info["normal_tex"] = desc
                    elif slot == "emission_color":
                        info["emissive_tex"] = desc
                        # Same linked-socket rule: emission colour factor
                        # becomes white; strength still scales it.
                        info["emissive"] = [1.0, 1.0, 1.0]
                    # metallic/roughness/alpha textures: the engine's
                    # packed metallic_roughness slot cannot represent
                    # Blender's separate greyscale maps — skipped.
                continue
            val = cls._socket_default(blend, sv)
            if val is None:
                continue
            if slot == "base_color" and isinstance(val, list):
                vals = [float(x) for x in val] + [1.0] * 4
                info["base_color"] = vals[:4]
            elif slot == "metallic":
                info["metallic"] = float(
                    val[0] if isinstance(val, list) else val)
            elif slot == "roughness":
                info["roughness"] = float(
                    val[0] if isinstance(val, list) else val)
            elif slot == "alpha":
                av = float(val[0] if isinstance(val, list) else val)
                info["base_color"][3] = av
            elif slot == "emission_color" and isinstance(val, list):
                info["emissive"] = [float(x) for x in val[:3]]
            elif slot == "emission_strength":
                es = float(val[0] if isinstance(val, list) else val)
                info["emissive_intensity"] = es if es else 1.0
        # principled node found but alpha socket untouched: prefer the
        # socket default over Material.a only when a value was read.

    # =================================================================
    #  images
    # =================================================================
    @classmethod
    def _packedfile_bytes(cls, blend, pf_ptr):
        rr = blend.resolve_ptr(pf_ptr)
        if rr is None:
            return None
        try:
            pv = blend.struct_view(rr[0], rr[1], "PackedFile")
        except ValueError:
            return None
        size = pv.get_int("size", 0) or 0
        rd = blend.resolve_ptr(pv.get_ptr("data"))
        if rd is None or size <= 0:
            return None
        blk, off = rd
        if off + size > blk.size:
            size = blk.size - off
        if size <= 0:
            return None
        return ("bytes", bytes(blk.data[off:off + size]))

    @classmethod
    def _image_descriptor(cls, blend, img_ptr, blend_dir):
        rr = blend.resolve_ptr(img_ptr)
        if rr is None:
            return None
        try:
            iv = blend.struct_view(rr[0], rr[1], "Image")
        except ValueError:
            return None
        desc = cls._packedfile_bytes(blend, iv.get_ptr("packedfile"))
        if desc is not None:
            return desc
        if iv.has("packedfiles"):
            for _p, pv in cls._walk_listbase(
                    blend, iv.get_struct("packedfiles"),
                    "ImagePackedFile", cap=10000):
                desc = cls._packedfile_bytes(
                    blend, pv.get_ptr("packedfile"))
                if desc is not None:
                    return desc
        fp = iv.get_string("filepath", "")
        if fp:
            if fp.startswith("//"):
                fp = os.path.join(blend_dir, fp[2:])
            fp = os.path.normpath(fp)
            if os.path.isfile(fp):
                return ("path", os.path.abspath(fp))
            Log.warn(f".blend: image file missing: {fp}")
        return None

    # =================================================================
    #  lights
    # =================================================================
    @classmethod
    def _collect_light(cls, blend, oview, W_eng, name):
        r = oview.resolve("data")
        if r is None:
            Log.warn(f".blend: lamp '{name}' has no data — skipped")
            return
        try:
            lv = blend.struct_view(r[0], r[1])   # "Light" (2.7x "Lamp")
        except ValueError:
            lv = None
            for cand in ("Light", "Lamp"):
                try:
                    lv = blend.struct_view(r[0], r[1], cand)
                    break
                except ValueError:
                    continue
            if lv is None:
                Log.warn(f".blend: lamp '{name}' data unreadable — "
                         f"skipped")
                return
        ltype = lv.get_int("type", 0)
        if ltype is None:
            ltype = 0
        kind = {0: "point", 1: "sun", 2: "spot", 3: "area"}.get(
            int(ltype))
        if kind is None:
            Log.debug(f".blend: lamp '{name}' type {ltype} skipped")
            return
        if kind == "area":
            Log.warn(f".blend: area light '{name}' — mapped to a point "
                     f"light (area lights unsupported)")
        color = [lv.get_float("r", 1.0) or 0.0,
                 lv.get_float("g", 1.0) or 0.0,
                 lv.get_float("b", 1.0) or 0.0]
        color = [c if c else 0.0 for c in color]
        energy = lv.get_float("energy", 10.0)
        if energy is None:
            energy = 10.0
        spotsize = lv.get_float("spotsize", math.radians(45.0)) \
            or math.radians(45.0)
        spotblend = lv.get_float("spotblend", 0.15)
        if spotblend is None:
            spotblend = 0.15
        dist = lv.get_float("dist", 0.0) or 0.0
        mode = lv.get_int("mode", 0) or 0
        if int(mode) & 1:
            Log.debug(f".blend: lamp '{name}' casts shadows in Blender "
                      f"(LA_SHADOW) — engine shadow flags unchanged")
        pos = [float(W_eng[0, 3]), float(W_eng[1, 3]),
               float(W_eng[2, 3])]
        # Blender lights emit along local -Z; convert to engine space.
        dvec = W_eng[:3, :3] @ np.array([0.0, 0.0, -1.0])
        ln = float(np.linalg.norm(dvec))
        if ln < 1e-12:
            dvec = np.array([0.0, -1.0, 0.0])
        else:
            dvec = dvec / ln
        cls._pending_lights.append({
            "kind": kind,
            "name": name,
            "color": (float(color[0]), float(color[1]),
                      float(color[2])),
            "energy": float(energy),
            "position": tuple(pos),
            "direction": (float(dvec[0]), float(dvec[1]),
                          float(dvec[2])),
            "spotsize": float(spotsize),
            "spotblend": float(spotblend),
            "dist": float(dist),
        })
        Log.info(f".blend: imported {kind} light '{name}'")

    @classmethod
    def attach_imported_lights(cls, engine, node):
        """Create engine light nodes from the descriptors collected by
        build_nodes().  Called from load_model_into right after a
        successful .blend import; every child is marked
        _heaven3d_imported_part = ".blend" so the project serializer
        SKIPS it (lights are re-created from the .blend source on load
        instead of duplicating).

        watts -> engine intensity heuristics (best-effort, documented):
          * point/spot intensity = clamp(energy * 0.08, 0.1, 200)
            (a 100 W bulb lands at 8.0, the engine's default point
            intensity);
          * range = Blender custom cutoff 'dist' when set, else
            clamp(sqrt(energy), 5, 200);
          * sun intensity = clamp(energy * 3.0, 0.05, 20) (Blender sun
            energy is W/m^2 — numerically close to the engine's sun
            scale).
        AREA lights were mapped to point during import (warn).
        Never raises: per-light failures Log.warn and continue."""
        lights = cls._pending_lights
        cls._pending_lights = []
        if not lights:
            return

        def _cl(v, a, b):
            return max(a, min(b, float(v)))

        for d in lights:
            try:
                kind = d.get("kind")
                name = d.get("name") or "Light"
                color = d.get("color", (1.0, 1.0, 1.0))
                energy = float(d.get("energy", 10.0))
                dist = float(d.get("dist", 0.0))
                rng = dist if dist > 0.01 else _cl(energy ** 0.5,
                                                 5.0, 200.0)
                intensity = _cl(energy * 0.08, 0.1, 200.0)
                if kind == "sun":
                    cls._attach_sun(engine, d, _cl)
                    continue
                if kind == "spot":
                    child = SceneNode(name, parent=node)
                    child.position = glm.vec3(*d.get(
                        "position", (0.0, 0.0, 0.0)))
                    dx, dy, dz = d.get("direction", (0.0, -1.0, 0.0))
                    pitch = math.degrees(math.asin(_cl(dy, -1.0, 1.0)))
                    yaw = math.degrees(math.atan2(-dx, -dz))
                    child.rotation_euler = glm.vec3(pitch, yaw, 0.0)
                    outer = _cl(math.degrees(
                        float(d.get("spotsize", math.radians(45.0)))),
                        0.1, 179.0)
                    inner = outer * (1.0 - _cl(
                        d.get("spotblend", 0.15), 0.0, 1.0))
                    child.is_spot_light = True
                    child.spot_light = SpotLight(
                        color=glm.vec3(*color),
                        intensity=intensity,
                        range=rng,
                        inner_angle=inner,
                        outer_angle=outer)
                    child._heaven3d_imported_part = ".blend"
                else:   # point (and area, mapped to point)
                    child = SceneNode(name, parent=node)
                    child.position = glm.vec3(*d.get(
                        "position", (0.0, 0.0, 0.0)))
                    child.is_point_light = True
                    child.point_light = PointLight(
                        color=glm.vec3(*color),
                        intensity=intensity,
                        range=rng)
                    child._heaven3d_imported_part = ".blend"
            except Exception as ex:
                Log.warn(f".blend: could not create light "
                         f"'{d.get('name')}': {ex}")

    @classmethod
    def _attach_sun(cls, engine, d, _cl):
        """Update the existing sun node (never duplicate); only create
        one (mirroring Scene._create_default_sun) when none exists."""
        scene = getattr(engine, "scene", None)
        if scene is None:
            return
        dx, dy, dz = d.get("direction", (-0.5, -1.0, -0.3))
        pitch = math.degrees(math.asin(_cl(dy, -1.0, 1.0)))
        yaw = math.degrees(math.atan2(-dx, -dz))
        energy = float(d.get("energy", 1.0))
        color = d.get("color", (1.0, 0.96, 0.9))
        sun = getattr(scene, "_sun_node", None)
        if sun is None:
            try:
                sun = scene.find_by_name("Sun")
            except Exception:
                sun = None
        if sun is not None:
            sun.rotation_euler = glm.vec3(pitch, yaw, 0.0)
            if getattr(sun, "sun_light", None) is not None:
                sun.sun_light.color = glm.vec3(*color)
                sun.sun_light.intensity = _cl(energy * 3.0, 0.05, 20.0)
            Log.info(f".blend: existing Sun updated from "
                     f"'{d.get('name')}'")
            return
        sun = SceneNode("Sun", parent=scene.root)
        sun.is_sun = True
        sun.position = glm.vec3(0.0, 50.0, 0.0)
        sun.rotation_euler = glm.vec3(pitch, yaw, 0.0)
        sun.sun_light = SunLight(direction=glm.vec3(dx, dy, dz),
                                 color=glm.vec3(*color),
                                 intensity=_cl(energy * 3.0, 0.05, 20.0))
        scene._sun_node = sun
        Log.info(f".blend: Sun created from '{d.get('name')}'")

    # =================================================================
    #  entry point — GlbImporter.build_nodes parity
    # =================================================================
    @classmethod
    def _id_name(cls, view):
        try:
            idv = view.get_struct("id")
            nm = idv.get_string("name") if idv is not None else ""
        except Exception:
            nm = ""
        if len(nm) > 2 and nm[:2] in cls._ID_PREFIXES:
            return nm[2:]
        return nm

    @classmethod
    def build_nodes(cls, path):
        """Parse a .blend file and return List[GlbImporter.NodeData]
        roots — one per mesh object, world transform (incl. Z-up->Y-up)
        baked into the vertices, one MeshData per material slot.  Raises
        ValueError on any hard failure (caught by load_model_into)."""
        cls._pending_lights = []
        path = os.fspath(path)
        if not os.path.isfile(path):
            raise ValueError(f".blend file not found: {path}")
        blend = cls._File.load(path)
        blend_dir = os.path.dirname(os.path.abspath(path))
        objects = cls._enumerate_objects(blend)
        roots = []
        cache = {}
        C = cls._ZUP_TO_YUP
        for blk, off in objects:
            try:
                oview = blend.struct_view(blk, off, "Object")
            except ValueError as ex:
                Log.warn(f".blend: object unreadable ({ex}) — skipped")
                continue
            name = cls._id_name(oview) or "object"
            otype = oview.get_int("type", -1)
            if otype == cls._OB_CAMERA:
                Log.info(f".blend: camera '{name}' skipped (cameras are "
                         f"not imported)")
                continue
            W = cls._world_matrix(blend, oview, cache, set())
            W_eng = C @ W
            if otype == cls._OB_LAMP:
                cls._collect_light(blend, oview, W_eng, name)
                continue
            if otype != cls._OB_MESH:
                Log.debug(f".blend: object '{name}' (type {otype}) "
                          f"skipped — only meshes/lamps are imported")
                continue
            mods = oview.get_struct("modifiers")
            if mods is not None and (mods.get_ptr("first")
                                     or mods.get_ptr("last")):
                Log.warn(f"object '{name}' has modifiers — they are NOT "
                         f"evaluated; apply modifiers in Blender before "
                         f"export")
            r = oview.resolve("data")
            if r is None:
                Log.warn(f".blend: mesh object '{name}' has no mesh "
                         f"data — skipped")
                continue
            try:
                mview = blend.struct_view(r[0], r[1], "Mesh")
            except ValueError as ex:
                Log.warn(f".blend: '{name}' data unreadable ({ex}) — "
                         f"skipped")
                continue
            parts = cls._decode_mesh(blend, mview, name, blend_dir)
            if not parts:
                Log.warn(f".blend: mesh '{name}' produced no triangles "
                         f"— skipped")
                continue
            meshes = []
            for md in parts:
                GlbImporter._bake_world_transform(md, W_eng)
                meshes.append(md)
            roots.append(cls.NodeData(
                name=name, matrix=None, translation=None, rotation=None,
                scale=None, meshes=meshes, children=[]))
        if not roots:
            Log.warn(f".blend import: no mesh objects in "
                     f"{os.path.basename(path)}")
        else:
            Log.info(f".blend imported: {os.path.basename(path)} "
                     f"({len(roots)} object(s), "
                     f"{len(cls._pending_lights)} light(s))")
        return roots

# [PATCHB:blend_importer_end]
