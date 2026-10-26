"""Generate hard .blend test files with real Blender (bpy) + ground truth.

Run:  python gen_blend.py <which> <out_dir>
  which = v5   -> files written by the installed bpy (5.x)
  which = v4   -> files written by bpy 4.2
  which = truth <file.blend> <out.json> <ref.glb>  -> dump ground truth
"""
import sys, os, json, math
import bpy, bmesh
from mathutils import Matrix, Vector, Euler, Quaternion

HERE = os.path.dirname(os.path.abspath(__file__))
V = bpy.app.version


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if not bpy.context.scene.world:
        bpy.context.scene.world = bpy.data.worlds.new("World")


def mat(name, color, metallic=0.0, rough=0.5, emission=None, estr=0.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    p = m.node_tree.nodes.get("Principled BSDF")
    p.inputs["Base Color"].default_value = (*color, 1.0)
    p.inputs["Metallic"].default_value = metallic
    p.inputs["Roughness"].default_value = rough
    if emission is not None:
        p.inputs["Emission Color"].default_value = (*emission, 1.0)
        p.inputs["Emission Strength"].default_value = estr
    return m


def link(obj):
    bpy.context.scene.collection.objects.link(obj)
    return obj


def mesh_obj(name, bm_builder):
    me = bpy.data.meshes.new(name + "_mesh")
    bm = bmesh.new()
    bm_builder(bm)
    bm.to_mesh(me)
    bm.free()
    return link(bpy.data.objects.new(name, me))


def save(path, compress=False):
    bpy.ops.wm.save_as_mainfile(filepath=path, compress=compress)
    print("wrote", path, os.path.getsize(path))


# --------------------------------------------------------------- 5.x files
def f_hierarchy(out):
    reset()
    red, green, blue = (mat("Red", (0.9, 0.1, 0.1)), mat("Green", (0.1, 0.8, 0.2), 0.0, 0.3),
                        mat("BlueMetal", (0.1, 0.2, 0.9), 1.0, 0.25))
    base = mesh_obj("Base", lambda bm: bmesh.ops.create_cube(bm, size=2.0))
    for m in (red, green, blue):
        base.data.materials.append(m)
    for i, poly in enumerate(base.data.polygons):
        poly.material_index = i % 3
    base.location = (0, 0, 1)
    base.rotation_euler = (0, 0, math.radians(30))
    # child with non-uniform scale, set AFTER moving so parentinv is non-identity
    arm = mesh_obj("Arm", lambda bm: bmesh.ops.create_cone(bm, cap_ends=True, segments=16, radius1=0.3, radius2=0.3, depth=2.0))
    arm.data.materials.append(green)
    arm.location = (2.5, 0, 2)
    arm.rotation_mode = 'QUATERNION'
    arm.rotation_quaternion = Quaternion((0, 1, 0), math.radians(45))
    arm.scale = (1.0, 1.0, 1.5)
    bpy.context.view_layer.update()
    mw = arm.matrix_world.copy()
    arm.parent = base
    arm.matrix_world = mw            # keep world pose -> Blender writes parentinv
    # grandchild: mirrored (negative scale), smooth shaded uv sphere, axis-angle rotation
    hand = mesh_obj("Hand", lambda bm: bmesh.ops.create_uvsphere(bm, u_segments=24, v_segments=12, radius=0.5))
    hand.data.materials.append(blue)
    for p in hand.data.polygons:
        p.use_smooth = True
    hand.parent = arm
    hand.location = (0, 0, 1.4)
    hand.scale = (-1.0, 1.0, 0.7)
    hand.rotation_mode = 'AXIS_ANGLE'
    hand.rotation_axis_angle = (math.radians(60), 1, 0, 0)
    # n-gon object: 12-sided cap + mixed tris/quads
    def ngon(bm):
        ret = bmesh.ops.create_circle(bm, cap_ends=True, segments=12, radius=1.0)
        bmesh.ops.translate(bm, verts=bm.verts, vec=(0, 0, 0))
    ng = mesh_obj("NGon", ngon)
    ng.data.materials.append(red)
    ng.location = (-3, 0, 0.01)
    ng.rotation_mode = 'ZXY'
    ng.rotation_euler = (math.radians(20), math.radians(10), math.radians(70))
    # empty parent with scale -> child cube
    emp = link(bpy.data.objects.new("Pivot", None))
    emp.location = (0, 4, 0)
    emp.scale = (2, 2, 2)
    small = mesh_obj("SmallCube", lambda bm: bmesh.ops.create_cube(bm, size=0.5))
    small.data.materials.append(red)
    small.parent = emp
    small.location = (0.5, 0, 0.25)
    save(out)


def f_modifiers(out):
    reset()
    m1, m2 = mat("Orange", (1.0, 0.45, 0.05)), mat("Teal", (0.05, 0.6, 0.6))
    # Mirror (X) + Subsurf 2 on a half-shape with clipped centre
    def half(bm):
        bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.translate(bm, verts=bm.verts, vec=(0.5, 0, 0))
    h = mesh_obj("MirrorSubsurf", half)
    h.data.materials.append(m1)
    mm = h.modifiers.new("Mirror", 'MIRROR'); mm.use_axis = (True, False, False); mm.use_mirror_merge = True
    ss = h.modifiers.new("Subsurf", 'SUBSURF'); ss.levels = 2; ss.render_levels = 2
    h.location = (0, 0, 1)
    # Array: 5 copies, relative + constant offset
    a = mesh_obj("Array", lambda bm: bmesh.ops.create_cube(bm, size=0.6))
    a.data.materials.append(m2)
    ar = a.modifiers.new("Array", 'ARRAY'); ar.count = 5
    ar.use_relative_offset = True; ar.relative_offset_displace = (1.5, 0, 0)
    ar.use_constant_offset = True; ar.constant_offset_displace = (0, 0, 0.3)
    a.location = (-4, 3, 0.3)
    a.rotation_euler = (0, 0, math.radians(15))
    # Solidify on a plane
    pl = mesh_obj("Solidify", lambda bm: bmesh.ops.create_grid(bm, x_segments=4, y_segments=4, size=1.5))
    pl.data.materials.append(m1)
    so = pl.modifiers.new("Solidify", 'SOLIDIFY'); so.thickness = 0.25; so.offset = -1.0
    pl.location = (4, -2, 0.5)
    # Triangulate + Mirror Y on a cone
    c = mesh_obj("TriMirror", lambda bm: bmesh.ops.create_cone(bm, cap_ends=True, segments=7, radius1=0.6, radius2=0.0, depth=1.2))
    c.data.materials.append(m2)
    mi = c.modifiers.new("MirrorY", 'MIRROR'); mi.use_axis = (False, True, False); mi.use_mirror_merge = False
    c.location = (0, -4, 0.6)
    tr = c.modifiers.new("Tri", 'TRIANGULATE')
    save(out)


def _img_node(nt, img, loc):
    n = nt.nodes.new("ShaderNodeTexImage"); n.image = img; n.location = loc
    return n


def f_textures(out, tex_dir):
    reset()
    brick = bpy.data.images.load(os.path.join(HERE, "brick_alpha.png"))
    brick.pack()
    nrm = bpy.data.images.load(os.path.join(HERE, "normal_bumps.png"))
    nrm.colorspace_settings.name = "Non-Color"
    nrm.pack()
    # material 1: packed albedo with alpha clip + packed normal map
    m = bpy.data.materials.new("BrickPacked"); m.use_nodes = True
    nt = m.node_tree; p = nt.nodes["Principled BSDF"]
    t = _img_node(nt, brick, (-400, 300))
    nt.links.new(t.outputs["Color"], p.inputs["Base Color"])
    nt.links.new(t.outputs["Alpha"], p.inputs["Alpha"])
    tn = _img_node(nt, nrm, (-600, -200))
    nm = nt.nodes.new("ShaderNodeNormalMap")
    nt.links.new(tn.outputs["Color"], nm.inputs["Color"])
    nt.links.new(nm.outputs["Normal"], p.inputs["Normal"])
    p.inputs["Roughness"].default_value = 0.8
    if hasattr(m, "surface_render_method"):
        m.surface_render_method = 'DITHERED'
    m.use_backface_culling = False
    cube = mesh_obj("BrickCube", lambda bm: bmesh.ops.create_cube(bm, size=2.0))
    cube.data.materials.append(m)
    cube.data.uv_layers.new(name="UVMap")
    # simple box UVs
    uv = cube.data.uv_layers["UVMap"].data
    corners = [(0, 0), (1, 0), (1, 1), (0, 1)]
    for poly in cube.data.polygons:
        for k, li in enumerate(poly.loop_indices):
            uv[li].uv = corners[k % 4]
    cube.location = (0, 0, 1)
    # material 2: external texture with relative path + emission
    ext = bpy.data.images.load(os.path.join(tex_dir, "checker ext.png"))
    m2 = bpy.data.materials.new("CheckerExternalGlow"); m2.use_nodes = True
    nt2 = m2.node_tree; p2 = nt2.nodes["Principled BSDF"]
    t2 = _img_node(nt2, ext, (-400, 300))
    nt2.links.new(t2.outputs["Color"], p2.inputs["Base Color"])
    p2.inputs["Emission Color"].default_value = (1.0, 0.3, 0.0, 1.0)
    p2.inputs["Emission Strength"].default_value = 2.0
    plane = mesh_obj("CheckerPlane", lambda bm: bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=3.0))
    plane.data.materials.append(m2)
    plane.data.uv_layers.new(name="UVMap")
    for poly in plane.data.polygons:
        for k, li in enumerate(poly.loop_indices):
            plane.data.uv_layers["UVMap"].data[li].uv = [(0, 0), (2, 0), (2, 2), (0, 2)][k % 4]
    plane.location = (0, -4, 0)
    # material 3: blended glass
    m3 = mat("BlueGlass", (0.2, 0.5, 1.0), 0.0, 0.05)
    m3.node_tree.nodes["Principled BSDF"].inputs["Alpha"].default_value = 0.35
    if hasattr(m3, "surface_render_method"):
        m3.surface_render_method = 'BLENDED'
    s = mesh_obj("GlassSphere", lambda bm: bmesh.ops.create_uvsphere(bm, u_segments=32, v_segments=16, radius=0.8))
    for p_ in s.data.polygons:
        p_.use_smooth = True
    s.data.materials.append(m3)
    s.location = (3, 0, 0.8)
    save(out)
    bpy.ops.file.make_paths_relative()
    save(out)


def f_colors(out):
    reset()
    # material driven by the colour attribute
    m = bpy.data.materials.new("VertexColored"); m.use_nodes = True
    nt = m.node_tree; p = nt.nodes["Principled BSDF"]
    ca = nt.nodes.new("ShaderNodeVertexColor"); ca.layer_name = "PaintCorner"
    nt.links.new(ca.outputs["Color"], p.inputs["Base Color"])
    grid = mesh_obj("PaintedGrid", lambda bm: bmesh.ops.create_grid(bm, x_segments=8, y_segments=8, size=2.0))
    me = grid.data
    me.materials.append(m)
    # corner-domain BYTE colours (rainbow by position)
    attr = me.color_attributes.new("PaintCorner", 'BYTE_COLOR', 'CORNER')
    for li, loop in enumerate(me.loops):
        co = me.vertices[loop.vertex_index].co
        attr.data[li].color = ((co.x + 2) / 4, (co.y + 2) / 4, 0.2, 1.0)
    me.color_attributes.active_color = attr
    # point-domain FLOAT colours on a second object
    m2 = bpy.data.materials.new("PointColored"); m2.use_nodes = True
    nt2 = m2.node_tree; p2 = nt2.nodes["Principled BSDF"]
    ca2 = nt2.nodes.new("ShaderNodeVertexColor"); ca2.layer_name = "PaintPoint"
    nt2.links.new(ca2.outputs["Color"], p2.inputs["Base Color"])
    sph = mesh_obj("PaintedSphere", lambda bm: bmesh.ops.create_icosphere(bm, subdivisions=3, radius=1.0))
    sph.data.materials.append(m2)
    a2 = sph.data.color_attributes.new("PaintPoint", 'FLOAT_COLOR', 'POINT')
    for i, v in enumerate(sph.data.vertices):
        z = (v.co.z + 1) / 2
        a2.data[i].color = (z, 0.1, 1 - z, 1.0)
    sph.data.color_attributes.active_color = a2
    for p_ in sph.data.polygons:
        p_.use_smooth = True
    sph.location = (3, 0, 1)
    # two UV maps on a cube: UVMap (atlas) + LightUV
    cube = mesh_obj("TwoUVCube", lambda bm: bmesh.ops.create_cube(bm, size=1.5))
    cube.data.materials.append(mat("Plain", (0.8, 0.8, 0.8)))
    u1 = cube.data.uv_layers.new(name="UVMap"); u2 = cube.data.uv_layers.new(name="LightUV")
    for poly in cube.data.polygons:
        for k, li in enumerate(poly.loop_indices):
            u1.data[li].uv = [(0, 0), (1, 0), (1, 1), (0, 1)][k % 4]
            u2.data[li].uv = [(0.5, 0.5), (1, 0.5), (1, 1), (0.5, 1)][k % 4]
    cube.location = (-3, 0, 0.75)
    save(out)


def f_big_compressed(out):
    reset()
    bpy.ops.mesh.primitive_monkey_add(size=2.0, location=(0, 0, 1))
    mk = bpy.context.active_object
    mk.name = "SuzanneHigh"
    sub = mk.modifiers.new("S", 'SUBSURF'); sub.levels = 3
    bpy.ops.object.modifier_apply(modifier="S")
    for p in mk.data.polygons:
        p.use_smooth = True
    # sharp edges on the eye rims: mark every 7th edge sharp to force split normals
    sharp = mk.data.attributes.get("sharp_edge") or mk.data.attributes.new("sharp_edge", 'BOOLEAN', 'EDGE')
    for i in range(0, len(mk.data.edges), 7):
        sharp.data[i].value = True
    mk.data.materials.append(mat("Gold", (1.0, 0.75, 0.2), 1.0, 0.3))
    # flat shaded faceted gem next to it
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1, radius=0.7, location=(2.5, 0, 0.7))
    gem = bpy.context.active_object; gem.name = "FlatGem"
    gem.data.materials.append(mat("Ruby", (0.8, 0.02, 0.1), 0.0, 0.1))
    save(out, compress=True)


# --------------------------------------------------------------- 4.2 file
def f_instances(out):
    reset()
    shared = bpy.data.meshes.new("SharedCrate")
    bm = bmesh.new(); bmesh.ops.create_cube(bm, size=1.0); bm.to_mesh(shared); bm.free()
    shared.materials.append(mat("Wood", (0.55, 0.35, 0.15), 0.0, 0.8))
    for i in range(5):
        o = link(bpy.data.objects.new(f"Crate{i}", shared))
        o.location = (i * 1.3 - 2.6, 0, 0.5 + (i % 2) * 0.5)
        o.rotation_euler = (0, 0, math.radians(i * 18))
        o.scale = (1, 1, 1 + i * 0.25)
    # collection instance: a "Lamp post" collection instanced 3 times
    coll = bpy.data.collections.new("LampPost")
    pole = bpy.data.objects.new("Pole", bpy.data.meshes.new("PoleMesh"))
    bm = bmesh.new(); bmesh.ops.create_cone(bm, cap_ends=True, segments=12, radius1=0.1, radius2=0.1, depth=3.0)
    bmesh.ops.translate(bm, verts=bm.verts, vec=(0, 0, 1.5)); bm.to_mesh(pole.data); bm.free()
    pole.data.materials.append(mat("Iron", (0.2, 0.2, 0.22), 1.0, 0.4))
    coll.objects.link(pole)
    head = bpy.data.objects.new("Head", bpy.data.meshes.new("HeadMesh"))
    bm = bmesh.new(); bmesh.ops.create_uvsphere(bm, u_segments=16, v_segments=8, radius=0.3)
    bmesh.ops.translate(bm, verts=bm.verts, vec=(0, 0, 3.1)); bm.to_mesh(head.data); bm.free()
    head.data.materials.append(mat("Bulb", (1, 1, 0.8), 0.0, 0.2, emission=(1, 0.9, 0.5), estr=5.0))
    coll.objects.link(head)
    for i in range(3):
        e = link(bpy.data.objects.new(f"LampInstance{i}", None))
        e.instance_type = 'COLLECTION'; e.instance_collection = coll
        e.location = (i * 3 - 3, 3, 0); e.rotation_euler = (0, 0, math.radians(30 * i))
    # lights + camera
    for kind, loc in (("POINT", (0, -2, 3)), ("SPOT", (3, -3, 4)), ("SUN", (0, 0, 10))):
        ld = bpy.data.lights.new(kind.lower(), kind); ld.energy = 500 if kind != "SUN" else 3
        lo = link(bpy.data.objects.new(kind.title() + "Light", ld)); lo.location = loc
    cam = link(bpy.data.objects.new("Camera", bpy.data.cameras.new("Cam"))); cam.location = (0, -8, 3)
    save(out)


# --------------------------------------------------------------- truth
def truth(blend, out_json, ref_glb):
    try:
        bpy.ops.wm.open_mainfile(filepath=blend, load_ui=False)
    except Exception as ex:
        print("OPEN FAILED", ex)
        json.dump({"error": str(ex)}, open(out_json, "w"))
        return
    dg = bpy.context.evaluated_depsgraph_get()
    objs, tris = [], 0
    lo = [1e30] * 3; hi = [-1e30] * 3
    lights = 0
    for inst in dg.object_instances:
        ob = inst.object
        if ob.type == 'LIGHT':
            lights += 1
            continue
        if ob.type not in ('MESH', 'CURVE', 'SURFACE', 'META', 'FONT'):
            continue
        try:
            me = ob.to_mesh()
        except Exception:
            continue
        if me is None:
            continue
        me.calc_loop_triangles()
        mw = inst.matrix_world
        n = len(me.loop_triangles)
        tris += n
        olo = [1e30] * 3; ohi = [-1e30] * 3
        for v in me.vertices:
            w = mw @ v.co
            e = (w.x, w.z, -w.y)          # engine Y-up
            for k in range(3):
                olo[k] = min(olo[k], e[k]); ohi[k] = max(ohi[k], e[k])
        if len(me.vertices):
            for k in range(3):
                lo[k] = min(lo[k], olo[k]); hi[k] = max(hi[k], ohi[k])
        objs.append({"name": ob.name, "instance": bool(inst.is_instance), "tris": n,
                     "verts": len(me.vertices), "bbox": [olo, ohi],
                     "materials": [m.name if m else None for m in ob.material_slots and [s.material for s in ob.material_slots] or []],
                     "color_attrs": [a.name for a in getattr(me, "color_attributes", [])],
                     "uv_layers": [u.name for u in me.uv_layers]})
        ob.to_mesh_clear()
    data = {"blender": bpy.app.version_string, "file_version": list(bpy.data.version),
            "tris": tris, "bbox": [lo, hi], "objects": objs, "lights": lights}
    json.dump(data, open(out_json, "w"), indent=1)
    try:
        bpy.ops.export_scene.gltf(filepath=ref_glb, export_format='GLB', export_apply=True,
                                  export_lights=True, use_visible=False)
    except Exception as ex:
        print("GLB export failed", ex)
    print("truth", os.path.basename(blend), tris, "tris")


if __name__ == "__main__":
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    which = argv[0]
    if which == "v5":
        out = argv[1]
        f_hierarchy(os.path.join(out, "b1_hierarchy_multimat_v50.blend"))
        f_modifiers(os.path.join(out, "b2_modifiers_v50.blend"))
        f_textures(os.path.join(out, "b3_textures_packed_v50.blend"), os.path.join(out, "textures"))
        f_colors(os.path.join(out, "b4_color_attributes_uv2_v50.blend"))
        f_big_compressed(os.path.join(out, "b5_big_zstd_compressed_v50.blend"))
    elif which == "v4":
        f_instances(os.path.join(argv[1], "b6_instances_lights_v42.blend"))
    elif which == "truth":
        truth(argv[1], argv[2], argv[3])
