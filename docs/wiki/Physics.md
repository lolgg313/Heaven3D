# Physics

Heaven3D has its own CPU physics engine — no Bullet, no PhysX, no extra dependency. Hot paths are accelerated with Numba when it's installed and fall back to plain Python with **bit-identical** results when it isn't.

## Concepts

Every scene object can have two physics parts:

- a **Collider** — its shape. A collider **without** a RigidBody is static level geometry.
- a **RigidBody** — makes it dynamic (physics moves it) or kinematic (your code moves it).

`add_cube`, `add_sphere` and `add_plane` give objects a matching collider by default, so scenes are solid the moment you press Play.

## Colliders

| Type | Inspector | Notes |
| --- | --- | --- |
| **Box** | + Box | On imported models it fits the **whole** model. |
| **Sphere** | + Sphere | |
| **Capsule** | + Capsule | Used by characters. |
| **Plane** | + Plane | Infinite, or limited by `size`. |
| **Heightfield** | (terrain) | Created automatically for terrain and islands. |
| **Mesh** | + Mesh (exact triangles) | Collides with every rendered triangle of every part, so buildings with interiors just work. A BVH rebuilds automatically when the mesh or transform changes. **Static / kinematic only** — a dynamic body with a mesh collider falls back to a bounding sphere. |

Common collider fields: `center`, `is_trigger`, `friction` (default 0.6), `restitution` (default 0). Colliders scale with their node.

**Edit Shape** in the Inspector shows a green outline you can drag in the viewport; **Size…** lets you type exact values.

## RigidBody

| Field | Default | Inspector | Meaning |
| --- | --- | --- | --- |
| `body_type` | `"dynamic"` | Body | `dynamic`, `kinematic` or `static` |
| `mass` | `1.0` | Mass | kg; heavy objects push light ones |
| `gravity_scale` | `1.0` | Gravity | 1 normal, 0 floating, 0.3 moon-like |
| `friction` | collider's | Friction | override (ice ≈ 0.05) |
| `restitution` | collider's | Bounce | override (rubber ball ≈ 0.9) |
| `linear_damping` | `0.02` | | velocity bleed per second |
| `angular_damping` | `0.05` | | spin bleed per second |
| `freeze_rotation` | `false` | Freeze Rot | stops it tipping over |

Runtime state: `velocity`, `angular_velocity`, `position`, `orientation`. Use `rb.add_force(vec3)` to push a body (it wakes its island).

### Triggers

Turn **Trigger ON** (or `collider.is_trigger = True`) and things pass through it. Blueprints on the node receive `on_trigger_enter(other)` / `on_trigger_exit(other)` instead of collision callbacks.

## The solver

- **Fixed-step** simulation (60 Hz with 2 substeps and 4 solver iterations by default; the *Physics Plus* package offers 120 Hz presets).
- **Sequential-impulse** solver with friction and restitution.
- **Sweep-and-prune** broadphase.
- **Persistent contact manifolds**.
- Known limit: boxes stacked more than two high keep a slight tremble, sink a little into each other and don't fall asleep. Keep stacks low, or use one tall box.
- **Simulation islands**: resting groups sleep and wake when touched or pushed.
- World gravity is `(0, -9.81, 0)` — change it with `engine.physics.gravity`.

## Collision events

Blueprints on either object receive (see [Scripting API Reference](Scripting-API-Reference#blueprint)):

```python
def on_collision_enter(self, other, contact): ...   # contact.point / .normal / .depth / .impulse  (or contact["point"] ...)
def on_collision_stay(self, other, contact): ...
def on_collision_exit(self, other): ...
def on_trigger_enter(self, other): ...
def on_trigger_exit(self, other): ...
```

`contact.normal` points from `other` toward `self.node`.

Events go to the blueprints on the two objects. A pair in which neither object has a blueprint has no one to tell, so no event is recorded for it; set `engine.physics.events_for_all = True` if you read `engine.physics.events` yourself and need every pair.

## Queries

```python
hit = engine.raycast(origin, direction, max_dist=100.0, ignore={player_node})
if hit:
    print(hit.node.name, hit.point, hit.normal, hit.distance)

entries = engine.physics.overlap_sphere(center, radius=3.0)
```

`raycast` ignores triggers unless you pass `hit_triggers=True`. Heightfield raycasts run as a Numba kernel (~0.01 ms per ray).

## Character controller

`CharacterController` is a kinematic capsule with sliding, slopes, step-offset and jumping. It's what `FPSController`, `EnemyAI` and the MakeHuman NPCs use.

```python
cc = engine.make_character(node, radius=0.35, height=1.8)
moved = cc.move(glm.vec3(0, 0, -5) * dt)    # returns the displacement actually applied
feet = cc.feet_position()
```

The FPS player uses a 50° slope limit and a 0.45 m step offset. Walking up slopes moves at the same speed as on flat ground, and walking downhill stays glued to the ground.

## Code examples

```python
# A bouncy ball
ball = engine.add_sphere(radius=0.5, name="Ball")
ball.position = glm.vec3(0, 8, 0)
engine.add_rigidbody(ball, mass=1.0, restitution=0.8)

# A building you can walk into
house = engine.load_model("assets/house.glb")
engine.add_mesh_collider(house)

# A sensor zone
zone = engine.add_cube(name="Zone")
zone.visible = False
zone.collider.is_trigger = True
```

## Performance notes

- Node transforms and collider shapes are cached, and each character checks the colliders around it once per move — a 12-character crowd costs about 3.4 ms of physics per frame.
- Numba kernels compile or load in a background thread while the game loads, so there are no first-use hitches. Set `HEAVEN3D_NO_WARMUP=1` to skip the warm-up.
- Without Numba, the caches still make physics roughly 6× faster than before 2.7.3.
- With Numba the pair search, the box-box and box-plane contacts and the contact solver are compiled kernels that give the same results as the Python code (`HEAVEN3D_NO_PHYS_KERNELS=1` switches them off). Measured on one machine: 400 boxes at rest 12.9 → about 1.5 ms a step, 400 boxes settling 26 → 15 ms, a moving pile of 300 bodies 132 → 37 ms.
- Bodies at rest sleep and cost almost nothing, so many loose crates are cheap; a large pile that keeps moving is what costs.
- Use Box / Sphere / Capsule for anything that moves; keep Mesh colliders for static geometry.
