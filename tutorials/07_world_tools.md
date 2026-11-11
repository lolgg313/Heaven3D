---
title: Terrain, buildings and effects
level: Intermediate
minutes: 20
template: outdoor_terrain
summary: Sculpt landscapes, generate buildings and add particle effects.
---
# Terrain, buildings and effects

## Terrain

Click the blue **Terrain** button. If the scene has no terrain yet, click **CREATE 3x3 KM TERRAIN**. Pick a tool and paint with the left mouse button:

- **Raise / Dig** — hills and holes; **Smooth** softens; **Stamp** makes plateaus; **Ramp** drags a slope from A to B
- **Paint** + a layer (Grass, Dirt, Rock, Asphalt, Sand); steep slopes turn to rock automatically
- the `[` and `]` keys change the brush size, [[Esc]] closes the panel

Real 3D grass grows on the grass layer and bends as you walk through it.

## Buildings

Click **Buildings** in the toolbar to open the Building Maker. Pick a **Style** (town house, farmhouse, row house, manor, stone hall, factory, bunker), choose the **Room** tool and drag a rectangle on the grid: walls, floor, ceiling and roof appear by themselves. A room painted with another **Finish** gets a wall between; rooms with the same finish join into one.

The other tools work on the walls: **Door**, **Window** and **Archway** (click a wall, or drag along several), **Ruined wall** and **No ceiling** for shelled houses, **Low wall** and **Sandbags** for cover, **Stairs** for a flight to the next storey ([[R]] turns it). Hold [[Ctrl]] or [[Shift]] to remove instead. [[PageUp]] / [[PageDown]] change the storey; the storeys above the one you edit are hidden so you can see inside.

**Generate house** makes a whole house, and **Generate village** a street of them (set **Damage %** for war damage). Every building is one object you can move, turn, duplicate and save as a prefab. The older module-by-module generator is still there under **Classic sector generator...**. Scripting: see `docs/BUILDING_MAKER.md`.

## Particle effects

Click **Particle** to open the Particle Studio. Click a preset (Smoke Plume, Fire, Electricity, Embers, Explosion, Rain, Magic Orb, Sparks) to drop it into the world, then move it with the gizmo. Design your own on the right and **SAVE PRESET**.
