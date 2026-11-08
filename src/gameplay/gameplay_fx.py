# @heaven3d-part gameplay/gameplay_fx.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# =====================================================================
# [FINALPATCH2:gameplay]
# FinalPatch gameplay upgrades (append-only monkey-patch module, in the
# same spirit as the engine's own [TRIPLEA:...] / [FINALPATCH:...]
# blocks: capture-previous + wrap + reassign, double-install guard).
#   1) Doorway physics: door frames collide with their EXACT frame mesh
#      (jambs + lintel only), so the visual opening is walkable.  The
#      old full-cell solid box + kinematic body is gone for good.
#   2) Weapon feedback: water shots splash (shader ripple ring +
#      synthesized splash sound + droplet quads); hard-surface hits add
#      an impact crack sound, a bullet-hole decal and a spark flash.
#   4) Smart flying enemies: wall-aware steering via 5 staggered,
#      AABB-prefiltered whisker raycasts - at most ONE short raycast per
#      frame per enemy, so there is zero FPS drop.
# Every wrapper captures the previous implementation and falls back to
# the stock behaviour on any error - the game can never break.
# NOTE: everything this block wraps is defined ABOVE this insertion
# point in the file, so direct name references at install time are
# safe; names that only exist while the engine runs (ctx, scenes...)
# are only touched inside function bodies.
# =====================================================================
if not globals().get("_FP2_GAMEPLAY_INSTALLED"):
    _FP2_GAMEPLAY_INSTALLED = True

    # ----------------------------------------------------- shared state
    _FP2_FX = []            # live effect dicts, advanced by the FX ticker
    _FP2_DECALS = []        # live bullet-hole decal fx dicts (oldest first)
    _FP2_QUAD_MESH = {}     # id(ctx) -> shared unit-quad Mesh
    _FP2_FX_MAX = 256       # hard cap on simultaneous effect quads
    _FP2_DECAL_MAX = 48     # hard cap on live bullet-hole decals
    _FP2_FX_FAILS = 0       # consecutive ticker failures -> auto-disable
    _FP2_FX_LAST_T = [0.0]  # monotonic timestamp of the last FX tick
    _FP2_LOGGED = set()     # one-shot log keys

    def _fp2_log_once(key, msg):
        if key in _FP2_LOGGED:
            return
        _FP2_LOGGED.add(key)
        try:
            Log.warn(msg)
        except Exception:
            pass

    # ---------------------------------------------------------------
    # Feature 1 - doorway physics: exact frame mesh, not a solid box.
    # ---------------------------------------------------------------
    _FP2_PREV_DEFAULT_COLLIDER = _fp_default_collider

    def _fp2_default_collider(key, cell, fh):
        # The stock plan for "door_frame" was a full-cell solid box
        # (2.0 x 3.0 x 0.12 m) plus a kinematic RigidBody, which sealed
        # the 1.64 m x 2.1 m visual opening that build_door_frame leaves
        # between the jambs and under the lintel.  A mesh plan makes the
        # engine's MeshCollider re-source the node's exact frame
        # triangles (jambs + lintel only, opening free); _apply_plan
        # always gives mesh plans rigidbody = None, i.e. static level
        # geometry - the same scheme the stairwell module proves on
        # every load, and {"type": "mesh"} plans serialize/round-trip.
        try:
            if key == "door_frame":
                return {"kind": "mesh", "body": "static"}
        except Exception:
            pass
        return _FP2_PREV_DEFAULT_COLLIDER(key, cell, fh)

    # Plain global rebind: every caller (rebuild_module, _spawn_module)
    # looks _fp_default_collider up in module globals at call time.
    _fp_default_collider = _fp2_default_collider

    _FP2_PREV_REBUILD_MODULE = ProceduralSectorGenerator.rebuild_module

    def _fp2_rebuild_module(self, node):
        # Old saves may carry a serialized solid-box / kinematic collider
        # plan for door frames inside mesh_params.  Drop it BEFORE
        # delegating so rebuild_module recomputes the plan through the
        # wrapped _fp_default_collider (mesh plan) and _apply_plan then
        # purges the stale kinematic rigidbody (mesh plans always set
        # node.rigidbody = None).  rebuild_module reads the module key
        # via p.get("module_key", "wall") - verified in the source.
        try:
            p = getattr(node, "mesh_params", None)
            if isinstance(p, dict) and p.get("module_key") == "door_frame":
                p["collider"] = None
        except Exception:
            pass
        return _FP2_PREV_REBUILD_MODULE(self, node)

    ProceduralSectorGenerator.rebuild_module = _fp2_rebuild_module

    # ---------------------------------------------------------------
    # Feature 2 - weapon FX: procedural sounds, splashes, decals.
    # ---------------------------------------------------------------
    _FP2_PREV_SYNTH_BANK = _synth_bank

    def _fp2_synth_bank():
        # _synth_bank() is called inside AudioSystem.__init__ at engine
        # construction - AFTER this module-level code runs - so the two
        # extra entries below are registered as pygame.mixer.Sound
        # automatically, exactly like the stock sounds.
        bank = _FP2_PREV_SYNTH_BANK()
        try:
            sr = _AUDIO_SAMPLE_RATE
            if "splash" not in bank:
                # ~0.45 s: low-passed noise burst (fast attack ~0.005 s,
                # medium decay) + a 700->180 Hz sine "plunge" body + a
                # softer, delayed noise "drip" tail.
                n = int(0.45 * sr)
                burst = _synth_lowpass(
                    _synth_noise(n, 101) * _synth_envelope(n, 0.005, 9.0),
                    0.25)
                plunge = (_synth_sine((700.0, 180.0), n)
                          * _synth_envelope(n, 0.005, 7.0))
                dn = int(0.16 * sr)
                drip = np.concatenate([
                    np.zeros(n - dn, np.float32),
                    _synth_lowpass(_synth_noise(dn, 102)
                                   * _synth_envelope(dn, 0.002, 18.0),
                                   0.18)])
                bank["splash"] = _audio_f32_to_stereo_bytes(
                    _synth_mix(burst * 1.0, plunge * 0.8, drip * 0.5))
            if "bullet_impact" not in bank:
                # ~0.18 s: sharp seeded noise crack (attack 0.001 s,
                # decay ~40) + quiet high sine ping sweep (2800->900 Hz)
                # + low thud sine (220->70 Hz).
                n = int(0.18 * sr)
                crack = (_synth_noise(n, 103)
                         * _synth_envelope(n, 0.001, 40.0))
                ping = (_synth_sine((2800.0, 900.0), n)
                        * _synth_envelope(n, 0.001, 45.0))
                thud = (_synth_sine((220.0, 70.0), n)
                        * _synth_envelope(n, 0.001, 25.0))
                bank["bullet_impact"] = _audio_f32_to_stereo_bytes(
                    _synth_mix(crack * 0.9, ping * 0.35, thud * 0.7))
        except Exception as ex:
            _fp2_log_once("synth", f"[FINALPATCH2] FX sounds skipped: {ex}")
        return bank

    _synth_bank = _fp2_synth_bank

    # ------------------------------------------------- quad FX helpers
    def _fp2_quad_mesh(ctx):
        # ONE shared unit quad (XY plane, facing +Z), cached per GL
        # context; every effect / decal node references this mesh.
        key = id(ctx)
        m = _FP2_QUAD_MESH.get(key)
        if m is None:
            pos = np.array([[-0.5, -0.5, 0.0], [0.5, -0.5, 0.0],
                            [0.5, 0.5, 0.0], [-0.5, 0.5, 0.0]],
                           dtype=np.float32)
            nrm = np.array([[0.0, 0.0, 1.0]] * 4, dtype=np.float32)
            uv = np.array([[0.0, 0.0], [1.0, 0.0],
                           [1.0, 1.0], [0.0, 1.0]], dtype=np.float32)
            idx = np.array([0, 1, 2, 0, 2, 3], dtype=np.uint32)
            m = Mesh.from_arrays(ctx, pos, nrm, uv, idx, name="_fp2_quad")
            _FP2_QUAD_MESH[key] = m
        return m

    # [FINALPATCH3:round_decals] shared round FX meshes + ragged hole texture
    _FP2_DISC_MESH = {}
    _FP2_SPHERE_MESH = {}
    _FP2_DECAL_PNG = [None]

    def _fp2_disc_mesh(ctx, segments=40):
        # ONE shared unit DISC (triangle fan on the XY plane facing +Z) so
        # bullet holes are round instead of square; cached per GL context.
        key = id(ctx)
        m = _FP2_DISC_MESH.get(key)
        if m is None:
            seg = max(12, int(segments))
            pos = [[0.0, 0.0, 0.0]]
            nrm = [[0.0, 0.0, 1.0]]
            uv = [[0.5, 0.5]]
            for i in range(seg):
                a = math.tau * float(i) / float(seg)
                ca, sa = math.cos(a), math.sin(a)
                pos.append([0.5 * ca, 0.5 * sa, 0.0])
                nrm.append([0.0, 0.0, 1.0])
                uv.append([0.5 + 0.5 * ca, 0.5 + 0.5 * sa])
            idx = []
            for i in range(seg):
                idx += [0, 1 + i, 1 + (i + 1) % seg]
            m = Mesh.from_arrays(ctx, np.array(pos, dtype=np.float32),
                                 np.array(nrm, dtype=np.float32),
                                 np.array(uv, dtype=np.float32),
                                 np.array(idx, dtype=np.uint32),
                                 name="_fp2_disc")
            _FP2_DISC_MESH[key] = m
        return m

    def _fp2_sphere_fx_mesh(ctx):
        # ONE shared low-poly unit sphere (radius 0.5, same 12x8 budget as
        # the enemy-bullet mesh) for volumetric splash droplets.
        key = id(ctx)
        m = _FP2_SPHERE_MESH.get(key)
        if m is None:
            m = Mesh.make_sphere(ctx, 0.5, 12, 8)
            _FP2_SPHERE_MESH[key] = m
        return m

    def _fp2_decal_png(size=64):
        # Ragged bullet-hole RGBA texture: punched near-black core, charred
        # rim, alpha erodes to 0 across an irregular noisy radius, so the
        # decal reads as a HOLE torn into the surface, not a square sticker.
        if _FP2_DECAL_PNG[0] is None:
            yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
            cx = (float(size) - 1.0) * 0.5
            dx = (xx - cx) / (float(size) * 0.5)
            dy = (yy - cx) / (float(size) * 0.5)
            r = np.sqrt(dx * dx + dy * dy)
            ang = np.arctan2(dy, dx)
            wob = (0.09 * np.sin(ang * 5.0 + 1.3)
                   + 0.05 * np.sin(ang * 11.0 + 4.1)
                   + 0.03 * np.sin(ang * 19.0 + 2.2))
            edge = 0.88 + wob                     # ragged outer radius
            core = 0.34 + 0.55 * wob              # irregular punched center
            alpha = np.clip((edge - r) / 0.20, 0.0, 1.0)
            hole = np.clip((core + 0.26 - r) / 0.26, 0.0, 1.0)
            lum = 0.22 - 0.10 * np.clip(r, 0.0, 1.0)      # charred rim ring
            lum = lum * (1.0 - 0.85 * hole) + 0.015       # near-black core
            rgba = np.zeros((size, size, 4), dtype=np.float32)
            rgba[..., 0] = lum * 1.06
            rgba[..., 1] = lum * 0.99
            rgba[..., 2] = lum * 0.92
            rgba[..., 3] = alpha
            _FP2_DECAL_PNG[0] = _fp2_rgba_to_png(rgba)
        return _FP2_DECAL_PNG[0]

    def _fp2_rgba_to_png(rgba):
        # uint8-capable RGBA8 PNG encoder, stdlib zlib/struct only (the
        # existing _fp_rgb_to_png path is RGB-only and would drop alpha).
        import zlib
        h, w = int(rgba.shape[0]), int(rgba.shape[1])
        raw = np.clip(rgba * 255.0, 0, 255).astype(np.uint8).tobytes()
        stride = w * 4
        rows = b"".join(b"\x00" + raw[y * stride:(y + 1) * stride]
                        for y in range(h))

        def _chunk(tag, payload):
            return (struct.pack(">I", len(payload)) + tag + payload
                    + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

        ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
        return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr)
                + _chunk(b"IDAT", zlib.compress(rows, 6))
                + _chunk(b"IEND", b""))

    def _fp2_euler_for_normal(normal, roll_deg=0.0):
        # The quad faces +Z; with the engine's R = Ry*Rx*Rz Euler
        # convention (see SceneNode.local_matrix), yaw = atan2(x, z) and
        # pitch = -asin(y) rotate the quad so its +Z face points along
        # +n (EnemyFlyShooter._fire uses the mirrored formula because a
        # node's -Z forward must point along its flight direction).
        n = _safe_normalize(_v3(normal), glm.vec3(0, 0, 1))
        yaw = math.degrees(math.atan2(n.x, n.z))
        pitch = math.degrees(-math.asin(_clamp(float(n.y), -1.0, 1.0)))
        return glm.vec3(pitch, yaw, float(roll_deg))

    def _fp2_spawn_quad(e, pos, normal, size, color, ttl, vel=None,
                        gravity=0.0, shrink=0.0, roll=0.0,
                        fade_after=0.0, kind="fx"):
        # A small BLEND quad: own Material instance (so the ticker can
        # fade its alpha), NO collider, parented to the scene root and
        # registered as a runtime node so stopping Play sweeps it up
        # together with bullets and other gameplay spawns.
        node = SceneNode("_fp2_" + kind, parent=e.scene.root)
        node.position = glm.vec3(pos)
        node.rotation_euler = _fp2_euler_for_normal(normal, roll)
        node.scale = glm.vec3(max(float(size), 0.001))
        if kind == "decal":
            node.mesh = _fp2_disc_mesh(e.ctx)       # [FINALPATCH3:round_decals]
        elif kind == "splash":
            node.mesh = _fp2_sphere_fx_mesh(e.ctx)  # [FINALPATCH3:sphere_splash]
        else:
            node.mesh = _fp2_quad_mesh(e.ctx)
        node.material = Material(
            name="_fp2_" + kind + "_mat",
            base_color=(float(color[0]), float(color[1]),
                        float(color[2]), float(color[3])),
            roughness=0.9, metallic=0.0,
            alpha_mode="BLEND", double_sided=True)
        if kind == "decal":
            node.material.base_color_tex = ("bytes", _fp2_decal_png())
        node.visible = True
        fx = {"node": node, "mat": node.material,
              "vel": glm.vec3(vel) if vel is not None else glm.vec3(0.0),
              "gravity": float(gravity), "ttl": float(ttl),
              "life": float(ttl), "shrink": float(shrink),
              "a0": float(color[3]), "fade_after": float(fade_after),
              "size": float(size), "kind": kind}
        _FP2_FX.append(fx)
        try:
            e._spawn_runtime_nodes.append(node)
        except Exception:
            pass
        if len(_FP2_FX) > _FP2_FX_MAX:
            _fp2_kill_fx(e, _FP2_FX[0])
        return fx

    def _fp2_forget_fx(fx):
        try:
            _FP2_FX.remove(fx)
        except ValueError:
            pass
        try:
            _FP2_DECALS.remove(fx)
        except ValueError:
            pass

    def _fp2_kill_fx(e, fx):
        node = fx.get("node")
        fx["node"] = None
        _fp2_forget_fx(fx)
        if node is not None:
            try:
                if node in e._spawn_runtime_nodes:
                    e._spawn_runtime_nodes.remove(node)
            except Exception:
                pass
            try:
                e.destroy_node(node)
            except Exception:
                try:
                    node.remove()
                except Exception:
                    pass

    def _fp2_tick_fx(e, dt):
        for fx in list(_FP2_FX):
            node = fx["node"]
            if node is None or getattr(node, "parent", None) is None:
                _fp2_forget_fx(fx)   # swept by stop-play or destroyed
                continue
            fx["life"] -= dt
            if fx["life"] <= 0.0:
                _fp2_kill_fx(e, fx)
                continue
            v = fx["vel"]
            if fx["gravity"] > 0.0:
                v.y -= fx["gravity"] * dt
            if v.x or v.y or v.z:
                node.position = node.position + v * dt
            a = fx["a0"]
            fade = fx["fade_after"]
            if fade > 0.0 and fx["life"] < fade:
                a *= fx["life"] / fade
            bc = fx["mat"].base_color
            fx["mat"].base_color = (bc[0], bc[1], bc[2],
                                    _clamp(a, 0.0, 1.0))
            if fx["shrink"] > 0.0:
                frac = fx["life"] / fx["ttl"]
                s = fx["size"] * (1.0 - fx["shrink"] * (1.0 - frac))
                node.scale = glm.vec3(max(s, 0.001))

    _FP2_PREV_PROCESS_SCHEDULER = HeavenEngine._process_scheduler

    def _fp2_process_scheduler(self):
        # _process_scheduler runs every frame while playing (the main
        # loop gates it on self._playing), making it the perfect
        # dependency-free per-frame hook for advancing the effect quads.
        _FP2_PREV_PROCESS_SCHEDULER(self)
        global _FP2_FX_FAILS
        if _FP2_FX_FAILS >= 5 or not _FP2_FX:
            return
        try:
            now = time.monotonic()
            last = _FP2_FX_LAST_T[0]
            _FP2_FX_LAST_T[0] = now
            dt = now - last if last > 0.0 else 1.0 / 60.0
            _fp2_tick_fx(self, _clamp(dt, 0.0, 0.1))
            _FP2_FX_FAILS = 0
        except Exception as ex:
            _FP2_FX_FAILS += 1   # repeated failure disables the ticker
            _fp2_log_once("fx_tick", f"[FINALPATCH2] FX ticker error: {ex}")

    HeavenEngine._process_scheduler = _fp2_process_scheduler

    # ------------------------------------------------- water ray test
    def _fp2_ray_water_t(ro, rd, max_t, now):
        # First t in (0, max_t] where the ray dips below the ocean
        # surface: 16 march steps + a few bisection refinements.  Cheap,
        # once per shot.  None when the ray stays dry.
        sea = float(_OCEAN_STATE.get("sea_level", 0.0))

        def _f(t):
            x = ro.x + rd.x * t
            z = ro.z + rd.z * t
            return (ro.y + rd.y * t) - (sea + _ocean_wave_height_at(x, z, now))

        if _f(0.0) <= 0.0:
            return None        # camera at/below the surface: no splash
        steps = 16
        prev_t = 0.0
        for i in range(1, steps + 1):
            t = max_t * i / steps
            if _f(t) <= 0.0:
                lo, hi = prev_t, t
                for _ in range(5):
                    mid = 0.5 * (lo + hi)
                    if _f(mid) <= 0.0:
                        hi = mid
                    else:
                        lo = mid
                return 0.5 * (lo + hi)
            prev_t = t
        return None

    def _fp2_water_splash_fx(e, point):
        now = time.monotonic() % 3600.0
        _ocean_register_ripple(point.x, point.z, now, 0.9)  # shader ring
        e.audio.play_at("splash", point, 0.9)   # fails safe if missing
        e.spawn_flash(point + glm.vec3(0, 0.15, 0),
                      color=(0.65, 0.85, 1.0), intensity=6.0,
                      range=4.0, ttl=0.12)
        up = glm.vec3(0, 1, 0)
        for _ in range(random.randint(14, 20)):   # [FINALPATCH3:sphere_splash]
            a = random.uniform(0.0, math.tau)
            r = random.uniform(0.1, 0.5)
            vel = glm.vec3(math.cos(a) * r,
                           random.uniform(1.2, 2.6),
                           math.sin(a) * r)
            _fp2_spawn_quad(
                e, point, up,
                size=random.uniform(0.05, 0.12),
                color=(0.75, 0.88, 1.0, 0.55),
                ttl=random.uniform(0.35, 0.55),
                vel=vel, gravity=5.5, shrink=0.6,
                fade_after=0.35,
                roll=random.uniform(0.0, 360.0), kind="splash")

    def _fp2_hard_impact_fx(e, hit, sound="bullet_impact"):
        if sound:
            e.audio.play_at(sound, hit.point, 0.8)
        n = _safe_normalize(_v3(hit.normal), glm.vec3(0, 1, 0))
        # Bullet-hole decal: offset along the normal (+ a tiny per-decal
        # epsilon jitter) so stacked decals never z-fight each other or
        # the surface; capped, oldest destroyed first, fades after ~20 s.
        # [FINALPATCH3:no_enemy_decals] flesh hits never leave bullet holes:
        # skip ONLY the decal when the struck node is, or descends from, an
        # enemy spawn marker; spark flash and impact sound are untouched.
        _node = getattr(hit, "node", None)
        _is_enemy = False
        while _node is not None:
            if getattr(_node, "spawn_kind", None) == "enemy":
                _is_enemy = True
                break
            _node = getattr(_node, "parent", None)
        if not _is_enemy:
            eps = 0.012 + (len(_FP2_DECALS) % 7) * 0.0007
            fx = _fp2_spawn_quad(
                e, hit.point + n * eps, n,
                size=random.uniform(0.09, 0.13),
                color=(0.05, 0.045, 0.04, 0.92),
                ttl=25.0, fade_after=5.0,
                roll=random.uniform(0.0, 360.0), kind="decal")
            _FP2_DECALS.append(fx)
            while len(_FP2_DECALS) > _FP2_DECAL_MAX:
                _fp2_kill_fx(e, _FP2_DECALS[0])
        # Tiny impact spark quad (fades + shrinks over ~0.08 s) ...
        _fp2_spawn_quad(
            e, hit.point + n * 0.02, n,
            size=random.uniform(0.10, 0.16),
            color=(1.0, 0.85, 0.6, 0.85),
            ttl=0.08, shrink=1.0, fade_after=0.08, kind="spark")
        # ... and a warm flash light, in ADDITION to the stock hit sound.
        e.spawn_flash(hit.point + n * 0.05, color=(1.0, 0.85, 0.6),
                      intensity=3.0, range=2.5, ttl=0.06)

    # ------------------------------------------------- Weapon._shoot
    _FP2_PREV_WEAPON_SHOOT = Weapon._shoot

    def _fp2_weapon_shoot_impl(self):
        # Verbatim replica of the stock Weapon._shoot with two inserted
        # FX sections (marked [FINALPATCH2]); every other line - ammo,
        # cooldown, spread, raycast, damage message, impulse, hit sound,
        # muzzle flash, recoil - is byte-for-byte the original logic.
        e = self.engine
        self.ammo -= 1
        self._cool = 1.0 / max(float(self.p("fire_rate")), 0.01)
        self._bloom = min(self._bloom + 1.0, 4.0)
        e.audio.play(self.p("shoot_sound"), 0.7)

        cam = e.camera
        d = cam.forward()
        spread = math.radians(float(self.p("spread_deg")) *
                              (1.0 + self._bloom * 0.8))
        if spread > 1e-6:
            ang = random.uniform(0.0, 2.0 * math.pi)
            rad = random.uniform(0.0, math.tan(spread))
            right = cam.right()
            up = glm.normalize(glm.cross(right, d))
            d = glm.normalize(d + right * (math.cos(ang) * rad)
                              + up * (math.sin(ang) * rad))

        rng = float(self.p("range"))
        hit = e.physics.raycast(cam.position, d, rng,
                                ignore=self._ignore)

        # ---- [FINALPATCH2] water test ---------------------------------
        # March/bisect the shot ray against the ocean surface; a splash
        # happens only when the water crossing is closer than any
        # physics hit (or there is no physics hit at all).
        water_point = None
        try:
            ocn = getattr(e.scene, "ocean", None)
            if bool(_OCEAN_STATE.get("enabled")) or (
                    ocn is not None
                    and bool(getattr(ocn, "enabled", False))):
                now = time.monotonic() % 3600.0
                limit = hit.distance if hit is not None else rng
                wt = _fp2_ray_water_t(cam.position, d,
                                      min(float(limit), rng), now)
                if wt is not None:
                    water_point = cam.position + d * wt
        except Exception:
            water_point = None

        if water_point is not None:
            # Water absorbs the shot: splash FX only, no hard-surface
            # damage / impulse (the water crossing was closer than any
            # physics hit, so the physics hit cannot win).  Ammo,
            # cooldown, shoot sound, muzzle flash and recoil still run
            # exactly as stock.
            try:
                _fp2_water_splash_fx(e, water_point)
            except Exception:
                pass
        elif hit is not None:
            payload = {"amount": float(self.p("damage")),
                       "point": hit.point, "direction": d,
                       "source": "weapon"}
            e.message_ancestors(hit.node, "damage", payload)
            e.message_subtree(hit.node, "damage", payload)
            rb = getattr(hit.node, "rigidbody", None)
            if rb is not None and rb.body_type == "dynamic":
                rb.add_impulse_at_point(d * float(self.p("impact_impulse")),
                                        hit.point)
            e.audio.play_at(self.p("hit_sound"), hit.point, 0.4)
            # ---- [FINALPATCH2] hard-surface impact FX -----------------
            try:
                _fp2_hard_impact_fx(e, hit)
            except Exception:
                pass

        if self.p("muzzle_flash"):
            e.spawn_flash(cam.position + d * 0.9 + glm.vec3(0, -0.08, 0),
                          color=(1.0, 0.72, 0.30), intensity=22.0,
                          range=7.0, ttl=0.05)

        # ---- recoil (drives the FPSController's persistent pitch/yaw so
        # the kick actually sticks instead of being overwritten next tick)
        kick = float(self.p("camera_kick"))
        pc = self._player_bp()
        yaw_jitter = kick * random.uniform(-0.35, 0.35)
        pitch_kick = kick * random.uniform(0.8, 1.2)
        if pc is not None:
            pc.pitch = _clamp(pc.pitch + pitch_kick, -89.0, 89.0)
            pc.yaw += yaw_jitter
        else:
            cam.pitch = _clamp(cam.pitch + pitch_kick, -89.0, 89.0)
            cam.yaw += yaw_jitter

    def _fp2_weapon_shoot(self):
        # Never break shooting: any exception that escaped the inner FX
        # guards falls back to the stock implementation for this shot.
        try:
            _fp2_weapon_shoot_impl(self)
        except Exception as ex:
            _fp2_log_once("shoot",
                          f"[FINALPATCH2] weapon FX fallback: {ex}")
            _FP2_PREV_WEAPON_SHOOT(self)

    Weapon._shoot = _fp2_weapon_shoot

    def _fp2_spawn_impact_fx(self, hit, sound="bullet_impact"):
        """Bullet hole, spark and flash at a raycast hit.

        The stock Weapon makes these itself, but a weapon from a package
        has no way in, so any shooter can call
        `engine.spawn_impact_fx(hit)` and leave the same marks.  Pass
        `sound=None` to keep the visuals and play your own.
        """
        try:
            _fp2_hard_impact_fx(self, hit, sound)
        except Exception as ex:
            _fp2_log_once("impact_api", f"[FINALPATCH2] impact FX failed: {ex}")

    HeavenEngine.spawn_impact_fx = _fp2_spawn_impact_fx

    # ---------------------------------------------------------------
    # Feature 4 - smart wall-aware flying enemy (zero FPS drop).
    # ---------------------------------------------------------------
    _FP2_PREV_FLY_BEGIN = EnemyFlyShooter.on_begin_play
    _FP2_PREV_FLY_ON_TICK = EnemyFlyShooter.on_tick

    def _fp2_fly_on_begin_play(self):
        _FP2_PREV_FLY_BEGIN(self)
        # Whisker cache: id -> (RayHit|None, cast_pos, cast_frame).
        self._fp2_whisk = {}
        self._fp2_frame = 0
        self._fp2_tick_failed = False

    EnemyFlyShooter.on_begin_play = _fp2_fly_on_begin_play

    def _fp2_yaw_rot(v, deg):
        r = math.radians(deg)
        c, s = math.cos(r), math.sin(r)
        return glm.vec3(v.x * c + v.z * s, 0.0, -v.x * s + v.z * c)

    def _fp2_ray(engine, ro, rd, max_t, ignore):
        # Cheap raycast: prefilter physics entries with the existing
        # slab test _ray_aabb_t (refreshing stale kinematic AABBs first)
        # and only ray-shape the survivors; nearest wins.  Skip rules
        # (ignore ids / invisible / triggers) mirror PhysicsWorld.raycast
        # exactly.  ANY exception falls back to the full stock raycast.
        try:
            phys = engine.physics
            ro = _v3(ro)
            rd = _safe_normalize(_v3(rd), glm.vec3(0, 0, -1))
            best = None
            best_t = float(max_t)
            for ent in phys.entries:
                if ignore and id(ent.node) in ignore:
                    continue
                if not ent.node.visible:
                    continue
                if ent.collider.is_trigger:
                    continue
                if ent.kind != "static":
                    try:
                        ent.refresh_aabb()
                    except Exception:
                        continue
                if not _ray_aabb_t(ro, rd, ent.aabb0, ent.aabb1, best_t):
                    continue
                t, nn = phys._ray_shape(ro, rd, best_t, ent.shape())
                if t is not None and t < best_t:
                    best_t = t
                    best = RayHit(ent.node, ent.collider,
                                  ro + rd * t, nn, t)
            return best
        except Exception:
            return engine.physics.raycast(ro, rd, max_t, ignore=ignore)

    def _fp2_fly_cast_whisker(self, e, pos, dir_h, wlen):
        # ONE whisker raycast per frame, round-robin over the five
        # directions (forward along the desired horizontal dir, +/-40
        # deg yaw offsets, up, down); the result is cached.
        if getattr(self, "_fp2_whisk", None) is None:
            self._fp2_whisk = {}
            self._fp2_frame = 0
        idx = self._fp2_frame % 5
        self._fp2_frame += 1
        dirs = (dir_h,
                _fp2_yaw_rot(dir_h, 40.0),
                _fp2_yaw_rot(dir_h, -40.0),
                glm.vec3(0.0, 1.0, 0.0),
                glm.vec3(0.0, -1.0, 0.0))
        rd = _safe_normalize(dirs[idx], glm.vec3(0, 0, -1))
        hit = _fp2_ray(e, pos, rd, wlen, self._ignore)
        self._fp2_whisk[idx] = (hit, glm.vec3(pos), self._fp2_frame)

    def _fp2_fly_whisk_hit(self, idx, pos):
        # A cached whisker is only trusted for <=4 frames and while the
        # node has moved less than half its body radius since the cast;
        # stale entries are treated as "no wall" (the round-robin will
        # refresh that whisker on a coming frame).  Net cost stays at
        # most one short raycast per frame per enemy.
        ent = getattr(self, "_fp2_whisk", None)
        ent = ent.get(idx) if ent else None
        if ent is None:
            return None
        hit, cast_pos, cast_frame = ent
        if self._fp2_frame - cast_frame > 4:
            return None
        if glm.length(pos - cast_pos) > self._body_r * 0.5:
            return None
        return hit

    def _fp2_fly_on_tick_impl(self, dt):
        # Verbatim replica of the stock EnemyFlyShooter.on_tick with the
        # movement section replaced by whisker-based wall-aware steering
        # (marked [FINALPATCH2]).  Detection, facing, standoff distance,
        # hover height, bobbing, shooting and the idle-hover branch are
        # the original logic; the helpers _face_towards / _los / _fire
        # are reused untouched.
        if not self._alive:
            return
        e = self.engine
        self._cool -= dt
        pos = self.node.world_position()

        pb = self._player_bp()
        player = pb.node if (pb is not None and not pb._dead) else None
        if player is not None:
            ppos = player.world_position()
            to_p = ppos - pos
            dist = glm.length(to_p)
            if dist <= float(self.p("detect_range")):
                self._face_towards(to_p, dt)
                move = glm.vec3(0.0)
                # Horizontal pursuit with a standoff distance (the drone
                # circles at shooting range instead of hugging the player).
                dh = glm.vec3(to_p.x, 0.0, to_p.z)
                d_h = glm.length(dh)
                speed = float(self.p("fly_speed"))
                dir_h = _safe_normalize(dh, glm.vec3(0, 0, -1))
                # ---- [FINALPATCH2] wall awareness ---------------------
                # 5 whiskers, ONE raycast per frame (round-robin); the
                # stock single forward probe is replaced by the forward
                # whisker - not doubled.
                wlen = speed * dt + self._body_r + 0.25
                _fp2_fly_cast_whisker(self, e, pos, dir_h, wlen)
                if d_h > float(self.p("keep_distance")) + 0.5:
                    v = dir_h * speed
                    fwd = _fp2_fly_whisk_hit(self, 0, pos)
                    if fwd is not None:
                        # Forward blocked: slide along the wall; cancel
                        # the horizontal move entirely (stall, as the
                        # original did) when fully blocked.
                        n = _safe_normalize(_v3(fwd.normal),
                                            glm.vec3(0, 1, 0))
                        v = v - n * glm.dot(v, n)
                        if glm.length(v) < 1e-4:
                            v = glm.vec3(0.0)
                    for sidx in (1, 2):
                        sh = _fp2_fly_whisk_hit(self, sidx, pos)
                        if sh is not None:
                            # Side whisker blocked: gentle push away
                            # from that side's normal.
                            n = _safe_normalize(_v3(sh.normal),
                                                glm.vec3(0, 1, 0))
                            v = v + n * (speed * 0.5)
                    move += v
                # Vertical: hover at the player's chest height + bobbing.
                want_y = ppos.y + 0.9 + math.sin(self._bob_t) * 0.25
                dy = _clamp(want_y - pos.y, -1.0, 1.0)
                vy = dy * min(speed, 3.0)
                # [FINALPATCH2] vertical whiskers gate move.y (the stock
                # vertical motion was unchecked and could tunnel through
                # roofs / floors).
                if vy > 0.0 and _fp2_fly_whisk_hit(self, 3, pos) is not None:
                    vy = 0.0
                elif vy < 0.0 and _fp2_fly_whisk_hit(self, 4, pos) is not None:
                    vy = 0.0
                move.y = vy
                self._bob_t += dt * 2.1
                if glm.length(move) > 1e-6:
                    _write_node_world_position(self.node, pos + move * dt)
                    pos = self.node.world_position()
                # Shoot when in range with a clear line of sight.
                if dist <= float(self.p("attack_range")):
                    chest = ppos + glm.vec3(0.0, 0.35, 0.0)
                    if self._los(pos, chest):
                        rate = max(0.05, float(self.p("shots_per_sec")))
                        if self._cool <= 0.0:
                            self._cool = 1.0 / rate
                            fwd = Scene.forward_from_rotation(
                                self.node.rotation_euler)
                            self._fire(pos + fwd * (self._body_r + 0.07),
                                       chest)
        else:
            # No player: gentle hover in place over the spawn point,
            # with the same up/down whisker gating as the pursuit branch.
            self._bob_t += dt * 1.4
            want = self._home + glm.vec3(0.0, math.sin(self._bob_t) * 0.15,
                                         0.0)
            _fp2_fly_cast_whisker(
                self, e, pos, glm.vec3(0, 0, -1),
                min(float(self.p("fly_speed")), 3.0) * dt
                + self._body_r + 0.25)
            if want.y > pos.y and _fp2_fly_whisk_hit(self, 3, pos) is not None:
                want.y = pos.y
            elif want.y < pos.y \
                    and _fp2_fly_whisk_hit(self, 4, pos) is not None:
                want.y = pos.y
            _write_node_world_position(self.node, want)

    def _fp2_fly_on_tick(self, dt):
        if getattr(self, "_fp2_tick_failed", False):
            return _FP2_PREV_FLY_ON_TICK(self, dt)
        try:
            _fp2_fly_on_tick_impl(self, dt)
        except Exception as ex:
            # Log once, then permanently fall back to the stock AI.
            self._fp2_tick_failed = True
            _fp2_log_once("fly_tick",
                          f"[FINALPATCH2] fly AI fell back to stock: {ex}")
            return _FP2_PREV_FLY_ON_TICK(self, dt)

    EnemyFlyShooter.on_tick = _fp2_fly_on_tick
# [FINALPATCH2:gameplay END]