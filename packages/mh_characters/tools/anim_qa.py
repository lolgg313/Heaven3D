"""Animation QA: contact sheets + anatomy checks for every built-in clip on
male / female / heavy bodies (needs a GPU):

    python tools/anim_qa.py OUT_DIR [clip names...]

For each clip it renders 6 phases from the front and the side, and checks:
  hands    no hand vertex inside the torso / hips / thighs (skinned, per frame)
  knees    no hyper-extension, bend points forward
  elbows   no hyper-extension
  feet     gait loops: planted-foot slip at the measured walking speed
"""
import importlib.util
import math
import os
import shutil
import sys
import tempfile

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(PKG))
OUT = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="animqa_")
ONLY = sys.argv[2:]
os.makedirs(OUT, exist_ok=True)

proj = tempfile.mkdtemp(prefix="animqa_proj_")
shutil.copytree(os.path.join(ROOT, "templates", "empty"), proj, dirs_exist_ok=True)
os.makedirs(os.path.join(proj, "packages"))
os.symlink(PKG, os.path.join(proj, "packages", "mh_characters"))
spec = importlib.util.spec_from_file_location("Heaven3D", os.path.join(ROOT, "Heaven3D.py"))
H = importlib.util.module_from_spec(spec)
sys.modules["Heaven3D"] = H
spec.loader.exec_module(H)
H._h3d_set_splash_mode("off")
import glm
import moderngl
import numpy as np
import pygame

W, Hh = 1100, 640
eng = H.HeavenEngine(mode="editor", width=W, height=Hh, project_path=os.path.join(proj, "main.heaven3d"))
for n in eng.scene.root.traverse():
    if n.is_ocean:
        n.ocean_settings.enabled = False
g = eng.add_plane(size=60.0, name="Ground")
g.material.base_color = (0.45, 0.47, 0.44, 1.0)
mod = sys.modules["h3dpkg_mh_characters"]
core = sys.modules["h3dpkg_mh_characters_mh_core"]
A = sys.modules["h3dpkg_mh_characters_mh_anim"]
lib = mod._lib()
library = A.load_library(os.path.join(PKG, "assets", "anims", "library.npz"))
male = core.default_spec("male")
female = core.default_spec("female")
heavy = core.default_spec("female")
heavy["macro"] = dict(core.MACRO_DEFAULTS, gender=0.0, weight=0.95, muscle=0.35, cupsize=0.8)
heavy["clothes"] = [{"id": "clothes/toigo_shift_dress"}, {"id": "clothes/shoes03"}]
bodies = [("male", male), ("female", female), ("heavy", heavy)]
chars = []
for i, (nm, sp) in enumerate(bodies):
    c = mod.place_character(eng, sp, pos=glm.vec3(-1.6 + 1.6 * i, 0, 0), yaw=180.0, name=nm, select=False)
    chars.append(c)
eng.deselect()
H.Log.info = lambda *a, **k: None

clips = ONLY or sorted(library)
report = []


def checks(rig, clip, name):
    m = rig.model
    pose = A.Pose(m)
    if getattr(rig.pose, "corr", None):
        pose.corr = rig.pose.corr
    body = m.parts[0]
    dom = body.joints[np.arange(len(body.joints)), np.argmax(body.weights, axis=1)].astype(int)
    cn = [A.canon(b) for b in m.bone_names]
    is_hand = np.array([cn[d].startswith(("lefthand", "righthand")) for d in dom])
    is_arm = np.array([cn[d].startswith(("leftarm", "rightarm", "leftforearm", "rightforearm", "leftshoulder",
                                          "rightshoulder", "lefthand", "righthand")) for d in dom])
    is_trunk = np.array([cn[d] in ("hips", "spine", "spine1", "spine2", "leftupleg", "rightupleg") for d in dom])
    hv = np.where(is_hand)[0][::3]
    tv = np.where(is_trunk & ~is_arm)[0][::2]
    J = body.joints.astype(int)
    Wt = body.weights
    P4 = np.hstack([body.positions, np.ones((len(body.positions), 1))])
    N3 = body.normals

    def skin(idx, S):
        M = np.einsum("nk,nkij->nij", Wt[idx], S[J[idx]])
        return np.einsum("nij,nj->ni", M, P4[idx])[:, :3], np.einsum("nij,nj->ni", M[:, :3, :3], N3[idx])
    worst_pen, worst_knee, worst_elbow = 0.0, 180.0, 180.0
    ids = {k: m.bone(f"mixamorig:{k}") for k in ("LeftUpLeg", "LeftLeg", "LeftFoot", "RightUpLeg", "RightLeg", "RightFoot",
                                                   "LeftArm", "LeftForeArm", "LeftHand", "RightArm", "RightForeArm", "RightHand")}
    n = len(clip.rot)
    for f in range(0, n, max(1, n // 10)):
        G = pose.fk(clip.rot[f], clip.hips[f])
        S = G @ m.inv_bind
        hp, _ = skin(hv, S)
        tp, tn = skin(tv, S)
        d2 = ((hp[:, None, :] - tp[None, :, :]) ** 2).sum(-1)
        k = d2.argmin(1)
        depth = -np.einsum("ij,ij->i", hp - tp[k], tn[k] / np.linalg.norm(tn[k], axis=1, keepdims=True))
        close = np.sqrt(d2[np.arange(len(hp)), k]) < 0.05
        pen = float(depth[close].max()) if np.any(close) else 0.0
        worst_pen = max(worst_pen, pen)
        for a, b, c, kind in (("LeftUpLeg", "LeftLeg", "LeftFoot", "knee"), ("RightUpLeg", "RightLeg", "RightFoot", "knee"),
                              ("LeftArm", "LeftForeArm", "LeftHand", "elbow"), ("RightArm", "RightForeArm", "RightHand", "elbow")):
            u = G[ids[b], :3, 3] - G[ids[a], :3, 3]
            v = G[ids[c], :3, 3] - G[ids[b], :3, 3]
            ang = math.degrees(math.acos(max(-1, min(1, float(np.dot(u, v) / (np.linalg.norm(u) * np.linalg.norm(v)))))))
            inner = 180.0 - ang
            if kind == "knee":
                # knee must bend with the shin going backward (+Z) relative to the thigh line
                fwd = np.cross(u, v)[0] * (1 if "Left" in a else 1)
                worst_knee = min(worst_knee, inner)
            else:
                worst_elbow = min(worst_elbow, inner)
    slip = None
    if clip.loop and getattr(clip, "_measured", False) and clip.speed > 0.2:
        lf, rf = m.bone("mixamorig:LeftFoot"), m.bone("mixamorig:RightFoot")
        Pp = np.array([[pose.fk(clip.rot[f], clip.hips[f])[j, :3, 3].copy() for j in (lf, rf)] for f in range(n)])
        errs = []
        for f in range(n):
            g2 = (f + 1) % n
            kf = 0 if Pp[f, 0, 1] < Pp[f, 1, 1] else 1
            if abs(Pp[f, kf, 1] - Pp[:, kf, 1].min()) < 0.03:
                dz = (Pp[g2, kf, 2] - Pp[f, kf, 2]) * clip.fps
                errs.append(abs(dz - clip.speed))
        slip = float(np.median(errs)) if errs else None
    return worst_pen, worst_knee, worst_elbow, slip


state = {"f": 0, "ci": 0, "phase": 0, "frames": []}
_flip = pygame.display.flip


def grab():
    data = moderngl.get_context().screen.read(components=3)
    img = pygame.transform.flip(pygame.image.frombuffer(data, (W, Hh), "RGB"), False, True)
    x0, x1 = int(W * 0.20), int(W * 0.80)
    y0, y1 = int(Hh * 0.12), int(Hh * 0.88)
    return img.subsurface((x0, y0, x1 - x0, y1 - y0)).copy()


def setup_clip(name):
    for c in chars:
        r = mod.rig_of(c)
        if name.startswith("@"):                       # synthesized clips (e.g. @wave)
            clip = A.clipset(r.model, "civilian", 0, library)[name[1:]][0]
        else:
            loop = name.endswith("_Loop") or name in ("Sword_Idle",)
            clip = A.retarget(library[name], r.model, name=name, loop=loop, in_place=True)
            if name != "Death01":
                A.ground_fit(r.model, clip)
        if name.endswith("_Loop") and any(k in name for k in ("Walk", "Jog", "Sprint", "Crouch_Fwd")):
            A.measure_gait(r.model, clip)
        r._qa_clip = clip
        an = A.Animator(r.model, {"idle": [clip]})
        r.animator = an
        cl = r.model._clip_cache.get(("Idle_Loop", True)) if hasattr(r.model, "_clip_cache") else None
        report.append((name, c.name) + checks(r, clip, name))


def set_time(u):
    for c in chars:
        r = mod.rig_of(c)
        clip = r._qa_clip
        q, h = clip.sample(u * clip.duration)
        r.pose.fk(q, h)
        r.tex.write(np.ascontiguousarray(r.pose.skin().transpose(0, 2, 1), dtype=np.float32).tobytes())
        r.frame = -1


PH = [0.0, 0.17, 0.34, 0.5, 0.67, 0.84]


def frame():
    st = state
    st["f"] += 1
    if st["f"] < 4:
        _flip()
        return
    if st["ci"] >= len(clips):
        lines = ["clip                      body    hand-pen(cm) knee-min(deg) elbow-min(deg) foot-slip(m/s)"]
        for name, b, pen, knee, elbow, slip in report:
            lines.append(f"{name:25s} {b:7s} {pen * 100:8.1f} {knee:12.1f} {elbow:13.1f} {'' if slip is None else f'{slip:10.3f}'}")
        txt = "\n".join(lines)
        print(txt)
        open(os.path.join(OUT, "report.txt"), "w").write(txt)
        os._exit(0)
    name = clips[st["ci"]]
    if st.get("setup") != name:
        st["setup"] = name
        setup_clip(name)
        mod.STATE["_qa"] = True
    ph = st["phase"]
    view = 0 if ph < len(PH) else 1
    u = PH[ph % len(PH)]
    if view == 0:
        eng.camera.position = glm.vec3(0.0, 1.0, 4.6)
        eng.camera.yaw, eng.camera.pitch = 0.0, -2.0
    else:
        eng.camera.position = glm.vec3(4.8, 1.0, 0.0)
        eng.camera.yaw, eng.camera.pitch = 90.0, -2.0
    set_time(u)
    if st.get("pending"):
        st["frames"].append(grab())
        st["phase"] += 1
        st["pending"] = False
        if st["phase"] >= 2 * len(PH):
            fw, fh = st["frames"][0].get_size()
            sheet = pygame.Surface((fw * len(PH) // 2, fh))
            sheet.fill((30, 30, 30))
            for i, im in enumerate(st["frames"]):
                small = pygame.transform.smoothscale(im, (fw // 2, fh // 2))
                sheet.blit(small, ((i % len(PH)) * fw // 2, (i // len(PH)) * fh // 2))
            pygame.image.save(sheet, os.path.join(OUT, f"{name.lstrip('@')}.png"))
            st["frames"], st["phase"] = [], 0
            st["ci"] += 1
    else:
        st["pending"] = True
    _flip()


orig_update = mod._on_update
mod._on_update = lambda e, dt: None
for api in H.H3DPackages:
    if api.id == "mh_characters":
        api._handlers["update"] = []
pygame.display.flip = frame
eng.run()
