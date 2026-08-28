"""
Pure text-to-speech engine for the standalone Voice Speaker app: type text
into the web page, it's synthesized with espeak-ng, optionally run through
an ffmpeg audio filter (the same effect palette bot_music.py uses for
music), and played out loud on the device — through whatever output
Android currently has selected (speaker, wired, or a connected Bluetooth
headset), via Termux:API's termux-media-player.

Deliberately has no discord.py/yt-dlp dependency and doesn't import
bot_tts.py/bot_music.py — this app is meant to run completely standalone
from the Discord bot (see the repo root's setup.sh launcher menu), so it
only shares *concepts* with those modules, not code or process state.
"""
import os
import re
import shutil
import subprocess
import tempfile
import xml.sax.saxutils

_HAS_ESPEAK = shutil.which("espeak-ng") is not None
_HAS_FFMPEG = shutil.which("ffmpeg") is not None
_HAS_TERMUX_PLAYER = shutil.which("termux-media-player") is not None

MAX_CHARS = 2000
SYNTHESIZE_TIMEOUT = 30
PLAY_STOP_TIMEOUT = 5

# The same 20 hand-picked variants bot_tts.py offers, kept as their own
# group in the UI since they're genuinely distinct-sounding (not just a
# different language) — ship inside espeak-ng-data already, no extra
# install needed.
VOICE_VARIANTS = [
    ("en-us", "Default"),
    ("en-us+m1", "Male 1"),
    ("en-us+m3", "Deep Male"),
    ("en-us+m5", "Male 5"),
    ("en-us+m7", "Male 7"),
    ("en-us+f1", "Female 1"),
    ("en-us+f3", "Female 3"),
    ("en-us+f4", "Female 4"),
    ("en-us+croak", "Croaky"),
    ("en-us+whisper", "Whisper"),
    ("en-us+whisperf", "Whisper (female)"),
    ("en-us+klatt", "Robotic"),
    ("en-us+klatt3", "Robotic 3"),
    ("en-us+announcer", "Announcer"),
    ("en-us+grandma", "Grandma"),
    ("en-us+grandpa", "Grandpa"),
    ("en-us+robosoft2", "Robot"),
    ("en-gb", "British"),
    ("en-gb-scotland", "Scottish"),
    ("en-gb-x-rp", "Posh British"),
]

_VOICE_LINE_RE = re.compile(r"\s{2,}")


def list_language_voices():
    """The rest of espeak-ng's bundled voices (100+ languages) via
    `espeak-ng --voices`, parsed fresh each call rather than hardcoded —
    "more options" than the curated list above, for free, and always
    matches whatever's actually installed. Falls back to an empty list if
    espeak-ng isn't available or the listing format ever changes shape."""
    if not _HAS_ESPEAK:
        return []
    try:
        out = subprocess.run(
            ["espeak-ng", "--voices"], capture_output=True, text=True, timeout=5,
        ).stdout
    except (subprocess.TimeoutExpired, OSError):
        return []

    seen = set()
    voices = []
    for line in out.splitlines()[1:]:
        if not line.strip():
            continue
        parts = _VOICE_LINE_RE.split(line.strip())
        if len(parts) < 4:
            continue
        lang, name = parts[1], parts[3].replace("_", " ")
        if lang in seen or lang in {v[0] for v in VOICE_VARIANTS}:
            continue
        seen.add(lang)
        voices.append((lang, name))
    voices.sort(key=lambda v: v[1])
    return voices


def _unavailable_reason():
    if not _HAS_ESPEAK:
        return "Needs the `espeak-ng` binary. Run `pkg install espeak-ng` (Termux) or install it for your OS."
    return None


def _playback_unavailable_reason():
    if _HAS_TERMUX_PLAYER:
        return None
    if shutil.which("ffplay") or shutil.which("aplay") or shutil.which("paplay") or shutil.which("afplay"):
        return None
    return (
        "No way to play audio out loud was found. On Termux, install the Termux:API app "
        "(F-Droid/Play Store) plus `pkg install termux-api`. On desktop, any of ffplay/aplay/"
        "paplay/afplay will do."
    )


def _synthesize(text: str, *, voice: str, amplitude: int, wpm: int,
                 pitch_pct: int = None, range_pct: int = None):
    """Blocking — run off any event loop that matters. Returns a temp WAV
    path on success (caller deletes it), or None on failure."""
    fd, path = tempfile.mkstemp(suffix=".wav", prefix="voicespeak_")
    os.close(fd)
    args = ["espeak-ng", "-w", path, "-a", str(amplitude), "-s", str(wpm)]
    if voice:
        args += ["-v", voice]
    if pitch_pct is not None or range_pct is not None:
        attrs = []
        if pitch_pct is not None:
            attrs.append(f'pitch="{pitch_pct:+d}%"')
        if range_pct is not None:
            attrs.append(f'range="{range_pct}%"')
        ssml = f'<speak><prosody {" ".join(attrs)}>{xml.sax.saxutils.escape(text)}</prosody></speak>'
        args += ["-m", ssml]
    else:
        args += [text]
    try:
        subprocess.run(args, capture_output=True, timeout=SYNTHESIZE_TIMEOUT, check=True)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        if os.path.exists(path):
            os.remove(path)
        return None
    return path


# ==================== effects (ported from bot_music.py) ====================
# Same math/filters as the music bot's effect modes, applied to the
# synthesized voice instead of a music track. Karaoke/mono are skipped —
# they're stereo vocal-imaging tricks that don't mean anything on espeak-ng's
# already-mono output.

def _param(value, lo, hi, default):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, value))


def _reverb_taps(amount_pct):
    amount = _param(amount_pct, 0, 100, 50) / 100
    base_delays = [40, 60, 90, 120, 180, 250]
    base_decays = [0.5, 0.4, 0.35, 0.25, 0.2, 0.15]
    n = max(1, round(len(base_delays) * (0.3 + 0.7 * amount)))
    delays = base_delays[:n]
    decays = [round(d * (0.3 + 0.7 * amount), 3) for d in base_decays[:n]]
    return f"aecho=0.8:0.7:{'|'.join(str(d) for d in delays)}:{'|'.join(str(d) for d in decays)}"


def _filter_nightcore(p):
    ratio = 1 + _param(p.get("amount"), 0, 100, 25) / 100
    return f"asetrate=44100*{ratio:.4f},aresample=44100"


def _filter_vaporwave(p):
    ratio = 1 - _param(p.get("amount"), 0, 100, 25) / 100 * 0.5
    return f"asetrate=44100*{ratio:.4f},aresample=44100"


def _filter_chipmunk(p):
    ratio = 1 + _param(p.get("amount"), 0, 200, 60) / 100
    return f"asetrate=44100*{ratio:.4f},aresample=44100"


def _filter_slowed_reverb(p):
    ratio = 1 - _param(p.get("slow"), 0, 50, 12) / 100
    return f"asetrate=44100*{ratio:.4f},aresample=44100,{_reverb_taps(p.get('reverb'))}"


def _filter_reverb(p):
    return _reverb_taps(p.get("reverb"))


def _filter_echo(p):
    delay = int(_param(p.get("delay"), 100, 2000, 500))
    decay = _param(p.get("decay"), 0, 100, 40) / 100
    return f"aecho=0.8:0.9:{delay}:{decay:.2f}"


def _filter_bass_boost(p):
    amount = _param(p.get("amount"), 0, 30, 12)
    return f"bass=g={amount:.1f}"


def _filter_8d(p):
    speed = _param(p.get("speed"), 1, 30, 9) / 100
    return f"apulsator=hz={speed:.3f}"


def _filter_muffled(p):
    cutoff = int(_param(p.get("cutoff"), 100, 2000, 500))
    return f"lowpass=f={cutoff}"


def _filter_custom(p):
    speed = _param(p.get("speed"), 50, 200, 100) / 100
    if p.get("tied", True):
        return f"asetrate=44100*{speed:.4f},aresample=44100"
    pitch = _param(p.get("pitch"), 50, 200, 100) / 100
    tempo = max(0.5, min(100.0, speed / pitch))
    return f"asetrate=44100*{pitch:.4f},aresample=44100,atempo={tempo:.4f}"


EFFECT_BUILDERS = {
    "nightcore": _filter_nightcore,
    "vaporwave": _filter_vaporwave,
    "chipmunk": _filter_chipmunk,
    "slowed_reverb": _filter_slowed_reverb,
    "reverb": _filter_reverb,
    "echo": _filter_echo,
    "bass_boost": _filter_bass_boost,
    "8d": _filter_8d,
    "muffled": _filter_muffled,
    "radio": lambda p: "highpass=f=300,lowpass=f=3400",
    "custom": _filter_custom,
}
EFFECT_MODES = ["off", "nightcore", "vaporwave", "chipmunk", "slowed_reverb", "reverb",
                "echo", "bass_boost", "8d", "muffled", "radio", "custom"]
EFFECT_LABELS = {
    "off": "Off",
    "nightcore": "Nightcore",
    "vaporwave": "Vaporwave",
    "chipmunk": "Chipmunk",
    "slowed_reverb": "Slowed + Reverb",
    "reverb": "Reverb",
    "echo": "Echo",
    "bass_boost": "Bass Boost",
    "8d": "8D Audio",
    "muffled": "Muffled",
    "radio": "Radio",
    "custom": "Custom",
}
EFFECT_PARAM_SPECS = {
    "nightcore": [{"id": "amount", "label": "Amount", "min": 0, "max": 100, "default": 25, "step": 1, "unit": "%"}],
    "vaporwave": [{"id": "amount", "label": "Amount", "min": 0, "max": 100, "default": 25, "step": 1, "unit": "%"}],
    "chipmunk": [{"id": "amount", "label": "Amount", "min": 0, "max": 200, "default": 60, "step": 1, "unit": "%"}],
    "slowed_reverb": [
        {"id": "slow", "label": "Slowed", "min": 0, "max": 50, "default": 12, "step": 1, "unit": "%"},
        {"id": "reverb", "label": "Reverb", "min": 0, "max": 100, "default": 50, "step": 1, "unit": "%"},
    ],
    "reverb": [{"id": "reverb", "label": "Amount", "min": 0, "max": 100, "default": 50, "step": 1, "unit": "%"}],
    "echo": [
        {"id": "delay", "label": "Delay", "min": 100, "max": 2000, "default": 500, "step": 50, "unit": "ms"},
        {"id": "decay", "label": "Decay", "min": 0, "max": 100, "default": 40, "step": 1, "unit": "%"},
    ],
    "bass_boost": [{"id": "amount", "label": "Amount", "min": 0, "max": 30, "default": 12, "step": 1, "unit": "dB"}],
    "8d": [{"id": "speed", "label": "Rotation speed", "min": 1, "max": 30, "default": 9, "step": 1, "unit": "%"}],
    "muffled": [{"id": "cutoff", "label": "Cutoff", "min": 100, "max": 2000, "default": 500, "step": 50, "unit": "Hz"}],
    "custom": [
        {"id": "speed", "label": "Speed", "min": 50, "max": 200, "default": 100, "step": 1, "unit": "%"},
        {"id": "pitch", "label": "Pitch", "min": 50, "max": 200, "default": 100, "step": 1, "unit": "%"},
    ],
}
EFFECT_TIED_MODES = {"custom"}


def effect_filter_for(mode: str, params: dict, tied: bool = True):
    if mode == "off" or mode not in EFFECT_BUILDERS:
        return None
    p = dict(params or {})
    if mode in EFFECT_TIED_MODES:
        p["tied"] = tied
    return EFFECT_BUILDERS[mode](p)


def _apply_effect(wav_path: str, effect_filter: str):
    """Runs `wav_path` through ffmpeg's -af filter, returning a new temp
    file path (caller deletes both). Returns `wav_path` unchanged if
    ffmpeg isn't installed or the filter fails — an effect that can't be
    applied shouldn't stop the voice from playing at all."""
    if not effect_filter or not _HAS_FFMPEG:
        return wav_path
    fd, out_path = tempfile.mkstemp(suffix=".wav", prefix="voicespeak_fx_")
    os.close(fd)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", wav_path, "-af", effect_filter, out_path],
            capture_output=True, timeout=SYNTHESIZE_TIMEOUT, check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        if os.path.exists(out_path):
            os.remove(out_path)
        return wav_path
    return out_path


def synthesize(text: str, *, voice: str, volume: int, rate: int, tone: int, pitch: int,
               effect_mode: str = "off", effect_params: dict = None, custom_tied: bool = True):
    """Full pipeline: sanitize length, synthesize with espeak-ng, run
    through the chosen effect filter if any. Returns a WAV path, or None
    on failure. Caller is responsible for playing and then deleting it."""
    text = (text or "").strip()
    if not text or len(text) > MAX_CHARS:
        return None

    range_pct = _param(tone, 1, 10, 5) * 20
    raw = _synthesize(
        text,
        voice=voice or "en-us",
        amplitude=int(_param(volume, 0, 200, 100)),
        wpm=int(_param(rate, 80, 400, 175)),
        pitch_pct=int(_param(pitch, -100, 100, 0)),
        range_pct=int(range_pct),
    )
    if raw is None:
        return None

    effect_filter = effect_filter_for(effect_mode, effect_params, tied=custom_tied)
    if not effect_filter:
        return raw

    processed = _apply_effect(raw, effect_filter)
    if processed != raw and os.path.exists(raw):
        os.remove(raw)
    return processed


def play(path: str) -> bool:
    """Plays `path` out loud right now, asynchronously — returns once
    playback has started, not once it's finished. Prefers
    termux-media-player (follows whatever output Android has selected —
    speaker, wired, or a connected Bluetooth headset); falls back to a
    common desktop player if this isn't Termux."""
    if _HAS_TERMUX_PLAYER:
        subprocess.Popen(
            ["termux-media-player", "play", path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return True
    for player in ("ffplay", "paplay", "aplay", "afplay"):
        exe = shutil.which(player)
        if not exe:
            continue
        args = [exe, "-nodisp", "-autoexit", "-loglevel", "quiet", path] if player == "ffplay" else [exe, path]
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    return False


def stop():
    """Stops whatever's currently playing, if anything."""
    if _HAS_TERMUX_PLAYER:
        subprocess.run(["termux-media-player", "stop"], capture_output=True, timeout=PLAY_STOP_TIMEOUT)
