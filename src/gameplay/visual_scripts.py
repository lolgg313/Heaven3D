# @heaven3d-part gameplay/visual_scripts.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# ---------------------------------------------------------------------------
# [VISUAL SCRIPTS]  Node graphs that run in the game
#
#   The editor's Graph tab draws these graphs; this is the part that RUNS
#   them, so it is in the engine (exported games have no editor).  A graph
#   is stored in the "graph" property of a VisualScript blueprint, in the
#   same form the Vitrail node editor saves:
#
#       {"nodes": [{"id": 1, "type": "Events/On Tick", "pos": [x, y],
#                   "values": {"Seconds": 2.0}}, ...],
#        "links": [[from_id, "Then", to_id, "In"], ...]}
#
#   Node kinds:
#     event   -- starts a chain when something happens (begin play, tick,
#                collision, message, key...).  Its "Then" exec output runs.
#     action  -- has an "In" exec input; does something, then continues
#                down an exec output ("Then", or e.g. "True"/"False").
#     pure    -- no exec pins; computes outputs from inputs when asked.
#
#   Add your own nodes from a project script or package:
#
#       from Heaven3D import vs_node
#       @vs_node("My Game/Heal", kind="action", inputs=[("Amount", "float", 10.0)])
#       def _heal(ctx, i):
#           ctx.node.user_data["hp"] = ctx.node.user_data.get("hp", 0) + i["Amount"]
#
#   The editor lists every registered node in its "Add node" menu.
# ---------------------------------------------------------------------------
VS_NODES: Dict[str, dict] = {}
_VS_EXEC = "exec"
_VS_MAX_STEPS = 20000          # an endless exec loop stops here instead of freezing the game


def vs_node(path, *, kind="pure", inputs=(), outputs=(), category=None, event=None, doc=""):
    """Register a visual-script node type.  ``inputs`` / ``outputs`` are
    (name, type[, default]) tuples; types: float int bool string vector
    color object any exec.  Action nodes get an "In" exec input and a
    "Then" exec output unless they declare their own exec outputs."""
    ins = [tuple(s) for s in inputs]
    outs = [tuple(s) for s in outputs]
    if kind == "action":
        if not any(s[1] == _VS_EXEC for s in ins):
            ins.insert(0, ("In", _VS_EXEC))
        if not any(s[1] == _VS_EXEC for s in outs):
            outs.insert(0, ("Then", _VS_EXEC))
    elif kind == "event":
        if not any(s[1] == _VS_EXEC for s in outs):
            outs.insert(0, ("Then", _VS_EXEC))
    cat = category or {"event": "event", "action": "output"}.get(kind, "math")

    def deco(fn):
        VS_NODES[path] = {"kind": kind, "inputs": ins, "outputs": outs, "category": cat,
                          "event": event, "fn": fn, "doc": doc or (fn.__doc__ or "").strip()}
        return fn
    return deco


class VSContext:
    """What a node function gets as ``ctx``."""

    def __init__(self, bp):
        self.bp = bp
        self.node = bp.node
        self.engine = bp.engine
        self.vars = bp.vars
        self.dt = 0.0
        self.values = {}           # node id -> outputs it produced when it last ran
        self.current = None        # id of the action node running now

    def target(self, obj):
        """An "object" input: a SceneNode, a node name, or empty (= this node)."""
        if obj is None or obj == "":
            return self.node
        if isinstance(obj, str):
            return self.engine.find_node(obj) if self.engine is not None else None
        return obj


def _vs_vec(v):
    if isinstance(v, (int, float)):
        return glm.vec3(float(v))
    try:
        return glm.vec3(float(v[0]), float(v[1]), float(v[2]))
    except Exception:
        return glm.vec3(0.0)


class VSGraph:
    """A loaded graph: links indexed for fast execution."""

    def __init__(self, data):
        data = data or {}
        self.nodes = {}
        for nd in data.get("nodes", []):
            spec = VS_NODES.get(nd.get("type"))
            if spec is not None:
                self.nodes[nd["id"]] = (nd, spec)
        self.data_in = {}          # (node id, input) -> (node id, output)
        self.exec_out = {}         # (node id, output) -> [node id, ...]
        for a, an, b, bn in data.get("links", []):
            if a not in self.nodes or b not in self.nodes:
                continue
            types = {s[0]: s[1] for s in self.nodes[a][1]["outputs"]}
            if types.get(an) == _VS_EXEC:
                self.exec_out.setdefault((a, an), []).append(b)
            else:
                self.data_in[(b, bn)] = (a, an)
        self.events = {}           # event key -> [node id]
        for nid, (nd, spec) in self.nodes.items():
            if spec["kind"] == "event" and spec["event"]:
                self.events.setdefault(spec["event"], []).append(nid)

    def has(self, event):
        return event in self.events

    # -- evaluation ------------------------------------------------------------------
    def inputs(self, ctx, nid, _depth=0):
        nd, spec = self.nodes[nid]
        vals = nd.get("values", {})
        out = {}
        for s in spec["inputs"]:
            name, typ = s[0], s[1]
            if typ == _VS_EXEC:
                continue
            src = self.data_in.get((nid, name))
            if src is not None:
                out[name] = self.output(ctx, src[0], src[1], _depth + 1)
            elif name in vals:
                v = vals[name]
                out[name] = tuple(v) if isinstance(v, list) else v
            else:
                out[name] = s[2] if len(s) > 2 else None
        return out

    def output(self, ctx, nid, name, _depth=0):
        nd, spec = self.nodes[nid]
        if spec["kind"] != "pure":                     # events/actions: what they produced
            return ctx.values.get(nid, {}).get(name)
        if _depth > 200:
            return None
        try:
            res = spec["fn"](ctx, self.inputs(ctx, nid, _depth)) or {}
        except Exception as ex:
            ctx.bp._vs_error(nd, ex)
            res = {}
        return res.get(name)

    # -- execution ---------------------------------------------------------------------
    def fire(self, ctx, event, args=None, match=None):
        args = args or {}
        for nid in self.events.get(event, ()):
            nd, spec = self.nodes[nid]
            ins = self.inputs(ctx, nid)
            if match is not None and not match(ins):
                continue
            if event == "timer":                        # every N seconds, per node
                secs = max(0.01, float(ins.get("Seconds") or 1.0))
                t = ctx.bp._timers.get(nid, 0.0) + args.get("dt", 0.0)
                ctx.bp._timers[nid] = t % secs if t >= secs else t
                if t < secs:
                    continue
            try:
                res = spec["fn"](ctx, ins, args)
            except Exception as ex:
                ctx.bp._vs_error(nd, ex)
                continue
            ctx.values[nid] = res or {}
            self.run(ctx, nid, "Then")

    def run(self, ctx, nid, pin, depth=0):
        """Run everything wired to exec output ``pin`` of node ``nid``."""
        for n in self.exec_out.get((nid, pin), ()):
            self._run_from(ctx, n, depth)

    def _run_from(self, ctx, start, depth):
        stack = [start]
        steps = 0
        while stack:
            steps += 1
            if steps > _VS_MAX_STEPS or depth > 64:
                ctx.bp._vs_error(None, RuntimeError("too many steps in one event - is there an endless loop?"))
                return
            cur = stack.pop()
            nd, spec = self.nodes[cur]
            if spec["kind"] != "action":
                continue
            ctx.current = cur
            try:
                res = spec["fn"](ctx, self.inputs(ctx, cur))
            except Exception as ex:
                ctx.bp._vs_error(nd, ex)
                continue
            nxt, outs = "Then", {}
            if isinstance(res, tuple):
                nxt, outs = res[0], (res[1] or {})
            elif isinstance(res, (str, list)):
                nxt = res
            elif isinstance(res, dict):
                outs = res
            if nxt == _VS_STOP:                         # Do Once that already ran
                continue
            if nxt == _VS_DELAY:
                ctx.values[cur] = {}
                if ctx.engine is not None:
                    g = self
                    ctx.engine.schedule(float(outs.get("seconds") or 0.0),
                                        lambda c=cur: g._after_delay(ctx, c))
                continue
            if nxt == _VS_FOR:
                n = max(0, min(100000, int(outs.get("count") or 0)))
                for k in range(n):
                    ctx.values[cur] = {"Index": k}
                    self.run(ctx, cur, "Body", depth + 1)
                nxt, outs = "Completed", {"Index": n}
            ctx.values[cur] = outs
            pins = nxt if isinstance(nxt, list) else ([nxt] if nxt else [])
            for p in reversed(pins):
                for n in reversed(self.exec_out.get((cur, p), ())):
                    stack.append(n)

    def _after_delay(self, ctx, nid):
        if ctx.bp.node is None or ctx.bp._g is not self:
            return                                      # destroyed, or the game restarted
        self.run(ctx, nid, "Then")


_VS_DELAY, _VS_FOR, _VS_STOP = "__delay__", "__for__", "__stop__"


@blueprint
class VisualScript(Blueprint):
    """Runs a node graph made in the editor's Graph tab (no code needed)."""

    def __init__(self):
        super().__init__()
        self.properties = {"graph": {"nodes": [], "links": []}}
        self.vars = {}
        self._g = None
        self._ctx = None
        self._errors = set()
        self._timers = {}
        self._keys_prev = set()
        self._vs_once = set()

    def _vs_error(self, nd, ex):
        key = (nd or {}).get("id"), type(ex).__name__, str(ex)
        if key in self._errors:
            return
        self._errors.add(key)
        where = ("node '%s'" % nd.get("type")) if nd else "graph"
        Log.error("[VisualScript] %s on '%s': %s" % (where, getattr(self.node, "name", "?"), ex))

    def graph(self):
        if self._g is None:
            self._g = VSGraph(self.properties.get("graph"))
            self._ctx = VSContext(self)
        return self._g

    def reload(self):
        """Pick up an edited graph (the editor calls this after a change)."""
        self._g = None

    def fire(self, event, args=None, match=None):
        g = self.graph()
        if g.has(event):
            g.fire(self._ctx, event, args, match)

    def on_begin_play(self):
        self._g = None
        self.vars.clear()
        self.vars.update(self.properties.get("variables") or {})
        self._timers.clear()
        self._errors.clear()
        self._keys_prev = set()
        self._vs_once = set()
        self.fire("begin_play")

    def on_tick(self, dt):
        g = self.graph()
        self._ctx.dt = dt
        if g.has("tick"):
            g.fire(self._ctx, "tick", {"Delta": dt})
        if g.has("timer"):
            g.fire(self._ctx, "timer", {"dt": dt})
        if g.has("key_pressed") or g.has("key_released"):
            inp = self.engine.input if self.engine is not None else None
            if inp is not None:
                now = set(getattr(inp, "_keys", ()))
                if g.has("key_pressed"):
                    for k in now - self._keys_prev:
                        g.fire(self._ctx, "key_pressed", {"key": k},
                               match=lambda i, k=k: str(i.get("Key", "")).lower() in ("", k))
                if g.has("key_released"):
                    for k in self._keys_prev - now:
                        g.fire(self._ctx, "key_released", {"key": k},
                               match=lambda i, k=k: str(i.get("Key", "")).lower() in ("", k))
                self._keys_prev = now

    def on_end_play(self):
        self.fire("end_play")

    def on_message(self, event, data):
        self.fire("message", {"event": event, "data": data},
                  match=lambda i: str(i.get("Event", "")) in ("", str(event)))

    def on_collision_enter(self, other, contact):
        self.fire("collision_enter", {"other": other})

    def on_trigger_enter(self, other):
        self.fire("trigger_enter", {"other": other})

    def on_trigger_exit(self, other):
        self.fire("trigger_exit", {"other": other})


# ---- events -------------------------------------------------------------------------
@vs_node("Events/On Begin Play", kind="event", event="begin_play")
def _vs_begin(ctx, i, a):
    """Runs once when the game starts."""
    return {}


@vs_node("Events/On Tick", kind="event", event="tick", outputs=[("Delta", "float"), ("Time", "float")])
def _vs_tick(ctx, i, a):
    """Runs every frame. Delta = seconds since the last frame."""
    return {"Delta": a.get("Delta", 0.0), "Time": getattr(ctx.engine, "time", 0.0)}


@vs_node("Events/On Timer", kind="event", event="timer", inputs=[("Seconds", "float", 1.0)])
def _vs_timer(ctx, i, a):
    """Runs every N seconds."""
    return {}


@vs_node("Events/On End Play", kind="event", event="end_play")
def _vs_end(ctx, i, a):
    """Runs when the game stops."""
    return {}


@vs_node("Events/On Key Pressed", kind="event", event="key_pressed", inputs=[("Key", "string", "space")],
         outputs=[("Key Name", "string")])
def _vs_key(ctx, i, a):
    """Runs when a key goes down (names like: space, w, a, left, return, 1)."""
    return {"Key Name": a.get("key", "")}


@vs_node("Events/On Key Released", kind="event", event="key_released", inputs=[("Key", "string", "space")],
         outputs=[("Key Name", "string")])
def _vs_key_up(ctx, i, a):
    """Runs when a key is let go."""
    return {"Key Name": a.get("key", "")}


@vs_node("Events/On Message", kind="event", event="message", inputs=[("Event", "string", "")],
         outputs=[("Message", "string"), ("Data", "any")])
def _vs_msg(ctx, i, a):
    """Runs when engine.send_message / Send Message sends this event (empty = any)."""
    return {"Message": a.get("event", ""), "Data": a.get("data")}


@vs_node("Events/On Collision", kind="event", event="collision_enter", outputs=[("Other", "object")])
def _vs_coll(ctx, i, a):
    """Runs when this object's collider hits another one."""
    return {"Other": a.get("other")}


@vs_node("Events/On Trigger Enter", kind="event", event="trigger_enter", outputs=[("Other", "object")])
def _vs_trig(ctx, i, a):
    """Runs when something enters this trigger collider."""
    return {"Other": a.get("other")}


@vs_node("Events/On Trigger Exit", kind="event", event="trigger_exit", outputs=[("Other", "object")])
def _vs_trig_exit(ctx, i, a):
    """Runs when something leaves this trigger collider."""
    return {"Other": a.get("other")}


# ---- flow -----------------------------------------------------------------------------
@vs_node("Flow/Branch", kind="action", category="logic", inputs=[("Condition", "bool", True)],
         outputs=[("True", "exec"), ("False", "exec")])
def _vs_branch(ctx, i):
    """Goes down True or False."""
    return "True" if i["Condition"] else "False"


@vs_node("Flow/Sequence", kind="action", category="logic",
         outputs=[("Then 0", "exec"), ("Then 1", "exec"), ("Then 2", "exec")])
def _vs_seq(ctx, i):
    """Runs Then 0, Then 1, Then 2 in order."""
    return ["Then 0", "Then 1", "Then 2"]


@vs_node("Flow/Delay", kind="action", category="logic", inputs=[("Seconds", "float", 1.0)])
def _vs_delay(ctx, i):
    """Continues after a delay (game time)."""
    return (_VS_DELAY, {"seconds": i["Seconds"]})


@vs_node("Flow/Do Once", kind="action", category="logic")
def _vs_once(ctx, i):
    """Lets the chain through the first time only (until Play restarts)."""
    done = ctx.bp._vs_once
    if ctx.current in done:
        return _VS_STOP
    done.add(ctx.current)
    return None


@vs_node("Flow/For Loop", kind="action", category="logic", inputs=[("Count", "int", 3)],
         outputs=[("Body", "exec"), ("Index", "int"), ("Completed", "exec")])
def _vs_for(ctx, i):
    """Runs Body Count times (Index = 0, 1, ...), then Completed."""
    return (_VS_FOR, {"count": int(i["Count"] or 0)})


# ---- actions ------------------------------------------------------------------------------
def _vs_xyz(i):
    return glm.vec3(float(i.get("X") or 0.0), float(i.get("Y") or 0.0), float(i.get("Z") or 0.0))


@vs_node("Actions/Print", kind="action", inputs=[("Text", "string", "Hello!"), ("Value", "any")])
def _vs_print(ctx, i):
    """Writes a line to the console."""
    v = i.get("Value")
    Log.info("[Graph] %s%s" % (i.get("Text") or "", "" if v is None else " %s" % (v,)))


_XYZ = [("X", "float", 0.0), ("Y", "float", 0.0), ("Z", "float", 0.0)]


@vs_node("Transform/Set Position", kind="action", category="vector",
         inputs=[("Target", "object")] + _XYZ)
def _vs_setpos(ctx, i):
    """Moves the object to X, Y, Z."""
    n = ctx.target(i.get("Target"))
    if n is not None:
        n.position = _vs_xyz(i)


@vs_node("Transform/Translate", kind="action", category="vector",
         inputs=[("Target", "object")] + _XYZ + [("Per Second", "bool", True)])
def _vs_translate(ctx, i):
    """Moves the object by X, Y, Z (per second: smooth movement in On Tick)."""
    n = ctx.target(i.get("Target"))
    if n is not None:
        d = _vs_xyz(i) * (ctx.dt if i.get("Per Second") else 1.0)
        n.position = n.position + d


@vs_node("Transform/Rotate", kind="action", category="vector",
         inputs=[("Target", "object")] + _XYZ + [("Per Second", "bool", True)])
def _vs_rotate(ctx, i):
    """Turns the object by X, Y, Z degrees (per second in On Tick)."""
    n = ctx.target(i.get("Target"))
    if n is not None:
        n.rotation_euler = n.rotation_euler + _vs_xyz(i) * (ctx.dt if i.get("Per Second") else 1.0)


@vs_node("Transform/Set Rotation", kind="action", category="vector", inputs=[("Target", "object")] + _XYZ)
def _vs_setrot(ctx, i):
    """Sets the rotation (degrees)."""
    n = ctx.target(i.get("Target"))
    if n is not None:
        n.rotation_euler = _vs_xyz(i)


@vs_node("Transform/Set Scale", kind="action", category="vector",
         inputs=[("Target", "object"), ("X", "float", 1.0), ("Y", "float", 1.0), ("Z", "float", 1.0)])
def _vs_setscale(ctx, i):
    """Sets the size."""
    n = ctx.target(i.get("Target"))
    if n is not None:
        n.scale = glm.max(_vs_xyz(i), glm.vec3(0.001))


@vs_node("Objects/Set Visible", kind="action", category="output",
         inputs=[("Target", "object"), ("Visible", "bool", False)])
def _vs_vis(ctx, i):
    """Shows or hides the object."""
    n = ctx.target(i.get("Target"))
    if n is not None:
        n.visible = bool(i.get("Visible"))


@vs_node("Objects/Destroy", kind="action", category="output", inputs=[("Target", "object")])
def _vs_destroy(ctx, i):
    """Removes the object from the game."""
    n = ctx.target(i.get("Target"))
    if n is not None and ctx.engine is not None:
        ctx.engine.destroy_node(n)


@vs_node("Objects/Set Color", kind="action", category="color",
         inputs=[("Target", "object"), ("Color", "color", (1.0, 0.3, 0.2, 1.0))])
def _vs_color(ctx, i):
    """Changes the object's material colour."""
    n = ctx.target(i.get("Target"))
    c = i.get("Color") or (1, 1, 1, 1)
    if n is not None and n.material is not None:
        n.material.base_color = tuple(float(x) for x in (list(c) + [1.0])[:4])


@vs_node("Objects/Spawn Copy", kind="action", category="output",
         inputs=[("Template", "object")] + _XYZ, outputs=[("Spawned", "object")])
def _vs_spawn(ctx, i):
    """Makes a copy of an object (by name or link) at X, Y, Z."""
    t = ctx.target(i.get("Template"))
    if t is None or ctx.engine is None:
        return {}
    return {"Spawned": ctx.engine.instantiate(t, position=_vs_xyz(i))}


@vs_node("Physics/Add Velocity", kind="action", category="vector", inputs=[("Target", "object")] + _XYZ)
def _vs_impulse(ctx, i):
    """Pushes a rigid body (adds to its velocity, m/s)."""
    n = ctx.target(i.get("Target"))
    rb = getattr(n, "rigidbody", None)
    if rb is not None:
        rb.velocity = rb.velocity + _vs_xyz(i)


@vs_node("Physics/Set Velocity", kind="action", category="vector", inputs=[("Target", "object")] + _XYZ)
def _vs_setvel(ctx, i):
    """Sets a rigid body's velocity (m/s)."""
    n = ctx.target(i.get("Target"))
    rb = getattr(n, "rigidbody", None)
    if rb is not None:
        rb.velocity = _vs_xyz(i)


@vs_node("Game/Send Message", kind="action", category="output",
         inputs=[("Event", "string", "door_open"), ("Data", "any")])
def _vs_send(ctx, i):
    """Sends a message to every script (they receive it in On Message)."""
    if ctx.engine is not None:
        ctx.engine.send_message(str(i.get("Event") or ""), i.get("Data"))


@vs_node("Game/Play Sound", kind="action", category="output",
         inputs=[("Sound", "string", ""), ("Volume", "float", 1.0)])
def _vs_sound(ctx, i):
    """Plays a sound file (path, or a name already loaded) at this object."""
    eng, name = ctx.engine, str(i.get("Sound") or "")
    if eng is None or not name:
        return
    au = eng.audio
    if not au.has_sound(name):
        au.load_sound(name, name)
    try:
        au.play_at(name, ctx.node.world_position(), volume=float(i.get("Volume") or 1.0))
    except Exception:
        au.play(name, volume=float(i.get("Volume") or 1.0))


@vs_node("Game/Flash Light", kind="action", category="color",
         inputs=[("Color", "color", (1.0, 0.8, 0.4, 1.0)), ("Intensity", "float", 30.0)])
def _vs_flash(ctx, i):
    """A short flash of light at this object (explosions, muzzle flashes)."""
    if ctx.engine is not None:
        c = i.get("Color") or (1, 1, 1)
        ctx.engine.spawn_flash(ctx.node.world_position(), color=tuple(c[:3]),
                               intensity=float(i.get("Intensity") or 30.0))


@vs_node("Game/Stop Game", kind="action", category="output")
def _vs_stop(ctx, i):
    """Ends Play mode (in an exported game: quits to the end screen)."""
    if ctx.engine is not None:
        ctx.engine.request_stop_play()


@vs_node("Variables/Set Variable", kind="action", category="input",
         inputs=[("Name", "string", "score"), ("Value", "any", 0.0)])
def _vs_setvar(ctx, i):
    """Stores a value under a name (read it with Get Variable)."""
    ctx.vars[str(i.get("Name"))] = i.get("Value")


@vs_node("Variables/Add To Variable", kind="action", category="input",
         inputs=[("Name", "string", "score"), ("Amount", "float", 1.0)])
def _vs_addvar(ctx, i):
    """Adds Amount to a number variable."""
    k = str(i.get("Name"))
    try:
        ctx.vars[k] = float(ctx.vars.get(k) or 0.0) + float(i.get("Amount") or 0.0)
    except (TypeError, ValueError):
        ctx.vars[k] = i.get("Amount")


# ---- pure ------------------------------------------------------------------------------------
@vs_node("Variables/Get Variable", inputs=[("Name", "string", "score")], outputs=[("Value", "any")],
         category="input")
def _vs_getvar(ctx, i):
    """Reads a value stored with Set Variable."""
    return {"Value": ctx.vars.get(str(i.get("Name")))}


@vs_node("Values/Number", inputs=[("Value", "float", 1.0)], outputs=[("Value", "float")], category="input")
def _vs_num(ctx, i):
    return {"Value": i["Value"]}


@vs_node("Values/Text", inputs=[("Value", "string", "text")], outputs=[("Value", "string")], category="input")
def _vs_text(ctx, i):
    return {"Value": i["Value"]}


@vs_node("Values/Boolean", inputs=[("Value", "bool", True)], outputs=[("Value", "bool")], category="input")
def _vs_bool(ctx, i):
    return {"Value": bool(i["Value"])}


@vs_node("Values/Make Vector", inputs=_XYZ, outputs=[("Vector", "vector")], category="vector")
def _vs_mkvec(ctx, i):
    return {"Vector": _vs_xyz(i)}


@vs_node("Values/Break Vector", inputs=[("Vector", "vector")],
         outputs=[("X", "float"), ("Y", "float"), ("Z", "float")], category="vector")
def _vs_brvec(ctx, i):
    v = _vs_vec(i.get("Vector") or (0, 0, 0))
    return {"X": v.x, "Y": v.y, "Z": v.z}


def _vs_math(path, fn, doc, ins=("A", "B")):
    @vs_node(path, inputs=[(n, "float", 0.0) for n in ins], outputs=[("Result", "float")], doc=doc)
    def _f(ctx, i):
        return {"Result": fn(*[float(i.get(n) or 0.0) for n in ins])}
    return _f


_vs_math("Math/Add", lambda a, b: a + b, "A + B")
_vs_math("Math/Subtract", lambda a, b: a - b, "A - B")
_vs_math("Math/Multiply", lambda a, b: a * b, "A x B")
_vs_math("Math/Divide", lambda a, b: a / b if b else 0.0, "A / B (0 when B is 0)")
_vs_math("Math/Power", lambda a, b: math.pow(a, b) if a >= 0 or float(b).is_integer() else 0.0, "A to the power B")
_vs_math("Math/Min", min, "The smaller of A and B")
_vs_math("Math/Max", max, "The larger of A and B")
_vs_math("Math/Sin", lambda a: math.sin(a), "sine (radians)", ("A",))
_vs_math("Math/Cos", lambda a: math.cos(a), "cosine (radians)", ("A",))
_vs_math("Math/Abs", abs, "|A|", ("A",))
_vs_math("Math/Lerp", lambda a, b, t: a + (b - a) * t, "Blend from A to B by T (0..1)", ("A", "B", "T"))
_vs_math("Math/Clamp", lambda v, lo, hi: max(lo, min(hi, v)), "Keep Value between Min and Max",
         ("Value", "Min", "Max"))


@vs_node("Math/Random", inputs=[("Min", "float", 0.0), ("Max", "float", 1.0)], outputs=[("Result", "float")])
def _vs_rand(ctx, i):
    """A random number between Min and Max."""
    import random as _r
    return {"Result": _r.uniform(float(i["Min"] or 0.0), float(i["Max"] or 0.0))}


def _vs_cmp(path, fn, doc):
    @vs_node(path, inputs=[("A", "float", 0.0), ("B", "float", 0.0)], outputs=[("Result", "bool")],
             category="logic", doc=doc)
    def _f(ctx, i):
        return {"Result": bool(fn(float(i.get("A") or 0.0), float(i.get("B") or 0.0)))}
    return _f


_vs_cmp("Logic/Greater", lambda a, b: a > b, "A > B")
_vs_cmp("Logic/Less", lambda a, b: a < b, "A < B")
_vs_cmp("Logic/Equal", lambda a, b: abs(a - b) < 1e-6, "A = B")


@vs_node("Logic/And", inputs=[("A", "bool", True), ("B", "bool", True)], outputs=[("Result", "bool")],
         category="logic")
def _vs_and(ctx, i):
    return {"Result": bool(i["A"]) and bool(i["B"])}


@vs_node("Logic/Or", inputs=[("A", "bool", False), ("B", "bool", False)], outputs=[("Result", "bool")],
         category="logic")
def _vs_or(ctx, i):
    return {"Result": bool(i["A"]) or bool(i["B"])}


@vs_node("Logic/Not", inputs=[("A", "bool", False)], outputs=[("Result", "bool")], category="logic")
def _vs_not(ctx, i):
    return {"Result": not bool(i["A"])}


@vs_node("Input/Key Down", inputs=[("Key", "string", "w")], outputs=[("Down", "bool")], category="input")
def _vs_keydown(ctx, i):
    """True while the key is held."""
    inp = getattr(ctx.engine, "input", None)
    return {"Down": bool(inp is not None and inp.key(str(i.get("Key") or "").lower()))}


@vs_node("Game/Time", outputs=[("Seconds", "float"), ("Delta", "float")], category="input")
def _vs_time(ctx, i):
    """Seconds since Play started, and this frame's delta."""
    return {"Seconds": getattr(ctx.engine, "time", 0.0), "Delta": ctx.dt}


@vs_node("Objects/Self", outputs=[("Object", "object")], category="input")
def _vs_self(ctx, i):
    """The object this script is on."""
    return {"Object": ctx.node}


@vs_node("Objects/Find Object", inputs=[("Name", "string", "Cube")], outputs=[("Object", "object")],
         category="input")
def _vs_find(ctx, i):
    """Finds an object by its name in the Outliner."""
    return {"Object": ctx.engine.find_node(str(i.get("Name") or "")) if ctx.engine is not None else None}


@vs_node("Objects/Get Position", inputs=[("Target", "object")],
         outputs=[("X", "float"), ("Y", "float"), ("Z", "float"), ("Vector", "vector")], category="vector")
def _vs_getpos(ctx, i):
    """Where an object is (world space)."""
    n = ctx.target(i.get("Target"))
    p = n.world_position() if n is not None else glm.vec3(0.0)
    return {"X": p.x, "Y": p.y, "Z": p.z, "Vector": p}


@vs_node("Objects/Distance", inputs=[("A", "object"), ("B", "object")], outputs=[("Meters", "float")],
         category="vector")
def _vs_dist(ctx, i):
    """Distance between two objects (empty A = this object)."""
    a, b = ctx.target(i.get("A")), ctx.target(i.get("B"))
    if a is None or b is None:
        return {"Meters": 0.0}
    return {"Meters": float(glm.length(a.world_position() - b.world_position()))}


@vs_node("Game/Camera Position", outputs=[("X", "float"), ("Y", "float"), ("Z", "float"), ("Vector", "vector")],
         category="vector")
def _vs_campos(ctx, i):
    """Where the camera (the player's eyes in an FPS) is."""
    c = getattr(ctx.engine, "camera", None)
    p = glm.vec3(c.position) if c is not None else glm.vec3(0.0)
    return {"X": p.x, "Y": p.y, "Z": p.z, "Vector": p}


@vs_node("Values/To Text", inputs=[("Value", "any")], outputs=[("Text", "string")], category="input")
def _vs_totext(ctx, i):
    v = i.get("Value")
    if isinstance(v, float):
        v = ("%.3f" % v).rstrip("0").rstrip(".")
    return {"Text": "" if v is None else str(v)}
