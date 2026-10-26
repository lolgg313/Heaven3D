"""
Editor-level checks that an imported model behaves as ONE object
(children/parts can not be selected, moved or deleted separately).

    python tests/test_one_object.py            # needs a GPU / desktop
"""
import sys, os, math, importlib.util, json, tempfile, shutil
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ASSETS = os.path.join(HERE, "importers", "assets")
eng_file = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "Heaven3D.py")
spec = importlib.util.spec_from_file_location("Heaven3D", eng_file)
H = importlib.util.module_from_spec(spec); sys.modules["Heaven3D"] = H; spec.loader.exec_module(H)
import pygame, moderngl, glm, numpy as np
H._h3d_set_splash_mode("off")
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)


def parts_of(node):
    return [c for c in node.children if getattr(c, "_heaven3d_imported_part", False)]


def rows(eng):
    return [c.name for c in eng.scene.root.children if not c.name.startswith("__")]


work = tempfile.mkdtemp(prefix="h3d_lock_")
truck_src = os.path.join(ASSETS, "glb", "CesiumMilkTruck.glb")
truck = os.path.join(work, "assets", "CesiumMilkTruck.glb")
os.makedirs(os.path.dirname(truck)); shutil.copy(truck_src, truck)
blend_src = os.path.join(ASSETS, "blend", "b1_hierarchy_multimat_v50.blend")
blend = os.path.join(work, "assets", "hierarchy.blend"); shutil.copy(blend_src, blend)
os.chdir(work)

eng = H.HeavenEngine(mode="editor", width=1000, height=700)
for n in eng.scene.root.traverse():
    if n.is_ocean:
        n.ocean_settings.enabled = False
base_rows = len(rows(eng))
node = eng.load_model(truck)
check("import returns one root node", node is not None)
P = parts_of(node)
check("multi-material model -> extra parts are locked children", len(P) >= 2, f"{1 + len(P)} parts")
check("hierarchy lists ONLY the model root", len(rows(eng)) == base_rows + 1, f"rows={rows(eng)}")
check("parts flagged locked", all(getattr(c, "_h3d_locked", False) for c in P))
# selecting a part (what a viewport click resolves to) selects the root
eng.select_node(P[-1])
check("clicking a part selects the whole object", eng.selected is node)
# real viewport pick: aim the camera at a part and pick the centre pixel
c = P[-1]
lo, hi = eng._spawn_mesh_local_aabb(node)
pos_arr, _ = H._mesh_cpu_triangles(c.mesh)
target = glm.vec3(*pos_arr.mean(0).tolist())
eng.camera.position = target + glm.vec3(0.0, 0.6, 6.0)
d = glm.normalize(target - eng.camera.position)
eng.camera.yaw = math.degrees(math.atan2(-d.x, -d.z)); eng.camera.pitch = math.degrees(math.asin(d.y))
eng.deselect()
eng._pick_scene((eng.width // 2, eng.height // 2))
check("viewport ray pick on a part resolves to the root", eng.selected is node,
      f"selected={getattr(eng.selected, 'name', None)}")
# gizmo moves everything together
node.position = glm.vec3(3, 0, 0)
wp = [glm.vec3(x.world_matrix()[3]) for x in P]
check("moving the root moves every part", all(abs(w.x - 3) < 1e-5 for w in wp))
# duplicate
eng.select_node(node)
eng._duplicate_node(node)
dup = eng.selected
check("duplicate creates one new root", dup is not node and dup is not None and len(rows(eng)) == base_rows + 2)
check("duplicate carries the same locked parts", len(parts_of(dup)) == len(P))
# material tweak survives save/load
node.material.base_color = (0.9, 0.1, 0.1, 1.0)
proj = os.path.join(work, "main.heaven3d")
eng.save_project(proj)
data = json.load(open(proj))
names = []
def walk(d):
    names.append(d["name"]); [walk(c) for c in d.get("children", [])]
walk(data["scene"])
check("save file stores the model once (no part nodes)", not any("_part" in n for n in names), f"{len(names)} nodes saved")
rel = [n for n in [data["scene"]] for c in n["children"] if c.get("source_path")]
src_paths = [c["source_path"] for c in data["scene"]["children"] if c.get("source_path")]
check("asset path saved relative to the project", all(not os.path.isabs(p) for p in src_paths), str(src_paths))
# delete the duplicate
eng._delete_node(dup)
check("deleting the root removes all its parts", len(rows(eng)) == base_rows + 1 and dup.parent is None)
# physics: whole-model colliders
eng.add_box_collider(node)
bc = node.collider
size = glm.vec3(*bc.size) if not isinstance(bc.size, glm.vec3) else bc.size
check("+Box collider fits the WHOLE model", abs(size.x - (hi - lo).x) < 1e-3 and abs(size.y - (hi - lo).y) < 1e-3,
      f"box={tuple(round(v, 3) for v in size)} model={tuple(round(v, 3) for v in (hi - lo))}")
node.collider = None
eng.add_mesh_collider(node)
view = H._mesh_shape_view(node, node.collider)
total = sum(int(x.mesh.index_count) // 3 for x in [node] + P)
ntri = len(view.get("tris", view.get("bvh", None).tris if hasattr(view.get("bvh", None), "tris") else [])) if isinstance(view, dict) else 0
proxy = getattr(node, "_h3d_collision_proxy", None)
check("+Mesh collider uses the triangles of ALL parts",
      proxy is not None and len(proxy[1].cpu_indices) // 3 == total, f"{total} triangles")
# play mode: a ball dropped onto a PART (not the root mesh) must land on it
ball = eng.add_sphere(radius=0.25, name="Ball")
top = float((glm.vec3(node.world_matrix() * glm.vec4(hi, 1))).y)
part_center = glm.vec3(P[-1].world_matrix() * glm.vec4(*pos_arr.mean(0).tolist(), 1.0))
ball.position = glm.vec3(part_center.x, top + 2.0, part_center.z)
eng.add_rigidbody(ball)
eng._begin_play()
for _ in range(240):
    eng.physics.step(1 / 60.0) if hasattr(eng.physics, "step") else None
y = float(ball.position.y)
check("dynamic ball rests on the model's mesh collider (not falling through)", y > float(lo.y) - 0.5,
      f"ball y={y:.2f}, model bottom={float(lo.y):.2f}, top={top:.2f}")
eng._stop_play()
# reload the project in a fresh scene
for ch in list(eng.scene.root.children):
    if ch.source_path:
        eng._delete_node(ch)
eng.load_project(proj)
re = [c for c in eng.scene.root.children if c.source_path]
check("project reload re-imports every model as one object (2 saved)",
      len(re) == 2 and all(len(parts_of(r)) == len(P) for r in re), f"roots={len(re)}")
check("material edit survived save/load", re and abs(re[0].material.base_color[0] - 0.9) < 1e-6)
# .blend lights are locked parts as well
bn = eng.load_model(blend)
before = len(rows(eng)) - 1
check(".blend with parented objects imports as one object", bn is not None and len(rows(eng)) == before + 1
      and all(getattr(c, "_h3d_locked", False) for c in parts_of(bn)), f"{1 + len(parts_of(bn))} parts")
fails = [r for r in RESULTS if not r[1]]
print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} checks passed")
shutil.rmtree(work, ignore_errors=True)
os._exit(0 if not fails else 1)
