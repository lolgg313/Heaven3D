import sys, os, json, bpy
sys.path.insert(0, os.path.dirname(__file__))
from gen_blend import truth
argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
src, out_json = argv[0], argv[1]
bpy.ops.wm.read_factory_settings(use_empty=True)
try:
    bpy.ops.import_scene.gltf(filepath=src)
except Exception as ex:
    json.dump({"error": str(ex)}, open(out_json, "w")); sys.exit(0)
tmp = out_json.replace(".json", ".blend")
bpy.ops.wm.save_as_mainfile(filepath=tmp)
truth(tmp, out_json, out_json.replace(".json", "_unused.glb"))
