# FarScream: open-island shooter map generator

**FarScream** is a demo package built on three others (*Tropical Islands*, *HQ FPS
Weapons*, *MakeHuman Characters*): press one button and you get a whole island
to fight over, furnished and populated, with a reason to go everywhere on it.

- **Military camps** (3): a bunker or two, army tents, a campfire with chairs,
  a table, supply crates, fuel drums and a barrier checkpoint where the road
  comes in. Soldiers with pistols and MP5s guard them; one camp has the army
  truck.
- **Radio outpost** on the highest flat ground, with fuel drums next to the
  station: blow them up.
- **Islander villages** (2) in the jungle: tents in a ring round a fire. The
  islanders don't carry guns, but if they see you they run at you and punch.
- **A plane crash** in a jungle clearing (smoke rising from it, crates
  scattered around, an MP5 in the grass) and **a shipwreck** on a beach.
- **Wrecked trucks** along the dirt roads, and the **pickup truck** waiting
  where you land.

Everything comes from the map's `seed`: the project only saves the seed and a
few settings, and the island and everything on it are rebuilt identically when
it opens (in the editor and in exported games).

## Getting started

1. In the Hub, create a project from the **FarScream** template (or add the
   *FarScream*, *Tropical Islands*, *HQ FPS Weapons* and *MakeHuman
   Characters* packages to a project, then **Packages > FarScream: generate
   new map**).
2. Press **Play**.
3. **Packages > FarScream: generate new map** rolls a new island (a new seed,
   a random shape). **FarScream: rebuild this map** rebuilds the current one.

## Playing

| | |
| --- | --- |
| WASD, mouse, Shift, Space | walk, look, sprint, jump |
| LMB / RMB / R | fire / aim / reload |
| Q / E, 1-9, wheel | switch weapons |
| **F** | open a supply crate, get into or out of a truck |
| W / S, A / D, Space (driving) | throttle and brake / reverse, steer, handbrake |
| mouse (driving) | swing the camera round; it settles back behind the truck |

- You start with the **M1911 pistol** and two spare magazines.
- Soldiers drop their **.45 ammunition**, and the MP5 if they carried one: walk
  over it to take it. The MP5 and the M1911 share the .45 pouch.
- **Supply crates** hold ammunition and medicine (F to open).
- **Red fuel drums** explode when shot: they hurt everyone around them, set off
  the drums next to them and send trucks flying.
- **Trucks** (the army truck and the pickup) drive in third person, Far Cry
  style: they have weight, grip that lets go in a hard turn or on the
  handbrake, suspension that follows the ground, and they run people over.
  Hitting a wall at speed hurts the driver.
- Bullet holes: at most 40 stay on the map; a new one removes the oldest.

**Objectives** (top left, with the distance and direction of the nearest one
and a marker over it): clear the three camps, blow up the fuel at the radio
station, find the plane crash, search the shipwreck. Clear the camps and the
radio station and the island is **liberated**.

## The map node

Select **FarScream Map** in the Outliner to change it in the Inspector:

| Property | |
| --- | --- |
| `seed` | everything on the island comes from it |
| `island` | the name of the Tropical Island the map is built on |
| `topology` | `random` or one of classic, hills, mountains, twin_peaks, plateau, volcano (used by *generate new map*) |
| `size` | island diameter in metres (used by *generate new map*) |
| `camps`, `villages` | how many of each |
| `soldier_health`, `islander_health`, `difficulty` | the fight (difficulty multiplies enemy damage) |
| `move_spawn` | put the Player Spawn on the landing beach |
| `spawn_distance` | characters appear within this distance of the player (metres) |

## The models, optimized without visible change

The 19 models (tents, bunkers, barrels, barriers, crates, chairs, table,
campfire, radio station, Li-2 plane, cargo ship, M3S army truck, pickup and
their wrecks) went through `tools/optimize_models.py` and
`tools/strip_lod_textures.py`:

- **Geometry**: duplicate vertices merged, degenerate triangles removed, and
  edges collapsed only while the shape moves less than 0.1 mm, normals turn
  less than half a degree and texture coordinates slide less than a quarter
  texel. UV seams, hard edges and borders never move.
- **Levels of detail**: lighter versions made the same way with larger
  tolerances, each drawn only from the distance where it differs from the full
  model by less than one pixel (1080p, 70 degree view). Levels that would save
  under 20 % are left out, and the trucks you drive always use full detail.
- **Textures are the originals** (byte for byte), and **streamed**: a texel
  can only be seen when it covers a screen pixel, so each material uses the
  largest version of its texture that could ever show at the camera's
  distance to the nearest copy of the model: the full image only when you are
  right next to it, 1/2, 1/4 ... further away (with room for 4x anisotropic
  filtering at grazing angles). Those smaller versions are box-filtered like
  the GPU's own mip levels, which is what it would sample there anyway, so the
  picture stays the same while the props use a few hundred MB of video memory instead of
  about 5.7 GB. Images are decoded on a worker thread (nearest first) and
  uploaded a few milliseconds' worth per frame.
- **Checked by screenshots**: every model from three sides, original against
  optimized: at most 0.07 % of the pixels differ by more than 2 of 255 (single
  pixels on silhouette edges), most views are identical. Camps seen from 30 and
  55 m with streamed against full-size textures: 0.02 % of the pixels differ by
  more than 8 of 255 (isolated pixels, 60 dB PSNR).
- **Shared meshes**: every copy of a model shares its meshes and materials, so
  the renderer draws all the barrels of the island in a few instanced calls.
- **Collisions**: small props collide as boxes, buildings and wrecks with a
  low-poly copy of themselves (vertices clustered into 10-25 cm cells).

Characters are prepared (body fitted, animations retargeted) on a worker
thread before you meet them, so they appear without a hitch.

`tools/texel_density.py <models_dir> --report` measures how many texels per
metre every texture gives its model. For these models every texture is below
what a 1440p screen can show from 40 cm, so none could be made smaller without
a visible change, and none was.

## Licence

The models were provided by the repository owner; their original licence is
not recorded. See `packages/farscream/ASSETS_LICENSE.md` before shipping them
in a game.
