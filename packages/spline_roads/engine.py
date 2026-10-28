"""Spline Roads - draw roads with control points; the road follows the terrain.

Editor:  Packages > "Spline road: new road" places a road with 4 control
points in front of the camera.  Move the orange points with the gizmo and the
road re-shapes itself live (smooth Catmull-Rom curve, sits on the ground,
centre line markings, side skirts, a mesh collider so cars drive on it).
"Spline road: add point" extends the selected road; "toggle loop" opens or
closes it.  Width / smoothness / markings are blueprint properties in the
Inspector.  Saved with the project and rebuilt on load, in games too.
"""
import math

import numpy as np

H3D = None                       # the package API (set in register)
CLS = {}                         # blueprint classes registered by this package


def _bp_classes():
    m = H3D.module
    Blueprint, blueprint = m.Blueprint, m.blueprint

    @blueprint
    class SplineRoad(Blueprint):
        """A road built through its SplinePoint nodes (by 'order')."""

        def __init__(self):
            super().__init__()
            self.properties.setdefault("width", 7.0)
            self.properties.setdefault("smoothness", 10)
            self.properties.setdefault("closed", True)
            self.properties.setdefault("markings", True)
            self.properties.setdefault("follow_ground", True)

    @blueprint
    class SplinePoint(Blueprint):
        """A control point of the SplineRoad named in 'road'."""

        def __init__(self):
            super().__init__()
            self.properties.setdefault("road", "")
            self.properties.setdefault("order", 0)

    CLS.update(SplineRoad=SplineRoad, SplinePoint=SplinePoint)
    return SplineRoad, SplinePoint


# --------------------------------------------------------------------------- helpers
def _roads(engine):
    out = []
    for n in engine.scene.root.traverse():
        for bp in getattr(n, "blueprints", []) or []:
            if bp.__class__.__name__ == "SplineRoad":
                out.append((n, bp))
    return out


def _points_of(engine, road_name):
    pts = []
    for n in engine.scene.root.children:
        for bp in getattr(n, "blueprints", []) or []:
            if bp.__class__.__name__ == "SplinePoint" and bp.properties.get("road") == road_name:
                pts.append((int(bp.properties.get("order", 0)), n))
    pts.sort(key=lambda t: t[0])
    return [n for _o, n in pts]


def _catmull(p0, p1, p2, p3, t):
    t2, t3 = t * t, t * t * t
    return 0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2 + (-p0 + 3 * p1 - 3 * p2 + p3) * t3)


def _centerline(P, closed, steps):
    n = len(P)
    if n < 2:
        return P.copy()
    out = []
    segs = n if closed else n - 1
    for i in range(segs):
        p0 = P[(i - 1) % n] if (closed or i > 0) else P[0] - (P[1] - P[0])
        p1, p2 = P[i], P[(i + 1) % n]
        p3 = P[(i + 2) % n] if (closed or i + 2 < n) else p2 + (p2 - p1)
        for k in range(steps):
            out.append(_catmull(p0, p1, p2, p3, k / float(steps)))
    if closed:
        out.append(out[0])
    else:
        out.append(P[-1])
    return np.array(out, dtype=np.float64)


def _ground(engine, x, z, fallback, ignore):
    glm = H3D.module.glm
    try:
        hit = engine.raycast(glm.vec3(x, fallback + 60.0, z), glm.vec3(0, -1, 0), 400.0, ignore=ignore)
        if hit is not None:
            return float(hit.point.y)
    except Exception:
        pass
    return fallback


def build_road(engine, road, bp):
    """(Re)build the road mesh, markings and collider from its points."""
    m = H3D.module
    glm = m.glm
    pts = _points_of(engine, road.name)
    if len(pts) < 2:
        return False
    width = max(1.0, float(bp.properties.get("width", 7.0)))
    steps = int(max(2, min(40, float(bp.properties.get("smoothness", 10)))))
    closed = bool(bp.properties.get("closed", True)) and len(pts) >= 3
    follow = bool(bp.properties.get("follow_ground", True))
    P = np.array([[p.position.x, p.position.y, p.position.z] for p in pts], dtype=np.float64)
    C = _centerline(P, closed, steps)
    ignore = {id(road)} | {id(p) for p in pts} | {id(c) for c in road.children}
    hw = width * 0.5
    # tangents / right vectors in XZ
    T = np.zeros_like(C)
    T[1:-1] = C[2:] - C[:-2]
    T[0] = (C[1] - C[-2]) if closed else (C[1] - C[0])
    T[-1] = T[0] if closed else (C[-1] - C[-2])
    T[:, 1] = 0.0
    T /= np.maximum(np.linalg.norm(T, axis=1, keepdims=True), 1e-9)
    R = np.stack([-T[:, 2], np.zeros(len(T)), T[:, 0]], axis=1)       # right = tangent x up
    L = C - R * hw
    Rr = C + R * hw
    if follow:
        for arr in (L, Rr):
            for i in range(len(arr)):
                arr[i, 1] = _ground(engine, arr[i, 0], arr[i, 2], arr[i, 1], ignore) + 0.06
        C[:, 1] = (L[:, 1] + Rr[:, 1]) * 0.5
    # world -> road-local
    inv = glm.inverse(road.world_matrix())

    def local(a):
        out = np.empty_like(a, dtype=np.float32)
        for i, v in enumerate(a):
            q = inv * glm.vec4(float(v[0]), float(v[1]), float(v[2]), 1.0)
            out[i] = (q.x, q.y, q.z)
        return out
    n = len(C)
    dist = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(C, axis=0), axis=1))])
    skirt = 0.6
    Ls, Rs = L.copy(), Rr.copy()
    Ls[:, 1] -= skirt
    Rs[:, 1] -= skirt
    pos = np.concatenate([L, Rr, Ls, Rs])
    nrm = np.zeros((4 * n, 3), dtype=np.float32)
    nrm[:2 * n, 1] = 1.0
    nrm[2 * n:3 * n] = -R
    nrm[3 * n:] = R
    uv = np.zeros((4 * n, 2), dtype=np.float32)
    v = dist / width
    uv[:n, 0], uv[n:2 * n, 0] = 0.0, 1.0
    uv[:n, 1] = uv[n:2 * n, 1] = uv[2 * n:3 * n, 1] = uv[3 * n:, 1] = v
    uv[2 * n:3 * n, 0], uv[3 * n:, 0] = 0.0, 1.0
    idx = []
    for i in range(n - 1):
        a, b, c, d = i, i + 1, n + i, n + i + 1                 # top surface
        idx += [a, c, b, b, c, d]
        a, b, c, d = 2 * n + i, 2 * n + i + 1, i, i + 1        # left skirt
        idx += [a, c, b, b, c, d]
        a, b, c, d = n + i, n + i + 1, 3 * n + i, 3 * n + i + 1  # right skirt
        idx += [a, c, b, b, c, d]
    old = road.mesh
    road.mesh = m.Mesh.from_arrays(engine.ctx, local(pos), nrm, uv, np.array(idx, dtype=np.uint32), name="spline_road")
    road.mesh_type = None
    if road.material is None:
        road.material = m.Material(name="Asphalt", base_color=(0.16, 0.165, 0.18, 1.0), metallic=0.0, roughness=0.88)
    if road.collider is None or road.collider.__class__.__name__ != "MeshCollider":
        road.collider = m.MeshCollider()
    _release(old)
    _build_markings(engine, road, C, R, dist, bp, local)
    try:
        engine._physics_reregister(road)
    except Exception:
        pass
    return True


def _build_markings(engine, road, C, R, dist, bp, local):
    m = H3D.module
    marks = None
    for ch in road.children:
        if ch.name == "Road Markings":
            marks = ch
    if not bp.properties.get("markings", True):
        if marks is not None:
            marks.visible = False
        return
    if marks is None:
        marks = m.SceneNode("Road Markings", parent=road)
        marks.material = m.Material(name="Paint", base_color=(0.95, 0.93, 0.86, 1.0), roughness=0.6,
                                    emissive=(0.12, 0.12, 0.1))
    marks.visible = True
    dash, gap, w = 3.0, 3.0, 0.16
    pos, idx = [], []
    total = float(dist[-1])
    s = 1.0
    while s + dash < total:
        for a_s in (s, s + dash):
            i = int(np.searchsorted(dist, a_s))
            i = min(max(i, 1), len(C) - 1)
            f = (a_s - dist[i - 1]) / max(dist[i] - dist[i - 1], 1e-9)
            c = C[i - 1] + (C[i] - C[i - 1]) * f
            r = R[i - 1]
            pos.append(c - r * w + [0, 0.025, 0])
            pos.append(c + r * w + [0, 0.025, 0])
        k = len(pos) - 4
        idx += [k, k + 1, k + 2, k + 2, k + 1, k + 3]
        s += dash + gap
    old = marks.mesh
    if pos:
        P = local(np.array(pos))
        marks.mesh = m.Mesh.from_arrays(engine.ctx, P, np.tile([0, 1, 0], (len(P), 1)).astype(np.float32),
                                        None, np.array(idx, dtype=np.uint32), name="road_markings")
    _release(old)


def _release(mesh):
    try:
        if mesh is not None:
            mesh.release()
    except Exception:
        pass


def _signature(engine, road, bp):
    pts = _points_of(engine, road.name)
    return (tuple((round(p.position.x, 3), round(p.position.y, 3), round(p.position.z, 3)) for p in pts),
            tuple(sorted((k, str(v)) for k, v in bp.properties.items())),
            tuple(round(float(x), 3) for x in road.position), tuple(round(float(x), 3) for x in road.rotation_euler))


# --------------------------------------------------------------------------- editor tools
def _point(engine, road, order, pos):
    m = H3D.module
    p = engine.add_sphere(radius=0.45, name=f"{road.name} P{order + 1}", solid=False)
    p.position = m.glm.vec3(pos)
    p.material.base_color = (1.0, 0.55, 0.12, 1.0)
    p.material.emissive = (0.5, 0.22, 0.02)
    engine.attach_blueprint(p, CLS["SplinePoint"])
    p.blueprints[-1].properties.update({"road": road.name, "order": order})
    return p


def new_road(engine):
    m = H3D.module
    glm = m.glm
    cam = engine.camera
    f = cam.forward()
    f = glm.normalize(glm.vec3(f.x, 0.0, f.z)) if (abs(f.x) + abs(f.z)) > 1e-6 else glm.vec3(0, 0, -1)
    center = cam.position + f * 40.0
    center.y = _ground(engine, center.x, center.z, 0.0, set())
    name = engine._unique_node_name("Road")
    road = m.SceneNode(name, parent=engine.scene.root)
    engine.attach_blueprint(road, CLS["SplineRoad"])
    r = glm.vec3(-f.z, 0, f.x)
    for i, (a, b) in enumerate(((-1, -1), (1, -1), (1, 1), (-1, 1))):
        _point(engine, road, i, center + f * (a * 22.0) + r * (b * 22.0))
    build_road(engine, road, road.blueprints[-1])
    engine.select_node(road)
    H3D.log(f"'{name}' created - move its orange points to shape it")


def _selected_road(engine):
    sel = getattr(engine, "selected", None)
    if sel is None:
        return None, None
    for bp in getattr(sel, "blueprints", []) or []:
        if bp.__class__.__name__ == "SplineRoad":
            return sel, bp
        if bp.__class__.__name__ == "SplinePoint":
            for road, rbp in _roads(engine):
                if road.name == bp.properties.get("road"):
                    return road, rbp
    return None, None


def add_point(engine):
    road, bp = _selected_road(engine)
    if road is None:
        H3D.warn("select a road (or one of its points) first")
        return
    pts = _points_of(engine, road.name)
    glm = H3D.module.glm
    if len(pts) >= 2:
        a, b = pts[-2].position, pts[-1].position
        pos = b + (b - a) * 0.6
    else:
        pos = pts[-1].position + glm.vec3(10, 0, 0)
    _point(engine, road, len(pts), pos)
    build_road(engine, road, bp)


def toggle_loop(engine):
    road, bp = _selected_road(engine)
    if road is None:
        H3D.warn("select a road (or one of its points) first")
        return
    bp.properties["closed"] = not bp.properties.get("closed", True)
    build_road(engine, road, bp)


# --------------------------------------------------------------------------- lifecycle
def _rebuild_all(engine, show_points):
    for road, bp in _roads(engine):
        try:
            build_road(engine, road, bp)
            road._srd_sig = _signature(engine, road, bp)
        except Exception as ex:
            H3D.warn(f"could not build {road.name}: {ex}")
        for p in _points_of(engine, road.name):
            p.visible = show_points


def register(h3d):
    global H3D
    H3D = h3d
    _bp_classes()
    state = {"t": 0.0}

    def on_update(engine, dt):
        if engine.mode != "editor" or engine._playing:
            return
        state["t"] += dt
        if state["t"] < 0.08:                 # live rebuild, ~12x per second at most
            return
        state["t"] = 0.0
        for road, bp in _roads(engine):
            sig = _signature(engine, road, bp)
            if getattr(road, "_srd_sig", None) != sig:
                road._srd_sig = sig
                try:
                    build_road(engine, road, bp)
                except Exception as ex:
                    h3d.warn(f"rebuild failed: {ex}")

    h3d.on("update", on_update)
    h3d.on("init", lambda e: _rebuild_all(e, e.mode == "editor"))
    h3d.on("project_loaded", lambda e, p: _rebuild_all(e, e.mode == "editor" and not e._playing))
    h3d.on("play_start", lambda e: _rebuild_all(e, False))
    h3d.on("play_stop", lambda e: _rebuild_all(e, True))
    h3d.add_tool("Spline road: new road", new_road, "#2E7CD6")
    h3d.add_tool("Spline road: add point", add_point)
    h3d.add_tool("Spline road: open / close loop", toggle_loop)
