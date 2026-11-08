# @heaven3d-part engine/engine.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# #############################################################################
# SECTION 15 —  ENGINE  (window + main loop + editor/runtime modes)
# #############################################################################

class HeavenEngine:
    """
    The top-level engine.  Owns the pygame window, the moderngl context,
    the scene, camera, renderer, and the editor UI.  In 'runtime' mode the
    editor UI / gizmos are skipped.

    Usage:
        engine = HeavenEngine(mode='editor')
        engine.run()
    """

    def __init__(self, mode: str = "editor",
                 width: int = 1690, height: int = 950,
                 project_path: Optional[str] = None):
        self.mode = mode
        self.width = int(width)
        self.height = int(height)
        self.project_path = project_path
        try:
            _h3d_start_numba_warmup()
        except Exception:
            pass

        pygame.init()
        pygame.display.set_mode((self.width, self.height),
                                flags=OPENGL | DOUBLEBUF)
        pygame.display.set_caption(f"Heaven3D v{HEAVEN_VERSION}")
        self.ctx = moderngl.create_context()
        # Ask for a reasonably modern context
        try:
            self.ctx.gc_mode = "auto"
        except Exception:
            pass

        # Core state
        self.scene = Scene()
        self.camera = Camera(position=(8, 6, 12), yaw=-35, pitch=-25,
                             aspect=self.width / float(self.height))
        self.input = InputState()
        self.renderer = Renderer(self.ctx, self.width, self.height)

        # ---- Game systems (CPU physics, audio, HUD, scheduling) ----
        # These exist in BOTH editor and runtime mode and run whenever the
        # game is playing (editor: after pressing Play; runtime: always).
        self.physics = PhysicsWorld()
        self.audio = AudioSystem()
        self.hud = HUDRenderer(self.ctx, self.width, self.height)
        self.time = 0.0                      # seconds of gameplay elapsed
        self._scheduler: List[tuple] = []    # (fire_time, fn, args, kwargs)
        self._destroy_queue: List[SceneNode] = []
        # Gameplay camera/mouse control (used by FPSController & friends):
        #   camera_locked  → the editor fly-camera stops consuming input
        #   capture_mouse  → pointer grab + hidden cursor while playing
        self.camera_locked = False
        self.capture_mouse = False
        self._capture_suspended = False      # ESC pressed during play
        self._mouse_captured = False

        # Default Unity-style scene: camera (Sun is now a SceneNode created by Scene)

        # Editor-only state
        self.selected: Optional[SceneNode] = None
        self.gizmo: Optional[Gizmo] = None
        self.ui: Optional[UIRenderer] = None
        self._console_lines: List[str] = []
        Log.set_console(self._log_to_console)
        self._playing = False
        self._blueprints: List[Blueprint] = []
        self._last_mouse = (0, 0)
        self._fps = 0.0
        self._fps_accum = 0.0
        self._fps_frames = 0
        self._fps_timer = 0.0

        self.editor_grid: Optional[EditorGrid] = None
        self.light_gizmos: Optional[LightGizmos] = None
        # Spawn-point debug outlines (player capsule / enemy sphere / pickup
        # cubes) — editor-only, drawn for every spawn node, hidden in Play.
        self.spawn_gizmos: Optional[SpawnGizmos] = None
        # Runtime spawn-system bookkeeping (Play-mode activation / cleanup).
        self._spawn_runtime_bps: List[Blueprint] = []
        self._spawn_runtime_nodes: List[SceneNode] = []
        self._spawn_snapshot: Dict[SceneNode, tuple] = {}
        self._play_hidden_meshes: List[tuple] = []
        self._enemy_bullet_mesh = None
        self._enemy_bullet_mat = None
        # Trigger Point end-screen: {"text", "color", "font_size", "t"} —
        # drawn centred on screen after the game stops, for a few seconds.
        self._end_screen: Optional[dict] = None
        # Deferred play-stop (a Trigger Point fired this frame) — processed
        # at the end of the gameplay section, never mid-callback.
        self._stop_play_requested = False
        # Screen rect of the toolbar Play/Stop button (set every frame by
        # _build_ui) — the ONLY clickable UI control while playing.
        self._play_stop_rect: Optional[UIRect] = None
        # Blender-style view gizmo (navball, top-right of the 3D viewport).
        # Editor-only: stays None in the standalone runtime build.
        self.view_gizmo: Optional[ViewGizmo] = None

        if self.mode == "editor":
            self.gizmo = Gizmo(self.ctx)
            self.ui = UIRenderer(self.ctx, self.width, self.height)
            # Infinite editor grid (editor-only; hidden at runtime + during Play)
            self.editor_grid = EditorGrid(self.ctx)
            # Unity-style light debug outlines (editor-only, selection-driven)
            self.light_gizmos = LightGizmos(self.ctx)
            # Spawn-point outlines: player capsule + enemy/pickup markers
            self.spawn_gizmos = SpawnGizmos(self.ctx)
            # Unity-style collider outline + grab-handle shape editor
            # (driven by the Inspector's "Edit Shape" button)
            self.shape_editor = ColliderShapeEditor(self.ctx)
            self.shape_editor.on_change = self._physics_reregister
            # Blender-style view gizmo (axis navball) — created ONLY here,
            # in editor mode; the runtime branch below never makes one.
            self.view_gizmo = ViewGizmo(self.ctx)
        else:
            # Runtime mode: no editor overlays.
            self.shape_editor: Optional[ColliderShapeEditor] = None
            # Runtime mode: no view gizmo either — pure game view.
            self.view_gizmo: Optional[ViewGizmo] = None
            # Runtime mode: auto-load game.heaven3d from cwd or project_path.
            # "Old guys old tricks" — drop the engine next to the project and
            # it boots the game with no editor UI.
            auto_path = self.project_path
            if auto_path is None:
                auto_path = os.path.join(os.getcwd(), "game.heaven3d")
            if os.path.isfile(auto_path):
                ProjectSerializer.load(auto_path, self.scene, self)
                # Begin play immediately in runtime mode
                self._begin_play()
            else:
                Log.warn(f"Runtime: no project found at {auto_path}")

        Log.info(f"Heaven3D v{HEAVEN_VERSION} started in '{self.mode}' mode "
                 f"(OpenGL {self.ctx.version_code // 100}.{self.ctx.version_code % 100})")

    # ------------------------------------------------------------------ run
    def run(self):
        clock = pygame.time.Clock()
        running = True
        last_time = pygame.time.get_ticks() / 1000.0
        while running:
            # ---- events ----
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    self.input.set_key(pygame.key.name(event.key), True)
                    self._on_key(event.key)
                elif event.type == pygame.KEYUP:
                    self.input.set_key(pygame.key.name(event.key), False)
                elif event.type == pygame.MOUSEBUTTONDOWN:
                    self._on_mouse_down(event.button, event.pos)
                elif event.type == pygame.MOUSEBUTTONUP:
                    self._on_mouse_up(event.button, event.pos)
                elif event.type == pygame.MOUSEMOTION:
                    self.input.mouse_pos = event.pos
                    self.input.mouse_delta = (event.rel[0], event.rel[1])
                    # While a scrollbar thumb is held, mouse motion scrolls
                    # the panel (and is not seen by anything else).
                    if (self.ui is not None and self.mode == "editor"
                            and self.ui.scrollbar_dragging):
                        ux = event.pos[0] / float(self.width)
                        uy = 1.0 - event.pos[1] / float(self.height)
                        self.ui.scrollbar_mouse_drag(ux, uy)
                elif event.type == pygame.MOUSEWHEEL:
                    # Route scroll to whichever UI panel is under the cursor.
                    # If the UI consumed it (cursor inside a scrollable panel
                    # OR any UI panel), the wheel must NOT also reach the
                    # camera — scrolling a panel never dollies the 3D view.
                    wheel_consumed = False
                    if self.ui is not None and self.mode == "editor":
                        mx = self.input.mouse_pos[0] / float(self.width)
                        my = 1.0 - self.input.mouse_pos[1] / float(self.height)
                        wheel_consumed = self.ui.scroll_wheel(mx, my, event.y)
                    if not wheel_consumed:
                        self.input.wheel = event.y

            # modifier keys
            mods = pygame.key.get_mods()
            self.input.shift = bool(mods & pygame.KMOD_SHIFT)
            self.input.alt = bool(mods & pygame.KMOD_ALT)
            self.input.ctrl = bool(mods & pygame.KMOD_CTRL)
            # Keep non-modal tkinter editor windows (the Library popup)
            # responsive without freezing the OpenGL viewport.
            self._pump_tkinter_events()

            # ---- update ----
            now = pygame.time.get_ticks() / 1000.0
            dt = min(now - last_time, 0.1)
            last_time = now
            self.time += dt

            # camera (frozen while a gameplay blueprint owns it, e.g. FPS)
            if not self.camera_locked:
                self.camera.update(dt, self.input)
            # gizmo drag — project mouse motion onto the active axis/plane
            if (self.mode == "editor" and self.gizmo is not None
                    and self.gizmo.dragging and self.input.lmb):
                mx, my = self.input.mouse_pos
                ro, rd = self._mouse_ray((mx, my))
                self.gizmo.update_drag(ro, rd, self.camera)
            # collider shape editor — drag update / hover highlight
            if (self.mode == "editor" and self.shape_editor is not None
                    and self.shape_editor.active and not self._playing):
                mx, my = self.input.mouse_pos
                ro, rd = self._mouse_ray((mx, my))
                if self.shape_editor.dragging and self.input.lmb:
                    self.shape_editor.update_drag(ro, rd, self.camera)
                else:
                    self.shape_editor.update_hover(ro, rd, self.camera)
            # Blender-style view gizmo: hover highlight, drag-to-orbit and
            # the eased axis-snap animation — edit time only, like Blender.
            if (self.mode == "editor" and not self._playing
                    and self.view_gizmo is not None):
                self.view_gizmo.update_hover(self.input.mouse_pos,
                                             self.width, self.height,
                                             self.camera)
                if self.view_gizmo.pressing and self.input.lmb:
                    self.view_gizmo.update_press(self.input.mouse_pos,
                                                 self.input.mouse_delta,
                                                 self.camera)
                self.view_gizmo.update(dt, self.camera)

            if self._playing:
                # 1) scheduled one-shot callbacks (respawns, flash lights)
                self._process_scheduler()
                # 2) gameplay scripts (they steer the world via input/AI)
                for bp in list(self._blueprints):
                    if bp.node is None:
                        continue
                    try:
                        bp.on_tick(dt)
                    except Exception as ex:
                        Log.error(f"Blueprint tick error in "
                                  f"{bp.__class__.__name__}: {ex}")
                # 3) CPU physics: fixed-step simulation + collision events
                self.physics.step(dt)
                self._flush_physics_events()
                # 4) deferred node destruction (safe after all callbacks)
                self._process_destroy_queue()
                # 5) default audio listener follows the camera unless a
                #    blueprint placed it explicitly this frame
                self.audio.set_listener(self.camera.position,
                                        self.camera.forward(),
                                        self.camera.right())
                # 6) a Trigger Point asked to stop the game — do it now,
                #    safely outside every callback / physics pass.
                if self._stop_play_requested:
                    self._stop_play_requested = False
                    self.toggle_play()

            self._apply_mouse_capture()

            # ---- render ----
            self.renderer.render(self.scene, self.camera)
            # [TRIPLEA:p3_outline_hook] Unity-style orange selection outline
            # (inverted hull).  Implemented in the appended patch block; a
            # no-op outside editor mode and during Play.
            self._render_selection_outline()
            # In-game HUD (crosshair / health / ammo).  Only drawn while
            # playing — the editor UI is untouched and still draws on top.
            if self._playing and self.hud is not None:
                try:
                    self.hud.render()
                except Exception as ex:
                    Log.error(f"HUD render error: {ex}")
            # Trigger Point end-screen: big centred message drawn on top of
            # everything (even after Play has stopped), fading out after a
            # few seconds.
            if self._end_screen is not None and self.hud is not None:
                es = self._end_screen
                age = self.time - es["t"]
                if age > 10.0:
                    self._end_screen = None
                else:
                    alpha = 1.0 if age < 8.0 else max(0.0, (10.0 - age) / 2.0)
                    col = (es["color"][0], es["color"][1], es["color"][2],
                           alpha)
                    try:
                        self.hud.rect(0.0, 0.415, 1.0, 0.17,
                                      (0.0, 0.0, 0.0, 0.45 * alpha))
                        ty = 0.5 - (es["font_size"] / max(self.height, 1)) \
                            * 0.55
                        self.hud.center_text(ty, es["text"],
                                             size=es["font_size"], color=col)
                        self.hud.render()
                    except Exception as ex:
                        Log.error(f"End-screen render error: {ex}")
            # Editor-only overlay: the infinite ground grid.  Hidden at
            # runtime and during Play so it doesn't appear in the game.
            if (self.mode == "editor" and not self._playing
                    and self.editor_grid is not None):
                self.editor_grid.render(self.camera)
            # Unity-style light outlines: show the radius / cone of any
            # selected point or spot light (editor-time only, never in Play).
            if (self.mode == "editor" and not self._playing
                    and self.light_gizmos is not None):
                self.light_gizmos.render(self.camera, self.selected)
            # Spawn-point markers (player capsule outline, enemy sphere,
            # pickup cubes) — visible at EDIT time, invisible during Play.
            if (self.mode == "editor" and not self._playing
                    and self.spawn_gizmos is not None):
                self.spawn_gizmos.render(self.camera, self.scene)
            # Unity-style collider outline: green debug wireframe for the
            # selected collider + grab handles while Edit Shape is active.
            if (self.mode == "editor" and not self._playing
                    and self.shape_editor is not None):
                self.shape_editor.render(self.camera, self.selected)
            # The transform gizmo is hidden while editing a collider shape
            # (Unity does the same in its collider edit mode).
            _shape_editing = (self.shape_editor is not None
                              and self.shape_editor.active)
            if self.gizmo and not _shape_editing:
                self.gizmo.render(self.camera)
            # Blender-style view gizmo in the top-right corner of the 3D
            # viewport — EDIT TIME ONLY: hidden during Play, and the object
            # doesn't even exist in the standalone runtime build.
            if (self.mode == "editor" and not self._playing
                    and self.view_gizmo is not None):
                self.view_gizmo.render(self.camera, self.width, self.height)
            if self.ui:
                self._build_ui()
                self.ui.render()

            pygame.display.flip()
            # Reset per-frame input
            self.input.mouse_delta = (0, 0)
            self.input.wheel = 0
            self.input.click_just_pressed = False
            self.input.end_frame()
            # Hard CPU-side framerate cap at 60 FPS.  This guarantees a
            # consistent update rate regardless of whether the GPU driver
            # honours vsync, and prevents the multi-thousand-FPS spikes that
            # starve the OS event loop.
            clock.tick(60)

            # FPS
            self._fps_accum += dt
            self._fps_frames += 1
            self._fps_timer += dt
            if self._fps_timer >= 0.5:
                self._fps = self._fps_frames / max(self._fps_accum, 1e-6)
                self._fps_timer = 0.0
                self._fps_accum = 0.0
                self._fps_frames = 0

        self.release()
        pygame.quit()

    # ------------------------------------------------------------------ input
    def _on_key(self, key):
        if key == pygame.K_ESCAPE and self._playing and self._mouse_captured:
            # During gameplay ESC frees the pointer (click to re-capture)
            # instead of deselecting / doing nothing.
            self._capture_suspended = True
            return
        if self._playing:
            # PLAY-MODE UI LOCK: every editor shortcut is frozen while the
            # game runs (no W/E/R gizmo switching, no F focus, no Delete).
            # The only hotkey that still works is Ctrl+P → Stop.
            if self.mode == "editor" and key == pygame.K_p \
                    and (pygame.key.get_mods() & pygame.KMOD_CTRL):
                self.toggle_play()
            return
        if self.mode != "editor":
            return
        if key == pygame.K_ESCAPE:
            # First ESC leaves collider Edit Shape mode, second deselects.
            if (self.shape_editor is not None
                    and self.shape_editor.active):
                self.shape_editor.exit()
                Log.info("Collider edit mode OFF")
            else:
                self.deselect()
        elif key == pygame.K_w and not (pygame.key.get_mods() & pygame.KMOD_CTRL):
            if self.gizmo: self.gizmo.set_mode(Gizmo.MOVE)
        elif key == pygame.K_e:
            if self.gizmo: self.gizmo.set_mode(Gizmo.ROTATE)
        elif key == pygame.K_r and not (pygame.key.get_mods() & pygame.KMOD_CTRL):
            if self.gizmo: self.gizmo.set_mode(Gizmo.SCALE)
        elif key == pygame.K_f:
            if self.selected:
                self.camera.frame(self.selected.world_position(), 8.0)
        elif key == pygame.K_DELETE:
            if self.selected:
                self._delete_node(self.selected)
        elif key == pygame.K_p and (pygame.key.get_mods() & pygame.KMOD_CTRL):
            self.toggle_play()

    def _on_mouse_down(self, button, pos):
        if button == 1:   # left
            self.input.lmb = True
            # UI clicks are processed with a strict priority so a click can
            # never bleed through the UI into the 3D scene:
            #   1) scrollbars (grab thumb / page on track)
            #   2) buttons (toolbar incl. Play/Stop — must work even while
            #      playing, otherwise Stop could never be reached)
            #   3) any other UI panel area → swallowed (no scene pick)
            # Only clicks that land outside every UI element reach the
            # gizmo / scene picking below.
            ux = pos[0] / float(self.width)
            uy = 1.0 - pos[1] / float(self.height)
            if self.ui and self.mode == "editor":
                if self._playing:
                    # ============ PLAY-MODE UI LOCK ============
                    # Every editor widget is FROZEN while the game runs:
                    # hierarchy, inspector, scrollbars, toolbar buttons —
                    # everything except the single Play/Stop button.
                    r = self._play_stop_rect
                    if r is not None and r.contains(ux, uy):
                        self.ui.handle_click(ux, uy)   # → Stop
                        self.input.click_just_pressed = True
                        self.input.click_pos = pos
                        return
                    if self.ui.point_in_panels(ux, uy):
                        # Locked panel area: swallow the click so it can
                        # never reach a frozen widget.
                        self.input.click_just_pressed = True
                        self.input.click_pos = pos
                        return
                    # Viewport clicks fall through to the gameplay-input
                    # branch below (weapon fire / pointer re-capture).
                else:
                    if self.ui.scrollbar_mouse_down(ux, uy):
                        self.input.click_just_pressed = True
                        self.input.click_pos = pos
                        return
                    if self.ui.handle_click(ux, uy):
                        self.input.click_just_pressed = True
                        self.input.click_pos = pos
                        return
                    if self.ui.point_in_panels(ux, uy):
                        self.input.click_just_pressed = True
                        self.input.click_pos = pos
                        return
            # Blender-style view gizmo (top-right of the 3D viewport):
            # presses on it are pure view navigation — click an axis ball to
            # snap the view, drag to orbit — and must never fall through to
            # the transform gizmo or scene picking.  Alt+click still orbits.
            if (self.view_gizmo is not None and self.mode == "editor"
                    and not self._playing and not self.input.alt
                    and self.view_gizmo.point_in_region(pos, self.width,
                                                        self.height)):
                part = self.view_gizmo.hit_test(pos, self.width, self.height,
                                                self.camera)
                self.view_gizmo.begin_press(part, pos)
                self.input.click_just_pressed = True
                self.input.click_pos = pos
                return
            if self.input.alt:
                pass  # orbit
            elif self.mode == "editor" and not self._playing:
                # Editor-only picking (disabled while playing):
                #   0) Collider shape-edit grab handles (Edit Shape mode)
                #   1) Gizmo handles (start a drag if hit)
                #   2) Scene objects (select)
                if (self.shape_editor is not None
                        and self.shape_editor.active):
                    ro, rd = self._mouse_ray(pos)
                    hid = self.shape_editor.pick(ro, rd, self.camera)
                    if hid is not None:
                        self.shape_editor.begin_drag(hid, ro, rd, self.camera)
                    # While Edit Shape is active a viewport click must NOT
                    # reselect / deselect the object (Unity behaves the same
                    # in collider edit mode) — swallow it either way.
                    self.input.click_just_pressed = True
                    self.input.click_pos = pos
                    return
                if self.gizmo and self.gizmo.visible and self.selected is not None:
                    ro, rd = self._mouse_ray(pos)
                    axis = self.gizmo.pick(ro, rd)
                    if axis is not None:
                        self.gizmo.begin_drag(axis, ro, rd, self.camera)
                        self.input.click_just_pressed = True
                        self.input.click_pos = pos
                        return
                # No gizmo hit → pick scene
                self._pick_scene(pos)
                self.input.click_just_pressed = True
                self.input.click_pos = pos
            else:
                # Runtime mode or Play in progress: the click is gameplay
                # input (Weapon fire).  Also re-captures the pointer after
                # ESC released it.
                self.input.lmb_just_pressed = True
                if self._playing and self.capture_mouse \
                        and self._capture_suspended:
                    self._capture_suspended = False
        elif button == 3:   # right
            self.input.rmb = True
            if self._playing:
                self.input.rmb_just_pressed = True
            # Right/middle drag grabs the pointer to look around in the
            # EDITOR.  While a game holds the pointer, the game owns it:
            # _apply_mouse_capture is the only thing allowed to change it.
            if not self._game_holds_pointer():
                pygame.event.set_grab(True)
                pygame.mouse.set_visible(False)
        elif button == 2:   # middle
            self.input.mmb = True
            if not self._game_holds_pointer():
                pygame.event.set_grab(True)
                pygame.mouse.set_visible(False)

    def _on_mouse_up(self, button, pos):
        if button == 1:
            self.input.lmb = False
            # End any active scrollbar drag
            if self.ui is not None and self.ui.scrollbar_dragging:
                self.ui.scrollbar_mouse_up()
            # End any active gizmo drag
            if self.gizmo and self.gizmo.dragging:
                self.gizmo.end_drag()
            # End any active collider shape-handle drag
            if self.shape_editor is not None and self.shape_editor.dragging:
                self.shape_editor.end_drag()
            # End any active view-gizmo press (a clean click snaps the view)
            if self.view_gizmo is not None and self.view_gizmo.pressing:
                self.view_gizmo.end_press(self.camera)
        elif button in (2, 3):
            if button == 3:
                self.input.rmb = False
            else:
                self.input.mmb = False
            # Releasing the right button used to free the pointer even in
            # play, so aiming down sights once cost you mouse-look for the
            # rest of the game.  In play the game keeps the pointer, and the
            # lock is taken again right here in case the OS dropped it with
            # the button release.
            if self._game_holds_pointer():
                self._mouse_regrab = 3
                try:
                    pygame.event.set_grab(True)
                    pygame.mouse.set_visible(False)
                except Exception:
                    pass
            else:
                pygame.event.set_grab(False)
                pygame.mouse.set_visible(True)

    # ------------------------------------------------------- mouse ray helper
    def _mouse_ray(self, mouse_pos) -> Tuple[glm.vec3, glm.vec3]:
        """Build a world-space ray (origin, direction) from a screen pixel."""
        mx, my = mouse_pos
        ndc_x = (mx / float(self.width)) * 2 - 1
        ndc_y = 1.0 - (my / float(self.height)) * 2
        ray_clip = glm.vec4(ndc_x, ndc_y, -1.0, 1.0)
        inv_proj = glm.inverse(self.camera.projection())
        ray_eye = inv_proj * ray_clip
        ray_eye = glm.vec4(ray_eye.x, ray_eye.y, -1.0, 0.0)
        inv_view = glm.inverse(self.camera.view())
        ray_world = inv_view * ray_eye
        ray_dir = glm.normalize(glm.vec3(ray_world.x, ray_world.y, ray_world.z))
        return self.camera.position, ray_dir

    # ----------------------------------------------------------- scene pick
    def _pick_scene(self, mouse_pos):
        """Pick the mesh under the cursor in two phases.
        (p3_pick_scene_triangles)

        1) Broad phase — the oriented bounding box (OBB) slab test collects
           EVERY hit with its entry distance, sorted front-to-back.  Each
           node's local AABB is transformed into world space by its world
           matrix; the ray is then tested with the slab method.  This
           avoids the old bounding-sphere test's worst-case behaviour where
           a large flat plane's sphere occludes every object resting on it.
        2) Narrow phase — the nearest candidates (at most 8) get a
           triangle-accurate ray test (_triplea_ray_mesh_triangles_t, using
           the CPU triangle copies from _mesh_cpu_triangles transformed to
           world space).  An OBB of a large or rotated mesh massively
           overestimates its footprint, so OBB-only picking let background
           geometry swallow clicks meant for a small object in front of it —
           including a non-physics mesh sitting in front of a physics body.
           When a mesh has no CPU triangle data its OBB distance is kept as
           the honest fallback; when it HAS triangles but the ray misses
           them all, the candidate is honestly rejected."""
        ray_origin, ray_dir = self._mouse_ray(mouse_pos)
        # [TRIPLEA:p3_pick_scene_triangles]

        hits = []
        for node, mesh, model in self.scene.traverse_meshes():
            bb_min, bb_max = mesh.bounding_box
            t = self._raycast_obb(ray_origin, ray_dir, bb_min, bb_max, model)
            if t is not None:
                hits.append((t, node, mesh, model))
        if not hits:
            self.deselect()
            return
        hits.sort(key=lambda h: h[0])

        if len(hits) == 1:
            best_node = hits[0][1]
        else:
            best_t = float("inf")
            best_node = None
            for obb_t, node, mesh, model in hits[:8]:
                # A candidate whose OBB entry is already beyond a proven hit
                # cannot possibly win — the sort makes this a safe early out.
                if obb_t >= best_t:
                    break
                tri_t = _triplea_ray_mesh_triangles_t(
                    ray_origin, ray_dir, mesh, model, best_t)
                if tri_t is False:
                    continue           # OBB hit, but missed every triangle
                eff_t = obb_t if tri_t is None else tri_t
                if eff_t < best_t:
                    best_t = eff_t
                    best_node = node
            if best_node is None:
                # Every front-runner missed its triangles or had no CPU data
                # — fall back to the OBB winner so picking never regresses.
                best_node = hits[0][1]
        if best_node is not None:
            self.select_node(best_node)
        else:
            self.deselect()

    @staticmethod
    def _raycast_obb(ro: glm.vec3, rd: glm.vec3,
                     bb_min: glm.vec3, bb_max: glm.vec3,
                     model: glm.mat4) -> Optional[float]:
        """Slab-method ray vs oriented bounding box test.
        Transforms the ray into the node's local space (cheap: one inverse
        per node, cached implicitly by pyglm) and tests against the local
        AABB.  Returns the near hit distance, or None."""
        inv = glm.inverse(model)
        local_ro = glm.vec3(inv * glm.vec4(ro, 1.0))
        # The ray direction must be transformed as a vector (w=0) and
        # re-normalised in local space.
        local_rd = glm.vec3(inv * glm.vec4(rd, 0.0))
        ln = glm.length(local_rd)
        if ln < 1e-8:
            return None
        local_rd = local_rd / ln

        tmin = -math.inf
        tmax = math.inf
        for i in range(3):
            d = local_rd[i]
            o = local_ro[i]
            lo = bb_min[i]
            hi = bb_max[i]
            if abs(d) < 1e-8:
                # Ray parallel to this slab — must be inside it
                if o < lo or o > hi:
                    return None
            else:
                t1 = (lo - o) / d
                t2 = (hi - o) / d
                if t1 > t2:
                    t1, t2 = t2, t1
                tmin = max(tmin, t1)
                tmax = min(tmax, t2)
                if tmin > tmax:
                    return None
        # Near hit (tmin) in local units; convert back to world distance.
        # Because we normalised local_rd, multiply by the inverse scale `ln`.
        t_world = tmin * ln if tmin >= 0 else tmax * ln
        return t_world if t_world >= 0 else None

    # ------------------------------------------------------------ selection
    def _selectable_root(self, node: SceneNode) -> SceneNode:
        """Redirect a node to its TOP-LEVEL ancestor (the direct child of
        the scene root).

        Imported models attach their sub-mesh primitives as CHILD SceneNodes
        under one model root.  Clicking a part in the viewport used to
        select that child on its own — the gizmo would then move/scale just
        the barrel or the stock, tearing the model apart.  Selection is a
        WHOLE-OBJECT operation in this editor, so any pick that lands on a
        sub-mesh climbs the parent chain to the object the user actually
        placed in the scene."""
        while (node.parent is not None
               and node.parent is not self.scene.root
               and not node.parent.name.startswith("__")):
            node = node.parent
        return node

    def select_node(self, node: SceneNode):
        if node is not None:
            node = self._selectable_root(node)
        # Switching the selection leaves any collider Edit Shape mode.
        if (self.shape_editor is not None and self.shape_editor.active
                and self.shape_editor.node is not node):
            self.shape_editor.exit()
        self.selected = node
        if self.gizmo:
            self.gizmo.set_target(node)
        Log.debug(f"Selected: {node.name}")

    def deselect(self):
        self.selected = None
        if self.gizmo:
            self.gizmo.set_target(None)

    def _delete_node(self, node: SceneNode):
        if node.is_sun:
            self.scene._sun_node = None
        if node.is_clouds:
            self.scene._clouds_node = None
        if node.is_ocean:  # [TRIPLEA:ocean_delete]
            self.scene._ocean_node = None
        # Unregister every blueprint on the node AND its descendants, so
        # play-mode ticks never fire on objects that no longer exist.
        for child in node.traverse():
            for bp in list(child.blueprints):
                if bp in self._blueprints:
                    self._blueprints.remove(bp)
            child.blueprints = []
        self.physics.remove_node(node)
        if (self.shape_editor is not None
                and self.shape_editor.node is node):
            self.shape_editor.exit()
        node.remove()
        if self.selected is node:
            self.deselect()

    def _duplicate_node(self, node: SceneNode):
        """Duplicate a node (and its whole subtree) next to itself in the
        hierarchy.

        Reuses the project serializer so EVERYTHING round-trips through the
        exact same code path as save/load: transform, primitive / GLB /
        terrain meshes, materials, blueprints and children.  That keeps
        duplicate behaviour automatically in sync with any future
        serialisable property."""
        if (node.is_sun or node.is_clouds
                or node.is_ocean):  # [TRIPLEA:ocean_dup_guard]
            Log.warn(f"Cannot duplicate the {node.name} node.")
            return
        if getattr(node, "spawn_kind", None) == "player":
            Log.warn("Only ONE Player Spawn is allowed per scene — "
                     "it cannot be duplicated.")
            return
        parent = node.parent if node.parent is not None else self.scene.root
        # The serializer resolves relative asset paths against the project
        # dir; without a saved project, fall back to the working directory.
        if self.project_path:
            project_dir = os.path.dirname(os.path.abspath(self.project_path))
        else:
            project_dir = os.getcwd()
        try:
            data = ProjectSerializer._serialize_node(node, project_dir)
            # Unique name: "Cube" → "Cube_Copy", "Cube_Copy1", …
            base = node.name + "_Copy"
            name = base
            i = 1
            existing = {c.name for c in parent.children}
            while name in existing:
                name = f"{base}{i}"
                i += 1
            data["name"] = name
            # Nudge the copy sideways so it does not sit exactly inside the
            # original (same trick every DCC/editor uses).
            pos = list(data.get("position", [0.0, 0.0, 0.0]))
            pos[0] += 0.5
            data["position"] = pos
            child_count_before = len(parent.children)
            ProjectSerializer._deserialize_node(data, parent, self,
                                                project_dir)
            if len(parent.children) > child_count_before:
                new_node = parent.children[-1]
                self.select_node(new_node)
                Log.info(f"Duplicated '{node.name}' → '{name}'")
            else:
                Log.error(f"Duplicate of '{node.name}' produced no node.")
        except Exception as ex:
            Log.error(f"Duplicate failed: {ex}")
            traceback.print_exc()

    # ---------------------------------------------------------- play mode
    def toggle_play(self):
        if self._playing:
            self._stop_play()
        else:
            self._begin_play()

    # ------------------------------------------------- trigger end-screen
    def show_end_screen(self, text: str, color, font_size: int):
        """Display a Trigger Point's message centred on screen (shown after
        the game stops, for a few seconds)."""
        c = list(color) if isinstance(color, (list, tuple)) \
            else [1.0, 0.85, 0.2]
        while len(c) < 3:
            c.append(1.0)
        self._end_screen = {
            "text": str(text) if str(text) else "LEVEL COMPLETE",
            "color": (_clamp(float(c[0]), 0.0, 1.0),
                      _clamp(float(c[1]), 0.0, 1.0),
                      _clamp(float(c[2]), 0.0, 1.0)),
            "font_size": int(_clamp(int(font_size), 8, 256)),
            "t": self.time,
        }

    def request_stop_play(self):
        """Ask the engine to leave Play mode at the end of the current
        gameplay section (deferred — never tears down mid-callback)."""
        self._stop_play_requested = True

    def _begin_play(self):
        # Play always leaves collider Edit Shape mode first.
        if self.shape_editor is not None and self.shape_editor.active:
            self.shape_editor.exit()
        # A new game clears any previous Trigger Point message / request.
        self._end_screen = None
        self._stop_play_requested = False
        # The Library popup is an EDIT-time tool — close it so its buttons
        # can't create objects mid-game (play-mode UI lock).
        lib = getattr(self, "_library_window", None)
        if lib is not None:
            try:
                lib.destroy()
            except Exception:
                pass
            self._library_window = None
        # Physics world is built BEFORE scripts wake up (so on_begin_play
        # can create CharacterControllers / raycast), then spawn points are
        # activated (their blueprints register in _blueprints), then every
        # blueprint wakes up, then the world is rebuilt AGAIN so the
        # auto-created colliders (FPS capsules, enemy spheres, pickup
        # triggers) are all registered.
        self.physics.rebuild(self.scene)
        # Spawn activation happens while _playing is still False so
        # attach_blueprint doesn't fire on_begin_play prematurely — the
        # uniform wake-up loop below handles every blueprint in one pass.
        self._activate_spawn_points()
        self._playing = True
        for bp in list(self._blueprints):
            try: bp.on_begin_play()
            except Exception as ex: Log.error(f"on_begin_play: {ex}")
        self.physics.rebuild(self.scene)
        self.physics.events.clear()

    def _stop_play(self):
        self._playing = False
        self.capture_mouse = False
        self.camera_locked = False
        self._capture_suspended = False
        self._scheduler.clear()
        self._destroy_queue.clear()
        self._stop_play_requested = False
        for bp in list(self._blueprints):
            try: bp.on_end_play()
            except Exception: pass
        # Strip runtime spawn blueprints / bullets and restore every spawn
        # marker to its exact edit-time state.
        self._deactivate_spawn_points()
        self.physics.clear()
        self.audio.stop_all()

    # ------------------------------------------------------------ blueprints
    def attach_blueprint(self, node: SceneNode, bp_or_cls,
                         properties: Optional[dict] = None) -> Optional[Blueprint]:
        if isinstance(bp_or_cls, type):
            bp = bp_or_cls()
        elif isinstance(bp_or_cls, Blueprint):
            bp = bp_or_cls
        elif isinstance(bp_or_cls, dict):
            bp = Blueprint.from_dict(bp_or_cls)
            if bp is None: return None
        else:
            return None
        bp.node = node
        bp.engine = self
        if properties:
            bp.properties.update(properties)
        if bp not in node.blueprints:
            node.blueprints.append(bp)
        if bp not in self._blueprints:
            self._blueprints.append(bp)
        if self._playing:
            try: bp.on_begin_play()
            except Exception as ex: Log.error(f"on_begin_play: {ex}")
        Log.info(f"Blueprint '{bp.__class__.__name__}' attached to '{node.name}'")
        return bp

    # ------------------------------------------------------------ gameplay API
    # Everything below is the scripting surface games use: physics setup,
    # messages, scheduling, spawning.  It never touches the editor UI.

    def _process_scheduler(self):
        if not self._scheduler:
            return
        due = [s for s in self._scheduler if s[0] <= self.time]
        self._scheduler = [s for s in self._scheduler if s[0] > self.time]
        for (_t, fn, args, kwargs) in due:
            try:
                fn(*args, **kwargs)
            except Exception as ex:
                Log.error(f"Scheduled call failed: {ex}")

    def schedule(self, delay: float, fn, *args, **kwargs):
        """Call fn once after `delay` seconds of gameplay."""
        self._scheduler.append((self.time + max(0.0, float(delay)),
                                fn, args, kwargs))

    def destroy_node(self, node: SceneNode):
        """Deferred node destruction (processed after all blueprint ticks,
        so scripts can safely call it during collisions / messages)."""
        if node is not None and node not in self._destroy_queue:
            self._destroy_queue.append(node)

    def _process_destroy_queue(self):
        if not self._destroy_queue:
            return
        queue, self._destroy_queue = self._destroy_queue, []
        for node in queue:
            if node.is_sun or node.is_clouds:
                continue
            for child in node.traverse():
                for bp in list(child.blueprints):
                    if bp in self._blueprints:
                        try:
                            bp.on_end_play()
                        except Exception:
                            pass
                        self._blueprints.remove(bp)
                child.blueprints = []
            self.physics.remove_node(node)
            node.remove()
            if self.selected is node:
                self.deselect()

    def _flush_physics_events(self):
        """Deliver collision / trigger events from the physics world to
        the blueprints living on both nodes involved."""
        events = self.physics.events
        if not events:
            return
        self.physics.events = []
        for (kind, na, nb, contact) in events:
            if kind in ("collision_enter", "collision_stay"):
                hook = ("on_collision_enter" if kind == "collision_enter"
                        else "on_collision_stay")
                info_a = {"point": contact.point, "normal": contact.normal,
                          "depth": contact.depth, "impulse": contact.jn}
                info_b = {"point": contact.point, "normal": -contact.normal,
                          "depth": contact.depth, "impulse": contact.jn}
                for bp in list(getattr(na, "blueprints", [])):
                    try:
                        getattr(bp, hook)(nb, info_a)
                    except Exception as ex:
                        Log.error(f"{hook} error on '{na.name}': {ex}")
                for bp in list(getattr(nb, "blueprints", [])):
                    try:
                        getattr(bp, hook)(na, info_b)
                    except Exception as ex:
                        Log.error(f"{hook} error on '{nb.name}': {ex}")
            elif kind == "collision_exit":
                for bp in list(getattr(na, "blueprints", [])):
                    try:
                        bp.on_collision_exit(nb)
                    except Exception as ex:
                        Log.error(f"on_collision_exit error: {ex}")
                for bp in list(getattr(nb, "blueprints", [])):
                    try:
                        bp.on_collision_exit(na)
                    except Exception as ex:
                        Log.error(f"on_collision_exit error: {ex}")
            elif kind in ("trigger_enter", "trigger_exit"):
                hook = ("on_trigger_enter" if kind == "trigger_enter"
                        else "on_trigger_exit")
                for bp in list(getattr(na, "blueprints", [])):
                    try:
                        getattr(bp, hook)(nb)
                    except Exception as ex:
                        Log.error(f"{hook} error on '{na.name}': {ex}")
                for bp in list(getattr(nb, "blueprints", [])):
                    try:
                        getattr(bp, hook)(na)
                    except Exception as ex:
                        Log.error(f"{hook} error on '{nb.name}': {ex}")

    def _game_holds_pointer(self) -> bool:
        """True while gameplay owns the pointer (mouse-look is on)."""
        return bool(self._playing and self.capture_mouse
                    and not self._capture_suspended)

    def _apply_mouse_capture(self):
        """Keep the pointer locked while the game wants it, every frame.

        Compared against the REAL grab and cursor state, not just the last
        value set here: whatever frees the pointer (a button release, the OS
        taking focus, a dialog) is taken back on the next frame instead of
        leaving the game unable to look around."""
        want = self._game_holds_pointer()
        regrab = getattr(self, "_mouse_regrab", 0)
        if regrab:
            self._mouse_regrab = regrab - 1
        if not want:
            # Not playing: only the hand-over matters.  The editor's own
            # right/middle-drag grab is left alone.
            if not self._mouse_captured:
                return
        else:
            try:
                held = bool(pygame.event.get_grab()) and not pygame.mouse.get_visible()
            except Exception:
                held = self._mouse_captured
            if self._mouse_captured and held and not regrab:
                return
        self._mouse_captured = want
        try:
            pygame.event.set_grab(want)
            pygame.mouse.set_visible(not want)
        except Exception:
            pass

    # ---- messages ----------------------------------------------------
    def send_message(self, event: str, data: Any = None):
        """Broadcast a message to EVERY blueprint (game events)."""
        for bp in list(self._blueprints):
            try:
                bp.on_message(event, data)
            except Exception as ex:
                Log.error(f"on_message('{event}') error in "
                          f"{bp.__class__.__name__}: {ex}")

    def message_node(self, node: SceneNode, event: str, data: Any = None):
        """Send a message to all blueprints on one node."""
        for bp in list(getattr(node, "blueprints", [])):
            try:
                bp.on_message(event, data)
            except Exception as ex:
                Log.error(f"on_message('{event}') error on "
                          f"'{node.name}': {ex}")

    def message_subtree(self, node: SceneNode, event: str, data: Any = None):
        """Send a message to a node and all its descendants."""
        for n in node.traverse():
            self.message_node(n, event, data)

    def message_ancestors(self, node: SceneNode, event: str, data: Any = None):
        """Send a message to a node and every ancestor up to the root —
        this is how a bullet hitting a child collider reaches the Health
        blueprint on the parent."""
        n = node
        while n is not None:
            self.message_node(n, event, data)
            n = n.parent

    # ---- lookup ------------------------------------------------------
    def find_node(self, name: str) -> Optional[SceneNode]:
        for n in self.scene.root.traverse():
            if n.name == name:
                return n
        return None

    def find_blueprints(self, class_name: str) -> List[Blueprint]:
        return [bp for bp in self._blueprints
                if type(bp).__name__ == class_name]

    def first_blueprint(self, class_name: str) -> Optional[Blueprint]:
        for bp in self._blueprints:
            if type(bp).__name__ == class_name:
                return bp
        return None

    def find_nodes_with_blueprint(self, class_name: str) -> List[SceneNode]:
        return [bp.node for bp in self.find_blueprints(class_name)
                if bp.node is not None]

    # ---- physics conveniences ----------------------------------------
    def raycast(self, origin, direction, max_dist: float = 1000.0,
                ignore: Optional[set] = None,
                hit_triggers: bool = False) -> Optional["RayHit"]:
        return self.physics.raycast(origin, direction, max_dist,
                                    ignore=ignore, hit_triggers=hit_triggers)

    def add_box_collider(self, node: SceneNode, size=None, **kw) -> BoxCollider:
        if size is None:
            size = node.mesh_params.get("size", (1, 1, 1)) \
                if node.mesh_type == "cube" else (1, 1, 1)
            if isinstance(size, (int, float)):
                size = (size, size, size)
        node.collider = BoxCollider(size=size, **kw)
        if self._playing:
            self.physics.add_node(node)
        return node.collider

    def add_sphere_collider(self, node: SceneNode, radius=None, **kw) -> SphereCollider:
        if radius is None:
            radius = node.mesh_params.get("radius", 0.5) \
                if node.mesh_type == "sphere" else 0.5
        node.collider = SphereCollider(radius=radius, **kw)
        if self._playing:
            self.physics.add_node(node)
        return node.collider

    def add_capsule_collider(self, node: SceneNode, radius: float = 0.4,
                             height: float = 1.8, **kw) -> CapsuleCollider:
        node.collider = CapsuleCollider(radius=radius, height=height, **kw)
        if self._playing:
            self.physics.add_node(node)
        return node.collider

    def add_plane_collider(self, node: SceneNode, size: float = 0.0, **kw) -> PlaneCollider:
        node.collider = PlaneCollider(size=size, **kw)
        if self._playing:
            self.physics.add_node(node)
        return node.collider

    def add_mesh_collider(self, node: SceneNode, **kw) -> MeshCollider:
        """Exact triangle-mesh collider built from the node's OWN mesh —
        for low-poly level geometry like buildings with interiors.  The
        collision triangles always match the rendered triangles (the BVH
        rebuilds automatically if the node's transform or mesh changes).
        Static/kinematic use; a dynamic body degrades to a bounding
        sphere (a warning is logged)."""
        node.collider = MeshCollider(**kw)
        if node.mesh is None:
            Log.warn(f"MeshCollider on '{node.name}': the node has no "
                     f"mesh yet — it will collide once a mesh is assigned.")
        if self._playing:
            self.physics.add_node(node)
        return node.collider

    def add_rigidbody(self, node: SceneNode, **kw) -> RigidBody:
        node.rigidbody = RigidBody(**kw)
        if self._playing:
            self.physics.remove_node(node)
            self.physics.add_node(node)
        return node.rigidbody

    def make_character(self, node: SceneNode, radius: float = 0.35,
                       height: float = 1.8, **kw) -> "CharacterController":
        """Capsule character controller for a node (auto-adds the matching
        capsule collider when missing)."""
        if node.collider is None:
            node.collider = CapsuleCollider(radius=radius, height=height)
        if self._playing:
            self.physics.add_node(node)
        return CharacterController(self.physics, node, radius=radius,
                                   height=height, **kw)

    # ---- spawning ------------------------------------------------------
    def instantiate(self, template: SceneNode, position=None,
                    rotation=None, make_visible: bool = True,
                    name: Optional[str] = None) -> Optional[SceneNode]:
        """Clone a template node subtree (meshes, materials, colliders,
        rigid bodies, blueprints) at runtime — the prefab workflow."""
        if self.project_path:
            project_dir = os.path.dirname(os.path.abspath(self.project_path))
        else:
            project_dir = os.getcwd()
        try:
            data = ProjectSerializer._serialize_node(template, project_dir)
            base = name or (template.name + "_spawn")
            existing = {n.name for n in self.scene.root.traverse()}
            uname, i = base, 1
            while uname in existing:
                uname = f"{base}{i}"
                i += 1
            data["name"] = uname
            before = len(self.scene.root.children)
            ProjectSerializer._deserialize_node(data, self.scene.root,
                                                self, project_dir)
            if len(self.scene.root.children) <= before:
                return None
            clone = self.scene.root.children[-1]
            if position is not None:
                _write_node_world_position(clone, _v3(position))
            if rotation is not None:
                clone.rotation_euler = _v3(rotation)
            if make_visible:
                for n in clone.traverse():
                    n.visible = True
            if self._playing:
                self.physics.add_node_recursive(clone)
            return clone
        except Exception as ex:
            Log.error(f"instantiate('{template.name}') failed: {ex}")
            traceback.print_exc()
            return None

    def spawn_flash(self, position, color=(1.0, 0.8, 0.4),
                    intensity: float = 30.0, range: float = 9.0,
                    ttl: float = 0.06) -> SceneNode:
        """Short-lived point light (muzzle flashes, explosions)."""
        node = SceneNode("_flash", parent=self.scene.root)
        node.position = _v3(position)
        node.is_point_light = True
        node.point_light = PointLight(color=glm.vec3(float(color[0]),
                                                     float(color[1]),
                                                     float(color[2])),
                                      intensity=float(intensity),
                                      range=float(range))
        self.schedule(ttl, self.destroy_node, node)
        return node

    # ------------------------------------------------------------ model load
    def load_model_into(self, node: SceneNode, path: str) -> bool:
        """Load a .glb/.gltf/.obj/.blend model into an existing node."""
        # [TRIPLEA:p3_model_import_gate]
        ext = os.path.splitext(path)[1].lower()
        try:
            # OBJ flows through the SAME trimesh-based importer as glTF —
            # GlbImporter.build_nodes/_make_resolver/_iter_scene_meshes are
            # format-agnostic (trimesh parses OBJ + MTL itself).
            # FinalPatch removes the Blender subprocess path completely.
            if ext not in (".glb", ".gltf", ".obj", ".blend"):
                Log.warn(f"Unsupported model format '{ext}' — supported: "
                         ".glb, .gltf, .obj and .blend.")
                return False
            if ext in (".glb", ".gltf", ".obj", ".blend"):
                roots = (BlendImporter.build_nodes(path) if ext == ".blend"
                         else ObjImporter.build_nodes(path) if ext == ".obj"  # [OBJPATCH:obj_dispatch]
                         else GlbImporter.build_nodes(path))  # [PATCHB:blend_dispatch]
                if not roots:
                    return False
                # Recursively attach GLB node data onto engine SceneNodes.
                # A single GLB node may contain MULTIPLE mesh primitives
                # (common for objects composed of several parts/materials).
                # The first primitive becomes the target node's own mesh;
                # the remaining primitives spawn child SceneNodes so none of
                # the geometry is silently dropped.
                def _apply_glb_transform(nd: GlbImporter.NodeData, target: SceneNode):
                    """Apply a glTF node's local transform (full TRS or a raw
                    column-major matrix) to the SceneNode.  Previously only
                    translation was honoured — rotated/scaled GLB sub-objects
                    imported in the wrong pose."""
                    if nd.matrix:
                        m = glm.mat4(*[float(v) for v in nd.matrix])
                        target.position = target.position + glm.vec3(m[3])
                        sx = glm.length(glm.vec3(m[0]))
                        sy = glm.length(glm.vec3(m[1]))
                        sz = glm.length(glm.vec3(m[2]))
                        # Negative determinant → one axis is mirrored.
                        if glm.determinant(glm.mat3(m)) < 0.0:
                            sx = -sx
                        target.scale = glm.vec3(target.scale.x * sx,
                                                target.scale.y * sy,
                                                target.scale.z * sz)
                        r = glm.mat3(glm.vec3(m[0]) / (sx if abs(sx) > 1e-12 else 1.0),
                                     glm.vec3(m[1]) / (sy if abs(sy) > 1e-12 else 1.0),
                                     glm.vec3(m[2]) / (sz if abs(sz) > 1e-12 else 1.0))
                        q = glm.quat_cast(glm.mat4(r))
                        # [TRIPLEA:p3_glb_rot_compose_matrix] Euler ADDITION
                        # is not rotation composition, and glm.eulerAngles()
                        # extracts XYZ-order angles while the engine composes
                        # Y-X-Z (SceneNode.local_matrix: R = Ry*Rx*Rz) — both
                        # bugs made imported sub-object rotations disobey the
                        # source mesh.  Compose properly: existing euler ->
                        # quat (Y-X-Z), multiply, convert back with the
                        # order-exact matrix helper (appended patch block).
                        target.rotation_euler = _quat_to_yxz_euler_deg(
                            _yxz_euler_deg_to_quat(target.rotation_euler) * q)
                    else:
                        if nd.translation:
                            target.position = target.position + glm.vec3(*nd.translation)
                        if nd.scale:
                            target.scale = glm.vec3(target.scale.x * float(nd.scale[0]),
                                                    target.scale.y * float(nd.scale[1]),
                                                    target.scale.z * float(nd.scale[2]))
                        if nd.rotation:
                            q = glm.quat(float(nd.rotation[3]), float(nd.rotation[0]),
                                         float(nd.rotation[1]), float(nd.rotation[2]))
                            # [TRIPLEA:p3_glb_rot_compose_trs] Same fix as the
                            # matrix branch: quaternion multiplication in the
                            # engine's Y-X-Z euler convention.  Note: the
                            # trimesh importer BAKES world transforms into the
                            # vertex data (see GlbImporter.build_nodes), so
                            # baked rotations are already honoured; this path
                            # keeps any explicit node TRS composing correctly
                            # with rotations the user later applies in the
                            # editor.
                            target.rotation_euler = _quat_to_yxz_euler_deg(
                                _yxz_euler_deg_to_quat(target.rotation_euler) * q)

                def _attach(nd: GlbImporter.NodeData, target: SceneNode):
                    _apply_glb_transform(nd, target)
                    for i, md in enumerate(nd.meshes):
                        mesh = Mesh.from_glb_mesh_data(self.ctx, md)
                        material = (Material.from_glb_info(md.material_info)
                                    if md.material_info else None)
                        if i == 0:
                            # First primitive lives on the target node itself.
                            target.mesh = mesh
                            target.material = material
                        else:
                            # Subsequent primitives become child nodes so
                            # each keeps its own mesh + material binding.
                            part = SceneNode(
                                f"{nd.name}_part{i}", parent=target)
                            part._heaven3d_imported_part = ext
                            part.mesh = mesh
                            part.material = material
                    for child in nd.children:
                        child_node = SceneNode(child.name, parent=target)
                        child_node._heaven3d_imported_part = ext
                        _attach(child, child_node)
                _attach(roots[0], node)
                # Additional ROOT nodes (multi-object GLB scenes) were silently
                # dropped before — attach them as children too.
                for extra in roots[1:]:
                    extra_node = SceneNode(extra.name, parent=node)
                    extra_node._heaven3d_imported_part = ext
                    _attach(extra, extra_node)
                node.source_path = path
                # [PATCHB:blend_lights] Import .blend lamps as engine light nodes.
                if ext == ".blend":
                    BlendImporter.attach_imported_lights(self, node)
                return True
            else:
                # [TRIPLEA:p3_model_import_warn]
                Log.warn(f"Unsupported model format '{ext}' — supported: "
                         ".glb, .gltf, .obj and .blend.")
                return False
        except Exception as ex:
            Log.error(f"Model load failed '{path}': {ex}")
            return False

    def load_model(self, path: str) -> Optional[SceneNode]:
        node = SceneNode(name=os.path.splitext(os.path.basename(path))[0],
                         parent=self.scene.root)
        if self.load_model_into(node, path):
            return node
        node.remove()
        return None

    # ------------------------------------------------------------ terrain
    def create_terrain(self, size: float = 200.0, resolution: int = 128,
                       height_scale: float = 10.0, seed: int = 42) -> SceneNode:
        node = SceneNode("Terrain", parent=self.scene.root)
        terrain = Terrain(self.ctx, size, resolution, height_scale, seed)
        terrain.generate()
        terrain.attach_to(node)
        node.user_data["terrain_config"] = {
            "size": size, "resolution": resolution,
            "height_scale": height_scale, "seed": seed,
        }
        return node

    def create_terrain_node(self, node: SceneNode, config: dict):
        terrain = Terrain(self.ctx, config.get("size", 200),
                          config.get("resolution", 128),
                          config.get("height_scale", 10),
                          config.get("seed", 42))
        terrain.generate()
        terrain.attach_to(node)

    # ------------------------------------------------------------ primitives
    def add_cube(self, size: float = 1.0, name: str = "Cube",
                 solid: bool = True) -> SceneNode:
        node = SceneNode(name, parent=self.scene.root)
        node.mesh = Mesh.make_cube(self.ctx, size)
        node.mesh_type = "cube"
        node.mesh_params = {"size": size}
        node.material = Material(name=name + "_mat",
                                 base_color=(0.8, 0.6, 0.3, 1.0),
                                 metallic=0.1, roughness=0.5)
        if solid:
            # Static box collider so scenes are solid the moment Play starts
            node.collider = BoxCollider(size=(size, size, size))
        return node

    def add_plane(self, size: float = 10.0, name: str = "Plane",
                  solid: bool = True) -> SceneNode:
        node = SceneNode(name, parent=self.scene.root)
        node.mesh = Mesh.make_plane(self.ctx, size, 1)
        node.mesh_type = "plane"
        node.mesh_params = {"size": size}
        node.material = Material(name=name + "_mat",
                                 base_color=(0.4, 0.4, 0.4, 1.0),
                                 roughness=0.9)
        if solid:
            node.collider = PlaneCollider(size=size)
        return node

    def add_sphere(self, radius: float = 1.0, name: str = "Sphere",
                   solid: bool = True) -> SceneNode:
        node = SceneNode(name, parent=self.scene.root)
        node.mesh = Mesh.make_sphere(self.ctx, radius)
        node.mesh_type = "sphere"
        node.mesh_params = {"radius": radius}
        node.material = Material(name=name + "_mat",
                                 base_color=(0.5, 0.6, 0.8, 1.0),
                                 metallic=0.0, roughness=0.4)
        if solid:
            node.collider = SphereCollider(radius=radius)
        return node

    # ------------------------------------------------------------ local lights
    def _unique_node_name(self, base: str) -> str:
        """Unity-style hierarchy naming: 'Point Light', 'Point Light (1)'."""
        existing = {n.name for n in self.scene.root.traverse()}
        if base not in existing:
            return base
        i = 1
        while f"{base} ({i})" in existing:
            i += 1
        return f"{base} ({i})"

    def add_point_light(self) -> SceneNode:
        """Create a Unity-style Point Light scene object and select it."""
        node = SceneNode(self._unique_node_name("Point Light"),
                         parent=self.scene.root)
        node.position = glm.vec3(0.0, 2.0, 0.0)
        node.is_point_light = True
        node.point_light = PointLight()
        self.select_node(node)
        Log.info(f"Added '{node.name}' to the scene")
        return node

    def add_spot_light(self) -> SceneNode:
        """Create a Unity-style Spot Light scene object and select it.
        The default cone points straight down, like a ceiling spotlight."""
        node = SceneNode(self._unique_node_name("Spot Light"),
                         parent=self.scene.root)
        node.position = glm.vec3(0.0, 4.0, 0.0)
        node.rotation_euler = glm.vec3(-90.0, 0.0, 0.0)
        node.is_spot_light = True
        node.spot_light = SpotLight()
        self.select_node(node)
        Log.info(f"Added '{node.name}' to the scene")
        return node

    # ------------------------------------------------------------ spawn points
    # Default Inspector values for every spawn-point kind.  The player
    # capsule itself is FIXED at the Unity / Blender FPS-controller size
    # (radius 0.35 m, height 1.8 m) — it is never user-resizable, which is
    # exactly what keeps the "fell through the floor" class of bugs away.
    SPAWN_DEFAULTS = {
        "player": {"health": 100.0, "bullets": 120,
                   "walk_speed": 6.0, "run_speed": 10.0,
                   "cam_rot_speed": 0.13},
        "enemy": {"health": 100.0, "bullet_damage": 10.0,
                  "fly_speed": 4.0, "cam_rot_speed": 240.0,
                  "shots_per_sec": 1.5},
        "bullet_pickup": {"bullets": 60},
        "health_pickup": {"health": 30.0},
        "trigger": {"text": "LEVEL COMPLETE",
                    "color": [1.0, 0.85, 0.20],
                    "font_size": 64},
    }

    def find_player_spawn(self) -> Optional[SceneNode]:
        """The scene's single Player Spawn node, or None."""
        for n in self.scene.root.traverse():
            if getattr(n, "spawn_kind", None) == "player":
                return n
        return None

    def _make_spawn_node(self, kind: str, name: str,
                         position) -> SceneNode:
        node = SceneNode(self._unique_node_name(name),
                         parent=self.scene.root)
        node.position = glm.vec3(*position)
        node.spawn_kind = kind
        node.spawn_config = dict(self.SPAWN_DEFAULTS[kind])
        return node

    def add_player_spawn(self) -> SceneNode:
        """Create THE Player Spawn point.  Only one may exist in a scene —
        requesting a second one simply selects the existing one."""
        existing = self.find_player_spawn()
        if existing is not None:
            Log.warn("Only ONE Player Spawn is allowed per scene — "
                     "selecting the existing one.")
            self.select_node(existing)
            return existing
        node = self._make_spawn_node("player", "Player Spawn",
                                     (0.0, SpawnGizmos.PLAYER_HEIGHT * 0.5
                                      + 0.02, 0.0))
        # Editor-time visual: a translucent teal capsule at the exact
        # Unity/Blender FPS-controller size.  Hidden automatically in Play.
        node.mesh = Mesh.make_capsule(self.ctx, SpawnGizmos.PLAYER_RADIUS,
                                      SpawnGizmos.PLAYER_HEIGHT)
        node.mesh_type = "capsule"
        node.mesh_params = {"radius": SpawnGizmos.PLAYER_RADIUS,
                            "height": SpawnGizmos.PLAYER_HEIGHT}
        node.material = Material(
            name="player_spawn_mat",
            base_color=(0.20, 0.90, 0.60, 0.35),
            roughness=0.6, emissive=(0.10, 0.55, 0.30),
            emissive_intensity=0.6, alpha_mode="BLEND", double_sided=True)
        self.select_node(node)
        Log.info(f"Added '{node.name}' to the scene (only one allowed)")
        return node

    def add_enemy_spawn(self) -> SceneNode:
        """Create an Enemy Spawn point: a soccer-ball sized flying shooter.
        Its mesh is the enemy's actual in-game body and can be replaced
        with any imported .glb model from the Inspector."""
        node = self._make_spawn_node("enemy", "Enemy Spawn", (0.0, 1.5, 0.0))
        node.mesh = Mesh.make_sphere(self.ctx, SpawnGizmos.ENEMY_RADIUS)
        node.mesh_type = "sphere"
        node.mesh_params = {"radius": SpawnGizmos.ENEMY_RADIUS}
        node.material = Material(
            name="enemy_spawn_mat",
            base_color=(0.85, 0.16, 0.12, 1.0),
            roughness=0.35, metallic=0.1,
            emissive=(0.45, 0.05, 0.03), emissive_intensity=0.8)
        self.select_node(node)
        Log.info(f"Added '{node.name}' to the scene")
        return node

    def add_bullet_pickup(self) -> SceneNode:
        """Create a Bullet Pickup point (half-soccer-ball cube)."""
        node = self._make_spawn_node("bullet_pickup", "Bullet Pickup",
                                     (0.0, 0.35, 0.0))
        node.mesh = Mesh.make_cube(self.ctx, SpawnGizmos.PICKUP_SIZE)
        node.mesh_type = "cube"
        node.mesh_params = {"size": SpawnGizmos.PICKUP_SIZE}
        node.material = Material(
            name="bullet_pickup_mat",
            base_color=(1.0, 0.62, 0.10, 1.0),
            roughness=0.3, metallic=0.35,
            emissive=(0.60, 0.32, 0.04), emissive_intensity=1.2)
        self.select_node(node)
        Log.info(f"Added '{node.name}' to the scene")
        return node

    def add_health_pickup(self) -> SceneNode:
        """Create a Health Pickup point (half-soccer-ball cube)."""
        node = self._make_spawn_node("health_pickup", "Health Pickup",
                                     (0.0, 0.35, 0.0))
        node.mesh = Mesh.make_cube(self.ctx, SpawnGizmos.PICKUP_SIZE)
        node.mesh_type = "cube"
        node.mesh_params = {"size": SpawnGizmos.PICKUP_SIZE}
        node.material = Material(
            name="health_pickup_mat",
            base_color=(0.20, 0.95, 0.35, 1.0),
            roughness=0.3, metallic=0.05,
            emissive=(0.05, 0.55, 0.12), emissive_intensity=1.2)
        self.select_node(node)
        Log.info(f"Added '{node.name}' to the scene")
        return node

    def add_trigger_point(self) -> SceneNode:
        """Create a Trigger Point: a 1x1x1 volume that stops the game and
        shows a centred message the moment the player crosses it.  Fully
        visible in the editor, completely invisible at runtime."""
        node = self._make_spawn_node("trigger", "Trigger Point",
                                     (0.0, 0.5, 0.0))
        node.mesh = Mesh.make_cube(self.ctx, 1.0)
        node.mesh_type = "cube"
        node.mesh_params = {"size": 1.0}
        node.material = Material(
            name="trigger_point_mat",
            base_color=(0.70, 0.30, 1.00, 0.30),
            roughness=0.6, emissive=(0.40, 0.12, 0.65),
            emissive_intensity=0.8, alpha_mode="BLEND", double_sided=True)
        self.select_node(node)
        Log.info(f"Added '{node.name}' to the scene")
        return node

    # ------------------------------------------------------ spawn activation
    def _activate_spawn_points(self):
        """Play-mode entry: turn spawn MARKERS into live gameplay objects.

        * Player Spawn → an FPSController + Weapon pair is attached at
          runtime (ground-snapped by the controller itself); the teal
          editor capsule mesh is hidden for the duration of Play.
        * Enemy Spawn  → an EnemyFlyShooter blueprint drives the node's
          own mesh (sphere by default, or the user's custom import).
        * Pickups      → the proven Pickup blueprint (trigger + spin/bob),
          parameterised from the node's spawn_config.
        Everything attached here is tagged `_runtime_spawn` and stripped
        again by `_deactivate_spawn_points` when Play stops, so the edit
        scene is restored exactly as it was.
        """
        self._spawn_runtime_bps = []
        self._spawn_runtime_nodes = []
        self._spawn_snapshot = {}
        self._play_hidden_meshes = []

        def _attach_runtime(node, bp_cls, props):
            bp = self.attach_blueprint(node, bp_cls, props)
            if bp is not None:
                bp._runtime_spawn = True
                self._spawn_runtime_bps.append(bp)
            return bp

        for node in list(self.scene.root.traverse()):
            kind = getattr(node, "spawn_kind", None)
            if not kind:
                continue
            cfg = node.spawn_config
            self._spawn_snapshot[node] = (glm.vec3(node.position),
                                          glm.vec3(node.rotation_euler),
                                          glm.vec3(node.scale),
                                          node.visible)
            node.visible = True
            if kind == "player":
                walk = max(0.1, float(cfg.get("walk_speed", 6.0)))
                run = max(walk, float(cfg.get("run_speed", 10.0)))
                _attach_runtime(node, FPSController, {
                    "move_speed": walk,
                    "sprint_multiplier": run / walk,
                    "mouse_sensitivity":
                        float(cfg.get("cam_rot_speed", 0.13)),
                    "max_health": float(cfg.get("health", 100.0)),
                    "capsule_radius": SpawnGizmos.PLAYER_RADIUS,
                    "capsule_height": SpawnGizmos.PLAYER_HEIGHT,
                    "eye_height": 1.62,
                })
                # Bullet Value = TOTAL starting bullets: the magazine takes
                # the first 30, the rest goes to the reserve.
                bullets = max(0, int(cfg.get("bullets", 120)))
                mag = min(30, bullets)
                _attach_runtime(node, Weapon, {
                    "magazine": mag,
                    "reserve_ammo": bullets - mag,
                })
                # Hide the editor capsule visual during Play (the player IS
                # the camera now).  Physics is untouched — the controller
                # builds its own gameplay capsule collider.
                if node.mesh is not None:
                    self._play_hidden_meshes.append((node, node.mesh))
                    node.mesh = None
            elif kind == "enemy":
                # Physics shape = the enemy's CURRENT mesh (sphere by
                # default, or the user's custom import) — a solid box fit
                # to the exact rendered bounds.
                self._fit_spawn_collider(node, is_trigger=False)
                _attach_runtime(node, EnemyFlyShooter, {
                    "max_health": float(cfg.get("health", 100.0)),
                    "bullet_damage": float(cfg.get("bullet_damage", 10.0)),
                    "fly_speed": float(cfg.get("fly_speed", 4.0)),
                    "turn_speed": float(cfg.get("cam_rot_speed", 240.0)),
                    "shots_per_sec": float(cfg.get("shots_per_sec", 1.5)),
                    "collider_radius": SpawnGizmos.ENEMY_RADIUS,
                })
            elif kind in ("bullet_pickup", "health_pickup"):
                is_bullet = kind == "bullet_pickup"
                amount = (float(cfg.get("bullets", 60)) if is_bullet
                          else float(cfg.get("health", 30.0)))
                # Trigger shape = the pickup's CURRENT mesh bounds (cube by
                # default, or the user's custom import).
                self._fit_spawn_collider(node, is_trigger=True)
                _attach_runtime(node, Pickup, {
                    "kind": "ammo" if is_bullet else "health",
                    "amount": amount,
                    "respawn_time": 20.0,
                    "trigger_radius": 0.55,
                    "spin": True, "bob": True,
                })
            elif kind == "trigger":
                # 1x1x1 level-end trigger volume — invisible at runtime.
                self._fit_spawn_collider(node, is_trigger=True)
                _attach_runtime(node, LevelEndTrigger, {
                    "text": str(cfg.get("text", "LEVEL COMPLETE")),
                    "color": list(cfg.get("color", [1.0, 0.85, 0.2])),
                    "font_size": int(cfg.get("font_size", 64)),
                })
                if node.mesh is not None:
                    self._play_hidden_meshes.append((node, node.mesh))
                    node.mesh = None

    def _deactivate_spawn_points(self):
        """Play-mode exit: remove every runtime blueprint / bullet node and
        restore each spawn marker's edit-time transform, visibility, mesh
        and collider-free state."""
        for bp in list(self._spawn_runtime_bps):
            try:
                bp.on_end_play()
            except Exception:
                pass
            if bp.node is not None and bp in bp.node.blueprints:
                bp.node.blueprints.remove(bp)
            if bp in self._blueprints:
                self._blueprints.remove(bp)
        self._spawn_runtime_bps = []
        for n in list(self._spawn_runtime_nodes):
            try:
                self.physics.remove_node(n)
            except Exception:
                pass
            for bp in list(n.blueprints):
                if bp in self._blueprints:
                    self._blueprints.remove(bp)
            n.blueprints = []
            n.remove()
        self._spawn_runtime_nodes = []
        for node, mesh in self._play_hidden_meshes:
            node.mesh = mesh
        self._play_hidden_meshes = []
        for node, (pos, rot, scale, vis) in self._spawn_snapshot.items():
            node.position = pos
            node.rotation_euler = rot
            node.scale = scale
            node.visible = vis
            # Colliders / rigid bodies were created at runtime by the
            # blueprints — drop them so the edit scene stays pristine.
            node.collider = None
            node.rigidbody = None
        self._spawn_snapshot = {}

    def open_library_menu(self):
        """Open the Unity-style Library popup used to create engine objects."""
        root = self._tk_root_window()
        if root is None:
            return
        try:
            import tkinter as tk
        except ImportError:
            Log.error("tkinter not available; cannot open Library.")
            return

        existing = getattr(self, "_library_window", None)
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.lift()
                    existing.focus_force()
                    return
            except Exception:
                self._library_window = None

        win = tk.Toplevel(root)
        self._library_window = win
        win.title("Library")
        win.resizable(False, False)
        win.configure(bg="#202124", padx=18, pady=16)
        try:
            win.attributes("-topmost", True)
        except Exception:
            pass

        def _close():
            try:
                win.destroy()
            finally:
                self._library_window = None

        def _add(kind: str):
            if kind == "point":
                self.add_point_light()
            elif kind == "spot":
                self.add_spot_light()
            elif kind == "player_spawn":
                self.add_player_spawn()
            elif kind == "enemy_spawn":
                self.add_enemy_spawn()
            elif kind == "bullet_pickup":
                self.add_bullet_pickup()
            elif kind == "health_pickup":
                self.add_health_pickup()
            elif kind == "trigger_point":
                self.add_trigger_point()
            elif kind == "sound_point":
                self.add_sound_point()
            _close()

        def _section(text):
            tk.Label(win, text=text, fg="#8ab4f8", bg="#202124",
                     font=("Segoe UI", 11, "bold")).pack(anchor="w",
                                                        pady=(10, 3))

        def _btn(text, kind):
            tk.Button(win, text=text, width=24,
                      command=lambda: _add(kind),
                      bg="#3c4043", fg="#ffffff", activebackground="#5f6368",
                      activeforeground="#ffffff", relief="flat", padx=12,
                      pady=8).pack(fill="x", pady=3)

        tk.Label(win, text="Library", fg="#8ab4f8", bg="#202124",
                 font=("Segoe UI", 14, "bold")).pack(anchor="w")
        tk.Label(win, text="Add an object to the 3D scene", fg="#d7d7d7",
                 bg="#202124", font=("Segoe UI", 11)).pack(anchor="w",
                                                         pady=(2, 4))
        _section("LIGHT")
        _btn("Point Light", "point")
        _btn("Spot Light", "spot")
        _section("SPAWN POINT")
        _btn("Player Spawn  (only 1)", "player_spawn")
        _btn("Enemy Spawn", "enemy_spawn")
        _section("PICKUP POINTS")
        _btn("Bullet Pickup", "bullet_pickup")
        _btn("Health Pickup", "health_pickup")
        _btn("Trigger Point", "trigger_point")
        _btn("Sound Point", "sound_point")
        win.protocol("WM_DELETE_WINDOW", _close)
        win.update_idletasks()
        # Center the compact popup over the Pygame window where possible.
        try:
            px, py = pygame.display.get_window_position()
            ww, wh = pygame.display.get_window_size()
            w, h = win.winfo_width(), win.winfo_height()
            win.geometry(f"+{px + (ww - w) // 2}+{py + (wh - h) // 2}")
        except Exception:
            pass
        win.lift()
        win.focus_force()

    # ---------------------------------------------------------- save / load
    def save_project(self, path: Optional[str] = None):
        if path is None:
            path = self.project_path or os.path.join(os.getcwd(), "game.heaven3d")
        self.project_path = path
        cam_state = {"position": list(self.camera.position),
                     "yaw": self.camera.yaw, "pitch": self.camera.pitch}
        settings = {"shadows": self.renderer.enable_shadows,
                    "ssao": self.renderer.enable_ssao,
                    "bloom": self.renderer.enable_bloom,
                    "fxaa": self.renderer.enable_fxaa,
                    "msaa": self.renderer.enable_msaa,
                    "msaa_samples": self.renderer.msaa_samples}
        ProjectSerializer.save(self.scene, path, cam_state, settings)

    def load_project(self, path: Optional[str] = None):
        if path is None:
            path = self.project_path or os.path.join(os.getcwd(), "game.heaven3d")
        if not os.path.isfile(path):
            Log.warn(f"No project file: {path}")
            return
        ProjectSerializer.load(path, self.scene, self)

    def export_runtime(self):
        path = os.path.join(os.getcwd(), "game.heaven3d")
        self.save_project(path)
        Log.info(f"Exported runtime project: {path}")

    # ----------------------------------------------- native file dialogs (tkinter)
    def _native_dialog(self, mode: str, title: str,
                       filetypes=None,
                       initialdir: Optional[str] = None,
                       defaultext: str = "") -> Optional[str]:
        """Open a native OS file dialog.  `mode` is 'open' or 'save'.
        Returns the chosen path string, or None if the user cancelled.
        Pauses the engine loop while the modal dialog is up."""
        if filetypes is None:
            filetypes = [("All files", "*.*")]
        try:
            import tkinter as tk
            from tkinter import filedialog
        except ImportError:
            Log.error("tkinter not available; cannot open file dialog.")
            return None
        # Reuse a single hidden root window
        if not hasattr(self, "_tk_root") or self._tk_root is None:
            try:
                self._tk_root = tk.Tk()
                self._tk_root.withdraw()
            except Exception:
                self._tk_root = None
                return None
        # Make sure the dialog appears above the pygame window
        try:
            self._tk_root.attributes("-topmost", True)
        except Exception:
            pass
        if initialdir is None:
            initialdir = os.getcwd()
        if mode == "open":
            path = filedialog.askopenfilename(
                title=title, filetypes=filetypes, initialdir=initialdir)
        elif mode == "save":
            path = filedialog.asksaveasfilename(
                title=title, filetypes=filetypes, initialdir=initialdir,
                defaultextension=defaultext)
        else:
            return None
        return path if path else None

    # ----------------------------------------------- shared tkinter root
    def _tk_root_window(self):
        """Return (creating if necessary) a hidden topmost tkinter root
        window shared by all native dialogs.  Calling .attributes('-topmost',
        True) keeps askfloat / askcolor / file dialogs from getting lost
        behind the Pygame window on Windows 10 and Linux WMs."""
        try:
            import tkinter as tk
        except ImportError:
            Log.error("tkinter not available; cannot open native dialog.")
            return None
        if not hasattr(self, "_tk_root") or self._tk_root is None:
            try:
                self._tk_root = tk.Tk()
                self._tk_root.withdraw()
            except Exception:
                self._tk_root = None
                return None
        try:
            self._tk_root.attributes("-topmost", True)
        except Exception:
            pass
        return self._tk_root

    def _pump_tkinter_events(self):
        """Process pending tkinter events while the Pygame loop keeps running.
        This lets the Library window behave like a proper Unity editor popup
        instead of freezing Heaven3D until it closes."""
        root = getattr(self, "_tk_root", None)
        if root is None:
            return
        try:
            root.update_idletasks()
            root.update()
        except Exception:
            # The root may have been closed by the OS; recreate it lazily the
            # next time a dialog or popup is requested.
            self._tk_root = None
            self._library_window = None

    # ----------------------------------------------- editable transforms
    def _edit_transform_value(self, node: SceneNode, attribute: str, axis: int):
        """Open an askfloat dialog and apply the entered value to the node's
        transform.  attribute is 'position' | 'rotation_euler' | 'scale';
        axis is 0/1/2 for X/Y/Z."""
        root = self._tk_root_window()
        if root is None:
            return
        try:
            from tkinter import simpledialog
        except ImportError:
            Log.error("tkinter.simpledialog not available.")
            return
        current = getattr(node, attribute)
        labels = {"position": ("X", "Y", "Z"),
                  "rotation_euler": ("Pitch", "Yaw", "Roll"),
                  "scale": ("Scale X", "Scale Y", "Scale Z")}
        sect_label, axis_label = labels[attribute][axis], ""
        title = f"Set {attribute} {labels[attribute][axis]}"
        prompt = f"{labels[attribute][axis]} ({attribute}):"
        try:
            value = simpledialog.askfloat(
                title, prompt, initialvalue=float(current[axis]),
                parent=root)
        except Exception as ex:
            Log.error(f"askfloat failed: {ex}")
            return
        if value is None:
            return  # user cancelled
        # Apply the new value, preserving the other two axes.
        if attribute == "scale":
            value = max(0.001, value)   # never allow zero/negative scale
        new_vec = glm.vec3(current)
        new_vec[axis] = value
        setattr(node, attribute, new_vec)
        Log.info(f"Set {node.name}.{attribute}[{axis}] = {value}")

    # ----------------------------------------------- material color picker
    def _edit_material_color(self, material):
        """Open the native color chooser and apply the picked color to the
        material's base_color (normalised to 0..1)."""
        root = self._tk_root_window()
        if root is None:
            return
        try:
            from tkinter import colorchooser
        except ImportError:
            Log.error("tkinter.colorchooser not available.")
            return
        # askcolor takes/returns 0..255 tuples
        initial = (int(material.base_color[0] * 255),
                   int(material.base_color[1] * 255),
                   int(material.base_color[2] * 255))
        try:
            result = colorchooser.askcolor(title="Pick Material Color",
                                           initialcolor=initial,
                                           parent=root)
        except Exception as ex:
            Log.error(f"askcolor failed: {ex}")
            return
        if result is None or result[0] is None:
            return  # user cancelled
        rgb255 = result[0]
        # Preserve alpha; normalise RGB to 0..1 floats
        material.base_color = (rgb255[0] / 255.0,
                               rgb255[1] / 255.0,
                               rgb255[2] / 255.0,
                               material.base_color[3])
        Log.info(f"Set material '{material.name}' color = "
                 f"({material.base_color[0]:.2f}, "
                 f"{material.base_color[1]:.2f}, "
                 f"{material.base_color[2]:.2f})")

    def _edit_sun_color(self, sun_light: "SunLight"):
        """Open the native color chooser and apply the picked color to the
        sun's light color."""
        root = self._tk_root_window()
        if root is None:
            return
        try:
            from tkinter import colorchooser
        except ImportError:
            Log.error("tkinter.colorchooser not available.")
            return
        initial = (int(sun_light.color[0] * 255),
                   int(sun_light.color[1] * 255),
                   int(sun_light.color[2] * 255))
        try:
            result = colorchooser.askcolor(title="Pick Sun Color",
                                           initialcolor=initial,
                                           parent=root)
        except Exception as ex:
            Log.error(f"askcolor failed: {ex}")
            return
        if result is None or result[0] is None:
            return
        rgb255 = result[0]
        sun_light.color = glm.vec3(rgb255[0] / 255.0,
                                   rgb255[1] / 255.0,
                                   rgb255[2] / 255.0)
        Log.info(f"Set sun color = ({sun_light.color[0]:.2f}, "
                 f"{sun_light.color[1]:.2f}, {sun_light.color[2]:.2f})")

    def _edit_light_color(self, light, title: str):
        """Open a native colour picker for a point or spot light."""
        root = self._tk_root_window()
        if root is None:
            return
        try:
            from tkinter import colorchooser
        except ImportError:
            Log.error("tkinter.colorchooser not available.")
            return
        initial = (int(_clamp(light.color[0], 0.0, 1.0) * 255),
                   int(_clamp(light.color[1], 0.0, 1.0) * 255),
                   int(_clamp(light.color[2], 0.0, 1.0) * 255))
        try:
            result = colorchooser.askcolor(title=title,
                                           initialcolor=initial,
                                           parent=root)
        except Exception as ex:
            Log.error(f"askcolor failed: {ex}")
            return
        if result is None or result[0] is None:
            return
        rgb255 = result[0]
        light.color = glm.vec3(rgb255[0] / 255.0,
                               rgb255[1] / 255.0,
                               rgb255[2] / 255.0)
        Log.info(f"Set {title} = ({light.color[0]:.2f}, "
                 f"{light.color[1]:.2f}, {light.color[2]:.2f})")

    def _edit_light_scalar(self, light, attribute: str,
                           minvalue: float, maxvalue: float,
                           label: Optional[str] = None):
        """Edit a numeric point/spot light property with a Unity-like dialog."""
        root = self._tk_root_window()
        if root is None:
            return
        try:
            from tkinter import simpledialog
        except ImportError:
            Log.error("tkinter.simpledialog not available.")
            return
        label = label or attribute.replace("_", " ").title()
        current = float(getattr(light, attribute))
        try:
            value = simpledialog.askfloat(
                f"Set {label}", f"{label} ({minvalue:g}..{maxvalue:g}):",
                initialvalue=current, parent=root,
                minvalue=minvalue, maxvalue=maxvalue)
        except Exception as ex:
            Log.error(f"askfloat failed: {ex}")
            return
        if value is None:
            return
        setattr(light, attribute, float(value))
        # Keep spot cones valid: Unity never allows Outer < Inner.
        if hasattr(light, "inner_angle") and hasattr(light, "outer_angle"):
            light.inner_angle = _clamp(float(light.inner_angle), 0.1, 179.0)
            light.outer_angle = _clamp(float(light.outer_angle), 0.1, 179.0)
            if attribute == "inner_angle" and light.outer_angle < light.inner_angle:
                light.outer_angle = light.inner_angle
            elif attribute == "outer_angle" and light.outer_angle < light.inner_angle:
                light.inner_angle = light.outer_angle
        Log.info(f"Set light.{attribute} = {getattr(light, attribute)}")

    # ----------------------------------------------- spawn point editors
    def _edit_spawn_scalar(self, node: SceneNode, key: str,
                           minvalue: float, maxvalue: float,
                           label: str, integer: bool = False):
        """Edit one Inspector value of a spawn point (health / bullets /
        speeds / pickup counts) with a Unity-style askfloat dialog."""
        root = self._tk_root_window()
        if root is None:
            return
        try:
            from tkinter import simpledialog
        except ImportError:
            Log.error("tkinter.simpledialog not available.")
            return
        current = float(node.spawn_config.get(key, 0.0))
        try:
            value = simpledialog.askfloat(
                f"Set {label}", f"{label} ({minvalue:g}..{maxvalue:g}):",
                initialvalue=current, parent=root,
                minvalue=minvalue, maxvalue=maxvalue)
        except Exception as ex:
            Log.error(f"askfloat failed: {ex}")
            return
        if value is None:
            return
        node.spawn_config[key] = int(round(value)) if integer \
            else float(value)
        Log.info(f"Set {node.name}.{key} = {node.spawn_config[key]}")

    def _edit_spawn_text(self, node: SceneNode):
        """Edit the Trigger Point's on-screen message."""
        root = self._tk_root_window()
        if root is None:
            return
        try:
            from tkinter import simpledialog
        except ImportError:
            Log.error("tkinter.simpledialog not available.")
            return
        current = str(node.spawn_config.get("text", "LEVEL COMPLETE"))
        try:
            value = simpledialog.askstring(
                "Set Trigger Text", "Text shown when the player crosses:",
                initialvalue=current, parent=root)
        except Exception as ex:
            Log.error(f"askstring failed: {ex}")
            return
        if value is None:
            return
        node.spawn_config["text"] = value if value else "LEVEL COMPLETE"
        Log.info(f"Set {node.name}.text = '{node.spawn_config['text']}'")

    def _edit_spawn_color(self, node: SceneNode):
        """Edit the Trigger Point's message color (native color picker)."""
        root = self._tk_root_window()
        if root is None:
            return
        try:
            from tkinter import colorchooser
        except ImportError:
            Log.error("tkinter.colorchooser not available.")
            return
        cur = node.spawn_config.get("color", [1.0, 0.85, 0.2])
        try:
            initial = "#%02x%02x%02x" % tuple(
                int(_clamp(float(cur[i]), 0.0, 1.0) * 255)
                for i in range(3))
            rgb, _hex = colorchooser.askcolor(color=initial,
                                              parent=root,
                                              title="Trigger Text Color")
        except Exception as ex:
            Log.error(f"colorchooser failed: {ex}")
            return
        if rgb is None:
            return
        node.spawn_config["color"] = [float(rgb[0]) / 255.0,
                                      float(rgb[1]) / 255.0,
                                      float(rgb[2]) / 255.0]
        Log.info(f"Set {node.name}.color = {node.spawn_config['color']}")

    def _change_spawn_mesh(self, node: SceneNode):
        """Swap an Enemy Spawn / pickup marker mesh for a custom imported
        .glb/.gltf model.  The model is auto-normalised to the marker's
        gameplay size (soccer ball for enemies, half-ball cube for
        pickups) so the swap never breaks scale, colliders or aim."""
        # [TRIPLEA:p3_spawn_mesh_dialog_filters]
        path = self._native_dialog(
            "open", "Import Custom Mesh",
            filetypes=[("All supported", "*.glb *.gltf *.obj *.blend"),
                       ("glTF binary", "*.glb"),
                       ("glTF", "*.gltf"),
                       ("Wavefront OBJ", "*.obj"),
                       ("Blender scene", "*.blend"),
                       ("All files", "*.*")])
        if not path:
            return
        # Preserve the marker's placement: the GLB importer ADDS the
        # model's own transform, so snapshot & restore afterwards.
        pos = glm.vec3(node.position)
        rot = glm.vec3(node.rotation_euler)
        # Drop children left over from a previous multi-part import.
        for c in list(node.children):
            self._delete_node(c)
        node.rotation_euler = glm.vec3(0.0)
        node.scale = glm.vec3(1.0)
        if not self.load_model_into(node, path):
            node.rotation_euler = rot
            return
        node.position = pos
        node.rotation_euler = rot
        # A custom mesh replaces the primitive marker geometry.
        node.mesh_type = None
        node.mesh_params = {}
        self._normalize_spawn_mesh_scale(node)
        Log.info(f"Spawn mesh for '{node.name}' replaced with "
                 f"{os.path.basename(path)} (auto-scaled).")

    def _spawn_mesh_local_aabb(self, node: SceneNode):
        """AABB of the node's ENTIRE mesh subtree (own mesh + imported
        child parts) expressed in the node's LOCAL frame — i.e. the frame
        colliders live in, so a collider built from it tracks the rendered
        mesh exactly through any node rotation / scale."""
        inv = glm.inverse(node.world_matrix())
        lo = glm.vec3(float("inf"))
        hi = glm.vec3(float("-inf"))
        found = False
        for child in node.traverse():
            if child.mesh is None:
                continue
            pos_arr, _idx = _mesh_cpu_triangles(child.mesh)
            if pos_arr is None or len(pos_arr) == 0:
                continue
            rel = inv * child.world_matrix()
            cols = np.array([[rel[c][r] for r in range(4)]
                             for c in range(4)], dtype=np.float32)
            hom = np.ones((len(pos_arr), 4), dtype=np.float32)
            hom[:, :3] = pos_arr
            pts = hom @ cols
            plo = pts[:, :3].min(axis=0)
            phi = pts[:, :3].max(axis=0)
            lo = glm.min(lo, glm.vec3(*plo))
            hi = glm.max(hi, glm.vec3(*phi))
            found = True
        return (lo, hi) if found else (None, None)

    def _fit_spawn_collider(self, node: SceneNode,
                            is_trigger: bool) -> Optional[BoxCollider]:
        """Rebuild the node's collider so its physics SHAPE matches its
        current mesh (called at Play time and after every mesh swap):
        the exact local-space bounding box of the rendered geometry."""
        lo, hi = self._spawn_mesh_local_aabb(node)
        if lo is None:
            return None
        size = glm.max(hi - lo, glm.vec3(0.02))
        center = (lo + hi) * 0.5
        node.collider = BoxCollider(
            size=(float(size.x), float(size.y), float(size.z)),
            center=(float(center.x), float(center.y), float(center.z)),
            is_trigger=is_trigger)
        return node.collider

    def _normalize_spawn_mesh_scale(self, node: SceneNode):
        """Uniform-scale a freshly imported spawn mesh so its largest
        world dimension matches the marker's gameplay size."""
        target = (SpawnGizmos.ENEMY_RADIUS * 2.0
                  if node.spawn_kind == "enemy"
                  else SpawnGizmos.PICKUP_SIZE)
        lo = glm.vec3(float("inf"))
        hi = glm.vec3(float("-inf"))
        found = False
        for child in node.traverse():
            if child.mesh is None:
                continue
            pos_arr, _idx = _mesh_cpu_triangles(child.mesh)
            if pos_arr is None or len(pos_arr) == 0:
                continue
            m = child.world_matrix()
            # Column-major glm mat4 as numpy: world = hom @ cols
            cols = np.array([[m[c][r] for r in range(4)]
                             for c in range(4)], dtype=np.float32)
            hom = np.ones((len(pos_arr), 4), dtype=np.float32)
            hom[:, :3] = pos_arr
            world = hom @ cols
            wlo = world[:, :3].min(axis=0)
            whi = world[:, :3].max(axis=0)
            lo = glm.min(lo, glm.vec3(*wlo))
            hi = glm.max(hi, glm.vec3(*whi))
            found = True
        if not found:
            return
        dims = hi - lo
        max_dim = max(dims.x, dims.y, dims.z)
        if max_dim <= 1e-6:
            return
        factor = target / max_dim
        node.scale = glm.vec3(node.scale.x * factor,
                              node.scale.y * factor,
                              node.scale.z * factor)

    # ----------------------------------------------- cloud settings editors
    def _toggle_clouds(self, settings: "CloudSettings"):
        settings.enabled = not settings.enabled
        Log.info(f"Clouds {'enabled' if settings.enabled else 'disabled'}")

    def _edit_cloud_scalar(self, settings: "CloudSettings", attribute: str,
                           minvalue: float, maxvalue: float):
        """Open an askfloat dialog and apply the value to a CloudSettings
        scalar (coverage / density / scale / altitude / speed)."""
        root = self._tk_root_window()
        if root is None:
            return
        try:
            from tkinter import simpledialog
        except ImportError:
            return
        current = float(getattr(settings, attribute))
        try:
            value = simpledialog.askfloat(
                f"Set cloud {attribute}", f"{attribute} ({minvalue}..{maxvalue}):",
                initialvalue=current, parent=root,
                minvalue=minvalue, maxvalue=maxvalue)
        except Exception as ex:
            Log.error(f"askfloat failed: {ex}")
            return
        if value is None:
            return
        setattr(settings, attribute, float(value))
        Log.info(f"Set clouds.{attribute} = {value}")

    def _edit_cloud_tint(self, settings: "CloudSettings"):
        """Open the native color chooser and apply it to the cloud tint."""
        root = self._tk_root_window()
        if root is None:
            return
        try:
            from tkinter import colorchooser
        except ImportError:
            return
        initial = (int(settings.tint[0] * 255),
                   int(settings.tint[1] * 255),
                   int(settings.tint[2] * 255))
        try:
            result = colorchooser.askcolor(title="Pick Cloud Tint",
                                           initialcolor=initial,
                                           parent=root)
        except Exception as ex:
            Log.error(f"askcolor failed: {ex}")
            return
        if result is None or result[0] is None:
            return
        rgb255 = result[0]
        settings.tint = glm.vec3(rgb255[0] / 255.0,
                                 rgb255[1] / 255.0,
                                 rgb255[2] / 255.0)
        Log.info(f"Set cloud tint = ({settings.tint[0]:.2f}, "
                 f"{settings.tint[1]:.2f}, {settings.tint[2]:.2f})")

    def _toggle_ocean(self, settings: "OceanSettings",
                      attribute: str = "enabled"):
        """Toggle a boolean OceanSettings flag (enabled / buoyancy_enabled)."""
        # [TRIPLEA:ocean_inspector_helpers]
        cur = bool(getattr(settings, attribute, True))
        setattr(settings, attribute, not cur)
        Log.info(f"Ocean {attribute} {'ON' if not cur else 'OFF'}")

    def _edit_ocean_scalar(self, settings: "OceanSettings", attribute: str,
                           minvalue: float, maxvalue: float):
        """Open an askfloat dialog and apply the value to an OceanSettings
        scalar (sea_level / wave_height / wave_speed / wind_strength /
        choppiness / foam_amount / buoyancy_strength)."""
        root = self._tk_root_window()
        if root is None:
            return
        try:
            from tkinter import simpledialog
        except ImportError:
            return
        current = float(getattr(settings, attribute))
        try:
            value = simpledialog.askfloat(
                f"Set ocean {attribute}",
                f"{attribute} ({minvalue}..{maxvalue}):",
                initialvalue=current, parent=root,
                minvalue=minvalue, maxvalue=maxvalue)
        except Exception as ex:
            Log.error(f"askfloat failed: {ex}")
            return
        if value is None:
            return
        setattr(settings, attribute, float(value))
        Log.info(f"Set ocean.{attribute} = {value}")

    def _edit_ocean_color(self, settings: "OceanSettings", attribute: str,
                          label: str):
        """Open the native colour chooser for an OceanSettings water colour.

        water_shallow and water_deep were already serialized in OceanSettings;
        this adds the missing Inspector editing path, using the same native
        colour dialog and 0..1 glm.vec3 convention as material/light colours.
        """
        root = self._tk_root_window()
        if root is None:
            return
        try:
            from tkinter import colorchooser
        except ImportError:
            return
        current = getattr(settings, attribute)
        initial = (int(_clamp(float(current[0]), 0.0, 1.0) * 255.0),
                   int(_clamp(float(current[1]), 0.0, 1.0) * 255.0),
                   int(_clamp(float(current[2]), 0.0, 1.0) * 255.0))
        try:
            rgb, _hex_value = colorchooser.askcolor(
                title=label, initialcolor=initial, parent=root)
        except Exception as ex:
            Log.error(f"colorchooser failed: {ex}")
            return
        if rgb is None:
            return
        setattr(settings, attribute,
                glm.vec3(float(rgb[0]) / 255.0,
                         float(rgb[1]) / 255.0,
                         float(rgb[2]) / 255.0))
        chosen = getattr(settings, attribute)
        Log.info(f"Set ocean.{attribute} = ({chosen[0]:.2f}, "
                 f"{chosen[1]:.2f}, {chosen[2]:.2f})")

    def _edit_material_scalar(self, material, attribute: str,
                              minvalue: float = 0.0, maxvalue: float = 1.0,
                              label: Optional[str] = None):
        """Open an askfloat for a material scalar (metallic / roughness /
        alpha_cutoff / emissive_intensity …)."""
        root = self._tk_root_window()
        if root is None:
            return
        try:
            from tkinter import simpledialog
        except ImportError:
            return
        label = label or attribute.replace("_", " ").title()
        current = float(getattr(material, attribute))
        try:
            value = simpledialog.askfloat(
                f"Set {label}", f"{label} ({minvalue:g}..{maxvalue:g}):",
                initialvalue=current, parent=root,
                minvalue=minvalue, maxvalue=maxvalue)
        except Exception as ex:
            Log.error(f"askfloat failed: {ex}")
            return
        if value is None:
            return
        setattr(material, attribute, float(value))
        Log.info(f"Set material '{material.name}.{attribute}' = {value}")

    def _edit_material_alpha(self, material):
        """Edit the base colour's alpha channel (the Transparency value).
        Setting alpha below 1.0 while the mode is OPAQUE reminds the user
        to switch the mode to BLEND — alpha alone doesn't blend."""
        root = self._tk_root_window()
        if root is None:
            return
        try:
            from tkinter import simpledialog
        except ImportError:
            return
        current = float(material.base_color[3])
        try:
            value = simpledialog.askfloat(
                "Set Transparency (Alpha)", "Alpha (0 = invisible .. 1 = solid):",
                initialvalue=current, parent=root,
                minvalue=0.0, maxvalue=1.0)
        except Exception as ex:
            Log.error(f"askfloat failed: {ex}")
            return
        if value is None:
            return
        bc = material.base_color
        material.base_color = (bc[0], bc[1], bc[2], float(value))
        Log.info(f"Set material '{material.name}' alpha = {value:.2f}")
        if value < 1.0 and (material.alpha_mode or "OPAQUE").upper() == "OPAQUE":
            Log.info("Alpha < 1 on an OPAQUE material has no visible effect "
                     "— click the Mode row and switch it to BLEND.")

    def _cycle_material_alpha_mode(self, material):
        """Cycle OPAQUE → MASK → BLEND → OPAQUE (glTF alphaMode)."""
        order = ["OPAQUE", "MASK", "BLEND"]
        cur = (material.alpha_mode or "OPAQUE").upper()
        if cur not in order:
            cur = "OPAQUE"
        material.alpha_mode = order[(order.index(cur) + 1) % len(order)]
        Log.info(f"Material '{material.name}' alpha mode = {material.alpha_mode}"
                 + (" (forward transparency pass)" if material.alpha_mode == "BLEND"
                    else ""))

    def _edit_material_emissive_color(self, material):
        """Native colour picker for the emissive (glow) colour."""
        root = self._tk_root_window()
        if root is None:
            return
        try:
            from tkinter import colorchooser
        except ImportError:
            Log.error("tkinter.colorchooser not available.")
            return
        initial = (int(_clamp(material.emissive[0], 0.0, 1.0) * 255),
                   int(_clamp(material.emissive[1], 0.0, 1.0) * 255),
                   int(_clamp(material.emissive[2], 0.0, 1.0) * 255))
        try:
            result = colorchooser.askcolor(title="Pick Emissive Color",
                                           initialcolor=initial,
                                           parent=root)
        except Exception as ex:
            Log.error(f"askcolor failed: {ex}")
            return
        if result is None or result[0] is None:
            return
        rgb255 = result[0]
        material.emissive = (rgb255[0] / 255.0,
                             rgb255[1] / 255.0,
                             rgb255[2] / 255.0)
        Log.info(f"Set material '{material.name}' emissive = "
                 f"({material.emissive[0]:.2f}, {material.emissive[1]:.2f}, "
                 f"{material.emissive[2]:.2f})")

    def _toggle_material_double_sided(self, material):
        material.double_sided = not material.double_sided
        Log.info(f"Material '{material.name}' double-sided = "
                 f"{'ON' if material.double_sided else 'OFF'}")

    # ------------------------------------------------ physics editors
    # The inspector's Physics section drives these.  Everything goes
    # through the engine's add_* conveniences (which register the node
    # with the live PhysicsWorld when playing), so edit-mode changes are
    # picked up by the next Play, and play-mode changes apply instantly.
    def _physics_reregister(self, node):
        """Force the PhysicsWorld to rebuild this node's entry after a
        component change (body type, trigger flag, shape size…).  At edit
        time nothing is registered yet — _begin_play() does a full
        rebuild() anyway."""
        if getattr(self, "_playing", False):
            self.physics.remove_node(node)
            self.physics.add_node(node)

    def _phys_add_collider(self, node, kind: str):
        if node.collider is not None:
            Log.warn(f"'{node.name}' already has a collider — remove it first.")
            return
        if kind == "box":
            self.add_box_collider(node)
        elif kind == "sphere":
            self.add_sphere_collider(node)
        elif kind == "capsule":
            self.add_capsule_collider(node)
        elif kind == "plane":
            self.add_plane_collider(node)
        elif kind == "mesh":
            if node.mesh is None:
                Log.warn(f"'{node.name}' has no mesh — a MeshCollider "
                         f"needs the node's own mesh for its triangles.")
                return
            self.add_mesh_collider(node)
        else:
            return
        Log.info(f"Added {kind} collider to '{node.name}' "
                 f"(static until you Add RigidBody)")

    def _phys_remove_collider(self, node):
        if self._playing:
            self.physics.remove_node(node)
        if (self.shape_editor is not None
                and self.shape_editor.node is node):
            self.shape_editor.exit()
        node.collider = None
        node.rigidbody = None   # a body without a shape is meaningless
        Log.info(f"Removed collider (and any rigidbody) from '{node.name}'")

    def _phys_add_rigidbody(self, node):
        if node.collider is None:
            Log.warn(f"'{node.name}' needs a collider first — "
                     f"a RigidBody requires a shape.")
            return
        if node.rigidbody is not None:
            Log.warn(f"'{node.name}' already has a RigidBody.")
            return
        self.add_rigidbody(node)
        Log.info(f"Added RigidBody to '{node.name}' "
                 f"(dynamic, mass {node.rigidbody.mass:g} kg)")

    def _phys_remove_rigidbody(self, node):
        node.rigidbody = None
        # Re-register as a static collider so it keeps blocking the world.
        self._physics_reregister(node)
        Log.info(f"Removed RigidBody from '{node.name}' (now static)")

    def _phys_cycle_body_type(self, node):
        rb = node.rigidbody
        if rb is None:
            return
        order = ["dynamic", "kinematic", "static"]
        cur = rb.body_type if rb.body_type in order else "dynamic"
        rb.body_type = order[(order.index(cur) + 1) % len(order)]
        # Body type changes registration semantics (state init, inertia),
        # so rebuild the entry instead of patching it in place.
        self._physics_reregister(node)
        Log.info(f"'{node.name}' body type = {rb.body_type}")

    def _phys_toggle_trigger(self, node):
        col = node.collider
        if col is None:
            return
        col.is_trigger = not col.is_trigger
        # Trigger state affects pair classification — drop stale pairs.
        self._physics_reregister(node)
        Log.info(f"'{node.name}' trigger = {'ON' if col.is_trigger else 'OFF'}")

    def _phys_toggle_freeze_rotation(self, node):
        rb = node.rigidbody
        if rb is None:
            return
        rb.freeze_rotation = not rb.freeze_rotation
        # Freeze changes the inertia tensor — rebuild the entry.
        self._physics_reregister(node)
        Log.info(f"'{node.name}' freeze rotation = "
                 f"{'ON' if rb.freeze_rotation else 'OFF'}")

    def _phys_edit_collider_scalar(self, node, attribute: str,
                                   minvalue: float, maxvalue: float,
                                   label: Optional[str] = None):
        col = node.collider
        if col is None:
            return
        root = self._tk_root_window()
        if root is None:
            return
        try:
            from tkinter import simpledialog
        except ImportError:
            return
        label = label or attribute.replace("_", " ").title()
        current = float(getattr(col, attribute))
        try:
            value = simpledialog.askfloat(
                f"Set {label}", f"{label} ({minvalue:g}..{maxvalue:g}):",
                initialvalue=current, parent=root,
                minvalue=minvalue, maxvalue=maxvalue)
        except Exception as ex:
            Log.error(f"askfloat failed: {ex}")
            return
        if value is None:
            return
        setattr(col, attribute, float(value))
        self._physics_reregister(node)
        Log.info(f"Set '{node.name}' collider.{attribute} = {value}")

    def _phys_edit_body_scalar(self, node, attribute: str,
                               minvalue: float, maxvalue: float,
                               label: Optional[str] = None):
        rb = node.rigidbody
        if rb is None:
            return
        root = self._tk_root_window()
        if root is None:
            return
        try:
            from tkinter import simpledialog
        except ImportError:
            return
        label = label or attribute.replace("_", " ").title()
        current = float(getattr(rb, attribute))
        try:
            value = simpledialog.askfloat(
                f"Set {label}", f"{label} ({minvalue:g}..{maxvalue:g}):",
                initialvalue=current, parent=root,
                minvalue=minvalue, maxvalue=maxvalue)
        except Exception as ex:
            Log.error(f"askfloat failed: {ex}")
            return
        if value is None:
            return
        setattr(rb, attribute, float(value))
        if attribute == "mass":
            rb.mass = max(1e-4, rb.mass)   # RigidBody invariant
        self._physics_reregister(node)
        Log.info(f"Set '{node.name}' rigidbody.{attribute} = {value}")

    def _phys_edit_shape(self, node):
        """Toggle Unity-style collider edit mode: the green debug outline
        appears in the viewport with grab handles you drag to reshape the
        collider.  Press the button again (now labelled "Done") or ESC to
        leave edit mode."""
        if node.collider is None or self.shape_editor is None:
            return
        if (self.shape_editor.active
                and self.shape_editor.node is node):
            self.shape_editor.exit()
            Log.info("Collider edit mode OFF")
        else:
            self.shape_editor.enter(node)
            Log.info("Collider edit mode ON — drag the green square "
                     "handles to reshape (ESC / Done exits)")

    def _phys_edit_shape_dialog(self, node):
        """Edit the collider's dimensions numerically.  Sphere/plane take
        one number; box takes 'x,y,z'; capsule takes 'radius,height'."""
        col = node.collider
        if col is None:
            return
        root = self._tk_root_window()
        if root is None:
            return
        try:
            from tkinter import simpledialog
        except ImportError:
            return
        try:
            if isinstance(col, BoxCollider):
                s = simpledialog.askstring(
                    "Set Box Size", "Size x,y,z:",
                    initialvalue=f"{col.size.x:.3g},{col.size.y:.3g},{col.size.z:.3g}",
                    parent=root)
                if s is None:
                    return
                parts = [max(1e-4, float(v.strip()))
                         for v in s.split(",")]
                if len(parts) != 3:
                    raise ValueError("need exactly 3 values")
                col.size = glm.vec3(*parts)
            elif isinstance(col, SphereCollider):
                v = simpledialog.askfloat(
                    "Set Radius", "Radius:",
                    initialvalue=float(col.radius), parent=root,
                    minvalue=0.0001, maxvalue=10000.0)
                if v is None:
                    return
                col.radius = float(v)
            elif isinstance(col, CapsuleCollider):
                s = simpledialog.askstring(
                    "Set Capsule", "Radius,height (total height incl. caps):",
                    initialvalue=f"{col.radius:.3g},{col.height:.3g}",
                    parent=root)
                if s is None:
                    return
                parts = [float(v.strip()) for v in s.split(",")]
                if len(parts) != 2:
                    raise ValueError("need exactly 2 values")
                col.radius = max(1e-4, parts[0])
                col.height = max(col.radius * 2.0, parts[1])
            elif isinstance(col, PlaneCollider):
                v = simpledialog.askfloat(
                    "Set Plane Size", "Size (0 = infinite):",
                    initialvalue=float(col.size), parent=root,
                    minvalue=0.0, maxvalue=100000.0)
                if v is None:
                    return
                col.size = float(v)
            else:
                Log.info(f"{type(col).__name__} has no editable dimensions.")
                return
        except (ValueError, AttributeError) as ex:
            Log.error(f"Bad shape value: {ex}")
            return
        self._physics_reregister(node)
        Log.info(f"Updated '{node.name}' collider shape")

    def save_project_dialog(self):
        """Toolbar action: open a Save-As dialog, then save."""
        path = self._native_dialog(
            "save", "Save Heaven3D Project",
            filetypes=[("Heaven3D project", "*.heaven3d"),
                       ("All files", "*.*")],
            defaultext=".heaven3d")
        if path:
            self.save_project(path)

    def load_project_dialog(self):
        """Toolbar action: open an Open dialog, then load."""
        path = self._native_dialog(
            "open", "Load Heaven3D Project",
            filetypes=[("Heaven3D project", "*.heaven3d"),
                       ("All files", "*.*")])
        if path:
            self.load_project(path)
            if self.ui:
                self.ui.refresh_inspector() if hasattr(self.ui, "refresh_inspector") else None

    def export_runtime_dialog(self):
        """Toolbar action: choose where to export the runtime build."""
        path = self._native_dialog(
            "save", "Export Runtime Build",
            filetypes=[("Heaven3D project", "*.heaven3d"),
                       ("All files", "*.*")],
            defaultext=".heaven3d")
        if path:
            old = self.project_path
            self.save_project(path)
            self.project_path = old
            Log.info(f"Exported runtime project: {path}")

    # ------------------------------------------------------------ UI build
    def _build_ui(self):
        if self.ui is None:
            return
        self.ui.begin_frame()
        T = THEME

        # ==================================================================
        #  TOP NAVBAR  — square panel, 2px separator, centred pill buttons
        # ==================================================================
        self.ui.panel(UIRect(0, 0.93, 1.0, 0.07), T["panel_dark"])
        # 2px separator strip under the navbar (subtle depth, VS Code style)
        self.ui.panel(UIRect(0, 0.928, 1.0, 0.002), T["border"])
        self.ui.text(UIRect(0.010, 0.947, 0.20, 0.036),
                     f"Heaven3D v{HEAVEN_VERSION}   |   FPS: {self._fps:.0f}",
                     T["text"])
        x = 0.205   # [TERRAINPATCH:navbar_slot] narrowed: room for Terrain
        bw = 0.066  # [TERRAINPATCH:navbar_slot]
        gap = 0.005 # [TERRAINPATCH:navbar_slot]
        btn_y, btn_h = 0.944, 0.042   # vertically centred in the 0.93..1.0 strip
        def _btn(label, cb, color=T["button"]):
            nonlocal x
            self.ui.button(UIRect(x, btn_y, bw, btn_h), label, cb, color)
            x += bw + gap
        play_color = T["play_green"] if not self._playing else T["danger"]
        # Remember the Play/Stop button's rect: while playing it is the
        # ONLY clickable control in the entire UI (play-mode UI lock).
        self._play_stop_rect = UIRect(x, btn_y, bw, btn_h)
        _btn("Play" if not self._playing else "Stop", self.toggle_play, play_color)
        _btn("Save",   self.save_project_dialog)
        _btn("Load",   self.load_project_dialog)
        _btn("Export", self.export_runtime_dialog)
        _btn("Cube",   lambda: self.add_cube())
        _btn("Plane",  lambda: self.add_plane())
        _btn("Sphere", lambda: self.add_sphere())
        _btn("Import", self.import_glb_dialog)
        _btn("Library", self.open_library_menu, T["accent_dim"])
        # [BUTTONBACK] Particle shrinks from 0.066 to 0.038 so the blue
        # Terrain button fits back in its old slot BETWEEN Particle and the
        # BUILDING EDITOR button (drawn by the FinalPatch wrap at x=0.935).
        # Layout math: Particle 0.844..0.882, Terrain 0.887..0.930,
        # standard 0.005 gaps, nothing overlaps the Building button.
        self.ui.button(UIRect(x, btn_y, 0.038, btn_h), "Particle",
                       self.toggle_particle_studio, _hex("#8B5A2B"))  # [GREATUPDATE:particle_button]
        x += 0.038 + gap
        self.ui.button(UIRect(x, btn_y, 0.043, btn_h), "Terrain",
                       self.toggle_terrain_editor, _hex("#2E7CD6"))  # [BUTTONBACK:terrain_button]
        x += 0.043 + gap

        # ==================================================================
        #  HIERARCHY (left) — padded, rounded, left-aligned rows
        # ==================================================================
        self.ui.panel(UIRect(0, 0.0, 0.18, 0.93), T["panel"])
        self.ui.panel(UIRect(0.1785, 0.0, 0.0015, 0.93), T["border"])  # separator
        self.ui.text(UIRect(0.009, 0.878, 0.16, 0.032), "Hierarchy", T["text"])
        # Body scrolls when there are more objects than fit.  Reserve a
        # sliver on the right for the scrollbar.
        self.ui.begin_scroll("hierarchy", UIRect(0.005, 0.020, 0.168, 0.850))
        row_h, row_step = 0.032, 0.038   # 29px row + 5px gap @900p
        y = 0.832
        content_top = y

        def _emit_outliner_node(node, depth=0):
            """Draw the full scene tree, including imported mesh children.

            The old Outliner stopped at direct children of __root__, so every
            node inside an imported model was invisible and looked unselected /
            broken.  Selection still resolves to the model root through
            select_node(), preserving the editor's whole-object behaviour.
            """
            nonlocal y
            if node.name.startswith("__"):
                return
            is_sel = node is self.selected
            is_visible = bool(getattr(node, "visible", True))
            label = ("    " * depth) + node.name
            self.ui.button(
                UIRect(0.009, y, 0.149, row_h), label,
                lambda n=node: self.select_node(n),
                T["selected"] if is_sel else T["button"],
                align="left", pad_x_px=16.8,
                text_color=(T["text_bright"] if is_sel else
                            (T["text"] if is_visible else T["text_dim"])))
            y -= row_step
            for child in node.children:
                if getattr(child, "_heaven3d_imported_part", False):
                    continue
                _emit_outliner_node(child, depth + 1)

        for node in self.scene.root.children:
            _emit_outliner_node(node)
        self.ui.end_scroll(y, content_top)

        # ==================================================================
        #  INSPECTOR (right) — sections, rounded value fields, airy spacing
        # ==================================================================
        self.ui.panel(UIRect(0.82, 0.0, 0.18, 0.93), T["panel"])
        self.ui.panel(UIRect(0.8200, 0.0, 0.0015, 0.93), T["border"])  # separator
        self.ui.text(UIRect(0.830, 0.878, 0.16, 0.032), "Inspector", T["text"])
        # The inspector body scrolls when the selected object has more
        # properties than fit on screen (long blueprints, many mesh stats…).
        self.ui.begin_scroll("inspector", UIRect(0.820, 0.020, 0.173, 0.850))
        if self.selected:
            n = self.selected
            # ---- layout metrics (normalised; ~px equivalents @900p) ----
            pad_x = 0.830         # 16px left inset from the panel edge
            col_w = 0.154         # right edge 0.984 — clears the scrollbar
            line_h = 0.032        # 29px row height
            row_gap = 0.005       # ~5px breathing room between rows
            section_gap = 0.024   # larger gap between sections
            y = 0.832
            content_top = y

            # -- three tiny layout helpers keep every row on the same grid --
            def _header(label, color=T["accent"]):
                nonlocal y
                self.ui.text(UIRect(pad_x, y, col_w, line_h), label, color)
                y -= line_h + 0.006

            def _row_text(label, value=None, color=T["text"]):
                nonlocal y
                txt = label if value is None else f"{label}  {value}"
                self.ui.text(UIRect(pad_x, y, col_w, line_h), txt, color)
                y -= line_h + row_gap

            def _row_button(label, cb, color=T["button"], align="left"):
                nonlocal y
                self.ui.button(UIRect(pad_x, y, col_w, line_h),
                               label, cb, color, align=align)
                y -= line_h + row_gap

            # Header: object name (the active element → bright white)
            _row_text(n.name, None, T["text_bright"])
            y -= section_gap * 0.5

            # ---- Transform section ----
            # Each value is a clickable field → _edit_transform_value opens
            # a native askfloat dialog and applies the entered value.
            _header("Transform")
            def _tf_btn(attr, axis, label, value):
                cb = lambda a=attr, ax=axis: self._edit_transform_value(n, a, ax)
                _row_button(f"{label}  {value:+.2f}", cb)
            _tf_btn("position", 0, "X", n.position.x)
            _tf_btn("position", 1, "Y", n.position.y)
            _tf_btn("position", 2, "Z", n.position.z)
            y -= 0.009   # sub-group gap: XYZ / rotation / scale read as 3 groups
            _tf_btn("rotation_euler", 0, "Pitch", n.rotation_euler.x)
            _tf_btn("rotation_euler", 1, "Yaw",   n.rotation_euler.y)
            _tf_btn("rotation_euler", 2, "Roll",  n.rotation_euler.z)
            y -= 0.009
            _tf_btn("scale", 0, "Scale X", n.scale.x)
            _tf_btn("scale", 1, "Scale Y", n.scale.y)
            _tf_btn("scale", 2, "Scale Z", n.scale.z)
            y -= section_gap

            # Spawn markers expose ONLY their gameplay values in the
            # Inspector — everything else (material, physics, mesh stats)
            # is intentionally hidden so the panel stays focused.
            _skip_spawn = bool(getattr(n, "spawn_kind", None))

            # ---- Player Spawn section ----
            if getattr(n, "spawn_kind", None) == "player":
                cfg = n.spawn_config
                _header("Player Spawn", T["green_text"])
                _row_text("FPS controller — Unity/Blender size", None,
                          T["text_dim"])
                _row_text("Capsule r 0.35  h 1.80 (fixed)", None,
                          T["text_dim"])
                def _p_btn(label, key, lo, hi, integer=False):
                    cb = lambda k=key, a=lo, b=hi, l=label, i=integer: \
                        self._edit_spawn_scalar(n, k, a, b, l, i)
                    val = cfg.get(key, 0)
                    txt = f"{label}  {int(val) if integer else f'{float(val):.3g}'}"
                    _row_button(txt, cb)
                _p_btn("Health Value", "health", 1.0, 100000.0)
                _p_btn("Bullet Value", "bullets", 0.0, 100000.0, True)
                _p_btn("Walk Speed", "walk_speed", 0.1, 100.0)
                _p_btn("Run Speed", "run_speed", 0.1, 200.0)
                _p_btn("Cam Rot Speed", "cam_rot_speed", 0.01, 5.0)
                y -= section_gap

            # ---- Enemy Spawn section ----
            elif getattr(n, "spawn_kind", None) == "enemy":
                cfg = n.spawn_config
                _header("Enemy Spawn", T["green_text"])
                _row_text("Flying sphere — soccer-ball size", None,
                          T["text_dim"])
                def _e_btn(label, key, lo, hi, integer=False):
                    cb = lambda k=key, a=lo, b=hi, l=label, i=integer: \
                        self._edit_spawn_scalar(n, k, a, b, l, i)
                    val = cfg.get(key, 0)
                    txt = f"{label}  {int(val) if integer else f'{float(val):.3g}'}"
                    _row_button(txt, cb)
                _e_btn("Health Value", "health", 1.0, 100000.0)
                _e_btn("Bullet Value", "bullet_damage", 0.0, 100000.0)
                _e_btn("Fly Speed", "fly_speed", 0.0, 200.0)
                _e_btn("Cam Rot Speed", "cam_rot_speed", 1.0, 1440.0)
                _e_btn("Shots / Sec", "shots_per_sec", 0.05, 60.0)
                _row_button("Change Mesh...",
                            lambda node=n: self._change_spawn_mesh(node),
                            T["accent_dim"])
                y -= section_gap

            # ---- Pickup sections ----
            elif getattr(n, "spawn_kind", None) in ("bullet_pickup",
                                                    "health_pickup"):
                cfg = n.spawn_config
                is_bullet = n.spawn_kind == "bullet_pickup"
                _header("Bullet Pickup" if is_bullet else "Health Pickup",
                        T["green_text"])
                _row_text("On-contact pickup — half-ball cube", None,
                          T["text_dim"])
                if is_bullet:
                    _row_button(
                        f"Bullet Count  {int(cfg.get('bullets', 60))}",
                        lambda: self._edit_spawn_scalar(
                            n, "bullets", 0.0, 100000.0,
                            "Bullet Count", True))
                else:
                    _row_button(
                        f"Health Count  {float(cfg.get('health', 30.0)):.3g}",
                        lambda: self._edit_spawn_scalar(
                            n, "health", 0.0, 100000.0, "Health Count"))
                _row_button("Change Mesh...",
                            lambda node=n: self._change_spawn_mesh(node),
                            T["accent_dim"])
                y -= section_gap

            # ---- Trigger Point section ----
            elif getattr(n, "spawn_kind", None) == "trigger":
                cfg = n.spawn_config
                _header("Trigger Point", T["green_text"])
                _row_text("Stops the game on player contact", None,
                          T["text_dim"])
                _txt = str(cfg.get("text", "LEVEL COMPLETE"))
                if len(_txt) > 18:
                    _txt = _txt[:17] + "…"
                _row_button(f"Text  {_txt}",
                            lambda node=n: self._edit_spawn_text(node))
                _col = cfg.get("color", [1.0, 0.85, 0.2])
                _row_button(
                    f"Color  ({float(_col[0]):.2f}, {float(_col[1]):.2f},"
                    f" {float(_col[2]):.2f})",
                    lambda node=n: self._edit_spawn_color(node))
                _row_button(f"Font Size  {int(cfg.get('font_size', 64))}",
                            lambda node=n: self._edit_spawn_scalar(
                                node, "font_size", 8.0, 256.0,
                                "Font Size", True))
                y -= section_gap

            # ---- Sound Point section ----  [AUDIOPATCH:sound_point]
            elif getattr(n, "spawn_kind", None) == "sound_point":
                cfg = n.spawn_config
                _header("Sound Point", T["accent"])
                _row_text("Plays a sound/music file in the game", None,
                          T["text_dim"])
                _snd = str(cfg.get("sound_path", "") or "")
                _slabel = os.path.basename(_snd) if _snd else "(choose file)"
                if len(_slabel) > 20:
                    _slabel = _slabel[:19] + "…"
                _row_button(f"Sound  {_slabel}",
                            lambda node=n: self._edit_sound_path(node))
                _row_button(
                    f"Volume  {_snd_float(cfg.get('volume', 0.9), 0.9):.2f}",
                    lambda node=n: self._edit_spawn_scalar(
                        node, "volume", 0.0, 1.0, "Volume"))
                _row_button(
                    "Trigger Radius  "
                    f"{_snd_float(cfg.get('radius', 3.0), 3.0):.1f} m",
                    lambda node=n: self._edit_spawn_scalar(
                        node, "radius", 0.5, 100.0, "Trigger Radius"))
                _sml = {"off": "Off", "once": "Play Once",
                        "loop": "Play as Loop"}
                _row_button(
                    "On Start Play  "
                    + _sml.get(str(cfg.get("start_mode", "off")), "Off"),
                    lambda node=n: self._cycle_sound_mode(node,
                                                          "start_mode"))
                _row_button(
                    "On Trigger Play  "
                    + _sml.get(str(cfg.get("trigger_mode", "once")),
                               "Play Once"),
                    lambda node=n: self._cycle_sound_mode(node,
                                                          "trigger_mode"))
                y -= section_gap

            # ---- Material section ----
            if n.material and not _skip_spawn:
                m = n.material
                _header("Material")
                _row_text("Name", m.name)
                _row_button(f"Metal {m.metallic:.2f}",
                            lambda mat=m: self._edit_material_scalar(mat, "metallic"))
                _row_button(f"Rough {m.roughness:.2f}",
                            lambda mat=m: self._edit_material_scalar(mat, "roughness"))
                # Color picker field — the background doubles as a swatch
                # (kept dim so the label stays readable).
                bc = m.base_color
                swatch = (bc[0] * 0.6, bc[1] * 0.6, bc[2] * 0.6, 1.0)
                _row_button(f"Color {bc[0]:.2f} {bc[1]:.2f} {bc[2]:.2f}",
                            lambda mat=m: self._edit_material_color(mat), swatch)
                y -= 0.009
                # -- Emission (glow) ----------------------------------
                em = m.emissive
                eswatch = (em[0] * 0.6, em[1] * 0.6, em[2] * 0.6, 1.0)
                _row_button(f"Emis {em[0]:.2f} {em[1]:.2f} {em[2]:.2f}",
                            lambda mat=m: self._edit_material_emissive_color(mat),
                            eswatch)
                _row_button(f"Emis Intens {m.emissive_intensity:.2f}",
                            lambda mat=m: self._edit_material_scalar(
                                mat, "emissive_intensity", 0.0, 100.0,
                                "Emissive Intensity"))
                y -= 0.009
                # -- Transparency --------------------------------------
                _row_button(f"Alpha {bc[3]:.2f}",
                            lambda mat=m: self._edit_material_alpha(mat))
                _mode = (m.alpha_mode or "OPAQUE").upper()
                _mode_color = T["accent_dim"] if _mode == "BLEND" else T["button"]
                _row_button(f"Mode {_mode}",
                            lambda mat=m: self._cycle_material_alpha_mode(mat),
                            _mode_color)
                if _mode != "OPAQUE":
                    _row_button(f"Cutoff {m.alpha_cutoff:.2f}",
                                lambda mat=m: self._edit_material_scalar(
                                    mat, "alpha_cutoff", 0.0, 1.0,
                                    "Alpha Cutoff"))
                _row_button(
                    f"2-Sided {'ON' if m.double_sided else 'OFF'}",
                    lambda mat=m: self._toggle_material_double_sided(mat),
                    T["play_green"] if m.double_sided else T["button"])
                # [TRIPLEA:p3_gif_inspector_rows] Animated-GIF texture rows —
                # planes only.  The ("path", p) descriptor round-trips
                # through Material.to_dict / ProjectSerializer unchanged, so
                # GIF assignments survive project save/load.
                if getattr(n, "mesh_type", None) == "plane":
                    _gif_path = m.base_color_tex
                    if isinstance(_gif_path, tuple) and len(_gif_path) == 2:
                        _gif_path = _gif_path[1]
                    _is_gif = (isinstance(_gif_path, str)
                               and _gif_path.lower().endswith(".gif"))
                    _gif_lbl = (f"GIF…  {os.path.basename(_gif_path)}"
                                if _is_gif else "GIF…")
                    _row_button(_gif_lbl,
                                lambda node=n: self._plane_gif_dialog(node),
                                T["accent_dim"])
                    if m.base_color_tex:
                        _row_button("Clear GIF",
                                    lambda node=n: self._clear_plane_gif(node))
                y -= section_gap

            # ---- Blueprint section ----
            if n.blueprint and not _skip_spawn:
                _header("Blueprint", T["green_text"])
                _row_text(n.blueprint.__class__.__name__)
                # Show a few properties if present
                for key, value in list(n.blueprint.properties.items())[:3]:
                    _row_text(f"{key}: {value}", None, T["text_dim"])
                y -= section_gap

            # ---- Sun Light section ----
            if n.is_sun and n.sun_light:
                sl = n.sun_light
                _header("Sun Light", T["green_text"])
                _row_text("Intensity", f"{sl.intensity:.2f}")
                _row_text("ShadowMap", f"{sl.shadow_map_size}px", T["text_dim"])
                _row_text("Bias", f"{sl.shadow_bias:.4f}", T["text_dim"])
                sc = sl.color
                swatch = (sc[0] * 0.6, sc[1] * 0.6, sc[2] * 0.6, 1.0)
                _row_button(f"Color {sc[0]:.2f} {sc[1]:.2f} {sc[2]:.2f}",
                            lambda light=sl: self._edit_sun_color(light), swatch)
                y -= section_gap

            # ---- Point Light section ----
            if n.is_point_light and n.point_light:
                pl = n.point_light
                _header("Point Light", T["green_text"])
                _row_button(f"Intensity  {pl.intensity:.2f}",
                            lambda light=pl: self._edit_light_scalar(
                                light, "intensity", 0.0, 1000.0, "Intensity"))
                _row_button(f"Range      {pl.range:.2f}",
                            lambda light=pl: self._edit_light_scalar(
                                light, "range", 0.01, 1000.0, "Range"))
                pc = pl.color
                swatch = (pc[0] * 0.6, pc[1] * 0.6, pc[2] * 0.6, 1.0)
                _row_button(f"Color {pc[0]:.2f} {pc[1]:.2f} {pc[2]:.2f}",
                            lambda light=pl: self._edit_light_color(
                                light, "Point Light Color"), swatch)
                y -= section_gap

            # ---- Spot Light section ----
            if n.is_spot_light and n.spot_light:
                sl2 = n.spot_light
                _header("Spot Light", T["green_text"])
                def _spot_scalar(label, attr, lo, hi):
                    cb = lambda light=sl2, a=attr, mn=lo, mx=hi, l=label: \
                        self._edit_light_scalar(light, a, mn, mx, l)
                    _row_button(f"{label:<10} {getattr(sl2, attr):.2f}", cb)
                _spot_scalar("Intensity", "intensity", 0.0, 1000.0)
                _spot_scalar("Range", "range", 0.01, 1000.0)
                _spot_scalar("Inner", "inner_angle", 0.1, 179.0)
                _spot_scalar("Outer", "outer_angle", 0.1, 179.0)
                sc2 = sl2.color
                swatch = (sc2[0] * 0.6, sc2[1] * 0.6, sc2[2] * 0.6, 1.0)
                _row_button(f"Color {sc2[0]:.2f} {sc2[1]:.2f} {sc2[2]:.2f}",
                            lambda light=sl2: self._edit_light_color(
                                light, "Spot Light Color"), swatch)
                y -= section_gap

            # ---- Clouds section ----
            if n.is_clouds and n.cloud_settings:
                cs = n.cloud_settings
                _header("Clouds", T["green_text"])
                _row_button(f"Enabled  {'ON' if cs.enabled else 'OFF'}",
                            lambda s=cs: self._toggle_clouds(s),
                            T["play_green"] if cs.enabled else T["button"],
                            align="center")
                def _cloud_btn(label, attr, mn, mx):
                    cb = lambda a=attr, lo=mn, hi=mx: \
                        self._edit_cloud_scalar(cs, a, lo, hi)
                    _row_button(f"{label}  {getattr(cs, attr):.3g}", cb)
                _cloud_btn("Coverage", "coverage", 0.0, 1.0)
                _cloud_btn("Density",  "density",  0.0, 1.5)
                _cloud_btn("Scale",    "scale",    0.1, 8.0)
                _cloud_btn("Altitude", "altitude", 50.0, 5000.0)
                _cloud_btn("Speed",    "speed",    0.0, 0.2)
                tc = cs.tint
                swatch = (tc[0] * 0.6, tc[1] * 0.6, tc[2] * 0.6, 1.0)
                _row_button(f"Tint {tc[0]:.2f} {tc[1]:.2f} {tc[2]:.2f}",
                            lambda s=cs: self._edit_cloud_tint(s), swatch)
                y -= section_gap
            # ---- Ocean section ----  # [TRIPLEA:ocean_inspector_section]
            if n.is_ocean and n.ocean_settings:
                ocn = n.ocean_settings
                _header("Ocean", T["green_text"])
                _row_button(f"Enabled  {'ON' if ocn.enabled else 'OFF'}",
                            lambda s=ocn: self._toggle_ocean(s, "enabled"),
                            T["play_green"] if ocn.enabled else T["button"],
                            align="center")
                _row_button(
                    f"Buoyancy  {'ON' if ocn.buoyancy_enabled else 'OFF'}",
                    lambda s=ocn: self._toggle_ocean(s, "buoyancy_enabled"),
                    T["play_green"] if ocn.buoyancy_enabled else T["button"],
                    align="center")
                def _ocean_btn(label, attr, mn, mx):
                    ob = lambda a=attr, lo=mn, hi=mx: \
                        self._edit_ocean_scalar(ocn, a, lo, hi)
                    _row_button(f"{label}  {getattr(ocn, attr):.3g}", ob)
                _ocean_btn("Sea Level",     "sea_level",         -200.0, 500.0)
                _ocean_btn("Wave Height",   "wave_height",          0.0,   5.0)
                _ocean_btn("Wave Speed",    "wave_speed",           0.0,   5.0)
                _ocean_btn("Wind Strength", "wind_strength",        0.0,   3.0)
                _ocean_btn("Choppiness",    "choppiness",           0.0,   2.0)
                _ocean_btn("Foam",          "foam_amount",          0.0,   3.0)
                # Native colour pickers for the two serialized water colours.
                sc = ocn.water_shallow
                sswatch = (sc[0] * 0.6, sc[1] * 0.6, sc[2] * 0.6, 1.0)
                _row_button(
                    f"Shallow  {sc[0]:.2f} {sc[1]:.2f} {sc[2]:.2f}",
                    lambda s=ocn: self._edit_ocean_color(
                        s, "water_shallow", "Shallow Water Color"), sswatch)
                dc = ocn.water_deep
                dswatch = (dc[0] * 0.6, dc[1] * 0.6, dc[2] * 0.6, 1.0)
                _row_button(
                    f"Deep     {dc[0]:.2f} {dc[1]:.2f} {dc[2]:.2f}",
                    lambda s=ocn: self._edit_ocean_color(
                        s, "water_deep", "Deep Water Color"), dswatch)
                _ocean_btn("Buoyancy Str",  "buoyancy_strength",    0.0,   5.0)
                y -= section_gap

            # ---- Mesh section ----
            if n.mesh is not None and not _skip_spawn:
                _header("Mesh")
                kind = n.mesh_type or ("glb" if n.source_path else "mesh")
                _row_text("Type",  kind)
                _row_text("Verts", n.mesh.vertex_count, T["text_dim"])
                _row_text("Tris",  n.mesh.index_count // 3, T["text_dim"])
                y -= section_gap

            # ---- Physics section ----
            # Assign colliders / rigid bodies directly from the inspector.
            # A collider alone is STATIC level geometry; adding a RigidBody
            # makes it dynamic/kinematic.  Box/Sphere auto-size from the
            # primitive mesh where possible (engine add_* conveniences).
            if not _skip_spawn:
                _header("Physics", T["green_text"])
            if _skip_spawn:
                pass
            elif n.collider is None:
                half = (col_w - 0.008) * 0.5
                self.ui.button(UIRect(pad_x, y, half, line_h), "+ Box",
                               lambda node=n: self._phys_add_collider(node, "box"),
                               T["button"], align="center")
                self.ui.button(UIRect(pad_x + half + 0.008, y, half, line_h),
                               "+ Sphere",
                               lambda node=n: self._phys_add_collider(node, "sphere"),
                               T["button"], align="center")
                y -= line_h + row_gap
                self.ui.button(UIRect(pad_x, y, half, line_h), "+ Capsule",
                               lambda node=n: self._phys_add_collider(node, "capsule"),
                               T["button"], align="center")
                self.ui.button(UIRect(pad_x + half + 0.008, y, half, line_h),
                               "+ Plane",
                               lambda node=n: self._phys_add_collider(node, "plane"),
                               T["button"], align="center")
                y -= line_h + row_gap
                # Exact triangle collision from the node's own mesh — for
                # low-poly buildings with interiors etc.  Only offered when
                # the node actually HAS a mesh to source triangles from.
                if n.mesh is not None:
                    self.ui.button(
                        UIRect(pad_x, y, col_w, line_h),
                        "+ Mesh (exact triangles)",
                        lambda node=n: self._phys_add_collider(node, "mesh"),
                        T["button"], align="center")
                    y -= line_h + row_gap
            else:
                col = n.collider
                _row_text("Collider",
                          type(col).__name__.replace("Collider", ""))
                if not isinstance(col, HeightfieldCollider):
                    # "Edit Shape" toggles the Unity-style green outline +
                    # grab handles in the viewport; "Size..." keeps the old
                    # numeric dialog for exact values.
                    editing = (self.shape_editor is not None
                               and self.shape_editor.active
                               and self.shape_editor.node is n)
                    half = (col_w - 0.008) * 0.5
                    self.ui.button(
                        UIRect(pad_x, y, half, line_h),
                        "Done" if editing else "Edit Shape",
                        lambda node=n: self._phys_edit_shape(node),
                        T["play_green"] if editing else T["button"],
                        align="center")
                    self.ui.button(
                        UIRect(pad_x + half + 0.008, y, half, line_h),
                        "Size...",
                        lambda node=n: self._phys_edit_shape_dialog(node),
                        T["button"], align="center")
                    y -= line_h + row_gap
                _row_button(f"Trigger {'ON' if col.is_trigger else 'OFF'}",
                            lambda node=n: self._phys_toggle_trigger(node),
                            T["play_green"] if col.is_trigger else T["button"])
                _row_button(f"Friction {col.friction:.2f}",
                            lambda node=n: self._phys_edit_collider_scalar(
                                node, "friction", 0.0, 4.0, "Friction"))
                _row_button(f"Bounce {col.restitution:.2f}",
                            lambda node=n: self._phys_edit_collider_scalar(
                                node, "restitution", 0.0, 1.0,
                                "Restitution (Bounce)"))
                if n.rigidbody is None:
                    _row_button("Add RigidBody",
                                lambda node=n: self._phys_add_rigidbody(node),
                                T["accent_dim"], align="center")
                else:
                    rb = n.rigidbody
                    _row_button(f"Body {rb.body_type.title()}",
                                lambda node=n: self._phys_cycle_body_type(node),
                                T["accent_dim"])
                    _row_button(f"Mass {rb.mass:.2f}",
                                lambda node=n: self._phys_edit_body_scalar(
                                    node, "mass", 0.001, 10000.0, "Mass (kg)"))
                    _row_button(f"Gravity {rb.gravity_scale:.2f}",
                                lambda node=n: self._phys_edit_body_scalar(
                                    node, "gravity_scale", -10.0, 10.0,
                                    "Gravity Scale"))
                    _row_button(f"Lin Damp {rb.linear_damping:.2f}",
                                lambda node=n: self._phys_edit_body_scalar(
                                    node, "linear_damping", 0.0, 1.0,
                                    "Linear Damping"))
                    _row_button(f"Ang Damp {rb.angular_damping:.2f}",
                                lambda node=n: self._phys_edit_body_scalar(
                                    node, "angular_damping", 0.0, 1.0,
                                    "Angular Damping"))
                    _row_button(f"Freeze Rot {'ON' if rb.freeze_rotation else 'OFF'}",
                                lambda node=n: self._phys_toggle_freeze_rotation(node),
                                T["play_green"] if rb.freeze_rotation else T["button"])
                    _row_button("Remove RigidBody",
                                lambda node=n: self._phys_remove_rigidbody(node),
                                T["danger"], align="center")
                _row_button("Remove Collider",
                            lambda node=n: self._phys_remove_collider(node),
                            T["danger"], align="center")
            y -= section_gap

            # ---- Object actions (bottom of the inspector) ----
            # Delete removes the selected object (and its subtree) from the
            # scene; Duplicate clones it — transform, mesh, material,
            # blueprint and all children — under the same parent.
            _header("Actions")
            btn_gap = 0.008
            half_w = (col_w - btn_gap) * 0.5
            _is_player_spawn = getattr(n, "spawn_kind", None) == "player"
            self.ui.button(
                UIRect(pad_x, y, half_w if not _is_player_spawn else col_w,
                       line_h),
                "Delete",
                lambda node=n: self._delete_node(node),
                T["danger"], align="center")
            if not _is_player_spawn:
                # The Player Spawn is unique per scene — no Duplicate.
                self.ui.button(
                    UIRect(pad_x + half_w + btn_gap, y, half_w, line_h),
                    "Duplicate",
                    lambda node=n: self._duplicate_node(node),
                    T["accent_dim"], align="center")
            y -= line_h + row_gap
        else:
            self.ui.text(UIRect(0.830, 0.820, 0.16, 0.030),
                         "(nothing selected)", T["text_dim"])
            y = 0.80
            content_top = 0.832
        self.ui.end_scroll(y, content_top)

        # ==================================================================
        #  CONSOLE (bottom) + status hint
        # ==================================================================
        self.ui.panel(UIRect(0.18, 0.0, 0.64, 0.12), T["panel_dark"])
        self.ui.panel(UIRect(0.18, 0.1185, 0.64, 0.0015), T["border"])  # separator
        # pygame.font.render() does NOT support '\n' — a newline-joined blob
        # becomes one ultra-wide single-line texture that the aspect-fit then
        # squashes into an unreadable strip.  Emit one text widget per line
        # instead (oldest at the top, newest at the bottom).
        console_lines = self._console_lines[-6:]
        if not console_lines:
            self.ui.text(UIRect(0.190, 0.005, 0.62, 0.018),
                         "(console)", T["text_dim"])
        else:
            line_h = 0.018
            top_y = 0.118 - line_h
            for i, line in enumerate(console_lines):
                self.ui.text(UIRect(0.190, top_y - i * line_h, 0.62, line_h),
                             line, T["text_dim"])

        # Status hint
        if (self.shape_editor is not None and self.shape_editor.active
                and not self._playing):
            self.ui.text(UIRect(0.190, 0.126, 0.6, 0.028),
                         "COLLIDER EDIT: drag the green square handles to "
                         "reshape | ESC / Done exits",
                         T["green_text"])
        else:
            self.ui.text(UIRect(0.190, 0.126, 0.6, 0.028),
                         "W/E/R gizmos | F focus | Del delete | RMB+WASD fly | Alt+LMB orbit | MMB pan",
                         T["text_dim"])

        # View-gizmo axis letters (white X / Y / Z on the balls) ride the
        # UI pass so they draw crisply on top of the 3D navball overlay.
        # Edit time only — same visibility rule as the gizmo itself.
        if (self.view_gizmo is not None and not self._playing):
            self.view_gizmo.emit_labels(self.ui, self.width, self.height,
                                        self.camera)

    def _log_to_console(self, line: str):
        self._console_lines.append(line)
        if len(self._console_lines) > 50:
            self._console_lines = self._console_lines[-50:]

    def import_glb_dialog(self):
        """Open a native Open-File dialog and import the chosen model.
        Name kept for the UI button binding; it accepts .glb, .gltf and
        .obj.  Falls back to scanning assets/ if tkinter is unavailable."""
        # [TRIPLEA:p3_import_dialog_filters]
        path = self._native_dialog(
            "open", "Import Model",
            filetypes=[("All supported", "*.glb *.gltf *.obj *.blend"),
                       ("glTF binary", "*.glb"),
                       ("glTF", "*.gltf"),
                       ("Wavefront OBJ", "*.obj"),
                       ("Blender scene", "*.blend"),
                       ("All files", "*.*")])
        if path is None:
            # Fallback: auto-grab the first importable model in ./assets
            assets = os.path.join(os.getcwd(), "assets")
            if os.path.isdir(assets):
                for f in os.listdir(assets):
                    if f.lower().endswith((".glb", ".gltf", ".obj", ".blend")):
                        path = os.path.join(assets, f)
                        break
        if not path:
            Log.warn("Import cancelled and no model found in assets/.")
            return
        node = self.load_model(path)
        if node:
            self.select_node(node)
            Log.info(f"Imported: {os.path.basename(path)}")

    # ------------------------------------------------------------ release
    def release(self):
        for bp in self._blueprints:
            try: bp.on_end_play()
            except Exception: pass
        try:
            self.audio.shutdown()
        except Exception:
            pass
        if self.hud is not None:
            try: self.hud.release()
            except Exception: pass
        if self.renderer:     self.renderer.release()
        if self.gizmo:        self.gizmo.release()
        if self.editor_grid:  self.editor_grid.release()
        if self.light_gizmos: self.light_gizmos.release()
        if self.spawn_gizmos: self.spawn_gizmos.release()
        if self.shape_editor: self.shape_editor.release()
        if self.view_gizmo:   self.view_gizmo.release()
        if self.ui:           self.ui.release()

