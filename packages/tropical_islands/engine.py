"""Tropical Islands - procedural AAA tropical islands for Heaven3D.

One click makes a whole island: terrain with beaches, jungle, cliffs and dirt
roads, textured by a slope / height aware 4-layer material, covered with CC0
vegetation (5 trees, 2 grasses, 2 flowers, 2 rock kinds) that sways in the
wind, inside the engine's ocean.  Every setting (seed, size, shape, densities,
roads, erosion ...) is a property of the TropicalIsland blueprint, so islands
are saved as a few numbers and rebuilt identically when the project opens.

Blueprint
  TropicalIsland   generation + display settings (Inspector)
Packages menu
  Islands: new tropical island / new seed / next shape / move player spawn to the beach
"""
import hashlib
import json
import math
import os
import random
import time

import numpy as np

H3D = None
G = None                         # island_gen module
CLS = {}
# Texture units: the engine's own terrain splat (TERRAINPATCH) binds sampler2DArray
# at 13 / 14 and a sampler2D at 15 on every material bind; GL forbids two sampler
# TYPES on one unit, so ours use the same types on those units (+ a free unit 10).
UNIT_SPLAT, UNIT_ALB, UNIT_NRM, UNIT_ARM = 15, 13, 14, 10
UNIT_ALPHA = 12                  # shadow pass only
STATE = {"islands": {}, "time": 0.0, "cam": None, "dirty": {}, "lib": None, "terrain_tex": None,
         "shaders_ok": False, "warned": set(), "shadow_prog": None}

GEN_KEYS = tuple(k for k in ("seed", "size", "topology", "max_height", "coast_roughness", "beach_width", "erosion",
                             "roads", "road_width", "jungle", "tree_density", "palm_density", "grass_density",
                             "flower_density", "rock_density", "resolution"))
VIEW_DEFAULTS = {"lod_crossfade": True, "detail_distance": 55.0, "far_distance": 140.0, "grass_distance": 70.0, "wind": 0.6,
                 "shadow_distance": 120.0, "tree_collision": True, "tropical_water": True}

# species -> how it is drawn
SPECIES = {
    # name: (group, wind amplitude (m at the top), sink (m), shadow, chunk)
    "coconut_palm": ("tree", 0.55, 0.15, True),
    "royal_palm": ("tree", 0.6, 0.15, True),
    "island_tree": ("tree", 0.22, 0.2, True),
    "jacaranda": ("tree", 0.3, 0.25, True),
    "acacia": ("tree", 0.25, 0.2, True),
    "grass_tall": ("grass", 0.09, 0.03, False),
    "grass_lush": ("grass", 0.07, 0.03, False),
    "flower_orange": ("flower", 0.06, 0.02, False),
    "flower_pink": ("flower", 0.07, 0.02, False),
    "boulder": ("rock", 0.0, 0.0, True),
    "mossy_rocks": ("rock", 0.0, 0.0, True),
}
TREE_CHUNK, SMALL_CHUNK = 64.0, 40.0
# albedo multipliers: the scanned foliage is photographed dark; lift it to lush tropical greens
TINTS = {("tree", "cards"): (1.55, 1.65, 1.25),
         ("coconut_palm", "palm"): (1.3, 1.35, 1.1), ("royal_palm", "palm"): (1.3, 1.35, 1.1),
         ("grass", "cards"): (1.7, 1.85, 1.2), ("flower", "cards"): (1.35, 1.4, 1.3),
         ("tree", "wood"): (1.05, 1.0, 0.95), ("island_tree", "wood"): (1.0, 0.96, 0.9), ("rock", "rock"): (1.1, 1.08, 1.02)}


def _m():
    return H3D.module


def _warn_once(key, msg):
    if key not in STATE["warned"]:
        STATE["warned"].add(key)
        H3D.warn(msg)


# =========================================================================== shaders
_WIND_GLSL = """
uniform float u_isl_time;
uniform vec4 u_isl_wind;              // xy = direction, z = strength
vec3 isl_wind(vec3 wp, vec4 a){
    if (a.x <= 0.0) return vec3(0.0);
    float t = u_isl_time;
    float gust = 0.55 + 0.45 * sin(t * 0.63 + wp.x * 0.021 + wp.z * 0.017) * sin(t * 0.23 + wp.z * 0.013 + 1.7);
    float sway = sin(t * (1.25 + a.z) + a.y) * 0.65 + sin(t * (2.7 + a.z) + a.y * 1.7) * 0.35;
    vec2 d = u_isl_wind.xy * (u_isl_wind.z * a.x);
    vec2 off = d * (gust * 0.85 + 0.3 * sway) + vec2(-d.y, d.x) * (0.22 * sway * gust);
    float fl = sin(t * 8.0 + a.y * 13.0 + wp.y * 2.3) * a.w * u_isl_wind.z;
    return vec3(off.x + fl * 0.6, -length(off) * 0.12 + fl * 0.4, off.y - fl * 0.6);
}
"""

# Amplify-style hemi-octahedral impostors: n x n baked views; the 3 nearest views
# are blended, each sampled where the view ray crosses that view's plane.
_OCT_GLSL = """
uniform int u_isl_imp;
uniform int u_isl_imp_n;
vec3 isl_oct_dir(vec2 g, float n){
    vec2 q = g / (n - 1.0) * 2.0 - 1.0;
    float px = (q.x + q.y) * 0.5; float pz = (q.x - q.y) * 0.5;
    return normalize(vec3(px, max(0.0, 1.0 - abs(px) - abs(pz)), pz));
}
vec3 isl_oct_uv(vec2 frame, vec3 Pl, vec3 Vl, float R, float n){
    vec3 d = isl_oct_dir(frame, n);
    vec3 r = cross(vec3(0.0, 1.0, 0.0), d); float l = length(r);
    r = l > 1e-3 ? r / l : vec3(1.0, 0.0, 0.0);
    vec3 u = cross(d, r);
    float dv = dot(Vl, d);
    float t = abs(dv) > 1e-3 ? dot(Pl, d) / dv : 0.0;
    vec3 Q = Pl - Vl * t;
    vec2 uv = vec2(dot(Q, r), dot(Q, u)) / R * 0.5 + 0.5;
    float inside = step(0.0, uv.x) * step(uv.x, 1.0) * step(0.0, uv.y) * step(uv.y, 1.0);
    uv = clamp(uv, 0.004, 0.996);
    return vec3((frame + uv) / n, inside);
}
void isl_oct_frames(vec2 f, float n, out vec2 f0, out vec2 f1, out vec2 f2, out vec3 w){
    f = clamp(f, vec2(0.0), vec2(n - 1.0));
    vec2 b = min(floor(f), vec2(n - 2.0));
    vec2 fr = f - b;
    if (fr.x + fr.y < 1.0){ f0 = b; f1 = b + vec2(1.0, 0.0); f2 = b + vec2(0.0, 1.0); w = vec3(1.0 - fr.x - fr.y, fr.x, fr.y); }
    else { f0 = b + vec2(1.0); f1 = b + vec2(0.0, 1.0); f2 = b + vec2(1.0, 0.0); w = vec3(fr.x + fr.y - 1.0, 1.0 - fr.x, 1.0 - fr.y); }
}
vec3 isl_to_local(vec3 v, float yaw){ float c = cos(yaw), s = sin(yaw); return vec3(c * v.x - s * v.z, v.y, s * v.x + c * v.z); }
vec3 isl_to_world(vec3 v, float yaw){ float c = cos(yaw), s = sin(yaw); return vec3(c * v.x + s * v.z, v.y, -s * v.x + c * v.z); }
"""

_IMP_VS = """
    if (u_isl_imp == 1){
        float iyaw = in_tangent.x; float iR = in_tangent.y;
        vec3 iC = (u_model * vec4(in_position + vec3(0.0, in_tangent.z, 0.0), 1.0)).xyz;
        vec3 iV = ISL_VIEWDIR;
        vec3 ir = cross(vec3(0.0, 1.0, 0.0), iV); float irl = length(ir);
        ir = irl > 1e-3 ? ir / irl : vec3(cos(iyaw), 0.0, -sin(iyaw));
        vec3 iu = cross(iV, ir);
        vec3 iP = iC + (in_uv.x * 2.0 - 1.0) * iR * ir + (in_uv.y * 2.0 - 1.0) * iR * iu;
        vec3 iVl = isl_to_local(iV, iyaw);
        iVl.y = max(iVl.y, 0.0);
        iVl = normalize(iVl + vec3(0.0, 1e-4, 0.0));
        vec2 ipx = iVl.xz / (abs(iVl.x) + abs(iVl.y) + abs(iVl.z));
        vec2 ig = vec2(ipx.x + ipx.y, ipx.x - ipx.y) * 0.5 + 0.5;
        ISL_OUT
    }
"""

_IMP_FS = """
void isl_impostor(){
    float n = float(u_isl_imp_n);
    vec3 P = v_world_pos; vec3 C = v_normal;
    float yaw = v_tangent.z; float R = v_tangent.w;
    vec3 Vl = normalize(v_h3d_color.xyz);
    vec3 Pl = isl_to_local(P - C, yaw);
    vec2 f0, f1, f2; vec3 w;
    isl_oct_frames(v_tangent.xy, n, f0, f1, f2, w);
    vec3 u0 = isl_oct_uv(f0, Pl, Vl, R, n), u1 = isl_oct_uv(f1, Pl, Vl, R, n), u2 = isl_oct_uv(f2, Pl, Vl, R, n);
    vec4 a0 = texture(u_albedo_tex, u0.xy); a0.a *= u0.z;
    vec4 a1 = texture(u_albedo_tex, u1.xy); a1.a *= u1.z;
    vec4 a2 = texture(u_albedo_tex, u2.xy); a2.a *= u2.z;
    float A = w.x * a0.a + w.y * a1.a + w.z * a2.a;
    if (A < u_alpha_cutoff) discard;
    vec3 col = (w.x * a0.a * a0.rgb + w.y * a1.a * a1.rgb + w.z * a2.a * a2.rgb) / max(A, 1e-4);
    vec3 n0 = texture(u_normal_tex, u0.xy).xyz * 2.0 - 1.0;
    vec3 n1 = texture(u_normal_tex, u1.xy).xyz * 2.0 - 1.0;
    vec3 n2 = texture(u_normal_tex, u2.xy).xyz * 2.0 - 1.0;
    vec3 N = normalize(w.x * a0.a * n0 + w.y * a1.a * n1 + w.z * a2.a * n2 + vec3(0.0, 1e-4, 0.0));
    N = normalize(isl_to_world(N, yaw));
    float tb = v_h3d_uv1.x > 0.0 ? v_h3d_uv1.x : 1.0; float tv = v_h3d_uv1.y;
    col *= u_base_color.rgb * clamp(vec3(tb * (1.0 - 0.06 * tv), tb * (1.0 + 0.04 * tv), tb * (1.0 - 0.1 * tv)), 0.0, 1.0);
    g_position = vec4(P, 1.0);
    g_normal = vec4(N, 1.0);
    g_albedo = vec4(pow(col, vec3(2.2)), 0.0);
    g_material = vec4(0.82, 0.0, 1.0, 1.0);
    g_emissive = vec4(0.0, 0.0, 0.0, 1.0);
}
"""

_TERRAIN_GLSL = """
uniform float u_isl_fade;             // 0 = off, >0 fading in (visible share), <0 fading out
float isl_dither(){
    ivec2 p = ivec2(mod(gl_FragCoord.xy, 4.0));
    int i = p.x + p.y * 4;
    int b[16] = int[16](0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5);
    return (float(b[i]) + 0.5) / 16.0;
}
uniform int u_isl_terrain;
uniform sampler2D u_isl_splat;
uniform sampler2DArray u_isl_alb;
uniform sampler2DArray u_isl_nrm;
uniform sampler2DArray u_isl_arm;
uniform vec4 u_isl_rect;              // x0, z0, 1/extent, 0
uniform vec4 u_isl_tile;              // metres per texture repeat: sand, grass, dirt, rock
uniform vec3 u_isl_grass_tint;
uniform vec3 u_isl_sand_tint;
uniform vec4 u_isl_misc;              // sea level, beach top, 0, 0

float isl_hash(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float isl_vnoise(vec2 p){
    vec2 i = floor(p); vec2 f = fract(p); f = f * f * (3.0 - 2.0 * f);
    return mix(mix(isl_hash(i), isl_hash(i + vec2(1.0, 0.0)), f.x),
               mix(isl_hash(i + vec2(0.0, 1.0)), isl_hash(i + vec2(1.0, 1.0)), f.x), f.y);
}
// one planar layer with anti-tiling (two scales mixed by macro noise)
void isl_planar(int k, vec2 p, float m, out vec3 a, out vec3 tn, out vec3 r){
    float s = 1.0 / u_isl_tile[k];
    vec2 uv1 = p * s;
    vec2 uv2 = vec2(p.x * 0.8 - p.y * 0.6, p.x * 0.6 + p.y * 0.8) * s * 0.31 + 0.37;
    vec3 a1 = texture(u_isl_alb, vec3(uv1, float(k))).rgb;
    vec3 a2 = texture(u_isl_alb, vec3(uv2, float(k))).rgb;
    vec3 n1 = texture(u_isl_nrm, vec3(uv1, float(k))).xyz * 2.0 - 1.0;
    vec3 n2 = texture(u_isl_nrm, vec3(uv2, float(k))).xyz * 2.0 - 1.0;
    vec3 r1 = texture(u_isl_arm, vec3(uv1, float(k))).rgb;
    vec3 r2 = texture(u_isl_arm, vec3(uv2, float(k))).rgb;
    float w = clamp(m * 0.8, 0.0, 0.55);
    a = mix(a1, a2, w); tn = normalize(mix(n1, n2, w)); r = mix(r1, r2, w);
}
void isl_terrain(){
    vec3 p = v_world_pos;
    vec3 Ng = normalize(v_normal);
    vec2 suv = (p.xz - u_isl_rect.xy) * u_isl_rect.z;
    vec4 w = texture(u_isl_splat, suv);
    // per-pixel slope auto-material: steep faces turn to rock, whatever the map says
    float slope = 1.0 - Ng.y;
    float mac = isl_vnoise(p.xz * 0.045) * 0.6 + isl_vnoise(p.xz * 0.19) * 0.4;
    float rockw = smoothstep(0.30 + mac * 0.08, 0.46 + mac * 0.08, slope);
    w = mix(w, vec4(0.0, 0.0, w.z * 0.3, 1.0), rockw);
    w = max(w, vec4(0.0));
    w /= max(w.x + w.y + w.z + w.w, 1e-4);
    vec3 A[4]; vec3 TN[4]; vec3 R[4];
    for (int k = 0; k < 3; k++){
        if (w[k] > 0.004) isl_planar(k, p.xz, mac, A[k], TN[k], R[k]);
        else { A[k] = vec3(0.0); TN[k] = vec3(0.0, 0.0, 1.0); R[k] = vec3(1.0, 1.0, 0.0); }
    }
    // rock: triplanar so cliffs don't stretch
    vec3 rockN = Ng;
    if (w.w > 0.004){
        float s = 1.0 / u_isl_tile.w;
        vec3 bw = pow(abs(Ng), vec3(4.0)); bw /= (bw.x + bw.y + bw.z);
        vec3 ax = texture(u_isl_alb, vec3(p.zy * s, 3.0)).rgb;
        vec3 ay = texture(u_isl_alb, vec3(p.xz * s, 3.0)).rgb;
        vec3 az = texture(u_isl_alb, vec3(p.xy * s, 3.0)).rgb;
        vec3 tx = texture(u_isl_nrm, vec3(p.zy * s, 3.0)).xyz * 2.0 - 1.0;
        vec3 ty = texture(u_isl_nrm, vec3(p.xz * s, 3.0)).xyz * 2.0 - 1.0;
        vec3 tz = texture(u_isl_nrm, vec3(p.xy * s, 3.0)).xyz * 2.0 - 1.0;
        vec3 rx = texture(u_isl_arm, vec3(p.zy * s, 3.0)).rgb;
        vec3 ry = texture(u_isl_arm, vec3(p.xz * s, 3.0)).rgb;
        vec3 rz = texture(u_isl_arm, vec3(p.xy * s, 3.0)).rgb;
        A[3] = ax * bw.x + ay * bw.y + az * bw.z;
        R[3] = rx * bw.x + ry * bw.y + rz * bw.z;
        vec3 nX = vec3(0.0, tx.y, tx.x) * sign(Ng.x);
        vec3 nY = vec3(ty.x, 0.0, ty.y);
        vec3 nZ = vec3(tz.x, tz.y, 0.0) * sign(Ng.z);
        rockN = normalize(Ng + nX * bw.x + nY * bw.y + nZ * bw.z);
        TN[3] = vec3(0.0, 0.0, 1.0);
    } else { A[3] = vec3(0.0); R[3] = vec3(1.0, 1.0, 0.0); TN[3] = vec3(0.0, 0.0, 1.0); }
    A[1] *= u_isl_grass_tint;
    A[0] = min(A[0] * u_isl_sand_tint, vec3(1.0));
    // height blending: the 'taller' texture wins at the borders (crisp, natural transitions)
    float hgt[4];
    for (int k = 0; k < 4; k++) hgt[k] = dot(A[k], vec3(0.333)) * (k == 3 ? 1.4 : 1.0) + (k == 2 ? -0.05 : 0.0);
    float mx = 0.0;
    for (int k = 0; k < 4; k++) mx = max(mx, w[k] + hgt[k] * 0.5 * step(0.004, w[k]));
    float bw4[4]; float tot = 0.0;
    for (int k = 0; k < 4; k++){ bw4[k] = max(w[k] + hgt[k] * 0.5 - (mx - 0.16), 0.0) * step(0.004, w[k]); tot += bw4[k]; }
    tot = max(tot, 1e-4);
    vec3 albedo = vec3(0.0); vec3 arm = vec3(0.0); vec3 tn = vec3(0.0);
    for (int k = 0; k < 4; k++){
        float b = bw4[k] / tot;
        albedo += A[k] * b; arm += R[k] * b; tn += TN[k] * b;
    }
    float brock = bw4[3] / tot;
    // planar layers share a top-down TBN (uv = world xz)
    vec3 T = normalize(vec3(Ng.y, -Ng.x, 0.0));
    vec3 B = cross(T, Ng);
    vec3 Np = normalize(T * tn.x + B * tn.y + Ng * max(tn.z, 0.2));
    vec3 N = normalize(mix(Np, rockN, brock));
    // macro brightness variation + wet sand at the waterline
    albedo *= 0.88 + 0.24 * mac;
    float wet = smoothstep(u_isl_misc.x + 0.9, u_isl_misc.x + 0.05, p.y) * (bw4[0] / tot + bw4[2] / tot);
    albedo *= mix(1.0, 0.6, wet);
    float rough = clamp(mix(arm.g, 0.22, wet), 0.05, 1.0);
    albedo = pow(albedo, vec3(2.2));
    g_position = vec4(p, 1.0);
    g_normal = vec4(N, 0.0);
    g_albedo = vec4(albedo, 0.0);
    g_material = vec4(rough, 0.0, 1.0, clamp(arm.r * 0.6 + 0.4, 0.0, 1.0));
    g_emissive = vec4(0.0, 0.0, 0.0, 1.0);
}
"""


def _patch_shaders():
    m = _m()
    ok = []
    # ---- geometry vertex: wind on foliage (attribute 10, unbound = no wind)
    gv = m.GEOMETRY_VERT
    if "isl_wind" not in gv:
        a = "uniform mat4 u_model;"
        b = "    v_world_pos = wp.xyz;"
        if a in gv and b in gv:
            gv = gv.replace(a, a + "\nlayout (location = 10) in vec4 in_isl;" + _WIND_GLSL, 1)
            gv = gv.replace(b, "    wp.xyz += isl_wind(wp.xyz, in_isl);\n" + b, 1)
            ok.append(True)
        else:
            ok.append(False)
        m.GEOMETRY_VERT = gv
    # ---- geometry vertex: octahedral impostor billboards
    gv = m.GEOMETRY_VERT
    imp_ok = False
    if "isl_oct_dir" not in gv and "v_h3d_color" in gv and "v_h3d_uv1" in gv and "in_normal" in gv and "u_view" in gv:
        body = gv.rstrip()
        if body.endswith("}"):
            vs = _IMP_VS.replace("ISL_VIEWDIR", "normalize(inverse(u_view)[3].xyz - iC)").replace(
                "ISL_OUT", "v_world_pos = iP; v_normal = iC; v_tangent = vec4(ig * (float(u_isl_imp_n) - 1.0), iyaw, iR);"
                           " v_h3d_color = vec4(iVl, 1.0); v_h3d_uv1 = in_normal.xy; gl_Position = u_proj * u_view * vec4(iP, 1.0);")
            gv = gv.replace("uniform mat4 u_model;", "uniform mat4 u_model;" + _OCT_GLSL, 1)
            gv = gv.rstrip()[:-1] + vs + "}\n"
            imp_ok = True
        m.GEOMETRY_VERT = gv
    # ---- geometry fragment: terrain material + impostor branches
    gf = m.GEOMETRY_FRAG
    if "isl_terrain" not in gf:
        a = "void main(){"
        if a in gf and "g_emissive" in gf:
            extra = ""
            if imp_ok and "v_h3d_color" in gf and "v_h3d_uv1" in gf and "u_normal_tex" in gf and "u_albedo_tex" in gf:
                gf = gf.replace(a, _OCT_GLSL + _IMP_FS + "\n" + a, 1)
                extra = "\n    if (u_isl_imp == 1) { isl_impostor(); return; }"
            else:
                imp_ok = False
            gf = gf.replace(a, _TERRAIN_GLSL + "\n" + a +
                            "\n    if (u_isl_fade != 0.0){ float isl_d = isl_dither();"
                            " if (u_isl_fade > 0.0 ? isl_d >= u_isl_fade : isl_d < -u_isl_fade) discard; }"
                            "\n    if (u_isl_terrain == 1) { isl_terrain(); return; }" + extra, 1)
            ok.append(True)
        else:
            ok.append(False)
        m.GEOMETRY_FRAG = gf
    # ---- shadow: alpha-tested cut-outs + wind
    sv = m.SHADOW_VERT
    if "isl_wind" not in sv:
        a = "uniform mat4 u_light_space;"
        if a in sv and sv.rstrip().endswith("}"):
            sv = sv.replace(a, a + "\nlayout (location = 2) in vec4 in_tangent;\nlayout (location = 3) in vec2 in_uv;"
                            "\nlayout (location = 10) in vec4 in_isl;"
                            "\nout vec2 v_isl_uv;\nout vec3 v_isl_P;\nout vec3 v_isl_C;\nout vec4 v_isl_T;\nout vec3 v_isl_V;"
                            "\nuniform vec3 u_isl_light_dir;" + _WIND_GLSL + _OCT_GLSL, 1)
            body = sv.rstrip()[:-1]
            svs = _IMP_VS.replace("ISL_VIEWDIR", "normalize(u_isl_light_dir)").replace(
                "ISL_OUT", "v_isl_P = iP; v_isl_C = iC; v_isl_T = vec4(ig * (float(u_isl_imp_n) - 1.0), iyaw, iR);"
                           " v_isl_V = iVl; gl_Position = u_light_space * vec4(iP, 1.0);")
            sv = body + ("    v_isl_uv = in_uv;\n    v_isl_P = vec3(0.0); v_isl_C = vec3(0.0); v_isl_T = vec4(0.0); v_isl_V = vec3(0.0, 1.0, 0.0);\n"
                         "    if (in_isl.x > 0.0) { vec4 isl_w = u_model * vec4(in_position, 1.0);"
                         " gl_Position += u_light_space * vec4(isl_wind(isl_w.xyz, in_isl), 0.0); }\n" + svs + "}\n")
            ok.append(True)
        else:
            ok.append(False)
        m.SHADOW_VERT = sv
    sf = m.SHADOW_FRAG
    if "u_isl_alpha" not in sf:
        if "void main(){}" in sf:
            sf = sf.replace("void main(){}", "in vec2 v_isl_uv;\nin vec3 v_isl_P;\nin vec3 v_isl_C;\nin vec4 v_isl_T;\nin vec3 v_isl_V;\n"
                            "uniform int u_isl_alpha;\nuniform sampler2D u_isl_alpha_tex;\n" + _OCT_GLSL +
                            "void main(){\n"
                            "    if (u_isl_alpha == 1 && texture(u_isl_alpha_tex, v_isl_uv).a < 0.5) discard;\n"
                            "    if (u_isl_alpha == 2){\n"
                            "        float n = float(u_isl_imp_n); vec2 f0, f1, f2; vec3 w;\n"
                            "        isl_oct_frames(v_isl_T.xy, n, f0, f1, f2, w);\n"
                            "        vec3 Pl = isl_to_local(v_isl_P - v_isl_C, v_isl_T.z); vec3 Vl = normalize(v_isl_V);\n"
                            "        vec3 u0 = isl_oct_uv(f0, Pl, Vl, v_isl_T.w, n), u1 = isl_oct_uv(f1, Pl, Vl, v_isl_T.w, n),"
                            " u2 = isl_oct_uv(f2, Pl, Vl, v_isl_T.w, n);\n"
                            "        float A = w.x * texture(u_isl_alpha_tex, u0.xy).a * u0.z + w.y * texture(u_isl_alpha_tex, u1.xy).a * u1.z"
                            " + w.z * texture(u_isl_alpha_tex, u2.xy).a * u2.z;\n"
                            "        if (A < 0.5) discard;\n"
                            "    }\n}")
            ok.append(True)
        else:
            ok.append(False)
        m.SHADOW_FRAG = sf
    for k in ("geometry", "shadow", "geometry_inst"):
        m.ShaderLib._cache.pop(k, None)
    STATE["shaders_ok"] = all(ok)
    STATE["imp_ok"] = bool(locals().get("imp_ok")) and "isl_impostor" in m.GEOMETRY_FRAG and "isl_oct_dir" in m.SHADOW_FRAG
    if not all(ok):
        H3D.warn(f"tropical island shader hooks partially applied {ok}")


# =========================================================================== assets
class _Part:
    """One drawable piece of one LOD of one variant: geometry + material key."""
    __slots__ = ("P", "N", "T", "UV", "S", "I", "mat")


class _Species:
    __slots__ = ("name", "group", "variants", "mats", "meta", "height")


def _tangents(P, N, UV, I):
    t = np.zeros((len(P), 3))
    p0, p1, p2 = P[I[:, 0]], P[I[:, 1]], P[I[:, 2]]
    u0, u1, u2 = UV[I[:, 0]], UV[I[:, 1]], UV[I[:, 2]]
    e1, e2 = p1 - p0, p2 - p0
    d1, d2 = u1 - u0, u2 - u0
    r = d1[:, 0] * d2[:, 1] - d2[:, 0] * d1[:, 1]
    r = np.where(np.abs(r) < 1e-12, 1e-12, r)
    tt = (e1 * d2[:, 1:2] - e2 * d1[:, 1:2]) / r[:, None]
    for k in range(3):
        np.add.at(t, I[:, k], tt)
    t = t - N * np.sum(t * N, axis=1, keepdims=True)
    ln = np.linalg.norm(t, axis=1, keepdims=True)
    bad = ln[:, 0] < 1e-8
    t[bad] = np.cross(N[bad], [0.0, 0.0, 1.0]) + 1e-6
    t /= np.maximum(np.linalg.norm(t, axis=1, keepdims=True), 1e-8)
    return np.hstack([t, np.ones((len(P), 1))]).astype(np.float32)


def _library():
    lib = STATE["lib"]
    if lib is not None:
        return lib
    m = _m()
    d = H3D.asset(os.path.join("assets", "veg"))
    lib = {}
    for name, (group, amp, sink, shadow) in SPECIES.items():
        path = os.path.join(d, name + ".npz")
        if not os.path.isfile(path):
            _warn_once("asset" + name, f"Tropical Islands: missing asset {path}")
            continue
        z = np.load(path)
        meta = json.loads(bytes(z["meta.json"]).decode())
        parts = {}
        for k in z.files:
            if k == "meta.json":
                continue
            tag, field = k.rsplit(".", 1)
            parts.setdefault(tag, {})[field] = z[k]
        sp = _Species()
        sp.name, sp.group, sp.meta = name, group, meta
        nv = int(meta.get("variants", 1))
        sp.variants = [[[] for _ in range(3)] for _ in range(nv)]
        for tag, arr in parts.items():
            bits = tag.split(".")
            vi, li, part = int(bits[0]), int(bits[1]), bits[2]
            key = part if len(bits) < 4 else part + ":" + bits[3]
            pp = _Part()
            pp.P = arr["P"].astype(np.float32)
            pp.N = arr["N"].astype(np.float32)
            uv = arr["UV"].astype(np.float32).copy()
            uv[:, 1] = 1.0 - uv[:, 1]                       # glTF top-down -> GL bottom-up
            pp.UV = uv
            pp.S = arr["S"].astype(np.float32)
            pp.I = arr["I"].astype(np.uint32)
            pp.T = _tangents(pp.P.astype(np.float64), pp.N.astype(np.float64), pp.UV.astype(np.float64),
                             pp.I.astype(np.int64))
            pp.mat = key
            sp.variants[vi][li].append(pp)
        sp.mats = {}

        def mat(key, base=None, normal=None, mr=None, mask=False, rough=0.8):
            def ap(f):
                return os.path.join(d, f) if f else None
            bc = TINTS.get((name, key.split(":")[0])) or TINTS.get((group, key.split(":")[0])) or (1.0, 1.0, 1.0)
            mt = m.Material(name=f"Island {name} {key}", base_color=(bc[0], bc[1], bc[2], 1.0), metallic=0.0,
                            roughness=rough, base_color_tex=ap(base), normal_tex=ap(normal),
                            metallic_roughness_tex=ap(mr), double_sided=mask,
                            alpha_mode="MASK" if mask else "OPAQUE", alpha_cutoff=0.5)
            sp.mats[key] = mt
        if "cards" in meta:
            mat("cards", meta["cards"]["albedo"], meta["cards"]["normal"], None, True, 0.78)
        if meta.get("kind") == "palm":
            mat("palm", meta["albedo"], meta["normal"], meta["mr"], True, 1.0)
        if "imp" in meta:
            mat("imp", meta["imp"]["albedo"], meta["imp"]["normal"], None, True, 0.85)
        for oi, om in enumerate((meta.get("oct") or {}).get("variants", [])):
            mat(f"oct{oi}", om["albedo"], om["normal"], None, True, 0.82)
        if meta.get("kind") == "rock":
            mat("rock", meta["mat"].get("base"), meta["mat"].get("normal"), meta["mat"].get("mr"), False, 1.0)
        for wk, wm in (meta.get("wood") or {}).items():
            mat("wood:" + wk, wm.get("base"), wm.get("normal"), wm.get("mr"), False, 1.0)
        sp.height = float(meta.get("height", 1.0))
        lib[name] = sp
    STATE["lib"] = lib
    return lib


def _terrain_textures(ctx):
    tt = STATE["terrain_tex"]
    if tt is not None:
        return tt
    from PIL import Image
    mgl = CLS["moderngl"]
    d = H3D.asset(os.path.join("assets", "terrain"))
    layers = ("aerial_beach_01", "forrest_ground_01", "dirt_floor", "mossy_rock")
    out = {}
    for kind, size in (("albedo", 2048), ("normal", 1024), ("arm", 1024)):
        data = []
        for ly in layers:
            im = Image.open(os.path.join(d, f"{ly}_{kind}.jpg")).convert("RGBA")
            if im.size != (size, size):
                im = im.resize((size, size), Image.LANCZOS)
            data.append(im.transpose(Image.FLIP_TOP_BOTTOM).tobytes())
        tex = ctx.texture_array((size, size, len(layers)), 4, b"".join(data))
        tex.build_mipmaps()
        tex.filter = (mgl.LINEAR_MIPMAP_LINEAR, mgl.LINEAR)
        try:
            tex.anisotropy = 8.0
        except Exception:
            pass
        out[kind] = tex
    STATE["terrain_tex"] = out
    return out


# =========================================================================== GPU meshes
def _mesh_classes():
    m = _m()
    moderngl = __import__("moderngl")
    CLS["moderngl"] = moderngl
    glm = m.glm

    class _IslVAO:
        __slots__ = ("vao", "prog", "mesh", "shadow")

        def __init__(self, vao, prog, mesh):
            self.vao, self.prog, self.mesh = vao, prog, mesh
            mem = set(prog._members.keys()) if hasattr(prog, "_members") else set()
            self.shadow = "u_light_space" in mem and "u_proj" not in mem

        def render(self, *a, **kw):
            mesh, prog = self.mesh, self.prog
            if self.shadow:
                if not mesh.cast_shadow:
                    return None
                fd = mesh.fade
                if (fd > 0.0 and fd < 0.5) or (fd < 0.0 and -fd >= 0.5):
                    return None                    # shadows switch LOD halfway through the fade
                lim = mesh.shadow_limit
                cam = STATE["cam"]
                if lim > 0 and cam is not None:
                    c = mesh.center
                    if (c[0] - cam[0]) ** 2 + (c[2] - cam[2]) ** 2 > lim * lim:
                        return None
                if mesh.imp and mesh.alpha_tex is not None:
                    try:
                        tex = _renderer().get_texture(mesh.alpha_tex)
                        tex.use(location=UNIT_ALPHA)
                        prog["u_isl_alpha_tex"].value = UNIT_ALPHA
                        prog["u_isl_alpha"].value = 2
                        prog["u_isl_imp"].value = 1
                        prog["u_isl_imp_n"].value = mesh.imp
                        sun = getattr(STATE["engine"].scene, "sun", None)
                        d = sun.direction if sun is not None else None
                        prog["u_isl_light_dir"].value = (-float(d.x), -float(d.y), -float(d.z)) if d is not None else (0.3, 0.9, 0.2)
                        try:
                            return self.vao.render(*a, **kw)
                        finally:
                            prog["u_isl_alpha"].value = 0
                            prog["u_isl_imp"].value = 0
                    except KeyError:
                        return None
                if mesh.alpha_tex is not None:
                    try:
                        tex = _renderer().get_texture(mesh.alpha_tex)
                        tex.use(location=UNIT_ALPHA)
                        prog["u_isl_alpha_tex"].value = UNIT_ALPHA
                        prog["u_isl_alpha"].value = 1
                        try:
                            return self.vao.render(*a, **kw)
                        finally:
                            prog["u_isl_alpha"].value = 0
                    except KeyError:
                        pass
                return self.vao.render(*a, **kw)
            if mesh.fade != 0.0:
                try:
                    prog["u_isl_fade"].value = float(mesh.fade)
                except KeyError:
                    pass
                try:
                    return self._draw(*a, **kw)
                finally:
                    try:
                        prog["u_isl_fade"].value = 0.0
                    except KeyError:
                        pass
            return self._draw(*a, **kw)

        def _draw(self, *a, **kw):
            mesh, prog = self.mesh, self.prog
            if mesh.imp:
                try:
                    prog["u_isl_imp"].value = 1
                    prog["u_isl_imp_n"].value = mesh.imp
                except KeyError:
                    return None                     # no impostor path in this program: draw nothing
                try:
                    return self.vao.render(*a, **kw)
                finally:
                    prog["u_isl_imp"].value = 0
            if mesh.terrain is not None:
                isl = mesh.terrain
                try:
                    isl.bind_terrain(prog)
                    prog["u_isl_terrain"].value = 1
                except KeyError:
                    return self.vao.render(*a, **kw)
                try:
                    return self.vao.render(*a, **kw)
                finally:
                    prog["u_isl_terrain"].value = 0
            return self.vao.render(*a, **kw)

        def release(self):
            try:
                self.vao.release()
            except Exception:
                pass

        def __getattr__(self, name):
            return getattr(self.vao, name)

    class IslandMesh(m.Mesh):
        """Merged island geometry: engine layout (pos, normal, tangent, uv) plus a
        second buffer with wind (in_isl) and colour variation (in_h3d_color)."""

        def __init__(self, ctx, name, inter, idx, extra=None, bbox=None, cpu=None):
            super().__init__(ctx, name)
            self.vbo = ctx.buffer(np.ascontiguousarray(inter, dtype=np.float32).tobytes())
            self.ibo = ctx.buffer(np.ascontiguousarray(idx, dtype=np.uint32).tobytes())
            self.xvbo = ctx.buffer(np.ascontiguousarray(extra, dtype=np.float32).tobytes()) if extra is not None else None
            self.vertex_count, self.index_count, self.indexed = len(inter), int(idx.size), True
            lo, hi = bbox
            self.bounding_box = (glm.vec3(*map(float, lo)), glm.vec3(*map(float, hi)))
            c = (np.asarray(lo) + np.asarray(hi)) * 0.5
            self.center = (float(c[0]), float(c[1]), float(c[2]))
            self.bounding_sphere = (glm.vec3(*self.center), float(np.linalg.norm(np.asarray(hi) - np.asarray(lo)) * 0.5))
            if cpu is not None:
                self.cpu_positions, self.cpu_indices = cpu
            self.cast_shadow = True
            self.shadow_limit = 0.0
            self.alpha_tex = None
            self.terrain = None
            self.imp = 0                       # octahedral impostor frames per side (0 = normal mesh)
            self.fade = 0.0                    # LOD cross-fade state (see IslandRuntime.update_fades)

        def get_vao(self, program):
            key = ("isl", id(program))
            v = self._vaos.get(key)
            if v is not None:
                return v
            attrs = set(program._members.keys()) if hasattr(program, "_members") else set()
            fmt, names = [], []
            for nm, n in (("in_position", 3), ("in_normal", 3), ("in_tangent", 4), ("in_uv", 2)):
                if nm in attrs:
                    fmt.append(f"{n}f")
                    names.append(nm)
                else:
                    fmt.append(f"{n}x4")
            content = [(self.vbo, " ".join(fmt), *names)]
            if self.xvbo is not None:
                xf, xn = [], []
                for nm in ("in_isl", "in_h3d_color"):
                    if nm in attrs:
                        xf.append("4f")
                        xn.append(nm)
                    else:
                        xf.append("4x4")
                if xn:
                    content.append((self.xvbo, " ".join(xf), *xn))
            vao = self.ctx.vertex_array(program, content, index_buffer=self.ibo)
            v = _IslVAO(vao, program, self)
            self._vaos[key] = v
            return v

        def release(self):
            for v in list(self._vaos.values()):
                try:
                    v.release()
                except Exception:
                    pass
            self._vaos.clear()
            for b in (self.vbo, self.ibo, self.xvbo):
                try:
                    if b is not None:
                        b.release()
                except Exception:
                    pass
            self.vbo = self.ibo = self.xvbo = None

    CLS["IslandMesh"] = IslandMesh


def _renderer():
    return STATE["engine"].renderer


# =========================================================================== merging
def _inst_tint(inst):
    """Per-instance colour variation from the instance position (stable across LODs)."""
    h1 = np.sin(inst[:, 0:1] * 12.9898 + inst[:, 2:3] * 78.233) * 43758.5453
    h2 = np.sin(inst[:, 0:1] * 39.3468 + inst[:, 2:3] * 11.135) * 24634.6345
    return (h1 - np.floor(h1)) * 2.0 - 1.0, 0.86 + (h2 - np.floor(h2)) * 0.24


def _merge_instances(parts_by_inst, amp, sink, group, seed):
    """parts_by_inst: list of (part, inst (k, 6)).  Returns interleaved arrays."""
    P_all, N_all, T_all, U_all, X_all, I_all = [], [], [], [], [], []
    base = 0
    rng = np.random.default_rng(seed)
    for part, inst in parts_by_inst:
        k = len(inst)
        if k == 0:
            continue
        nv = len(part.P)
        yaw, sc = inst[:, 3:4], inst[:, 4:5]
        c, s = np.cos(yaw), np.sin(yaw)
        px, py, pz = part.P[None, :, 0], part.P[None, :, 1], part.P[None, :, 2]
        x = (c * px + s * pz) * sc + inst[:, 0:1]
        z = (-s * px + c * pz) * sc + inst[:, 2:3]
        y = py * sc + inst[:, 1:2] - sink * sc
        P = np.stack([x, y, z], -1).reshape(-1, 3)
        nx, ny, nz = part.N[None, :, 0], part.N[None, :, 1], part.N[None, :, 2]
        N = np.stack([c * nx + s * nz, np.broadcast_to(ny, (k, nv)), -s * nx + c * nz], -1).reshape(-1, 3)
        tx, ty, tz, tw = part.T[None, :, 0], part.T[None, :, 1], part.T[None, :, 2], part.T[None, :, 3]
        T = np.stack([c * tx + s * tz, np.broadcast_to(ty, (k, nv)), -s * tx + c * tz, np.broadcast_to(tw, (k, nv))],
                     -1).reshape(-1, 4)
        U = np.broadcast_to(part.UV[None], (k, nv, 2)).reshape(-1, 2)
        phase = rng.uniform(0, 6.283, (k, 1))
        jit = rng.uniform(0.0, 0.5, (k, 1))
        sway = part.S[None, :] * amp * sc
        flutter = np.broadcast_to(part.S[None, :] * ((0.02 if group == "tree" else 0.012) * (amp > 0)), (k, nv))
        X = np.stack([sway, np.broadcast_to(phase, (k, nv)), np.broadcast_to(jit, (k, nv)), flutter], -1).reshape(-1, 4)
        # colour variation (stored as 1 - rgb for the engine's vertex colour channel)
        if group in ("grass", "flower", "tree"):
            v, br = _inst_tint(inst)
            col = np.concatenate([br * (1.0 - 0.06 * v), br * (1.0 + 0.04 * v), br * (1.0 - 0.1 * v)], 1)
        else:
            col = rng.uniform(0.85, 1.05, (k, 1)) * np.ones((k, 3))
        col = np.clip(col, 0.0, 1.0)
        C = np.concatenate([1.0 - col, np.ones((k, 1))], 1)
        C = np.broadcast_to(C[:, None, :], (k, nv, 4)).reshape(-1, 4)
        I = (part.I[None, :, :].astype(np.int64) + (np.arange(k)[:, None, None] * nv)).reshape(-1, 3) + base
        P_all.append(P); N_all.append(N); T_all.append(T); U_all.append(U)
        X_all.append(np.concatenate([X, C], 1)); I_all.append(I)
        base += k * nv
    if not P_all:
        return None
    P = np.vstack(P_all).astype(np.float32)
    inter = np.hstack([P, np.vstack(N_all), np.vstack(T_all), np.vstack(U_all)]).astype(np.float32)
    return inter, np.vstack(I_all).astype(np.uint32).reshape(-1), np.vstack(X_all).astype(np.float32), \
        (P.min(0), P.max(0))


# =========================================================================== the island runtime
class IslandRuntime:
    def __init__(self, engine, node, bp):
        self.engine, self.node, self.bp = engine, node, bp
        self.isl = None
        self.key = None
        self.terrain_nodes = []
        self._terrain_set = set()
        self._fading, self._fade_chs = set(), {}
        self._arr = None
        self._last_cam = None
        self._pending = True
        self._snap = set()
        self.chunks = {}             # (species, cx, cz) -> chunk dict
        self.queue = []
        self.splat_tex = None
        self.col_pool = []
        self.col_t = 0.0

    # ---------------------------------------------------------------- params
    def gen_params(self):
        p = dict(G.DEFAULTS)
        for k in GEN_KEYS:
            if k in self.bp.properties:
                p[k] = self.bp.properties[k]
        return p

    def view(self, k):
        return self.bp.properties.get(k, VIEW_DEFAULTS[k])

    @staticmethod
    def params_key(p):
        return hashlib.sha1(json.dumps([getattr(G, "GEN_VERSION", 0), p], sort_keys=True).encode()).hexdigest()[:16]

    # ---------------------------------------------------------------- build
    def build(self):
        p = self.gen_params()
        key = self.params_key(p)
        t0 = time.time()
        isl = self._load_cache(key)
        if isl is None:
            H3D.log(f"Generating tropical island '{self.node.name}' ({p['topology']}, {p['size']:.0f} m, seed {p['seed']}) ...")
            isl = G.generate(p)
            self._save_cache(key, isl)
        self.clear()
        self.isl, self.key = isl, key
        self._build_terrain()
        self._collider()
        self._assign_chunks()
        cam = self.engine.camera.position
        self.update_chunks((cam.x, cam.y, cam.z), budget=None)
        if self.view("tropical_water"):
            _tropical_water(self.engine)
        H3D.log(f"Island '{self.node.name}' ready in {time.time() - t0:.1f} s: "
                + ", ".join(f"{len(v)} {k}" for k, v in isl.instances.items() if len(v)))

    def _cache_dir(self):
        pp = getattr(self.engine, "project_path", None)
        base = os.path.dirname(os.path.abspath(pp)) if pp else None
        if not base or not os.path.isdir(base):
            return None
        return os.path.join(base, ".island_cache")

    def _load_cache(self, key):
        d = self._cache_dir()
        if not d:
            return None
        f = os.path.join(d, f"island_{key}.npz")
        if not os.path.isfile(f):
            return None
        try:
            z = np.load(f, allow_pickle=False)
            isl = G.Island()
            meta = json.loads(bytes(z["meta"]).decode())
            isl.params, isl.extent, isl.cell = meta["params"], meta["extent"], meta["cell"]
            isl.spawn, isl.sea_level, isl.beach_top = tuple(meta["spawn"]), meta["sea_level"], meta["beach_top"]
            isl.heights, isl.splat = z["heights"], z["splat"]
            isl.instances = {k: z["inst_" + k] for k in meta["species"]}
            isl.roads = [z[f"road_{i}"] for i in range(meta["roads"])]
            return isl
        except Exception as ex:
            _warn_once("cache" + key, f"island cache unreadable ({ex}); regenerating")
            return None

    def _save_cache(self, key, isl):
        d = self._cache_dir()
        if not d:
            return
        try:
            os.makedirs(d, exist_ok=True)
            for f in os.listdir(d):                       # keep the cache small
                if f.startswith("island_") and f.endswith(".npz"):
                    fp = os.path.join(d, f)
                    if time.time() - os.path.getmtime(fp) > 3600 * 24 * 7:
                        os.remove(fp)
            meta = {"params": isl.params, "extent": isl.extent, "cell": isl.cell, "spawn": list(isl.spawn),
                    "sea_level": isl.sea_level, "beach_top": isl.beach_top, "species": list(isl.instances),
                    "roads": len(isl.roads)}
            arrays = {"heights": isl.heights, "splat": isl.splat,
                      "meta": np.frombuffer(json.dumps(meta).encode(), np.uint8)}
            for k, v in isl.instances.items():
                arrays["inst_" + k] = v
            for i, r in enumerate(isl.roads):
                arrays[f"road_{i}"] = r
            np.savez_compressed(os.path.join(d, f"island_{key}.npz"), **arrays)
        except Exception as ex:
            _warn_once("cachew", f"could not write the island cache: {ex}")

    def clear(self):
        for n in self.terrain_nodes:
            self._drop(n)
        self.terrain_nodes = []
        for ch in self.chunks.values():
            for n in ch["nodes"] + ch.get("old", []):
                self._drop(n)
        self.chunks = {}
        self._fading, self._fade_chs = set(), {}
        self._arr = None
        self._last_cam = None
        self._pending = True
        self._snap = set()
        self.queue = []
        for n in self.col_pool:
            self._drop(n)
        self.col_pool = []
        if self.splat_tex is not None:
            try:
                self.splat_tex.release()
            except Exception:
                pass
            self.splat_tex = None

    def _drop(self, n):
        try:
            if n.mesh is not None:
                n.mesh.release()
        except Exception:
            pass
        try:
            if n.parent is not None:
                n.remove()
        except Exception:
            pass

    def _child(self, name, mesh, mat):
        m = _m()
        ch = m.SceneNode(f"{self.node.name} {name}", parent=self.node)
        ch._heaven3d_imported_part = "island"
        ch._h3d_locked = True
        ch.mesh = mesh
        ch.material = mat
        return ch

    # ---------------------------------------------------------------- terrain
    def _build_terrain(self):
        m = _m()
        isl = self.isl
        ctx = self.engine.ctx
        h = isl.heights.astype(np.float32)
        N = h.shape[0]
        ext = isl.extent
        xs = np.linspace(-ext / 2, ext / 2, N).astype(np.float32)
        gy, gx = np.gradient(h.astype(np.float64), isl.cell)
        nrm = np.dstack([-gx, np.ones_like(gx), -gy])
        nrm /= np.linalg.norm(nrm, axis=2, keepdims=True)
        sp = isl.splat
        mgl = CLS["moderngl"]
        self.splat_tex = ctx.texture((sp.shape[1], sp.shape[0]), 4, np.ascontiguousarray(sp).tobytes())
        self.splat_tex.filter = (mgl.LINEAR, mgl.LINEAR)
        self.splat_tex.repeat_x = self.splat_tex.repeat_y = False
        _terrain_textures(ctx)
        mat = m.Material(name="Island terrain", base_color=(1, 1, 1, 1), roughness=0.9)
        nch = 8
        step = (N - 1) // nch
        for cz in range(nch):
            for cx in range(nch):
                z0, x0 = cz * step, cx * step
                z1, x1 = (N - 1) if cz == nch - 1 else z0 + step, (N - 1) if cx == nch - 1 else x0 + step
                sub = h[z0:z1 + 1, x0:x1 + 1]
                if sub.max() < -12.0:                     # deep sea: the ocean hides it anyway
                    continue
                X, Z = np.meshgrid(xs[x0:x1 + 1], xs[z0:z1 + 1])
                P = np.stack([X, sub, Z], -1).reshape(-1, 3)
                Nn = nrm[z0:z1 + 1, x0:x1 + 1].reshape(-1, 3)
                T = np.zeros((len(P), 4), np.float32)
                T[:, 0] = Nn[:, 1]
                T[:, 1] = -Nn[:, 0]
                T[:, 3] = 1.0
                T[:, :3] /= np.maximum(np.linalg.norm(T[:, :3], axis=1, keepdims=True), 1e-6)
                UV = np.stack([(X + ext / 2) / ext, (Z + ext / 2) / ext], -1).reshape(-1, 2)
                inter = np.hstack([P, Nn, T, UV]).astype(np.float32)
                w, hh = x1 - x0 + 1, z1 - z0 + 1
                a = (np.arange(hh - 1)[:, None] * w + np.arange(w - 1)[None, :]).reshape(-1)
                idx = np.stack([a, a + w, a + 1, a + 1, a + w, a + w + 1], 1).reshape(-1).astype(np.uint32)
                mesh = CLS["IslandMesh"](ctx, "island_terrain", inter, idx, None, (P.min(0), P.max(0)),
                                         cpu=(P.astype(np.float32), idx))
                mesh.terrain = self
                self.terrain_nodes.append(self._child(f"terrain {cx},{cz}", mesh, mat))
        self._terrain_set = set(self.terrain_nodes)

    def bind_terrain(self, prog):
        tt = STATE["terrain_tex"]
        self.splat_tex.use(location=UNIT_SPLAT)
        tt["albedo"].use(location=UNIT_ALB)
        tt["normal"].use(location=UNIT_NRM)
        tt["arm"].use(location=UNIT_ARM)
        prog["u_isl_splat"].value = UNIT_SPLAT
        prog["u_isl_alb"].value = UNIT_ALB
        prog["u_isl_nrm"].value = UNIT_NRM
        prog["u_isl_arm"].value = UNIT_ARM
        wp = self.node.world_position()
        e = self.isl.extent
        prog["u_isl_rect"].value = (float(wp.x - e / 2), float(wp.z - e / 2), 1.0 / e, 0.0)
        prog["u_isl_tile"].value = (7.0, 5.0, 4.0, 14.0)
        prog["u_isl_grass_tint"].value = (0.92, 1.08, 0.78)
        if "u_isl_sand_tint" in prog:
            prog["u_isl_sand_tint"].value = (1.32, 1.22, 1.0)
        prog["u_isl_misc"].value = (float(wp.y), float(wp.y + self.isl.beach_top), 0.0, 0.0)

    def _collider(self):
        m = _m()
        isl = self.isl
        try:
            hf = m.HeightfieldCollider(size=isl.extent, resolution=8, height_scale=0.0, seed=0)
            hf.size = float(isl.extent)
            hf.resolution = int(isl.heights.shape[0] - 1)
            hf.heights = isl.heights.astype(np.float32)
            hf._cell = hf.size / hf.resolution
            self.node.collider = hf
        except Exception as ex:
            _warn_once("hf", f"island collider not created: {ex}")

    def height_at(self, x, z):
        wp = self.node.world_position()
        return float(G.height_at(self.isl, x - wp.x, z - wp.z)) + wp.y

    # ---------------------------------------------------------------- vegetation chunks
    def _assign_chunks(self):
        lib = _library()
        e = self.isl.extent
        for name, inst in self.isl.instances.items():
            if name not in lib or len(inst) == 0:
                continue
            group = lib[name].group
            C = TREE_CHUNK if group in ("tree", "rock") else SMALL_CHUNK
            ix = np.floor((inst[:, 0] + e / 2) / C).astype(np.int64)
            iz = np.floor((inst[:, 2] + e / 2) / C).astype(np.int64)
            key = ix * 100000 + iz
            order = np.argsort(key, kind="stable")
            ks = key[order]
            bounds = np.flatnonzero(np.diff(ks)) + 1
            for grp in np.split(order, bounds):
                if len(grp) == 0:
                    continue
                cx, cz = int(ix[grp[0]]), int(iz[grp[0]])
                cxw = (cx + 0.5) * C - e / 2
                czw = (cz + 0.5) * C - e / 2
                self.chunks[(name, cx, cz)] = {"inst": inst[grp], "center": (cxw, czw), "size": C, "lod": -1,
                                               "y": float(np.median(inst[grp][:, 1])),
                                               "want": -1, "nodes": [], "group": group, "name": name}

    def _chunk_arrays(self):
        keys = list(self.chunks.keys())
        chs = [self.chunks[k] for k in keys]
        gid = {"tree": 0, "rock": 0, "grass": 1, "flower": 2}
        self._arr = {"chs": chs,
                     "cx": np.array([c["center"][0] for c in chs], np.float64),
                     "cz": np.array([c["center"][1] for c in chs], np.float64),
                     "cy": np.array([c.get("y", 0.0) for c in chs], np.float64),
                     "half": np.array([c["size"] * 0.5 for c in chs], np.float64),
                     "g": np.array([gid.get(c["group"], 0) for c in chs], np.int64)}

    def _desired_lods(self, cam):
        """All chunks at once (numpy); same rules as before: 3D distance to the
        chunk, hysteresis around the current LOD, grass / flowers near only."""
        a = getattr(self, "_arr", None)
        if a is None or len(a["chs"]) != len(self.chunks):
            self._chunk_arrays()
            a = self._arr
        if not a["chs"]:
            self._dist = np.zeros(0)
            return a["chs"], np.zeros(0, np.int64)
        wp = self.node.world_position()
        dx = a["cx"] + wp.x - cam[0]
        dz = a["cz"] + wp.z - cam[2]
        dy = np.maximum(0.0, cam[1] - (a["cy"] + wp.y))
        d = np.maximum(0.0, np.sqrt(dx * dx + dz * dz) - a["half"])
        d = np.sqrt(d * d + dy * dy)
        self._dist = d
        cur = np.array([c["lod"] for c in a["chs"]], np.int64)
        near = np.full(len(d), float(self.view("detail_distance")))
        far = np.full(len(d), float(self.view("far_distance")))
        near[cur == 0] += 8.0
        far[cur == 1] += 8.0
        want = np.where(d < near, 0, np.where(d < far, 1, 2))
        gl = float(self.view("grass_distance"))
        want = np.where(a["g"] == 1, np.where(d < gl, 0, 3), want)
        want = np.where(a["g"] == 2, np.where(d < gl * 1.25, 0, 3), want)
        return a["chs"], want

    def update_chunks(self, cam, budget=0.006):
        t0 = time.time()
        last = getattr(self, "_last_cam", None)
        view = tuple(float(self.view(k)) for k in ("detail_distance", "far_distance", "grass_distance"))
        jump2 = 0.0 if last is None else (cam[0] - last[0]) ** 2 + (cam[1] - last[1]) ** 2 + (cam[2] - last[2]) ** 2
        moved = last is None or jump2 > 0.25
        if not moved and view == getattr(self, "_last_view", None) and not getattr(self, "_pending", True):
            return 0
        chs, want = self._desired_lods(cam)
        for ch, w, dd in zip(chs, want.tolist(), self._dist.tolist()):
            ch["want"] = w
            ch["dist"] = dd
        todo = [ch for ch in chs if ch["want"] != ch["lod"]]
        jumped = last is not None and jump2 > 15.0 ** 2
        if jumped:
            # a teleport (game start, respawn, editor focus): the chunks that are wrong
            # right now show the right detail at once instead of cross-fading
            self._snap = {id(ch) for ch in todo}
        self._last_cam, self._last_view = cam, view
        if not todo:
            self._pending = False
            self._snap = set()
            return 0
        # nearest first, by distance to the chunk's edge (what decides its detail),
        # so the trees right next to the camera get their detail first
        todo.sort(key=lambda ch: ch["dist"])
        n = 0
        for ch in todo:
            self._build_chunk(ch)
            n += 1
            if budget is not None and time.time() - t0 > budget:
                if not (jumped and ch is not todo[-1] and todo[n]["dist"] < 25.0):
                    break                          # after a teleport the surroundings come first, whole
        self._pending = n < len(todo)
        self._order_children()
        return n

    def _order_children(self):
        """Keep chunks that share a material next to each other in the scene
        graph, so the renderer binds each material once per run of draws."""
        ch = self.node.children
        rank = {}

        def key(n):
            m = n.material
            if m is None:
                return (0, 0)
            return (1, rank.setdefault(id(m), len(rank)))
        for n in ch:
            key(n)
        if not self.terrain_nodes:
            return
        ch.sort(key=lambda n: (0, 0) if n in self._terrain_set else (2, 0) if n.name.startswith("_runtime_") else key(n))

    FADE_TIME = 0.5

    def _build_chunk(self, ch):
        want = ch["want"]
        for nd in ch.get("old", []):                      # a fade still running: finish it now
            self._drop(nd)
        snap = getattr(self, "_snap", None)
        fade = ch["lod"] >= 0 and bool(self.view("lod_crossfade")) and not (snap and id(ch) in snap)
        if snap:
            snap.discard(id(ch))
        old = ch["nodes"]
        if fade:
            ch["old"] = old
            for nd in old:
                nd.mesh.fade = -1e-4                       # fully visible, fading out
        else:
            ch["old"] = []
            for nd in old:
                self._drop(nd)
        ch["nodes"] = []
        ch["lod"] = want
        ch["t"] = 0.0 if fade else 1.0
        self._build_chunk_nodes(ch)
        if fade:
            for nd in ch["nodes"]:
                nd.mesh.fade = 1e-4                        # invisible, fading in
            self._fading.add(id(ch))
            self._fade_chs[id(ch)] = ch

    def update_fades(self, dt):
        if not self._fading:
            return
        done = []
        for cid in list(self._fading):
            ch = self._fade_chs.get(cid)
            if ch is None:
                done.append(cid)
                continue
            ch["t"] = min(1.0, ch.get("t", 1.0) + dt / self.FADE_TIME)
            t = ch["t"]
            if t >= 1.0:
                for nd in ch.get("old", []):
                    self._drop(nd)
                ch["old"] = []
                for nd in ch["nodes"]:
                    nd.mesh.fade = 0.0
                done.append(cid)
            else:
                for nd in ch["nodes"]:
                    nd.mesh.fade = max(t, 1e-4)
                for nd in ch.get("old", []):
                    nd.mesh.fade = -max(t, 1e-4)
        for cid in done:
            self._fading.discard(cid)
            self._fade_chs.pop(cid, None)

    def _build_chunk_nodes(self, ch):
        want = ch["want"]
        if want >= 3:
            return
        lib = _library()
        sp = lib[ch["name"]]
        group, amp, sink, shadow = SPECIES[ch["name"]]
        inst = ch["inst"].copy()
        wp = self.node.world_position()
        inst[:, 0] += 0.0
        octm = (sp.meta.get("oct") or {})
        if want == 2 and STATE.get("imp_ok") and octm.get("variants"):
            self._build_octahedral(ch, sp, octm, inst, sink if group != "rock" else 0.0, shadow)
            return
        by_mat = {}
        nvar = len(sp.variants)
        var = np.clip(inst[:, 5].astype(np.int64), 0, nvar - 1)
        for vi in range(nvar):
            sel = inst[var == vi]
            if len(sel) == 0:
                continue
            for part in sp.variants[vi][want]:
                by_mat.setdefault(part.mat, []).append((part, sel))
        seed = hash((ch["name"], ch["center"])) & 0xFFFFFFFF
        for mk, items in by_mat.items():
            res = _merge_instances(items, amp if mk != "rock" else 0.0,
                                   sink if group != "rock" else 0.12, group, seed)
            if res is None:
                continue
            inter, idx, extra, bbox = res
            pad = 0.9 if amp > 0 else 0.0
            bbox = (bbox[0] - pad, bbox[1] + pad)
            mesh = CLS["IslandMesh"](self.engine.ctx, f"island_{ch['name']}", inter, idx, extra, bbox)
            mat = sp.mats.get(mk) or sp.mats.get(mk.split(":")[0])
            mesh.cast_shadow = shadow and want < 2 and mk != "imp"
            mesh.shadow_limit = float(self.view("shadow_distance"))
            if mat is not None and (mat.alpha_mode or "").upper() == "MASK":
                mesh.alpha_tex = mat.base_color_tex
            nd = self._child(f"{ch['name']} {mk}", mesh, mat)
            ch["nodes"].append(nd)

    def _build_octahedral(self, ch, sp, octm, inst, sink, shadow):
        """One camera-facing quad per tree, drawn as a hemi-octahedral impostor."""
        vs = octm["variants"]
        var = np.clip(inst[:, 5].astype(np.int64), 0, len(vs) - 1)
        for vi, vm in enumerate(vs):
            sel = inst[var == vi]
            k = len(sel)
            if k == 0:
                continue
            sc = sel[:, 4]
            R = float(vm["radius"]) * sc
            base = np.column_stack([sel[:, 0], sel[:, 1] - sink * sc, sel[:, 2]])
            P = np.repeat(base, 4, axis=0)
            tv, tb = _inst_tint(sel)
            N = np.repeat(np.column_stack([tb[:, 0], tv[:, 0], np.zeros(k)]), 4, axis=0)   # tint (brightness, hue)
            T = np.repeat(np.column_stack([sel[:, 3], R, float(vm["cy"]) * sc, np.ones(k)]), 4, axis=0)
            UV = np.tile([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]], (k, 1))
            I = (np.array([0, 1, 2, 0, 2, 3])[None, :] + (np.arange(k) * 4)[:, None]).reshape(-1)
            inter = np.hstack([P, N, T, UV]).astype(np.float32)
            rmax = float(R.max())
            lo = base.min(0) - [rmax, 0.0, rmax]
            hi = base.max(0) + [rmax, float(vm["cy"]) * float(sc.max()) + rmax, rmax]
            mesh = CLS["IslandMesh"](self.engine.ctx, f"island_{ch['name']}", inter, I.astype(np.uint32), None, (lo, hi))
            mat = sp.mats.get(f"oct{vi}")
            mesh.imp = int(octm["n"])
            mesh.cast_shadow = shadow
            mesh.shadow_limit = float(self.view("shadow_distance"))
            mesh.alpha_tex = mat.base_color_tex if mat is not None else None
            ch["nodes"].append(self._child(f"{ch['name']} oct{vi}", mesh, mat))

    # ---------------------------------------------------------------- tree collision (Play)
    def update_colliders(self, player_pos, dt):
        if not self.view("tree_collision") or self.isl is None:
            return
        self.col_t -= dt
        if self.col_t > 0.0:
            return
        self.col_t = 0.35
        m = _m()
        glm = m.glm
        e = self.engine
        wp = self.node.world_position()
        px, pz = player_pos.x - wp.x, player_pos.z - wp.z
        near = []
        for name in ("coconut_palm", "royal_palm", "island_tree", "jacaranda", "acacia", "boulder"):
            a = self.isl.instances.get(name)
            if a is None or len(a) == 0:
                continue
            d2 = (a[:, 0] - px) ** 2 + (a[:, 2] - pz) ** 2
            sel = np.flatnonzero(d2 < 22.0 ** 2)
            for i in sel[:40]:
                near.append((name, a[i], d2[i]))
        near.sort(key=lambda t: t[2])
        near = near[:48]
        while len(self.col_pool) < len(near):
            n = m.SceneNode(f"_runtime_island_col_{len(self.col_pool)}", parent=self.node)
            n._heaven3d_imported_part = "island"           # hidden in the Hierarchy, never saved
            n._h3d_locked = True
            n.collider = m.CapsuleCollider(radius=0.3, height=4.0)
            self.col_pool.append(n)
        for i, n in enumerate(self.col_pool):
            if i < len(near):
                name, a, _d = near[i]
                if name == "boulder":
                    r, hgt = 0.55 * float(a[4]), 1.2 * float(a[4])
                elif name in ("coconut_palm", "royal_palm"):
                    r, hgt = 0.22 * float(a[4]), 6.0
                else:
                    r, hgt = 0.3 * float(a[4]), 5.0
                n.collider.radius, n.collider.height = r, hgt
                n.position = glm.vec3(float(a[0]), float(a[1]) + hgt * 0.5 - 0.2, float(a[2]))
                n.visible = True
            else:
                n.visible = False
                n.position = glm.vec3(0.0, -10000.0, 0.0)
        self._sync_colliders()

    def drop_colliders(self):
        for n in self.col_pool:
            try:
                self.engine.physics.remove_node(n)
            except Exception:
                pass
            self._drop(n)
        self.col_pool = []

    def ensure_ground(self):
        """Keep the island's heightfield in the physics world.  The engine
        collects colliders when Play starts; an island (re)built after that
        (exported games build it on their first frame) must add itself."""
        ph = getattr(self.engine, "physics", None)
        col = getattr(self.node, "collider", None)
        if ph is None or col is None:
            return
        ent = ph.entry_for(self.node)
        if ent is not None and ent.collider is col:
            return
        try:
            if ent is not None:                    # a stale collider from an earlier build
                ph.remove_node(self.node)
                self.col_t = 0.0                   # (that removed the capsules too: re-add them now)
            ph.add_node(self.node)
        except Exception as ex:
            _warn_once("phys", f"island ground not added to physics: {ex}")

    def _sync_colliders(self):
        """Trunk / boulder capsules follow the player; re-register the pool
        with the physics world so the moved ones collide where they are."""
        ph = getattr(self.engine, "physics", None)
        if ph is None:
            return
        try:
            for n in self.col_pool:
                if ph.entry_for(n) is not None:
                    ph.remove_node(n)
                if n.visible:
                    ph.add_node(n)
        except Exception as ex:
            _warn_once("physc", f"tree colliders not added to physics: {ex}")


def _tropical_water(engine):
    glm = _m().glm
    for n in engine.scene.root.traverse():
        if getattr(n, "is_ocean", False) and getattr(n, "ocean_settings", None) is not None:
            o = n.ocean_settings
            try:
                o.water_shallow = glm.vec3(0.06, 0.62, 0.6)
                o.water_deep = glm.vec3(0.0, 0.17, 0.3)
            except Exception:
                pass


# =========================================================================== editor / runtime glue
def islands(engine):
    c = STATE.get("isl_cache")
    now = time.monotonic()
    if c is not None and c[0] is engine and now - c[1] < 0.5:
        return c[2]
    out = _scan_islands(engine)
    STATE["isl_cache"] = (engine, now, out)
    return out


def _scan_islands(engine):
    out = []
    for n in engine.scene.root.traverse():
        for bp in getattr(n, "blueprints", []) or []:
            if bp.__class__.__name__ == "TropicalIsland":
                out.append((n, bp))
    return out


def runtime_of(node):
    return getattr(node, "_isl_rt", None)


def ensure_island(engine, node, bp, force=False):
    rt = runtime_of(node)
    if rt is None or rt.bp is not bp:
        rt = IslandRuntime(engine, node, bp)
        node._isl_rt = rt
    key = rt.params_key(rt.gen_params())
    if force or rt.key != key or not rt.terrain_nodes or rt.terrain_nodes[0].parent is not node:
        try:
            rt.build()
        except Exception as ex:
            import traceback
            traceback.print_exc()
            H3D.warn(f"island '{node.name}' could not be built: {ex}")
    return rt


def _all_islands(engine, force=False):
    STATE["engine"] = engine
    STATE.pop("isl_cache", None)
    for n, bp in islands(engine):
        ensure_island(engine, n, bp, force=force)


def _blueprints():
    m = _m()
    Blueprint, blueprint = m.Blueprint, m.blueprint

    @blueprint
    class TropicalIsland(Blueprint):
        """A procedural tropical island.  Generation settings rebuild the island;
        view settings (distances, wind) apply instantly.
        topology: classic, volcano, mountains, hills, atoll, twin_peaks, plateau, archipelago, flat"""

        def __init__(self):
            super().__init__()
            for k, v in G.DEFAULTS.items():
                self.properties.setdefault(k, v)
            for k, v in VIEW_DEFAULTS.items():
                self.properties.setdefault(k, v)

    CLS["TropicalIsland"] = TropicalIsland


def _fix_samplers(engine):
    """New samplers default to texture unit 0; a sampler2DArray sharing a unit
    with the engine's sampler2Ds makes every draw invalid.  Give them their
    own units as soon as the programs exist (and again if they are rebuilt)."""
    m = _m()
    seen = STATE.setdefault("sampler_fixed", {})
    for name in ("geometry", "geometry_inst", "shadow"):
        try:
            prog = m.ShaderLib.get(engine.ctx, name)
        except Exception:
            prog = None
        if prog is None or seen.get(name) == id(prog):
            continue
        for u, unit in (("u_isl_splat", UNIT_SPLAT), ("u_isl_alb", UNIT_ALB), ("u_isl_nrm", UNIT_NRM),
                        ("u_isl_arm", UNIT_ARM), ("u_isl_alpha_tex", UNIT_ALPHA)):
            try:
                if u in prog:
                    prog[u].value = unit
            except Exception:
                pass
        seen[name] = id(prog)


def _on_update(engine, dt):
    STATE["engine"] = engine
    _fix_samplers(engine)
    STATE["time"] += dt
    cam = engine.camera.position
    STATE["cam"] = (cam.x, cam.y, cam.z)
    isls = islands(engine)
    if not isls:
        return
    # wind uniforms, once per frame on the programs that use them
    m = _m()
    try:
        wind = max(float(bp.properties.get("wind", VIEW_DEFAULTS["wind"])) for _n, bp in isls)
        for name in ("geometry", "shadow"):
            prog = m.ShaderLib.get(engine.ctx, name)
            if prog is not None:
                if "u_isl_time" in prog:
                    prog["u_isl_time"].value = STATE["time"]
                if "u_isl_wind" in prog:
                    prog["u_isl_wind"].value = (0.86, 0.5, wind, 0.0)
    except Exception:
        pass
    playing = engine._playing
    now = time.monotonic()
    for node, bp in isls:
        rt = runtime_of(node)
        if rt is None or rt.isl is None or not rt.terrain_nodes or rt.terrain_nodes[0].parent is not node:
            ensure_island(engine, node, bp)
            continue
        if not playing and engine.mode == "editor":
            key = rt.params_key(rt.gen_params())
            if key != rt.key:
                t = STATE["dirty"].get(id(node))
                if t is None:
                    STATE["dirty"][id(node)] = now
                elif now - t > 0.8:
                    STATE["dirty"].pop(id(node), None)
                    ensure_island(engine, node, bp)
                    continue
        rt.update_fades(min(dt, 0.5))
        rt.update_chunks(STATE["cam"], budget=0.008 if engine.mode == "editor" else 0.006)
        if playing:
            rt.ensure_ground()
            pl = _player(engine)
            if pl is not None:
                rt.update_colliders(pl.world_position(), dt)


def _player(engine):
    c = STATE.get("player_cache")
    now = time.monotonic()
    if c is not None and c[0] is engine and now - c[1] < 1.0 and c[2] is not None and c[2].parent is not None:
        return c[2]
    p = _scan_player(engine)
    STATE["player_cache"] = (engine, now, p)
    return p


def _scan_player(engine):
    try:
        for n in engine.scene.root.traverse():
            for bp in getattr(n, "blueprints", []) or []:
                if bp.__class__.__name__ in ("FPSController", "ThirdPersonController", "PlayerController"):
                    return n
    except Exception:
        pass
    return None


def _fit_light_frustum(original, self, scene, light_view):
    """With an island in the scene, fit the sun's shadow map around the camera
    (crisp shadows nearby) instead of around the whole multi-kilometre scene."""
    eng = STATE.get("engine")
    if eng is None or not islands(eng):
        return original(self, scene, light_view)
    glm = _m().glm
    cam = eng.camera.position
    r = max(40.0, max(float(bp.properties.get("shadow_distance", 160.0)) for _n, bp in islands(eng)))
    fwd = glm.vec3(0.0)
    try:
        f = eng.camera.front if hasattr(eng.camera, "front") else None
        if f is not None:
            fwd = glm.normalize(glm.vec3(f.x, 0.0, f.z)) * (r * 0.35)
    except Exception:
        pass
    c = light_view * glm.vec4(cam.x + fwd.x, cam.y, cam.z + fwd.z, 1.0)
    rr = r * 0.75
    near = max(0.1, -c.z - 600.0)
    far = -c.z + 600.0
    return glm.ortho(c.x - rr, c.x + rr, c.y - rr, c.y + rr, near, far)


# =========================================================================== tools
def _selected_island(engine):
    s = getattr(engine, "selected", None)
    while s is not None:
        for bp in getattr(s, "blueprints", []) or []:
            if bp.__class__.__name__ == "TropicalIsland":
                return s, bp
        s = s.parent
    li = islands(engine)
    return li[0] if li else (None, None)


def new_island(engine, params=None, name="Tropical Island", select=True, move_spawn=True):
    m = _m()
    glm = m.glm
    STATE["engine"] = engine
    node = m.SceneNode(engine._unique_node_name(name), parent=engine.scene.root)
    node.position = glm.vec3(0.0, 0.0, 0.0)
    engine.attach_blueprint(node, CLS["TropicalIsland"])
    STATE.pop("isl_cache", None)
    bp = node.blueprints[-1]
    bp.properties["seed"] = random.randrange(1, 1 << 30)
    bp.properties.update(params or {})
    rt = ensure_island(engine, node, bp, force=True)
    if move_spawn:
        place_spawn(engine, rt)
    if select:
        engine.select_node(node)
    # frame the island
    try:
        e = rt.isl.extent
        engine.camera.position = glm.vec3(0.0, e * 0.32, e * 0.55)
        engine.camera.yaw, engine.camera.pitch = 0.0, -30.0
    except Exception:
        pass
    return node


def place_spawn(engine, rt):
    sp = engine.find_player_spawn()
    if sp is None or rt is None or rt.isl is None:
        return
    glm = _m().glm
    wp = rt.node.world_position()
    x, y, z = rt.isl.spawn
    sp.position = glm.vec3(x + wp.x, rt.height_at(x + wp.x, z + wp.z) + 1.2, z + wp.z)
    try:                                              # look inland (towards the island centre)
        sp.rotation_euler.y = math.degrees(math.atan2(x, z))
    except Exception:
        pass


def tool_new_island(engine):
    node = new_island(engine)
    H3D.log(f"'{node.name}' created - change its shape, size and densities in the Inspector")


def tool_new_seed(engine):
    node, bp = _selected_island(engine)
    if node is None:
        return tool_new_island(engine)
    bp.properties["seed"] = random.randrange(1, 1 << 30)
    rt = ensure_island(engine, node, bp)
    place_spawn(engine, rt)


def tool_next_shape(engine):
    node, bp = _selected_island(engine)
    if node is None:
        return tool_new_island(engine)
    t = G.TOPOLOGIES
    cur = bp.properties.get("topology", "classic")
    bp.properties["topology"] = t[(t.index(cur) + 1) % len(t)] if cur in t else t[0]
    rt = ensure_island(engine, node, bp)
    place_spawn(engine, rt)
    H3D.log(f"'{node.name}' is now a {bp.properties['topology']} island")


def tool_spawn_to_beach(engine):
    node, bp = _selected_island(engine)
    if node is not None:
        place_spawn(engine, runtime_of(node))


def register(h3d):
    global H3D, G
    H3D = h3d
    G = h3d.load("island_gen.py")
    _patch_shaders()
    _mesh_classes()
    _blueprints()
    m = h3d.module
    h3d.on("update", _on_update)
    h3d.on("init", lambda e: (_fix_samplers(e), _all_islands(e)))
    h3d.on("project_loaded", lambda e, p: _all_islands(e, force=True))
    h3d.on("play_stop", lambda e: [runtime_of(n).drop_colliders() for n, _b in _scan_islands(e) if runtime_of(n)])
    try:
        h3d.wrap(m.Renderer, "_fit_light_frustum", _fit_light_frustum)
    except Exception as ex:
        h3d.warn(f"island shadow fitting not installed: {ex}")
    h3d.add_tool("Islands: new tropical island", tool_new_island, "#1E9E8A")
    h3d.add_tool("Islands: new seed (selected island)", tool_new_seed)
    h3d.add_tool("Islands: next shape (selected island)", tool_next_shape)
    h3d.add_tool("Islands: move player spawn to the beach", tool_spawn_to_beach)


__all__ = ["new_island", "islands", "runtime_of", "ensure_island", "place_spawn"]
