# @heaven3d-part engine/main.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)

# Heaven3D_runtime.py is built from this same source with this set to
# "runtime" (build.py), so `python Heaven3D_runtime.py` starts the game.
_H3D_DEFAULT_MODE = "editor"


def main():
    parser = argparse.ArgumentParser(
        description="Heaven3D — a complete 3D game engine on ModernGL.")
    parser.add_argument("--runtime", action="store_true",
                        help="Run in standalone runtime mode (no editor UI).")
    parser.add_argument("--project", type=str, default=None,
                        help="Path to a .heaven3d project file to load.")
    parser.add_argument("--width", type=int, default=1690)  # [FINALPATCH3:ui_scale_120]
    parser.add_argument("--height", type=int, default=950)  # [FINALPATCH3:ui_scale_120]
    parser.add_argument("--version", action="store_true")
    # [BRANDING] Boot-splash control.
    parser.add_argument("--no-splash", action="store_true",
                        help="Skip the boot splash entirely.")
    parser.add_argument("--splash", action="store_true",
                        help="Force the full first-run welcome splash.")
    parser.add_argument("--reset-welcome", action="store_true",
                        help="Forget that the engine has booted before, so "
                             "the next launch shows the welcome splash again.")
    parser.add_argument("--classic-ui", action="store_true",
                        help="Use the classic editor interface instead of the Vitrail one.")
    args = parser.parse_args()

    # [BRANDING] Resolve the splash mode before the engine is constructed —
    # the splash runs at the tail of HeavenEngine.__init__.
    if args.reset_welcome:
        _h3d_reset_welcome()
    if args.no_splash:
        _h3d_set_splash_mode("off")
    elif args.splash:
        _h3d_set_splash_mode("force")

    if args.version:
        print(f"Heaven3D v{HEAVEN_VERSION}")
        return

    mode = "runtime" if (args.runtime or _H3D_DEFAULT_MODE == "runtime") else "editor"
    engine = HeavenEngine(mode=mode, width=args.width, height=args.height,
                          project_path=args.project)
    engine.run()

