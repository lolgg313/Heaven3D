# Editor Guide

Start the editor from the Hub (**Open** on a project) or directly:

```bash
python Heaven3D.py                                   # editor, default scene
python Heaven3D.py --project "My Game/main.heaven3d" # editor with a project
heaven3d --project "My Game/main.heaven3d"           # the same, when installed with pip
```

A new scene always contains a **Sun**, **Clouds** and an **Ocean**. They are ordinary objects in the Outliner: select them to change their settings, or switch them off.

> This page describes the editor since 3.1.3. `python Heaven3D.py --classic-ui` (or `HEAVEN3D_CLASSIC_UI=1`) brings the older interface back.

## Screen layout

| Area | Contents |
| --- | --- |
| **Menu bar** | **File** (new scene, open, save, import, export), **Edit** (undo / redo, duplicate, delete, rename, save as prefab, tools), **Create** (every object you can add), **View**, **Play**, **Tools**, **Packages** (tools of installed packages), **Help** (API reference, shortcuts) |
| **Toolbar** | Play, Save, Open · Move / Rotate / Scale · add cube, sphere, plane, point light, player spawn, enemy spawn · **Import** · **Terrain**, **Particles**, **Buildings** |
| **Outliner** (left) | Every object in the scene, with a search box. Click to select, right-click for Rename / Duplicate / Delete, drag an object onto another to parent it, click the eye to hide it. |
| **Viewport** (middle) | The 3D world. **Lit / Lit + Wire / Wire** buttons in its top-left corner, the view gizmo in its top-right corner. |
| **Inspector** (right) | Settings of the selected object: name, visibility, tag, Transform, Material, Physics, blueprints and their properties, light / spawn / ocean settings. Drag a number to scrub it, double-click to type. A world tool (Terrain, Particles, Buildings) opens here in its place. |
| **Bottom panel** | Tabs: **Console** (log + Python prompt), **Assets** (project files), **Script** (code editor with hot reload), **Graph** (visual scripts), **Material** (node materials), **Animation** (keyframes), **API** (reference) |
| **Status bar** | hints, object count, frame rate |

**View** shows or hides the Outliner, Inspector and bottom panel (Ctrl+J) and sets the interface size.

## Camera controls

Hold the **right mouse button** while you fly:

| Input | Action |
| --- | --- |
| RMB + move mouse | look around |
| RMB + W / A / S / D | fly forward / left / back / right |
| RMB + Q / E | down / up |
| Shift | fly 4× faster |
| RMB + mouse wheel | change fly speed |
| Mouse wheel | zoom |
| Alt + left drag | orbit |
| Middle drag | pan |
| F | frame the selected object (use it when you get lost) |

On a Mac trackpad, the right mouse button is a two-finger click (or Ctrl + click).

## Editing shortcuts

| Key | Action |
| --- | --- |
| Left click | select an object (any part of an imported model or a building selects the whole object) |
| W / E / R | move / rotate / scale gizmo |
| F | focus the selection |
| Ctrl + D | duplicate |
| Delete | delete |
| F2 | rename |
| Ctrl + Z / Ctrl + Y | undo / redo |
| Esc | leave collider *Edit Shape* mode, then deselect |
| F4 | next view mode |
| Ctrl + S / Ctrl + O | save / open |
| Ctrl + I / Ctrl + E | import a model / export the game |
| Ctrl + P or F5 | start / stop Play |
| Ctrl + R | reload scripts |

While playing, the game fills the window and a small **Stop** pill floats over it; everything returns to its starting place when you stop.

> ⚠️ There is no auto-save. Press **Ctrl + S** often. The previous save is kept as `main.heaven3d.bak`.

## Units and conventions

- **1 unit = 1 metre.** The default player is 1.8 m tall with eyes at 1.62 m.
- **Y is up.** Objects face **−Z**.
- Rotations are Euler angles in **degrees**, applied in Unity's order: **Y, then X, then Z**.
- Colours are three numbers from 0 to 1: `1 0 0` red, `0.2 0.6 1` sky blue, `1 0.8 0.1` gold.

## View modes

Buttons at the top-left of the viewport, the **View** menu, or **F4**:

- **Lit** — the normal view
- **Lit + Wire** — the lit scene with every triangle edge drawn on top (check model density)
- **Wire** — edges only, seen through everything (x-ray), for finding hidden objects

The selected object's edges are drawn in orange. **View > Grid** toggles the ground grid. View modes only affect editing; Play and exported games are always *Lit*.

## The Create menu

| Section | Object | What it is |
| --- | --- | --- |
| Shapes | **Cube**, **Sphere**, **Plane** | primitives, solid from the start |
| Light | **Point light**, **Spot light** | bulbs and torches with colour, intensity, range (and cone angles) |
| Spawns | **Player spawn** | where the player starts (one per scene); holds health, bullets, walk / run speed and mouse sensitivity |
| | **Enemy spawn** | a flying drone that hunts and shoots the player |
| Pickups | **Bullet pickup**, **Health pickup** | touch to get ammo or health |
| | **Trigger point** | the finish line: shows a message such as *LEVEL COMPLETE* and stops the game |
| | **Sound point** | plays a sound or music file |
| Logic | **Scripted object**, **Animated object** | an object with a visual script / a keyframe animation ready to edit |

Spawns and pickups can use any 3D model: select one and click **Custom mesh...** in its settings panel. See [[Built-in Blueprints]] for every tunable value.

## Materials

Every mesh has a PBR material in the Inspector (colour, metal, rough, emission, alpha mode). Imported glTF / `.blend` materials also bring normal, metal-rough, emissive and occlusion maps. A **node material** made in the Assets tab is edited in the **Material** tab and shared by every object that uses it. See [[Rendering]].

## Physics in the Inspector

The **Physics** panel adds a collider (box, sphere, capsule, plane, mesh) and an optional **RigidBody**. **Edit Shape** lets you drag the collider's green outline in the viewport. See [[Physics]].

## Scripts, visual scripts, animation

- **Script** tab: write blueprints in Python; Ctrl + S saves and hot-reloads them. Attached blueprints show their properties in the Inspector.
- **Graph** tab: visual scripts (events, conditions, actions) for the selected object, no code.
- **Animation** tab: keyframe an object's transform and play it in the game.

All three also run in exported games. See [[Scripting Guide]].

## Assets and prefabs

The **Assets** tab shows the project folder. Double-click a model to import it or drag it into the viewport; right-click > Create makes scripts, node materials and prefabs. **Edit > Save as Prefab** stores the selection as a reusable `.prefab`; instances can be applied, reverted or unlinked from the Inspector.

## World tools

**Terrain**, **Particles** and **Buildings** in the toolbar open in the Inspector's place. See [[World Tools]].

## Importing and exporting

**File > Import model** (Ctrl + I) loads `.glb`, `.gltf`, `.blend`, `.obj`, `.fbx` or `.x`; each model arrives as **one object**. **File > Export game** (Ctrl + E) builds the game for players. See [[Importing Models]] and [[Exporting Games]].
