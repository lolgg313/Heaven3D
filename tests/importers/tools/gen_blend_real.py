"""Make the "real world" .blend test set with real Blender (bpy) + ground truth.

    python gen_blend_real.py <out_dir> <tag>        (run with a Python that has bpy)

Writes r1..r5 .blend files (Blender's 'Compress' = zstd) and truth_<tag>.json:
per object Blender's evaluated triangles, world bounding box and a sample of
corners (position + normal) - what you see in Blender's viewport.

  r1_curves     Bezier / Poly / NURBS curves: bevel depth + resolution, extrude,
                caps, radius, 2D fills, NURBS surfaces
  r2_modifiers  Simple Deform (twist / bend / taper / stretch, every axis, limits,
                lock), Displace (normal / axis / global), Smooth
  r3_normals    custom split normals (random per corner, sharp edges + flat faces
                + n-gons, Weighted Normal on Suzanne), shape keys
  r4_scene      materials (Mix Shader, Diffuse, Emission, metal, RGB node,
                object-linked), hidden objects / collections, external textures
"""
import json
import math
import os
import random
import sys

import bpy
import bmesh
from mathutils import Vector

OUT = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else sys.argv[1]
TAG = (sys.argv[sys.argv.index("--") + 2] if "--" in sys.argv else sys.argv[2]) if len(sys.argv) > 2 else "v50"
os.makedirs(OUT, exist_ok=True)
random.seed(7)
GRID = 9.0


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.preferences.filepaths.save_version = 0      # no .blend1 backups


def link(o):
    bpy.context.scene.collection.objects.link(o)
    return o


def slot(i, row=0):
    return Vector((i * GRID, row * GRID, 0.0))


# ------------------------------------------------------------------ r1
def curve(name, kind, pts, i, cyclic=False, dim="3D", **kw):
    cu = bpy.data.curves.new(name, "CURVE")
    cu.dimensions = dim
    sp = cu.splines.new(kind)
    if kind == "BEZIER":
        sp.bezier_points.add(len(pts) - 1)
        for p, co in zip(sp.bezier_points, pts):
            p.co = co
            p.handle_left_type = p.handle_right_type = "AUTO"
    else:
        sp.points.add(len(pts) - 1)
        for p, co in zip(sp.points, pts):
            p.co = (*co, 1)
        if kind == "NURBS":
            sp.order_u = 4
            sp.use_endpoint_u = True
    sp.use_cyclic_u = cyclic
    radius = kw.pop("radius_mid", None)
    for k, v in kw.items():
        setattr(cu, k, v)
    if radius is not None:
        sp.bezier_points[1].radius = radius
    o = link(bpy.data.objects.new(name, cu))
    o.location = slot(i % 8, i // 8)
    return o


def r1():
    reset()
    P3 = [(0, 0, 0), (2, 2, 0), (4, 0, 1)]
    SQ = [(0, 0, 0), (2, 0, 0), (2, 2, 0), (0, 2, 0)]
    C4 = [(1, 0, 0), (0, 1, 0), (-1, 0, 0), (0, -1, 0)]
    specs = [
        ("BezDepth", "BEZIER", P3, {}, dict(bevel_depth=0.2, bevel_resolution=3)),
        ("BezRes0", "BEZIER", P3, {}, dict(bevel_depth=0.3, bevel_resolution=0, resolution_u=4)),
        ("BezExtrude", "BEZIER", P3, {}, dict(extrude=0.5)),
        ("BezDepthExtrude", "BEZIER", P3, {}, dict(bevel_depth=0.2, extrude=0.4, bevel_resolution=2)),
        ("Fill2D", "POLY", SQ, dict(cyclic=True, dim="2D"), dict(fill_mode="BOTH")),
        ("Fill2DExtrude", "POLY", SQ, dict(cyclic=True, dim="2D"), dict(fill_mode="BOTH", extrude=0.3)),
        ("Circle2D", "BEZIER", C4, dict(cyclic=True, dim="2D"), dict(fill_mode="BOTH", extrude=0.2)),
        ("Poly3D", "POLY", [(0, 0, 0), (1, 0, 0), (1, 1, 1), (2, 1, 1)], {}, dict(bevel_depth=0.1)),
        ("Nurbs", "NURBS", [(0, 0, 0), (1, 2, 0), (3, 2, 0), (4, 0, 1), (5, 0, 0)], {},
         dict(bevel_depth=0.15, bevel_resolution=1)),
        ("Caps", "BEZIER", P3, {}, dict(bevel_depth=0.2, use_fill_caps=True)),
        ("FrontOnly", "POLY", SQ, dict(cyclic=True, dim="2D"), dict(fill_mode="FRONT", extrude=0.3)),
        ("Radius", "BEZIER", P3, {}, dict(bevel_depth=0.2, radius_mid=2.0)),
        ("Open2D", "POLY", [(0, 0, 0), (1, 0, 0), (1, 1, 0)], dict(dim="2D"), dict(extrude=0.5)),
        ("CyclicPipe", "BEZIER", [(0, 0, 0), (3, 0, 0), (3, 3, 0), (0, 3, 1)], dict(cyclic=True),
         dict(bevel_depth=0.25)),
    ]
    tol = {}
    for i, (name, kind, pts, a, kw) in enumerate(specs):
        curve(name, kind, pts, i, **a, **kw)
    tol["CyclicPipe"] = 0.08          # the ring of a closed pipe may sit rotated (same surface)
    for j, op in enumerate(("primitive_nurbs_surface_sphere_add", "primitive_nurbs_surface_surface_add")):
        getattr(bpy.ops.surface, op)(location=slot(6 + j, 1))
        bpy.context.object.name = ("NurbsSphere", "NurbsPatch")[j]
    return tol


# ------------------------------------------------------------------ r2
def box(name, i, row):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(1.0, 0.6, 3.0), verts=bm.verts)
    bmesh.ops.translate(bm, vec=(0.2, 0.1, 0.4), verts=bm.verts)
    bmesh.ops.subdivide_edges(bm, edges=bm.edges, cuts=5, use_grid_fill=True)
    bm.to_mesh(me)
    bm.free()
    o = link(bpy.data.objects.new(name, me))
    o.location = slot(i, row)
    o.rotation_euler = (0.3, 0.0, 0.5)
    return o


def r2():
    reset()
    cases = []
    for mode in ("TWIST", "BEND", "TAPER", "STRETCH"):
        for axis in ("X", "Y", "Z"):
            cases.append(("SD_%s_%s" % (mode, axis), "SIMPLE_DEFORM",
                          dict(deform_method=mode, deform_axis=axis,
                               factor=0.6 if mode in ("TAPER", "STRETCH") else 0.9)))
    cases += [("SD_BendLimits", "SIMPLE_DEFORM", dict(deform_method="BEND", deform_axis="Z", factor=1.2,
                                                       limits=(0.2, 0.7))),
              ("SD_TwistHalf", "SIMPLE_DEFORM", dict(deform_method="TWIST", deform_axis="Z", factor=1.5,
                                                      limits=(0.0, 0.5))),
              ("SD_TaperLockX", "SIMPLE_DEFORM", dict(deform_method="TAPER", deform_axis="Z", factor=0.5,
                                                       lock_x=True)),
              ("DispNormal", "DISPLACE", dict(strength=0.3)),
              ("DispZMid", "DISPLACE", dict(strength=0.5, direction="Z", mid_level=0.2)),
              ("DispGlobalX", "DISPLACE", dict(strength=-0.4, direction="X", space="GLOBAL")),
              ("Smooth", "SMOOTH", dict(factor=0.5, iterations=3)),
              ("SmoothXY", "SMOOTH", dict(factor=1.0, iterations=2, use_z=False)),
              ("SmoothNeg", "SMOOTH", dict(factor=-0.5, iterations=1))]
    for i, (name, kind, kw) in enumerate(cases):
        o = box(name, i % 7, i // 7)
        m = o.modifiers.new("M", kind)
        for k, v in kw.items():
            if k == "limits":
                m.limits = v
            else:
                setattr(m, k, v)
    return {}


# ------------------------------------------------------------------ r3
def r3():
    reset()
    bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=12, location=slot(0))
    o = bpy.context.object
    o.name = "RandomNormals"
    bpy.ops.object.shade_smooth()
    me = o.data
    ns = []
    for lp in me.loops:
        v = me.vertices[lp.vertex_index].co.normalized()
        ns.append((v + Vector([random.uniform(-.4, .4) for _ in range(3)])).normalized())
    me.normals_split_custom_set(ns)
    bpy.ops.mesh.primitive_cylinder_add(vertices=12, location=slot(1))
    o = bpy.context.object
    o.name = "SharpCylinder"
    o.rotation_euler = (0.4, 0.2, 0.1)
    me = o.data
    for f in me.polygons:
        f.use_smooth = len(f.vertices) == 4
    me.polygons[0].use_smooth = False
    for e in me.edges[:6]:
        e.use_edge_sharp = True
    me.normals_split_custom_set_from_vertices([(v.co + Vector((0.3, 0.1, 0))).normalized() for v in me.vertices])
    bpy.ops.mesh.primitive_monkey_add(location=slot(2))
    o = bpy.context.object
    o.name = "WeightedSuzanne"
    bpy.ops.object.shade_smooth()
    bpy.ops.object.modifier_add(type="WEIGHTED_NORMAL")
    bpy.ops.object.modifier_apply(modifier=o.modifiers[0].name)
    bpy.ops.mesh.primitive_cube_add(location=slot(3))
    o = bpy.context.object
    o.name = "CustomCube"
    o.data.normals_split_custom_set_from_vertices([v.co.normalized() for v in o.data.vertices])
    # shape keys: relative values, a muted key, a key relative to another key
    bpy.ops.mesh.primitive_cube_add(location=slot(4))
    o = bpy.context.object
    o.name = "ShapeKeys"
    o.shape_key_add(name="Basis")
    k = o.shape_key_add(name="Tall")
    for i, v in enumerate(o.data.vertices):
        if v.co.z > 0:
            k.data[i].co = v.co + Vector((0, 0, 2))
    k.value = 0.75
    k2 = o.shape_key_add(name="Muted")
    for i, v in enumerate(o.data.vertices):
        k2.data[i].co = v.co * 3
    k2.value = 1.0
    k2.mute = True
    k3 = o.shape_key_add(name="Wide")
    k3.relative_key = k
    for i, v in enumerate(o.data.vertices):
        k3.data[i].co = k.data[i].co + Vector((v.co.x * 0.5, 0, 0))
    k3.value = 0.5
    return {}


# ------------------------------------------------------------------ r4
def pmat(name, color, **inputs):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    p = m.node_tree.nodes["Principled BSDF"]
    p.inputs["Base Color"].default_value = (*color, 1)
    for k, v in inputs.items():
        p.inputs[k.replace("_", " ")].default_value = v
    return m


def shader_mat(name, kind, color):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.remove(nt.nodes["Principled BSDF"])
    n = nt.nodes.new(kind)
    n.inputs["Color"].default_value = (*color, 1)
    if kind == "ShaderNodeEmission":
        n.inputs["Strength"].default_value = 3
    nt.links.new(n.outputs[0], nt.nodes["Material Output"].inputs["Surface"])
    return m


def r4():
    reset()
    td = os.path.join(OUT, "textures")
    os.makedirs(td, exist_ok=True)

    def sphere(name, i, m):
        bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8, location=slot(i))
        o = bpy.context.object
        o.name = name
        o.data.materials.append(m)
        return o
    m = bpy.data.materials.new("Mix")
    m.use_nodes = True
    nt = m.node_tree
    a = nt.nodes["Principled BSDF"]
    a.inputs["Base Color"].default_value = (1, 0, 0, 1)
    b = nt.nodes.new("ShaderNodeBsdfPrincipled")
    b.inputs["Base Color"].default_value = (0, 0, 1, 1)
    mix = nt.nodes.new("ShaderNodeMixShader")
    mix.inputs[0].default_value = 0.5
    nt.links.new(a.outputs[0], mix.inputs[1])
    nt.links.new(b.outputs[0], mix.inputs[2])
    nt.links.new(mix.outputs[0], nt.nodes["Material Output"].inputs["Surface"])
    sphere("MixRedBlue", 0, m)
    sphere("Glow", 1, shader_mat("EmitOnly", "ShaderNodeEmission", (0, 1, 0)))
    sphere("Matte", 2, shader_mat("Diffuse", "ShaderNodeBsdfDiffuse", (1, 1, 0)))
    sphere("Gold", 3, pmat("Metal", (0.9, 0.6, 0.2), Metallic=1.0, Roughness=0.2))
    m = bpy.data.materials.new("RGBNode")
    m.use_nodes = True
    rgb = m.node_tree.nodes.new("ShaderNodeRGB")
    rgb.outputs[0].default_value = (0, 1, 1, 1)
    m.node_tree.links.new(rgb.outputs[0], m.node_tree.nodes["Principled BSDF"].inputs["Base Color"])
    sphere("CyanViaRGB", 4, m)
    bpy.ops.mesh.primitive_cube_add(location=slot(5))
    o = bpy.context.object
    o.name = "ObjLinked"
    o.data.materials.append(pmat("MeshRed", (1, 0, 0)))
    o.material_slots[0].link = "OBJECT"
    o.material_slots[0].material = pmat("ObjGreen", (0, 1, 0))
    # an external texture next to the file, with a mapping node
    im = bpy.data.images.new("brick", 8, 8, alpha=True)
    im.pixels = [c for y in range(8) for x in range(8)
                 for c in ((0.8, 0.2, 0.1, 1) if (x // 2 + y // 2) % 2 else (0.9, 0.9, 0.9, 1))]
    im.filepath_raw = os.path.join(td, "brick.png")
    im.file_format = "PNG"
    im.save()
    im.filepath = "//textures/brick.png"
    bpy.ops.mesh.primitive_cube_add(location=slot(6))
    o = bpy.context.object
    o.name = "Bricks"
    m = bpy.data.materials.new("BrickMat")
    m.use_nodes = True
    nt = m.node_tree
    t = nt.nodes.new("ShaderNodeTexImage")
    t.image = im
    mp = nt.nodes.new("ShaderNodeMapping")
    mp.inputs["Scale"].default_value = (2, 2, 1)
    tc = nt.nodes.new("ShaderNodeTexCoord")
    nt.links.new(tc.outputs["UV"], mp.inputs["Vector"])
    nt.links.new(mp.outputs["Vector"], t.inputs["Vector"])
    nt.links.new(t.outputs["Color"], nt.nodes["Principled BSDF"].inputs["Base Color"])
    o.data.materials.append(m)
    # hidden things - none of these may be imported
    bpy.ops.mesh.primitive_cube_add(location=slot(0, 1))
    bpy.context.object.name = "HiddenRender"
    bpy.context.object.hide_render = True
    ex = bpy.data.collections.new("Excluded")
    bpy.context.scene.collection.children.link(ex)
    hc = bpy.data.collections.new("RenderOff")
    bpy.context.scene.collection.children.link(hc)
    hc.hide_render = True
    for coll, name, i in ((ex, "InExcluded", 1), (hc, "InRenderOff", 2)):
        bpy.ops.mesh.primitive_cube_add(location=slot(i, 1))
        o = bpy.context.object
        o.name = name
        bpy.context.scene.collection.objects.unlink(o)
        coll.objects.link(o)
    bpy.context.view_layer.layer_collection.children["Excluded"].exclude = True
    return {}


HIDDEN = {"HiddenRender", "InExcluded", "InRenderOff"}


def truth(path, tol):
    bpy.ops.wm.open_mainfile(filepath=path)
    dg = bpy.context.evaluated_depsgraph_get()
    objs = []
    for ob in bpy.context.scene.objects:
        if ob.type not in ("MESH", "CURVE", "SURFACE"):
            continue
        ev = ob.evaluated_get(dg)
        me = ev.to_mesh()
        me.calc_loop_triangles()
        # objects Blender doesn't evaluate (excluded collections) keep an
        # identity world matrix: use their own transform for where they are
        W = ob.matrix_basis if ob.name in HIDDEN else ob.matrix_world
        N3 = W.to_3x3().inverted().transposed()
        cn = me.corner_normals
        corners = []
        for li, lp in enumerate(me.loops):
            corners.append([*(W @ me.vertices[lp.vertex_index].co), *(N3 @ cn[li].vector).normalized()])
        step = max(1, len(corners) // 120)
        pts = [W @ v.co for v in me.vertices]
        entry = {"name": ob.name, "tris": len(me.loop_triangles),
                 "bbox": [[min(p[i] for p in pts) for i in range(3)], [max(p[i] for p in pts) for i in range(3)]]
                 if pts else None,
                 "corners": [[round(x, 4) for x in c] for c in corners[::step]],
                 "hidden": ob.name in HIDDEN, "tol": tol.get(ob.name, 0.002)}
        if ob.material_slots and ob.material_slots[0].material:
            entry["material"] = ob.material_slots[0].material.name
        objs.append(entry)
        ev.to_mesh_clear()
    return objs


if __name__ == "__main__":
    T = {}
    for fn in (r1, r2, r3, r4):
        tol = fn()
        p = os.path.join(OUT, "%s_%s.blend" % (fn.__name__, TAG))
        bpy.ops.wm.save_as_mainfile(filepath=p, compress=True)
        T[os.path.basename(p)] = truth(p, tol)
        print("wrote", os.path.basename(p), len(T[os.path.basename(p)]), "objects")
    json.dump(T, open(os.path.join(OUT, "truth_%s.json" % TAG), "w"), separators=(",", ":"))
