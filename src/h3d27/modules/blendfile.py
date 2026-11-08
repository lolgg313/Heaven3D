# @heaven3d-part h3d27/modules/blendfile.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# ------------------------------------------------ h3d_blendfile
# ---------------------------------------------------------------------------
#  Low-level .blend reader: every Blender version from 2.4x to 5.x.
#    * legacy 12-byte header  "BLENDER" + ptr('_'|'-') + endian('v'|'V') + "NNN"
#    * 5.x 17-byte header     "BLENDER" + "17" + "-" + "01" + 'v' + "NNNN"
#      (large BHead: code i32, sdna i32, old u64, len i64, nr i64)
#    * gzip (<= 2.9x "compress") and zstd (3.0+ "compress") containers
#    * 32/64-bit pointers, little AND big endian
#  Fields are always resolved by NAME through the file's own SDNA, so no
#  byte offsets are hard-coded anywhere.
# ---------------------------------------------------------------------------
import os, struct, bisect, gzip, io
import numpy as np


class BlendFormatError(ValueError):
    pass


def _decompress(data, path):
    if data[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(data)
        except Exception as ex:
            raise BlendFormatError(f"gzip-compressed .blend could not be "
                                   f"decompressed: {ex}")
    if data[:4] == b"\x28\xb5\x2f\xfd":
        # Blender 3.0+ writes a SEEKABLE zstd stream = several frames.
        try:                                   # Python 3.14+
            from compression import zstd as _z
            return _z.decompress(data)
        except ImportError:
            pass
        try:
            import zstandard as _zs
            with _zs.ZstdDecompressor().stream_reader(
                    io.BytesIO(data), read_across_frames=True) as r:
                return r.read()
        except ImportError:
            raise BlendFormatError(
                f"'{os.path.basename(path)}' was saved with Blender's "
                f"'Compress' option (zstd). Install the decoder with:  "
                f"pip install zstandard   (or re-save the file in Blender "
                f"with File > Save As > Compress turned off).")
        except Exception as ex:
            raise BlendFormatError(f"zstd-compressed .blend could not be "
                                   f"decompressed: {ex}")
    return data


class SDNAField:
    __slots__ = ("name", "type", "offset", "size", "is_ptr", "dims",
                 "elem_size", "is_func")

    def __init__(self, name, type_, offset, size, is_ptr, dims, elem_size,
                 is_func):
        self.name, self.type, self.offset, self.size = name, type_, offset, size
        self.is_ptr, self.dims, self.elem_size = is_ptr, dims, elem_size
        self.is_func = is_func


class SDNA:
    def __init__(self, raw, endian, ptrsize):
        self.endian = endian
        self.ptrsize = ptrsize
        p = 0
        u32 = struct.Struct(endian + "I")
        u16 = struct.Struct(endian + "H")
        i16 = struct.Struct(endian + "h")

        def tag(t):
            nonlocal p
            if raw[p:p + 4] != t:
                raise BlendFormatError(f"corrupt SDNA (expected {t!r})")
            p += 4

        def align():
            nonlocal p
            p = (p + 3) & ~3

        def strings(n):
            nonlocal p
            out = []
            for _ in range(n):
                e = raw.index(b"\0", p)
                out.append(raw[p:e].decode("latin-1"))
                p = e + 1
            return out

        tag(b"SDNA")
        tag(b"NAME")
        n = u32.unpack_from(raw, p)[0]; p += 4
        self.names = strings(n)
        align(); tag(b"TYPE")
        n = u32.unpack_from(raw, p)[0]; p += 4
        self.types = strings(n)
        align(); tag(b"TLEN")
        self.tlen = [u16.unpack_from(raw, p + 2 * i)[0] for i in range(n)]
        p += 2 * n
        align(); tag(b"STRC")
        n = u32.unpack_from(raw, p)[0]; p += 4
        self.struct_type = []           # sdna index -> type index
        self.structs = {}               # struct name -> {field: SDNAField}
        self.struct_by_index = []       # sdna index -> struct name
        self.struct_size = {}
        type_to_struct = {}
        raw_structs = []
        for si in range(n):
            t, nf = u16.unpack_from(raw, p)[0], u16.unpack_from(raw, p + 2)[0]
            p += 4
            fl = []
            for _ in range(nf):
                ft, fn = u16.unpack_from(raw, p)[0], u16.unpack_from(raw, p + 2)[0]
                p += 4
                fl.append((ft, fn))
            raw_structs.append((t, fl))
            type_to_struct[t] = si
        for si, (t, fl) in enumerate(raw_structs):
            sname = self.types[t]
            self.struct_by_index.append(sname)
            self.struct_type.append(t)
            fields = {}
            off = 0
            for ft, fn in fl:
                full = self.names[fn]
                is_func = full.startswith("(*")
                is_ptr = full.startswith("*") or is_func
                base = full
                dims = []
                if "[" in full:
                    base = full[:full.index("[")]
                    rest = full[full.index("["):]
                    for part in rest.split("]")[:-1]:
                        dims.append(int(part.lstrip("[")))
                base = base.lstrip("*")
                if is_func:
                    base = full[2:full.index(")")]
                elem = ptrsize if is_ptr else self.tlen[ft]
                count = 1
                for d in dims:
                    count *= d
                size = elem * count
                fields[base] = SDNAField(base, self.types[ft], off, size,
                                         is_ptr, dims, elem, is_func)
                off += size
            self.structs[sname] = fields
            self.struct_size[sname] = self.tlen[t]

    def struct_name(self, sdna_index):
        if 0 <= sdna_index < len(self.struct_by_index):
            return self.struct_by_index[sdna_index]
        return None


class Block:
    __slots__ = ("code", "sdna", "old", "size", "count", "data", "struct",
                 "scope")

    def __init__(self, code, sdna, old, size, count, data):
        # 2-letter ID codes are written as an int: big-endian files store
        # "ME" as b"\0\0ME" — normalise to b"ME\0\0" for every endianness.
        if code[:2] == b"\0\0" and code[2:] != b"\0\0":
            code = code[2:] + b"\0\0"
        self.code, self.sdna, self.old = code, sdna, old
        self.size, self.count, self.data = size, count, data
        self.struct = None
        self.scope = 0


class BlendFile:
    """Parsed .blend file: blocks + SDNA + pointer resolution."""

    def __init__(self, path):
        self.path = os.fspath(path)
        with open(self.path, "rb") as fh:
            data = fh.read()
        data = _decompress(data, self.path)
        if data[:7] != b"BLENDER":
            raise BlendFormatError(
                f"'{os.path.basename(self.path)}' is not a Blender file")
        self.raw = data
        if data[7:9] == b"17" and data[9:10] == b"-":
            # Blender 5.x header: BLENDER17-01v0500
            fmt = int(data[10:12])
            if fmt != 1:
                raise BlendFormatError(
                    f"unknown .blend file-format version {fmt} — this file "
                    f"is newer than Heaven3D understands")
            self.ptrsize = 8
            self.endian = "<" if data[12:13] == b"v" else ">"
            self.version = int(data[13:17])
            hdr_len = 17
            self.large_bhead = True
        else:
            self.ptrsize = {b"_": 4, b"-": 8}.get(data[7:8])
            self.endian = {b"v": "<", b"V": ">"}.get(data[8:9])
            if self.ptrsize is None or self.endian is None:
                raise BlendFormatError("corrupt .blend header")
            self.version = int(data[9:12])
            hdr_len = 12
            self.large_bhead = False
        self.blocks = []
        self._parse_blocks(hdr_len)
        dna = next((b for b in self.blocks if b.code == b"DNA1"), None)
        if dna is None:
            raise BlendFormatError("corrupt .blend: no SDNA block")
        self.sdna = SDNA(dna.data, self.endian, self.ptrsize)
        for b in self.blocks:
            b.struct = self.sdna.struct_name(b.sdna)
        # Address scopes.  Blender 5.x writes DETERMINISTIC per-ID
        # addresses, so DATA pointers are only unique inside the group
        # "ID block + the DATA blocks that follow it".  Pointers are
        # resolved inside their own scope first, then file-wide.
        scope = 0
        self._scopes = {0: []}
        for b in self.blocks:
            if b.code != b"DATA":
                scope += 1
                self._scopes[scope] = []
            b.scope = scope
            if b.old:
                self._scopes[scope].append(b)
        self._scope_index = {}
        for sc, bl in self._scopes.items():
            bl.sort(key=lambda b: b.old)
            self._scope_index[sc] = ([b.old for b in bl], bl)
        live = [b for b in self.blocks if b.old]
        # file-wide fallback: ID blocks win over DATA blocks on collision
        live.sort(key=lambda b: (b.old, b.code == b"DATA"))
        self._addrs = [b.old for b in live]
        self._live = live
        self._by_code = {}
        for b in self.blocks:
            self._by_code.setdefault(b.code, []).append(b)

    # ------------------------------------------------------------ blocks
    def _parse_blocks(self, pos):
        d, e = self.raw, self.endian
        n = len(d)
        if self.large_bhead:
            st = struct.Struct(e + "4siQqq")       # code sdna old len nr
            hs = st.size
            while pos + hs <= n:
                code, sdna, old, size, nr = st.unpack_from(d, pos)
                pos += hs
                if size < 0 or pos + size > n:
                    raise BlendFormatError("truncated .blend (block data)")
                self.blocks.append(Block(code, sdna, old, size, nr,
                                         d[pos:pos + size]))
                pos += size
                if code == b"ENDB":
                    return
        else:
            ps = self.ptrsize
            st = struct.Struct(e + ("4sII" if ps == 4 else "4sIQ") + "II")
            hs = st.size
            while pos + hs <= n:
                code, size, old, sdna, nr = st.unpack_from(d, pos)
                pos += hs
                if pos + size > n:
                    raise BlendFormatError("truncated .blend (block data)")
                self.blocks.append(Block(code, sdna, old, size, nr,
                                         d[pos:pos + size]))
                pos += size
                if code == b"ENDB":
                    return
        # ENDB missing: tolerate (some writers), as long as DNA1 was read

    def _ptr_at(self, blk, off):
        n = self.ptrsize
        if off + n > len(blk.data):
            return 0
        return struct.unpack_from(self.endian + ("I" if n == 4 else "Q"),
                                  blk.data, off)[0]

    def blocks_by_code(self, code):
        return self._by_code.get(code, [])

    @staticmethod
    def _lookup(addrs, blocks, ptr):
        i = bisect.bisect_right(addrs, ptr) - 1
        if i < 0:
            return None
        # several blocks may share the start address: take the first
        # (lowest index) one whose range contains the pointer
        j = bisect.bisect_left(addrs, addrs[i])
        for k in range(j, i + 1):
            b = blocks[k]
            off = ptr - b.old
            if off < max(b.size, 1):
                return b, off
        return None

    def resolve(self, ptr, ctx=None):
        """old pointer -> (block, offset) or None.  ctx = the Block (or
        View) the pointer was read from: its ID scope is searched first."""
        if not ptr:
            return None
        if ctx is not None:
            blk = ctx.blk if isinstance(ctx, View) else ctx
            sc = self._scope_index.get(getattr(blk, "scope", -1))
            if sc is not None and sc[0]:
                r = self._lookup(sc[0], sc[1], ptr)
                if r is not None:
                    return r
        return self._lookup(self._addrs, self._live, ptr)

    def view(self, ptr_or_block, sname=None, off=0, ctx=None):
        """Struct view of a pointer, or of (block, offset)."""
        if isinstance(ptr_or_block, Block):
            blk = ptr_or_block
        else:
            r = self.resolve(ptr_or_block, ctx)
            if r is None:
                return None
            blk, off = r
        sname = sname or blk.struct
        if sname not in self.sdna.structs:
            return None
        return View(self, blk, off, sname)

    def array(self, ptr, dtype, count=None, ctx=None):
        """Raw typed array at a pointer (whole block if count is None)."""
        r = self.resolve(ptr, ctx)
        if r is None:
            return None
        blk, off = r
        dt = np.dtype(dtype).newbyteorder(self.endian)
        avail = (blk.size - off) // dt.itemsize
        if count is None:
            count = avail
        if count > avail or count < 0:
            return None
        return np.frombuffer(blk.data, dtype=dt, count=count, offset=off)

    def struct_array(self, ptr, sname, count=None, ctx=None):
        """List of Views for an array of structs at a pointer."""
        r = self.resolve(ptr, ctx)
        if r is None:
            return []
        blk, off = r
        ss = self.sdna.struct_size.get(sname)
        if not ss:
            return []
        avail = (blk.size - off) // ss
        if count is None:
            count = avail
        count = max(0, min(count, avail))
        return [View(self, blk, off + i * ss, sname) for i in range(count)]

    def struct_records(self, ptr, sname, count, fields, ctx=None):
        """Fast numpy structured read of selected fields of a struct array.
        fields: {name: numpy dtype str}  ->  dict name -> ndarray."""
        r = self.resolve(ptr, ctx)
        if r is None:
            return None
        blk, off = r
        ss = self.sdna.struct_size.get(sname)
        sf = self.sdna.structs.get(sname)
        if not ss or sf is None:
            return None
        avail = (blk.size - off) // ss
        if count is None:
            count = avail
        if count > avail:
            count = avail
        names, formats, offsets = [], [], []
        for fname, dt in fields.items():
            f = sf.get(fname)
            if f is None:
                continue
            base = np.dtype(dt).newbyteorder(self.endian)
            if f.dims:
                shape = tuple(f.dims)
                if base.itemsize * int(np.prod(shape)) > f.size:
                    continue
                formats.append((base, shape))
            else:
                if base.itemsize > f.size:
                    continue
                formats.append(base)
            names.append(fname)
            offsets.append(f.offset)
        if not names:
            return {}
        dt = np.dtype({"names": names, "formats": formats,
                       "offsets": offsets, "itemsize": ss})
        rec = np.frombuffer(blk.data, dtype=dt, count=count, offset=off)
        return {n: rec[n] for n in names}

    def listbase(self, lb_view, sname=None, cap=200000):
        """Walk a ListBase {first,last} -> Views (next pointer chain)."""
        out = []
        if lb_view is None:
            return out
        ptr = lb_view.ptr("first")
        ctx = lb_view
        seen = set()
        while ptr and ptr not in seen and len(out) < cap:
            seen.add(ptr)
            v = self.view(ptr, sname, ctx=ctx)
            if v is None:
                break
            out.append(v)
            ctx = v
            # every ListBase element starts with {next, prev}: read the
            # link at offset 0 (works for nested headers like ModifierData)
            ptr = v.ptr("next") if "next" in v.fields else self._ptr_at(v.blk, v.off)
        return out


class View:
    __slots__ = ("bf", "blk", "off", "sname", "fields")

    def __init__(self, bf, blk, off, sname):
        self.bf, self.blk, self.off, self.sname = bf, blk, off, sname
        self.fields = bf.sdna.structs[sname]

    def has(self, name):
        return name in self.fields

    def _pos(self, f):
        p = self.off + f.offset
        if p < 0 or p + f.size > len(self.blk.data):
            return None
        return p

    def int(self, name, default=0):
        f = self.fields.get(name)
        if f is None or f.is_ptr:
            return default
        p = self._pos(f)
        if p is None:
            return default
        t = f.type
        uns = t.startswith("u") or t in ("bool", "char")
        code = {1: "B" if uns else "b", 2: "H" if uns else "h",
                4: "I" if uns else "i", 8: "Q" if uns else "q"}.get(f.elem_size)
        if code is None:
            return default
        return struct.unpack_from(self.bf.endian + code, self.blk.data, p)[0]

    def float(self, name, default=0.0):
        f = self.fields.get(name)
        if f is None or f.is_ptr or f.type not in ("float", "double"):
            return default
        p = self._pos(f)
        if p is None:
            return default
        return struct.unpack_from(self.bf.endian + ("f" if f.elem_size == 4 else "d"),
                                  self.blk.data, p)[0]

    def floats(self, name, default=None):
        f = self.fields.get(name)
        if f is None or f.is_ptr or f.type not in ("float", "double"):
            return default
        p = self._pos(f)
        if p is None:
            return default
        dt = np.dtype("f4" if f.elem_size == 4 else "f8").newbyteorder(self.bf.endian)
        a = np.frombuffer(self.blk.data, dtype=dt, count=f.size // f.elem_size, offset=p)
        return a.astype(np.float64).reshape(f.dims) if f.dims else a.astype(np.float64)

    def ints(self, name, default=None):
        f = self.fields.get(name)
        if f is None or f.is_ptr:
            return default
        p = self._pos(f)
        if p is None:
            return default
        uns = f.type.startswith("u") or f.type in ("char",)
        code = {1: "u1" if uns else "i1", 2: "u2" if uns else "i2",
                4: "u4" if uns else "i4", 8: "u8" if uns else "i8"}.get(f.elem_size)
        dt = np.dtype(code).newbyteorder(self.bf.endian)
        a = np.frombuffer(self.blk.data, dtype=dt, count=f.size // f.elem_size, offset=p)
        return a.reshape(f.dims) if f.dims else a

    def ptr(self, name):
        f = self.fields.get(name)
        if f is None or not f.is_ptr:
            return 0
        p = self._pos(f)
        if p is None:
            return 0
        return struct.unpack_from(self.bf.endian + ("I" if self.bf.ptrsize == 4 else "Q"),
                                  self.blk.data, p)[0]

    def deref(self, name, sname=None):
        return self.bf.view(self.ptr(name), sname, ctx=self)

    def resolve(self, name):
        return self.bf.resolve(self.ptr(name), self)

    def array(self, name, dtype, count=None):
        return self.bf.array(self.ptr(name), dtype, count, ctx=self)

    def struct_array(self, name, sname, count=None):
        return self.bf.struct_array(self.ptr(name), sname, count, ctx=self)

    def cstring(self, name):
        """char* field -> python str (or '')."""
        r = self.bf.resolve(self.ptr(name), self)
        if r is None:
            return ""
        blk, off = r
        z = blk.data.find(b"\0", off)
        return blk.data[off:z if z >= 0 else len(blk.data)].decode(
            "utf-8", "replace")

    def str(self, name, default=""):
        f = self.fields.get(name)
        if f is None or f.is_ptr:
            return default
        p = self._pos(f)
        if p is None:
            return default
        raw = self.blk.data[p:p + f.size]
        z = raw.find(b"\0")
        if z >= 0:
            raw = raw[:z]
        return raw.decode("utf-8", "replace")

    def sub(self, name):
        """Embedded struct field."""
        f = self.fields.get(name)
        if f is None or f.is_ptr or f.type not in self.bf.sdna.structs:
            return None
        return View(self.bf, self.blk, self.off + f.offset, f.type)

    def id_name(self):
        idv = self.sub("id")
        nm = idv.str("name") if idv is not None else ""
        return nm[2:] if len(nm) > 2 else nm

    def addr(self):
        return self.blk.old + self.off