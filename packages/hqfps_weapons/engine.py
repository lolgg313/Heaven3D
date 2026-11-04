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
# HQFPS's own number.  In Player.prefab the weapon rig hangs off the camera
# as   View_Root(y 1.6) / Camera_Root / Equipment_Controller /
# Equipment_Handler / FP_Equipment(0, -1.6, 0.33),  so the rig origin sits
# 1.6 m below the eye -- cancelling the standing figure the rigs are
# authored around -- and 0.33 m in front of it.  In camera axes:
# (+right, +up, +forward) metres.
HAND_OFFSET = (0.0, -1.6, 0.33)
AIM_DISTANCE = 0.26                       # how far out the SIGHTS sit
AIM_FALLBACK = (-0.055, 1.475, AIM_DISTANCE)
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


_THROWN = {}
# Parts that stay in the hand when the weapon leaves it.
_NOT_THROWN = {"MolotovCocktail": ("Lighter",)}


def _thrown_mesh(engine, weapon):
    """(mesh, material) for the object a throwable actually becomes.

    It is the weapon's own model in its bind pose, centred on itself so the
    node's position is the object's middle, with the lighter (and anything
    else that stays in the hand) left behind.
    """
    hit = _THROWN.get(weapon)
    if hit is not None:
        return hit
    m = _m()
    model, path = _model("models_gun", weapon)
    out = (None, None)
    if model is not None:
        skip = _NOT_THROWN.get(weapon, ())
        pos, nrm, uv, idx, mat = [], [], [], [], None
        base = 0
        for pr in model.primitives():
            if pr["material"] in skip:
                continue
            pos.append(pr["positions"])
            nrm.append(pr["normals"])
            uv.append(pr["uvs"])
            idx.append(pr["indices"] + base)
            base += len(pr["positions"])
            mat = mat or pr["material"]
        if pos:
            P = np.concatenate(pos)
            P = P - (P.min(0) + P.max(0)) * 0.5          # centre it on itself
            mesh = m.Mesh.from_arrays(engine.ctx, P, np.concatenate(nrm),
                                      np.concatenate(uv), np.concatenate(idx),
                                      name="hq_thrown_%s" % weapon)
            b, n = _textures_for(weapon, mat)
            material = m.Material(name="HQ %s (thrown)" % weapon,
                                  base_color=(1.0, 1.0, 1.0, 1.0),
                                  metallic=0.15, roughness=0.55,
                                  base_color_tex=b, normal_tex=n)
            out = (mesh, material)
    _THROWN[weapon] = out
    return out


# ---- ammunition props ----------------------------------------------------
# HQFPS ships a container per calibre and a plain military can with no
# markings; the Item Database says which weapon takes which round, and
# these are the boxes those rounds come in.
AMMO_MODELS = {
    "45ACP": "AmmoPack_.45ACP.glb",
    "357M": "AmmoPack_.357M.glb",
    "5.56x45mm": "AmmoPack_5.56x45mm.glb",
    "12 Gauge Shell": "AmmoPack_ShotgunShells.glb",
    "300WM": "MilitaryAmmoCan_300WM.glb",
    "7.62x39mm": "PlasticAmmoBox_7.62x39mm.glb",
    "7.62x51mm": "PlasticAmmoBox_7.62x51mm.glb",
    "Arrow": "Crossbow_Arrow.glb",
    "Flare": "FlareGun_Flare.glb",
    # no calibre painted on it: the box that feeds every gun
    "": "MilitaryAmmoCan_Empty.glb",
}
_AMMO_GEO = {}


def _ammo_textures(material):
    """(base, normal) for an ammo prop's material."""
    tex = os.path.join(_assets(), "textures", "ammo")
    stem = "AmmoPacks" if str(material).startswith("AmmoPacks") else "Ammo"
    b = os.path.join(tex, stem + ".jpg")
    n = os.path.join(tex, stem + "_NRM.jpg")
    return (b if os.path.isfile(b) else None), (n if os.path.isfile(n) else None)


def _ammo_prop(engine, ammo_type):
    """[(mesh, material, size)] for one ammo box, built once and shared.

    These are static props, so the engine's own glTF importer loads them --
    which is also what keeps their UVs the right way up.
    """
    key = ammo_type or ""
    hit = _AMMO_GEO.get(key)
    if hit is not None:
        return hit
    m = _m()
    fn = AMMO_MODELS.get(key)
    out = []
    if fn:
        path = os.path.join(_assets(), "models_ammo", fn)
        if os.path.isfile(path):
            try:
                for nd in m.GlbImporter.build_nodes(path):
                    for md in _flatten_meshes(nd):
                        mesh = m.Mesh.from_arrays(engine.ctx, md.positions, md.normals,
                                                  md.uvs, md.indices,
                                                  name="hq_ammo_%s" % md.name,
                                                  tangents=md.tangents)
                        mat_name = ((md.material_info or {}).get("name")
                                    if isinstance(md.material_info, dict) else None)
                        b, n = _ammo_textures(mat_name or md.name)
                        out.append((mesh, m.Material(
                            name="HQ Ammo %s" % (mat_name or md.name),
                            base_color=(1.0, 1.0, 1.0, 1.0),
                            metallic=0.2, roughness=0.6,
                            base_color_tex=b, normal_tex=n)))
            except Exception as ex:
                _warn_once("ammoprop_%s" % key,
                           "could not build the %s ammo box (%s)"
                           % (ammo_type or "universal", ex))
        else:
            _warn_once("ammofile_%s" % key, "missing ammo model %s" % fn)
    _AMMO_GEO[key] = out
    return out


def _flatten_meshes(node):
    """Every MeshData under one imported node, names included."""
    out = list(getattr(node, "meshes", ()) or ())
    for ch in getattr(node, "children", ()) or ():
        out += _flatten_meshes(ch)
    return out


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
    "FlareGun": "Guns/Special/FlareGun", "Crossbow": "Bows/Crossbow",
}
# Weapons whose shot clips are not named `<family>_Shoot *`.
_FIRE_PATS = {"Crossbow": ["Bows/Crossbow/*LaunchArrow*"]}
_SOUNDS = {}
_AUDIO_EXT = (".wav", ".ogg", ".mp3")


def _audio_files(paths):
    """Flatten a glob result down to playable clips.

    A pattern like `*Reload*` matches the `Gun_Reloading` FOLDER as happily
    as a clip, and handing the mixer a directory is guaranteed silence, so
    folders are expanded into the clips inside them.
    """
    out = []
    for p in sorted(paths):
        if os.path.isdir(p):
            for root, _dirs, names in os.walk(p):
                out += [os.path.join(root, n) for n in names
                        if n.lower().endswith(_AUDIO_EXT)]
        elif p.lower().endswith(_AUDIO_EXT):
            out.append(p)
    return sorted(out)


def _sounds(weapon, what):
    """Every matching sound file for one weapon and one event."""
    key = (weapon, what)
    hit = _SOUNDS.get(key)
    if hit is not None:
        return hit
    audio = os.path.join(_assets(), "audio", "Equipment")
    pats = []
    fam = _FAMILY.get(weapon)
    if what == "fire":
        pats = _FIRE_PATS.get(weapon) or (
            [fam + "_Shoot *", fam + "_Shoot*"] if fam else [])
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
    elif what.startswith("surface:"):
        pats = ["Surface Effects/%s/BulletImpact*" % what.split(":", 1)[1],
                "Impact Effects/Small Object/*"]
    elif what == "use":
        pats = ["Tools/%s/*" % weapon, "Heals/%s*" % weapon]
    elif what == "explode":
        pats = ["Throwables/Explosive Grenade/*Explosion*"]
    out = []
    for p in pats:
        out = _audio_files(glob.glob(os.path.join(audio, p)))
        if out:
            break
    _SOUNDS[key] = out
    return out


_FLESH_BPS = ("Health", "MHEnemy", "MHCharacter", "EnemyAI", "EnemyFlyShooter",
              "FlyingEnemyAI", "FPSController", "NPCWalker")


def _is_flesh(node):
    """True if the thing that was hit is a creature rather than scenery.

    Bullet holes belong on walls, not on people -- and HQFPS has separate
    flesh and surface impact sounds for the same reason.
    """
    n = node
    while n is not None:
        for b in getattr(n, "blueprints", ()):
            if b.__class__.__name__ in _FLESH_BPS:
                return True
        if getattr(n, "spawn_kind", None) in ("enemy", "player", "flying_enemy"):
            return True
        n = getattr(n, "parent", None)
    return False


def _surface_of(node):
    """Which HQFPS surface set a hit belongs to.

    The template tags every surface in Unity; Heaven3D has no such tag, so
    the material and node names pick the set and concrete is the fallback.
    """
    txt = ((getattr(getattr(node, "material", None), "name", "") or "")
           + " " + (getattr(node, "name", "") or "")).lower()
    for key, folder in (("metal", "Metal"), ("steel", "Metal"), ("iron", "Metal"),
                        ("wood", "Wood"), ("crate", "Wood"), ("plank", "Wood"),
                        ("tree", "Wood"), ("box", "Wood")):
        if key in txt:
            return folder
    return "Concrete"


def _cue(engine, path):
    """Register one sound file under its own path and hand back that key.

    Playing a path straight off disk relies on the engine auto-loading it
    under the same name it was asked for; loading it here, by name, means
    the shot is audible either way.
    """
    try:
        a = engine.audio
        if not a.has_sound(path):
            a.load_sound(path, name=path)
    except Exception:
        pass
    return path


def _play(engine, weapon, what, volume=0.7):
    files = _sounds(weapon, what)
    if not files:
        return
    try:
        engine.audio.play(_cue(engine, random.choice(files)), volume)
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

    def _idle_pose(self):
        """(clip, time) half way through the idle loop -- the pose every
        measurement is taken from."""
        idle = self.clip("Idle")
        return idle, (self.model.clips[idle].duration * 0.5 if idle else 0.0)

    def posed_points(self):
        """Every vertex of this half, skinned into the idle pose."""
        if self.model is None:
            return None
        clip, t = self._idle_pose()
        P = self.model.palette(clip, t)
        pts = []
        for pr in self.model.primitives():
            v = pr["positions"]
            j = pr["joints"].astype(int)
            w = pr["weights"]
            out = np.zeros_like(v)
            for k in range(4):
                M = P[j[:, k]]
                out += w[:, k:k + 1] * (np.einsum("nij,nj->ni", M[:, :3, :3], v) + M[:, :3, 3])
            pts.append(out)
        return np.concatenate(pts) if pts else None

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
        self.stats = catalogue().get(weapon, {})
        self.anchor = self._grip_anchor()
        self.clip = None
        self.t = 0.0
        self.speed = 1.0
        self.loop = True
        self.then = None                    # clip to fall back to when this one ends
        self.aim_off = self._sight_offset()
        self.pos = _Spring(90.0, 14.0)
        self.rot = _Spring(90.0, 14.0)
        self.sway = _Spring(55.0, 11.0)
        self.aim = 0.0
        self.play("Draw", loop=False, then="Idle")

    def _grip_anchor(self):
        """Rig-space point the viewmodel hangs from: its own origin.

        HQFPS hangs the whole rig off the camera at a fixed offset
        (HAND_OFFSET) and lets the authored animation decide where the
        hands go, so there is nothing to measure -- every weapon's idle,
        draw and reload already sit where its author put them.  Measuring a
        grip point per weapon, as this used to, re-anchored every clip on
        the hands and quietly undid that.
        """
        return np.zeros(3)

    def _sight_offset(self):
        """Camera-space offset that lines a weapon's sights up with the
        crosshair, measured off the posed weapon mesh.

        Every gun sits differently in the hands, so a single hand-tuned aim
        pose suits one of them and none of the rest.  Posing the weapon and
        reading its own centreline and sight height gives each one an aim
        pose for free.
        """
        if self.gun.model is None:
            return AIM_FALLBACK
        try:
            pts = self.gun.posed_points()
            # Centreline left-to-right, and the top of the weapon where the
            # sights are.  A percentile rather than the max, so one stray
            # vertex (a sling loop, an aerial) cannot throw the aim off.
            # The eye looks over the REAR sight, so that is the point to
            # put on the crosshair: of the weapon's top surface, the part
            # nearest the shooter.  Percentiles rather than extremes, so a
            # sling loop or an aerial cannot throw the aim off.
            top = pts[pts[:, 1] >= np.percentile(pts[:, 1], 90.0)]
            if not len(top):
                top = pts
            rear = top[top[:, 2] <= np.percentile(top[:, 2], 25.0)]
            if not len(rear):
                rear = top
            sx = float(np.median(rear[:, 0]))
            sy = float(np.percentile(rear[:, 1], 90.0))
            sz = float(np.median(rear[:, 2]))
            # The rig is turned 180 degrees, so its +X points left: the
            # lateral term comes back out with the sign it went in with,
            # and the vertical one flips.
            return (sx, -sy, AIM_DISTANCE - sz)
        except Exception as ex:
            _warn_once("sight_%s" % self.weapon,
                       "could not measure %s's sights (%s); using the default aim pose"
                       % (self.weapon, ex))
            return AIM_FALLBACK

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
        axm, aym, azm = self.aim_off
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
        self.root.rotation_euler = glm.vec3(-(cam.pitch + r[0] * 1.2),
                                            cam.yaw + YAW_FLIP + r[1] * 1.2,
                                            -(r[2] * 1.2 + sw[0] * 8.0))
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
            self.cat = {}
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

        # ---- ammo ----
        def ammo_type(self, weapon=None):
            """The name of the round a weapon takes, e.g. '45ACP'.

            Weapons that take the same round share one reserve, so the type
            -- not the weapon -- is what the reserve is keyed by.
            """
            st = self.cat.get(weapon or self.name, {})
            return st.get("ammo_type")

        def reserve_of(self, weapon=None):
            a = self.ammo_type(weapon)
            return self.reserve.get(a, 0) if a else 0

        def take_reserve(self, n, weapon=None):
            """Draw up to n rounds out of this weapon's pouch."""
            a = self.ammo_type(weapon)
            if not a:
                return 0
            got = min(int(n), self.reserve.get(a, 0))
            self.reserve[a] = self.reserve.get(a, 0) - got
            return got

        def add_reserve(self, n, weapon=None):
            """Put rounds back, up to the calibre's carry limit."""
            a = self.ammo_type(weapon)
            if not a:
                return 0
            st = self.cat.get(weapon or self.name, {})
            cap = int(st.get("ammo_capacity", 0) or 0) or 10 ** 6
            before = self.reserve.get(a, 0)
            self.reserve[a] = min(before + int(n), cap)
            return self.reserve[a] - before

        # ---- lifecycle ----
        def _retire_builtin_weapon(self):
            """Stand down Heaven3D's own `Weapon` on this node.

            A Player Spawn grows an FPSController *and* a built-in Weapon
            when play starts.  That one also fires on LMB and draws its own
            crosshair, so leaving it running gives every click two shots and
            the screen two reticles.  Retiring it is safe: play-stop cleanup
            only removes blueprints it still finds in the lists.
            """
            e = self.engine
            for bp in list(self.node.blueprints):
                if bp.__class__.__name__ != "Weapon":
                    continue
                try:
                    bp.on_end_play()
                except Exception:
                    pass
                self.node.blueprints.remove(bp)
                if e is not None and bp in e._blueprints:
                    e._blueprints.remove(bp)
                H3D.log("stood down the built-in Weapon - the HQ FPS arsenal "
                        "has the trigger now")

        def on_begin_play(self):
            self._retire_builtin_weapon()
            cat = catalogue()
            want = [w.strip() for w in str(self.p("weapons") or "").split(",") if w.strip()]
            self.names = [w for w in want if w in cat] or [w for w in DEFAULT_ORDER if w in cat]
            if not self.names:
                H3D.warn("no HQ FPS weapons found on disk - nothing to equip")
                return
            self.cat = cat
            # Reserves are held per CALIBRE, the way HQFPS's inventory does
            # it: the MP5 and the M1911 both eat 45ACP out of one pouch, the
            # R870 and the double barrel share 12 gauge, and no round goes
            # into a gun that does not take it.  Each type is capped at the
            # carry limit the Item Database gives it.
            for n in self.names:
                st = cat[n]
                mag = int(st.get("magazine", 0) or 0)
                self.ammo[n] = mag
                a = self.ammo_type(n)
                if not a or not mag:
                    continue
                cap = int(st.get("ammo_capacity", 0) or 0) or mag * 4
                want = mag * int(self.p("reserve_magazines"))
                self.reserve[a] = min(max(self.reserve.get(a, 0), want), cap)
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
            self._impact(hit)

        def _impact(self, hit):
            """Mark and sound a hit.

            The hole, spark and flash are the engine's own -- the ones the
            built-in Weapon leaves -- and the engine decides where a hole
            belongs, exactly as it always has.  Only the SOUND is ours:
            HQFPS has separate flesh and surface sets.
            """
            e = self.engine
            try:
                clips = (_sounds(self.name, "hit") if _is_flesh(hit.node)
                         else _sounds(self.name, "surface:" + _surface_of(hit.node)))
                snd = _cue(e, random.choice(clips)) if clips else "bullet_impact"
            except Exception:
                snd = "bullet_impact"
            fx = getattr(e, "spawn_impact_fx", None)
            if fx is None:
                _warn_once("impact_api", "this Heaven3D has no spawn_impact_fx; "
                                         "shots will not leave bullet holes")
                try:
                    e.audio.play_at(snd, hit.point, 0.5)
                except Exception:
                    pass
                return
            fx(hit, sound=snd)

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
                    thud = random.choice(_sounds(self.name, "hit") or ["hit"])
                    e.audio.play_at(_cue(e, thud), hit.point, 0.5)
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
            mesh, material = _thrown_mesh(e, name)
            if mesh is not None:
                node.mesh, node.material = mesh, material
            else:
                # Only if the model could not be read at all.
                _warn_once("thrown_%s" % name,
                           "no mesh for the thrown %s; using a plain ball" % name)
                node.scale = glm.vec3(0.12)
                node.mesh_type = "sphere"
                try:
                    node.mesh = m.Mesh.make_sphere(e.ctx, radius=1.0)
                except Exception:
                    node.mesh = None
                node.material = m.Material(name="HQ Throwable",
                                           base_color=(0.18, 0.2, 0.16, 1.0),
                                           metallic=0.4, roughness=0.5)
            # reuse the engine's own arced explosive rather than reimplement it
            proj = m.BlueprintRegistry.get("Projectile")
            if proj is None:
                return
            boom = _sounds(name, "explode")
            e.attach_blueprint(node, proj, {
                "boom_sound": _cue(e, random.choice(boom)) if boom else "explosion",
                "speed": float(st.get("throw_speed", 14.0)),
                "gravity": 9.81,
                "damage": float(st.get("blast_damage", 90.0) or 90.0),
                "blast_radius": float(st.get("blast_radius", 5.0) or 5.0),
                "lifetime": 3.0,
                "owner_name": self.node.name,
            })
            self.ammo[name] = max(0, self.ammo.get(name, 1) - 1)
            if self.ammo[name] <= 0 and self.reserve_of(name) > 0:
                self.ammo[name] = self.take_reserve(1, name)

        def _use_tool(self):
            st = self.stats
            clip = "Use" if self.vm.duration("Use") > 0.0 else "Switch"
            if self.vm.duration(clip) > 0.0:
                self.vm.play(clip, loop=False, then="Idle")
                self._cool = self.vm.duration(clip)
            else:
                self._cool = 0.4
            _play(self.engine, self.name, "use", 0.6 * float(self.p("volume")))
            _ = st

        # ---- reloading ----
        def _reload_input(self, inp):
            if not inp.was_key_pressed("r"):
                return
            st, name = self.stats, self.name
            mag = int(st.get("magazine", 0) or 0)
            if not mag or self.ammo.get(name, 0) >= mag:
                return
            if not self.p("infinite_reserve") and self.reserve_of(name) <= 0:
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
            take = self.take_reserve(need, name)
            self.ammo[name] = self.ammo.get(name, 0) + take

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
                                                     self.reserve_of(name)), size=22)
                a = self.ammo_type(name)
                if a:
                    e.hud.text(0.86, 0.862, a, size=13, color=(0.72, 0.74, 0.78, 1.0))
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
                # A pickup can name the calibre; otherwise it feeds whatever
                # is in hand.
                want = data.get("ammo_type") if isinstance(data, dict) else None
                if want:
                    for n in self.names:
                        if self.ammo_type(n) == want:
                            self.add_reserve(amt, n)
                            return
                elif self.name:
                    self.add_reserve(amt)

    CLS["HQWeapons"] = HQWeapons

    @blueprint
    class HQAmmoPickup(Blueprint):
        """A box of ammunition, in its own HQFPS container.

        `ammo_type` is a calibre from the Item Database ("45ACP",
        "7.62x39mm", "12 Gauge Shell", ...) and the box wears that
        calibre's own model.  Leave it EMPTY for the universal can: a
        plain military box with no markings that tops up every calibre
        the player carries.
        """

        DEFAULTS = {
            "ammo_type": "",            # "" = the universal box
            "amount": 0,                # 0 = a full carry limit of the round
            "respawn_time": 25.0,
            "spin": True,
            "pickup_range": 1.6,
            "sound": "pickup",
        }

        def __init__(self):
            super().__init__()
            for k, v in self.DEFAULTS.items():
                self.properties.setdefault(k, v)
            self.parts = []
            self._gone = 0.0
            self._spin = 0.0

        def p(self, k):
            return self.properties.get(k, self.DEFAULTS.get(k))

        def on_begin_play(self):
            self._build()
            self._gone = 0.0

        def on_end_play(self):
            for n in self.parts:
                if n.parent is not None:
                    n.remove()
            self.parts = []

        def _build(self):
            m = _m()
            self.on_end_play()
            for mesh, material in _ammo_prop(self.engine, str(self.p("ammo_type") or "")):
                ch = m.SceneNode("ammo part", parent=self.node)
                ch.mesh, ch.material = mesh, material
                ch._heaven3d_imported_part = "hqfps"
                self.parts.append(ch)
            if not self.parts:
                _warn_once("ammo_empty_%s" % self.p("ammo_type"),
                           "no model for the '%s' ammo box" % self.p("ammo_type"))

        # ---- the player walks into it ----
        def on_tick(self, dt):
            if self._gone > 0.0:
                self._gone -= dt
                if self._gone <= 0.0:
                    for n in self.parts:
                        n.visible = True
                return
            if self.p("spin"):
                self._spin = (self._spin + dt * 55.0) % 360.0
                self.node.rotation_euler.y = self._spin
            pc = self.engine.first_blueprint("FPSController")
            if pc is None:
                return
            d = glm.length(glm.vec3(pc.node.world_position())
                           - glm.vec3(self.node.world_position()))
            if d <= float(self.p("pickup_range")):
                self._take(pc)

        def _take(self, pc):
            holder = next((b for b in pc.node.blueprints
                           if b.__class__.__name__ == "HQWeapons"), None)
            if holder is None:
                return
            want = str(self.p("ammo_type") or "")
            amount = int(self.p("amount") or 0)
            given = []
            # The universal can fills every calibre the player is carrying;
            # a marked box fills only its own.
            types = ([t for t in (holder.ammo_type(n) for n in holder.names) if t]
                     if not want else [want])
            for t in dict.fromkeys(types):
                weapon = next((n for n in holder.names if holder.ammo_type(n) == t), None)
                if weapon is None:
                    continue
                cap = int(holder.cat.get(weapon, {}).get("ammo_capacity", 0) or 0)
                n = holder.add_reserve(amount or cap or 30, weapon)
                if n:
                    given.append("%d %s" % (n, t))
            if not given:
                return
            try:
                self.engine.audio.play_at(str(self.p("sound")),
                                          self.node.world_position(), 0.7)
            except Exception:
                pass
            H3D.log("picked up " + ", ".join(given))
            for n in self.parts:
                n.visible = False
            rt = float(self.p("respawn_time"))
            self._gone = rt if rt > 0.0 else 1e9

    CLS["HQAmmoPickup"] = HQAmmoPickup


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


def place_ammo(engine, pos=None, ammo_type="", name=None):
    """Drop one ammo box into the scene."""
    m = _m()
    node = m.SceneNode(engine._unique_node_name(
        name or ("Ammo %s" % (ammo_type or "Box"))), parent=engine.scene.root)
    if pos is not None:
        node.position = pos
    engine.attach_blueprint(node, CLS["HQAmmoPickup"], {"ammo_type": ammo_type})
    return node


def tool_ammo_box(engine):
    n = place_ammo(engine, ammo_type="")
    engine.select_node(n)
    H3D.log("'%s' tops up every calibre the player carries" % n.name)


def tool_ammo_each(engine):
    """One box of every calibre, in a row."""
    glm = _m().glm
    types = [t for t in AMMO_MODELS if t]
    for i, t in enumerate(sorted(types)):
        place_ammo(engine, pos=glm.vec3((i - len(types) * 0.5) * 0.6, 0.1, 0.0),
                   ammo_type=t)
    H3D.log("%d ammo boxes added, one per calibre" % len(types))


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
    h3d.add_tool("HQ FPS: universal ammo box", tool_ammo_box)
    h3d.add_tool("HQ FPS: one ammo box per calibre", tool_ammo_each)


__all__ = ["register", "give_weapons", "place_ammo", "catalogue", "Viewmodel",
           "DEFAULT_ORDER", "AMMO_MODELS"]
