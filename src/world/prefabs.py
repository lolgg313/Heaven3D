# @heaven3d-part world/prefabs.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# ---------------------------------------------------------------------------
# [PREFABS]  Reusable objects saved as .prefab files
#
#   A prefab is an object (with its children, meshes, materials, colliders,
#   rigid bodies, scripts, lights, particles ...) written to a .prefab file
#   with the same serializer projects use, asset paths relative to the
#   prefab file.  Placing it makes an *instance*: an ordinary object that
#   remembers which prefab it came from.
#
#     engine.save_prefab(node, "assets/prefabs/Crate.prefab")
#     engine.spawn_prefab("assets/prefabs/Crate.prefab", position=(0, 2, 0))
#     engine.prefab_apply(instance)    # instance -> prefab (+ other instances)
#     engine.prefab_revert(instance)   # prefab -> instance
#     engine.prefab_unlink(instance)   # keep the object, forget the prefab
#
#   An instance's own name, location, rotation and scale are always its own.
#   Anything else edited on an instance makes it "modified": it keeps those
#   edits until you Apply or Revert.  Unmodified instances follow the
#   prefab - when the file changes (Apply here, or edited in another
#   project) they are rebuilt from it, live and on project load.
#
#   The project still stores each instance in full, so a game plays the same
#   even if a .prefab file goes missing.
# ---------------------------------------------------------------------------
import hashlib as _pf_hashlib
import re as _pf_re

PREFAB_EXT = ".prefab"
_PF_OWN_KEYS = ("name", "position", "rotation_euler", "scale", "prefab")
_PF_DEPTH = [0]


def _pf_strip(data):
    """The part of a serialized node a prefab decides (the root's own
    name / transform and its prefab link are left out)."""
    return {k: v for k, v in data.items() if k not in _PF_OWN_KEYS}


_PF_PATHLIKE = _pf_re.compile(r"^.{0,4000}\.[A-Za-z0-9]{1,6}$")


def _pf_norm(v):
    """Asset paths count by file name only: the same prefab stored relative
    to the project, to the prefab file or made portable on save / export
    must compare equal."""
    if isinstance(v, dict):
        return {k: _pf_norm(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_pf_norm(x) for x in v]
    if isinstance(v, float):
        return round(v, 5)
    if isinstance(v, str) and ("/" in v or "\\" in v) and _PF_PATHLIKE.match(v):
        return v.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return v


def _pf_hash(data):
    raw = json.dumps(_pf_norm(_pf_strip(data)), sort_keys=True, separators=(",", ":"), default=str)
    return _pf_hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _pf_project_dir(engine):
    p = getattr(engine, "project_path", None)
    return os.path.dirname(os.path.abspath(p)) if p else os.getcwd()


def _pf_abs(engine, path):
    if not path:
        return None
    if os.path.isabs(path):
        return os.path.normpath(path)
    return os.path.normpath(os.path.join(_pf_project_dir(engine), *str(path).replace("\\", "/").split("/")))


def _pf_empty_node(name):
    return {"name": name, "position": [0.0, 0.0, 0.0], "rotation_euler": [0.0, 0.0, 0.0],
            "scale": [1.0, 1.0, 1.0], "visible": True, "tag": "", "children": []}


def prefab_read(path):
    """The prefab file as a dict (raises ValueError for anything else).  An
    empty prefab (or an old asset-browser placeholder) reads as an empty group."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and not isinstance(data.get("node"), dict) \
            and data.get("type") in ("prefab", "heaven3d_prefab"):
        data = dict(data)
        data["node"] = _pf_empty_node(str(data.get("name") or os.path.splitext(os.path.basename(path))[0]))
    if not isinstance(data, dict) or not isinstance(data.get("node"), dict):
        raise ValueError(f"{os.path.basename(path)} is not a Heaven3D prefab "
                         f"(make one with 'Save as Prefab' on an object)")
    return data


def _pf_is_ref(s):
    try:
        return bool(H3DPortable.is_media_ref(s))
    except Exception:
        return False


def _pf_relativize(data, folder, resolve_from=None):
    """Asset paths in a prefab's node data relative to the prefab's folder
    ('../models/crate.glb'), so the prefab works wherever the project goes
    (another PC, an exported game).  ``resolve_from``: the folder the stored
    relative paths were relative to (a moved prefab), default ``folder``."""
    base = resolve_from or folder

    def fix(v):
        if not _pf_is_ref(v):
            return v
        try:
            p = v if os.path.isabs(v) else None
            if p is None:
                cand = os.path.normpath(os.path.join(base, *str(v).replace("\\", "/").split("/")))
                p = cand if os.path.exists(cand) else _from_portable_path(v, base)
            if not p or not os.path.exists(p):
                return v
            rel = os.path.relpath(os.path.abspath(p), os.path.abspath(folder))
            return rel.replace(os.sep, "/")
        except ValueError:                     # another drive on Windows: keep it absolute
            return os.path.abspath(p) if p else v
        except Exception:
            return v

    def walk(o):
        if isinstance(o, dict):
            for k, v in list(o.items()):
                if isinstance(v, str):
                    o[k] = fix(v)
                else:
                    walk(v)
        elif isinstance(o, list):
            for i, v in enumerate(o):
                if isinstance(v, str):
                    o[i] = fix(v)
                else:
                    walk(v)
    walk(data)
    return data


def _pf_absolutize(data, folder):
    """Resolve a prefab's relative asset paths against its folder (what the
    project loader's resolver does for project files)."""
    def fix(v):
        if not _pf_is_ref(v) or os.path.isabs(v):
            return v
        cand = os.path.normpath(os.path.join(folder, *str(v).replace("\\", "/").split("/")))
        if os.path.exists(cand):
            return cand
        try:
            p = _from_portable_path(v, folder)
            return p if p and os.path.exists(p) else v
        except Exception:
            return v

    def walk(o):
        if isinstance(o, dict):
            for k, v in list(o.items()):
                if isinstance(v, str):
                    o[k] = fix(v)
                else:
                    walk(v)
        elif isinstance(o, list):
            for i, v in enumerate(o):
                if isinstance(v, str):
                    o[i] = fix(v)
                else:
                    walk(v)
    walk(data)
    return data


def prefab_create_empty(path, name=None):
    """A new, empty prefab (an empty group) - fill it by Apply-ing an edited
    instance or by dropping an object on it in Assets."""
    path = os.path.abspath(path)
    if not path.lower().endswith(PREFAB_EXT):
        path += PREFAB_EXT
    os.makedirs(os.path.dirname(path), exist_ok=True)
    nm = name or os.path.splitext(os.path.basename(path))[0]
    data = {"type": "heaven3d_prefab", "version": HEAVEN_VERSION, "name": nm, "color": [0.36, 0.66, 1.0, 1.0],
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"), "node": _pf_empty_node(nm)}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)
    return path


def prefab_relocate(old_path, new_path):
    """A .prefab moved / renamed on disk: keep its asset paths pointing at
    the same files from the new place."""
    try:
        with open(new_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict) or not isinstance(data.get("node"), dict):
        return False
    old_dir = os.path.dirname(os.path.abspath(old_path))
    new_dir = os.path.dirname(os.path.abspath(new_path))
    if os.path.normcase(old_dir) == os.path.normcase(new_dir):
        return True
    _pf_relativize(data["node"], new_dir, resolve_from=old_dir)
    tmp = new_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, new_path)
    return True


def prefab_link(node):
    """{'path': absolute .prefab path, 'src': hash, 'base': hash} or None."""
    return getattr(node, "prefab", None) or None


def prefab_root(node):
    """The prefab instance this node belongs to (itself, a parent, or None)."""
    n = node
    while n is not None:
        if prefab_link(n):
            return n
        n = n.parent
    return None


def prefab_is_modified(node, project_dir=None):
    link = prefab_link(node)
    if not link:
        return False
    try:
        cur = _pf_hash(ProjectSerializer._serialize_node(node, project_dir or os.getcwd()))
    except Exception:
        return False
    return cur != link.get("base")


def _pf_mark_synced(node, project_dir, src_hash):
    node.prefab["src"] = src_hash
    node.prefab["base"] = _pf_hash(ProjectSerializer._serialize_node(node, project_dir))


def _pf_build(engine, path, parent, own=None, index=None):
    """Make a fresh instance of the prefab at ``path`` under ``parent``.
    ``own`` carries the instance's own name / transform."""
    data = prefab_read(path)
    nd = json.loads(json.dumps(data["node"]))
    _pf_absolutize(nd, os.path.dirname(os.path.abspath(path)))
    own = own or {}
    nd["name"] = own.get("name") or data.get("name") or nd.get("name") or "Prefab"
    for k in ("position", "rotation_euler", "scale"):
        if own.get(k) is not None:
            nd[k] = [float(x) for x in own[k]]
    nd.pop("prefab", None)
    before = list(parent.children)
    _PF_DEPTH[0] += 1
    try:
        if _PF_DEPTH[0] > 8:
            raise ValueError("prefabs nest more than 8 deep (does a prefab contain itself?)")
        ProjectSerializer._deserialize_node(nd, parent, engine, os.path.dirname(os.path.abspath(path)))
    finally:
        _PF_DEPTH[0] -= 1
    new = [c for c in parent.children if c not in before]
    if not new:
        raise ValueError(f"prefab {os.path.basename(path)} produced no object")
    node = new[-1]
    if index is not None and 0 <= index < len(parent.children) - 1:
        parent.children.remove(node)
        parent.children.insert(index, node)
    node.prefab = {"path": os.path.normpath(os.path.abspath(path)), "src": _pf_hash(data["node"]), "base": None}
    _pf_mark_synced(node, _pf_project_dir(engine), node.prefab["src"])
    return node


def _pf_own_of(node):
    return {"name": node.name, "position": list(node.position),
            "rotation_euler": list(node.rotation_euler), "scale": list(node.scale)}


def _pf_replace(engine, node, path):
    """Rebuild ``node`` from its prefab in place; returns the new node."""
    parent = node.parent or engine.scene.root
    index = parent.children.index(node) if node in parent.children else None
    own = _pf_own_of(node)
    was_selected = getattr(engine, "selected", None) is node
    playing = bool(getattr(engine, "_playing", False))
    new = _pf_build(engine, path, parent, own, index)
    try:
        engine._delete_node(node)
    except Exception:
        node.remove()
    if playing:
        try:
            engine.physics.add_node_recursive(new)
        except Exception:
            pass
    if was_selected:
        try:
            engine.select_node(new)
        except Exception:
            engine.selected = new
    return new


# ---- serializer: instances remember their prefab --------------------------
_PF_PREV_SER = ProjectSerializer._serialize_node.__func__
_PF_PREV_DESER = ProjectSerializer._deserialize_node.__func__


def _pf_serialize_node(cls, node, project_dir):
    d = _PF_PREV_SER(cls, node, project_dir)
    link = prefab_link(node)
    if link:
        try:
            rel = _to_portable_path(link["path"], project_dir)
        except Exception:
            rel = link["path"]
        d["prefab"] = {"path": rel, "src": link.get("src"), "base": link.get("base")}
    return d


def _pf_deserialize_node(cls, data, parent, engine, project_dir):
    before = len(parent.children)
    _PF_PREV_DESER(cls, data, parent, engine, project_dir)
    info = data.get("prefab") if isinstance(data, dict) else None
    if not info or len(parent.children) <= before:
        return
    node = parent.children[-1]
    raw = info.get("path")
    try:
        path = _from_portable_path(raw, project_dir)
    except Exception:
        path = raw
    if raw and (not path or not os.path.isfile(path)) and not os.path.isabs(str(raw)):
        # a missing file: keep it project-relative so it relinks when restored
        path = os.path.join(project_dir, *str(raw).replace("\\", "/").split("/"))
    node.prefab = {"path": os.path.normpath(path) if path else path,
                   "src": info.get("src"), "base": info.get("base")}
    unmodified = _pf_hash(data) == info.get("base")
    if not unmodified or not path or not os.path.isfile(path):
        return                                   # keep the instance as saved
    try:
        pdata = prefab_read(path)
        src = _pf_hash(pdata["node"])
        if src != info.get("src"):
            # the prefab changed since this instance was last in sync
            Log.info(f"[Prefab] '{node.name}' updated from {os.path.basename(path)}")
            _pf_replace(engine, node, path)
        else:
            # same content: re-baseline against this engine's own serialization
            _pf_mark_synced(node, project_dir, src)
    except Exception as ex:
        Log.warn(f"[Prefab] {os.path.basename(str(path))}: {ex} (kept the saved copy)")


ProjectSerializer._serialize_node = classmethod(_pf_serialize_node)
ProjectSerializer._deserialize_node = classmethod(_pf_deserialize_node)


# ---- engine API --------------------------------------------------------------
def _pf_save_prefab(self, node, path, link=True):
    """Write ``node`` (and its children) to a .prefab file; the object
    becomes an instance of it unless link=False.  Returns the path."""
    if node is None or node is self.scene.root:
        raise ValueError("select an object first")
    if node.is_sun or node.is_clouds or node.is_ocean:
        raise ValueError(f"the {node.name} can't be a prefab")
    if any(getattr(n, "spawn_kind", None) == "player" for n in node.traverse()):
        raise ValueError("the Player Spawn can't be in a prefab (only one per scene)")
    if any(n.is_sun or n.is_clouds or n.is_ocean for n in node.traverse()):
        raise ValueError("the Sun, Clouds and Ocean can't be in a prefab")
    path = _pf_abs(self, path)
    if not path.lower().endswith(PREFAB_EXT):
        path += PREFAB_EXT
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    nd = ProjectSerializer._serialize_node(node, folder)
    nd.pop("prefab", None)
    _pf_relativize(nd, folder)                # prefab-relative asset paths (portable)
    nd["position"] = [0.0, 0.0, 0.0]          # the instance places it
    nd["rotation_euler"] = [0.0, 0.0, 0.0]
    nd["scale"] = [1.0, 1.0, 1.0]
    name = os.path.splitext(os.path.basename(path))[0]
    nd["name"] = name
    color = [0.36, 0.66, 1.0, 1.0]
    try:
        if node.material is not None:
            color = [float(x) for x in list(node.material.base_color)[:3]] + [1.0]
    except Exception:
        pass
    data = {"type": "heaven3d_prefab", "version": HEAVEN_VERSION, "name": name, "color": color,
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"), "node": nd}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)
    if link:
        node.prefab = {"path": os.path.normpath(path), "src": _pf_hash(nd), "base": None}
        _pf_mark_synced(node, _pf_project_dir(self), node.prefab["src"])
    Log.info(f"[Prefab] saved '{node.name}' -> {path}")
    return path


def _pf_spawn_prefab(self, path, position=None, rotation=None, scale=None, parent=None, name=None):
    """Place an instance of a .prefab (path absolute or project-relative).
    Works in the editor and during Play (from scripts / blueprints)."""
    path = _pf_abs(self, path)
    parent = parent or self.scene.root
    data = prefab_read(path)
    base = name or data.get("name") or os.path.splitext(os.path.basename(path))[0]
    taken = {n.name for n in self.scene.root.traverse()}
    uname, i = base, 1
    while uname in taken:
        uname = f"{base}{i}"
        i += 1
    own = {"name": uname, "position": None, "rotation_euler": None, "scale": None}
    if position is not None:
        own["position"] = list(_v3(position))
    if rotation is not None:
        own["rotation_euler"] = list(_v3(rotation))
    if scale is not None:
        own["scale"] = list(_v3(scale)) if not isinstance(scale, (int, float)) else [float(scale)] * 3
    node = _pf_build(self, path, parent, own)
    if getattr(self, "_playing", False):
        try:
            self.physics.add_node_recursive(node)
        except Exception:
            pass
    return node


def _pf_instances(self, path=None):
    """Every prefab instance in the scene (of one prefab when path is given)."""
    want = os.path.normcase(_pf_abs(self, path)) if path else None
    out = []
    for n in self.scene.root.traverse():
        link = prefab_link(n)
        if link and (want is None or os.path.normcase(str(link.get("path"))) == want):
            out.append(n)
    return out


def _pf_apply(self, node):
    """Write an instance back into its prefab, then refresh every other
    unmodified instance of it.  Returns the number of instances updated."""
    root = prefab_root(node)
    if root is None:
        raise ValueError("not a prefab instance")
    path = root.prefab["path"]
    pd = _pf_project_dir(self)
    others = [n for n in self.prefab_instances(path) if n is not root and not prefab_is_modified(n, pd)]
    keep = dict(root.prefab)
    _pf_save_prefab(self, root, path, link=False)
    root.prefab = keep
    _pf_mark_synced(root, pd, _pf_hash(prefab_read(path)["node"]))
    done = 0
    for n in others:
        try:
            _pf_replace(self, n, path)
            done += 1
        except Exception as ex:
            Log.warn(f"[Prefab] could not refresh '{n.name}': {ex}")
    Log.info(f"[Prefab] applied '{root.name}' to {os.path.basename(path)}"
             + (f" and updated {done} other instance(s)" if done else ""))
    return done


def _pf_revert(self, node):
    """Throw away an instance's edits (its name and transform stay)."""
    root = prefab_root(node)
    if root is None:
        raise ValueError("not a prefab instance")
    path = root.prefab["path"]
    if not os.path.isfile(path):
        raise FileNotFoundError(f"prefab not found: {path}")
    return _pf_replace(self, root, path)


def _pf_unlink(self, node):
    root = prefab_root(node)
    if root is not None:
        root.prefab = None
    return root


def _pf_replace_with(self, node, path):
    """Make the prefab at ``path`` hold ``node`` (Unity: drop an object on a
    prefab asset).  ``node`` becomes an instance; other unedited instances
    in the scene update, edited ones keep their edits.  -> instances updated"""
    path = os.path.normpath(_pf_abs(self, path))
    root = prefab_root(node)
    if root is node and os.path.normcase(str(root.prefab.get("path"))) == os.path.normcase(path):
        return self.prefab_apply(node)
    for n in node.traverse():
        link = prefab_link(n)
        if link and os.path.normcase(str(link.get("path"))) == os.path.normcase(path):
            raise ValueError(f"'{n.name}' is already an instance of {os.path.basename(path)} - "
                             f"a prefab can't hold itself")
    pd = _pf_project_dir(self)
    others = [n for n in self.prefab_instances(path) if not prefab_is_modified(n, pd)]
    _pf_save_prefab(self, node, path, link=True)
    done = 0
    for n in others:
        try:
            _pf_replace(self, n, path)
            done += 1
        except Exception as ex:
            Log.warn(f"[Prefab] could not refresh '{n.name}': {ex}")
    Log.info(f"[Prefab] {os.path.basename(path)} now holds '{node.name}'"
             + (f" ({done} other instance(s) updated)" if done else ""))
    return done


def _pf_retarget_file(fpath, resolve_dir, mapping):
    """Rewrite the asset paths inside a .prefab / .vmat file at ``fpath``:
    each stored path is resolved against ``resolve_dir`` (where the file used
    to be), passed through ``mapping`` (old absolute -> new absolute or None)
    and stored relative to the file's folder again.  -> changed?"""
    try:
        with open(fpath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False
    here = os.path.dirname(os.path.abspath(fpath))
    changed = [False]

    def fix(v):
        if not _pf_is_ref(v):
            return v
        if os.path.isabs(v):
            absv = os.path.normpath(v)
        else:
            absv = os.path.normpath(os.path.join(resolve_dir, *str(v).replace("\\", "/").split("/")))
            if not os.path.exists(absv) and mapping(absv) is None:
                try:
                    alt = _from_portable_path(v, resolve_dir)
                    if alt and os.path.exists(alt):
                        absv = os.path.normpath(alt)
                except Exception:
                    pass
        tgt = mapping(absv) or absv
        try:
            out = os.path.relpath(tgt, here).replace(os.sep, "/") if not os.path.isabs(v) or mapping(absv) else v
        except ValueError:
            out = tgt
        if out != v:
            changed[0] = True
        return out

    def walk(o):
        if isinstance(o, dict):
            for k, v in list(o.items()):
                if isinstance(v, str):
                    o[k] = fix(v)
                else:
                    walk(v)
        elif isinstance(o, list):
            for i, v in enumerate(o):
                if isinstance(v, str):
                    o[i] = fix(v)
                else:
                    walk(v)
    walk(data)
    if changed[0]:
        tmp = fpath + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, fpath)
    return changed[0]


def _pf_assets_moved(self, old, new):
    """A file or folder was renamed / moved inside the project (Assets panel):
    objects keep their prefab, material and model links, moved .prefab /
    .vmat files keep pointing at the same assets, and other prefabs /
    materials that used a moved asset follow it.  -> links updated"""
    old = os.path.normpath(os.path.abspath(old))
    new = os.path.normpath(os.path.abspath(new))
    oc = os.path.normcase(old)

    def mapping(p):
        pc = os.path.normcase(os.path.normpath(p))
        if pc == oc:
            return new
        if pc.startswith(oc + os.sep):
            return os.path.join(new, os.path.normpath(p)[len(old) + 1:])
        return None
    n_links = 0
    refresh = set()
    pd = _pf_project_dir(self)
    # instances in sync with their prefab stay in sync after the move
    in_sync = [n for n in self.scene.root.traverse()
               if prefab_link(n) and not prefab_is_modified(n, pd)]
    for n in self.scene.root.traverse():
        link = prefab_link(n)
        if link and mapping(str(link.get("path"))):
            link["path"] = mapping(str(link["path"]))
            n_links += 1
        ma = getattr(n, "material_asset", None)
        if ma and mapping(ma):
            n.material_asset = mapping(ma)
            refresh.add(n.material_asset)
            n_links += 1
        sp = getattr(n, "source_path", None)
        if sp and mapping(sp):
            n.source_path = mapping(sp)
            n_links += 1
    # the moved files themselves
    moved = []
    if os.path.isdir(new):
        for dp, _dn, fn in os.walk(new):
            for f in fn:
                if f.lower().endswith((PREFAB_EXT, ".vmat")):
                    nf = os.path.join(dp, f)
                    moved.append((os.path.join(old, os.path.relpath(nf, new)), nf))
    elif new.lower().endswith((PREFAB_EXT, ".vmat")):
        moved.append((old, new))
    for of, nf in moved:
        _pf_retarget_file(nf, os.path.dirname(of), mapping)
        if nf.lower().endswith(".vmat"):                 # its bake lives next to it
            stem_o = os.path.splitext(os.path.basename(of))[0]
            bdir = os.path.join(os.path.dirname(of), ".baked")
            for f in (os.listdir(bdir) if os.path.isdir(bdir) else ()):
                if f.startswith(stem_o + "_") or f == stem_o + ".json":
                    try:
                        os.remove(os.path.join(bdir, f))
                    except OSError:
                        pass
    # every other prefab / material of the project that used a moved asset
    skip = {os.path.normcase(nf) for _of, nf in moved}
    for dp, dn, fn in os.walk(pd):
        dn[:] = [d for d in dn if not d.startswith(".") and d not in ("__pycache__", "exports", "saves")]
        for f in fn:
            if f.lower().endswith((PREFAB_EXT, ".vmat")):
                fp = os.path.join(dp, f)
                if os.path.normcase(fp) not in skip:
                    _pf_retarget_file(fp, dp, mapping)
    for vm in refresh:
        try:
            self.refresh_material_asset(vm)
        except Exception as ex:
            Log.warn(f"[Assets] {os.path.basename(vm)}: {ex}")
    for n in in_sync:
        try:
            _pf_mark_synced(n, pd, _pf_hash(prefab_read(prefab_link(n)["path"])["node"]))
        except Exception:
            pass
    return n_links


HeavenEngine.prefab_replace_with = _pf_replace_with
HeavenEngine.assets_moved = _pf_assets_moved
HeavenEngine.save_prefab = _pf_save_prefab
HeavenEngine.spawn_prefab = _pf_spawn_prefab
HeavenEngine.prefab_instances = _pf_instances
HeavenEngine.prefab_apply = _pf_apply
HeavenEngine.prefab_revert = _pf_revert
HeavenEngine.prefab_unlink = _pf_unlink
HeavenEngine.prefab_is_modified = lambda self, node: prefab_is_modified(prefab_root(node) or node,
                                                                       _pf_project_dir(self))
