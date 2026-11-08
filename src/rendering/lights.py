# @heaven3d-part rendering/lights.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# #############################################################################
# SECTION 6  —  LIGHTS
# #############################################################################

@dataclass
class SunLight:
    direction: glm.vec3 = glm.vec3(-0.5, -1.0, -0.3)
    color: glm.vec3 = glm.vec3(1.0, 0.96, 0.90)
    intensity: float = 3.0
    shadow_map_size: int = 2048
    shadow_bias: float = 0.001

    def __post_init__(self):
        # Ensure direction is normalised
        d = glm.length(self.direction)
        if d > 0:
            self.direction = self.direction / d

    def view_matrix(self) -> glm.mat4:
        # Look from -direction towards origin (we'll offset by target later)
        eye = -self.direction * 50.0
        return glm.lookAt(eye, glm.vec3(0, 0, 0), glm.vec3(0, 1, 0))

    def proj_matrix(self) -> glm.mat4:
        return glm.ortho(-50, 50, -50, 50, 0.1, 200.0)


@dataclass
class PointLight:
    """Unity-style omnidirectional local light.

    Position comes from the SceneNode transform; color, intensity and range
    live here so the node can be saved, duplicated and inspected."""
    color: glm.vec3 = field(default_factory=lambda: glm.vec3(1.0, 0.92, 0.78))
    intensity: float = 8.0
    range: float = 12.0

    def to_dict(self) -> dict:
        return {"color": [float(self.color.x), float(self.color.y), float(self.color.z)],
                "intensity": float(self.intensity),
                "range": float(self.range)}

    @classmethod
    def from_dict(cls, d: dict) -> "PointLight":
        c = d.get("color", [1.0, 0.92, 0.78])
        return cls(color=glm.vec3(float(c[0]), float(c[1]), float(c[2])),
                   intensity=float(d.get("intensity", 8.0)),
                   range=max(0.01, float(d.get("range", 12.0))))


@dataclass
class SpotLight:
    """Unity-style cone light.  The cone direction follows the node's forward
    vector, so rotating the node with the editor gizmo aims the light."""
    color: glm.vec3 = field(default_factory=lambda: glm.vec3(1.0, 0.96, 0.88))
    intensity: float = 14.0
    range: float = 18.0
    inner_angle: float = 25.0   # full inner cone angle, degrees
    outer_angle: float = 35.0   # full outer cone angle, degrees

    def to_dict(self) -> dict:
        return {"color": [float(self.color.x), float(self.color.y), float(self.color.z)],
                "intensity": float(self.intensity),
                "range": float(self.range),
                "inner_angle": float(self.inner_angle),
                "outer_angle": float(self.outer_angle)}

    @classmethod
    def from_dict(cls, d: dict) -> "SpotLight":
        c = d.get("color", [1.0, 0.96, 0.88])
        inner = _clamp(float(d.get("inner_angle", 25.0)), 0.1, 179.0)
        outer = _clamp(float(d.get("outer_angle", 35.0)), 0.1, 179.0)
        if outer < inner:
            outer = inner
        return cls(color=glm.vec3(float(c[0]), float(c[1]), float(c[2])),
                   intensity=float(d.get("intensity", 14.0)),
                   range=max(0.01, float(d.get("range", 18.0))),
                   inner_angle=inner,
                   outer_angle=outer)


@dataclass
class AmbientLight:
    color: glm.vec3 = glm.vec3(0.25, 0.27, 0.32)
    intensity: float = 0.6


@dataclass
class CloudSettings:
    """Procedural cloud layer — optimised EEVEE-quality clouds.

    Rendered in the sky's background branch of the lighting pass: a
    wind-animated FBM noise field projected onto a plane at `altitude`,
    with sun-lit silver linings (density-gradient toward the sun), warm
    forward-scattering at sunset, belly shading for thick cores, horizon
    dissolve, and day/night tinting.  The cloud layer also dims the sun
    disc behind overcast pixels and slightly softens direct light / IBL
    under heavy coverage, so the ground lighting stays coherent with the
    sky.  Everything is hot-editable from the Inspector.
    """
    enabled: bool = True
    coverage: float = 0.45          # 0..1 — fraction of the sky filled
    density: float = 0.85           # opacity + belly darkening
    scale: float = 1.0              # noise scale (larger = bigger cloud masses)
    altitude: float = 800.0         # world height of the cloud plane
    speed: float = 0.012            # wind drift speed (UV units / second)
    tint: glm.vec3 = glm.vec3(1.0, 1.0, 1.0)   # base cloud colour multiplier
    wind_dir: Tuple[float, float] = (0.8, 0.6)  # wind direction on XZ

    def to_dict(self) -> dict:
        return {"enabled": self.enabled,
                "coverage": self.coverage,
                "density": self.density,
                "scale": self.scale,
                "altitude": self.altitude,
                "speed": self.speed,
                "tint": [float(self.tint.x), float(self.tint.y), float(self.tint.z)],
                "wind_dir": [float(self.wind_dir[0]), float(self.wind_dir[1])]}

    @classmethod
    def from_dict(cls, d: dict) -> "CloudSettings":
        t = d.get("tint", [1.0, 1.0, 1.0])
        w = d.get("wind_dir", [0.8, 0.6])
        return cls(enabled=bool(d.get("enabled", True)),
                   coverage=float(d.get("coverage", 0.45)),
                   density=float(d.get("density", 0.85)),
                   scale=float(d.get("scale", 1.0)),
                   altitude=float(d.get("altitude", 800.0)),
                   speed=float(d.get("speed", 0.012)),
                   tint=glm.vec3(float(t[0]), float(t[1]), float(t[2])),
                   wind_dir=(float(w[0]), float(w[1])))

