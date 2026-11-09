# @heaven3d-part rendering/water.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# #############################################################################
# TRIPLE-A WATER & REFLECTIONS  (runtime block, patch module p2_water)
# [TRIPLEA:triplea_water_runtime]
#   * OceanSettings dataclass (serialized with the scene, hot-editable)
#   * GLSL programs "ssr" / "ssr_composite" / "ocean", registered through a
#     ShaderLib.get wrapper that delegates unknown names to the previously
#     bound implementation (order-independent vs other patch modules)
#   * _OCEAN_STATE — per-frame CPU mirror of the water surface fed by the
#     render pass — plus a PhysicsWorld._fixed_step runtime wrap adding
#     buoyancy, water drag and contact ripples for floating bodies
# #############################################################################


@dataclass
class OceanSettings:
    """Triple-A procedural ocean — a default scene feature, exactly like
    the Sun and the Clouds.

    Rendered as a standalone fullscreen pass between lighting and forward
    transparency: each pixel's world ray is intersected with an analytic
    3-octave Gerstner wave field at `sea_level`, depth-composited against
    the G-buffer, shaded with Beer-Lambert absorption, fresnel sky
    reflection, sun glint and foam, then blitted back over the HDR scene.
    The same wave sum is mirrored on the CPU (_ocean_wave_height_at) to
    drive buoyancy on dynamic physics bodies.  Everything is hot-editable
    from the Inspector.
    """
    enabled: bool = True
    sea_level: float = 0.0              # world height of the mean surface
    water_shallow: glm.vec3 = glm.vec3(0.16, 0.44, 0.46)
    water_deep: glm.vec3 = glm.vec3(0.012, 0.085, 0.17)
    wave_height: float = 0.45           # peak amplitude of the wave sum (m)
    wave_speed: float = 1.0             # global phase-speed multiplier
    wind_dir: Tuple[float, float] = (1.0, 0.35)   # XZ travel direction
    wind_strength: float = 1.0          # scales phase speed (0 = glassy)
    choppiness: float = 0.7             # Gerstner crest sharpening
    foam_amount: float = 1.0            # shoreline + crest + ripple foam
    buoyancy_enabled: bool = True
    buoyancy_strength: float = 1.0

    def to_dict(self) -> dict:
        return {"enabled": self.enabled,
                "sea_level": self.sea_level,
                "water_shallow": [float(self.water_shallow.x),
                                  float(self.water_shallow.y),
                                  float(self.water_shallow.z)],
                "water_deep": [float(self.water_deep.x),
                               float(self.water_deep.y),
                               float(self.water_deep.z)],
                "wave_height": self.wave_height,
                "wave_speed": self.wave_speed,
                "wind_dir": [float(self.wind_dir[0]), float(self.wind_dir[1])],
                "wind_strength": self.wind_strength,
                "choppiness": self.choppiness,
                "foam_amount": self.foam_amount,
                "buoyancy_enabled": self.buoyancy_enabled,
                "buoyancy_strength": self.buoyancy_strength}

    @classmethod
    def from_dict(cls, d: dict) -> "OceanSettings":
        s = d.get("water_shallow", [0.16, 0.44, 0.46])
        dp = d.get("water_deep", [0.012, 0.085, 0.17])
        w = d.get("wind_dir", [1.0, 0.35])
        return cls(enabled=bool(d.get("enabled", True)),
                   sea_level=float(d.get("sea_level", 0.0)),
                   water_shallow=glm.vec3(float(s[0]), float(s[1]),
                                          float(s[2])),
                   water_deep=glm.vec3(float(dp[0]), float(dp[1]),
                                       float(dp[2])),
                   wave_height=float(d.get("wave_height", 0.45)),
                   wave_speed=float(d.get("wave_speed", 1.0)),
                   wind_dir=(float(w[0]), float(w[1])),
                   wind_strength=float(d.get("wind_strength", 1.0)),
                   choppiness=float(d.get("choppiness", 0.7)),
                   foam_amount=float(d.get("foam_amount", 1.0)),
                   buoyancy_enabled=bool(d.get("buoyancy_enabled", True)),
                   buoyancy_strength=float(d.get("buoyancy_strength", 1.0)))


# ---- SSR: short-radius screen-space reflections -------------------------
# Marches a view-space ray against the G-buffer position field (there is
# no sampleable depth texture — g_depth is a renderbuffer), half-res.
SSR_FRAG = """#version 330 core
// [TRIPLEA:triplea_water_runtime]
in vec2 v_uv;
out vec4 frag_color;

uniform sampler2D g_position;   // world xyz, w = 1 for geometry
uniform sampler2D g_normal;     // world normal
uniform sampler2D g_albedo;     // rgb = albedo, a = metallic
uniform sampler2D g_material;   // r = roughness
uniform sampler2D u_scene;      // lit HDR scene (reflection source)

uniform mat4 u_view;
uniform mat4 u_proj;
uniform vec3 u_camera_pos;

const float MAX_DIST     = 8.0;    // short radius — GPU-cheap
const int   MAX_STEPS    = 24;
const int   REFINE_STEPS = 4;
const float THICKNESS    = 0.3;

vec3 to_view(vec3 p){ return (u_view * vec4(p, 1.0)).xyz; }
vec2 to_uv(vec3 pv){
    vec4 c = u_proj * vec4(pv, 1.0);
    return (c.xy / c.w) * 0.5 + 0.5;
}
float scene_depth_at(vec2 uv){
    vec4 sg = texture(g_position, uv);
    return (sg.w > 0.5) ? -to_view(sg.xyz).z : MAX_DIST * 4.0;
}

void main(){
    frag_color = vec4(0.0);
    vec4 gp = texture(g_position, v_uv);
    if (gp.w < 0.5) return;                          // sky — no reflector
    vec3  N = normalize(texture(g_normal, v_uv).xyz);
    vec4  alb = texture(g_albedo, v_uv);
    float metallic  = alb.a;
    float roughness = texture(g_material, v_uv).r;
    // Skip rough surfaces: glossy-only SSR keeps the pass cheap.
    float rough_w = 1.0 - smoothstep(0.25, 0.60, roughness);
    if (rough_w <= 0.004) return;

    vec3 P = gp.xyz;
    vec3 V = normalize(P - u_camera_pos);
    vec3 R = reflect(V, N);
    if (dot(R, N) <= 0.0) return;
    // Small normal bias kills self-intersection acne on the source pixel.
    vec3 pv0 = to_view(P + N * 0.05);
    vec3 rv  = normalize(mat3(u_view) * R);

    float step_len = MAX_DIST / float(MAX_STEPS);
    float prev_t   = 0.0;
    float t        = step_len;
    float prev_d   = -1.0;
    float hit_t    = -1.0;
    vec2  hit_uv   = vec2(0.0);
    for (int i = 0; i < MAX_STEPS; ++i){
        vec3 sp = pv0 + rv * t;
        if (sp.z > -0.05) break;                     // behind the camera
        vec2 suv = to_uv(sp);
        if (suv.x < 0.0 || suv.x > 1.0 || suv.y < 0.0 || suv.y > 1.0) break;
        float diff = -sp.z - scene_depth_at(suv);    // >0 → ray is behind
        if (diff > 0.0 && prev_d <= 0.0){
            // Binary refinement on the crossing between prev_t and t.
            float lo = prev_t, hi = t;
            for (int j = 0; j < REFINE_STEPS; ++j){
                float m = 0.5 * (lo + hi);
                vec3 mp = pv0 + rv * m;
                vec2 muv = to_uv(mp);
                float mdiff = -mp.z - scene_depth_at(muv);
                if (mdiff > 0.0) hi = m; else lo = m;
                hit_uv = muv;
            }
            vec3 hp = pv0 + rv * hi;
            if (abs(-hp.z - scene_depth_at(hit_uv)) < THICKNESS){
                hit_t = hi;
            }
            break;
        }
        prev_d = diff;
        prev_t = t;
        t += step_len;
    }
    if (hit_t < 0.0) return;

    hit_uv = clamp(hit_uv, vec2(0.0), vec2(1.0));
    vec3 hitN = normalize(texture(g_normal, hit_uv).xyz);
    if (dot(hitN, R) > 0.2) return;                  // back face — reject
    // Screen-edge fade (no valid data outside the frame).
    vec2 ee = min(hit_uv, 1.0 - hit_uv);
    float edge = clamp(min(ee.x, ee.y) / 0.08, 0.0, 1.0);
    edge = edge * edge * (3.0 - 2.0 * edge);
    float dist_f = 1.0 - clamp(hit_t / MAX_DIST, 0.0, 1.0);
    float fres = pow(1.0 - clamp(dot(-V, N), 0.0, 1.0), 5.0);
    // Fresnel-weighted, metals reflect at all angles (metallic boost).
    float w = rough_w * edge * (0.35 + 0.65 * dist_f)
            * mix(0.15 + 0.85 * fres, 1.0, metallic);
    vec3 tint = mix(vec3(1.0), alb.rgb * 2.0, metallic);
    vec3 refl = texture(u_scene, hit_uv).rgb;
    frag_color = vec4(refl * tint * w, 1.0);
}
"""

# ---- SSR composite: additive blend scene + reflections -----------------
SSR_COMPOSITE_FRAG = """#version 330 core
// [TRIPLEA:triplea_water_runtime]
in vec2 v_uv;
out vec4 frag_color;
uniform sampler2D u_scene;
uniform sampler2D u_ssr;
void main(){
    frag_color = vec4(texture(u_scene, v_uv).rgb
                      + texture(u_ssr, v_uv).rgb, 1.0);
}
"""

# ---- Ocean: analytic wave surface composited over the HDR scene -------
OCEAN_FRAG = """#version 330 core
// [TRIPLEA:triplea_water_runtime]
in vec2 v_uv;
out vec4 frag_color;

uniform sampler2D g_position;   // world xyz, w = 1 for geometry
uniform sampler2D u_scene;      // lit HDR scene colour
uniform sampler2D u_env;        // equirect environment (procedural sky)

uniform mat4  u_inv_proj;
uniform mat4  u_inv_view;
uniform vec3  u_camera_pos;
uniform vec3  u_sun_dir;
uniform vec3  u_sun_color;
uniform float u_sun_intensity;
uniform float u_time;

uniform float u_sea_level;
uniform vec3  u_water_shallow;
uniform vec3  u_water_deep;
uniform float u_wave_height;
uniform float u_wave_speed;
uniform vec2  u_wind_dir;
uniform float u_wind_strength;
uniform float u_choppiness;
uniform float u_foam_amount;

#define OCEAN_MAX_RIPPLES 8
uniform vec4 u_ripples[OCEAN_MAX_RIPPLES];   // xz, birth_time, strength
uniform int  u_ripple_count;

const float PI = 3.14159265359;
""" + CLOUD_GLSL + """

// ---- 3-octave Gerstner wave field (mirrored on the CPU for buoyancy) --
const float WL[3] = float[3](23.0, 11.0, 5.5);    // wavelengths (m)
const float FR[3] = float[3](0.5, 0.3, 0.2);      // amplitude fractions
const float AN[3] = float[3](0.0, 0.65, -0.85);   // dir offsets vs wind

vec2 wave_dir(int i, vec2 wd){
    float ca = cos(AN[i]);
    float sa = sin(AN[i]);
    return vec2(wd.x * ca - wd.y * sa, wd.x * sa + wd.y * ca);
}

float wave_height_at(vec2 xz){
    vec2 wd = normalize(u_wind_dir + vec2(1e-5, 0.0));
    float spd = u_wave_speed * (0.35 + 0.65 * u_wind_strength);
    float h = 0.0;
    for (int i = 0; i < 3; ++i){
        vec2 d = wave_dir(i, wd);
        float k = 2.0 * PI / WL[i];
        float om = sqrt(9.81 * k) * spd;      // deep-water dispersion
        h += u_wave_height * FR[i] * sin(k * dot(d, xz) + om * u_time);
    }
    return h;
}

// Expanding ring ripples emitted by floating / splashing bodies.
vec2 ripple_grad(vec2 xz){
    vec2 g = vec2(0.0);
    for (int i = 0; i < OCEAN_MAX_RIPPLES; ++i){
        if (i >= u_ripple_count) break;
        vec4 rp = u_ripples[i];
        float age = u_time - rp.z;
        if (age < 0.0 || age > 4.0) continue;
        vec2 dxz = xz - rp.xy;
        float r = max(length(dxz), 1e-3);
        float ring = age * 2.5;                     // expansion speed (m/s)
        float band = exp(-pow((r - ring) * 1.6, 2.0));
        float amp = rp.w * exp(-age * 1.1) * exp(-r * 0.12);
        g += (dxz / r) * band * amp * cos((r - ring) * 7.0) * 0.6;
    }
    return g;
}

float ws_hash(vec2 p){
    p = fract(p * vec2(234.34, 435.345));
    p += dot(p, p + 34.23);
    return fract(p.x * p.y);
}
// gradient of a quintic value-noise field (height range about -0.5..0.5)
vec2 noise_grad(vec2 p){
    vec2 i = floor(p);
    vec2 f = fract(p);
    vec2 u  = f * f * f * (f * (f * 6.0 - 15.0) + 10.0);
    vec2 du = 30.0 * f * f * (f * (f - 2.0) + 1.0);
    float a = ws_hash(i);
    float b = ws_hash(i + vec2(1.0, 0.0));
    float c = ws_hash(i + vec2(0.0, 1.0));
    float d = ws_hash(i + vec2(1.0, 1.0));
    return du * (vec2(b - a, c - a) + (a - b - c + d) * u.yx);
}

// Surface normal: the 3 Gerstner waves (the same ones physics floats things
// on) plus a 12-wave detail spectrum that only shapes the normal (heights
// and buoyancy are unchanged).  Every wave is faded out once it gets smaller
// than about two pixels (fp = metres per pixel at the hit), which is what
// keeps distant water from shimmering or showing a lattice; the slope
// energy that fades out is returned as roughness (var) so the far ocean
// gets a wider, softer sun path and blurrier reflections instead.
vec3 wave_normal(vec2 xz, float fp, out float var){
    vec2 wd = normalize(u_wind_dir + vec2(1e-5, 0.0));
    float spd = u_wave_speed * (0.35 + 0.65 * u_wind_strength);
    vec3 n = vec3(0.0, 1.0, 0.0);
    var = 0.0;
    float q = clamp(u_choppiness, 0.0, 1.5) * 0.25;
    vec2 disp = vec2(0.0);                       // big waves' horizontal sway
    for (int i = 0; i < 3; ++i){
        vec2 d = wave_dir(i, wd);
        float k = 2.0 * PI / WL[i];
        float om = sqrt(9.81 * k) * spd;
        float A = u_wave_height * FR[i];
        float wa = k * A;
        float ph = k * dot(d, xz) + om * u_time;
        float c = cos(ph);
        float fade = 1.0 - smoothstep(0.12, 0.45, fp / WL[i]);
        n.x -= d.x * wa * c * fade;
        n.z -= d.y * wa * c * fade;
        n.y -= q * wa * sin(ph) * fade;          // crest sharpening
        var += wa * wa * 0.5 * (1.0 - fade);
        disp += d * (A * 1.6 * c);
    }
    // detail: 2 swell waves (3.6, 2.5 m) + 3 octaves of wind-driven
    // noise ripples (1.6 m .. 0.13 m).  Both ride the big waves (disp), and
    // the noise has no direction pattern, so the surface never tiles.
    vec2 dx = xz + disp;
    float wang = atan(wd.y, wd.x);
    float calm = sqrt(clamp(u_wave_height / 0.45, 0.0, 3.0));
    float s = 0.085 * (0.3 + 0.7 * clamp(u_wind_strength, 0.0, 2.0)) * calm;
    float wl = 3.6;
    for (int i = 0; i < 2; ++i){
        if (fp > 0.45 * wl){ var += s * s * 0.5; wl *= 0.70; s *= 0.92; continue; }   // fully faded
        float ang = wang + sin(float(i) * 2.39996 + 0.5) * 1.25;
        vec2 d = vec2(cos(ang), sin(ang));
        float k = 2.0 * PI / wl;
        float om = sqrt(9.81 * k) * spd;
        float ph = k * dot(d, dx) + om * u_time + float(i) * 1.7;
        float fade = 1.0 - smoothstep(0.12, 0.45, fp / wl);
        float sl = s * cos(ph) * fade;
        n.x -= d.x * sl;
        n.z -= d.y * sl;
        var += s * s * 0.5 * (1.0 - fade);
        wl *= 0.70;
        s *= 0.92;
    }
    mat2 rot = mat2(0.8, 0.6, -0.6, 0.8);
    vec2 np = vec2(dot(dx, wd), dot(dx, vec2(-wd.y, wd.x)) * 1.6) / 1.6
            - vec2(u_time * 0.55 * spd, 0.0);
    float sz = 1.6;
    float ns = s * 0.75;
    for (int i = 0; i < 3; ++i){
        if (fp > 0.45 * sz){                          // this and smaller octaves are sub-pixel
            var += ns * ns * 0.35 * (i == 0 ? 1.879 : (i == 1 ? 1.5625 : 1.0));
            break;
        }
        vec2 g = noise_grad(np) / sz;                 // slope of the noise height field
        g = vec2(g.x * wd.x - g.y * wd.y * 1.6, g.x * wd.y + g.y * wd.x * 1.6);
        float fade = 1.0 - smoothstep(0.12, 0.45, fp / sz);
        n.x -= g.x * ns * fade;
        n.z -= g.y * ns * fade;
        var += ns * ns * 0.35 * (1.0 - fade);
        np = rot * np * 2.3 + vec2(5.2, 1.3) - vec2(u_time * 0.4, 0.0);
        sz /= 2.3;
        ns *= 0.75;
    }
    vec2 rg = ripple_grad(xz);
    n.x -= rg.x;
    n.z -= rg.y;
    n.y = max(n.y, 0.25);                        // never tip past ~75 degrees
    return normalize(n);
}

vec3 world_dir_from_uv(vec2 uv){
    vec4 ndc  = vec4(uv * 2.0 - 1.0, 1.0, 1.0);
    vec4 vpos = u_inv_proj * ndc;
    vpos /= vpos.w;
    vec3 wpos = (u_inv_view * vec4(vpos.xyz, 1.0)).xyz;
    return normalize(wpos - u_camera_pos);
}

vec3 env_sample(vec3 d){
    vec2 uv = vec2(atan(d.z, d.x) / (2.0 * PI) + 0.5,
                   asin(clamp(d.y, -1.0, 1.0)) / PI + 0.5);
    return textureLod(u_env, uv, 0.0).rgb;
}

float hash21(vec2 p){
    p = fract(p * vec2(234.34, 435.345));
    p += dot(p, p + 34.23);
    return fract(p.x * p.y);
}
float vnoise(vec2 p){
    vec2 i = floor(p);
    vec2 f = fract(p);
    f = f * f * f * (f * (f * 6.0 - 15.0) + 10.0);
    return mix(mix(hash21(i),                  hash21(i + vec2(1.0, 0.0)), f.x),
               mix(hash21(i + vec2(0.0, 1.0)), hash21(i + vec2(1.0, 1.0)), f.x),
               f.y);
}
float fnoise(vec2 p){                            // 3-octave, rotated: soft foam patterns
    mat2 r = mat2(0.8, 0.6, -0.6, 0.8);
    float v = vnoise(p) * 0.55;
    p = r * p * 2.1 + 7.3;
    v += vnoise(p) * 0.3;
    p = r * p * 2.1 + 3.1;
    v += vnoise(p) * 0.15;
    return v;
}
// metres covered by one pixel at distance t along D, on the water plane
// px_ang: angle one pixel spans (from fwidth(D), taken at the top of main()
// where all pixels of a quad run together - derivatives inside branches
// are undefined in GLSL)
float pixel_footprint(float t, vec3 D, float px_ang){
    return t * px_ang / max(abs(D.y), 0.03);
}

void main(){
    vec4 gp = texture(g_position, v_uv);
    vec3 scene_col = texture(u_scene, v_uv).rgb;
    vec3 O = u_camera_pos;
    vec3 D = world_dir_from_uv(v_uv);
    float px_ang = length(fwidth(D));
    bool has_geom = gp.w > 0.5;
    float scene_t = has_geom ? distance(gp.xyz, O) : 1e9;

    float cam_h = u_sea_level + wave_height_at(O.xz);
    bool under = O.y < cam_h;

    // ---- march the ray through the animated water slab ----
    float y0 = u_sea_level - u_wave_height;
    float y1 = u_sea_level + u_wave_height;
    float t_hit = -1.0;
    bool facing = !((O.y > y1 && D.y > -1e-5) || (O.y < y0 && D.y <= 1e-5));
    if (facing){
        float ta = (abs(D.y) > 1e-5) ? (y0 - O.y) / D.y : -1e9;
        float tb = (abs(D.y) > 1e-5) ? (y1 - O.y) / D.y :  1e9;
        float t0 = max(min(ta, tb), 0.0);
        float t1 = min(max(ta, tb), scene_t);
        if (O.y > y0 && O.y < y1) t0 = 0.0;    // eye inside the slab
        if (t1 > t0){
            float prev_t = t0;
            vec3 pp = O + D * t0;
            float prev_f = pp.y - (u_sea_level + wave_height_at(pp.xz));
            const int STEPS = 16;
            for (int i = 1; i <= STEPS; ++i){
                float t = t0 + (t1 - t0) * float(i) / float(STEPS);
                vec3 p = O + D * t;
                float f = p.y - (u_sea_level + wave_height_at(p.xz));
                if (f * prev_f <= 0.0){        // sign change → crossing
                    float lo = prev_t, hi = t;
                    for (int j = 0; j < 4; ++j){
                        float m = 0.5 * (lo + hi);
                        vec3 mp = O + D * m;
                        float mf = mp.y - (u_sea_level + wave_height_at(mp.xz));
                        if (mf * prev_f > 0.0) lo = m; else hi = m;
                    }
                    t_hit = 0.5 * (lo + hi);
                    break;
                }
                prev_t = t;
                prev_f = f;
            }
        }
    }

    vec3 col = scene_col;
    bool water_visible = (t_hit > 0.0 && t_hit < scene_t);
    if (water_visible){
        vec3 P = O + D * t_hit;
        float fp = pixel_footprint(t_hit, D, px_ang);
        float var;
        vec3 N = wave_normal(P.xz, fp, var);
        vec3 V = -D;
        float ndv = clamp(dot(N, V), 0.0, 1.0);
        float fres = 0.02 + 0.98 * pow(1.0 - ndv, 5.0);
        vec3 L = -u_sun_dir;                     // direction TOWARD the sun
        float rough = clamp(0.04 + sqrt(var) * 1.3, 0.04, 0.55);

        vec3 R = reflect(D, N);
        R.y = abs(R.y);                          // the sea never reflects the ground below the horizon
        vec3 refl = textureLod(u_env, cloud_eq(R), rough * 6.0).rgb;
        // The sky's clouds, mirrored.  Faded out where the water is far
        // enough that a pixel covers a lot of wavy surface (fp) - there the
        // reflected clouds would be sub-pixel noise that sparkles as the
        // waves move; the blurred sky above is what the eye sees anyway.
        float cl_w = (1.0 - smoothstep(0.12, 0.7, fp)) * smoothstep(0.03, 0.06, fres)
                   * smoothstep(0.05, 0.22, R.y);   // low reflected rays hit far-off, sub-pixel clouds
        if (!under && cl_w > 0.0){
            vec4 cl = cloud_layer(P, R, true);
            refl = mix(refl, cl.rgb, cl.a * cl_w);
        }
        // Sun glitter: normalised Blinn-Phong whose width follows the
        // roughness -> a sharp glint nearby, a wide glittering path far off.
        vec3 Hh = normalize(L + V);
        float a2 = rough * rough;
        float shin = clamp(2.0 / max(a2 * a2, 1e-5) - 2.0, 8.0, 6000.0);
        float spec = (shin + 8.0) / (8.0 * PI) * pow(max(dot(N, Hh), 0.0), shin)
                   * smoothstep(-0.02, 0.06, L.y);
        refl += u_sun_color * u_sun_intensity * min(spec * 0.08, 40.0);

        // Water column between the surface and the scene geometry behind
        // it drives Beer-Lambert absorption + the shallow/deep gradient.
        float wdepth = has_geom ? max(scene_t - t_hit, 0.0) : 80.0;
        vec3 absorb = exp(-wdepth * vec3(0.42, 0.18, 0.12));
        // Fake refraction: nudge the scene sample along the wave normal.
        vec2 ruv = clamp(v_uv + N.xz * (0.06 * clamp(wdepth, 0.0, 4.0)),
                         0.0, 1.0);
        vec3 behind = texture(u_scene, ruv).rgb;
        if (!has_geom) behind = u_water_deep * 0.5;
        vec3 body = mix(u_water_shallow, u_water_deep,
                        1.0 - exp(-wdepth * 0.10));
        // lit body: the water itself catches some sun and sky
        float sky_lum = 0.35 + 0.65 * max(L.y, 0.0);
        vec3 trans = behind * absorb + body * (1.0 - absorb) * 0.6 * (0.55 + 0.45 * sky_lum);

        // Subsurface scattering: sunlight shining through the thin top of
        // a wave toward the viewer turns crests a luminous green-blue.
        float h01 = clamp((P.y - u_sea_level) / max(u_wave_height, 1e-3) * 0.5 + 0.5, 0.0, 1.0);
        vec3 Lh = normalize(vec3(L.x, max(L.y, 0.0) * 0.3, L.z));
        float sss = pow(clamp(dot(V, -Lh), 0.0, 1.0), 3.0) * (0.25 + 0.75 * h01 * h01)
                  + 0.25 * h01 * h01 * h01;
        vec3 sss_col = mix(u_water_shallow, vec3(0.10, 0.55, 0.50), 0.5);
        trans += sss_col * u_sun_color * u_sun_intensity * sss * 0.11
               * smoothstep(-0.05, 0.2, L.y) * (1.0 - fres);

        vec3 wcol;
        if (!under){
            wcol = mix(trans, refl, fres);
        } else {
            // Seen from below: compressed-hemisphere Snell window toward
            // the sky, total-internal reflection (deep teal) at grazing.
            vec3 Ndn = -N;
            float f2 = 0.02 + 0.98 * pow(1.0 - clamp(dot(Ndn, -D), 0.0, 1.0), 5.0);
            vec3 sd = normalize(vec3(D.x * 0.5, abs(D.y), D.z * 0.5));
            vec3 up = env_sample(sd);
            // Attenuate the sky window by the submerged path length only —
            // wdepth (geometry beyond the surface) is meaningless here.
            vec3 absorb_up = exp(-t_hit * vec3(0.05, 0.022, 0.015));
            wcol = mix(up * absorb_up, u_water_deep * 1.5,
                       clamp(f2 * 1.8, 0.0, 1.0));
        }

        // ---- foam: shoreline (geometry pierces the surface), wind-streaked
        // whitecaps on the crests and ripple rings.  Its pattern is averaged
        // out with distance (fp) so far foam is a soft sheen, never noise.
        float far = smoothstep(0.08, 0.6, fp);
        float foam = 0.0;
        if (has_geom){
            float shore = 1.0 - smoothstep(0.0, 1.2, wdepth);
            float fn = mix(fnoise(P.xz * 2.2 + vec2(u_time * 0.5, -u_time * 0.35)), 0.5, far);
            foam += shore * smoothstep(0.35, 0.75, fn + shore * 0.35);
        }
        float crest = smoothstep(0.80, 1.0, h01) * clamp(u_wind_strength, 0.0, 2.0);
        if (crest > 0.0){                        // whitecaps only where crests are
            vec2 wdir = normalize(u_wind_dir + vec2(1e-5, 0.0));
            vec2 fq = vec2(dot(P.xz, wdir), dot(P.xz, vec2(-wdir.y, wdir.x)) * 2.6) * 0.9
                    + vec2(-u_time * 0.6, 0.0);
            float streak = mix(fnoise(fq), 0.5, far);
            foam += crest * smoothstep(0.42, 0.78, streak) * 0.8;
        }
        foam += length(ripple_grad(P.xz)) * 2.0;
        foam = clamp(foam * u_foam_amount, 0.0, 1.0);
        if (foam > 0.0){
            // lit like everything else: sky light + sun (dark at night)
            vec3 zen = textureLod(u_env, cloud_eq(vec3(0.0, 1.0, 0.0)), 4.0).rgb;
            vec3 foam_rgb = vec3(0.9, 0.96, 1.0)
                          * (dot(zen, vec3(0.3, 0.5, 0.2)) * 0.6 + u_sun_intensity * 0.3 * max(L.y, 0.0));
            wcol = mix(wcol, foam_rgb, foam);
        }

        // Aerial perspective: far water melts into the horizon haze
        // instead of ending in a hard line.
        if (!under){
            vec3 haze = env_sample(normalize(vec3(D.x, 0.015, D.z)));
            wcol = mix(wcol, haze, (1.0 - exp(-t_hit * 0.0011)) * 0.6);
        }
        col = wcol;
    }

    // ---- underwater fog / absorption for a submerged camera ----
    if (under){
        float path = water_visible ? t_hit : (has_geom ? scene_t : 60.0);
        col *= exp(-path * vec3(0.10, 0.045, 0.03));   // blue-green loss
        // cheap caustic shimmer drifting over everything submerged
        float ca = vnoise(O.xz * 0.5 + D.xz * path * 0.5
                          + vec2(u_time * 0.35, -u_time * 0.27));
        col *= 0.9 + 0.2 * ca;
        vec3 fogc = u_water_deep * (0.6 + 0.4 * max(-D.y, 0.0));
        col = mix(col, fogc, 1.0 - exp(-path * 0.055));
    }

    frag_color = vec4(col, 1.0);
}
"""


# ---------------------------------------------------------------- ShaderLib
_TRIPLEA_SHADER_SOURCES = {
    "ssr":           (FULLSCREEN_VERT, SSR_FRAG),
    "ssr_composite": (FULLSCREEN_VERT, SSR_COMPOSITE_FRAG),
    "ocean":         (FULLSCREEN_VERT, OCEAN_FRAG),
}
_SHADERLIB_GET_PRE_TRIPLEA = ShaderLib.get   # previously-bound (classmethod)


def _shaderlib_get_triplea(cls, ctx: moderngl.Context, name: str
                           ) -> Optional[moderngl.Program]:
    """ShaderLib.get wrapper adding the Triple-A water programs.

    Compiles and caches exactly like the original implementation; any
    unknown name is delegated to the previously-bound `get`, so this
    wrapper composes cleanly with other patch modules no matter which
    order their module-level blocks run in."""
    if name not in _TRIPLEA_SHADER_SOURCES:
        return _SHADERLIB_GET_PRE_TRIPLEA(ctx, name)
    if name in ShaderLib._cache:
        return ShaderLib._cache[name]
    vert, frag = _TRIPLEA_SHADER_SOURCES[name]
    try:
        prog = ctx.program(vertex_shader=vert, fragment_shader=frag)
        ShaderLib._cache[name] = prog
        Log.debug(f"Shader compiled: {name}")
        return prog
    except Exception as ex:
        Log.error(f"Shader '{name}' compile failed: {ex}")
        return None


ShaderLib.get = classmethod(_shaderlib_get_triplea)


# ------------------------------------------------------- ocean CPU mirror
# Updated every frame by the injected render pass (render() has the
# scene; PhysicsWorld does not).  Read by the buoyancy hook below.
_OCEAN_STATE = {
    "enabled": False,
    "sea_level": 0.0,
    "wave_height": 0.45,
    "wave_speed": 1.0,
    "wind_dir": (1.0, 0.35),
    "wind_strength": 1.0,
    "choppiness": 0.7,
    "buoyancy_enabled": True,
    "buoyancy_strength": 1.0,
    "time": 0.0,
    "ripples": [],      # [[x, z, birth_time, strength], ...] — max 8
}

# 3-octave Gerstner constants — MUST match OCEAN_FRAG exactly so physics
# and visuals agree on where the surface is.
_OCEAN_WAVELENGTHS = (23.0, 11.0, 5.5)
_OCEAN_AMP_FRAC = (0.5, 0.3, 0.2)
_OCEAN_DIR_OFFSET = (0.0, 0.65, -0.85)


def _ocean_wave_height_at(x: float, z: float, t: float) -> float:
    """CPU mirror of OCEAN_FRAG.wave_height_at (vertical component)."""
    st = _OCEAN_STATE
    wx, wz = st["wind_dir"]
    wl = math.hypot(wx, wz)
    if wl < 1e-5:
        wx, wz = 1.0, 0.0
    else:
        wx, wz = wx / wl, wz / wl
    spd = st["wave_speed"] * (0.35 + 0.65 * st["wind_strength"])
    h = 0.0
    for wav, fr, an in zip(_OCEAN_WAVELENGTHS, _OCEAN_AMP_FRAC,
                           _OCEAN_DIR_OFFSET):
        ca, sa = math.cos(an), math.sin(an)
        dx, dz = wx * ca - wz * sa, wx * sa + wz * ca
        k = 2.0 * math.pi / wav
        om = math.sqrt(9.81 * k) * spd
        h += st["wave_height"] * fr * math.sin(k * (dx * x + dz * z)
                                               + om * t)
    return h


def _ocean_register_ripple(x: float, z: float, t: float, strength: float):
    """Track up to 8 expanding ring ripples for the ocean shader."""
    rip = _OCEAN_STATE["ripples"]
    # Merge with a fresh nearby ripple instead of spamming new rings.
    for r in rip:
        if (t - r[2]) < 0.35 and (r[0] - x) ** 2 + (r[1] - z) ** 2 < 1.0:
            r[3] = max(r[3], strength)
            return
    rip.append([x, z, t, strength])
    del rip[:-8]


def _ocean_body_radius(entry) -> float:
    """Approximate a physics body as a sphere from its collider dims."""
    col = entry.collider
    try:
        s = entry.scale
        smax = max(abs(s.x), abs(s.y), abs(s.z), 1e-4)
    except Exception:
        smax = 1.0
    if isinstance(col, SphereCollider):
        return col.radius * smax
    if isinstance(col, BoxCollider):
        return 0.5 * max(col.size.x, col.size.y, col.size.z) * smax
    if isinstance(col, CapsuleCollider):
        return max(col.radius, 0.5 * col.height) * smax
    return 0.5 * smax


# ------------------------------------------------------------- buoyancy
_PHYS_FIXED_STEP_PRE_TRIPLEA = PhysicsWorld._fixed_step
_OCEAN_BUOYANCY_WARNED = [False]


def _fixed_step_with_ocean_buoyancy(self, h: float):
    """Runtime wrap of PhysicsWorld._fixed_step (original text untouched).

    Applies Archimedes-lite buoyancy + water drag to every dynamic body
    whose position dips below the CPU-mirrored wave height, registers
    shader ripples for bodies breaking the surface, then runs the
    original step.  Any failure disables buoyancy silently (one warn)."""
    st = _OCEAN_STATE
    if st.get("enabled") and st.get("buoyancy_enabled"):
        try:
            t = float(st.get("time", 0.0))
            sea = float(st.get("sea_level", 0.0))
            strength = float(st.get("buoyancy_strength", 1.0))
            gmag = abs(self.gravity.y) if abs(self.gravity.y) > 1e-6 else 9.81
            for e in self.entries:
                if e.kind != "dynamic" or e.body is None:
                    continue
                b = e.body
                r = _ocean_body_radius(e)
                water_y = sea + _ocean_wave_height_at(b.position.x,
                                                      b.position.z, t)
                depth = water_y - b.position.y    # + → centre below surface
                if depth <= -r:
                    continue                      # fully above the waves
                sub = min(1.0, max(0.0, (depth + r) / (2.0 * r)))
                # Up to ~1.35x gravity of lift at full submersion → bodies
                # bob with ~3/4 of their volume submerged at equilibrium.
                b.add_force(glm.vec3(0.0, gmag * b.mass * strength
                                     * sub * 1.35, 0.0))
                # Water drag, stronger on the vertical component.
                lin = max(0.0, 1.0 - 1.6 * sub * h)
                ver = max(0.0, 1.0 - 2.6 * sub * h)
                b.velocity = glm.vec3(b.velocity.x * lin,
                                      b.velocity.y * ver,
                                      b.velocity.z * lin)
                speed = glm.length(b.velocity)
                if abs(depth) < r and speed > 0.5:
                    _ocean_register_ripple(b.position.x, b.position.z, t,
                                           min(1.0, speed * 0.18))
        except Exception as ex:
            if not _OCEAN_BUOYANCY_WARNED[0]:
                _OCEAN_BUOYANCY_WARNED[0] = True
                Log.warn(f"Ocean buoyancy disabled after error: {ex}")
    return _PHYS_FIXED_STEP_PRE_TRIPLEA(self, h)


PhysicsWorld._fixed_step = _fixed_step_with_ocean_buoyancy

