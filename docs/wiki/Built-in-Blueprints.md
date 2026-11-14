# Built-in Blueprints

Heaven3D ships ready-made gameplay **blueprints** (behaviours) you can attach to any object and tune from the Inspector, no code required. Most of them are created for you by the editor's **Library** objects.

All built-in blueprints keep their values in `properties` and fall back to class defaults, so older saves never break.

| Blueprint | Created by | Purpose |
| --- | --- | --- |
| [FPSController](#fpscontroller) | Player Spawn | first-person player |
| [Weapon](#weapon) | Player Spawn | hitscan gun |
| [Projectile](#projectile) | code | grenade / rocket |
| [Health](#health) | code / Inspector | destructible objects |
| [EnemyAI](#enemyai) | code / Inspector | walking melee or hitscan enemy |
| [EnemyFlyShooter](#enemyflyshooter) | Enemy Spawn | flying drone |
| [Pickup](#pickup) | Bullet / Health Pickup | health, ammo or custom event |
| [LevelEndTrigger](#levelendtrigger) | Trigger Point | finish line |
| [Spawner](#spawner) | code / Inspector | clone a prefab at intervals |
| [MovingPlatform](#movingplatform) | code / Inspector | ping-pong platform |
| [TriggerZone](#triggerzone) | code / Inspector | box volume that broadcasts events |

---

## FPSController

Complete first-person player. Attach it to an empty node where the player should spawn; a capsule collider is created automatically.

**Controls:** W A S D move · Shift sprint · Space jump · mouse look (pointer lock; Esc releases, click re-captures).

| Property | Default |
| --- | --- |
| `move_speed` | 6.0 |
| `sprint_multiplier` | 1.65 |
| `jump_height` | 1.15 |
| `gravity` | 22.0 |
| `mouse_sensitivity` | 0.13 |
| `capsule_radius` / `capsule_height` | 0.35 / 1.8 |
| `eye_height` | 1.62 |
| `max_health` | 100 |
| `respawn_on_death` / `respawn_delay` | true / 2.5 s |
| `head_bob` | true |
| `footstep_sound` | `"footstep"` |
| `show_hud` | true |

The **Player Spawn**'s Inspector values map onto it: *Health Value*, *Bullet Value*, *Walk Speed*, *Run Speed*, *Cam Rot Speed* (mouse sensitivity). Jumping has coyote time and a jump buffer.

## Weapon

Hitscan gun with ammo, reload, spread/bloom, recoil and muzzle flash. Attach to the same node as the `FPSController` (or a child); it always aims through the gameplay camera. **Left mouse** fires, **R** reloads.

| Property | Default | | Property | Default |
| --- | --- | --- | --- | --- |
| `damage` | 25 | | `shoot_sound` | `"shoot"` |
| `fire_rate` | 8 /s | | `hit_sound` | `"hit"` |
| `spread_deg` | 0.6 | | `reload_sound` | `"reload"` |
| `range` | 150 m | | `empty_sound` | `"click"` |
| `magazine` | 30 | | `camera_kick` | 0.5 |
| `reserve_ammo` | 120 | | `impact_impulse` | 6.0 |
| `reload_time` | 1.4 s | | `muzzle_flash` | true |
| `auto` | true | | `show_crosshair`, `show_hud` | true |

Hits send a `damage` message to the object hit, push rigid bodies and leave bullet-hole decals (`engine.spawn_impact_fx`).

## Projectile

Arced explosive (grenade / rocket). The node's forward direction at spawn is the launch direction. It raycasts every frame so it never tunnels through walls.

| `speed` 26 | `gravity` 8 | `damage` 45 | `blast_radius` 4 m | `lifetime` 6 s | `boom_sound` `"explosion"` | `owner_name` `""` |
| --- | --- | --- | --- | --- | --- | --- |

## Health

Generic destructible-object health. Listens for `damage` and `heal` messages (sent by Weapon, Projectile, Pickup and package weapons). On death it can hide, destroy or explode the node and optionally respawn it, and it broadcasts `died` with `{"node": node}`.

| Property | Default |
| --- | --- |
| `max_health` | 100 |
| `hide_on_death` | true |
| `destroy_on_death` | false |
| `respawn` / `respawn_delay` | false / 4 s |
| `death_sound` | `"hurt"` |
| `explode_on_death` | false |
| `blast_damage` / `blast_radius` | 40 / 3.5 m |

## EnemyAI

A walking enemy: patrols near home, chases the player inside `detect_range`, attacks inside `attack_range`. Uses a capsule character controller (gravity and walls handled). Pair with **Health** for damage/death; without one it tracks its own HP.

| Property | Default |
| --- | --- |
| `move_speed` | 3.4 |
| `detect_range` | 26 m |
| `attack_range` | 2.1 m |
| `attack_damage` / `attack_cooldown` | 12 / 1.2 s |
| `attack_style` | `"melee"` or `"hitscan"` |
| `hitscan_range` | 22 m |
| `patrol_radius` | 7 m |
| `require_los` | false |
| `max_health` | 60 (when no Health blueprint) |
| `capsule_radius` / `capsule_height` | 0.4 / 1.7 |
| `growl_sound` | `"enemy"` |
| `gravity` | 22 |

For realistic animated human enemies, use `MHEnemy` from [[MakeHuman Characters]].

## EnemyFlyShooter

The flying drone created by **Enemy Spawn**. A soccer-ball-sized sphere (or the spawn point's own mesh, via **Custom mesh...**) that hovers, hunts the player, turns to track them and fires bullets. Only level geometry blocks its line of sight.

| Property | Inspector label | Default |
| --- | --- | --- |
| `max_health` | Health Value | 100 |
| `bullet_damage` | Bullet Value | 10 |
| `fly_speed` | Fly Speed | 4 |
| `turn_speed` | Camera Rotation Speed (°/s) | 240 |
| `shots_per_sec` | Shooting Speed / Shots / Sec | 1.5 |
| `detect_range` | | 60 m |
| `attack_range` | | 30 m |
| `keep_distance` | | 5 m |
| `collider_radius` | | 0.11 m |
| `bullet_speed` | | 16 |

## Pickup

Trigger pickup. A trigger sphere is created automatically. When the player touches it, it applies its effect, plays a sound, hides, and respawns after `respawn_time` (0 = never).

| Property | Default |
| --- | --- |
| `kind` | `"health"`, `"ammo"` or `"event"` |
| `amount` | 30 |
| `event_name` | `"picked_up"` (for `kind = "event"`) |
| `respawn_time` | 15 s |
| `sound` | `"pickup"` |
| `spin` / `bob` | true / true |
| `trigger_radius` | 1.1 m |

Bullet and Health Pickups expose the amount as *Bullet Count* / *Health Count*.

## LevelEndTrigger

The invisible 1 × 1 × 1 volume created by **Trigger Point**. When the player crosses it the game stops and the text appears in the middle of the screen.

| `text` | `color` | `font_size` | `sound` |
| --- | --- | --- | --- |
| `"LEVEL COMPLETE"` | `[1.0, 0.85, 0.2]` | 64 | `"heal"` |

## Spawner

Clones a template node (usually an invisible enemy prefab) at intervals until `max_alive` of its clones are alive.

| `template_name` | `interval` | `max_alive` | `spawn_radius` | `start_delay` | `sound` |
| --- | --- | --- | --- | --- | --- |
| `"Enemy"` | 4 s | 5 | 2 m | 1 s | `""` |

## MovingPlatform

Kinematic ping-pong platform with smooth easing at both ends. Players standing on it are carried by the contact solver.

| `offset` | `period` |
| --- | --- |
| `[0, 3, 0]` (metres from the start) | 5 s |

## TriggerZone

Box trigger volume. Broadcasts `"<event_name>_enter"` and `"<event_name>_exit"` messages when the player (or anything, if `player_only` is off) walks in or out. Great for doors, wave triggers and cutscenes.

| `size` | `event_name` | `once` | `player_only` |
| --- | --- | --- | --- |
| `[2, 2, 2]` | `"zone"` | false | true |

```python
@blueprint
class Door(Blueprint):
    def on_message(self, event, data):
        if event == "vault_enter":
            self.node.visible = False
```

---

## Using built-ins from code

```python
from Heaven3D import FPSController, Weapon, Health, EnemyAI

player = SceneNode("player", parent=engine.scene.root)
player.position = glm.vec3(0, 1, 0)
engine.attach_blueprint(player, FPSController, {"move_speed": 7.5})
engine.attach_blueprint(player, Weapon, {"damage": 40, "magazine": 12, "auto": False})

crate = engine.add_cube(name="Barrel")
engine.attach_blueprint(crate, Health, {"max_health": 50, "explode_on_death": True})
```

Messages they understand and send are listed on [Scripting API Reference](Scripting-API-Reference#messages).
