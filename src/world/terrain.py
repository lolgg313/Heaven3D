# @heaven3d-part world/terrain.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# #############################################################################
# SECTION 14 —  TERRAIN  (GameGuru-style heightmap)
# #############################################################################

def _heightmap_noise(resolution: int, seed: int = 42,
                     height_scale: float = 10.0) -> np.ndarray:
    n = resolution + 1
    xs = np.linspace(0, 1, n, dtype=np.float32)
    zs = np.linspace(0, 1, n, dtype=np.float32)
    X, Z = np.meshgrid(xs, zs)
    heights = np.zeros_like(X)
    amplitude = 1.0
    frequency = 2.0
    rng = np.random.RandomState(seed)
    for octave in range(6):
        ph_x = rng.uniform(0, math.pi * 2)
        ph_z = rng.uniform(0, math.pi * 2)
        wave = (np.sin(X * frequency * math.pi * 2 + ph_x) *
                np.cos(Z * frequency * math.pi * 2 + ph_z))
        heights += wave * amplitude
        amplitude *= 0.5
        frequency *= 2.0
    h_min, h_max = float(heights.min()), float(heights.max())
    if h_max - h_min > 1e-8:
        heights = (heights - h_min) / (h_max - h_min)
    return heights * height_scale


class Terrain:
    def __init__(self, ctx: moderngl.Context, size: float = 200.0,
                 resolution: int = 128, height_scale: float = 10.0,
                 seed: int = 42):
        self.ctx = ctx
        self.size = float(size)
        self.resolution = int(resolution)
        self.height_scale = float(height_scale)
        self.seed = int(seed)
        self.heights: Optional[np.ndarray] = None
        self.mesh: Optional[Mesh] = None

    def generate(self):
        self.heights = _heightmap_noise(self.resolution, self.seed,
                                        self.height_scale)
        self._build_mesh()

    def _build_mesh(self):
        if self.heights is None:
            return
        h, w = self.heights.shape
        step = self.size / max(self.resolution, 1)
        origin = -self.size * 0.5
        positions = np.zeros((h * w, 3), dtype=np.float32)
        for zi in range(h):
            for xi in range(w):
                idx = zi * w + xi
                positions[idx, 0] = origin + xi * step
                positions[idx, 1] = float(self.heights[zi, xi])
                positions[idx, 2] = origin + zi * step
        # normals via finite differences
        normals = np.zeros_like(positions)
        for zi in range(h):
            for xi in range(w):
                xL = self.heights[zi, max(0, xi - 1)]
                xR = self.heights[zi, min(w - 1, xi + 1)]
                zU = self.heights[max(0, zi - 1), xi]
                zD = self.heights[min(h - 1, zi + 1), xi]
                nx = (xL - xR)
                nz = (zU - zD)
                ny = 2.0 * step
                length = math.sqrt(nx*nx + ny*ny + nz*nz) + 1e-8
                idx = zi * w + xi
                normals[idx] = (nx/length, ny/length, nz/length)
        uvs = np.zeros((h * w, 2), dtype=np.float32)
        for zi in range(h):
            for xi in range(w):
                idx = zi * w + xi
                uvs[idx] = ((xi * step) * 0.1, (zi * step) * 0.1)
        # indices
        indices = []
        for zi in range(h - 1):
            for xi in range(w - 1):
                a = zi * w + xi
                b = a + 1
                c = a + w
                d = c + 1
                indices.extend([a, c, b, b, c, d])
        indices = np.array(indices, dtype=np.uint32)
        self.mesh = Mesh.from_arrays(self.ctx, positions, normals, uvs, indices,
                                     "terrain")
        Log.info(f"Terrain built: {self.resolution}x{self.resolution}")

    def attach_to(self, node: SceneNode):
        if self.mesh:
            node.mesh = self.mesh
            node.is_terrain = True




import random  # used by gameplay blueprints (spread, patrols, spawns)
