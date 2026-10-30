# Licence of the character assets

Everything in `assets/` is released under **CC0 1.0 Universal** (Creative Commons Zero, a public
domain dedication): <https://creativecommons.org/publicdomain/zero/1.0/>.

You may copy, modify, distribute and use these assets, including in commercial and
closed-source games, without asking permission and without giving credit. Credit is welcome but
never required.

## Sources

| What | From | Licence |
| --- | --- | --- |
| Base mesh `hm08`, morph targets (macro + detail), joint helpers | MPFB 2 (`src/mpfb/data`), the MakeHuman project | CC0 |
| Mixamo-compatible skeleton and its vertex weights (`rig.mixamo.json`, `weights.mixamo.json`) | MPFB 2 | CC0 |
| Proxies, eyes, eyebrows, eyelashes, hair, system clothes, skins | MakeHuman system assets pack | CC0 |
| Clothes, hair, eyebrows, eyelashes, skins, extra looks | MakeHuman community asset packs: dress01, gloves01, hats01, hats02, pants01, shirts01, shoes01, skirts01, suits01, suits02, suits05, underwear01, underwear04, hair01, glasses01, jewelry01, masks01, eyebrows01, eyelashes01, skins01, skins02, skins03, clothes_materials01, system_clothes_materials01, system_hair_materials01, system_hair_materials02, system_eye_materials01 | CC0 (only the assets each pack lists as CC0) |
| Built-in animations (`assets/anims/library.npz`, 38 motion-captured clips) | Quaternius, *Universal Animation Library* 1 and 2 (Standard), <https://quaternius.com/packs/universalanimationlibrary.html>, packed by `tools/build_anims.py` | CC0 |
| Pistol prop | made in code for this package | CC0 |

Asset packs: <https://static.makehumancommunity.org/assets/assetpacks/index.html>.
MakeHuman / MPFB: <https://static.makehumancommunity.org>. The MakeHuman project's statement on
CC0 output: *"there is no limitation on what you can do with this combined output"*.

## Left out on purpose

- Every asset marked **CC-BY** (it would require attribution), for example the whole
  `underwear02` pack, `culturalibre_hero_mask_5` and `culturalibre_heroine_boots_4`.
- Assets and colour variants named after **trademarked characters or franchises** (superhero
  costumes such as the Captain America / Spider-Man / Superman / X-Men looks, Rei Ayanami,
  Yoko Tsuno, Spider-Gwen, Doc Ock glasses, Dr Manhattan and Tigra skins, a "Jungle Book" hair,
  a White Walker skin) - CC0 covers the files, not someone else's brand.
- A few items not suited to a general game kit (political slogan hat, slave collar, bag on
  head, explicit / annotated skins, a thong).

`catalog.json` → `skipped` lists every asset skipped while building.

## Not included

Mixamo animations: free from Adobe for use in your games, but not redistributable as files, so
you download them from mixamo.com yourself (see `docs/CHARACTERS.md`). The built-in clips above
are CC0 motion capture and cover every slot without them.
