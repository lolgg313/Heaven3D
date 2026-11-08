# @heaven3d-part h3d27/engine/01_shaders.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
    # ---------------------------------------------------------------
    # 1. Shaders: vertex colours, 2nd UV set, per-texture UV transform /
    #    wrap mode, normal scale, AO strength (anchored string surgery,
    #    same idiom as the terrain patch; any missing anchor = disabled).
    # ---------------------------------------------------------------
    _H3D27_VERT_IN = (
        "\nlayout (location = 8) in vec4 in_h3d_color;   // (1-r,1-g,1-b,a): unbound = white"
        "\nlayout (location = 9) in vec2 in_h3d_uv1;"
        "\nout vec4 v_h3d_color;"
        "\nout vec2 v_h3d_uv1;")
    _H3D27_VERT_MAIN = (
        "\n    v_h3d_color = vec4(1.0 - in_h3d_color.rgb, in_h3d_color.a);"
        "\n    v_h3d_uv1 = in_h3d_uv1;")
    _H3D27_FRAG_HEAD = """
in vec4 v_h3d_color;
in vec2 v_h3d_uv1;
uniform int   u_h3d_on;            // 1 = material carries Heaven3D 2.7 import data
uniform int   u_h3d_uvset[5];      // albedo, metal-rough, normal, emissive, ao
uniform int   u_h3d_xf_on[5];
uniform mat3  u_h3d_uvxf[5];
uniform int   u_h3d_wrap[10];      // 0 repeat, 1 clamp, 2 mirror (s,t per slot)
uniform float u_h3d_normal_scale;
uniform float u_h3d_ao_strength;
uniform int   u_h3d_emis_srgb;
float h3d_wrap1(float x, int m){
    if (m == 2) return 1.0 - abs(mod(x, 2.0) - 1.0);
    if (m == 1) return clamp(x, 0.0, 1.0);
    return x;
}
vec2 h3d_uv(int s){
    if (u_h3d_on != 1) return v_uv;
    vec2 b = (u_h3d_uvset[s] == 1) ? v_h3d_uv1 : v_uv;
    if (u_h3d_xf_on[s] == 1) b = (u_h3d_uvxf[s] * vec3(b, 1.0)).xy;
    return vec2(h3d_wrap1(b.x, u_h3d_wrap[2 * s]), h3d_wrap1(b.y, u_h3d_wrap[2 * s + 1]));
}
"""
    _H3D27_SLOTS = (("u_albedo_tex", 0), ("u_mr_tex", 1), ("u_normal_tex", 2),
                    ("u_emissive_tex", 3), ("u_ao_tex", 4))

    def _h3d27_patch_vert(src):
        a1 = "layout (location = 3) in vec2 in_uv;"
        a2 = "    v_uv = in_uv;"
        if a1 not in src or a2 not in src or "in_h3d_color" in src:
            return src, False
        src = src.replace(a1, a1 + _H3D27_VERT_IN, 1)
        src = src.replace(a2, a2 + _H3D27_VERT_MAIN, 1)
        return src, True

    def _h3d27_patch_frag(src, forward):
        a_in = "in vec2 v_uv;"
        if a_in not in src or "h3d_uv(" in src:
            return src, False
        src = src.replace(a_in, a_in + _H3D27_FRAG_HEAD, 1)
        for sampler, slot in _H3D27_SLOTS:
            src = src.replace(f"texture({sampler}, v_uv)", f"texture({sampler}, h3d_uv({slot}))")
        if forward:
            src = src.replace("    vec4 rgba = u_base_color;\n",
                              "    vec4 rgba = u_base_color;\n    rgba *= v_h3d_color;\n", 1)
        else:
            src = src.replace("    vec4 albedo_rgba = u_base_color;\n",
                              "    vec4 albedo_rgba = u_base_color;\n    albedo_rgba *= v_h3d_color;\n", 1)
        src = src.replace("        N = normalize(T * tn.x + B * tn.y + N * tn.z);",
                          "        if (u_h3d_on == 1) tn.xy *= u_h3d_normal_scale;\n"
                          "        N = normalize(T * tn.x + B * tn.y + N * tn.z);")
        src = src.replace("        ao = texture(u_ao_tex, h3d_uv(4)).r;",
                          "        ao = mix(1.0, texture(u_ao_tex, h3d_uv(4)).r,"
                          " (u_h3d_on == 1) ? u_h3d_ao_strength : 1.0);")
        src = src.replace("        emis *= texture(u_emissive_tex, h3d_uv(3)).rgb;",
                          "    {\n        vec3 h3d_et = texture(u_emissive_tex, h3d_uv(3)).rgb;\n"
                          "        if (u_h3d_on == 1 && u_h3d_emis_srgb == 1) h3d_et = pow(h3d_et, vec3(2.2));\n"
                          "        emis *= h3d_et;\n    }")
        return src, True

    try:
        _ok = []
        GEOMETRY_VERT, _o = _h3d27_patch_vert(GEOMETRY_VERT); _ok.append(_o)
        FORWARD_VERT, _o = _h3d27_patch_vert(FORWARD_VERT); _ok.append(_o)
        GEOMETRY_INST_VERT, _o = _h3d27_patch_vert(GEOMETRY_INST_VERT); _ok.append(_o)
        GEOMETRY_FRAG, _o = _h3d27_patch_frag(GEOMETRY_FRAG, False); _ok.append(_o)
        FORWARD_FRAG, _o = _h3d27_patch_frag(FORWARD_FRAG, True); _ok.append(_o)
        _H3D27_SHADERS_OK = all(_ok)
        if not _H3D27_SHADERS_OK:
            print("[Heaven3D][WARN] 2.7 importer shader hooks partially applied: %s" % _ok)
    except Exception as _h3d_ex:
        _H3D27_SHADERS_OK = False
        print(f"[Heaven3D][WARN] 2.7 importer shader hooks failed: {_h3d_ex}")
