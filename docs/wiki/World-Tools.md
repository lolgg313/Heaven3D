# World Tools

The editor toolbar has three world-building tools: **Terrain**, **Particles** (Particle Studio) and **Buildings**. Each opens on the right, in the Inspector's place. For whole procedural islands, see the [[Tropical Islands]] package; for roads, see [[Car Race Kit and Spline Roads]].

## Terrain

Click **Terrain** in the toolbar. If the scene has no terrain yet, click **CREATE 3x3 KM TERRAIN**. Then pick a tool and paint with the left mouse button.

| Tool | What it does |
| --- | --- |
| **Raise** | build hills |
| **Dig** | dig holes and valleys |
| **Smooth** | soften bumps |
| **Stamp** | make flat plateaus |
| **Ramp** | drag a straight slope from point A to point B |
| **Paint** + a layer | paint **Grass**, **Dirt**, **Rock**, **Asphalt** or **Sand** |

- Steep slopes turn to rock automatically.
- Real 3D grass grows on the grass layer and bends as you walk through it.
- `[` and `]` change the brush size; **Esc** closes the panel.
- Terrain gets a **heightfield collider**, so the player walks on it and raycasts hit it.

The *Outdoor Terrain* template starts with a sculpted 3 × 3 km landscape.

### From code

```python
terrain = engine.create_terrain(size=200.0, resolution=128, height_scale=10.0, seed=42)
```

The terrain is saved as its configuration (size, resolution, height scale, seed) plus your sculpting, and rebuilt on load.

## Buildings (Building Maker)

Click **Buildings** in the toolbar. Pick a **Style** (town house, farmhouse, row house, manor, stone hall, factory, bunker), choose the **Room** tool and drag a rectangle on the 2 m grid: walls, floor, ceiling and roof appear by themselves. A building is **one object** with one collider.

| Tool | What it does |
| --- | --- |
| **Room** / **Erase** | paint or remove cells; rooms with another finish get a wall between them |
| **Wall**, **Door**, **Window**, **Archway** | click a wall, or drag along several |
| **Ruined wall**, **No ceiling** | shelled houses |
| **Low wall**, **Sandbags** | cover |
| **Stairs** | a flight to the next storey (**R** turns it) |

**Ctrl** or **Shift** + drag removes. **PageUp / PageDown** change the storey. Every stroke is one undo step.

- **Generate house** makes a whole house, **Generate village** a street of eight (set **Damage %** for war damage).
- **Generate town** makes blocks of houses on a grid of streets: one undo step and one *Town* group, whose houses stay selectable one by one.
- Towns are drawn in **districts** with three levels of detail (rooms and glass near, frames and shutters further away, bare shells far), so hundreds of houses stay fast. **Detail** (Low / Medium / High / Ultra) sets the distances; the panel shows districts, triangles, draw calls and video memory.
- The older module-by-module generator (military, office, lab, bunker, street) is under **Classic sector generator...**.

From code:

```python
house = engine.add_building("town", position=(0, 0, 0), house=(4, 3, 2))    # width, depth, floors in cells
town = engine.add_town(6, 6, position=(40, 0, 0), seed=7)                    # 6 x 6 blocks
engine.set_building_detail(preset="high")
```

Full guide: [docs/BUILDING_MAKER.md](https://github.com/lolgg313/Heaven3D/blob/main/docs/BUILDING_MAKER.md).

## Particle Studio

Click **Particles** to open the Particle Studio. Click a preset to drop it into the world, then move it with the gizmo:

**Smoke Plume** · **Fire** · **Electricity** · **Embers** · **Explosion** · **Rain** · **Magic Orb** · **Sparks**

Design your own emitter on the right-hand side of the panel and press **SAVE PRESET** to reuse it.

## Sky, clouds and ocean

These are scene objects rather than tools: select **Sun**, **Clouds** or **Ocean** in the Outliner and edit them in the Inspector. Rotating the Sun changes the time of day. All settings are listed on [[Rendering]].
