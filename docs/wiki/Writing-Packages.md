# Writing Packages

A package is a folder with a `package.json` manifest and one or two Python entry files. This page is the full reference; [[Packages]] covers installing and using them.

## Quick start

1. Hub → **Packages** → **+ Create package**. Give it a name and choose where it runs (Hub, Editor, Games).
2. A working example (a blueprint, an editor tool and a Hub page) is generated in `~/.heaven3d/packages/<id>` and switched on.
3. Edit the files, then reopen the editor (or **Restart now** for Hub packages).
4. Share it: **⋯ > Export as .h3dpkg…** on its card. Others install it with **Install from file**.

## Folder layout

```
my_package/
├── package.json     the manifest (required)
├── engine.py        code for the editor and for games   (optional)
├── hub.py           code for the Hub                     (optional)
├── templates/       project templates the Hub will offer (optional)
│   └── my_template/ main.heaven3d + template.json + .heaven3d_thumb.png
├── icon.png         shown on the Packages page           (optional)
└── anything else    models, textures, more .py files…
```

A `.h3dpkg` is simply a zip with `package.json` at the top.

## package.json

```json
{
  "id": "car_race_kit",
  "name": "Car Race Kit",
  "version": "1.0.0",
  "author": "You",
  "category": "Gameplay",
  "description": "What it does, in one or two sentences.",
  "heaven3d": ">=2.7.0",
  "targets": ["editor", "game"],
  "engine": "engine.py",
  "hub": "hub.py",
  "requires": ["spline_roads"],
  "default_settings": {"quality": "High"}
}
```

| Key | Meaning |
| --- | --- |
| `id` | lowercase letters, digits and `_`, starting with a letter (it names the folder) |
| `targets` | where it runs: `"hub"`, `"editor"`, `"game"` (any combination) |
| `engine` | entry file for the editor and games; must define `register(h3d)` |
| `hub` | entry file for the Hub; must define `register(hub)` |
| `requires` | other packages to load first (installed and copied automatically) |
| `heaven3d` | minimum engine version, e.g. `">=2.7.0"` |
| `default_settings` | starting values for `h3d.settings` / `hub.settings` |
| `category` | `Theme`, `Physics`, `Terrain`, `Gameplay`, `Tools`, `Audio`, `Templates`… (sets the tile colour) |

---

## Engine API (`engine.py`)

```python
def register(h3d):
    m = h3d.module                      # the Heaven3D module: Blueprint, SceneNode, Mesh, Material, glm, THEME, ...

    @h3d.blueprint                      # a blueprint you can attach to objects; works in games too
    class Spinner(m.Blueprint):
        def __init__(self):
            super().__init__()
            self.properties.setdefault("speed", 90.0)    # shown and editable in the Inspector
        def on_tick(self, dt):
            self.node.rotation_euler.y += float(self.properties["speed"]) * dt

    def add_cube(engine):               # an editor tool (Packages menu in the menu bar)
        cube = engine.add_cube(name="Spinning Cube")
        engine.attach_blueprint(cube, Spinner)
    tool = h3d.add_tool("My tools: add spinning cube", add_cube)
    tool["label"] = "My tools: add spinner"          # tools can be renamed any time

    @h3d.on("play_tick")
    def tick(engine, dt):
        engine.hud.text(0.02, 0.95, "Hello from a package", size=18)
```

### The `h3d` object

| Member | |
| --- | --- |
| `h3d.engine` | the running `HeavenEngine` (set after the `init` event) |
| `h3d.module` | the Heaven3D module (all engine classes and helpers) |
| `h3d.mode` | `"editor"` or `"runtime"` (a game) |
| `h3d.path`, `h3d.asset("tex.png")` | this package's folder / a file inside it |
| `h3d.settings`, `h3d.save_settings()` | a dict saved for you (project packages save into the project, so settings ship with the game) |
| `h3d.on(event, fn)` | subscribe to an event (also usable as a decorator) |
| `h3d.add_tool(label, fn, color=None)` | add a button to the editor's **Packages** menu; `fn(engine)`; returns a dict whose `"label"` you can change |
| `h3d.blueprint` | decorator that registers a Blueprint class |
| `h3d.wrap(owner, "name", wrapper)` | safely replace a method: `wrapper(original, *args, **kw)`; if your wrapper raises, the original runs |
| `h3d.theme(text="#1F2328", panel="#F8F9FB", ...)` | change editor colours (`THEME` keys) |
| `h3d.load("helper.py")` | import another file of the package as a module |
| `h3d.log(msg)`, `h3d.warn(msg)` | write to the editor console / game log |

### Events

| Event | Arguments | When |
| --- | --- | --- |
| `init` | `engine` | the engine (and the project) is ready |
| `update` | `engine, dt` | every frame, in the editor and in games |
| `play_start` / `play_stop` | `engine` | Play pressed / stopped (games start in play) |
| `play_tick` | `engine, dt` | every frame while playing |
| `render` | `engine` | after the 3D scene is drawn — draw with `engine.hud`, then call `engine.hud.render()` |
| `key` | `engine, key` | a key was pressed; return `True` to consume it |
| `project_loaded` / `project_saved` | `engine, path` | after a project is opened / saved |

### Useful engine calls

`engine.add_cube/add_sphere/add_plane`, `engine.attach_blueprint(node, Class)`, `engine.find_blueprints("Name")`, `engine.raycast(origin, direction, max_dist, ignore=set())`, `engine.input.key("w")`, `engine.input.was_key_pressed("r")`, `engine.camera` (position / yaw / pitch), `engine.camera_locked`, `engine.hud.text/center_text/rect/bar`, `engine.audio.play("pickup")`, `engine.save_game(slot, data)` / `engine.load_game(slot)`, `engine.physics`, `engine.select_node(node)`, `engine.spawn_impact_fx(hit)`, and `h3d.module.Mesh.from_arrays(engine.ctx, positions, normals, uvs, indices)` for generated geometry. Full list: [[Scripting API Reference]].

### Reaching another package from code

Loaded packages are importable modules named `h3dpkg_<id>`; extra files loaded with `h3d.load` are `h3dpkg_<id>_<file>`:

```python
import sys
mh = sys.modules["h3dpkg_mh_characters"]
core = sys.modules["h3dpkg_mh_characters_mh_core"]
```

---

## Hub API (`hub.py`)

```python
def register(hub):
    hub.set_theme({"bg": "#f4f5f8", "text": "#1d2129", "dark": False})   # see light_theme/hub.py for every key

    def build(page, ui):                      # ui: C, F, px, Card, Btn, Pill, Switch, label, entry, scrollable, toast, ...
        ui.label(page, "My page", 20, "bold").pack(anchor="w", padx=32, pady=24)
        ui.Btn(page, "Do it", lambda: ui.toast("Done!"), kind="primary").pack(anchor="w", padx=32)
    hub.add_page("My page", build, icon="templates")      # icons: projects learn templates packages settings ...

    hub.add_project_action("Count files", lambda project: hub.toast(project["name"]))

    @hub.on("project_created")
    def created(project):
        hub.log("new project at " + project["path"])
```

| Member | |
| --- | --- |
| `hub.set_theme(dict)` | replace Hub colours before the window is built |
| `hub.add_page(title, build, icon=)` | add a sidebar page; `build(page_frame, ui)` runs each time it opens |
| `hub.add_project_action(label, fn)` | add an item to every project's **⋯** menu; `fn(project)` |
| `hub.on(event, fn)` | `ready(hub)`, `project_created(project)`, `project_opened(project, mode)`, `export_finished(project)` |
| `hub.toast(msg)`, `hub.log(msg)` | feedback |
| `hub.settings`, `hub.save_settings()` | saved per user |
| `hub.config`, `hub.projects`, `hub.path`, `hub.root` | Hub settings, the project list, this package's folder, the Tk window |

---

## Rules of thumb

- **Games only see project packages.** Test your `game` target by adding the package to a project and pressing Play from the Hub.
- Store everything a game needs **inside the package folder** and reach it with `h3d.asset(...)`.
- Save user choices in `h3d.settings` and call `h3d.save_settings()`; in a project package the file travels with the exported game.
- Prefer `h3d.wrap` over monkey-patching by hand — a failing wrapper falls back to the original.
- Bump `version` in `package.json` when you ship changes.

## Examples to read

| Package | Shows |
| --- | --- |
| `packages/physics_plus` | settings, tools, `init` — the whole API in ~40 lines |
| `packages/light_theme` | `h3d.theme` and `hub.set_theme` |
| `packages/spline_roads` | generated meshes, live rebuild while editing, blueprints with child nodes |
| `packages/car_race_kit` | a full game mode, `requires`, a template, save games |
| `packages/mh_characters` | shader hooks, GPU skinning, a custom editor panel, big assets |
| `packages/tropical_islands` | procedural generation, caching, LOD, impostors |
| `packages/hqfps_weapons` | glTF skinning runtime, viewmodels, tests |
