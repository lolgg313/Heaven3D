"""Physics Plus - simulation quality + gravity presets.

Shows the package API in ~40 lines:  h3d.settings / save_settings(),
h3d.add_tool() and the "init" event.  Add this package to a project and the
chosen quality ships with the exported game (settings.json lives in the
project's packages/physics_plus folder)."""

QUALITY = {                 # steps per second, substeps, solver iterations
    "Normal": (60, 2, 4),   # the engine's default
    "High": (120, 2, 8),
    "Ultra": (120, 4, 14),
}
GRAVITY = {"Earth": -9.81, "Mars": -3.71, "Moon": -1.62, "Low": -4.0}


def register(h3d):
    W = h3d.module.PhysicsWorld
    glm = h3d.module.glm

    def apply_quality(name):
        hz, sub, it = QUALITY.get(name, QUALITY["High"])
        W.FIXED_DT = 1.0 / hz
        W.SUBSTEPS = sub
        W.SOLVER_ITERATIONS = it
        h3d.settings["quality"] = name
        q_tool["label"] = f"Physics quality: {name}"

    def apply_gravity(engine, name):
        h3d.settings["gravity"] = name
        g_tool["label"] = f"Gravity: {name}"
        if engine is not None and getattr(engine, "physics", None) is not None:
            engine.physics.gravity = glm.vec3(0.0, GRAVITY.get(name, -9.81), 0.0)

    def cycle(table, key, apply):
        names = list(table)
        cur = h3d.settings.get(key, names[0])
        nxt = names[(names.index(cur) + 1) % len(names)] if cur in names else names[0]
        apply(nxt)
        h3d.save_settings()
        h3d.log(f"{key} -> {nxt}")

    q_tool = h3d.add_tool("Physics quality", lambda engine: cycle(QUALITY, "quality", apply_quality))
    g_tool = h3d.add_tool("Gravity", lambda engine: cycle(GRAVITY, "gravity",
                                                          lambda n: apply_gravity(engine, n)))
    apply_quality(h3d.settings.get("quality", "High"))
    apply_gravity(None, h3d.settings.get("gravity", "Earth"))
    h3d.on("init", lambda engine: apply_gravity(engine, h3d.settings.get("gravity", "Earth")))
    h3d.on("play_start", lambda engine: apply_gravity(engine, h3d.settings.get("gravity", "Earth")))
