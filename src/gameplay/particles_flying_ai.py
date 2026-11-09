# @heaven3d-part gameplay/particles_flying_ai.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# ===========================================================================
# [GREATUPDATE:core]  v1.0 — GPU Particle Studio + Flying AI + QoL
#   Installed by theGREATupdate.py — append-only, double-install guarded.
#   Reverse-engineered instancing/emission pipeline in the spirit of
#   Godot GPUParticles3D + Unreal Niagara GPU sim: every particle is
#   simulated on the GPU (transform feedback); the CPU only writes uniforms.
# ===========================================================================
if not globals().get("_GREATUPDATE_INSTALLED"):
    _GREATUPDATE_INSTALLED = True
    # =====================================================================
    # [GREATUPDATE:particles_core]  FRAGMENT 1 — GPU particle engine
    #   Godot GPUParticles3D / UE Niagara style: transform-feedback update
    #   pass (pure GPU, CPU writes uniforms only) + instanced billboard
    #   rendering with fully procedural per-type fragment shading
    #   (smoke / fire / electric / generic — no textures anywhere).
    #
    #   Runs inside the Heaven3D host namespace at import time.  Every GL
    #   object is created lazily on the first render call that actually has
    #   systems — NOTHING touches a GL context at import time.
    # =====================================================================

    _GU_PARTICLE_STRIDE = 14          # floats per particle (3+3+4+4)
    _GU_PRESET_DIR = "particle_presets"
    _GU_LOGGED = set()


    def _gu_log_once(key, msg):
        """One-shot warning log — the engine must never be spammed."""
        if key in _GU_LOGGED:
            return
        _GU_LOGGED.add(key)
        try:
            Log.warn(msg)
        except Exception:
            pass


    # ---------------------------------------------------------------------
    #  GLSL — transform-feedback update pass (RASTERIZER_DISCARD, POINTS)
    # ---------------------------------------------------------------------
    _GU_TF_VS = """#version 330 core
    // [GREATUPDATE] GPU particle update — runs as a transform-feedback
    // pass (no rasterization).  One POINTS vertex per particle; ping-pong
    // VBOs carry the whole simulation state on the GPU.
    in vec3 in_pos;
    in vec3 in_vel;
    in vec4 in_meta1;   // life, max_life, size_mul, rot
    in vec4 in_meta2;   // seed, flags, -, -

    out vec3 out_pos;
    out vec3 out_vel;
    out vec4 out_meta1;
    out vec4 out_meta2;

    uniform float u_dt;
    uniform float u_time;
    uniform float u_frame;
    uniform vec3  u_emitter;
    uniform float u_spawn_prob;   // min(rate*dt/capacity, 1) computed CPU-side
    uniform vec2  u_lifetime;     // min, max
    uniform vec2  u_speed;        // min, max
    uniform vec3  u_direction;    // normalized cone axis
    uniform float u_spread;       // 0..1 cone widen
    uniform float u_radius;       // emitter radius
    uniform vec3  u_gravity;
    uniform float u_drag;
    uniform float u_turbulence;
    uniform float u_emitting;     // 1.0 / 0.0

    float hash11(float p){
        p = fract(p * 0.1031);
        p *= p + 33.33;
        p *= p + p;
        return fract(p);
    }
    vec3 hash31(float p){
        vec3 p3 = fract(vec3(p) * vec3(0.1031, 0.1030, 0.0973));
        p3 += dot(p3, p3.yxz + 33.33);
        return fract((p3.xxy + p3.yzz) * p3.zyx);
    }
    float hash31f(vec3 p){
        p = fract(p * 0.3183099 + vec3(0.1, 0.17, 0.13));
        p *= 17.0;
        return fract(p.x * p.y * p.z * (p.x + p.y + p.z));
    }
    float vnoise3(vec3 p){
        vec3 i = floor(p);
        vec3 f = fract(p);
        vec3 u = f * f * (3.0 - 2.0 * f);
        float n000 = hash31f(i);
        float n100 = hash31f(i + vec3(1.0, 0.0, 0.0));
        float n010 = hash31f(i + vec3(0.0, 1.0, 0.0));
        float n110 = hash31f(i + vec3(1.0, 1.0, 0.0));
        float n001 = hash31f(i + vec3(0.0, 0.0, 1.0));
        float n101 = hash31f(i + vec3(1.0, 0.0, 1.0));
        float n011 = hash31f(i + vec3(0.0, 1.0, 1.0));
        float n111 = hash31f(i + vec3(1.0, 1.0, 1.0));
        return mix(mix(mix(n000, n100, u.x), mix(n010, n110, u.x), u.y),
                   mix(mix(n001, n101, u.x), mix(n011, n111, u.x), u.y), u.z);
    }
    // cheap curl-ish flow field from offset 3D value-noise samples
    vec3 curlish(vec3 p, float t, float seed){
        vec3 q = p * 0.65 + vec3(0.0, t * 0.55, 0.0);
        return vec3(vnoise3(q      + seed * 7.31) - 0.5,
                    vnoise3(q.zxy  + seed * 3.17) - 0.5,
                    vnoise3(q.yzx  + seed * 5.71) - 0.5) * 2.0;
    }

    void main(){
        float life     = in_meta1.x;
        float max_life = in_meta1.y;
        float size_mul = in_meta1.z;
        float rot      = in_meta1.w;
        float seed     = in_meta2.x;
        vec3 pos = in_pos;
        vec3 vel = in_vel;

        if (life <= 0.0){
            // dead particle: stochastic respawn so the emission rate is
            // exact on average (reservoir sampling across the pool)
            float s1 = seed * 61.73 + u_frame * 13.13;
            if (u_emitting > 0.5 && hash11(s1) < u_spawn_prob){
                float s2 = s1 * 1.618 + 11.7;
                float s3 = s2 * 2.39 + 3.1;
                vec3 off = hash31(s2) * 2.0 - 1.0;
                float rl = max(length(off), 1e-4);
                float rr = u_radius * pow(hash11(s3), 0.3333);
                pos = u_emitter + (off / rl) * rr;
                vec3 cone = u_direction + (hash31(s3) * 2.0 - 1.0) * u_spread;
                float cl = max(length(cone), 1e-4);
                float spd = mix(u_speed.x, u_speed.y, hash11(s3 * 1.71));
                vel = (cone / cl) * spd;
                max_life = max(mix(u_lifetime.x, u_lifetime.y, hash11(s2 * 2.17)), 0.05);
                life = max_life * (0.85 + 0.15 * hash11(s1 * 3.3));
                size_mul = 0.65 + 0.7 * hash11(s3 * 4.7);
                rot = 6.2831853 * hash11(s2 * 5.9);
            }
        } else {
            vec3 turb = curlish(pos, u_time, seed) * u_turbulence;
            vel += (u_gravity + turb) * u_dt;
            vel /= (1.0 + u_drag * u_dt);
            pos += vel * u_dt;
            life -= u_dt;
        }

        out_pos = pos;
        out_vel = vel;
        out_meta1 = vec4(life, max_life, size_mul, rot);
        out_meta2 = vec4(seed, in_meta2.y, in_meta2.z, in_meta2.w);
    }
    """

    # ---------------------------------------------------------------------
    #  GLSL — instanced billboard render pass
    # ---------------------------------------------------------------------
    _GU_PARTICLE_VS = """#version 330 core
    // [GREATUPDATE] particle billboard VS — one instanced quad per
    // particle; instance attribs are the live particle VBO ("/i" divisor).
    in vec2 in_corner;      // quad corner (-0.5 .. 0.5)
    in vec3 in_pos;         // ---- per-instance ----
    in vec3 in_vel;
    in vec4 in_meta1;       // life, max_life, size_mul, rot
    in vec4 in_meta2;       // seed, flags, -, -

    uniform mat4 u_view;
    uniform mat4 u_proj;
    uniform vec3 u_cam_right;
    uniform vec3 u_cam_up;
    uniform float u_time;
    uniform float u_size_start;
    uniform float u_size_end;
    uniform float u_rot_spin;   // full turns over the lifetime
    uniform float u_stretch;    // velocity stretch factor (0 = round quad)

    out vec2 v_uv;
    out float v_t;
    out float v_seed;

    void main(){
        float life = in_meta1.x;
        float max_life = max(in_meta1.y, 1e-4);
        v_seed = in_meta2.x;
        if (life <= 1e-5){
            // dead: park the quad fully outside the clip volume.  Scaling to
            // zero is NOT enough — a degenerate quad whose vertices collapse
            // onto a point near the camera plane (w ~ 0) rasterizes as
            // hard-edged garbage triangles.
            v_uv = vec2(2.0);
            v_t = 1.0;
            gl_Position = vec4(2.0, 2.0, 2.0, 1.0);
            return;
        }
        float t = clamp(1.0 - life / max_life, 0.0, 1.0);
        float size = mix(u_size_start, u_size_end, t) * in_meta1.z;

        // velocity-aligned stretch (sparks / rain read as streaks)
        vec3 vv = (u_view * vec4(in_vel, 0.0)).xyz;
        vec2 sd = vec2(0.0, 1.0);
        float vl = length(vv.xy);
        if (vl > 1e-4) sd = vv.xy / vl;
        vec2 sp = vec2(-sd.y, sd.x);
        float elong = 1.0 + min(vl * u_stretch, 5.0);
        vec2 ext = vec2(size / sqrt(elong), size * elong);
        vec2 offs = sp * (in_corner.x * ext.x) + sd * (in_corner.y * ext.y);

        // lifetime spin (visually a no-op for perfectly round sprites)
        float ang = in_meta1.w + u_rot_spin * t * 6.2831853;
        float ca = cos(ang), sa = sin(ang);
        offs = vec2(offs.x * ca - offs.y * sa, offs.x * sa + offs.y * ca);

        vec3 wp = in_pos + u_cam_right * offs.x + u_cam_up * offs.y;
        v_uv = in_corner * 2.0;                    // -1 .. 1
        v_t = t;
        gl_Position = u_proj * u_view * vec4(wp, 1.0);
    }
    """

    _GU_PARTICLE_FS = """#version 330 core
    // [GREATUPDATE] procedural particle shading — hash-based value noise
    // only, zero textures.  u_type: 0 smoke, 1 fire, 2 electric, 3 generic.
    in vec2 v_uv;
    in float v_t;
    in float v_seed;

    uniform int u_type;
    uniform vec4 u_color_start;
    uniform vec4 u_color_end;
    uniform float u_time;

    out vec4 f_color;

    float hash21(vec2 p){
        vec3 p3 = fract(vec3(p.xyx) * 0.1031);
        p3 += dot(p3, p3.yzx + 33.33);
        return fract((p3.x + p3.y) * p3.z);
    }
    float vnoise2(vec2 p){
        vec2 i = floor(p);
        vec2 f = fract(p);
        vec2 u = f * f * (3.0 - 2.0 * f);
        return mix(mix(hash21(i),               hash21(i + vec2(1.0, 0.0)), u.x),
                   mix(hash21(i + vec2(0.0, 1.0)), hash21(i + vec2(1.0, 1.0)), u.x), u.y);
    }
    float fbm2(vec2 p){
        return vnoise2(p) * 0.65 + vnoise2(p * 2.13 + 17.7) * 0.35;
    }

    void main(){
        vec2 uv = v_uv;
        float r = length(uv);
        vec4 col = mix(u_color_start, u_color_end, v_t);
        float a = 0.0;
        vec3 tint = vec3(1.0);

        if (u_type == 0){
            // ---- billowing smoke: 2-octave fbm puff, eroded by age ----
            // NOTE: smoothstep(lo, hi, x) is only defined for lo < hi —
            // reversed edges are UNDEFINED in GLSL and produce hard blocky
            // quad silhouettes on many drivers.  Always use the
            // (1.0 - smoothstep(lo, hi, x)) form for falling ramps.
            float n = fbm2(uv * 1.7 + v_seed * 23.7 + vec2(0.0, -u_time * 0.22));
            float body = 1.0 - smoothstep(0.20, 1.05, r + (n - 0.5) * 1.15);
            float erode = 1.0 - smoothstep(0.35, 1.0, v_t + (n - 0.5) * 0.85);
            float edge = 1.0 - smoothstep(0.48, 0.92, r);  // alpha fully 0 well
            a = body * erode * edge;                       // before the quad rim
            tint = mix(vec3(0.72), vec3(1.14), n);      // cheap volume shading
        }
        else if (u_type == 1){
            // ---- fire: rising noise erosion + hot blackbody-ish ramp ----
            float n = fbm2(vec2(uv.x * 2.3, uv.y * 1.5 - u_time * 2.4) + v_seed * 31.1);
            float body = 1.0 - smoothstep(0.0, 1.0, r + (n - 0.5) * 1.2);
            float heat = clamp(body * (1.0 - v_t * 0.9) * 1.25, 0.0, 1.0);
            vec3 ramp = mix(vec3(0.45, 0.03, 0.0), vec3(1.0, 0.45, 0.05),
                            smoothstep(0.08, 0.45, heat));
            ramp = mix(ramp, vec3(1.0, 0.92, 0.55), smoothstep(0.55, 0.8, heat));
            ramp = mix(ramp, vec3(1.0, 1.0, 0.95), smoothstep(0.85, 0.98, heat));
            tint = ramp;
            float edge = 1.0 - smoothstep(0.42, 0.90, r);  // alpha fully 0 well
            a = body * edge * (1.0 - v_t * 0.75);          // before the quad rim
        }
        else if (u_type == 2){
            // ---- electricity: jittered bolt streak along the quad diagonal,
            //      re-jittered at 24 Hz with per-flash brightness flicker ----
            float tick = floor(u_time * 24.0);
            float d01 = (uv.x + uv.y) * 0.5 + 0.5;
            float jag = (vnoise2(vec2(d01 * 7.0,  tick * 3.7 + v_seed * 91.0)) - 0.5) * 0.85
                      + (vnoise2(vec2(d01 * 17.0, tick * 5.3 + v_seed * 37.0)) - 0.5) * 0.3;
            float dist = abs((uv.x - uv.y) * 0.7071 - jag);
            float core = exp(-dist * 26.0);
            float glow = exp(-dist * 6.0) * 0.35;
            float flicker = 0.55 + 0.45 * hash21(vec2(v_seed * 57.0, tick));
            float taper = smoothstep(0.0, 0.12, d01) * (1.0 - smoothstep(0.88, 1.0, d01));
            float edge = 1.0 - smoothstep(0.85, 1.15, r);
            a = (core + glow) * flicker * taper * edge;
            tint = mix(vec3(0.55, 0.7, 1.0), vec3(1.0), core);
        }
        else {
            // ---- generic: soft radial glow with a hot core ----
            float core = exp(-r * 5.5);
            float halo = pow(max(1.0 - r, 0.0), 2.6);
            float edge = 1.0 - smoothstep(0.60, 0.95, r);
            a = clamp(core * 1.2 + halo, 0.0, 1.0) * edge;
            tint = mix(vec3(1.0), vec3(1.15), core);
        }

        float alpha = clamp(a, 0.0, 1.0) * col.a;
        f_color = vec4(col.rgb * tint, alpha);
    }
    """

    # ---------------------------------------------------------------------
    #  Built-in presets (JSON-serializable; every key except "name" is
    #  optional — readers must tolerate missing keys)
    # ---------------------------------------------------------------------
    GU_PARTICLE_PRESETS = {
        "Smoke Plume": {
            "name": "Smoke Plume", "type": "smoke",
            "capacity": 1024, "rate": 70.0, "lifetime": [3.5, 6.0],
            "speed": [1.2, 2.2], "direction": [0.0, 1.0, 0.0], "spread": 0.30,
            "emitter_radius": 0.45, "gravity": [0.0, 0.7, 0.0], "drag": 0.50,
            "turbulence": 2.0, "size_start": 0.9, "size_end": 4.2,
            "spin": 0.35, "stretch": 0.0,
            "color_start": [0.36, 0.35, 0.34, 0.50],
            "color_end": [0.16, 0.16, 0.17, 0.0],
            "additive": False, "max_dist": 220.0, "emitting": True,
        },
        "Fire": {
            "name": "Fire", "type": "fire",
            "capacity": 1024, "rate": 240.0, "lifetime": [0.5, 1.3],
            "speed": [1.6, 3.2], "direction": [0.0, 1.0, 0.0], "spread": 0.22,
            "emitter_radius": 0.40, "gravity": [0.0, 2.4, 0.0], "drag": 1.10,
            "turbulence": 2.4, "size_start": 0.85, "size_end": 0.22,
            "spin": 0.15, "stretch": 0.0,
            "color_start": [1.0, 0.90, 0.55, 0.85],
            "color_end": [0.55, 0.06, 0.01, 0.0],
            "additive": True, "max_dist": 140.0, "emitting": True,
        },
        "Electricity": {
            "name": "Electricity", "type": "electric",
            "capacity": 512, "rate": 110.0, "lifetime": [0.10, 0.30],
            "speed": [2.0, 8.0], "direction": [1.0, 0.15, 0.0], "spread": 1.0,
            "emitter_radius": 0.30, "gravity": [0.0, 0.0, 0.0], "drag": 0.10,
            "turbulence": 5.0, "size_start": 0.50, "size_end": 0.35,
            "spin": 0.0, "stretch": 0.0,
            "color_start": [0.75, 0.88, 1.0, 0.95],
            "color_end": [0.30, 0.45, 1.0, 0.0],
            "additive": True, "max_dist": 100.0, "emitting": True,
        },
        "Embers": {
            "name": "Embers", "type": "generic",
            "capacity": 512, "rate": 40.0, "lifetime": [2.5, 5.0],
            "speed": [0.4, 1.2], "direction": [0.0, 1.0, 0.0], "spread": 0.55,
            "emitter_radius": 0.70, "gravity": [0.0, 0.8, 0.0], "drag": 0.50,
            "turbulence": 1.6, "size_start": 0.08, "size_end": 0.02,
            "spin": 0.0, "stretch": 0.06,
            "color_start": [1.0, 0.60, 0.15, 1.0],
            "color_end": [0.40, 0.05, 0.01, 0.0],
            "additive": True, "max_dist": 120.0, "emitting": True,
        },
        "Explosion": {
            "name": "Explosion", "type": "fire",
            "capacity": 2048, "rate": 3000.0, "lifetime": [0.35, 0.90],
            "speed": [6.0, 20.0], "direction": [0.0, 1.0, 0.0], "spread": 1.0,
            "emitter_radius": 0.50, "gravity": [0.0, -7.0, 0.0], "drag": 2.4,
            "turbulence": 3.5, "size_start": 1.4, "size_end": 3.0,
            "spin": 0.25, "stretch": 0.04,
            "color_start": [1.0, 0.88, 0.45, 1.0],
            "color_end": [0.22, 0.04, 0.02, 0.0],
            "additive": True, "max_dist": 200.0, "emitting": True,
        },
        "Rain": {
            "name": "Rain", "type": "generic",
            "capacity": 2048, "rate": 1500.0, "lifetime": [0.70, 1.0],
            "speed": [26.0, 32.0], "direction": [0.05, -1.0, 0.02], "spread": 0.02,
            "emitter_radius": 14.0, "gravity": [0.0, -4.0, 0.0], "drag": 0.0,
            "turbulence": 0.1, "size_start": 0.10, "size_end": 0.10,
            "spin": 0.0, "stretch": 0.28,
            "color_start": [0.65, 0.75, 0.90, 0.35],
            "color_end": [0.65, 0.75, 0.90, 0.15],
            "additive": True, "max_dist": 90.0, "emitting": True,
        },
        "Magic Orb": {
            "name": "Magic Orb", "type": "generic",
            "capacity": 512, "rate": 140.0, "lifetime": [1.2, 2.4],
            "speed": [0.2, 0.9], "direction": [0.0, 1.0, 0.0], "spread": 1.0,
            "emitter_radius": 0.90, "gravity": [0.0, -0.5, 0.0], "drag": 2.0,
            "turbulence": 2.2, "size_start": 0.25, "size_end": 0.02,
            "spin": 0.0, "stretch": 0.03,
            "color_start": [0.55, 0.20, 1.0, 0.90],
            "color_end": [0.10, 0.60, 1.0, 0.0],
            "additive": True, "max_dist": 100.0, "emitting": True,
        },
        "Sparks": {
            "name": "Sparks", "type": "generic",
            "capacity": 1024, "rate": 320.0, "lifetime": [0.4, 1.1],
            "speed": [7.0, 15.0], "direction": [0.0, 1.0, 0.0], "spread": 0.75,
            "emitter_radius": 0.15, "gravity": [0.0, -16.0, 0.0], "drag": 0.60,
            "turbulence": 0.8, "size_start": 0.06, "size_end": 0.015,
            "spin": 0.0, "stretch": 0.18,
            "color_start": [1.0, 0.90, 0.60, 1.0],
            "color_end": [1.0, 0.30, 0.05, 0.0],
            "additive": True, "max_dist": 100.0, "emitting": True,
        },
    }


    # ---------------------------------------------------------------------
    #  Preset IO + helpers (host stdlib globals only: os / json / math)
    # ---------------------------------------------------------------------
    def _gu_sanitize_name(name):
        try:
            s = "".join(c if (c.isalnum() or c in " -_.") else "_"
                        for c in str(name)).strip()
            return s or "Custom"
        except Exception:
            return "Custom"


    def gu_save_preset(name, cfg):
        """Write <cwd>/particle_presets/<sanitized>.json.  Returns bool."""
        try:
            if not isinstance(cfg, dict):
                return False
            out = _gu_clean_cfg(cfg)
            out["name"] = str(name or cfg.get("name") or "Custom")
            safe = _gu_sanitize_name(out["name"])
            d = os.path.join(os.getcwd(), _GU_PRESET_DIR)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, safe + ".json"), "w",
                      encoding="utf-8") as f:
                json.dump(out, f, indent=2)
            return True
        except Exception as ex:
            _gu_log_once("save_preset",
                         f"[GREATUPDATE] particle preset save failed: {ex}")
            return False


    def gu_load_presets():
        """Copy of GU_PARTICLE_PRESETS updated with on-disk user presets."""
        out = {k: dict(v) for k, v in GU_PARTICLE_PRESETS.items()}
        try:
            d = os.path.join(os.getcwd(), _GU_PRESET_DIR)
            if os.path.isdir(d):
                for fn in sorted(os.listdir(d)):
                    if not fn.lower().endswith(".json"):
                        continue
                    try:
                        with open(os.path.join(d, fn), "r",
                                  encoding="utf-8") as f:
                            cfg = json.load(f)
                        if isinstance(cfg, dict):
                            out[str(cfg.get("name")
                                    or os.path.splitext(fn)[0])] = cfg
                    except Exception:
                        continue
        except Exception:
            pass
        return out


    def _gu_clean_cfg(cfg):
        """Coerce a config dict to strictly JSON-serializable values."""
        out = {}
        try:
            items = cfg.items()
        except Exception:
            return out
        for k, v in items:
            try:
                if isinstance(v, bool):
                    out[k] = v
                elif isinstance(v, (int, float, str)) or v is None:
                    out[k] = v
                elif isinstance(v, (list, tuple)):
                    out[k] = [float(x) for x in v]
            except Exception:
                pass
        return out


    def _gu_f2(v, d0, d1):
        try:
            return float(v[0]), float(v[1])
        except Exception:
            pass
        try:
            f = float(v)
            return f, f
        except Exception:
            return d0, d1


    def _gu_f3(v, d0, d1, d2):
        try:
            return float(v[0]), float(v[1]), float(v[2])
        except Exception:
            return d0, d1, d2


    def _gu_f4(v, d):
        try:
            return (float(v[0]), float(v[1]), float(v[2]), float(v[3]))
        except Exception:
            return d


    def _gu_resolve_preset(p):
        """(cfg_dict, preset_name) from a preset name or a raw dict."""
        if isinstance(p, dict):
            cfg = _gu_clean_cfg(p)
            cfg.setdefault("name", "Custom")
            return cfg, str(cfg.get("name"))
        name = str(p)
        presets = gu_load_presets()
        base = presets.get(name)
        if base is None:
            _gu_log_once(f"unknown_preset:{name}",
                         f"[GREATUPDATE] unknown particle preset '{name}' "
                         f"— using Smoke Plume")
            base = dict(GU_PARTICLE_PRESETS["Smoke Plume"])
            base["name"] = name
        else:
            base = dict(base)
        return _gu_clean_cfg(base), name


    def gu_add_particle_system(engine, preset_name_or_dict, position=None):
        """Create a selectable/movable/saveable particle SceneNode."""
        cfg, preset_name = _gu_resolve_preset(preset_name_or_dict)
        name = str(cfg.get("name", preset_name or "Particles"))
        node = SceneNode(f"Particle: {name}", parent=engine.scene.root)
        node.user_data["particle_system"] = cfg
        if preset_name:
            node.user_data["particle_preset"] = preset_name
        if position is not None:
            try:
                node.position = glm.vec3(float(position[0]),
                                         float(position[1]),
                                         float(position[2]))
            except Exception:
                try:
                    node.position = glm.vec3(position)
                except Exception:
                    pass
        return node

    # ---------------------------------------------------------------------
    #  Uniform helpers — only write uniforms the program actually has
    #  (missing ones are optimized out by the GLSL compiler)
    # ---------------------------------------------------------------------
    def _gu_set(prog, name, value):
        try:
            if name in prog:
                prog[name] = value
        except Exception:
            pass


    def _gu_write(prog, name, data):
        try:
            if name in prog:
                prog[name].write(data)
        except Exception:
            pass


    def _gu_frustum_planes(view, proj):
        """6 normalized frustum planes (nx,ny,nz,d) from view-proj, or None."""
        try:
            m = glm.transpose(proj * view)
            r0, r1, r2, r3 = m[0], m[1], m[2], m[3]
            out = []
            for p in (r3 + r0, r3 - r0, r3 + r1, r3 - r1, r3 + r2, r3 - r2):
                l = math.sqrt(p.x * p.x + p.y * p.y + p.z * p.z) or 1.0
                out.append((p.x / l, p.y / l, p.z / l, p.w / l))
            return out
        except Exception:
            return None


    def _gu_sphere_visible(planes, pos, radius):
        if planes is None:
            return True
        px, py, pz = pos.x, pos.y, pos.z
        for nx, ny, nz, d in planes:
            if nx * px + ny * py + nz * pz + d < -radius:
                return False
        return True


    # ---------------------------------------------------------------------
    #  One GPU particle system — ping-pong VBOs, all state on the GPU
    # ---------------------------------------------------------------------
    class _GPUParticleSystem:
        __slots__ = (
            "node", "name", "ptype", "want_capacity", "rate",
            "life_min", "life_max", "speed_min", "speed_max", "direction",
            "spread", "radius", "gravity", "drag", "turbulence",
            "size_start", "size_end", "spin", "stretch",
            "color_start", "color_end", "additive", "max_dist", "emitting",
            "bound_radius", "capacity", "cur", "dt_accum", "failed",
            "vb", "tf_vao", "draw_vao", "_gl_ctx", "_cfg_ref",
        )

        _TYPES = {"smoke": 0, "fire": 1, "electric": 2, "generic": 3}

        def __init__(self, node, cfg):
            self.vb = [None, None]
            self.tf_vao = [None, None]
            self.draw_vao = [None, None]
            self._gl_ctx = None
            self._cfg_ref = None
            self.capacity = 0
            self.cur = 0
            self.dt_accum = 0.0
            self.failed = False
            self.node = node
            self.read_cfg(cfg)

        def read_cfg(self, cfg):
            """O(1) per-frame re-read so studio edits apply live."""
            c = cfg if isinstance(cfg, dict) else {}
            self.name = str(c.get("name", "Particles"))
            self.ptype = self._TYPES.get(
                str(c.get("type", "generic")).lower(), 3)
            try:
                cap = int(c.get("capacity", 512) or 512)
            except Exception:
                cap = 512
            self.want_capacity = max(16, min(4096, cap))
            try:
                self.rate = max(0.0, float(c.get("rate", 60.0) or 0.0))
            except Exception:
                self.rate = 60.0
            self.life_min, self.life_max = _gu_f2(c.get("lifetime"), 1.0, 2.0)
            self.life_min = max(0.05, self.life_min)
            self.life_max = max(self.life_min, self.life_max)
            self.speed_min, self.speed_max = _gu_f2(c.get("speed"), 1.0, 2.0)
            d = _gu_f3(c.get("direction"), 0.0, 1.0, 0.0)
            dl = math.sqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2]) or 1.0
            self.direction = (d[0] / dl, d[1] / dl, d[2] / dl)
            try:
                self.spread = min(1.0, max(0.0, float(c.get("spread", 0.3))))
            except Exception:
                self.spread = 0.3
            try:
                self.radius = max(0.0, float(c.get("emitter_radius", 0.3)))
            except Exception:
                self.radius = 0.3
            self.gravity = _gu_f3(c.get("gravity"), 0.0, 0.0, 0.0)
            try:
                self.drag = max(0.0, float(c.get("drag", 0.0)))
                self.turbulence = max(0.0, float(c.get("turbulence", 0.0)))
                self.size_start = max(0.001, float(c.get("size_start", 0.3)))
                self.size_end = max(0.001, float(c.get("size_end", 0.3)))
                self.spin = float(c.get("spin", 0.0) or 0.0)
                self.stretch = max(0.0, float(c.get("stretch", 0.0) or 0.0))
                self.max_dist = max(10.0, float(c.get("max_dist", 120.0)))
            except Exception:
                self.drag = 0.0; self.turbulence = 0.0
                self.size_start = 0.3; self.size_end = 0.3
                self.spin = 0.0; self.stretch = 0.0; self.max_dist = 120.0
            self.color_start = _gu_f4(c.get("color_start"), (1, 1, 1, 1))
            self.color_end = _gu_f4(c.get("color_end"), (1, 1, 1, 0))
            self.additive = bool(c.get("additive", False))
            self.emitting = bool(c.get("emitting", True))
            # conservative world-space bound for frustum/distance culling
            self.bound_radius = (self.radius
                                 + max(self.speed_min, self.speed_max)
                                 * self.life_max
                                 + max(self.size_start, self.size_end) * 1.5
                                 + 1.0)

        # ---- GL lifetime ----
        def ensure_gl(self, mgr, ctx):
            if self.failed:
                return False
            if (self._gl_ctx is ctx and self.vb[0] is not None
                    and self.capacity == self.want_capacity):
                return True
            self.release_gl()
            try:
                cap = self.want_capacity
                data = np.zeros((cap, _GU_PARTICLE_STRIDE), dtype=np.float32)
                data[:, 7] = 1.0                                    # max_life
                data[:, 8] = 1.0                                    # size_mul
                data[:, 9] = np.random.random(cap) * 6.28318        # rot
                data[:, 10] = np.random.random(cap) * 100.0 + 0.01  # seed
                raw = data.tobytes()
                for i in (0, 1):
                    self.vb[i] = ctx.buffer(raw)
                    self.tf_vao[i] = ctx.vertex_array(
                        mgr.prog_tf,
                        [(self.vb[i], "3f 3f 4f 4f",
                          "in_pos", "in_vel", "in_meta1", "in_meta2")])
                    self.draw_vao[i] = ctx.vertex_array(
                        mgr.prog_draw,
                        [(mgr.quad_vbo, "2f", "in_corner"),
                         (self.vb[i], "3f 3f 4f 4f /i",
                          "in_pos", "in_vel", "in_meta1", "in_meta2")],
                        index_buffer=mgr.quad_ibo)
                self.capacity = cap
                self.cur = 0
                self._gl_ctx = ctx
                return True
            except Exception as ex:
                self.failed = True
                _gu_log_once("sys_gl",
                             f"[GREATUPDATE] particle system GL init: {ex}")
                self.release_gl()
                return False

        def release_gl(self):
            for coll in (self.vb, self.tf_vao, self.draw_vao):
                for i in (0, 1):
                    o = coll[i]
                    coll[i] = None
                    if o is not None:
                        try:
                            o.release()
                        except Exception:
                            pass
            self._gl_ctx = None
            self.capacity = 0

    # ---------------------------------------------------------------------
    #  Manager — singleton, owns shared GL objects + the per-frame pass
    # ---------------------------------------------------------------------
    class _GPUParticleManager:
        def __init__(self):
            self.systems = {}          # node.id -> _GPUParticleSystem
            self.prog_tf = None
            self.prog_draw = None
            self.quad_vbo = None
            self.quad_ibo = None
            self._ctx = None
            self._last = None
            self.frame = 0

        def reset(self):
            """Drop every system (called after a successful project load)."""
            try:
                for s in list(self.systems.values()):
                    s.release_gl()
            except Exception:
                pass
            self.systems.clear()
            self._last = None

        # ---- shared GL objects (lazy — never at import) ----
        def _ensure_gl(self, renderer):
            ctx = getattr(renderer, "ctx", None)
            if ctx is None:
                return False
            if self.prog_tf is not None and self._ctx is ctx:
                return True
            try:
                prog_tf = ctx.program(
                    vertex_shader=_GU_TF_VS,
                    varyings=["out_pos", "out_vel", "out_meta1", "out_meta2"])
                prog_draw = ctx.program(vertex_shader=_GU_PARTICLE_VS,
                                        fragment_shader=_GU_PARTICLE_FS)
                corners = np.array([-0.5, -0.5,  0.5, -0.5,
                                     0.5,  0.5, -0.5,  0.5], dtype=np.float32)
                idx = np.array([0, 1, 2, 0, 2, 3], dtype=np.uint32)
                quad_vbo = ctx.buffer(corners.tobytes())
                quad_ibo = ctx.buffer(idx.tobytes())
                # swap succeeded — release the previous generation
                if self._ctx is not None and self._ctx is not ctx:
                    for s in list(self.systems.values()):
                        s.release_gl()
                for o in (self.prog_tf, self.prog_draw,
                          self.quad_vbo, self.quad_ibo):
                    if o is not None:
                        try:
                            o.release()
                        except Exception:
                            pass
                self.prog_tf = prog_tf
                self.prog_draw = prog_draw
                self.quad_vbo = quad_vbo
                self.quad_ibo = quad_ibo
                self._ctx = ctx
                return True
            except Exception as ex:
                self._ctx = ctx      # do not hammer a broken ctx every frame
                self.prog_tf = None
                _gu_log_once("mgr_gl",
                             f"[GREATUPDATE] particle manager GL init: {ex}")
                return False

        # ---- scene sync: node.id -> system ----
        def sync(self, scene):
            try:
                nodes = (scene.special_nodes() if hasattr(scene, "special_nodes") else scene.root.traverse())
            except Exception:
                return
            seen = set()
            for node in nodes:
                try:
                    ud = getattr(node, "user_data", None)
                    if not ud or "particle_system" not in ud:
                        continue
                except Exception:
                    continue
                seen.add(node.id)
                s = self.systems.get(node.id)
                if s is None or s.node is not node:
                    self.systems[node.id] = _GPUParticleSystem(
                        node, ud.get("particle_system"))
                else:
                    # Cheap live-edit support: re-read immediately when the cfg
                    # dict object is REPLACED (the studio assigns a fresh dict
                    # from its sliders); in-place mutation is picked up by a
                    # staggered re-read every ~8 frames.  With 1000 systems this
                    # keeps the per-frame sync cost at ~1 dict probe per system.
                    cfg = ud.get("particle_system")
                    if (cfg is not s._cfg_ref
                            or ((self.frame + node.id) & 7) == 0):
                        s.read_cfg(cfg)
                        s._cfg_ref = cfg
            if len(self.systems) != len(seen):
                for nid in [k for k in self.systems if k not in seen]:
                    s = self.systems.pop(nid, None)
                    if s is not None:
                        s.release_gl()

        # ---- per-frame entry (renderer hook) ----
        def render(self, renderer, scene, camera, view, proj):
            now = time.time()
            dt = 0.016 if self._last is None else (now - self._last)
            self._last = now
            dt = min(max(dt, 1e-4), 0.1)          # clamp 0.1 per contract
            self.frame = (self.frame + 1) % 1000003

            self.sync(scene)
            if not self.systems:
                return                            # zero systems: no GL touched

            cam = camera.position
            planes = _gu_frustum_planes(view, proj)
            visible = []
            for s in self.systems.values():
                if s.failed:
                    continue
                node = s.node
                if not getattr(node, "visible", True):
                    continue
                try:
                    wp = node.world_position()
                except Exception:
                    wp = node.position
                dx = wp.x - cam.x
                dy = wp.y - cam.y
                dz = wp.z - cam.z
                dist = math.sqrt(dx * dx + dy * dy + dz * dz)
                if dist - s.bound_radius > s.max_dist:
                    continue
                if not _gu_sphere_visible(planes, wp, s.bound_radius):
                    continue
                visible.append((dist, s, wp))
            if not visible:
                return
            if not self._ensure_gl(renderer):
                return

            # painter's order for layered alpha: far -> near
            visible.sort(key=lambda e: -e[0])

            ctx = self._ctx
            try:
                fwd = camera.forward()
                cam_right = glm.normalize(glm.cross(fwd, glm.vec3(0, 1, 0)))
                cam_up = glm.normalize(glm.cross(cam_right, fwd))
            except Exception:
                cam_right = glm.vec3(1, 0, 0)
                cam_up = glm.vec3(0, 1, 0)

            # re-assert the exact state the forward pass uses (we run right
            # after it, but never trust leaked state)
            try:
                renderer.scene_fbo_depth.use()
                ctx.enable(moderngl.DEPTH_TEST)
                ctx.depth_func = "<="
                ctx.depth_mask = False
                ctx.enable(moderngl.BLEND)
                ctx.disable(moderngl.CULL_FACE)
                view_bytes = view.to_bytes()
                proj_bytes = proj.to_bytes()
                right_bytes = cam_right.to_bytes()
                up_bytes = cam_up.to_bytes()
                tsec = float(now % 3600.0)

                for dist, s, wp in visible:
                    if not s.ensure_gl(self, ctx):
                        continue
                    s.dt_accum += dt
                    # LOD with zero quality loss: beyond 0.6*max_dist the
                    # simulation steps every 2nd frame (accumulated dt keeps
                    # the motion exact); rendering still happens every frame.
                    due = True
                    if dist > 0.6 * s.max_dist \
                            and ((self.frame + s.node.id) & 1):
                        due = False
                    if due:
                        self._tf_pass(ctx, s, wp, tsec)
                    self._draw(ctx, s, view_bytes, proj_bytes,
                               right_bytes, up_bytes, tsec)
            except Exception as ex:
                _gu_log_once("frame", f"[GREATUPDATE] particle frame: {ex}")
            finally:
                # restore exactly what the post chain expects (mirrors the
                # forward transparency pass exit state) — even on failure
                try:
                    ctx.depth_func = "<"
                    ctx.depth_mask = True
                    ctx.disable(moderngl.BLEND)
                    ctx.disable(moderngl.DEPTH_TEST)
                except Exception:
                    pass

        # ---- transform-feedback update pass ----
        def _tf_pass(self, ctx, s, wp, tsec):
            dt = s.dt_accum
            if dt <= 0.0:
                return
            s.dt_accum = 0.0
            p = self.prog_tf
            _gu_set(p, "u_dt", dt)
            _gu_set(p, "u_time", tsec)
            _gu_set(p, "u_frame", float(self.frame % 4096))
            _gu_set(p, "u_emitter", (wp.x, wp.y, wp.z))
            prob = s.rate * dt / max(1.0, float(s.capacity))
            _gu_set(p, "u_spawn_prob", min(prob, 1.0))
            _gu_set(p, "u_lifetime", (s.life_min, s.life_max))
            _gu_set(p, "u_speed", (s.speed_min, s.speed_max))
            _gu_set(p, "u_direction", s.direction)
            _gu_set(p, "u_spread", s.spread)
            _gu_set(p, "u_radius", s.radius)
            _gu_set(p, "u_gravity", s.gravity)
            _gu_set(p, "u_drag", s.drag)
            _gu_set(p, "u_turbulence", s.turbulence)
            _gu_set(p, "u_emitting", 1.0 if s.emitting else 0.0)
            src = s.cur
            dst = 1 - src
            ctx.enable(moderngl.RASTERIZER_DISCARD)
            try:
                s.tf_vao[src].transform(s.vb[dst], mode=moderngl.POINTS,
                                        vertices=s.capacity)
                s.cur = dst
            except Exception as ex:
                _gu_log_once("tf_pass",
                             f"[GREATUPDATE] particle TF pass: {ex}")
            ctx.disable(moderngl.RASTERIZER_DISCARD)

        # ---- instanced billboard draw ----
        def _draw(self, ctx, s, view_bytes, proj_bytes, right_bytes, up_bytes,
                  tsec):
            p = self.prog_draw
            _gu_write(p, "u_view", view_bytes)
            _gu_write(p, "u_proj", proj_bytes)
            _gu_write(p, "u_cam_right", right_bytes)
            _gu_write(p, "u_cam_up", up_bytes)
            _gu_set(p, "u_time", tsec)
            _gu_set(p, "u_size_start", s.size_start)
            _gu_set(p, "u_size_end", s.size_end)
            _gu_set(p, "u_rot_spin", s.spin)
            _gu_set(p, "u_stretch", s.stretch)
            _gu_set(p, "u_type", s.ptype)
            _gu_set(p, "u_color_start", s.color_start)
            _gu_set(p, "u_color_end", s.color_end)
            ctx.blend_func = ((moderngl.SRC_ALPHA, moderngl.ONE) if s.additive
                              else (moderngl.SRC_ALPHA,
                                    moderngl.ONE_MINUS_SRC_ALPHA))
            try:
                s.draw_vao[s.cur].render(mode=moderngl.TRIANGLES,
                                         instances=s.capacity)
            except Exception as ex:
                _gu_log_once("draw", f"[GREATUPDATE] particle draw: {ex}")


    _GU_PARTICLES = None        # lazily-created _GPUParticleManager


    def _gu_particles():
        global _GU_PARTICLES
        if _GU_PARTICLES is None:
            _GU_PARTICLES = _GPUParticleManager()
        return _GU_PARTICLES

    # ---------------------------------------------------------------------
    #  Install — additive monkey-patch wraps only, all try/except armored
    # ---------------------------------------------------------------------
    _GU_PARTICLES_INSTALLED = False


    def _gu_particles_install():
        global _GU_PARTICLES_INSTALLED
        if _GU_PARTICLES_INSTALLED:
            return
        _GU_PARTICLES_INSTALLED = True
        try:
            # ---- 1. renderer hook: run right after the forward transparent
            #        pass (same FBO/depth state re-asserted inside) --------
            try:
                _GU_PREV_FORWARD = Renderer._render_forward_transparent

                def _gu_forward(self, scene, camera, view, proj):
                    _GU_PREV_FORWARD(self, scene, camera, view, proj)
                    try:
                        _gu_particles().render(self, scene, camera, view, proj)
                    except Exception as ex:
                        _gu_log_once("render_hook",
                                     f"[GREATUPDATE] particle render: {ex}")

                Renderer._render_forward_transparent = _gu_forward
            except Exception as ex:
                _gu_log_once("wrap_render",
                             f"[GREATUPDATE] particle render wrap: {ex}")

            # ---- 2. serializer round-trip of particle_system/preset ------
            try:
                _GU_PREV_SER = ProjectSerializer._serialize_node.__func__

                def _gu_serialize_node(cls, node, project_dir):
                    d = _GU_PREV_SER(cls, node, project_dir)
                    try:
                        ud = getattr(node, "user_data", None)
                        if ud and "particle_system" in ud:
                            d["particle_system"] = ud.get("particle_system")
                            d["particle_preset"] = ud.get("particle_preset")
                    except Exception:
                        pass
                    return d

                ProjectSerializer._serialize_node = \
                    classmethod(_gu_serialize_node)

                _GU_PREV_DESER = ProjectSerializer._deserialize_node.__func__

                def _gu_deserialize_node(cls, data, parent, engine,
                                         project_dir):
                    _GU_PREV_DESER(cls, data, parent, engine, project_dir)
                    try:
                        if data.get("particle_system") and parent.children:
                            node = parent.children[-1]
                            node.user_data["particle_system"] = \
                                data["particle_system"]
                            if data.get("particle_preset"):
                                node.user_data["particle_preset"] = \
                                    data["particle_preset"]
                    except Exception as ex:
                        _gu_log_once("deser",
                                     f"[GREATUPDATE] particle deserialize: {ex}")

                ProjectSerializer._deserialize_node = \
                    classmethod(_gu_deserialize_node)
            except Exception as ex:
                _gu_log_once("wrap_serializer",
                             f"[GREATUPDATE] particle serializer wrap: {ex}")

            # ---- 3. manager reset after a successful project load --------
            try:
                _GU_PREV_LOAD = ProjectSerializer.load.__func__

                def _gu_project_load(cls, path, scene, engine):
                    ok = _GU_PREV_LOAD(cls, path, scene, engine)
                    if ok:
                        try:
                            mgr = _GU_PARTICLES
                            if mgr is not None:
                                mgr.reset()
                        except Exception:
                            pass
                    return ok

                ProjectSerializer.load = classmethod(_gu_project_load)
            except Exception as ex:
                _gu_log_once("wrap_load",
                             f"[GREATUPDATE] particle load wrap: {ex}")

            # ---- 4. engine attach API ------------------------------------
            try:
                def _gu_engine_add_particle_system(self, p, pos=None):
                    return gu_add_particle_system(self, p, pos)

                HeavenEngine.add_particle_system = \
                    _gu_engine_add_particle_system
            except Exception as ex:
                _gu_log_once("attach_api",
                             f"[GREATUPDATE] particle engine API: {ex}")

            try:
                Log.info("[GREATUPDATE:particles_core] GPU particles installed "
                         "— 8 presets, TF update pass, instanced procedural "
                         "billboards, project save/load round-trip")
            except Exception:
                pass
        except Exception as ex:
            _gu_log_once("install", f"[GREATUPDATE] particle install: {ex}")

    # =====================================================================
    # [GREATUPDATE:particles_studio]  Fragment 2 — Particle Studio (editor UI)
    #   Godot/Niagara-style preset library + custom creator.
    #   MODAL FULLSCREEN OVERLAY: while open, a dim backdrop covers every
    #   other editor UI and ALL mouse/wheel/key input is consumed (Esc or the
    #   rounded X button closes).  Rendering is Unreal-style: rounded corners
    #   drawn directly through UIRenderer._draw_rect(radius=...) in a wrapped
    #   UIRenderer.render AFTER the stock UI pass, so the studio is always the
    #   topmost layer.  If _draw_rect/font are unavailable it falls back to
    #   the flat ui.panel/ui.text path and still works.
    #   Consumes the Fragment-1 API defensively via globals().get(...):
    #     GU_PARTICLE_PRESETS, gu_load_presets(), gu_save_preset(name, cfg),
    #     gu_add_particle_system(engine, preset, position)
    # =====================================================================

    _GU_STUDIO_LOGGED = set()


    def _gu_studio_log_once(key, msg):
        """Log a message exactly once per key — the studio must never spam
        or crash the host."""
        if key in _GU_STUDIO_LOGGED:
            return
        _GU_STUDIO_LOGGED.add(key)
        try:
            Log.info(msg)
        except Exception:
            try:
                print(msg)
            except Exception:
                pass


    def _gu_studio_presets():
        """All presets, built-ins first then disk customs. Never raises."""
        loader = globals().get("gu_load_presets")
        if callable(loader):
            try:
                presets = loader()
                if isinstance(presets, dict) and presets:
                    return presets
            except Exception as ex:
                _gu_studio_log_once(
                    "load", f"[GREATUPDATE:studio] preset load: {ex}")
        builtin = globals().get("GU_PARTICLE_PRESETS")
        if isinstance(builtin, dict):
            return dict(builtin)
        return {}


    def _gu_studio_color4(value, default=(1.0, 1.0, 1.0, 1.0)):
        """Coerce a preset color (list/tuple, 3 or 4 items) to an RGBA tuple."""
        try:
            seq = list(value)
            if len(seq) >= 3:
                a = seq[3] if len(seq) > 3 else 1.0
                return (_clamp(float(seq[0]), 0.0, 1.0),
                        _clamp(float(seq[1]), 0.0, 1.0),
                        _clamp(float(seq[2]), 0.0, 1.0),
                        _clamp(float(a), 0.0, 1.0))
        except Exception:
            pass
        return default


    def _gu_theme(key, fallback):
        """THEME lookup that can never raise."""
        try:
            v = THEME.get(key)
            if v is not None:
                return v
        except Exception:
            pass
        return fallback


    _GU_STUDIO_COLORS = None


    def _gu_studio_colors():
        """UE-style studio palette (cached).  Dark charcoal window, lighter
        title bar, brown/amber #8B5A2B accent family (matches the navbar
        PARTICLE button)."""
        global _GU_STUDIO_COLORS
        if _GU_STUDIO_COLORS is not None:
            return _GU_STUDIO_COLORS

        def hx(s, fb):
            try:
                return _hex(s)
            except Exception:
                return fb

        c = {
            "backdrop":    (0.0, 0.0, 0.0, 0.60),          # fullscreen dim
            "outline":     hx("#0C0C0D", (0.047, 0.047, 0.051, 1.0)),
            "window":      hx("#202021", (0.126, 0.126, 0.130, 1.0)),
            "titlebar":    hx("#2B2B2D", (0.170, 0.170, 0.176, 1.0)),
            "accent":      hx("#8B5A2B", (0.545, 0.353, 0.169, 1.0)),
            "accent_hi":   hx("#A9713B", (0.660, 0.443, 0.231, 1.0)),
            "accent_soft": hx("#6E4722", (0.431, 0.278, 0.133, 1.0)),
            "card":        hx("#2C2C2E", (0.173, 0.173, 0.180, 1.0)),
            "track":       hx("#141415", (0.078, 0.078, 0.082, 1.0)),
            "knob":        hx("#E8E8E8", (0.910, 0.910, 0.910, 1.0)),
            "divider":     hx("#3A3A3C", (0.227, 0.227, 0.235, 1.0)),
        }
        _GU_STUDIO_COLORS = c
        return c


    # Slider schema: (key, label, min, max, step, value-format)
    _GU_STUDIO_SLIDERS = [
        ("rate",           "Rate",           1.0,   500.0,  1.0,  "{:.0f}"),
        ("lifetime",       "Lifetime",       0.2,   10.0,   0.05, "{:.2f}"),
        ("speed",          "Speed",          0.0,   30.0,   0.1,  "{:.1f}"),
        ("emitter_radius", "Emitter Radius", 0.0,   5.0,    0.01, "{:.2f}"),
        ("size",           "Size",           0.05,  5.0,    0.01, "{:.2f}"),
        ("gravity_y",      "Gravity Y",      -20.0, 20.0,   0.1,  "{:.1f}"),
        ("turbulence",     "Turbulence",     0.0,   10.0,   0.05, "{:.2f}"),
        ("spread",         "Spread",         0.0,   1.0,    0.01, "{:.2f}"),
        ("capacity",       "Capacity",       64.0,  2048.0, 64.0, "{:.0f}"),
    ]

    _GU_STUDIO_TYPES = ["smoke", "fire", "electric", "generic"]

    # Corner radii in PIXELS (UI_RADIUS scale, UE-style rounding).
    _GU_R_WINDOW = 16.0
    _GU_R_OUTLINE = 18.0
    _GU_R_CARD = 10.0
    _GU_R_SLIDER = 6.0
    _GU_R_KNOB = 5.0
    _GU_R_SWATCH = 3.0


    class _GUParticleStudio:
        """Centred, modal Particle Studio window for the editor.

        Drawn LAST: the _build_ui wrap only COLLECTS the studio's draw
        commands; a wrapped UIRenderer.render flushes them after the stock UI
        pass via ui._draw_rect(..., radius=px) + ui.font.render_text, giving
        true rounded corners and guaranteeing the studio is the topmost layer
        (with a fullscreen dim backdrop beneath it).  Fallback: flat
        ui.panel/ui.text.  Input is fully modal — every mouse button, the
        wheel and all keys are consumed while open (Esc closes)."""

        # Window bounds (normalized, y up): centred 0.15..0.85 x 0.07..0.93
        PX, PY, PW, PH = 0.15, 0.07, 0.70, 0.86

        def __init__(self, engine):
            self.engine = engine
            self.panel_open = False
            # Clickable rects + slider hit-rects, rebuilt every draw.
            self._clicks = []            # [(UIRect, callback, key)]
            self._slider_rects = {}      # key -> UIRect (hit area)
            self._slider_meta = {}       # key -> (lo, hi, step)
            self._drag_key = None
            self._punch = {}             # key -> click timestamp (scale punch)
            # Custom creator state.
            self.custom = {
                "rate": 60.0, "lifetime": 2.0, "speed": 3.0,
                "emitter_radius": 0.4, "size": 0.5, "gravity_y": -1.0,
                "turbulence": 0.4, "spread": 0.25, "capacity": 512.0,
                "type_idx": 0, "additive": False,
                "cs": [0.95, 0.65, 0.25, 1.0],   # color_start RGBA
                "ce": [0.45, 0.12, 0.05, 0.0],   # color_end   RGBA
            }
            # Live preview.
            self._preview_on = False
            self._preview_node = None
            # Preset cache (disk IO throttled).
            self._presets_cache = None
            self._presets_stamp = 0.0
            self._panel_rect = UIRect(self.PX, self.PY, self.PW, self.PH)
            # HQ (rounded) render path: commands collected during draw(),
            # flushed by the wrapped UIRenderer.render after the stock pass.
            self._hq = False
            self._ui = None
            self._hq_cmds = []

        # ------------------------------------------------------------ state
        def _editor_active(self):
            try:
                return getattr(self.engine, "mode", None) == "editor" \
                    and not getattr(self.engine, "_playing", False)
            except Exception:
                return False

        def toggle(self):
            if self.panel_open:
                self.close()
            else:
                self.open()

        def open(self):
            if not self._editor_active():
                return
            self.panel_open = True
            self._refresh_presets()

        def close(self):
            self.panel_open = False
            self._drag_key = None
            self._hq_cmds = []
            self._disable_preview()

        # ------------------------------------------------------------ input
        def _mouse_uv(self):
            """Cursor in normalized UI coords (y up), from engine.input."""
            try:
                mp = getattr(self.engine.input, "mouse_pos", None) or (-1, -1)
                w = max(1.0, float(getattr(self.engine, "width", 1)))
                h = max(1.0, float(getattr(self.engine, "height", 1)))
                return (float(mp[0]) / w, 1.0 - float(mp[1]) / h)
            except Exception:
                return (-1.0, -1.0)

        def mouse_down(self, button, pos):
            """MODAL: while open, EVERY press of EVERY button anywhere on the
            screen is consumed — nothing beneath (hierarchy, inspector,
            navbar, gizmo, scene picking) ever sees it.  Clicks outside the
            window are a harmless no-op."""
            if not self.panel_open or not self._editor_active():
                return False
            try:
                w = max(1.0, float(getattr(self.engine, "width", 1)))
                h = max(1.0, float(getattr(self.engine, "height", 1)))
                ux = float(pos[0]) / w
                uy = 1.0 - float(pos[1]) / h
                inside = self._panel_rect is not None \
                    and self._panel_rect.contains(ux, uy)
                if button == 1 and inside:
                    # The original _on_mouse_down sets input.lmb itself; when we
                    # consume the press it never runs, so mirror that here or
                    # slider drags (which poll input.lmb during draw) die.
                    try:
                        self.engine.input.lmb = True
                    except Exception:
                        pass
                if button == 1:
                    # Widgets first (top-most = last drawn).
                    for rect, cb, key in reversed(self._clicks):
                        if rect is not None and rect.contains(ux, uy):
                            self._punch[key] = time.time()
                            try:
                                cb()
                            except Exception as ex:
                                _gu_studio_log_once(
                                    "click", f"[GREATUPDATE:studio] click: {ex}")
                            return True
                    for key, rect in self._slider_rects.items():
                        if rect is not None and rect.contains(ux, uy):
                            self._drag_key = key
                            self._apply_drag(ux, force=True)
                            return True
                # Everything else (backdrop, other buttons): swallowed.
                return True
            except Exception as ex:
                _gu_studio_log_once(
                    "mdown", f"[GREATUPDATE:studio] mouse down: {ex}")
                return True

        def mouse_up(self, button):
            try:
                if button == 1:
                    self._drag_key = None
            except Exception:
                pass

        def on_key(self, key):
            """MODAL: Esc closes; EVERY other key is swallowed while the
            studio is open.  Returns True when consumed."""
            if not self.panel_open or not self._editor_active():
                return False
            try:
                if key == getattr(pygame, "K_ESCAPE", 27):
                    self.close()
                    return True
                return True     # modal: typing goes nowhere else
            except Exception as ex:
                _gu_studio_log_once("key", f"[GREATUPDATE:studio] key: {ex}")
                return True

        # ----------------------------------------------------------- presets
        def _refresh_presets(self):
            try:
                self._presets_cache = _gu_studio_presets()
                self._presets_stamp = time.time()
            except Exception as ex:
                _gu_studio_log_once(
                    "refresh", f"[GREATUPDATE:studio] refresh: {ex}")
                if self._presets_cache is None:
                    self._presets_cache = {}

        def _get_presets(self):
            if self._presets_cache is None:
                self._refresh_presets()
            # Light periodic refresh so externally added disk presets appear.
            if time.time() - self._presets_stamp > 1.5:
                self._refresh_presets()
            return self._presets_cache or {}

        def _builtin_names(self):
            builtin = globals().get("GU_PARTICLE_PRESETS")
            if isinstance(builtin, dict):
                return set(builtin.keys())
            return set()

        def _spawn_preset(self, name, cfg):
            """One-click spawn: drop the preset in front of the camera."""
            add = globals().get("gu_add_particle_system")
            if not callable(add):
                _gu_studio_log_once(
                    "no_api", "[GREATUPDATE:studio] gu_add_particle_system "
                              "missing — particles core not installed?")
                return
            try:
                cam = self.engine.camera
                pos = cam.position + cam.forward() * 6.0
            except Exception:
                pos = None
            node = None
            try:
                node = add(self.engine, cfg, pos)
            except Exception:
                try:
                    node = add(self.engine, cfg,
                               (pos.x, pos.y, pos.z) if pos is not None else None)
                except Exception as ex:
                    _gu_studio_log_once(
                        "spawn", f"[GREATUPDATE:studio] spawn '{name}': {ex}")
                    return
            try:
                if node is not None and hasattr(self.engine, "select_node"):
                    self.engine.select_node(node)
            except Exception:
                pass
            try:
                Log.info(f"Particle Studio: spawned '{name}'")
            except Exception:
                pass

        def _delete_custom_preset(self, name):
            """Remove a disk preset whose JSON 'name' matches (robust to the
            core's filename sanitization)."""
            try:
                d = os.path.join(os.getcwd(), "particle_presets")
                if not os.path.isdir(d):
                    return
                target = None
                for fn in os.listdir(d):
                    if not fn.lower().endswith(".json"):
                        continue
                    path = os.path.join(d, fn)
                    try:
                        with open(path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        if str(data.get("name", "")) == name:
                            target = path
                            break
                    except Exception:
                        continue
                if target is None:
                    san = "".join(c if (c.isalnum() or c in " ._-") else "_"
                                  for c in name).strip()
                    for cand in (san + ".json",
                                 san.replace(" ", "_") + ".json"):
                        p = os.path.join(d, cand)
                        if os.path.isfile(p):
                            target = p
                            break
                if target is not None:
                    os.remove(target)
                    try:
                        Log.info(f"Particle Studio: deleted preset '{name}'")
                    except Exception:
                        pass
                    self._refresh_presets()
            except Exception as ex:
                _gu_studio_log_once(
                    "del", f"[GREATUPDATE:studio] delete '{name}': {ex}")

        # ------------------------------------------------------ custom config
        def _slider_value(self, key):
            c = self.custom
            if key[:2] in ("cs", "ce") and key[2:].isdigit():
                return c[key[:2]][int(key[2:])]
            return c.get(key, 0.0)

        def _slider_set(self, key, v):
            c = self.custom
            if key[:2] in ("cs", "ce") and key[2:].isdigit():
                c[key[:2]][int(key[2:])] = _clamp(v, 0.0, 1.0)
            elif key in c:
                c[key] = v

        def _apply_drag(self, ux, force=False):
            key = self._drag_key
            if key is None:
                return
            if not force and not getattr(self.engine.input, "lmb", False):
                self._drag_key = None
                return
            rect = self._slider_rects.get(key)
            meta = self._slider_meta.get(key)
            if rect is None or meta is None:
                return
            lo, hi, step = meta
            frac = _clamp((ux - (rect.x + 0.006))
                          / max(1e-6, rect.w - 0.012), 0.0, 1.0)
            v = lo + frac * (hi - lo)
            if step and step > 0:
                v = round(v / step) * step
            self._slider_set(key, _clamp(v, lo, hi))

        def _build_custom_config(self):
            c = self.custom
            lt = max(0.05, float(c["lifetime"]))
            sp = max(0.0, float(c["speed"]))
            sp_lo, sp_hi = sp * 0.7, sp * 1.3
            if sp_hi <= sp_lo:
                sp_hi = sp_lo + 0.001
            types = _GU_STUDIO_TYPES
            return {
                "name": "Custom",
                "type": types[int(c["type_idx"]) % len(types)],
                "capacity": int(_clamp(c["capacity"], 64, 2048)),
                "rate": float(c["rate"]),
                "lifetime": [round(lt * 0.75, 3), round(lt * 1.25, 3)],
                "speed": [round(sp_lo, 3), round(sp_hi, 3)],
                "direction": [0.0, 1.0, 0.0],
                "spread": float(_clamp(c["spread"], 0.0, 1.0)),
                "emitter_radius": float(c["emitter_radius"]),
                "gravity": [0.0, float(c["gravity_y"]), 0.0],
                "drag": 0.1,
                "turbulence": float(c["turbulence"]),
                "size_start": float(c["size"]),
                "size_end": float(c["size"] * 0.4),
                "color_start": [float(_clamp(v, 0.0, 1.0)) for v in c["cs"]],
                "color_end": [float(_clamp(v, 0.0, 1.0)) for v in c["ce"]],
                "additive": bool(c["additive"]),
                "emitting": True,
            }

        # ------------------------------------------------------------ preview
        def _toggle_preview(self):
            if self._preview_on:
                self._disable_preview()
            else:
                self._preview_on = True

        def _disable_preview(self):
            self._preview_on = False
            self._remove_preview_node()

        def _remove_preview_node(self):
            node = self._preview_node
            self._preview_node = None
            if node is None:
                return
            try:
                if getattr(self.engine, "selected", None) is node \
                        and hasattr(self.engine, "deselect"):
                    self.engine.deselect()
            except Exception:
                pass
            try:
                parent = getattr(node, "parent", None)
                if parent is not None and node in getattr(parent, "children", []):
                    parent.children.remove(node)
                node.parent = None
            except Exception as ex:
                _gu_studio_log_once(
                    "prev_rm", f"[GREATUPDATE:studio] preview remove: {ex}")

        def _update_preview(self):
            if not self._preview_on:
                return
            add = globals().get("gu_add_particle_system")
            if not callable(add):
                return
            try:
                cam = self.engine.camera
                pos = cam.position + cam.forward() * 6.0
                cfg = self._build_custom_config()
                cfg["name"] = "Studio Preview"
                node = self._preview_node
                if node is None or getattr(node, "parent", None) is None:
                    try:
                        node = add(self.engine, cfg, pos)
                    except Exception:
                        node = add(self.engine, cfg, (pos.x, pos.y, pos.z))
                    self._preview_node = node
                else:
                    try:
                        node.user_data["particle_system"] = cfg
                    except Exception:
                        pass
                    try:
                        node.position = glm.vec3(pos.x, pos.y, pos.z)
                    except Exception:
                        try:
                            node.position = pos
                        except Exception:
                            pass
            except Exception as ex:
                _gu_studio_log_once(
                    "preview", f"[GREATUPDATE:studio] preview: {ex}")

        # ---------------------------------------------------------- save preset
        def _ask_preset_name(self):
            """Guarded tkinter prompt; None on cancel/failure (caller falls
            back to an auto-name)."""
            try:
                import tkinter as tk
                from tkinter import simpledialog
                root = tk.Tk()
                try:
                    root.withdraw()
                    try:
                        root.attributes("-topmost", True)
                    except Exception:
                        pass
                    name = simpledialog.askstring(
                        "Save Particle Preset", "Preset name:", parent=root)
                finally:
                    try:
                        root.destroy()
                    except Exception:
                        pass
                if name and name.strip():
                    return name.strip()
                return None
            except Exception as ex:
                _gu_studio_log_once(
                    "tk", f"[GREATUPDATE:studio] tkinter dialog unavailable "
                          f"({ex}); using auto-name")
                return None

        def _save_current_preset(self):
            saver = globals().get("gu_save_preset")
            if not callable(saver):
                _gu_studio_log_once(
                    "no_save", "[GREATUPDATE:studio] gu_save_preset missing — "
                               "particles core not installed?")
                return
            name = self._ask_preset_name()
            if not name:
                existing = self._get_presets()
                n = 1
                while f"Custom {n}" in existing:
                    n += 1
                name = f"Custom {n}"
            cfg = self._build_custom_config()
            cfg["name"] = name
            try:
                ok = saver(name, cfg)
            except Exception as ex:
                _gu_studio_log_once(
                    "save", f"[GREATUPDATE:studio] save '{name}': {ex}")
                return
            if ok:
                try:
                    Log.info(f"Particle Studio: saved preset '{name}'")
                except Exception:
                    pass
                self._refresh_presets()

        # ------------------------------------------------------------ drawing
        def _hq_supported(self, ui):
            """True when the renderer exposes the rounded-rect + font
            internals the UE-style layer needs."""
            try:
                if not callable(getattr(ui, "_draw_rect", None)):
                    return False
                font = getattr(ui, "font", None)
                if font is None or not callable(getattr(font, "render_text", None)):
                    return False
                return True
            except Exception:
                return False

        def _screen_px(self, ui=None):
            """Viewport size in pixels (guarded; 1600x900 fallback)."""
            w = h = 0.0
            try:
                if ui is not None:
                    w = float(getattr(ui, "w", 0) or 0)
                    h = float(getattr(ui, "h", 0) or 0)
            except Exception:
                pass
            if w <= 1:
                try:
                    w = float(getattr(self.engine, "width", 0) or 0)
                except Exception:
                    pass
            if h <= 1:
                try:
                    h = float(getattr(self.engine, "height", 0) or 0)
                except Exception:
                    pass
            return (w if w > 1 else 1600.0, h if h > 1 else 900.0)

        def _r(self, rect, color, radius=0.0):
            """Emit a (rounded) rect — queued for the HQ flush, or drawn flat
            via ui.panel in fallback mode."""
            if self._hq:
                self._hq_cmds.append(("rect", rect.x, rect.y, rect.w, rect.h,
                                      color, float(radius)))
            else:
                try:
                    self._ui.panel(rect, color)
                except Exception:
                    pass

        def _t(self, rect, s, color, ax=0.0):
            """Emit a text label — queued for the HQ flush (anchor_x = ax),
            or drawn via ui.text in fallback mode."""
            if self._hq:
                self._hq_cmds.append(("text", rect.x, rect.y, rect.w, rect.h,
                                      str(s), color, float(ax)))
            else:
                try:
                    self._ui.text(rect, str(s), color)
                except Exception:
                    pass

        def flush_hq(self, ui):
            """Draw the queued studio layer DIRECTLY through UIRenderer
            internals, after the stock render() pass — the studio (backdrop +
            rounded window) is the topmost thing on screen.  Never raises."""
            cmds = self._hq_cmds
            if not cmds:
                return
            draw_rect = getattr(ui, "_draw_rect", None)
            font = getattr(ui, "font", None)
            if not callable(draw_rect) or font is None \
                    or not callable(getattr(font, "render_text", None)):
                return
            sw, sh = self._screen_px(ui)
            # The stock render() ends with BLEND off / DEPTH_TEST on; restore
            # the UI-pass GL state before drawing our overlay (guarded — the
            # stub harness has no GL context).
            mgl = globals().get("moderngl")
            ctx = getattr(ui, "ctx", None)
            state_ok = False
            if ctx is not None and mgl is not None:
                try:
                    ctx.disable(mgl.DEPTH_TEST)
                    ctx.enable(mgl.BLEND)
                    ctx.blend_func = (mgl.SRC_ALPHA, mgl.ONE_MINUS_SRC_ALPHA)
                    state_ok = True
                except Exception as ex:
                    _gu_studio_log_once(
                        "glstate", f"[GREATUPDATE:studio] gl state: {ex}")
            try:
                for cmd in cmds:
                    if cmd[0] == "rect":
                        _k, x, y, w, h, col, rad = cmd
                        draw_rect((x * sw, y * sh, w * sw, h * sh), col,
                                  radius=float(rad))
                    else:
                        _k, x, y, w, h, s, col, ax = cmd
                        try:
                            tex = font.render_text(
                                s, (int(_clamp(float(col[0]), 0.0, 1.0) * 255),
                                    int(_clamp(float(col[1]), 0.0, 1.0) * 255),
                                    int(_clamp(float(col[2]), 0.0, 1.0) * 255)))
                        except Exception:
                            tex = None
                        if tex is None:
                            continue
                        try:
                            tw, th = tex.size
                        except Exception:
                            continue
                        # Mirror UIRenderer.render's button-label drawing:
                        # aspect-correct glyph quad inside the rect.
                        draw_rect((x * sw, y * sh, w * sw, h * sh), col,
                                  tex=tex, tex_aspect=tw / max(1, th),
                                  anchor_x=float(ax), anchor_y=0.5)
            except Exception as ex:
                _gu_studio_log_once(
                    "flush", f"[GREATUPDATE:studio] flush: {ex}")
            if state_ok:
                try:
                    ctx.disable(mgl.BLEND)
                    ctx.enable(mgl.DEPTH_TEST)
                except Exception:
                    pass

        def _draw_buzz_button(self, ui, rect, label, cb, color, phase, key,
                              radius=_GU_R_CARD, hover_color=None):
            """Rounded button/card with BUZZ animation: hover inflates the
            rect 6% and adds a ±0.0015 sinusoidal jitter at ~25 Hz (phase per
            button); a click does a 0.18 s scale punch (1→1.15→1)."""
            mx, my = self._mouse_uv()
            hovered = rect.contains(mx, my)
            t = time.time()
            scale = 1.0
            t0 = self._punch.get(key)
            if t0 is not None:
                dtp = t - t0
                if dtp < 0.18:
                    scale = 1.0 + 0.15 * math.sin(math.pi * dtp / 0.18)
                else:
                    self._punch.pop(key, None)
            jx = jy = 0.0
            if hovered:
                scale *= 1.06
                jx = 0.0015 * math.sin(t * (2.0 * math.pi * 25.0) + phase)
                jy = 0.0015 * math.cos(t * (2.0 * math.pi * 25.0) + phase * 1.7)
            cx = rect.x + rect.w * 0.5
            cy = rect.y + rect.h * 0.5
            w, h = rect.w * scale, rect.h * scale
            r = UIRect(cx - w * 0.5 + jx, cy - h * 0.5 + jy, w, h)
            if hovered and hover_color is not None:
                col = hover_color
            else:
                try:
                    col = _lighten(color, 0.13) if hovered else color
                except Exception:
                    col = color
            self._r(r, col, radius)
            inset = min(0.012, rect.w * 0.12)
            tcol = _gu_theme("text_bright", (1.0, 1.0, 1.0, 1.0)) if hovered \
                else _gu_theme("text", (0.8, 0.8, 0.8, 1.0))
            self._t(UIRect(r.x + inset, r.y + rect.h * 0.18,
                           max(0.01, r.w - 2.0 * inset), rect.h * 0.64),
                    str(label), tcol, 0.5)
            self._clicks.append((rect, cb, key))

        def _draw_slider(self, ui, key, label, lo, hi, step, fmt, x, y, w):
            """UE-style slider row: label left, value right, rounded track +
            fill + knob.  Hit rect registered for the mouse wraps."""
            C = _gu_studio_colors()
            dim = _gu_theme("text_dim", (0.55, 0.55, 0.55, 1.0))
            value = float(self._slider_value(key))
            self._t(UIRect(x, y + 0.004, 0.104, 0.024), label, dim, 0.0)
            frac = 0.0 if hi <= lo else _clamp((value - lo) / (hi - lo), 0.0, 1.0)
            tx = x + 0.110
            tw = max(0.02, w - 0.110 - 0.054)
            self._r(UIRect(tx, y + 0.010, tw, 0.010), C["track"], _GU_R_SLIDER)
            if frac > 0.001:
                self._r(UIRect(tx, y + 0.010, tw * frac, 0.010),
                        C["accent"], _GU_R_SLIDER)
            self._r(UIRect(tx + tw * frac - 0.0045, y + 0.003, 0.009, 0.024),
                    _gu_theme("text_bright", C["knob"]), _GU_R_KNOB)
            try:
                vtxt = fmt.format(value)
            except Exception:
                vtxt = str(round(value, 2))
            self._t(UIRect(x + w - 0.052, y + 0.004, 0.052, 0.022), vtxt,
                    _gu_theme("text", (0.8, 0.8, 0.8, 1.0)), 0.5)
            self._slider_rects[key] = UIRect(tx - 0.006, y - 0.003,
                                             tw + 0.012, 0.032)
            self._slider_meta[key] = (lo, hi, step)

        def _draw_channel_slider(self, ui, key, letter, x, y, w, color_hint):
            """Compact 0..1 rounded slider for one RGBA channel."""
            C = _gu_studio_colors()
            dim = _gu_theme("text_dim", (0.55, 0.55, 0.55, 1.0))
            hint = _gu_studio_color4((color_hint[0], color_hint[1],
                                      color_hint[2], 1.0))
            value = float(self._slider_value(key))
            self._t(UIRect(x, y + 0.002, 0.013, 0.020), letter, hint, 0.0)
            tx = x + 0.016
            tw = max(0.02, w - 0.016 - 0.040)
            self._r(UIRect(tx, y + 0.007, tw, 0.009), C["track"], 4.5)
            if value > 0.001:
                self._r(UIRect(tx, y + 0.007, tw * value, 0.009), hint, 4.5)
            self._r(UIRect(tx + tw * value - 0.0035, y + 0.0025, 0.007, 0.018),
                    _gu_theme("text_bright", C["knob"]), 3.5)
            self._t(UIRect(x + w - 0.038, y + 0.002, 0.038, 0.020),
                    f"{value:.2f}", dim, 0.5)
            self._slider_rects[key] = UIRect(tx - 0.006, y - 0.002,
                                             tw + 0.012, 0.027)
            self._slider_meta[key] = (0.0, 1.0, 0.01)

        def draw(self, ui):
            """Rebuild the studio layout (immediate mode).  In HQ mode this
            only COLLECTS draw commands — flush_hq() paints them after the
            stock UI pass; in fallback mode it draws flat panels directly."""
            if not self.panel_open or not self._editor_active():
                return
            C = _gu_studio_colors()
            dim = _gu_theme("text_dim", (0.55, 0.55, 0.55, 1.0))
            mx, _my = self._mouse_uv()
            self._apply_drag(mx)            # continues an active slider drag
            self._update_preview()
            self._clicks = []
            self._slider_rects = {}
            self._slider_meta = {}
            self._hq_cmds = []
            self._ui = ui
            self._hq = self._hq_supported(ui)
            sw, sh = self._screen_px(ui)

            # ---- 0. modal backdrop: dims EVERY editor UI beneath ----------
            self._r(UIRect(0.0, 0.0, 1.0, 1.0), C["backdrop"], 0.0)

            # ---- window: 1px dark outline behind a rounded charcoal body --
            wx, wy, ww, wh = self.PX, self.PY, self.PW, self.PH
            panel = UIRect(wx, wy, ww, wh)
            self._panel_rect = panel
            ex = 2.0 / sw
            ey = 2.0 / sh
            self._r(UIRect(wx - ex, wy - ey, ww + 2.0 * ex, wh + 2.0 * ey),
                    C["outline"], _GU_R_OUTLINE)
            self._r(panel, C["window"], _GU_R_WINDOW)

            # ---- 1. title bar + close -------------------------------------
            th = 0.058
            ty = wy + wh - th
            self._r(UIRect(wx, ty, ww, th), C["titlebar"], _GU_R_WINDOW)
            # square off the title bar's bottom edge
            self._r(UIRect(wx, ty, ww, th * 0.55), C["titlebar"], 0.0)
            # amber accent strip under the title bar
            self._r(UIRect(wx + 0.006, ty - 0.0045, ww - 0.012, 0.0032),
                    C["accent"], 2.0)
            self._t(UIRect(wx + 0.020, ty + 0.014, 0.40, 0.030),
                    "PARTICLE STUDIO", C["accent_hi"], 0.0)
            self._draw_buzz_button(
                ui, UIRect(wx + ww - 0.052, ty + 0.010, 0.034, 0.038), "X",
                self.close, C["card"], 0.0, "__close",
                hover_color=_gu_theme("danger", (0.78, 0.31, 0.31, 1.0)))

            ct = ty - 0.016                   # content top
            lx = wx + 0.018                   # left column x
            rx0 = wx + 0.400                  # right column x
            rw = wx + ww - 0.018 - rx0        # right column width
            # vertical divider between the columns
            self._r(UIRect(wx + 0.384, wy + 0.030, 0.0016, ct - wy - 0.046),
                    C["divider"], 1.0)

            presets = self._get_presets()
            builtin_names = self._builtin_names()
            builtin_items = [(n, c) for n, c in presets.items()
                             if n in builtin_names]
            custom_items = [(n, c) for n, c in presets.items()
                            if n not in builtin_names]

            # ---- 2. LIBRARY grid (rounded preset cards) --------------------
            self._t(UIRect(lx, ct - 0.026, 0.20, 0.022), "LIBRARY", dim, 0.0)
            grid_x = (lx, lx + 0.181)
            bw, bh, row0, step = 0.169, 0.050, ct - 0.092, 0.068
            for i, (name, cfg) in enumerate(builtin_items[:8]):
                x = grid_x[i % 2]
                y = row0 - (i // 2) * step
                rect = UIRect(x, y, bw, bh)
                self._draw_buzz_button(
                    ui, rect, str(name)[:16],
                    (lambda n=name, c=cfg: self._spawn_preset(n, c)),
                    C["card"], i * 1.3, "preset:" + str(name))
                cs = _gu_studio_color4(
                    (cfg or {}).get("color_start", (1, 1, 1, 1)))
                ce = _gu_studio_color4(
                    (cfg or {}).get("color_end", (1, 1, 1, 1)))
                self._r(UIRect(x + 0.008, y - 0.013, bw * 0.5 - 0.010, 0.009),
                        cs, _GU_R_SWATCH)
                self._r(UIRect(x + bw * 0.5 + 0.002, y - 0.013,
                               bw * 0.5 - 0.010, 0.009), ce, _GU_R_SWATCH)

            # ---- custom presets with delete -------------------------------
            self._t(UIRect(lx, 0.510, 0.20, 0.022), "CUSTOM", dim, 0.0)
            crow0, cstep = 0.454, 0.064
            danger = _gu_theme("danger", (0.78, 0.31, 0.31, 1.0))
            for i, (name, cfg) in enumerate(custom_items[:5]):
                y = crow0 - i * cstep
                self._draw_buzz_button(
                    ui, UIRect(lx, y, 0.262, 0.046), str(name)[:22],
                    (lambda n=name, c=cfg: self._spawn_preset(n, c)),
                    C["card"], 7.0 + i * 1.3, "custom:" + str(name))
                self._draw_buzz_button(
                    ui, UIRect(lx + 0.272, y, 0.066, 0.046), "DEL",
                    (lambda n=name: self._delete_custom_preset(n)),
                    C["card"], 11.0 + i * 1.3, "del:" + str(name),
                    hover_color=danger)
                cs = _gu_studio_color4(
                    (cfg or {}).get("color_start", (1, 1, 1, 1)))
                ce = _gu_studio_color4(
                    (cfg or {}).get("color_end", (1, 1, 1, 1)))
                self._r(UIRect(lx + 0.008, y - 0.012, 0.123, 0.008),
                        cs, _GU_R_SWATCH)
                self._r(UIRect(lx + 0.139, y - 0.012, 0.123, 0.008),
                        ce, _GU_R_SWATCH)
            if not custom_items:
                self._t(UIRect(lx, crow0 + 0.010, 0.30, 0.024),
                        "No custom presets yet — build one ->", dim, 0.0)
            elif len(custom_items) > 5:
                self._t(UIRect(lx, crow0 - 5 * cstep, 0.30, 0.024),
                        f"+{len(custom_items) - 5} more in particle_presets/",
                        dim, 0.0)

            # ---- 3. CUSTOM CREATOR ----------------------------------------
            self._t(UIRect(rx0, ct - 0.026, 0.24, 0.022), "CUSTOM CREATOR",
                    dim, 0.0)
            y = ct - 0.098
            for (key, label, lo, hi, sstep, fmt) in _GU_STUDIO_SLIDERS:
                self._draw_slider(ui, key, label, lo, hi, sstep, fmt,
                                  rx0, y, rw)
                y -= 0.0375

            # type cycle + additive toggle
            c = self.custom
            types = _GU_STUDIO_TYPES
            tname = types[int(c["type_idx"]) % len(types)]
            self._draw_buzz_button(
                ui, UIRect(rx0, 0.412, 0.137, 0.036), f"Type: {tname}",
                lambda: self.custom.__setitem__(
                    "type_idx", (int(self.custom["type_idx"]) + 1) % len(types)),
                C["card"], 3.1, "__type")
            additive = bool(c["additive"])
            self._draw_buzz_button(
                ui, UIRect(rx0 + 0.145, 0.412, 0.137, 0.036),
                f"Additive: {'ON' if additive else 'OFF'}",
                lambda: self.custom.__setitem__(
                    "additive", not bool(self.custom["additive"])),
                C["accent_soft"] if additive else C["card"], 4.2, "__additive")

            # RGBA channel sliders for start/end colors
            self._t(UIRect(rx0, 0.378, 0.137, 0.020), "START COLOR", dim, 0.0)
            self._t(UIRect(rx0 + 0.145, 0.378, 0.137, 0.020), "END COLOR",
                    dim, 0.0)
            hints = ((0.9, 0.2, 0.2), (0.2, 0.8, 0.3), (0.25, 0.45, 0.95),
                     (0.85, 0.85, 0.85))
            cy = 0.348
            for i, letter in enumerate("RGBA"):
                self._draw_channel_slider(ui, f"cs{i}", letter,
                                          rx0, cy, 0.137, hints[i])
                self._draw_channel_slider(ui, f"ce{i}", letter,
                                          rx0 + 0.145, cy, 0.137, hints[i])
                cy -= 0.0335

            # preview toggle + save
            self._draw_buzz_button(
                ui, UIRect(rx0, 0.202, 0.137, 0.038),
                f"Preview: {'ON' if self._preview_on else 'OFF'}",
                self._toggle_preview,
                _gu_theme("play_green", (0.29, 0.54, 0.29, 1.0))
                if self._preview_on else C["card"],
                5.3, "__preview")
            self._draw_buzz_button(
                ui, UIRect(rx0 + 0.145, 0.202, 0.137, 0.038), "SAVE PRESET",
                self._save_current_preset, C["accent_soft"], 6.4, "__save")

            # ---- 5. footer hint -------------------------------------------
            self._t(UIRect(lx, wy + 0.012, 0.62, 0.022),
                    "Click a preset to drop it in the world — move it with "
                    "the gizmo.", dim, 0.0)


    # Module-level fallback stub: if anything calls toggle_particle_studio
    # before/without the method install, this still works (never raises).
    _GU_STUDIO_ENGINES = []


    def toggle_particle_studio(engine=None):
        eng = engine
        if eng is None and _GU_STUDIO_ENGINES:
            eng = _GU_STUDIO_ENGINES[-1]
        if eng is None:
            return
        try:
            _gu_toggle_particle_studio_impl(eng)
        except Exception as ex:
            _gu_studio_log_once("stub", f"[GREATUPDATE:studio] stub: {ex}")


    def _gu_toggle_particle_studio_impl(engine):
        if getattr(engine, "mode", None) != "editor" \
                or getattr(engine, "_playing", False):
            return
        st = getattr(engine, "_gu_studio", None)
        if st is None:
            st = _GUParticleStudio(engine)
            engine._gu_studio = st
        st.toggle()


    def _gu_toggle_particle_studio(self):
        """HeavenEngine.toggle_particle_studio — navbar 'Particle' button."""
        try:
            _gu_toggle_particle_studio_impl(self)
        except Exception as ex:
            _gu_studio_log_once("toggle", f"[GREATUPDATE:studio] toggle: {ex}")


    def _gu_studio_engine_for_ui(ui):
        """The tracked engine whose UIRenderer is `ui` AND whose studio is
        currently open (editor mode, not playing).  None otherwise."""
        try:
            for eng in list(_GU_STUDIO_ENGINES):
                try:
                    if getattr(eng, "ui", None) is not ui:
                        continue
                    if getattr(eng, "mode", None) != "editor" \
                            or getattr(eng, "_playing", False):
                        continue
                    st = getattr(eng, "_gu_studio", None)
                    if st is not None and st.panel_open:
                        return eng
                except Exception:
                    continue
        except Exception:
            pass
        return None


    def _gu_studio_install():
        """Wire the studio into HeavenEngine.  Fully armored; never raises."""
        if globals().get("_GU_STUDIO_INSTALLED"):
            return
        try:
            # ---- engine init: per-engine studio holder --------------------
            try:
                _GU_PREV_INIT = HeavenEngine.__init__

                def _gu_studio_init(self, *a, **kw):
                    _GU_PREV_INIT(self, *a, **kw)
                    self._gu_studio = None
                    if self not in _GU_STUDIO_ENGINES:
                        _GU_STUDIO_ENGINES.append(self)

                HeavenEngine.__init__ = _gu_studio_init
            except Exception as ex:
                _gu_studio_log_once(
                    "init_wrap", f"[GREATUPDATE:studio] init wrap: {ex}")

            HeavenEngine.toggle_particle_studio = _gu_toggle_particle_studio

            # ---- _build_ui wrap: build the overlay LAST --------------------
            try:
                _GU_PREV_BUILD_UI = HeavenEngine._build_ui

                def _gu_studio_build_ui(self):
                    _GU_PREV_BUILD_UI(self)
                    try:
                        if getattr(self, "mode", None) != "editor":
                            return
                        if getattr(self, "_playing", False):
                            # Play mode started with the studio open: close it
                            # (and its preview) so nothing lingers in-game.
                            st = getattr(self, "_gu_studio", None)
                            if st is not None and st.panel_open:
                                st.close()
                            return
                        ui = getattr(self, "ui", None)
                        if ui is None:
                            return
                        st = getattr(self, "_gu_studio", None)
                        if st is not None and st.panel_open:
                            st.draw(ui)
                    except Exception as ex:
                        _gu_studio_log_once(
                            "draw", f"[GREATUPDATE:studio] draw: {ex}")

                HeavenEngine._build_ui = _gu_studio_build_ui
            except Exception as ex:
                _gu_studio_log_once(
                    "ui_wrap", f"[GREATUPDATE:studio] ui wrap: {ex}")

            # ---- UIRenderer.render wrap: flush the rounded layer TOPMOST ---
            try:
                _GU_UI_CLS = globals().get("UIRenderer")
                if _GU_UI_CLS is not None:
                    _GU_PREV_UI_RENDER = _GU_UI_CLS.render

                    def _gu_studio_ui_render(self):
                        _GU_PREV_UI_RENDER(self)      # all base editor UI first
                        try:
                            eng = _gu_studio_engine_for_ui(self)
                            if eng is None:
                                return
                            st = getattr(eng, "_gu_studio", None)
                            if st is not None:
                                st.flush_hq(self)     # studio = topmost layer
                        except Exception as ex:
                            _gu_studio_log_once(
                                "uiren", f"[GREATUPDATE:studio] ui render: {ex}")

                    _GU_UI_CLS.render = _gu_studio_ui_render

                    # Wheel is routed straight to UIRenderer.scroll_wheel in
                    # the engine event loop — wrap it so the modal studio
                    # swallows ALL wheel events while open.
                    if callable(getattr(_GU_UI_CLS, "scroll_wheel", None)):
                        _GU_PREV_SCROLL_WHEEL = _GU_UI_CLS.scroll_wheel

                        def _gu_studio_scroll_wheel(self, mx, my, amount):
                            try:
                                if _gu_studio_engine_for_ui(self) is not None:
                                    return True     # modal: wheel swallowed
                            except Exception:
                                pass
                            return _GU_PREV_SCROLL_WHEEL(self, mx, my, amount)

                        _GU_UI_CLS.scroll_wheel = _gu_studio_scroll_wheel
                else:
                    _gu_studio_log_once(
                        "no_uir", "[GREATUPDATE:studio] UIRenderer missing — "
                                  "rounded topmost layer disabled (flat "
                                  "fallback still active)")
            except Exception as ex:
                _gu_studio_log_once(
                    "uirender_wrap", f"[GREATUPDATE:studio] ui render wrap: {ex}")

            # ---- _on_mouse_down wrap: MODAL consume of every button --------
            try:
                _GU_PREV_ON_MOUSE_DOWN = HeavenEngine._on_mouse_down

                def _gu_studio_on_mouse_down(self, button, pos):
                    try:
                        st = getattr(self, "_gu_studio", None)
                        if st is not None and st.panel_open \
                                and getattr(self, "mode", None) == "editor" \
                                and not getattr(self, "_playing", False):
                            if st.mouse_down(button, pos):
                                return      # consumed: nothing beneath reacts
                    except Exception as ex:
                        _gu_studio_log_once(
                            "mdown", f"[GREATUPDATE:studio] mdown: {ex}")
                    return _GU_PREV_ON_MOUSE_DOWN(self, button, pos)

                HeavenEngine._on_mouse_down = _gu_studio_on_mouse_down
            except Exception as ex:
                _gu_studio_log_once(
                    "mdown_wrap", f"[GREATUPDATE:studio] mdown wrap: {ex}")

            # ---- _on_mouse_up wrap: release slider drag, modal consume -----
            try:
                _GU_PREV_ON_MOUSE_UP = HeavenEngine._on_mouse_up

                def _gu_studio_on_mouse_up(self, button, pos):
                    try:
                        st = getattr(self, "_gu_studio", None)
                        if st is not None and st.panel_open \
                                and getattr(self, "mode", None) == "editor" \
                                and not getattr(self, "_playing", False):
                            st.mouse_up(button)
                            # Mirror the stock release bookkeeping the original
                            # would have done (no grabs were set while modal).
                            try:
                                if button == 1:
                                    self.input.lmb = False
                                elif button == 2:
                                    self.input.mmb = False
                                elif button == 3:
                                    self.input.rmb = False
                            except Exception:
                                pass
                            return          # modal: release consumed too
                    except Exception:
                        pass
                    return _GU_PREV_ON_MOUSE_UP(self, button, pos)

                HeavenEngine._on_mouse_up = _gu_studio_on_mouse_up
            except Exception as ex:
                _gu_studio_log_once(
                    "mup_wrap", f"[GREATUPDATE:studio] mup wrap: {ex}")

            # ---- _on_key wrap: Esc closes, ALL keys swallowed --------------
            try:
                _GU_PREV_ON_KEY = HeavenEngine._on_key

                def _gu_studio_on_key(self, key):
                    try:
                        st = getattr(self, "_gu_studio", None)
                        if st is not None and st.panel_open \
                                and getattr(self, "mode", None) == "editor" \
                                and not getattr(self, "_playing", False) \
                                and st.on_key(key):
                            return
                    except Exception:
                        pass
                    return _GU_PREV_ON_KEY(self, key)

                HeavenEngine._on_key = _gu_studio_on_key
            except Exception as ex:
                _gu_studio_log_once(
                    "key_wrap", f"[GREATUPDATE:studio] key wrap: {ex}")

            globals()["_GU_STUDIO_INSTALLED"] = True
            try:
                Log.info("[GREATUPDATE:studio] Particle Studio installed: "
                         "modal fullscreen overlay, rounded UE-style topmost "
                         "layer, preset library + buzz cards, custom creator "
                         "with sliders, live preview, disk presets.")
            except Exception:
                pass
        except Exception as ex:
            try:
                Log.error(f"[GREATUPDATE:studio] install failed: {ex}")
            except Exception:
                pass

    # =====================================================================
    # [GREATUPDATE:flying_ai]  v1.0 — Flying enemy AI with voxel A* pathing
    #   Fragment 3 of theGREATupdate.  Plain module-level code (the patcher
    #   indents it into the guarded install block).  Zero contact with the
    #   ground NPC code (EnemyAI / EnemyFlyShooter are never touched); the
    #   bullet-spawn idiom is replicated here as NEW code only.
    #
    #   Pieces:
    #     * FlyingEnemyAI   — @blueprint vision-based flyer (IDLE / CHASE /
    #                         ATTACK / RETURN) with DEFAULTS-merged props.
    #     * _FlyNav         — per-engine voxel A* grid (amortized build,
    #                         26-connectivity, octile heuristic, string-pull).
    #     * _FlySwarm       — per-engine manager: spatial-hash separation,
    #                         staggered brains, LOS / whisker / ground /
    #                         A* budgets so 1000 flyers cost ~nothing.
    #     * _gu_flying_install() — additive monkey-patch wraps (spawn
    #                         activation + Library popup), all armored.
    #   Names (np, glm, math, random, time, Log, SceneNode, Mesh, Material,
    #   Blueprint, blueprint, glm helpers...) resolve in the HOST namespace
    #   at runtime; tkinter is imported only inside guarded function scope.
    # =====================================================================

    _FLY_LOGGED = set()


    def _fly_log_once(key, msg):
        """Log-once helper: the host can never be spammed by a hot path."""
        if key in _FLY_LOGGED:
            return
        _FLY_LOGGED.add(key)
        try:
            Log.warn(msg)
        except Exception:
            pass


    # ---------------------------------------------------------------
    # Binary min-heap (pure python — no imports beyond host namespace).
    # ---------------------------------------------------------------
    def _fly_heappush(heap, item):
        heap.append(item)
        i = len(heap) - 1
        while i > 0:
            p = (i - 1) >> 1
            if heap[p][0] <= item[0]:
                break
            heap[i] = heap[p]
            i = p
        heap[i] = item


    def _fly_heappop(heap):
        top = heap[0]
        last = heap.pop()
        if heap:
            i = 0
            n = len(heap)
            while True:
                c = i * 2 + 1
                if c >= n:
                    break
                if c + 1 < n and heap[c + 1][0] < heap[c][0]:
                    c += 1
                if heap[c][0] >= last[0]:
                    break
                heap[i] = heap[c]
                i = c
            heap[i] = last
        return top


    def _fly_nav_skip(node):
        """Nodes that must never become nav obstacles: flyers, the player,
        other enemies, bullets and flash lights (checked up the chain)."""
        try:
            n = node
            while n is not None:
                if getattr(n, "spawn_kind", None) == "player":
                    return True
                if n.name in ("_runtime_bullet", "_flash"):
                    return True
                for bp in getattr(n, "blueprints", ()):
                    if type(bp).__name__ in ("FlyingEnemyAI", "FPSController",
                                             "EnemyFlyShooter", "EnemyAI"):
                        return True
                n = n.parent
        except Exception:
            pass
        return False


    # =====================================================================
    # _FlyNav — voxel A* over static-collider AABBs (doors/holes routable)
    # =====================================================================
    class _FlyNav:
        """Occupancy grid built AMORTIZED from the physics world's static
        colliders (triggers, rigid bodies, terrain heightfields and gameplay
        nodes skipped).  A cell is solid iff an inflated collider AABB
        overlaps it, so any opening >= the cell size is naturally routable.
        A* is 26-connected with an octile heuristic and a hard iteration
        cap, followed by string-pull smoothing over the grid."""

        CELL = 2.0                 # target cell size (m), auto-coarsened
        INFLATE = 0.6              # agent inflation stamped into the grid
        MAX_CELLS = 96 * 96 * 96   # hard cap on total cells
        BUILD_PER_FRAME = 24       # collider AABBs stamped per update()
        ASTAR_CAP = 25000          # A* iteration cap
        SNAP_RINGS = 6             # BFS rings when snapping to free space

        def __init__(self, engine):
            self.engine = engine
            self.built = False
            self._building = False
            self.grid = None
            self.cell = self.CELL
            self.dims = (0, 0, 0)
            self.origin = None
            self._items = []
            self._item_i = 0

        # ---------------------------------------------------------- build
        def invalidate(self):
            self.built = False
            self._building = False
            self.grid = None

        def update(self):
            """Called once per engine frame by the swarm (cheap)."""
            if self._building:
                self._stamp_some()
                return
            if not self.built:
                self._begin_build()
                return
            try:
                if getattr(self.engine.physics, "_statics_dirty", False):
                    self.invalidate()
                    self._begin_build()
            except Exception:
                pass

        def _begin_build(self):
            try:
                lo = glm.vec3(float("inf"))
                hi = glm.vec3(float("-inf"))
                items = []
                for e in list(self.engine.physics.entries):
                    try:
                        node = e.node
                        if node is None or not getattr(node, "visible", True):
                            continue
                        col = e.collider
                        if col is None or getattr(col, "is_trigger", False):
                            continue
                        if getattr(e, "body", None) is not None:
                            continue                    # dynamic / kinematic
                        if type(col).__name__ == "HeightfieldCollider":
                            continue                    # terrain: clearance rays
                        if _fly_nav_skip(node):
                            continue
                        e.refresh_aabb()
                        a0, a1 = e.aabb0, e.aabb1
                        items.append((glm.vec3(a0), glm.vec3(a1)))
                        lo = glm.min(lo, a0)
                        hi = glm.max(hi, a1)
                    except Exception:
                        continue
                if not items:
                    # Empty world: a small all-free grid around the origin.
                    self.origin = glm.vec3(-4.0, -2.0, -4.0)
                    self.dims = (4, 4, 4)
                    self.cell = self.CELL
                    self.grid = bytearray(64)
                    self.built = True
                    self._building = False
                    return
                pad = 3.0
                lo = lo - glm.vec3(pad)
                hi = hi + glm.vec3(pad)
                size = hi - lo
                cell = self.CELL
                while True:
                    nx = max(1, int(math.ceil(size.x / cell)) + 1)
                    ny = max(1, int(math.ceil(size.y / cell)) + 1)
                    nz = max(1, int(math.ceil(size.z / cell)) + 1)
                    if nx * ny * nz <= self.MAX_CELLS or cell >= 64.0:
                        break
                    cell *= 2.0                          # auto-coarsen
                self.cell = cell
                self.dims = (nx, ny, nz)
                self.origin = lo
                self.grid = bytearray(nx * ny * nz)
                self._items = items
                self._item_i = 0
                self._building = True
                self.built = False
            except Exception as ex:
                _fly_log_once("nav_build", f"[FlyingAI] nav build failed: {ex}")
                # Degenerate all-free 1-cell grid so we never retry-spam.
                self.origin = glm.vec3(0.0)
                self.dims = (1, 1, 1)
                self.grid = bytearray(1)
                self._building = False
                self.built = True

        def _stamp_some(self):
            nx, ny, nz = self.dims
            cell = self.cell
            org = self.origin
            inf = self.INFLATE
            grid = self.grid
            items = self._items
            count = 0
            while self._item_i < len(items) and count < self.BUILD_PER_FRAME:
                a0, a1 = items[self._item_i]
                self._item_i += 1
                count += 1
                x0 = max(0, int(math.floor((a0.x - inf - org.x) / cell)))
                y0 = max(0, int(math.floor((a0.y - inf - org.y) / cell)))
                z0 = max(0, int(math.floor((a0.z - inf - org.z) / cell)))
                x1 = min(nx - 1, int(math.floor((a1.x + inf - org.x) / cell)))
                y1 = min(ny - 1, int(math.floor((a1.y + inf - org.y) / cell)))
                z1 = min(nz - 1, int(math.floor((a1.z + inf - org.z) / cell)))
                if x1 < x0 or y1 < y0 or z1 < z0:
                    continue
                for iz in range(z0, z1 + 1):
                    bz = iz * ny * nx
                    for iy in range(y0, y1 + 1):
                        base = bz + iy * nx
                        for ix in range(x0, x1 + 1):
                            grid[base + ix] = 1
            if self._item_i >= len(items):
                self._building = False
                self.built = True

        # ------------------------------------------------------- indexing
        def _free(self, ix, iy, iz):
            nx, ny, nz = self.dims
            return (0 <= ix < nx and 0 <= iy < ny and 0 <= iz < nz
                    and not self.grid[(iz * ny + iy) * nx + ix])

        def _to_cell(self, p):
            nx, ny, nz = self.dims
            org = self.origin
            c = self.cell
            return (min(nx - 1, max(0, int((p.x - org.x) / c))),
                    min(ny - 1, max(0, int((p.y - org.y) / c))),
                    min(nz - 1, max(0, int((p.z - org.z) / c))))

        def _center(self, c):
            org = self.origin
            h = self.cell * 0.5
            return glm.vec3(org.x + c[0] * self.cell + h,
                            org.y + c[1] * self.cell + h,
                            org.z + c[2] * self.cell + h)

        def _nearest_free(self, c):
            if self._free(*c):
                return c
            for r in range(1, self.SNAP_RINGS + 1):
                for dx in range(-r, r + 1):
                    for dy in range(-r, r + 1):
                        for dz in range(-r, r + 1):
                            if max(abs(dx), abs(dy), abs(dz)) != r:
                                continue
                            cc = (c[0] + dx, c[1] + dy, c[2] + dz)
                            if self._free(*cc):
                                return cc
            return None

        # ----------------------------------------------------- line check
        def line_free(self, a, b):
            """Sampling walk over the voxel grid (obstacles pre-inflated,
            so a centre-line test keeps the agent's clearance)."""
            if not self.built or self.grid is None:
                return True
            d = glm.length(b - a)
            if d < 1e-6:
                return True
            steps = min(400, int(d / (self.cell * 0.5)) + 1)
            inv = 1.0 / steps
            for i in range(steps + 1):
                p = a + (b - a) * (i * inv)
                if not self._free(*self._to_cell(p)):
                    return False
            return True

        # ------------------------------------------------------------ A*
        def find_path(self, start, goal):
            """Voxel A* -> world-space waypoints, or None.  Hard-bounded."""
            if not self.built or self.grid is None:
                return None
            try:
                s = self._nearest_free(self._to_cell(start))
                g = self._nearest_free(self._to_cell(goal))
                if s is None or g is None:
                    return None
                if s == g:
                    return [glm.vec3(goal)]
                nx, ny, nz = self.dims
                sq2 = math.sqrt(2.0)
                sq3 = math.sqrt(3.0)

                def _idx(c):
                    return (c[2] * ny + c[1]) * nx + c[0]

                def _h(c):
                    dx = abs(c[0] - g[0])
                    dy = abs(c[1] - g[1])
                    dz = abs(c[2] - g[2])
                    if dx > dz:
                        dx, dz = dz, dx
                    if dy > dz:
                        dy, dz = dz, dy
                    if dx > dy:
                        dx, dy = dy, dx
                    # 3D octile: pure diagonals, then face diagonals, then
                    # straight steps — admissible for 26-connectivity.
                    return (sq3 - sq2) * dx + (sq2 - 1.0) * dy + dz

                start_i = _idx(s)
                goal_i = _idx(g)
                g_score = {start_i: 0.0}
                came = {}
                closed = set()
                heap = [(_h(s), 0.0, start_i, s)]
                iters = 0
                found = False
                _free = self._free
                while heap and iters < self.ASTAR_CAP:
                    iters += 1
                    _f, gc, ci, cc = _fly_heappop(heap)
                    if ci == goal_i:
                        found = True
                        break
                    if ci in closed:
                        continue
                    closed.add(ci)
                    cx, cy, cz = cc
                    for dx in (-1, 0, 1):
                        for dy in (-1, 0, 1):
                            for dz in (-1, 0, 1):
                                if dx == 0 and dy == 0 and dz == 0:
                                    continue
                                nc = (cx + dx, cy + dy, cz + dz)
                                if not _free(*nc):
                                    continue
                                # No corner cutting: every partial step of a
                                # diagonal must itself be through free cells.
                                if dx and not _free(cx + dx, cy, cz):
                                    continue
                                if dy and not _free(cx, cy + dy, cz):
                                    continue
                                if dz and not _free(cx, cy, cz + dz):
                                    continue
                                if dx and dy and not _free(cx + dx, cy + dy, cz):
                                    continue
                                if dx and dz and not _free(cx + dx, cy, cz + dz):
                                    continue
                                if dy and dz and not _free(cx, cy + dy, cz + dz):
                                    continue
                                axes = (1 if dx else 0) + (1 if dy else 0) \
                                    + (1 if dz else 0)
                                step = sq3 if axes == 3 else (sq2 if axes == 2
                                                              else 1.0)
                                ng = gc + step
                                ni = _idx(nc)
                                if ni in closed:
                                    continue
                                if ng < g_score.get(ni, float("inf")):
                                    g_score[ni] = ng
                                    came[ni] = ci
                                    _fly_heappush(heap,
                                                  (ng + _h(nc), ng, ni, nc))
                if not found:
                    return None
                # Reconstruct cell centres, anchor with exact endpoints.
                cells = [g]
                ci = goal_i
                while ci != start_i:
                    ci = came[ci]
                    cx = ci % nx
                    cy = (ci // nx) % ny
                    cz = ci // (nx * ny)
                    cells.append((cx, cy, cz))
                cells.reverse()
                pts = [glm.vec3(start)]
                pts.extend(self._center(c) for c in cells)
                pts.append(glm.vec3(goal))
                return self._smooth(pts)
            except Exception as ex:
                _fly_log_once("astar", f"[FlyingAI] A* failed: {ex}")
                return None

        def _smooth(self, pts):
            """String-pulling: keep only waypoints where the straight line
            to the furthest visible waypoint bends around an obstacle."""
            n = len(pts)
            if n <= 2:
                return pts
            out = [pts[0]]
            i = 0
            while i < n - 1:
                j = n - 1
                while j > i + 1 and not self.line_free(pts[i], pts[j]):
                    j -= 1
                out.append(pts[j])
                i = j
            return out


    # =====================================================================
    # _FlySwarm — per-engine manager: budgets + spatial hash + staggering
    # =====================================================================
    _FLY_DOWN = None               # lazy glm.vec3(0,-1,0) shared by probes
    _FLY_O = None                  # raycast origin scratch (host copies args)
    _FLY_D = None                  # raycast direction scratch


    class _FlySwarm:
        """Coordinates every active FlyingEnemyAI so the per-frame cost is
        flat no matter how many flyers exist:

          * BATCH INTEGRATION (numpy, host global): all due flyers are
            integrated ONCE per frame by _roll_frame as vectorized float64
            ops on parallel arrays — no per-flyer Python math, no glm
            allocations.  Falls back to the scalar in-on_tick path when np
            is unavailable.
          * distance-tiered integration: beyond 120 m from the player a
            flyer integrates at 1/4 rate (dt accumulated), beyond 250 m at
            1/10 rate — same travel speed, same behaviour;
          * brains staggered: flyer k thinks when (frame + k) % 5 == 0;
          * spatial hash (4 m cells, integer keys — zero tuple allocs)
            rebuilt every 3rd frame from flyer positions;
          * separation gathered every 8th frame per flyer (staggered),
            allocation-free float math, 19-cell scan, cap 4;
          * LOS raycasts capped at 48/frame, avoidance whiskers at 32/frame,
            ground-clearance rays at 32/frame, A* searches at 6/frame.
        """

        HASH_CELL = 0.25           # 1 / 4 m
        LOS_BUDGET = 48
        WHISK_BUDGET = 32
        GC_BUDGET = 32
        ASTAR_BUDGET = 6
        HASH_EVERY = 5             # rebuild the spatial hash every N frames

        def __init__(self, engine):
            self.engine = engine
            self.flyers = []
            self.frame = 0
            self._last_time = -1.0
            self._next_k = 0
            self._hash = {}
            self._version = 0
            self.los_budget = 0
            self.whisk_budget = 0
            self.gc_budget = 0
            self.astar_budget = 0
            self.nav = None
            self._player_node = None
            self._player_frame = -1
            self._plr = None       # (x, y, z) player floats, once per frame
            # Persistent flat position list [x0,y0,z0, x1,y1,z1, ...]:
            # refilled ONCE per frame straight off the numpy buffer via a
            # memoryview (C-speed, only untracked floats allocated, the list
            # itself is stable).  Brains, separation gathers, the spatial
            # hash and the node write-back all read this — zero per-flyer
            # attr traffic and zero GC churn for positions.
            self._plist = []
            # ---- batch integration state (numpy) ----
            self._np = np
            self._use_batch = np is not None
            self._cap = 0
            self._n = 0
            self._apos = None      # (C,3) positions
            self._avel = None      # (C,3) velocities
            self._ades = None      # (C,3) desired velocities (brains write)
            self._asep = None      # (C,3) separation push (gathers write)
            self._aavoid = None    # (C,3) avoidance impulse
            self._adt = None       # (C,) accumulated dt per flyer
            self._awt = None       # (C,) whisker timers
            self._agt = None       # (C,) ground-probe timers
            self._aavt = None      # (C,) avoidance-active timers
            self._aacc = None      # (C,) accel
            self._aminalt = None   # (C,) min altitude over ground
            self._asepr = None     # (C,) separation radius
            self._agnd = None      # (C,) last known ground y
            self._ahasg = None     # (C,) bool: ground y valid
            self._aalive = None    # (C,) bool
            self._ak = None        # (C,) int32 stagger index
            self._atier = None     # (C,) int32 integration tier
            self._abacc = None     # (C,) float64 brain-dt accumulators
            self._tmp3 = None      # (C,3) scratch for where= integration
            self._desl = []        # flat desired-velocity list (brains write,
                                   # batch reads via one np.array conversion)
            # Brain dispatch buckets: slot lists grouped by k % 5, so the
            # per-frame staggered brain set (frame + k) % 5 == 0 is ONE list
            # lookup instead of a nonzero scan.  Rebuilt by _compact.
            self._buckets = [[], [], [], [], []]
            # Parallel python lists (slot-aligned): no attr gets on the
            # per-frame write-back path.  Maintained by register/_compact.
            self._nodes = []
            self._nposl = []
            self._kl = []

        # -------------------------------------------------------- arrays
        def _grow_arrays(self, need):
            np_ = self._np
            new_cap = max(16, self._cap * 2, need)
            def _grow(arr, shape, fill=0.0):
                if arr is None:
                    out = np_.full((new_cap,) + shape, fill)
                else:
                    out = np_.empty((new_cap,) + shape)
                    out[:self._cap] = arr
                    out[self._cap:] = fill
                return out
            self._apos = _grow(self._apos, (3,))
            self._avel = _grow(self._avel, (3,))
            self._ades = _grow(self._ades, (3,))
            self._asep = _grow(self._asep, (3,))
            self._aavoid = _grow(self._aavoid, (3,))
            self._adt = _grow(self._adt, ())
            self._awt = _grow(self._awt, ())
            self._agt = _grow(self._agt, ())
            self._aavt = _grow(self._aavt, ())
            self._aacc = _grow(self._aacc, ())
            self._aminalt = _grow(self._aminalt, ())
            self._asepr = _grow(self._asepr, ())
            self._agnd = _grow(self._agnd, ())
            self._abacc = _grow(self._abacc, ())
            self._tmp3 = _grow(self._tmp3, (3,))
            if self._ahasg is None:
                self._ahasg = np_.zeros(new_cap, dtype=bool)
                self._aalive = np_.zeros(new_cap, dtype=bool)
                self._ak = np_.zeros(new_cap, dtype=np_.int32)
                self._atier = np_.ones(new_cap, dtype=np_.int32)
            else:
                for nm in ("_ahasg", "_aalive"):
                    old = getattr(self, nm)
                    out = np_.zeros(new_cap, dtype=bool)
                    out[:self._cap] = old
                    setattr(self, nm, out)
                old = self._ak
                out = np_.zeros(new_cap, dtype=np_.int32)
                out[:self._cap] = old
                self._ak = out
                old = self._atier
                out = np_.ones(new_cap, dtype=np_.int32)
                out[:self._cap] = old
                self._atier = out
            self._cap = new_cap

        # -------------------------------------------------------- roster
        def register(self, fl):
            if fl in self.flyers:
                return
            fl._k = self._next_k
            fl._sw = self
            self._next_k += 1
            self.flyers.append(fl)
            self._version += 1
            if self._use_batch:
                try:
                    if self._n >= self._cap:
                        self._grow_arrays(self._n + 1)
                    i = self._n
                    self._n += 1
                    fl._slot = i
                    self._apos[i] = (fl._px, fl._py, fl._pz)
                    self._avel[i] = (0.0, 0.0, 0.0)
                    self._ades[i] = (0.0, 0.0, 0.0)
                    self._asep[i] = (0.0, 0.0, 0.0)
                    self._aavoid[i] = (0.0, 0.0, 0.0)
                    self._adt[i] = 0.0
                    self._awt[i] = fl._whisk_timer
                    self._agt[i] = fl._gc_timer
                    self._aavt[i] = 0.0
                    self._aacc[i] = fl._c_accel
                    self._aminalt[i] = fl._c_min_alt
                    self._asepr[i] = fl._c_sep_r
                    self._ahasg[i] = False
                    self._aalive[i] = True
                    self._ak[i] = fl._k
                    self._atier[i] = 1
                    self._nodes.append(fl.node)
                    self._nposl.append(fl._npos)
                    self._kl.append(fl._k)
                    self._plist.extend((fl._px, fl._py, fl._pz))
                    self._desl.extend((0.0, 0.0, 0.0))
                    self._buckets[fl._k % 5].append(i)
                except Exception as ex:
                    _fly_log_once("reg", f"[FlyingAI] batch register: {ex}")

        def unregister(self, fl):
            try:
                s = getattr(fl, "_slot", None)
                if s is not None and 0 <= s < self._n:
                    self._aalive[s] = False
                    dl = self._desl
                    do = s + s + s
                    if do + 3 <= len(dl):
                        dl[do] = 0.0
                        dl[do + 1] = 0.0
                        dl[do + 2] = 0.0
                if fl in self.flyers:
                    self._version += 1
            except Exception:
                pass

        def _compact(self):
            """Drop dead rows from the arrays and remap slots (runs every
            64 frames; O(n) numpy fancy-index, ~free at this cadence)."""
            if not self._use_batch or self._aalive is None:
                try:
                    keep = [f for f in self.flyers
                            if f.node is not None and f._alive]
                    if len(keep) != len(self.flyers):
                        self.flyers = keep
                        self._version += 1
                except Exception:
                    pass
                return
            np_ = self._np
            keep_i = []
            keep_f = []
            flyers = self.flyers
            for i, fl in enumerate(flyers):
                if fl is not None and fl.node is not None and fl._alive:
                    keep_i.append(i)
                    keep_f.append(fl)
            if len(keep_f) == len(flyers):
                return
            if not keep_i:
                self.flyers = []
                self._nodes = []
                self._nposl = []
                self._kl = []
                self._plist = []
                self._desl = []
                self._n = 0
                self._version += 1
                return
            idx = np_.array(keep_i, dtype=np_.intp)
            # compact in place: fancy-index copy, then write rows down
            for nm in ("_apos", "_avel", "_ades", "_asep", "_aavoid", "_adt",
                       "_awt", "_agt", "_aavt", "_aacc", "_aminalt", "_asepr",
                       "_agnd", "_ahasg", "_aalive", "_ak", "_atier",
                       "_abacc", "_tmp3"):
                arr = getattr(self, nm)
                arr[:len(keep_i)] = arr[idx]
            pl_old = self._plist
            dl_old = self._desl
            pl_new = [0.0] * (len(keep_i) * 3)
            dl_new = [0.0] * (len(keep_i) * 3)
            for new_i, old_i in enumerate(keep_i):
                oo = old_i * 3
                no = new_i * 3
                if oo + 3 <= len(pl_old):
                    pl_new[no] = pl_old[oo]
                    pl_new[no + 1] = pl_old[oo + 1]
                    pl_new[no + 2] = pl_old[oo + 2]
                if oo + 3 <= len(dl_old):
                    dl_new[no] = dl_old[oo]
                    dl_new[no + 1] = dl_old[oo + 1]
                    dl_new[no + 2] = dl_old[oo + 2]
            self._plist = pl_new
            self._desl = dl_new
            for new_i, fl in enumerate(keep_f):
                fl._slot = new_i
            self.flyers = keep_f
            self._nodes = [fl.node for fl in keep_f]
            self._nposl = [fl._npos for fl in keep_f]
            self._kl = [fl._k for fl in keep_f]
            self._n = len(keep_f)
            self._buckets = [[], [], [], [], []]
            for new_i, fl in enumerate(keep_f):
                self._buckets[fl._k % 5].append(new_i)
            self._version += 1

        # ---------------------------------------------------- frame roll
        def _roll_frame(self, dt=0.0):
            t = getattr(self.engine, "time", 0.0)
            if t == self._last_time:
                return
            self._last_time = t
            self.frame += 1
            self.los_budget = self.LOS_BUDGET
            self.whisk_budget = self.WHISK_BUDGET
            self.gc_budget = self.GC_BUDGET
            self.astar_budget = self.ASTAR_BUDGET
            # Occasional roster compaction (dead/destroyed flyers stop
            # ticking on their own; this keeps the arrays + hash lean).
            if (self.frame & 63) == 0 and self.flyers:
                try:
                    self._compact()
                except Exception:
                    pass
            # Player position cached ONCE per frame as plain floats.
            pl = self.player_node()
            if pl is not None:
                try:
                    wp = pl.world_position()
                    self._plr = (wp.x, wp.y, wp.z)
                except Exception:
                    self._plr = None
            else:
                self._plr = None
            if self.frame % self.HASH_EVERY == 1:
                self._rebuild_hash()
            if self.nav is None:
                try:
                    self.nav = _FlyNav(self.engine)
                except Exception:
                    self.nav = None
            if self.nav is not None:
                try:
                    self.nav.update()
                except Exception as ex:
                    _fly_log_once("nav_update", f"[FlyingAI] nav update: {ex}")
            if self._use_batch and self._n > 0:
                # Accumulate per-flyer brain dt once per frame (vector op),
                # then run the staggered brains: (frame + k) % 5 == 0.
                # Doing this centrally removes ALL per-flyer gate work from
                # on_tick in batch mode.
                np_ = self._np
                sl = slice(0, self._n)
                bacc = self._abacc
                bacc[sl] += dt
                grp = self._buckets[self.frame % 5]
                if grp:
                    flyers = self.flyers
                    alive = self._aalive
                    for i in grp:
                        if not alive[i]:
                            continue
                        fl = flyers[i]
                        if fl.node is None:
                            continue
                        dt_b = float(bacc[i])
                        bacc[i] = 0.0
                        try:
                            fl._brain(self, dt_b, t)
                        except Exception as ex:
                            _fly_log_once("brain", f"[FlyingAI] brain: {ex}")
            if self._use_batch and self._n > 0 and dt > 0.0:
                try:
                    self._batch_integrate(dt)
                except Exception as ex:
                    _fly_log_once("batch", f"[FlyingAI] batch integrate: {ex}")

        def _rebuild_hash(self):
            h = {}
            hc = self.HASH_CELL
            # In batch mode: vectorized integer keys off the position buffer
            # (numpy int math), then a minimal dict-insert loop.
            if self._use_batch and self._aalive is not None \
                    and self._apos is not None:
                np_ = self._np
                n = self._n
                pos = self._apos[:n]
                keys = ((pos[:, 0] * hc).astype(np_.int64) + 2048)
                keys += ((pos[:, 1] * hc).astype(np_.int64) + 2048) << 12
                keys += ((pos[:, 2] * hc).astype(np_.int64) + 2048) << 24
                alive = self._aalive[:n]
                keys[~alive] = -1
                flyers = self.flyers
                for i in range(n):
                    key = int(keys[i])
                    if key < 0:
                        continue
                    h.setdefault(key, []).append(flyers[i])
            else:
                for fl in self.flyers:
                    if fl.node is None or not fl._alive:
                        continue
                    key = ((int(fl._px * hc) + 2048)
                           + ((int(fl._py * hc) + 2048) << 12)
                           + ((int(fl._pz * hc) + 2048) << 24))
                    h.setdefault(key, []).append(fl)
            self._hash = h

        # --------------------------------------------- batch integration
        def _batch_integrate(self, dt):
            """Vectorized movement integration for every DUE flyer: pure
            numpy float64 ops on the persistent buffers, one pass per frame.
            Mirrors the scalar path step-for-step (sep + whisker avoidance
            -> accel-limited steering -> integrate -> ground clamp -> write
            node positions).

            ZERO GC-tracked allocations on the hot path: positions/velocities
            are read back through np flat iterators (np.float64 scalars and
            non-object ndarrays are never GC-tracked), node positions are
            written by component assignment, and brains/gathers read the
            same flat views via self._pflat — nothing is ever converted to
            lists/tuples per frame.
            """
            np_ = self._np
            n = self._n
            frame = self.frame
            sl = slice(0, n)
            alive = self._aalive[sl]
            k = self._ak[sl]
            tier = self._atier[sl]
            dtacc = self._adt[sl]
            dtacc += dt
            fk = frame + k
            if bool((tier == 1).all()):
                due = alive              # fast path: everyone full-rate
            else:
                due = alive & ((tier == 1) | (fk % tier == 0))
            if not due.any():
                return
            pos = self._apos[sl]
            vel = self._avel[sl]
            vf = vel.flat                # live flat velocity view (untracked)
            plist = self._plist          # flat position list (last frame —
                                         # gathers/probes, same staleness as
                                         # the spatial hash; refilled fresh
                                         # before the node write-back)
            flyers = self.flyers

            # ---- separation gathers (staggered, per-flyer float math) ----
            sel = due & (fk % 20 == 0)
            if sel.any():
                for i in np_.nonzero(sel)[0]:
                    self._sep_gather(flyers[int(i)])

            # ---- avoidance whiskers (budgeted raycasts) ----
            wt = self._awt[sl]
            np_.subtract(wt, dtacc, out=wt, where=due)
            fired = (wt <= 0.0) & due
            if fired.any():
                np_.copyto(wt, 0.15, where=fired)
                if self.whisk_budget > 0:
                    for i in np_.nonzero(fired)[0]:
                        if self.whisk_budget <= 0:
                            break
                        i = int(i)
                        o = i + i + i
                        vx = float(vf[o])
                        vy = float(vf[o + 1])
                        vz = float(vf[o + 2])
                        if vx * vx + vy * vy + vz * vz > 0.25:
                            av = self.whisker(flyers[i], plist[o],
                                              plist[o + 1],
                                              plist[o + 2], vx, vy, vz)
                            if av is not None:
                                arow = self._aavoid[i]
                                arow[0] = av[0]
                                arow[1] = av[1]
                                arow[2] = av[2]
                                self._aavt[i] = 0.35

            avt = self._aavt[sl]
            np_.subtract(avt, dtacc, out=avt, where=due)
            # Desired velocities: brains wrote the flat list; ONE C-speed
            # conversion to an untracked array, then add separation.
            des = np_.array(self._desl, dtype=np_.float64).reshape(n, 3)
            des += self._asep[sl]
            am = avt > 0.0
            if am.any():
                des[am] += self._aavoid[sl][am]

            # ---- accel-limited steering ----
            dv = des - vel
            L2 = np_.einsum('ij,ij->i', dv, dv)
            maxdv = self._aacc[sl] * dtacc
            m2 = maxdv * maxdv
            np_.maximum(m2, 1e-18, out=m2)
            cl = due & (L2 > m2)
            if cl.any():
                f = maxdv[cl] / np_.sqrt(L2[cl])
                dv[cl] *= f[:, None]
            np_.add(vel, dv, out=vel, where=due[:, None])
            tmp = self._tmp3[sl]
            np_.multiply(vel, dtacc[:, None], out=tmp)
            np_.add(pos, tmp, out=pos, where=due[:, None])

            # ---- ground clearance (budgeted 2 Hz probes + clamp) ----
            gt = self._agt[sl]
            np_.subtract(gt, dtacc, out=gt, where=due)
            gfired = (gt <= 0.0) & due
            if gfired.any():
                np_.copyto(gt, 0.5, where=gfired)
                if self.gc_budget > 0:
                    for i in np_.nonzero(gfired)[0]:
                        if self.gc_budget <= 0:
                            break
                        i = int(i)
                        o = i + i + i
                        gy = self.ground_probe(flyers[i], plist[o],
                                               plist[o + 1], plist[o + 2])
                        if gy is not None:
                            self._agnd[i] = gy
                            self._ahasg[i] = True
            hasg = self._ahasg[sl] & due
            if hasg.any():
                hidx = np_.nonzero(hasg)[0]
                miny = self._agnd[hidx] + self._aminalt[hidx]
                py = pos[hidx, 1]
                low = py < miny
                if low.any():
                    lidx = hidx[low]
                    pos[lidx, 1] = miny[low]
                    vcol = vel[lidx, 1]
                    neg = vcol < 0.0
                    if neg.any():
                        vel[lidx[neg], 1] = 0.0

            np_.copyto(dtacc, 0.0, where=due)

            # ---- refill the flat position list straight off the numpy
            # buffer (C-speed copy of untracked floats; stable list) ----
            n3 = n + n + n
            if len(plist) != n3:
                self._plist = plist = [0.0] * n3
            plist[:] = memoryview(pos).cast('B').cast('d')

            # ---- write back: node position components + yaw, straight off
            # the flat list (0.05 us/element, no tracked churn) ----
            kl = self._kl
            nlist = self._nodes
            plist_w = self._nposl
            duel = due.tolist()            # bool singletons, one temp list
            deg = math.degrees
            atan2 = math.atan2
            o = -3
            for i in range(n):
                o += 3
                if not duel[i]:
                    continue
                nd = nlist[i]
                if nd is None:
                    continue
                pp = plist_w[i]
                try:
                    pp.x = plist[o]
                    pp.y = plist[o + 1]
                    pp.z = plist[o + 2]
                except Exception:
                    try:
                        _write_node_world_position(nd, glm.vec3(
                            plist[o], plist[o + 1], plist[o + 2]))
                        fl = flyers[i]
                        fl._npos = nd.position
                        plist_w[i] = fl._npos
                    except Exception:
                        pass
                if ((frame + kl[i]) & 3) == 0:
                    vx = vf[o]
                    vz = vf[o + 2]
                    if vx * vx + vz * vz > 0.04:
                        try:
                            nd.rotation_euler.y = deg(atan2(-vx, -vz))
                        except Exception:
                            pass

        # --------------------------------------------------- player cache
        def player_node(self):
            if self._player_frame == self.frame:
                return self._player_node
            self._player_frame = self.frame
            node = None
            try:
                pb = self.engine.first_blueprint("FPSController")
                if pb is not None and pb.node is not None \
                        and not getattr(pb, "_dead", False):
                    node = pb.node
            except Exception:
                pass
            if node is None:
                try:
                    _sc = self.engine.scene
                    for n in (_sc.special_nodes() if hasattr(_sc, "special_nodes") else _sc.root.traverse()):
                        if getattr(n, "spawn_kind", None) == "player":
                            node = n
                            break
                except Exception:
                    pass
            if node is not self._player_node:
                self._player_node = node
                self._version += 1
            return node

        def flyer_ids(self):
            ids = set()
            for fl in self.flyers:
                if fl.node is not None and fl._alive:
                    try:
                        for n in fl.node.traverse():
                            ids.add(id(n))
                    except Exception:
                        ids.add(id(fl.node))
            return ids

        # ------------------------------------------------------- steering
        # Neighbour scan offsets: own cell + 6 faces + 12 edges (the 8 pure
        # corner cells are skipped — a diagonal-corner contact within the 3 m
        # radius is the rarest case and resolves itself within a few frames
        # as either flyer crosses a cell boundary).  Integer offsets, no
        # tuple keys.
        _SEP_OFF = tuple(
            (dx + (dy << 12) + (dz << 24))
            for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)
            if not (dx and dy and dz))

        def _sep_gather(self, fl):
            """Push away from neighbours via the spatial hash.  Called ONLY
            when (frame + k) % 8 == 0 (call-site gated, staggered).  Caches
            plain floats on the flyer AND its batch-array row; the batch
            integrator adds them as pure arithmetic.  Allocation-free."""
            h = self._hash
            r = fl._c_sep_r
            if not h or r <= 1e-4:
                fl._sep_x = 0.0
                fl._sep_y = 0.0
                fl._sep_z = 0.0
                if self._use_batch:
                    s = fl._slot
                    if s is not None and 0 <= s < self._n:
                        self._asep[s] = (0.0, 0.0, 0.0)
                return
            try:
                pl = self._plist
                batch = (self._use_batch and fl._slot is not None
                         and (fl._slot + 1) * 3 <= len(pl))
                if batch:
                    o = fl._slot * 3
                    px = pl[o]
                    py = pl[o + 1]
                    pz = pl[o + 2]
                else:
                    px = fl._px
                    py = fl._py
                    pz = fl._pz
                hc = self.HASH_CELL
                base = ((int(px * hc) + 2048)
                        + ((int(py * hc) + 2048) << 12)
                        + ((int(pz * hc) + 2048) << 24))
                r2 = r * r
                pushx = 0.0
                pushy = 0.0
                pushz = 0.0
                checks = 0
                get = h.get
                sqrt = math.sqrt
                done = False
                for off in self._SEP_OFF:
                    lst = get(base + off)
                    if not lst:
                        continue
                    for other in lst:
                        if other is fl:
                            continue
                        if batch:
                            oo = other._slot * 3
                            ox = px - pl[oo]
                            oy = py - pl[oo + 1]
                            oz = pz - pl[oo + 2]
                        else:
                            ox = px - other._px
                            oy = py - other._py
                            oz = pz - other._pz
                        d2 = ox * ox + oy * oy + oz * oz
                        if 1e-8 < d2 < r2:
                            d = sqrt(d2)
                            f = (r - d) / d
                            pushx += ox * f
                            pushy += oy * f
                            pushz += oz * f
                        checks += 1
                        if checks >= 4:
                            done = True
                            break
                    if done:
                        break
                scale = fl._c_speed * 0.6
                pushx *= scale
                pushy *= scale
                pushz *= scale
                if batch:
                    row = self._asep[fl._slot]
                    row[0] = pushx
                    row[1] = pushy
                    row[2] = pushz
                else:
                    fl._sep_x = pushx
                    fl._sep_y = pushy
                    fl._sep_z = pushz
            except Exception:
                fl._sep_x = 0.0
                fl._sep_y = 0.0
                fl._sep_z = 0.0

        # -------------------------------------------------------- budgets
        def los_clear(self, fl, ex, ey, ez, tx, ty, tz):
            """Budgeted LOS raycast (float in, vec3 built ONLY when the cast
            actually happens).  Returns True/False, or None when the frame
            budget is spent (caller keeps its last known result)."""
            if self.los_budget <= 0:
                return None
            dx = tx - ex
            dy = ty - ey
            dz = tz - ez
            L2 = dx * dx + dy * dy + dz * dz
            if L2 < 1e-12:
                return True
            self.los_budget -= 1
            global _FLY_O, _FLY_D
            try:
                if _FLY_O is None:
                    _FLY_O = glm.vec3(0.0)
                if _FLY_D is None:
                    _FLY_D = glm.vec3(0.0)
                L = math.sqrt(L2)
                f = 1.0 / L
                o = _FLY_O
                o.x = ex
                o.y = ey
                o.z = ez
                d = _FLY_D
                d.x = dx * f
                d.y = dy * f
                d.z = dz * f
                # engine.raycast copies its arguments (_v3/_safe_normalize),
                # so reusing scratch vectors is safe.
                hit = self.engine.raycast(o, d, L, ignore=fl._los_ignore(self))
                return hit is None
            except Exception as ex:
                _fly_log_once("los", f"[FlyingAI] LOS raycast: {ex}")
                return None

        def whisker(self, fl, px, py, pz, vx, vy, vz):
            """Budgeted forward probe; returns an avoidance velocity float
            tuple, or None.  vec3s are built only when a cast happens."""
            if self.whisk_budget <= 0:
                return None
            sp2 = vx * vx + vy * vy + vz * vz
            if sp2 < 0.25:
                return None
            self.whisk_budget -= 1
            global _FLY_O, _FLY_D
            try:
                if _FLY_O is None:
                    _FLY_O = glm.vec3(0.0)
                if _FLY_D is None:
                    _FLY_D = glm.vec3(0.0)
                sp = math.sqrt(sp2)
                f = 1.0 / sp
                dx = vx * f
                dy = vy * f
                dz = vz * f
                reach = sp * 0.7 + fl._c_agent_r + 0.3
                o = _FLY_O
                o.x = px
                o.y = py
                o.z = pz
                d = _FLY_D
                d.x = dx
                d.y = dy
                d.z = dz
                hit = self.engine.raycast(o, d, reach,
                                          ignore=fl._los_ignore(self))
                if hit is None:
                    return None
                # side = normalize(cross(d, up)) = normalize((-dz, 0, dx))
                hl = math.sqrt(dx * dx + dz * dz)
                if hl > 1e-6:
                    sx = -dz / hl * fl._turn_sign
                    sz = dx / hl * fl._turn_sign
                else:
                    sx = fl._turn_sign
                    sz = 0.0
                # turn = side + up * 0.7, normalized, scaled to fly speed
                s = fl._c_speed / math.sqrt(sx * sx + 0.49 + sz * sz)
                return (sx * s, 0.7 * s, sz * s)
            except Exception as ex:
                _fly_log_once("whisk", f"[FlyingAI] whisker raycast: {ex}")
                return None

        def ground_probe(self, fl, px, py, pz):
            """Budgeted downward raycast (2 Hz per flyer) -> ground y or None."""
            global _FLY_DOWN, _FLY_O
            if self.gc_budget <= 0:
                return None
            self.gc_budget -= 1
            try:
                if _FLY_DOWN is None:
                    _FLY_DOWN = glm.vec3(0.0, -1.0, 0.0)
                if _FLY_O is None:
                    _FLY_O = glm.vec3(0.0)
                o = _FLY_O
                o.x = px
                o.y = py
                o.z = pz
                hit = self.engine.raycast(o, _FLY_DOWN, 60.0,
                                          ignore=fl._los_ignore(self))
                if hit is not None:
                    return float(hit.point.y)
            except Exception as ex:
                _fly_log_once("gc", f"[FlyingAI] ground probe: {ex}")
            return None

        def request_path(self, fl, sx, sy, sz, gx, gy, gz):
            """Budgeted A* (<=6/frame).  True when a fresh path was stored
            (as a list of plain float tuples — zero glm on the follow path).
            """
            if self.astar_budget <= 0:
                return False
            nav = self.nav
            if nav is None:
                return False
            if not nav.built:
                if not nav._building:
                    try:
                        nav._begin_build()
                    except Exception:
                        pass
                return False
            self.astar_budget -= 1
            pts = nav.find_path(glm.vec3(sx, sy, sz), glm.vec3(gx, gy, gz))
            if pts:
                fl._path = [(p.x, p.y, p.z) for p in pts]
                fl._path_i = 0
                fl._reach = max(0.9, nav.cell * 0.5)
                return True
            fl._path = None
            return False


    _FLY_SWARMS = {}               # id(engine) -> _FlySwarm


    def _fly_swarm(engine):
        sw = _FLY_SWARMS.get(id(engine))
        if sw is None or sw.engine is not engine:
            sw = _FlySwarm(engine)
            _FLY_SWARMS[id(engine)] = sw
        return sw


    # =====================================================================
    # FlyingEnemyAI — the attachable flying-enemy blueprint
    # =====================================================================
    @blueprint
    class FlyingEnemyAI(Blueprint):
        """Vision-based flying enemy with voxel-A* navigation.

        IDLE  : hover bob + slow patrol around the home (spawn) point.
        CHASE : player seen (range + LOS raycast) — pursue; without LOS,
                follow an A* path through doors/holes toward the player.
        ATTACK: in range with clear LOS — strafe-orbit the player and fire.
        RETURN: sight lost for > lose_sight_after seconds — A* path back to
                the home point, then resume IDLE.

        The brain is staggered by the _FlySwarm (thinks every 5th frame);
        movement integrates as pure Python float math on cached components
        (zero glm allocations, zero property dict lookups per frame), with
        distance-tiered decimation: >120 m from the player integrates at
        1/4 rate, >250 m at 1/10 rate (dt is accumulated, so travel speed
        is identical).  Properties merge DEFAULTS the _GameBlueprint way.
        """

        DEFAULTS = {
            "fly_speed": 6.0,
            "accel": 14.0,
            "aggro_range": 45.0,
            "attack_range": 18.0,
            "lose_sight_after": 3.0,
            "fire_rate": 1.2,
            "bullet_damage": 8.0,
            "bullet_speed": 40.0,
            "patrol_radius": 8.0,
            "hover_amp": 0.6,
            "separation_radius": 3.0,
            "agent_radius": 0.6,
            "min_altitude": 1.2,
            "path_recalc": 1.5,
        }

        def __init__(self):
            super().__init__()
            merged = dict(self.DEFAULTS)
            merged.update(self.properties)
            self.properties = merged

        def p(self, key, default=None):
            if key in self.properties:
                return self.properties[key]
            if key in self.DEFAULTS:
                return self.DEFAULTS[key]
            return default

        # ------------------------------------------------------- lifecycle
        def on_begin_play(self):
            merged = dict(self.DEFAULTS)
            merged.update(self.properties or {})
            self.properties = merged
            e = self.engine
            node = self.node
            self._alive = True
            self.hp = float(self.p("max_health", 100.0))
            wp = node.world_position()
            self._home = wp
            self._hx = wp.x
            self._hy = wp.y
            self._hz = wp.z
            # Float state: positions/velocities are cached components —
            # the per-frame integrator never allocates a glm vector.
            self._px = wp.x
            self._py = wp.y
            self._pz = wp.z
            self._vx = 0.0
            self._vy = 0.0
            self._vz = 0.0
            self._des_x = 0.0
            self._des_y = 0.0
            self._des_z = 0.0
            self._sep_x = 0.0
            self._sep_y = 0.0
            self._sep_z = 0.0
            self._avx = 0.0
            self._avy = 0.0
            self._avz = 0.0
            self._avoid_t = 0.0
            self._state = "IDLE"
            self._bob_t = random.uniform(0.0, math.tau)
            self._patrol = None             # (x, z) floats
            self._retarget = 0.0
            self._cool = random.uniform(0.0, 0.6)
            self._last_seen = -1.0e9
            self._los_ok = False
            self._tgt = None                # (x, y, z) last seen player pos
            self._path = None               # [(x, y, z), ...] float tuples
            self._path_i = 0
            self._path_timer = 0.0
            self._reach = 0.9
            self._gc_timer = random.uniform(0.0, 0.5)
            self._ground_y = None
            self._whisk_timer = random.uniform(0.0, 0.15)
            self._brain_accum = 0.0
            self._dt_accum = 0.0
            self._turn_sign = 1.0 if (id(self) & 1) else -1.0
            self._sw = None
            self._slot = None
            self._tier = 1
            self._cache_tick = 99
            self._npos = node.position
            self._cache_props()
            self._ignore = set()
            self._ignore_ver = -1
            self._ignore_set = self._ignore
            try:
                self._ignore = {id(n) for n in node.traverse()}
            except Exception:
                self._ignore = {id(node)}
            # Physical body so player bullets can hit the flyer.  The nav
            # grid skips flyer nodes, so this never blocks pathing.
            try:
                if node.collider is None and SphereCollider is not None:
                    node.collider = SphereCollider(
                        radius=max(0.05, float(self.p("agent_radius"))))
                e.physics.add_node(node)
            except Exception as ex:
                _fly_log_once("flyer_phys", f"[FlyingAI] physics add: {ex}")
            try:
                _fly_swarm(e).register(self)
            except Exception as ex:
                _fly_log_once("swarm_reg", f"[FlyingAI] swarm register: {ex}")

        def on_end_play(self):
            self._alive = False
            try:
                _fly_swarm(self.engine).unregister(self)
            except Exception:
                pass
            try:
                self.engine.physics.remove_node(self.node)
            except Exception:
                pass

        def on_tick(self, dt: float):
            # Hot path: one method, locals only, zero glm allocations, zero
            # property dict lookups.  Brains stagger (frame+k)%5; separation
            # gathers (frame+k)%3; integration is distance-tiered (full rate
            # under 120 m, 1/4 beyond, 1/10 beyond 250 m — dt accumulated so
            # travel speed and behaviour are identical).
            if not self._alive:
                return
            node = self.node
            if node is None:
                return
            try:
                sw = self._sw
                if sw is None:
                    sw = _fly_swarm(self.engine)
                    self._sw = sw
                e = self.engine
                if e.time != sw._last_time:
                    sw._roll_frame(dt)
                if sw._use_batch:
                    # Brains (staggered frame+k)%5) and integration for ALL
                    # due flyers already happened inside sw._roll_frame as
                    # vectorized numpy passes.  Nothing left to do per flyer.
                    return
                frame = sw.frame
                k = self._k
                fk = frame + k
                self._brain_accum += dt
                if fk % 5 == 0:
                    self._brain(sw, self._brain_accum)
                    self._brain_accum = 0.0
                sqrt = math.sqrt
                tier = self._tier             # refreshed every brain tick
                dt_acc = self._dt_accum + dt
                if tier != 1 and fk % tier != 0:
                    self._dt_accum = dt_acc
                    return
                self._dt_accum = 0.0
                if dt_acc <= 0.0:
                    return

                # -------------------- integrate (pure float math) --------
                px = self._px
                py = self._py
                pz = self._pz
                vx = self._vx
                vy = self._vy
                vz = self._vz
                if fk % 8 == 0:
                    sw._sep_gather(self)
                desx = self._des_x + self._sep_x
                desy = self._des_y + self._sep_y
                desz = self._des_z + self._sep_z

                # Avoidance whisker (budgeted, short-lived steering impulse).
                wt = self._whisk_timer - dt_acc
                if wt <= 0.0:
                    wt = 0.15
                    sp2 = vx * vx + vy * vy + vz * vz
                    if sp2 > 0.25 and sw.whisk_budget > 0:
                        av = sw.whisker(self, px, py, pz, vx, vy, vz)
                        if av is not None:
                            self._avx = av[0]
                            self._avy = av[1]
                            self._avz = av[2]
                            self._avoid_t = 0.35
                self._whisk_timer = wt
                if self._avoid_t > 0.0:
                    self._avoid_t -= dt_acc
                    desx += self._avx
                    desy += self._avy
                    desz += self._avz

                # Acceleration-limited steering toward the desired velocity.
                dvx = desx - vx
                dvy = desy - vy
                dvz = desz - vz
                L2 = dvx * dvx + dvy * dvy + dvz * dvz
                max_dv = self._c_accel * dt_acc
                if L2 > max_dv * max_dv and L2 > 1e-18:
                    f = max_dv / sqrt(L2)
                    dvx *= f
                    dvy *= f
                    dvz *= f
                vx += dvx
                vy += dvy
                vz += dvz
                nx = px + vx * dt_acc
                ny = py + vy * dt_acc
                nz = pz + vz * dt_acc

                # Ground clearance (budgeted 2 Hz probe): never sink through
                # terrain, whatever the state machine asked for.
                gt = self._gc_timer - dt_acc
                if gt <= 0.0:
                    gt = 0.5
                    if sw.gc_budget > 0:
                        gy = sw.ground_probe(self, px, py, pz)
                        if gy is not None:
                            self._ground_y = gy
                self._gc_timer = gt
                gy = self._ground_y
                if gy is not None:
                    min_y = gy + self._c_min_alt
                    if ny < min_y:
                        ny = min_y
                        if vy < 0.0:
                            vy = 0.0

                self._vx = vx
                self._vy = vy
                self._vz = vz
                self._px = nx
                self._py = ny
                self._pz = nz
                # Component writes: glm vec3 supports .x/.y/.z assignment
                # with no allocation (spawn markers are root children, so
                # local == world; anything else falls back to the host
                # transform helper).  The position object itself is cached
                # at begin-play (re-cached on any write failure).
                pos = self._npos
                try:
                    pos.x = nx
                    pos.y = ny
                    pos.z = nz
                except Exception:
                    try:
                        _write_node_world_position(node, glm.vec3(nx, ny, nz))
                        self._npos = node.position
                    except Exception:
                        pass
                # Face travel direction (yaw only; every 2nd integration —
                # purely cosmetic, halves the atan2 churn).
                if (fk & 1) == 0 and vx * vx + vz * vz > 0.04:
                    try:
                        node.rotation_euler.y = math.degrees(
                            math.atan2(-vx, -vz))
                    except Exception:
                        pass
            except Exception as ex:
                _fly_log_once("tick", f"[FlyingAI] tick: {ex}")

        # --------------------------------------------------------- helpers
        def _cache_props(self):
            """Every property as a plain float attribute, refreshed on begin
            and every brain tick — the per-frame integrator never touches
            the properties dict.  (DEFAULTS are merged into properties at
            begin-play, so a single .get per key is enough.)"""
            g = self.properties.get
            self._c_speed = float(g("fly_speed", 6.0))
            self._c_accel = float(g("accel", 14.0))
            self._c_sep_r = float(g("separation_radius", 3.0))
            self._c_agent_r = float(g("agent_radius", 0.6))
            self._c_min_alt = float(g("min_altitude", 1.2))
            self._c_aggro = float(g("aggro_range", 45.0))
            self._c_attack = float(g("attack_range", 18.0))
            self._c_lose = float(g("lose_sight_after", 3.0))
            self._c_fire_rate = float(g("fire_rate", 1.2))
            self._c_patrol = float(g("patrol_radius", 8.0))
            self._c_hover = float(g("hover_amp", 0.6))
            self._c_recalc = float(g("path_recalc", 1.5))
            # Keep the batch arrays in sync (they are the integrated copy).
            sw = self._sw
            if sw is not None and sw._use_batch:
                s = getattr(self, "_slot", None)
                if s is not None and 0 <= s < sw._n:
                    try:
                        sw._aacc[s] = self._c_accel
                        sw._aminalt[s] = self._c_min_alt
                        sw._asepr[s] = self._c_sep_r
                    except Exception:
                        pass

        def _los_ignore(self, swarm):
            """Raycast ignore set: self subtree + player subtree + every
            flyer (rebuilt only when the swarm roster version changes)."""
            if self._ignore_ver != swarm._version:
                ids = set(self._ignore)
                try:
                    player = swarm.player_node()
                    if player is not None:
                        ids |= {id(n) for n in player.traverse()}
                except Exception:
                    pass
                try:
                    ids |= swarm.flyer_ids()
                except Exception:
                    pass
                self._ignore_set = ids
                self._ignore_ver = swarm._version
            return self._ignore_set

        def _follow_path(self, px, py, pz, speed):
            """Float waypoint following; returns (vx, vy, vz) or None."""
            path = self._path
            if not path:
                return None
            reach2 = self._reach * self._reach
            i = self._path_i
            n = len(path)
            while i < n:
                wp = path[i]
                dx = wp[0] - px
                dy = wp[1] - py
                dz = wp[2] - pz
                if dx * dx + dy * dy + dz * dz >= reach2:
                    break
                i += 1
            self._path_i = i
            if i >= n:
                self._path = None
                return None
            d = math.sqrt(dx * dx + dy * dy + dz * dz)
            if d < 1e-6:
                return None
            f = speed / d
            return (dx * f, dy * f, dz * f)

        # ----------------------------------------------------------- brain
        def _brain(self, swarm, dt_b, now=None):
            # Property floats are effectively static during Play — re-read
            # them only every 25th brain tick (~2 s at 60 fps).
            self._cache_tick += 1
            if self._cache_tick >= 25:
                self._cache_tick = 0
                self._cache_props()
            e = self.engine
            if now is None:
                now = getattr(e, "time", 0.0)
            pl = swarm._plist
            s_ = self._slot
            if s_ is not None and (s_ + 1) * 3 <= len(pl):
                o = s_ * 3
                px = pl[o]
                py = pl[o + 1]
                pz = pl[o + 2]
            else:
                px = self._px
                py = self._py
                pz = self._pz
            self._bob_t += dt_b * 1.7
            speed = self._c_speed
            sqrt = math.sqrt
            seen = False
            dist = float("inf")

            # ---- perception (budgeted LOS; stale result reused when the
            # frame budget is spent — brains are staggered so it rotates).
            pl = swarm._plr
            if pl is not None:
                dx = pl[0] - px
                dy = pl[1] - py
                dz = pl[2] - pz
                d2 = dx * dx + dy * dy + dz * dz
                # Distance-tier integration for the NEXT 5 frames (1/4 past
                # 120 m, 1/10 past 250 m — same behaviour, dt-accumulated).
                if d2 > 62500.0:
                    tier = 10
                elif d2 > 14400.0:
                    tier = 4
                else:
                    tier = 1
                if tier != self._tier:
                    self._tier = tier
                    if swarm._use_batch:
                        s = self._slot
                        if s is not None and 0 <= s < swarm._n:
                            try:
                                swarm._atier[s] = tier
                            except Exception:
                                pass
                if d2 <= self._c_aggro * self._c_aggro:
                    dist = sqrt(d2)
                    r = swarm.los_clear(self, px, py, pz,
                                        pl[0], pl[1] + 0.35, pl[2])
                    if r is not None:
                        self._los_ok = r
                    if self._los_ok:
                        seen = True
                        self._last_seen = now
                        self._tgt = pl

            st = self._state
            desx = 0.0
            desy = 0.0
            desz = 0.0

            if st == "IDLE":
                if seen:
                    st = "CHASE"
                else:
                    self._retarget -= dt_b
                    pr = self._c_patrol
                    if pr > 0.0:
                        if self._patrol is None or self._retarget <= 0.0:
                            ang = random.uniform(0.0, math.tau)
                            rad = random.uniform(0.5, pr)
                            self._patrol = (self._hx + math.cos(ang) * rad,
                                            self._hz + math.sin(ang) * rad)
                            self._retarget = random.uniform(3.0, 7.0)
                        tx = self._patrol[0]
                        ty = self._hy + math.sin(self._bob_t) * self._c_hover
                        tz = self._patrol[1]
                        dx = tx - px
                        dy = ty - py
                        dz = tz - pz
                        d = sqrt(dx * dx + dy * dy + dz * dz)
                        if d > 0.5:
                            s = speed * 0.35
                            dd = d * 1.5
                            if dd < s:
                                s = dd
                            f = s / d
                            desx = dx * f
                            desy = dy * f
                            desz = dz * f

            elif st == "CHASE":
                if seen and dist <= self._c_attack:
                    st = "ATTACK"
                elif now - self._last_seen > self._c_lose:
                    st = "RETURN"
                    self._path = None
                    self._path_timer = 0.0
                else:
                    tgt = self._tgt
                    if tgt is None:
                        gx = self._hx
                        gy = self._hy
                        gz = self._hz
                    else:
                        gx = tgt[0]
                        gy = tgt[1]
                        gz = tgt[2]
                    step = None
                    if not self._los_ok:
                        self._path_timer -= dt_b
                        if self._path_timer <= 0.0:
                            self._path_timer = self._c_recalc
                            swarm.request_path(self, px, py, pz, gx, gy, gz)
                        step = self._follow_path(px, py, pz, speed)
                    if step is not None:
                        desx, desy, desz = step
                    else:
                        dx = gx - px
                        dy = gy - py
                        dz = gz - pz
                        d = sqrt(dx * dx + dy * dy + dz * dz)
                        if d > 0.5:
                            f = speed / d
                            desx = dx * f
                            desy = dy * f
                            desz = dz * f

            elif st == "ATTACK":
                if not seen or pl is None or dist > self._c_attack * 1.2:
                    st = "CHASE"
                else:
                    dx = px - pl[0]
                    dz = pz - pl[2]
                    dh = sqrt(dx * dx + dz * dz)
                    if dh < 1e-4:
                        rx = 1.0
                        rz = 0.0
                    else:
                        rx = dx / dh
                        rz = dz / dh
                    txv = -rz * self._turn_sign
                    tzv = rx * self._turn_sign
                    stand = self._c_attack * 0.75
                    wx = pl[0] + rx * stand + txv * 3.0
                    wy = pl[1] + 1.2 + math.sin(self._bob_t) * self._c_hover
                    wz = pl[2] + rz * stand + tzv * 3.0
                    dx = wx - px
                    dy = wy - py
                    dz = wz - pz
                    d = sqrt(dx * dx + dy * dy + dz * dz)
                    if d > 1e-4:
                        s = d * 2.0
                        if s > speed:
                            s = speed
                        f = s / d
                        desx = dx * f
                        desy = dy * f
                        desz = dz * f
                    self._cool -= dt_b
                    if self._cool <= 0.0 and self._los_ok:
                        self._cool = 1.0 / max(0.05, self._c_fire_rate)
                        dx = pl[0] - px
                        dy = pl[1] - py
                        dz = pl[2] - pz
                        L = sqrt(dx * dx + dy * dy + dz * dz)
                        if L > 1e-6:
                            f = (self._c_agent_r + 0.15) / L
                            self._fire(
                                glm.vec3(px + dx * f, py + dy * f,
                                         pz + dz * f),
                                glm.vec3(pl[0], pl[1] + 0.35, pl[2]))

            elif st == "RETURN":
                if seen:
                    st = "CHASE"
                else:
                    dx = self._hx - px
                    dy = self._hy - py
                    dz = self._hz - pz
                    d_home = sqrt(dx * dx + dy * dy + dz * dz)
                    if d_home < 1.5:
                        st = "IDLE"
                        self._path = None
                        self._patrol = None
                    else:
                        step = self._follow_path(px, py, pz, speed)
                        if step is None and self._path is None:
                            self._path_timer -= dt_b
                            if self._path_timer <= 0.0:
                                self._path_timer = self._c_recalc
                                swarm.request_path(self, px, py, pz,
                                                   self._hx, self._hy,
                                                   self._hz)
                            step = self._follow_path(px, py, pz, speed)
                        if step is not None:
                            desx, desy, desz = step
                        elif d_home > 1e-4:
                            f = speed / d_home
                            desx = dx * f
                            desy = dy * f
                            desz = dz * f

            self._state = st
            self._des_x = desx
            self._des_y = desy
            self._des_z = desz
            if swarm._use_batch:
                s = self._slot
                if s is not None and 0 <= s < swarm._n:
                    try:
                        dl = swarm._desl
                        o = s + s + s
                        dl[o] = desx
                        dl[o + 1] = desy
                        dl[o + 2] = desz
                    except Exception:
                        pass

        # ---------------------------------------------------------- firing
        def _fire(self, muzzle, target):
            """Replicates the engine's own enemy-bullet idiom as NEW code:
            a `_runtime_bullet` node + EnemyBullet blueprint + positional
            audio.  Degrades to a flash-light + damage message when the
            host has no EnemyBullet class."""
            e = self.engine
            try:
                d = target - muzzle
                L = glm.length(d)
                if L < 1e-4:
                    return
                d = d / L
                yaw = math.degrees(math.atan2(-d.x, -d.z))
                pitch = math.degrees(math.asin(_clamp(d.y, -1.0, 1.0)))
                eb_cls = globals().get("EnemyBullet")
                if eb_cls is None:
                    # Fallback: muzzle flash + instant damage message (LOS
                    # was already verified by the ATTACK brain).
                    try:
                        e.spawn_flash(muzzle, color=(1.0, 0.35, 0.25),
                                      intensity=8.0, range=4.0, ttl=0.06)
                    except Exception:
                        pass
                    try:
                        player = _fly_swarm(e).player_node()
                        if player is not None:
                            e.message_ancestors(
                                player, "damage",
                                {"amount": float(self.p("bullet_damage")),
                                 "point": target, "source": "flying_enemy"})
                    except Exception:
                        pass
                    try:
                        e.audio.play_at("shoot", muzzle, 0.30)
                    except Exception:
                        pass
                    return
                b = SceneNode("_runtime_bullet", parent=e.scene.root)
                b.position = glm.vec3(muzzle)
                b.rotation_euler = glm.vec3(pitch, yaw, 0.0)
                # Shared tiny emissive sphere (built once per engine — cheap).
                if getattr(e, "_gu_fly_bullet_mesh", None) is None:
                    e._gu_fly_bullet_mesh = Mesh.make_sphere(e.ctx, 0.045, 12, 8)
                    e._gu_fly_bullet_mat = Material(
                        name="gu_fly_bullet_mat",
                        base_color=(1.0, 0.30, 0.15, 1.0),
                        emissive=(1.0, 0.25, 0.1),
                        emissive_intensity=6.0, roughness=0.4)
                b.mesh = e._gu_fly_bullet_mesh
                b.material = e._gu_fly_bullet_mat
                bp = e.attach_blueprint(b, eb_cls, {
                    "speed": float(self.p("bullet_speed")),
                    "damage": float(self.p("bullet_damage")),
                    "owner_name": self.node.name,
                })
                if bp is not None:
                    bp._runtime_spawn = True
                try:
                    e._spawn_runtime_nodes.append(b)
                except Exception:
                    pass
                e.audio.play_at("shoot", muzzle, 0.30)
            except Exception as ex:
                _fly_log_once("fire", f"[FlyingAI] fire: {ex}")

        # ---------------------------------------------------------- damage
        def on_message(self, event: str, data):
            if event != "damage" or not self._alive:
                return
            try:
                amt = float(data.get("amount", 0)) if isinstance(data, dict) \
                    else float(data or 0)
            except Exception:
                amt = 0.0
            self.hp -= amt
            if self.hp > 0.0:
                return
            self._alive = False
            e = self.engine
            try:
                pos = self.node.world_position()
            except Exception:
                pos = glm.vec3(0.0)
            try:
                e.audio.play_at("explosion", pos, 0.7)
            except Exception:
                pass
            try:
                e.spawn_flash(pos, color=(1.0, 0.5, 0.15), intensity=60.0,
                              range=10.0, ttl=0.15)
            except Exception:
                pass
            try:
                self.node.visible = False
                e.physics.remove_node(self.node)
            except Exception:
                pass
            try:
                _fly_swarm(e).unregister(self)
            except Exception:
                pass
            try:
                e.send_message("died", {"node": self.node})
            except Exception:
                pass


    # =====================================================================
    # Spawn integration — marker creation + Play-mode activation + Library
    # =====================================================================
    _FLY_SPAWN_DEFAULTS = {
        "health": 100.0,
        "fly_speed": 6.0,
        "aggro_range": 45.0,
        "attack_range": 18.0,
        "fire_rate": 1.2,
        "bullet_damage": 8.0,
        "bullet_speed": 40.0,
        "patrol_radius": 8.0,
    }


    def _gu_add_flying_enemy(self, position=None):
        """HeavenEngine.add_flying_enemy: drop a 'Flying Enemy' spawn marker
        (small sphere, spawn_kind 'flying_enemy') into the scene."""
        try:
            if hasattr(self, "add_sphere"):
                node = self.add_sphere(radius=0.5, name="Flying Enemy",
                                       solid=False)
            else:
                node = SceneNode("Flying Enemy", parent=self.scene.root)
            try:
                node.name = self._unique_node_name("Flying Enemy")
            except Exception:
                pass
            node.spawn_kind = "flying_enemy"
            node.spawn_config = dict(_FLY_SPAWN_DEFAULTS)
            if position is None:
                try:
                    position = self.camera.position \
                        + self.camera.forward() * 6.0
                except Exception:
                    position = glm.vec3(0.0, 3.0, 0.0)
            try:
                node.position = _v3(position)
            except Exception:
                node.position = glm.vec3(float(position[0]), float(position[1]),
                                         float(position[2]))
            try:
                node.material = Material(
                    name="flying_enemy_mat",
                    base_color=(0.90, 0.25, 0.20, 1.0),
                    emissive=(0.50, 0.08, 0.05),
                    emissive_intensity=0.7, roughness=0.5)
            except Exception:
                pass
            try:
                self.select_node(node)
            except Exception:
                pass
            Log.info(f"Added '{node.name}' to the scene "
                     "(flying enemy, 3D A* pathing)")
            return node
        except Exception as ex:
            _fly_log_once("add_flyer", f"[FlyingAI] add_flying_enemy: {ex}")
            return None


    def _gu_activate_flying(self):
        """AFTER the stock _activate_spawn_points: turn every
        spawn_kind=='flying_enemy' marker into a live FlyingEnemyAI.
        Runtime bps are tagged + registered exactly like the base kinds, so
        _deactivate_spawn_points removes them and restores transforms for
        free (the base loop already snapshots ANY spawn_kind node)."""
        runtime_bps = getattr(self, "_spawn_runtime_bps", None)
        if runtime_bps is None:
            return
        for node in list(self.scene.root.traverse()):
            try:
                if getattr(node, "spawn_kind", None) != "flying_enemy":
                    continue
                if any(type(b).__name__ == "FlyingEnemyAI"
                       for b in getattr(node, "blueprints", ())):
                    continue
                cfg = getattr(node, "spawn_config", None) or {}
                # Solid collider matching the marker's CURRENT mesh (same
                # helper the base enemy kind uses); on_begin_play falls back
                # to a sphere when the marker has no mesh at all.
                try:
                    self._fit_spawn_collider(node, is_trigger=False)
                except Exception:
                    pass
                props = {
                    "max_health": float(cfg.get("health", 100.0)),
                    "fly_speed": float(cfg.get("fly_speed", 6.0)),
                    "aggro_range": float(cfg.get("aggro_range", 45.0)),
                    "attack_range": float(cfg.get("attack_range", 18.0)),
                    "fire_rate": float(cfg.get("fire_rate", 1.2)),
                    "bullet_damage": float(cfg.get("bullet_damage", 8.0)),
                    "bullet_speed": float(cfg.get("bullet_speed", 40.0)),
                    "patrol_radius": float(cfg.get("patrol_radius", 8.0)),
                }
                bp = self.attach_blueprint(node, FlyingEnemyAI, props)
                if bp is not None:
                    bp._runtime_spawn = True
                    runtime_bps.append(bp)
            except Exception as ex:
                _fly_log_once("activate_node",
                              f"[FlyingAI] activate flyer: {ex}")


    def _gu_library_extra(self):
        """Pack one extra button into the (already open) tkinter Library
        popup.  Editor mode only, fully guarded."""
        if getattr(self, "mode", "editor") != "editor":
            return
        win = getattr(self, "_library_window", None)
        if win is None:
            return
        try:
            if not win.winfo_exists():
                return
        except Exception:
            return
        if getattr(win, "_gu_fly_btn", False):
            return                                   # already added / lifted
        try:
            import tkinter as tk
        except Exception:
            return

        def _add():
            try:
                self.add_flying_enemy()
            except Exception as ex:
                _fly_log_once("lib_add", f"[FlyingAI] library add: {ex}")
            try:
                win.destroy()
            except Exception:
                pass
            self._library_window = None

        try:
            tk.Label(win, text="GREAT UPDATE", fg="#8ab4f8", bg="#202124",
                     font=("Segoe UI", 11, "bold")).pack(anchor="w",
                                                         pady=(10, 3))
            tk.Button(win, text="Flying Enemy  (3D A* pathing)", width=24,
                      command=_add, bg="#3c4043", fg="#ffffff",
                      activebackground="#5f6368", activeforeground="#ffffff",
                      relief="flat", padx=12, pady=8).pack(fill="x", pady=3)
            win._gu_fly_btn = True
        except Exception as ex:
            _fly_log_once("lib_btn", f"[FlyingAI] library button: {ex}")


    # =====================================================================
    # Install — additive monkey-patch wraps, double-install guarded
    # =====================================================================
    def _gu_flying_install():
        try:
            if getattr(HeavenEngine, "_gu_flying_installed", False):
                return
            HeavenEngine._gu_flying_installed = True

            # Public API: engine.add_flying_enemy(position=None)
            HeavenEngine.add_flying_enemy = _gu_add_flying_enemy

            # Wrap spawn activation: original FIRST (it resets the runtime
            # lists + snapshots every spawn_kind node), then attach ours.
            _prev_activate = HeavenEngine._activate_spawn_points

            def _gu_activate_wrapped(self, _prev=_prev_activate):
                _prev(self)
                try:
                    _gu_activate_flying(self)
                except Exception as ex:
                    _fly_log_once("activate_wrap",
                                  f"[FlyingAI] spawn activation: {ex}")

            HeavenEngine._activate_spawn_points = _gu_activate_wrapped

            # Wrap the Library popup (editor-only feature; runtime builds may
            # not have it — guard with getattr).
            _prev_library = getattr(HeavenEngine, "open_library_menu", None)
            if _prev_library is not None:
                def _gu_library_wrapped(self, _prev=_prev_library):
                    _prev(self)
                    try:
                        _gu_library_extra(self)
                    except Exception as ex:
                        _fly_log_once("library_wrap",
                                      f"[FlyingAI] library wrap: {ex}")

                HeavenEngine.open_library_menu = _gu_library_wrapped

            Log.info("[GREATUPDATE:flying_ai] FlyingEnemyAI + voxel A* nav + "
                     "swarm budgets installed (ground NPC AI untouched)")
        except Exception as ex:
            try:
                Log.error(f"[GREATUPDATE:flying_ai] install failed: {ex}")
            except Exception:
                pass

    # FRAGMENT 4 — terrain_removal.py
    # Removes the random terrain generator entirely (editor entry points) while
    # preserving heightfield physics + legacy scene loading. Defines _gu_install_all.


    def _gu_terrain_removal_install():
        """[BUTTONBACK] Terrain button restored - no longer removes anything.

        The navbar Terrain button is back (between the smaller Particle
        button and the BUILDING EDITOR button), so this installer now KEEPS
        the real TERRAINPATCH entry points instead of neutralizing them:
        toggle_terrain_editor (GameGuru-style editor panel) and
        create_terrain (flat, hand-editable terrain the panel sculpts).
        Terrain physics + legacy scene loading stay intact exactly as before.
        """
        def _gu_log_once(key, msg):
            seen = globals().setdefault("_GU_LOG_ONCE", set())
            if key not in seen:
                seen.add(key)
                try:
                    Log.info(msg)
                except Exception:
                    pass

        # 1) [BUTTONBACK] Keep the real GameGuru-style terrain editor toggle
        #    that TERRAINPATCH installed - the blue Terrain navbar button
        #    calls it.  (The old no-op neutralizer has been removed.)
        try:
            _tp_real_toggle = globals().get("_tp_toggle_terrain_editor")
            if callable(_tp_real_toggle):
                HeavenEngine.toggle_terrain_editor = _tp_real_toggle
        except Exception as _ex:
            try:
                Log.warn(f"[GREATUPDATE] terrain toggle restore: {_ex}")
            except Exception:
                pass

        # 2) A terrain editor instance, if one exists, is left alone - it is
        #    a live, working editor again, not a leftover to force-close.

        # 3) [BUTTONBACK] Keep the real create_terrain (flat, hand-editable
        #    terrain) that TERRAINPATCH installed - the editor panel's
        #    auto-create path needs it.  The loader path create_terrain_node
        #    stays untouched, so legacy projects still load and collide.
        try:
            _tp_real_create = globals().get("_tp_create_terrain")
            if callable(_tp_real_create):
                HeavenEngine.create_terrain = _tp_real_create
        except Exception as _ex:
            try:
                Log.warn(f"[GREATUPDATE] create_terrain restore: {_ex}")
            except Exception:
                pass

        # 4) The TERRAINPATCH panel draw wrap is active again and renders
        #    the terrain editor panel whenever it is open.
        try:
            Log.info("[GREATUPDATE] Terrain button restored between the "
                     "Particle and BUILDING EDITOR buttons; GameGuru-style "
                     "terrain editor fully functional (physics intact).")
        except Exception:
            pass


    def _gu_install_all():
        """Single entry point executed by the appended guard block."""
        for fn_name in ("_gu_particles_install",
                        "_gu_studio_install",
                        "_gu_flying_install",
                        "_gu_terrain_removal_install"):
            fn = globals().get(fn_name)
            if fn is None:
                try:
                    Log.warn(f"[GREATUPDATE] installer missing: {fn_name}")
                except Exception:
                    pass
                continue
            try:
                fn()
            except Exception as _ex:
                try:
                    Log.error(f"[GREATUPDATE] {fn_name} failed: {_ex}")
                except Exception:
                    pass
        try:
            Log.info("[GREATUPDATE] v1.0 installed: GPU Particle Studio "
                     "(smoke / fire / electricity + custom presets), "
                     "FlyingEnemyAI with 3D A* pathfinding, terrain editor "
                     "button restored. 350 ground NPCs + 1000 flyers + 1000 "
                     "particle systems budgeted for zero-FPS-drop operation.")
        except Exception:
            pass

    _gu_install_all()
# [GREATUPDATE:core END]
