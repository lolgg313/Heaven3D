# @heaven3d-part core/scene_graph.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# #############################################################################
# SECTION 1  —  SCENE GRAPH  (transform hierarchy in pure Python + pyglm)
# #############################################################################

_GLM_VEC3 = type(glm.vec3(0.0))
_GLM_MAT4 = type(glm.mat4(1.0))


class SceneNode:
    """
    A transform in the scene graph.  Holds local pos/rot/scale, a parent,
    children, an optional Mesh, an optional Material, and an optional Blueprint.

    World transforms are computed lazily by walking up the parent chain.
    """
    _next_id = 0

    def __init__(self, name: str = "Node", parent: Optional["SceneNode"] = None):
        self.id = SceneNode._next_id
        SceneNode._next_id += 1
        self.name = name
        self.parent: Optional[SceneNode] = None
        self.children: List[SceneNode] = []
        self.position = glm.vec3(0.0)
        # Euler angles in degrees (Unity convention: Y-X-Z rotation order)
        self.rotation_euler = glm.vec3(0.0)
        self.scale = glm.vec3(1.0)
        self.mesh: Optional["Mesh"] = None
        self.material: Optional["Material"] = None
        # Multiple blueprints may be attached to one node (e.g. EnemyAI +
        # Health).  `blueprint` remains as a first-item convenience view.
        self.blueprints: List["Blueprint"] = []
        # Physics components (CPU engine, SECTION 14A): a Collider makes the
        # node solid (static level geometry); add a RigidBody for dynamic or
        # kinematic motion.  None = no collision.
        self.collider: Optional["Collider"] = None
        self.rigidbody: Optional["RigidBody"] = None
        self.tag: str = ""
        self.source_path: Optional[str] = None
        self.visible = True
        self.is_terrain = False
        # Primitive-mesh type tag (None | "cube" | "plane" | "sphere").
        # Lets save/load rebuild editor-generated primitives that have no
        # source_path on disk.
        self.mesh_type: Optional[str] = None
        # Params used to regenerate the primitive (e.g. cube size).
        self.mesh_params: Dict[str, Any] = {}
        self.user_data: Dict[str, Any] = {}
        self.is_sun = False
        self.sun_light: Optional["SunLight"] = None
        # Unity-style local lights.  These are first-class scene nodes: they
        # appear in the Hierarchy, move/rotate with the transform gizmo, and
        # round-trip through save/load exactly like meshes and the Sun.
        self.is_point_light = False
        self.point_light: Optional["PointLight"] = None
        self.is_spot_light = False
        self.spot_light: Optional["SpotLight"] = None
        # Procedural cloud layer — a first-class scene feature, exactly like
        # the Sun.  The default "Clouds" node carries no mesh; it only holds
        # a CloudSettings block that drives the sky pass.
        self.is_clouds = False
        self.cloud_settings: Optional["CloudSettings"] = None
        # Triple-A procedural ocean — a first-class scene feature, exactly
        # like the Clouds: the default "Ocean" node carries no mesh, only
        # an OceanSettings block that drives the water pass + buoyancy.
        # [TRIPLEA:ocean_scenenode_fields]
        self.is_ocean = False
        self.ocean_settings: Optional["OceanSettings"] = None
        # Gameplay spawn markers (Library → Spawn Point section):
        #   None | "player" | "enemy" | "bullet_pickup" | "health_pickup"
        # A scene may contain AT MOST ONE "player" spawn (enforced by the
        # engine everywhere a spawn can be created: Library, Duplicate,
        # project load).  `spawn_config` holds the Inspector-editable
        # gameplay values (health / bullets / speeds / pickup amounts).
        self.spawn_kind: Optional[str] = None
        self.spawn_config: Dict[str, Any] = {}

        if parent is not None:
            parent.add_child(self)

    # ---- blueprint convenience (multi-blueprint aware) ----
    @property
    def blueprint(self) -> Optional["Blueprint"]:
        """The FIRST attached blueprint (kept for backward compatibility)."""
        return self.blueprints[0] if self.blueprints else None

    @blueprint.setter
    def blueprint(self, bp: Optional["Blueprint"]):
        if bp is None:
            self.blueprints = []
        else:
            self.blueprints = [bp]

    # ---- hierarchy ----
    def add_child(self, child: "SceneNode"):
        if child.parent is not None:
            child.parent.children.remove(child)
        child.parent = self
        self.children.append(child)
        return child

    def remove(self):
        if self.parent is not None:
            self.parent.children.remove(self)
            self.parent = None

    # ---- local matrix ----
    def local_matrix(self) -> glm.mat4:
        # Memoised on the exact TRS values: physics / AI / rendering ask for
        # the same node's matrix thousands of times per frame, and rebuilding
        # three rotations each time was the engine's #1 CPU cost.  The cache
        # holds COPIES of the values, so in-place edits (node.position.x = 1)
        # are always seen; a fresh copy is returned so callers may mutate it.
        p, r, sc = self.position, self.rotation_euler, self.scale
        if type(p) is _GLM_VEC3 and type(r) is _GLM_VEC3 and type(sc) is _GLM_VEC3:
            c = self.__dict__.get("_h3d_lm")
            if c is not None and c[0] == p and c[1] == r and c[2] == sc:
                return _GLM_MAT4(c[3])
            M = self._local_matrix_uncached()
            self.__dict__["_h3d_lm"] = (_GLM_VEC3(p), _GLM_VEC3(r), _GLM_VEC3(sc), M)
            return _GLM_MAT4(M)
        return self._local_matrix_uncached()

    def _local_matrix_uncached(self) -> glm.mat4:
        T = glm.translate(glm.mat4(1.0), self.position)
        # Unity-style rotation: Y then X then Z
        R = (glm.rotate(glm.mat4(1.0), math.radians(self.rotation_euler.y),
                        glm.vec3(0, 1, 0))
             * glm.rotate(glm.mat4(1.0), math.radians(self.rotation_euler.x),
                          glm.vec3(1, 0, 0))
             * glm.rotate(glm.mat4(1.0), math.radians(self.rotation_euler.z),
                          glm.vec3(0, 0, 1)))
        S = glm.scale(glm.mat4(1.0), self.scale)
        return T * R * S

    # ---- world matrix (recursive) ----
    def world_matrix(self) -> glm.mat4:
        if self.parent is None:
            return self.local_matrix()
        return self.parent.world_matrix() * self.local_matrix()

    def world_position(self) -> glm.vec3:
        return glm.vec3(self.world_matrix()[3])

    # ---- convenience: walk the whole tree ----
    def traverse(self) -> List["SceneNode"]:
        result = [self]
        for c in self.children:
            result.extend(c.traverse())
        return result

    def __repr__(self):
        return f"<SceneNode '{self.name}' pos={tuple(round(v,2) for v in self.position)}>"


class Scene:
    """Root scene container.  The Sun is a first-class SceneNode so it can
    be selected, moved and rotated like any object.  Rotating the Sun node
    changes the light direction, shadow orientation and the procedural sky
    (day / night cycle) exactly like Unity's Directional Light."""
    def __init__(self):
        self.root = SceneNode("__root__")
        self._sun_node: Optional[SceneNode] = None
        self._clouds_node: Optional[SceneNode] = None
        self._ocean_node: Optional[SceneNode] = None  # [TRIPLEA:ocean_scene_init_field]
        self._create_default_sun()
        self._create_default_clouds()
        self._create_default_ocean()  # [TRIPLEA:ocean_scene_init_call]

    def _create_default_sun(self):
        """Create the default Directional Light (Sun) node.
        Unity-style default rotation so light comes from a pleasant angle."""
        sun = SceneNode("Sun", parent=self.root)
        sun.is_sun = True
        sun.position = glm.vec3(0.0, 50.0, 0.0)
        # Pitch -50, Yaw -30  →  light shines down-right (nice default)
        sun.rotation_euler = glm.vec3(-50.0, -30.0, 0.0)
        sun.sun_light = SunLight()
        self._sun_node = sun

    def _create_default_clouds(self):
        """Create the default procedural Clouds node — exactly like the Sun,
        the cloud layer ships with EVERY new scene as a selectable,
        inspectable, serialisable Hierarchy node.  It has no mesh: the node
        only carries a CloudSettings block consumed by the renderer's sky
        pass.  Delete it to get a clear sky; every parameter (coverage,
        density, scale, altitude, wind speed, tint) is hot-editable in the
        Inspector."""
        clouds = SceneNode("Clouds", parent=self.root)
        clouds.is_clouds = True
        # Display/gizmo position only — the cloud plane itself is controlled
        # by CloudSettings.altitude.
        clouds.position = glm.vec3(0.0, 80.0, 0.0)
        clouds.cloud_settings = CloudSettings()
        self._clouds_node = clouds

    @property
    def clouds(self) -> Optional["CloudSettings"]:
        """Return the active CloudSettings, or None if the Clouds node was
        deleted from the scene (clear sky)."""
        if self._clouds_node is not None and self._clouds_node.cloud_settings is not None:
            return self._clouds_node.cloud_settings
        return None

    def _create_default_ocean(self):
        """Create the default Ocean node — exactly like the Sun and the
        Clouds, the ocean ships with EVERY new scene as a selectable,
        inspectable, serialisable Hierarchy node.  It has no mesh: the
        node only carries an OceanSettings block consumed by the water
        render pass and the buoyancy hook.  Delete it to remove the sea;
        every parameter (sea level, colours, waves, wind, foam, buoyancy)
        is hot-editable in the Inspector."""
        # [TRIPLEA:ocean_scene_methods]
        ocean = SceneNode("Ocean", parent=self.root)
        ocean.is_ocean = True
        # Display/gizmo position only — the water surface height is driven
        # by OceanSettings.sea_level, not the node transform.
        ocean.position = glm.vec3(0.0, 0.0, 0.0)
        ocean.ocean_settings = OceanSettings()
        self._ocean_node = ocean

    @property
    def ocean(self) -> Optional["OceanSettings"]:
        """Return the active OceanSettings, or None if the Ocean node was
        deleted from the scene (no water)."""
        if self._ocean_node is not None and self._ocean_node.ocean_settings is not None:
            return self._ocean_node.ocean_settings
        return None

    def _sync_sun_direction(self):
        """Derive the SunLight direction vector from the sun node's rotation.
        The direction follows the node's forward vector (where the light shines).
        This is identical to Unity's Directional Light behaviour."""
        if self._sun_node is None:
            return
        yaw = self._sun_node.rotation_euler.y
        pitch = self._sun_node.rotation_euler.x
        cp = math.cos(math.radians(pitch))
        sp = math.sin(math.radians(pitch))
        sy = math.sin(math.radians(yaw))
        cy = math.cos(math.radians(yaw))
        # Forward vector (OpenGL convention: yaw=0,pitch=0 → -Z)
        forward = glm.vec3(-sy * cp, sp, -cy * cp)
        self._sun_node.sun_light.direction = glm.normalize(forward)

    @property
    def sun(self) -> Optional["SunLight"]:
        """Return the active SunLight, syncing direction from the sun node's
        current rotation first.  If the sun node was deleted, returns None."""
        if self._sun_node is not None and self._sun_node.sun_light is not None:
            self._sync_sun_direction()
            return self._sun_node.sun_light
        return None

    def add(self, node: SceneNode) -> SceneNode:
        return self.root.add_child(node)

    def traverse_meshes(self) -> List[Tuple[SceneNode, "Mesh", glm.mat4]]:
        """Yield (node, mesh, world_matrix) for every node with a mesh."""
        out = []
        for n in self.root.traverse():
            if n.visible and n.mesh is not None:
                out.append((n, n.mesh, n.world_matrix()))
        return out

    @staticmethod
    def forward_from_rotation(rotation_euler: glm.vec3) -> glm.vec3:
        """Return the node's Unity-style forward vector from Euler degrees.
        yaw=0, pitch=0 points down -Z — the same convention used by the Sun."""
        yaw = rotation_euler.y
        pitch = rotation_euler.x
        cp = math.cos(math.radians(pitch))
        sp = math.sin(math.radians(pitch))
        sy = math.sin(math.radians(yaw))
        cy = math.cos(math.radians(yaw))
        return glm.normalize(glm.vec3(-sy * cp, sp, -cy * cp))

    def traverse_point_lights(self) -> List[Tuple[SceneNode, "PointLight"]]:
        """Return every visible point-light node in the scene graph."""
        return [(n, n.point_light) for n in self.root.traverse()
                if n.visible and n.is_point_light and n.point_light is not None]

    def traverse_spot_lights(self) -> List[Tuple[SceneNode, "SpotLight"]]:
        """Return every visible spot-light node in the scene graph."""
        return [(n, n.spot_light) for n in self.root.traverse()
                if n.visible and n.is_spot_light and n.spot_light is not None]

    def find_by_name(self, name: str) -> Optional[SceneNode]:
        for n in self.root.traverse():
            if n.name == name:
                return n
        return None

