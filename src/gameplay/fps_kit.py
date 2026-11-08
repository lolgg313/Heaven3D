# @heaven3d-part gameplay/fps_kit.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# #############################################################################
# SECTION 14D —  FPS GAMEPLAY KIT  (ready-made Blueprints for shooter games)
# #############################################################################
#
#  Everything needed to build a complete FPS with zero engine edits:
#      FPSController   pointer-lock mouse-look player: WASD, sprint, jump
#                      (coyote time + input buffer), gravity, head-bob,
#                      footsteps, health, damage vignette, respawn
#      Weapon          hitscan gun: auto/semi fire, spread + bloom, ammo,
#                      reload, recoil, muzzle flash, crosshair, physics
#                      impulse on hit bodies
#      Projectile      grenade/rocket: arced flight, blast damage, flash
#      Health          generic destructible: damage, death (hide/destroy/
#                      explode), optional respawn — props, barrels, enemies
#      EnemyAI         patrol / chase / attack (melee or hitscan) with
#                      gravity + capsule collision, growls
#      Pickup          trigger pickup: health / ammo / custom event,
#                      spin + bob, respawn timer
#      Spawner         clones a template node (waves of enemies)
#      MovingPlatform  kinematic ping-pong platform
#      TriggerZone     box trigger that broadcasts game events
#
#  Attach them in the editor (or from code) and press Play.

class _GameBlueprint(Blueprint):
    """Base for the built-in gameplay blueprints: class-level DEFAULTS +
    a fallback getter so older saves never KeyError."""
    DEFAULTS: Dict[str, Any] = {}

    def __init__(self):
        super().__init__()
        merged = dict(self.DEFAULTS)
        merged.update(self.properties)
        self.properties = merged

    def p(self, key: str, default: Any = None) -> Any:
        if key in self.properties:
            return self.properties[key]
        if key in self.DEFAULTS:
            return self.DEFAULTS[key]
        return default

    @staticmethod
    def _is_or_descendant(node: "SceneNode", ancestor: "SceneNode") -> bool:
        n = node
        while n is not None:
            if n is ancestor:
                return True
            n = n.parent
        return False

    def _player_bp(self) -> Optional["FPSController"]:
        return self.engine.first_blueprint("FPSController")

    def _player_root(self, other: "SceneNode") -> Optional["SceneNode"]:
        """Climb `other`'s ancestor chain looking for the node that owns
        the FPSController (works whether the trigger was touched by the
        player capsule itself or a child collider)."""
        pb = self._player_bp()
        if pb is None or pb.node is None:
            return None
        return pb.node if self._is_or_descendant(other, pb.node) else None


@blueprint
class FPSController(_GameBlueprint):
    """Complete first-person player.  Attach to an empty node placed where
    the player should spawn; a capsule collider is created automatically.

    Controls: WASD move · Shift sprint · Space jump · mouse look (pointer
    lock — ESC releases, click re-captures)."""
    DEFAULTS = {
        "move_speed": 6.0,
        "sprint_multiplier": 1.65,
        "jump_height": 1.15,
        "gravity": 22.0,
        "mouse_sensitivity": 0.13,
        "capsule_radius": 0.35,
        "capsule_height": 1.8,
        "eye_height": 1.62,
        "max_health": 100.0,
        "respawn_on_death": True,
        "respawn_delay": 2.5,
        "head_bob": True,
        "footstep_sound": "footstep",
        "show_hud": True,
    }

    def on_begin_play(self):
        e = self.engine
        if self.node.collider is None:
            self.node.collider = CapsuleCollider(
                radius=float(self.p("capsule_radius")),
                height=float(self.p("capsule_height")))
        e.physics.add_node(self.node)
        self.cc = CharacterController(
            e.physics, self.node,
            radius=float(self.p("capsule_radius")),
            height=float(self.p("capsule_height")),
            slope_limit_deg=50.0, step_offset=0.45)
        self.yaw = float(self.node.rotation_euler.y)
        self.pitch = float(self.node.rotation_euler.x)
        self.max_hp = float(self.p("max_health"))
        self.hp = self.max_hp
        self._vy = 0.0
        self._coyote = 0.0
        self._jump_buffer = 0.0
        self._step_dist = 0.0
        self._bob_phase = 0.0
        self._bob_x = 0.0
        self._bob_y = 0.0
        self._vignette = 0.0
        self._dead = False
        self._death_timer = 0.0
        self._fall_speed = 0.0
        self._was_grounded = False
        self._spawn = self.node.world_position()
        # ---- spawn ground-snap (never start inside / under the floor) ----
        # Raycast straight down from a little above the spawn: if solid
        # ground exists below, the capsule's feet are planted exactly on it.
        # This kills the classic "player fell under the map on frame 1" bug
        # that plagues open-source engines, even when the designer placed
        # the spawn point slightly inside geometry.
        try:
            h = float(self.p("capsule_height"))
            probe = self._spawn + glm.vec3(0.0, h * 0.5 + 0.25, 0.0)
            hit = e.physics.raycast(probe, glm.vec3(0.0, -1.0, 0.0), 500.0,
                                    ignore={id(self.node)})
            if hit is not None and hit.normal.y > 0.5:
                snapped = glm.vec3(self._spawn.x,
                                   hit.point.y + h * 0.5 + 0.03,
                                   self._spawn.z)
                _write_node_world_position(self.node, snapped)
                self._spawn = snapped
        except Exception:
            pass
        e.capture_mouse = True
        e.camera_locked = True

    def on_end_play(self):
        e = self.engine
        e.capture_mouse = False
        e.camera_locked = False

    # ---------------------------------------------------------------- tick
    def on_tick(self, dt: float):
        e = self.engine
        if self._dead:
            self._death_timer -= dt
            if e.hud is not None:
                e.hud.damage_vignette(0.8)
                e.hud.center_text(0.42, "YOU DIED", size=44,
                                  color=(0.9, 0.1, 0.1, 1.0))
                if self.p("respawn_on_death"):
                    e.hud.center_text(0.52,
                                      f"respawning in "
                                      f"{max(0.0, self._death_timer):.1f}s",
                                      size=20, color=(1, 1, 1, 0.9))
            if self.p("respawn_on_death") and self._death_timer <= 0.0:
                self._respawn()
            return

        # ---- mouse look ----
        dx, dy = e.input.mouse_delta
        if abs(dx) < 300 and abs(dy) < 300:      # spike guard
            sens = float(self.p("mouse_sensitivity"))
            self.yaw -= dx * sens
            self.pitch = _clamp(self.pitch - dy * sens, -89.0, 89.0)
        cam = e.camera
        cam.yaw = self.yaw
        cam.pitch = self.pitch

        # ---- move intent (horizontal, camera-relative) ----
        yr = math.radians(self.yaw)
        fwd = glm.vec3(-math.sin(yr), 0.0, -math.cos(yr))
        right = glm.vec3(-fwd.z, 0.0, fwd.x)
        wish = glm.vec3(0.0)
        if e.input.key('w'):
            wish += fwd
        if e.input.key('s'):
            wish -= fwd
        if e.input.key('d'):
            wish += right
        if e.input.key('a'):
            wish -= right
        if glm.length(wish) > 0.0:
            wish = glm.normalize(wish)
        speed = float(self.p("move_speed")) * \
            (float(self.p("sprint_multiplier"))
             if e.input.key('left shift') else 1.0)

        # ---- jump / gravity ----
        g = float(self.p("gravity"))
        grounded = self.cc.is_grounded
        self._coyote = 0.12 if grounded else self._coyote - dt
        if e.input.was_key_pressed('space'):
            self._jump_buffer = 0.12
        else:
            self._jump_buffer -= dt
        if self._jump_buffer > 0.0 and self._coyote > 0.0:
            self._vy = math.sqrt(2.0 * g * float(self.p("jump_height")))
            self._coyote = 0.0
            self._jump_buffer = 0.0
            e.audio.play_at("jump", self.node.world_position(), 0.4)
        if grounded and self._vy < 0.0:
            self._vy = -2.0
        self._vy -= g * dt
        # Terminal velocity: on very slow machines a long frame could
        # otherwise build up enough fall speed to tunnel through thin
        # floors before the substepped mover can catch it.
        self._vy = max(self._vy, -55.0)

        disp = glm.vec3(wish.x * speed, self._vy, wish.z * speed) * dt
        moved = self.cc.move(disp)
        if self.cc.hit_ceiling:
            self._vy = min(self._vy, 0.0)
        grounded_after = self.cc.is_grounded

        # ---- absolute never-fall-through safety net ----------------------
        # Whatever happens — NaN from a physics edge case, a hole in the
        # level, a huge frame hitch on a weak PC — the player can NEVER be
        # left falling under the world: the kill floor teleports them back
        # to their (ground-snapped) spawn instead.
        _pos = self.node.world_position()
        if (math.isnan(_pos.x) or math.isnan(_pos.y) or math.isnan(_pos.z)
                or _pos.y < -400.0):
            self._respawn()
            return

        # ---- land / footsteps / head-bob ----
        h_speed = glm.length(glm.vec3(moved.x, 0.0, moved.z)) / max(dt, 1e-6)
        if grounded_after and not self._was_grounded and self._fall_speed < -8.0:
            e.audio.play_at("land", self.node.world_position(), 0.5)
        self._fall_speed = self._vy
        self._was_grounded = grounded_after
        bob_target_x = bob_target_y = 0.0
        if grounded_after and h_speed > 0.6:
            self._step_dist += glm.length(glm.vec3(moved.x, 0.0, moved.z))
            stride = 2.3 * (speed / float(self.p("move_speed")))
            if self._step_dist >= stride:
                self._step_dist = 0.0
                e.audio.play_at(self.p("footstep_sound"),
                                self.node.world_position(), 0.3)
            self._bob_phase += h_speed * dt * 1.85
            if self.p("head_bob"):
                bob_target_x = math.sin(self._bob_phase) * 0.028
                bob_target_y = abs(math.sin(self._bob_phase)) * 0.038
        self._bob_x += (bob_target_x - self._bob_x) * min(1.0, dt * 10.0)
        self._bob_y += (bob_target_y - self._bob_y) * min(1.0, dt * 10.0)

        # ---- camera + audio listener ----
        eye_off = float(self.p("eye_height")) - float(self.p("capsule_height")) * 0.5
        eye = self.cc.position + glm.vec3(self._bob_x,
                                          eye_off + self._bob_y, 0.0)
        cam.position = eye
        e.audio.set_listener(eye, cam.forward(), cam.right())

        # ---- HUD ----
        if e.hud is not None and self.p("show_hud"):
            if self._vignette > 0.0:
                e.hud.damage_vignette(self._vignette)
                self._vignette = max(0.0, self._vignette - dt * 1.4)
            frac = _clamp(self.hp / max(self.max_hp, 1e-6), 0.0, 1.0)
            fg = (0.25 + (1.0 - frac) * 0.7, 0.85 * frac + 0.1, 0.15, 1.0)
            e.hud.bar(0.07, 0.925, 0.22, 0.022, frac, fg=fg)
            e.hud.text(0.07, 0.895, f"HP {int(round(self.hp))}", size=18)

    # ---------------------------------------------------------------- damage
    def on_message(self, event: str, data: Any):
        if event == "damage" and not self._dead:
            amt = float(data.get("amount", 0)) if isinstance(data, dict) \
                else float(data or 0)
            self.hp -= amt
            self._vignette = 1.0
            self.engine.audio.play("hurt", 0.45)
            if self.hp <= 0.0:
                self.hp = 0.0
                self._dead = True
                self._death_timer = float(self.p("respawn_delay"))
                self.engine.send_message("player_died", {"node": self.node})
        elif event == "heal":
            amt = float(data.get("amount", 0)) if isinstance(data, dict) \
                else float(data or 0)
            if self.hp < self.max_hp:
                self.engine.audio.play("heal", 0.5)
            self.hp = min(self.max_hp, self.hp + amt)

    def _respawn(self):
        self.hp = self.max_hp
        self._dead = False
        self._vy = 0.0
        self._vignette = 0.4
        _write_node_world_position(self.node, self._spawn)
        self.cc.is_grounded = False


@blueprint
class Weapon(_GameBlueprint):
    """Hitscan gun with ammo, reload, spread/bloom, recoil and muzzle
    flash.  Attach to the same node as the FPSController (or a child of
    it) — it always aims through the gameplay camera."""
    DEFAULTS = {
        "damage": 25.0,
        "fire_rate": 8.0,
        "spread_deg": 0.6,
        "range": 150.0,
        "magazine": 30,
        "reserve_ammo": 120,
        "reload_time": 1.4,
        "auto": True,
        "shoot_sound": "shoot",
        "hit_sound": "hit",
        "reload_sound": "reload",
        "empty_sound": "click",
        "camera_kick": 0.5,
        "impact_impulse": 6.0,
        "muzzle_flash": True,
        "show_crosshair": True,
        "show_hud": True,
    }

    def on_begin_play(self):
        self.ammo = int(self.p("magazine"))
        self.reserve = int(self.p("reserve_ammo"))
        self._cool = 0.0
        self._reloading = 0.0
        self._bloom = 0.0
        root = self.node
        while root.parent is not None and root.parent.parent is not None:
            root = root.parent
        self._ignore = {id(n) for n in root.traverse()}

    def on_tick(self, dt: float):
        e = self.engine
        self._cool -= dt
        self._bloom = max(0.0, self._bloom - dt * 5.0)
        if self._reloading > 0.0:
            self._reloading -= dt
            if self._reloading <= 0.0:
                need = int(self.p("magazine")) - self.ammo
                take = min(need, self.reserve)
                self.ammo += take
                self.reserve -= take

        fire = e.input.lmb if self.p("auto") else e.input.lmb_just_pressed
        if fire and self._reloading <= 0.0 and self._cool <= 0.0:
            if self.ammo > 0:
                self._shoot()
            elif e.input.lmb_just_pressed:
                e.audio.play(self.p("empty_sound"), 0.5)
        if e.input.was_key_pressed('r') and self._reloading <= 0.0 \
                and self.ammo < int(self.p("magazine")) and self.reserve > 0:
            self._reloading = float(self.p("reload_time"))
            e.audio.play(self.p("reload_sound"), 0.6)

        # ---- HUD ----
        if e.hud is not None:
            if self.p("show_crosshair"):
                e.hud.crosshair(spread=self._bloom * 0.008)
            if self.p("show_hud"):
                label = f"{self.ammo} / {self.reserve}"
                e.hud.text(0.86, 0.895, label, size=22)
                if self._reloading > 0.0:
                    frac = 1.0 - self._reloading / max(
                        float(self.p("reload_time")), 1e-6)
                    e.hud.bar(0.82, 0.925, 0.15, 0.016, frac,
                              fg=(0.9, 0.75, 0.2, 1.0))
                    e.hud.text(0.845, 0.86, "RELOADING", size=14,
                               color=(0.95, 0.8, 0.3, 1.0))

    def _shoot(self):
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

        hit = e.physics.raycast(cam.position, d, float(self.p("range")),
                                ignore=self._ignore)
        if hit is not None:
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

    def on_message(self, event: str, data: Any):
        if event == "add_ammo":
            amt = int(data.get("amount", 0)) if isinstance(data, dict) \
                else int(data or 0)
            self.reserve += amt


@blueprint
class Projectile(_GameBlueprint):
    """Arced explosive (grenade / rocket).  The node's +forward at spawn
    is the launch direction.  Raycasts every frame so it never tunnels."""
    DEFAULTS = {
        "speed": 26.0,
        "gravity": 8.0,
        "damage": 45.0,
        "blast_radius": 4.0,
        "lifetime": 6.0,
        "boom_sound": "explosion",
        "owner_name": "",
    }

    def on_begin_play(self):
        fwd = Scene.forward_from_rotation(self.node.rotation_euler)
        self._vel = fwd * float(self.p("speed"))
        self._age = 0.0
        self._ignore = {id(self.node)}
        owner_name = str(self.p("owner_name") or "")
        if owner_name:
            owner = self.engine.find_node(owner_name)
            if owner is not None:
                self._ignore |= {id(n) for n in owner.traverse()}

    def on_tick(self, dt: float):
        e = self.engine
        self._age += dt
        pos = self.node.world_position()
        if self._age >= float(self.p("lifetime")):
            self._explode(pos)
            return
        self._vel.y -= float(self.p("gravity")) * dt
        step = self._vel * dt
        L = glm.length(step)
        if L > 1e-6:
            hit = e.physics.raycast(pos, step / L, L + 0.05,
                                    ignore=self._ignore)
            if hit is not None:
                self._explode(hit.point)
                return
        _write_node_world_position(self.node, pos + step)

    def _explode(self, point: glm.vec3):
        e = self.engine
        radius = float(self.p("blast_radius"))
        seen = set()
        for entry in e.physics.overlap_sphere(point, radius,
                                              include_triggers=False):
            n = entry.node
            if n is self.node or id(n) in seen:
                continue
            seen.add(id(n))
            d = glm.length(n.world_position() - point)
            dmg = float(self.p("damage")) * _clamp(
                1.0 - d / max(radius, 1e-3), 0.0, 1.0)
            if dmg > 0.0:
                e.message_ancestors(n, "damage",
                                    {"amount": dmg, "point": point,
                                     "source": "projectile"})
            rb = getattr(n, "rigidbody", None)
            if rb is not None and rb.body_type == "dynamic":
                away = _safe_normalize(n.world_position() - point
                                       + glm.vec3(0, 0.4, 0))
                rb.add_impulse(away * dmg * 0.25)
        e.audio.play_at(self.p("boom_sound"), point, 0.9)
        e.spawn_flash(point, color=(1.0, 0.55, 0.20), intensity=70.0,
                      range=14.0, ttl=0.13)
        e.destroy_node(self.node)


@blueprint
class Health(_GameBlueprint):
    """Generic destructible object health.  Listens for "damage" / "heal"
    messages (sent by Weapon / Projectile / Pickup).  On death it can
    hide, destroy, or explode the node, and optionally respawn it."""
    DEFAULTS = {
        "max_health": 100.0,
        "destroy_on_death": False,
        "hide_on_death": True,
        "respawn": False,
        "respawn_delay": 4.0,
        "death_sound": "hurt",
        "explode_on_death": False,
        "blast_damage": 40.0,
        "blast_radius": 3.5,
    }

    def on_begin_play(self):
        self.max_hp = float(self.p("max_health"))
        self.hp = self.max_hp
        self._dead = False
        self._spawn = self.node.world_position()
        self._spawn_rot = glm.vec3(self.node.rotation_euler)

    def on_message(self, event: str, data: Any):
        if event == "damage" and not self._dead:
            amt = float(data.get("amount", 0)) if isinstance(data, dict) \
                else float(data or 0)
            self.hp -= amt
            if self.hp <= 0.0:
                self.hp = 0.0
                self._die()
        elif event == "heal":
            amt = float(data.get("amount", 0)) if isinstance(data, dict) \
                else float(data or 0)
            self.hp = min(self.max_hp, self.hp + amt)

    def _die(self):
        e = self.engine
        self._dead = True
        e.send_message("died", {"node": self.node})
        pos = self.node.world_position()
        if self.p("explode_on_death"):
            radius = float(self.p("blast_radius"))
            for entry in e.physics.overlap_sphere(pos, radius,
                                                  include_triggers=False):
                n = entry.node
                if n is self.node:
                    continue
                d = glm.length(n.world_position() - pos)
                dmg = float(self.p("blast_damage")) * _clamp(
                    1.0 - d / max(radius, 1e-3), 0.0, 1.0)
                if dmg > 0.0:
                    e.message_ancestors(n, "damage",
                                        {"amount": dmg, "point": pos,
                                         "source": "explosion"})
            e.audio.play_at("explosion", pos, 0.9)
            e.spawn_flash(pos, color=(1.0, 0.5, 0.15), intensity=80.0,
                          range=15.0, ttl=0.15)
        else:
            e.audio.play_at(self.p("death_sound"), pos, 0.6)
        if self.p("destroy_on_death"):
            e.destroy_node(self.node)
            return
        if self.p("hide_on_death"):
            self.node.visible = False
            e.physics.remove_node(self.node)
        if self.p("respawn"):
            e.schedule(float(self.p("respawn_delay")), self._respawn)

    def _respawn(self):
        if self.node is None:
            return
        self.hp = self.max_hp
        self._dead = False
        self.node.visible = True
        _write_node_world_position(self.node, self._spawn)
        self.node.rotation_euler = glm.vec3(self._spawn_rot)
        self.engine.physics.add_node_recursive(self.node)


@blueprint
class EnemyAI(_GameBlueprint):
    """Simple melee/hitscan enemy: patrols near home, chases the player
    inside detect_range, attacks inside attack_range.  Uses a capsule
    CharacterController (gravity + walls handled).  Pair with a Health
    blueprint for damage/death; without one it tracks its own HP."""
    DEFAULTS = {
        "move_speed": 3.4,
        "detect_range": 26.0,
        "attack_range": 2.1,
        "attack_damage": 12.0,
        "attack_cooldown": 1.2,
        "attack_style": "melee",        # "melee" | "hitscan"
        "hitscan_range": 22.0,
        "growl_sound": "enemy",
        "patrol_radius": 7.0,
        "capsule_radius": 0.4,
        "capsule_height": 1.7,
        "require_los": False,
        "max_health": 60.0,             # fallback when no Health blueprint
        "gravity": 22.0,
    }

    def on_begin_play(self):
        e = self.engine
        if self.node.collider is None:
            self.node.collider = CapsuleCollider(
                radius=float(self.p("capsule_radius")),
                height=float(self.p("capsule_height")))
        e.physics.add_node(self.node)
        self.cc = CharacterController(
            e.physics, self.node,
            radius=float(self.p("capsule_radius")),
            height=float(self.p("capsule_height")),
            slope_limit_deg=50.0, step_offset=0.45)
        self._vy = 0.0
        self._cool = 0.0
        self._alive = True
        self._home = self.node.world_position()
        self._patrol_target: Optional[glm.vec3] = None
        self._retarget = 0.0
        self._has_health_bp = any(type(b).__name__ == "Health"
                                  for b in self.node.blueprints)
        self.hp = float(self.p("max_health"))
        self._ignore = {id(n) for n in self.node.traverse()}

    def _face(self, dir_h: glm.vec3):
        self.node.rotation_euler.y = math.degrees(
            math.atan2(-dir_h.x, -dir_h.z))

    def _can_see(self, eye: glm.vec3, target: glm.vec3) -> bool:
        d = target - eye
        L = glm.length(d)
        if L < 1e-6:
            return True
        hit = self.engine.physics.raycast(eye, d / L, L,
                                          ignore=self._ignore)
        return hit is None

    def on_tick(self, dt: float):
        if not self._alive:
            return
        e = self.engine
        self._cool -= dt
        pos = self.node.world_position()
        move_dir = glm.vec3(0.0)
        speed = 0.0

        pb = self._player_bp()
        player = pb.node if (pb is not None and not pb._dead) else None
        if player is not None:
            ppos = player.world_position()
            to_p = ppos - pos
            d_h = glm.length(glm.vec3(to_p.x, 0.0, to_p.z))
            eye = pos + glm.vec3(0, float(self.p("capsule_height")) * 0.42, 0)
            if d_h <= float(self.p("attack_range")) and self._cool <= 0.0:
                self._attack(pb, eye, ppos, to_p)
            elif d_h <= float(self.p("detect_range")):
                if (not self.p("require_los")) or self._can_see(eye, ppos):
                    dir_h = _safe_normalize(glm.vec3(to_p.x, 0.0, to_p.z),
                                            glm.vec3(0, 0, -1))
                    move_dir = dir_h
                    speed = float(self.p("move_speed"))
                    self._face(dir_h)

        if speed == 0.0 and float(self.p("patrol_radius")) > 0.0:
            self._retarget -= dt
            if self._patrol_target is None or self._retarget <= 0.0:
                ang = random.uniform(0.0, 2.0 * math.pi)
                rad = random.uniform(1.0, float(self.p("patrol_radius")))
                self._patrol_target = self._home + glm.vec3(
                    math.cos(ang) * rad, 0.0, math.sin(ang) * rad)
                self._retarget = random.uniform(2.0, 5.0)
            to_t = self._patrol_target - pos
            d_t = glm.length(glm.vec3(to_t.x, 0.0, to_t.z))
            if d_t > 0.5:
                dir_t = _safe_normalize(glm.vec3(to_t.x, 0.0, to_t.z),
                                        glm.vec3(0, 0, -1))
                move_dir = dir_t
                speed = float(self.p("move_speed")) * 0.45
                self._face(dir_t)
            else:
                self._patrol_target = None

        # gravity + collide-and-slide
        if self.cc.is_grounded and self._vy < 0.0:
            self._vy = -2.0
        self._vy -= float(self.p("gravity")) * dt
        self.cc.move(glm.vec3(move_dir.x * speed, self._vy,
                              move_dir.z * speed) * dt)

    def _attack(self, pb, eye: glm.vec3, ppos: glm.vec3, to_p: glm.vec3):
        e = self.engine
        self._cool = float(self.p("attack_cooldown"))
        e.audio.play_at(self.p("growl_sound"), self.node.world_position(),
                        0.75)
        dir_h = _safe_normalize(glm.vec3(to_p.x, 0.0, to_p.z),
                                glm.vec3(0, 0, -1))
        self._face(dir_h)
        if str(self.p("attack_style")) == "hitscan":
            chest = ppos + glm.vec3(0, -0.25, 0)
            d = chest - eye
            L = glm.length(d)
            if L > float(self.p("hitscan_range")):
                return
            hit = e.physics.raycast(eye, d / L, L, ignore=self._ignore)
            if hit is not None and self._is_or_descendant(hit.node,
                                                          pb.node):
                e.message_ancestors(hit.node, "damage",
                                    {"amount": float(self.p("attack_damage")),
                                     "point": hit.point, "source": "enemy"})
        else:
            e.message_ancestors(pb.node, "damage",
                                {"amount": float(self.p("attack_damage")),
                                 "point": ppos, "source": "enemy"})

    def on_message(self, event: str, data: Any):
        if event == "died":
            if isinstance(data, dict) and data.get("node") is self.node:
                self._alive = False
            return
        # Fallback health when no Health blueprint is present
        if event == "damage" and not self._has_health_bp and self._alive:
            amt = float(data.get("amount", 0)) if isinstance(data, dict) \
                else float(data or 0)
            self.hp -= amt
            if self.hp <= 0.0:
                self._alive = False
                self.node.visible = False
                self.engine.physics.remove_node(self.node)
                self.engine.audio.play_at("hurt",
                                          self.node.world_position(), 0.6)
                self.engine.send_message("died", {"node": self.node})


@blueprint
class Pickup(_GameBlueprint):
    """Trigger pickup (health / ammo / custom event).  A trigger sphere is
    created automatically.  Touches the player → applies its effect,
    plays a sound, hides, and respawns after respawn_time (0 = never)."""
    DEFAULTS = {
        "kind": "health",               # "health" | "ammo" | "event"
        "amount": 30.0,
        "event_name": "picked_up",
        "respawn_time": 15.0,
        "sound": "pickup",
        "spin": True,
        "bob": True,
        "trigger_radius": 1.1,
    }

    def on_begin_play(self):
        if self.node.collider is None:
            self.node.collider = SphereCollider(
                radius=float(self.p("trigger_radius")), is_trigger=True)
        else:
            self.node.collider.is_trigger = True
        self.engine.physics.add_node(self.node)
        self._base_pos = glm.vec3(self.node.position)
        self._collected = False

    def on_tick(self, dt: float):
        if self._collected:
            return
        if self.p("spin"):
            self.node.rotation_euler.y = (
                self.node.rotation_euler.y + 110.0 * dt) % 360.0
        if self.p("bob"):
            self.node.position.y = self._base_pos.y + math.sin(
                self.engine.time * 2.2) * 0.12

    def on_trigger_enter(self, other: "SceneNode"):
        if self._collected:
            return
        player = self._player_root(other)
        if player is None:
            return
        self._collected = True
        kind = str(self.p("kind"))
        amt = self.p("amount")
        if kind == "health":
            self.engine.message_node(player, "heal", {"amount": amt})
        elif kind == "ammo":
            root = player
            self.engine.message_subtree(root, "add_ammo", {"amount": amt})
        else:
            self.engine.send_message(str(self.p("event_name")),
                                     {"node": self.node, "other": player})
        self.engine.audio.play_at(str(self.p("sound")),
                                  self.node.world_position(), 0.7)
        self.node.visible = False
        self.engine.physics.remove_node(self.node)
        rt = float(self.p("respawn_time"))
        if rt > 0.0:
            self.engine.schedule(rt, self._respawn)

    def _respawn(self):
        if self.node is None:
            return
        self._collected = False
        self.node.visible = True
        self.engine.physics.add_node(self.node)


@blueprint
class EnemyBullet(_GameBlueprint):
    """Small glowing sphere fired by an EnemyFlyShooter.  Flies straight,
    raycasts every frame so it never tunnels (even on very slow machines),
    hurts the player on contact and dies on any impact."""
    DEFAULTS = {
        "speed": 16.0,
        "damage": 10.0,
        "lifetime": 5.0,
        "owner_name": "",
    }

    def on_begin_play(self):
        fwd = Scene.forward_from_rotation(self.node.rotation_euler)
        self._vel = fwd * float(self.p("speed"))
        self._age = 0.0
        self._ignore = {id(self.node)}
        owner_name = str(self.p("owner_name") or "")
        if owner_name:
            owner = self.engine.find_node(owner_name)
            if owner is not None:
                self._ignore |= {id(n) for n in owner.traverse()}
        # Enemy bullets must never shoot the player-spawn marker's own
        # gameplay siblings... only the world + the player are targets.

    def on_tick(self, dt: float):
        e = self.engine
        self._age += dt
        pos = self.node.world_position()
        if self._age >= float(self.p("lifetime")):
            e.destroy_node(self.node)
            return
        step = self._vel * dt
        L = glm.length(step)
        if L > 1e-6:
            hit = e.physics.raycast(pos, step / L, L + 0.02,
                                    ignore=self._ignore)
            if hit is not None:
                pb = self._player_bp()
                if pb is not None and not pb._dead and \
                        self._is_or_descendant(hit.node, pb.node):
                    e.message_ancestors(hit.node, "damage",
                                        {"amount": float(self.p("damage")),
                                         "point": hit.point,
                                         "source": "enemy_bullet"})
                e.spawn_flash(hit.point, color=(1.0, 0.35, 0.25),
                              intensity=12.0, range=4.0, ttl=0.06)
                e.audio.play_at("hit", hit.point, 0.25)
                e.destroy_node(self.node)
                return
        _write_node_world_position(self.node, pos + step)


@blueprint
class EnemyFlyShooter(_GameBlueprint):
    """Flying drone enemy driven by an Enemy Spawn point.  A soccer-ball
    sized sphere (custom meshes supported — the spawn point's own mesh IS
    the enemy body) that hovers, hunts the player, turns to track them at
    `turn_speed` and fires bullets at `shots_per_sec`."""
    DEFAULTS = {
        "max_health": 100.0,          # Inspector: Health Value
        "bullet_damage": 10.0,        # Inspector: Bullet Value
        "fly_speed": 4.0,             # Inspector: Fly Speed
        "turn_speed": 240.0,          # Inspector: Camera Rotation Speed (deg/s)
        "shots_per_sec": 1.5,         # Inspector: Shooting Speed (bullets/s)
        "detect_range": 60.0,
        "attack_range": 30.0,
        "keep_distance": 5.0,         # hover standoff from the player
        "collider_radius": 0.11,      # soccer-ball radius (FIFA ≈ 22 cm Ø)
        "bullet_speed": 16.0,
    }

    def on_begin_play(self):
        e = self.engine
        if self.node.collider is None:
            # Soccer-ball solid sphere (scaled with the node's world scale
            # so a custom mesh keeps a matching hit-volume).  Only a
            # fallback: spawn activation normally pre-fits a box collider
            # matching the CURRENT mesh exactly.
            ws = _world_scale(self.node)
            r = float(self.p("collider_radius")) * max(ws.x, ws.y, ws.z)
            self.node.collider = SphereCollider(radius=max(0.05, r))
        e.physics.add_node(self.node)
        # Effective body radius (for wall probes + muzzle offset), derived
        # from whatever collider the mesh produced.
        col = self.node.collider
        ws = _world_scale(self.node)
        if isinstance(col, BoxCollider):
            self._body_r = 0.5 * max(col.size.x * ws.x,
                                     col.size.y * ws.y,
                                     col.size.z * ws.z)
        elif isinstance(col, SphereCollider):
            self._body_r = col.radius * max(ws.x, ws.y, ws.z)
        else:
            self._body_r = float(self.p("collider_radius")) \
                * max(ws.x, ws.y, ws.z)
        self._body_r = max(0.05, self._body_r)
        self.hp = float(self.p("max_health"))
        self._alive = True
        self._cool = 0.0
        self._bob_t = random.uniform(0.0, math.tau)
        self._home = self.node.world_position()
        self._ignore = {id(n) for n in self.node.traverse()}
        self.yaw = float(self.node.rotation_euler.y)

    # ------------------------------------------------------------- helpers
    def _face_towards(self, to_target: glm.vec3, dt: float):
        """Rotate yaw towards the target, capped at turn_speed deg/s —
        the Inspector's 'Camera Rotation Speed'."""
        dh = glm.vec3(to_target.x, 0.0, to_target.z)
        if glm.length(dh) < 1e-6:
            return
        want = math.degrees(math.atan2(-dh.x, -dh.z))
        cur = self.yaw
        diff = (want - cur + 540.0) % 360.0 - 180.0
        step = _clamp(diff,
                      -float(self.p("turn_speed")) * dt,
                      float(self.p("turn_speed")) * dt)
        self.yaw = cur + step
        self.node.rotation_euler.y = self.yaw

    def _los(self, eye: glm.vec3, target: glm.vec3) -> bool:
        """Clear line of sight to the target — only LEVEL GEOMETRY blocks
        the shot.  The player's own capsule must be ignored (the aim point
        sits inside it, so counting it would make every shot 'blocked')."""
        d = target - eye
        L = glm.length(d)
        if L < 1e-6:
            return True
        ignore = self._ignore
        pb = self._player_bp()
        if pb is not None and pb.node is not None:
            ignore = ignore | {id(n) for n in pb.node.traverse()}
        return self.engine.physics.raycast(eye, d / L, L,
                                           ignore=ignore) is None

    def _fire(self, muzzle: glm.vec3, target: glm.vec3):
        e = self.engine
        d = target - muzzle
        L = glm.length(d)
        if L < 1e-4:
            return
        d = d / L
        yaw = math.degrees(math.atan2(-d.x, -d.z))
        pitch = math.degrees(math.asin(_clamp(d.y, -1.0, 1.0)))
        b = SceneNode("_runtime_bullet", parent=e.scene.root)
        b.position = glm.vec3(muzzle)
        b.rotation_euler = glm.vec3(pitch, yaw, 0.0)
        # Shared tiny emissive sphere (built once per engine — cheap).
        if getattr(e, "_enemy_bullet_mesh", None) is None:
            e._enemy_bullet_mesh = Mesh.make_sphere(e.ctx, 0.045, 12, 8)
            e._enemy_bullet_mat = Material(
                name="enemy_bullet_mat",
                base_color=(1.0, 0.25, 0.15, 1.0),
                emissive=(1.0, 0.2, 0.1),
                emissive_intensity=6.0, roughness=0.4)
        b.mesh = e._enemy_bullet_mesh
        b.material = e._enemy_bullet_mat
        bp = e.attach_blueprint(b, EnemyBullet, {
            "speed": float(self.p("bullet_speed")),
            "damage": float(self.p("bullet_damage")),
            "owner_name": self.node.name,
        })
        if bp is not None:
            bp._runtime_spawn = True
        e._spawn_runtime_nodes.append(b)
        e.audio.play_at("shoot", muzzle, 0.30)

    # ---------------------------------------------------------------- tick
    def on_tick(self, dt: float):
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
                if d_h > float(self.p("keep_distance")) + 0.5:
                    dir_h = _safe_normalize(dh, glm.vec3(0, 0, -1))
                    # Wall probe: never fly into geometry.
                    probe = e.physics.raycast(
                        pos, dir_h,
                        float(self.p("fly_speed")) * dt + self._body_r + 0.15,
                        ignore=self._ignore)
                    if probe is None:
                        move += dir_h * float(self.p("fly_speed"))
                # Vertical: hover at the player's chest height + bobbing.
                want_y = ppos.y + 0.9 + math.sin(self._bob_t) * 0.25
                dy = _clamp(want_y - pos.y, -1.0, 1.0)
                move.y = dy * min(float(self.p("fly_speed")), 3.0)
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
            # No player: gentle hover in place over the spawn point.
            self._bob_t += dt * 1.4
            want = self._home + glm.vec3(0.0, math.sin(self._bob_t) * 0.15, 0.0)
            _write_node_world_position(self.node, want)

    # ------------------------------------------------------------- damage
    def on_message(self, event: str, data: Any):
        if event != "damage" or not self._alive:
            return
        amt = float(data.get("amount", 0)) if isinstance(data, dict) \
            else float(data or 0)
        self.hp -= amt
        if self.hp <= 0.0:
            self._alive = False
            e = self.engine
            pos = self.node.world_position()
            e.audio.play_at("explosion", pos, 0.7)
            e.spawn_flash(pos, color=(1.0, 0.5, 0.15), intensity=60.0,
                          range=10.0, ttl=0.15)
            self.node.visible = False
            e.physics.remove_node(self.node)
            e.send_message("died", {"node": self.node})


@blueprint
class LevelEndTrigger(_GameBlueprint):
    """Invisible 1x1x1 level-end volume (a Trigger Point).  The moment the
    player crosses it the game stops completely and the configured text
    appears in the middle of the screen (custom text, color, font size)."""
    DEFAULTS = {
        "text": "LEVEL COMPLETE",
        "color": [1.0, 0.85, 0.2],
        "font_size": 64,
        "sound": "heal",
    }

    def on_begin_play(self):
        self._fired = False

    def on_trigger_enter(self, other: "SceneNode"):
        if self._fired:
            return
        player = self._player_root(other)
        if player is None:
            return
        self._fired = True
        e = self.engine
        try:
            e.audio.play(str(self.p("sound")), 0.7)
        except Exception:
            pass
        e.show_end_screen(str(self.p("text")), self.p("color"),
                          int(self.p("font_size")))
        e.request_stop_play()


@blueprint
class Spawner(_GameBlueprint):
    """Clones a template node (usually an invisible enemy prefab) at
    intervals until max_alive of its children are alive."""
    DEFAULTS = {
        "template_name": "Enemy",
        "interval": 4.0,
        "max_alive": 5,
        "spawn_radius": 2.0,
        "start_delay": 1.0,
        "sound": "",
    }

    def on_begin_play(self):
        self._timer = float(self.p("start_delay"))
        self._alive: List[SceneNode] = []
        self._warned = False

    @staticmethod
    def _node_alive(n: SceneNode) -> bool:
        while n.parent is not None:
            n = n.parent
        return n.name == "__root__"

    def on_tick(self, dt: float):
        e = self.engine
        self._alive = [n for n in self._alive if self._node_alive(n)]
        self._timer -= dt
        if self._timer > 0.0 or len(self._alive) >= int(self.p("max_alive")):
            return
        self._timer = float(self.p("interval"))
        tmpl = e.find_node(str(self.p("template_name")))
        if tmpl is None or tmpl is self.node:
            if not self._warned:
                self._warned = True
                Log.warn(f"Spawner '{self.node.name}': template "
                         f"'{self.p('template_name')}' not found.")
            return
        ang = random.uniform(0.0, 2.0 * math.pi)
        rad = random.uniform(0.0, float(self.p("spawn_radius")))
        pos = self.node.world_position() + glm.vec3(math.cos(ang) * rad,
                                                    0.5,
                                                    math.sin(ang) * rad)
        clone = e.instantiate(tmpl, position=pos, make_visible=True)
        if clone is not None:
            self._alive.append(clone)
            if str(self.p("sound")):
                e.audio.play_at(str(self.p("sound")), pos, 0.6)

    def on_message(self, event: str, data: Any):
        if event == "died" and isinstance(data, dict):
            n = data.get("node")
            self._alive = [x for x in self._alive if x is not n]


@blueprint
class MovingPlatform(_GameBlueprint):
    """Kinematic ping-pong platform (smooth ease at both ends).  Players
    standing on it are pushed/carried by the contact solver."""
    DEFAULTS = {
        "offset": [0.0, 3.0, 0.0],
        "period": 5.0,
    }

    def on_begin_play(self):
        if self.node.rigidbody is None:
            self.node.rigidbody = RigidBody(body_type="kinematic")
        else:
            self.node.rigidbody.body_type = "kinematic"
        self.engine.physics.add_node(self.node)
        self._start = glm.vec3(self.node.position)

    def on_tick(self, dt: float):
        off = self.p("offset")
        period = max(float(self.p("period")), 0.1)
        phase = 0.5 - 0.5 * math.cos(2.0 * math.pi *
                                     self.engine.time / period)
        self.node.position = self._start + glm.vec3(
            float(off[0]), float(off[1]), float(off[2])) * phase


@blueprint
class TriggerZone(_GameBlueprint):
    """Box trigger volume: broadcasts "<event_name>_enter" /
    "<event_name>_exit" engine messages when the player walks in/out.
    Great for level-end zones, doors, wave triggers."""
    DEFAULTS = {
        "size": [2.0, 2.0, 2.0],
        "event_name": "zone",
        "once": False,
        "player_only": True,
    }

    def on_begin_play(self):
        if self.node.collider is None:
            s = self.p("size")
            self.node.collider = BoxCollider(
                size=(float(s[0]), float(s[1]), float(s[2])),
                is_trigger=True)
        else:
            self.node.collider.is_trigger = True
        self.engine.physics.add_node(self.node)
        self._fired = False

    def on_trigger_enter(self, other: "SceneNode"):
        if self._fired and self.p("once"):
            return
        if self.p("player_only") and self._player_root(other) is None:
            return
        self._fired = True
        self.engine.send_message(str(self.p("event_name")) + "_enter",
                                 {"node": self.node, "other": other})

    def on_trigger_exit(self, other: "SceneNode"):
        if self.p("player_only") and self._player_root(other) is None:
            return
        self.engine.send_message(str(self.p("event_name")) + "_exit",
                                 {"node": self.node, "other": other})

