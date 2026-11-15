# Licence of the FarScream models

The 19 models in `assets/models/` (tents, bunkers, barrels, barriers, crates,
chairs, table, campfire, radio station, Li-2 plane, cargo ship, M3S army truck,
pickup truck and their wrecks) were **provided by the repository owner
(lolgg313)** for this demo.  Their original author and licence are not
recorded here: before you ship a game that uses them, make sure you have the
right to (ask the owner, or replace them with models whose licence you know).

The optimized copies differ from the originals only in ways you can't see:
merged duplicate vertices, edge collapses smaller than 0.1 mm, and extra
lighter levels of detail (`*_LOD<n>.glb`) that are only drawn from the
distance where they differ by less than a pixel.  Textures are the originals,
byte for byte (the `_LOD` files carry none).

The code of the package (`engine.py`, `fs_layout.py`, `tools/`) is under the
Heaven3D licence (see `LICENSE` at the top of the repository).
