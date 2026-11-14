# Building Maker

Paint buildings on a 2 m grid in the editor (**Buildings** in the toolbar), or make them from a script.
A building is **one object**: its walls, floors, stairs and roof are baked into a few meshes per storey
(one per material) with one static collider, so a whole village costs little to draw and to simulate.

## In the editor

| Tool | What it does |
| --- | --- |
| Room | Drag a rectangle of cells. Walls, floor, ceiling and roof follow. Same finish joins, another finish gets a wall. |
| Erase | Drag cells away. |
| Wall | Click or drag along cell edges to add a wall (Ctrl: remove one). |
| Door / Window / Archway | Click a wall, or drag along several. |
| Ruined wall | Shell a wall down to a broken edge with rubble at its foot. |
| Low wall / Sandbags | 1 m cover along an edge. |
| Stairs | A flight over two cells up to the next storey (`R` turns it). |
| No ceiling | Open cells to the sky (shelled rooms, courtyards). |

`Ctrl` or `Shift` + drag removes. `PageUp` / `PageDown` change the storey. Right-drag looks around as usual.
Every stroke is one undo step.

**Styles**: town house (red brick), farmhouse (whitewash), row house (rose render), manor (ochre render,
slate hip roof), stone hall, factory (sooty brick, corrugated iron), bunker (board-formed concrete).
**Finishes** set the inside of a room: wallpaper, plaster, checker-tiled hall, green paint, bare brick,
concrete, stone cellar, and two outdoor yards (cobbles, concrete) that get no walls or roof.

The textures are generated once per computer (a few seconds per material, then cached in
`~/.heaven3d/cache`). Saving a project copies the ones it uses into `assets/building_maker`, so the
project folder and every exported game are complete.

## Towns

**Town** in the Buildings panel makes blocks of houses on a grid of cobbled streets (about 7 houses a block,
a block is 34 m x 24 m). The houses repeat a few dozen designs; every house is its own building in one
"Town" group, and a click in the viewport still selects a single house. The whole town is one undo step.

Buildings that stand still are drawn in **districts** (48 m squares) instead of part by part:

| Level | Distance (Medium) | What is drawn |
| --- | --- | --- |
| near | under 45 m | everything: rooms, stairs, glazing bars, glass |
| mid | under 140 m | the outside with frames, shutters, sills; dark panes instead of glass and rooms |
| far | beyond | the bare shell (walls, roofs) with dark panes: about an eighth of the triangles |

A district is one draw call per material and one in the shadow pass, a far district only keeps its shell in
video memory, and one out of view costs nothing. A building with a room open to the sky, a shelled or a
missing outside wall keeps its inside at every distance. **Detail** in the panel picks the distances
(Low 25 / 80 m, Medium 45 / 140 m, High 80 / 220 m, Ultra 150 / 400 m); the two lines under it show what
the last frame drew. A selected, moving or storey-cut building is drawn on its own until it is left alone.

```python
town = engine.add_town(6, 6, position=(0, 0, 0), seed=7, damage=0.1)   # 6 x 6 blocks, ~250 houses
engine.set_building_detail(preset="high")              # or near=60, mid=200, far=800 (far: stop drawing beyond)
print(engine.building_stats())                         # districts, triangles, draw calls, video memory
```

`HEAVEN3D_NO_CITY=1` draws every building part by part again (to compare).

## From a script

```python
# a generated house: (width, depth, floors[, damage 0..1]) in cells
house = engine.add_building("town", position=(0, 0, 0), house=(4, 3, 2))

# an empty building, filled by hand
b = engine.add_building("bunker", position=(20, 0, 0))
engine.building_edit(b, cells=[(x, 0, z) for x in range(4) for z in range(3)], finish="concrete")
engine.building_edit(b, edges={(1, 0, -1, "N"): "door",        # the wall on the -z side of cell (1, 0, 0)
                               (3, 0, 1, "E"): "window"})      # the wall on the +x side of cell (3, 0, 1)
engine.building_edit(b, stairs={(0, 0, 0): 1})                 # a flight heading +z
```

* Cells are `(x, storey, z)`; a cell is 2 m x 3 m x 2 m.
* An edge is `(x, storey, z, "N")` for the wall between cell `(x, z)` and `(x, z + 1)`, or `"E"` for the
  wall between `(x, z)` and `(x + 1, z)`. Kinds: `wall`, `none`, `door`, `window`, `arch`, `ruin`,
  `half`, `sandbags`; `None` puts the automatic wall back.
* Stair directions: `0` +x, `1` +z, `2` -x, `3` -z.
* `engine.building_edit(b, erase=[...])` removes cells, `open_cells=[...]` removes their ceiling.

Styles are in `BK_STYLES`, finishes in `BK_FINISHES`; both are plain dictionaries you can add to before
making a building.
