# @heaven3d-part physics/physics_overhaul.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# ============================================================ physics overhaul
# [TRIPLEA:physics_overhaul_block]
# Persistent contact manifolds (warm starting), simulation islands with
# sleeping, sweep-and-prune broadphase, and an optional numba SoA integrator.
# Sleeping bodies cost ZERO: they are excluded from integration, AABB
# refresh, the solver and transform write-back; they only exist as cached
# AABBs so an awake intruder can find and wake them.

_PHYS_WAKE_VEL2 = 0.04   # (0.2 m/s)^2 relative velocity that wakes an island


class PersistentManifold:
    """Contact manifold between two _PhysEntry objects, persisted across
    frames so accumulated impulses (jn/jt0/jt1) warm-start the solver."""
    MATCH_RADIUS = 0.04
    __slots__ = ("body_a", "body_b", "contacts", "last_frame")

    def __init__(self, ea, eb):
        self.body_a = ea              # _PhysEntry refs
        self.body_b = eb
        self.contacts = []
        self.last_frame = -1

    def refresh(self, new_contacts, frame):
        old = self.contacts
        if old:
            r2 = self.MATCH_RADIUS * self.MATCH_RADIUS
            for c in new_contacts:
                best = None
                best_d = r2
                for o in old:
                    dx = o.point.x - c.point.x
                    dy = o.point.y - c.point.y
                    dz = o.point.z - c.point.z
                    d = dx * dx + dy * dy + dz * dz
                    if d < best_d:
                        best_d = d
                        best = o
                if best is not None:   # warm start from nearest old contact
                    c.jn = best.jn
                    c.jt0 = best.jt0
                    c.jt1 = best.jt1
        self.contacts = list(new_contacts)
        self.last_frame = frame

    def is_stale(self, frame, max_age=2):
        return frame - self.last_frame > max_age


class ManifoldManager:
    """Owns all PersistentManifolds, keyed by sorted (id(entryA), id(entryB))."""

    def __init__(self):
        self.manifolds: Dict[tuple, PersistentManifold] = {}
        self.frame = 0

    @staticmethod
    def key(ea, eb):
        ia, ib = id(ea), id(eb)
        return (ia, ib) if ia <= ib else (ib, ia)

    def register_contacts(self, ea, eb, new_contacts, frame):
        key = self.key(ea, eb)
        m = self.manifolds.get(key)
        if m is None:
            m = PersistentManifold(ea, eb)
            self.manifolds[key] = m
        m.refresh(new_contacts, frame)
        if frame > self.frame:
            self.frame = frame
        return m

    def cull_stale(self, frame):
        self.frame = frame
        # Manifolds involving a SLEEPING entry are dormant, not stale:
        # keep them so a later intruder wakes the island through them.
        dead = [k for k, m in self.manifolds.items()
                if m.is_stale(frame)
                and not (m.body_a.asleep or m.body_b.asleep)]
        for k in dead:
            del self.manifolds[k]

    def active_manifolds(self, awake_ids=None):
        out = []
        for m in self.manifolds.values():
            if not m.contacts or m.is_stale(self.frame):
                continue
            if awake_ids is not None and id(m.body_a) not in awake_ids \
                    and id(m.body_b) not in awake_ids:
                continue
            out.append(m)
        return out

    def purge_entries(self, entry_ids):
        if not entry_ids:
            return
        self.manifolds = {k: m for k, m in self.manifolds.items()
                          if id(m.body_a) not in entry_ids
                          and id(m.body_b) not in entry_ids}


class PhysicsIsland:
    """A set of dynamically interacting _PhysEntry objects (one sleep unit)."""
    __slots__ = ("entries",)

    def __init__(self):
        self.entries = []


class IslandManager:
    """Builds islands by flood-fill over manifold adjacency and puts
    low-energy islands to sleep (dynamic entries only, never kinematic)."""
    SLEEP_EPSILON = 0.0008
    SLEEP_DELAY = 0.5

    def __init__(self):
        self.islands = []
        self._timers = {}         # id(entry) -> accumulated low-energy time
        self._entry_island = {}   # id(entry) -> PhysicsIsland

    def rebuild(self, awake_entries, manifolds):
        adj = {}
        for m in manifolds:
            a, b = m.body_a, m.body_b
            adj.setdefault(id(a), []).append(b)
            adj.setdefault(id(b), []).append(a)
        self.islands = []
        self._entry_island = {}
        seen = set()
        for e in awake_entries:
            if id(e) in seen:
                continue
            isl = PhysicsIsland()
            stack = [e]
            seen.add(id(e))
            while stack:
                cur = stack.pop()
                isl.entries.append(cur)
                for nb in adj.get(id(cur), ()):
                    if id(nb) not in seen and not nb.asleep \
                            and nb.kind == "dynamic":
                        seen.add(id(nb))
                        stack.append(nb)
            for cur in isl.entries:
                self._entry_island[id(cur)] = isl
            self.islands.append(isl)

    def try_sleep(self, island, dt):
        """Sleep the island once every member's kinetic energy
        (mass*|v|^2 + |w|^2) stayed below SLEEP_EPSILON for SLEEP_DELAY."""
        ready = True
        for e in island.entries:
            b = e.body
            ke = b.mass * glm.dot(b.velocity, b.velocity) \
                + glm.dot(b.angular_velocity, b.angular_velocity)
            if ke < self.SLEEP_EPSILON:
                t = self._timers.get(id(e), 0.0) + dt
                self._timers[id(e)] = t
                if t < self.SLEEP_DELAY:
                    ready = False
            else:
                self._timers[id(e)] = 0.0
                ready = False
        if ready and island.entries:
            for e in island.entries:
                e.asleep = True
                e.body.velocity = glm.vec3(0.0)
                e.body.angular_velocity = glm.vec3(0.0)
            return True
        return False

    def wake_island_of(self, entry, manifolds):
        """Wake `entry` plus everything reachable through its manifolds
        (its island -- whether or not it joined the latest rebuild)."""
        mans = list(manifolds)
        seen = set()
        stack = [entry]
        while stack:
            cur = stack.pop()
            if id(cur) in seen:
                continue
            seen.add(id(cur))
            cur.asleep = False
            self._timers[id(cur)] = 0.0
            for m in mans:
                if m.body_a is cur:
                    stack.append(m.body_b)
                elif m.body_b is cur:
                    stack.append(m.body_a)

    def purge_entries(self, entry_ids):
        if not entry_ids:
            return
        for i in entry_ids:
            self._timers.pop(i, None)
            self._entry_island.pop(i, None)


class SweepAndPrune:
    """Single-axis (X) sweep-and-prune broadphase with full Y/Z overlap."""

    def __init__(self):
        self.last_pairs = set()

    @staticmethod
    def _yz_overlap(a, b):
        return a.aabb0.y <= b.aabb1.y and b.aabb0.y <= a.aabb1.y \
            and a.aabb0.z <= b.aabb1.z and b.aabb0.z <= a.aabb1.z

    @staticmethod
    def _pair(ia, ib):
        return (ia, ib) if ia <= ib else (ib, ia)

    def run(self, entries):
        """All overlapping pairs inside one list (sorted by aabb0.x)."""
        order = sorted(entries, key=lambda e: e.aabb0.x)
        pairs = set()
        active = []
        for e in order:
            x0 = e.aabb0.x
            kept = []
            for a in active:
                if a.aabb1.x >= x0:
                    if self._yz_overlap(a, e):
                        pairs.add(self._pair(id(a), id(e)))
                    kept.append(a)
            kept.append(e)
            active = kept
        self.last_pairs = pairs
        return pairs

    def run_cross(self, list_a, list_b):
        """Overlapping (a, b) pairs between two lists (aabb overlap XYZ)."""
        pairs = set()
        if not list_a or not list_b:
            return pairs
        sb = sorted(list_b, key=lambda e: e.aabb0.x)
        for e in list_a:
            x0 = e.aabb0.x
            x1 = e.aabb1.x
            for o in sb:
                if o.aabb0.x > x1:
                    break
                if o.aabb1.x < x0:
                    continue
                if self._yz_overlap(e, o):
                    pairs.add(self._pair(id(e), id(o)))
        return pairs


@_jit(cache=True)
def _soa_integrate(pos, vel, force, invmass, gscale, grav, h, lin_damp):
    """Structure-of-arrays linear integrator (numba-accelerated when
    available).  Mirrors the scalar loop in PhysicsWorld._substep exactly:
    v += (gravity*gravity_scale + force*inv_mass) * h; v *= damping;
    p += v*h; force = 0.  Angular damping/orientation stay in Python."""
    n = pos.shape[0]
    for i in range(n):
        im = invmass[i]
        ax = grav[0] * gscale[i] + force[i, 0] * im
        ay = grav[1] * gscale[i] + force[i, 1] * im
        az = grav[2] * gscale[i] + force[i, 2] * im
        damp = 1.0 - lin_damp[i] * h
        if damp < 0.0:
            damp = 0.0
        vx = (vel[i, 0] + ax * h) * damp
        vy = (vel[i, 1] + ay * h) * damp
        vz = (vel[i, 2] + az * h) * damp
        vel[i, 0] = vx
        vel[i, 1] = vy
        vel[i, 2] = vz
        pos[i, 0] += vx * h
        pos[i, 1] += vy * h
        pos[i, 2] += vz * h
        force[i, 0] = 0.0
        force[i, 1] = 0.0
        force[i, 2] = 0.0


def _pw_soa_integrate_awake(self, awake, h):
    """Sync awake dynamic bodies to flat float32 arrays, run _soa_integrate,
    sync back.  Angular damping/orientation integration and the NaN guard
    are applied per body afterwards, identical to the scalar loop."""
    n = len(awake)
    bufs = self._soa
    if bufs is None or bufs[0].shape[0] < n:
        cap = max(64, 2 * n)
        bufs = (np.empty((cap, 3), np.float32), np.empty((cap, 3), np.float32),
                np.empty((cap, 3), np.float32), np.empty(cap, np.float32),
                np.empty(cap, np.float32), np.empty(cap, np.float32))
        self._soa = bufs
    pos = bufs[0][:n]
    vel = bufs[1][:n]
    force = bufs[2][:n]
    invmass = bufs[3][:n]
    gscale = bufs[4][:n]
    lindamp = bufs[5][:n]
    bodies = []
    for i, e in enumerate(awake):
        b = e.body
        bodies.append(b)
        p = b.position
        v = b.velocity
        f = b._force
        pos[i, 0] = p.x
        pos[i, 1] = p.y
        pos[i, 2] = p.z
        vel[i, 0] = v.x
        vel[i, 1] = v.y
        vel[i, 2] = v.z
        force[i, 0] = f.x
        force[i, 1] = f.y
        force[i, 2] = f.z
        invmass[i] = b.inv_mass
        gscale[i] = b.gravity_scale
        lindamp[i] = b.linear_damping
    grav = np.array([self.gravity.x, self.gravity.y, self.gravity.z],
                    dtype=np.float32)
    _soa_integrate(pos, vel, force, invmass, gscale, grav, h, lindamp)
    for i, b in enumerate(bodies):
        b.velocity = glm.vec3(float(vel[i, 0]), float(vel[i, 1]),
                              float(vel[i, 2]))
        b.position = glm.vec3(float(pos[i, 0]), float(pos[i, 1]),
                              float(pos[i, 2]))
        b._force = glm.vec3(0.0)
        if not b.freeze_rotation:
            b.angular_velocity *= max(0.0, 1.0 - b.angular_damping * h)
            if glm.length(b.angular_velocity) > 1e-8:
                w = b.angular_velocity
                wq = glm.quat(0.0, w.x * 0.5 * h, w.y * 0.5 * h,
                              w.z * 0.5 * h)
                _finalpatch_integrate_body_orientation(b, self._entry_by_body.get(id(b)), h)
        # safety: never let a body leave the world numerically
        if not (math.isfinite(b.position.x) and math.isfinite(b.position.y)
                and math.isfinite(b.position.z)):
            b.position = glm.vec3(0.0, 5.0, 0.0)
            b.velocity = glm.vec3(0.0)


def _pw_wake_body(self, body):
    """Wake the island containing `body` (called by RigidBody.add_force /
    add_impulse / add_impulse_at_point through the body._world back-ref).
    NOTE: a DIRECT assignment to body.velocity on an already-sleeping body
    does not wake it -- game code should use add_impulse/add_force for that
    (kinematic movers are unaffected: kinematic entries never sleep)."""
    e = self._entry_by_body.get(id(body))
    if e is not None:
        self.island_mgr.wake_island_of(e, self.manifold_mgr.manifolds.values())


PhysicsWorld.wake_body = _pw_wake_body
PhysicsWorld._soa_integrate_awake = _pw_soa_integrate_awake

