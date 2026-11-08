# @heaven3d-part h3d27/engine/03_materials.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# ---------------------------------------------------------------
# 3. Materials
# ---------------------------------------------------------------
_H3D27_SLOT_KEYS = ("base_color", "metallic_roughness", "normal", "emissive", "occlusion")

def _h3d27_pack_mr(pack):
    """Blender: build a glTF-style metal(B)/rough(G) texture from the
    image channels the node tree routes into Metallic / Roughness."""
    try:
        from PIL import Image
        import io as _io
        metal, rough = pack.get("metal"), pack.get("rough")
        srcs = [x for x in (metal, rough) if x is not None]
        if not srcs:
            return None
        if (metal and rough and metal[0]["desc"] == rough[0]["desc"]
                and metal[1] == "b" and rough[1] == "g"):
            return metal[0]

        def load(ti):
            kind, payload = ti["desc"]
            return Image.open(_io.BytesIO(payload) if kind == "bytes" else payload).convert("RGBA")

        imgs = [(load(t), ch) for t, ch in srcs]
        size = max((im.size for im, _ in imgs), key=lambda s: s[0] * s[1])
        out = np.full((size[1], size[0], 3), 255, np.uint8)

        def chan(im, ch):
            a = np.asarray(im.resize(size), np.uint8)
            if ch in ("r", "g", "b"):
                return a[..., "rgb".index(ch)]
            return np.asarray(im.convert("L").resize(size), np.uint8)
        if rough is not None:
            out[..., 1] = chan(load(rough[0]), rough[1])
        if metal is not None:
            out[..., 2] = chan(load(metal[0]), metal[1])
        else:
            out[..., 2] = 255
        buf = _io.BytesIO()
        Image.fromarray(out, "RGB").save(buf, "PNG")
        base = dict((rough or metal)[0])
        base["desc"] = ("bytes", buf.getvalue())
        return base
    except Exception as ex:
        _h3d27_log_once("mrpack", f"[Import] metal/rough texture packing failed: {ex}")
        return None

def _h3d27_material_from_info(info):
    ext = info.get("_h3d", {})
    slots = dict(ext.get("slots", {}))
    mr = slots.get("metallic_roughness")
    if mr is not None and "pack" in mr:
        mr = _h3d27_pack_mr(mr["pack"])
        if mr is None:
            slots.pop("metallic_roughness", None)
        else:
            slots["metallic_roughness"] = mr
    base = dict(info)
    for key, field in zip(_H3D27_SLOT_KEYS, ("base_color_tex", "metallic_roughness_tex",
                                              "normal_tex", "emissive_tex", "occlusion_tex")):
        s = slots.get(key)
        base[field] = s["desc"] if s else None
    mat = Material.from_glb_info(base)
    uvset, xf_on, xf, wrap = [], [], [], []
    for key in _H3D27_SLOT_KEYS:
        s = slots.get(key) or {}
        uvset.append(int(s.get("uvset", 0)))
        m = s.get("xf")
        if m is not None:
            xf_on.append(1)
            xf.extend(np.asarray(m, np.float32).T.ravel().tolist())   # column-major
        else:
            xf_on.append(0)
            xf.extend([1, 0, 0, 0, 1, 0, 0, 0, 1])
        ws = int(s.get("wrap", 0)); wt = int(s.get("wrap_t", ws))
        wrap += [ws, wt]
    mat._h3d = {"uvset": uvset, "xf_on": xf_on, "xf": xf, "wrap": wrap,
                "normal_scale": float((slots.get("normal") or {}).get("scale", 1.0)),
                "ao_strength": float((slots.get("occlusion") or {}).get("strength", 1.0)),
                "emis_srgb": 1}
    return mat

_H3D27_PREV_BIND = Renderer._bind_material

def _h3d27_bind_material(self, prog, mat):
    _H3D27_PREV_BIND(self, prog, mat)
    try:
        on = prog.get("u_h3d_on", None)
        if on is None:
            return
        ext = getattr(mat, "_h3d", None)
        on.value = 1 if ext else 0
        if not ext:
            return
        for name, data, dt in (("u_h3d_uvset", ext["uvset"], "i4"),
                               ("u_h3d_xf_on", ext["xf_on"], "i4"),
                               ("u_h3d_uvxf", ext["xf"], "f4"),
                               ("u_h3d_wrap", ext["wrap"], "i4")):
            u = prog.get(name, None)
            if u is not None:
                u.write(np.asarray(data, dt).tobytes())
        for name, val in (("u_h3d_normal_scale", ext["normal_scale"]),
                          ("u_h3d_ao_strength", ext["ao_strength"]),
                          ("u_h3d_emis_srgb", ext["emis_srgb"])):
            u = prog.get(name, None)
            if u is not None:
                u.value = val
        # clamp-to-edge sampling (mirror is done in the shader)
        for i, attr in enumerate(("base_color_tex", "metallic_roughness_tex", "normal_tex",
                                  "emissive_tex", "occlusion_tex")):
            src = getattr(mat, attr, None)
            if not src:
                continue
            ws, wt = ext["wrap"][2 * i], ext["wrap"][2 * i + 1]
            if ws == 0 and wt == 0:
                continue
            tex = self.get_texture(src)
            tex.repeat_x = ws != 1
            tex.repeat_y = wt != 1
    except Exception as ex:
        _h3d27_log_once("bind", f"[Import] material binding: {ex}")

Renderer._bind_material = _h3d27_bind_material
