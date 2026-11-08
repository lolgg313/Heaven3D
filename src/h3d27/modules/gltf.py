# @heaven3d-part h3d27/modules/gltf.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# ------------------------------------------------ h3d_gltf
# ---------------------------------------------------------------------------
#  Heaven3D glTF 2.0 / GLB importer (pure Python + numpy, spec-complete for
#  everything a static renderer can show).
#  Output: list of material "parts" with the node hierarchy, skins (default
#  pose), morph weights and GPU instancing baked into world space, plus
#  KHR_lights_punctual descriptors.
# ---------------------------------------------------------------------------
import os, io, json, math, struct, base64
from urllib.parse import unquote
import numpy as np

_COMP = {5120: "i1", 5121: "u1", 5122: "i2", 5123: "u2", 5125: "u4", 5126: "f4"}
_NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}
_NORM_DIV = {"i1": 127.0, "u1": 255.0, "i2": 32767.0, "u2": 65535.0}


class GltfError(ValueError):
    pass


def _log(logger, level, msg):
    if logger is not None:
        try:
            getattr(logger, level)(msg)
        except Exception:
            pass


def _trs_matrix(t, r, s):
    M = np.eye(4)
    x, y, z, w = r
    n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    R = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                  [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                  [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
    M[:3, :3] = R * np.asarray(s, float)[None, :]
    M[:3, 3] = t
    return M


def _quat_mats(q):
    q = np.asarray(q, np.float64)
    q = q / np.maximum(np.linalg.norm(q, axis=1, keepdims=True), 1e-20)
    x, y, z, w = q.T
    R = np.empty((len(q), 3, 3))
    R[:, 0, 0] = 1 - 2 * (y * y + z * z); R[:, 0, 1] = 2 * (x * y - z * w); R[:, 0, 2] = 2 * (x * z + y * w)
    R[:, 1, 0] = 2 * (x * y + z * w); R[:, 1, 1] = 1 - 2 * (x * x + z * z); R[:, 1, 2] = 2 * (y * z - x * w)
    R[:, 2, 0] = 2 * (x * z - y * w); R[:, 2, 1] = 2 * (y * z + x * w); R[:, 2, 2] = 1 - 2 * (x * x + y * y)
    return R


class GltfDocument:
    def __init__(self, path, logger=None):
        self.path = os.fspath(path)
        self.dir = os.path.dirname(os.path.abspath(self.path))
        self.log = logger
        self.bin_chunk = None
        self._buffers = {}
        self._views = {}
        self._acc = {}
        self._images = {}
        self.warned = set()
        with open(self.path, "rb") as fh:
            data = fh.read()
        if data[:4] == b"glTF":
            ver, length = struct.unpack_from("<II", data, 4)
            if ver != 2:
                raise GltfError(f"GLB version {ver} is not supported (glTF 2.0 only) — "
                                f"re-export the model as glTF 2.0")
            pos = 12
            js = None
            while pos + 8 <= min(len(data), length):
                clen, ctype = struct.unpack_from("<II", data, pos)
                chunk = data[pos + 8:pos + 8 + clen]
                if ctype == 0x4E4F534A:          # JSON
                    js = chunk
                elif ctype == 0x004E4942 and self.bin_chunk is None:   # BIN
                    self.bin_chunk = chunk
                pos += 8 + clen + ((4 - clen % 4) % 4 if clen % 4 else 0)
            if js is None:
                raise GltfError("GLB has no JSON chunk")
            self.J = json.loads(js.decode("utf-8"))
        else:
            txt = data.decode("utf-8-sig")
            self.J = json.loads(txt)
        asset = self.J.get("asset", {})
        if str(asset.get("version", "2.0"))[:1] == "1":
            raise GltfError("glTF 1.0 files are not supported — re-export as glTF 2.0")
        req = set(self.J.get("extensionsRequired", []))
        self.supported_ext = {
            "KHR_materials_unlit", "KHR_texture_transform", "KHR_materials_emissive_strength",
            "KHR_materials_pbrSpecularGlossiness", "KHR_mesh_quantization", "EXT_mesh_gpu_instancing",
            "KHR_lights_punctual", "KHR_node_visibility", "EXT_texture_webp", "EXT_meshopt_compression",
            "KHR_meshopt_compression", "KHR_draco_mesh_compression", "KHR_materials_transmission",
            "KHR_materials_volume", "KHR_materials_ior", "KHR_materials_specular",
            "KHR_materials_clearcoat", "KHR_materials_sheen", "KHR_materials_variants",
            "KHR_texture_basisu", "EXT_texture_avif", "KHR_materials_dispersion",
            "KHR_materials_iridescence", "KHR_materials_anisotropy", "KHR_materials_diffuse_transmission",
            "KHR_xmp_json_ld", "KHR_animation_pointer"}
        for e in req - self.supported_ext:
            self.warn(e, f"glTF requires extension '{e}' which Heaven3D can't decode — "
                         f"the model may look different")

    def warn(self, key, msg):
        if key not in self.warned:
            self.warned.add(key)
            _log(self.log, "warn", msg)

    # ------------------------------------------------------------ buffers
    def _uri_bytes(self, uri):
        if uri.startswith("data:"):
            head, _, payload = uri.partition(",")
            if ";base64" in head:
                return base64.b64decode(payload)
            return unquote(payload).encode("latin-1")
        for cand in (unquote(uri), uri):
            p = cand if os.path.isabs(cand) else os.path.join(self.dir, cand)
            if os.path.isfile(p):
                with open(p, "rb") as fh:
                    return fh.read()
        raise GltfError(f"glTF resource '{unquote(uri)}' not found next to "
                        f"{os.path.basename(self.path)}")

    def buffer(self, i):
        if i in self._buffers:
            return self._buffers[i]
        b = self.J["buffers"][i]
        if "uri" in b:
            data = self._uri_bytes(b["uri"])
        elif self.bin_chunk is not None and i == 0:
            data = self.bin_chunk
        else:
            ext = b.get("extensions", {})
            if "EXT_meshopt_compression" in ext or "KHR_meshopt_compression" in ext or \
                    any(k for k in ext):
                data = b""            # fallback buffer: only reached through compressed views
            else:
                data = self.bin_chunk or b""
        self._buffers[i] = data
        return data

    def view(self, i):
        """bufferView -> bytes (meshopt-decoded when needed)."""
        if i in self._views:
            return self._views[i]
        bv = self.J["bufferViews"][i]
        ext = bv.get("extensions", {})
        mo = ext.get("EXT_meshopt_compression") or ext.get("KHR_meshopt_compression")
        if mo is not None:
            src = self.buffer(mo["buffer"])
            off = mo.get("byteOffset", 0)
            comp = src[off:off + mo["byteLength"]]
            data = decode_meshopt(comp, mo["count"], mo["byteStride"], mo.get("mode", "ATTRIBUTES"),
                                  mo.get("filter", "NONE"))
        else:
            buf = self.buffer(bv["buffer"])
            off = bv.get("byteOffset", 0)
            data = buf[off:off + bv["byteLength"]]
            if len(data) < bv["byteLength"]:
                raise GltfError("glTF buffer is shorter than its bufferView (truncated file?)")
        self._views[i] = data
        return data

    def _read_array(self, view_idx, byte_offset, ctype, nc, count, stride_override=None):
        dt = np.dtype(ctype).newbyteorder("<")
        elem = dt.itemsize * nc
        data = self.view(view_idx)
        bv = self.J["bufferViews"][view_idx]
        stride = stride_override or bv.get("byteStride") or elem
        mo = (bv.get("extensions", {}).get("EXT_meshopt_compression")
              or bv.get("extensions", {}).get("KHR_meshopt_compression"))
        if mo is not None:
            stride = mo["byteStride"]
        need = byte_offset + (count - 1) * stride + elem if count else 0
        if need > len(data):
            raise GltfError("glTF accessor reads past the end of its bufferView")
        if stride == elem:
            return np.frombuffer(data, dt, count * nc, byte_offset).reshape(count, nc)
        raw = np.frombuffer(data, np.uint8, len(data) - byte_offset, byte_offset)
        if len(raw) < count * stride:
            raw = np.concatenate([raw, np.zeros(count * stride - len(raw), np.uint8)])
        rows = raw[:count * stride].reshape(count, stride)[:, :elem]
        return np.ascontiguousarray(rows).view(dt).reshape(count, nc)

    def accessor(self, i, as_float=True):
        key = (i, as_float)
        if key in self._acc:
            return self._acc[key]
        a = self.J["accessors"][i]
        ctype = _COMP[a["componentType"]]
        nc = _NCOMP[a["type"]]
        count = a["count"]
        if a["type"] in ("MAT2", "MAT3") and np.dtype(ctype).itemsize < 4:
            # matrix columns are 4-byte aligned
            rows = 2 if a["type"] == "MAT2" else 3
            colb = (rows * np.dtype(ctype).itemsize + 3) & ~3
            cols = self._read_array(a["bufferView"], a.get("byteOffset", 0), "u1", colb * rows, count)
            arr = np.concatenate([cols[:, c * colb:c * colb + rows * np.dtype(ctype).itemsize]
                                  .copy().view(np.dtype(ctype).newbyteorder("<")) for c in range(rows)], 1)
        elif "bufferView" in a:
            arr = self._read_array(a["bufferView"], a.get("byteOffset", 0), ctype, nc, count)
        else:
            arr = np.zeros((count, nc), np.dtype(ctype))
        sp = a.get("sparse")
        if sp:
            arr = np.array(arr, copy=True)
            ic = _COMP[sp["indices"]["componentType"]]
            idx = self._read_array(sp["indices"]["bufferView"], sp["indices"].get("byteOffset", 0),
                                   ic, 1, sp["count"])[:, 0].astype(np.int64)
            val = self._read_array(sp["values"]["bufferView"], sp["values"].get("byteOffset", 0),
                                   ctype, nc, sp["count"])
            arr[idx] = val
        if as_float:
            if a.get("normalized") and ctype in _NORM_DIV:
                out = np.maximum(arr.astype(np.float64) / _NORM_DIV[ctype], -1.0)
            else:
                out = arr.astype(np.float64)
        else:
            out = arr.astype(np.int64)
        self._acc[key] = out
        return out

    # ------------------------------------------------------------ images
    def image_descriptor(self, tex_index):
        """texture index -> ('bytes'|'path', payload) or None."""
        if tex_index is None:
            return None
        texs = self.J.get("textures", [])
        if not (0 <= tex_index < len(texs)):
            return None
        t = texs[tex_index]
        src = t.get("source")
        ext = t.get("extensions", {})
        for name in ("EXT_texture_webp", "EXT_texture_avif", "KHR_texture_basisu"):
            if name in ext and "source" in ext[name]:
                cand = ext[name]["source"]
                if name == "KHR_texture_basisu":
                    if src is None:
                        self.warn("ktx2", "glTF uses KTX2/Basis textures with no PNG/JPEG "
                                          "fallback — those textures can't be shown")
                    continue
                src = cand if (name != "EXT_texture_avif" or src is None) else src
                break
        if src is None:
            return None
        if src in self._images:
            return self._images[src]
        im = self.J["images"][src]
        desc = None
        try:
            if "bufferView" in im:
                desc = ("bytes", bytes(self.view(im["bufferView"])))
            elif "uri" in im:
                uri = im["uri"]
                if uri.startswith("data:"):
                    desc = ("bytes", self._uri_bytes(uri))
                else:
                    p = os.path.join(self.dir, unquote(uri))
                    if not os.path.isfile(p):
                        p = os.path.join(self.dir, uri)
                    if os.path.isfile(p):
                        desc = ("path", os.path.abspath(p))
                    else:
                        self.warn("img" + uri, f"glTF texture '{unquote(uri)}' not found")
        except Exception as ex:
            self.warn("img%d" % src, f"glTF image {src} unreadable: {ex}")
        self._images[src] = desc
        return desc

    def sampler_wrap(self, tex_index):
        texs = self.J.get("textures", [])
        if tex_index is None or not (0 <= tex_index < len(texs)):
            return (0, 0)
        si = texs[tex_index].get("sampler")
        if si is None:
            return (0, 0)
        s = self.J.get("samplers", [])[si]
        m = {10497: 0, 33071: 1, 33648: 2}
        return (m.get(s.get("wrapS", 10497), 0), m.get(s.get("wrapT", 10497), 0))


# ======================================================================
#  materials
# ======================================================================
def _engine_color(lin):
    """glTF factors are LINEAR; the engine shader gamma-decodes (pow 2.2)."""
    return [float(max(c, 0.0)) ** (1.0 / 2.2) for c in lin]


def _uv_xform(tinfo):
    """KHR_texture_transform -> 3x3 matrix in the ENGINE's uv space
    (v flipped: v_engine = 1 - v_gltf)."""
    ext = (tinfo or {}).get("extensions", {}).get("KHR_texture_transform")
    if not ext:
        return None, None
    off = ext.get("offset", [0.0, 0.0]); rot = ext.get("rotation", 0.0)
    sc = ext.get("scale", [1.0, 1.0])
    c, s = math.cos(rot), math.sin(rot)
    T = np.array([[1, 0, off[0]], [0, 1, off[1]], [0, 0, 1.0]])
    R = np.array([[c, s, 0], [-s, c, 0], [0, 0, 1.0]])
    S = np.array([[sc[0], 0, 0], [0, sc[1], 0], [0, 0, 1.0]])
    M = T @ R @ S
    F = np.array([[1, 0, 0], [0, -1, 1], [0, 0, 1.0]])      # v -> 1-v (self-inverse)
    return F @ M @ F, ext.get("texCoord")


def _slot(doc, tinfo, extra=None):
    if not tinfo or "index" not in tinfo:
        return None
    desc = doc.image_descriptor(tinfo["index"])
    if desc is None:
        return None
    xf, tc_override = _uv_xform(tinfo)
    uvset = tinfo.get("texCoord", 0) if tc_override is None else tc_override
    ws, wt = doc.sampler_wrap(tinfo["index"])
    d = {"desc": desc, "uvset": int(uvset), "xf": xf, "wrap": ws, "wrap_t": wt}
    if extra:
        d.update(extra)
    return d


def _load_rgba(desc):
    from PIL import Image
    kind, payload = desc
    img = Image.open(io.BytesIO(payload) if kind == "bytes" else payload)
    return img.convert("RGBA")


def _sg_solve(diffuse, specular, gloss):
    """KHR_materials_pbrSpecularGlossiness -> metallic/roughness (the
    standard conversion used by the Khronos tools).  Linear inputs, (...,3)."""
    eps, dspec = 1e-6, 0.04
    one_minus = 1.0 - specular.max(-1)

    def bright(c):
        return np.sqrt(0.299 * c[..., 0] ** 2 + 0.587 * c[..., 1] ** 2 + 0.114 * c[..., 2] ** 2)
    pd, ps = bright(diffuse), bright(specular)
    a = dspec
    b = pd * one_minus / (1 - a) + ps - 2 * a
    c = a - ps
    D = np.maximum(b * b - 4 * a * c, 0.0)
    metallic = np.clip((-b + np.sqrt(D)) / (2 * a), 0.0, 1.0)
    metallic = np.where(ps < a, 0.0, metallic)
    m = metallic[..., None]
    from_d = diffuse * (one_minus[..., None] / (1 - dspec) / np.maximum(1 - m, eps))
    from_s = (specular - dspec * (1 - m)) / np.maximum(m, eps)
    base = np.clip(from_d + (from_s - from_d) * (m * m), 0.0, 1.0)
    return base, metallic, 1.0 - gloss


def _specgloss_convert(doc, sg, info, slots):
    """Fill base colour + metal/rough (factors or baked textures)."""
    dfac = np.asarray(sg.get("diffuseFactor", [1, 1, 1, 1]), float)
    sfac = np.asarray(sg.get("specularFactor", [1, 1, 1]), float)
    gfac = float(sg.get("glossinessFactor", 1.0))
    dslot = _slot(doc, sg.get("diffuseTexture"))
    sslot = _slot(doc, sg.get("specularGlossinessTexture"))
    if dslot is None and sslot is None:
        base, met, rough = _sg_solve(dfac[None, :3], sfac[None, :3], np.array([gfac]))
        return list(base[0]) + [float(dfac[3])], float(met[0]), float(rough[0])
    try:
        from PIL import Image
        imgs = [(_load_rgba(x["desc"]) if x else None) for x in (dslot, sslot)]
        size = max((im.size for im in imgs if im is not None), key=lambda z: z[0] * z[1])
        def arr(im, default):
            if im is None:
                return np.tile(np.asarray(default, np.float64), (size[1], size[0], 1))
            return np.asarray(im.resize(size), np.float64) / 255.0
        d = arr(imgs[0], [1, 1, 1, 1]); sgl = arr(imgs[1], [1, 1, 1, 1])
        diffuse = srgb_to_linear(d[..., :3]) * dfac[:3]
        specular = srgb_to_linear(sgl[..., :3]) * sfac
        gloss = sgl[..., 3] * gfac
        base, met, rough = _sg_solve(diffuse, specular, gloss)
        bc = np.concatenate([linear_to_srgb(base), (d[..., 3:4] * dfac[3])], -1)
        out_b = io.BytesIO(); Image.fromarray(np.clip(bc * 255 + 0.5, 0, 255).astype(np.uint8), "RGBA").save(out_b, "PNG")
        mr = np.zeros(base.shape[:2] + (3,), np.uint8); mr[..., 0] = 255
        mr[..., 1] = np.clip(rough * 255 + 0.5, 0, 255); mr[..., 2] = np.clip(met * 255 + 0.5, 0, 255)
        out_m = io.BytesIO(); Image.fromarray(mr, "RGB").save(out_m, "PNG")
        ref = dslot or sslot
        slots["base_color"] = dict(ref, desc=("bytes", out_b.getvalue()))
        slots["metallic_roughness"] = dict(ref, desc=("bytes", out_m.getvalue()))
        return [1.0, 1.0, 1.0, 1.0], 1.0, 1.0
    except Exception as ex:
        doc.warn("sg", f"spec-gloss conversion failed ({ex}) — using diffuse only")
        if dslot:
            slots["base_color"] = dslot
        return list(dfac), 0.0, 1.0 - gfac


def material_info(doc, mi):
    mats = doc.J.get("materials", [])
    info = {"name": "default", "base_color": _engine_color([1, 1, 1]) + [1.0], "metallic": 1.0,
            "roughness": 1.0, "emissive": [0.0, 0.0, 0.0], "emissive_intensity": 1.0,
            "base_color_tex": None, "metallic_roughness_tex": None, "normal_tex": None,
            "emissive_tex": None, "occlusion_tex": None, "double_sided": False,
            "alpha_mode": "OPAQUE", "alpha_cutoff": 0.5,
            "_h3d": {"vertex_colors": True, "slots": {}}}
    if mi is None or not (0 <= mi < len(mats)):
        info["metallic"] = 0.0           # spec default material is metal=1 rough=1;
        info["roughness"] = 1.0          # metal with no env looks black - use dielectric
        return info
    m = mats[mi]
    slots = info["_h3d"]["slots"]
    info["name"] = m.get("name") or f"material_{mi}"
    ext = m.get("extensions", {})
    pbr = m.get("pbrMetallicRoughness", {})
    base = list(pbr.get("baseColorFactor", [1, 1, 1, 1]))
    metallic = float(pbr.get("metallicFactor", 1.0))
    rough = float(pbr.get("roughnessFactor", 1.0))
    s = _slot(doc, pbr.get("baseColorTexture"))
    if s: slots["base_color"] = s
    s = _slot(doc, pbr.get("metallicRoughnessTexture"))
    if s: slots["metallic_roughness"] = s
    sg = ext.get("KHR_materials_pbrSpecularGlossiness")
    if sg is not None and "pbrMetallicRoughness" not in m:
        slots.pop("base_color", None); slots.pop("metallic_roughness", None)
        base, metallic, rough = _specgloss_convert(doc, sg, info, slots)
    nt = m.get("normalTexture")
    s = _slot(doc, nt, {"scale": float((nt or {}).get("scale", 1.0))})
    if s: slots["normal"] = s
    ot = m.get("occlusionTexture")
    s = _slot(doc, ot, {"strength": float((ot or {}).get("strength", 1.0))})
    if s: slots["occlusion"] = s
    s = _slot(doc, m.get("emissiveTexture"))
    if s: slots["emissive"] = s
    emis = list(m.get("emissiveFactor", [0, 0, 0]))
    estr = float(ext.get("KHR_materials_emissive_strength", {}).get("emissiveStrength", 1.0))
    info["alpha_mode"] = m.get("alphaMode", "OPAQUE").upper()
    info["alpha_cutoff"] = float(m.get("alphaCutoff", 0.5))
    info["double_sided"] = bool(m.get("doubleSided", False))
    tr = ext.get("KHR_materials_transmission")
    if tr and float(tr.get("transmissionFactor", 0.0)) > 0.01 and info["alpha_mode"] == "OPAQUE":
        # no refraction pass: approximate glass with alpha blending; rough
        # (frosted) transmission stays more opaque, volume tints the body
        t = float(tr.get("transmissionFactor", 0.0))
        info["alpha_mode"] = "BLEND"
        base[3] = base[3] * float(np.clip(1.0 - t * (0.72 - 0.45 * rough), 0.25, 1.0))
        vol = ext.get("KHR_materials_volume")
        if vol and "attenuationColor" in vol:
            ac = vol["attenuationColor"]
            base[:3] = [base[i] * (0.5 + 0.5 * float(ac[i])) for i in range(3)]
    if "KHR_materials_unlit" in ext:
        # unlit: no lighting response, colour comes through the emissive path
        emis = list(base[:3])
        estr = 1.0
        if "base_color" in slots:
            slots["emissive"] = dict(slots["base_color"])
        base = [0.0, 0.0, 0.0, base[3]]
        metallic, rough = 0.0, 1.0
        info["_h3d"]["unlit"] = True
    info["base_color"] = _engine_color(base[:3]) + [float(base[3])]
    info["metallic"] = float(np.clip(metallic, 0, 1))
    info["roughness"] = float(np.clip(rough, 0, 1))
    info["emissive"] = [float(c) for c in emis]
    info["emissive_intensity"] = estr
    return info


# ======================================================================
#  geometry
# ======================================================================
def _expand_mode(mode, idx, n):
    """-> (triangles (T,3) or None, lines (L,2) or None, points (P,) or None)."""
    if idx is None:
        idx = np.arange(n, dtype=np.int64)
    if mode == 4:
        k = len(idx) // 3 * 3
        return idx[:k].reshape(-1, 3), None, None
    if mode == 5:            # strip
        if len(idx) < 3:
            return np.zeros((0, 3), np.int64), None, None
        i = np.arange(len(idx) - 2)
        a, b, c = idx[i], idx[i + 1], idx[i + 2]
        odd = (i % 2) == 1
        t = np.stack([a, np.where(odd, c, b), np.where(odd, b, c)], 1)
        return t, None, None
    if mode == 6:            # fan
        if len(idx) < 3:
            return np.zeros((0, 3), np.int64), None, None
        i = np.arange(1, len(idx) - 1)
        return np.stack([np.full(len(i), idx[0]), idx[i], idx[i + 1]], 1), None, None
    if mode == 1:
        k = len(idx) // 2 * 2
        return None, idx[:k].reshape(-1, 2), None
    if mode == 3:
        return None, np.stack([idx[:-1], idx[1:]], 1), None
    if mode == 2:
        return None, np.stack([idx, np.roll(idx, -1)], 1), None
    if mode == 0:
        return None, None, idx
    return None, None, None


def _flat_normals(P, tris):
    a, b, c = P[tris[:, 0]], P[tris[:, 1]], P[tris[:, 2]]
    fn = np.cross(b - a, c - a)
    ln = np.linalg.norm(fn, axis=1, keepdims=True)
    return fn / np.maximum(ln, 1e-20)


def _line_geometry(P, lines, width):
    """Line segments -> thin square tubes (8 triangles each)."""
    a, b = P[lines[:, 0]], P[lines[:, 1]]
    d = b - a
    ln = np.linalg.norm(d, axis=1, keepdims=True)
    keep = ln[:, 0] > 1e-12
    a, b, d, ln, src = a[keep], b[keep], d[keep], ln[keep], lines[keep]
    d = d / ln
    up = np.where(np.abs(d[:, 1:2]) < 0.9, np.array([[0, 1.0, 0]]), np.array([[1.0, 0, 0]]))
    u = np.cross(d, up); u /= np.linalg.norm(u, axis=1, keepdims=True)
    v = np.cross(d, u)
    h = width * 0.5
    offs = [u * h + v * h, -u * h + v * h, -u * h - v * h, u * h - v * h]
    verts, tris, srcv = [], [], []
    n = len(a)
    for k in range(4):
        verts.append(a + offs[k]); verts.append(b + offs[k])
    V = np.stack(verts, 1).reshape(-1, 3)             # per segment 8 verts
    base = np.arange(n)[:, None] * 8
    quad = []
    for k in range(4):
        k2 = (k + 1) % 4
        i0, i1, j0, j1 = 2 * k, 2 * k + 1, 2 * k2, 2 * k2 + 1
        quad += [[i0, j0, j1], [i0, j1, i1]]
    T = (base[:, :, None] + np.array(quad)[None]).reshape(-1, 3)
    which = np.tile(np.array([0, 1] * 4), n).reshape(n, 8)
    srcv = np.where(which == 0, src[:, :1], src[:, 1:2]).ravel()
    return V, T, srcv


def _point_geometry(P, pts, size):
    """Points -> small octahedra."""
    h = size * 0.5
    o = np.array([[h, 0, 0], [-h, 0, 0], [0, h, 0], [0, -h, 0], [0, 0, h], [0, 0, -h]])
    faces = np.array([[0, 2, 4], [2, 1, 4], [1, 3, 4], [3, 0, 4], [2, 0, 5], [1, 2, 5], [3, 1, 5], [0, 3, 5]])
    c = P[pts]
    V = (c[:, None, :] + o[None]).reshape(-1, 3)
    T = (np.arange(len(pts))[:, None, None] * 6 + faces[None]).reshape(-1, 3)
    srcv = np.repeat(pts, 6)
    return V, T, srcv


class _Prim:
    __slots__ = ("pos", "nrm", "tan", "uv0", "uv1", "col", "tris", "mat", "flat_shaded", "skin_j")


def _decode_primitive(doc, prim, mesh_weights, node_weights, model_scale):
    attrs = prim.get("attributes", {})
    if "POSITION" not in attrs:
        return None
    ext = prim.get("extensions", {})
    if "KHR_draco_mesh_compression" in ext:
        return _decode_draco(doc, prim, ext["KHR_draco_mesh_compression"])
    P = doc.accessor(attrs["POSITION"]).copy()
    n = len(P)
    N = doc.accessor(attrs["NORMAL"]).copy() if "NORMAL" in attrs else None
    T = doc.accessor(attrs["TANGENT"]).copy() if "TANGENT" in attrs else None
    UV0 = doc.accessor(attrs["TEXCOORD_0"]) if "TEXCOORD_0" in attrs else None
    UV1 = doc.accessor(attrs["TEXCOORD_1"]) if "TEXCOORD_1" in attrs else None
    COL = doc.accessor(attrs["COLOR_0"]) if "COLOR_0" in attrs else None
    # ---- morph targets (default weights) ----
    targets = prim.get("targets", [])
    weights = node_weights if node_weights is not None else mesh_weights
    if targets and weights:
        for w, tg in zip(weights, targets):
            if not w:
                continue
            if "POSITION" in tg:
                P += w * doc.accessor(tg["POSITION"])
            if "NORMAL" in tg and N is not None:
                N += w * doc.accessor(tg["NORMAL"])
            if "TANGENT" in tg and T is not None:
                T[:, :3] += w * doc.accessor(tg["TANGENT"])
    idx = doc.accessor(prim["indices"], as_float=False)[:, 0] if "indices" in prim else None
    mode = prim.get("mode", 4)
    tris, lines, pts = _expand_mode(mode, idx, n)
    pr = _Prim()
    pr.mat = prim.get("material")
    pr.flat_shaded = False
    if tris is None:
        # lines / points -> visible thin geometry
        if lines is not None and len(lines):
            V, Tr, src = _line_geometry(P, lines, max(model_scale * 0.004, 1e-4))
        elif pts is not None and len(pts):
            V, Tr, src = _point_geometry(P, pts, max(model_scale * 0.012, 1e-4))
        else:
            return None
        pr.pos = V
        pr.tris = Tr
        pr.nrm = None
        pr.flat_shaded = True
        pr.tan = None
        pr.uv0 = UV0[src] if UV0 is not None else None
        pr.uv1 = UV1[src] if UV1 is not None else None
        pr.col = COL[src] if COL is not None else None
        return pr
    if not len(tris):
        return None
    pr.pos, pr.tris = P, tris.astype(np.int64)
    pr.tan = T
    pr.uv0, pr.uv1, pr.col = UV0, UV1, COL
    if N is None:
        # spec: missing normals MUST be flat -> un-index the primitive
        flat = pr.tris.ravel()
        fn = np.repeat(_flat_normals(P, pr.tris), 3, axis=0)
        pr.pos = P[flat]; pr.nrm = fn
        pr.tan = None if T is None else T[flat]
        pr.uv0 = None if UV0 is None else UV0[flat]
        pr.uv1 = None if UV1 is None else UV1[flat]
        pr.col = None if COL is None else COL[flat]
        pr.tris = np.arange(len(flat)).reshape(-1, 3)
        pr.flat_shaded = True
    else:
        pr.nrm = N
    pr.skin_j = attrs.get("JOINTS_0")
    return pr


def _decode_draco(doc, prim, dext):
    try:
        import DracoPy
    except ImportError:
        doc.warn("draco", "glTF is Draco-compressed — install the decoder with:  pip install DracoPy")
        return None
    data = doc.view(dext["bufferView"])
    dm = DracoPy.decode(bytes(data))
    pr = _Prim()
    pr.mat = prim.get("material")
    pr.flat_shaded = False
    pts = np.asarray(dm.points, np.float64).reshape(-1, 3)
    faces = np.asarray(dm.faces, np.int64).reshape(-1, 3)
    by_id = {}
    for a in getattr(dm, "attributes", []) or []:
        uid = a.get("unique_id") if isinstance(a, dict) else getattr(a, "unique_id", None)
        dat = a.get("data") if isinstance(a, dict) else getattr(a, "data", None)
        if uid is not None and dat is not None:
            by_id[uid] = np.asarray(dat, np.float64)
    amap = dext.get("attributes", {})

    def get(name, comps):
        uid = amap.get(name)
        if uid is None or uid not in by_id:
            return None
        a = by_id[uid].reshape(len(pts), -1)
        acc = doc.J["accessors"][prim["attributes"][name]]
        if acc.get("normalized") and _COMP[acc["componentType"]] in _NORM_DIV:
            a = a / _NORM_DIV[_COMP[acc["componentType"]]]
        return a[:, :comps]
    pr.pos = pts
    pr.tris = faces
    pr.nrm = get("NORMAL", 3)
    if pr.nrm is None and getattr(dm, "normals", None) is not None and len(dm.normals):
        pr.nrm = np.asarray(dm.normals, np.float64).reshape(-1, 3)
    pr.tan = get("TANGENT", 4)
    pr.uv0 = get("TEXCOORD_0", 2)
    if pr.uv0 is None and getattr(dm, "tex_coord", None) is not None and len(dm.tex_coord):
        pr.uv0 = np.asarray(dm.tex_coord, np.float64).reshape(-1, 2)
    pr.uv1 = get("TEXCOORD_1", 2)
    pr.col = get("COLOR_0", 4)
    pr.skin_j = None
    if pr.nrm is None:
        flat = faces.ravel()
        pr.nrm = np.repeat(_flat_normals(pts, faces), 3, axis=0)
        pr.pos = pts[flat]
        for a in ("tan", "uv0", "uv1", "col"):
            v = getattr(pr, a)
            if v is not None:
                setattr(pr, a, v[flat])
        pr.tris = np.arange(len(flat)).reshape(-1, 3)
    return pr


# ======================================================================
#  scene traversal
# ======================================================================
def _node_local(n):
    if "matrix" in n:
        return np.asarray(n["matrix"], float).reshape(4, 4).T.copy()
    return _trs_matrix(n.get("translation", [0, 0, 0]), n.get("rotation", [0, 0, 0, 1]),
                       n.get("scale", [1, 1, 1]))


def load_gltf(path, logger=None):
    """-> (parts, lights)."""
    doc = GltfDocument(path, logger)
    J = doc.J
    nodes = J.get("nodes", [])
    meshes = J.get("meshes", [])
    # ---- choose scene ----
    scenes = J.get("scenes", [])
    if scenes:
        si = J.get("scene", 0)
        if not (0 <= si < len(scenes)):
            si = 0
        roots = list(scenes[si].get("nodes", []))
        if len(scenes) > 1:
            _log(logger, "info", f"glTF has {len(scenes)} scenes — importing scene "
                                 f"'{scenes[si].get('name', si)}'")
    else:
        child = {c for n in nodes for c in n.get("children", [])}
        roots = [i for i in range(len(nodes)) if i not in child]
    # ---- world matrices (default pose) ----
    world = {}
    visible = {}
    parent = {}
    stack = [(r, np.eye(4), True) for r in roots]
    order = []
    guard = 0
    while stack and guard < 1_000_000:
        guard += 1
        i, PW, vis = stack.pop()
        if i in world or not (0 <= i < len(nodes)):
            continue
        n = nodes[i]
        v = vis and n.get("extensions", {}).get("KHR_node_visibility", {}).get("visible", True)
        W = PW @ _node_local(n)
        world[i] = W
        visible[i] = v
        order.append(i)
        for c in n.get("children", []):
            parent[c] = i
            stack.append((c, W, v))
    # joints that are not in the scene graph still need matrices
    for sk in J.get("skins", []):
        for j in sk.get("joints", []):
            if j not in world and 0 <= j < len(nodes):
                chain = [j]
                while chain[-1] in parent and parent[chain[-1]] not in world:
                    chain.append(parent[chain[-1]])
                W = np.eye(4)
                for k in reversed(chain):
                    W = W @ _node_local(nodes[k])
                world[j] = W
    # model size estimate (for line/point widths)
    allp = []
    for i in order:
        mi = nodes[i].get("mesh")
        if mi is None:
            continue
        for p in meshes[mi].get("primitives", []):
            a = J["accessors"][p["attributes"]["POSITION"]] if "POSITION" in p.get("attributes", {}) else None
            if a and "min" in a and "max" in a:
                lo = np.r_[a["min"][:3], 1.0] @ world[i].T
                hi = np.r_[a["max"][:3], 1.0] @ world[i].T
                allp += [lo[:3], hi[:3]]
    model_scale = float(np.linalg.norm(np.ptp(np.array(allp), 0))) if allp else 1.0
    if not np.isfinite(model_scale) or model_scale <= 0:
        model_scale = 1.0
    # ---- primitives ----
    groups = {}
    lights = []
    mat_cache = {}
    prim_cache = {}
    lp = J.get("extensions", {}).get("KHR_lights_punctual", {}).get("lights", [])
    for i in order:
        n = nodes[i]
        if not visible.get(i, True):
            continue
        W = world[i]
        le = n.get("extensions", {}).get("KHR_lights_punctual")
        if le is not None and 0 <= le.get("light", -1) < len(lp):
            lights.append(_light_desc(lp[le["light"]], W, n.get("name", f"light{i}")))
        mi = n.get("mesh")
        if mi is None or not (0 <= mi < len(meshes)):
            continue
        mesh = meshes[mi]
        inst = _instances(doc, n)
        skin = None
        if "skin" in n and 0 <= n["skin"] < len(J.get("skins", [])):
            skin = J["skins"][n["skin"]]
        for pi, prim in enumerate(mesh.get("primitives", [])):
            key = (mi, pi, tuple(n.get("weights", []) or []))
            if key not in prim_cache:
                try:
                    prim_cache[key] = _decode_primitive(doc, prim, mesh.get("weights"),
                                                        n.get("weights"), model_scale)
                except Exception as ex:
                    doc.warn(f"prim{mi}_{pi}", f"glTF mesh {mesh.get('name', mi)} primitive {pi} "
                                               f"unreadable ({ex}) — skipped")
                    prim_cache[key] = None
            pr = prim_cache[key]
            if pr is None:
                continue
            pos, nrm, tan = pr.pos, pr.nrm, pr.tan
            if skin is not None and getattr(pr, "skin_j", None) is not None and not pr.flat_shaded:
                pos, nrm, tan = _apply_skin(doc, prim, skin, world, pos, nrm, tan)
                mats = [np.eye(4)]            # skinned: node transform ignored (spec)
            else:
                mats = [W @ M for M in inst] if inst is not None else [W]
            mk = pr.mat
            if mk not in mat_cache:
                mat_cache[mk] = material_info(doc, mk)
            minfo = mat_cache[mk]
            g = groups.setdefault(mk, {"info": minfo, "chunks": []})
            for M in mats:
                g["chunks"].append(_bake_chunk(pr, pos, nrm, tan, M))
    parts = []
    for mk, g in groups.items():
        parts.append(_merge_chunks(g["info"], g["chunks"]))
    _log(logger, "info", f"glTF imported: {os.path.basename(path)} ({len(order)} nodes, "
                         f"{len(parts)} material part(s), {len(lights)} light(s))")
    return parts, lights


def _instances(doc, n):
    ext = n.get("extensions", {}).get("EXT_mesh_gpu_instancing")
    if not ext:
        return None
    a = ext.get("attributes", {})
    cnt = None
    T = doc.accessor(a["TRANSLATION"]) if "TRANSLATION" in a else None
    R = doc.accessor(a["ROTATION"]) if "ROTATION" in a else None
    S = doc.accessor(a["SCALE"]) if "SCALE" in a else None
    for x in (T, R, S):
        if x is not None:
            cnt = len(x)
    if not cnt:
        return None
    Rm = _quat_mats(R) if R is not None else np.repeat(np.eye(3)[None], cnt, 0)
    out = []
    for k in range(cnt):
        M = np.eye(4)
        M[:3, :3] = Rm[k] * (S[k][None, :] if S is not None else 1.0)
        if T is not None:
            M[:3, 3] = T[k]
        out.append(M)
    return out


def _apply_skin(doc, prim, skin, world, pos, nrm, tan):
    attrs = prim["attributes"]
    joints = skin.get("joints", [])
    if not joints:
        return pos, nrm, tan
    if "inverseBindMatrices" in skin:
        ibm = doc.accessor(skin["inverseBindMatrices"]).reshape(-1, 4, 4).transpose(0, 2, 1)
    else:
        ibm = np.repeat(np.eye(4)[None], len(joints), 0)
    J = np.stack([world.get(j, np.eye(4)) for j in joints]) @ ibm[:len(joints)]
    n = len(pos)
    Msum = np.zeros((n, 4, 4))
    wsum = np.zeros(n)
    k = 0
    while f"JOINTS_{k}" in attrs and f"WEIGHTS_{k}" in attrs:
        jj = doc.accessor(attrs[f"JOINTS_{k}"], as_float=False)
        ww = doc.accessor(attrs[f"WEIGHTS_{k}"])
        jj = np.clip(jj, 0, len(J) - 1)
        for c in range(jj.shape[1]):
            w = ww[:, c]
            Msum += w[:, None, None] * J[jj[:, c]]
            wsum += w
        k += 1
    bad = wsum < 1e-8
    Msum[bad] = np.eye(4)
    Msum[~bad] /= wsum[~bad, None, None]
    p = np.einsum("nij,nj->ni", Msum[:, :3, :3], pos) + Msum[:, :3, 3]
    nn = None
    if nrm is not None:
        inv_t = np.linalg.inv(Msum[:, :3, :3]).transpose(0, 2, 1)
        nn = np.einsum("nij,nj->ni", inv_t, nrm)
        nn /= np.maximum(np.linalg.norm(nn, axis=1, keepdims=True), 1e-20)
    tt = None
    if tan is not None:
        t3 = np.einsum("nij,nj->ni", Msum[:, :3, :3], tan[:, :3])
        t3 /= np.maximum(np.linalg.norm(t3, axis=1, keepdims=True), 1e-20)
        sign = np.sign(np.linalg.det(Msum[:, :3, :3])); sign[sign == 0] = 1
        tt = np.concatenate([t3, (tan[:, 3] * sign)[:, None]], 1)
    return p, nn, tt


def _bake_chunk(pr, pos, nrm, tan, M):
    M3 = M[:3, :3]
    P = pos @ M3.T + M[:3, 3]
    det = float(np.linalg.det(M3))
    Nn = None
    if nrm is not None:
        try:
            Ninv = np.linalg.inv(M3).T
        except np.linalg.LinAlgError:
            Ninv = M3
        Nn = nrm @ Ninv.T
        Nn /= np.maximum(np.linalg.norm(Nn, axis=1, keepdims=True), 1e-20)
    else:
        Nn = np.repeat(_flat_normals(P, pr.tris), 3, axis=0)[:len(P)] if False else None
    Tt = None
    if tan is not None:
        t3 = tan[:, :3] @ M3.T
        t3 /= np.maximum(np.linalg.norm(t3, axis=1, keepdims=True), 1e-20)
        Tt = np.concatenate([t3, (tan[:, 3] * (1 if det >= 0 else -1))[:, None]], 1)
    tris = pr.tris if det >= 0 else pr.tris[:, [0, 2, 1]]
    if Nn is None:
        # line/point geometry: flat normals from the triangles
        flat = tris.ravel()
        Nn = np.repeat(_flat_normals(P, tris), 3, axis=0)
        P = P[flat]
        uv0 = None if pr.uv0 is None else pr.uv0[flat]
        uv1 = None if pr.uv1 is None else pr.uv1[flat]
        col = None if pr.col is None else pr.col[flat]
        tris = np.arange(len(flat)).reshape(-1, 3)
        return {"pos": P, "nrm": Nn, "tan": None, "uv0": uv0, "uv1": uv1, "col": col, "tris": tris}
    return {"pos": P, "nrm": Nn, "tan": Tt, "uv0": pr.uv0, "uv1": pr.uv1, "col": pr.col, "tris": tris}


def _merge_chunks(info, chunks):
    counts = [len(c["pos"]) for c in chunks]

    def cat(key, width, fill):
        if all(c[key] is None for c in chunks):
            return None
        out = []
        for c, n in zip(chunks, counts):
            v = c[key]
            if v is None:
                out.append(np.tile(np.asarray(fill, float), (n, 1)))
            else:
                v = np.asarray(v, float)
                if v.shape[1] < width:      # COLOR_0 vec3 -> rgba
                    v = np.concatenate([v, np.ones((len(v), width - v.shape[1]))], 1)
                out.append(v[:, :width])
        return np.concatenate(out)
    pos = np.concatenate([c["pos"] for c in chunks])
    nrm = np.concatenate([c["nrm"] for c in chunks])
    uv0 = cat("uv0", 2, [0, 0])
    uv1 = cat("uv1", 2, [0, 0])
    col = cat("col", 4, [1, 1, 1, 1])
    has_tan = all(c["tan"] is not None for c in chunks)
    tan = np.concatenate([c["tan"] for c in chunks]) if has_tan else None
    offs = np.concatenate([[0], np.cumsum(counts)[:-1]])
    tris = np.concatenate([c["tris"] + o for c, o in zip(chunks, offs)])
    # glTF uv origin is top-left; the engine samples bottom-left
    if uv0 is not None:
        uv0 = np.c_[uv0[:, 0], 1.0 - uv0[:, 1]]
    if uv1 is not None:
        uv1 = np.c_[uv1[:, 0], 1.0 - uv1[:, 1]]
    if col is not None:
        # engine multiplies before its gamma-2.2 decode: pre-encode linear rgb
        col = np.c_[np.clip(col[:, :3], 0, None) ** (1 / 2.2), np.clip(col[:, 3], 0, 1)]
    # drop unreferenced vertices, then weld duplicates
    used = np.zeros(len(pos), bool); used[tris.ravel()] = True
    if not used.all():
        remap = np.cumsum(used) - 1
        tris = remap[tris]
        sel = used
        pos, nrm = pos[sel], nrm[sel]
        uv0 = None if uv0 is None else uv0[sel]; uv1 = None if uv1 is None else uv1[sel]
        col = None if col is None else col[sel]; tan = None if tan is None else tan[sel]
    first, inv = weld(pos, nrm, uv0, uv1, col, tan)
    return {"name": info["name"], "positions": pos[first].astype(np.float32),
            "normals": nrm[first].astype(np.float32),
            "uv0": None if uv0 is None else uv0[first].astype(np.float32),
            "uv1": None if uv1 is None else uv1[first].astype(np.float32),
            "colors": None if col is None else col[first].astype(np.float32),
            "tangents": None if tan is None else tan[first].astype(np.float32),
            "indices": inv[tris].astype(np.uint32).ravel(),
            "material_info": info}


def _light_desc(L, W, name):
    t = L.get("type", "point")
    col = L.get("color", [1, 1, 1])
    inten = float(L.get("intensity", 1.0))
    d = W[:3, :3] @ np.array([0, 0, -1.0])
    d /= max(np.linalg.norm(d), 1e-12)
    if t == "directional":
        kind, energy = "sun", inten / 683.0 * 4.0          # lux -> ~W/m2 heuristic
    else:
        kind = "spot" if t == "spot" else "point"
        energy = inten * 4 * math.pi / 683.0 * 100.0         # candela -> ~W
    spot = L.get("spot", {})
    outer = float(spot.get("outerConeAngle", math.pi / 4))
    inner = float(spot.get("innerConeAngle", 0.0))
    return {"kind": kind, "name": L.get("name") or name, "color": tuple(float(c) for c in col),
            "energy": energy, "position": tuple(float(x) for x in W[:3, 3]),
            "direction": tuple(float(x) for x in d), "spotsize": outer * 2.0,
            "spotblend": float(1.0 - inner / outer) if outer > 0 else 0.15,
            "dist": float(L.get("range", 0.0) or 0.0)}