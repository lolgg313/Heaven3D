"""
Portable projects (no window / GPU needed):

    python tests/test_portable_projects.py

Imports assets from a fake "Downloads" folder, makes the project portable
(what every Save does), moves the project somewhere else, deletes the
originals and checks every reference still resolves.  Also tests game
save data.  Uses the H3DPortable module built into Heaven3D.py.
"""
import os, sys, json, shutil, tempfile, importlib.util, contextlib, io
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
A = os.path.join(HERE, 'importers', 'assets')
spec = importlib.util.spec_from_file_location("Heaven3D", os.path.join(ROOT, "Heaven3D.py"))
H = importlib.util.module_from_spec(spec); sys.modules["Heaven3D"] = H
with contextlib.redirect_stdout(io.StringIO()):
    spec.loader.exec_module(H)
P = H.H3DPortable
root = tempfile.mkdtemp(prefix='h3dport_')
dl = os.path.join(root, 'home', 'Downloads'); os.makedirs(dl)
# outside assets
shutil.copytree(os.path.join(A, 'gltf', 'Box With Spaces'), os.path.join(dl, 'Box With Spaces'))
shutil.copy(os.path.join(A, 'glb', 'Fox.glb'), os.path.join(dl, 'Fox.glb'))
os.makedirs(os.path.join(dl, 'blendstuff'))
shutil.copy(os.path.join(A, 'blend', 'b3_textures_packed_v50.blend'), os.path.join(dl, 'blendstuff', 'b3.blend'))
shutil.copytree(os.path.join(A, 'blend', 'textures'), os.path.join(dl, 'blendstuff', 'textures'))
os.makedirs(os.path.join(dl, 'objpack', 'tex'))
open(os.path.join(dl, 'objpack', 'crate.obj'), 'w').write('mtllib crate.mtl\nv 0 0 0\nv 1 0 0\nv 0 1 0\nusemtl m\nf 1 2 3\n')
open(os.path.join(dl, 'objpack', 'crate.mtl'), 'w').write('newmtl m\nmap_Kd tex/wood.png\n')
shutil.copy(os.path.join(A, 'blend', 'textures', os.listdir(os.path.join(A, 'blend', 'textures'))[0]), os.path.join(dl, 'objpack', 'tex', 'wood.png'))
open(os.path.join(dl, 'boom.wav'), 'wb').write(b'RIFF0000WAVE')
open(os.path.join(dl, 'Fox.glb.dup'), 'w').write('x')
# second, different file with the same name -> must get _1
os.makedirs(os.path.join(dl, 'other')); open(os.path.join(dl, 'other', 'boom.wav'), 'wb').write(b'RIFF1111WAVE')
proj = os.path.join(root, 'home', 'Projects', 'My Game'); os.makedirs(os.path.join(proj, 'assets', 'Models'))
shutil.copy(os.path.join(A, 'glb', 'Fox.glb'), os.path.join(proj, 'assets', 'Models', 'Hero.glb'))
data = {"type": "heaven3d_project", "scene": {"children": [
    {"name": "box", "source_path": os.path.join(dl, 'Box With Spaces', 'Box With Spaces.gltf')},
    {"name": "fox", "source_path": os.path.join(dl, 'Fox.glb')},
    {"name": "fox2", "source_path": os.path.join(dl, 'Fox.glb')},
    {"name": "b3", "source_path": os.path.join(dl, 'blendstuff', 'b3.blend')},
    {"name": "crate", "source_path": os.path.join(dl, 'objpack', 'crate.obj')},
    {"name": "hero", "source_path": 'assets\\Models\\Hero.glb'},
    {"name": "emb", "material": {"albedo": "__embedded__"}},
    {"name": "snd", "blueprints": [{"params": {"sound_path": os.path.join(dl, 'boom.wav')}},
                                    {"params": {"sound_path": os.path.join(dl, 'other', 'boom.wav')}}]},
    {"name": "gone", "source_path": "C:\\Users\\bob\\Desktop\\missing.glb"},
    {"name": "text", "tag": "not a path"}]}}
logs = []
rep = P.make_portable(data, proj, logs.append)
print(json.dumps([c.get('source_path') or c.get('blueprints') for c in data['scene']['children']], indent=0))
print('report', rep)
for dp, dn, fn in os.walk(os.path.join(proj, 'assets')):
    for f in fn: print('  ', os.path.relpath(os.path.join(dp, f), proj))
print(open(os.path.join(proj, 'assets/imported/Box With Spaces/Box With Spaces.gltf')).read().count('uri'))
# move the project somewhere else (other OS / other user) + delete Downloads
moved = os.path.join(root, 'elsewhere', 'Copy of My Game'); shutil.copytree(proj, moved); shutil.rmtree(os.path.join(root, 'home'))
# simulate a case-insensitive Windows reference
data['scene']['children'][5]['source_path'] = 'ASSETS/models/hero.GLB'
d2 = json.loads(json.dumps(data))
P.resolve_project_data(d2, moved, logs.append)
ok = 0
for c in d2['scene']['children']:
    for v in [c.get('source_path')] + [b['params']['sound_path'] for b in c.get('blueprints', [])]:
        if v and P.is_media_ref(v):
            exists = os.path.isfile(v); ok += exists
            print('RESOLVED' if exists else 'MISSING ', v.replace(root, '<root>'))
# save data
sd = P.SaveData("My Game: Deluxe?", portable_root=moved)
sd.save("slot 1", {"coins": 7}); sd.save("slot 1", {"coins": 9})
ok_save = sd.load("slot 1") == {"coins": 9}
open(sd.path("slot 1"), 'w').write('{broken')
ok_bak = sd.load("slot 1") == {"coins": 7}
print('save data round trip:', ok_save, ' damaged save recovered from backup:', ok_bak)
expected = 8
print(f"\n{ok}/{expected} references resolved after the move (1 is deliberately missing)")
shutil.rmtree(root, ignore_errors=True)
sys.exit(0 if (ok == expected and ok_save and ok_bak) else 1)
