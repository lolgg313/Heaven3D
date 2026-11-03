"""Builds the compact asset library of the MakeHuman Characters package.

You only need this to regenerate packages/mh_characters/assets/ (it is already
built and shipped).  Input: the MPFB 2 source tree (base mesh, targets, rigs)
and MakeHuman asset packs (clothes, hair, skins ...).  Only CC0 assets are
copied; anything that is not CC0 or that is named after a trademarked
character is skipped, so everything in assets/ can be shipped in commercial,
closed-source games with no attribution or licence obligations.

    python tools/build_assets.py --mpfb <mpfb2>/src/mpfb/data \
        --packs <makehuman_system_assets> <pack1> <pack2> ... --out assets

Requires numpy and Pillow.
"""
import argparse
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import sys

import numpy as np
from PIL import Image

# Names that refer to trademarked characters / franchises, or that are not
# suitable defaults for a general game kit.  Matched against asset, variant
# and folder names (lower case).
EXCLUDE = re.compile(
    r"captain_america|captain_marvel|hawkeye|nightwing|vision|x-men|nightcrawler|deadpool|spider|"
    r"superman|batman|the_spot|mockingbird|ladybug|rei_ayanami|yoko_tsuno|doc_ock|dr_manhattan|tigra|"
    r"white_walker|junglebook|maga|trans_flag|slave|bag_on_head|annotated|genitals|teddy|erotic|"
    r"orkify|baboon|thong|_comic")

TEX_LIMIT = {"skin": 1024, "full": 768, "top": 768, "bottom": 768, "hair": 512, "eyebrows": 256,
             "eyelashes": 256, "eyes": 256, "default": 512}


def log(*a):
    print(*a, flush=True)


# --------------------------------------------------------------------------- parsing
def parse_obj(path):
    vs, vts, faces, groups = [], [], [], []
    group = ""
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("v "):
                p = line.split()
                vs.append((float(p[1]), float(p[2]), float(p[3])))
            elif line.startswith("vt "):
                p = line.split()
                vts.append((float(p[1]), float(p[2])))
            elif line.startswith("f "):
                corners = []
                for tok in line.split()[1:]:
                    s = tok.split("/")
                    vi = int(s[0]) - 1
                    ti = int(s[1]) - 1 if len(s) > 1 and s[1] else -1
                    corners.append((vi, ti))
                faces.append(corners)
                groups.append(group)
            elif line.startswith("g "):
                group = line[2:].strip()
    return np.array(vs, np.float64), np.array(vts or [(0.0, 0.0)], np.float64), faces, groups


def render_mesh(faces, vts, keep=None):
    """Unique (vertex, uv) corners -> render vertices; triangulated fan."""
    key_to_r, src, uv, tris = {}, [], [], []
    for fi, corners in enumerate(faces):
        if keep is not None and not keep[fi]:
            continue
        idx = []
        for vi, ti in corners:
            k = (vi, ti)
            r = key_to_r.get(k)
            if r is None:
                r = key_to_r[k] = len(src)
                src.append(vi)
                uv.append(vts[ti] if ti >= 0 else (0.0, 0.0))
            idx.append(r)
        for j in range(1, len(idx) - 1):
            tris.append((idx[0], idx[j], idx[j + 1]))
    return (np.array(src, np.int32), np.array(uv, np.float32),
            np.array(tris, np.uint32).reshape(-1, 3))


def parse_ranges(tokens):
    out, i = [], 0
    while i < len(tokens):
        a = int(tokens[i])
        if i + 2 < len(tokens) and tokens[i + 1] == "-":
            out.extend(range(a, int(tokens[i + 2]) + 1))
            i += 3
        else:
            out.append(a)
            i += 1
    return out


def parse_mhclo(path):
    d = {"tags": [], "scale": {}, "ref": [], "w": [], "off": [], "delete": [], "z_depth": 50}
    in_verts = in_delete = False
    with open(path, encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.strip().lstrip("\ufeff")
            if not line or line.startswith("#"):
                continue
            p = line.split()
            if re.match(r"^-?\d", p[0]):
                if in_delete:
                    d["delete"].extend(parse_ranges(p))
                elif in_verts and len(p) >= 9:
                    d["ref"].append((int(p[0]), int(p[1]), int(p[2])))
                    d["w"].append((float(p[3]), float(p[4]), float(p[5])))
                    d["off"].append((float(p[6]), float(p[7]), float(p[8])))
                elif in_verts and len(p) == 1:
                    v = int(p[0])
                    d["ref"].append((v, v, v))
                    d["w"].append((1.0, 0.0, 0.0))
                    d["off"].append((0.0, 0.0, 0.0))
                continue
            key = p[0]
            if key == "verts":
                in_verts, in_delete = True, False
            elif key == "delete_verts":
                in_delete = True
                if len(p) > 1:
                    d["delete"].extend(parse_ranges(p[1:]))
            elif key in ("x_scale", "y_scale", "z_scale") and len(p) >= 4:
                d["scale"][key[0]] = (int(p[1]), int(p[2]), float(p[3]))
            elif key == "tag":
                d["tags"].append(" ".join(p[1:]).lower())
            elif key in ("name", "obj_file", "material", "basemesh", "uuid"):
                d[key] = " ".join(p[1:])
            elif key == "z_depth":
                d["z_depth"] = int(float(p[1]))
    return d


def parse_mhmat(path):
    d = {"dir": os.path.dirname(path)}
    with open(path, encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.strip().lstrip("﻿")
            if not line or line.startswith("#") or line.startswith("//"):
                continue
            p = line.split(None, 1)
            if len(p) == 2:
                d[p[0]] = p[1].strip()
    return d


def mat_file(d, key):
    v = d.get(key)
    if not v:
        return None
    for cand in (os.path.join(d["dir"], v), os.path.join(d["dir"], os.path.basename(v))):
        if os.path.isfile(cand):
            return cand
    return None


# --------------------------------------------------------------------------- textures
class TexStore:
    def __init__(self, out):
        self.dir = os.path.join(out, "tex")
        os.makedirs(self.dir, exist_ok=True)
        self.done = {}
        self.bytes = 0

    def add(self, path, limit, normal=False, force_alpha=None):
        if not path:
            return None
        key = (os.path.abspath(path), limit, normal)
        if key in self.done:
            return self.done[key]
        try:
            im = Image.open(path)
            im.load()
        except Exception as ex:
            log("   ! texture skipped", path, ex)
            self.done[key] = None
            return None
        alpha = False
        if not normal and im.mode in ("RGBA", "LA", "P"):
            a = im.convert("RGBA").getchannel("A")
            alpha = a.getextrema()[0] < 245
        if force_alpha is not None:
            alpha = force_alpha and im.mode in ("RGBA", "LA", "P")
        im = im.convert("RGBA" if alpha else "RGB")
        w, h = im.size
        s = min(1.0, float(limit) / max(w, h))
        if s < 1.0:
            im = im.resize((max(4, int(w * s)), max(4, int(h * s))), Image.LANCZOS)
        buf = io.BytesIO()
        if alpha:
            # 256-colour palette with alpha: a third of the size, no visible loss on cut-out textures
            im.quantize(colors=256, method=Image.Quantize.FASTOCTREE).save(buf, "PNG", optimize=True)
            ext = ".png"
        else:
            im.save(buf, "JPEG", quality=84 if not normal else 90, optimize=True)
            ext = ".jpg"
        data = buf.getvalue()
        name = hashlib.sha1(data).hexdigest()[:16] + ext
        p = os.path.join(self.dir, name)
        if not os.path.exists(p):
            with open(p, "wb") as f:
                f.write(data)
            self.bytes += len(data)
        rel = "tex/" + name
        self.done[key] = (rel, alpha)
        return self.done[key]


# --------------------------------------------------------------------------- classification
SLOT_RULES = [
    ("feet", r"shoe|boot|flats|booties|t-bar|sandal|heels|stiletto"),
    ("hands", r"glove"),
    ("eyewear", r"glasses|goggle"),
    ("head", r"hat|cap\b|_cap|helmet|fedora|casco|hood(?!_down)"),
    ("face", r"mask|mouth_protection"),
    ("jewelry", r"earring|ring_|_ring|necklace|choker|piercing|gem|septum"),
    ("legs", r"sock|stocking|pantyhose|leg_warmer"),
    ("arms", r"armsleeve"),
    ("underwear", r"panties|female_top_01|bra\b|briefs"),
    ("full", r"suit|dress|robe|uniform|bodysuit|skinsuit|armor|tunic|kimono|cyborg"),
    ("bottom", r"pants|jeans|skirt|shorts"),
    ("top", r"shirt|top|sweater|camisole|bodice|tank|polo"),
]


def slot_of(name):
    n = name.lower()
    if "viking_boots" in n:
        return "feet"
    if "viking_pants" in n:
        return "bottom"
    if "viking_tunic" in n:
        return "top"
    for slot, rx in SLOT_RULES:
        if re.search(rx, n):
            return slot
    return "accessory"


def gender_of(name, tags):
    n = name.lower()
    t = " ".join(tags)
    fem = bool(re.search(r"female|heroine|woman|dress|skirt|bodice|camisole|halter|ballet|stiletto|"
                         r"bikini|lace|pearl|gogo|flapper|cloche|kimono", n)) or "female" in tags
    mal = bool(re.search(r"(?<!fe)male\b|(?<!fe)male_|\bhero_|_hero_|\bm_suit|man\b", n)) or \
        ("male" in tags and "female" not in tags)
    if fem and not mal:
        return "female"
    if mal and not fem:
        return "male"
    return "any"


# --------------------------------------------------------------------------- main build
def find_assets(roots):
    """Yield (kind, folder, mhclo/proxy path, pack license map)."""
    items = []
    for root in roots:
        lic = {}
        for pj in _glob(os.path.join(root, "packs"), ".json"):
            try:
                for k, v in json.load(open(pj, encoding="utf-8")).items():
                    lic[k] = v.get("license", "")
            except Exception:
                pass
        for kind in ("clothes", "hair", "eyebrows", "eyelashes", "eyes", "proxymeshes"):
            base = os.path.join(root, kind)
            if not os.path.isdir(base):
                continue
            for name in sorted(os.listdir(base)):
                d = os.path.join(base, name)
                if not os.path.isdir(d):
                    continue
                files = [f for f in os.listdir(d) if f.endswith(".mhclo") or f.endswith(".proxy")]
                for f in files:
                    items.append((kind, name, os.path.join(d, f), lic.get(name, lic.get(os.path.splitext(f)[0], "CC0"))))
    return items


def _glob(d, ext):
    if not os.path.isdir(d):
        return []
    return [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.endswith(ext)]


def build(mpfb, roots, out):
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(os.path.join(out, "parts"))
    tex = TexStore(out)
    cat = {"version": 1, "license": "CC0 1.0 Universal (public domain dedication)", "skeleton": "mixamo",
           "parts": {}, "skins": {}, "eye_materials": {}, "modifiers": [], "credits": []}

    # ---- base mesh -----------------------------------------------------
    log("base mesh")
    v, vt, faces, groups = parse_obj(os.path.join(mpfb, "3dobjs", "base.obj"))
    keep = [g == "body" for g in groups]
    src, uv, tris = render_mesh(faces, vt, keep)
    joints = {}
    for fi, g in enumerate(groups):
        if g.startswith("joint-"):
            joints.setdefault(g, set()).update(c[0] for c in faces[fi])
    jnames = sorted(joints)
    jidx = [np.array(sorted(joints[n]), np.int32) for n in jnames]
    joff = np.cumsum([0] + [len(a) for a in jidx]).astype(np.int32)

    # ---- rig + weights ----------------------------------------------
    rig = json.load(open(os.path.join(mpfb, "rigs", "standard", "rig.mixamo.json")))["bones"]
    order, seen = [], set()

    def visit(b):
        if b in seen:
            return
        p = rig[b]["parent"]
        if p:
            visit(p)
        seen.add(b)
        order.append(b)
    for b in sorted(rig):
        visit(b)
    bones = []
    for b in order:
        info = rig[b]

        def end(e):
            if e["strategy"] == "CUBE":
                return {"cube": e["cube_name"]}
            if e["strategy"] == "MEAN":
                return {"verts": e["vertex_indices"]}
            if e["strategy"] == "VERTEX":
                return {"verts": [e["vertex_index"]]}
            return {"pos": e["default_position"]}
        bones.append({"name": b, "parent": order.index(info["parent"]) if info["parent"] else -1,
                      "head": end(info["head"]), "tail": end(info["tail"]), "roll": float(info["roll"])})
    wj = json.load(open(os.path.join(mpfb, "rigs", "standard", "weights.mixamo.json")))["weights"]
    dense = np.zeros((len(v), len(order)), np.float32)
    for bi, b in enumerate(order):
        for vi, w in wj.get(b, []):
            dense[int(vi), bi] = float(w)
    top = np.argsort(-dense, axis=1)[:, :4]
    topw = np.take_along_axis(dense, top, axis=1)
    s = topw.sum(axis=1, keepdims=True)
    s[s <= 0] = 1.0
    topw /= s
    np.savez_compressed(os.path.join(out, "core.npz"), base_v=v.astype(np.float32), body_src=src, body_uv=uv,
                        body_tris=tris, joint_idx=np.concatenate(jidx), joint_off=joff,
                        w_idx=top.astype(np.uint8), w_val=topw.astype(np.float16))
    cat["joints"] = jnames
    cat["bones"] = bones
    log(f"  {len(v)} verts, body {len(src)} render verts / {len(tris)} tris, {len(order)} bones")

    # ---- targets ------------------------------------------------------
    log("targets")
    tdir = os.path.join(mpfb, "targets")
    arrays = {}
    names = []

    def add_target(rel):
        p = os.path.join(tdir, rel + ".target.gz")
        if not os.path.isfile(p) or rel in arrays:
            return os.path.isfile(p)
        ii, dd = [], []
        with gzip.open(p, "rt", encoding="utf-8", errors="replace") as f:
            for line in f:
                q = line.split()
                if len(q) == 4 and q[0].isdigit():
                    ii.append(int(q[0]))
                    dd.append((float(q[1]), float(q[2]), float(q[3])))
        arrays[rel + ".i"] = np.array(ii, np.uint16)
        arrays[rel + ".d"] = np.array(dd, np.float16)
        names.append(rel)
        return True
    for root, _d, files in os.walk(os.path.join(tdir, "macrodetails")):
        for f in files:
            if f.endswith(".target.gz") and "baby" not in f and "child" not in f:
                add_target(os.path.relpath(os.path.join(root, f), tdir)[:-10].replace(os.sep, "/"))
    for f in os.listdir(os.path.join(tdir, "breast")):
        if f.endswith(".target.gz") and f.startswith("female-") and "baby" not in f and "child" not in f:
            add_target("breast/" + f[:-10])
    tj = json.load(open(os.path.join(tdir, "target.json")))
    skip_groups = {"expression", "genitals", "asym", "measure", "breast"}
    for group, gd in tj.items():
        if group in skip_groups:
            continue
        for c in gd.get("categories", []):
            o = c.get("opposites", {})
            if c.get("has_left_and_right"):
                pairs = [("neg", [o.get("negative-left"), o.get("negative-right")]),
                         ("pos", [o.get("positive-left"), o.get("positive-right")])]
            else:
                pairs = [("neg", [o.get("negative-unsided")]), ("pos", [o.get("positive-unsided")])]
            mod = {"group": group, "name": c["name"], "label": c.get("label", c["name"]).replace("-", " ")}
            ok = False
            for sign, tl in pairs:
                got = []
                for t in tl:
                    if t and add_target(group + "/" + t):
                        got.append(group + "/" + t)
                mod[sign] = got
                ok = ok or bool(got)
            if ok:
                cat["modifiers"].append(mod)
    np.savez_compressed(os.path.join(out, "targets.npz"), **arrays)
    cat["targets"] = sorted(names)
    log(f"  {len(names)} targets, {len(cat['modifiers'])} modifiers")

    # ---- skins --------------------------------------------------------
    log("skins")
    for root in roots:
        sd = os.path.join(root, "skins")
        if not os.path.isdir(sd):
            continue
        lic = {}
        for pj in _glob(os.path.join(root, "packs"), ".json"):
            for k, vv in json.load(open(pj, encoding="utf-8")).items():
                lic[k] = vv.get("license", "")
        for name in sorted(os.listdir(sd)):
            d = os.path.join(sd, name)
            if not os.path.isdir(d) or EXCLUDE.search(name.lower()) or lic.get(name, "CC0") != "CC0":
                continue
            mats = _glob(d, ".mhmat")
            if not mats:
                continue
            m = parse_mhmat(mats[0])
            t = tex.add(mat_file(m, "diffuseTexture"), TEX_LIMIT["skin"], force_alpha=False)
            if not t:
                continue
            nt = tex.add(mat_file(m, "normalmapTexture"), 512, normal=True)
            tags = [w.lower() for w in re.findall(r"[a-z]+", name.lower())]
            cat["skins"][name] = {"diffuse": t[0], "normal": nt[0] if nt else None,
                                  "gender": "female" if "female" in tags else ("male" if "male" in tags else "any"),
                                  "tags": tags}
    log(f"  {len(cat['skins'])} skins")

    # ---- eye materials ----------------------------------------------
    for root in roots:
        for md in (os.path.join(root, "eyes", "materials"),):
            for p in _glob(md, ".mhmat"):
                m = parse_mhmat(p)
                t = tex.add(mat_file(m, "diffuseTexture"), TEX_LIMIT["eyes"], force_alpha=False)
                if t:
                    cat["eye_materials"][os.path.splitext(os.path.basename(p))[0]] = {"diffuse": t[0]}
    log(f"  {len(cat['eye_materials'])} eye materials")

    # ---- variant materials (clothes_materials / hair_materials packs) ---
    variants = {}
    for root in roots:
        for kind in ("clothes", "hair"):
            kd = os.path.join(root, kind)
            if not os.path.isdir(kd):
                continue
            for asset in os.listdir(kd):
                ad = os.path.join(kd, asset)
                if not os.path.isdir(ad):
                    continue
                for sub in sorted(os.listdir(ad)):
                    sdir = os.path.join(ad, sub)
                    if os.path.isdir(sdir):
                        for p in _glob(sdir, ".mhmat"):
                            variants.setdefault(asset, []).append(p)

    # ---- geometry parts (proxies, clothes, hair, brows, lashes, eyes) ---
    log("parts")
    count, skipped = 0, []
    for kind, name, path, lic in find_assets(roots):
        if lic != "CC0" or EXCLUDE.search(name.lower()):
            skipped.append(f"{name} ({lic or 'licence?'})")
            continue
        d = parse_mhclo(path)
        if d.get("basemesh", "hm08") != "hm08" or not d["ref"]:
            skipped.append(f"{name} (not hm08)")
            continue
        objp = os.path.join(os.path.dirname(path), d.get("obj_file", ""))
        if not os.path.isfile(objp):
            skipped.append(f"{name} (no obj)")
            continue
        pv, pvt, pf, _g = parse_obj(objp)
        if len(pv) != len(d["ref"]):
            skipped.append(f"{name} (vertex count mismatch {len(pv)} vs {len(d['ref'])})")
            continue
        rsrc, ruv, rtris = render_mesh(pf, pvt)
        cat_kind = {"proxymeshes": "proxy"}.get(kind, kind)
        slot = {"hair": "hair", "eyebrows": "eyebrows", "eyelashes": "eyelashes", "eyes": "eyes",
                "proxy": "body"}.get(cat_kind) or slot_of(name)
        pid = f"{cat_kind}/{name}"
        sc = np.ones((3, 3), np.float32)
        has_scale = np.zeros(3, np.uint8)
        for ai, ax in enumerate("xyz"):
            if ax in d["scale"]:
                a, b, den = d["scale"][ax]
                sc[ai] = (a, b, den)
                has_scale[ai] = 1
        np.savez_compressed(os.path.join(out, "parts", name + ".npz"),
                            ref=np.array(d["ref"], np.uint16), w=np.array(d["w"], np.float16),
                            off=np.array(d["off"], np.float16), scale=sc, has_scale=has_scale,
                            src=rsrc.astype(np.uint16 if len(pv) < 65536 else np.int32), uv=ruv.astype(np.float16),
                            tris=rtris.astype(np.uint16 if len(rsrc) < 65536 else np.uint32),
                            delete=np.array(sorted(set(d["delete"])), np.uint16))
        # materials: own material first, then variants
        limit = TEX_LIMIT.get(slot, TEX_LIMIT["default"])
        mats = []
        own = []
        if d.get("material"):
            mp = os.path.normpath(os.path.join(os.path.dirname(path), d["material"]))
            if os.path.isfile(mp):
                own = [mp]
        for mp in own + variants.get(name, []):
            vname = os.path.splitext(os.path.basename(mp))[0]
            if EXCLUDE.search(vname.lower()) or EXCLUDE.search(os.path.basename(os.path.dirname(mp)).lower()):
                continue
            m = parse_mhmat(mp)
            alpha_hint = None
            if slot in ("eyes", "eyebrows", "eyelashes", "hair"):
                alpha_hint = slot != "eyes"
            t = tex.add(mat_file(m, "diffuseTexture"), limit, force_alpha=alpha_hint)
            nt = tex.add(mat_file(m, "normalmapTexture"), max(128, min(384, limit // 2)), normal=True) \
                if slot not in ("eyebrows", "eyelashes", "eyes") else None
            col = [float(x) for x in (m.get("diffuseColor", "1 1 1").split() + ["1", "1", "1"])[:3]]
            mats.append({"name": vname, "diffuse": t[0] if t else None, "alpha": bool(t and t[1]),
                         "normal": nt[0] if nt else None, "color": col,
                         "double_sided": m.get("backfaceCull", "True").lower() == "false"})
        seen_m = set()
        mats = [mm for mm in mats if not (mm["name"] in seen_m or seen_m.add(mm["name"]))]
        cat["parts"][pid] = {"name": name, "kind": cat_kind, "slot": slot, "gender": gender_of(name, d["tags"]),
                             "z_depth": d["z_depth"], "file": "parts/" + name + ".npz", "materials": mats,
                             "verts": int(len(rsrc)), "tris": int(len(rtris)),
                             "delete_count": int(len(set(d["delete"]))), "tags": d["tags"][:8]}
        count += 1
    log(f"  {count} parts; skipped {len(skipped)}")
    for s_ in skipped:
        log("   - skipped", s_)
    cat["skipped"] = skipped
    with open(os.path.join(out, "catalog.json"), "w", encoding="utf-8") as f:
        json.dump(cat, f, indent=1, sort_keys=True)
    total = 0
    for r, _d, fs in os.walk(out):
        total += sum(os.path.getsize(os.path.join(r, x)) for x in fs)
    log(f"done: {total / 1e6:.1f} MB in {out} (textures {tex.bytes / 1e6:.1f} MB)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mpfb", required=True, help="mpfb2/src/mpfb/data")
    ap.add_argument("--packs", nargs="+", required=True, help="unzipped asset pack folders")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    build(a.mpfb, a.packs, a.out)
