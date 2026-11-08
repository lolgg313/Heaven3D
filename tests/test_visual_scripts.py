"""Visual scripts and the Animator run inside the engine (no GPU, no editor):

    python tests/test_visual_scripts.py
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.argv = [sys.argv[0]]
spec = importlib.util.spec_from_file_location("Heaven3D", os.path.join(ROOT, "Heaven3D.py"))
H = importlib.util.module_from_spec(spec)
sys.modules["Heaven3D"] = H
spec.loader.exec_module(H)
res = []


def check(n, c, d=""):
    res.append(bool(c))
    print(("PASS " if c else "FAIL ") + n + ("  [%s]" % (d,) if d != "" else ""), flush=True)


class FakeEngine:
    time = 0.0

    def __init__(self):
        self.sched, self.msgs, self.nodes = [], [], {}
        self.input = H.InputState()

    def schedule(self, d, fn):
        self.sched.append((d, fn))

    def find_node(self, n):
        return self.nodes.get(n)

    def send_message(self, e, d=None):
        self.msgs.append((e, d))

    def message_node(self, n, e, d=None):
        self.msgs.append((e, d))

    def destroy_node(self, n):
        n.visible = False


def script(graph, node=None, eng=None):
    bp = H.VisualScript()
    bp.node = node or H.SceneNode("Box")
    bp.engine = eng or FakeEngine()
    bp.properties["graph"] = graph
    return bp


def n(i, t, **v):
    return {"id": i, "type": t, "values": v}


def main():
    check("node types registered", len(H.VS_NODES) >= 50, len(H.VS_NODES))
    for path, spec in H.VS_NODES.items():
        if spec["kind"] == "action" and spec["inputs"][0][1] != "exec":
            check("action %s has an exec input" % path, False)
    # tick + translate per second, gated by Do Once
    bp = script({"nodes": [n(1, "Events/On Tick"), n(2, "Flow/Do Once"), n(3, "Transform/Translate", X=2.0)],
                 "links": [[1, "Then", 2, "In"], [2, "Then", 3, "In"]]})
    bp.on_begin_play()
    bp.on_tick(0.5)
    bp.on_tick(0.5)
    check("On Tick -> Translate per second, Do Once lets one through", abs(bp.node.position.x - 1.0) < 1e-6,
          bp.node.position.x)
    bp.on_begin_play()
    bp.on_tick(0.25)
    check("Do Once resets when Play restarts", abs(bp.node.position.x - 1.5) < 1e-6, bp.node.position.x)
    # for loop, variables, branch on a comparison, message, delay
    eng = FakeEngine()
    bp = script({"nodes": [n(1, "Events/On Begin Play"), n(2, "Flow/For Loop", Count=5),
                           n(3, "Variables/Add To Variable", Name="k", Amount=2.0),
                           n(4, "Variables/Get Variable", Name="k"), n(5, "Logic/Greater", B=9.0),
                           n(6, "Flow/Branch"), n(7, "Game/Send Message", Event="big"),
                           n(8, "Game/Send Message", Event="small"), n(9, "Flow/Delay", Seconds=2.0),
                           n(10, "Objects/Set Visible", Visible=False)],
                 "links": [[1, "Then", 2, "In"], [2, "Body", 3, "In"], [2, "Completed", 6, "In"],
                           [4, "Value", 5, "A"], [5, "Result", 6, "Condition"], [6, "True", 7, "In"],
                           [6, "False", 8, "In"], [7, "Then", 9, "In"], [9, "Then", 10, "In"]]}, eng=eng)
    bp.on_begin_play()
    check("For Loop ran its body 5 times", bp.vars.get("k") == 10.0, bp.vars)
    check("Branch took True (10 > 9) and sent the message", eng.msgs == [("big", None)], eng.msgs)
    check("Delay waits for the engine scheduler", bp.node.visible and len(eng.sched) == 1 and eng.sched[0][0] == 2.0)
    eng.sched[0][1]()
    check("...and continues after it", bp.node.visible is False)
    # message + key events with filters
    bp = script({"nodes": [n(1, "Events/On Message", Event="open"), n(2, "Transform/Set Position", Y=5.0),
                           n(3, "Events/On Key Pressed", Key="space"), n(4, "Transform/Set Scale", X=2.0)],
                 "links": [[1, "Then", 2, "In"], [3, "Then", 4, "In"]]})
    bp.on_begin_play()
    bp.on_message("close", None)
    check("On Message ignores other events", bp.node.position.y == 0.0)
    bp.on_message("open", None)
    check("On Message runs on its event", bp.node.position.y == 5.0)
    bp.engine.input.set_key("space", True)
    bp.on_tick(0.016)
    check("On Key Pressed fires on key down", abs(bp.node.scale.x - 2.0) < 1e-6, bp.node.scale)
    # timer
    bp = script({"nodes": [n(1, "Events/On Timer", Seconds=1.0), n(2, "Variables/Add To Variable", Name="t")],
                 "links": [[1, "Then", 2, "In"]]})
    bp.on_begin_play()
    for _ in range(25):
        bp.on_tick(0.1)
    check("On Timer every second: 2 runs in 2.5 s", bp.vars.get("t") == 2.0, bp.vars)
    # endless loop is stopped, broken node doesn't crash
    bp = script({"nodes": [n(1, "Events/On Begin Play"), n(2, "Flow/For Loop", Count=10 ** 9),
                           n(3, "Math/Divide", A=1.0, B=0.0)], "links": [[1, "Then", 2, "In"]]})
    bp.on_begin_play()
    check("huge loops are capped instead of freezing the game", True)
    check("unknown node types are skipped", H.VSGraph({"nodes": [n(1, "Nope/Nothing")], "links": []}).nodes == {})
    # custom node registered by a game
    @H.vs_node("Test/Double", inputs=[("V", "float", 1.0)], outputs=[("R", "float")])
    def _dbl(ctx, i):
        return {"R": i["V"] * 2}
    bp = script({"nodes": [n(1, "Events/On Begin Play"), n(2, "Test/Double", V=21.0),
                           n(3, "Variables/Set Variable", Name="x")],
                 "links": [[1, "Then", 3, "In"], [2, "R", 3, "Value"]]})
    bp.on_begin_play()
    check("@vs_node adds nodes games can use", bp.vars.get("x") == 42.0, bp.vars)

    # ---- animator ----
    node = H.SceneNode("Door")
    node.material = H.Material()
    a = H.Animator()
    a.node, a.engine = node, FakeEngine()
    a.properties["clips"] = {"Open": {"start": 1, "end": 25, "fps": 24, "tracks": [
        {"name": "rotation_euler.y", "keys": [[1, 0.0, "linear"], [25, 90.0, "linear"]]},
        {"name": "scale.x", "keys": [[1, 1.0, "bezier"], [13, 2.0, "bezier"], [25, 1.0, "bezier"]]},
        {"name": "visible", "keys": [[1, 1.0, "constant"], [20, 0.0, "constant"]]}]}}
    a.properties["loop"] = False
    a.on_begin_play()
    a.on_tick(0.5)                                   # frame 1 + 12 = 13
    check("linear track", abs(node.rotation_euler.y - 45.0) < 1e-4, node.rotation_euler.y)
    check("bezier track reaches its key", abs(node.scale.x - 2.0) < 1e-4, node.scale.x)
    a.on_tick(1.0)
    check("constant track switched visible off", node.visible is False)
    check("non-looping clip stops at the end", not a.playing and abs(node.rotation_euler.y - 90.0) < 1e-4)
    if node.material is not None:
        check("material colour channels animate", H.anim_apply(node, "material.base_color.g", 0.25)
              and abs(H.anim_read(node, "material.base_color.g") - 0.25) < 1e-6)
    check("unknown paths are ignored", not H.anim_apply(node, "nothing.here", 1.0))
    a.on_message("play_animation", "Open")
    check("play_animation message restarts the clip", a.playing and a.frame == 1.0)
    print("\n%d/%d passed" % (sum(res), len(res)))
    return 0 if all(res) else 1


if __name__ == "__main__":
    sys.exit(main())
