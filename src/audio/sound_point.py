# @heaven3d-part audio/sound_point.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# =====================================================================
# [AUDIOPATCH:sound_point]
# [AUDIOPATCH:sound_point v2 — editor-only logo]
# Sound Point — a spawnable speaker-logo billboard node that plays a
# user-picked sound/music file from the PC (tkinter file dialog):
#   * On Start Play   — Off / Play Once / Play as Loop
#   * On Trigger Play — Off / Play Once / Play as Loop, fired when the
#                       player enters the trigger radius (a loop stops
#                       again when the player leaves)
# The speaker-logo billboard is an EDITOR-ONLY visualisation — hidden
# during Play and in the standalone runtime, exactly like the Trigger
# Point's cube (only the SOUND plays at runtime, never the visual).
# Append-only, double-install guarded, try/except everywhere.
# Adds no assets, no dependencies, no serializer changes.
# =====================================================================
if not globals().get("_SND_PATCH_INSTALLED"):
    _SND_PATCH_INSTALLED = True

    _SND_MODES = ("off", "once", "loop")

    def _snd_float(value, default):
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default)

    # ---- 1) spawn defaults ------------------------------------------
    try:
        HeavenEngine.SPAWN_DEFAULTS["sound_point"] = {
            "sound_path": "",
            "volume": 0.9,
            "radius": 3.0,
            "start_mode": "off",
            "trigger_mode": "once",
        }
    except Exception:
        pass

    # ---- 2) creation (Library → "Sound Point" button) ----------------
    def _snd_add_sound_point(self):
        """Create a Sound Point: a speaker-logo billboard that plays a
        user-picked sound/music file when Play starts and/or when the
        player walks into its trigger radius."""
        node = self._make_spawn_node("sound_point", "Sound Point",
                                     (0.0, 0.5, 0.0))
        node.mesh = Mesh.make_cube(self.ctx, 1.0)
        node.mesh_type = "cube"
        node.mesh_params = {"size": 1.0}
        node.material = Material(
            name="sound_point_mat",
            base_color=(0.15, 0.75, 0.95, 0.30),
            roughness=0.6, emissive=(0.05, 0.45, 0.60),
            emissive_intensity=0.8, alpha_mode="BLEND", double_sided=True)
        self.select_node(node)
        Log.info(f"Added '{node.name}' to the scene")
        return node

    HeavenEngine.add_sound_point = _snd_add_sound_point

    # ---- 3) inspector editors ----------------------------------------
    def _snd_edit_sound_path(self, node):
        """Pick a sound/music file with the native tkinter file dialog."""
        path = self._native_dialog(
            "open", "Choose Sound / Music File",
            filetypes=[("Audio files", "*.wav *.ogg *.mp3"),
                       ("WAV audio", "*.wav"),
                       ("OGG audio", "*.ogg"),
                       ("MP3 audio", "*.mp3"),
                       ("All files", "*.*")])
        if not path:
            return
        node.spawn_config["sound_path"] = str(path)
        try:
            if os.path.splitext(str(path))[1].lower() != ".mp3":
                self.audio.load_sound(str(path))  # warm the cache
        except Exception:
            pass
        Log.info(f"Set {node.name}.sound_path = {path}")

    HeavenEngine._edit_sound_path = _snd_edit_sound_path

    def _snd_cycle_sound_mode(self, node, key):
        """Cycle Off → Play Once → Play as Loop for one Sound Point mode."""
        cur = str(node.spawn_config.get(key, "off"))
        try:
            idx = _SND_MODES.index(cur)
        except ValueError:
            idx = 0
        nxt = _SND_MODES[(idx + 1) % len(_SND_MODES)]
        node.spawn_config[key] = nxt
        Log.info(f"Set {node.name}.{key} = {nxt}")

    HeavenEngine._cycle_sound_mode = _snd_cycle_sound_mode

    # ---- 4) runtime behaviour -----------------------------------------
    class _SoundPointRuntime(_GameBlueprint):
        """Plays the configured file on Play start and/or on player
        proximity.  Attached at runtime only (never serialized)."""
        DEFAULTS = {
            "sound_path": "",
            "volume": 0.9,
            "radius": 3.0,
            "start_mode": "off",
            "trigger_mode": "once",
        }

        def on_begin_play(self):
            self._snd_ch = {"start": None, "trigger": None}
            self._snd_music = {"start": False, "trigger": False}
            self._snd_inside = False
            if not str(self.p("sound_path", "") or ""):
                return
            mode = str(self.p("start_mode", "off"))
            if mode in ("once", "loop"):
                self._snd_play("start", loop=(mode == "loop"))

        def _snd_play(self, slot, loop):
            self._snd_stop(slot)
            e = self.engine
            path = str(self.p("sound_path", "") or "")
            if not path:
                return
            vol = _clamp(_snd_float(self.p("volume", 0.9), 0.9), 0.0, 1.0)
            try:
                if os.path.splitext(path)[1].lower() == ".mp3":
                    # pygame.mixer.Sound cannot reliably decode mp3 —
                    # stream it through the music channel instead.
                    e.audio.play_music(path, loop=loop, volume=vol)
                    self._snd_music[slot] = True
                else:
                    self._snd_ch[slot] = e.audio.play(path, vol, loop=loop)
            except Exception as ex:
                Log.warn(f"Sound Point could not play '{path}': {ex}")

        def _snd_stop(self, slot=None):
            slots = ("start", "trigger") if slot is None else (slot,)
            for s in slots:
                try:
                    if self._snd_music.get(s):
                        self.engine.audio.stop_music()
                        self._snd_music[s] = False
                    ch = self._snd_ch.get(s)
                    if ch is not None:
                        try:
                            ch.stop()
                        except Exception:
                            pass
                        self._snd_ch[s] = None
                except Exception:
                    pass

        def _snd_listener_pos(self):
            pb = self._player_bp()
            if pb is not None and getattr(pb, "node", None) is not None:
                try:
                    return pb.node.world_position()
                except Exception:
                    pass
            try:
                return self.engine.camera.position
            except Exception:
                return None

        def on_tick(self, dt):
            mode = str(self.p("trigger_mode", "once"))
            if mode not in ("once", "loop"):
                return
            if not str(self.p("sound_path", "") or ""):
                return
            if self.node is None:
                return
            pp = self._snd_listener_pos()
            if pp is None:
                return
            try:
                dist = glm.length(self.node.world_position() - pp)
            except Exception:
                return
            radius = max(0.1, _snd_float(self.p("radius", 3.0), 3.0))
            inside = dist <= radius
            if inside and not self._snd_inside:
                # Player stepped in: (re)start — once-per-entry or loop.
                self._snd_play("trigger", loop=(mode == "loop"))
            elif (not inside) and self._snd_inside and mode == "loop":
                # Player walked away: stop a trigger loop.
                self._snd_stop("trigger")
            self._snd_inside = inside

        def on_end_play(self):
            self._snd_stop()

    # ---- 5) speaker-logo billboard (EDITOR ONLY — never at runtime) --
    _SND_ICON_VERT = """#version 330 core
layout (location = 0) in vec3 in_position;
layout (location = 1) in vec2 in_uv;
uniform mat4 u_view;
uniform mat4 u_proj;
out vec2 v_uv;
void main(){
    v_uv = in_uv;
    gl_Position = u_proj * u_view * vec4(in_position, 1.0);
}
"""

    _SND_ICON_FRAG = """#version 330 core
uniform sampler2D u_tex;
in vec2 v_uv;
out vec4 frag_color;
void main(){
    vec4 c = texture(u_tex, v_uv);
    if (c.a < 0.025) discard;
    frag_color = c;
}
"""

    def _snd_icon_surface(size=64):
        """Procedural speaker-with-waves logo — no asset files."""
        surf = pygame.Surface((size, size), pygame.SRCALPHA)
        S = float(size)
        cyan = (96, 222, 255, 255)
        w = max(2, int(S * 0.055))
        pygame.draw.rect(surf, cyan,
                         (S * 0.10, S * 0.36, S * 0.22, S * 0.28),
                         border_radius=max(1, int(S * 0.05)))
        pygame.draw.polygon(surf, cyan, [(S * 0.30, S * 0.34),
                                         (S * 0.55, S * 0.16),
                                         (S * 0.55, S * 0.84),
                                         (S * 0.30, S * 0.66)])
        pygame.draw.arc(surf, cyan,
                        pygame.Rect(S * 0.52, S * 0.32, S * 0.26, S * 0.36),
                        math.radians(-55.0), math.radians(55.0), w)
        pygame.draw.arc(surf, cyan,
                        pygame.Rect(S * 0.56, S * 0.20, S * 0.40, S * 0.60),
                        math.radians(-50.0), math.radians(50.0), w)
        return surf

    class _SoundPointIcons:
        """Camera-facing speaker logos for every Sound Point, drawn on
        top of the world each frame (depth test off, alpha blend)."""
        _MAX_VERTICES = 4092

        def __init__(self, ctx):
            self.ctx = ctx
            self._prog = None
            self._vbo = None
            self._vao = None
            self._tex = None

        def _ensure(self):
            if self._prog is not None:
                return True
            try:
                self._prog = self.ctx.program(
                    vertex_shader=_SND_ICON_VERT,
                    fragment_shader=_SND_ICON_FRAG)
                self._vbo = self.ctx.buffer(
                    reserve=self._MAX_VERTICES * 5 * 4, dynamic=True)
                self._vao = self.ctx.vertex_array(
                    self._prog,
                    [(self._vbo, "3f 2f", "in_position", "in_uv")])
                surf = _snd_icon_surface(64)
                data = pygame.image.tostring(surf, "RGBA", True)
                self._tex = self.ctx.texture(surf.get_size(), 4, data)
                self._tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
                return True
            except Exception as ex:
                Log.warn(f"Sound Point icon init failed: {ex}")
                self._prog = None
                return False

        def render(self, camera, scene):
            if not self._ensure():
                return
            try:
                items = []
                for node in (scene.special_nodes() if hasattr(scene, "special_nodes") else scene.root.traverse()):
                    if getattr(node, "spawn_kind", None) != "sound_point":
                        continue
                    if not getattr(node, "visible", True):
                        continue
                    center = node.world_position() + glm.vec3(0.0, 0.72, 0.0)
                    dist = glm.length(center - camera.position)
                    items.append((float(dist), center))
                if not items:
                    return
                items.sort(key=lambda item: item[0], reverse=True)
                fwd = camera.forward()
                right = camera.right()
                up = _safe_normalize(glm.cross(right, fwd),
                                     glm.vec3(0.0, 1.0, 0.0))
                verts = []
                for _dist, center in items:
                    size = _clamp(_dist * 0.052, 0.48, 1.35)
                    h = size * 0.5
                    bl = center - right * h - up * h
                    br = center + right * h - up * h
                    tr = center + right * h + up * h
                    tl = center - right * h + up * h
                    verts.extend((bl.x, bl.y, bl.z, 0.0, 0.0,
                                  br.x, br.y, br.z, 1.0, 0.0,
                                  tr.x, tr.y, tr.z, 1.0, 1.0,
                                  bl.x, bl.y, bl.z, 0.0, 0.0,
                                  tr.x, tr.y, tr.z, 1.0, 1.0,
                                  tl.x, tl.y, tl.z, 0.0, 1.0))
                self._prog["u_view"].write(camera.view().to_bytes())
                self._prog["u_proj"].write(camera.projection().to_bytes())
                self._prog["u_tex"] = 0
                self._tex.use(location=0)
                self.ctx.disable(moderngl.DEPTH_TEST)
                self.ctx.enable(moderngl.BLEND)
                self.ctx.blend_func = (moderngl.SRC_ALPHA,
                                       moderngl.ONE_MINUS_SRC_ALPHA)
                self.ctx.depth_mask = False
                step = self._MAX_VERTICES * 5
                for start in range(0, len(verts), step):
                    chunk = verts[start:start + step]
                    self._vbo.write(
                        np.asarray(chunk, dtype=np.float32).tobytes())
                    self._vao.render(moderngl.TRIANGLES,
                                     vertices=len(chunk) // 5)
                self.ctx.depth_mask = True
                self.ctx.disable(moderngl.BLEND)
                self.ctx.enable(moderngl.DEPTH_TEST)
            except Exception:
                pass

    # Every-frame draw hook: _render_selection_outline runs in ALL modes,
    # so we gate hard below — the logo is an EDITOR-ONLY visualisation,
    # hidden during Play and in the standalone runtime, exactly like the
    # Trigger Point's cube (same gate the engine uses for spawn gizmos).
    _SND_PREV_OUTLINE = HeavenEngine._render_selection_outline

    def _snd_render_selection_outline(self):
        _SND_PREV_OUTLINE(self)
        try:
            if getattr(self, "mode", None) != "editor":
                return  # standalone runtime: no logo, ever
            if getattr(self, "_playing", False):
                return  # Play mode: logo hides like the trigger cube
            if getattr(self, "ctx", None) is None:
                return
            rend = getattr(self, "_snd_icons", None)
            if rend is None:
                rend = _SoundPointIcons(self.ctx)
                self._snd_icons = rend
            rend.render(self.camera, self.scene)
        except Exception:
            pass

    HeavenEngine._render_selection_outline = _snd_render_selection_outline

    # ---- 6) play-mode activation --------------------------------------
    _SND_PREV_ACTIVATE = HeavenEngine._activate_spawn_points

    def _snd_activate_spawn_points(self):
        _SND_PREV_ACTIVATE(self)
        try:
            for node in list(self.scene.root.traverse()):
                if getattr(node, "spawn_kind", None) != "sound_point":
                    continue
                cfg = getattr(node, "spawn_config", None) or {}
                bp = self.attach_blueprint(node, _SoundPointRuntime, {
                    "sound_path": str(cfg.get("sound_path", "") or ""),
                    "volume": _snd_float(cfg.get("volume", 0.9), 0.9),
                    "radius": _snd_float(cfg.get("radius", 3.0), 3.0),
                    "start_mode": str(cfg.get("start_mode", "off")),
                    "trigger_mode": str(cfg.get("trigger_mode", "once")),
                })
                if bp is not None:
                    bp._runtime_spawn = True
                    self._spawn_runtime_bps.append(bp)
                # Hide the translucent editor cube during Play (the
                # speaker logo is editor-only too — invisible at runtime).
                if node.mesh is not None:
                    self._play_hidden_meshes.append((node, node.mesh))
                    node.mesh = None
        except Exception as ex:
            Log.warn(f"Sound Point activation failed: {ex}")

    HeavenEngine._activate_spawn_points = _snd_activate_spawn_points
# [AUDIOPATCH:sound_point END]


# [WALLGLASSPATCH:wall_physics_glass_shatter BEGIN v1]
# #############################################################################
#
# WALL <-> GLASS PHYSICS CORRECTION + OPTIMIZED EXACT-POINT GLASS SHATTER
# (append-only, monkey-patch integration - ZERO existing engine lines were
# modified; same integration style as FinalPatch / FINALPATCH2 / FINALPATCH3).
#
#   1. WALLS WITH WINDOWS NOW HAVE PHYSICS: the "window_frame" module (the
#      wall ring around the opening: sill strip, header strip, both side
#      strips) collides with its EXACT mesh triangles via the engine's
#      native MeshCollider - the exact same {"kind": "mesh"} plan that
#      stairwells (stock v3) and door frames (FINALPATCH2) already use.
#      The window ring mesh is mirror-symmetric about x=0, so the
#      MeshCollider transpose-rotation convention is exact at all 4 yaws.
#      Before this patch that wall had NO collider at all (only a thin
#      band across the opening plus the glass box blocked anything).
#   2. GLASS PANES HAVE NO PHYSICS: the "glass_pane" plan is now
#      {"body": "none"} - no collider, no rigidbody, no physics cost.
#   3. GLASS SHATTERS REALISTICALLY AT THE EXACT SHOT POINT: weapon shots,
#      projectiles and enemy bullets (and ONLY those - an arming guard
#      keeps AI line-of-sight, editor picking and spawn probes byte-for-
#      byte stock) are tested analytically against a uniform 2 m spatial
#      hash of live panes (Amanatides-Woo voxel walk, O(cells crossed)).
#      On a hit the pane vanishes instantly, a radial shard burst erupts
#      from the exact bullet hole and a 3D glass-shatter sound plays.
#   4. ZERO FPS IMPACT BY CONSTRUCTION: panes are event-driven (no per-
#      frame work), shards come from a fixed-size pool that shares ONE
#      mesh + ONE material (the renderer's (id(mesh), id(material))
#      instancing groups every live shard into a single instanced draw),
#      and a hard live-budget recycles the oldest shard instantly, so
#      per-frame shard cost is bounded by the budget - never by the number
#      of shattered panes.  Shattering 10,000 panes cannot drop FPS.
#   5. NON-DESTRUCTIVE: stopping Play (and starting Play) restores every
#      shattered pane, so editor scenes / save files are never affected.
#      Shard nodes are "_runtime_" nodes (never serialized) and are swept
#      by the engine's own stop-play cleanup.
#
# Every installation step below is individually guarded: if any engine
# name is missing, that step is skipped and the rest still installs, so
# this block can NEVER crash engine startup.

_WG_VERSION = 1
_WG_ENGINE = None              # published by the HeavenEngine.__init__ wrap
_WG_PANES = []                 # flat list of pane records
_WG_BUCKETS = {}               # (ix, iy, iz) -> [pane record index, ...]
_WG_DIRTY = [True]             # registry rebuild flag
_WG_FAILS = [0]                # repeated-failure circuit breaker
_WG_LOGGED = set()
_WG_ARMED = [0]                # >0 only inside weapon/projectile raycasts
_WG_SHATTERED = []             # panes shattered this play session (restore)
_WG_LAST_SOUND = [0.0]         # glass-sound rate limiter
_WG_LAST_T = [0.0]             # shard ticker clock

_WG_BUCKET = 2.0               # one building cell = 2.0 m
_WG_BUDGET = 640               # HARD cap of live shards (bounded frame cost)
_WG_SHARDS_PER_HIT = 12        # shards spawned per shattered pane
_WG_POOL = []                  # every shard slot ever allocated
_WG_FREE = []                  # free slot indices (stack)
_WG_LIVE = []                  # live slot indices
_WG_SHARD_MESH = [None]        # shared shard mesh (one for all shards)
_WG_SHARD_MAT = [None]         # shared shard material (one for all shards)


def _wg_log_once(key, msg):
    if key not in _WG_LOGGED:
        _WG_LOGGED.add(key)
        try:
            Log.info(msg)
        except Exception:
            pass


# ---------------------------------------------------------------------------
#  1. Collider plans: window walls get exact mesh physics, glass gets none
# ---------------------------------------------------------------------------
try:
    _WG_PREV_FP_COLLIDER = _fp_default_collider

    def _wg_fp_default_collider(key, cell, fh):
        try:
            if key == "window_frame":
                # The wall ring around the opening (sill + header + side
                # strips) collides with its exact triangles.  The opening
                # itself stays open (the glass pane is separate).
                return {"kind": "mesh", "body": "static"}
            if key == "glass_pane":
                # Glass carries NO collider and NO rigidbody.
                return {"body": "none"}
        except Exception:
            pass
        return _WG_PREV_FP_COLLIDER(key, cell, fh)

    # Plain global rebind: every caller (generate_sector pass 3,
    # rebuild_module, paint_click, glass spawn) looks the name up in
    # module globals at call time - the same integration FINALPATCH2 used
    # for door frames.
    _fp_default_collider = _wg_fp_default_collider
except Exception as _wg_ex:
    _wg_log_once("collider", "[WALLGLASSPATCH] collider plan wrap skipped: "
                 + str(_wg_ex))


# ---------------------------------------------------------------------------
#  2. Glass pane registry (uniform spatial hash, event-driven)
# ---------------------------------------------------------------------------
def _wg_is_glass_pane(node):
    try:
        mp = getattr(node, "mesh_params", None)
        if isinstance(mp, dict) and mp.get("module_key") == "glass_pane":
            return True
    except Exception:
        pass
    try:
        nm = getattr(node, "name", "")
        return isinstance(nm, str) and nm.startswith("__fp_glass_")
    except Exception:
        return False


def _wg_pane_local_rect(node):
    # Local-space pane rectangle from the mesh's own CPU vertices (exact;
    # adapts to any future glass geometry).  Fallback: build_glass_pane's
    # constants (1.38 m x 1.18 m pane centred at (0, 1.6, -cell/2 + t/2)).
    try:
        mesh = getattr(node, "mesh", None)
        pos = getattr(mesh, "cpu_positions", None) if mesh is not None else None
        if pos is not None and len(pos) >= 4:
            mn = pos.min(axis=0)
            mx = pos.max(axis=0)
            c = (mn + mx) * 0.5
            half = (mx - mn) * 0.5
            if float(half[0]) > 1e-4 and float(half[1]) > 1e-4:
                return (float(c[0]), float(c[1]), float(c[2]),
                        float(half[0]), float(half[1]))
    except Exception:
        pass
    try:
        mp = getattr(node, "mesh_params", None) or {}
        cell = float(mp.get("cell_size", 2.0))
    except Exception:
        cell = 2.0
    return (0.0, 1.6, -cell * 0.5 + 0.06, cell * 0.5 - 0.31, 0.59)


def _wg_rebuild_registry(engine):
    # One scene traversal at Play start (or once after a mid-play sector
    # generation).  10k panes -> one pass, then ZERO per-frame cost: the
    # registry is only ever touched by armed shot raycasts.
    _WG_PANES.clear()
    _WG_BUCKETS.clear()
    try:
        root = engine.scene.root
        inv = 1.0 / _WG_BUCKET
        for node in root.traverse():
            try:
                if not _wg_is_glass_pane(node):
                    continue
                if not getattr(node, "visible", True):
                    continue
                if getattr(node, "mesh", None) is None:
                    continue
                lx, ly, lz, lhx, lhy = _wg_pane_local_rect(node)
                M = node.world_matrix()
                c4 = M * glm.vec4(lx, ly, lz, 1.0)
                u4 = M * glm.vec4(1.0, 0.0, 0.0, 0.0)
                v4 = M * glm.vec4(0.0, 1.0, 0.0, 0.0)
                n4 = M * glm.vec4(0.0, 0.0, 1.0, 0.0)
                ul = math.sqrt(u4.x * u4.x + u4.y * u4.y + u4.z * u4.z)
                vl = math.sqrt(v4.x * v4.x + v4.y * v4.y + v4.z * v4.z)
                nl = math.sqrt(n4.x * n4.x + n4.y * n4.y + n4.z * n4.z)
                if ul < 1e-9 or vl < 1e-9 or nl < 1e-9:
                    continue
                rec = {
                    "node": node,
                    "c": glm.vec3(c4.x, c4.y, c4.z),
                    "u": glm.vec3(u4.x / ul, u4.y / ul, u4.z / ul),
                    "v": glm.vec3(v4.x / vl, v4.y / vl, v4.z / vl),
                    "n": glm.vec3(n4.x / nl, n4.y / nl, n4.z / nl),
                    "hx": lhx * ul,
                    "hy": lhy * vl,
                    "alive": True,
                }
                idx = len(_WG_PANES)
                _WG_PANES.append(rec)
                # Loose spatial hash: register the pane in EVERY bucket its
                # rect can touch (its extent is smaller than a bucket, so at
                # most 8 buckets).  Float noise at bucket boundaries can then
                # never make a ray miss a pane; double-testing a pane from a
                # neighbouring bucket is harmless (best_t ordering still
                # returns the nearest hit, and the pane dies only once).
                ex = abs(rec["u"].x) * rec["hx"] + abs(rec["v"].x) * rec["hy"] + 0.03
                ey = abs(rec["u"].y) * rec["hx"] + abs(rec["v"].y) * rec["hy"] + 0.03
                ez = abs(rec["u"].z) * rec["hx"] + abs(rec["v"].z) * rec["hy"] + 0.03
                bx0 = int(math.floor((c4.x - ex) * inv))
                bx1 = int(math.floor((c4.x + ex) * inv))
                by0 = int(math.floor((c4.y - ey) * inv))
                by1 = int(math.floor((c4.y + ey) * inv))
                bz0 = int(math.floor((c4.z - ez) * inv))
                bz1 = int(math.floor((c4.z + ez) * inv))
                for bx in range(bx0, bx1 + 1):
                    for by in range(by0, by1 + 1):
                        for bz in range(bz0, bz1 + 1):
                            _WG_BUCKETS.setdefault((bx, by, bz), []).append(idx)
            except Exception:
                continue
    except Exception as ex:
        _wg_log_once("registry", "[WALLGLASSPATCH] registry build: "
                     + str(ex))
        return                      # stay dirty: retry on the next shot
    _WG_DIRTY[0] = False


# ---------------------------------------------------------------------------
#  3. Exact analytic ray-vs-pane test (Amanatides-Woo voxel walk)
# ---------------------------------------------------------------------------
def _wg_ray_glass(ro, rd, max_t):
    # Nearest live pane hit by the ray within max_t, or None.  Pure-float
    # inner loop over a 2 m spatial hash: a 100 m shot crosses ~50 cells,
    # each holding at most one pane - negligible next to the engine's own
    # brute-force physics raycast.  Runs ONLY for armed shot raycasts.
    best_rec = None
    best_point = None
    best_t = float(max_t)
    if best_t <= 1e-6:
        return None
    try:
        ox = float(ro.x); oy = float(ro.y); oz = float(ro.z)
        dx = float(rd.x); dy = float(rd.y); dz = float(rd.z)
        inv = 1.0 / _WG_BUCKET
        ix = int(math.floor(ox * inv))
        iy = int(math.floor(oy * inv))
        iz = int(math.floor(oz * inv))
        BIG = 1.0e30
        if abs(dx) < 1e-12:
            step_x = 0; tmax_x = BIG; tdelta_x = BIG
        else:
            step_x = 1 if dx > 0.0 else -1
            tmax_x = (((ix + 1) if dx > 0.0 else ix) * _WG_BUCKET - ox) / dx
            tdelta_x = abs(_WG_BUCKET / dx)
        if abs(dy) < 1e-12:
            step_y = 0; tmax_y = BIG; tdelta_y = BIG
        else:
            step_y = 1 if dy > 0.0 else -1
            tmax_y = (((iy + 1) if dy > 0.0 else iy) * _WG_BUCKET - oy) / dy
            tdelta_y = abs(_WG_BUCKET / dy)
        if abs(dz) < 1e-12:
            step_z = 0; tmax_z = BIG; tdelta_z = BIG
        else:
            step_z = 1 if dz > 0.0 else -1
            tmax_z = (((iz + 1) if dz > 0.0 else iz) * _WG_BUCKET - oz) / dz
            tdelta_z = abs(_WG_BUCKET / dz)
        t = 0.0
        guard = 0
        while t <= best_t and guard < 4096:
            guard += 1
            lst = _WG_BUCKETS.get((ix, iy, iz))
            if lst:
                for ri in lst:
                    rec = _WG_PANES[ri]
                    if not rec["alive"]:
                        continue
                    node = rec["node"]
                    if getattr(node, "parent", None) is None:
                        rec["alive"] = False      # deleted pane: prune
                        continue
                    n = rec["n"]
                    dn = dx * n.x + dy * n.y + dz * n.z
                    if -1e-10 < dn < 1e-10:
                        continue
                    c = rec["c"]
                    tt = ((c.x - ox) * n.x + (c.y - oy) * n.y
                          + (c.z - oz) * n.z) / dn
                    if tt <= 1e-6 or tt >= best_t:
                        continue
                    px = ox + dx * tt
                    py = oy + dy * tt
                    pz = oz + dz * tt
                    u = rec["u"]; v = rec["v"]
                    wx = px - c.x; wy = py - c.y; wz = pz - c.z
                    if (abs(wx * u.x + wy * u.y + wz * u.z) <= rec["hx"]
                            and abs(wx * v.x + wy * v.y + wz * v.z)
                            <= rec["hy"]):
                        best_t = tt
                        best_rec = rec
                        best_point = glm.vec3(px, py, pz)
            if tmax_x < tmax_y and tmax_x < tmax_z:
                t = tmax_x; tmax_x += tdelta_x; ix += step_x
            elif tmax_y < tmax_z:
                t = tmax_y; tmax_y += tdelta_y; iy += step_y
            else:
                t = tmax_z; tmax_z += tdelta_z; iz += step_z
    except Exception:
        return None
    if best_rec is None:
        return None
    return (best_rec, best_point, best_t)


# ---------------------------------------------------------------------------
#  4. Shard pool: fixed budget, ONE shared mesh + ONE shared material
# ---------------------------------------------------------------------------
def _wg_shard_mesh(ctx):
    if _WG_SHARD_MESH[0] is None:
        # One small irregular triangle shard (~6 cm).  Every pooled shard
        # shares this single mesh, so the renderer's instancing groups all
        # live shards into one instanced draw call.
        s = 0.06
        pos = np.array([[-s, -s * 0.55, 0.0],
                        [s * 1.15, -s * 0.35, 0.0],
                        [-s * 0.15, s * 1.05, 0.0]], dtype=np.float32)
        nrm = np.array([[0.0, 0.0, 1.0]] * 3, dtype=np.float32)
        uv = np.zeros((3, 2), dtype=np.float32)
        idx = np.array([0, 1, 2], dtype=np.uint32)
        _WG_SHARD_MESH[0] = Mesh.from_arrays(ctx, pos, nrm, uv, idx,
                                             name="_wg_shard")
    return _WG_SHARD_MESH[0]


def _wg_shard_material():
    if _WG_SHARD_MAT[0] is None:
        _WG_SHARD_MAT[0] = Material(
            name="_wg_shard_mat",
            base_color=(0.74, 0.86, 0.92, 0.55),
            roughness=0.08, metallic=0.0,
            alpha_mode="BLEND", double_sided=True)
    return _WG_SHARD_MAT[0]


def _wg_pool_grow(engine):
    # Lazily grow the pool in chunks, never beyond _WG_BUDGET.  Shard nodes
    # are "_runtime_" nodes: never serialized, swept by stop-play cleanup.
    ctx = getattr(engine, "ctx", None)
    if ctx is None:
        return
    room = _WG_BUDGET - len(_WG_POOL)
    if room <= 0:
        return
    chunk = 64 if room >= 64 else room
    mesh = _wg_shard_mesh(ctx)
    mat = _wg_shard_material()
    for _ in range(chunk):
        try:
            node = SceneNode("_runtime_wgshard", parent=engine.scene.root)
            node.mesh = mesh
            node.material = mat
            node.visible = False
            node.scale = glm.vec3(1.0)
            idx = len(_WG_POOL)
            _WG_POOL.append({"node": node, "vel": glm.vec3(0.0),
                             "spin": glm.vec3(0.0), "life": 0.0,
                             "ttl": 1.0, "size": 1.0})
            _WG_FREE.append(idx)
            try:
                engine._spawn_runtime_nodes.append(node)
            except Exception:
                pass
        except Exception:
            break


def _wg_steal_oldest():
    # Budget full: recycle the OLDEST live shard instantly.  This is the
    # hard guarantee that no amount of shattering can ever raise the
    # per-frame cost past _WG_BUDGET iterations.
    if not _WG_LIVE:
        return None
    return _WG_LIVE.pop(0)


def _wg_spawn_shards(engine, rec, point, direction):
    if not _WG_FREE and len(_WG_POOL) < _WG_BUDGET:
        _wg_pool_grow(engine)
    if not _WG_FREE and not _WG_LIVE:
        return
    u = rec["u"]; v = rec["v"]; nrm = rec["n"]; c = rec["c"]
    hx = rec["hx"]; hy = rec["hy"]
    total = _WG_SHARDS_PER_HIT
    burst = (total * 7) // 10          # 70% spray from the exact hit point
    for i in range(total):
        if _WG_FREE:
            si = _WG_FREE.pop()
        else:
            si = _wg_steal_oldest()
            if si is None:
                return
        slot = _WG_POOL[si]
        node = slot["node"]
        if node is None or getattr(node, "parent", None) is None:
            _WG_FREE.append(si)         # swept while stopping play: no leak
            continue
        if i < burst:
            # Radial burst centred on the EXACT bullet hole.
            ox = random.uniform(-0.22, 0.22)
            oy = random.uniform(-0.22, 0.22)
            base = point
        else:
            # A few larger shards fall from across the (now gone) pane.
            ox = random.uniform(-hx, hx)
            oy = random.uniform(-hy, hy)
            base = c
        pos = base + u * ox + v * oy + nrm * random.uniform(-0.012, 0.012)
        rx = ox + random.uniform(-0.35, 0.35)
        ry = oy + random.uniform(-0.35, 0.35)
        rdir = u * rx + v * ry
        L = glm.length(rdir)
        if L > 1e-6:
            rdir = rdir / L
        else:
            rdir = u
        # Out-of-plane push along the bullet direction + tiny up-kick;
        # gravity (in the ticker) settles every shard realistically.
        vel = (rdir * random.uniform(0.8, 2.6)
               + direction * random.uniform(0.4, 1.4))
        try:
            vel = glm.vec3(vel.x, vel.y + random.uniform(0.2, 1.0), vel.z)
        except Exception:
            vel = glm.vec3(0.0, 1.0, 0.0)
        slot["vel"] = vel
        slot["spin"] = glm.vec3(random.uniform(-540.0, 540.0),
                                random.uniform(-540.0, 540.0),
                                random.uniform(-540.0, 540.0))
        ttl = random.uniform(0.85, 1.35)
        slot["life"] = ttl
        slot["ttl"] = ttl
        slot["size"] = random.uniform(0.6, 1.6)
        node.position = glm.vec3(pos)
        node.rotation_euler = glm.vec3(random.uniform(0.0, 360.0),
                                       random.uniform(0.0, 360.0),
                                       random.uniform(0.0, 360.0))
        node.scale = glm.vec3(slot["size"])
        node.visible = True
        _WG_LIVE.append(si)


def _wg_tick(engine, dt):
    # Bounded by design: at most _WG_BUDGET iterations, ever.  Dead shards
    # vanish (visible=False) and return to the pool - no node churn, no
    # allocation, no physics (shards are pure visual ballistic debris).
    if not _WG_LIVE:
        return
    keep = []
    for si in _WG_LIVE:
        slot = _WG_POOL[si]
        node = slot["node"]
        if node is None or getattr(node, "parent", None) is None:
            continue                    # swept by stop-play: drop silently
        slot["life"] -= dt
        if slot["life"] <= 0.0:
            node.visible = False
            _WG_FREE.append(si)
            continue
        vel = slot["vel"]
        vel.y -= 9.8 * dt
        node.position = node.position + vel * dt
        sp = slot["spin"]
        re = node.rotation_euler
        node.rotation_euler = glm.vec3(re.x + sp.x * dt,
                                       re.y + sp.y * dt,
                                       re.z + sp.z * dt)
        # Shrink to nothing over the last 35% of life -> pop-free vanish.
        frac = slot["life"] / slot["ttl"]
        if frac < 0.35:
            s = slot["size"] * (frac / 0.35)
            node.scale = glm.vec3(s if s > 0.001 else 0.001)
        keep.append(si)
    _WG_LIVE[:] = keep


# ---------------------------------------------------------------------------
#  5. Shatter: exact point, vanish, sound, shards - all O(1)
# ---------------------------------------------------------------------------
def _wg_shatter(engine, rec, point, direction):
    if not rec["alive"]:
        return
    rec["alive"] = False
    node = rec["node"]
    try:
        node.visible = False            # pane vanishes instantly; the
        _WG_SHATTERED.append(node)      # renderer + physics both skip it
    except Exception:
        pass
    try:
        now = time.monotonic()
        if now - _WG_LAST_SOUND[0] >= 0.03:   # 3D sound, rate-limited
            _WG_LAST_SOUND[0] = now
            engine.audio.play_at("glass_shatter", point, 0.9)
    except Exception:
        pass
    try:
        _wg_spawn_shards(engine, rec, point, direction)
    except Exception as ex:
        _wg_log_once("spawn", "[WALLGLASSPATCH] shard spawn: " + str(ex))


# ---------------------------------------------------------------------------
#  6. Play-state lifecycle: restore panes, fix colliders, rebuild registry
# ---------------------------------------------------------------------------
def _wg_prepare_for_play(engine):
    # (a) restore panes shattered in a previous play session (self-heal)
    for node in list(_WG_SHATTERED):
        try:
            if getattr(node, "parent", None) is not None:
                node.visible = True
        except Exception:
            pass
    del _WG_SHATTERED[:]
    # (b) belt-and-braces upgrade for the CURRENT scene: every window wall
    #     gets the exact-triangle MeshCollider, every glass pane loses its
    #     collider - even nodes spawned before this patch was installed.
    try:
        for node in engine.scene.root.traverse():
            mp = getattr(node, "mesh_params", None)
            if not isinstance(mp, dict):
                continue
            key = mp.get("module_key")
            if key == "window_frame":
                if not isinstance(getattr(node, "collider", None),
                                  MeshCollider):
                    node.collider = MeshCollider()
                    node.rigidbody = None
                mp["collider"] = {"kind": "mesh", "body": "static"}
            elif key == "glass_pane":
                if getattr(node, "collider", None) is not None:
                    node.collider = None
                node.rigidbody = None
                mp["collider"] = {"body": "none"}
    except Exception as ex:
        _wg_log_once("prepare", "[WALLGLASSPATCH] prepare: " + str(ex))
    _WG_DIRTY[0] = True


def _wg_after_stop(engine):
    # Editor safety: stopping Play restores every shattered pane, so the
    # edit-time scene (and anything saved afterwards) is never affected.
    for node in list(_WG_SHATTERED):
        try:
            if getattr(node, "parent", None) is not None:
                node.visible = True
        except Exception:
            pass
    del _WG_SHATTERED[:]
    # Pool nodes were swept by the engine's own stop-play cleanup; drop
    # the stale references so the pool re-allocates lazily next Play.
    del _WG_LIVE[:]
    del _WG_FREE[:]
    del _WG_POOL[:]
    _WG_DIRTY[0] = True


# ---------------------------------------------------------------------------
#  7. Hook installation (each step individually guarded)
# ---------------------------------------------------------------------------

# 7a. Publish the engine instance.
try:
    _WG_PREV_ENGINE_INIT = HeavenEngine.__init__

    def _wg_engine_init(self, *a, **kw):
        _WG_PREV_ENGINE_INIT(self, *a, **kw)
        global _WG_ENGINE
        _WG_ENGINE = self

    HeavenEngine.__init__ = _wg_engine_init
except Exception as _wg_ex:
    _wg_log_once("init", "[WALLGLASSPATCH] engine hook skipped: "
                 + str(_wg_ex))

# 7b. PhysicsWorld.raycast wrap: after the stock raycast, armed shots also
#     test the glass registry and shatter the pane at the exact hit point.
#     The stock RayHit is returned unchanged (the bullet realistically
#     continues through the shattered pane), so every caller - weapon,
#     projectile, enemy bullet - keeps byte-for-byte stock behaviour.
try:
    _WG_PREV_RAYCAST = PhysicsWorld.raycast

    def _wg_raycast(self, origin, direction, max_dist: float = 1000.0,
                    ignore=None, hit_triggers: bool = False):
        hit = _WG_PREV_RAYCAST(self, origin, direction, max_dist, ignore,
                               hit_triggers)
        eng = _WG_ENGINE
        if (_WG_ARMED[0] > 0 and eng is not None and _WG_FAILS[0] < 5
                and getattr(eng, "_playing", False)):
            try:
                if _WG_DIRTY[0]:
                    _wg_rebuild_registry(eng)
                if _WG_PANES:
                    lim = (hit.distance if hit is not None
                           else float(max_dist))
                    ro = _v3(origin)
                    rd = _safe_normalize(_v3(direction),
                                         glm.vec3(0, 0, -1))
                    got = _wg_ray_glass(ro, rd, lim)
                    if got is not None:
                        rec, point, _t = got
                        _wg_shatter(eng, rec, point, rd)
                _WG_FAILS[0] = 0
            except Exception as ex:
                _WG_FAILS[0] += 1     # circuit breaker: 5 strikes -> off
                _wg_log_once("raycast",
                             "[WALLGLASSPATCH] glass test: " + str(ex))
        return hit

    PhysicsWorld.raycast = _wg_raycast
except Exception as _wg_ex:
    _wg_log_once("raycastwrap", "[WALLGLASSPATCH] raycast wrap skipped: "
                 + str(_wg_ex))


# 7c. Arming guards: ONLY weapon shots, projectiles and enemy bullets may
#     shatter glass.  AI line-of-sight, editor picking, spawn probes and
#     every other raycast never touch the shatter system.
def _wg_wrap_armed(cls, method_name):
    try:
        prev = getattr(cls, method_name, None)
        if prev is None:
            return

        def _wg_armed(self, *a, _wg_prev=prev, **kw):
            _WG_ARMED[0] += 1
            try:
                return _wg_prev(self, *a, **kw)
            finally:
                _WG_ARMED[0] -= 1

        setattr(cls, method_name, _wg_armed)
    except Exception:
        pass

try:
    _wg_wrap_armed(Weapon, "_shoot")
except Exception:
    pass
try:
    _wg_wrap_armed(Projectile, "on_tick")
except Exception:
    pass
try:
    _wg_wrap_armed(EnemyBullet, "on_tick")
except Exception:
    pass

# 7d. Per-frame shard ticker: chained onto the scheduler hook that the
#     main loop runs every Play frame (the FINALPATCH2 hook idiom).
try:
    _WG_PREV_SCHED = HeavenEngine._process_scheduler

    def _wg_process_scheduler(self):
        _WG_PREV_SCHED(self)
        if _WG_LIVE and _WG_FAILS[0] < 5:
            try:
                now = time.monotonic()
                last = _WG_LAST_T[0]
                _WG_LAST_T[0] = now
                dt = now - last if last > 0.0 else 1.0 / 60.0
                _wg_tick(self, _clamp(dt, 0.0, 0.1))
            except Exception as ex:
                _WG_FAILS[0] += 1
                _wg_log_once("tick", "[WALLGLASSPATCH] shard tick: "
                             + str(ex))
                if _WG_FAILS[0] >= 5:
                    # Breaker tripped: never leave shards frozen mid-air -
                    # hide every live shard and hand the slots back.
                    for si in list(_WG_LIVE):
                        try:
                            nd = _WG_POOL[si]["node"]
                            if nd is not None:
                                nd.visible = False
                        except Exception:
                            pass
                        _WG_FREE.append(si)
                    del _WG_LIVE[:]

    HeavenEngine._process_scheduler = _wg_process_scheduler
except Exception as _wg_ex:
    _wg_log_once("sched", "[WALLGLASSPATCH] scheduler wrap skipped: "
                 + str(_wg_ex))

# 7e. Begin/end Play lifecycle.
try:
    _WG_PREV_BEGIN_PLAY = HeavenEngine._begin_play

    def _wg_begin_play(self):
        # Collider fixes + pane restore run BEFORE the stock begin-play so
        # the stock physics.rebuild() picks up the corrected colliders.
        try:
            _wg_prepare_for_play(self)
        except Exception as ex:
            _wg_log_once("beginpre", "[WALLGLASSPATCH] begin-play prep: "
                         + str(ex))
        _WG_PREV_BEGIN_PLAY(self)
        try:
            _wg_rebuild_registry(self)
        except Exception as ex:
            _wg_log_once("beginpost", "[WALLGLASSPATCH] registry: "
                         + str(ex))

    HeavenEngine._begin_play = _wg_begin_play
except Exception as _wg_ex:
    _wg_log_once("begin", "[WALLGLASSPATCH] begin-play wrap skipped: "
                 + str(_wg_ex))

try:
    _WG_PREV_STOP_PLAY = HeavenEngine._stop_play

    def _wg_stop_play(self):
        _WG_PREV_STOP_PLAY(self)
        try:
            _wg_after_stop(self)
        except Exception as ex:
            _wg_log_once("stop", "[WALLGLASSPATCH] stop-play: " + str(ex))

    HeavenEngine._stop_play = _wg_stop_play
except Exception as _wg_ex:
    _wg_log_once("stopwrap", "[WALLGLASSPATCH] stop-play wrap skipped: "
                 + str(_wg_ex))

# 7f. Mid-play sector generation: mark the registry dirty so the next
#     armed shot re-indexes (amortized; generation itself already applies
#     the corrected collider plans via the wrapped _fp_default_collider).
try:
    _WG_PREV_GEN_SECTOR = ProceduralSectorGenerator.generate_sector

    def _wg_generate_sector(self, *a, **kw):
        out = _WG_PREV_GEN_SECTOR(self, *a, **kw)
        _WG_DIRTY[0] = True
        return out

    ProceduralSectorGenerator.generate_sector = _wg_generate_sector
except Exception:
    pass

# 7g. Glass-shatter sound: one extra entry in the procedural sound bank
#     (the FINALPATCH2 idiom - _synth_bank() runs inside
#     AudioSystem.__init__ at engine construction, after this block).
try:
    _WG_PREV_SYNTH_BANK = _synth_bank

    def _wg_synth_bank():
        bank = _WG_PREV_SYNTH_BANK()
        try:
            if "glass_shatter" not in bank:
                sr = _AUDIO_SAMPLE_RATE
                # ~0.32 s: bright unfiltered noise crash (fast attack,
                # quick decay) + two high glassy sine pings + a delayed
                # tinkle tail.
                n = int(0.32 * sr)
                crash = (_synth_noise(n, 301)
                         * _synth_envelope(n, 0.0008, 30.0))
                p1 = (_synth_sine((3400.0, 2100.0), n)
                      * _synth_envelope(n, 0.0008, 42.0))
                p2 = (_synth_sine((5100.0, 2900.0), n)
                      * _synth_envelope(n, 0.0008, 55.0))
                dn = int(0.18 * sr)
                tail = np.concatenate([
                    np.zeros(n - dn, np.float32),
                    _synth_sine((6200.0, 4300.0), dn)
                    * _synth_envelope(dn, 0.001, 30.0) * 0.5])
                bank["glass_shatter"] = _audio_f32_to_stereo_bytes(
                    _synth_mix(crash * 0.9, p1 * 0.30, p2 * 0.22,
                               tail * 0.35))
        except Exception as ex:
            _wg_log_once("synth", "[WALLGLASSPATCH] glass sound skipped: "
                         + str(ex))
        return bank

    _synth_bank = _wg_synth_bank
except Exception as _wg_ex:
    _wg_log_once("synthwrap", "[WALLGLASSPATCH] synth wrap skipped: "
                 + str(_wg_ex))

# [WALLGLASSPATCH:wall_physics_glass_shatter END v1]

