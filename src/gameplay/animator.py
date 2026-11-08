# @heaven3d-part gameplay/animator.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# ---------------------------------------------------------------------------
# [ANIMATOR]  Keyframe animation made in the editor's Animation tab
#
#   An Animator blueprint holds named clips.  A clip is a set of tracks;
#   a track animates ONE number on the object, named by a path:
#
#       position.x  rotation_euler.y  scale.z  visible
#       material.metallic  material.base_color.r  material.emissive_intensity
#       point_light.intensity  spot_light.range  user_data.speed
#
#   Keys are [frame, value, interp] with interp "constant", "linear" or
#   "bezier" (smooth, no overshoot) -- the same curves the editor draws.
#
#   From code:  bp = engine.first_blueprint("Animator"); bp.play("Open")
#   From a visual script or another blueprint: send the message
#   "play_animation" with the clip name as data.
# ---------------------------------------------------------------------------
_ANIM_COMPONENTS = {"x": 0, "y": 1, "z": 2, "w": 3, "r": 0, "g": 1, "b": 2, "a": 3}


def _anim_tangent(keys, i):
    if i <= 0 or i >= len(keys) - 1:
        return 0.0
    a, b, c = keys[i - 1], keys[i], keys[i + 1]
    if (b[1] - a[1]) * (c[1] - b[1]) <= 0:
        return 0.0
    return (c[1] - a[1]) / max(1e-9, c[0] - a[0])


def anim_sample(keys, frame, default=0.0):
    """Value of a track (sorted [frame, value, interp] keys) at ``frame``."""
    if not keys:
        return default
    if frame <= keys[0][0]:
        return keys[0][1]
    if frame >= keys[-1][0]:
        return keys[-1][1]
    lo, hi = 0, len(keys) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if keys[mid][0] <= frame:
            lo = mid
        else:
            hi = mid
    k0, k1 = keys[lo], keys[hi]
    h = k1[0] - k0[0]
    t = (frame - k0[0]) / h if h else 0.0
    interp = k0[2] if len(k0) > 2 else "bezier"
    if interp == "constant":
        return k0[1]
    if interp == "linear":
        return k0[1] + (k1[1] - k0[1]) * t
    m0, m1 = _anim_tangent(keys, lo) * h, _anim_tangent(keys, hi) * h
    t2, t3 = t * t, t * t * t
    return ((2 * t3 - 3 * t2 + 1) * k0[1] + (t3 - 2 * t2 + t) * m0 +
            (-2 * t3 + 3 * t2) * k1[1] + (t3 - t2) * m1)


def _anim_resolve(node, path):
    """-> (owner, attribute, component index or None) for a track path."""
    parts = path.split(".")
    comp = None
    if len(parts) >= 2 and parts[-1] in _ANIM_COMPONENTS:    # "position.x"
        comp = _ANIM_COMPONENTS[parts[-1]]
        parts = parts[:-1]
    owner = node
    for p in parts[:-1]:
        owner = owner.get(p) if isinstance(owner, dict) else getattr(owner, p, None)
        if owner is None:
            return None
    return owner, parts[-1], comp


def anim_read(node, path, default=0.0):
    """Current value of an animatable number on ``node`` (see the header)."""
    r = _anim_resolve(node, path)
    if r is None:
        return default
    owner, attr, comp = r
    try:
        v = owner.get(attr) if isinstance(owner, dict) else getattr(owner, attr)
        if comp is not None:
            v = v[comp]
        if isinstance(v, bool):
            return 1.0 if v else 0.0
        return float(v)
    except Exception:
        return default


def anim_apply(node, path, value):
    """Set an animatable number on ``node``.  Returns True on success."""
    r = _anim_resolve(node, path)
    if r is None:
        return False
    owner, attr, comp = r
    try:
        cur = owner.get(attr) if isinstance(owner, dict) else getattr(owner, attr)
        if comp is None:
            if isinstance(cur, bool):
                new = value >= 0.5
            elif isinstance(cur, int):
                new = int(round(value))
            else:
                new = float(value)
        elif isinstance(cur, tuple):
            lst = list(cur)
            lst[comp] = float(value)
            new = tuple(lst)
        elif isinstance(cur, list):
            cur[comp] = float(value)
            return True
        else:                                          # glm vectors: replace, don't mutate shared ones
            new = type(cur)(cur)
            new[comp] = float(value)
        if isinstance(owner, dict):
            owner[attr] = new
        else:
            setattr(owner, attr, new)
        return True
    except Exception:
        return False


def anim_paths(node):
    """Track paths that make sense for ``node`` (the editor's "Add track" list)."""
    out = []
    for a in ("position", "rotation_euler", "scale"):
        out += ["%s.%s" % (a, c) for c in "xyz"]
    out.append("visible")
    if node.material is not None:
        out += ["material.base_color.%s" % c for c in "rgba"]
        out += ["material.metallic", "material.roughness", "material.emissive_intensity"]
        out += ["material.emissive.%s" % c for c in "rgb"]
    for attr in ("point_light", "spot_light"):
        lt = getattr(node, attr, None)
        if lt is not None:
            out += ["%s.intensity" % attr, "%s.range" % attr] + ["%s.color.%s" % (attr, c) for c in "rgb"]
    if getattr(node, "sun_light", None) is not None:
        out += ["sun_light.intensity"]
    return out


@blueprint
class Animator(Blueprint):
    """Plays keyframe clips made in the editor's Animation tab."""

    def __init__(self):
        super().__init__()
        self.properties = {"clips": {}, "clip": "", "autoplay": True, "loop": True, "speed": 1.0}
        self.frame = 0.0
        self.playing = False
        self.current = ""
        self.finished_clip = ""

    def clip(self, name=None):
        clips = self.properties.get("clips") or {}
        name = name or self.current or self.properties.get("clip") or (next(iter(clips)) if clips else "")
        return clips.get(name), name

    def play(self, name=None, restart=True):
        c, name = self.clip(name)
        if c is None:
            Log.warn("[Animator] '%s' has no clip '%s'" % (getattr(self.node, "name", "?"), name))
            return
        if restart or name != self.current:
            self.frame = float(c.get("start", 1))
        self.current = name
        self.playing = True
        self._apply(c)

    def stop(self):
        self.playing = False

    def on_begin_play(self):
        self.current = self.properties.get("clip") or ""
        if self.properties.get("autoplay", True):
            self.play()

    def on_message(self, event, data):
        if event == "play_animation":
            self.play(str(data) if data else None)
        elif event == "stop_animation":
            self.stop()

    def on_tick(self, dt):
        if not self.playing:
            return
        c, _name = self.clip()
        if c is None:
            self.playing = False
            return
        start, end = float(c.get("start", 1)), float(c.get("end", 120))
        self.frame += dt * float(c.get("fps", 24)) * float(self.properties.get("speed", 1.0) or 0.0)
        if self.frame > end:
            if self.properties.get("loop", True) and end > start:
                self.frame = start + (self.frame - start) % (end - start)
            else:
                self.frame = end
                self.playing = False
                self.finished_clip = self.current
                if self.engine is not None:
                    self.engine.message_node(self.node, "animation_finished", self.current)
        self._apply(c)

    def _apply(self, c, frame=None):
        f = self.frame if frame is None else frame
        node = self.node
        for t in c.get("tracks", ()):
            keys = t.get("keys")
            if keys:
                anim_apply(node, t.get("name", ""), anim_sample(keys, f))
