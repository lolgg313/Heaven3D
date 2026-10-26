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

1. **Turn the ocean off.** Select **Ocean** in the Hierarchy and click **Enabled ON** so it says OFF.
2. **Floor:** click **Cube**, then set Scale `40 / 1 / 40`, Y `-0.5`, Color `0.35 0.42 0.33`, Rough `0.9`.
3. **Crates:** add a Cube, Scale `2 / 2 / 2`, Y `1`. Move it with [[W]] and press **Duplicate** a few times.
4. **Wall:** one more Cube with Scale `12 / 3 / 0.6`, Y `1.5`, Z `12`.
5. **Player:** click **Library** → **Player Spawn (only 1)**. Put it at X 0, Y 1, Z -6 and set **Yaw** to 180 so the player faces the arena.
6. **Save**, then press **Play**. Click into the view to grab the mouse and walk with [[W]] [[A]] [[S]] [[D]], jump with [[Space]]. [[Esc]] frees the mouse; [[Ctrl]] + [[P]] stops.

> NOTE: Every Cube and Sphere is solid automatically — it gets a box or sphere collider, so you can't walk through it.

> WATCH OUT: The editor doesn't auto-save. Click Save often.

## Everything in the Library

- **Point Light / Spot Light** — bulbs and torches
- **Player Spawn** — where the player starts (one per level)
- **Enemy Spawn** — a flying drone that hunts and shoots the player
- **Bullet Pickup / Health Pickup** — touch to get ammo or health
- **Trigger Point** — the finish line; shows a message like LEVEL COMPLETE
- **Sound Point** — plays a sound or music file
