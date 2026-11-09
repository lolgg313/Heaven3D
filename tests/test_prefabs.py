"""Prefabs in the real engine (needs a GPU / display):

    python tests/test_prefabs.py

  * Save as Prefab writes a .prefab and links the object to it
  * spawn_prefab places instances (unique names, own transform, children,
    materials, imported model, blueprints) - in the editor and during Play
  * editing an instance marks it modified; its own name / transform don't
  * Apply writes the instance into the prefab and refreshes the other
    unmodified instances; modified ones keep their edits
  * Revert rebuilds an instance from the prefab; Unlink forgets the prefab
  * save + reload keeps the links; when the .prefab changed on disk the
    unmodified instances follow it on load, modified ones don't
  * Duplicate keeps the link; a prefab can hold another prefab instance
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.argv = [sys.argv[0]]
spec = importlib.util.spec_from_file_location("Heaven3D", os.path.join(ROOT, "Heaven3D.py"))
H = importlib.util.module_from_spec(spec)
sys.modules["Heaven3D"] = H
spec.loader.exec_module(H)
H._h3d_set_splash_mode("off")
import glm  # noqa: E402

res = []


def check(n, c, d=""):
    res.append(bool(c))
    print(("PASS " if c else "FAIL ") + n + ("  [%s]" % (d,) if d != "" else ""), flush=True)


def col(n):
    return tuple(round(float(x), 3) for x in n.material.base_color[:3])


def main():
    work = tempfile.mkdtemp(prefix="h3d_prefab_")
    os.makedirs(os.path.join(work, "assets"))
    duck = os.path.join(ROOT, "tests", "importers", "assets", "glb")
    glb = None
    for f in sorted(os.listdir(duck)) if os.path.isdir(duck) else []:
        if f.lower().endswith(".glb"):
            glb = os.path.join(work, "assets", f)
            shutil.copy(os.path.join(duck, f), glb)
            break
    os.chdir(work)
    e = H.HeavenEngine(mode="editor", width=900, height=600)
    e.project_path = os.path.join(work, "main.heaven3d")
    for n in e.scene.root.traverse():
        if n.is_ocean:
            n.ocean_settings.enabled = False

    # ---- build an object worth reusing ----
    crate = e.add_cube(name="Crate")
    crate.material.base_color = (0.8, 0.2, 0.1, 1.0)
    crate.position = glm.vec3(3, 1, -2)
    lamp = e.add_point_light()
    lamp.remove()
    crate.add_child(lamp)
    lamp.position = glm.vec3(0, 1.5, 0)
    model = e.load_model(glb) if glb else None
    if model is not None:
        model.remove()
        crate.add_child(model)
    e.attach_blueprint(crate, H.Blueprint.from_dict({"class": "Animator"}) or H.Blueprint())
    n_kids = len(crate.children)

    path = e.save_prefab(crate, "prefabs/Crate")
    check("Save as Prefab writes a .prefab file", os.path.isfile(path) and path.endswith(".prefab"), path)
    data = json.load(open(path))
    check("the prefab holds the object and its children",
          data.get("type") == "heaven3d_prefab" and len(data["node"]["children"]) == n_kids,
          len(data["node"].get("children", [])))
    check("the prefab's own transform is neutral", data["node"]["position"] == [0, 0, 0])
    if model is not None:
        sp = [c.get("source_path") for c in data["node"]["children"] if c.get("source_path")]
        check("asset paths are stored relative to the prefab file", sp and not os.path.isabs(sp[0]), sp)
    check("the object became an instance", H.prefab_link(crate) is not None and not e.prefab_is_modified(crate))

    # ---- instances ----
    a = e.spawn_prefab("prefabs/Crate.prefab", position=(10, 0, 0))
    b = e.spawn_prefab(path, position=(-10, 0, 0), rotation=(0, 45, 0))
    check("spawn_prefab places instances with unique names", a.name != b.name and a.name.startswith("Crate"),
          (a.name, b.name))
    check("instances get their own transform", abs(a.position.x - 10) < 1e-5 and abs(b.rotation_euler.y - 45) < 1e-5)
    check("instances carry children + material", len(a.children) == n_kids and col(a) == (0.8, 0.2, 0.1),
          (len(a.children), col(a)))
    check("instances carry blueprints", len(a.blueprints) == len(crate.blueprints), len(a.blueprints))
    if model is not None:
        m2 = [c for c in a.children if c.source_path]
        check("imported model inside the prefab loads", m2 and m2[0].mesh is not None)
    check("prefab_instances finds them all", len(e.prefab_instances(path)) == 3, len(e.prefab_instances(path)))

    # ---- modified ----
    a.position = glm.vec3(11, 2, 0)
    a.name = "Renamed"
    check("moving / renaming an instance doesn't modify it", not e.prefab_is_modified(a))
    b.material.base_color = (0.1, 0.9, 0.1, 1.0)
    check("editing an instance's material marks it modified", e.prefab_is_modified(b))
    check("... and only that instance", not e.prefab_is_modified(a))

    # ---- apply ----
    crate.material.base_color = (0.2, 0.3, 0.9, 1.0)
    e.prefab_apply(crate)
    a2 = [n for n in e.prefab_instances(path) if n.name == "Renamed"][0]
    check("Apply writes the prefab", json.load(open(path))["node"]["material"]["base_color"][:3] == [0.2, 0.3, 0.9],
          json.load(open(path))["node"]["material"]["base_color"])
    check("Apply refreshes unmodified instances", col(a2) == (0.2, 0.3, 0.9), col(a2))
    check("... keeping their own name and place", abs(a2.position.x - 11) < 1e-5 and a2.name == "Renamed")
    bb = [n for n in e.prefab_instances(path) if n.name == b.name][0]
    check("Apply leaves modified instances alone", col(bb) == (0.1, 0.9, 0.1), col(bb))

    # ---- revert ----
    rb = e.prefab_revert(bb)
    check("Revert rebuilds from the prefab", col(rb) == (0.2, 0.3, 0.9) and not e.prefab_is_modified(rb), col(rb))
    check("Revert keeps the instance's rotation", abs(rb.rotation_euler.y - 45) < 1e-5)

    # ---- duplicate ----
    e._duplicate_node(rb)
    dup = e.selected
    check("Duplicate keeps the prefab link", dup is not rb and H.prefab_link(dup) is not None
          and not e.prefab_is_modified(dup))

    # ---- nested prefab ----
    holder = e.add_cube(name="Holder")
    inner = e.spawn_prefab(path)
    inner.remove()
    holder.add_child(inner)
    p2 = e.save_prefab(holder, "prefabs/Holder.prefab")
    outer = e.spawn_prefab(p2, position=(0, 0, 20))
    nested = [c for c in outer.children if H.prefab_link(c)]
    check("a prefab can contain another prefab's instance",
          len(nested) == 1 and os.path.normcase(H.prefab_link(nested[0])["path"]) == os.path.normcase(path),
          [H.prefab_link(c) for c in outer.children])

    # ---- save / reload / outside edit ----
    e.prefab_unlink(dup)
    check("Unlink forgets the prefab", H.prefab_link(dup) is None)
    bb2 = [n for n in e.prefab_instances(path) if n.name == "Renamed"][0]
    bb2.material.roughness = 0.07                 # modified on purpose
    mod_name = bb2.name
    e.save_project(e.project_path)
    saved = json.load(open(e.project_path))
    links = [c for c in saved["scene"]["children"] if c.get("prefab")]
    check("the project stores prefab links (relative)",
          links and not os.path.isabs(links[0]["prefab"]["path"]), [c["prefab"]["path"] for c in links][:3])
    e.load_project(e.project_path)
    check("links survive reload", len(e.prefab_instances(path)) >= 3, len(e.prefab_instances(path)))
    unmod = [n for n in e.prefab_instances(path) if n.name != mod_name and n.parent is e.scene.root]
    check("reloaded instances are not marked modified", unmod and not any(e.prefab_is_modified(n) for n in unmod),
          [(n.name, e.prefab_is_modified(n)) for n in unmod])
    check("a modified instance stays modified after reload",
          e.prefab_is_modified([n for n in e.prefab_instances(path) if n.name == mod_name][0]))
    pd = json.load(open(path))
    pd["node"]["material"]["base_color"] = [1.0, 1.0, 0.0, 1.0]       # someone edits the prefab
    json.dump(pd, open(path, "w"))
    e.load_project(e.project_path)
    unmod = [n for n in e.prefab_instances(path) if n.name != mod_name and n.parent is e.scene.root]
    check("unmodified instances follow a changed prefab on load",
          unmod and all(col(n) == (1.0, 1.0, 0.0) for n in unmod), [col(n) for n in unmod])
    keep = [n for n in e.prefab_instances(path) if n.name == mod_name][0]
    check("modified instances keep their edits on load", col(keep) == (0.2, 0.3, 0.9), col(keep))
    check("an unlinked object is plain after reload",
          not any(H.prefab_link(n) for n in e.scene.root.traverse() if n.name == dup.name))

    # ---- missing prefab file ----
    os.rename(path, path + ".gone")
    e.load_project(e.project_path)
    check("a missing .prefab keeps the saved instances", len(e.prefab_instances(path)) >= 3)
    os.rename(path + ".gone", path)

    # ---- play mode ----
    e.toggle_play()
    try:
        n0 = len(e.scene.root.children)
        s = e.spawn_prefab(path, position=(0, 5, 0))
        check("spawn_prefab works during Play", s is not None and len(e.scene.root.children) == n0 + 1)
    finally:
        e.toggle_play()
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
