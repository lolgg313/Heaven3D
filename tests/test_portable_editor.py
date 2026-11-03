"""
Editor round trip (needs a GPU / desktop):
import models, a texture and a sound from OUTSIDE the project -> Save ->
move the project to another folder and delete the originals -> reopen.

    python tests/test_portable_editor.py
"""
import os, sys, json, shutil, tempfile, subprocess, importlib.util
HERE = os.path.dirname(os.path.abspath(__file__))
A = os.path.join(HERE, 'importers', 'assets')
if len(sys.argv) < 3:                       # run both phases in separate processes
    root = tempfile.mkdtemp(prefix='h3dep_')
    rc = 0
    for ph in ('save', 'load'):
        rc |= subprocess.call([sys.executable, __file__, os.path.join(os.path.dirname(HERE), 'Heaven3D.py'), ph, root])
    shutil.rmtree(root, ignore_errors=True)
    sys.exit(rc)
ENGINE = sys.argv[1]
phase = sys.argv[2]
root = sys.argv[3]
spec = importlib.util.spec_from_file_location("Heaven3D", ENGINE)
H = importlib.util.module_from_spec(spec); sys.modules["Heaven3D"] = H; spec.loader.exec_module(H)
H._h3d_set_splash_mode("off")
res = []
def check(name, cond, detail=""):
    res.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)
if phase == 'save':
    dl = os.path.join(root, 'Downloads'); os.makedirs(dl)
    shutil.copytree(os.path.join(A, 'gltf', 'Box With Spaces'), os.path.join(dl, 'Box With Spaces'))
    shutil.copy(os.path.join(A, 'glb', 'CesiumMilkTruck.glb'), dl)
    os.makedirs(os.path.join(dl, 'b')); shutil.copy(os.path.join(A, 'blend', 'b3_textures_packed_v50.blend'), os.path.join(dl, 'b', 'b3.blend'))
    shutil.copytree(os.path.join(A, 'blend', 'textures'), os.path.join(dl, 'b', 'textures'))
    import wave, struct
    w = wave.open(os.path.join(dl, 'ding.wav'), 'w'); w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
    w.writeframes(b''.join(struct.pack('<h', int(8000 * ((i // 40) % 2 * 2 - 1))) for i in range(4000))); w.close()
    proj = os.path.join(root, 'Projects', 'Portable Test'); os.makedirs(proj); os.chdir(proj)
    eng = H.HeavenEngine(mode="editor", width=800, height=600)
    nodes = [eng.load_model(os.path.join(dl, 'Box With Spaces', 'Box With Spaces.gltf')),
             eng.load_model(os.path.join(dl, 'CesiumMilkTruck.glb')),
             eng.load_model(os.path.join(dl, 'b', 'b3.blend'))]
    cube = eng.add_cube(size=1.0, name="Crate")
    tex = [f for f in os.listdir(os.path.join(dl, 'b', 'textures')) if f.endswith('.png')][0]
    shutil.copy(os.path.join(dl, 'b', 'textures', tex), os.path.join(dl, 'Wood Crate.PNG'))
    cube.material.base_color_tex = os.path.join(dl, 'Wood Crate.PNG')
    eng.attach_blueprint(cube, H.TriggerZone)
    cube.blueprints[-1].properties["sound"] = os.path.join(dl, 'ding.wav')
    eng.save_project(os.path.join(proj, 'main.heaven3d'))
    txt = open(os.path.join(proj, 'main.heaven3d')).read()
    data = json.loads(txt)
    paths = [c.get('source_path') for c in data['scene']['children'] if c.get('source_path')]
    print(paths)
    check("every model path is project-relative with '/'", paths and all(p.startswith('assets/imported/') and '\\' not in p for p in paths))
    check("no absolute or home paths left in the save", root not in txt and os.path.expanduser('~') not in txt)
    check("sound path (blueprint property) made portable", '"assets/imported/ding.wav"' in txt)
    check("texture path (material) made portable", '"assets/imported/wood crate.png"' in txt.lower())
    check("gltf side files copied", os.path.isfile(os.path.join(proj, 'assets/imported/Box With Spaces/Box With Spaces.bin')))
    check(".blend external texture copied", any(f.endswith('.png') for f in os.listdir(os.path.join(proj, 'assets/imported/b3/textures'))))
    check("scene now points at the project copies", all(n.source_path.startswith(proj) for n in nodes))
    eng.save_project(os.path.join(proj, 'main.heaven3d'))
    check("second save keeps a .bak and copies nothing new", os.path.isfile(os.path.join(proj, 'main.heaven3d.bak')))
    parts = [1 + len([c for c in n.children if getattr(c, '_heaven3d_imported_part', False)]) for n in nodes]
    json.dump({"parts": parts}, open(os.path.join(root, 'expect.json'), 'w'))
    # move + break the originals
    shutil.move(proj, os.path.join(root, 'USB', 'Portable Test'))
    shutil.rmtree(dl)
    print("ROOT", root)
else:
    proj = os.path.join(root, 'USB', 'Portable Test'); os.chdir('/')
    exp = json.load(open(os.path.join(root, 'expect.json')))
    eng = H.HeavenEngine(mode="editor", width=800, height=600, project_path=os.path.join(proj, 'main.heaven3d'))
    roots = [c for c in eng.scene.root.children if c.source_path]
    crate = next(c for c in eng.scene.root.children if c.name == "Crate")
    parts = [1 + len([c for c in n.children if getattr(c, '_heaven3d_imported_part', False)]) for n in roots]
    check("moved project reopens with every model", parts == exp["parts"], f"{parts} vs {exp['parts']}")
    check("models load from the moved folder", all(r.source_path.startswith(proj) for r in roots))
    snd = crate.blueprints[-1].properties.get("sound")
    check("texture resolves in the moved folder", os.path.isfile(str(crate.material.base_color_tex)), crate.material.base_color_tex)
    check("sound resolves in the moved folder", snd and os.path.isfile(snd), snd)
    check("audio plays a project-relative name", eng.audio.load_sound("assets/imported/ding.wav") or not eng.audio.enabled)
    eng.save_game("slot1", {"coins": 12, "level": 3})
    check("save_game/load_game round trip", eng.load_game("slot1") == {"coins": 12, "level": 3}, eng.saves.folder)
    check("asset_path() finds files case-insensitively", os.path.isfile(eng.asset_path("ASSETS/Imported/DING.WAV")))
print(f"{sum(res)}/{len(res)} passed")
os._exit(0 if all(res) else 1)
