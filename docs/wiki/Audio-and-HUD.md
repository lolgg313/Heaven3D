# Audio and HUD

## Audio

Heaven3D's `AudioSystem` (on top of pygame's mixer) plays built-in synthesised sounds, your own files, 3D positional audio and music. It's **silent-safe**: on a machine with no audio device every call simply does nothing, so games never crash because of sound.

### Built-in sounds

13 sounds are synthesised at start-up, so a project needs no audio files to sound like a game:

`shoot` · `shotgun` · `hit` · `explosion` · `footstep` · `jump` · `land` · `pickup` · `reload` · `hurt` · `enemy` · `click` · `heal`

```python
engine.audio.play("pickup")
```

### Your own sounds

`.wav` and `.ogg` files work anywhere a sound name is accepted. Paths are resolved through the project, so they keep working after the project is moved or exported.

```python
engine.audio.play("assets/sfx/door.ogg")                 # auto-loaded on first use
engine.audio.load_sound("assets/sfx/door.ogg", "door")   # or pre-load under a name
engine.audio.play("door", volume=0.8)
```

### 3D sound

```python
engine.audio.play_at("explosion", position=node.world_position(), volume=1.0,
                     ref_distance=3.0, max_distance=60.0)
```

Volume falls off with distance from the listener and the sound is panned toward its direction. The listener follows the camera.

### Music

```python
engine.audio.play_music("assets/music/theme.ogg", loop=True, volume=0.6)
engine.audio.stop_music()
```

### In the editor

The **Create > Sound point** object plays a sound or music file in the level. Built-in blueprints have sound properties (`shoot_sound`, `footstep_sound`, `death_sound`, …) that accept a built-in name or a file.

### Volume

Players control the master volume from the **Esc** pause menu (Resume, Sound volume, Exit) in Play and in exported games. From code: `engine.audio.set_master_volume(0.5)`, `engine.audio.stop_all()`.

---

## HUD

`engine.hud` draws 2D overlays on top of the game. Coordinates are **normalised**: `(0, 0)` is the top-left corner and `(1, 1)` the bottom-right, so layouts work at any window size. HUD elements live for **one frame**, so draw them every frame (in `on_tick`).

```python
hud = self.engine.hud
hud.text(0.03, 0.04, f"Coins: {self.coins}", size=32, color=(1, 0.85, 0.2, 1))
hud.center_text(0.40, "YOU WIN!", size=72, color=(0.3, 1, 0.4, 1))
hud.bar(0.03, 0.92, 0.25, 0.025, self.hp / self.max_hp)          # health bar
hud.rect(0.0, 0.0, 1.0, 0.06, (0, 0, 0, 0.4))                     # translucent strip
hud.crosshair(spread=0.01)
hud.damage_vignette(0.6)
```

| Call | |
| --- | --- |
| `text(x, y, s, size=20, color=(1,1,1,1), shadow=True)` | text with optional drop shadow |
| `center_text(y, s, size=28, color=...)` | centred horizontally |
| `rect(x, y, w, h, color)` | filled rectangle |
| `bar(x, y, w, h, frac, fg, bg, border)` | progress bar, `frac` 0–1 |
| `crosshair(color, size, gap, thickness, spread, dot)` | 4-line crosshair with optional centre dot |
| `damage_vignette(intensity)` | red screen edges when hurt |

The built-in `FPSController` and `Weapon` draw their own health, ammo and crosshair when `show_hud` is on; turn it off to draw your own.

### From a package

Packages draw in the `render` event and must flush the HUD themselves:

```python
@h3d.on("render")
def overlay(engine):
    engine.hud.text(0.02, 0.95, "FPS mod", size=16)
    engine.hud.render()
```

See [[Writing Packages]].
