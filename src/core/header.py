# @heaven3d-part core/header.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
  Heaven3D  v2  —  A complete 3D game engine built from the metal up
================================================================================

  Stack  : Python 3.10+ | ModernGL 5.12 (OpenGL 4.6) | pygame | pyglm | numpy | numba

      pip install moderngl pygame pyglm numpy numba

  ---------------------------------------------------------------------------
  WHAT THIS IS
  ---------------------------------------------------------------------------
  A from-scratch 3D game engine.  ModernGL gives raw access to the OpenGL
  Core profile so we can build a real modern pipeline:

      * Deferred renderer with a G-buffer (positions / normals / albedo /
        material / full-colour emissive)
      * HDR float framebuffers + ACES filmic tone mapping
      * PBR materials (Cook-Torrance GGX, metallic-roughness) with editable
        emission + a forward transparency pass for true alpha blending
      * Inspector-driven physics: assign colliders & rigid bodies to any
        mesh straight from the properties panel
      * Real SSAO, screen-space reflections, bloom, PCF soft shadows
      * FXAA 3.11 (full Quality path) stacked with hardware MSAA (x4)
      * Procedural day/night sky + animated EEVEE-quality volumetric-style
        clouds that ship with every scene by default (just like the Sun)

  Panda3D / Unity / Unreal / GameGuru are NOT used.  Everything here is ours.

  ---------------------------------------------------------------------------
  TWO FILES
  ---------------------------------------------------------------------------
      Heaven3D.py   — the engine (this file)
      YourGame.py   — a sample game demonstrating the full pipeline

  ---------------------------------------------------------------------------
  EDITOR MODE
  ---------------------------------------------------------------------------
      python YourGame.py            # launches the editor

  STANDALONE RUNTIME ("old guys old tricks")
  ---------------------------------------------------------------------------
      python Heaven3D.py --runtime  # boots game.heaven3d in same folder,
                                    # no editor UI, no gizmos — pure runtime

  Or build a real .exe later with PyInstaller:
      pyinstaller --onefile Heaven3D.py

  ---------------------------------------------------------------------------
  GAMEPLAY SYSTEMS  (v2.5 — pure CPU, zero extra dependencies)
  ---------------------------------------------------------------------------
      * CPU physics engine: rigid bodies (box / sphere / capsule / plane /
        heightfield / triangle-mesh colliders — the mesh shape collides
        with the node's exact triangles, for low-poly buildings with
        interiors), sequential-impulse solver, friction + restitution,
        raycasts, overlap queries, triggers, collision events, fixed-step
        simulation, and a kinematic CharacterController (slide, slopes,
        step-offset, jump) for first/third-person games.
      * Audio: engine.audio.play("shoot") — 13 built-in synthesised sounds,
        your own .wav/.ogg files, 3D positional audio, music.  Silent-safe
        on machines with no audio device.
      * HUD: engine.hud — crosshair, bars, text, vignette (in-game only;
        the editor UI is untouched).
      * Ready-made FPS blueprints (attach & play):
            FPSController · Weapon · Projectile · Health · EnemyAI ·
            Pickup · Spawner · MovingPlatform · TriggerZone

      Minimal FPS:

          eng = HeavenEngine(mode="runtime")
          ground = eng.add_cube(size=1.0, name="Ground")   # solid by default
          ground.scale = glm.vec3(50, 1, 50)   # collider scales with node
          player = SceneNode("player", parent=eng.scene.root)
          player.position = glm.vec3(0, 1, 0)
          eng.attach_blueprint(player, FPSController)
          eng.attach_blueprint(player, Weapon)
          eng._begin_play(); eng.run()

================================================================================
"""
