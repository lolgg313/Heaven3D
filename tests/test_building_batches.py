"""Building sectors drawn as cached batches look exactly like before (needs a GPU / display):

    python tests/test_building_batches.py [shots folder]

Every scenario is rendered twice in the same engine: with the sector batches
and with them switched off (the old per-object path).  The two pictures must
match.  Scenarios: plain sectors, a sector moved by a script, a single module
moved / hidden / re-materialled / erased by a script, a module selected,
a module given a script, a deleted sector, a project saved and loaded.
Also checks that batches really are used (draw counts) and that a frame's
Python work stays small with many sectors.
"""
import importlib.util
import os
import sys
import tempfile
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
SHOTS = sys.argv[1] if len(sys.argv) > 1 else None
sys.argv = [sys.argv[0]]
spec = importlib.util.spec_from_file_location("Heaven3D", os.path.join(ROOT, "Heaven3D.py"))
H = importlib.util.module_from_spec(spec)
sys.modules["Heaven3D"] = H
spec.loader.exec_module(H)
H._h3d_set_splash_mode("off")
import glm  # noqa: E402
import pygame  # noqa: E402

res = []
W, HH = 320, 200


def check(n, c, d=""):
    res.append(bool(c))
    print(("PASS " if c else "FAIL ") + n + ("  [%s]" % (d,) if d != "" else ""), flush=True)


def shot(e, frames=3):
    for _ in range(frames):
        e.renderer.render(e.scene, e.camera)
    e.ctx.finish()
    return np.frombuffer(e.ctx.screen.read(components=3), np.uint8).reshape(HH, W, 3)[::-1].astype(np.int16)


def same_both_ways(e, tag, frames=4):
    P = H._H3D274_PERF
    a = shot(e, frames)
    P["on"] = False
    try:
        b = shot(e, 2)
    finally:
        P["on"] = True
    d = np.abs(a - b)
    if SHOTS:
        for nm, img in (("batched", a), ("plain", b)):
            pygame.image.save(pygame.image.frombuffer(np.ascontiguousarray(img.astype(np.uint8)).tobytes(), (W, HH),
                                                      "RGB"), os.path.join(SHOTS, "%s_%s.png" % (tag.replace(" ", "_"), nm)))
    ok = float(d.mean()) < 0.6 and float((d > 24).mean()) < 0.002
    check(tag + ": same picture batched and not", ok, "mean %.3f, >24: %.4f%%" % (d.mean(), 100 * (d > 24).mean()))
    return a


def main():
    work = tempfile.mkdtemp(prefix="h3dbatch_")
    os.chdir(work)
    e = H.HeavenEngine(mode="editor", width=W, height=HH)
    e.project_path = os.path.join(work, "main.heaven3d")
    for n in e.scene.root.traverse():
        if n.is_ocean:
            n.ocean_settings.enabled = False
        if n.is_clouds:
            n.visible = False
    gen = e.procedural_generator or H.ProceduralSectorGenerator(e)
    e.procedural_generator = gen
    secs = [e.add_procedural_sector((x, 0.0, 0.0), b, 3, (8, 8))
            for x, b in ((0.0, "military"), (22.0, "urban"), (44.0, "military"))]
    e.deselect()
    e.camera.position = glm.vec3(22, 18, 48)
    e.camera.yaw, e.camera.pitch = 0.0, -22.0
    same_both_ways(e, "three sectors")
    shot(e, 1)
    check("sectors really are batched", len(H._SB["cache"]) >= 3
          and sum(1 for v in H._SB["cache"].values() if v.get("groups")) == 3,
          sum(1 for v in H._SB["cache"].values() if v.get("groups")))

    secs[1].position = glm.vec3(22.0, 0.0, -6.0)            # a script moves a whole sector
    same_both_ways(e, "sector moved by a script")
    mod = [c for c in secs[0].children if c.mesh is not None][5]
    mod.position = mod.position + glm.vec3(0, 3.0, 0)       # a script moves one module
    same_both_ways(e, "module moved by a script", frames=6)
    mod2 = [c for c in secs[2].children if c.mesh is not None][40]
    mod2.visible = False
    same_both_ways(e, "module hidden", frames=6)
    mod3 = [c for c in secs[2].children if c.mesh is not None][60]
    red = H.Material(name="red", base_color=(1.0, 0.0, 0.0, 1.0))
    mod3.material = red
    same_both_ways(e, "module re-materialled", frames=6)
    victim = [c for c in secs[0].children if c.mesh is not None][80]
    victim.remove()
    same_both_ways(e, "module erased", frames=2)
    e.select_node([c for c in secs[1].children if c.mesh is not None][3])
    same_both_ways(e, "module selected", frames=2)
    check("a sector with a selected module is drawn object by object",
          id(secs[1]) not in [id(x["node"]) for x in H._SB["frame"].get(id(e.scene), (None, []))[1]]
          if H._SB["frame"].get(id(e.scene)) else True)
    e.deselect()
    e.attach_blueprint([c for c in secs[2].children if c.mesh is not None][10],
                       H.BlueprintRegistry.get("Animator") or H.Blueprint)
    same_both_ways(e, "module given a script", frames=2)
    shot(e, 1)
    ent = H._SB["cache"].get(id(secs[2]))
    check("a sector holding a scripted module isn't batched", ent is None or ent.get("groups") is None)
    e._delete_node(secs[0])
    same_both_ways(e, "sector deleted", frames=2)
    e.save_project(e.project_path)
    e.load_project(e.project_path)
    for n in e.scene.root.traverse():
        if n.is_ocean:
            n.ocean_settings.enabled = False
        if n.is_clouds:
            n.visible = False
    same_both_ways(e, "saved and loaded", frames=3)

    # draw calls + Python time with many sectors
    for k in range(30):
        e.add_procedural_sector((float(k % 6) * 22.0, 0.0, -40.0 - 22.0 * (k // 6)), "urban", 3, (8, 8))
    e.deselect()
    shot(e, 2)
    r = e.renderer
    n_mod = sum(1 for _ in e.scene.root.traverse())
    check("30+ sectors draw in few calls", r.stat_draw_calls < 200, "%d calls for %d objects" % (r.stat_draw_calls, n_mod))
    import moderngl
    old_r, old_c = moderngl.VertexArray.render, moderngl.Context.copy_framebuffer
    moderngl.VertexArray.render = lambda *a, **k: None
    moderngl.Context.copy_framebuffer = lambda *a, **k: None
    try:
        t0 = time.perf_counter()
        for _ in range(10):
            r.render(e.scene, e.camera)
        ms = (time.perf_counter() - t0) / 10 * 1000
        H._H3D274_PERF["on"] = False
        t0 = time.perf_counter()
        for _ in range(3):
            r.render(e.scene, e.camera)
        ms_off = (time.perf_counter() - t0) / 3 * 1000
        H._H3D274_PERF["on"] = True
    finally:
        moderngl.VertexArray.render, moderngl.Context.copy_framebuffer = old_r, old_c
    check("renderer Python time with %d objects: %.1f ms (was %.1f ms)" % (n_mod, ms, ms_off), ms < ms_off / 3, "")
    print("\n%d/%d passed" % (sum(res), len(res)))
    return 0 if all(res) else 1


if __name__ == "__main__":
    try:
        code = main()
    except Exception:
        import traceback
        traceback.print_exc()
        code = 1
    sys.stdout.flush()
    os._exit(code)
