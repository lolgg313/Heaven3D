"""HQ FPS Weapons - the HQ FPS Template weapon system inside Heaven3D.

Twenty GPU-skinned first-person viewmodels sharing one 44-bone bare-skin
arm rig (Arm_Standard, no gloves), each paired with its own weapon mesh and
its own copy of the original baked animation clips.  Stats are the ones from
the Unity project, read out of the Info_*.asset files rather than retyped.

Attach HQWeapons to the player (the node carrying FPSController) and the
whole arsenal comes with it: draw, idle, fire, aim-fire, reload, empty
reload, holster, melee swings and throws, with camera shake and recoil.

Controls
  LMB         fire / swing / throw        RMB   aim
  R           reload                      1-0   pick a weapon
  mouse wheel / Q / E                     next / previous weapon
  F           toggle the flashlight (when it is the held item)

Compatibility
  The GPU skinning hooks are the same `u_mh_*` uniforms mh_characters uses,
  and patching is idempotent, so the two packages can share a scene without
  fighting over the shaders.
"""
import glob
import json
import math
import os
import random

import numpy as np

H3D = None
SKIN = None                       # hq_skin module
CLS = {}
UNIT = 11                         # bone-palette texture unit, shared with mh_characters
STATE = {"warned": set()}

# ---- viewmodel placement -------------------------------------------------
# Where the idle hands sit, in CAMERA axes: (+right, +up, +forward) metres.
# The rig is authored around a standing character, so its hands sit near
# y = 1.44 in rig space; Viewmodel.anchor cancels that out, which is what
# lets one offset place all twenty weapons the same way.
HAND_OFFSET = (0.0, -0.17, 0.38)
AIM_OFFSET = (-0.055, -0.125, 0.32)       # centred and closer when aiming
VIEW_SCALE = 1.0

# The HQFPS rigs keep Unity's convention: the weapon points along the rig's
# +Z and the rig's +X is the character's left.  Heaven3D nodes face -Z, so
# the viewmodel root is turned 180 degrees about Y to line the two up.  That
# turn also reverses how pitch and roll read, hence the negated angles in
# Viewmodel.update -- measured, not guessed: the vertex furthest from the
# hands (the muzzle) sits at +Z on every weapon.
YAW_FLIP = 180.0


def _m():
    return H3D.module


def _warn_once(key, msg):
    if key not in STATE["warned"]:
        STATE["warned"].add(key)
        H3D.warn(msg)


# =========================================================================== shaders
# Byte-identical to mh_characters' hooks on purpose: whichever package loads
# first installs them, the other sees `mh_skin_matrix` already present and
# leaves the source alone.  Diverging here would mean two sets of uniforms
# patched into one shader.
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
        return src, True                      # already installed (by either package)
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
        H3D.warn("skinning hooks only partly applied %s - the viewmodel may render in its bind pose" % ok)
    return all(ok)


# =========================================================================== skinned meshes
def _mesh_classes():
    m = _m()
    moderngl = __import__("moderngl")

    class _SkinVAO:
        """Binds the owning rig's bone palette for the duration of one draw."""
        __slots__ = ("vao", "prog", "mesh")

        def __init__(self, vao, prog, mesh):
            self.vao, self.prog, self.mesh = vao, prog, mesh

        def render(self, *a, **kw):
            half = self.mesh._hq_half
            prog = self.prog
            on = half is not None and half.tex is not None
            if on:
                try:
                    half.tex.use(location=UNIT)
                    prog["u_mh_bones"].value = UNIT
                    prog["u_mh_skin"].value = 1
                except KeyError:
                    on = False
            try:
                return self.vao.render(*a, **kw)
            finally:
                if on:
                    # leave the flag off so unskinned meshes drawn after this
                    # one are not silently deformed by a stale palette
                    prog["u_mh_skin"].value = 0

        def release(self):
            try:
                self.vao.release()
            except Exception:
                pass

        def __getattr__(self, name):
            return getattr(self.vao, name)

    class HQSkinnedMesh(m.Mesh):
        """A Mesh over shared GPU buffers, skinned on the GPU by its half."""

        def __init__(self, ctx, geo, name):
            super().__init__(ctx, name)
            self.vbo, self.ibo = geo.vbo, geo.ibo
            self.vertex_count, self.index_count, self.indexed = geo.nv, geo.ni, True
            self.cpu_positions, self.cpu_indices = geo.cpu_pos, geo.cpu_idx
            self.bounding_box, self.bounding_sphere = geo.bbox, geo.bsphere
            self._hq_skin_vbo = geo.skin_vbo
            self._hq_half = None

        def get_vao(self, program):
            key = ("hq", id(program))
            v = self._vaos.get(key)
            if v is not None:
                return v
            attrs = set(program._members.keys()) if hasattr(program, "_members") else set()
            fmt, names = [], []
            for nm, n in (("in_position", 3), ("in_normal", 3), ("in_tangent", 4), ("in_uv", 2)):
                if nm in attrs:
                    fmt.append("%df" % n)
                    names.append(nm)
                else:
                    fmt.append("%dx4" % n)
            content = [(self.vbo, " ".join(fmt), *names)]
            if "in_mh_joints" in attrs and "in_mh_weights" in attrs:
                content.append((self._hq_skin_vbo, "4f 4f", "in_mh_joints", "in_mh_weights"))
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
            self.vbo = self.ibo = None            # shared: owned by the geometry cache

    CLS["Mesh"] = HQSkinnedMesh
    CLS["moderngl"] = moderngl


# =========================================================================== geometry cache
class _Geo:
    """GPU buffers of one primitive, shared by every viewmodel using it."""
    __slots__ = ("vbo", "ibo", "skin_vbo", "nv", "ni", "bbox", "bsphere",
                 "cpu_pos", "cpu_idx", "material", "name")


_GEO = {}


def _geo_for(engine, path, model):
    """Upload (once) every skinned primitive of a GLB."""
    key = os.path.abspath(path)
    cached = _GEO.get(key)
    if cached is not None:
        return cached
    m = _m()
    glm = m.glm
    out = []
    for pr in model.primitives():
        tmp = m.Mesh.from_arrays(engine.ctx, pr["positions"], pr["normals"], pr["uvs"],
                                 pr["indices"], name="hq_tmp", tangents=pr["tangents"])
        g = _Geo()
        g.vbo, g.ibo = tmp.vbo, tmp.ibo
        tmp.vbo = tmp.ibo = None                  # hand the buffers over, don't free them
        g.nv, g.ni = tmp.vertex_count, tmp.index_count
        g.cpu_pos, g.cpu_idx = tmp.cpu_positions, tmp.cpu_indices
        sk = np.hstack([pr["joints"], pr["weights"]]).astype(np.float32)
        g.skin_vbo = engine.ctx.buffer(sk.tobytes())
        # The bind-pose bounds are useless for a skinned mesh - the pose moves
        # the geometry far outside them - so the bounds are generous on purpose
        # and keep the viewmodel out of the frustum culler.
        g.bbox = (glm.vec3(-2.0, -2.0, -2.0), glm.vec3(2.0, 2.0, 2.0))
        g.bsphere = (glm.vec3(0.0, 0.0, 0.0), 4.0)
        g.material = pr["material"]
        g.name = pr["name"]
        out.append(g)
    _GEO[key] = out
    return out


# =========================================================================== assets
def _assets():
    return H3D.asset("assets")


_MODEL_CACHE = {}


def _model(kind, weapon):
    """SkinnedModel for one half ("models" = arms, "models_gun" = weapon)."""
    path = os.path.join(_assets(), kind, weapon + ".glb")
    if not os.path.isfile(path):
        return None, None
    key = os.path.abspath(path)
    mod = _MODEL_CACHE.get(key)
    if mod is None:
        mod = SKIN.SkinnedModel(path)
        _MODEL_CACHE[key] = mod
    return mod, path


_TEX_CACHE = {}


def _textures_for(weapon, material):
    """(base_color, normal) image paths for a material name.

    Arms always use the shared Arm_Standard set.  A weapon material maps to
    the matching file in its own folder; HQFPS names those `<Material>_01`,
    and the FP models reference exactly one of them (the _02 sets belong to
    the world pickup models), so the first match is the right one.
    """
    if material is None:
        return None, None
    key = (weapon, material)
    if key in _TEX_CACHE:
        return _TEX_CACHE[key]
    tex = os.path.join(_assets(), "textures")
    base = norm = None
    if material.startswith("Arm_"):
        b = os.path.join(tex, "Arm_Standard.png")
        n = os.path.join(tex, "Arm_Standard_NRM.png")
        base = b if os.path.isfile(b) else None
        norm = n if os.path.isfile(n) else None
    else:
        folder = os.path.join(tex, "weapons", weapon)
        for pat in (material + "_01.*", material + ".*", material + "*"):
            hits = [h for h in sorted(glob.glob(os.path.join(folder, pat)))
                    if "_NRM" not in h and "_MASK" not in h]
            if hits:
                base = hits[0]
                stem = os.path.splitext(os.path.basename(base))[0]
                nrm = sorted(glob.glob(os.path.join(folder, stem + "_NRM.*")))
                norm = nrm[0] if nrm else None
                break
    _TEX_CACHE[key] = (base, norm)
    return base, norm


_STATS = None


def _stats():
    global _STATS
    if _STATS is None:
        try:
            with open(os.path.join(_assets(), "weapons.json"), encoding="utf-8") as f:
                _STATS = json.load(f)
        except (OSError, ValueError) as ex:
            H3D.warn("weapons.json unreadable (%s); falling back to defaults" % ex)
            _STATS = {}
    return _STATS


# ---- audio ---------------------------------------------------------------
# Which sound family each weapon draws from.  HQFPS groups the clips by gun
# class rather than by weapon, so several weapons share a family.
_FAMILY = {
    "AKM": "Guns/AssaultRifles/AR1", "M1A": "Guns/AssaultRifles/AR2",
    "F1": "Guns/AssaultRifles/AR3", "M1911": "Guns/Pistols/Pistol",
    "MP5": "Guns/SMGs/SMG1", "R870": "Guns/Shotguns/Shotgun1",
    "DoubleBarrelShotgun": "Guns/Shotguns/Shotgun1",
    "HuntingRifle": "Guns/Snipers/Sniper1", "Revolver": "Guns/Revolvers/Revolver",
    "FlareGun": "Guns/Special/FlareGun", "Crossbow": "Guns/Bows/Crossbow",
}
_SOUNDS = {}


def _sounds(weapon, what):
    """Every matching sound file for one weapon and one event."""
    key = (weapon, what)
    hit = _SOUNDS.get(key)
    if hit is not None:
        return hit
    audio = os.path.join(_assets(), "audio", "Equipment")
    pats = []
    fam = _FAMILY.get(weapon)
    if what == "fire" and fam:
        pats = [fam + "_Shoot *", fam + "_Shoot*"]
    elif what == "dry":
        pats = ["Guns/!Misc/*DryFire*", "Guns/!Misc/*Dry*"]
    elif what == "reload":
        pats = ["Guns/!Handling/*%s*" % weapon, "Guns/!Handling/*Reload*", "!Foley/Weapon_Foley*"]
    elif what == "draw":
        pats = ["!Foley/Weapon_Foley*"]
    elif what == "swing":
        pats = ["Melee/Swing/*"]
    elif what == "hit":
        pats = ["Melee/Hit/*"]
    elif what == "explode":
        pats = ["Throwables/Explosive Grenade/*Explosion*"]
    out = []
    for p in pats:
        out = sorted(glob.glob(os.path.join(audio, p)))
        if out:
            break
    _SOUNDS[key] = out
    return out


def _play(engine, weapon, what, volume=0.7):
    files = _sounds(weapon, what)
    if not files:
        return
    try:
        engine.audio.play(random.choice(files), volume)
    except Exception:
        pass


# =========================================================================== springs
class _Spring:
    """Critically-ish damped spring returning to zero, driven by impulses.

    The viewmodel's recoil, sway and step bob all ride on these: HQFPS does
    the same thing, and it is what keeps a burst from stacking into a jerk.
    """

    def __init__(self, k=110.0, d=16.0):
        self.k, self.d = float(k), float(d)
        self.value = np.zeros(3)
        self.vel = np.zeros(3)

    def kick(self, v):
        self.vel = self.vel + np.asarray(v, dtype=float)

    def push(self, v):
        self.value = self.value + np.asarray(v, dtype=float)

    def update(self, dt):
        # Semi-implicit Euler, substepped so a long frame cannot make the
        # spring explode (dt can spike badly on the first frame after a load).
        n = max(1, min(8, int(dt / 0.008) + 1))
        h = dt / n
        for _ in range(n):
            self.vel += (-self.value * self.k - self.vel * self.d) * h
            self.value += self.vel * h
        if not np.isfinite(self.value).all():
            self.value[:] = 0.0
            self.vel[:] = 0.0
        return self.value


# =========================================================================== one skinned half
# Clip aliases: most weapons name these one way, a couple the other.
_ALIASES = {
    "AimFire": ("AimFire", "FireAim"),
    "ReloadEmpty": ("ReloadEmpty", "EmptyReload"),
    "Reload": ("Reload", "ReloadPart1"),
    "Fire": ("Fire", "Stab1", "ComboAttack", "StrongAttack", "ShortThrow", "Use"),
    "Idle": ("Idle", "Hold"),
    "Hold": ("Hold", "Idle"),
    "Draw": ("Draw", "Show", "PickUp"),
    "Holster": ("Holster", "Hide"),
}


class _Half:
    """One skinned half of a viewmodel: the arms, or the weapon."""

    def __init__(self, engine, parent, weapon, kind, tag):
        self.engine = engine
        self.model, path = _model(kind, weapon)
        self.nodes = []
        self.tex = None
        self.anchor = np.zeros(3)
        if self.model is None:
            return
        m = _m()
        nb = len(self.model.joints)
        self.tex = engine.ctx.texture((nb * 4, 1), 4, dtype="f4")
        mgl = CLS["moderngl"]
        self.tex.filter = (mgl.NEAREST, mgl.NEAREST)
        for g in _geo_for(engine, path, self.model):
            ch = m.SceneNode("%s %s" % (tag, g.name), parent=parent)
            ch._heaven3d_imported_part = "hqfps"
            ch._h3d_locked = True
            mesh = CLS["Mesh"](engine.ctx, g, "hq_%s" % g.name)
            mesh._hq_half = self
            ch.mesh = mesh
            base, nrm = _textures_for(weapon, g.material)
            ch.material = m.Material(name="HQ %s" % (g.material or tag),
                                     base_color=(1.0, 1.0, 1.0, 1.0),
                                     metallic=0.15, roughness=0.55,
                                     base_color_tex=base, normal_tex=nrm,
                                     double_sided=False)
            self.nodes.append(ch)
        self.upload(None, 0.0)

    def clip(self, name):
        """Resolve a canonical clip name against what this model actually has."""
        if self.model is None:
            return None
        if name in self.model.clips:
            return name
        for cand in _ALIASES.get(name, ()):
            if cand in self.model.clips:
                return cand
        return None

    def duration(self, name):
        c = self.clip(name)
        return self.model.clips[c].duration if c else 0.0

    def upload(self, clip, t):
        if self.model is None or self.tex is None:
            return
        p = self.model.palette(self.clip(clip) if clip else None, t)
        # The GLSL mat4 is built from four texels as columns, so the rows go
        # out transposed - same convention mh_characters uses.
        self.tex.write(np.ascontiguousarray(p.transpose(0, 2, 1), dtype=np.float32).tobytes())

    def hand_anchor(self):
        """Mid-point of the two hands in the idle pose, in rig space.

        Subtracting this is what lets one HAND_OFFSET place all 20 weapons:
        the rigs are authored around a standing figure, so without it the
        hands would float about 1.4 m above the camera.
        """
        if self.model is None:
            return np.zeros(3)
        names = [n.get("name", "") for n in self.model.glb.nodes]
        idle = self.clip("Idle")
        G = self.model.global_matrices(idle, 0.0 if idle is None else
                                       self.model.clips[idle].duration * 0.5)
        pts = [G[i][:3, 3] for i, nm in enumerate(names) if nm in ("Hand.L", "Hand.R")]
        if not pts:
            return np.zeros(3)
        return np.mean(pts, axis=0)

    def set_visible(self, on):
        for n in self.nodes:
            n.visible = bool(on)

    def destroy(self):
        for n in self.nodes:
            if n.mesh is not None:
                n.mesh.release()
            if n.parent is not None:
                n.remove()
        self.nodes = []
        if self.tex is not None:
            try:
                self.tex.release()
            except Exception:
                pass
            self.tex = None


# =========================================================================== viewmodel
class Viewmodel:
    """The held weapon: two skinned halves, an animation state and the
    springs that make it move with the camera."""

    def __init__(self, engine, weapon):
        m = _m()
        self.engine = engine
        self.weapon = weapon
        self.root = m.SceneNode("HQ Viewmodel %s" % weapon, parent=engine.scene.root)
        self.root._heaven3d_imported_part = "hqfps"
        self.root._h3d_locked = True
        self.arms = _Half(engine, self.root, weapon, "models", "arms")
        self.gun = _Half(engine, self.root, weapon, "models_gun", "gun")
        self.anchor = self.arms.hand_anchor()
        self.clip = None
        self.t = 0.0
        self.speed = 1.0
        self.loop = True
        self.then = None                    # clip to fall back to when this one ends
        self.pos = _Spring(90.0, 14.0)
        self.rot = _Spring(90.0, 14.0)
        self.sway = _Spring(55.0, 11.0)
        self.aim = 0.0
        self.play("Draw", loop=False, then="Idle")

    # ---- animation ----
    def play(self, clip, loop=False, speed=1.0, then=None):
        self.clip, self.loop, self.speed, self.then = clip, loop, float(speed), then
        self.t = 0.0

    def playing(self, clip):
        return self.clip == clip

    def duration(self, clip):
        """Longest of the two halves, so neither gets cut off."""
        return max(self.arms.duration(clip), self.gun.duration(clip))

    def finished(self):
        d = self.duration(self.clip)
        return (not self.loop) and d > 0.0 and self.t >= d

    def advance(self, dt):
        d = self.duration(self.clip)
        self.t += dt * self.speed
        if d > 0.0:
            if self.loop:
                self.t %= d
            elif self.t >= d:
                self.t = d
                if self.then:
                    self.play(self.then, loop=True)

    # ---- per-frame ----
    def update(self, dt, aiming, look_delta, move, grounded):
        self.advance(dt)
        self.arms.upload(self.clip, self.t)
        self.gun.upload(self.clip, self.t)
        self.aim += ((1.0 if aiming else 0.0) - self.aim) * min(1.0, dt * 10.0)

        # sway from mouse movement and strafing, as HQFPS does
        dx, dy = look_delta
        self.sway.kick((-dx * 0.0016, -dy * 0.0016, 0.0))
        self.sway.kick((-float(move[0]) * 0.004, 0.0, 0.0))
        sw = self.sway.update(dt)
        p = self.pos.update(dt)
        r = self.rot.update(dt)

        cam = self.engine.camera
        glm = _m().glm
        fwd, right = cam.forward(), cam.right()
        up = glm.normalize(glm.cross(right, fwd))

        ox, oy, oz = HAND_OFFSET
        axm, aym, azm = AIM_OFFSET
        k = self.aim
        offx = ox + (axm - ox) * k + sw[0] + p[0]
        offy = oy + (aym - oy) * k + sw[1] + p[1]
        offz = oz + (azm - oz) * k - p[2]          # recoil drives the gun back

        # Place the root so the rig's hand anchor lands exactly on that
        # offset: solve  T + R*(s*anchor) = eye + offset  for T.  After the
        # 180-degree turn the rig's axes in world are  +X = left (-right),
        # +Y = up, +Z = forward, so the anchor comes back out with the signs
        # below.
        ax, ay, az = (self.anchor * VIEW_SCALE).tolist()
        self.root.position = (cam.position
                              + right * (offx + ax)
                              + up * (offy - ay)
                              + fwd * (offz - az))
        self.root.rotation_euler = glm.vec3(-(cam.pitch + r[0] * 2.0),
                                            cam.yaw + YAW_FLIP + r[1] * 2.0,
                                            -(r[2] * 2.0 + sw[0] * 14.0))
        self.root.scale = glm.vec3(VIEW_SCALE)

    def muzzle(self):
        """World position to spawn the flash at: a little ahead of the hands."""
        cam = self.engine.camera
        glm = _m().glm
        return cam.position + cam.forward() * 0.55 + glm.vec3(0.0, -0.06, 0.0)

    def set_visible(self, on):
        self.arms.set_visible(on)
        self.gun.set_visible(on)

    def destroy(self):
        self.arms.destroy()
        self.gun.destroy()
        if self.root.parent is not None:
            self.root.remove()


# =========================================================================== weapon kinds
def _kind(name, st):
    if "melee_swings" in st and "rpm" not in st:
        return "melee"
    if "throw_speed" in st:
        return "throw"
    if "rpm" in st or "fire_interval" in st:
        return "gun"
    return "tool"


def catalogue():
    """{name: stats} for every weapon that has both halves on disk."""
    out = {}
    for name, st in sorted(_stats().items()):
        if not os.path.isfile(os.path.join(_assets(), "models", name + ".glb")):
            continue
        d = dict(st)
        d["kind"] = _kind(name, st)
        out[name] = d
    return out


DEFAULT_ORDER = ["M1911", "Revolver", "MP5", "AKM", "M1A", "F1", "R870",
                 "DoubleBarrelShotgun", "HuntingRifle", "FlareGun", "Crossbow",
                 "CombatKnife", "BaseballBat", "FireAxe",
                 "FragGrenade", "MolotovCocktail", "Flashlight", "Syringe", "Compass"]


# =========================================================================== blueprints
def _blueprints():
    m = _m()
    Blueprint, blueprint, glm = m.Blueprint, m.blueprint, m.glm

    @blueprint
    class HQWeapons(Blueprint):
        """The HQ FPS arsenal.  Attach to the player node (the one with
        FPSController).  LMB fire, RMB aim, R reload, 1-0 / wheel to switch."""

        DEFAULTS = {
            "weapons": "",              # comma separated; empty = the default order
            "start_index": 0,
            "infinite_reserve": False,
            "reserve_magazines": 4,
            "show_hud": True,
            "camera_shake": 1.0,
            "volume": 0.7,
        }

        def __init__(self):
            super().__init__()
            for k, v in self.DEFAULTS.items():
                self.properties.setdefault(k, v)
            self.vm = None
            self.index = 0
            self.names = []
            self.ammo = {}
            self.reserve = {}
            self._cool = 0.0
            self._busy = 0.0            # reload / swing / throw lockout
            self._pending = None        # (time_left, callable) one-shot action
            self._swing = 0
            self._bloom = 0.0
            self._ignore = set()
            self._switch_to = None
            self._queued = None

        def p(self, k):
            return self.properties.get(k, self.DEFAULTS.get(k))

        # ---- lifecycle ----
        def on_begin_play(self):
            cat = catalogue()
            want = [w.strip() for w in str(self.p("weapons") or "").split(",") if w.strip()]
            self.names = [w for w in want if w in cat] or [w for w in DEFAULT_ORDER if w in cat]
            if not self.names:
                H3D.warn("no HQ FPS weapons found on disk - nothing to equip")
                return
            self.cat = cat
            for n in self.names:
                st = cat[n]
                mag = int(st.get("magazine", 0) or 0)
                self.ammo[n] = mag
                self.reserve[n] = mag * int(self.p("reserve_magazines"))
            root = self.node
            while root.parent is not None and root.parent.parent is not None:
                root = root.parent
            self._ignore = {id(n) for n in root.traverse()}
            self.index = max(0, min(int(self.p("start_index")), len(self.names) - 1))
            self._equip(self.index)

        def on_end_play(self):
            if self.vm is not None:
                self.vm.destroy()
                self.vm = None

        # ---- equipping ----
        def _equip(self, i):
            if self.vm is not None:
                self.vm.destroy()
            self.index = i % len(self.names)
            name = self.names[self.index]
            self.vm = Viewmodel(self.engine, name)
            self._cool = 0.0
            self._busy = self.vm.duration("Draw")
            self._pending = None
            self._swing = 0
            self._queued = None
            st = self.cat[name]
            self.vm.speed = float(st.get("draw_time") and 1.0 or 1.0)
            _play(self.engine, name, "draw", 0.5 * float(self.p("volume")))

        def _begin_switch(self, i):
            """Ask for a weapon swap.

            Requests made mid-reload or mid-throw are queued rather than
            dropped: pressing 2 during a reload should still get you weapon
            2 once the reload ends, not nothing at all.
            """
            if self.vm is None or not self.names:
                return
            i %= len(self.names)
            if i == self.index and self._queued is None:
                return
            self._queued = i
            self._try_queued()

        def _try_queued(self):
            if self._queued is None or self._busy > 0.0 or self.vm is None:
                return
            self._switch_to, self._queued = self._queued, None
            self.vm.play("Holster", loop=False)
            self._busy = max(0.12, self.vm.duration("Holster"))
            self._pending = (self._busy, self._do_switch)

        def _do_switch(self):
            i, self._switch_to = self._switch_to, None
            if i is not None:
                self._equip(i)

        # ---- helpers ----
        @property
        def name(self):
            return self.names[self.index] if self.names else None

        @property
        def stats(self):
            return self.cat[self.name]

        def _player(self):
            return self.engine.first_blueprint("FPSController")

        def _shake(self, pitch, yaw):
            """Recoil has to live on the controller's own angles, or the
            controller overwrites it again next tick."""
            s = float(self.p("camera_shake"))
            pc = self._player()
            cam = self.engine.camera
            if pc is not None:
                pc.pitch = max(-89.0, min(89.0, pc.pitch + pitch * s))
                pc.yaw += yaw * s
            else:
                cam.pitch = max(-89.0, min(89.0, cam.pitch + pitch * s))
                cam.yaw += yaw * s

        # ---- the tick ----
        def on_tick(self, dt):
            if self.vm is None or not self.names:
                return
            e = self.engine
            inp = e.input
            self._cool = max(0.0, self._cool - dt)
            self._bloom = max(0.0, self._bloom - dt * 4.0)
            if self._busy > 0.0:
                self._busy = max(0.0, self._busy - dt)
            if self._pending is not None:
                left, fn = self._pending
                left -= dt
                if left <= 0.0:
                    self._pending = None
                    fn()
                else:
                    self._pending = (left, fn)

            self._try_queued()
            self._switch_input(inp)
            aiming = bool(inp.rmb) and self.stats.get("kind") != "throw"
            if self._busy <= 0.0:
                self._use_input(inp, aiming)
                self._reload_input(inp)

            mv = (1.0 if inp.key("d") else 0.0) - (1.0 if inp.key("a") else 0.0)
            self.vm.update(dt, aiming, inp.mouse_delta, (mv, 0.0), True)
            self._idle_state(aiming)
            if self.p("show_hud"):
                self._hud(e, aiming)

        def _switch_input(self, inp):
            if self._busy > 0.0:
                return
            for i in range(min(10, len(self.names))):
                if inp.was_key_pressed(str((i + 1) % 10)):
                    self._begin_switch(i)
                    return
            if inp.wheel:
                self._begin_switch(self.index + (1 if inp.wheel > 0 else -1))
            elif inp.was_key_pressed("e"):
                self._begin_switch(self.index + 1)
            elif inp.was_key_pressed("q"):
                self._begin_switch(self.index - 1)

        def _idle_state(self, aiming):
            """Return to idle/hold once a one-shot clip has run out."""
            vm = self.vm
            if vm.clip in ("Idle", "Hold"):
                want = "Hold" if aiming else "Idle"
                if vm.clip != want and vm.duration(want) > 0.0:
                    keep = vm.t
                    vm.play(want, loop=True)
                    vm.t = keep % max(vm.duration(want), 1e-3)

        # ---- using ----
        def _use_input(self, inp, aiming):
            st = self.stats
            kind = st.get("kind")
            auto = bool(st.get("auto", 0))
            want = inp.lmb if auto else inp.lmb_just_pressed
            if not want or self._cool > 0.0:
                return
            if kind == "gun":
                self._fire(aiming)
            elif kind == "melee":
                self._melee()
            elif kind == "throw":
                self._throw()
            elif kind == "tool":
                self._use_tool()

        def _fire(self, aiming):
            st, name = self.stats, self.name
            mag = int(st.get("magazine", 0) or 0)
            if mag and self.ammo.get(name, 0) <= 0:
                if self.vm.duration("DryFire") > 0.0:
                    self.vm.play("DryFire", loop=False, then="Idle")
                _play(self.engine, name, "dry", 0.5 * float(self.p("volume")))
                self._cool = 0.25
                return
            if mag:
                self.ammo[name] -= 1
            self._cool = float(st.get("fire_interval", 0.2))
            self._bloom = min(self._bloom + 1.0, 5.0)
            clip = "AimFire" if aiming and self.vm.duration("AimFire") > 0.0 else "Fire"
            self.vm.play(clip, loop=False, speed=float(st.get("fire_anim_speed", 1.0)), then="Idle")
            _play(self.engine, name, "fire", float(self.p("volume")))

            pellets = max(1, int(st.get("pellets", 1) or 1))
            spread = float(st.get("spread", 1.0) or 1.0)
            # HQFPS RaySpread is in degrees of cone half-angle for buckshot;
            # a single-ray gun gets a much tighter cone that blooms with use.
            cone = spread if pellets > 1 else spread * 0.25 * (1.0 + self._bloom * 0.5)
            if aiming:
                cone *= 0.35
            dmg = float(st.get("damage", 25.0) or 25.0)
            rng = float(st.get("range", 150.0) or 150.0)
            for _ in range(pellets):
                self._ray(cone, dmg, rng, float(st.get("impulse", 10.0) or 10.0))

            self.engine.spawn_flash(self.vm.muzzle(), color=(1.0, 0.72, 0.30),
                                    intensity=22.0, range=7.0, ttl=0.05)
            # recoil: into the screen and up, with the jitter HQFPS uses
            self.vm.pos.kick((random.uniform(-0.25, 0.25), 0.1, 1.9))   # +z = back along the spring
            self.vm.rot.kick((-2.2, random.uniform(-1.4, 1.4), random.uniform(-1.6, 1.6)))
            scale = 0.25 if aiming else 1.0
            self._shake(dmg * 0.012 * scale * random.uniform(0.8, 1.2),
                        dmg * 0.006 * scale * random.uniform(-1.0, 1.0))

        def _ray(self, cone_deg, damage, rng, impulse):
            e = self.engine
            cam = e.camera
            d = cam.forward()
            ang = math.radians(max(0.0, cone_deg))
            if ang > 1e-6:
                a = random.uniform(0.0, 2.0 * math.pi)
                r = math.tan(ang) * math.sqrt(random.random())
                right = cam.right()
                up = glm.normalize(glm.cross(right, d))
                d = glm.normalize(d + right * (math.cos(a) * r) + up * (math.sin(a) * r))
            hit = e.physics.raycast(cam.position, d, rng, ignore=self._ignore)
            if hit is None:
                return
            self._damage(hit.node, damage, hit.point, d, impulse)
            try:
                e.audio.play_at(random.choice(_sounds(self.name, "hit") or ["hit"]), hit.point, 0.35)
            except Exception:
                pass

        def _damage(self, node, amount, point, direction, impulse):
            e = self.engine
            payload = {"amount": float(amount), "point": point,
                       "direction": direction, "source": "hqfps_weapon"}
            # Health may sit on the node that was hit, on a parent, or on a
            # child hitbox, so all three have to be reached.  Gather the
            # unique set first: ancestors and subtree BOTH include the node
            # itself, and sending to both would deal the damage twice.
            seen, targets = set(), []
            n = node
            while n is not None:
                if id(n) not in seen:
                    seen.add(id(n))
                    targets.append(n)
                n = n.parent
            for ch in node.traverse():
                if id(ch) not in seen:
                    seen.add(id(ch))
                    targets.append(ch)
            for t in targets:
                for b in list(t.blueprints):
                    try:
                        b.on_message("damage", payload)
                    except Exception as ex:
                        H3D.warn("damage handler on '%s' raised: %s" % (t.name, ex))
            rb = getattr(node, "rigidbody", None)
            if rb is not None and rb.body_type == "dynamic":
                rb.add_impulse_at_point(direction * float(impulse) * 0.1, point)

        # ---- melee ----
        def _melee(self):
            st = self.stats
            swings = st.get("melee_swings") or [{"damage": 25.0, "cooldown": 0.5,
                                                 "cast_delay": 0.15, "cast_radius": 0.1,
                                                 "impulse": 10.0, "anim_speed": 1.0}]
            sw = swings[self._swing % len(swings)]
            self._swing += 1
            clips = [c for c in ("Stab1", "Stab2", "ComboAttack", "StrongAttack", "Fire")
                     if self.vm.duration(c) > 0.0]
            clip = clips[(self._swing - 1) % len(clips)] if clips else "Fire"
            self.vm.play(clip, loop=False, speed=float(sw.get("anim_speed", 1.0)), then="Idle")
            self._cool = float(sw.get("cooldown", 0.5))
            _play(self.engine, self.name, "swing", 0.5 * float(self.p("volume")))
            self.vm.pos.kick((0.0, 0.0, 1.2))
            self.vm.rot.kick((-1.5, 0.0, 2.5))
            # the hit lands partway through the swing, not on the keypress
            delay = float(sw.get("cast_delay", 0.15))
            dmg = float(sw.get("damage", 25.0))
            imp = float(sw.get("impulse", 10.0))
            rad = float(sw.get("cast_radius", 0.1))
            rng = float(st.get("melee_range", 1.2) or 1.2)
            self._pending = (delay, lambda: self._melee_hit(dmg, imp, rng, rad))

        def _melee_hit(self, damage, impulse, rng, radius):
            e = self.engine
            cam = e.camera
            fwd, right = cam.forward(), cam.right()
            up = glm.normalize(glm.cross(right, fwd))
            # A swing is a short arc, not a point: sample a few rays across
            # the blade so a near-miss down the centre still connects.
            seen = set()
            for sx, sy in ((0.0, 0.0), (-1.0, 0.0), (1.0, 0.0), (0.0, -1.0), (0.0, 1.0)):
                d = glm.normalize(fwd + right * (sx * radius * 2.2) + up * (sy * radius * 2.2))
                hit = e.physics.raycast(cam.position, d, rng, ignore=self._ignore)
                if hit is None or id(hit.node) in seen:
                    continue
                seen.add(id(hit.node))
                self._damage(hit.node, damage, hit.point, d, impulse)
                try:
                    e.audio.play_at(random.choice(_sounds(self.name, "hit") or ["hit"]), hit.point, 0.5)
                except Exception:
                    pass
                self._shake(0.5, random.uniform(-0.3, 0.3))
                return                      # one swing, one target
            self._shake(0.15, 0.0)

        # ---- throwables ----
        def _throw(self):
            st = self.stats
            clip = "LongThrow" if self.vm.duration("LongThrow") > 0.0 else "ShortThrow"
            self.vm.play(clip, loop=False, speed=float(st.get("throw_anim_speed", 1.0)), then="Idle")
            self._busy = max(0.3, self.vm.duration(clip))
            self._cool = self._busy
            delay = min(float(st.get("throw_spawn_delay", 0.5)), max(0.05, self._busy - 0.05))
            self._pending = (delay, self._spawn_throwable)

        def _spawn_throwable(self):
            e, st = self.engine, self.stats
            name = self.name
            if self.ammo.get(name, 0) <= 0 and int(st.get("magazine", 0) or 0):
                return
            cam = e.camera
            node = m.SceneNode("HQ %s (thrown)" % name, parent=e.scene.root)
            node.position = cam.position + cam.forward() * 0.5
            node.rotation_euler = glm.vec3(cam.pitch, cam.yaw, 0.0)
            node.scale = glm.vec3(0.12)
            node.mesh_type = "sphere"
            try:
                node.mesh = m.Mesh.make_sphere(e.ctx, radius=1.0)
            except Exception:
                node.mesh = None
            node.material = m.Material(name="HQ Throwable", base_color=(0.18, 0.2, 0.16, 1.0),
                                       metallic=0.4, roughness=0.5)
            # reuse the engine's own arced explosive rather than reimplement it
            proj = m.BlueprintRegistry.get("Projectile")
            if proj is None:
                return
            e.attach_blueprint(node, proj, {
                "speed": float(st.get("throw_speed", 14.0)),
                "gravity": 9.81,
                "damage": float(st.get("blast_damage", 90.0) or 90.0),
                "blast_radius": float(st.get("blast_radius", 5.0) or 5.0),
                "lifetime": 3.0,
                "owner_name": self.node.name,
            })
            self.ammo[name] = max(0, self.ammo.get(name, 1) - 1)
            if self.ammo[name] <= 0 and self.reserve.get(name, 0) > 0:
                self.reserve[name] -= 1
                self.ammo[name] = 1

        def _use_tool(self):
            st = self.stats
            clip = "Use" if self.vm.duration("Use") > 0.0 else "Switch"
            if self.vm.duration(clip) > 0.0:
                self.vm.play(clip, loop=False, then="Idle")
                self._cool = self.vm.duration(clip)
            else:
                self._cool = 0.4
            _ = st

        # ---- reloading ----
        def _reload_input(self, inp):
            if not inp.was_key_pressed("r"):
                return
            st, name = self.stats, self.name
            mag = int(st.get("magazine", 0) or 0)
            if not mag or self.ammo.get(name, 0) >= mag:
                return
            if not self.p("infinite_reserve") and self.reserve.get(name, 0) <= 0:
                return
            empty = self.ammo.get(name, 0) <= 0 and int(st.get("has_empty_reload", 0))
            clip = "ReloadEmpty" if empty and self.vm.duration("ReloadEmpty") > 0.0 else "Reload"
            dur = float(st.get("empty_reload_time" if empty else "reload_time", 2.0) or 2.0)
            anim = self.vm.duration(clip)
            self.vm.play(clip, loop=False,
                         speed=(anim / dur if anim > 0.0 and dur > 0.0 else 1.0), then="Idle")
            self._busy = dur
            self._cool = dur
            _play(self.engine, name, "reload", 0.55 * float(self.p("volume")))
            self._pending = (dur, self._finish_reload)

        def _finish_reload(self):
            st, name = self.stats, self.name
            mag = int(st.get("magazine", 0) or 0)
            need = mag - self.ammo.get(name, 0)
            if need <= 0:
                return
            if self.p("infinite_reserve"):
                self.ammo[name] = mag
                return
            take = min(need, self.reserve.get(name, 0))
            self.ammo[name] = self.ammo.get(name, 0) + take
            self.reserve[name] = self.reserve.get(name, 0) - take

        # ---- hud ----
        def _hud(self, e, aiming):
            if e.hud is None:
                return
            st, name = self.stats, self.name
            kind = st.get("kind")
            if kind == "gun" and not aiming:
                e.hud.crosshair(spread=0.004 + self._bloom * 0.006)
            label = name.replace("_", " ")
            e.hud.text(0.72, 0.94, label, size=16, color=(0.85, 0.87, 0.9, 1.0))
            mag = int(st.get("magazine", 0) or 0)
            if mag:
                e.hud.text(0.86, 0.895, "%d / %d" % (self.ammo.get(name, 0),
                                                     self.reserve.get(name, 0)), size=22)
            elif kind == "melee":
                e.hud.text(0.86, 0.895, "MELEE", size=18, color=(0.8, 0.8, 0.85, 1.0))
            if self._busy > 0.0 and self.vm.clip in ("Reload", "ReloadEmpty"):
                dur = float(st.get("reload_time", 2.0) or 2.0)
                e.hud.bar(0.82, 0.925, 0.15, 0.016, 1.0 - self._busy / max(dur, 1e-6),
                          fg=(0.9, 0.75, 0.2, 1.0))
                e.hud.text(0.845, 0.86, "RELOADING", size=14, color=(0.95, 0.8, 0.3, 1.0))

        def on_message(self, event, data):
            if event == "add_ammo":
                amt = int(data.get("amount", 0)) if isinstance(data, dict) else int(data or 0)
                if self.name:
                    self.reserve[self.name] = self.reserve.get(self.name, 0) + amt

    CLS["HQWeapons"] = HQWeapons


# =========================================================================== tools
def give_weapons(engine, node=None, weapons="", start=0):
    """Attach the arsenal to `node` (default: whatever has the FPSController)."""
    m = _m()
    if node is None:
        pc = engine.first_blueprint("FPSController")
        node = pc.node if pc is not None else None
    if node is None:
        H3D.warn("no FPSController in the scene - put the player down first")
        return None
    for bp in list(node.blueprints):
        if bp.__class__.__name__ == "HQWeapons":
            return bp
    return engine.attach_blueprint(node, CLS["HQWeapons"],
                                   {"weapons": weapons, "start_index": int(start)})


def tool_give(engine):
    bp = give_weapons(engine)
    if bp is not None:
        H3D.log("HQ FPS weapons attached to '%s'" % bp.node.name)


def _holders(engine):
    out = []
    for n in engine.scene.root.traverse():
        for bp in n.blueprints:
            if bp.__class__.__name__ == "HQWeapons":
                out.append(bp)
    return out


def _on_play_stop(engine):
    for bp in _holders(engine):
        if getattr(bp, "vm", None) is not None:
            bp.vm.destroy()
            bp.vm = None


def register(h3d):
    global H3D, SKIN
    H3D = h3d
    SKIN = h3d.load("hq_skin.py")
    _patch_shaders()
    _mesh_classes()
    _blueprints()
    h3d.on("play_stop", _on_play_stop)
    h3d.add_tool("HQ FPS: give the player every weapon", tool_give, "#B5473A")


__all__ = ["register", "give_weapons", "catalogue", "Viewmodel", "DEFAULT_ORDER"]
