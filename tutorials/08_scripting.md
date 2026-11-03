---
title: Your own game rules (Python)
level: Intermediate
minutes: 25
template: coin_collector
summary: Blueprints in the scripts folder — the coin collector explained.
---
# Your own game rules with Python

Behaviour in Heaven3D lives in **Blueprints**: small Python classes you attach to objects. Create the **Coin Collector** practice project and open `scripts/coins.py` in any text editor (VS Code is free and great).

## Scripts load automatically

Every `.py` file in a project's **scripts** folder is loaded when the editor or the game starts. Blueprints defined there show up after loading and are saved with your level.

## The lifecycle

- `on_begin_play(self)` — once, when the game starts
- `on_tick(self, dt)` — every frame (about 60 times a second); `dt` is the time since the last frame
- `on_message(self, event, data)` — when any code calls `send_message`
- `on_collision_enter(self, other, contact)` — this object hit something solid
- `on_end_play(self)` — when you press Stop

## A minimal blueprint

```
import glm
from Heaven3D import Blueprint, blueprint

@blueprint
class Spinner(Blueprint):
    def on_tick(self, dt):
        self.node.rotation_euler.y += 90 * dt     # degrees per second
```

Save it as `scripts/spinner.py`. To attach it in code:

```
eng.attach_blueprint(node, Spinner)
```

> WATCH OUT: Always put `@blueprint` above the class — that registers it so it can be saved with the level.

## Inside a blueprint

- `self.node` — the object: `.position`, `.rotation_euler`, `.scale`, `.visible`, `.material`
- `self.engine.audio.play("pickup")` — built-in sounds: shoot, shotgun, hit, explosion, footstep, jump, land, pickup, reload, hurt, enemy, click, heal
- `self.engine.hud.text(x, y, "Hi", size=24)` — text on screen (call every frame)
- `self.engine.first_blueprint("FPSController")` — find the player
- `self.engine.send_message("event", data)` — tell every blueprint something happened
- `self.engine.input.key("e")` / `was_key_pressed("e")` — keyboard

> TIP: To detect the player touching something, measure the distance in `on_tick` — exactly like the Coin blueprint does. It's the most reliable method.

## Challenges

1. Make the coins spin faster (change `120 * dt`).
2. Give the player 90 seconds instead of 60 (`TIME_LIMIT`).
3. Play the "jump" sound when a coin is collected.
