---
title: Physics
level: Intermediate
minutes: 15
template: empty
summary: Colliders, rigid bodies, triggers, bounce and friction.
---
# Physics

Every object can have two physics parts. Select an object and scroll to **Physics** in the Inspector.

## Collider — the shape

**+ Box**, **+ Sphere**, **+ Capsule**, **+ Plane** or **+ Mesh (exact triangles)**. Click **Edit Shape** to drag the green outline, or **Size…** to type exact values.

## RigidBody — "gravity affects me"

Click **Add RigidBody**. Without one, the object is a fixed part of the level.

- **Body** — Dynamic (physics moves it), Kinematic (code moves it), Static (never moves)
- **Mass** — heavy objects push light ones
- **Gravity** — 1 normal, 0 floating, 0.3 moon
- **Friction / Bounce** — ice: friction 0.05; rubber ball: bounce 0.9
- **Trigger ON** — things pass through it (zones, pickups)
- **Freeze Rot** — stops it from tipping over

## Try it

1. Add a Sphere and move it to Y 8.
2. Click **Add RigidBody** and set **Bounce** to 0.8.
3. Press Play and watch it bounce.

> TIP: A Mesh collider is for static or kinematic objects. A dynamic body with a Mesh collider falls back to a bounding sphere — use Box/Sphere/Capsule for things that move.
