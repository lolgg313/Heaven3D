"""Packs the built-in CC0 animation set (assets/anims/library.npz).

Source: Quaternius "Universal Animation Library" 1 and 2 (Standard), CC0 1.0,
https://quaternius.com/packs/universalanimationlibrary.html - downloaded from
opengameart.org:
  universal_animation_librarystandard.zip   -> Godot/AnimationLibrary_Godot_Standard.glb
  universal_animation_library_2standard.zip -> Unreal-Godot/UAL2_Standard.glb

    python tools/build_anims.py UAL1.glb UAL2.glb assets/anims/library.npz

Each clip is stored as its source skeleton's global rotations (quaternions) and
joint positions per frame plus the rest pose, so it is retargeted onto every
character exactly like a Mixamo file.
"""
import importlib.util
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("mh_anim", os.path.join(HERE, "..", "mh_anim.py"))
A = importlib.util.module_from_spec(spec)
spec.loader.exec_module(A)

KEEP = {
    0: ["Idle_Loop", "Idle_Talking_Loop", "Walk_Loop", "Walk_Formal_Loop", "Jog_Fwd_Loop", "Sprint_Loop",
        "Death01", "Hit_Chest", "Hit_Head", "Punch_Jab", "Punch_Cross", "Pistol_Idle_Loop", "Pistol_Shoot",
        "Pistol_Aim_Neutral", "Pistol_Reload", "Sword_Attack", "Sword_Idle", "Interact", "Dance_Loop",
        "Crouch_Idle_Loop", "Crouch_Fwd_Loop", "PickUp_Table", "Sitting_Idle_Loop", "A_TPose"],
    1: ["Idle_FoldArms_Loop", "Idle_TalkingPhone_Loop", "Idle_No_Loop", "Yes", "Idle_Rail_Call", "Zombie_Idle_Loop",
        "Zombie_Walk_Fwd_Loop", "Zombie_Scratch", "Hit_Knockback", "Melee_Hook", "Walk_Carry_Loop", "Consume",
        "OverhandThrow", "Idle_Lantern_Loop"],
}


def main(u1, u2, out):
    arrays = {}
    index = {"source": "Quaternius Universal Animation Library 1 + 2 (Standard), CC0 1.0", "rigs": [], "clips": {}}
    for ri, path in enumerate((u1, u2)):
        srcs = A.load_gltf(path)
        s0 = srcs[0]
        rig = f"rig{ri}"
        index["rigs"].append({"id": rig, "names": s0.names, "parents": [int(p) for p in s0.parents]})
        arrays[f"{rig}.rest"] = s0.rest.astype(np.float32)
        for s in srcs:
            if s.name not in KEEP[ri]:
                continue
            q = A.m_to_q(s.frames[:, :, :3, :3]).astype(np.float32)
            p = s.frames[:, :, :3, 3].astype(np.float32)
            arrays[f"{s.name}.q"] = q
            arrays[f"{s.name}.p"] = p
            index["clips"][s.name] = {"rig": rig, "frames": int(len(q)), "fps": float(s.fps)}
    missing = [n for r in KEEP.values() for n in r if n not in index["clips"]]
    if missing:
        print("missing clips:", missing)
    arrays["index.json"] = np.frombuffer(json.dumps(index).encode("utf-8"), dtype=np.uint8)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    np.savez_compressed(out, **arrays)
    print(f"{len(index['clips'])} clips -> {out} ({os.path.getsize(out) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main(*sys.argv[1:4])
