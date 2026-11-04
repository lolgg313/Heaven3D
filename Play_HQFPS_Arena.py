"""Play the HQ FPS Arena.

    python Play_HQFPS_Arena.py                   # 1600x900
    python Play_HQFPS_Arena.py 1920 1080
    python Play_HQFPS_Arena.py --where            # just print the project path

The arena is a TEMPLATE (templates/hqfps_arena), not a change to any
existing scene.  The first run makes a project from it -- in your usual
Heaven3D Projects folder, with the hqfps_weapons and mh_characters
packages copied in, exactly as the Hub would -- and later runs reuse it,
so anything you change in the editor stays changed.

Controls
    WASD / Shift / Space    move, sprint, jump
    mouse                   look          ESC   release the mouse
    LMB  fire / swing / throw             RMB   aim down sights
    R    reload              1-0 / wheel / Q-E   switch weapon
"""
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = "hqfps_arena"
PROJECT_NAME = "HQ FPS Arena"


def hub():
    spec = importlib.util.spec_from_file_location(
        "Heaven3D_Hub", os.path.join(HERE, "Heaven3D_Hub.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _version(pkg_dir):
    try:
        with open(os.path.join(pkg_dir, "package.json"), encoding="utf-8") as fh:
            return json.load(fh).get("version")
    except (OSError, ValueError):
        return None


def project_path():
    """Make the project from the template if it is not there yet."""
    hb = hub()
    cfg = hb.Config()
    root = cfg.settings.get("projects_root") or hb._default_projects_root()
    dest = os.path.join(root, PROJECT_NAME)
    scene = os.path.join(dest, "main.heaven3d")
    if os.path.isfile(scene):
        # Keep the packages in step with the ones shipped here, so a fix to
        # the weapons reaches a project made weeks ago.  Copying them is
        # ~280 MB, so only a changed version is worth re-copying.
        for pid in ("hqfps_weapons", "mh_characters"):
            try:
                have = _version(os.path.join(dest, "packages", pid))
                ship = _version(os.path.join(HERE, "packages", pid))
                if ship and have != ship:
                    print("updating %s %s -> %s ..." % (pid, have or "(missing)", ship))
                    hb.add_package_to_project(dest, pid)
            except Exception as ex:
                print("could not refresh package %s: %s" % (pid, ex))
        return scene
    print("Creating '%s' from the %s template in:\n  %s" % (PROJECT_NAME, TEMPLATE, dest))
    hb.create_project(cfg, PROJECT_NAME, TEMPLATE, root)
    return scene


def main(argv):
    scene = project_path()
    if "--where" in argv:
        print(scene)
        return 0
    w, h = 1600, 900
    nums = [a for a in argv if a.isdigit()]
    if len(nums) >= 2:
        w, h = int(nums[0]), int(nums[1])

    spec = importlib.util.spec_from_file_location("Heaven3D", os.path.join(HERE, "Heaven3D.py"))
    H = importlib.util.module_from_spec(spec)
    sys.modules["Heaven3D"] = H
    spec.loader.exec_module(H)
    H._h3d_set_splash_mode("off")
    print(__doc__.split("Controls")[1].rstrip())
    # Runtime mode loads the scene and starts play inside the constructor;
    # the template's own HQFPSArena blueprint arms the player and spawns
    # the horde, so there is nothing to wire up here.
    H.HeavenEngine(mode="runtime", width=w, height=h, project_path=scene).run()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
