# Heaven3D Packages

Packages add new features to **the Hub**, **the editor** and **your games**, written in plain Python.
Some examples: a light theme, a spline-road tool, better physics, a ready-made car racing kit, extra Hub pages.

The **Packages** page in the Hub installs, enables, creates and shares them.

> Packages run Python code on your computer. Only install packages from people you trust.

---

## 1. What a package looks like

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

### package.json

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

| key | meaning |
| --- | --- |
| `id` | lowercase letters, digits and `_`, starting with a letter (it names the folder) |
| `targets` | where it runs: `"hub"`, `"editor"`, `"game"` (any combination) |
| `engine` | entry file for the editor and games; needs `register(h3d)` |
| `hub` | entry file for the Hub; needs `register(hub)` |
| `requires` | other packages to load first (installed and copied automatically) |
| `heaven3d` | minimum engine version, e.g. `">=2.7.0"` |
| `default_settings` | starting values for `h3d.settings` / `hub.settings` |
| `category` | `Theme`, `Physics`, `Terrain`, `Gameplay`, `Tools`, … (sets the tile colour) |

---

## 2. Where packages live and when they run

| Location | Used by |
| --- | --- |
| `~/.heaven3d/packages/<id>` (your **library**) | The Hub (`hub` target) and the editor (`editor`/`game` targets), when the package is **On** |
| `<project>/packages/<id>` (**project packages**) | That project in the editor, **and every game exported from it** (`game` target) |
| `<Heaven3D>/packages/<id>` | Packages included with Heaven3D, offered on the Packages page |

- **Games only use project packages.** That keeps exported games self-contained. In the Hub, use
  **Add to project** on a package card, or **⋯ > Packages…** on a project. A project template can list
  the packages it needs (`"packages": [...]` in `template.json`); they're added automatically.
- A project's copy wins over the library copy, so a project keeps the exact version it was built with.
- Changes to `hub` packages apply after **Restart now** on the Packages page. `editor`/`game` packages
  load the next time the editor or game starts.
- **Safe mode**: start the Hub or the editor with `--safe-mode` (or set `HEAVEN3D_SAFE_MODE=1`) and no
  package is loaded. Use it to recover from a broken package.

---

## 3. Engine API (`engine.py`)

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

    def add_cube(engine):               # an editor tool (Packages menu in the Hierarchy header)
        cube = engine.add_cube(name="Spinning Cube")
        engine.attach_blueprint(cube, Spinner)
    tool = h3d.add_tool("My tools: add spinning cube", add_cube)
    tool["label"] = "My tools: add spinner"          # tools can be renamed any time

    @h3d.on("play_tick")
    def tick(engine, dt):
        engine.hud.text(0.02, 0.95, "Hello from a package", size=18)
```

### `h3d` reference

| | |
| --- | --- |
| `h3d.engine` | the running `HeavenEngine` (set after the `init` event) |
| `h3d.module` | the Heaven3D module (all engine classes and helpers) |
| `h3d.mode` | `"editor"` or `"runtime"` (a game) |
| `h3d.path`, `h3d.asset("tex.png")` | this package's folder / a file inside it |
| `h3d.settings`, `h3d.save_settings()` | a dict saved for you (project packages save into the project, so settings ship with the game) |
| `h3d.on(event, fn)` | subscribe to an event (also usable as a decorator) |
| `h3d.add_tool(label, fn, color=None)` | add a button to the editor's **Packages** menu; `fn(engine)` |
| `h3d.blueprint` | decorator that registers a Blueprint class |
| `h3d.wrap(owner, "name", wrapper)` | safely replace a method: `wrapper(original, *args, **kw)`; if your wrapper raises, the original runs |
| `h3d.theme(text="#1F2328", panel="#F8F9FB", ...)` | change editor colours (`THEME` keys) |
| `h3d.load("helper.py")` | import another file of the package as a module |
| `h3d.log(msg)`, `h3d.warn(msg)` | write to the editor console / game log |

### Events

| event | arguments | when |
| --- | --- | --- |
| `init` | `engine` | the engine (and the project) is ready |
| `update` | `engine, dt` | every frame, in the editor and in games |
| `play_start` / `play_stop` | `engine` | Play pressed / stopped (games start in play) |
| `play_tick` | `engine, dt` | every frame while playing |
| `render` | `engine` | after the 3D scene is drawn (draw overlays with `engine.hud`, then `engine.hud.render()`) |
| `key` | `engine, key` | a key was pressed; return `True` to consume it |
| `project_loaded` / `project_saved` | `engine, path` | after a project is opened / saved |

Errors in packages never stop the engine. They're logged, and a per-frame handler that keeps
failing is switched off.

### Useful engine calls inside packages

`engine.add_cube/add_sphere/add_plane`, `engine.attach_blueprint(node, Class)`,
`engine.find_blueprints("Name")`, `engine.raycast(origin, direction, max_dist, ignore=set())`,
`engine.input.key("w")`, `engine.input.was_key_pressed("r")`, `engine.camera` (position/yaw/pitch),
`engine.camera_locked`, `engine.hud.text/center_text/rect/bar`, `engine.audio.play("pickup")`,
`engine.save_game(slot, data)` / `engine.load_game(slot)`, `engine.physics`, `engine.select_node(node)`,
`h3d.module.Mesh.from_arrays(engine.ctx, positions, normals, uvs, indices)` for generated geometry.

---

## 4. Hub API (`hub.py`)

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

| | |
| --- | --- |
| `hub.set_theme(dict)` | replace Hub colours before the window is built |
| `hub.add_page(title, build, icon=)` | add a sidebar page; `build(page_frame, ui)` runs each time it opens |
| `hub.add_project_action(label, fn)` | add an item to every project's **⋯** menu; `fn(project)` |
| `hub.on(event, fn)` | `ready(hub)`, `project_created(project)`, `project_opened(project, mode)`, `export_finished(project)` |
| `hub.toast(msg)`, `hub.log(msg)` | feedback |
| `hub.settings`, `hub.save_settings()` | saved per user |
| `hub.config`, `hub.projects`, `hub.path`, `hub.root` | Hub settings, the project list, this package's folder, the Tk window |

---

## 5. Making, testing and sharing

1. Hub > **Packages** > **Create package**. You get a working example (a blueprint, an editor tool and
   a Hub page) in `~/.heaven3d/packages/<id>`, already switched on.
2. Edit the files, then reopen the editor (or **Restart now** for Hub packages).
3. Share it: **⋯ > Export as .h3dpkg…**. Others install it with **Install from file**.
   A `.h3dpkg` is a zip with `package.json` at the top.

Command line:

```bash
python Heaven3D_Hub.py --list-packages
python Heaven3D_Hub.py --install-package my_package.h3dpkg
python Heaven3D_Hub.py --safe-mode
```

## 6. Included packages

| Package | Targets | What it adds |
| --- | --- | --- |
| **Light Theme** | Hub, Editor | a light look for the Hub and the editor |
| **Physics Plus** | Editor, Game | physics quality presets (120 Hz, more solver iterations), gravity presets |
| **Spline Roads** | Editor, Game | roads drawn with control points that follow the terrain, with markings and a collider |
| **Car Race Kit** | Editor, Game | arcade car, checkpoints, race manager (laps, countdown, best lap), *Race Track* template |
| **MakeHuman Characters** | Editor, Game | MPFB2 / MakeHuman people (morphs, 175 CC0 clothes, hair, skins) with a Character Creator, Mixamo / glTF / BVH animation retargeting, path-walking NPCs, enemies, foot IK, *Character Town* template. See [CHARACTERS.md](CHARACTERS.md) |

Their source in `packages/` is a good place to learn from.
