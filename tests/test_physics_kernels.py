"""The compiled contacts and contact solver give the Python results (needs a GPU / display):

    python tests/test_physics_kernels.py

  * box-box and box-plane contacts: 50,000 random cases (axis-aligned, nearly
    aligned, any rotation; bounded and endless planes), compiled kernel
    against the Python code: same hit / no hit, the same numbers bit for bit
  * a live pile (boxes, spheres, a kinematic pusher, friction, bounces, an
    impulse): at EVERY solver call the Python solver runs on a copy of the
    state next to the compiled one; velocities, positions and the stored
    impulses must be identical bit for bit
  * nothing sinks through the floor or flies away; stacks of two boxes stay
    where they were put; switched off, the Python code gives the same result
"""
import importlib.util
import os
import random
import shutil
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.pop("HEAVEN3D_NO_PHYS_KERNELS", None)
proj = tempfile.mkdtemp(prefix="h3dkern_")
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


def bits(v):
    return struct.pack("3f", v.x, v.y, v.z)


# ---- 1. contacts ----
rng = np.random.default_rng(11)


def box(c, ang, hs):
    q = glm.quat(glm.vec3(*(rng.uniform(-1, 1, 3) * ang)))
    return {"kind": "box", "c": glm.vec3(*c), "q": q,
            "e": (q * glm.vec3(1, 0, 0), q * glm.vec3(0, 1, 0), q * glm.vec3(0, 0, 1)), "h": glm.vec3(*hs)}


check("numba is there, so the kernels are what runs", H._HAS_NUMBA and H._PK_STATE["on"]
      and H._contact_box_box is H._pk_contact_box_box)
n = hits = exact = mism = 0
for k in range(30000):
    mode = k % 5
    ang = (0.0, 0.0, 0.02, 1.0, 3.1)[mode]
    A = box(rng.uniform(-1, 1, 3), ang, rng.uniform(0.2, 1.2, 3))
    B = box(rng.uniform(-1.6, 1.6, 3) * (0.3 if mode == 1 else 1.0), ang, rng.uniform(0.2, 1.2, 3))
    old, new = H._PK_PY_BOX_BOX(A, B), H._pk_contact_box_box(A, B)
    n += 1
    if (old is None) != (new is None):
        mism += 1
    elif old is not None:
        hits += 1
        (p0, n0, d0), (p1, n1, d1) = old[0], new[0]
        exact += bits(p0) == bits(p1) and bits(n0) == bits(n1) and d0 == d1
check("box-box contact: %d random pairs, %d touching, all bit-identical to the Python code" % (n, hits),
      mism == 0 and exact == hits and hits > 10000, (mism, hits - exact))
n = hits = exact = mism = 0
for k in range(10000):
    A = box(rng.uniform(-1, 1, 3) + np.array([0, 0.4, 0]), (0.0, 0.3, 3.1)[k % 3], rng.uniform(0.2, 1.2, 3))
    q = glm.quat(glm.vec3(*(rng.uniform(-1, 1, 3) * (0.0 if k % 2 else 0.6))))
    P = {"kind": "plane", "p": glm.vec3(*rng.uniform(-0.5, 0.5, 3)), "n": glm.normalize(q * glm.vec3(0, 1, 0)),
         "u": q * glm.vec3(1, 0, 0), "v": q * glm.vec3(0, 0, 1), "hx": float(rng.uniform(0.3, 3)),
         "hz": float(rng.uniform(0.3, 3)), "bounded": bool(k % 4 != 0)}
    for is_a in (True, False):
        old, new = H._PK_PY_BOX_PLANE(A, P, is_a), H._pk_contact_box_plane(A, P, is_a)
        n += 1
        if (old is None) != (new is None) or (old is not None and len(old) != len(new)):
            mism += 1
        elif old is not None:
            hits += 1
            exact += all(bits(a[0]) == bits(b[0]) and bits(a[1]) == bits(b[1]) and a[2] == b[2]
                         for a, b in zip(old, new))
check("box-plane contact: %d cases, %d touching, all bit-identical" % (n, hits),
      mism == 0 and exact == hits and hits > 5000, (mism, hits - exact))

# ---- 2. the solver inside a live simulation ----
eng = H.HeavenEngine(mode="editor", width=960, height=540, project_path=PROJECT)
for _n in [x for x in eng.scene.root.children if x.name == "Ocean"]:
    eng._delete_node(_n)
eng.add_plane(size=120.0)
random.seed(4)
bodies = []
for i in range(220):
    c = eng.add_cube(size=1.0) if i % 5 else eng.add_sphere(radius=0.5)
    c.name = "Body%03d" % i
    c.position = glm.vec3((i % 8) * 0.9 + random.uniform(-.15, .15), 0.8 + (i // 64) * 1.25,
                          (i // 8 % 8) * 0.9 + random.uniform(-.15, .15))
    c.rotation_euler = glm.vec3(0.0, random.uniform(0, 90) if i % 3 == 0 else 0.0, 0.0)
    eng.add_rigidbody(c, mass=1.0 + (i % 3), restitution=0.6 if i % 7 == 0 else None,
                      friction=0.1 if i % 11 == 0 else None)
    bodies.append(c)
pusher = eng.add_cube(size=2.0)
pusher.name = "Pusher"
pusher.position = glm.vec3(-6.0, 1.0, 3.0)
eng.add_rigidbody(pusher, body_type="kinematic")
eng.deselect()

PW = H.PhysicsWorld
stat = {"vel": 0, "vel_bad": 0, "pos": 0, "pos_bad": 0, "contacts": 0, "small": 0, "most": 0}
_fast_vel, _fast_pos = PW._solve_contacts_velocity, PW._solve_contacts_position


def bodies_of(contacts):
    out = {}
    for c in contacts:
        for e in (c.a, c.b):
            if e.body is not None:
                out[id(e.body)] = e.body
    return list(out.values())


def vel_shadow(self, contacts):
    bs = bodies_of(contacts)
    v0 = [glm.vec3(b.velocity) for b in bs]
    j0 = [(c.jn, c.jt0) for c in contacts]
    H._PK_PY_SOLVE_VEL(self, contacts)                       # the Python solver
    want = ([bits(b.velocity) for b in bs], [(c.jn, c.jt0) for c in contacts])
    for b, v in zip(bs, v0):
        b.velocity.x, b.velocity.y, b.velocity.z = v.x, v.y, v.z
    for c, (a, t) in zip(contacts, j0):
        c.jn, c.jt0 = a, t
    _fast_vel(self, contacts)                                # the compiled one, from the same state
    got = ([bits(b.velocity) for b in bs], [(c.jn, c.jt0) for c in contacts])
    stat["vel"] += 1
    stat["contacts"] += len(contacts)
    stat["most"] = max(stat["most"], len(contacts))
    stat["small"] += len(contacts) < H._PK_MIN_CONTACTS
    stat["vel_bad"] += want != got


def pos_shadow(self, contacts):
    bs = bodies_of(contacts)
    p0 = [glm.vec3(b.position) for b in bs]
    for c in contacts:
        H._PK_PY_SOLVE_POS(self, c)
    want = [bits(b.position) for b in bs]
    for b, p in zip(bs, p0):
        b.position.x, b.position.y, b.position.z = p.x, p.y, p.z
    _fast_pos(self, contacts)
    stat["pos"] += 1
    stat["pos_bad"] += want != [bits(b.position) for b in bs]


def sim():
    PW._solve_contacts_velocity, PW._solve_contacts_position = vel_shadow, pos_shadow
    eng.toggle_play()
    for k in range(420):
        if k < 200:
            pusher.position = pusher.position + glm.vec3(0.05, 0.0, 0.0)     # ploughs into the pile
        eng.physics.step(1 / 60.0)
        eng._flush_physics_events()
        if k == 120:
            bodies[5].rigidbody.add_impulse(glm.vec3(14.0, 6.0, -9.0))
    check("velocity solver: %d calls, %d contacts solved, all bit-identical to the Python solver"
          % (stat["vel"], stat["contacts"]), stat["vel"] > 500 and stat["vel_bad"] == 0 and stat["most"] > 300,
          (stat["vel_bad"], stat["most"]))
    check("position correction: %d calls, all bit-identical" % stat["pos"],
          stat["pos"] == stat["vel"] and stat["pos_bad"] == 0, stat["pos_bad"])
    PW._solve_contacts_velocity, PW._solve_contacts_position = _fast_vel, _fast_pos
    for k in range(500):
        eng.physics.step(1 / 60.0)
    ys = [b.position.y for b in bodies]
    far = max(max(abs(b.position.x), abs(b.position.z)) for b in bodies)
    asleep = sum(1 for e in eng.physics.entries if e.kind == "dynamic" and e.asleep)
    check("the pile comes to rest: nothing sank through the floor or flew away",
          min(ys) > 0.3 and max(ys) < 12.0 and far < 60.0, (round(min(ys), 3), round(max(ys), 2), round(far, 1)))
    speed = sorted(glm.length(b.rigidbody.velocity) for b in bodies)
    # (this pile is spawned interpenetrating with a pusher inside it: it keeps creeping, with the
    #  Python solver too; a pile that can rest is checked below)
    check("... and nothing in it is fast", speed[-1] < 1.5, (round(speed[len(speed) // 2], 4), round(speed[-1], 3)))
    eng.toggle_play()
    for n in bodies + [pusher]:
        eng._delete_node(n)
    stack = []
    for i in range(72):                                   # 36 stacks of two boxes
        c = eng.add_cube(size=1.0)
        c.position = glm.vec3((i % 6) * 1.5, 0.55 + (i // 36) * 1.1, (i // 6 % 6) * 1.5)
        eng.add_rigidbody(c, mass=1.0)
        stack.append(c)
    eng.deselect()
    eng.toggle_play()
    for k in range(400):
        eng.physics.step(1 / 60.0)
    asleep = sum(1 for e in eng.physics.entries if e.kind == "dynamic" and e.asleep)
    tops = [b.position.y for b in stack[36:]]
    check("36 stacks of two boxes stand where they were put",
          all(abs(b.position.x - (i % 6) * 1.5) < 0.05 and abs(b.position.z - (i // 6 % 6) * 1.5) < 0.05
              for i, b in enumerate(stack)), "asleep %d of %d" % (asleep, len(stack)))
    check("... at the right height (no sinking, no bouncing)", all(1.47 < y < 1.51 for y in tops),
          (round(min(tops), 3), round(max(tops), 3)))
    eng.toggle_play()
    # few contacts: the Python loop (the kernel isn't worth its set-up there)
    H._PK_STATE["on"] = False
    A, B = box((0, 0, 0), 1.0, (0.5, 0.5, 0.5)), box((0.4, 0.3, 0.2), 1.0, (0.5, 0.5, 0.5))
    a = H._pk_contact_box_box(A, B)
    H._PK_STATE["on"] = True
    b = H._pk_contact_box_box(A, B)
    check("switched off, the same call gives the Python result", a is not None and b is not None
          and bits(a[0][0]) == bits(b[0][0]) and a[0][2] == b[0][2])
    check("no kernel ever failed", H._PK_STATE["errors"] == 0, H._PK_STATE["errors"])


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
