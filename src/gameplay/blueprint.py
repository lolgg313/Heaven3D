# @heaven3d-part gameplay/blueprint.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# #############################################################################
# SECTION 5  —  BLUEPRINT SYSTEM  (attachable scripts, UE-style)
# #############################################################################

class BlueprintRegistry:
    _registry: Dict[str, type] = {}

    @classmethod
    def register(cls, bp_cls: type):
        cls._registry[bp_cls.__name__] = bp_cls
        return bp_cls

    @classmethod
    def get(cls, name: str): return cls._registry.get(name)

    @classmethod
    def all_names(cls) -> List[str]: return sorted(cls._registry.keys())


def blueprint(cls):
    """Decorator: register a Blueprint subclass for save/load restoration."""
    BlueprintRegistry.register(cls)
    return cls


class Blueprint:
    """
    Attachable script.  Subclass and override the lifecycle hooks:

        @blueprint
        class SpinAndBob(Blueprint):
            def on_begin_play(self): ...
            def on_tick(self, dt): ...

    Attach via engine.attach_blueprint(node, SpinAndBob)
    """
    def __init__(self):
        self.node: Optional[SceneNode] = None
        self.engine = None
        self.properties: dict = {}

    def on_begin_play(self): pass
    def on_tick(self, dt: float): pass
    def on_end_play(self): pass
    def on_message(self, event: str, data: Any): pass

    # ---- physics callbacks (dispatched by the engine each fixed step) ----
    # `other` is the other SceneNode; `contact` (enter/stay only) exposes
    # .point / .normal (pointing from `other` toward self.node) / .depth /
    # .impulse.  Trigger callbacks fire for is_trigger colliders instead.
    def on_collision_enter(self, other: "SceneNode", contact): pass
    def on_collision_stay(self, other: "SceneNode", contact): pass
    def on_collision_exit(self, other: "SceneNode"): pass
    def on_trigger_enter(self, other: "SceneNode"): pass
    def on_trigger_exit(self, other: "SceneNode"): pass

    def log(self, msg): Log.info(f"[{self.__class__.__name__}] {msg}")

    def to_dict(self) -> dict:
        return {"class": self.__class__.__name__,
                "properties": self.properties}

    @classmethod
    def from_dict(cls, d: dict) -> Optional["Blueprint"]:
        bp_cls = BlueprintRegistry.get(d.get("class", ""))
        if bp_cls is None:
            Log.warn(f"Blueprint '{d.get('class')}' not registered; not restored.")
            return None
        bp = bp_cls()
        bp.properties = d.get("properties", {})
        return bp

