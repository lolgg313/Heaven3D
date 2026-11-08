# @heaven3d-part audio/audio.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# #############################################################################
# SECTION 14B —  AUDIO SYSTEM  (pygame.mixer: files, 3D positional, synth)
# #############################################################################
#
#  engine.audio gives every game:
#      * audio.play("shot.wav") / audio.play("shoot")   — files or built-ins
#      * audio.play_at("explosion", position)           — 3D positional:
#        distance attenuation + constant-power stereo pan from the listener
#      * audio.play_music(path) / stop_music()          — background music
#      * a procedural SOUND BANK synthesised at boot with numpy (no asset
#        files needed): shoot, shotgun, hit, explosion, footstep, jump,
#        land, pickup, reload, hurt, enemy, click, heal
#      * graceful silent mode when the machine has no audio device —
#        the game never crashes, it just plays quiet.

_AUDIO_SAMPLE_RATE = 44100


def _audio_f32_to_stereo_bytes(samples: np.ndarray) -> bytes:
    """float32 mono [-1,1] -> interleaved little-endian int16 stereo bytes."""
    samples = np.clip(samples, -1.0, 1.0)
    i16 = (samples * 32767.0).astype(np.int16)
    stereo = np.empty(len(i16) * 2, dtype=np.int16)
    stereo[0::2] = i16
    stereo[1::2] = i16
    return stereo.tobytes()


def _synth_envelope(n: int, attack: float, decay: float,
                    sr: int = _AUDIO_SAMPLE_RATE) -> np.ndarray:
    """Exponential-decay envelope with a linear attack ramp (seconds)."""
    env = np.ones(n, dtype=np.float32)
    a = max(1, int(attack * sr))
    env[:a] = np.linspace(0.0, 1.0, a, dtype=np.float32)
    t = np.arange(n, dtype=np.float32) / float(sr)
    env *= np.exp(-t * max(decay, 0.01))
    return env


def _synth_noise(n: int, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.uniform(-1.0, 1.0, n).astype(np.float32)


def _synth_sine(freq, n: int, sr: int = _AUDIO_SAMPLE_RATE) -> np.ndarray:
    t = np.arange(n, dtype=np.float32) / float(sr)
    if isinstance(freq, (tuple, list)):   # sweep f0 -> f1
        f0, f1 = float(freq[0]), float(freq[1])
        phase = 2.0 * math.pi * (f0 * t + (f1 - f0) * t * t /
                                 (2.0 * max(float(t[-1]), 1e-6)))
    else:
        phase = 2.0 * math.pi * float(freq) * t
    return np.sin(phase).astype(np.float32)


def _synth_lowpass(x: np.ndarray, alpha: float = 0.15) -> np.ndarray:
    """One-pole low-pass filter (cheap smoothing)."""
    if len(x) == 0:
        return x
    out = np.empty_like(x)
    acc = 0.0
    a = _clamp(alpha, 0.001, 1.0)
    for i in range(len(x)):
        acc += a * (float(x[i]) - acc)
        out[i] = acc
    return out


def _synth_mix(*parts) -> np.ndarray:
    n = max(len(p) for p in parts)
    out = np.zeros(n, dtype=np.float32)
    for p in parts:
        out[:len(p)] += p
    peak = float(np.max(np.abs(out))) if n else 0.0
    if peak > 1e-6:
        out *= 0.85 / peak
    return out


def _synth_bank() -> Dict[str, bytes]:
    """Build the procedural sound bank -> {name: stereo int16 bytes}."""
    sr = _AUDIO_SAMPLE_RATE
    bank: Dict[str, bytes] = {}

    def put(name, samples):
        bank[name] = _audio_f32_to_stereo_bytes(samples)

    # ---- shoot: sharp noise crack + low body thump ----
    n = int(0.14 * sr)
    crack = _synth_noise(n, 1) * _synth_envelope(n, 0.001, 34.0)
    thump = _synth_sine((190.0, 55.0), n) * _synth_envelope(n, 0.001, 26.0)
    put("shoot", _synth_mix(crack * 0.9, thump * 0.7))

    # ---- shotgun: bigger boom ----
    n = int(0.28 * sr)
    boom = _synth_noise(n, 2) * _synth_envelope(n, 0.001, 16.0)
    body = _synth_sine((130.0, 42.0), n) * _synth_envelope(n, 0.001, 13.0)
    put("shotgun", _synth_mix(boom * 1.0, body * 0.9))

    # ---- hit marker: short tick ----
    n = int(0.06 * sr)
    tick = _synth_noise(n, 3) * _synth_envelope(n, 0.0005, 70.0)
    ping = _synth_sine(950.0, n) * _synth_envelope(n, 0.0005, 55.0)
    put("hit", _synth_mix(tick * 0.8, ping * 0.5))

    # ---- explosion: long rumble ----
    n = int(0.8 * sr)
    blast = _synth_noise(n, 4) * _synth_envelope(n, 0.002, 5.5)
    sub = _synth_sine((85.0, 28.0), n) * _synth_envelope(n, 0.002, 5.0)
    put("explosion", _synth_mix(_synth_lowpass(blast, 0.35) * 1.1, sub))

    # ---- footstep: soft filtered thud ----
    n = int(0.07 * sr)
    step = _synth_noise(n, 5) * _synth_envelope(n, 0.001, 60.0)
    put("footstep", _synth_mix(_synth_lowpass(step, 0.10) * 1.2))

    # ---- jump: quick rising blip ----
    n = int(0.12 * sr)
    put("jump", _synth_mix(_synth_sine((280.0, 520.0), n)
                           * _synth_envelope(n, 0.005, 22.0)))

    # ---- land: low thud ----
    n = int(0.10 * sr)
    land = _synth_noise(n, 6) * _synth_envelope(n, 0.001, 40.0)
    put("land", _synth_mix(_synth_lowpass(land, 0.12) * 1.2,
                           _synth_sine(90.0, n)
                           * _synth_envelope(n, 0.001, 30.0) * 0.6))

    # ---- pickup: happy two-note blip ----
    n1 = int(0.07 * sr)
    n2 = int(0.10 * sr)
    a = _synth_sine(660.0, n1) * _synth_envelope(n1, 0.002, 30.0)
    b = _synth_sine(990.0, n2) * _synth_envelope(n2, 0.002, 25.0)
    put("pickup", _synth_mix(a, np.concatenate([np.zeros(n1, np.float32),
                                                b])))

    # ---- reload: click ... clack ----
    n = int(0.30 * sr)
    out = np.zeros(n, np.float32)
    c1 = int(0.02 * sr)
    out[:c1] += _synth_noise(c1, 7) * _synth_envelope(c1, 0.0005, 120.0)
    off = int(0.16 * sr)
    c2 = n - off
    out[off:] += _synth_noise(c2, 8) * _synth_envelope(c2, 0.0005, 90.0)
    put("reload", _synth_mix(out))

    # ---- hurt: falling grunt ----
    n = int(0.22 * sr)
    sq = np.sign(_synth_sine((210.0, 90.0), n)) * 0.6
    put("hurt", _synth_mix(_synth_lowpass(sq, 0.3)
                           * _synth_envelope(n, 0.003, 16.0)))

    # ---- enemy growl: wobbling saw ----
    n = int(0.45 * sr)
    t = np.arange(n, dtype=np.float32) / sr
    wob = 78.0 + 9.0 * np.sin(2 * math.pi * 7.0 * t)
    phase = np.cumsum(wob) / sr * 2.0 * math.pi
    saw = (2.0 * (phase / (2 * math.pi) % 1.0) - 1.0).astype(np.float32)
    put("enemy", _synth_mix(_synth_lowpass(saw, 0.2)
                            * _synth_envelope(n, 0.01, 7.0)))

    # ---- click (empty gun / UI) ----
    n = int(0.03 * sr)
    put("click", _synth_mix(_synth_sine(1700.0, n)
                            * _synth_envelope(n, 0.0003, 120.0)))

    # ---- heal: soft major chord ----
    n = int(0.25 * sr)
    chord = (_synth_sine(523.0, n) + _synth_sine(659.0, n)
             + _synth_sine(784.0, n)) / 3.0
    put("heal", _synth_mix(chord * _synth_envelope(n, 0.01, 12.0)))
    return bank


class AudioSystem:
    """Game audio: file sounds, built-in synth sounds, 3D positional
    playback, music.  Never raises into the game loop — audio problems
    degrade to silence with a warning."""

    def __init__(self, num_channels: int = 32):
        self.enabled = False
        self.master_volume = 1.0
        self._sounds: Dict[str, "pygame.mixer.Sound"] = {}
        self._failed_once: set = set()
        self._channels = 0
        self._next_ch = 0
        self.listener_pos = glm.vec3(0.0)
        self.listener_right = glm.vec3(1.0, 0.0, 0.0)
        self.listener_fwd = glm.vec3(0.0, 0.0, -1.0)
        self.ref_distance = 4.0      # full volume up to this range
        self.max_distance = 80.0     # silent beyond this range
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init(frequency=_AUDIO_SAMPLE_RATE, size=-16,
                                  channels=2, buffer=512)
            pygame.mixer.set_num_channels(num_channels)
            self._channels = num_channels
            self.enabled = True
            for name, payload in _synth_bank().items():
                try:
                    self._sounds[name] = pygame.mixer.Sound(buffer=payload)
                except Exception as ex:
                    Log.warn(f"Synth sound '{name}' failed: {ex}")
            Log.info(f"Audio ready ({len(self._sounds)} built-in sounds, "
                     f"{num_channels} channels)")
        except Exception as ex:
            self.enabled = False
            Log.warn(f"Audio device unavailable ({ex}); running silent. "
                     f"Set SDL_AUDIODRIVER=dummy for headless testing.")

    # ------------------------------------------------------------- loading
    def load_sound(self, path_or_name, name: Optional[str] = None) -> bool:
        """Load a sound file (wav/ogg/mp3) under `name` (default: filename
        stem).  Returns True on success.  Missing files warn, never raise."""
        if not self.enabled:
            return False
        path = str(path_or_name)
        key = name or os.path.splitext(os.path.basename(path))[0]
        try:
            if not os.path.isfile(path):
                raise FileNotFoundError(path)
            self._sounds[key] = pygame.mixer.Sound(path)
            return True
        except Exception as ex:
            if key not in self._failed_once:
                self._failed_once.add(key)
                Log.warn(f"Sound '{key}' could not be loaded: {ex}")
            return False

    def has_sound(self, name: str) -> bool:
        return name in self._sounds

    # ------------------------------------------------------------- playback
    def _alloc_channel(self):
        if not self.enabled or self._channels <= 0:
            return None
        ch = pygame.mixer.find_channel()
        if ch is None:
            ch = pygame.mixer.Channel(self._next_ch % self._channels)
            self._next_ch += 1
        return ch

    def play(self, name, volume: float = 1.0, loop: bool = False):
        """Play a 2D sound (UI, gunfire at the player, etc.).
        `name` is a built-in synth name, a loaded sound name, or a path to
        a sound file (auto-loaded on first use)."""
        if not self.enabled:
            return None
        key = str(name)
        snd = self._sounds.get(key)
        if snd is None:
            # Auto-load under the KEY, not the filename stem -- `play` looks
            # the sound up by exactly the string it was given.
            if os.path.isfile(key) and self.load_sound(key, name=key):
                snd = self._sounds.get(key)
        if snd is None:
            if key not in self._failed_once:
                self._failed_once.add(key)
                Log.warn(f"Unknown sound '{key}' (not built-in, not loaded, "
                         f"no file).")
            return None
        ch = self._alloc_channel()
        if ch is None:
            return None
        v = _clamp(float(volume), 0.0, 1.0) * self.master_volume
        try:
            ch.set_volume(v)
            ch.play(snd, loops=-1 if loop else 0)
            return ch
        except Exception as ex:
            Log.warn(f"play('{key}') failed: {ex}")
            return None

    def _compute_pan(self, position) -> Tuple[float, float, float, float]:
        """(left_gain, right_gain, attenuated_volume, distance) for a
        world position."""
        to_src = _v3(position) - self.listener_pos
        dist = glm.length(to_src)
        att = self.ref_distance / (self.ref_distance + dist)
        fwd_flat = glm.vec3(self.listener_fwd.x, 0.0, self.listener_fwd.z)
        src_flat = glm.vec3(to_src.x, 0.0, to_src.z)
        pan = 0.0
        if glm.length(src_flat) > 1e-4 and glm.length(fwd_flat) > 1e-4:
            right = _safe_normalize(glm.cross(
                _safe_normalize(fwd_flat, glm.vec3(0, 0, -1)),
                glm.vec3(0, 1, 0)), glm.vec3(1, 0, 0))
            pan = _clamp(glm.dot(_safe_normalize(src_flat), right), -1.0, 1.0)
        ang = (pan + 1.0) * math.pi * 0.25          # 0..pi/2
        vol = _clamp(att, 0.0, 1.0)
        return vol * math.cos(ang), vol * math.sin(ang), vol, dist

    def play_at(self, name, position, volume: float = 1.0,
                ref_distance: Optional[float] = None,
                max_distance: Optional[float] = None):
        """3D positional playback: volume falls off with distance from the
        listener and the sound is panned toward its world direction."""
        if not self.enabled:
            return None
        old_ref = self.ref_distance
        if ref_distance:
            self.ref_distance = float(ref_distance)
        try:
            left, right_v, vol, dist = self._compute_pan(position)
        finally:
            self.ref_distance = old_ref
        mx = float(max_distance) if max_distance else self.max_distance
        if dist > mx:
            return None
        vol *= _clamp(float(volume), 0.0, 1.0)
        left *= _clamp(float(volume), 0.0, 1.0) * self.master_volume
        right_v *= _clamp(float(volume), 0.0, 1.0) * self.master_volume
        key = str(name)
        snd = self._sounds.get(key)
        if snd is None:
            # Auto-load under the KEY, not the filename stem -- `play` looks
            # the sound up by exactly the string it was given.
            if os.path.isfile(key) and self.load_sound(key, name=key):
                snd = self._sounds.get(key)
        if snd is None:
            return None
        ch = self._alloc_channel()
        if ch is None:
            return None
        try:
            ch.set_volume(left, right_v)
            ch.play(snd)
            return ch
        except Exception:
            try:
                ch.set_volume(vol)
                ch.play(snd)
                return ch
            except Exception:
                return None

    # ------------------------------------------------------------- music
    def play_music(self, path: str, loop: bool = True, volume: float = 0.6):
        if not self.enabled:
            return
        try:
            pygame.mixer.music.load(path)
            pygame.mixer.music.set_volume(
                _clamp(volume, 0.0, 1.0) * self.master_volume)
            pygame.mixer.music.play(-1 if loop else 0)
        except Exception as ex:
            Log.warn(f"Music '{path}' failed: {ex}")

    def stop_music(self):
        if not self.enabled:
            return
        try:
            pygame.mixer.music.stop()
        except Exception:
            pass

    # ------------------------------------------------------------- control
    def set_listener(self, position, forward=None, right=None):
        """Point the 3D audio listener at a world pose (call every frame
        from the player/camera)."""
        self.listener_pos = _v3(position)
        if forward is not None:
            self.listener_fwd = _safe_normalize(_v3(forward),
                                                glm.vec3(0, 0, -1))
        if right is not None:
            self.listener_right = _safe_normalize(_v3(right),
                                                  glm.vec3(1, 0, 0))
        elif forward is not None:
            self.listener_right = _safe_normalize(glm.cross(
                self.listener_fwd, glm.vec3(0, 1, 0)), glm.vec3(1, 0, 0))

    def set_master_volume(self, v: float):
        self.master_volume = _clamp(float(v), 0.0, 1.0)

    def stop_all(self):
        if not self.enabled:
            return
        try:
            pygame.mixer.stop()
        except Exception:
            pass

    def shutdown(self):
        if not self.enabled:
            return
        try:
            pygame.mixer.stop()
            pygame.mixer.music.stop()
        except Exception:
            pass
        self.enabled = False

