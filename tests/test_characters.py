"""MakeHuman Characters package (needs a GPU / llvmpipe):  python tests/test_characters.py

Character generation (morphs, clothes, skeleton), the Character Creator panel,
GPU skinning, Mixamo-style FBX + BVH import and retargeting, NPCs walking a
path, an enemy that chases / attacks / dies, foot IK on a slope, Play/Stop
restore, save -> reload, and the exported-game (runtime) mode."""
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ENGINE = os.path.join(ROOT, "Heaven3D.py")
PKG = os.path.join(ROOT, "packages", "mh_characters")
OUT = os.environ.get("H3D_SHOTS") or tempfile.mkdtemp(prefix="h3dchar_shots_")
os.makedirs(OUT, exist_ok=True)
res = []


def check(n, c, d=""):
    res.append(bool(c))
    print(("PASS " if c else "FAIL ") + n + (f"  [{d}]" if d else ""), flush=True)


# ---------------------------------------------------------------- test animation files
def _fbx_prop(v):
    if isinstance(v, str):
        b = v.encode("utf-8")
        return b"S" + struct.pack("<I", len(b)) + b
    if isinstance(v, bool):
        return b"C" + struct.pack("<B", int(v))
    if isinstance(v, int):
        return b"L" + struct.pack("<q", v)
    if isinstance(v, float):
        return b"D" + struct.pack("<d", v)
    if isinstance(v, tuple) and v[0] == "I":
        return b"I" + struct.pack("<i", v[1])
    if isinstance(v, tuple) and v[0] in ("l", "f", "d"):
        fmt = {"l": "<q", "f": "<f", "d": "<d"}[v[0]]
        raw = b"".join(struct.pack(fmt, x) for x in v[1])
        return v[0].encode() + struct.pack("<III", len(v[1]), 0, len(raw)) + raw
    raise TypeError(v)


def _fbx_node(name, props=(), children=(), base=0):
    pb = b"".join(_fbx_prop(p) for p in props)
    head_len = 13 + len(name) + len(pb)
    kids = b""
    off = base + head_len
    for ch in children:
        k = ch(off)
        kids += k
        off += len(k)
    if children:
        kids += b"\x00" * 13
    end = base + head_len + len(kids)
    return struct.pack("<III", end, len(props), len(pb)) + bytes([len(name)]) + name.encode() + pb + kids


def N(name, props=(), children=()):
    return lambda base: _fbx_node(name, props, children, base)


def P(name, *vals, kind="Lcl Translation"):
    return N("P", [name, kind, "", "A"] + list(vals))


def write_test_fbx(path, frames=30):
    """A tiny Mixamo-like walk: T-pose skeleton (mixamorig: names, cm, +Z forward)
    whose legs swing and knees bend."""
    B = [("Hips", None, (0, 100, 0)), ("Spine", "Hips", (0, 10, 0)), ("Spine1", "Spine", (0, 12, 0)),
         ("Spine2", "Spine1", (0, 12, 0)), ("Neck", "Spine2", (0, 15, 0)), ("Head", "Neck", (0, 10, 0)),
         ("LeftShoulder", "Spine2", (6, 10, 0)), ("LeftArm", "LeftShoulder", (12, 0, 0)),
         ("LeftForeArm", "LeftArm", (27, 0, 0)), ("LeftHand", "LeftForeArm", (25, 0, 0)),
         ("RightShoulder", "Spine2", (-6, 10, 0)), ("RightArm", "RightShoulder", (-12, 0, 0)),
         ("RightForeArm", "RightArm", (-27, 0, 0)), ("RightHand", "RightForeArm", (-25, 0, 0)),
         ("LeftUpLeg", "Hips", (9, -6, 0)), ("LeftLeg", "LeftUpLeg", (0, -44, 0)), ("LeftFoot", "LeftLeg", (0, -42, 0)),
         ("LeftToeBase", "LeftFoot", (0, -6, 13)), ("RightUpLeg", "Hips", (-9, -6, 0)),
         ("RightLeg", "RightUpLeg", (0, -44, 0)), ("RightFoot", "RightLeg", (0, -42, 0)),
         ("RightToeBase", "RightFoot", (0, -6, 13))]
    ids = {n: 1000 + i for i, (n, _p, _t) in enumerate(B)}
    objs, conns = [], []
    times = [int(i * 46186158000 / 30) for i in range(frames)]
    cid = 5000
    for n, par, t in B:
        objs.append(N("Model", [ids[n], f"mixamorig:{n}\x00\x01Model", "LimbNode"],
                      [N("Properties70", [], [P("Lcl Translation", float(t[0]), float(t[1]), float(t[2]))])]))
        if par:
            conns.append(N("C", ["OO", ids[n], ids[par]]))
        curve = None
        if n in ("LeftUpLeg", "RightUpLeg"):
            s = 1.0 if n.startswith("Left") else -1.0
            curve = [-35.0 * s * math.sin(2 * math.pi * i / frames) for i in range(frames)]
        if n in ("LeftLeg", "RightLeg"):
            ph = 0.0 if n.startswith("Left") else 0.5
            curve = [40.0 * max(0.0, math.sin(2 * math.pi * (i / frames + ph))) for i in range(frames)]
        if curve is not None:
            cn = cid
            cid += 1
            objs.append(N("AnimationCurveNode", [cn, "R\x00\x01AnimCurveNode", ""],
                          [N("Properties70", [], [P("d|X", 0.0, kind="Number"), P("d|Y", 0.0, kind="Number"),
                                                  P("d|Z", 0.0, kind="Number")])]))
            conns.append(N("C", ["OP", cn, ids[n], "Lcl Rotation"]))
            cv = cid
            cid += 1
            objs.append(N("AnimationCurve", [cv, "\x00\x01AnimCurve", ""],
                          [N("KeyTime", [("l", times)]), N("KeyValueFloat", [("f", curve)])]))
            conns.append(N("C", ["OP", cv, cn, "d|X"]))
    nodes = [N("GlobalSettings", [], [N("Properties70", [], [
                 N("P", ["UpAxis", "int", "Integer", "", ("I", 1)]),
                 N("P", ["UnitScaleFactor", "double", "Number", "", 1.0])])]),
             N("Objects", [], objs), N("Connections", [], conns)]
    data = b"Kaydara FBX Binary  \x00\x1a\x00" + struct.pack("<I", 7400)
    off = len(data)
    for nd in nodes:
        b = nd(off)
        data += b
        off += len(b)
    data += b"\x00" * 13
    with open(path, "wb") as f:
        f.write(data)


def write_test_bvh(path, frames=20):
    lines = ["HIERARCHY", "ROOT Hips", "{", "OFFSET 0 0 0", "CHANNELS 6 Xposition Yposition Zposition Zrotation Xrotation Yrotation"]
    def joint(name, off, kids, depth):
        lines.append("JOINT " + name)
        lines.append("{")
        lines.append("OFFSET %g %g %g" % off)
        lines.append("CHANNELS 3 Zrotation Xrotation Yrotation")
        if kids:
            for k in kids:
                joint(*k, depth + 1)
        else:
            lines.extend(["End Site", "{", "OFFSET 0 -5 0", "}"])
        lines.append("}")
    joint("Spine", (0, 10, 0), [("Neck", (0, 30, 0), [("Head", (0, 10, 0), [])])], 1)
    joint("LeftUpLeg", (9, -6, 0), [("LeftLeg", (0, -44, 0), [("LeftFoot", (0, -42, 0), [])])], 1)
    joint("RightUpLeg", (-9, -6, 0), [("RightLeg", (0, -44, 0), [("RightFoot", (0, -42, 0), [])])], 1)
    lines += ["}", "MOTION", f"Frames: {frames}", "Frame Time: 0.0333333"]
    for i in range(frames):
        a = 30.0 * math.sin(2 * math.pi * i / frames)
        vals = [0, 92, 0, 0, 0, 0] + [0, 0, 0] * 3 + [0, -a, 0] + [0, 0, 0] * 2 + [0, a, 0] + [0, 0, 0] * 2
        lines.append(" ".join(f"{v:g}" for v in vals))
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------- engine boot
def boot(project, mode):
    import importlib.util
    spec = importlib.util.spec_from_file_location("Heaven3D", ENGINE)
    H = importlib.util.module_from_spec(spec)
    sys.modules["Heaven3D"] = H
    spec.loader.exec_module(H)
    H._h3d_set_splash_mode("off")
    eng = H.HeavenEngine(mode=mode, width=1280, height=760, project_path=os.path.join(project, "main.heaven3d"))
    return H, eng


def shot(name, W=1280, Hh=760):
    import pygame
    import moderngl
    data = moderngl.get_context().screen.read(components=3)
    pygame.image.save(pygame.transform.flip(pygame.image.frombuffer(data, (W, Hh), "RGB"), False, True),
                      os.path.join(OUT, name + ".png"))


def phase_editor(project):
    import numpy as np
    H, eng = boot(project, "editor")
    import glm
    import pygame
    for n in eng.scene.root.traverse():
        if n.is_ocean:
            n.ocean_settings.enabled = False
    ground = eng.add_plane(size=120.0, name="Ground")
    ground.material.base_color = (0.36, 0.42, 0.33, 1.0)
    ramp = eng.add_cube(size=1.0, name="Ramp")
    ramp.scale = glm.vec3(6.0, 0.4, 6.0)
    ramp.position = glm.vec3(-8.0, 0.4, 6.0)
    ramp.rotation_euler = glm.vec3(25.0, 0.0, 0.0)
    ramp.collider = H.BoxCollider(size=(1.0, 1.0, 1.0))
    mod = sys.modules["h3dpkg_mh_characters"]
    core = sys.modules["h3dpkg_mh_characters_mh_core"]
    anim = sys.modules["h3dpkg_mh_characters_mh_anim"]
    tools = {t["label"]: t for t in H._H3D27_PKG_STATE["tools"]}
    check("package + tools registered", "Characters: new NPC walker (+ path)" in tools and "NPC path: new path" in tools,
          sorted(k for k in tools if "Char" in k or "path" in k))
    check("skinning patched into geometry + shadow shaders", "mh_skin_matrix" in H.GEOMETRY_VERT and "mh_skin_matrix" in H.SHADOW_VERT)
    lib = mod._lib()
    check("CC0 library: clothes, hair, skins", len(lib.list_parts(kind="clothes")) > 150 and len(lib.list_parts(slot="hair")) > 20
          and len(lib.cat["skins"]) > 40, f"{len(lib.list_parts(kind='clothes'))} clothes, {len(lib.list_parts(slot='hair'))} hair, {len(lib.cat['skins'])} skins")

    # --- characters ---------------------------------------------------
    hero = mod.place_character(eng, core.default_spec("male"), pos=glm.vec3(0, 0, 0), yaw=180.0, name="Hero")
    r = mod.rig_of(hero)
    check("character built (body + clothes + hair + eyes)", r is not None and len(r.parts) >= 6 and
          {p.slot for p in r.model.parts} >= {"skin", "full", "feet", "hair", "eyes"}, [p.slot for p in r.model.parts] if r else None)
    check("feet on the ground (node = capsule centre)", abs((hero.position.y - r.half) - 0.0) < 0.05, f"{hero.position.y:.3f} - {r.half:.3f}")
    check("parts are locked, not saved", all(getattr(c, "_heaven3d_imported_part", None) == "mh" and c._h3d_locked for c in hero.children))
    tall = core.build(lib, dict(core.default_spec("male"), macro=dict(core.MACRO_DEFAULTS, gender=1.0, height=1.0)))
    short = core.build(lib, dict(core.default_spec("female"), macro=dict(core.MACRO_DEFAULTS, gender=0.0, height=0.0)))
    check("height / gender morphs", tall.height > r.height + 0.08 and short.height < r.height - 0.15,
          f"tall {tall.height:.2f} default {r.height:.2f} short {short.height:.2f}")
    fat = core.build(lib, dict(core.default_spec("male"), macro=dict(core.MACRO_DEFAULTS, gender=1.0, weight=1.0)))
    def waist(model):
        p = model.parts[0].positions
        band = p[(p[:, 1] > 0.95) & (p[:, 1] < 1.1) & (np.abs(p[:, 0]) < 0.25)]
        return np.ptp(band[:, 0]) * np.ptp(band[:, 2])
    check("weight morph widens the waist", waist(fat) > waist(r.model) * 1.08, f"{waist(r.model):.4f} -> {waist(fat):.4f}")
    covered = sum(len(p.indices) for p in r.model.parts if p.slot == "skin")
    naked = core.build(lib, dict(core.default_spec("male"), clothes=[]))
    check("body under clothes is hidden (delete_verts)", covered < sum(len(p.indices) for p in naked.parts if p.slot == "skin") * 0.8)
    low = core.build(lib, dict(core.default_spec("male"), body="proxy/male1591"))
    check("low-poly proxy body", len(low.parts[0].positions) < 2500, len(low.parts[0].positions))
    names = set(r.model.bone_names)
    check("Mixamo skeleton (52 bones)", len(names) == 52 and {"mixamorig:Hips", "mixamorig:LeftFoot", "mixamorig:Head"} <= names)

    # --- animation import -------------------------------------------------
    anim_dir = os.path.join(project, "animations")
    os.makedirs(anim_dir, exist_ok=True)
    fbx = os.path.join(tempfile.mkdtemp(), "Walking.fbx")
    write_test_fbx(fbx)
    bvh = os.path.join(anim_dir, "Idle_mocap.bvh")
    write_test_bvh(bvh)
    eng.select_node(hero)
    ok = mod.import_animation(eng, "walk", fbx)
    check("FBX import copies into project/animations", ok and os.path.isfile(os.path.join(anim_dir, "Walking.fbx")) and
          _char(hero).properties["animations"]["walk"] == "animations/Walking.fbx")
    r = mod.rig_of(hero)
    src = anim.load_any(os.path.join(anim_dir, "Walking.fbx"))[0]
    clip = r.clips["walk"][0]
    check("FBX retargeted onto the character", "walk" in r.user_clips and clip.rot.shape[1] == 52 and len(clip.rot) == 30)
    pose = anim.Pose(r.model)
    li = r.model.bone("mixamorig:LeftLeg")
    lu = r.model.bone("mixamorig:LeftUpLeg")
    turn = np.diag([-1.0, 1.0, -1.0])
    sidx = {anim.canon(n): i for i, n in enumerate(src.names)}
    worst = 0.0
    for f in range(0, 30, 3):
        G = pose.fk(clip.rot[f], clip.hips[f])
        dt = G[li, :3, 3] - G[lu, :3, 3]
        ds = (src.frames[f, sidx["leftleg"], :3, 3] - src.frames[f, sidx["leftupleg"], :3, 3]) @ turn.T
        c = float(np.dot(dt, ds) / (np.linalg.norm(dt) * np.linalg.norm(ds)))
        worst = max(worst, math.degrees(math.acos(max(-1.0, min(1.0, c)))))
    check("thigh follows the source thigh (T-pose -> A-pose retarget)", worst < 3.0, f"worst {worst:.2f} deg")
    found = mod.auto_slots(anim_dir, project)
    check("animation folder auto-assign by file name", found.get("walk") == "animations/Walking.fbx" and found.get("idle") == "animations/Idle_mocap.bvh", found)
    bsrc = anim.load_any(bvh)[0]
    bclip = anim.retarget(bsrc, r.model)
    check("BVH loads + retargets", len(bclip.rot) == 20)
    # FK swings the foot
    zs = [pose.fk(clip.rot[f], clip.hips[f])[r.model.bone("mixamorig:LeftFoot"), 2, 3] for f in range(30)]
    check("walk clip swings the leg", max(zs) - min(zs) > 0.25, f"{max(zs) - min(zs):.2f} m")

    # --- creator panel ------------------------------------------------------
    st = mod.STATE
    st["tab"] = "Body"
    state = {"f": 0}
    others = []

    def labels():
        return [b[1] for b in eng.ui._button_rects]

    def frame():
        f = state["f"] = state["f"] + 1
        if f == 4:
            check("Character Creator opens for the selection", any(str(l).startswith("Gender") for l in labels()), [l for l in labels() if ":" in str(l)][:3])
            shot("creator_body")
            # drag the Height slider to the right end
            b = next(b for b in eng.ui._button_rects if str(b[1]).startswith("Height"))
            rect = b[0]
            state["h0"], state["feet0"] = r.height, hero.position.y - r.half
            eng.input.mouse_pos = (int((rect.x + rect.w * 0.98) * eng.width), int((1 - rect.y - rect.h * 0.5) * eng.height))
            eng.input.lmb = True
            b[2]()
        if f == 6:
            eng.input.lmb = False
        if f == 14:
            r2 = mod.rig_of(hero)
            check("Height slider rebuilds the character, feet stay put", r2.height > state["h0"] + 0.05 and
                  abs((hero.position.y - r2.half) - state["feet0"]) < 0.02, f"{state['h0']:.2f} -> {r2.height:.2f}")
            st["tab"] = "Clothes"
            st["clothes_slot"] = "head"
        if f == 16:
            shot("creator_clothes")
            n0 = len(mod.rig_of(hero).parts)
            state["n0"] = n0
            b = next(b for b in eng.ui._button_rects if "fedora" in str(b[1]).lower())
            b[2]()
        if f == 22:
            r2 = mod.rig_of(hero)
            check("adding a hat from the list", len(r2.parts) == state["n0"] + 1 and any(p.pid == "clothes/fedora01" or "fedora" in p.pid for p in r2.model.parts))
            st["tab"] = "Anim"
        if f == 24:
            shot("creator_anim")
            check("Anim tab shows the imported Mixamo walk", any("Walking.fbx" in str(l) for l in labels()))
            st["tab"] = "Body"
            # --- gameplay scene ---
            eng.deselect()
            spawn = eng.add_player_spawn()
            spawn.position = glm.vec3(0.0, 1.0, 14.0)
            tools["Characters: new NPC walker (+ path)"]["fn"](eng)
            npc = [n for n in eng.scene.root.children if n.name.startswith("NPC") and mod._char_bp(n)][-1]
            path = [n for n in eng.scene.root.children if mod._bp(n, "NPCPath")][-1]
            npc.position = glm.vec3(4.0, npc.position.y, -2.0)
            for i, (_o, wp, _b) in enumerate(mod._waypoints(eng, path.name)):
                wp.position = glm.vec3(4.0 + 3.0 * math.cos(i * math.pi / 2), 0.0, 3.0 * math.sin(i * math.pi / 2))
            state["npc"], state["path"] = npc, path
            enemy = mod.place_character(eng, core.random_spec(lib, 11, style="zombie"), pos=glm.vec3(-3.0, 0.0, 5.0),
                                        name="Enemy", select=False)
            eng.attach_blueprint(enemy, mod.CLS["MHEnemy"])
            enemy.blueprints[-1].properties.update({"chase_speed": 3.0, "require_los": False, "detect_range": 30.0})
            state["enemy"] = enemy
            gunner = mod.place_character(eng, core.random_spec(lib, 12, gender="male", style="soldier"),
                                         pos=glm.vec3(9.0, 0.0, 9.0), name="Gunner", select=False)
            eng.attach_blueprint(gunner, mod.CLS["MHEnemy"])
            gunner.blueprints[-1].properties.update({"attack_style": "hitscan", "require_los": False, "detect_range": 4.0})
            state["gunner"] = gunner
            top = 0.4 + 0.2 / math.cos(math.radians(25.0))          # ramp surface under the centre
            slope = mod.place_character(eng, core.default_spec("female"), pos=glm.vec3(-8.0, top, 6.0), yaw=90.0,
                                        name="SlopeStander", select=False)
            state["slope"] = slope
            eng.camera.position = glm.vec3(0.0, 3.0, 16.0)
            eng.camera.yaw, eng.camera.pitch = 0.0, -10.0
        if f == 27:
            shot("scene_editor")
            state["npc0"] = glm.vec3(state["npc"].position)
            eng.toggle_play()
        if f == 30:
            check("waypoints hidden in Play", not any(n.visible for n in eng.scene.root.children if mod._bp(n, "NPCWaypoint")))
            rn = mod.rig_of(state["npc"])
            check("built-in CC0 mocap clips (Quaternius UAL) drive the NPC", rn.clips["walk"][0].name in ("Walk_Loop", "Walk_Formal_Loop")
                  and rn.clips["death"][0].name == "Death01" and rn.clips["walk"][0].speed > 0.8,
                  f"{rn.clips['walk'][0].name} @ {rn.clips['walk'][0].speed:.2f} m/s")
            wv = rn.clips.get("wave", [None])[0]
            ok = wv is not None and getattr(wv, "mask", None) is not None
            if ok:
                A_ = sys.modules["h3dpkg_mh_characters_mh_anim"]
                P_ = A_.Pose(rn.model)
                G_ = P_.fk(*wv.sample(1.3))
                bi = {k: rn.model.bone("mixamorig:" + k) for k in ("Hips", "RightArm", "RightHand")}
                hx, ax_, hd_ = (G_[bi[k], :3, 3] for k in ("Hips", "RightArm", "RightHand"))
                ok = hd_[1] > ax_[1] + 0.15 and abs(hd_[0] - hx[0]) > abs(ax_[0] - hx[0])
            check("wave: hand raised above the shoulder and outside the body", ok)
            rg = mod.rig_of(state["gunner"])
            check("hitscan enemy carries a pistol (pistol moveset + gun part)", rg.moveset == "pistol" and rg.gun is not None
                  and rg.animator.overlay is not None)
            re_ = mod.rig_of(state["enemy"])
            check("zombie skin -> zombie moveset", re_.moveset == "zombie" and re_.clips["walk"][0].name == "Zombie_Walk_Fwd_Loop")
            state["path_len"] = 0.0
            state["last"] = glm.vec3(state["npc"].position)
        if 30 < f < 200:
            npc = state["npc"]
            state["path_len"] += glm.length(npc.position - state["last"])
            state["last"] = glm.vec3(npc.position)
        if f == 200:
            npc = state["npc"]
            moved = state["path_len"]
            check("NPC walks its path", moved > 3.0, f"walked {moved:.2f} m")
            wb = mod._bp(npc, "NPCWalker")
            an = mod.rig_of(npc).animator
            check("NPC walk anim follows its smoothed speed", abs(an.speed_target - wb._vel) < 0.05)
            en = state["enemy"]
            pb = eng.first_blueprint("FPSController")
            d = glm.length(en.world_position() - pb.node.world_position())
            eb = mod._bp(en, "MHEnemy")
            check("enemy noticed and chased the player", eb.state == "chase" and d < 6.0, f"state {eb.state}, distance {d:.1f} m")
            sl = mod.rig_of(state["slope"])
            G = sl.pose.G
            lf, rf = sl.model.bone("mixamorig:LeftFoot"), sl.model.bone("mixamorig:RightFoot")
            check("foot IK on the slope: pelvis drops, feet at different heights", sl.pose.ik_pelvis < -0.01 and
                  abs(G[lf, 1, 3] - G[rf, 1, 3]) > 0.03, f"pelvis {sl.pose.ik_pelvis:.3f}, feet dy {abs(G[lf, 1, 3] - G[rf, 1, 3]):.3f}")
            eng.camera.position = glm.vec3(-4.0, 2.2, 11.0)
            shot("play_scene")
        if f == 320:
            pb = eng.first_blueprint("FPSController")
            check("enemy attacks hurt the player", pb.hp < pb.max_hp, f"hp {pb.hp:.0f}/{pb.max_hp:.0f}")
            en = state["enemy"]
            eng.message_ancestors(en, "damage", {"amount": 500.0})
            state["dead_t"] = f
        if f == 330:
            en = state["enemy"]
            eb = mod._bp(en, "MHEnemy")
            check("enemy dies: death animation, collider removed", not eb.alive and mod.rig_of(en).animator.dead and
                  all(e.node is not en for e in eng.physics.entries))
        if f == 380:
            shot("enemy_dead")
            eng.toggle_play()
        if f == 384:
            npc = state["npc"]
            check("Stop restores the NPC", glm.length(npc.position - state["npc0"]) < 1e-3)
            check("waypoints visible again", all(n.visible for n in eng.scene.root.children if mod._bp(n, "NPCWaypoint")))
            eng.save_project(os.path.join(project, "main.heaven3d"))
            txt = open(os.path.join(project, "main.heaven3d")).read()
            check("characters saved as blueprints (no geometry)", "MHCharacter" in txt and "NPCWalker" in txt and "MHEnemy" in txt
                  and '"_mh_' not in txt and "Hero skin" not in txt)
            print(f"\n{sum(res)}/{len(res)} passed (editor)")
            os._exit(0 if all(res) else 1)
        _flip()
    _flip = pygame.display.flip
    pygame.display.flip = frame
    eng.run()


def _char(node):
    for b in node.blueprints:
        if b.__class__.__name__ == "MHCharacter":
            return b


def phase_reload(project, mode):
    H, eng = boot(project, mode)
    import pygame
    import glm
    mod = sys.modules["h3dpkg_mh_characters"]
    state = {"f": 0}

    def frame():
        f = state["f"] = state["f"] + 1
        if f == 3:
            chars = mod.characters(eng)
            hero = next(n for n, _b in chars if n.name == "Hero")
            r = mod.rig_of(hero)
            check(f"{mode}: characters rebuilt from the project", len(chars) >= 4 and r is not None and len(r.parts) >= 6,
                  f"{len(chars)} characters")
            check(f"{mode}: imported Mixamo walk still assigned", r is not None and "walk" in r.user_clips)
            check(f"{mode}: no duplicated parts", all(sum(1 for c in n.children if getattr(c, '_heaven3d_imported_part', None) == 'mh')
                                                      == len(mod.rig_of(n).parts) for n, _b in chars))
        if mode == "runtime" and 5 <= f < 40:
            for n, _b in mod.characters(eng):
                wb = mod._bp(n, "NPCWalker")
                if wb is not None:
                    state.setdefault("p0", {}).setdefault(n.name, glm.vec3(n.position))
                    r = mod.rig_of(n)
                    state["same"] = state.get("same", True) and (wb.rig is r)
                    if r is not None:
                        state["vmax"] = max(state.get("vmax", 0.0), r.animator.speed)
                        state["moved"] = max(state.get("moved", 0.0), glm.length(n.position - state["p0"][n.name]))
        if f == 40:
            if mode == "runtime":
                en = next(n for n, _b in mod.characters(eng) if n.name == "Enemy")
                check("runtime: enemy AI is live in the exported game", mod._bp(en, "MHEnemy").alive)
                check("runtime: walking NPCs play their walk animation (not sliding in idle)",
                      state.get("same", False) and state.get("moved", 0.0) > 0.3 and state.get("vmax", 0.0) > 0.3,
                      f"moved {state.get('moved', 0.0):.2f} m, anim speed {state.get('vmax', 0.0):.2f}")
            shot(f"reload_{mode}")
            print(f"\n{sum(res)}/{len(res)} passed ({mode})")
            os._exit(0 if all(res) else 1)
        _flip()
    _flip = pygame.display.flip
    pygame.display.flip = frame
    eng.run()


if __name__ == "__main__":
    if len(sys.argv) > 2:
        project, phase = sys.argv[1], sys.argv[2]
        if phase == "editor":
            phase_editor(project)
        else:
            phase_reload(project, "editor" if phase == "reload" else "runtime")
        sys.exit(0)
    proj = tempfile.mkdtemp(prefix="h3dchar_")
    shutil.copytree(os.path.join(ROOT, "templates", "empty"), proj, dirs_exist_ok=True)
    os.makedirs(os.path.join(proj, "packages"))
    shutil.copytree(PKG, os.path.join(proj, "packages", "mh_characters"),
                    ignore=shutil.ignore_patterns("__pycache__", "tools"))
    failed = 0
    for phase in ("editor", "reload", "runtime"):
        failed += subprocess.run([sys.executable, __file__, proj, phase]).returncode != 0
    print("\nSCREENSHOTS", OUT)
    print("ALL PASSED" if failed == 0 else f"{failed} phase(s) failed")
    sys.exit(1 if failed else 0)
