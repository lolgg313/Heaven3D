"""Headless checks for the weapon skinning runtime -- no GPU, no engine.

Run:  python packages/hqfps_weapons/tests/test_skin.py
"""
import glob
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, PKG)

from hq_skin import SkinnedModel, quat_slerp, quat_to_mat4  # noqa: E402

MODELS = os.path.join(PKG, "assets", "models")        # arms + 44-bone arm rig
GUNS = os.path.join(PKG, "assets", "models_gun")      # the weapon meshes
I4 = np.eye(4)
fails = []


def check(cond, label):
    if not cond:
        fails.append(label)
    print("  %-64s %s" % (label, "ok" if cond else "FAIL"))


def test_quat():
    print("quaternion math")
    q = np.array([0.0, 0.0, 0.0, 1.0])
    check(np.abs(quat_to_mat4(q) - I4).max() < 1e-12, "identity quat -> identity matrix")
    a = np.array([0.0, 0.0, 0.0, 1.0])
    # build the target exactly: a 90-degree turn about +Y
    h = np.sqrt(0.5)
    b = np.array([0.0, h, 0.0, h])
    check(np.abs(quat_slerp(a, b, 0.0) - a).max() < 1e-12, "slerp(t=0) == a")
    check(np.abs(quat_slerp(a, b, 1.0) - b).max() < 1e-12, "slerp(t=1) == b")
    mid = quat_slerp(a, b, 0.5)
    check(abs(np.linalg.norm(mid) - 1.0) < 1e-12, "slerp midpoint stays unit length")
    # halfway between 0 and 90 degrees about +Y is 45 degrees
    want = np.array([0.0, np.sin(np.pi / 8), 0.0, np.cos(np.pi / 8)])
    check(np.abs(mid - want).max() < 1e-12, "slerp midpoint is the 45-degree rotation")
    # shortest arc: a negated target represents the same rotation, so the
    # result must match the un-negated case rather than taking the long way
    far = quat_slerp(a, -b, 0.5)
    check(abs(np.linalg.norm(far) - 1.0) < 1e-12, "slerp handles opposite-sign quats")
    check(np.abs(far - mid).max() < 1e-12, "slerp takes the shortest arc")


def test_models(folder, label):
    files = sorted(glob.glob(os.path.join(folder, "*.glb")))
    print("\n%d %s models" % (len(files), label))
    check(len(files) > 0, "%s models present" % label)
    for f in files:
        name = os.path.splitext(os.path.basename(f))[0]
        m = SkinnedModel(f)
        # bind pose must reconstruct to identity -- validates inverse-bind,
        # node hierarchy, TRS composition and FK all at once
        err = np.abs(m.palette(None, 0.0) - I4).max()
        ok_rest = err < 1e-3
        ok_clips = True
        aniso = 0.0
        for cn, c in m.clips.items():
            for frac in (0.0, 0.5, 1.0):
                P = m.palette(cn, c.duration * frac)
                if not np.isfinite(P).all():
                    ok_clips = False
                    break
                R = P[:, :3, :3]
                # Loose bound: catches a degenerate or exploded matrix.  It
                # cannot be tight, because the authored data really does
                # carry uniform scale -- HQFPS keys scale 0.6 on every bone
                # of the double-barrel shotgun, in every clip (det 0.216).
                d = np.linalg.det(R)
                if d.min() < 0.05 or d.max() > 20.0:
                    ok_clips = False
                    break
                # Tight bound, and the one that actually proves the maths:
                # a correct bone matrix is a rotation times a near-uniform
                # scale, so its singular values must be nearly equal.  Bad
                # quaternions, a transposed matrix or a mis-ordered TRS all
                # show up here as shear long before they break the
                # determinant.  Real data peaks at 1.23.
                sv = np.linalg.svd(R, compute_uv=False)
                aniso = max(aniso, float((sv[:, 0] / np.maximum(sv[:, 2], 1e-9)).max()))
                if aniso > 2.0:
                    ok_clips = False
                    break
        check(ok_rest and ok_clips and len(m.clips) > 0,
              "%-4s %-22s %2d bones, %2d clips, rest_err=%.1e, shear=%.2f"
              % (label, name, len(m.joints), len(m.clips), err, aniso))


def test_sampling():
    print("\nclip sampling")
    f = os.path.join(MODELS, "M1911.glb")
    if not os.path.exists(f):
        check(False, "M1911.glb present")
        return
    m = SkinnedModel(f)
    c = m.clips["Fire"]
    check(c.duration > 0.0, "Fire clip has non-zero duration")
    # clamping outside the range must not explode
    for t in (-5.0, 0.0, c.duration, c.duration + 5.0):
        P = m.palette("Fire", t)
        if not np.isfinite(P).all():
            check(False, "palette finite at t=%.1f" % t)
            return
    check(True, "palette finite outside clip range (clamped)")
    a = m.palette("Fire", 0.0)
    b = m.palette("Fire", c.duration * 0.5)
    check(np.abs(a - b).max() > 1e-4, "clip actually animates")


def test_no_start_snap():
    """Every clip must be usable from t=0.

    The Blender NLA bake wrote the armature rest pose into each strip's
    first frame; left in, a clip snapped to a half-A-pose for one frame
    every time it started.  Clip() drops that key, so the pose at t=0 has
    to agree with the pose just after it.
    """
    print("\nno rest-pose snap at clip start")
    worst = (0.0, "", "")
    for folder in (MODELS, GUNS):
        for f in sorted(glob.glob(os.path.join(folder, "*.glb"))):
            m = SkinnedModel(f)
            for cn, c in m.clips.items():
                if c.duration < 0.2:
                    continue
                g0 = m.global_matrices(cn, 0.0)[:, :3, 3]
                g1 = m.global_matrices(cn, 0.05)[:, :3, 3]
                g2 = m.global_matrices(cn, 0.10)[:, :3, 3]
                step1 = float(np.abs(g0 - g1).max())
                step2 = float(np.abs(g1 - g2).max())
                # Flag only a FIRST step that dwarfs the one after it.  Real
                # motion is smooth frame to frame; the rest-pose artifact was
                # a one-frame outlier.  (HQFPS does legitimately teleport loose
                # bullet props into the hand, which is why the ratio matters
                # and a bare distance threshold would not do.)
                if step1 > 0.05 and step1 / max(step2, 1e-4) > 8.0 and step1 > worst[0]:
                    worst = (step1, os.path.basename(f), cn)
    check(worst[0] == 0.0, "no clip snaps on its first frame%s"
          % ("" if worst[0] == 0.0 else "  [%s %s jumped %.3f m]" % (worst[1], worst[2], worst[0])))


def test_idle_loops():
    print("\nidle clips loop seamlessly")
    for name in ("M1911", "AKM", "MP5"):
        f = os.path.join(MODELS, name + ".glb")
        if not os.path.exists(f):
            continue
        m = SkinnedModel(f)
        c = m.clips["Idle"]
        a = m.global_matrices("Idle", 0.0)[:, :3, 3]
        b = m.global_matrices("Idle", c.duration)[:, :3, 3]
        d = float(np.abs(a - b).max())
        check(d < 1e-3, "%-8s Idle start == end (%.1e m)" % (name, d))


def test_uv_convention():
    """UVs must reach the engine the way the engine's own importer sends them.

    glTF puts the UV origin at the top-left and Heaven3D uploads textures
    bottom-left, so its GLB importer flips V.  A loader that forgets is not
    obviously broken -- the model is perfectly shaped and perfectly lit, it
    just wears the wrong half of its own atlas.
    """
    print()
    print("UV origin matches the engine's importer")
    f = os.path.join(GUNS, "AKM.glb")
    m = SkinnedModel(f)
    g = m.glb.json
    raw = None
    for node in m.glb.nodes:
        if "mesh" in node and "skin" in node:
            prim = g["meshes"][node["mesh"]]["primitives"][0]
            raw = m.glb.accessor(prim["attributes"]["TEXCOORD_0"]).reshape(-1, 2)
            break
    ours = m.primitives()[0]["uvs"]
    check(raw is not None and ours is not None, "AKM has texture coordinates")
    if raw is None or ours is None:
        return
    check(np.abs(ours[:, 0] - raw[:, 0]).max() < 1e-6, "U is passed through unchanged")
    check(np.abs(ours[:, 1] - (1.0 - raw[:, 1])).max() < 1e-6,
          "V is flipped, as Heaven3D's own glTF importer does")


def test_grip_hand():
    """Each weapon must be anchored to the hand that is holding it.

    Both hands are on a rifle, but the knife is held in the right hand with
    the left arm hanging at the side, so the mid-point of the two sits in
    mid-air -- which is exactly where the melee weapons used to appear.
    """
    print()
    print("the holding hand is on the weapon")
    worst_mid = (0.0, "")
    bad = []
    for f in sorted(glob.glob(os.path.join(MODELS, "*.glb"))):
        name = os.path.splitext(os.path.basename(f))[0]
        gf = os.path.join(GUNS, name + ".glb")
        if not os.path.exists(gf):
            continue
        arms, gun = SkinnedModel(f), SkinnedModel(gf)
        clip = "Idle" if "Idle" in arms.clips else sorted(arms.clips)[0]
        t = arms.clips[clip].duration * 0.5
        names = [n.get("name", "") for n in arms.glb.nodes]
        G = arms.global_matrices(clip, t)
        hands = [G[i][:3, 3] for i, nm in enumerate(names) if nm in ("Hand.L", "Hand.R")]
        if len(hands) < 2:
            continue
        P = gun.palette(clip if clip in gun.clips else None, t)
        pts = []
        for pr in gun.primitives():
            v, j, w = pr["positions"], pr["joints"].astype(int), pr["weights"]
            o = np.zeros_like(v)
            for k in range(4):
                M = P[j[:, k]]
                o += w[:, k:k + 1] * (np.einsum("nij,nj->ni", M[:, :3, :3], v) + M[:, :3, 3])
            pts.append(o)
        pts = np.concatenate(pts)
        d = [float(np.linalg.norm(pts - h, axis=1).min()) for h in hands]
        if min(d) > 0.18:
            bad.append("%s (%.2f m)" % (name, min(d)))
        mid = np.mean(hands, axis=0)
        gap = float(np.linalg.norm(pts - mid, axis=1).min())
        if gap > worst_mid[0]:
            worst_mid = (gap, name)
    check(not bad, "every weapon has a hand within 18 cm of it%s"
          % ("" if not bad else "  " + ", ".join(bad)))
    # Not a requirement, just the evidence for why the anchor is one hand.
    print("  %-64s %s" % ("worst mid-point gap: %s at %.2f m"
                          % (worst_mid[1], worst_mid[0]), "info"))


def test_arm_gun_clips_match():
    """One clip name has to drive both halves of the viewmodel."""
    print("\narm and gun clip names agree")
    bad = []
    pairs = 0
    for f in sorted(glob.glob(os.path.join(MODELS, "*.glb"))):
        name = os.path.splitext(os.path.basename(f))[0]
        g = os.path.join(GUNS, name + ".glb")
        if not os.path.exists(g):
            continue          # Unarmed has no weapon mesh
        pairs += 1
        a, b = set(SkinnedModel(f).clips), set(SkinnedModel(g).clips)
        if a != b:
            bad.append("%s: %s" % (name, sorted(a ^ b)))
    check(pairs >= 19, "%d arm/gun pairs found" % pairs)
    check(not bad, "every weapon's arm clips == its gun clips%s"
          % ("" if not bad else "  " + "; ".join(bad)))


if __name__ == "__main__":
    test_quat()
    test_models(MODELS, "arm")
    test_models(GUNS, "gun")
    test_sampling()
    test_no_start_snap()
    test_idle_loops()
    test_uv_convention()
    test_grip_hand()
    test_arm_gun_clips_match()
    print()
    if fails:
        print("%d FAILED:" % len(fails))
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("all checks passed")
