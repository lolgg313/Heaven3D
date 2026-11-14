# Project Format

A Heaven3D project is a folder. The scene is a JSON file, `main.heaven3d`, that you can read, diff and generate.

## Project folder

```
My Game/
├── main.heaven3d          the scene
├── main.heaven3d.bak      previous save (automatic)
├── project.json           name and template (used by the Hub)
├── .heaven3d_thumb.png    Hub thumbnail
├── assets/                models, textures, sounds
│   └── imported/          copies of files imported from outside the project
├── scripts/               *.py blueprints, loaded automatically
├── packages/              project packages (shipped with exported games)
├── animations/            Mixamo / glTF / BVH clips (MakeHuman Characters)
├── saves/                 save games while playing from the editor
├── .island_cache/         Tropical Islands build cache
└── .heaven3d_logs/        logs of sessions started from the Hub
```

Only `main.heaven3d` is required.

## `main.heaven3d`

```json
{
  "version": "2.7.0",
  "type": "heaven3d_project",
  "scene": { ...root node... },
  "camera": {"position": [22.0, 14.0, -26.0], "yaw": 141.8, "pitch": -21.5},
  "settings": {"shadows": true, "ssao": true, "bloom": true, "fxaa": true, "msaa": true, "msaa_samples": 4},
  "saved_at": "2026-10-26 02:39:50"
}
```

| Key | |
| --- | --- |
| `version` | engine version that wrote the file |
| `type` | always `"heaven3d_project"` |
| `scene` | the root node (`"__root__"`) with every object as nested `children` |
| `camera` | the editor camera when saved |
| `settings` | renderer switches |
| `saved_at` | timestamp |

## Nodes

Every node has the same keys; unused ones are `null` / `false`:

```json
{
  "name": "Ground",
  "position": [0.0, -0.5, 0.0],
  "rotation_euler": [0.0, 0.0, 0.0],
  "scale": [60.0, 1.0, 60.0],
  "source_path": null,
  "mesh_type": "cube",
  "mesh_params": {"size": 1.0},
  "material": { ... },
  "blueprints": [{"class": "Coin", "properties": {}}],
  "visible": true,
  "tag": "",
  "collider": {"type": "box", "size": [1.0, 1.0, 1.0], "center": [0,0,0], "is_trigger": false, "friction": 0.6, "restitution": 0.0},
  "rigidbody": null,
  "spawn_kind": null,
  "spawn_config": null,
  "is_sun": false,        "sun_light": null,
  "is_point_light": false, "point_light": null,
  "is_spot_light": false,  "spot_light": null,
  "is_clouds": false,      "cloud_settings": null,
  "is_ocean": false,       "ocean_settings": null,
  "is_terrain": false,     "terrain_config": null,
  "children": []
}
```

| Key | Meaning |
| --- | --- |
| `position`, `rotation_euler`, `scale` | local transform (degrees, Y→X→Z) |
| `source_path` | model file, relative to the project (`assets/...`) |
| `mesh_type`, `mesh_params` | `"cube"` / `"plane"` / `"sphere"` (/ `"capsule"`) primitives, rebuilt on load |
| `material` | PBR material (see below) |
| `blueprints` | attached behaviours: class name + properties |
| `collider` | `type` = `box` / `sphere` / `capsule` / `plane` / `mesh` / … with its size fields |
| `rigidbody` | body type, mass, damping, gravity scale… |
| `spawn_kind`, `spawn_config` | Create-menu spawns, e.g. `"player"` with `{"health", "bullets", "walk_speed", "run_speed", "cam_rot_speed"}` |
| `sun_light`, `point_light`, `spot_light` | light settings |
| `cloud_settings`, `ocean_settings` | see [[Rendering]] |
| `terrain_config` | size, resolution, height scale, seed (+ sculpt data) |

### Material

```json
{
  "name": "Ground_mat",
  "base_color": [0.33, 0.4, 0.31, 1.0],
  "metallic": 0.0, "roughness": 0.95,
  "emissive": [0, 0, 0], "emissive_intensity": 1.0,
  "base_color_tex": null, "metallic_roughness_tex": null, "normal_tex": null,
  "emissive_tex": null, "occlusion_tex": null,
  "alpha_mode": "OPAQUE", "alpha_cutoff": 0.5, "double_sided": false
}
```

Textures embedded in a GLB are saved as `"__embedded__"` and re-read from the source model on load.

### Imported models

An imported model is saved as **one node** with `source_path`; its parts are rebuilt from the file on load, so the JSON stays small and stays in sync with the model.

### Package data

Packages store their objects as ordinary nodes with their own blueprints and properties. For example a MakeHuman character is an `MHCharacter` blueprint whose `character` property is a JSON description (morphs, clothes, hair, skin); an island is a `TropicalIsland` blueprint with a handful of generation numbers. Meshes are regenerated when the project opens.

## `project.json`

Written by the Hub when the project is created:

```json
{
  "name": "My Game",
  "template": "fps_arena",
  "created": "2026-11-06T11:54:00",
  "engine_version": "2.7.3",
  "hub_version": "1.4.0"
}
```

The game name used for save folders comes from `name` (falling back to the folder name).

## Generating scenes from code

```python
eng = HeavenEngine(mode="editor", project_path=None)
eng.add_cube(name="Floor").scale = glm.vec3(40, 1, 40)
eng.save_project("My Game/main.heaven3d")
```

Paths are made portable automatically on save; see [[Portable Projects and Save Games]].
