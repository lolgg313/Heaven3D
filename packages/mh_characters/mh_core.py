"""MakeHuman character generation in pure numpy (no Blender, no OpenGL).

Implements what MPFB 2 / MakeHuman do when you build a character:
  * macro modifiers (gender, age, muscle, weight, height, proportions, breasts,
    African / Asian / Caucasian) blended exactly like MPFB's TargetService,
  * regional modifiers (nose, chin, stomach, legs, ...),
  * fitting proxies and clothes to the morphed body (.mhclo reference
    triangles + scaled offsets) and hiding the body under the clothes
    (delete_verts),
  * the Mixamo-compatible skeleton of MPFB (rig.mixamo.json) placed from the
    morphed joint helpers, plus skin weights transferred to every garment.

Output is in Heaven3D model space: metres, +Y up, the character faces -Z
(the same forward axis EnemyAI / FPSController use) and stands on y = 0.
"""
import json
import math
import os
import random

import numpy as np

DM = 0.1                      # MakeHuman units are decimetres

MACRO_DEFAULTS = {"gender": 0.5, "age": 0.5, "muscle": 0.5, "weight": 0.5, "height": 0.5,
                  "proportions": 0.5, "cupsize": 0.5, "firmness": 0.5,
                  "african": 0.0, "asian": 0.0, "caucasian": 1.0}

# macro.json of MPFB (parts with lowest/highest/low/high).  Age is limited to
# adults here (young .. old) - no child or baby bodies in a game NPC kit.
_MACRO = {
    "gender": [(-0.01, 1.01, "female", "male")],
    "age": [(0.49999, 1.01, "young", "old")],
    "muscle": [(-0.01, 0.49998, "minmuscle", "averagemuscle"), (0.49999, 1.01, "averagemuscle", "maxmuscle")],
    "weight": [(-0.01, 0.49998, "minweight", "averageweight"), (0.49999, 1.01, "averageweight", "maxweight")],
    "proportions": [(-0.01, 0.4999, "uncommonproportions", ""), (0.50, 1.01, "", "idealproportions")],
    "height": [(-0.01, 0.49, "minheight", ""), (0.51, 1.01, "", "maxheight")],
    "cupsize": [(-0.01, 0.49998, "mincup", "averagecup"), (0.49999, 1.01, "averagecup", "maxcup")],
    "firmness": [(-0.01, 0.4998, "minfirmness", "averagefirmness"), (0.4999, 1.01, "averagefirmness", "maxfirmness")],
}

SLOTS_SINGLE = ("hair", "eyebrows", "eyelashes", "eyes")
BODY_SLOT_ORDER = ("underwear", "legs", "bottom", "top", "full", "arms", "hands", "feet", "jewelry", "face",
                   "eyewear", "head", "accessory")


def age_years(v):
    """MakeHuman age slider -> years (0.5 = 25, 1.0 = 90)."""
    v = min(1.0, max(0.5, float(v)))
    return 25.0 + (v - 0.5) * 2.0 * 65.0


def _interp(name, value):
    out = []
    for lowest, highest, low, high in _MACRO[name]:
        if lowest < value < highest:
            pct = (value - lowest) / (highest - lowest)
            if low:
                out.append((low, round(1.0 - pct, 4)))
            if high:
                out.append((high, round(pct, 4)))
    return out


def macro_targets(macro, cutoff=0.01):
    """MPFB TargetService.calculate_target_stack_from_macro_info_dict."""
    m = dict(MACRO_DEFAULTS)
    m.update({k: float(v) for k, v in (macro or {}).items() if k in MACRO_DEFAULTS})
    m["age"] = min(1.0, max(0.5, m["age"]))
    for k in ("gender", "muscle", "weight", "height", "proportions", "cupsize", "firmness"):
        m[k] = min(1.0, max(0.0, m[k]))
    race = {r: max(0.0, m[r]) for r in ("african", "asian", "caucasian")}
    s = sum(race.values())
    race = {r: (v / s if s > 1e-6 else 1.0 / 3.0) for r, v in race.items()}
    c = {k: _interp(k, m[k]) for k in _MACRO}
    out = []
    for r, rw in race.items():
        if rw > 0.0001:
            for a, aw in c["age"]:
                for g, gw in c["gender"]:
                    w = rw * gw * aw
                    if w > cutoff:
                        out.append((f"macrodetails/{r}-{g}-{a}", w))
    for g, gw in c["gender"]:
        for a, aw in c["age"]:
            for mu, mw in c["muscle"]:
                for we, ww in c["weight"]:
                    base = gw * aw * mw * ww
                    if base > cutoff:
                        out.append((f"macrodetails/universal-{g}-{a}-{mu}-{we}", base))
                    for h, hw in c["height"]:
                        if base * hw > cutoff:
                            out.append((f"macrodetails/height/{g}-{a}-{mu}-{we}-{h}", base * hw))
                    for p, pw in c["proportions"]:
                        if base * pw > cutoff:
                            out.append((f"macrodetails/proportions/{g}-{a}-{mu}-{we}-{p}", base * pw))
                    if g == "female":
                        for cu, cw in c["cupsize"]:
                            for fi, fw in c["firmness"]:
                                w = aw * mw * ww * cw * fw
                                name = f"breast/female-{a}-{mu}-{we}-{cu}-{fi}"
                                if w > cutoff and "averagecup-averagefirmness" not in name:
                                    out.append((name, w))
    return out


# --------------------------------------------------------------------------- library
class Library:
    """The shipped CC0 asset library (assets/): catalog, base mesh, targets, parts."""

    def __init__(self, root):
        self.root = root
        with open(os.path.join(root, "catalog.json"), encoding="utf-8") as f:
            self.cat = json.load(f)
        core = np.load(os.path.join(root, "core.npz"))
        self.base_v = core["base_v"].astype(np.float64)
        self.body_src = core["body_src"].astype(np.int64)
        self.body_uv = core["body_uv"].astype(np.float32)
        self.body_tris = core["body_tris"].astype(np.int64)
        ji, jo = core["joint_idx"].astype(np.int64), core["joint_off"]
        self.joints = {n: ji[jo[i]:jo[i + 1]] for i, n in enumerate(self.cat["joints"])}
        self.w_idx = core["w_idx"].astype(np.int64)
        self.w_val = core["w_val"].astype(np.float32)
        self.bones = self.cat["bones"]
        self.bone_names = [b["name"] for b in self.bones]
        self.parents = np.array([b["parent"] for b in self.bones], np.int64)
        self.parts = self.cat["parts"]
        self.mods = {m["name"]: m for m in self.cat["modifiers"]}
        self._targets = None
        self._tcache = {}
        self._pcache = {}
        self._known_targets = set(self.cat.get("targets", []))

    # -- raw data ---------------------------------------------------------
    def path(self, rel):
        return os.path.join(self.root, *rel.split("/")) if rel else None

    def target(self, name):
        t = self._tcache.get(name)
        if t is None:
            if name not in self._known_targets:
                return None
            if self._targets is None:
                self._targets = np.load(os.path.join(self.root, "targets.npz"))
            i = self._targets[name + ".i"].astype(np.int64)
            t = (i, self._targets[name + ".d"].astype(np.float64).reshape(-1, 3)) if len(i) else False
            self._tcache[name] = t
        if t is False:
            return None
        return t

    def part(self, pid):
        p = self._pcache.get(pid)
        if p is None:
            info = self.parts[pid]
            z = np.load(self.path(info["file"]))
            p = {k: z[k] for k in z.files}
            p["ref"] = p["ref"].astype(np.int64)
            p["w"] = p["w"].astype(np.float64)
            p["off"] = p["off"].astype(np.float64)
            p["src"] = p["src"].astype(np.int64)
            p["uv"] = p["uv"].astype(np.float32)
            p["tris"] = p["tris"].astype(np.int64)
            p["delete"] = p["delete"].astype(np.int64)
            p["info"] = info
            self._pcache[pid] = p
        return p

    # -- browsing ----------------------------------------------------------
    def list_parts(self, slot=None, kind=None, gender=None):
        out = []
        for pid, info in sorted(self.parts.items()):
            if slot and info["slot"] != slot:
                continue
            if kind and info["kind"] != kind:
                continue
            if gender and info["gender"] not in ("any", gender):
                continue
            out.append(pid)
        return out

    def clothes_slots(self):
        seen = []
        for s in BODY_SLOT_ORDER:
            if any(i["slot"] == s and i["kind"] == "clothes" for i in self.parts.values()):
                seen.append(s)
        return seen


# --------------------------------------------------------------------------- spec
def default_spec(gender="male"):
    male = gender != "female"
    return {
        "macro": dict(MACRO_DEFAULTS, gender=1.0 if male else 0.0),
        "modifiers": {},
        "body": "basemesh",
        "skin": "young_caucasian_male" if male else "young_caucasian_female",
        "skin_color": [1.0, 1.0, 1.0],
        "eyes": "brown",
        "eyes_mesh": "eyes/low-poly",
        "eyebrows": "eyebrows/eyebrow001" if male else "eyebrows/eyebrow010",
        "eyelashes": "eyelashes/eyelashes01" if male else "eyelashes/eyelashes02",
        "hair": "hair/short02" if male else "hair/long01",
        "hair_material": "",
        "hair_color": [1.0, 1.0, 1.0],
        "clothes": ([{"id": "clothes/male_casualsuit01"}, {"id": "clothes/shoes01"}] if male else
                    [{"id": "clothes/female_casualsuit01"}, {"id": "clothes/shoes03"}]),
    }


def normalize_spec(spec, lib=None):
    s = default_spec()
    if isinstance(spec, dict):
        for k, v in spec.items():
            s[k] = v
    s["macro"] = dict(MACRO_DEFAULTS, **{k: float(v) for k, v in (s.get("macro") or {}).items()
                                         if k in MACRO_DEFAULTS})
    s["modifiers"] = {k: float(v) for k, v in (s.get("modifiers") or {}).items()}
    cl = []
    for c in s.get("clothes") or []:
        if isinstance(c, str):
            c = {"id": c}
        if isinstance(c, dict) and c.get("id"):
            if lib is None or c["id"] in lib.parts:
                cl.append({"id": c["id"], "material": c.get("material", ""),
                           "color": list(c.get("color", [1.0, 1.0, 1.0]))[:3]})
    s["clothes"] = cl
    for k in ("skin_color", "hair_color"):
        v = list(s.get(k) or [1, 1, 1])[:3]
        s[k] = [float(x) for x in v] + [1.0] * (3 - len(v))
    return s


def spec_key(spec):
    return json.dumps(spec, sort_keys=True)


# --------------------------------------------------------------------------- geometry helpers
def _vertex_normals(pos, tris, n_src=None, src=None):
    """Smooth normals.  When `src` is given, faces are accumulated on the
    source vertex so UV seams stay smooth."""
    if src is not None:
        acc = np.zeros((n_src, 3))
        p = pos
        a, b, c = p[tris[:, 0]], p[tris[:, 1]], p[tris[:, 2]]
        fn = np.cross(b - a, c - a)
        for k in range(3):
            np.add.at(acc, src[tris[:, k]], fn)
        n = acc[src]
    else:
        n = np.zeros_like(pos)
        a, b, c = pos[tris[:, 0]], pos[tris[:, 1]], pos[tris[:, 2]]
        fn = np.cross(b - a, c - a)
        for k in range(3):
            np.add.at(n, tris[:, k], fn)
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    ln[ln < 1e-12] = 1.0
    return (n / ln).astype(np.float32)


def _fit(coords, part):
    ref, w, off = part["ref"], part["w"], part["off"]
    pos = coords[ref[:, 0]] * w[:, 0:1] + coords[ref[:, 1]] * w[:, 1:2] + coords[ref[:, 2]] * w[:, 2:3]
    scale = np.ones(3)
    for ax in range(3):
        if part["has_scale"][ax]:
            a, b, den = part["scale"][ax]
            if den:
                scale[ax] = abs(coords[int(a), ax] - coords[int(b), ax]) / float(den)
    return pos + off * scale


def _transfer_weights(lib, ref, w, nb):
    n = len(ref)
    dense = np.zeros((n, nb), np.float32)
    rows = np.arange(n)
    for k in range(3):
        wk = w[:, k].astype(np.float32)
        for j in range(4):
            np.add.at(dense, (rows, lib.w_idx[ref[:, k], j]), wk * lib.w_val[ref[:, k], j])
    return _top4(dense)


def _top4(dense):
    dense = np.maximum(dense, 0.0)
    idx = np.argsort(-dense, axis=1)[:, :4]
    val = np.take_along_axis(dense, idx, axis=1)
    s = val.sum(axis=1, keepdims=True)
    bad = s[:, 0] < 1e-8
    s[s < 1e-8] = 1.0
    val = val / s
    if np.any(bad):                       # unweighted: follow the hips
        idx[bad] = 0
        val[bad] = (1.0, 0.0, 0.0, 0.0)
    return idx.astype(np.float32), val.astype(np.float32)


def _vec_roll_to_mat3(nor, roll):
    """Blender's bone matrix from a direction (Z-up space) and a roll."""
    nor = nor / max(np.linalg.norm(nor), 1e-12)
    target = np.array([0.0, 1.0, 0.0])
    axis = np.cross(target, nor)
    if axis.dot(axis) > 1e-10:
        axis /= np.linalg.norm(axis)
        theta = math.acos(max(-1.0, min(1.0, float(target.dot(nor)))))
        b = _axis_angle(axis, theta)
    else:
        b = np.diag([1.0, 1.0, 1.0]) if target.dot(nor) > 0 else np.diag([-1.0, -1.0, 1.0])
    return _axis_angle(nor, roll) @ b


def _axis_angle(axis, ang):
    x, y, z = axis
    c, s = math.cos(ang), math.sin(ang)
    t = 1.0 - c
    return np.array([[t * x * x + c, t * x * y - s * z, t * x * z + s * y],
                     [t * x * y + s * z, t * y * y + c, t * y * z - s * x],
                     [t * x * z - s * y, t * y * z + s * x, t * z * z + c]])


# obj (Y-up) <-> Blender (Z-up) basis change, and the 180 deg turn so the
# character faces -Z (Heaven3D forward).
_OBJ_FROM_BL = np.array([[1.0, 0, 0], [0, 0, 1.0], [0, -1.0, 0]])
_TURN = np.diag([-1.0, 1.0, -1.0])


# --------------------------------------------------------------------------- model
class Part:
    __slots__ = ("pid", "slot", "positions", "normals", "uvs", "indices", "joints", "weights", "material")

    def __init__(self, pid, slot, positions, normals, uvs, indices, joints, weights, material):
        self.pid, self.slot = pid, slot
        self.positions, self.normals, self.uvs = positions, normals, uvs
        self.indices, self.joints, self.weights, self.material = indices, joints, weights, material


class CharacterModel:
    """A built character: parts (skinned geometry + materials) and its skeleton."""

    def __init__(self):
        self.parts = []
        self.bone_names = []
        self.parents = None
        self.rest_world = None        # (B,4,4) bone frames in model space
        self.rest_local = None        # (B,4,4)
        self.inv_bind = None          # (B,4,4)
        self.bone_len = None
        self.heads = None
        self.tails = None
        self.height = 1.75
        self.metrics = {}
        self.spec = None

    def bone(self, name):
        try:
            return self.bone_names.index(name)
        except ValueError:
            return -1


def _material(lib, entry, color, slot):
    mat = {"diffuse": None, "normal": None, "color": [1.0, 1.0, 1.0, 1.0], "alpha_mode": "OPAQUE",
           "double_sided": False, "roughness": 0.7, "cutoff": 0.5}
    if entry:
        mat["diffuse"] = lib.path(entry.get("diffuse"))
        mat["normal"] = lib.path(entry.get("normal"))
        base = entry.get("color", [1, 1, 1])
        mat["color"] = [float(base[i]) * float(color[i]) for i in range(3)] + [1.0]
        mat["double_sided"] = bool(entry.get("double_sided"))
        if entry.get("alpha"):
            mat["alpha_mode"] = "MASK"
            mat["double_sided"] = True
    else:
        mat["color"] = [0.55 * float(color[0]), 0.55 * float(color[1]), 0.58 * float(color[2]), 1.0]
    if slot in ("hair",):
        mat["roughness"], mat["cutoff"] = 0.55, 0.45
    elif slot in ("eyebrows", "eyelashes"):
        mat["roughness"], mat["cutoff"], mat["alpha_mode"], mat["double_sided"] = 0.8, 0.35, "MASK", True
    elif slot == "eyes":
        mat["roughness"] = 0.15
    elif slot == "skin":
        mat["roughness"] = 0.62
    return mat


def _pick_material(info, name):
    mats = info.get("materials") or []
    if not mats:
        return None
    if name:
        for m in mats:
            if m["name"] == name:
                return m
    return mats[0]


def build(lib, spec):
    """spec (dict) -> CharacterModel.  Pure numpy, ~50-300 ms."""
    spec = normalize_spec(spec, lib)
    coords = lib.base_v.copy()
    for name, w in macro_targets(spec["macro"]):
        t = lib.target(name)
        if t is not None:
            coords[t[0]] += w * t[1]
    for mname, val in spec["modifiers"].items():
        mod = lib.mods.get(mname)
        if mod is None or abs(val) < 1e-4:
            continue
        for tname in (mod.get("pos") if val > 0 else mod.get("neg")) or []:
            t = lib.target(tname)
            if t is not None:
                coords[t[0]] += abs(val) * t[1]

    nb = len(lib.bones)
    model = CharacterModel()
    model.spec = spec
    deleted = np.zeros(len(coords), bool)
    wanted = []                         # (pid, slot, material entry, colour)
    for c in spec["clothes"]:
        info = lib.parts.get(c["id"])
        if info is None:
            continue
        wanted.append((c["id"], info["slot"], _pick_material(info, c.get("material")), c.get("color", [1, 1, 1])))
        d = lib.part(c["id"])["delete"]
        if len(d):
            deleted[d[d < len(deleted)]] = True
    for slot in ("hair", "eyebrows", "eyelashes"):
        pid = spec.get(slot)
        if pid and pid in lib.parts:
            info = lib.parts[pid]
            col = spec["hair_color"] if slot != "eyelashes" else [1, 1, 1]
            mname = spec.get("hair_material") if slot == "hair" else ""
            wanted.append((pid, slot, _pick_material(info, mname), col))
    eyes = spec.get("eyes_mesh") or "eyes/low-poly"
    if eyes in lib.parts:
        em = lib.cat["eye_materials"].get(spec.get("eyes") or "brown") or next(iter(lib.cat["eye_materials"].values()))
        wanted.append((eyes, "eyes", {"diffuse": em["diffuse"], "color": [1, 1, 1]}, [1, 1, 1]))

    # ---- body -----------------------------------------------------------
    skin = lib.cat["skins"].get(spec.get("skin") or "")
    skin_entry = {"diffuse": skin["diffuse"], "normal": skin.get("normal"), "color": [1, 1, 1]} if skin else None
    body_pid = spec.get("body") or "basemesh"
    if body_pid != "basemesh" and body_pid in lib.parts:
        bp = lib.part(body_pid)
        pos = _fit(coords, bp)
        main = bp["ref"][np.arange(len(bp["ref"])), np.argmax(bp["w"], axis=1)]
        vdel = deleted[main]
        tris = bp["tris"]
        keep = ~vdel[bp["src"][tris]].any(axis=1)
        tris = tris[keep]
        rp = pos[bp["src"]]
        nrm = _vertex_normals(pos[bp["src"]], tris, len(pos), bp["src"])
        j, w = _transfer_weights(lib, bp["ref"][bp["src"]], bp["w"][bp["src"]], nb)
        body = (rp, nrm, bp["uv"], tris, j, w)
    else:
        tris = lib.body_tris
        keep = ~deleted[lib.body_src[tris]].any(axis=1)
        tris = tris[keep]
        rp = coords[lib.body_src]
        nrm = _vertex_normals(rp, tris, len(coords), lib.body_src)
        j = lib.w_idx[lib.body_src].astype(np.float32)
        w = lib.w_val[lib.body_src].astype(np.float32)
        body = (rp, nrm, lib.body_uv, tris, j, w)
    geoms = [("body", "skin", body, _material(lib, skin_entry, spec["skin_color"], "skin"))]

    # ---- clothes / hair / eyes ------------------------------------------
    for pid, slot, mentry, col in wanted:
        p = lib.part(pid)
        pos = _fit(coords, p)
        src = p["src"]
        rp = pos[src]
        tris = p["tris"]
        nrm = _vertex_normals(rp, tris, len(pos), src)
        jj, ww = _transfer_weights(lib, p["ref"][src], p["w"][src], nb)
        geoms.append((pid, slot, (rp, nrm, p["uv"], tris, jj, ww), _material(lib, mentry, col, slot)))

    # ---- skeleton -------------------------------------------------------
    cubes = {n: coords[idx].mean(axis=0) for n, idx in lib.joints.items() if len(idx)}

    def end(e):
        if "cube" in e and e["cube"] in cubes:
            return cubes[e["cube"]]
        if "verts" in e:
            return coords[np.array(e["verts"], np.int64)].mean(axis=0)
        p = e.get("pos", [0, 0, 0])            # Blender default position, metres Z-up
        return _OBJ_FROM_BL @ np.array(p, np.float64) / DM
    heads = np.array([end(b["head"]) for b in lib.bones]) * DM
    tails = np.array([end(b["tail"]) for b in lib.bones]) * DM

    # ground: lowest point of body + footwear
    ys = [g[2][0][:, 1].min() for g in geoms if g[1] in ("skin", "feet")]
    ground = min(ys) * DM if ys else 0.0
    shift = np.array([0.0, -ground, 0.0])
    heads = (heads + shift) @ _TURN.T
    tails = (tails + shift) @ _TURN.T

    rest = np.zeros((nb, 4, 4))
    for i, b in enumerate(lib.bones):
        d_obj = (tails[i] - heads[i]) @ _TURN.T              # back to obj orientation for the roll
        d_bl = _OBJ_FROM_BL.T @ d_obj
        rb = _vec_roll_to_mat3(d_bl, b["roll"])
        r_obj = _OBJ_FROM_BL @ rb
        rest[i, :3, :3] = _TURN @ r_obj
        rest[i, :3, 3] = heads[i]
        rest[i, 3, 3] = 1.0
    local = np.zeros_like(rest)
    for i, p in enumerate(lib.parents):
        local[i] = rest[i] if p < 0 else np.linalg.inv(rest[p]) @ rest[i]
    model.bone_names = list(lib.bone_names)
    model.parents = lib.parents.copy()
    model.rest_world = rest
    model.rest_local = local
    model.inv_bind = np.linalg.inv(rest)
    model.heads, model.tails = heads, tails
    model.bone_len = np.linalg.norm(tails - heads, axis=1)

    for pid, slot, (rp, nrm, uv, tris, jj, ww), mat in geoms:
        P = ((rp * DM + shift) @ _TURN.T).astype(np.float32)
        N = (nrm @ _TURN.T).astype(np.float32)
        model.parts.append(Part(pid, slot, P, N, uv.astype(np.float32), tris.astype(np.uint32).reshape(-1),
                                jj.astype(np.float32), ww.astype(np.float32), mat))
    body_p = model.parts[0].positions
    model.height = float(body_p[:, 1].max()) if len(body_p) else 1.7
    hips = model.bone("mixamorig:Hips")
    lf, rf = model.bone("mixamorig:LeftFoot"), model.bone("mixamorig:RightFoot")
    model.metrics = {
        "hip_height": float(heads[hips][1]) if hips >= 0 else 0.95,
        "ankle_height": float(0.5 * (heads[lf][1] + heads[rf][1])) if lf >= 0 and rf >= 0 else 0.08,
        "leg_length": float(model.bone_len[model.bone("mixamorig:LeftUpLeg")] +
                            model.bone_len[model.bone("mixamorig:LeftLeg")]),
        "half_width": float(np.abs(body_p[:, 0]).max()) if len(body_p) else 0.3,
    }
    return model


# --------------------------------------------------------------------------- random characters
MALE_HAIR = ["hair/short01", "hair/short02", "hair/short04", "hair/afro01", "hair/culturalibre_hair_02",
             "hair/cortu_short_messy_hair", "hair/faydaen_hair_1"]
FEMALE_HAIR = ["hair/long01", "hair/bob01", "hair/bob02", "hair/braid01", "hair/ponytail01", "hair/short03",
               "hair/toigo_blunt_bob", "hair/toigo_curled_under_bob", "hair/toigo_inverted_bob_with_bangs",
               "hair/elvs_french_braid_variation", "hair/rehmanpolanski_hair_bun_brown", "hair/culturalibre_hair_01",
               "hair/o4saken_long01", "hair/littleright_bobcut_hair", "hair/afro01"]
CIVIL_FULL = {"male": ["clothes/male_casualsuit01", "clothes/male_casualsuit02", "clothes/male_casualsuit03",
                       "clothes/male_casualsuit04", "clothes/male_casualsuit05", "clothes/male_casualsuit06",
                       "clothes/male_elegantsuit01", "clothes/male_worksuit01", "clothes/toigo_male_suit_3",
                       "clothes/toigo_male_suit_tie_and_jacket", "clothes/toigo_male_double-breasted_suit",
                       "clothes/toigo_suit_with_dinner_jacket"],
              "female": ["clothes/female_casualsuit01", "clothes/female_casualsuit02", "clothes/female_elegantsuit01",
                         "clothes/female_sportsuit01", "clothes/toigo_shift_dress", "clothes/toigo_halter_dress_midi",
                         "clothes/toigo_halter_dress_knee_length", "clothes/toigo_female_suit",
                         "clothes/toigo_female_suit_2", "clothes/toigo_keyhole_neck_dress",
                         "clothes/toigo_camisole_dress_with_full_skirt", "clothes/toigo_dress_with_tiered_skirt"]}
CIVIL_TOP = {"male": ["clothes/namuhekam_male_polo_shirt", "clothes/elvs_crude_t-shirt_male",
                      "clothes/toigo_fisherman_sweater", "clothes/toigo_basic_tucked_t-shirt"],
             "female": ["clothes/joepal_crude_t-shirt_female", "clothes/toigo_camisole_top",
                        "clothes/toigo_turtleneck_halter_top", "clothes/toigo_basic_tucked_t-shirt",
                        "clothes/toigo_fisherman_sweater", "clothes/toigo_keyhole_tank_top"]}
CIVIL_BOTTOM = {"male": ["clothes/cortu_cargo_pants", "clothes/toigo_wool_pants", "clothes/cortu_jeans_shorts"],
                "female": ["clothes/toigo_long_full_skirt", "clothes/toigo_tiered_skirt", "clothes/cortu_cargo_pants",
                           "clothes/toigo_harem_pants", "clothes/frankyaye_mini_skirt_01", "clothes/cortu_jeans_shorts"]}
CIVIL_FEET = {"male": ["clothes/shoes01", "clothes/shoes02", "clothes/shoes04", "clothes/shoes05",
                       "clothes/toigo_ankle_boots_male", "clothes/toigo_mj_cloth_shoes"],
              "female": ["clothes/shoes03", "clothes/shoes06", "clothes/toigo_ballet_flats",
                         "clothes/toigo_ankle_boots_female", "clothes/toigo_flats", "clothes/shoes02"]}
ENEMY_FULL = ["clothes/matcreator_mc-scifi-armor_guardian", "clothes/matcreator_mc-scifi-armor_jupiter7",
              "clothes/joachip_cyborg_suit", "clothes/donitz_monk_robe", "clothes/male_worksuit01",
              "clothes/matcreator_mc-skinsuit_2022", "clothes/culturalibre_hero_suit_2"]
ENEMY_HEAD = ["clothes/culturalibre_warrior_helmet", "clothes/culturalibre_skull_helmet",
              "clothes/javherre_casco_caballero_templario_templar_knight_helmet",
              "clothes/grinsegold_corinthian_helmet", "clothes/culturalibre_motorcycle_helmet"]
ENEMY_FEET = ["clothes/culturalibre_hero_boots_1", "clothes/rehmanpolanski_viking_boots",
              "clothes/toigo_ankle_boots_male", "clothes/culturalibre_male_boots"]
ZOMBIE_SKINS = ["naim_abbassi_zombie_skin", "sohh_female_zombie_skin"]
STYLES = ("civilian", "soldier", "viking", "zombie", "scifi")


def _skin_for(lib, gender, race, age, rng):
    agegrp = "young" if age < 0.62 else ("middleage" if age < 0.82 else "old")
    name = f"{agegrp}_{race}_{gender}"
    if name in lib.cat["skins"]:
        return name
    cands = [k for k, v in lib.cat["skins"].items() if race in k and v["gender"] in (gender, "any")]
    return rng.choice(cands) if cands else ("young_caucasian_" + gender)


def _ok(lib, ids):
    return [i for i in ids if i in lib.parts]


def random_spec(lib, seed=None, gender=None, style="civilian"):
    """A believable random character.  style: civilian | soldier | viking | zombie | scifi."""
    rng = random.Random(seed)
    g = gender if gender in ("male", "female") else rng.choice(("male", "female"))
    male = g == "male"
    race_name = rng.choices(("caucasian", "african", "asian"), weights=(0.45, 0.3, 0.25))[0]
    race = {"african": 0.0, "asian": 0.0, "caucasian": 0.0}
    race[race_name] = 0.75
    other = [r for r in race if r != race_name]
    mix = rng.random() * 0.25
    race[other[0]] += mix
    race[other[1]] += 0.25 - mix
    clamp = lambda v, a=0.0, b=1.0: max(a, min(b, v))
    macro = {"gender": clamp(rng.uniform(0.85, 1.0) if male else rng.uniform(0.0, 0.15)),
             "age": clamp(rng.uniform(0.5, 0.88)), "muscle": clamp(rng.gauss(0.5, 0.14)),
             "weight": clamp(rng.gauss(0.5, 0.16), 0.1, 0.95), "height": clamp(rng.gauss(0.5, 0.07), 0.3, 0.7),
             "proportions": clamp(rng.uniform(0.45, 0.85)), "cupsize": clamp(rng.gauss(0.5, 0.18)),
             "firmness": clamp(rng.gauss(0.5, 0.15))}
    macro.update(race)
    if style in ("soldier", "viking", "scifi"):
        macro["muscle"] = clamp(macro["muscle"] + 0.25)
        macro["age"] = clamp(macro["age"], 0.5, 0.7)
    face = [m for m in lib.mods if m.split("-")[0] in ("nose", "mouth", "chin", "eye", "ear", "cheek", "eyebrows")]
    mods = {m: round(rng.uniform(-0.35, 0.35), 3) for m in rng.sample(face, min(len(face), 10))}
    for m in ("stomach-pregnant-decr-incr", "torso-vshape-decr-incr", "hip-scale-horiz-decr-incr"):
        if m in lib.mods and rng.random() < 0.5:
            mods[m] = round(rng.uniform(-0.3, 0.3), 3)
    s = default_spec(g)
    s["macro"], s["modifiers"] = macro, mods
    s["skin"] = _skin_for(lib, g, race_name, macro["age"], rng)
    s["eyes"] = rng.choice(sorted(lib.cat["eye_materials"]))
    s["eyebrows"] = rng.choice(_ok(lib, [f"eyebrows/eyebrow{n:03d}" for n in range(1, 13)]))
    s["eyelashes"] = rng.choice(_ok(lib, ["eyelashes/eyelashes01", "eyelashes/eyelashes02", "eyelashes/eyelashes03"]))
    hair = _ok(lib, MALE_HAIR if male else FEMALE_HAIR)
    s["hair"] = rng.choice(hair) if hair and not (male and rng.random() < 0.12) else ""
    if s["hair"]:
        mats = lib.parts[s["hair"]].get("materials") or []
        s["hair_material"] = rng.choice(mats)["name"] if mats else ""
    if macro["age"] > 0.8 and s["hair"]:
        s["hair_color"] = [0.75, 0.75, 0.75] if rng.random() < 0.6 else [1, 1, 1]

    def mat_of(pid):
        mats = lib.parts[pid].get("materials") or []
        return rng.choice(mats)["name"] if len(mats) > 1 else ""

    clothes = []
    if style == "civilian":
        if rng.random() < 0.6:
            full = _ok(lib, CIVIL_FULL[g])
            if full:
                clothes.append(rng.choice(full))
        else:
            for pool in (CIVIL_TOP[g], CIVIL_BOTTOM[g]):
                pool = _ok(lib, pool)
                if pool:
                    clothes.append(rng.choice(pool))
        clothes.append(rng.choice(_ok(lib, CIVIL_FEET[g])))
        if rng.random() < 0.15:
            clothes.append(rng.choice(_ok(lib, ["clothes/fedora01", "clothes/jujube_newsboy_cap",
                                                "clothes/grinsegold_uncle_joshis_hat"]) or ["clothes/fedora01"]))
        if rng.random() < 0.2:
            clothes.append(rng.choice(_ok(lib, lib.list_parts(slot="eyewear"))))
    elif style == "zombie":
        pool = _ok(lib, CIVIL_FULL[g])
        clothes.append(rng.choice(pool))
        s["skin"] = rng.choice(_ok_skins(lib, ZOMBIE_SKINS) or [s["skin"]])
        s["skin_color"] = [0.85, 0.95, 0.8]
        s["eyes"] = "grey" if "grey" in lib.cat["eye_materials"] else s["eyes"]
        macro["weight"] = clamp(macro["weight"] - 0.15)
    elif style == "viking":
        clothes += _ok(lib, ["clothes/rehmanpolanski_viking_tunic", "clothes/rehmanpolanski_viking_pants",
                             "clothes/rehmanpolanski_viking_boots"])
        if rng.random() < 0.5:
            clothes += _ok(lib, ["clothes/culturalibre_warrior_helmet"])
        if "rehmanpolanski_skin_viking_tattoos" in lib.cat["skins"] and male and rng.random() < 0.5:
            s["skin"] = "rehmanpolanski_skin_viking_tattoos"
    elif style == "scifi":
        clothes.append(rng.choice(_ok(lib, ["clothes/matcreator_mc-scifi-armor_guardian",
                                            "clothes/matcreator_mc-scifi-armor_jupiter7",
                                            "clothes/matcreator_mc-skinsuit_2022", "clothes/joachip_cyborg_suit"])))
        clothes.append(rng.choice(_ok(lib, ENEMY_FEET)))
    else:   # soldier
        clothes.append(rng.choice(_ok(lib, ["clothes/male_worksuit01", "clothes/matcreator_mc-scifi-armor_guardian",
                                            "clothes/cortu_cargo_pants"])))
        if "clothes/cortu_cargo_pants" in clothes:
            clothes.append(rng.choice(_ok(lib, CIVIL_TOP[g])))
        clothes.append(rng.choice(_ok(lib, ENEMY_FEET)))
        if rng.random() < 0.7:
            clothes.append(rng.choice(_ok(lib, ["clothes/mrgreaterthan_m1_helmet", "clothes/culturalibre_motorcycle_helmet",
                                                "clothes/culturalibre_warrior_helmet"])))
    s["clothes"] = [{"id": c, "material": mat_of(c), "color": [1, 1, 1]} for c in clothes if c]
    if any(lib.parts[c["id"]]["slot"] == "head" for c in s["clothes"]):
        if s["hair"] and not s["hair"].startswith("hair/short"):
            s["hair"] = rng.choice(_ok(lib, ["hair/short02", "hair/short04"]) or [""])
            s["hair_material"] = ""
    return s


def _ok_skins(lib, names):
    return [n for n in names if n in lib.cat["skins"]]
