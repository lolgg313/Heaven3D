"""Car Race Kit - arcade car, checkpoints and a race manager.

Editor (Packages menu):
  Race kit: add car            a drivable car in front of the camera
  Race kit: add checkpoint     a gate; checkpoints are passed in 'order'
                               (order 0 = start / finish line)
  Race kit: add race manager   laps, countdown, timer and HUD
Game controls: W/S or Up/Down = throttle/brake, A/D or Left/Right = steer,
Space = handbrake, R = back to the last checkpoint, Enter = restart race.
"""
import math
import os

H3D = None
CLS = {}


def _clamp(v, a, b):
    return a if v < a else (b if v > b else v)


def _sign(v):
    return 1.0 if v > 0 else (-1.0 if v < 0 else 0.0)


def _ignore_set(node):
    s = {id(node)}
    for c in node.traverse():
        s.add(id(c))
    return s


def _classes():
    m = H3D.module
    Blueprint, blueprint, glm = m.Blueprint, m.blueprint, m.glm

    @blueprint
    class ArcadeCar(Blueprint):
        """Drivable arcade car with chase camera."""

        def __init__(self):
            super().__init__()
            p = self.properties
            p.setdefault("max_speed_kmh", 160.0)
            p.setdefault("acceleration", 14.0)
            p.setdefault("brake", 30.0)
            p.setdefault("steering", 75.0)
            p.setdefault("ride_height", 0.55)
            p.setdefault("camera_distance", 8.0)
            p.setdefault("camera_height", 3.0)
            # your own 3D models (Packages > Race kit: car body / wheel model...)
            p.setdefault("body_model", "")         # .glb .gltf .blend .obj ("" = built-in body)
            p.setdefault("body_scale", 1.0)        # 1.0 = auto-fit to car length
            p.setdefault("body_offset_y", 0.0)
            p.setdefault("body_turn", 0.0)         # degrees; 180 if the model faces backwards (long side is auto-aligned)
            p.setdefault("wheel_model", "")
            p.setdefault("wheel_scale", 1.0)       # 1.0 = auto-fit to wheel size
            p.setdefault("wheel_turn", 0.0)        # degrees around the up axis
            p.setdefault("mirror_left_wheels", True)
            p.setdefault("wheel_track", 0.95)      # half the distance between left and right wheels
            p.setdefault("wheel_base", 1.35)       # half the distance between front and rear wheels
            p.setdefault("show_wheels", True)      # off if your body model has its own wheels

        # ------------------------------------------------------------ play
        def on_begin_play(self):
            n = self.node
            self.start = (glm.vec3(n.position), glm.vec3(n.rotation_euler))
            self.respawn = self.start
            self.speed = 0.0
            self.vy = 0.0
            self.yaw = float(n.rotation_euler.y)
            self.pitch = float(n.rotation_euler.x)
            self.roll = float(n.rotation_euler.z)
            # the RaceManager may have frozen us already (begin order varies)
            self.frozen = getattr(self, "frozen", False)
            self.cam = None
            self.engine.camera_locked = True
            self.ignore = _ignore_set(n)

        def on_end_play(self):
            self.engine.camera_locked = False
            self.frozen = False
            self.node.position = glm.vec3(self.start[0])
            self.node.rotation_euler = glm.vec3(self.start[1])

        def reset_to(self, pos, yaw):
            self.node.position = glm.vec3(pos) + glm.vec3(0, 1.0, 0)
            self.yaw = float(yaw)
            self.speed = 0.0
            self.vy = 0.0

        def heading(self):
            r = math.radians(self.yaw)
            return glm.vec3(-math.sin(r), 0.0, -math.cos(r))

        def _ground(self, p):
            hit = self.engine.raycast(p + glm.vec3(0, 2.5, 0), glm.vec3(0, -1, 0), 6.0, ignore=self.ignore)
            return None if hit is None else float(hit.point.y)

        def on_tick(self, dt):
            e, inp, n, p = self.engine, self.engine.input, self.node, self.properties
            vmax = float(p["max_speed_kmh"]) / 3.6
            fwd = inp.key("w") or inp.key("up")
            back = inp.key("s") or inp.key("down")
            steer = (1.0 if (inp.key("a") or inp.key("left")) else 0.0) - \
                    (1.0 if (inp.key("d") or inp.key("right")) else 0.0)
            hand = inp.key("space")
            if inp.was_key_pressed("r"):
                self.reset_to(self.respawn[0], self.respawn[1].y)
            if self.frozen:
                fwd = back = hand = False
                steer = 0.0
            # -- engine / brakes / drag
            if fwd:
                self.speed += (float(p["acceleration"]) * (1.0 - _clamp(self.speed / vmax, 0, 1)) if self.speed >= 0
                               else float(p["brake"])) * dt
            elif back:
                self.speed -= (float(p["brake"]) if self.speed > 0.5 else float(p["acceleration"]) * 0.6) * dt
                self.speed = max(self.speed, -vmax * 0.3)
            else:
                drag = (1.5 + 0.012 * self.speed * self.speed) * dt
                self.speed -= _sign(self.speed) * min(abs(self.speed), drag)
            if hand:
                self.speed -= _sign(self.speed) * min(abs(self.speed), 22.0 * dt)
            # -- steering (stronger when slow, a bit looser with the handbrake)
            grip = _clamp(abs(self.speed) / 5.0, 0.0, 1.0) * (1.0 - 0.5 * _clamp(abs(self.speed) / vmax, 0, 1))
            if hand:
                grip *= 1.6
            self.yaw += steer * float(p["steering"]) * grip * dt * (1.0 if self.speed >= 0 else -1.0)
            h = self.heading()
            pos = glm.vec3(n.position)
            move = h * (self.speed * dt)
            # -- walls: stop / bounce on steep obstacles in front
            if abs(self.speed) > 0.1:
                d = h * _sign(self.speed)
                hit = e.raycast(pos + glm.vec3(0, 0.6, 0), d, abs(self.speed * dt) + 2.3, ignore=self.ignore)
                if hit is not None and abs(hit.normal.y) < 0.55:
                    move = glm.vec3(0.0)
                    self.speed *= -0.25
                    try:
                        e.audio.play("hit", 0.6)
                    except Exception:
                        pass
            new = pos + move
            # -- suspension: four wheel probes -> height, pitch and roll
            r = glm.vec3(-h.z, 0.0, h.x)
            hf, hb = self._ground(new + h * 1.4), self._ground(new - h * 1.4)
            hl, hr = self._ground(new - r * 0.85), self._ground(new + r * 0.85)
            ys = [v for v in (hf, hb, hl, hr) if v is not None]
            ride = float(p["ride_height"])
            if ys:
                target = sum(ys) / len(ys) + ride
                if new.y > target + 0.25:              # airborne (jumps, drops)
                    self.vy -= 22.0 * dt
                    new.y = max(target, new.y + self.vy * dt)
                    if new.y <= target:
                        self.vy = 0.0
                else:
                    new.y += (target - new.y) * _clamp(18.0 * dt, 0.0, 1.0)
                    self.vy = 0.0
                tp = math.degrees(math.atan2((hf - hb), 2.8)) if (hf is not None and hb is not None) else 0.0
                tr = math.degrees(math.atan2((hr - hl), 1.7)) if (hl is not None and hr is not None) else 0.0
                k = _clamp(10.0 * dt, 0.0, 1.0)
                self.pitch += (tp - self.pitch) * k
                self.roll += (tr - self.roll) * k
            else:
                self.vy -= 22.0 * dt
                new.y += self.vy * dt
                if new.y < -60.0:
                    self.reset_to(self.respawn[0], self.respawn[1].y)
                    new = glm.vec3(n.position)
            n.position = new
            n.rotation_euler = glm.vec3(self.pitch, self.yaw, self.roll)
            self._spin_wheels(dt, steer)
            self._camera(dt, new, h)
            hud = e.hud
            if hud is not None:
                hud.text(0.80, 0.86, f"{abs(self.speed) * 3.6:5.0f}", size=54, color=(1, 1, 1, 1))
                hud.text(0.915, 0.89, "km/h", size=22, color=(0.75, 0.8, 0.9, 1))
                hud.bar(0.80, 0.84, 0.17, 0.012, _clamp(abs(self.speed) / vmax, 0, 1),
                        fg=(0.3, 0.75, 1.0, 1.0), bg=(0.1, 0.12, 0.18, 0.8))

        def _spin_wheels(self, dt, steer):
            spin = getattr(self, "_wheel_spin", 0.0) + math.degrees(self.speed * dt / 0.36)
            self._wheel_spin = spin % 360.0
            for w in self.node.children:
                if w.name.startswith("Wheel Pivot"):
                    front = w.position.z < 0.0
                    w.rotation_euler = glm.vec3(-self._wheel_spin, steer * 24.0 if front else 0.0, 0.0)

        def _camera(self, dt, pos, h):
            cam = self.engine.camera
            p = self.properties
            want = pos - h * float(p["camera_distance"]) + glm.vec3(0, float(p["camera_height"]), 0)
            if self.cam is None:
                self.cam = glm.vec3(want)
            self.cam += (want - self.cam) * _clamp(6.0 * dt, 0.0, 1.0)
            cam.position = glm.vec3(self.cam)
            d = glm.normalize((pos + glm.vec3(0, 1.0, 0)) - self.cam)
            cam.yaw = math.degrees(math.atan2(-d.x, -d.z))
            cam.pitch = math.degrees(math.asin(_clamp(d.y, -1.0, 1.0)))

    @blueprint
    class RaceCheckpoint(Blueprint):
        """A gate the car must pass; 'order' 0 is the start/finish line."""

        def __init__(self):
            super().__init__()
            self.properties.setdefault("order", 0)
            self.properties.setdefault("radius", 9.0)

    @blueprint
    class RaceManager(Blueprint):
        """Countdown, checkpoints in order, laps, lap times, best lap, HUD."""

        def __init__(self):
            super().__init__()
            self.properties.setdefault("laps", 3)
            self.properties.setdefault("countdown", 3)

        def on_begin_play(self):
            e = self.engine
            self.cps = sorted([bp for bp in e.find_blueprints("RaceCheckpoint")],
                              key=lambda b: int(b.properties.get("order", 0)))
            cars = e.find_blueprints("ArcadeCar")
            self.car = cars[0] if cars else None
            best = e.load_game("race_best", default={}) or {}
            self.best_lap = best.get("lap")
            self.best_total = best.get("total")
            self.restart()

        def restart(self):
            self.state = "countdown"
            self.t = 0.0
            self.lap = 1
            self.next = 1 if len(self.cps) > 1 else 0
            self.lap_start = 0.0
            self.race_time = 0.0
            self.lap_times = []
            self.msg = None
            car = self.car
            if car is not None:
                start = getattr(car, "start", None)
                if start is not None:                  # back to the grid
                    car.node.position = glm.vec3(start[0])
                    car.yaw = float(start[1].y)
                    car.speed = car.vy = 0.0
                car.frozen = True                      # released by "GO!"
                car.respawn = (glm.vec3(car.node.position), glm.vec3(car.node.rotation_euler))

        def on_tick(self, dt):
            e, hud = self.engine, self.engine.hud
            laps = int(self.properties.get("laps", 3))
            self.t += dt
            if e.input.was_key_pressed("return"):
                self.restart()
            if self.state == "countdown":
                left = float(self.properties.get("countdown", 3)) - self.t
                if left <= 0:
                    self.state = "racing"
                    self.t = 0.0
                    if self.car:
                        self.car.frozen = False
                    self._flash("GO!", (0.3, 1.0, 0.45, 1.0))
                    try:
                        e.audio.play("pickup")
                    except Exception:
                        pass
                elif hud is not None:
                    hud.center_text(0.36, str(int(math.ceil(left))), size=120, color=(1.0, 0.85, 0.2, 1.0))
            elif self.state == "racing":
                self.race_time = self.t
                self._check()
            if hud is None:
                return
            hud.text(0.03, 0.04, f"Lap {min(self.lap, laps)} / {laps}", size=34, color=(1, 1, 1, 1))
            hud.text(0.03, 0.10, f"Time  {self._fmt(self.race_time)}", size=28, color=(0.85, 0.9, 1, 1))
            if self.lap_times:
                hud.text(0.03, 0.15, f"Last  {self._fmt(self.lap_times[-1])}", size=22, color=(0.75, 0.8, 0.9, 1))
            if self.best_lap:
                hud.text(0.03, 0.19, f"Best  {self._fmt(self.best_lap)}", size=22, color=(0.55, 0.85, 1.0, 1))
            if self.state == "racing" and self.cps and self.car is not None:
                cp = self.cps[self.next].node.position
                d = math.dist((cp.x, cp.z), (self.car.node.position.x, self.car.node.position.z))
                hud.text(0.03, 0.24, f"Next checkpoint  {d:4.0f} m", size=20, color=(1.0, 0.85, 0.3, 1))
            if self.state == "finished":
                hud.center_text(0.32, "FINISHED!", size=84, color=(0.3, 1.0, 0.45, 1.0))
                hud.center_text(0.45, f"Total {self._fmt(self.race_time)}   -   Enter to race again",
                                size=28, color=(1, 1, 1, 1))
            if self.msg and self.t - self.msg[2] < 1.2 and self.state != "finished":
                hud.center_text(0.30, self.msg[0], size=64, color=self.msg[1])

        def _flash(self, text, color):
            self.msg = (text, color, self.t)

        def _check(self):
            if not self.cps or self.car is None:
                return
            cp = self.cps[self.next]
            pos = self.car.node.position
            c = cp.node.position
            if math.dist((c.x, c.z), (pos.x, pos.z)) > float(cp.properties.get("radius", 9.0)) or abs(c.y - pos.y) > 8:
                return
            self.car.respawn = (glm.vec3(c), glm.vec3(cp.node.rotation_euler))
            try:
                self.engine.audio.play("pickup", 0.5)
            except Exception:
                pass
            if self.next == 0:                       # crossed the finish line
                lap_time = self.t - self.lap_start
                self.lap_times.append(lap_time)
                self.lap_start = self.t
                new_best = self.best_lap is None or lap_time < self.best_lap
                if new_best:
                    self.best_lap = lap_time
                if self.lap >= int(self.properties.get("laps", 3)):
                    self.state = "finished"
                    self.car.frozen = True
                    best_total = self.best_total is None or self.race_time < self.best_total
                    if best_total:
                        self.best_total = self.race_time
                    self.engine.save_game("race_best", {"lap": round(self.best_lap, 3),
                                                        "total": round(self.best_total, 3)})
                    return
                self.lap += 1
                self._flash("BEST LAP!" if new_best else f"LAP {self.lap}", (0.55, 0.85, 1.0, 1.0))
            self.next = (self.next + 1) % len(self.cps)

        @staticmethod
        def _fmt(t):
            t = max(0.0, float(t or 0.0))
            return f"{int(t // 60):02d}:{t % 60:05.2f}"

    CLS.update(ArcadeCar=ArcadeCar, RaceCheckpoint=RaceCheckpoint, RaceManager=RaceManager)


# --------------------------------------------------------------------------- building blocks
def _part(engine, parent, kind, name, pos, scale, color, rough=0.5, metal=0.0, emissive=None):
    m = H3D.module
    if kind == "cube":
        n = engine.add_cube(size=1.0, name=name, solid=False)
    else:
        n = engine.add_sphere(radius=0.5, name=name, solid=False)
    n.remove()
    parent.add_child(n)
    n.position = m.glm.vec3(*pos)
    n.scale = m.glm.vec3(*scale)
    n.material.base_color = color
    n.material.roughness = rough
    n.material.metallic = metal
    if emissive:
        n.material.emissive = emissive
    return n


def build_car(engine, position, yaw=0.0, color=(0.85, 0.12, 0.1, 1.0)):
    m = H3D.module
    glm = m.glm
    car = m.SceneNode(engine._unique_node_name("Race Car"), parent=engine.scene.root)
    car.position = glm.vec3(position)
    car.rotation_euler = glm.vec3(0.0, yaw, 0.0)
    _part(engine, car, "cube", "Body", (0, 0.15, 0), (1.9, 0.55, 4.2), color, 0.25, 0.6)
    _part(engine, car, "cube", "Cabin", (0, 0.62, 0.35), (1.55, 0.5, 1.9), (0.08, 0.1, 0.14, 1.0), 0.1, 0.3)
    _part(engine, car, "cube", "Spoiler", (0, 0.72, 1.95), (1.8, 0.08, 0.4), color, 0.3, 0.5)
    for i, (x, z) in enumerate(((-0.95, -1.35), (0.95, -1.35), (-0.95, 1.35), (0.95, 1.35))):
        _part(engine, car, "sphere", f"Wheel {i + 1}", (x, -0.18, z), (0.36, 0.72, 0.72), (0.05, 0.05, 0.06, 1.0), 0.8)
    for x in (-0.6, 0.6):
        _part(engine, car, "cube", "Headlight", (x, 0.2, -2.11), (0.4, 0.14, 0.04), (1, 1, 0.9, 1), 0.2, 0.0, (2.5, 2.4, 2.0))
        _part(engine, car, "cube", "Taillight", (x, 0.25, 2.11), (0.45, 0.12, 0.04), (0.9, 0.05, 0.05, 1), 0.3, 0.0, (1.6, 0.05, 0.05))
    engine.attach_blueprint(car, CLS["ArcadeCar"])
    return car


def build_checkpoint(engine, position, yaw=0.0, order=0, width=12.0):
    m = H3D.module
    glm = m.glm
    gate = m.SceneNode(engine._unique_node_name("Checkpoint" if order else "Start Finish"), parent=engine.scene.root)
    gate.position = glm.vec3(position)
    gate.rotation_euler = glm.vec3(0.0, yaw, 0.0)
    col = (0.95, 0.95, 0.95, 1.0) if order == 0 else (1.0, 0.6, 0.1, 1.0)
    for x in (-width / 2, width / 2):
        _part(engine, gate, "cube", "Post", (x, 2.5, 0), (0.4, 5.0, 0.4), (0.2, 0.22, 0.26, 1.0), 0.4, 0.6)
    _part(engine, gate, "cube", "Banner", (0, 5.2, 0), (width + 0.4, 0.9, 0.25), col, 0.5, 0.0,
          (0.6, 0.6, 0.6) if order == 0 else (0.9, 0.45, 0.05))
    engine.attach_blueprint(gate, CLS["RaceCheckpoint"])
    gate.blueprints[-1].properties.update({"order": int(order), "radius": width * 0.75})
    return gate


def _front(engine, dist=15.0):
    glm = H3D.module.glm
    cam = engine.camera
    f = cam.forward()
    f = glm.normalize(glm.vec3(f.x, 0.0, f.z)) if (abs(f.x) + abs(f.z)) > 1e-6 else glm.vec3(0, 0, -1)
    p = cam.position + f * dist
    hit = engine.raycast(p + glm.vec3(0, 50, 0), glm.vec3(0, -1, 0), 200.0)
    p.y = (hit.point.y if hit is not None else 0.0)
    return p, math.degrees(math.atan2(-f.x, -f.z))


def add_car(engine):
    p, yaw = _front(engine)
    car = build_car(engine, p + H3D.module.glm.vec3(0, 0.6, 0), yaw)
    engine.select_node(car)


def add_checkpoint(engine):
    p, yaw = _front(engine, 25.0)
    order = len(engine.find_blueprints("RaceCheckpoint"))
    engine.select_node(build_checkpoint(engine, p, yaw, order))


def add_manager(engine):
    m = H3D.module
    if engine.find_blueprints("RaceManager"):
        H3D.warn("this scene already has a race manager")
        return
    n = m.SceneNode("Race Manager", parent=engine.scene.root)
    engine.attach_blueprint(n, CLS["RaceManager"])
    engine.select_node(n)


# --------------------------------------------------------------------------- custom models
BODY_PARTS = ("Body", "Cabin", "Spoiler", "Headlight", "Taillight")


def _bounds(engine, node, frame):
    """AABB of node's whole mesh subtree expressed in `frame`'s space."""
    np = H3D.module.np
    glm = H3D.module.glm
    inv = glm.inverse(frame.world_matrix())
    lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
    for child in node.traverse():
        if child.mesh is None:
            continue
        pos, _i = H3D.module._mesh_cpu_triangles(child.mesh)
        if pos is None or len(pos) == 0:
            continue
        m = inv * child.world_matrix()
        cols = np.array([[m[c][r] for r in range(4)] for c in range(4)], dtype=np.float64)
        hom = np.ones((len(pos), 4)); hom[:, :3] = pos
        pts = (hom @ cols)[:, :3]
        lo, hi = np.minimum(lo, pts.min(0)), np.maximum(hi, pts.max(0))
    if not np.isfinite(lo).all():
        raise RuntimeError("the model has no mesh")
    return lo, hi


def _place(engine, model, frame, turn, size, axes, align):
    """Turn the model, scale it uniformly so its size along `axes` (in
    frame space) is `size`, then move it so its bounds line up as `align`
    says: per axis None = centre, ('min', v) = lowest point at v."""
    glm = H3D.module.glm
    model.position = glm.vec3(0.0)
    model.rotation_euler = glm.vec3(0.0, turn, 0.0)
    model.scale = glm.vec3(1.0)
    lo, hi = _bounds(engine, model, frame)
    ext = hi - lo
    k = size / max(max(float(ext[a]) for a in axes), 1e-6)
    model.scale = glm.vec3(k)
    lo, hi = _bounds(engine, model, frame)
    off = [0.0, 0.0, 0.0]
    for ax in range(3):
        if align[ax] is None:
            off[ax] = -(lo[ax] + hi[ax]) * 0.5
        else:
            off[ax] = align[ax][1] - lo[ax]
    model.position = glm.vec3(*off)        # frame is the model's parent (car or wheel pivot)


def _auto_turn(engine, model, long_axis_z):
    """0 or 90 degrees so the model's long (body) / thin (wheel axle) side ends up right."""
    glm = H3D.module.glm
    model.position = glm.vec3(0.0); model.rotation_euler = glm.vec3(0.0); model.scale = glm.vec3(1.0)
    lo, hi = _bounds(engine, model, model)
    ex, ez = float(hi[0] - lo[0]), float(hi[2] - lo[2])
    if long_axis_z:            # car body: length along Z, front towards -Z (the car's forward)
        # glTF / Blender exports face +Z, so they need half a turn; models
        # built lengthwise along X usually face -X.  body_turn fixes the rest.
        return -90.0 if ex > ez * 1.05 else 180.0
    return 90.0 if ez < ex * 0.95 else 0.0     # wheel: axle (thin side) along X


def _load_child(engine, parent, path, name):
    node = engine.load_model(path)
    if node is None:
        raise RuntimeError(f"could not load {os.path.basename(path)}")
    node.remove()
    parent.add_child(node)
    node.name = name
    node.collider = None
    return node


def _same_source(node, path):
    try:
        return os.path.normcase(os.path.abspath(node.source_path or "")) == os.path.normcase(os.path.abspath(path))
    except Exception:
        return False


def wheel_slots(p):
    x = float(p.get("wheel_track", 0.95))
    z = float(p.get("wheel_base", 1.35))
    return ((-x, -0.18, -z), (x, -0.18, -z), (-x, -0.18, z), (x, -0.18, z))


def apply_car_models(engine, car, bp):
    """Show the built-in car or the user's body / wheel models (auto-fitted)."""
    glm = H3D.module.glm
    p = bp.properties
    ref = str(p.get("body_model") or "")
    body_path = engine.asset_path(ref) if ref else ""
    custom = next((c for c in car.children if c.name == "Custom Body"), None)
    if body_path and os.path.isfile(body_path):
        if custom is None or not _same_source(custom, body_path):
            if custom is not None:
                engine._delete_node(custom)
            custom = _load_child(engine, car, body_path, "Custom Body")
        turn = _auto_turn(engine, custom, True) + float(p.get("body_turn", 0.0))
        _place(engine, custom, car, turn, 4.3 * (float(p.get("body_scale", 1.0)) or 1.0), (2,),
               (None, ("min", -0.55 + float(p.get("body_offset_y", 0.0))), None))
        custom.visible = True
    elif custom is not None:
        engine._delete_node(custom)
        custom = None
    for node in car.children:
        if any(node.name.startswith(b) for b in BODY_PARTS):
            node.visible = custom is None
    # ---- wheels -------------------------------------------------------
    ref = str(p.get("wheel_model") or "")
    wheel_path = engine.asset_path(ref) if ref else ""
    show = bool(p.get("show_wheels", True))
    slots = wheel_slots(p)
    pivots = [c for c in car.children if c.name.startswith("Wheel Pivot")]
    want = bool(show and wheel_path and os.path.isfile(wheel_path))
    if want and (len(pivots) != 4 or not all(pv.children and _same_source(pv.children[0], wheel_path) for pv in pivots)):
        for pv in pivots:
            engine._delete_node(pv)
        pivots = []
        for i in range(4):
            pv = H3D.module.SceneNode(f"Wheel Pivot {i + 1}", parent=car)
            _load_child(engine, pv, wheel_path, f"Custom Wheel {i + 1}")
            pivots.append(pv)
    if not want:
        for pv in pivots:
            engine._delete_node(pv)
        pivots = []
    for i, pv in enumerate(pivots):
        pv.position = glm.vec3(*slots[i])
        pv.rotation_euler = glm.vec3(0.0)
        wheel = pv.children[0]
        left = slots[i][0] < 0
        turn = (_auto_turn(engine, wheel, False) + float(p.get("wheel_turn", 0.0))
                + (180.0 if (left and p.get("mirror_left_wheels", True)) else 0.0))
        _place(engine, wheel, pv, turn, 0.74 * (float(p.get("wheel_scale", 1.0)) or 1.0), (1, 2), (None, None, None))
    for node in car.children:
        if node.name.startswith("Wheel ") and not node.name.startswith("Wheel Pivot"):
            node.visible = show and not pivots
            k = node.name.split()[-1]
            if k.isdigit() and 1 <= int(k) <= 4:
                node.position = glm.vec3(*slots[int(k) - 1])
    car._crk_sig = _model_sig(bp)


MODEL_KEYS = ("body_model", "body_scale", "body_offset_y", "body_turn", "wheel_model", "wheel_scale",
              "wheel_turn", "mirror_left_wheels", "wheel_track", "wheel_base", "show_wheels")


def _model_sig(bp):
    p = bp.properties
    return tuple(str(p.get(k, "")) for k in MODEL_KEYS)


def _cars(engine):
    for bp in engine.find_blueprints("ArcadeCar"):
        if bp.node is not None:
            yield bp.node, bp


def _apply_all(engine):
    for car, bp in _cars(engine):
        try:
            apply_car_models(engine, car, bp)
        except Exception as ex:
            H3D.warn(f"{car.name}: {ex}")


def _selected_car(engine):
    sel = getattr(engine, "selected", None)
    if sel is not None:
        for bp in getattr(sel, "blueprints", []) or []:
            if bp.__class__.__name__ == "ArcadeCar":
                return sel, bp
    cars = list(_cars(engine))
    return cars[0] if len(cars) == 1 else (None, None)


MODEL_TYPES = [("3D models", "*.glb *.gltf *.blend *.obj"), ("All files", "*.*")]


def _pick_model(engine, what):
    car, bp = _selected_car(engine)
    if car is None:
        H3D.warn("select a car first")
        return
    path = engine._native_dialog("open", f"Choose a {what} model", filetypes=MODEL_TYPES)
    if not path:
        return
    bp.properties["body_model" if what == "body" else "wheel_model"] = path
    try:
        apply_car_models(engine, car, bp)
        H3D.log(f"{car.name}: {what} model set to {os.path.basename(path)} (fine-tune in the Inspector)")
    except Exception as ex:
        H3D.warn(f"could not use that model: {ex}")


def _turn_body(engine):
    car, bp = _selected_car(engine)
    if car is None:
        H3D.warn("select a car first")
        return
    bp.properties["body_turn"] = (float(bp.properties.get("body_turn", 0.0)) + 180.0) % 360.0
    apply_car_models(engine, car, bp)


def _reset_models(engine):
    car, bp = _selected_car(engine)
    if car is None:
        H3D.warn("select a car first")
        return
    bp.properties["body_model"] = ""
    bp.properties["wheel_model"] = ""
    apply_car_models(engine, car, bp)


def register(h3d):
    global H3D
    H3D = h3d
    _classes()
    h3d.add_tool("Race kit: add car", add_car, "#B23A2E")
    h3d.add_tool("Race kit: add checkpoint", add_checkpoint)
    h3d.add_tool("Race kit: add race manager", add_manager)
    h3d.add_tool("Race kit: car body model…", lambda e: _pick_model(e, "body"))
    h3d.add_tool("Race kit: car wheel model…", lambda e: _pick_model(e, "wheel"))
    h3d.add_tool("Race kit: turn car body 180°", _turn_body)
    h3d.add_tool("Race kit: built-in car look", _reset_models)
    state = {"t": 0.0}

    def watch(engine, dt):                      # Inspector edits re-fit the models
        if engine.mode != "editor" or engine._playing:
            return
        state["t"] += dt
        if state["t"] < 0.25:
            return
        state["t"] = 0.0
        for car, bp in _cars(engine):
            if getattr(car, "_crk_sig", None) != _model_sig(bp):
                try:
                    apply_car_models(engine, car, bp)
                except Exception as ex:
                    car._crk_sig = _model_sig(bp)
                    h3d.warn(f"{car.name}: {ex}")
    h3d.on("update", watch)
    h3d.on("init", _apply_all)
    h3d.on("project_loaded", lambda e, p: _apply_all(e))
