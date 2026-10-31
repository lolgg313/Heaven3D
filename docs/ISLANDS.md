# Tropical Islands

The **Tropical Islands** package generates whole tropical islands inside Heaven3D: terrain
with beaches, cliffs and eroded valleys, dirt roads, jungle, meadows, flowers and rocks,
in the engine's ocean. One click makes an island; every setting is a property you can change
in the Inspector, and the island rebuilds. Islands are saved as a few numbers and come back
identically when the project opens, in the editor and in exported games.

## Assets and licence

Everything is **CC0 1.0** (public domain): free for commercial games, no credit needed.

| Kind | Assets | Source |
| --- | --- | --- |
| Trees (5) | coconut palm (5 shapes: straight, bent, double, triple, double-bent), royal palm | Nobiax, *Free palm treeZ v3* and *Palm tree v2* (opengameart.org) |
| | island tree, jacaranda (flowering), acacia | Poly Haven: `island_tree_01`, `jacaranda_tree`, `tree_small_02` |
| Grass (2) | tall grass, lush grass | Poly Haven: `grass_medium_02`, `grass_medium_01` |
| Flowers (2) | orange gazania, pink periwinkle | Poly Haven: `flower_gazania`, `periwinkle_plant` |
| Stones (2) | boulder, mossy rocks (6 shapes) | Poly Haven: `boulder_01`, `rock_moss_set_01` |
| Terrain (4) | beach sand, jungle floor, dirt, mossy rock (PBR: colour, normal, roughness / AO) | Poly Haven: `aerial_beach_01`, `forrest_ground_01`, `dirt_floor`, `mossy_rock` |

The Poly Haven plants are photo-scans with millions of triangles. `tools/build_assets.py` turns
them into game-ready assets: leaves and twigs are clustered and rendered on the GPU into
texture-atlas cards (3 levels of detail), trunks and rocks are decimated, every tree and palm gets
an impostor (3 baked cards) for far away, and grass / flowers become crossed cards. See
`packages/tropical_islands/ASSETS_LICENSE.md`.

## Getting started

1. In the Hub, open **Packages**, **Install** *Tropical Islands* and **Add to project** (or create a
   project from the **Tropical Island** template).
2. In the editor, **Packages > Islands: new tropical island**. The island appears around the
   origin in a few seconds, the ocean turns tropical turquoise and the Player Spawn moves to a
   beach at the end of a dirt road, facing inland.
3. Press **Play** and explore on foot.

| Tool | |
| --- | --- |
| Islands: new tropical island | a new island with a random seed |
| Islands: new seed (selected island) | same settings, a different island |
| Islands: next shape (selected island) | cycles through the 9 shapes |
| Islands: move player spawn to the beach | puts the Player Spawn back on the island's beach |

## Settings (Inspector, TropicalIsland)

Generation settings rebuild the island (about 3-6 s; the result is cached in the project's
`.island_cache` folder, so reopening is instant):

| Property | |
| --- | --- |
| `seed` | any number: a different island for every seed |
| `size` | diameter in metres, 150 - 3000 |
| `topology` | `classic`, `volcano` (cone with crater), `mountains` (ridges), `hills`, `atoll` (ring with a lagoon), `twin_peaks`, `plateau` (terraced mesa with cliffs), `archipelago` (several islands), `flat` |
| `max_height` | peak height in metres (0 = the shape's natural height) |
| `coast_roughness` | 0 round ... 1 ragged with bays and capes |
| `beach_width` | 0 ... 1 |
| `erosion` | 0 ... 1: river valleys and talus slopes |
| `roads`, `road_width` | number of dirt roads (beach landings, clearings, a lookout) and their width |
| `jungle` | 0 ... 1: how much of the land is jungle |
| `tree_density`, `palm_density`, `grass_density`, `flower_density`, `rock_density` | multipliers |
| `resolution` | heightmap cells per side (0 = automatic, up to 513) |

View settings apply instantly:

| Property | |
| --- | --- |
| `detail_distance` | trees closer than this use full detail (default 55 m) |
| `far_distance` | trees farther than this become impostors (default 140 m) |
| `grass_distance` | grass and flowers are drawn up to this distance (default 70 m) |
| `shadow_distance` | vegetation casts shadows up to this distance; the sun's shadow map follows the camera (default 120 m) |
| `wind` | wind strength for all plants (0 = still) |
| `tree_collision` | the player bumps into trunks and boulders in Play |
| `tropical_water` | tint the ocean turquoise |

## How it works

- **Terrain**: domain-warped noise shaped by the chosen topology, D8 flow-accumulation river
  erosion plus thermal erosion, a flattened beach profile around sea level, and roads found
  with A* (avoiding steep slopes and the sea), smoothed, and carved into the terrain.
- **Material**: a splat map (sand / grass / dirt / rock) from height, slope, roads and noise.
  The terrain shader blends the 4 PBR layers by height (the "taller" texture wins at borders),
  turns steep pixels to rock (slope auto-material), samples cliffs triplanar, hides tiling with a
  second rotated scale and macro variation, and darkens wet sand at the waterline.
- **Vegetation**: species follow masks (coastal palms, jungle, meadows with flowers, rocks on
  slopes and beaches), away from roads. Instances are merged into chunk meshes per species
  (64 m for trees and rocks, 40 m for grass), each switching between 3 levels of detail by
  distance; grass and flowers are only drawn near the camera. Chunks are rebuilt on a per-frame
  time budget, so moving around never stalls the editor or the game.
- **Wind** sways every plant in the vertex shader (gusts, per-plant phase, leaf flutter); leaf
  cards cast alpha-tested shadows that sway too.
- **Physics**: the island is a heightfield collider; in Play, capsule colliders follow the
  player for nearby trunks and boulders.

## Rebuilding the assets

The shipped assets are already built. To rebuild them from the CC0 sources (needs a GPU):

```
python tools/build_assets.py <polyhaven_dir> <palms_dir> assets
python tools/preview.py assets/veg/island_tree.npz preview.png   # LOD0 / LOD1 / LOD2 side by side
python tools/make_template.py                                    # the Tropical Island template
```

## Scripting

```python
isl = sys.modules["h3dpkg_tropical_islands"]
node = isl.new_island(engine, {"topology": "volcano", "size": 900, "seed": 42})
rt = isl.runtime_of(node)
y = rt.height_at(10.0, -35.0)          # ground height at a world position
palms = rt.isl.instances["coconut_palm"]   # x, y, z, yaw, scale, variant per palm
```
