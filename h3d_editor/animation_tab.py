# SPDX-License-Identifier: MIT
"""Animation tab: keyframe the selected object with Vitrail's timeline.

Clips live on the object's Animator blueprint (the engine plays them in
Play and in exported games).  Scrubbing previews on the object; "Key"
records the current value of every track (or press I over a track).
"""
import json

from vitrail import Button, Column, Dropdown, InputDialog, Label, Row
from vitrail.animation import AnimationPlayer, Clip, Timeline, TransportBar


class AnimationTab:
    def __init__(self, editor, page):
        self.ed = editor
        self.h3d = editor.h3d
        self.page = page
        bar = Row(spacing=3, parent=page)
        self.title = Label("Animation", bold=True, width=200, expand=0, parent=bar)
        self.add_btn = Button("Add Animator to selection", self.add_animator, icon="add", primary=True,
                              expand=0, parent=bar)
        self.clips = Dropdown(["(no clip)"], label="Clip", width=200, expand=0, parent=bar,
                              on_change=lambda v: self.load_clip(v))
        Button("New clip", self.new_clip, icon="add", expand=0, parent=bar)
        self.track_pick = Dropdown(["position.y"], label="Track", width=260, expand=0, parent=bar)
        Button("Add track", self.add_track, icon="add", expand=0, parent=bar)
        Button("Key all", self.key_all, icon="keyframe-next", expand=0, parent=bar,
               tooltip="Record every track's current value at this frame")
        self.body = Row(spacing=0, fill=True, parent=page)
        self.player = None
        self.timeline = None
        self.transport = None
        self.node = self.bp = None
        self.clip_name = None
        self._last_json = None

    def find_bp(self, n):
        for bp in (n.blueprints if n is not None else ()):
            if type(bp).__name__ == "Animator":
                return bp
        return None

    def bind(self, n):
        self.flush()
        bp = self.find_bp(n)
        if n is self.node and bp is self.bp:
            return
        self.node, self.bp = n, bp
        self.add_btn.visible = n is not None and bp is None
        self.title.text = ("Animator: " + n.name) if bp is not None else (
            "Select an object" if n is None else n.name + " has no Animator")
        if n is not None:
            paths = self.h3d.anim_paths(n)
            self.track_pick.options = [(p, p) for p in paths]
            self.track_pick.set_value("position.y" if "position.y" in paths else (paths[0] if paths else None), emit=False)
        clips = list((bp.properties.get("clips") or {}).keys()) if bp is not None else []
        self.clips.options = [(c, c) for c in clips] or [("", "(no clip)")]
        cur = (bp.properties.get("clip") if bp is not None else None) or (clips[0] if clips else "")
        self.clips.set_value(cur, emit=False)
        self.load_clip(cur)
        self.ed.ui.invalidate(layout=True)

    def add_animator(self):
        n = self.ed.engine.selected
        if n is None:
            self.ed.toast("Select an object first")
            return
        bp = self.ed.add_blueprint(n, "Animator")
        if bp is not None and not bp.properties.get("clips"):
            bp.properties["clips"] = {"Clip": {"start": 1, "end": 120, "fps": 24, "tracks": []}}
            bp.properties["clip"] = "Clip"
        self.node = None
        self.bind(n)

    def new_clip(self):
        if self.bp is None:
            self.add_animator()
            return

        def made(name):
            if not name:
                return
            self.flush()
            self.bp.properties.setdefault("clips", {})[name] = {"start": 1, "end": 120, "fps": 24, "tracks": []}
            self.bp.properties["clip"] = name
            n = self.node
            self.node = None
            self.bind(n)
        InputDialog.ask(self.ed.ui, "New clip", "Name", made, default="Clip%d" % (len(self.bp.properties.get("clips", {})) + 1))

    # -- the vitrail clip <-> the blueprint's clip dict -----------------------------------
    def load_clip(self, name):
        self.body.clear()
        self.player = self.timeline = self.transport = None
        self.clip_name = None
        if self.bp is None or not name or name not in (self.bp.properties.get("clips") or {}):
            Label("Select an object, add an Animator, then add tracks and keys.", dim=True, parent=self.body)
            self.body.invalidate(layout=True)
            return
        self.clip_name = name
        self.bp.properties["clip"] = name
        d = self.bp.properties["clips"][name]
        clip = Clip(name, start=int(d.get("start", 1)), end=int(d.get("end", 120)), fps=int(d.get("fps", 24)))
        for t in d.get("tracks", ()):
            self._make_track(clip, t["name"], t.get("keys", ()))
        self.player = AnimationPlayer(clip).attach(self.ed.ui, shortcuts=False)
        col = Column(spacing=2, fill=True, parent=self.body)
        self.transport = TransportBar(self.player, parent=col)
        self.timeline = Timeline(self.player, fill=True, parent=col)
        self._last_json = json.dumps(self.to_dict())
        self.body.invalidate(layout=True)

    def _make_track(self, clip, path, keys=()):
        n = self.node
        h3d = self.h3d
        return clip.add_track(path, keys=[tuple(k) for k in keys],
                              target=lambda v, p=path: h3d.anim_apply(n, p, v),
                              getter=lambda p=path: h3d.anim_read(n, p))

    def to_dict(self):
        c = self.player.clip
        d = c.to_dict()
        d.pop("name", None)
        return d

    def add_track(self):
        if self.player is None:
            if self.bp is None:
                self.add_animator()
            if self.player is None:
                return
        path = self.track_pick.value
        if not path or any(t.name == path for t in self.player.clip.tracks):
            return
        tr = self._make_track(self.player.clip, path)
        tr.player = self.player
        tr.add_key(int(self.player.frame), self.h3d.anim_read(self.node, path))
        self.player.keys_changed.emit()
        if self.timeline is not None:
            self.timeline.invalidate(layout=True)
        self.flush(force=True)

    def key_all(self):
        if self.player is None:
            return
        f = round(self.player.frame)
        for t in self.player.clip.tracks:
            t.add_key(f, self.h3d.anim_read(self.node, t.name))
        self.player.keys_changed.emit()
        if self.timeline is not None:
            self.timeline.invalidate()
        self.flush(force=True)

    def flush(self, force=False):
        if self.player is None or self.bp is None or self.clip_name is None:
            return
        j = json.dumps(self.to_dict())
        if j != self._last_json:
            self._last_json = j
            self.bp.properties.setdefault("clips", {})[self.clip_name] = json.loads(j)

    def stop_preview(self):
        if self.player is not None and self.player.playing:
            self.player.pause()
