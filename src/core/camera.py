# @heaven3d-part core/camera.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# #############################################################################
# SECTION 7  —  CAMERA  (Unity 2022.2 free-fly editor camera)
# #############################################################################

class Camera:
    """
    Unity 2022.2-style editor camera with fly / orbit / pan.
      • Right-drag         → look around (yaw world-Y, pitch local-X)
      • WASD (while RMB)   → move on plane
      • Q/E                → down / up
      • Shift              → sprint
      • Wheel              → adjust move speed (RMB held) or dolly zoom
      • Alt + Left-drag    → orbit around pivot
      • Middle-drag        → pan
      • F                  → frame selection
    """
    def __init__(self, position=None, yaw: float = 0.0, pitch: float = 0.0,
                 fov: float = 60.0, aspect: float = 16/9,
                 near: float = 0.1, far: float = 2000.0):
        self.position = glm.vec3(position) if position else glm.vec3(0, 3, -10)
        self.yaw = float(yaw)
        self.pitch = float(pitch)
        self.fov = float(fov)
        self.aspect = float(aspect)
        self.near = float(near)
        self.far = float(far)
        self.move_speed = 12.0
        self.look_speed = 0.25
        self.sprint_mult = 4.0
        self.orbit_pivot = glm.vec3(0, 0, 0)
        self.orbit_distance = glm.length(self.position - self.orbit_pivot) or 10.0

    def projection(self) -> glm.mat4:
        return glm.perspective(math.radians(self.fov), self.aspect,
                               self.near, self.far)

    def view(self) -> glm.mat4:
        return glm.lookAt(self.position, self.position + self.forward(), self.up())

    def forward(self) -> glm.vec3:
        # OpenGL convention: yaw=0, pitch=0 → looking down -Z
        cp = math.cos(math.radians(self.pitch))
        return glm.vec3(
            -math.sin(math.radians(self.yaw)) * cp,
             math.sin(math.radians(self.pitch)),
            -math.cos(math.radians(self.yaw)) * cp,
        )

    def right(self) -> glm.vec3:
        return glm.normalize(glm.cross(self.forward(), glm.vec3(0, 1, 0)))

    def up(self) -> glm.vec3:
        return glm.vec3(0, 1, 0)

    # ---- input handling (called by Editor each frame) ----
    def update(self, dt: float, input_state: "InputState"):
        look_dt = 0.0
        if input_state.rmb:
            dx, dy = input_state.mouse_delta
            self.yaw -= dx * self.look_speed
            self.pitch = _clamp(self.pitch - dy * self.look_speed, -89, 89)
            speed = self.move_speed * (self.sprint_mult if input_state.shift else 1.0)
            fwd = self.forward()
            # Unity-style free-fly: W/S move along the TRUE 3D forward vector
            # (including the pitch), so the camera flies up/down with the view
            # direction.  Q/E remain for explicit world-vertical strafing.
            right = self.right()
            move = glm.vec3(0)
            if input_state.key('w'): move += fwd
            if input_state.key('s'): move -= fwd
            if input_state.key('d'): move += right
            if input_state.key('a'): move -= right
            if input_state.key('e'): move += glm.vec3(0, 1, 0)
            if input_state.key('q'): move -= glm.vec3(0, 1, 0)
            if glm.length(move) > 0:
                self.position += glm.normalize(move) * speed * dt
        elif input_state.mmb:
            dx, dy = input_state.mouse_delta
            right = self.right()
            up = self.up()
            pan_speed = (self.orbit_distance * 0.0015 + 0.02)
            self.position -= right * dx * pan_speed * 60
            self.position += up * dy * pan_speed * 60
            self.orbit_pivot -= right * dx * pan_speed * 60
            self.orbit_pivot += up * dy * pan_speed * 60
        elif input_state.alt and input_state.lmb:
            dx, dy = input_state.mouse_delta
            self.yaw -= dx * self.look_speed * 0.8
            self.pitch = _clamp(self.pitch - dy * self.look_speed * 0.8, -89, 89)
            # keep camera on sphere around pivot
            self._orbit_from_pivot()

        # Wheel: dolly
        if input_state.wheel != 0:
            if input_state.rmb:
                self.move_speed = _clamp(self.move_speed + input_state.wheel * 2.0, 1.0, 500.0)
            else:
                self.orbit_distance = _clamp(self.orbit_distance - input_state.wheel * 2.0, 1.0, 1000.0)
                self._orbit_from_pivot()

    def _orbit_from_pivot(self):
        cp = math.cos(math.radians(self.pitch))
        offset = glm.vec3(
            -math.sin(math.radians(self.yaw)) * cp,
             math.sin(math.radians(self.pitch)),
            -math.cos(math.radians(self.yaw)) * cp,
        ) * self.orbit_distance
        self.position = self.orbit_pivot + offset

    def frame(self, target: glm.vec3, distance: float = 10.0):
        """Position the camera to look at `target` from `distance` away.
        Picks a pleasant default viewing angle (preserves current yaw/pitch
        only if the camera is already roughly aimed at the target; otherwise
        uses a Unity-perspective-style default)."""
        self.orbit_pivot = glm.vec3(target)
        self.orbit_distance = float(distance)
        # Compute the direction from a default viewing angle, then place the
        # camera behind that direction at the requested distance.
        # Unity perspective default: yaw ~ -35°, pitch ~ -25°
        yaw = self.yaw if abs(self.yaw) > 0.5 else -35.0
        pitch = self.pitch if abs(self.pitch) > 0.5 else -25.0
        cp = math.cos(math.radians(pitch))
        forward = glm.vec3(
            -math.sin(math.radians(yaw)) * cp,
             math.sin(math.radians(pitch)),
            -math.cos(math.radians(yaw)) * cp,
        )
        # Camera sits at target - forward * distance so it looks back at target
        self.position = glm.vec3(target) - forward * distance
        self.yaw = yaw
        self.pitch = pitch


class InputState:
    """Snapshot of input held by the editor each frame."""
    def __init__(self):
        self._keys = set()
        self._pressed = set()     # keys that went DOWN this frame only
        self.mouse_pos = (0, 0)
        self.mouse_delta = (0.0, 0.0)
        self.wheel = 0
        self.lmb = False
        self.rmb = False
        self.mmb = False
        self.lmb_just_pressed = False
        self.rmb_just_pressed = False
        self.alt = False
        self.shift = False
        self.ctrl = False
        self.click_just_pressed = False
        self.click_pos = (0, 0)

    def key(self, name: str) -> bool:
        return name.lower() in self._keys

    def was_key_pressed(self, name: str) -> bool:
        """True only on the frame the key went down (jump, reload, ...)."""
        return name.lower() in self._pressed

    def set_key(self, name: str, down: bool):
        k = name.lower()
        if down:
            if k not in self._keys:
                self._pressed.add(k)
            self._keys.add(k)
        else:
            self._keys.discard(k)

    def end_frame(self):
        """Clear per-frame edges (called by the engine at frame end)."""
        self._pressed.clear()
        self.lmb_just_pressed = False
        self.rmb_just_pressed = False

