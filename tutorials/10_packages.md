---
title: Packages: add new features
level: Intermediate
minutes: 20
summary: Install packages, use them in a game, and write your own in Python.
---
# Packages: add new features

Packages add features to **the Hub**, **the editor** and **your games**: a light theme, a road tool, better physics, a whole racing kit. They're plain Python, so you can also write your own.

> WATCH OUT: Packages run Python code on your computer. Only install packages from people you trust.

## Install the included packages

1. In the Hub, open **Packages**.
2. Under **Included with Heaven3D**, press **Install** on *Car Race Kit*. *Spline Roads* comes with it, because the kit needs it.
3. Install *Light Theme* too, then press **Restart now** to see the Hub in light colours. Switch it **Off** to go back to dark.

## Make a racing game in two minutes

1. **Projects > + New project**. Pick the new **Race Track** template (it appears once *Car Race Kit* is installed).
2. Press **Play**: wait for 3, 2, 1, GO! and drive with [[W]] [[A]] [[S]] [[D]] or the arrow keys. [[Space]] is the handbrake, [[R]] puts you back on the track, [[Enter]] restarts the race.
3. Your best lap is saved and shows up the next time you race.

## Use your own car model

The race car starts as a simple dummy car. Swap in real models:

1. Click the car, then open **Packages > Race kit: car body model…** and pick a `.glb`, `.gltf`, `.blend` or `.obj` file. The model is scaled to the car's length, centred and set on the wheels.
2. **Packages > Race kit: car wheel model…** puts your wheel model on all four corners. Use a single wheel with its axle running sideways. The left wheels are mirrored, and while driving the wheels spin and the front ones steer.
3. Fine-tune it in the Inspector under *ArcadeCar*:
   - `body_turn`: `180` if the car drives backwards (or use **Race kit: turn car body 180°**)
   - `body_scale`, `body_offset_y`: size and height of the body
   - `wheel_scale`, `wheel_turn`, `wheel_track` (how far apart left and right), `wheel_base` (how far apart front and back)
   - `show_wheels`: switch off if your body model already has its own wheels
4. **Race kit: built-in car look** brings the dummy car back.

> TIP: The models are copied into your project when you save, so the game still finds them after you move the project or export it.

## Use packages in your own project

A game only uses the packages inside its project folder, so exported games never miss anything.

1. On the **Packages** page, press **Add to project** on a package and pick your project. You can also use **⋯ > Packages…** on a project.
2. Open the project. The **Packages** button in the Hierarchy header lists the package tools, for example:
   - **Spline road: new road**: then move the orange points with the gizmo; the road follows live.
   - **Race kit: add car / add checkpoint / add race manager**, and **car body model… / car wheel model…** for your own car.
   - **Physics quality: High**: click to cycle Normal / High / Ultra.
3. **Export** the game as usual. The packages travel inside it.

> TIP: Blueprints from packages (like *SplineRoad* or *ArcadeCar*) show their settings in the Inspector, such as road width or car top speed.

## Write your own package

1. **Packages > + Create package**. Give it a name and choose where it runs (Hub, Editor, Games).
2. Its folder opens. `engine.py` already contains a working example:

```python
def register(h3d):
    m = h3d.module

    @h3d.blueprint
    class Spinner(m.Blueprint):
        def on_tick(self, dt):
            self.node.rotation_euler.y += 90 * dt

    def add_cube(engine):
        cube = engine.add_cube(name="Spinning Cube")
        engine.attach_blueprint(cube, Spinner)

    h3d.add_tool("Add spinning cube", add_cube)
```

3. Open any project: your tool is in the **Packages** menu. Press it, then **Play**.
4. Share your package: **⋯ > Export as .h3dpkg…** on its card. Friends install it with **Install from file**.

> NOTE: The full reference is `docs/PACKAGES.md` in your Heaven3D folder. It covers every event (update, play_tick, key, render, …), Hub pages, themes and settings.

## If something goes wrong

A broken package never stops the engine. Its error is shown in the editor console and on its card. If the Hub itself won't start, run it with `--safe-mode`, then switch the package off.
