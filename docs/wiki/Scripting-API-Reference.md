# Scripting API Reference

Everything a script or package can use, from `Heaven3D.py` (engine 3.1.4). For a tutorial-style introduction, read the [[Scripting Guide]] first.

```python
import glm                                   # PyGLM: vec3, mat4, quat, distance, normalize, ...
from Heaven3D import (HeavenEngine, SceneNode, Blueprint, blueprint,
                      Mesh, Material, FPSController, Weapon, Health)
```

Conventions: 1 unit = 1 metre · Y up · nodes face −Z · Euler angles in degrees, applied Y → X → Z · colours 0–1.

---

## Blueprint

Base class for behaviours. Register subclasses with `@blueprint`.

| Member | |
| --- | --- |
| `node` | the `SceneNode` it's attached to |
| `engine` | the `HeavenEngine` |
| `properties` | `dict` saved with the level, editable in the Inspector |
| `log(msg)` | log with the class name |
| `on_begin_play()` | once when play starts |
| `on_tick(dt)` | every frame while playing |
| `on_end_play()` | when play stops |
| `on_message(event, data)` | engine messages |
| `on_collision_enter(other, contact)` | `contact.point`, `.normal` (from `other` toward `self.node`), `.depth`, `.impulse` (also `contact["point"]` ...) |
| `on_collision_stay(other, contact)` | every physics step while touching |
| `on_collision_exit(other)` | |
| `on_trigger_enter(other)` / `on_trigger_exit(other)` | for trigger colliders |
| `to_dict()` / `Blueprint.from_dict(d)` | serialisation (`{"class": ..., "properties": ...}`) |

`BlueprintRegistry.get(name)` and `BlueprintRegistry.all_names()` look up registered classes.

---

## SceneNode

A transform in the scene graph.

```python
node = SceneNode("Crate", parent=engine.scene.root)
```

| Attribute | Type | |
| --- | --- | --- |
| `name`, `id`, `tag` | str, int, str | |
| `position`, `rotation_euler`, `scale` | `glm.vec3` | local transform (rotation in degrees) |
| `parent`, `children` | node, list | |
| `mesh`, `material` | `Mesh`, `Material` | optional |
| `blueprints` / `blueprint` | list / first | attached behaviours |
| `collider`, `rigidbody` | | physics components (see [[Physics]]) |
| `visible` | bool | |
| `source_path` | str | model file it was loaded from |
| `mesh_type`, `mesh_params` | `"cube"`/`"plane"`/`"sphere"`, dict | primitive rebuild info |
| `user_data` | dict | free storage |
| `spawn_kind`, `spawn_config` | | Library spawn markers (`"player"`, `"enemy"`, `"bullet_pickup"`, `"health_pickup"`) |
| `is_sun` / `is_point_light` / `is_spot_light` / `is_clouds` / `is_ocean` / `is_terrain` | bool | special node kinds, with `sun_light`, `point_light`, `spot_light`, `cloud_settings`, `ocean_settings` |

| Method | |
| --- | --- |
| `add_child(child)` | reparent `child` under this node |
| `remove()` | detach from its parent |
| `local_matrix()` / `world_matrix()` | `glm.mat4` |
| `world_position()` | `glm.vec3` |
| `traverse()` | this node and all descendants |

## Scene

`engine.scene` — `root` (the root node), `find_by_name(name)`, `sun`, `clouds`, `ocean`, `traverse_meshes()`, `traverse_point_lights()`, `traverse_spot_lights()`.

---

## HeavenEngine

```python
engine = HeavenEngine(mode="editor" | "runtime", width=1690, height=950, project_path=None)
engine.run()
```

### Main attributes

| | |
| --- | --- |
| `scene` | the `Scene` |
| `camera` | `Camera`: `position`, `yaw`, `pitch`, `fov`, `near`, `far`, `forward()`, `right()`, `view()`, `projection()`, `frame(target, distance)` |
| `camera_locked` | when `True`, scripts own the camera |
| `physics` | `PhysicsWorld`: `gravity`, `raycast`, `overlap_sphere`, `add_node`, `remove_node`, `step` |
| `audio` | `AudioSystem` (below) |
| `hud` | `HUDRenderer` (below) |
| `input` | `InputState` (below) |
| `time` | seconds of gameplay elapsed |
| `mode` | `"editor"` or `"runtime"` |
| `project_path` | path of the open `.heaven3d` |
| `selected` | selected node (editor) |
| `ctx` | the ModernGL context |
| `saves` | the save-game store |

### Creating objects

| Method | Returns | |
| --- | --- | --- |
| `add_cube(size=1.0, name="Cube", solid=True)` | node | with a box collider when `solid` |
| `add_plane(size=10.0, name="Plane", solid=True)` | node | with a plane collider |
| `add_sphere(radius=1.0, name="Sphere", solid=True)` | node | with a sphere collider |
| `add_point_light()` / `add_spot_light()` | node | |
| `add_player_spawn()` | node | only one per scene |
| `add_enemy_spawn()`, `add_bullet_pickup()`, `add_health_pickup()`, `add_trigger_point()` | node | Create-menu objects |
| `find_player_spawn()` | node or `None` | |
| `load_model(path)` | node | `.glb` `.gltf` `.blend` `.obj` `.fbx` `.x` |
| `add_building(style="town", position=(0, 0, 0), house=None, name=None, seed=None)` | node | a painted building; `house=(width, depth, floors[, damage])` generates one |
| `building_edit(node, cells=None, erase=None, edges=None, stairs=None, open_cells=None, finish=None)` | node | change a building and re-bake what changed |
| `add_town(blocks_x=3, blocks_z=3, position=(0, 0, 0), seed=None, damage=0.1, variety=36)` | node | a "Town" group: blocks of houses on a grid of streets |
| `set_building_detail(near=None, mid=None, far=None, district=None, preset=None)` | dict | level-of-detail distances of buildings; presets `low` `medium` `high` `ultra` |
| `building_stats()` | dict | districts, triangles, draw calls and video memory of the last frame |
| `load_model_into(node, path)` | bool | replace a node's model |
| `create_terrain(size=200, resolution=128, height_scale=10, seed=42)` | node | |
| `instantiate(template, position=None, rotation=None, make_visible=True, name=None)` | node | clone a subtree (prefab) |
| `destroy_node(node)` | | deferred until after ticks |

### Blueprints and messages

| Method | |
| --- | --- |
| `attach_blueprint(node, cls_or_instance_or_dict, properties=None)` | attach and return the blueprint (starts it if playing) |
| `find_blueprints("ClassName")` | all instances |
| `first_blueprint("ClassName")` | first instance or `None` |
| `find_nodes_with_blueprint("ClassName")` | nodes |
| `find_node(name)` | node by name |
| `send_message(event, data=None)` | broadcast to every blueprint |
| `message_node(node, event, data=None)` | blueprints on one node |
| `message_subtree(node, event, data=None)` | a node and its descendants |
| `message_ancestors(node, event, data=None)` | a node and every ancestor |
| `schedule(delay, fn, *args, **kwargs)` | call once after `delay` seconds of gameplay |

### Physics helpers

| Method | |
| --- | --- |
| `raycast(origin, direction, max_dist=1000, ignore=None, hit_triggers=False)` | `RayHit` (`node`, `collider`, `point`, `normal`, `distance`) or `None` |
| `add_box_collider(node, size=None, **kw)` | fits a cube's size by default |
| `add_sphere_collider(node, radius=None, **kw)` | |
| `add_capsule_collider(node, radius=0.4, height=1.8, **kw)` | |
| `add_plane_collider(node, size=0.0, **kw)` | |
| `add_mesh_collider(node, **kw)` | exact triangles (static / kinematic) |
| `add_rigidbody(node, mass=1, body_type="dynamic", friction=None, restitution=None, linear_damping=0.02, angular_damping=0.05, gravity_scale=1, freeze_rotation=False)` | |
| `make_character(node, radius=0.35, height=1.8, **kw)` | `CharacterController` with `move(displacement)`, `position()`, `feet_position()` |

`**kw` for colliders: `center`, `is_trigger`, `friction`, `restitution`.

### Effects

| Method | |
| --- | --- |
| `spawn_flash(position, color=(1, 0.8, 0.4), intensity=30, range=9, ttl=0.06)` | short-lived point light |
| `spawn_impact_fx(hit, sound="bullet_impact")` | bullet hole, spark and flash at a `RayHit` |
| `show_end_screen(text, color, font_size)` | centred end-of-level message |

### Play control and projects

| Method | |
| --- | --- |
| `toggle_play()` | start / stop Play (editor) |
| `request_stop_play()` | leave Play at the end of the frame |
| `save_project(path=None)` / `load_project(path=None)` | |
| `select_node(node)` / `deselect()` | editor selection |

### Save games and assets

| Method | |
| --- | --- |
| `save_game(slot="save", data=None)` | atomic JSON save with backup |
| `load_game(slot="save", default=None)` | |
| `asset_path(ref)` | resolve a project-relative path wherever the project is |

See [[Portable Projects and Save Games]].

---

## InputState — `engine.input`

| | |
| --- | --- |
| `key("w")` | key held (lower-case names) |
| `was_key_pressed("r")` | true only on the frame the key went down |
| `mouse_pos`, `mouse_delta`, `wheel` | |
| `lmb`, `rmb`, `mmb` | buttons held |
| `lmb_just_pressed`, `rmb_just_pressed` | this frame only |
| `shift`, `ctrl`, `alt` | modifiers |

## AudioSystem — `engine.audio`

| | |
| --- | --- |
| `play(name, volume=1.0, loop=False)` | 2D sound: a built-in name, a loaded name or a file path (auto-loaded) |
| `play_at(name, position, volume=1.0, ref_distance=None, max_distance=None)` | 3D positional sound |
| `play_music(path, loop=True, volume=0.6)` / `stop_music()` | |
| `load_sound(path, name=None)` / `has_sound(name)` | |
| `set_master_volume(v)` / `stop_all()` | |
| `set_listener(position, forward=None, right=None)` | normally done for you by the camera |

Built-in synthesised sounds: `shoot`, `shotgun`, `hit`, `explosion`, `footstep`, `jump`, `land`, `pickup`, `reload`, `hurt`, `enemy`, `click`, `heal`. Audio is silent-safe: on machines without an audio device, calls do nothing. See [[Audio and HUD]].

## HUDRenderer — `engine.hud`

Coordinates are **normalised**, `(0, 0)` = top-left, `(1, 1)` = bottom-right. Call every frame.

| | |
| --- | --- |
| `text(x, y, s, size=20, color=(1,1,1,1), shadow=True)` | |
| `center_text(y, s, size=28, color=(1,1,1,1))` | horizontally centred |
| `rect(x, y, w, h, color=(1,1,1,1))` | filled rectangle |
| `bar(x, y, w, h, frac, fg=..., bg=..., border=...)` | progress bar (health, reload…) |
| `crosshair(color=..., size=0.010, gap=0.004, thickness=0.0022, spread=0.0, dot=True)` | |
| `damage_vignette(intensity=0.5)` | red screen edges |
| `render()` | draw now (packages drawing in the `render` event) |

---

## Mesh

```python
mesh = Mesh.from_arrays(engine.ctx, positions, normals=None, uvs=None, indices=None, name="mesh")
node.mesh = mesh
```

Arrays are NumPy (`float32` positions `N×3`, normals `N×3`, uvs `N×2`, `uint32` indices). Tangents are computed for you. Primitives: `Mesh.make_cube(ctx, size)`, `make_plane(ctx, size, segments)`, `make_sphere(ctx, radius)`, `make_capsule(ctx, radius, height)`.

## Material

```python
node.material = Material(name="Gold", base_color=(1, 0.8, 0.1, 1), metallic=1.0, roughness=0.2,
                         emissive=(0, 0, 0), emissive_intensity=1.0)
```

All fields are on [Rendering](Rendering#materials-pbr).

---

## Messages

Events the built-in blueprints send or understand:

| Event | Data | Sent by | Handled by |
| --- | --- | --- | --- |
| `damage` | `{"amount", "point", "direction", "source"}` | Weapon, Projectile, EnemyAI, EnemyFlyShooter, packages | Health, EnemyAI, EnemyFlyShooter, FPSController, `MHEnemy` |
| `heal` | `{"amount"}` | Health Pickup | FPSController, Health |
| `add_ammo` | `{"amount"}` | Bullet Pickup, ammo pickups | Weapon, HQ FPS weapons |
| `died` | `{"node"}` | Health, EnemyAI, EnemyFlyShooter, `MHEnemy` | your scripts (scores, doors, waves) |
| `player_died` | `{"node"}` | FPSController | your scripts |
| `<event_name>_enter` / `_exit` | `{"node", "other"}` | TriggerZone | your scripts |
| `<event_name>` | | Pickup with `kind = "event"` | your scripts |

---

## Environment variables

| Variable | Effect |
| --- | --- |
| `HEAVEN3D_SAFE_MODE=1` | start without loading packages |
| `HEAVEN3D_NO_PERF=1` | disable the 2.7.3 rendering optimisations (for comparison) |
| `HEAVEN3D_NO_WARMUP=1` | skip the background Numba warm-up |
| `HEAVEN3D_NO_PHYS_KERNELS=1` | use the Python contact and solver code instead of the compiled kernels |
| `HEAVEN3D_NO_CITY=1` | draw every painted building part by part instead of in districts |
| `HEAVEN3D_CLASSIC_UI=1` | same as `--classic-ui`: the editor interface from before 3.1.3 |
| `HEAVEN3D_PIP_LITE=1` | (while running `pip install`) leave the asset packages out |
| `HEAVEN3D_MAC_COMPAT=1` | use the macOS code paths on another OS (testing) |
