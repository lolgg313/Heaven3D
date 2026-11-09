# @heaven3d-part rendering/shaders.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# #############################################################################
# SECTION 9  —  SHADER LIBRARY  (GLSL sources, compiled + cached)
# #############################################################################

# ---- Shadow-map depth shader (geometry pass from sun's POV) ----
SHADOW_VERT = """#version 330 core
layout (location = 0) in vec3 in_position;
uniform mat4 u_model;
uniform mat4 u_light_space;
void main(){
    gl_Position = u_light_space * u_model * vec4(in_position, 1.0);
}
"""
SHADOW_FRAG = """#version 330 core
void main(){}
"""

# ---- Geometry pass (writes G-buffer via MRT) ----
GEOMETRY_VERT = """#version 330 core
layout (location = 0) in vec3 in_position;
layout (location = 1) in vec3 in_normal;
layout (location = 2) in vec4 in_tangent;
layout (location = 3) in vec2 in_uv;
uniform mat4 u_model;
uniform mat4 u_view;
uniform mat4 u_proj;
out vec3 v_world_pos;
out vec3 v_normal;
out vec4 v_tangent;
out vec2 v_uv;
void main(){
    vec4 wp = u_model * vec4(in_position, 1.0);
    v_world_pos = wp.xyz;
    // Correct normal transform for non-uniform scale (inverse-transpose).
    v_normal = transpose(inverse(mat3(u_model))) * in_normal;
    // Tangents transform fine with the plain model matrix (they lie in the
    // surface plane); handedness is preserved in w.
    v_tangent = vec4(mat3(u_model) * in_tangent.xyz, in_tangent.w);
    v_uv = in_uv;
    gl_Position = u_proj * u_view * wp;
}
"""
GEOMETRY_FRAG = """#version 330 core
in vec3 v_world_pos;
in vec3 v_normal;
in vec4 v_tangent;
in vec2 v_uv;

uniform vec4  u_base_color;
uniform float u_metallic;
uniform float u_roughness;
uniform vec3  u_emissive;
uniform float u_emissive_intensity;
uniform sampler2D u_albedo_tex;
uniform sampler2D u_mr_tex;
uniform sampler2D u_normal_tex;
uniform sampler2D u_emissive_tex;
uniform sampler2D u_ao_tex;
uniform int u_has_albedo;
uniform int u_has_mr;
uniform int u_has_normal;
uniform int u_has_emissive;
uniform int u_has_ao;
// Transparency support (glTF alphaMode):
//   0 = OPAQUE  (ignore alpha)
//   1 = MASK    (discard fragments below u_alpha_cutoff — cut-out geometry
//                like leaves/fences renders correctly in the deferred pipeline)
//   2 = BLEND   (never reaches this pass — BLEND meshes are skipped by the
//                renderer and drawn in the forward transparency pass AFTER
//                lighting, where real per-pixel alpha blending happens)
uniform int   u_alpha_mode;
uniform float u_alpha_cutoff;
// glTF doubleSided: foliage/cloth cards are lit from BOTH faces.  Without
// this, backface fragments keep vertex normals that point AWAY from the
// camera and the sun, so they shade almost pure black (the classic "black
// ivy in Sponza" artefact).  When set, backfaces flip the shading normal.
uniform int   u_double_sided;

layout (location = 0) out vec4 g_position;   // xyz = world pos, w = 1 if geometry
layout (location = 1) out vec4 g_normal;     // xyz = world normal
layout (location = 2) out vec4 g_albedo;     // rgb = albedo, a = metallic
layout (location = 3) out vec4 g_material;   // r = roughness, g = emissive len (debug), b = surface alpha, a = AO
// Emissive rides its own attachment so the COLOUR survives: the old single
// float in g_material.g forced the lighting pass to reuse albedo as the
// glow colour, which made "red emissive on grey metal" glow grey.
layout (location = 4) out vec4 g_emissive;   // rgb = emissive (HDR, intensity folded in)

void main(){
    // ---- albedo + alpha (alpha needed for cut-out / blended materials) ----
    vec4 albedo_rgba = u_base_color;
    if (u_has_albedo == 1)
        albedo_rgba *= texture(u_albedo_tex, v_uv);
    vec3 albedo = albedo_rgba.rgb;
    float surface_alpha = albedo_rgba.a;

    // Alpha test for cut-out materials (MASK: leaves, fences, grates).
    // BLEND is handled outside the deferred path (forward transparency
    // pass), so only MASK discards here.
    if (u_alpha_mode == 1){
        if (surface_alpha < u_alpha_cutoff)
            discard;
    } else if (u_alpha_mode == 2){
        // Safety net: a BLEND material should never reach the deferred pass
        // (the renderer routes it to the forward pass).  If one slips
        // through, keep the old soft cut-out rather than rendering opaque.
        if (surface_alpha < u_alpha_cutoff * 0.5)
            discard;
    }

    albedo = pow(albedo, vec3(2.2));   // sRGB → linear

    float metallic = u_metallic;
    float roughness = u_roughness;
    if (u_has_mr == 1){
        vec4 mr = texture(u_mr_tex, v_uv);
        metallic *= mr.b;
        roughness *= mr.g;
    }
    metallic  = clamp(metallic, 0.0, 1.0);
    roughness = clamp(roughness, 0.045, 1.0);

    // ---- normal mapping with a REAL TBN frame (per-vertex tangents) ----
    vec3 N = normalize(v_normal);
    if (u_has_normal == 1){
        vec3 tn = texture(u_normal_tex, v_uv).xyz * 2.0 - 1.0;
        vec3 T = normalize(v_tangent.xyz);
        // Gram-Schmidt: keep T exactly perpendicular to the (interpolated) N
        T = normalize(T - N * dot(N, T));
        vec3 B = cross(N, T) * v_tangent.w;
        N = normalize(T * tn.x + B * tn.y + N * tn.z);
    }

    // Two-sided lighting (glTF doubleSided): flip the shading normal on
    // backfaces so leaf cards/fences light correctly from both sides.
    if (u_double_sided == 1 && !gl_FrontFacing)
        N = -N;

    // ---- baked AO (glTF occlusionTexture, R channel) ----
    float ao = 1.0;
    if (u_has_ao == 1)
        ao = texture(u_ao_tex, v_uv).r;

    vec3 emis = u_emissive * u_emissive_intensity;
    if (u_has_emissive == 1)
        emis *= texture(u_emissive_tex, v_uv).rgb;

    g_position = vec4(v_world_pos, 1.0);
    // g_normal.w carries the doubleSided (foliage) flag for the lighting
    // pass — it was previously always 1.0 and nothing consumed it.
    g_normal   = vec4(N, u_double_sided == 1 ? 1.0 : 0.0);
    g_albedo   = vec4(albedo, metallic);
    // Surface alpha in g_material.b is informational (BLEND uses the forward
    // pass now); baked AO rides in .a.
    g_material = vec4(roughness, length(emis), surface_alpha, ao);
    g_emissive = vec4(emis, 1.0);
}
"""

# ---- Forward transparency pass (BLEND materials) -------------------------
# Deferred rendering can't alpha-blend (one surface per pixel), so glTF
# BLEND materials skip the geometry pass entirely and are drawn HERE —
# after the lighting pass, back-to-front, with hardware blending into the
# HDR scene buffer.  Shading mirrors the deferred look (sun lambert +
# roughness spec + IBL ambient + emissive) so glass/tinted surfaces sit
# coherently next to opaque PBR geometry.
FORWARD_VERT = """#version 330 core
layout (location = 0) in vec3 in_position;
layout (location = 1) in vec3 in_normal;
layout (location = 2) in vec4 in_tangent;
layout (location = 3) in vec2 in_uv;
uniform mat4 u_model;
uniform mat4 u_view;
uniform mat4 u_proj;
out vec3 v_world_pos;
out vec3 v_normal;
out vec4 v_tangent;
out vec2 v_uv;
void main(){
    vec4 wp = u_model * vec4(in_position, 1.0);
    v_world_pos = wp.xyz;
    v_normal = transpose(inverse(mat3(u_model))) * in_normal;
    v_tangent = vec4(mat3(u_model) * in_tangent.xyz, in_tangent.w);
    v_uv = in_uv;
    gl_Position = u_proj * u_view * wp;
}
"""
FORWARD_FRAG = """#version 330 core
in vec3 v_world_pos;
in vec3 v_normal;
in vec4 v_tangent;
in vec2 v_uv;

uniform vec4  u_base_color;
uniform float u_metallic;
uniform float u_roughness;
uniform vec3  u_emissive;
uniform float u_emissive_intensity;
uniform sampler2D u_albedo_tex;
uniform sampler2D u_mr_tex;
uniform sampler2D u_normal_tex;
uniform sampler2D u_emissive_tex;
uniform int u_has_albedo;
uniform int u_has_mr;
uniform int u_has_normal;
uniform int u_has_emissive;
uniform float u_alpha_cutoff;    // near-zero alpha is still discarded
uniform int   u_double_sided;

uniform vec3  u_sun_dir;
uniform vec3  u_sun_color;
uniform float u_sun_intensity;
uniform vec3  u_ambient_color;
uniform float u_ambient_intensity;
uniform float u_ibl_intensity;
uniform sampler2D u_env;
uniform vec3  u_camera_pos;

out vec4 frag_color;

const float PI = 3.14159265359;
vec2 dir_to_equirect(vec3 d){
    float u = atan(d.z, d.x) / (2.0 * PI) + 0.5;
    float v = asin(clamp(d.y, -1.0, 1.0)) / PI + 0.5;
    return vec2(u, v);
}

void main(){
    vec4 rgba = u_base_color;
    if (u_has_albedo == 1)
        rgba *= texture(u_albedo_tex, v_uv);
    float alpha = clamp(rgba.a, 0.0, 1.0);
    // Fully-invisible texels (window glass texture holes) still punch out.
    if (alpha <= max(0.003, u_alpha_cutoff * 0.01))
        discard;
    vec3 albedo = pow(rgba.rgb, vec3(2.2));

    float metallic  = clamp(u_metallic, 0.0, 1.0);
    float roughness = u_roughness;
    if (u_has_mr == 1){
        vec4 mr = texture(u_mr_tex, v_uv);
        metallic  *= mr.b;
        roughness *= mr.g;
    }
    metallic  = clamp(metallic, 0.0, 1.0);
    roughness = clamp(roughness, 0.045, 1.0);

    vec3 N = normalize(v_normal);
    if (u_has_normal == 1){
        vec3 tn = texture(u_normal_tex, v_uv).xyz * 2.0 - 1.0;
        vec3 T = normalize(v_tangent.xyz);
        T = normalize(T - N * dot(N, T));
        vec3 B = cross(N, T) * v_tangent.w;
        N = normalize(T * tn.x + B * tn.y + N * tn.z);
    }
    if (u_double_sided == 1 && !gl_FrontFacing)
        N = -N;

    vec3 V = normalize(u_camera_pos - v_world_pos);
    float ndv = max(dot(N, V), 0.0);
    vec3 F0 = mix(vec3(0.04), albedo, metallic);

    // ---- Sun: lambert diffuse + roughness-driven Blinn specular --------
    vec3 L = normalize(-u_sun_dir);
    float ndl = max(dot(N, L), 0.0);
    vec3 H = normalize(L + V);
    float ndh = max(dot(N, H), 0.0);
    float spec_pow = mix(8.0, 1024.0, pow(1.0 - roughness, 3.0));
    vec3 sun = (albedo * (1.0 - metallic) + F0 * pow(ndh, spec_pow))
             * ndl * u_sun_color * u_sun_intensity;

    // ---- IBL ambient (same env probes as the deferred pass) -----------
    vec3 irradiance = textureLod(u_env, dir_to_equirect(N), 5.0).rgb;
    vec3 R = reflect(-V, N);
    vec3 prefiltered = textureLod(u_env, dir_to_equirect(R),
                                  roughness * 4.0).rgb;
    vec3 F = F0 + (max(vec3(1.0 - roughness), F0) - F0)
                * pow(1.0 - ndv, 5.0);
    vec3 ibl = ((vec3(1.0) - F) * (1.0 - metallic) * irradiance * albedo
              + prefiltered * F) * u_ibl_intensity;
    vec3 fill = u_ambient_color * u_ambient_intensity * albedo * 0.25;

    // ---- Emissive (full colour, like the deferred path) ---------------
    vec3 emis = u_emissive * u_emissive_intensity;
    if (u_has_emissive == 1)
        emis *= texture(u_emissive_tex, v_uv).rgb;

    frag_color = vec4(sun + ibl + fill + emis, alpha);
}
"""

# ---- Lighting pass (fullscreen quad samples the G-buffer) ----
FULLSCREEN_VERT = """#version 330 core
out vec2 v_uv;
void main(){
    vec2 p = vec2((gl_VertexID == 1) ? 3.0 : -1.0,
                  (gl_VertexID == 2) ? 3.0 : -1.0);
    gl_Position = vec4(p, 0.0, 1.0);
    v_uv = p * 0.5 + 0.5;
}
"""
CLOUD_GLSL = """
// ---------------------------------------------------------------- clouds
// Procedural cumulus deck, shared by the sky (lighting
// pass) and the ocean's reflections, so the water mirrors the same clouds.
// One analytic ray/plane hit (no ray march): a domain-warped FBM
// shape with detail erosion, lit with a Beer-Lambert optical depth toward
// the sun, a powder term, a two-lobe Henyey-Greenstein phase
// (silver lining + forward glow) and aerial perspective toward the horizon.
// Needs from the including shader: PI, u_env (equirect, mipmapped),
// u_sun_dir / u_sun_color / u_sun_intensity and u_time.
uniform int   u_clouds_enabled;
uniform float u_cloud_coverage;   // 0..1 - fraction of sky filled
uniform float u_cloud_density;    // opacity + belly darkening
uniform float u_cloud_scale;      // noise scale (larger = bigger masses)
uniform float u_cloud_altitude;   // world height of the cloud plane
uniform float u_cloud_speed;      // wind drift (UV units / second)
uniform vec3  u_cloud_tint;       // base colour multiplier
uniform vec2  u_cloud_wind;       // wind direction on XZ

float cloud_hash(vec2 p){
    p = fract(p * vec2(234.34, 435.345));
    p += dot(p, p + 34.23);
    return fract(p.x * p.y);
}
float cloud_noise(vec2 p){
    vec2 i = floor(p);
    vec2 f = fract(p);
    vec2 u = f * f * f * (f * (f * 6.0 - 15.0) + 10.0);   // quintic: no grid creases
    float a = cloud_hash(i);
    float b = cloud_hash(i + vec2(1.0, 0.0));
    float c = cloud_hash(i + vec2(0.0, 1.0));
    float d = cloud_hash(i + vec2(1.0, 1.0));
    return mix(mix(a, b, u.x), mix(c, d, u.x), u.y);
}
float cloud_fbm(vec2 p, int oct){
    float v = 0.0;
    float a = 0.5;
    float n = 0.0;
    mat2 rot = mat2(0.8, 0.6, -0.6, 0.8);   // rotate+scale kills grid alignment
    for (int i = 0; i < 6; ++i){
        if (i >= oct) break;
        v += a * cloud_noise(p);
        n += a;
        p = rot * p * 2.03 + vec2(11.5);
        a *= 0.5;
    }
    return v / max(n, 1e-4);
}
vec2 cloud_warp(vec2 p){
    return p + (vec2(cloud_noise(p * 0.37 + vec2(3.1, 7.7)),
                     cloud_noise(p * 0.37 + vec2(8.3, 1.9))) - 0.5) * 1.1;
}
// coverage threshold: 0 = clear sky, 1 = overcast
float cloud_threshold(){ return mix(0.80, 0.30, clamp(u_cloud_coverage, 0.0, 1.0)); }
// Cumulus density at (warped) deck coordinates.  detail = extra erosion octaves.
float cloud_density(vec2 w, int detail){
    float c = cloud_fbm(w, 4) - cloud_threshold();
    if (c <= -0.06) return 0.0;                    // clear sky: skip the detail
    if (detail > 0){
        float det = cloud_fbm(w * 4.3 + vec2(17.0, 5.0), detail);
        c -= (1.0 - det) * 0.16 * (1.0 - smoothstep(0.0, 0.22, c));
    }
    return clamp(c * (3.2 + u_cloud_density * 2.4), 0.0, 1.0);
}
// cheap version for the light taps (shape only)
float cloud_density_lo(vec2 w){
    return clamp((cloud_fbm(w, 3) - cloud_threshold()) * (3.2 + u_cloud_density * 2.4), 0.0, 1.0);
}
float cloud_hg(float c, float g){
    float g2 = g * g;
    return (1.0 - g2) / (4.0 * PI * pow(max(1.0 + g2 - 2.0 * g * c, 1e-4), 1.5));
}
vec2 cloud_eq(vec3 d){
    return vec2(atan(d.z, d.x) / (2.0 * PI) + 0.5, asin(clamp(d.y, -1.0, 1.0)) / PI + 0.5);
}
// rgb = cloud colour, a = coverage, for a ray from ro along rd.
// refl = true: the cheaper version used for the ocean's reflections (no
// erosion detail, no light taps) - waves blur it anyway.
vec4 cloud_layer(vec3 ro, vec3 rd, bool refl){
    if (u_clouds_enabled != 1 || rd.y <= 0.012) return vec4(0.0);
    float t = (u_cloud_altitude - ro.y) / rd.y;
    if (t <= 0.0) return vec4(0.0);
    t = min(t, 60000.0);
    float sc = 0.0012 * u_cloud_scale;
    vec2 w = cloud_warp((ro.xz + rd.xz * t) * sc + u_cloud_wind * (u_time * u_cloud_speed));
    float den;
    if (refl)
        den = clamp((cloud_fbm(w, 3) - cloud_threshold()) * (3.2 + u_cloud_density * 2.4), 0.0, 1.0);
    else
        den = cloud_density(w, 2);
    den *= smoothstep(0.012, 0.13, rd.y);
    if (den <= 0.003) return vec4(0.0);

    vec3 to_sun = normalize(-u_sun_dir);
    float mu = dot(rd, to_sun);
    float day = smoothstep(-0.10, 0.30, to_sun.y);
    vec3 amb_top = textureLod(u_env, cloud_eq(vec3(0.0, 1.0, 0.0)), 3.0).rgb;
    vec3 amb_hor = refl ? amb_top * 1.1
                        : textureLod(u_env, cloud_eq(normalize(vec3(rd.x, 0.06, rd.z))), 3.0).rgb;
    vec3 sun_rad = u_sun_color * u_sun_intensity;
    // optical depth toward the sun: one shape tap sunward (sky only)
    float od = den * 0.35;
    if (!refl){
        vec2 sd = normalize(to_sun.xz + vec2(1e-4)) * (0.06 + 0.08 * (1.0 - abs(to_sun.y)));
        od += cloud_density_lo(w + sd);
    } else {
        od += den * 0.6;
    }
    float T = exp(-od * (0.8 + 1.2 * u_cloud_density));
    float powder = 1.0 - exp(-den * 3.5);
    float phase = mix(cloud_hg(mu, 0.6), cloud_hg(mu, -0.25), 0.4) * 4.0 * PI;   // ~1 on average
    vec3 amb = mix(amb_hor, amb_top, 0.6) * (1.30 - 0.38 * den * u_cloud_density)
             + vec3(0.02, 0.02, 0.035);
    vec3 col = u_cloud_tint * (amb + sun_rad * 0.42 * T * mix(1.0, powder, 0.35) * phase * day);
    col += sun_rad * 0.20 * pow(max(mu, 0.0), 10.0) * (1.0 - den) * day;      // silver lining
    float fog = 1.0 - exp(-t * 0.00003);                                       // aerial perspective
    col = mix(col, amb_hor * 1.08, fog * 0.75);
    return vec4(col, clamp(den * (0.82 + 0.18 * u_cloud_density), 0.0, 0.98) * (1.0 - fog * 0.5));
}
"""


LIGHTING_FRAG = """#version 330 core
in vec2 v_uv;
out vec4 frag_color;

uniform sampler2D g_position;
uniform sampler2D g_normal;
uniform sampler2D g_albedo;
uniform sampler2D g_material;
uniform sampler2D g_emissive;   // rgb = HDR emissive colour (own attachment)
uniform sampler2DShadow u_shadow_map;   // hardware-compare depth map (PCF)
uniform sampler2D u_ssao;        // screen-space AO (white when disabled)
uniform sampler2D u_env;         // equirect environment (procedural sky, mipmapped)
uniform int   u_use_shadow;      // 0 → skip shadow sampling entirely

uniform vec3  u_sun_dir;
uniform vec3  u_sun_color;
uniform float u_sun_intensity;
uniform vec3  u_ambient_color;
uniform float u_ambient_intensity;
uniform float u_ibl_intensity;
uniform float u_ao_direct;     // how much AO also dampens DIRECT light (0..1)
uniform vec3  u_camera_pos;
uniform mat4  u_light_space;
uniform mat4  u_inv_proj;        // for world-ray reconstruction (sky + IBL)
uniform mat4  u_inv_view;

// Unity-style local lights, evaluated in the deferred lighting pass.
#define MAX_POINT_LIGHTS 16
#define MAX_SPOT_LIGHTS  16
uniform int   u_point_count;
uniform vec3  u_point_pos[MAX_POINT_LIGHTS];
uniform vec3  u_point_color[MAX_POINT_LIGHTS];
uniform float u_point_intensity[MAX_POINT_LIGHTS];
uniform float u_point_range[MAX_POINT_LIGHTS];
uniform int   u_spot_count;
uniform vec3  u_spot_pos[MAX_SPOT_LIGHTS];
uniform vec3  u_spot_dir[MAX_SPOT_LIGHTS];
uniform vec3  u_spot_color[MAX_SPOT_LIGHTS];
uniform float u_spot_intensity[MAX_SPOT_LIGHTS];
uniform float u_spot_range[MAX_SPOT_LIGHTS];
uniform float u_spot_inner_cos[MAX_SPOT_LIGHTS];
uniform float u_spot_outer_cos[MAX_SPOT_LIGHTS];
// ---- Clustered deferred lighting (16x16 px tiles x 16 depth slices) ----
// # [TRIPLEA:cluster_lighting_uniforms]
// u_cluster_mask is an R32UI texture; texel (tile.x, tile.y * 16 + slice)
// holds a 32-bit light bitmask (bits 0..15 = point lights, 16..31 = spots).
uniform mat4  u_view;               // camera view matrix (view-space depth)
uniform float u_near;               // camera near plane
uniform float u_far;                // camera far plane
uniform int   u_clustered_enabled;  // 0 → original full light loop
uniform ivec2 u_cluster_dims;       // tile counts (x, y), 16 px per tile
uniform usampler2D u_cluster_mask;

const float PI = 3.14159265359;

// ---------------------------------------------------------------- env map
vec2 dir_to_equirect(vec3 d){
    float u = atan(d.z, d.x) / (2.0 * PI) + 0.5;
    float v = asin(clamp(d.y, -1.0, 1.0)) / PI + 0.5;
    return vec2(u, v);
}
vec3 world_dir_from_uv(vec2 uv){
    vec4 ndc  = vec4(uv * 2.0 - 1.0, 1.0, 1.0);
    vec4 vpos = u_inv_proj * ndc;
    vpos /= vpos.w;
    vec3 wpos = (u_inv_view * vec4(vpos.xyz, 1.0)).xyz;
    return normalize(wpos - u_camera_pos);
}

// ---------------------------------------------------------------- shadow
// 16-tap Poisson PCF with interleaved-gradient-noise rotation (kills banding),
// slope-scaled bias and a normal-offset — the EEVEE soft-shadow look.
const vec2 POISSON[16] = vec2[](
    vec2(-0.94201624, -0.39906216), vec2( 0.94558609, -0.76890725),
    vec2(-0.09418410, -0.92938870), vec2( 0.34495938,  0.29387760),
    vec2(-0.91588581,  0.45771432), vec2(-0.81544232, -0.87912464),
    vec2(-0.38277543,  0.27676845), vec2( 0.97484398,  0.75648379),
    vec2( 0.44323325, -0.97511554), vec2( 0.53742981, -0.47373420),
    vec2(-0.26496911, -0.41893023), vec2( 0.79197514,  0.19090188),
    vec2(-0.24188840,  0.99706507), vec2(-0.81409955,  0.91437590),
    vec2( 0.19984126,  0.78641367), vec2( 0.14383161, -0.14100790)
);
float shadow_calc(vec3 world_pos, vec3 N, vec3 L, float foliage){
    if (u_use_shadow == 0) return 0.0;
    vec2 sm_size = vec2(textureSize(u_shadow_map, 0));
    vec2 texel = 1.0 / sm_size;
    // Biases in WORLD units, scaled by the real size of one shadow texel
    // (read off the orthographic light matrix: row 0 = 2/width, row 2 =
    // 2/depth-range).  A fixed NDC bias meant ~0.7 m on islands (detached,
    // floating shadows) and too little on small scenes (acne stripes).
    vec3 m_rx = vec3(u_light_space[0][0], u_light_space[1][0], u_light_space[2][0]);
    vec3 m_rz = vec3(u_light_space[0][2], u_light_space[1][2], u_light_space[2][2]);
    float texel_ws = 2.0 / max(length(m_rx) * sm_size.x, 1e-6);
    float depth_per_m = 0.5 * length(m_rz);
    // Normal-offset toward the light side of the surface (double-sided
    // foliage may face away), largest at grazing angles where acne lives.
    vec3 Nl = dot(N, L) < 0.0 ? -N : N;
    float cos_t = clamp(dot(Nl, L), 0.0, 1.0);
    float sin_t = sqrt(max(0.0, 1.0 - cos_t * cos_t));
    float tan_t = min(sin_t / max(cos_t, 0.05), 5.0);
    vec3 p = world_pos + Nl * (texel_ws * (0.35 + 1.4 * sin_t));
    vec4 ls = u_light_space * vec4(p, 1.0);
    vec3 proj = ls.xyz / ls.w;
    proj = proj * 0.5 + 0.5;
    if (proj.z > 1.0 || proj.x < 0.0 || proj.x > 1.0 || proj.y < 0.0 || proj.y > 1.0)
        return 0.0;
    // Fade out over the last 6% of the shadow area: no hard line where the
    // shadow map ends (it follows the camera on big terrains).
    vec2 edge = abs(proj.xy * 2.0 - 1.0);
    float fade = 1.0 - smoothstep(0.88, 1.0, max(edge.x, edge.y));
    float current = proj.z;
    // Slope-scaled depth bias covering the PCF footprint.  Foliage cards are
    // treated as thick (leaves don't self-shadow into black clumps).
    float bias_m = texel_ws * (0.6 + 1.1 * tan_t);
    if (foliage > 0.5) bias_m = max(bias_m, 0.45);
    float bias = bias_m * depth_per_m;
    // Interleaved gradient noise rotation per pixel
    float ign = fract(52.9829189 * fract(dot(gl_FragCoord.xy, vec2(0.06711056, 0.00583715))));
    float ang = ign * 6.2831853;
    float ca = cos(ang), sa = sin(ang);
    mat2 rot = mat2(ca, -sa, sa, ca);
    float shadow = 0.0;
    float radius = 1.6;
    // sampler2DShadow: each tap is a HARDWARE-filtered depth comparison
    // (bilinear PCF) — 16 rotated taps × 4 hw-taps = effectively 64 samples.
    for (int i = 0; i < 16; ++i){
        vec2 off = rot * POISSON[i] * texel * radius;
        float lit = texture(u_shadow_map, vec3(proj.xy + off, current - bias));
        shadow += 1.0 - lit;
    }
    return shadow / 16.0 * fade;
}

// ---------------------------------------------------------------- PBR
vec3 fresnel_schlick(float cos_theta, vec3 F0){
    return F0 + (1.0 - F0) * pow(clamp(1.0 - cos_theta, 0.0, 1.0), 5.0);
}
vec3 fresnel_schlick_roughness(float cos_theta, vec3 F0, float rough){
    // IBL fresnel: reduce reflectivity at high roughness (Sebastien Lagarde)
    return F0 + (max(vec3(1.0 - rough), F0) - F0)
              * pow(clamp(1.0 - cos_theta, 0.0, 1.0), 5.0);
}
float dist_ggx(vec3 N, vec3 H, float rough){
    float a = rough * rough;
    float a2 = a * a;
    float ndh = max(dot(N, H), 0.0);
    float d = (ndh * ndh * (a2 - 1.0) + 1.0);
    return a2 / max(PI * d * d, 0.0001);
}
float geom_smith_ggx(float ndv, float rough){
    float r = rough + 1.0;
    float k = (r * r) / 8.0;
    return ndv / (ndv * (1.0 - k) + k);
}
float geometry_smith(vec3 N, vec3 V, vec3 L, float rough){
    return geom_smith_ggx(max(dot(N,V),0.0), rough)
         * geom_smith_ggx(max(dot(N,L),0.0), rough);
}

// One direct-light GGX evaluation shared by the Sun and all local lights.
// The foliage flag keeps the existing two-sided translucency behaviour for
// cut-out leaves without changing ordinary opaque PBR shading.
vec3 direct_light_contrib(vec3 N, vec3 V, vec3 L, vec3 radiance,
                          vec3 albedo, float metallic, float roughness,
                          float foliage){
    float ndl = max(dot(N, L), 0.0);
    if (foliage > 0.5)
        ndl = clamp((dot(N, L) + 0.55) / 1.55, 0.0, 1.0);
    if (ndl <= 0.0)
        return vec3(0.0);
    vec3 H = normalize(V + L);
    vec3 F0 = mix(vec3(0.04), albedo, metallic);
    float ndv = max(dot(N, V), 0.0);
    float NDF = dist_ggx(N, H, roughness);
    float G   = geometry_smith(N, V, L, roughness);
    vec3  F   = fresnel_schlick(max(dot(H, V), 0.0), F0);
    vec3 spec = (NDF * G * F) / max(4.0 * ndv * ndl + 0.001, 0.001);
    vec3 kS = F;
    vec3 kD = (vec3(1.0) - kS) * (1.0 - metallic);
    return (kD * albedo / PI + spec) * radiance * ndl;
}

// Unity-like smooth range falloff: inverse-square core plus a fourth-power
// fade that reaches exactly zero at the light's Range value.
float local_light_attenuation(float distance_to_light, float range){
    float d = max(distance_to_light, 0.0001);
    float t = clamp(1.0 - pow(d / max(range, 0.001), 4.0), 0.0, 1.0);
    return (t * t) / (d * d + 1.0);
}

uniform float u_time;             // seconds - drives the cloud drift
""" + CLOUD_GLSL + """
void main(){
    vec4 gp = texture(g_position, v_uv);
    vec3 world_pos = gp.xyz;
    vec3 ray_dir = world_dir_from_uv(v_uv);

    // ---- background: procedural sky with realistic sun disc + clouds ---
    if (gp.w < 0.5){
        vec3 env = textureLod(u_env, dir_to_equirect(ray_dir), 0.0).rgb;
        // Direction TO the sun (opposite of light travel direction)
        vec3 to_sun = normalize(-u_sun_dir);
        float sun_cos = dot(ray_dir, to_sun);
        float sun_elev = to_sun.y;   // elevation above horizon
        // Sun disc (~0.5 degree angular diameter)
        float sun_disc = smoothstep(0.9993, 0.99998, sun_cos);
        // Corona / atmospheric glow
        float corona = pow(max(sun_cos, 0.0), 256.0) * 0.8;
        float glow   = pow(max(sun_cos, 0.0), 32.0)  * 0.25;
        // Sunset / sunrise horizon tint when sun is near or below horizon
        float below_horizon = max(0.0, -sun_elev);
        float horizon_glow = pow(max(sun_cos, 0.0), 3.0) * below_horizon * 0.5;
        // Visibility fade when sun drops below horizon
        float sun_vis = smoothstep(-0.05, 0.05, sun_elev);

        // ---- procedural clouds (default scene feature, see CLOUD_GLSL) ----
        vec4 cl = cloud_layer(u_camera_pos, ray_dir, false);
        float cloud_alpha = cl.a;
        vec3 cloud_rgb = cl.rgb;

        // Sun, partially occluded by whatever cloud covers this pixel.
        float sun_block = 1.0 - cloud_alpha * 0.92;
        vec3 sun_radiance = u_sun_color * u_sun_intensity;
        env += sun_radiance * (sun_disc * 5.0 + corona + glow) * sun_vis * sun_block;
        env += vec3(1.0, 0.35, 0.12) * horizon_glow * u_sun_intensity * 0.4 * sun_block;
        // Clouds composite over sky + (occluded) sun.
        env = mix(env, cloud_rgb, cloud_alpha);
        frag_color = vec4(env, 1.0);
        return;
    }

    vec4 g_norm = texture(g_normal, v_uv);
    vec3 N = normalize(g_norm.xyz);
    // glTF doubleSided flag (1.0 = cut-out foliage/cloth) — see geometry pass.
    float foliage = g_norm.w;
    vec4 alb = texture(g_albedo, v_uv);
    vec3 albedo = alb.rgb;
    float metallic = alb.a;
    vec4 mat_g = texture(g_material, v_uv);
    float roughness = mat_g.r;
    // Full-colour HDR emissive from its own G-buffer attachment (was:
    // magnitude-only in mat_g.g, wrongly tinted by albedo downstream).
    vec3 emis = texture(g_emissive, v_uv).rgb;
    float baked_ao = mat_g.a;

    vec3 V = normalize(u_camera_pos - world_pos);
    vec3 F0 = mix(vec3(0.04), albedo, metallic);
    float ndv = max(dot(N, V), 0.0);

    // Combined occlusion: screen-space AO × baked AO map.
    float ao = texture(u_ssao, v_uv).r * baked_ao;

    // ---- Sun (key light) -------------------------------------------
    vec3 L = normalize(-u_sun_dir);
    float ndl = max(dot(N, L), 0.0);
    if (foliage > 0.5)
        ndl = clamp((dot(N, L) + 0.55) / 1.55, 0.0, 1.0);
    float sh = (ndl > 0.0) ? shadow_calc(world_pos, N, L, foliage) : 0.0;
    // Most engines (Unity/Godot sliders, UE's AO-on-direct option) partially
    // apply AO to direct light too — it grounds objects instead of letting
    // contact zones stay fully sunlit.  u_ao_direct ~0.35 is the sweet spot.
    float ao_direct = mix(1.0, ao, u_ao_direct);
    // Overcast coupling: heavy cloud coverage scatters direct sunlight, so
    // the ground dims coherently with the sky (EEVEE does the same when you
    // crank a volumetric cloud layer's density).
    float overcast = (u_clouds_enabled == 1)
        ? clamp(u_cloud_coverage * u_cloud_density, 0.0, 1.0) : 0.0;
    vec3 sun_contrib = direct_light_contrib(
        N, V, L, u_sun_color * u_sun_intensity,
        albedo, metallic, roughness, foliage)
        * (1.0 - sh) * ao_direct * (1.0 - overcast * 0.45);

    // ---- Point lights --------------------------------------------------
    // # [TRIPLEA:cluster_lighting_point_loop]
    // Local lights are not shadow-casting yet (Unity's per-light shadows
    // require extra shadow maps); they still use the same GGX BRDF, smooth
    // Range falloff, colour and intensity controls as Unity.
    vec3 local_contrib = vec3(0.0);
    // Clustered lookup: the 32-bit light bitmask of this fragment's
    // (tile, depth-slice).  With clustering disabled the mask stays
    // all-ones and the loops below behave exactly like the originals.
    uint cl_mask = 0xFFFFFFFFu;
    if (u_clustered_enabled == 1){
        ivec2 cl_tile = clamp(ivec2(gl_FragCoord.xy / 16.0),
                              ivec2(0), u_cluster_dims - ivec2(1));
        float cl_depth = -(u_view * vec4(world_pos, 1.0)).z;
        float cl_t = log(max(cl_depth, u_near) / u_near) /
                     log(u_far / u_near);
        int cl_slice = int(clamp(cl_t * 16.0, 0.0, 15.0));
        cl_mask = texelFetch(u_cluster_mask,
                             ivec2(cl_tile.x, cl_tile.y * 16 + cl_slice),
                             0).r;
    }
    for (int i = 0; i < MAX_POINT_LIGHTS; ++i){
        if (i >= u_point_count)
            break;
        if ((cl_mask & (1u << i)) == 0u)
            continue;   // light not assigned to this cluster
        vec3 to_light = u_point_pos[i] - world_pos;
        float dist = length(to_light);
        if (dist >= u_point_range[i])
            continue;
        vec3 pl = to_light / max(dist, 0.0001);
        float att = local_light_attenuation(dist, u_point_range[i]);
        vec3 radiance = u_point_color[i] * u_point_intensity[i] * att;
        local_contrib += direct_light_contrib(
            N, V, pl, radiance, albedo, metallic, roughness, foliage);
    }

    // ---- Spot lights ---------------------------------------------------
    // # [TRIPLEA:cluster_lighting_spot_loop]
    for (int i = 0; i < MAX_SPOT_LIGHTS; ++i){
        if (i >= u_spot_count)
            break;
        if ((cl_mask & (1u << (16 + i))) == 0u)
            continue;   // light not assigned to this cluster
        vec3 to_light = u_spot_pos[i] - world_pos;
        float dist = length(to_light);
        if (dist >= u_spot_range[i])
            continue;
        vec3 sl = to_light / max(dist, 0.0001);
        // Compare the direction from the light to the pixel with the node's
        // forward direction.  Smoothstep gives Unity's soft inner/outer cone.
        float cone = dot(-sl, normalize(u_spot_dir[i]));
        // outer_cos is smaller than inner_cos, so smoothstep(outer, inner)
        // would have reversed/undefined edges on some drivers.  Build the
        // Hermite fade manually instead.
        float cone_t = clamp((cone - u_spot_outer_cos[i]) /
                             max(u_spot_inner_cos[i] - u_spot_outer_cos[i], 0.00001),
                             0.0, 1.0);
        float cone_fade = cone_t * cone_t * (3.0 - 2.0 * cone_t);
        if (cone_fade <= 0.0001)
            continue;
        float att = local_light_attenuation(dist, u_spot_range[i]) * cone_fade;
        vec3 radiance = u_spot_color[i] * u_spot_intensity[i] * att;
        local_contrib += direct_light_contrib(
            N, V, sl, radiance, albedo, metallic, roughness, foliage);
    }
    local_contrib *= ao_direct;

    // ---- IBL ambient (EEVEE world lighting) -------------------------
    // Diffuse irradiance from a heavily blurred (high-LOD) env sample,
    // roughness-weighted prefiltered specular for the reflections.
    vec3 irradiance = textureLod(u_env, dir_to_equirect(N), 5.0).rgb
                    * (1.0 - overcast * 0.25);   // clouds bounce some sky light back
    vec3 R = reflect(-V, N);
    vec3 prefiltered = textureLod(u_env, dir_to_equirect(R), roughness * 4.0).rgb;
    vec3 F_ibl = fresnel_schlick_roughness(ndv, F0, roughness);
    vec3 kS_ibl = F_ibl;
    vec3 kD_ibl = (vec3(1.0) - kS_ibl) * (1.0 - metallic);
    // Cheap specular-occlusion: AO dampens reflections in crevices.
    float spec_ao = ao * (1.0 - roughness * 0.5);
    // Vegetation translucency (glTF doubleSided cut-out foliage only):
    // leaves pass light through, so their effective ambient diffuse is far
    // higher than a stone's at the same spot, and their waxy micro-surface
    // reflects much less sky-specular than a flat 0.45-roughness lobe
    // suggests.  Without this, shadowed foliage renders as black/blue clumps
    // (the classic "black Sponza ivy" artefact).  Stones, metals, banners —
    // everything single-sided — is completely unaffected.
    vec3 ibl = (kD_ibl * irradiance * albedo * mix(1.0, 3.0, foliage)
              + prefiltered * F_ibl * spec_ao * mix(1.0, 0.55, foliage))
             * ao * u_ibl_intensity;

    // Small constant fill so pure-black shadows never crush to zero.
    vec3 fill = u_ambient_color * u_ambient_intensity * albedo * ao * 0.25;

    vec3 color = ibl + fill + sun_contrib + local_contrib + emis;
    frag_color = vec4(color, 1.0);
}
"""

# ---- SSAO (view-space, noise-rotated hemisphere kernel — the standard
# high-quality approach; result is blurred by SSAO_BLUR and applied to the
# ambient/IBL terms in the lighting pass) ----
SSAO_FRAG = """#version 330 core
in vec2 v_uv;
out vec4 frag_color;
uniform sampler2D g_position;
uniform sampler2D g_normal;
uniform sampler2D u_noise;      // 4x4 random rotation vectors (tiled)
uniform mat4 u_proj;
uniform mat4 u_view;
uniform vec2 u_noise_scale;     // screen_size / 4
uniform int u_kernel_size;
#define MAX_KERNEL 64
uniform vec3 u_samples[MAX_KERNEL];
uniform float u_radius;
uniform float u_bias;

void main(){
    vec4 gp = texture(g_position, v_uv);
    if (gp.w < 0.5){ frag_color = vec4(1.0); return; }

    // Work in VIEW space (learnopengl-style SSAO).
    vec3 frag_view = (u_view * vec4(gp.xyz, 1.0)).xyz;
    vec3 normal_view = normalize(mat3(u_view) * normalize(texture(g_normal, v_uv).xyz));

    vec3 random_vec = texture(u_noise, v_uv * u_noise_scale).xyz;
    vec3 tangent   = normalize(random_vec - normal_view * dot(random_vec, normal_view));
    vec3 bitangent = cross(normal_view, tangent);
    mat3 TBN = mat3(tangent, bitangent, normal_view);

    float occlusion = 0.0;
    for (int i = 0; i < MAX_KERNEL; ++i){
        if (i >= u_kernel_size) break;
        // Sample position in view space, oriented along the surface normal.
        vec3 sample_pos = frag_view + TBN * u_samples[i] * u_radius;
        // Project to screen UV so we can compare against real geometry.
        vec4 offset = u_proj * vec4(sample_pos, 1.0);
        offset.xyz /= offset.w;
        vec2 suv = offset.xy * 0.5 + 0.5;
        if (suv.x < 0.0 || suv.x > 1.0 || suv.y < 0.0 || suv.y > 1.0)
            continue;
        vec4 sp = texture(g_position, suv);
        if (sp.w < 0.5) continue;   // sky pixel: nothing to occlude with
        float sample_depth = (u_view * vec4(sp.xyz, 1.0)).z;
        // Range check: only occlude when the geometry is actually near.
        float range_check = smoothstep(0.0, 1.0, u_radius / max(abs(frag_view.z - sample_depth), 1e-4));
        occlusion += (sample_depth >= sample_pos.z + u_bias ? 1.0 : 0.0) * range_check;
    }
    occlusion = 1.0 - (occlusion / float(u_kernel_size));
    frag_color = vec4(occlusion, occlusion, occlusion, 1.0);
}
"""

# ---- SSAO blur (4x4 box blur — with the rotated noise kernel this is what
# turns the raw AO speckle into smooth contact shading) ----
SSAO_BLUR_FRAG = """#version 330 core
in vec2 v_uv;
out vec4 frag_color;
uniform sampler2D u_tex;
void main(){
    vec2 texel = 1.0 / vec2(textureSize(u_tex, 0));
    float result = 0.0;
    for (int x = -2; x < 2; ++x)
        for (int y = -2; y < 2; ++y)
            result += texture(u_tex, v_uv + vec2(float(x), float(y)) * texel).r;
    frag_color = vec4(vec3(result / 16.0), 1.0);
}
"""

# ---- Bloom (soft-knee bright extract + blur) ----
BRIGHT_FRAG = """#version 330 core
in vec2 v_uv;
out vec4 frag_color;
uniform sampler2D u_tex;
uniform float u_threshold;   // e.g. 1.0
uniform float u_knee;        // e.g. 0.5 — soft transition band (Unity/EEVEE style)
void main(){
    vec3 c = texture(u_tex, v_uv).rgb;
    float brightness = max(c.r, max(c.g, c.b));
    // Soft knee: quadratic ramp inside the knee band, linear above.
    float soft = clamp(brightness - u_threshold + u_knee, 0.0, 2.0 * u_knee);
    soft = soft * soft / (4.0 * u_knee + 1e-4);
    float contribution = max(soft, brightness - u_threshold) / max(brightness, 1e-4);
    frag_color = vec4(c * contribution, 1.0);
}
"""
BLUR_FRAG = """#version 330 core
in vec2 v_uv;
out vec4 frag_color;
uniform sampler2D u_tex;
uniform vec2 u_direction;
void main(){
    vec2 texel = 1.0 / vec2(textureSize(u_tex, 0));
    vec3 result = texture(u_tex, v_uv).rgb * 0.227027;
    result += texture(u_tex, v_uv + vec2(1.384615) * u_direction * texel).rgb * 0.316216;
    result += texture(u_tex, v_uv - vec2(1.384615) * u_direction * texel).rgb * 0.316216;
    result += texture(u_tex, v_uv + vec2(3.230769) * u_direction * texel).rgb * 0.070270;
    result += texture(u_tex, v_uv - vec2(3.230769) * u_direction * texel).rgb * 0.070270;
    frag_color = vec4(result, 1.0);
}
"""
COMPOSITE_FRAG = """#version 330 core
in vec2 v_uv;
out vec4 frag_color;
uniform sampler2D u_scene;
uniform sampler2D u_bloom;    // tight half-res glow
uniform sampler2D u_bloom2;   // wide quarter-res halo
uniform float u_bloom_strength;
uniform float u_exposure;
void main(){
    vec3 scene = texture(u_scene, v_uv).rgb;
    vec3 bloom = texture(u_bloom, v_uv).rgb * 0.65
               + texture(u_bloom2, v_uv).rgb * 0.55;
    vec3 hdr = scene + bloom * u_bloom_strength;
    // exposure
    hdr *= u_exposure;
    // ACES filmic tonemap
    hdr = (hdr * (2.51 * hdr + 0.03)) / (hdr * (2.43 * hdr + 0.59) + 0.14);
    hdr = clamp(hdr, 0.0, 1.0);
    // gamma
    hdr = pow(hdr, vec3(1.0 / 2.2));
    frag_color = vec4(hdr, 1.0);
}
"""

# ---- Raw blit (1:1 texture copy — used when FXAA is disabled) ----
# The old fallback reused the composite shader, which applies ACES + gamma
# a SECOND time to the already-tonemapped final_color (a washed-out,
# over-bright image).  A raw copy keeps FXAA on/off visually identical
# apart from the antialiasing itself.
BLIT_FRAG = """#version 330 core
in vec2 v_uv;
out vec4 frag_color;
uniform sampler2D u_tex;
void main(){
    frag_color = vec4(texture(u_tex, v_uv).rgb, 1.0);
}
"""

# ---- FXAA 3.11 (final antialiasing pass) ----
# Full "Quality" path of NVIDIA FXAA 3.11 by Timothy Lottes, ported to a
# GLSL 330 single-pass shader (BSD-3-Clause, NVIDIA Corporation).
# Compared to the old console-grade variant this adds:
#   * early-out on flat pixels     → sharp areas stay razor sharp (and free)
#   * horizontal/vertical edge classification + end-of-edge search (8 steps)
#   * sub-pixel aliasing reduction → kills crawling dots on thin geometry
# Preset 15 (8 search steps) is the cheap/quality sweet spot — the early
# exit means flat pixels cost a handful of taps, only true edges pay more.
FXAA_FRAG = """#version 330 core
in vec2 v_uv;
out vec4 frag_color;
uniform sampler2D u_tex;      // tonemapped LDR image
uniform vec2 u_inv_size;      // 1.0 / resolution

// Tuning knobs (defaults recommended for quality presets 12-15)
#define FXAA_SUBPIX             0.75
#define FXAA_EDGE_THRESHOLD     (1.0/6.0)
#define FXAA_EDGE_THRESHOLD_MIN (1.0/12.0)

float fxaa_luma(vec3 rgb) { return dot(rgb, vec3(0.299, 0.587, 0.114)); }

void main(){
    vec2 posM = v_uv;
    vec3 rgbM = texture(u_tex, posM).rgb;
    float lumaM = fxaa_luma(rgbM);
    float lumaS = fxaa_luma(texture(u_tex, posM + vec2( 0.0, 1.0) * u_inv_size).rgb);
    float lumaE = fxaa_luma(texture(u_tex, posM + vec2( 1.0, 0.0) * u_inv_size).rgb);
    float lumaN = fxaa_luma(texture(u_tex, posM + vec2( 0.0,-1.0) * u_inv_size).rgb);
    float lumaW = fxaa_luma(texture(u_tex, posM + vec2(-1.0, 0.0) * u_inv_size).rgb);

    // ---- local contrast range + early exit ----
    float maxSM = max(lumaS, lumaM),  minSM = min(lumaS, lumaM);
    float maxESM = max(lumaE, maxSM), minESM = min(lumaE, minSM);
    float maxWN = max(lumaN, lumaW),  minWN = min(lumaN, lumaW);
    float rangeMax = max(maxWN, maxESM);
    float rangeMin = min(minWN, minESM);
    float range = rangeMax - rangeMin;
    float rangeMaxClamped = max(FXAA_EDGE_THRESHOLD_MIN,
                                rangeMax * FXAA_EDGE_THRESHOLD);
    if (range < rangeMaxClamped) { frag_color = vec4(rgbM, 1.0); return; }

    // ---- corner lumas ----
    float lumaNW = fxaa_luma(texture(u_tex, posM + vec2(-1.0,-1.0) * u_inv_size).rgb);
    float lumaSE = fxaa_luma(texture(u_tex, posM + vec2( 1.0, 1.0) * u_inv_size).rgb);
    float lumaNE = fxaa_luma(texture(u_tex, posM + vec2( 1.0,-1.0) * u_inv_size).rgb);
    float lumaSW = fxaa_luma(texture(u_tex, posM + vec2(-1.0, 1.0) * u_inv_size).rgb);

    // ---- edge direction: is the edge horizontal or vertical? ----
    float lumaNS = lumaN + lumaS;
    float lumaWE = lumaW + lumaE;
    float subpixRcpRange = 1.0 / range;
    float subpixNSWE = lumaNS + lumaWE;
    float edgeHorz1 = (-2.0 * lumaM) + lumaNS;
    float edgeVert1 = (-2.0 * lumaM) + lumaWE;

    float lumaNESE = lumaNE + lumaSE;
    float lumaNWNE = lumaNW + lumaNE;
    float edgeHorz2 = (-2.0 * lumaE) + lumaNESE;
    float edgeVert2 = (-2.0 * lumaN) + lumaNWNE;

    float lumaNWSW = lumaNW + lumaSW;
    float lumaSWSE = lumaSW + lumaSE;
    float edgeHorz4 = (abs(edgeHorz1) * 2.0) + abs(edgeHorz2);
    float edgeVert4 = (abs(edgeVert1) * 2.0) + abs(edgeVert2);
    float edgeHorz3 = (-2.0 * lumaW) + lumaNWSW;
    float edgeVert3 = (-2.0 * lumaS) + lumaSWSE;
    float edgeHorz = abs(edgeHorz3) + edgeHorz4;
    float edgeVert = abs(edgeVert3) + edgeVert4;

    float subpixNWSWNESE = lumaNWSW + lumaNESE;
    float lengthSign = u_inv_size.x;
    bool  horzSpan = edgeHorz >= edgeVert;
    float subpixA = subpixNSWE * 2.0 + subpixNWSWNESE;

    if (!horzSpan) lumaN = lumaW;
    if (!horzSpan) lumaS = lumaE;
    if ( horzSpan) lengthSign = u_inv_size.y;
    float subpixB = (subpixA * (1.0/12.0)) - lumaM;

    // ---- gradient pair selection ----
    float gradientN = lumaN - lumaM;
    float gradientS = lumaS - lumaM;
    float lumaNN = lumaN + lumaM;
    float lumaSS = lumaS + lumaM;
    bool  pairN = abs(gradientN) >= abs(gradientS);
    float gradient = max(abs(gradientN), abs(gradientS));
    if (pairN) lengthSign = -lengthSign;
    float subpixC = clamp(abs(subpixB) * subpixRcpRange, 0.0, 1.0);

    // ---- search along the edge for its ends (quality preset 15: 8 steps) ----
    vec2 posB = posM;
    vec2 offNP;
    offNP.x = (!horzSpan) ? 0.0 : u_inv_size.x;
    offNP.y = ( horzSpan) ? 0.0 : u_inv_size.y;
    if (!horzSpan) posB.x += lengthSign * 0.5;
    if ( horzSpan) posB.y += lengthSign * 0.5;

    const float FXAA_P[8] = float[8](1.0, 1.5, 2.0, 2.0, 2.0, 2.0, 4.0, 8.0);

    vec2 posN = posB - offNP * FXAA_P[0];
    vec2 posP = posB + offNP * FXAA_P[0];
    float subpixD = ((-2.0) * subpixC) + 3.0;
    float lumaEndN = fxaa_luma(texture(u_tex, posN).rgb);
    float subpixE = subpixC * subpixC;
    float lumaEndP = fxaa_luma(texture(u_tex, posP).rgb);

    if (!pairN) lumaNN = lumaSS;
    float gradientScaled = gradient * (1.0/4.0);
    float lumaMM = lumaM - lumaNN * 0.5;
    float subpixF = subpixD * subpixE;
    bool  lumaMLTZero = lumaMM < 0.0;

    lumaEndN -= lumaNN * 0.5;
    lumaEndP -= lumaNN * 0.5;
    bool doneN = abs(lumaEndN) >= gradientScaled;
    bool doneP = abs(lumaEndP) >= gradientScaled;
    if (!doneN) posN -= offNP * FXAA_P[1];
    bool doneNP = (!doneN) || (!doneP);
    if (!doneP) posP += offNP * FXAA_P[1];

    for (int i = 2; i < 8; i++) {
        if (!doneNP) break;
        if (!doneN) lumaEndN = fxaa_luma(texture(u_tex, posN).rgb) - lumaNN * 0.5;
        if (!doneP) lumaEndP = fxaa_luma(texture(u_tex, posP).rgb) - lumaNN * 0.5;
        doneN = abs(lumaEndN) >= gradientScaled;
        doneP = abs(lumaEndP) >= gradientScaled;
        if (!doneN) posN -= offNP * FXAA_P[i];
        doneNP = (!doneN) || (!doneP);
        if (!doneP) posP += offNP * FXAA_P[i];
    }

    // ---- how far along the span is this pixel? ----
    float dstN = posM.x - posN.x;
    float dstP = posP.x - posM.x;
    if (!horzSpan) dstN = posM.y - posN.y;
    if (!horzSpan) dstP = posP.y - posM.y;

    bool  goodSpanN = (lumaEndN < 0.0) != lumaMLTZero;
    float spanLength = dstP + dstN;
    bool  goodSpanP = (lumaEndP < 0.0) != lumaMLTZero;
    float spanLengthRcp = 1.0 / spanLength;

    bool  directionN = dstN < dstP;
    float dst = min(dstN, dstP);
    bool  goodSpan = directionN ? goodSpanN : goodSpanP;
    float subpixG = subpixF * subpixF;
    float pixelOffset = (dst * (-spanLengthRcp)) + 0.5;
    float subpixH = subpixG * FXAA_SUBPIX;

    // ---- final sub-pixel shift + filtered sample ----
    float pixelOffsetGood = goodSpan ? pixelOffset : 0.0;
    float pixelOffsetSubpix = max(pixelOffsetGood, subpixH);
    if (!horzSpan) posM.x += pixelOffsetSubpix * lengthSign;
    if ( horzSpan) posM.y += pixelOffsetSubpix * lengthSign;
    frag_color = vec4(texture(u_tex, posM).rgb, 1.0);
}
"""

# ---- Gizmo / line shader ----
LINE_VERT = """#version 330 core
layout (location = 0) in vec3 in_position;
layout (location = 1) in vec3 in_color;
uniform mat4 u_view;
uniform mat4 u_proj;
uniform mat4 u_model;
out vec3 v_color;
void main(){
    gl_Position = u_proj * u_view * u_model * vec4(in_position, 1.0);
    v_color = in_color;
}
"""
LINE_FRAG = """#version 330 core
in vec3 v_color;
out vec4 frag_color;
void main(){
    frag_color = vec4(v_color, 1.0);
}
"""

# ---- Editor grid shader.  Renders a large ground plane at Y=0 and draws a
# fading infinite grid in the fragment stage using the world XZ coordinates.
# Used only in the editor (hidden at runtime and during Play).
GRID_VERT = """#version 330 core
layout (location = 0) in vec3 in_position;
uniform mat4 u_view;
uniform mat4 u_proj;
uniform mat4 u_model;
out vec3 v_world_pos;
void main(){
    vec4 wp = u_model * vec4(in_position, 1.0);
    v_world_pos = wp.xyz;
    gl_Position = u_proj * u_view * wp;
}
"""
GRID_FRAG = """#version 330 core
in vec3 v_world_pos;
out vec4 frag_color;

uniform vec3 u_camera_pos;
uniform vec3 u_grid_color_minor;   // e.g. dark grey
uniform vec3 u_grid_color_major;   // e.g. brighter grey
uniform float u_cell_size;         // world units per minor cell
uniform float u_major_every;       // draw a major line every N minor cells
uniform float u_fade_distance;     // world units over which the grid fades out

float grid_line(vec2 p, float scale, float thickness){
    vec2 g = abs(fract(p / scale - 0.5) - 0.5) / fwidth(p / scale);
    float line = min(g.x, g.y);
    return 1.0 - min(line, 1.0);
}

void main(){
    // Only draw on the XZ plane (the geometry IS on Y=0; this is a guard).
    vec2 coord = v_world_pos.xz;

    // Distance-based opacity so the grid fades into the distance (and
    // doesn't clutter the horizon).
    float dist = length(v_world_pos.xz - u_camera_pos.xz);
    float fade = 1.0 - smoothstep(u_fade_distance * 0.5, u_fade_distance, dist);

    // Minor + major grid lines
    float minor = grid_line(coord, u_cell_size, 1.0);
    float major = grid_line(coord, u_cell_size * u_major_every, 1.0);
    vec3 color = mix(u_grid_color_minor, u_grid_color_major, major);

    float alpha = max(minor * 0.35, major * 0.75) * fade;
    if (alpha < 0.01)
        discard;

    frag_color = vec4(color, alpha);
}
"""

# ---- Flat-shaded fallback (UI quads, text).  All UI geometry is specified
# in PIXEL space; the shader converts to NDC using u_resolution so the
# aspect ratio is correct (text is never horizontally squashed).
FLAT_VERT = """#version 330 core
layout (location = 0) in vec2 in_position;   // unit quad 0..1
layout (location = 1) in vec2 in_uv;
uniform vec4 u_rect;       // x, y, w, h  in pixels (origin = bottom-left)
uniform vec2 u_resolution; // screen size in pixels
uniform vec2 u_anchor;     // pivot of u_rect used for aspect correction:
                           // (0,0) = bottom-left, (0.5,0.5) = centre
out vec2 v_uv;
out vec2 v_local;          // fragment position inside the rect, in PIXELS
void main(){
    // Map the unit quad into the pixel rectangle.
    vec2 px = in_position * u_rect.zw + u_rect.xy;
    // Pixels → NDC.  Standard bottom-up projection (no Y flip) so the
    // shader matches the bottom-left origin used by _build_ui.
    vec2 ndc = vec2(
        (px.x / u_resolution.x) * 2.0 - 1.0,
        (px.y / u_resolution.y) * 2.0 - 1.0
    );
    gl_Position = vec4(ndc, 0.0, 1.0);
    v_uv = in_uv;
    v_local = in_position * u_rect.zw;   // 0..w, 0..h — for the rounded SDF
}
"""
FLAT_FRAG = """#version 330 core
in vec2 v_uv;
in vec2 v_local;
out vec4 frag_color;
uniform vec4 u_color;
uniform vec4 u_rect;      // zw = quad size in pixels (drives the corner SDF)
uniform float u_radius;   // corner radius in pixels (0 = perfectly square)
uniform sampler2D u_tex;
uniform int u_use_tex;

// Signed distance to a rounded box centred on the origin.
float rounded_box_sdf(vec2 p, vec2 half_size, float r){
    vec2 q = abs(p) - half_size + vec2(r);
    return length(max(q, vec2(0.0))) + min(max(q.x, q.y), 0.0) - r;
}

void main(){
    vec4 c = u_color;
    if (u_use_tex == 1) {
        c *= texture(u_tex, v_uv);
    } else if (u_radius > 0.0) {
        // Anti-aliased rounded corners: fade alpha over a ~1.8px band at
        // the rounded-rect edge.  Texture quads (glyphs) are never rounded.
        vec2 hs = u_rect.zw * 0.5;
        float r = min(u_radius, min(hs.x, hs.y));
        float d = rounded_box_sdf(v_local - hs, hs, r);
        float coverage = 1.0 - smoothstep(-0.9, 0.9, d);
        c.a *= coverage;
    }
    frag_color = c;
}
"""


class ShaderLib:
    """Compiles and caches ModernGL programs."""
    _cache: Dict[str, moderngl.Program] = {}

    @classmethod
    def get(cls, ctx: moderngl.Context, name: str) -> Optional[moderngl.Program]:
        if name in cls._cache:
            return cls._cache[name]
        sources = {
            "shadow":    (SHADOW_VERT, SHADOW_FRAG),
            "geometry":  (GEOMETRY_VERT, GEOMETRY_FRAG),
            "forward":   (FORWARD_VERT, FORWARD_FRAG),
            "fullscreen":(FULLSCREEN_VERT, None),
            "lighting":  (FULLSCREEN_VERT, LIGHTING_FRAG),
            "ssao":      (FULLSCREEN_VERT, SSAO_FRAG),
            "ssao_blur": (FULLSCREEN_VERT, SSAO_BLUR_FRAG),
            "bright":    (FULLSCREEN_VERT, BRIGHT_FRAG),
            "blur":      (FULLSCREEN_VERT, BLUR_FRAG),
            "composite": (FULLSCREEN_VERT, COMPOSITE_FRAG),
            "blit":      (FULLSCREEN_VERT, BLIT_FRAG),
            "fxaa":      (FULLSCREEN_VERT, FXAA_FRAG),
            "line":      (LINE_VERT, LINE_FRAG),
            "grid":      (GRID_VERT, GRID_FRAG),
            "flat":      (FLAT_VERT, FLAT_FRAG),
        }
        if name not in sources:
            Log.error(f"Unknown shader '{name}'")
            return None
        vert, frag = sources[name]
        try:
            prog = ctx.program(vertex_shader=vert,
                               fragment_shader=frag) if frag \
                   else ctx.program(vertex_shader=vert)
            cls._cache[name] = prog
            Log.debug(f"Shader compiled: {name}")
            return prog
        except Exception as ex:
            Log.error(f"Shader '{name}' compile failed: {ex}")
            return None

