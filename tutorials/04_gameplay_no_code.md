---
title: Make it a game (no code)
level: Beginner
minutes: 20
template: fps_arena
summary: Enemies, pickups, a finish line and difficulty tuning.
---
# Make it a game — no code needed

The **First-Person Arena** template already has everything wired up. Create the practice project, open it and press **Play**.

## Playing controls

- [[W]] [[A]] [[S]] [[D]] walk, [[Shift]] run, [[Space]] jump
- Left mouse shoot (hold for automatic), [[R]] reload
- [[Esc]] release the mouse, [[Ctrl]] + [[P]] stop

## Add your own gameplay pieces

1. **Create > Enemy spawn.** Put it in the air (Y about 2.5). [[Ctrl]] + [[D]] makes more drones.
2. **Create > Health pickup / Bullet pickup.** Place them on the floor (Y 0.5).
3. **Create > Trigger point** at the far end — the exit. Change **Text** in the Inspector to set the message.

## Tune the difficulty

Select a spawn or pickup and change its numbers in the Inspector:

- **Player Spawn:** Health Value, Bullet Value, Walk Speed, Run Speed, Cam Rot Speed (mouse sensitivity)
- **Enemy Spawn:** Health Value, Bullet Value (damage), Fly Speed, Shots / Sec
- **Pickups:** Bullet Count / Health Count

> TIP: Enemies and pickups can use any 3D model: select one, click **Custom mesh...** in its settings panel and pick a .glb, .gltf or .blend file. A drone model from a free site makes the game look professional instantly.

> NOTE: While playing, the game fills the window and the editor is locked — only **Stop** ([[Ctrl]] + [[P]]) works. Everything returns to its starting place when you stop.
