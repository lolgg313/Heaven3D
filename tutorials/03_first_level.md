---
title: Build your first level
level: Beginner
minutes: 20
template: empty
summary: A floor, crates, walls and a player you can walk around with.
---
# Build your first level

You will build a small arena: a grass floor, crates to hide behind, walls and a starting point for the player.

## Step by step

1. **Turn the ocean off.** Select **Ocean** in the Outliner and untick **Enabled** in the Inspector's Ocean panel.
2. **Floor:** add a cube (toolbar, or **Create > Cube**), then set Scale `40 / 1 / 40`, Y `-0.5`, Color `0.35 0.42 0.33`, Rough `0.9`.
3. **Crates:** add a Cube, Scale `2 / 2 / 2`, Y `1`. Move it with [[W]] and press [[Ctrl]] + [[D]] a few times to duplicate it.
4. **Wall:** one more Cube with Scale `12 / 3 / 0.6`, Y `1.5`, Z `12`.
5. **Player:** **Create > Player spawn** (there can be only one). Put it at X 0, Y 1, Z -6 and set Rotation Y to 180 so the player faces the arena.
6. Save ([[Ctrl]] + [[S]]), then press **Play**. Click into the view to grab the mouse and walk with [[W]] [[A]] [[S]] [[D]], jump with [[Space]]. [[Esc]] frees the mouse; [[Ctrl]] + [[P]] stops.

> NOTE: Every Cube and Sphere is solid automatically — it gets a box or sphere collider, so you can't walk through it.

> WATCH OUT: The editor doesn't auto-save. Save often ([[Ctrl]] + [[S]]). A mistake is undone with [[Ctrl]] + [[Z]].

## Everything in the Create menu

- **Point Light / Spot Light** — bulbs and torches
- **Player Spawn** — where the player starts (one per level)
- **Enemy Spawn** — a flying drone that hunts and shoots the player
- **Bullet Pickup / Health Pickup** — touch to get ammo or health
- **Trigger Point** — the finish line; shows a message like LEVEL COMPLETE
- **Sound Point** — plays a sound or music file
- **Scripted object / Animated object** — an object with a visual script or a keyframe animation ready to edit

Whole houses and streets come from the **Buildings** tool: see *World tools*.
