# Getting Started

This page takes you from a fresh install to a playable level in about ten minutes. If you haven't installed Heaven3D yet, see [[Installation]].

## 1. Open the Hub

```bash
python Heaven3D_Hub.py
```

(or double-click **Heaven3D Hub** in a standalone edition). The Hub is your home screen: projects, templates, tutorials, packages and settings. See [[Heaven3D Hub]].

## 2. Create a project

1. Go to **Projects → + New project**.
2. Pick a template — start with **First-Person Arena** to see a working game, or **Empty 3D** to build from scratch. All templates are listed on [[Templates]].
3. Give it a name and a folder, then create it.

A project is just a folder:

```
My Game/
├── main.heaven3d      ← your scene (the level)
├── project.json       ← name and template (used by the Hub)
├── assets/            ← models, textures, sounds you use
├── scripts/           ← your own Python game logic (blueprints)
└── packages/          ← packages this game uses (optional)
```

## 3. Play it

Press **▶ Play** on the project card. The game opens in its own window, exactly as your players will see it.

| Key | Action |
| --- | --- |
| W A S D | walk |
| Shift | run |
| Space | jump |
| Left mouse | shoot (hold for automatic) |
| R | reload |
| Esc | pause menu / release the mouse |

## 4. Edit it

Press **Open** on the project card to start the editor. You'll see:

- **Menu bar and toolbar** (top): File, Edit, Create, View, Play, Tools, Packages, Help; Play, Save, Open, the Move / Rotate / Scale tools, quick add buttons, Import, Terrain, Particles, Buildings
- **Outliner** (left): every object in the scene
- **Viewport** (middle): the 3D world, with **Lit / Lit + Wire / Wire** buttons in its top-left corner
- **Inspector** (right): settings of the selected object — drag a number, or double-click it to type
- **Bottom panel**: Console, Assets, Script, Graph, Material, Animation, API

Hold the **right mouse button** and use **W A S D** to fly around. Press **F** to jump to the selected object. Full controls: [[Editor Guide]].

## 5. Build your first level (Empty 3D template)

1. **Turn the ocean off**: select **Ocean** in the Outliner and untick **Enabled** in the Inspector.
2. **Floor**: add a cube (toolbar or **Create > Cube**), set Scale `40 / 1 / 40`, Y `-0.5`, Color `0.35 0.42 0.33`, Rough `0.9`.
3. **Crates**: add a Cube, Scale `2 / 2 / 2`, Y `1`. Move it with **W** and press **Ctrl+D** a few times.
4. **Wall**: a Cube with Scale `12 / 3 / 0.6`, Y `1.5`, Z `12`.
5. **Player**: **Create > Player spawn** (one per scene). Put it at X 0, Y 1, Z -6 and set Rotation Y to 180.
6. Save (**Ctrl+S**), then press **Play** (or **Ctrl+P**). Click the view to grab the mouse; **Ctrl+P** again stops.

> Every Cube and Sphere is solid automatically — it gets a box or sphere collider. One unit is one metre; the player is 1.8 m tall.

> ⚠️ The editor doesn't auto-save. Press **Ctrl+S** often; **Ctrl+Z** undoes a mistake.

## 6. Make it a game

From the **Create** menu, add:

- **Enemy Spawn** — a flying drone that hunts and shoots (place it at Y ≈ 2.5)
- **Health Pickup** / **Bullet Pickup** — place on the floor (Y 0.5)
- **Trigger Point** — the finish line; edit its **Text** in the Inspector

Tune numbers in the Inspector (player speed, enemy health, fire rate…). Details on [[Built-in Blueprints]].

## 7. Next steps

- Write your own game rules in Python: [[Scripting Guide]]
- Import models from Blender, Sketchfab, Kenney, Quaternius: [[Importing Models]]
- Add people, islands, cars or HQ weapons: [[Packages]]
- Send the game to friends: [[Exporting Games]]

The Hub's **Learn** page has the same path as offline step-by-step [[Tutorials]].
