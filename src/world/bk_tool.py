# @heaven3d-part world/bk_tool.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# #############################################################################
# BUILDING MAKER - the paint tool (drives BKData from the mouse)
#
#   Room        drag a rectangle of cells; walls, floor, ceiling and roof
#               follow by themselves (rooms with the same finish merge)
#   Erase       drag a rectangle away
#   Wall / Door / Window / Archway / Ruined wall / Low wall / Sandbags
#               click or drag along cell edges
#   Stairs      click a cell (R turns the flight)
#   No ceiling  drag cells open to the sky (shelled rooms, courtyards)
# Ctrl or Shift with any tool removes instead.  PageUp / PageDown change the
# storey; the storeys above the one being edited are hidden while the tool
# is open.  Every stroke is one undo step (the editor supplies on_edit).
# #############################################################################

BK_TOOLS = (("room", "Room", "Drag to paint rooms; walls follow"),
            ("erase", "Erase", "Drag to remove cells"),
            ("wall", "Wall", "Click / drag along edges: add a wall"),
            ("door", "Door", "Click a wall for a door"),
            ("window", "Window", "Click / drag along walls: windows"),
            ("arch", "Archway", "Click a wall for a wide opening"),
            ("ruin", "Ruined wall", "Click / drag along walls: shell them"),
            ("half", "Low wall", "Click / drag along edges: 1 m wall"),
            ("sandbags", "Sandbags", "Click / drag along edges: sandbags"),
            ("stairs", "Stairs", "Click a cell: a flight up (R turns it)"),
            ("open", "No ceiling", "Drag cells open to the sky"))
_BK_EDGE_TOOLS = ("wall", "door", "window", "arch", "ruin", "half", "sandbags")
_BK_RECT_TOOLS = ("room", "erase", "open")


class _BKGhost(_FPGhost):
    _MAX_VERTS = 6000

    def draw_lines(self, engine, camera, lines, model):
        ctx = getattr(engine, "ctx", None)
        if ctx is None or not lines or not self._ensure(ctx):
            return
        try:
            count = min(len(lines) // 6, self._MAX_VERTS)
            self._vbo.write(np.asarray(lines[:count * 6], dtype=np.float32).tobytes())
            self._prog["u_view"].write(camera.view().to_bytes())
            self._prog["u_proj"].write(camera.projection().to_bytes())
            self._prog["u_model"].write(model.to_bytes())
            ctx.disable(moderngl.DEPTH_TEST)
            ctx.disable(moderngl.BLEND)
            self._vao.render(moderngl.LINES, vertices=count)
            ctx.enable(moderngl.DEPTH_TEST)
        except Exception:
            pass


class BKTool:
    def __init__(self, engine):
        self.engine = engine
        self.active = False
        self.node = None
        self.tool = "room"
        self.level = 0
        self.style = "town"
        self.finish = None                 # a BK_FINISHES name; None: the style's first
        self.cut = True                    # hide the storeys above the edited one
        self.stair_dir = 1
        self.drag = None
        self.hover = None                  # {"cell", "edge", "p"}
        self.on_edit = None                # fn(node, before, after, label, created)
        self.ghost = _BKGhost()
        self.message = ""

    # ---- state ----
    def open(self):
        self.active = True
        sel = getattr(self.engine, "selected", None)
        if bk_is_building(sel):
            self.set_node(sel)
        elif not self._alive(self.node):
            self.node = None
        self._view()

    def close(self):
        self.active = False
        self.drag = None
        for n in self.buildings():
            n._bk_view_top = None
            bk_apply_view(n)

    def buildings(self):
        return [n for n in self.engine.scene.root.traverse() if bk_is_building(n)]

    def _alive(self, node):
        if node is None or not bk_is_building(node):
            return False
        p = node
        while p is not None:
            if p is self.engine.scene.root:
                return True
            p = p.parent
        return False

    def target(self):
        if not self._alive(self.node):
            self.node = None
        return self.node

    def set_node(self, node):
        old = self.node
        self.node = node if self._alive(node) else None
        if old is not None and old is not self.node and self._alive(old):
            old._bk_view_top = None
            bk_apply_view(old)
        if self.node is not None:
            d = bk_data(self.node)
            self.style = d.style
            lo, hi = d.levels()
            self.level = int(np.clip(self.level, lo - 1, hi + 1))
        self._view()

    def _view(self):
        n = self.target()
        if n is not None:
            n._bk_view_top = self.level if (self.active and self.cut) else None
            bk_apply_view(n)

    def set_level(self, lv):
        self.level = int(np.clip(int(lv), -4, 40))
        self._view()

    def set_cut(self, on):
        self.cut = bool(on)
        self._view()

    def finish_name(self):
        n = self.target()
        names = bk_data(n).finishes if n is not None else BK_STYLES[self.style]["finishes"]
        return self.finish if self.finish in BK_FINISHES else names[0]

    # ---- edits (each returns True when something changed) ----
    def _commit(self, node, before, label, levels=None, created=False):
        d = bk_data(node)
        after = d.to_params()
        if after == before and not created:
            return False
        bk_rebuild(self.engine, node, levels)
        self._view()
        if self.on_edit is not None:
            try:
                self.on_edit(node, before, after, label, created)
            except Exception as ex:
                Log.warn(f"[Building Maker] undo step skipped: {ex}")
        return True

    def restore(self, node, params):
        """Undo / redo: put a building back to a saved state."""
        if not self._alive(node):
            return
        node._bk = BKData(params)
        bk_rebuild(self.engine, node)
        self._view()

    def new_building(self, position=(0.0, 0.0, 0.0)):
        node = bk_new(self.engine, self.style, position)
        if self.finish in BK_FINISHES:
            bk_data(node).finish_index(self.finish)
        self.set_node(node)
        return node

    def set_style(self, style):
        if style not in BK_STYLES:
            return
        self.style = style
        n = self.target()
        if n is None:
            return
        d = bk_data(n)
        if d.style == style:
            return
        before = d.to_params()
        d.style = style
        d.roof = {}
        # rooms keep their slot; the slots take the new style's finishes
        fin = BK_STYLES[style]["finishes"]
        d.finishes = [fin[i % len(fin)] if i < len(fin) else f for i, f in enumerate(d.finishes)] or list(fin)
        self.finish = None
        self._commit(n, before, "Building style")

    def set_roof(self, type_=None, pitch=None):
        n = self.target()
        if n is None:
            return
        d = bk_data(n)
        before = d.to_params()
        if type_ is not None:
            d.roof["type"] = type_
        if pitch is not None:
            d.roof["pitch"] = float(pitch)
        self._commit(n, before, "Roof")

    def generate(self, w=4, depth=3, floors=2, damage=0.0, position=None):
        """A new generated house in front of the camera."""
        e = self.engine
        if position is None:
            try:
                ro, rd = e._mouse_ray((e.width * 0.5, e.height * 0.55))
                t = -ro.y / rd.y if rd.y < -1e-4 else 14.0
                t = float(np.clip(t, 6.0, 60.0))
                position = (ro.x + rd.x * t - w * _BK_C / 2, 0.0, ro.z + rd.z * t - depth * _BK_C / 2)
            except Exception:
                position = (0.0, 0.0, 0.0)
        node = bk_new(e, self.style, position)
        d = bk_data(node)
        bk_generate_house(d, w, depth, floors, damage)
        self.set_node(node)
        self.level = 0
        self._commit(node, None, "Generate " + node.name, created=True)
        return node

    def generate_village(self, houses=8, damage=0.25, position=None, seed=None):
        """Two rows of houses facing a cobbled street, in front of the
        camera.  Every house is its own building.  -> [nodes]"""
        e = self.engine
        seed = int(time.time() * 1000) % 1000003 if seed is None else int(seed)
        houses = int(np.clip(houses, 2, 40))
        per_row = (houses + 1) // 2
        styles = [s for s in ("town", "village", "rose", "manor", "town", "stone") if s in BK_STYLES]
        plan = []
        for row in (0, 1):
            x = 0
            for i in range(per_row if row == 0 else houses - per_row):
                k = row * 100 + i
                w = 3 + int(_bk_hash("vw", seed, k) * 3)
                dp = 3 + int(_bk_hash("vd", seed, k) * 2)
                fl = 1 + int(_bk_hash("vf", seed, k) * 2.6)
                st = styles[int(_bk_hash("vs", seed, k) * len(styles)) % len(styles)]
                dm = (0.35 + 0.4 * _bk_hash("vm", seed, k)) if _bk_hash("vr", seed, k) < damage else 0.0
                plan.append((row, x, w, dp, fl, st, dm, k))
                x += w + (1 if _bk_hash("vg", seed, k) > 0.5 else 0)
        length = max(p[1] + p[2] for p in plan)
        street = 3
        if position is None:
            try:
                ro, rd = e._mouse_ray((e.width * 0.5, e.height * 0.6))
                t = float(np.clip(-ro.y / rd.y if rd.y < -1e-4 else 30.0, 10.0, 90.0))
                position = (ro.x + rd.x * t - length * _BK_C / 2, 0.0, ro.z + rd.z * t - street * _BK_C / 2)
            except Exception:
                position = (0.0, 0.0, 0.0)
        ox = round(position[0] / _BK_C) * _BK_C
        oz = round(position[2] / _BK_C) * _BK_C
        out = []
        keep_style = self.style
        road = bk_new(e, "town", (ox - _BK_C, position[1], oz), name="Street")
        dr = bk_data(road)
        fi = dr.finish_index("yard")
        for x in range(length + 2):
            for z in range(street):
                dr.cells[(x, 0, z)] = fi
        bk_rebuild(e, road)
        out.append(road)
        for row, x, w, dp, fl, st, dm, k in plan:
            z = (-dp * _BK_C) if row == 0 else street * _BK_C
            node = bk_new(e, st, (ox + x * _BK_C, position[1], oz + z), seed=seed + k)
            bk_generate_house(bk_data(node), w, dp, fl, dm, seed=seed + k, front=1 if row == 0 else 0)
            bk_rebuild(e, node)
            out.append(node)
        self.style = keep_style
        for node in out:
            if self.on_edit is not None:
                try:
                    self.on_edit(node, None, bk_data(node).to_params(), "Generate " + node.name, True)
                except Exception:
                    pass
        self.set_node(out[-1])
        self.level = 0
        self._view()
        return out

    # ---- picking ----
    def _frame(self):
        n = self.target()
        return n.world_matrix() if n is not None else glm.mat4(1.0)

    def locate(self, pos):
        """Mouse position -> {"cell": (x, z), "edge": (x, y, z, o), "p": (lx, lz)} on the level plane."""
        e = self.engine
        try:
            ro, rd = e._mouse_ray(pos)
        except Exception:
            return None
        inv = glm.inverse(self._frame())
        o = inv * glm.vec4(ro.x, ro.y, ro.z, 1.0)
        dv = inv * glm.vec4(rd.x, rd.y, rd.z, 0.0)
        py = self.level * _BK_H + 0.01
        if abs(dv.y) < 1e-6:
            return None
        t = (py - o.y) / dv.y
        if t <= 0 or t > 2000:
            return None
        lx, lz = o.x + dv.x * t, o.z + dv.z * t
        cx, cz = int(math.floor(lx / _BK_C)), int(math.floor(lz / _BK_C))
        fx, fz = lx / _BK_C - cx, lz / _BK_C - cz
        cand = ((fz, (cx, self.level, cz - 1, "N")), (1 - fz, (cx, self.level, cz, "N")),
                (fx, (cx - 1, self.level, cz, "E")), (1 - fx, (cx, self.level, cz, "E")))
        edge = min(cand, key=lambda c: c[0])[1]
        return {"cell": (cx, cz), "edge": edge, "p": (lx, lz)}

    # ---- mouse ----
    def mouse_down(self, pos, remove=False):
        h = self.locate(pos)
        if h is None:
            return False
        n = self.target()
        tool = self.tool
        if tool == "erase":
            tool, remove = "room", True
        self.drag = {"tool": tool, "remove": bool(remove), "start": h["cell"], "end": h["cell"],
                     "before": bk_data(n).to_params() if n is not None else None, "edges": set(),
                     "changed": False, "levels": set()}
        if tool in _BK_EDGE_TOOLS:
            self._edge_stroke(h)
        return True

    def mouse_move(self, pos):
        self.hover = self.locate(pos)
        dr = self.drag
        if dr is None or self.hover is None:
            return
        dr["end"] = self.hover["cell"]
        if dr["tool"] in _BK_EDGE_TOOLS:
            self._edge_stroke(self.hover)

    def mouse_up(self, pos):
        dr, self.drag = self.drag, None
        if dr is None:
            return
        h = self.locate(pos)
        if h is not None:
            dr["end"] = h["cell"]
        tool = dr["tool"]
        n = self.target()
        if tool in _BK_EDGE_TOOLS:
            if n is not None and dr["changed"]:
                self._commit(n, dr["before"], BK_TOOL_LABEL.get(tool, tool), None)
            return
        (x0, z0), (x1, z1) = dr["start"], dr["end"]
        xa, xb, za, zb = min(x0, x1), max(x0, x1), min(z0, z1), max(z0, z1)
        if (xb - xa + 1) * (zb - za + 1) > 4096:
            self.message = "That is more than 4096 cells at once - drag a smaller area"
            return
        y = self.level
        created = False
        if n is None:
            if dr["remove"] or tool != "room" and tool != "stairs":
                self.message = "Paint a room first"
                return
            # a new building starts at the corner of the first stroke
            wp = glm.vec3(xa * _BK_C, 0.0, za * _BK_C)
            n = self.new_building((wp.x, wp.y, wp.z))
            xb, zb, xa, za = xb - xa, zb - za, 0, 0
            y = self.level = 0
            created = True
            dr["before"] = None
        d = bk_data(n)
        before = dr["before"]
        if tool == "room":
            if dr["remove"]:
                for x in range(xa, xb + 1):
                    for z in range(za, zb + 1):
                        if d.cells.pop((x, y, z), None) is not None:
                            d.open.discard((x, y, z))
                            d.stairs.pop((x, y, z), None)
                # walls that no longer touch a cell go with it
                for e in [e for e in d.edges if e[1] == y]:
                    a, b = d.edge_cells(e)
                    if a not in d.cells and b not in d.cells and d.edges[e] in _BK_WALLISH + ("ruin", "none"):
                        del d.edges[e]
                label = "Erase cells"
            else:
                fi = d.finish_index(self.finish_name())
                for x in range(xa, xb + 1):
                    for z in range(za, zb + 1):
                        d.cells[(x, y, z)] = fi
                label = "Paint room"
        elif tool == "open":
            for x in range(xa, xb + 1):
                for z in range(za, zb + 1):
                    if (x, y, z) in d.cells:
                        (d.open.discard if dr["remove"] else d.open.add)((x, y, z))
            label = "Ceiling"
        elif tool == "stairs":
            c = (dr["end"][0] if not created else 0, y, dr["end"][1] if not created else 0)
            if dr["remove"]:
                st = d.stair_cells().get(c)
                if st:
                    d.stairs.pop(st[0], None)
            else:
                dx, dz = ((1, 0), (0, 1), (-1, 0), (0, -1))[self.stair_dir % 4]
                fi = d.finish_index(self.finish_name())
                if c not in d.cells:
                    d.cells[c] = fi
                c2 = (c[0] + dx, y, c[2] + dz)
                d.cells.setdefault(c2, d.cells[c])
                occupied = d.stair_cells()
                for k in (c, c2):
                    if k in occupied:
                        d.stairs.pop(occupied[k][0], None)
                d.stairs[c] = self.stair_dir % 4
                # the wall between the two cells of the flight must be open
                e = (min(c[0], c2[0]), y, min(c[2], c2[2]), "E" if dx else "N")
                if d.edge_kind(e):
                    d.edges[e] = "none"
            label = "Stairs"
        else:
            return
        self._commit(n, before, label, {y, y + 1} if not created else None, created=created)

    def _edge_stroke(self, h):
        dr = self.drag
        n = self.target()
        if n is None or dr is None:
            self.message = "Paint a room first"
            return
        e = h["edge"]
        if e in dr["edges"]:
            return
        dr["edges"].add(e)
        d = bk_data(n)
        tool = dr["tool"]
        old = d.edges.get(e)
        a, b = d.edge_cells(e)
        ia, ib = d.indoor(a), d.indoor(b)
        auto = "wall" if (ia != ib or (ia and ib and d.cells[a] != d.cells[b])) else None
        if dr["remove"]:
            new = None if auto is None else "none"         # take the wall away
            if tool != "wall" and auto == "wall":
                new = None                                  # ...or put the plain wall back
        elif tool == "wall":
            new = None if auto == "wall" else "wall"
        else:
            new = tool
        if new == old:
            return
        if new is None:
            d.edges.pop(e, None)
        else:
            d.edges[e] = new
        dr["changed"] = True
        bk_rebuild(self.engine, n, {e[1], e[1] + 1})
        self._view()

    def key(self, key):
        """-> True when the key was used."""
        if key == pygame.K_r and self.tool == "stairs":
            self.stair_dir = (self.stair_dir + 1) % 4
            return True
        if key == pygame.K_PAGEUP:
            self.set_level(self.level + 1)
            return True
        if key == pygame.K_PAGEDOWN:
            self.set_level(self.level - 1)
            return True
        return False

    # ---- overlay ----
    def overlay(self):
        """Line list (x, y, z, r, g, b per vertex) in the building's frame."""
        L = []
        y = self.level * _BK_H + 0.03

        def seg(a, b, c):
            L.extend((a[0], a[1], a[2], c[0], c[1], c[2], b[0], b[1], b[2], c[0], c[1], c[2]))

        def rect(x0, z0, x1, z1, c, yy=y):
            p = [(x0, yy, z0), (x1, yy, z0), (x1, yy, z1), (x0, yy, z1)]
            for i in range(4):
                seg(p[i], p[(i + 1) % 4], c)
        n = self.target()
        h = self.hover
        cells = [(k[0], k[2]) for k in bk_data(n).cells] if n is not None else []
        if h is not None:
            cells.append(h["cell"])
        if cells:
            xs, zs = [c[0] for c in cells], [c[1] for c in cells]
            x0, x1 = max(min(xs) - 3, (h["cell"][0] if h else 0) - 40), min(max(xs) + 4, (h["cell"][0] if h else 0) + 40)
            z0, z1 = max(min(zs) - 3, (h["cell"][1] if h else 0) - 40), min(max(zs) + 4, (h["cell"][1] if h else 0) + 40)
            g = (0.42, 0.46, 0.52)
            for x in range(x0, x1 + 1):
                seg((x * _BK_C, y, z0 * _BK_C), (x * _BK_C, y, z1 * _BK_C), g)
            for z in range(z0, z1 + 1):
                seg((x0 * _BK_C, y, z * _BK_C), (x1 * _BK_C, y, z * _BK_C), g)
        if h is None:
            return L
        dr = self.drag
        remove = (dr["remove"] if dr else False) or self.tool == "erase"
        col = (1.0, 0.35, 0.25) if remove else (0.35, 1.0, 0.45)
        tool = dr["tool"] if dr else ("room" if self.tool == "erase" else self.tool)
        if tool in _BK_RECT_TOOLS:
            (ax, az) = dr["start"] if dr else h["cell"]
            (bx, bz) = dr["end"] if dr else h["cell"]
            xa, xb, za, zb = min(ax, bx), max(ax, bx) + 1, min(az, bz), max(az, bz) + 1
            for k in range(3):
                rect(xa * _BK_C + 0.03 * k, za * _BK_C + 0.03 * k, xb * _BK_C - 0.03 * k, zb * _BK_C - 0.03 * k, col)
            rect(xa * _BK_C, za * _BK_C, xb * _BK_C, zb * _BK_C, col, y + _BK_H - 0.2)
            for px, pz in ((xa, za), (xb, za), (xb, zb), (xa, zb)):
                seg((px * _BK_C, y, pz * _BK_C), (px * _BK_C, y + _BK_H - 0.2, pz * _BK_C), col)
        elif tool == "stairs":
            cx, cz = h["cell"]
            dx, dz = ((1, 0), (0, 1), (-1, 0), (0, -1))[self.stair_dir % 4]
            x0, z0 = min(cx, cx + dx), min(cz, cz + dz)
            x1, z1 = max(cx, cx + dx) + 1, max(cz, cz + dz) + 1
            rect(x0 * _BK_C, z0 * _BK_C, x1 * _BK_C, z1 * _BK_C, col)
            a = ((cx + 0.5 - dx * 0.4) * _BK_C, y, (cz + 0.5 - dz * 0.4) * _BK_C)
            b = ((cx + 0.5 + dx * 1.4) * _BK_C, y + _BK_H, (cz + 0.5 + dz * 1.4) * _BK_C)
            seg(a, b, col)
            for sg in (-1, 1):
                seg(b, (b[0] - dx * 0.5 + dz * 0.3 * sg, b[1] - 0.4, b[2] - dz * 0.5 + dx * 0.3 * sg), col)
        else:
            x, _y, z, o = h["edge"]
            if o == "N":
                p0, p1, w = (x * _BK_C, (z + 1) * _BK_C), ((x + 1) * _BK_C, (z + 1) * _BK_C), (0.0, 0.15)
            else:
                p0, p1, w = ((x + 1) * _BK_C, z * _BK_C), ((x + 1) * _BK_C, (z + 1) * _BK_C), (0.15, 0.0)
            top = 1.0 if tool in ("half", "sandbags") else _BK_H - 0.2
            for yy in (y, y + top):
                rect(p0[0] - w[0], p0[1] - w[1], p1[0] + w[0], p1[1] + w[1], col, yy)
            for px, pz in ((p0[0] - w[0], p0[1] - w[1]), (p1[0] + w[0], p1[1] + w[1]),
                           (p0[0] + w[0], p0[1] + w[1]), (p1[0] - w[0], p1[1] - w[1])):
                seg((px, y, pz), (px, y + top, pz), col)
        return L

    def draw(self, camera):
        e = self.engine
        try:
            pos = e.input.mouse_pos
        except Exception:
            return
        ui = getattr(e, "ui", None)
        over_ui = False
        try:
            over_ui = bool(ui.point_in_panels(pos[0] / float(e.width), 1.0 - pos[1] / float(e.height)))
        except Exception:
            pass
        if over_ui and self.drag is None:
            self.hover = None
        else:
            self.mouse_move(pos)
        self.ghost.draw_lines(e, camera, self.overlay(), self._frame())


BK_TOOL_LABEL = {k: label for k, label, _tip in BK_TOOLS}


def _bk_tool(engine):
    t = getattr(engine, "building_tool", None)
    if t is None:
        t = engine.building_tool = BKTool(engine)
    return t


if not globals().get("_BK_TOOL_INSTALLED"):
    _BK_TOOL_INSTALLED = True
    _BK_PREV_MOUSE_DOWN = HeavenEngine._on_mouse_down
    _BK_PREV_MOUSE_UP = HeavenEngine._on_mouse_up
    _BK_PREV_ON_KEY = HeavenEngine._on_key
    _BK_PREV_GRID_RENDER = EditorGrid.render
    _BK_PREV_DELETE = HeavenEngine._delete_node

    def _bk_tool_on(self):
        t = getattr(self, "building_tool", None)
        return t if (t is not None and t.active and getattr(self, "mode", None) == "editor"
                     and not getattr(self, "_playing", False)) else None

    def _bk_on_mouse_down(self, button, pos):
        try:
            t = _bk_tool_on(self)
            inp = getattr(self, "input", None)
            if t is not None and button == 1 and not bool(getattr(inp, "alt", False)):
                ux, uy = pos[0] / float(self.width), 1.0 - pos[1] / float(self.height)
                blocked = False
                ui = getattr(self, "ui", None)
                if ui is not None:
                    try:
                        blocked = bool(ui.point_in_panels(ux, uy))
                    except Exception:
                        blocked = False
                vg = getattr(self, "view_gizmo", None)
                if vg is not None and not blocked:
                    try:
                        blocked = bool(vg.point_in_region(pos, self.width, self.height))
                    except Exception:
                        pass
                if not blocked:
                    mods = pygame.key.get_mods()
                    remove = bool(mods & (pygame.KMOD_CTRL | pygame.KMOD_SHIFT)) or bool(getattr(inp, "ctrl", False))
                    if t.mouse_down(pos, remove):
                        if inp is not None:
                            inp.lmb = True
                            inp.click_just_pressed = True
                            inp.click_pos = pos
                        return
        except Exception as ex:
            Log.warn(f"[Building Maker] click skipped: {ex}")
        return _BK_PREV_MOUSE_DOWN(self, button, pos)

    def _bk_on_mouse_up(self, button, pos):
        try:
            t = getattr(self, "building_tool", None)
            if t is not None and t.drag is not None and button == 1:
                inp = getattr(self, "input", None)
                if inp is not None:
                    inp.lmb = False
                t.mouse_up(pos)
                return
        except Exception as ex:
            Log.warn(f"[Building Maker] stroke skipped: {ex}")
            traceback.print_exc()
        return _BK_PREV_MOUSE_UP(self, button, pos)

    def _bk_on_key(self, key):
        try:
            t = _bk_tool_on(self)
            if t is not None and not (pygame.key.get_mods() & pygame.KMOD_CTRL) and t.key(key):
                return
        except Exception as ex:
            Log.warn(f"[Building Maker] key skipped: {ex}")
        return _BK_PREV_ON_KEY(self, key)

    def _bk_grid_render(self, camera):
        _BK_PREV_GRID_RENDER(self, camera)
        try:
            engine = _FP_ENGINE
            t = _bk_tool_on(engine) if engine is not None else None
            if t is not None:
                t.draw(camera)
        except Exception:
            pass

    def _bk_delete_node(self, node):
        try:
            t = getattr(self, "building_tool", None)
            if t is not None and t.node is not None and node is not None:
                if any(n is t.node for n in node.traverse()):
                    t.node = None
                    t.drag = None
        except Exception:
            pass
        return _BK_PREV_DELETE(self, node)

    HeavenEngine._on_mouse_down = _bk_on_mouse_down
    HeavenEngine._on_mouse_up = _bk_on_mouse_up
    HeavenEngine._on_key = _bk_on_key
    HeavenEngine._delete_node = _bk_delete_node
    EditorGrid.render = _bk_grid_render
