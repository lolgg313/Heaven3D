# @heaven3d-part h3d27/engine/07_thumbnails.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# ---------------------------------------------------------------
# 7. Project thumbnails for the Hub (written next to the save file)
# ---------------------------------------------------------------
_H3D27_PREV_SAVE = HeavenEngine.save_project

def _h3d27_save_project(self, path=None):
    result = _H3D27_PREV_SAVE(self, path)
    try:
        target = path or self.project_path
        r = getattr(self, "renderer", None)
        tex = getattr(r, "final_color", None) if r is not None else None
        if target and tex is not None:
            w, h = tex.size
            surf = pygame.image.frombuffer(tex.read(), (w, h), "RGB")
            surf = pygame.transform.flip(surf, False, True)
            tw = 384
            th = max(1, int(h * tw / max(w, 1)))
            surf = pygame.transform.smoothscale(surf, (tw, th))
            pygame.image.save(surf, os.path.join(
                os.path.dirname(os.path.abspath(target)), ".heaven3d_thumb.png"))
    except Exception as ex:
        _h3d27_log_once("thumb", f"[Hub] project thumbnail not written: {ex}")
    return result

HeavenEngine.save_project = _h3d27_save_project
