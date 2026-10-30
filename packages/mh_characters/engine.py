"""MakeHuman Characters - MPFB2 / MakeHuman humans inside Heaven3D.

Characters are generated in the engine from the shipped CC0 MakeHuman asset
library (no Blender needed): body morphs, skin, eyes, hair, eyebrows and 250+
CC0 clothes, rigged with MPFB's Mixamo skeleton and GPU-skinned.  Mixamo
(.fbx), glTF (.glb/.gltf) and BVH animations play on them through automatic
retargeting; built-in procedural idle/walk/run/attack/hit/death clips are used
for any slot you leave empty, so characters move out of the box.

Blueprints
  MHCharacter   the character itself (look, animations, foot IK)
  NPCWalker     walks an NPC path (loop / ping-pong / random), waits at points
  MHEnemy       patrols, sees / hears the player, chases, attacks, takes damage, dies
  NPCPath / NPCWaypoint   the path walked by NPCs and patrolled by enemies

Editor: select a character to open the Character Creator panel.  Packages menu:
new character / NPC walker / enemy / crowd, NPC paths, animation assignment.
"""
import json
import math
import os
import random
import time

import numpy as np

H3D = None
CORE = None
ANIM = None
LIB = None
CLS = {}
UNIT = 11                         # texture unit of the bone palette
STATE = {"creator": None, "drag": None, "dirty": {}, "tab": "Body", "clothes_slot": "all",
         "clothes_gender": "fit", "style": "civilian", "warned": set(), "list_page": {}}
SLOT_KEYWORDS = {
    "idle": ("idle", "breath", "stand"), "walk": ("walk",), "run": ("run", "jog", "sprint"),
    "attack": ("punch", "attack", "slash", "swing", "kick", "melee", "hook", "jab", "strike"),
    "shoot": ("shoot", "firing", "fire", "rifle", "pistol", "gun"), "hit": ("hit", "react", "impact", "damage"),
    "death": ("death", "dying", "die", "dead"), "wave": ("wave", "waving", "greet", "talk")}


def _m():
    return H3D.module


def _lib():
    global LIB
    if LIB is None:
        LIB = CORE.Library(H3D.asset("assets"))
    return LIB


def _warn_once(key, msg):
    if key not in STATE["warned"]:
        STATE["warned"].add(key)
        H3D.warn(msg)


# =========================================================================== shaders
_SKIN_GLSL = """
uniform int u_mh_skin;
uniform sampler2D u_mh_bones;
in vec4 in_mh_joints;
in vec4 in_mh_weights;
mat4 mh_bone(int i){
    return mat4(texelFetch(u_mh_bones, ivec2(i * 4 + 0, 0), 0), texelFetch(u_mh_bones, ivec2(i * 4 + 1, 0), 0),
                texelFetch(u_mh_bones, ivec2(i * 4 + 2, 0), 0), texelFetch(u_mh_bones, ivec2(i * 4 + 3, 0), 0));
}
mat4 mh_skin_matrix(){
    if (u_mh_skin != 1) return mat4(1.0);
    ivec4 j = ivec4(in_mh_joints + vec4(0.5));
    vec4 w = in_mh_weights;
    return w.x * mh_bone(j.x) + w.y * mh_bone(j.y) + w.z * mh_bone(j.z) + w.w * mh_bone(j.w);
}
"""


def _patch_vertex(src, shadow=False):
    if "mh_skin_matrix" in src:
        return src, True
    anchor = "uniform mat4 u_model;"
    if anchor not in src:
        return src, False
    out = src.replace(anchor, anchor + _SKIN_GLSL, 1)
    if shadow:
        a = "gl_Position = u_light_space * u_model * vec4(in_position, 1.0);"
        if a not in out:
            return src, False
        return out.replace(a, "gl_Position = u_light_space * u_model * (mh_skin_matrix() * vec4(in_position, 1.0));"), True
    a1 = "vec4 wp = u_model * vec4(in_position, 1.0);"
    a2 = "transpose(inverse(mat3(u_model))) * in_normal;"
    a3 = "vec4(mat3(u_model) * in_tangent.xyz, in_tangent.w);"
    if a1 not in out or a2 not in out or a3 not in out:
        return src, False
    out = out.replace(a1, "mat4 mh_S = mh_skin_matrix();\n    vec4 wp = u_model * (mh_S * vec4(in_position, 1.0));", 1)
    out = out.replace(a2, "transpose(inverse(mat3(u_model))) * (mat3(mh_S) * in_normal);", 1)
    out = out.replace(a3, "vec4(mat3(u_model) * (mat3(mh_S) * in_tangent.xyz), in_tangent.w);", 1)
    return out, True


def _patch_shaders():
    m = _m()
    ok = []
    for name, shadow in (("GEOMETRY_VERT", False), ("FORWARD_VERT", False), ("SHADOW_VERT", True)):
        src = getattr(m, name, None)
        if not isinstance(src, str):
            ok.append(False)
            continue
        new, good = _patch_vertex(src, shadow)
        setattr(m, name, new)
        ok.append(good)
    for k in ("geometry", "forward", "shadow"):
        m.ShaderLib._cache.pop(k, None)
    if not all(ok):
        H3D.warn(f"skinning shader hooks partially applied {ok} - characters may render in their rest pose")
    return all(ok)


# =========================================================================== GPU skinned meshes
class _GeoPart:
    """GPU buffers of one character part, shared by every identical character."""
    __slots__ = ("vbo", "ibo", "skin_vbo", "nv", "ni", "bbox", "bsphere", "cpu_pos", "cpu_idx", "material",
                 "slot", "pid")


def _make_geo(ctx, part, height):
    m = _m()
    tmp = m.Mesh.from_arrays(ctx, part.positions, part.normals, part.uvs, part.indices, name="mh_tmp")
    g = _GeoPart()
    g.vbo, g.ibo = tmp.vbo, tmp.ibo
    tmp.vbo = tmp.ibo = None
    g.nv, g.ni = tmp.vertex_count, tmp.index_count
    g.cpu_pos, g.cpu_idx = tmp.cpu_positions, tmp.cpu_indices
    sk = np.hstack([part.joints, part.weights]).astype(np.float32)
    g.skin_vbo = ctx.buffer(sk.tobytes())
    glm = m.glm
    h = max(1.0, height)
    g.bbox = (glm.vec3(-h * 0.9, -0.25, -h * 0.9), glm.vec3(h * 0.9, h + 0.3, h * 0.9))
    g.bsphere = (glm.vec3(0.0, h * 0.5, 0.0), h * 1.1)
    g.material, g.slot, g.pid = part.material, part.slot, part.pid
    return g


def _mesh_classes():
    m = _m()
    moderngl = __import__("moderngl")

    class _SkinVAO:
        __slots__ = ("vao", "prog", "mesh")

        def __init__(self, vao, prog, mesh):
            self.vao, self.prog, self.mesh = vao, prog, mesh

        def render(self, *a, **kw):
            rig = self.mesh._mh_rig
            prog = self.prog
            on = rig is not None and rig.tex is not None
            if on:
                try:
                    rig.tex.use(location=UNIT)
                    prog["u_mh_bones"].value = UNIT
                    prog["u_mh_skin"].value = 1
                except KeyError:
                    on = False
            try:
                return self.vao.render(*a, **kw)
            finally:
                if on:
                    prog["u_mh_skin"].value = 0

        def release(self):
            try:
                self.vao.release()
            except Exception:
                pass

        def __getattr__(self, name):
            return getattr(self.vao, name)

    class SkinnedMesh(m.Mesh):
        """A Mesh drawing shared GPU buffers, skinned on the GPU by its rig."""

        def __init__(self, ctx, geo, name):
            super().__init__(ctx, name)
            self.vbo, self.ibo = geo.vbo, geo.ibo
            self.vertex_count, self.index_count, self.indexed = geo.nv, geo.ni, True
            self.cpu_positions, self.cpu_indices = geo.cpu_pos, geo.cpu_idx
            self.bounding_box, self.bounding_sphere = geo.bbox, geo.bsphere
            self._mh_skin_vbo = geo.skin_vbo
            self._mh_rig = None

        def get_vao(self, program):
            key = ("mh", id(program))
            v = self._vaos.get(key)
            if v is not None:
                return v
            attrs = set(program._members.keys()) if hasattr(program, "_members") else set()
            fmt, names = [], []
            for nm, n in (("in_position", 3), ("in_normal", 3), ("in_tangent", 4), ("in_uv", 2)):
                if nm in attrs:
                    fmt.append(f"{n}f")
                    names.append(nm)
                else:
                    fmt.append(f"{n}x4")
            content = [(self.vbo, " ".join(fmt), *names)]
            if "in_mh_joints" in attrs and "in_mh_weights" in attrs:
                content.append((self._mh_skin_vbo, "4f 4f", "in_mh_joints", "in_mh_weights"))
            vao = self.ctx.vertex_array(program, content, index_buffer=self.ibo)
            v = _SkinVAO(vao, program, self)
            self._vaos[key] = v
            return v

        def release(self):
            for v in list(self._vaos.values()):
                try:
                    v.release()
                except Exception:
                    pass
            self._vaos.clear()
            self.vbo = self.ibo = None       # shared: owned by the geometry cache

    CLS["SkinnedMesh"] = SkinnedMesh
    CLS["moderngl"] = moderngl


# =========================================================================== model cache
_MODELS = {}         # spec key -> (model, [geo], procedural clips, extras)
_ORDER = []


def _model_for(engine, spec):
    key = CORE.spec_key(CORE.normalize_spec(spec, _lib()))
    hit = _MODELS.get(key)
    if hit is not None:
        return hit
    t0 = time.perf_counter()
    model = CORE.build(_lib(), spec)
    geos = [_make_geo(engine.ctx, p, model.height) for p in model.parts]
    hit = (model, geos, None, {})
    _MODELS[key] = hit
    _ORDER.append(key)
    if len(_ORDER) > 48:                    # keep the cache bounded
        old = _ORDER.pop(0)
        _MODELS.pop(old, None)
    H3D.log(f"character built in {1000 * (time.perf_counter() - t0):.0f} ms "
            f"({sum(g.nv for g in geos):,} verts, {len(geos)} parts)")
    return hit


def _library():
    return ANIM.load_library(H3D.asset("assets/anims/library.npz"))


def _procedural(model):
    c = getattr(model, "_procedural", None)
    if c is None:
        c = model._procedural = {k: [v] for k, v in ANIM.procedural_clips(model).items()}
    return c


def _box(lo, hi):
    """Axis-aligned box (positions, normals, indices)."""
    x0, y0, z0 = lo
    x1, y1, z1 = hi
    faces = [((1, 0, 0), [(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)]),
             ((-1, 0, 0), [(x0, y0, z1), (x0, y1, z1), (x0, y1, z0), (x0, y0, z0)]),
             ((0, 1, 0), [(x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (x1, y1, z0)]),
             ((0, -1, 0), [(x0, y0, z1), (x0, y0, z0), (x1, y0, z0), (x1, y0, z1)]),
             ((0, 0, 1), [(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]),
             ((0, 0, -1), [(x1, y0, z0), (x0, y0, z0), (x0, y1, z0), (x1, y1, z0)])]
    P, N, I = [], [], []
    for n, quad in faces:
        b = len(P)
        P += quad
        N += [n] * 4
        I += [b, b + 1, b + 2, b, b + 2, b + 3]
    return np.array(P, np.float64), np.array(N, np.float64), I


# pistol in its own frame: origin = top of the grip (in the palm), barrel along -Z, +Y up
_PISTOL = [((-0.016, 0.000, -0.170), (0.016, 0.040, 0.035)),     # slide
           ((-0.013, -0.105, -0.010), (0.013, 0.000, 0.035)),    # grip
           ((-0.004, -0.032, -0.055), (0.004, -0.004, -0.045)),  # trigger guard front
           ((-0.004, -0.034, -0.050), (0.004, -0.028, -0.008)),  # trigger guard bottom
           ((-0.009, 0.006, -0.200), (0.009, 0.026, -0.170))]    # barrel tip
_MUZZLE = np.array([0.0, 0.016, -0.205, 1.0])


def _gun_grip(model, library):
    """Pistol frame relative to the right hand bone, from the aiming pose."""
    g = getattr(model, "_gun_grip", None)
    if g is not None:
        return g
    hand = model.bone("mixamorig:RightHand")
    fore = model.bone("mixamorig:RightForeArm")
    pose = ANIM.Pose(model)
    src = library.get("Pistol_Aim_Neutral") or library.get("Pistol_Idle_Loop")
    if src is not None:
        clip = ANIM.retarget(src, model, loop=False)
        G = pose.fk(clip.rot[0], clip.hips[0]).copy()
    else:
        G = model.rest_world.copy()
    Hm = G[hand]
    hand_dir = Hm[:3, 3] - G[fore, :3, 3]
    hand_dir /= max(np.linalg.norm(hand_dir), 1e-6)
    grip_pt = Hm[:3, 3] + hand_dir * 0.055 + np.array([0.0, -0.012, 0.0])
    fwd = np.array([hand_dir[0], 0.0, hand_dir[2]])
    if np.linalg.norm(fwd) < 1e-3:
        fwd = np.array([0.0, 0.0, -1.0])
    fwd /= np.linalg.norm(fwd)
    z = -fwd                                      # barrel along -Z of the gun frame
    y = np.array([0.0, 1.0, 0.0])
    x = np.cross(y, z)
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    Gw = np.eye(4)
    Gw[:3, 0], Gw[:3, 1], Gw[:3, 2], Gw[:3, 3] = x, y, z, grip_pt
    g = model._gun_grip = np.linalg.inv(Hm) @ Gw
    return g


def _gun_geo(engine, model, library):
    """A pistol as a part skinned 100% to the right hand."""
    hit = getattr(model, "_gun_geo", None)
    if hit is not None:
        return hit
    hand = model.bone("mixamorig:RightHand")
    M = model.rest_world[hand] @ _gun_grip(model, library)
    P, N, I = [], [], []
    for lo, hi in _PISTOL:
        p, n, i = _box(lo, hi)
        I += [k + len(P) for k in i]
        P += list(p)
        N += list(n)
    P = np.array(P)
    N = np.array(N)
    Pw = (np.hstack([P, np.ones((len(P), 1))]) @ M.T)[:, :3]
    Nw = N @ M[:3, :3].T
    part = CORE.Part("gun/pistol", "weapon", Pw.astype(np.float32), Nw.astype(np.float32),
                     np.zeros((len(P), 2), np.float32), np.array(I, np.uint32),
                     np.tile([float(hand), 0, 0, 0], (len(P), 1)).astype(np.float32),
                     np.tile([1.0, 0, 0, 0], (len(P), 1)).astype(np.float32),
                     {"diffuse": None, "normal": None, "color": [0.09, 0.09, 0.1, 1.0], "alpha_mode": "OPAQUE",
                      "double_sided": False, "roughness": 0.35, "cutoff": 0.5})
    g = _make_geo(engine.ctx, part, model.height)
    model._gun_geo = g
    return g


def desired_moveset(node, bp):
    ms = str(bp.properties.get("moveset") or "auto")
    if ms != "auto":
        return ms
    en = _bp(node, "MHEnemy")
    spec = bp.properties.get("character") or {}
    if "zombie" in str(spec.get("skin", "")):
        return "zombie"
    if en is not None:
        return "pistol" if en.properties.get("attack_style") == "hitscan" else "brawler"
    return "civilian"


# =========================================================================== rig
class Rig:
    """Runtime of one character node: part nodes, animation, pose, foot IK."""

    def __init__(self, engine, node, bp):
        m = _m()
        self.engine, self.node, self.bp = engine, node, bp
        spec = bp.properties.get("character") or CORE.default_spec()
        self.spec_key = CORE.spec_key(CORE.normalize_spec(spec, _lib()))
        self.model, self.geos, _unused, self.retarget_cache = _model_for(engine, spec)
        self.height = self.model.height
        self.half = self.height * 0.5
        nb = len(self.model.bone_names)
        self.tex = engine.ctx.texture((nb * 4, 1), 4, dtype="f4")
        mgl = CLS["moderngl"]
        self.tex.filter = (mgl.NEAREST, mgl.NEAREST)
        self.pose = ANIM.Pose(self.model)
        self.user_clips = {}
        self.seed = (abs(hash(node.name)) % 100003) + random.randint(0, 99)
        self.parts = []
        for g in self.geos:
            self.parts.append(self._add_part(g))
        self.gun = None
        self.moveset = None
        self.set_moveset(desired_moveset(node, bp))
        self.acc = 0.0
        self.frame = random.randint(0, 7)
        self._ik_cache = {}
        self.vis_off = 0.0
        self._vis_y, self._vis_v = 0.0, 0.0
        self._last_wy = None
        self.preview = None
        self.update(0.0, force=True)

    def _add_part(self, g):
        m = _m()
        ch = m.SceneNode(f"{self.node.name} {g.slot}", parent=self.node)
        ch._heaven3d_imported_part = "mh"
        ch._h3d_locked = True
        ch.position = m.glm.vec3(0.0, -self.half, 0.0)
        mesh = CLS["SkinnedMesh"](self.engine.ctx, g, f"mh_{g.slot}")
        mesh._mh_rig = self
        ch.mesh = mesh
        mat = g.material
        ch.material = m.Material(name=f"MH {g.slot}", base_color=tuple(mat["color"]),
                                 metallic=0.6 if g.slot == "weapon" else 0.0,
                                 roughness=float(mat["roughness"]), base_color_tex=mat["diffuse"],
                                 normal_tex=mat["normal"], double_sided=bool(mat["double_sided"]),
                                 alpha_mode=mat["alpha_mode"], alpha_cutoff=float(mat["cutoff"]))
        return ch

    def set_moveset(self, ms):
        """(Re)build the clip set + animator for a moveset (civilian, brawler, pistol, zombie, sword)."""
        self.moveset = ms
        lib = _library()
        overrides = self._load_user_clips()
        if lib:
            clips = ANIM.clipset(self.model, ms, self.seed, lib, overrides)
        else:
            clips = dict(_procedural(self.model))
            clips.update({k: [v] for k, v in overrides.items()})
        for slot in ("idle", "walk", "run"):
            if slot not in clips:
                clips[slot] = _procedural(self.model)[slot]
        self.clips = clips
        self.animator = ANIM.Animator(self.model, clips, seed=self.seed)
        if not hasattr(self.model, "_arm_clear"):
            idle = clips["idle"][0]
            try:
                self.model._arm_clear = ANIM.arm_clearance(self.model, idle)
            except Exception:
                self.model._arm_clear = {}
        self.pose.set_arm_clearance(self.model._arm_clear)
        want_gun = ms == "pistol" and bool(lib)
        if want_gun and self.gun is None:
            self.gun = self._add_part(_gun_geo(self.engine, self.model, lib))
            self.parts.append(self.gun)
        elif not want_gun and self.gun is not None:
            try:
                self.gun.mesh.release()
            except Exception:
                pass
            if self.gun.parent is not None:
                self.gun.remove()
            self.parts.remove(self.gun)
            self.gun = None
        if self.animator.has("upper"):
            self.animator.set_overlay("upper")
            if self.animator.overlay:
                self.animator.overlay[1] = 1.0

    def muzzle_world(self):
        """World position of the pistol muzzle (or the right hand)."""
        glm = _m().glm
        hand = self.model.bone("mixamorig:RightHand")
        G = self.pose.G
        loc = G[hand] @ _gun_grip(self.model, _library()) @ _MUZZLE if self.gun is not None else G[hand][:, 3]
        M = self.node.world_matrix() * glm.translate(glm.mat4(1.0), glm.vec3(0.0, -self.half + self.vis_off, 0.0))
        return glm.vec3(M * glm.vec4(float(loc[0]), float(loc[1]), float(loc[2]), 1.0))

    # -- animation sources ------------------------------------------------
    def _resolve(self, path):
        if not path:
            return None
        if os.path.isabs(path) and os.path.isfile(path):
            return path
        pp = getattr(self.engine, "project_path", None)
        bases = [os.path.dirname(os.path.abspath(pp))] if pp else []
        bases += [os.getcwd(), H3D.path]
        for b in bases:
            p = os.path.join(b, path)
            if os.path.isfile(p):
                return p
        return None

    def _load_user_clips(self):
        out = {}
        self.user_clips = {}
        anims = self.bp.properties.get("animations") or {}
        for slot, ref in anims.items():
            if not ref:
                continue
            path, which = ANIM.split_ref(ref)
            full = self._resolve(path)
            if full is None:
                _warn_once(("missing", path), f"animation file not found: {path}")
                continue
            try:
                st = os.stat(full)
                key = (full, which, st.st_mtime, slot)
                clip = self.retarget_cache.get(key)
                if clip is None:
                    src = ANIM.pick(ANIM.load_source_cached(full), which)
                    loop = slot in ANIM.LOOP_SLOTS
                    clip = ANIM.retarget(src, self.model, name=slot, loop=loop, in_place=True)
                    ANIM.ground_fit(self.model, clip)
                    if slot in ("attack", "shoot"):
                        clip.events = {"hit": 0.45}
                    self.retarget_cache[key] = clip
                out[slot] = clip
                self.user_clips[slot] = ref
            except Exception as ex:
                _warn_once(("bad", full), f"could not use animation '{os.path.basename(full)}' for {slot}: {ex}")
        return out

    # -- per frame --------------------------------------------------------
    def update(self, dt, force=False):
        e = self.engine
        node = self.node
        self.acc += dt
        self.frame += 1
        try:
            cam = e.camera.position
            p = node.world_position()
            d = math.sqrt((cam.x - p.x) ** 2 + (cam.y - p.y) ** 2 + (cam.z - p.z) ** 2)
        except Exception:
            p, d = None, 0.0
        # smooth out instant capsule pops (stairs / step-ups) so the body glides up
        # (critically damped follow: no velocity jumps, so no visible stutter)
        if p is not None and e._playing and dt > 0.0:
            if self._last_wy is None or abs(p.y - self._last_wy) > 0.6:
                self._vis_y, self._vis_v = p.y, 0.0          # spawn / teleport / big fall: snap
            else:
                st = 0.18
                om = 2.0 / st
                x = om * min(dt, 0.1)
                ex = 1.0 / (1.0 + x + 0.48 * x * x + 0.235 * x * x * x)
                ch = self._vis_y - p.y
                tmp = (self._vis_v + om * ch) * min(dt, 0.1)
                self._vis_v = (self._vis_v - om * tmp) * ex
                self._vis_y = p.y + (ch + tmp) * ex
            self._last_wy = p.y
            self.vis_off = max(-0.6, min(0.6, self._vis_y - p.y))
            if abs(self.vis_off) < 1e-4 and abs(self._vis_v) < 1e-3:
                self.vis_off = 0.0
        elif not e._playing:
            self._last_wy, self.vis_off, self._vis_v = None, 0.0, 0.0
        y = -self.half + self.vis_off
        for ch in self.parts:
            ch.position.y = y
        step = 1 if d < 40 else (2 if d < 80 else 4)       # animation LOD (smooth up close)
        if not force and (self.frame % step) != 0:
            return
        if not node.visible and not force:
            return
        dt_eff, self.acc = self.acc, 0.0
        q, h = self.animator.update(dt_eff)
        self.pose.fk(q, h)
        if (bool(self.bp.properties.get("foot_ik", True)) and e._playing and d < 30.0
                and not self.animator.dead):
            self._foot_ik(dt_eff, d)
        skin = self.pose.skin()
        self.tex.write(np.ascontiguousarray(skin.transpose(0, 2, 1), dtype=np.float32).tobytes())

    def _foot_ik(self, dt, dist):
        m = _m()
        glm = m.glm
        node = self.node
        M = node.world_matrix() * glm.translate(glm.mat4(1.0), glm.vec3(0.0, -self.half + self.vis_off, 0.0))
        Mi = glm.inverse(M)
        e = self.engine
        fresh = dist < 18.0 or self.frame % 2 == 0
        cache = self._ik_cache

        def ground(k, x, z, y_start):
            if not fresh and k in cache:
                return cache[k]
            wp = M * glm.vec4(x, y_start + 0.6, z, 1.0)
            hit = _ground_ray(e, wp.x, wp.y, wp.z, y_start + 1.4, id(node))
            res = None
            if hit is not None:
                lp = Mi * glm.vec4(hit.point, 1.0)
                ln = Mi * glm.vec4(hit.normal, 0.0)
                if abs(lp.y) <= 0.6:                    # ignore ledges / holes
                    res = (float(lp.y), (float(ln.x), float(ln.y), float(ln.z)))
            cache[k] = res
            return res
        self.pose.foot_ik(ground, dt)

    def destroy(self):
        for ch in self.parts:
            try:
                if ch.mesh is not None:
                    ch.mesh.release()
            except Exception:
                pass
            if ch.parent is not None:
                ch.remove()
        self.parts = []
        try:
            self.tex.release()
        except Exception:
            pass
        self.tex = None


_GROUND = {"t": -10.0, "items": [], "all": set()}


def _ground_items(engine):
    """Cached xz bounds of the colliders feet can stand on (refreshed every second)."""
    g = _GROUND
    now = float(getattr(engine, "time", 0.0))
    if now - g["t"] < 1.0 and g["items"]:
        return g
    items, ids = [], set()
    for ent in getattr(engine.physics, "entries", []):
        n = ent.node
        ids.add(id(n))
        if ent.collider.is_trigger or ent.collider.__class__.__name__ == "CapsuleCollider":
            continue
        try:
            ent.refresh_aabb()
            lo, hi = ent.aabb0, ent.aabb1
            items.append((id(n), lo.x - 0.05, lo.z - 0.05, hi.x + 0.05, hi.z + 0.05))
        except Exception:
            items.append((id(n), -1e9, -1e9, 1e9, 1e9))
    g.update(t=now, items=items, all=ids)
    return g


def _ground_ray(engine, x, y, z, length, self_id):
    g = _ground_items(engine)
    cand = {i for i, x0, z0, x1, z1 in g["items"] if x0 <= x <= x1 and z0 <= z <= z1 and i != self_id}
    if not cand:
        return None
    glm = _m().glm
    try:
        return engine.physics.raycast(glm.vec3(x, y, z), glm.vec3(0.0, -1.0, 0.0), length, ignore=g["all"] - cand)
    except Exception:
        return None


def rig_of(node):
    return getattr(node, "_mh_rig", None)


def ensure_rig(engine, node, bp=None, force=False):
    bp = bp or _char_bp(node)
    if bp is None:
        return None
    r = rig_of(node)
    spec = bp.properties.get("character") or CORE.default_spec()
    key = CORE.spec_key(CORE.normalize_spec(spec, _lib()))
    if r is not None and not force and r.spec_key == key and r.parts and r.parts[0].parent is node:
        return r
    old_h = r.height if r is not None else float(bp.properties.get("capsule_height") or 0.0)
    if r is not None:
        r.destroy()
    for ch in list(node.children):                    # stray parts (e.g. after undo)
        if getattr(ch, "_heaven3d_imported_part", None) == "mh":
            ch.remove()
    try:
        r = Rig(engine, node, bp)
    except Exception as ex:
        H3D.warn(f"could not build character '{node.name}': {ex}")
        import traceback
        traceback.print_exc()
        node._mh_rig = None
        return None
    node._mh_rig = r
    if old_h > 0.0 and abs(old_h - r.height) > 1e-4 and not engine._playing:
        node.position.y += (r.height - old_h) * 0.5       # keep the feet where they were
    bp.properties["capsule_height"] = round(r.height, 4)
    return r


def _char_bp(node):
    for bp in getattr(node, "blueprints", []) or []:
        if bp.__class__.__name__ == "MHCharacter":
            return bp
    return None


def _bp(node, name):
    for bp in getattr(node, "blueprints", []) or []:
        if bp.__class__.__name__ == name:
            return bp
    return None


def characters(engine):
    out = []
    for n in engine.scene.root.traverse():
        bp = _char_bp(n)
        if bp is not None:
            out.append((n, bp))
    return out


# =========================================================================== blueprints
def _blueprints():
    m = _m()
    Blueprint, blueprint, glm = m.Blueprint, m.blueprint, m.glm

    @blueprint
    class MHCharacter(Blueprint):
        """A MakeHuman character: look (character), moveset, animations per slot, foot IK."""

        def __init__(self):
            super().__init__()
            self.properties.setdefault("foot_ik", True)
            self.properties.setdefault("preview", "idle")
            self.properties.setdefault("moveset", "auto")
            self.properties.setdefault("animations", {})
            self.properties.setdefault("character", CORE.default_spec())
            self.properties.setdefault("capsule_height", 0.0)

        def on_begin_play(self):
            r = ensure_rig(self.engine, self.node, self)
            if r is not None:
                r.animator.stop_action()
                r.animator.set_loop(None)
                r.animator.set_speed(0.0, snap=True)

    class _Mover(Blueprint):
        """Shared movement for NPCs and enemies: capsule controller, smooth
        acceleration and turning, and crowd steering (separation, following,
        side-stepping) so characters flow around each other instead of bumping."""
        DEFAULTS = {}

        def __init__(self):
            super().__init__()
            for k, v in self.DEFAULTS.items():
                self.properties.setdefault(k, v)

        def p(self, k):
            return self.properties.get(k, self.DEFAULTS.get(k))

        def _setup(self):
            e = self.engine
            n = self.node
            self.rig = ensure_rig(e, n)
            h = self.rig.height if self.rig else 1.75
            self._home = glm.vec3(n.position)
            self._home_rot = glm.vec3(n.rotation_euler)
            self._made_collider = n.collider is None
            if n.collider is None:
                n.collider = m.CapsuleCollider(radius=min(0.32, max(0.22, 0.15 * h)), height=h)
            e.physics.add_node(n)
            self.cc = m.CharacterController(e.physics, n, radius=n.collider.radius, height=h,
                                            slope_limit_deg=50.0, step_offset=0.42)
            self._vy = 0.0
            self._yaw = n.rotation_euler.y
            self._ignore = {id(x) for x in n.traverse()}
            self._speed = 0.0                    # commanded, accelerates smoothly
            self._vel = 0.0                      # measured, smoothed (drives the animation)
            self._side = 1.0 if (id(n) // 16) % 2 else -1.0
            self._stuck = 0.0
            self._dt = 1.0 / 60.0

        def _restore(self):
            n = self.node
            n.position = glm.vec3(self._home)
            n.rotation_euler = glm.vec3(self._home_rot)
            n.visible = True
            if getattr(self, "_made_collider", False):
                n.collider = None
            r = rig_of(n)
            if r is not None:
                r.animator.stop_action()
                r.animator.set_loop(None)
                r.animator.set_speed(0.0, snap=True)
                r.update(0.0, force=True)

        def _turn_to(self, d, dt, rate=300.0):
            if abs(d.x) + abs(d.z) < 1e-6:
                return 0.0
            want = math.degrees(math.atan2(-d.x, -d.z))
            diff = (want - self._yaw + 180.0) % 360.0 - 180.0
            # ease in: fast for big turns, gentle when nearly aligned; never overshoots
            mag = min(abs(diff), max(abs(diff) * min(1.0, dt * 7.0), 25.0 * dt), rate * dt)
            stepd = math.copysign(mag, diff)
            self._yaw = (self._yaw + stepd + 180.0) % 360.0 - 180.0
            self.node.rotation_euler.y = self._yaw
            return abs(diff - stepd)

        def _forward(self):
            a = math.radians(self._yaw)
            return glm.vec3(-math.sin(a), 0.0, -math.cos(a))

        def _steer(self, desired, base_speed):
            """Crowd avoidance: returns (direction, speed factor)."""
            pos = self.node.position
            dl = math.hypot(desired.x, desired.z)
            if dl < 1e-6:
                return desired, 1.0
            dx, dz = desired.x / dl, desired.z / dl
            sx = sz = 0.0
            slow = 1.0
            for other, ox, oz, ovx, ovz in STATE.get("agents", ()):
                if other is self.node:
                    continue
                rx, rz = pos.x - ox, pos.z - oz
                dist = math.hypot(rx, rz)
                if dist < 1e-4 or dist > 2.4:
                    continue
                nx, nz = rx / dist, rz / dist
                if dist < 1.25:                           # personal space
                    k = (1.25 - dist) / 1.25
                    sx += nx * k * 1.8
                    sz += nz * k * 1.8
                ahead = -(dx * nx + dz * nz)              # 1 = straight ahead
                if ahead > 0.6 and dist < 2.2:
                    ov = math.hypot(ovx, ovz)
                    same = (ovx * dx + ovz * dz) / ov if ov > 0.05 else -1.0
                    if same > 0.5 and ov > 0.3:          # follow: keep a gap, match pace
                        slow = min(slow, max(0.15, (dist - 0.8) / 1.0), max(0.3, ov / max(base_speed, 0.1) + 0.1))
                    else:                                 # standing or coming at us: step aside
                        side_x, side_z = -dz * self._side, dx * self._side
                        k = (2.2 - dist) / 2.2
                        sx += side_x * k * 1.4
                        sz += side_z * k * 1.4
                        slow = min(slow, max(0.35, (dist - 0.6) / 1.2))
            vx, vz = dx + sx, dz + sz
            vl = math.hypot(vx, vz)
            if vl < 1e-4:
                vx, vz, vl = dx, dz, 1.0
            vx, vz = vx / vl, vz / vl
            # low-pass the steering so avoidance never makes the heading flicker
            px, pz = getattr(self, "_sdir", (vx, vz))
            k = min(1.0, self._dt * 5.0)
            px, pz = px + (vx - px) * k, pz + (vz - pz) * k
            pl = math.hypot(px, pz) or 1.0
            self._sdir = (px / pl, pz / pl)
            self._slow = getattr(self, "_slow", slow) + (slow - getattr(self, "_slow", slow)) * min(1.0, self._dt * 6.0)
            return glm.vec3(px / pl, 0.0, pz / pl), self._slow

        def _move(self, target_speed, dt, accel=2.6, decel=5.0):
            self._dt = dt
            if target_speed > self._speed:
                self._speed = min(target_speed, self._speed + accel * dt)
            else:
                self._speed = max(target_speed, self._speed - decel * dt)
            fwd = self._forward()
            if self.cc.is_grounded and self._vy < 0.0:
                self._vy = -2.0
            self._vy -= 22.0 * dt
            before = glm.vec3(self.node.position)
            self.cc.move(glm.vec3(fwd.x * self._speed, self._vy, fwd.z * self._speed) * dt)
            mv = self.node.position - before
            actual = math.sqrt(mv.x * mv.x + mv.z * mv.z) / max(dt, 1e-4)
            self._vel += (min(actual, self._speed + 0.3) - self._vel) * min(1.0, dt * 10.0)
            self._vx, self._vz = mv.x / max(dt, 1e-4), mv.z / max(dt, 1e-4)
            if self.rig is not None:
                self.rig.animator.set_speed(self._vel if self._speed > 0.02 else min(self._vel, 0.0))
            return actual

        def _player(self):
            pb = self.engine.first_blueprint("FPSController")
            if pb is None or getattr(pb, "_dead", False) or pb.node is None:
                return None, None
            return pb, pb.node

    @blueprint
    class NPCWalker(_Mover):
        """Walks the NPC path named in 'path' (an NPCPath node).  mode: loop,
        pingpong or random.  Waits 'wait_time' s at each point (or the point's
        own 'wait').  With no path it wanders around its start point.  Walkers
        keep their own lane on the path, give way to each other, and stop to
        talk to the player when 'greet_player' is on."""
        DEFAULTS = {"path": "", "speed": 1.3, "run": False, "mode": "loop", "wait_time": 1.5,
                    "start_at_nearest": True, "wander_radius": 6.0, "greet_player": True, "greet_range": 2.6}

        def on_begin_play(self):
            self._setup()
            self.points = _path_points(self.engine, str(self.p("path")))
            self.idx = 0
            self.dir = 1
            self.wait = random.uniform(0.0, 0.8)
            self.talking = False
            self._wander = None
            self._lane = random.uniform(-0.55, 0.55)
            if self.points and self.p("start_at_nearest"):
                pos = self.node.position
                self.idx = min(range(len(self.points)), key=lambda i: glm.length(self.points[i][0] - pos))

        def on_end_play(self):
            self._restore()

        def _next(self):
            n = len(self.points)
            mode = str(self.p("mode"))
            if mode == "random" and n > 1:
                self.idx = random.choice([i for i in range(n) if i != self.idx])
            elif mode == "pingpong" and n > 1:
                if not 0 <= self.idx + self.dir < n:
                    self.dir = -self.dir
                self.idx += self.dir
            else:
                self.idx = (self.idx + 1) % max(1, n)

        def _target(self):
            """Waypoint shifted sideways by this walker's lane."""
            pt, wait = self.points[self.idx]
            prev = self.points[(self.idx - self.dir) % len(self.points)][0] if len(self.points) > 1 else self._home
            seg = pt - prev
            sl = math.hypot(seg.x, seg.z)
            if sl > 1e-3:
                pt = pt + glm.vec3(-seg.z / sl, 0.0, seg.x / sl) * self._lane
            return pt, wait

        def on_tick(self, dt):
            if self.rig is None:
                return
            an = self.rig.animator
            speed = float(self.p("speed")) * (2.6 if self.p("run") else 1.0)
            pb, pn = self._player()
            if pn is not None and self.p("greet_player"):
                to = pn.world_position() - self.node.world_position()
                d = math.sqrt(to.x * to.x + to.z * to.z)
                if d < float(self.p("greet_range")) or (self.talking and d < float(self.p("greet_range")) * 1.6):
                    self._turn_to(glm.vec3(to.x, 0, to.z), dt, 200.0)
                    self._move(0.0, dt)
                    if self._speed < 0.2:
                        if not self.talking and an.has("wave") and not an.busy:
                            an.play("wave", fade=0.25)        # wave hello, then chat
                        an.set_loop("talk")
                        self.talking = True
                    return
            if self.talking:
                an.set_loop(None)
                self.talking = False
            if self.wait > 0.0:
                self.wait -= dt
                self._move(0.0, dt)
                return
            if self.points:
                target, wait = self._target()
            else:
                if self._wander is None:
                    r = float(self.p("wander_radius"))
                    a = random.uniform(0, 2 * math.pi)
                    self._wander = self._home + glm.vec3(math.cos(a) * r * random.random(), 0,
                                                         math.sin(a) * r * random.random())
                target, wait = self._wander, float(self.p("wait_time"))
            to = target - self.node.position
            d = math.sqrt(to.x * to.x + to.z * to.z)
            if d < 0.7:
                crowded = any(math.hypot(ox - target.x, oz - target.z) < 0.9
                              for o, ox, oz, _a, _b in STATE.get("agents", ()) if o is not self.node)
                self.wait = 0.0 if crowded else (wait if wait >= 0 else float(self.p("wait_time")))
                self._wander = None
                if self.points:
                    self._next()
                if self.wait > 0:
                    self._move(0.0, dt)
                return
            dirv, slow = self._steer(glm.vec3(to.x, 0.0, to.z), speed)
            err = self._turn_to(dirv, dt, 220.0)
            turn_slow = max(0.25, 1.0 - err / 100.0)
            arrive = min(1.0, d / 1.2 + 0.35)
            moved = self._move(speed * slow * turn_slow * arrive, dt)
            if moved < 0.08 and self._speed > 0.4 and d > 1.0:
                self._stuck += dt
                if self._stuck > 3.0:                    # blocked for a while: give up this point
                    self._stuck = 0.0
                    self._wander = None
                    if self.points:
                        self._next()
            else:
                self._stuck = max(0.0, self._stuck - dt)

    @blueprint
    class MHEnemy(_Mover):
        """Patrols its 'path' (or wanders), notices the player (sight cone,
        hearing range, optional line of sight), chases, attacks (melee or
        hitscan with a pistol), plays hit / death animations.  Takes 'damage' messages."""
        DEFAULTS = {"path": "", "patrol_speed": 1.2, "chase_speed": 3.8, "detect_range": 18.0, "fov": 120.0,
                    "hearing_range": 4.0, "require_los": True, "lose_interest": 6.0, "attack_range": 1.5,
                    "attack_damage": 12.0, "attack_cooldown": 1.3, "attack_style": "melee", "hitscan_range": 25.0,
                    "max_health": 80.0, "wait_time": 2.0, "growl_sound": "enemy", "death_sound": "hurt"}

        def on_begin_play(self):
            self._setup()
            self.points = _path_points(self.engine, str(self.p("path")))
            self.idx = 0
            self.wait = 0.0
            self.hp = float(self.p("max_health"))
            self.alive = True
            self.state = "patrol"
            self.alert = 0.0
            self.cool = 0.0
            self.stagger = 0.0
            self._wander = None
            self._attacking = False
            if self.rig is not None and self.rig.moveset != desired_moveset(self.node, _char_bp(self.node)):
                self.rig.set_moveset(desired_moveset(self.node, _char_bp(self.node)))
            if self.points:
                pos = self.node.position
                self.idx = min(range(len(self.points)), key=lambda i: glm.length(self.points[i][0] - pos))

        def on_end_play(self):
            self._restore()

        def _eye(self):
            h = self.rig.height if self.rig else 1.7
            return self.node.world_position() + glm.vec3(0, h * 0.42, 0)

        def _sees(self, pn):
            pos = self.node.world_position()
            pp = pn.world_position()
            to = pp - pos
            d = math.sqrt(to.x * to.x + to.z * to.z)
            if d > float(self.p("detect_range")):
                return False, d
            if d > float(self.p("hearing_range")):
                f = self._forward()
                cosang = (f.x * to.x + f.z * to.z) / max(d, 1e-4)
                if cosang < math.cos(math.radians(float(self.p("fov")) * 0.5)):
                    return False, d
            if self.p("require_los"):
                eye = self._eye()
                v = pp - eye
                L = glm.length(v)
                if L > 1e-4:
                    pb, _pn = self._player()
                    hit = self.engine.physics.raycast(eye, v / L, L, ignore=set(self._ignore))
                    if hit is not None and pb is not None and not _is_under(hit.node, pb.node):
                        return False, d
            return True, d

        def on_tick(self, dt):
            if not self.alive or self.rig is None:
                return
            e = self.engine
            an = self.rig.animator
            ranged = self.p("attack_style") == "hitscan"
            self.cool -= dt
            if self.stagger > 0:
                self.stagger -= dt
                self._move(0.0, dt, decel=8.0)
                return
            pb, pn = self._player()
            seen, d = (False, 1e9) if pn is None else self._sees(pn)
            if seen:
                if self.state != "chase":
                    e.audio.play_at(self.p("growl_sound"), self.node.world_position(), 0.6)
                self.state, self.alert = "chase", float(self.p("lose_interest"))
            elif self.state == "chase":
                self.alert -= dt
                if self.alert <= 0 or pn is None:
                    self.state = "patrol"
            for ev in an.events:
                if ev == "hit" and self._attacking and pn is not None:
                    self._deal(pb, pn)
            if self._attacking:
                if an.action is None:
                    self._attacking = False
                elif not ranged:
                    if pn is not None:
                        to = pn.world_position() - self.node.world_position()
                        self._turn_to(glm.vec3(to.x, 0, to.z), dt, 360.0)
                    self._move(0.0, dt, decel=8.0)
                    return
            if self.state == "chase" and pn is not None:
                to = pn.world_position() - self.node.world_position()
                rng = float(self.p("attack_range")) if not ranged else \
                    min(float(self.p("hitscan_range")), float(self.p("detect_range")) * 0.75)
                self._turn_to(glm.vec3(to.x, 0, to.z), dt, 360.0)
                if d <= rng:
                    keep = 0.0 if not ranged or d < rng * 0.6 else float(self.p("patrol_speed")) * 0.8
                    self._move(keep, dt, decel=8.0)
                    if self.cool <= 0.0 and (not ranged or seen):
                        self.cool = float(self.p("attack_cooldown"))
                        self._attacking = an.play("shoot" if ranged else "attack", fade=0.08)
                        if not ranged:
                            e.audio.play_at(self.p("growl_sound"), self.node.world_position(), 0.5)
                else:
                    dirv, slow = self._steer(glm.vec3(to.x, 0, to.z), float(self.p("chase_speed")))
                    self._turn_to(dirv, dt, 300.0)
                    self._move(float(self.p("chase_speed")) * max(slow, 0.5), dt)
                return
            # patrol
            if self.wait > 0:
                self.wait -= dt
                self._move(0.0, dt)
                return
            if self.points:
                target, w = self.points[self.idx]
            else:
                if self._wander is None:
                    a = random.uniform(0, 2 * math.pi)
                    r = random.uniform(1.0, 6.0)
                    self._wander = self._home + glm.vec3(math.cos(a) * r, 0, math.sin(a) * r)
                target, w = self._wander, -1
            to = target - self.node.position
            dd = math.sqrt(to.x * to.x + to.z * to.z)
            if dd < 0.6:
                self.wait = w if w >= 0 else float(self.p("wait_time"))
                self._wander = None
                if self.points:
                    self.idx = (self.idx + 1) % len(self.points)
                self._move(0.0, dt)
                return
            dirv, slow = self._steer(glm.vec3(to.x, 0, to.z), float(self.p("patrol_speed")))
            err = self._turn_to(dirv, dt, 200.0)
            self._move(float(self.p("patrol_speed")) * slow * max(0.25, 1.0 - err / 100.0), dt)

        def _deal(self, pb, pn):
            e = self.engine
            dmg = float(self.p("attack_damage"))
            if self.p("attack_style") == "hitscan":
                muzzle = self.rig.muzzle_world() if self.rig is not None else self._eye()
                chest = pn.world_position() + glm.vec3(0, -0.25, 0)
                v = chest - muzzle
                L = glm.length(v)
                if L > float(self.p("hitscan_range")) or L < 1e-4:
                    return
                e.audio.play_at("shoot", muzzle, 0.5)
                try:
                    e.spawn_flash(muzzle, color=(1.0, 0.75, 0.3), intensity=30.0, range=4.0, ttl=0.06)
                except Exception:
                    pass
                hit = e.physics.raycast(muzzle, v / L, L, ignore=self._ignore)
                if hit is not None and _is_under(hit.node, pn):
                    e.message_ancestors(hit.node, "damage", {"amount": dmg, "point": hit.point, "source": "enemy"})
            else:
                to = pn.world_position() - self.node.world_position()
                if math.sqrt(to.x * to.x + to.z * to.z) <= float(self.p("attack_range")) * 1.35:
                    e.message_ancestors(pn, "damage", {"amount": dmg, "point": pn.world_position(), "source": "enemy"})

        def on_message(self, event, data):
            if event != "damage" or not getattr(self, "alive", False):
                return
            amt = float(data.get("amount", 0)) if isinstance(data, dict) else float(data or 0)
            self.hp -= amt
            self.state, self.alert = "chase", float(self.p("lose_interest"))
            if self.hp <= 0:
                self._die()
            elif self.rig is not None and amt > 0:
                self._attacking = False
                self.rig.animator.play("hit", fade=0.06)
                self.stagger = 0.35

        def _die(self):
            e = self.engine
            self.alive = False
            self.hp = 0.0
            e.audio.play_at(self.p("death_sound"), self.node.world_position(), 0.7)
            if self.rig is not None:
                self.rig.animator.set_speed(0.0, snap=True)
                self.rig.animator.set_overlay(None)
                if self.rig.animator.overlay:
                    self.rig.animator.overlay = None
                self.rig.animator.play("death", fade=0.12, hold=True)
            try:
                e.physics.remove_node(self.node)
            except Exception:
                pass
            e.send_message("died", {"node": self.node})

    @blueprint
    class NPCPath(Blueprint):
        """A path for NPCs / enemies.  Its points are the NPCWaypoint nodes whose
        'path' is this node's name, visited in 'order'."""

        def __init__(self):
            super().__init__()
            self.properties.setdefault("closed", True)

    @blueprint
    class NPCWaypoint(Blueprint):
        """A point of the NPCPath named in 'path'.  'wait' < 0 = use the walker's wait_time."""

        def __init__(self):
            super().__init__()
            self.properties.setdefault("path", "")
            self.properties.setdefault("order", 0)
            self.properties.setdefault("wait", -1.0)

    CLS.update(MHCharacter=MHCharacter, NPCWalker=NPCWalker, MHEnemy=MHEnemy, NPCPath=NPCPath, NPCWaypoint=NPCWaypoint)


def _is_under(node, ancestor):
    while node is not None:
        if node is ancestor:
            return True
        node = node.parent
    return False


# =========================================================================== paths
def _waypoints(engine, path_name):
    pts = []
    for n in engine.scene.root.children:
        bp = _bp(n, "NPCWaypoint")
        if bp is not None and bp.properties.get("path") == path_name:
            pts.append((int(bp.properties.get("order", 0)), n, bp))
    pts.sort(key=lambda t: t[0])
    return pts


def _path_points(engine, path_name):
    glm = _m().glm
    if not path_name:
        return []
    return [(glm.vec3(n.world_position()), float(bp.properties.get("wait", -1.0)))
            for _o, n, bp in _waypoints(engine, path_name)]


def _paths(engine):
    return [n for n in engine.scene.root.children if _bp(n, "NPCPath") is not None]


def _add_waypoint(engine, path_node, order, pos):
    m = _m()
    p = engine.add_sphere(radius=0.22, name=f"{path_node.name} P{order + 1}", solid=False)
    p.position = m.glm.vec3(pos)
    p.material.base_color = (0.25, 0.85, 1.0, 1.0)
    p.material.emissive = (0.05, 0.35, 0.5)
    engine.attach_blueprint(p, CLS["NPCWaypoint"])
    p.blueprints[-1].properties.update({"path": path_node.name, "order": order, "wait": -1.0})
    return p


def _ground_point(engine, dist=8.0):
    m = _m()
    glm = m.glm
    cam = engine.camera
    f = cam.forward()
    o = glm.vec3(cam.position)
    hit = None
    try:
        hit = engine.raycast(o, f, 200.0)
    except Exception:
        hit = None
    if hit is not None and hit.distance < 80.0:
        return glm.vec3(hit.point)
    if f.y < -0.05:
        t = -o.y / f.y
        if 0 < t < 120:
            return o + f * t
    fl = glm.normalize(glm.vec3(f.x, 0.0, f.z)) if abs(f.x) + abs(f.z) > 1e-5 else glm.vec3(0, 0, -1)
    p = o + fl * dist
    p.y = 0.0
    return p


def new_path(engine, center=None, radius=5.0, count=4, select=True):
    m = _m()
    glm = m.glm
    c = center if center is not None else _ground_point(engine)
    path = m.SceneNode(engine._unique_node_name("NPC Path"), parent=engine.scene.root)
    path.position = glm.vec3(c)
    engine.attach_blueprint(path, CLS["NPCPath"])
    for i in range(count):
        a = 2 * math.pi * i / count
        _add_waypoint(engine, path, i, c + glm.vec3(math.cos(a) * radius, 0.0, math.sin(a) * radius))
    if select:
        engine.select_node(path)
    return path


def _selected_path(engine):
    sel = getattr(engine, "selected", None)
    if sel is None:
        return None
    if _bp(sel, "NPCPath") is not None:
        return sel
    wb = _bp(sel, "NPCWaypoint")
    if wb is not None:
        for p in _paths(engine):
            if p.name == wb.properties.get("path"):
                return p
    for nm in ("NPCWalker", "MHEnemy"):
        b = _bp(sel, nm)
        if b is not None:
            for p in _paths(engine):
                if p.name == b.properties.get("path"):
                    return p
    return None


def tool_new_path(engine):
    p = new_path(engine)
    H3D.log(f"'{p.name}' created - move its blue points with the gizmo; NPCs walk them in order")


def tool_add_point(engine):
    path = _selected_path(engine)
    if path is None:
        H3D.warn("select an NPC path, one of its points, or a walker first")
        return
    pts = _waypoints(engine, path.name)
    glm = _m().glm
    if len(pts) >= 2:
        a, b = pts[-2][1].position, pts[-1][1].position
        pos = b + (b - a) * 0.8
    elif pts:
        pos = pts[-1][1].position + glm.vec3(3, 0, 0)
    else:
        pos = glm.vec3(path.position)
    engine.select_node(_add_waypoint(engine, path, len(pts), pos))


# =========================================================================== creation tools
def place_character(engine, spec=None, pos=None, name="Character", yaw=None, select=True):
    m = _m()
    glm = m.glm
    spec = spec or CORE.random_spec(_lib(), seed=random.randrange(1 << 30))
    node = m.SceneNode(engine._unique_node_name(name), parent=engine.scene.root)
    engine.attach_blueprint(node, CLS["MHCharacter"])
    bp = node.blueprints[-1]
    bp.properties["character"] = spec
    p = pos if pos is not None else _ground_point(engine)
    r = ensure_rig(engine, node, bp)
    h = r.height if r else 1.75
    node.position = glm.vec3(p.x, p.y + h * 0.5 + 0.01, p.z)
    if yaw is None:
        cam = engine.camera.position
        yaw = math.degrees(math.atan2(-(cam.x - p.x), -(cam.z - p.z)))
    node.rotation_euler.y = yaw
    if select:
        engine.select_node(node)
    return node


def tool_new_character(engine):
    node = place_character(engine, CORE.random_spec(_lib(), seed=random.randrange(1 << 30)))
    STATE["tab"] = "Body"
    H3D.log(f"'{node.name}' added - edit it in the Character Creator panel")


def tool_new_npc(engine):
    glm = _m().glm
    c = _ground_point(engine)
    path = new_path(engine, c, radius=5.0, count=4, select=False)
    pts = _waypoints(engine, path.name)
    node = place_character(engine, CORE.random_spec(_lib(), seed=random.randrange(1 << 30)),
                           pos=glm.vec3(pts[0][1].position), name="NPC")
    engine.attach_blueprint(node, CLS["NPCWalker"])
    node.blueprints[-1].properties["path"] = path.name
    H3D.log(f"'{node.name}' walks '{path.name}' in Play")


def tool_new_enemy(engine, style=None):
    style = style or random.choice(("soldier", "zombie", "viking", "scifi"))
    node = place_character(engine, CORE.random_spec(_lib(), seed=random.randrange(1 << 30), style=style), name="Enemy")
    engine.attach_blueprint(node, CLS["MHEnemy"])
    eb = node.blueprints[-1]
    if style == "zombie":
        eb.properties.update({"chase_speed": 1.9, "attack_range": 1.4, "require_los": False})
    if style in ("soldier", "scifi"):
        eb.properties.update({"attack_style": "hitscan", "attack_damage": 8.0, "attack_cooldown": 1.1})
    _char_bp(node).properties["foot_ik"] = True
    if engine.find_player_spawn() is None:
        H3D.warn("add a Player Spawn (Library) so enemies have someone to chase")
    H3D.log(f"'{node.name}' ({style}) added - foot IK on, give it an NPC path to patrol")


def tool_crowd(engine, count=6):
    glm = _m().glm
    c = _ground_point(engine, 12.0)
    path = new_path(engine, c, radius=7.0, count=6, select=False)
    pts = _waypoints(engine, path.name)
    for i in range(count):
        a = pts[i % len(pts)][1].position
        b = pts[(i + 1) % len(pts)][1].position
        pos = a + (b - a) * random.uniform(0.15, 0.85)
        node = place_character(engine, CORE.random_spec(_lib(), seed=random.randrange(1 << 30)),
                               pos=glm.vec3(pos), name="NPC", select=False)
        engine.attach_blueprint(node, CLS["NPCWalker"])
        wb = node.blueprints[-1]
        wb.properties.update({"path": path.name, "mode": random.choice(("loop", "loop", "random", "pingpong")),
                              "speed": round(random.uniform(1.0, 1.5), 2),
                              "wait_time": round(random.uniform(0.5, 3.0), 1)})
    engine.select_node(path)
    H3D.log(f"crowd of {count} on '{path.name}'")


def tool_scan_animations(engine):
    pp = getattr(engine, "project_path", None)
    d = os.path.join(os.path.dirname(os.path.abspath(pp)), "animations") if pp else None
    if not d or not os.path.isdir(d):
        H3D.warn("put your Mixamo .fbx files in an 'animations' folder next to your project first")
        return
    found = auto_slots(d, os.path.dirname(os.path.abspath(pp)))
    if not found:
        H3D.warn("no animation files recognised (name them e.g. 'Walking.fbx', 'Running.fbx', 'Punching.fbx')")
        return
    n = 0
    for node, bp in characters(engine):
        anims = dict(bp.properties.get("animations") or {})
        for slot, rel in found.items():
            if not anims.get(slot):
                anims[slot] = rel
        bp.properties["animations"] = anims
        ensure_rig(engine, node, bp, force=True)
        n += 1
    H3D.log(f"animations {', '.join(f'{k}={os.path.basename(v)}' for k, v in found.items())} given to {n} character(s)")


def auto_slots(folder, project_dir):
    found = {}
    files = sorted(f for f in os.listdir(folder) if os.path.splitext(f)[1].lower() in (".fbx", ".glb", ".gltf", ".bvh"))
    for slot, keys in SLOT_KEYWORDS.items():
        for f in files:
            low = f.lower()
            if any(k in low for k in keys):
                if slot == "walk" and "run" in low:
                    continue
                found[slot] = os.path.relpath(os.path.join(folder, f), project_dir).replace(os.sep, "/")
                break
    return found


def import_animation(engine, slot, path):
    """Copy an animation file into <project>/animations and assign it to the selected character's slot."""
    sel = getattr(engine, "selected", None)
    bp = _char_bp(sel) if sel is not None else None
    if bp is None or not path:
        return False
    pp = getattr(engine, "project_path", None)
    rel = path
    if pp:
        proj = os.path.dirname(os.path.abspath(pp))
        dst_dir = os.path.join(proj, "animations")
        try:
            os.makedirs(dst_dir, exist_ok=True)
            dst = os.path.join(dst_dir, os.path.basename(path))
            if os.path.abspath(dst) != os.path.abspath(path):
                import shutil
                shutil.copy2(path, dst)
            rel = os.path.relpath(dst, proj).replace(os.sep, "/")
        except OSError as ex:
            H3D.warn(f"could not copy into the project ({ex}); using the original path")
    try:
        srcs = ANIM.load_source_cached(_resolve_path(engine, rel) or path)
        n = len(srcs[0].frames)
    except Exception as ex:
        H3D.warn(f"'{os.path.basename(path)}' is not a usable animation: {ex}")
        return False
    anims = dict(bp.properties.get("animations") or {})
    anims[slot] = rel
    bp.properties["animations"] = anims
    ensure_rig(engine, sel, bp, force=True)
    H3D.log(f"{slot} = {os.path.basename(path)} ({n} frames)")
    return True


def _resolve_path(engine, rel):
    if os.path.isabs(rel):
        return rel if os.path.isfile(rel) else None
    pp = getattr(engine, "project_path", None)
    if pp:
        p = os.path.join(os.path.dirname(os.path.abspath(pp)), rel)
        if os.path.isfile(p):
            return p
    return None


# =========================================================================== Character Creator (editor panel)
X0, X1 = 0.582, 0.814            # panel x range (between viewport and Inspector)
TOP, BOT = 0.79, 0.022
ROW = 0.029
PAD_Y = 3.0                      # small vertical padding = readable text in compact rows


def _creator_target(engine):
    if engine.mode != "editor" or engine._playing:
        return None, None
    sel = getattr(engine, "selected", None)
    if sel is None:
        return None, None
    bp = _char_bp(sel)
    return (sel, bp) if bp is not None else (None, None)


def _spec(bp):
    s = CORE.normalize_spec(bp.properties.get("character"), _lib())
    bp.properties["character"] = s
    return s


def _mark(engine, node):
    STATE["dirty"][id(node)] = (time.monotonic(), node)


class _UI:
    """Tiny immediate-mode widget helpers on top of the editor UI."""

    def __init__(self, engine):
        self.e = engine
        self.ui = engine.ui
        self.T = _m().THEME
        self.y = TOP

    def row(self, h=ROW):
        self.y -= h + 0.004
        return self.y

    def label(self, text, color=None, indent=0.0):
        y = self.row(0.024)
        self.ui.text(_m().UIRect(X0 + 0.006 + indent, y, X1 - X0 - 0.012, 0.024), text, color or self.T["text_dim"])

    def header(self, text):
        self.y -= 0.006
        y = self.row(0.026)
        self.ui.text(_m().UIRect(X0 + 0.006, y, X1 - X0 - 0.012, 0.026), text, self.T["green_text"])

    def buttons(self, items, h=ROW):
        """items: [(label, callback, active)] on one row."""
        y = self.row(h)
        n = len(items)
        gap = 0.004
        w = (X1 - X0 - 0.012 - gap * (n - 1)) / max(1, n)
        x = X0 + 0.006
        for lab, cb, active in items:
            col = self.T["accent"] if active else self.T["button"]
            self.ui.button(_m().UIRect(x, y, w, h), lab, cb, col, pad_x_px=6.0, pad_y_px=PAD_Y)
            x += w + gap

    def slider(self, key, label, value, lo, hi, setter, fmt=None):
        R = _m().UIRect
        y = self.row(0.027)
        x, w = X0 + 0.006, X1 - X0 - 0.012
        frac = 0.0 if hi == lo else max(0.0, min(1.0, (value - lo) / (hi - lo)))

        def start(key=key, lo=lo, hi=hi, setter=setter, x=x, w=w):
            STATE["drag"] = {"key": key, "x": x, "w": w, "lo": lo, "hi": hi, "set": setter}
            _drag_apply(self.e)
        self.ui.button(R(x, y, w, 0.027), " ", start, self.T["panel_dark"])
        if frac > 0.003:
            self.ui.button(R(x, y, max(0.004, w * frac), 0.027), " ", start, self.T["accent_dim"])
        txt = fmt(value) if fmt else f"{value:.2f}"
        # transparent button on top: the label must be drawn after the bars
        self.ui.button(R(x, y, w, 0.027), f"{label}:  {txt}", start, (0.0, 0.0, 0.0, 0.0), align="left",
                       pad_x_px=8.0, pad_y_px=PAD_Y)

    def item(self, label, cb, active=False, h=0.026):
        y = self.row(h)
        col = self.T["accent"] if active else self.T["button"]
        self.ui.button(_m().UIRect(X0 + 0.006, y, X1 - X0 - 0.012, h), label, cb, col, align="left",
                       pad_x_px=8.0, pad_y_px=PAD_Y)


def _drag_apply(engine):
    d = STATE["drag"]
    if not d:
        return
    mx = engine.input.mouse_pos[0] / float(max(1, engine.width))
    f = max(0.0, min(1.0, (mx - d["x"]) / max(1e-6, d["w"])))
    d["set"](d["lo"] + (d["hi"] - d["lo"]) * f)


def _pretty(pid_or_name):
    n = pid_or_name.split("/")[-1]
    for pre in ("toigo_", "culturalibre_", "cortu_", "rehmanpolanski_", "matcreator_", "mindfront_", "joepal_",
                "elvs_", "spamrakuen_", "v0rt3x_", "marco_105_", "frankyaye_", "grinsegold_", "scailman_", "wolgade_",
                "javherre_", "donitz_", "aethelraed_", "namuhekam_", "skalldyrssuppe_", "kwnet_at_", "mrgreaterthan_",
                "jujube_", "joachip_", "ews_", "wdg_", "punkduck_", "learning_", "littleright_", "o4saken_",
                "faydaen_", "sonntag78_", "thegreatengineer_", "elvaerwyn_", "jartur69_", "bogdan666_", "dariush086_"):
        if n.startswith(pre):
            n = n[len(pre):]
    return n.replace("_", " ").replace("-", " ").strip()


def _build_creator(engine):
    ui = engine.ui
    m = _m()
    R = m.UIRect
    reg = ui.scroll_regions.get("mh_creator")
    node, bp = _creator_target(engine)
    if node is None:
        STATE["creator"] = None
        if reg is not None:
            reg.viewport = None
        return
    STATE["creator"] = node
    T = m.THEME
    lib = _lib()
    s = _spec(bp)
    rig = rig_of(node)
    ui.panel(R(X0 - 0.004, BOT - 0.004, X1 - X0 + 0.008, TOP - BOT + 0.044), T["panel"])
    ui.text(R(X0 + 0.006, TOP + 0.008, X1 - X0 - 0.07, 0.03), f"Character Creator  -  {node.name}", T["text"])
    w = _UI(engine)

    def setm(k):
        def f(v):
            s["macro"][k] = v
            _mark(engine, node)
        return f
    tabs = ("Body", "Face", "Clothes", "Hair", "Skin", "Anim", "AI")
    w.y = TOP + 0.002
    w.buttons([(t, (lambda t=t: STATE.update(tab=t)), STATE["tab"] == t) for t in tabs[:4]], h=0.028)
    w.buttons([(t, (lambda t=t: STATE.update(tab=t)), STATE["tab"] == t) for t in tabs[4:]], h=0.028)
    style = STATE["style"]

    def randomize(gender=None):
        def f():
            sp = CORE.random_spec(lib, seed=random.randrange(1 << 30), gender=gender, style=STATE["style"])
            bp.properties["character"] = sp
            _mark(engine, node)
        return f

    def cycle_style():
        st = CORE.STYLES
        STATE["style"] = st[(st.index(STATE["style"]) + 1) % len(st)] if STATE["style"] in st else st[0]
    w.buttons([("Random", randomize(), False), ("Male", randomize("male"), False),
               ("Female", randomize("female"), False), (f"Style: {style}", cycle_style, False)], h=0.028)
    vp = R(X0, BOT, X1 - X0, w.y - BOT - 0.006)
    ui.begin_scroll("mh_creator", vp)
    top_y = w.y
    tab = STATE["tab"]
    mac = s["macro"]
    if tab == "Body":
        w.header("Macro")
        w.slider("gender", "Gender  (female - male)", mac["gender"], 0, 1, setm("gender"), lambda v: f"{v * 100:.0f}% male")
        w.slider("age", "Age", mac["age"], 0.5, 1.0, setm("age"), lambda v: f"{CORE.age_years(v):.0f} years")
        w.slider("muscle", "Muscle", mac["muscle"], 0, 1, setm("muscle"), lambda v: f"{v * 100:.0f}%")
        w.slider("weight", "Weight", mac["weight"], 0, 1, setm("weight"), lambda v: f"{50 + v * 100:.0f}%")
        w.slider("height", "Height", mac["height"], 0, 1, setm("height"),
                 lambda v: f"{rig.height:.2f} m" if rig else f"{v:.2f}")
        w.slider("proportions", "Proportions", mac["proportions"], 0, 1, setm("proportions"))
        if mac["gender"] < 0.7:
            w.slider("cupsize", "Breast size", mac["cupsize"], 0, 1, setm("cupsize"))
            w.slider("firmness", "Breast firmness", mac["firmness"], 0, 1, setm("firmness"))
        w.header("Ethnicity (blend)")
        for r in ("african", "asian", "caucasian"):
            w.slider(r, r.capitalize(), mac[r], 0, 1, setm(r), lambda v: f"{v * 100:.0f}")
        w.header("Body mesh")
        opts = [("Full", "basemesh")] + [(_BODY_LABELS.get(p, _pretty(p)), p) for p in lib.list_parts(kind="proxy")]

        def setbody(pid):
            def f():
                s["body"] = pid
                _mark(engine, node)
            return f
        for i in range(0, len(opts), 4):
            w.buttons([(lab, setbody(pid), s.get("body", "basemesh") == pid) for lab, pid in opts[i:i + 4]], h=0.026)
        _modifier_sliders(w, engine, node, s, ("torso", "hip", "stomach", "buttocks", "pelvis", "arms", "legs",
                                               "neck", "hands", "feet"))
    elif tab == "Face":
        w.buttons([("Reset face", lambda: (s["modifiers"].clear(), _mark(engine, node)), False),
                   ("Random face", lambda: (_random_face(s), _mark(engine, node)), False)])
        _modifier_sliders(w, engine, node, s, ("head", "forehead", "eyebrows", "eyes", "nose", "mouth", "cheek",
                                               "chin", "ears"))
    elif tab == "Clothes":
        _clothes_tab(w, engine, node, s, lib)
    elif tab == "Hair":
        _hair_tab(w, engine, node, s, lib)
    elif tab == "Skin":
        _skin_tab(w, engine, node, s, lib)
    elif tab == "Anim":
        _anim_tab(w, engine, node, bp, rig)
    elif tab == "AI":
        _ai_tab(w, engine, node, bp)
    ui.end_scroll(w.y, top_y)


def _random_face(s):
    lib = _lib()
    for k in [k for k in s["modifiers"] if lib.mods.get(k, {}).get("group") in
              ("head", "forehead", "eyebrows", "eyes", "nose", "mouth", "cheek", "chin", "ears")]:
        s["modifiers"].pop(k)
    rnd = CORE.random_spec(lib, seed=random.randrange(1 << 30))
    for k, v in rnd["modifiers"].items():
        if lib.mods.get(k, {}).get("group") in ("head", "forehead", "eyebrows", "eyes", "nose", "mouth", "cheek",
                                                "chin", "ears"):
            s["modifiers"][k] = v


def _modifier_sliders(w, engine, node, s, groups):
    lib = _lib()
    for g in groups:
        mods = [mm for mm in lib.cat["modifiers"] if mm["group"] == g and mm["name"] != "bulge-decr-incr"]
        if not mods:
            continue
        open_ = STATE.setdefault("open_groups", {"torso": True, "nose": True})
        is_open = open_.get(g, False)
        used = sum(1 for mm in mods if abs(s["modifiers"].get(mm["name"], 0.0)) > 1e-3)
        w.item(f"{'v' if is_open else '>'}  {g.capitalize()}  ({len(mods)} sliders{f', {used} set' if used else ''})",
               lambda g=g: open_.__setitem__(g, not open_.get(g, False)), is_open)
        if not is_open:
            continue
        for mm in mods:
            nm = mm["name"]

            def setter(v, nm=nm):
                v = 0.0 if abs(v) < 0.03 else round(v, 3)
                if v == 0.0:
                    s["modifiers"].pop(nm, None)
                else:
                    s["modifiers"][nm] = v
                _mark(engine, node)
            w.slider("mod:" + nm, _mod_label(mm, g)[:34], s["modifiers"].get(nm, 0.0), -1.0, 1.0, setter,
                     lambda v: f"{v:+.2f}")


_WORDS = [("decr incr", ""), ("in out", "in / out"), ("down up", "down / up"), ("backward forward", "back / fwd"),
          ("concave convex", "concave / convex"), ("compress uncompress", "compress"), ("square round", "square / round"),
          ("pointed triangle", "pointed"), ("trans ", "move "), ("scale horiz", "width"), ("scale vert", "height"),
          ("scale depth", "depth"), ("circ", "girth"), ("dist", "distance"), ("napetowaist", "nape to waist"),
          ("waisttohip", "waist to hip"), ("frontchest", "front chest"), ("upperarm", "upper arm"),
          ("lowerarm", "forearm"), ("upperleg", "thigh"), ("lowerleg", "calf"), ("upperlegs", "thighs"),
          ("lowerlegs", "calves"), ("vshape", "V shape"), ("cupidsbow", "cupid's bow")]


def _mod_label(mm, group):
    lab = " " + mm["label"].replace("-", " ") + " "
    for g in (group, group.rstrip("s"), "measure"):
        lab = lab.replace(" " + g + " ", " ")
    for a, b in _WORDS:
        lab = lab.replace(a, b)
    lab = " ".join(lab.split())
    return lab.capitalize() if lab else mm["name"]


_BODY_LABELS = {"basemesh": "Full", "proxy/female1605": "F low-poly", "proxy/male1591": "M low-poly",
                "proxy/proxy741": "Lowest 741", "proxy/female_generic": "F generic", "proxy/male_generic": "M generic",
                "proxy/female_muscle_13442": "F athletic", "proxy/male_muscle_13290": "M athletic"}


def _paged(w, key, items, per=18):
    """items: [(label, cb, active)] -> paged list with prev / next."""
    page = STATE["list_page"].get(key, 0)
    pages = max(1, (len(items) + per - 1) // per)
    page = min(page, pages - 1)
    if pages > 1:
        w.buttons([("< prev", lambda: STATE["list_page"].__setitem__(key, max(0, page - 1)), False),
                   (f"page {page + 1} / {pages}", lambda: None, False),
                   ("next >", lambda: STATE["list_page"].__setitem__(key, min(pages - 1, page + 1)), False)], h=0.026)
    for lab, cb, active in items[page * per:(page + 1) * per]:
        w.item(lab, cb, active)


def _clothes_tab(w, engine, node, s, lib):
    worn = {c["id"]: c for c in s["clothes"]}
    w.header(f"Wearing ({len(worn)})")
    for c in list(s["clothes"]):
        info = lib.parts.get(c["id"])
        if info is None:
            continue
        mats = info.get("materials") or []

        def remove(cid=c["id"]):
            s["clothes"] = [x for x in s["clothes"] if x["id"] != cid]
            _mark(engine, node)

        def next_mat(c=c, mats=mats):
            if len(mats) < 2:
                return
            names = [mm["name"] for mm in mats]
            cur = names.index(c.get("material")) if c.get("material") in names else 0
            c["material"] = names[(cur + 1) % len(names)]
            _mark(engine, node)
        mlab = (f"look {[mm['name'] for mm in mats].index(c.get('material')) + 1 if c.get('material') in [mm['name'] for mm in mats] else 1}/{len(mats)}"
                if len(mats) > 1 else "1 look")
        w.buttons([(_pretty(c["id"])[:26], lambda: None, True), (mlab, next_mat, False), ("remove", remove, False)], h=0.026)
        col = c.setdefault("color", [1.0, 1.0, 1.0])

        def tint(v, c=c):
            c["color"] = [v, v, v]
            _mark(engine, node)
        w.slider("tint:" + c["id"], "   brightness", col[0], 0.25, 1.0, tint, lambda v: f"{v * 100:.0f}%")
    w.buttons([("Take all off", lambda: (s.__setitem__("clothes", []), _mark(engine, node)), False),
               ("Random outfit", lambda: _random_outfit(engine, node, s), False)])
    slots = ["all"] + lib.clothes_slots()
    cur = STATE["clothes_slot"] if STATE["clothes_slot"] in slots else "all"
    w.header("Add clothes")
    for i in range(0, len(slots), 5):
        w.buttons([(sl, (lambda sl=sl: STATE.update(clothes_slot=sl)), cur == sl) for sl in slots[i:i + 5]], h=0.025)
    gender = "male" if s["macro"]["gender"] >= 0.5 else "female"
    fitmode = STATE["clothes_gender"]
    w.buttons([("fits this body", lambda: STATE.update(clothes_gender="fit"), fitmode == "fit"),
               ("show everything", lambda: STATE.update(clothes_gender="all"), fitmode == "all")], h=0.025)
    items = []
    for pid in lib.list_parts(kind="clothes", slot=None if cur == "all" else cur,
                              gender=gender if fitmode == "fit" else None):
        info = lib.parts[pid]

        def toggle(pid=pid, info=info):
            if pid in {c["id"] for c in s["clothes"]}:
                s["clothes"] = [x for x in s["clothes"] if x["id"] != pid]
            else:
                if info["slot"] in ("full", "top", "bottom", "feet", "head", "face"):
                    clash = {"full": ("full", "top", "bottom"), "top": ("full", "top"), "bottom": ("full", "bottom"),
                             "feet": ("feet",), "head": ("head",), "face": ("face",)}[info["slot"]]
                    s["clothes"] = [x for x in s["clothes"] if lib.parts.get(x["id"], {}).get("slot") not in clash]
                s["clothes"].append({"id": pid, "material": "", "color": [1.0, 1.0, 1.0]})
            _mark(engine, node)
        items.append((f"{'[x]' if pid in worn else '[ ]'}  {_pretty(pid)}   - {info['slot']}", toggle, pid in worn))
    _paged(w, "clothes:" + cur + fitmode, items)


def _random_outfit(engine, node, s):
    gender = "male" if s["macro"]["gender"] >= 0.5 else "female"
    rnd = CORE.random_spec(_lib(), seed=random.randrange(1 << 30), gender=gender, style=STATE["style"])
    s["clothes"] = rnd["clothes"]
    _mark(engine, node)


def _hair_tab(w, engine, node, s, lib):
    def set_(k, v, extra=None):
        def f():
            s[k] = v
            if extra:
                s.update(extra)
            _mark(engine, node)
        return f
    w.header("Eyes")
    em = sorted(lib.cat["eye_materials"])
    for i in range(0, len(em), 5):
        w.buttons([(e_, set_("eyes", e_), s.get("eyes") == e_) for e_ in em[i:i + 5]], h=0.025)
    w.header("Hair colour")
    col = s["hair_color"]

    def hc(i):
        def f(v):
            col[i] = v
            _mark(engine, node)
        return f
    for i, ch in enumerate(("red", "green", "blue")):
        w.slider(f"hair{i}", f"   {ch}", col[i], 0.1, 1.0, hc(i), lambda v: f"{v * 100:.0f}%")
    hp = s.get("hair") or ""
    if hp in lib.parts and len(lib.parts[hp].get("materials") or []) > 1:
        names = [mm["name"] for mm in lib.parts[hp]["materials"]]
        cur = names.index(s.get("hair_material")) if s.get("hair_material") in names else 0
        w.buttons([(f"hair look {cur + 1}/{len(names)}: {_pretty(names[cur])[:22]}",
                    set_("hair_material", names[(cur + 1) % len(names)]), False)])
    w.header("Hair")
    items = [("(bald)", set_("hair", ""), not hp)]
    items += [(_pretty(p), set_("hair", p, {"hair_material": ""}), hp == p) for p in lib.list_parts(slot="hair")]
    _paged(w, "hair", items, per=14)
    w.header("Eyebrows")
    eb = [(_pretty(p), set_("eyebrows", p), s.get("eyebrows") == p) for p in lib.list_parts(slot="eyebrows")]
    eb.insert(0, ("(none)", set_("eyebrows", ""), not s.get("eyebrows")))
    _paged(w, "brows", eb, per=10)
    w.header("Eyelashes")
    el = [(_pretty(p), set_("eyelashes", p), s.get("eyelashes") == p) for p in lib.list_parts(slot="eyelashes")]
    _paged(w, "lashes", el, per=10)


def _skin_tab(w, engine, node, s, lib):
    col = s["skin_color"]

    def sc(i):
        def f(v):
            col[i] = v
            _mark(engine, node)
        return f
    w.header("Skin tint")
    for i, ch in enumerate(("red", "green", "blue")):
        w.slider(f"skin{i}", f"   {ch}", col[i], 0.3, 1.2, sc(i), lambda v: f"{v * 100:.0f}%")
    w.buttons([("reset tint", lambda: (s.__setitem__("skin_color", [1.0, 1.0, 1.0]), _mark(engine, node)), False)])
    w.header("Skin texture")

    def set_skin(k):
        def f():
            s["skin"] = k
            _mark(engine, node)
        return f
    names = sorted(lib.cat["skins"], key=lambda k: (0 if k.split("_")[0] in ("young", "middleage", "old") else 1, k))
    _paged(w, "skins", [(_pretty(k), set_skin(k), s.get("skin") == k) for k in names], per=16)


def _anim_tab(w, engine, node, bp, rig):
    anims = bp.properties.setdefault("animations", {})
    w.header("Preview")
    pv = bp.properties.get("preview", "idle")

    def preview(slot):
        def f():
            bp.properties["preview"] = slot
            r = rig_of(node)
            if r is not None and not engine._playing:
                _apply_preview(r, bp)
        return f
    sl = list(ANIM.SLOTS)
    for i in range(0, len(sl), 4):
        w.buttons([(x, preview(x), pv == x) for x in sl[i:i + 4]], h=0.026)
    w.header("Moveset")
    cur_ms = str(bp.properties.get("moveset") or "auto")

    def set_ms(ms):
        def f():
            bp.properties["moveset"] = ms
            r = rig_of(node)
            if r is not None:
                r.set_moveset(desired_moveset(node, bp))
                _apply_preview(r, bp)
        return f
    opts = ["auto"] + list(ANIM.MOVESETS)
    for i in range(0, len(opts), 3):
        w.buttons([(o, set_ms(o), cur_ms == o) for o in opts[i:i + 3]], h=0.026)
    w.label(f"now: {rig.moveset if rig else '-'}  (auto: enemies with hitscan get a pistol, zombie skins walk like zombies)")
    w.header("Animation per slot  (Mixamo .fbx, .glb, .bvh)")
    w.label("empty = built-in CC0 motion-captured animation", indent=0.0)
    for slot in ANIM.SLOTS:
        cur = anims.get(slot) or ""

        def load(slot=slot):
            path = engine._native_dialog("open", f"Animation for '{slot}'",
                                         filetypes=[("Animations", "*.fbx *.glb *.gltf *.bvh"), ("All files", "*.*")])
            if path:
                import_animation(engine, slot, path)

        def clear(slot=slot):
            anims.pop(slot, None)
            ensure_rig(engine, node, bp, force=True)
        status = os.path.basename(cur) if cur else "built-in"
        if cur and rig is not None and slot not in rig.user_clips:
            status += "  (!)"
        row = [(f"{slot}: {status}"[:34], lambda: None, bool(cur)), ("Load...", load, False), ("clear", clear, False)]
        clips = _clips_in(engine, cur) if cur else []
        if len(clips) > 1:
            fname, which = ANIM.split_ref(cur)
            names = [c.name for c in clips]
            k = names.index(which) if which in names else 0

            def nxt(slot=slot, fname=fname, names=names, k=k):
                anims[slot] = f"{fname}#{names[(k + 1) % len(names)]}"
                ensure_rig(engine, node, bp, force=True)
            row.insert(1, (f"clip {k + 1}/{len(names)}", nxt, False))
        w.buttons(row, h=0.026)
    w.buttons([("Use files in project 'animations' folder", lambda: tool_scan_animations(engine), False)])
    w.header("Foot IK")
    fi = bool(bp.properties.get("foot_ik", True))
    w.buttons([(f"Foot IK: {'ON' if fi else 'off'}", lambda: bp.properties.__setitem__("foot_ik", not fi), fi)])
    w.label("feet plant on slopes / stairs in Play (pelvis drops, knees bend)")


def _ai_tab(w, engine, node, bp):
    walker = _bp(node, "NPCWalker")
    enemy = _bp(node, "MHEnemy")
    if walker is None and enemy is None:
        w.label("this character has no behaviour yet:")
        w.buttons([("make NPC walker", lambda: engine.attach_blueprint(node, CLS["NPCWalker"]), False),
                   ("make enemy", lambda: (engine.attach_blueprint(node, CLS["MHEnemy"]),
                                           bp.properties.__setitem__("foot_ik", True)), False)])
        return
    b = walker or enemy
    paths = [p.name for p in _paths(engine)]
    w.header("Path")
    cur = b.properties.get("path", "")
    opts = [""] + paths
    for i in range(0, len(opts), 3):
        w.buttons([(p or "(none: wander)", (lambda p=p: b.properties.__setitem__("path", p)), cur == p)
                   for p in opts[i:i + 3]], h=0.026)
    w.buttons([("new path here", lambda: b.properties.__setitem__("path", new_path(
        engine, _m().glm.vec3(node.position.x, node.position.y - (rig_of(node).half if rig_of(node) else 0.9), node.position.z),
        select=False).name), False)])
    if walker is not None:
        w.header("NPC walker")
        modes = ("loop", "pingpong", "random")
        w.buttons([(mo, (lambda mo=mo: walker.properties.__setitem__("mode", mo)), walker.properties.get("mode") == mo)
                   for mo in modes], h=0.026)
        _num(w, walker, "speed", "Walk speed", 0.4, 3.0, "m/s")
        _num(w, walker, "wait_time", "Wait at points", 0.0, 10.0, "s")
        _flag(w, walker, "run", "Run instead of walk")
        _flag(w, walker, "greet_player", "Stop & wave at the player")
    if enemy is not None:
        w.header("Enemy")
        st = enemy.properties.get("attack_style", "melee")
        w.buttons([(x, (lambda x=x: enemy.properties.__setitem__("attack_style", x)), st == x) for x in ("melee", "hitscan")],
                  h=0.026)
        _num(w, enemy, "max_health", "Health", 10, 400, "")
        _num(w, enemy, "attack_damage", "Damage", 1, 60, "")
        _num(w, enemy, "attack_range", "Attack range", 0.8, 4.0, "m")
        _num(w, enemy, "detect_range", "Sight range", 4, 60, "m")
        _num(w, enemy, "fov", "Field of view", 30, 360, "deg")
        _num(w, enemy, "chase_speed", "Chase speed", 0.8, 6.0, "m/s")
        _num(w, enemy, "patrol_speed", "Patrol speed", 0.4, 3.0, "m/s")
        _flag(w, enemy, "require_los", "Needs line of sight")


def _clips_in(engine, ref):
    path, _w = ANIM.split_ref(ref)
    full = _resolve_path(engine, path)
    if not full:
        return []
    try:
        return ANIM.load_source_cached(full)
    except Exception:
        return []


def _num(w, b, key, label, lo, hi, unit):
    def setv(v, key=key):
        b.properties[key] = round(v, 2)
    w.slider(f"{id(b)}:{key}", label, float(b.properties.get(key, lo)), lo, hi, setv, lambda v: f"{v:.1f} {unit}")


def _flag(w, b, key, label):
    on = bool(b.properties.get(key))
    w.buttons([(f"{label}: {'yes' if on else 'no'}", lambda: b.properties.__setitem__(key, not on), on)], h=0.026)


# =========================================================================== lifecycle
def _all_rigs(engine, force=False):
    for node, bp in characters(engine):
        ensure_rig(engine, node, bp, force=force)


def _set_waypoints_visible(engine, vis):
    for n in engine.scene.root.children:
        if _bp(n, "NPCWaypoint") is not None:
            n.visible = vis


def _on_update(engine, dt):
    # slider drag
    d = STATE["drag"]
    if d is not None:
        if engine.input.lmb:
            _drag_apply(engine)
        else:
            STATE["drag"] = None
    # debounced rebuilds from the creator
    now = time.monotonic()
    for k, (t, node) in list(STATE["dirty"].items()):
        if now - t > (0.25 if STATE["drag"] else 0.08):
            STATE["dirty"].pop(k, None)
            if node.parent is not None:
                ensure_rig(engine, node)
                r = rig_of(node)
                bp = _char_bp(node)
                if r is not None and bp is not None and not engine._playing:
                    _apply_preview(r, bp)
    playing = engine._playing
    agents = []
    for node, bp in characters(engine):
        mv = _bp(node, "NPCWalker") or _bp(node, "MHEnemy")
        if mv is not None and getattr(mv, "alive", True) is not False and hasattr(mv, "_vel"):
            agents.append((node, node.position.x, node.position.z, getattr(mv, "_vx", 0.0), getattr(mv, "_vz", 0.0)))
        elif playing:
            agents.append((node, node.position.x, node.position.z, 0.0, 0.0))
    STATE["agents"] = agents
    for node, bp in characters(engine):
        r = rig_of(node)
        if r is not None and not playing:
            want = desired_moveset(node, bp)
            if want != r.moveset:
                r.set_moveset(want)
                _apply_preview(r, bp)
        if r is None or not r.parts or r.parts[0].parent is not node:
            r = ensure_rig(engine, node, bp)
            if r is None:
                continue
            if not playing:
                _apply_preview(r, bp)
        r.update(min(0.1, dt))


def _preview_speed(r, slot):
    c = r.animator.clip(slot) if slot in ("walk", "run", "sprint") else None
    return c.speed if c is not None else 0.0


def _apply_preview(r, bp):
    pv = bp.properties.get("preview", "idle")
    r.animator.stop_action()
    r.animator.set_speed(_preview_speed(r, pv), snap=True)
    if pv in ("talk",):
        r.animator.set_loop(pv)
        if r.animator.loop:
            r.animator.loop[1] = 1.0
    elif pv not in ("idle", "walk", "run", "sprint"):
        r.animator.play(pv, hold=pv == "death")


def _on_play_start(engine):
    _GROUND["t"] = -10.0
    _set_waypoints_visible(engine, False)
    for node, bp in characters(engine):
        r = ensure_rig(engine, node, bp)
        if r is not None:
            r.animator.stop_action()
            r.animator.set_speed(0.0)


def _on_play_stop(engine):
    _set_waypoints_visible(engine, True)
    for node, bp in characters(engine):
        r = rig_of(node)
        if r is not None:
            r.pose.ik_pelvis = 0.0
            r.pose.ik_offsets = [0.0, 0.0]
            _apply_preview(r, bp)


def register(h3d):
    global H3D, CORE, ANIM
    H3D = h3d
    CORE = h3d.load("mh_core.py")
    ANIM = h3d.load("mh_anim.py")
    _patch_shaders()
    _mesh_classes()
    _blueprints()
    m = h3d.module

    h3d.on("update", _on_update)
    h3d.on("init", lambda e: _all_rigs(e))
    h3d.on("project_loaded", lambda e, p: (_all_rigs(e, force=True),
                                           _set_waypoints_visible(e, not (e.mode == "runtime" or e._playing))))
    h3d.on("play_start", _on_play_start)
    h3d.on("play_stop", _on_play_stop)

    h3d.add_tool("Characters: new character", tool_new_character, "#7B5CC4")
    h3d.add_tool("Characters: new NPC walker (+ path)", tool_new_npc, "#7B5CC4")
    h3d.add_tool("Characters: new enemy (foot IK)", tool_new_enemy, "#B5473A")
    h3d.add_tool("Characters: crowd of 6 on a path", tool_crowd)
    h3d.add_tool("Characters: use project 'animations' folder", tool_scan_animations)
    h3d.add_tool("NPC path: new path", tool_new_path)
    h3d.add_tool("NPC path: add point", tool_add_point)

    if h3d.mode == "editor":
        def build_ui(original, self, *a, **kw):
            r = original(self, *a, **kw)
            if self.ui is not None:
                _build_creator(self)
            return r
        h3d.wrap(m.HeavenEngine, "_build_ui", build_ui)


# public helpers (tests / your own scripts)
__all__ = ["place_character", "new_path", "ensure_rig", "rig_of", "import_animation", "characters", "tool_new_npc",
           "tool_new_enemy", "tool_crowd"]
