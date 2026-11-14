"""The compiled broadphase and the event shortcut change nothing (needs a GPU / display):

    python tests/test_physics_fast_paths.py

  * the compiled sweep-and-prune returns the same pairs IN THE SAME ORDER as
    the Python loops (random boxes, equal x values, both pair searches); a
    box that is not a number goes to the Python loop
  * a pile of 260 bodies (boxes, spheres, a trigger zone, an impulse, a body
    teleported through the zone) is simulated once; at every step the old
    code runs next to the new one on the SAME state:
      - every broadphase call gives the identical set
      - the scripts receive exactly the same collision / trigger events
        (enter, stay, exit) although contacts of pairs without any script
        are no longer worked out a second time
"""
import collections
import importlib.util
import os
import random
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
proj = tempfile.mkdtemp(prefix="h3dphys_")
shutil.copytree(os.path.join(ROOT, "templates", "empty"), proj, dirs_exist_ok=True)
PROJECT = [os.path.join(proj, f) for f in os.listdir(proj) if f.endswith(".heaven3d")][0]
sys.argv = [sys.argv[0]]
spec = importlib.util.spec_from_file_location("Heaven3D", os.path.join(ROOT, "Heaven3D.py"))
H = importlib.util.module_from_spec(spec)
sys.modules["Heaven3D"] = H
spec.loader.exec_module(H)
H._h3d_set_splash_mode("off")
import glm  # noqa: E402
import numpy as np  # noqa: E402
import pygame  # noqa: E402

res = []


def check(n, c, d=""):
    res.append(bool(c))
    print(("PASS " if c else "FAIL ") + n + ("  [%s]" % (d,) if d != "" else ""), flush=True)


# ---- 1. the pair search on its own ----
class _Box:
    __slots__ = ("aabb0", "aabb1")


def boxes(rng, n, spread, dup):
    out = []
    for i in range(n):
        e = _Box()
        c = rng.uniform(-spread, spread, 3)
        if dup and i % 3 == 0:
            c[0] = round(c[0])                # equal x values: the order must stay the same
        h = rng.uniform(0.2, 2.5, 3)
        e.aabb0, e.aabb1 = glm.vec3(*(c - h)), glm.vec3(*(c + h))
        out.append(e)
    return out


sap = H.SweepAndPrune()
rng = np.random.default_rng(5)
bad = cases = pairs = 0
for n in (0, 1, 2, 23, 24, 60, 300, 1200):
    for spread in (2.0, 12.0, 60.0):
        for dup in (False, True):
            es, other = boxes(rng, n, spread, dup), boxes(rng, max(1, n // 3), spread, dup)
            a, b = sap.run(es), sap._run_py(es)
            c, d = sap.run_cross(es, other), sap._run_cross_py(es, other)
            cases += 2
            pairs += len(a) + len(c)
            bad += (a != b or list(a) != list(b)) + (c != d or list(c) != list(d))
check("numba is there, so the compiled pair search is what runs", H._HAS_NUMBA)
check("compiled pair search = Python loops, same pairs in the same order (%d cases, %d pairs)" % (cases, pairs),
      bad == 0 and pairs > 10000, bad)
es = boxes(rng, 40, 5.0, False)
es[7].aabb0 = glm.vec3(float("nan"), 0.0, 0.0)
check("a box that is not a number is left to the Python loop", H._sap_run_fast(es) is None
      and sap.run(es) == sap._run_py(es))

# ---- 2. a whole simulation, old code next to new code on the same state ----
eng = H.HeavenEngine(mode="editor", width=960, height=540, project_path=PROJECT)
for n in [n for n in eng.scene.root.children if n.name == "Ocean"]:
    eng._delete_node(n)
seen = {"n": 0, "ok": 0}


class Logger(H.Blueprint):
    def on_collision_enter(self, other, contact):          # contact.point and contact["point"] both work
        seen["n"] += 1
        seen["ok"] += (contact.point is contact["point"] and contact.depth == contact["depth"]
                       and contact.normal is contact["normal"] and contact.impulse == contact["impulse"]
                       and isinstance(contact, dict) and not hasattr(contact, "nothing"))
eng.add_plane(size=120.0)
random.seed(4)
bodies = []
for i in range(260):
    c = eng.add_cube(size=1.0) if i % 5 else eng.add_sphere(radius=0.5)
    c.name = "Body%03d" % i
    c.position = glm.vec3((i % 8) * 0.9 + random.uniform(-.15, .15), 0.8 + (i // 64) * 1.25, (i // 8 % 8) * 0.9 + random.uniform(-.15, .15))
    eng.add_rigidbody(c, mass=1.0 + (i % 3))
    if i % 13 == 0: eng.attach_blueprint(c, Logger)
    bodies.append(c)
z = eng.add_cube(size=3.0); z.name = "Zone"; z.position = glm.vec3(3, 1.5, 3); z.collider.is_trigger = True
eng.attach_blueprint(z, Logger)
eng.deselect()
stat = {"sap": 0, "sap_bad": 0, "steps": 0, "ev_bad": 0, "ev_new": 0, "ev_all": 0, "kinds": collections.Counter()}
SAP = H.SweepAndPrune
_run, _cross = SAP.run, SAP.run_cross
def run(self, entries):
    a = _run(self, entries); b = self._run_py(entries); self.last_pairs = a
    stat["sap"] += 1; stat["sap_bad"] += (a != b or list(a) != list(b)); return a
def cross(self, la, lb):
    a = _cross(self, la, lb); b = self._run_cross_py(la, lb)
    stat["sap"] += 1; stat["sap_bad"] += (a != b or list(a) != list(b)); return a
SAP.run, SAP.run_cross = run, cross
PW = H.PhysicsWorld
_disp = PW._dispatch_events
shadow = {"s": set(), "t": set(), "n": {}}
def sig(ev):
    kind, na, nb, c = ev
    return (kind, na.name, nb.name, None if c is None else (round(c.depth, 6), round(c.point.x, 5), round(c.point.y, 5), round(c.point.z, 5)))
def disp(self):
    keep = (self._solid_pairs, self._trigger_pairs, self._pair_nodes, self.events)
    # old behaviour (every pair), on its own running state
    self._solid_pairs, self._trigger_pairs, self._pair_nodes, self.events = shadow["s"], shadow["t"], shadow["n"], []
    self.events_for_all = True
    _disp(self)
    old = [e for e in self.events if e[1].blueprints or e[2].blueprints]     # what any script could have received
    stat["ev_all"] += len(self.events)
    shadow["s"], shadow["t"], shadow["n"] = self._solid_pairs, self._trigger_pairs, self._pair_nodes
    # new behaviour
    self._solid_pairs, self._trigger_pairs, self._pair_nodes, self.events = keep[0], keep[1], keep[2], []
    self.events_for_all = False
    _disp(self)
    new = self.events
    stat["ev_new"] += len(new); stat["steps"] += 1
    for e in new: stat["kinds"][e[0]] += 1
    if collections.Counter(map(sig, old)) != collections.Counter(map(sig, new)):
        stat["ev_bad"] += 1
    self.events = keep[3] + new
PW._dispatch_events = disp


def sim():
    eng.toggle_play()
    for k in range(300):
        eng.physics.step(1 / 60.0)
        eng._flush_physics_events()
        if k == 120:
            bodies[5].rigidbody.add_impulse(glm.vec3(14.0, 6.0, -9.0))
        if k == 180:
            bodies[40].position = glm.vec3(3, 9, 3)          # dropped through the trigger zone
    eng.toggle_play()
    k = stat["kinds"]
    check("every broadphase call of the simulation equals the old code (%d calls)" % stat["sap"],
          stat["sap"] > 1000 and stat["sap_bad"] == 0, stat["sap_bad"])
    check("scripts receive exactly the same events at every step (%d steps, %d events)"
          % (stat["steps"], stat["ev_new"]), stat["steps"] == 300 and stat["ev_bad"] == 0, stat["ev_bad"])
    check("... enters, stays, exits and trigger events all occurred",
          all(k[n] > 0 for n in ("collision_enter", "collision_stay", "collision_exit", "trigger_enter",
                                 "trigger_exit")), dict(k))
    check("... while far fewer contacts were worked out for events", stat["ev_new"] < stat["ev_all"] * 0.5,
          (stat["ev_new"], stat["ev_all"]))
    PW._dispatch_events = _disp
    SAP.run, SAP.run_cross = _run, _cross
    eng.physics.events_for_all = True
    check("PhysicsWorld.events_for_all = True records every pair again", eng.physics.events_for_all)
    check("a script reads its contact as contact.point or contact[\"point\"]",
          seen["n"] > 100 and seen["ok"] == seen["n"], seen)


st = {"f": 0}
_flip = pygame.display.flip


def flip():
    st["f"] += 1
    if st["f"] == 4:
        try:
            sim()
        except Exception as ex:
            import traceback
            traceback.print_exc()
            check("simulation", False, ex)
        print("\n%d/%d passed" % (sum(res), len(res)), flush=True)
        shutil.rmtree(proj, ignore_errors=True)
        os._exit(0 if all(res) else 1)
    _flip()


pygame.display.flip = flip
eng.run()
