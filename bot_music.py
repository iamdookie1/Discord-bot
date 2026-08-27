"""
Voice/music commands. Needs PyNaCl (voice encryption), davey (Discord's
mandatory DAVE end-to-end voice encryption protocol, required by Discord
itself since March 2026), the `ffmpeg` binary on PATH, and yt-dlp (stream
extraction) to actually play audio — all optional installs, so these
commands degrade to a clear error instead of crashing if any piece is
missing.

Playback state lives in GuildMusicState, one per guild. The Discord-side
"now playing" menu (an embed + buttons on a single message, reused across
the whole queue) is edited immediately on any state change and otherwise
ticks every MENU_REFRESH_SECONDS for the elapsed-time counter — that
interval is deliberately conservative (Discord rate-limits message edits
to roughly 5 per 5 seconds). The equivalent web controls in the Flask UI
don't go through Discord's API at all, so they can poll far more often;
see get_state_dict()/web_*() below, called from app.py.
"""
import array
import asyncio
import json
import os
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request

import discord

import guild_settings
import voice_owner

try:
    import nacl  # noqa: F401
    _HAS_NACL = True
except ImportError:
    _HAS_NACL = False

try:
    import davey  # noqa: F401
    _HAS_DAVEY = True
except ImportError:
    _HAS_DAVEY = False

try:
    import yt_dlp
    _HAS_YTDLP = True
except ImportError:
    _HAS_YTDLP = False

_HAS_FFMPEG = shutil.which("ffmpeg") is not None

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}
YTDLP_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    # Skipping cert verification and capping the socket timeout are both
    # safe here (we're only fetching public video metadata, not anything
    # sensitive) and shave real time off every lookup, which matters most
    # on a phone's mobile connection.
    "nocheckcertificate": True,
    "socket_timeout": 8,
    "default_search": "ytsearch",
    # A recurring YouTube-side anti-bot workaround: without this, extraction
    # can succeed but hand back a stream URL that 403s the moment ffmpeg
    # actually requests it. Keeping yt-dlp itself up to date (see run.py)
    # matters at least as much — this alone doesn't fix an outdated version.
    "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
}

def _param(value, lo, hi, default):
    """Clamps a slider value from the web UI to its valid range, falling
    back to the default for anything missing/unparseable."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, value))


def _reverb_taps(amount_pct):
    """A handful of short, closely-spaced aecho taps rather than one long
    one — ffmpeg has no real reverb filter (afreeverb isn't compiled into
    most builds), and a single ~1s echo just sounds like a discrete
    repeat, not a reverb tail. `amount_pct` (0-100) scales both how many
    taps are active and how loud they are, from barely-there to a dense
    wash."""
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
    """Free-form speed/pitch, with an optional "tied" mode where pitch
    just follows speed (the classic nightcore/vaporwave trick — one
    asetrate does both, always safe) versus independent control (pitch
    via asetrate, tempo corrected back separately via atempo)."""
    speed = _param(p.get("speed"), 50, 200, 100) / 100
    if p.get("tied", True):
        return f"asetrate=44100*{speed:.4f},aresample=44100"
    pitch = _param(p.get("pitch"), 50, 200, 100) / 100
    tempo = max(0.5, min(100.0, speed / pitch))  # ffmpeg's atempo range, single call
    return f"asetrate=44100*{pitch:.4f},aresample=44100,atempo={tempo:.4f}"


# mode -> function(params_dict) -> ffmpeg -af filter chain string. Applied
# by building a fresh audio source (see _start_source/_apply_effect_live) —
# an ffmpeg filter can't be changed on an already-running process, so
# switching effects (or tweaking a slider) on the currently-playing track
# means building a new source at its current position and hot-swapping it
# in, not stopping and restarting playback.
_EFFECT_BUILDERS = {
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
    "karaoke": lambda p: "pan=stereo|c0=c0-c1|c1=c1-c0",
    "mono": lambda p: "pan=mono|c0=0.5*c0+0.5*c1",
    "custom": _filter_custom,
}
EFFECT_MODES = ["off", "nightcore", "vaporwave", "chipmunk", "slowed_reverb", "reverb", "echo",
                "bass_boost", "8d", "muffled", "radio", "karaoke", "mono", "custom"]
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
    "karaoke": "Karaoke (vocal reduction)",
    "mono": "Mono",
    "custom": "Custom",
}

# Per-mode tunable sliders, sent to the web UI as-is so it can build them
# generically instead of hardcoding a form per effect. (id, label, min,
# max, default, step, unit) — modes not listed here (radio/karaoke/mono)
# have nothing to tweak.
EFFECT_PARAM_SPECS = {
    "nightcore": [{"id": "amount", "label": "Amount", "min": 0, "max": 100, "default": 25, "step": 1, "unit": "%"}],
    "vaporwave": [{"id": "amount", "label": "Amount", "min": 0, "max": 100, "default": 25, "step": 1, "unit": "%"}],
    "chipmunk": [{"id": "amount", "label": "Amount", "min": 0, "max": 200, "default": 60, "step": 1, "unit": "%"}],
    "slowed_reverb": [
        {"id": "slow", "label": "Slowed", "min": 0, "max": 50, "default": 12, "step": 1, "unit": "%"},
        {"id": "reverb", "label": "Reverb", "min": 0, "max": 100, "default": 50, "step": 1, "unit": "%"},
    ],
    "reverb": [{"id": "reverb", "label": "Reverb", "min": 0, "max": 100, "default": 50, "step": 1, "unit": "%"}],
    "echo": [
        {"id": "delay", "label": "Delay", "min": 100, "max": 2000, "default": 500, "step": 50, "unit": "ms"},
        {"id": "decay", "label": "Decay", "min": 0, "max": 100, "default": 40, "step": 1, "unit": "%"},
    ],
    "bass_boost": [{"id": "amount", "label": "Amount", "min": 0, "max": 30, "default": 12, "step": 1, "unit": "dB"}],
    "8d": [{"id": "speed", "label": "Speed", "min": 1, "max": 30, "default": 9, "step": 1, "unit": ""}],
    "muffled": [{"id": "cutoff", "label": "Cutoff", "min": 100, "max": 2000, "default": 500, "step": 50, "unit": "Hz"}],
    "custom": [
        {"id": "speed", "label": "Speed", "min": 50, "max": 200, "default": 100, "step": 1, "unit": "%"},
        {"id": "pitch", "label": "Pitch", "min": 50, "max": 200, "default": 100, "step": 1, "unit": "%"},
    ],
}
# Modes with a "pitch follows speed" checkbox alongside their sliders.
EFFECT_TIED_MODES = {"custom"}


def _effect_params_for(state, mode=None) -> dict:
    """Current (or default) slider values for a mode, merged so the UI
    always has concrete numbers to show even before anything's been
    tweaked."""
    mode = mode or state.effect_mode
    specs = EFFECT_PARAM_SPECS.get(mode, [])
    stored = state.effect_params.get(mode, {})
    return {s["id"]: stored.get(s["id"], s["default"]) for s in specs}


def _effect_filter_for(state) -> str | None:
    if state.effect_mode == "off":
        return None
    builder = _EFFECT_BUILDERS.get(state.effect_mode)
    if not builder:
        return None
    params = _effect_params_for(state)
    if state.effect_mode in EFFECT_TIED_MODES:
        params = {**params, "tied": state.custom_tied}
    return builder(params)

# A prefetched stream URL older than this is discarded and re-fetched fresh
# instead of trusted — well under the hours-long expiry YouTube stream URLs
# normally carry, just a safety margin in case a track sits queued a while.
PREFETCH_MAX_AGE_SECONDS = 600

# Waiting this long before starting the background prefetch keeps it clear
# of the current track's own startup window (ffmpeg's initial buffer-fill
# right after voice_client.play() is the most CPU-sensitive moment) — on a
# phone especially, doing heavy yt-dlp work at the exact same time can
# starve the audio player thread of CPU/GIL time and cause audible
# stutter. The extraction thread is also deprioritized (see _extract's
# `background` flag) as a second line of defense for the rest of the song.
PREFETCH_DELAY_SECONDS = 5
# How much to lower (os.nice) the background prefetch thread's scheduling
# priority by — verified to be thread-scoped on Linux (including Termux/
# Android), not process-wide, so it only ever affects that one lookup.
PREFETCH_NICENESS = 10

CROSSFADE_MIN_SECONDS = 0
CROSSFADE_MAX_SECONDS = 10

# Effect mode/slider changes hot-swap in a freshly-filtered source (see
# _apply_effect_live) instead of stopping and restarting the track — this
# is that swap's own short blend window, purely to smooth over the filter
# change itself, not a real crossfade between two different songs.
EFFECT_SWAP_FADE_SECONDS = 0.2

_SPOTIFY_TRACK_RE = re.compile(r"open\.spotify\.com/(?:intl-\w+/)?track/([A-Za-z0-9]+)")
_SPOTIFY_OTHER_RE = re.compile(r"open\.spotify\.com/(?:intl-\w+/)?(album|playlist|artist)/([A-Za-z0-9]+)")

MENU_REFRESH_SECONDS = 5
IDLE_DISCONNECT_SECONDS = 5 * 60
VOLUME_STEP = 0.1
MIN_VOLUME = 0.0
MAX_VOLUME = 2.0
PROGRESS_BAR_LENGTH = 18
LOOP_MODES = ["off", "track", "queue"]


def _unavailable_reason():
    missing = []
    if not _HAS_NACL:
        missing.append("PyNaCl")
    if not _HAS_DAVEY:
        missing.append("davey")
    if not _HAS_FFMPEG:
        missing.append("the ffmpeg binary")
    if not _HAS_YTDLP:
        missing.append("yt-dlp")
    if not missing:
        return None
    return "Music needs " + ", ".join(missing) + " installed. Run `bash setup.sh` again, or install manually."


# ==================== per-guild playback state ====================

class GuildMusicState:
    def __init__(self):
        self.queue = []            # list[dict(title, query, duration, requester)] — no url; see _play_next
        self.current = None        # dict, same shape as a queue entry, or None
        self.source = None         # discord.PCMVolumeTransformer of the current track
        self.volume = 1.0          # 0.0 - 2.0
        self.loop_mode = "off"     # off | track | queue
        self.position = 0.0        # seconds played before the current "run" started
        self.resumed_at = None     # time.monotonic() when playback last (re)started
        self.is_paused = False
        self.menu_message = None   # discord.Message — the persistent now-playing menu
        self.refresh_task = None   # asyncio.Task ticking the menu's elapsed time
        self.idle_task = None      # asyncio.Task that disconnects after IDLE_DISCONNECT_SECONDS
        self.text_channel = None   # last text channel !play was used in — for skip/error messages
        self.effect_mode = "off"   # off | one of EFFECT_MODES — see _EFFECT_BUILDERS
        self.effect_params: dict = {}  # {mode: {param_id: value}} — remembers each mode's last tweak
        self.custom_tied = True    # "custom" mode only — pitch follows speed when True
        self.pending_restart = None    # {"track":, "elapsed":} set when restarting the current track
        self.crossfade_seconds = 0.0   # 0 disables it — see _schedule_crossfade
        self.crossfade_task = None     # asyncio.Task counting down to the next crossfade


_states: dict[int, GuildMusicState] = {}


def _state(guild_id: int) -> GuildMusicState:
    return _states.setdefault(guild_id, GuildMusicState())


def _elapsed(state: GuildMusicState) -> float:
    if state.current is None:
        return 0.0
    if state.is_paused or state.resumed_at is None:
        return state.position
    return state.position + (time.monotonic() - state.resumed_at)


def _reset_position(state: GuildMusicState):
    state.position = 0.0
    state.resumed_at = time.monotonic()
    state.is_paused = False


def _adjust_volume(state: GuildMusicState, delta: float):
    state.volume = round(min(max(state.volume + delta, MIN_VOLUME), MAX_VOLUME), 2)
    if state.source is not None:
        state.source.volume = state.volume


def _cancel_crossfade_task(state: GuildMusicState):
    if state.crossfade_task and not state.crossfade_task.done():
        state.crossfade_task.cancel()
    state.crossfade_task = None


# ==================== formatting ====================

def _fmt_time(seconds) -> str:
    seconds = max(0, int(seconds or 0))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _progress_bar(elapsed: float, duration) -> str | None:
    if not duration:
        return None
    ratio = min(max(elapsed / duration, 0.0), 1.0)
    filled = min(int(ratio * PROGRESS_BAR_LENGTH), PROGRESS_BAR_LENGTH - 1)
    return "▬" * filled + "🔘" + "▬" * (PROGRESS_BAR_LENGTH - filled - 1)


def _build_embed(state: GuildMusicState) -> discord.Embed:
    track = state.current
    if not track:
        return discord.Embed(
            title="Nothing playing",
            description="Use `!play <song>` to start something.",
            color=discord.Color.dark_grey(),
        )

    elapsed = _elapsed(state)
    duration = track.get("duration")
    embed = discord.Embed(title="🎵 Now Playing", description=f"**{track['title']}**", color=discord.Color(0xFFB454))

    bar = _progress_bar(elapsed, duration)
    if bar:
        embed.add_field(name="Progress", value=f"{bar}\n`{_fmt_time(elapsed)} / {_fmt_time(duration)}`", inline=False)
    else:
        embed.add_field(name="Elapsed", value=f"`{_fmt_time(elapsed)}`", inline=False)

    embed.add_field(name="Status", value="⏸️ Paused" if state.is_paused else "▶️ Playing", inline=True)
    embed.add_field(name="Volume", value=f"{round(state.volume * 100)}%", inline=True)
    embed.add_field(name="Loop", value=state.loop_mode.capitalize(), inline=True)

    if state.queue:
        upcoming = ", ".join(t["title"] for t in state.queue[:3])
        more = f" (+{len(state.queue) - 3} more)" if len(state.queue) > 3 else ""
        embed.add_field(name="Up next", value=f"{upcoming}{more}", inline=False)

    embed.set_footer(text=f"Requested by {track.get('requester', '?')}")
    return embed


# ==================== the interactive menu ====================

async def _same_voice_channel(interaction: discord.Interaction, guild: discord.Guild) -> bool:
    vc = guild.voice_client if guild else None
    user_voice = getattr(interaction.user, "voice", None)
    if not vc or not user_voice or not user_voice.channel or user_voice.channel.id != vc.channel.id:
        await interaction.response.send_message("Join the same voice channel to control playback.", ephemeral=True)
        return False
    return True


class MusicMenuView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self._sync_labels()

    def _sync_labels(self):
        state = _state(self.guild_id)
        self.pause_btn.label = "▶️ Resume" if state.is_paused else "⏸️ Pause"
        self.loop_btn.label = f"🔁 Loop: {state.loop_mode.capitalize()}"

    @discord.ui.button(label="⏸️ Pause", style=discord.ButtonStyle.secondary, row=0)
    async def pause_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if not await _same_voice_channel(interaction, guild):
            return
        state = _state(guild.id)
        vc = guild.voice_client
        if not vc or not state.current:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)
            return
        if state.is_paused:
            vc.resume()
            state.is_paused = False
            state.resumed_at = time.monotonic()
        else:
            vc.pause()
            state.position = _elapsed(state)
            state.is_paused = True
        self._sync_labels()
        await interaction.response.edit_message(embed=_build_embed(state), view=self)

    @discord.ui.button(label="⏭️ Skip", style=discord.ButtonStyle.secondary, row=0)
    async def skip_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if not await _same_voice_channel(interaction, guild):
            return
        vc = guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()):
            state = _state(guild.id)
            state.pending_restart = None
            _cancel_crossfade_task(state)
            vc.stop()
            await interaction.response.send_message("Skipped.", ephemeral=True)
        else:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)

    @discord.ui.button(label="⏹️ Stop", style=discord.ButtonStyle.danger, row=0)
    async def stop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if not await _same_voice_channel(interaction, guild):
            return
        state = _state(guild.id)
        vc = guild.voice_client
        state.pending_restart = None
        _cancel_crossfade_task(state)
        state.queue.clear()
        state.loop_mode = "off"
        state.current = None
        if vc:
            vc.stop()
        await interaction.response.send_message("Stopped and cleared the queue.", ephemeral=True)

    @discord.ui.button(label="🔉 Vol -", style=discord.ButtonStyle.secondary, row=1)
    async def vol_down_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if not await _same_voice_channel(interaction, guild):
            return
        state = _state(guild.id)
        _adjust_volume(state, -VOLUME_STEP)
        await interaction.response.edit_message(embed=_build_embed(state), view=self)

    @discord.ui.button(label="🔊 Vol +", style=discord.ButtonStyle.secondary, row=1)
    async def vol_up_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if not await _same_voice_channel(interaction, guild):
            return
        state = _state(guild.id)
        _adjust_volume(state, VOLUME_STEP)
        await interaction.response.edit_message(embed=_build_embed(state), view=self)

    @discord.ui.button(label="🔁 Loop: Off", style=discord.ButtonStyle.secondary, row=1)
    async def loop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if not await _same_voice_channel(interaction, guild):
            return
        state = _state(guild.id)
        state.loop_mode = LOOP_MODES[(LOOP_MODES.index(state.loop_mode) + 1) % len(LOOP_MODES)]
        self._sync_labels()
        await interaction.response.edit_message(embed=_build_embed(state), view=self)

    @discord.ui.button(label="📋 Queue", style=discord.ButtonStyle.secondary, row=1)
    async def queue_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = _state(interaction.guild.id)
        if not state.queue:
            await interaction.response.send_message("Queue is empty.", ephemeral=True)
            return
        lines = [f"{i + 1}. {t['title']}" for i, t in enumerate(state.queue[:10])]
        more = f"\n...and {len(state.queue) - 10} more" if len(state.queue) > 10 else ""
        await interaction.response.send_message("**Up next:**\n" + "\n".join(lines) + more, ephemeral=True)


async def _refresh_menu(guild: discord.Guild, state: GuildMusicState):
    if state.menu_message is None:
        return
    try:
        await state.menu_message.edit(embed=_build_embed(state), view=MusicMenuView(guild.id))
    except (discord.NotFound, discord.HTTPException):
        state.menu_message = None
        _cancel_refresh_task(state)


def _ensure_refresh_task(guild: discord.Guild, state: GuildMusicState):
    if state.refresh_task and not state.refresh_task.done():
        return
    state.refresh_task = asyncio.create_task(_refresh_loop(guild, state))


def _cancel_refresh_task(state: GuildMusicState):
    if state.refresh_task and not state.refresh_task.done():
        state.refresh_task.cancel()
    state.refresh_task = None


async def _refresh_loop(guild: discord.Guild, state: GuildMusicState):
    try:
        while state.current is not None and state.menu_message is not None:
            await asyncio.sleep(MENU_REFRESH_SECONDS)
            if state.current is None or state.menu_message is None:
                break
            await _refresh_menu(guild, state)
    except asyncio.CancelledError:
        pass


async def _show_menu(channel, guild: discord.Guild, state: GuildMusicState):
    state.menu_message = await channel.send(embed=_build_embed(state), view=MusicMenuView(guild.id))
    _ensure_refresh_task(guild, state)


def _cancel_idle_disconnect(state: GuildMusicState):
    if state.idle_task and not state.idle_task.done():
        state.idle_task.cancel()
    state.idle_task = None


def _schedule_idle_disconnect(guild: discord.Guild, voice_client: discord.VoiceClient):
    state = _state(guild.id)
    _cancel_idle_disconnect(state)

    async def _watch():
        try:
            await asyncio.sleep(IDLE_DISCONNECT_SECONDS)
            if state.current is None and guild.voice_client:
                await guild.voice_client.disconnect(force=True)
                voice_owner.release(guild.id)
                _cancel_refresh_task(state)
                if state.menu_message:
                    try:
                        idle_embed = discord.Embed(
                            title="Disconnected",
                            description=f"Left voice after {IDLE_DISCONNECT_SECONDS // 60} minutes of inactivity.",
                            color=discord.Color.dark_grey(),
                        )
                        await state.menu_message.edit(embed=idle_embed, view=None)
                    except (discord.NotFound, discord.HTTPException):
                        pass
                state.menu_message = None
        except asyncio.CancelledError:
            pass

    state.idle_task = asyncio.create_task(_watch())


# ==================== extraction & playback ====================

async def _maybe_resolve_spotify(query: str) -> str:
    """If `query` is a Spotify link, resolves it to a "song — artist"
    search string via Spotify's own public oEmbed endpoint (an official,
    documented, no-API-key-needed endpoint — not an unofficial scrape).
    Playback still happens through YouTube either way: Spotify's actual
    audio streams are DRM-protected and no bot can play them directly.
    Non-Spotify queries pass through unchanged."""
    if "open.spotify.com" not in query:
        return query

    if _SPOTIFY_OTHER_RE.search(query) and not _SPOTIFY_TRACK_RE.search(query):
        raise ValueError(
            "Only single Spotify track links are supported — album/playlist links would need "
            "Spotify API credentials this bot doesn't have."
        )
    if not _SPOTIFY_TRACK_RE.search(query):
        return query  # some other spotify.com URL shape — let yt-dlp's normal error handling take it

    oembed_url = f"https://open.spotify.com/oembed?url={urllib.parse.quote(query, safe='')}"
    loop = asyncio.get_event_loop()

    def _fetch():
        req = urllib.request.Request(oembed_url, headers={"User-Agent": "discord-bot-control-panel"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read().decode("utf-8"))

    try:
        data = await loop.run_in_executor(None, _fetch)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        raise ValueError("Couldn't look up that Spotify link right now — try again in a bit.")

    title = (data.get("title") or "").strip()
    if not title:
        raise ValueError("Couldn't read that Spotify link.")
    return title


async def _extract(query: str, background: bool = False):
    loop = asyncio.get_event_loop()

    def _run():
        if background:
            try:
                # An absolute set, not a relative nudge like os.nice() —
                # matters because executor threads are pooled/reused, and
                # os.nice()'s "add N to whatever it already is" would stack
                # higher every time a prefetch happens to reuse the same
                # thread over a long-running session. PRIO_PROCESS + who=0
                # is thread-scoped on Linux (verified: only the calling
                # thread's niceness changes, not the whole process).
                os.setpriority(os.PRIO_PROCESS, 0, PREFETCH_NICENESS)
            except (AttributeError, OSError):
                pass  # not available on this platform — fine, just skip the deprioritization
        with yt_dlp.YoutubeDL(YTDLP_OPTIONS) as ydl:
            info = ydl.extract_info(query, download=False)
            if info and "entries" in info:
                # A search can legitimately come back empty (typo, no
                # matches, region-locked results all filtered out) — that's
                # not an error condition to crash on, just nothing found.
                entries = [e for e in (info.get("entries") or []) if e]
                if not entries:
                    raise ValueError(f"No results found for “{query}”.")
                info = entries[0]
            if not info or not info.get("url"):
                raise ValueError(f"No results found for “{query}”.")
            return {
                "title": info.get("title", query),
                "url": info["url"],
                "duration": info.get("duration"),
                "http_headers": info.get("http_headers") or {},
                # The specific video's own stable URL — re-extracting via
                # THIS instead of the original free-text query next time
                # guarantees we land on the exact same video again. A
                # generic search re-run later (for a loop repeat, a
                # restart, a prefetch) can come back with a different
                # upload that happens to share the same title.
                "webpage_url": info.get("webpage_url") or None,
            }

    return await loop.run_in_executor(None, _run)


def _extract_target(track: dict) -> str:
    """What to pass to _extract() when RE-resolving a track that's already
    been played at least once — the stable per-video URL once we have it,
    not the original free-text query, so a repeat (loop mode), a restart
    (effect toggle), or a prefetch always lands on the same video instead
    of whatever a fresh text search happens to turn up this time."""
    return track.get("webpage_url") or track["query"]


def _mix_pcm(a: bytes, b: bytes, t: float) -> bytes:
    """Linearly blends two 16-bit stereo PCM frames: `a` at (1-t) gain,
    `b` at t gain. Used one 20ms frame at a time to crossfade between an
    outgoing and incoming track's audio."""
    if len(a) < len(b):
        a = a + b"\x00" * (len(b) - len(a))
    elif len(b) < len(a):
        b = b + b"\x00" * (len(a) - len(b))
    samples_a = array.array("h")
    samples_a.frombytes(a)
    samples_b = array.array("h")
    samples_b.frombytes(b)
    out_gain = 1.0 - t
    mixed = array.array("h", (
        max(-32768, min(32767, int(sa * out_gain + sb * t)))
        for sa, sb in zip(samples_a, samples_b)
    ))
    return mixed.tobytes()


class _CrossfadeSource(discord.AudioSource):
    """Wraps an outgoing and incoming PCMVolumeTransformer and blends them
    frame-by-frame for `fade_frames` reads (20ms each), then reads purely
    from `incoming` from then on. Meant to be hot-swapped into a live
    VoiceClient via `voice_client.source = ...` — NOT via stop()/play(),
    since VoiceClient.stop() cleans up (kills) the outgoing source's ffmpeg
    process, which would defeat the whole point of crossfading into it
    without a gap. Swapping `.source` on a running AudioPlayer just changes
    what the next read() pulls from, with no interruption."""

    def __init__(self, outgoing: discord.PCMVolumeTransformer, incoming: discord.PCMVolumeTransformer, fade_frames: int):
        self.outgoing = outgoing
        self.incoming = incoming
        self.fade_frames = max(1, fade_frames)
        self._frame = 0
        self._done = False

    @property
    def volume(self) -> float:
        return self.incoming.volume

    @volume.setter
    def volume(self, value: float):
        self.outgoing.volume = value
        self.incoming.volume = value

    def read(self) -> bytes:
        if self._done:
            return self.incoming.read()

        out_data = self.outgoing.read()
        in_data = self.incoming.read()
        if not in_data:
            # the incoming track failed or was somehow shorter than the
            # fade window — fall back to the outgoing track rather than
            # cutting to silence.
            self._done = True
            return out_data
        if not out_data:
            self._done = True
            return in_data

        t = min(1.0, self._frame / self.fade_frames)
        mixed = _mix_pcm(out_data, in_data, t)
        self._frame += 1
        if self._frame >= self.fade_frames:
            self._done = True
        return mixed

    def is_opus(self) -> bool:
        return False

    def cleanup(self):
        for src in (self.outgoing, self.incoming):
            try:
                src.cleanup()
            except Exception:
                pass


def _apply_effect_live(guild: discord.Guild, voice_client: discord.VoiceClient, state: "GuildMusicState") -> bool:
    """Swaps in a freshly-filtered source for the currently playing track
    without ever stopping playback. No re-extraction needed — the track is
    already streaming right now, so its "url"/"http_headers" are still
    good — just a new ffmpeg process seeked to the current position with
    the new filter, hot-swapped in via the same _CrossfadeSource trick
    real track-to-track crossfades use (a VoiceClient.source assignment,
    not stop()/play()). The old source keeps playing right up until the
    swap completes, so nothing ever pauses or restarts."""
    track = state.current
    if not track or not state.source:
        return False

    before_options = FFMPEG_OPTIONS["before_options"]
    headers = track.get("http_headers") or {}
    if headers:
        header_str = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
        before_options += f' -headers "{header_str}"'
    seek = _elapsed(state)
    if seek > 0:
        before_options += f" -ss {seek:.2f}"

    options = FFMPEG_OPTIONS["options"]
    effect_filter = _effect_filter_for(state)
    if effect_filter:
        options += f' -af "{effect_filter}"'

    incoming = discord.PCMVolumeTransformer(
        discord.FFmpegPCMAudio(track["url"], before_options=before_options, options=options),
        volume=state.volume,
    )

    fade_frames = max(1, round(EFFECT_SWAP_FADE_SECONDS * 50))  # 50 frames/sec — 20ms each
    mix = _CrossfadeSource(state.source, incoming, fade_frames)
    voice_client.source = mix
    state.source = mix
    return True


def _schedule_crossfade(guild: discord.Guild, voice_client: discord.VoiceClient, state: "GuildMusicState", track: dict):
    """Arms a timer to start crossfading into whatever's queued next,
    `state.crossfade_seconds` before `track` would naturally end. Safe to
    call any time playback (re)starts or the crossfade duration changes —
    always cancels whatever was scheduled before it. Deliberately doesn't
    check `state.queue` here (it can gain an item — someone queuing a song
    from the web mid-track — between now and when the timer fires); that
    check happens at fire time instead, in _do_crossfade."""
    _cancel_crossfade_task(state)
    if state.crossfade_seconds <= 0:
        return
    duration = track.get("duration") if track else None
    if not duration:
        return
    delay = duration - state.crossfade_seconds - _elapsed(state)
    if delay <= 0.5:
        return
    state.crossfade_task = asyncio.create_task(_crossfade_watch(guild, voice_client, state, track, delay))


async def _crossfade_watch(guild: discord.Guild, voice_client: discord.VoiceClient, state: "GuildMusicState", track: dict, delay: float):
    try:
        await asyncio.sleep(delay)
    except asyncio.CancelledError:
        return
    if state.current is not track or state.pending_restart:
        return
    await _do_crossfade(guild, voice_client, state)


async def _do_crossfade(guild: discord.Guild, voice_client: discord.VoiceClient, state: "GuildMusicState"):
    # Same loop-mode requeue _play_next does for the track that's ending —
    # done here too since crossfade bypasses _play_next entirely. Without
    # this, loop mode would silently stop working the moment a crossfade
    # happens instead of a hard cut (the just-played track just vanished
    # instead of going back in the queue).
    outgoing_track = state.current
    if state.loop_mode == "track" and outgoing_track:
        outgoing_track.pop("_prefetch", None)
        state.queue.insert(0, outgoing_track)
    elif state.loop_mode == "queue" and outgoing_track:
        outgoing_track.pop("_prefetch", None)
        state.queue.append(outgoing_track)

    if not state.queue:
        return
    next_track = state.queue[0]

    prefetch = next_track.get("_prefetch")
    if prefetch and prefetch.get("url") and (time.monotonic() - prefetch.get("at", 0)) < PREFETCH_MAX_AGE_SECONDS:
        fresh = prefetch
    else:
        try:
            fresh = await _extract(_extract_target(next_track))
        except Exception:
            return  # best-effort — the natural end-of-track flow still covers this song normally

    # something else (a skip, a stop, an effect toggle) may have taken over
    # while we were awaiting extraction above — if the currently-live
    # source isn't the one we started this crossfade from, bail rather
    # than stomp on whatever's playing now.
    if voice_client.source is not state.source or not state.queue or state.queue[0] is not next_track:
        return

    next_track["url"] = fresh["url"]
    next_track["http_headers"] = fresh.get("http_headers") or {}
    next_track["webpage_url"] = fresh.get("webpage_url") or next_track.get("webpage_url")

    before_options = FFMPEG_OPTIONS["before_options"]
    headers = next_track.get("http_headers") or {}
    if headers:
        header_str = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
        before_options += f' -headers "{header_str}"'
    options = FFMPEG_OPTIONS["options"]
    effect_filter = _effect_filter_for(state)
    if effect_filter:
        options += f' -af "{effect_filter}"'

    incoming = discord.PCMVolumeTransformer(
        discord.FFmpegPCMAudio(next_track["url"], before_options=before_options, options=options),
        volume=state.volume,
    )

    fade_frames = max(1, round(state.crossfade_seconds * 50))  # 50 frames/sec — 20ms each
    mix = _CrossfadeSource(state.source, incoming, fade_frames)
    voice_client.source = mix
    state.source = mix

    state.queue.pop(0)
    next_track.pop("_prefetch", None)
    state.current = next_track
    _reset_position(state)

    await _refresh_menu(guild, state)
    _schedule_crossfade(guild, voice_client, state, next_track)
    if state.queue:
        asyncio.create_task(_prefetch_next(guild.id))


def _start_source(guild: discord.Guild, voice_client: discord.VoiceClient, state: "GuildMusicState",
                   track: dict, seek_seconds: float = 0.0):
    """Builds and starts playback for `track` (which must already have a
    fresh "url"/"http_headers"). Shared by normal advancement, effect
    restarts, and anything else that needs to (re)start a source."""
    before_options = FFMPEG_OPTIONS["before_options"]
    headers = track.get("http_headers") or {}
    if headers:
        header_str = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
        before_options += f' -headers "{header_str}"'
    if seek_seconds > 0:
        before_options += f" -ss {seek_seconds:.2f}"

    options = FFMPEG_OPTIONS["options"]
    effect_filter = _effect_filter_for(state)
    if effect_filter:
        options += f' -af "{effect_filter}"'

    source = discord.PCMVolumeTransformer(
        discord.FFmpegPCMAudio(track["url"], before_options=before_options, options=options),
        volume=state.volume,
    )
    state.source = source

    def _after(_error):
        if state.pending_restart:
            restart = state.pending_restart
            state.pending_restart = None
            _cancel_crossfade_task(state)
            fut = asyncio.run_coroutine_threadsafe(
                _resume_after_restart(guild, voice_client, state, restart["track"], restart["elapsed"]),
                voice_client.loop,
            )
        else:
            fut = asyncio.run_coroutine_threadsafe(_play_next(guild, voice_client), voice_client.loop)
        try:
            fut.result()
        except Exception:
            pass

    voice_client.play(source, after=_after)
    _schedule_crossfade(guild, voice_client, state, track)
    if state.queue:
        asyncio.create_task(_prefetch_next(guild.id))


async def _prefetch_next(guild_id: int):
    """Resolves a fresh stream URL for the next-up track ahead of time, so
    the gap between songs is just whatever's left of _extract's latency
    instead of the full lookup — kicked off right as the current track
    starts, giving it the whole rest of that track to finish quietly in
    the background. Waits PREFETCH_DELAY_SECONDS first and runs the actual
    extraction at a lower thread priority so it doesn't compete with the
    audio player thread for CPU right when it matters most (see the
    constants above) — a prefetch that starts a few seconds late is still
    miles ahead of not prefetching at all."""
    state = _states.get(guild_id)
    if not state or not state.queue:
        return
    upcoming = state.queue[0]
    if upcoming.get("_prefetch") is not None:
        return
    upcoming["_prefetch"] = {"status": "pending"}

    try:
        await asyncio.sleep(PREFETCH_DELAY_SECONDS)
    except asyncio.CancelledError:
        upcoming["_prefetch"] = None
        return
    # the track that was upcoming when we started sleeping might not be
    # anymore (skip/reorder while we waited) — bail rather than prefetch
    # the wrong thing.
    if not state.queue or state.queue[0] is not upcoming:
        return

    try:
        fresh = await _extract(_extract_target(upcoming), background=True)
        upcoming["_prefetch"] = {
            "url": fresh["url"],
            "http_headers": fresh.get("http_headers") or {},
            "webpage_url": fresh.get("webpage_url") or upcoming.get("webpage_url"),
            "at": time.monotonic(),
        }
    except Exception:
        upcoming["_prefetch"] = None


async def _play_next(guild: discord.Guild, voice_client: discord.VoiceClient):
    state = _state(guild.id)
    _cancel_crossfade_task(state)  # whatever was scheduled for the track that just ended is stale now

    if state.loop_mode == "track" and state.current:
        state.current.pop("_prefetch", None)
        state.queue.insert(0, state.current)
    elif state.loop_mode == "queue" and state.current:
        state.current.pop("_prefetch", None)
        state.queue.append(state.current)

    if not state.queue:
        state.current = None
        state.source = None
        await _refresh_menu(guild, state)
        _schedule_idle_disconnect(guild, voice_client)
        return

    _cancel_idle_disconnect(state)
    track = state.queue.pop(0)

    # A prefetch resolved while the previous track was still playing saves
    # this from having to block on a fresh lookup at all. Otherwise (no
    # prefetch, it failed, or it's stale enough to distrust) fall back to
    # resolving right now — same reliability guarantee as before, just not
    # always on the critical path anymore.
    prefetch = track.pop("_prefetch", None)
    if prefetch and prefetch.get("url") and (time.monotonic() - prefetch.get("at", 0)) < PREFETCH_MAX_AGE_SECONDS:
        fresh = prefetch
    else:
        try:
            fresh = await _extract(_extract_target(track))
        except Exception:
            if state.text_channel:
                try:
                    await state.text_channel.send(f"Couldn't play **{track['title']}** — skipping.")
                except discord.HTTPException:
                    pass
            await _play_next(guild, voice_client)
            return

    track["url"] = fresh["url"]
    track["http_headers"] = fresh.get("http_headers") or {}
    track["webpage_url"] = fresh.get("webpage_url") or track.get("webpage_url")
    state.current = track
    _reset_position(state)

    _start_source(guild, voice_client, state, track)
    await _refresh_menu(guild, state)


async def _resume_after_restart(guild: discord.Guild, voice_client: discord.VoiceClient,
                                 state: "GuildMusicState", track: dict, elapsed: float):
    """Continuation after an internal (non-advancing) stop — e.g. an effect
    toggle — restarts the same track from where it left off, once the old
    player has actually finished tearing down."""
    _cancel_crossfade_task(state)
    try:
        fresh = await _extract(_extract_target(track))
    except Exception:
        if state.text_channel:
            try:
                await state.text_channel.send(f"Couldn't reapply that to **{track['title']}** — stopped.")
            except discord.HTTPException:
                pass
        state.current = None
        state.source = None
        await _refresh_menu(guild, state)
        return

    track["url"] = fresh["url"]
    track["http_headers"] = fresh.get("http_headers") or {}
    track["webpage_url"] = fresh.get("webpage_url") or track.get("webpage_url")
    state.current = track
    state.position = elapsed
    state.resumed_at = time.monotonic()
    state.is_paused = False

    _start_source(guild, voice_client, state, track, seek_seconds=elapsed)
    await _refresh_menu(guild, state)


# ==================== chat commands ====================

async def _resolve_music_channel(ctx):
    """The server's configured music voice channel, or None with an error
    already sent. !join/!play always target this channel — not wherever
    the invoking member happens to be sitting — since it's meant to be one
    fixed, predictable spot per server."""
    channel_id = guild_settings.get_music_channel(ctx.guild.id)
    channel = ctx.guild.get_channel(channel_id) if channel_id else None
    if not channel or not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
        await ctx.send("The configured music channel couldn't be found — pick one again from the Control Deck web UI.")
        return None
    return channel


async def _cmd_join(ctx):
    reason = _unavailable_reason()
    if reason:
        await ctx.send(reason)
        return
    if not ctx.guild:
        await ctx.send("This only works in a server.")
        return
    channel = await _resolve_music_channel(ctx)
    if not channel:
        return
    if ctx.guild.voice_client:
        await ctx.guild.voice_client.move_to(channel)
    else:
        vc = await channel.connect()
        voice_owner.claim(ctx.guild.id, "music")
        _schedule_idle_disconnect(ctx.guild, vc)
    await ctx.send(f"Joined **{channel.name}**.")


async def _cmd_leave(ctx):
    state = _state(ctx.guild.id) if ctx.guild else None
    if ctx.guild and ctx.guild.voice_client:
        if state:
            state.pending_restart = None
            _cancel_crossfade_task(state)
        await ctx.guild.voice_client.disconnect(force=True)
        voice_owner.release(ctx.guild.id)
        if state:
            state.queue.clear()
            state.current = None
            state.source = None
            state.loop_mode = "off"
            _cancel_refresh_task(state)
            _cancel_idle_disconnect(state)
            state.menu_message = None
        await ctx.send("Left the voice channel.")
    else:
        await ctx.send("I'm not in a voice channel.")


async def _cmd_play(ctx):
    reason = _unavailable_reason()
    if reason:
        await ctx.send(reason)
        return
    if not ctx.guild:
        await ctx.send("This only works in a server.")
        return
    if not ctx.content:
        await ctx.send("Usage: `!play <song name, e.g. \"Song Title by Artist\">`, a YouTube link, or a Spotify track link")
        return

    vc = ctx.guild.voice_client
    if not vc:
        channel = await _resolve_music_channel(ctx)
        if not channel:
            return
        vc = await channel.connect()
        voice_owner.claim(ctx.guild.id, "music")

    state = _state(ctx.guild.id)
    state.text_channel = ctx.channel
    await ctx.send("Looking that up...")
    try:
        query = await _maybe_resolve_spotify(ctx.content)
        track = await _extract(query)
    except Exception as exc:  # noqa: BLE001
        await ctx.send(f"Couldn't find that: {exc}")
        return
    track["requester"] = str(ctx.author)
    track["query"] = query
    state.queue.append(track)

    if not vc.is_playing() and not vc.is_paused():
        await _play_next(ctx.guild, vc)
        await _show_menu(ctx.channel, ctx.guild, state)
    else:
        await ctx.send(f"Queued **{track['title']}** (position {len(state.queue)}).")


async def _cmd_menu(ctx):
    if not ctx.guild:
        await ctx.send("This only works in a server.")
        return
    await _show_menu(ctx.channel, ctx.guild, _state(ctx.guild.id))


async def _cmd_pause(ctx):
    vc = ctx.guild.voice_client if ctx.guild else None
    state = _state(ctx.guild.id) if ctx.guild else None
    if vc and state and vc.is_playing():
        vc.pause()
        state.position = _elapsed(state)
        state.is_paused = True
        await _refresh_menu(ctx.guild, state)
        await ctx.send("Paused.")
    else:
        await ctx.send("Nothing is playing.")


async def _cmd_resume(ctx):
    vc = ctx.guild.voice_client if ctx.guild else None
    state = _state(ctx.guild.id) if ctx.guild else None
    if vc and state and vc.is_paused():
        vc.resume()
        state.is_paused = False
        state.resumed_at = time.monotonic()
        await _refresh_menu(ctx.guild, state)
        await ctx.send("Resumed.")
    else:
        await ctx.send("Nothing is paused.")


async def _cmd_skip(ctx):
    vc = ctx.guild.voice_client if ctx.guild else None
    state = _state(ctx.guild.id) if ctx.guild else None
    if vc and (vc.is_playing() or vc.is_paused()):
        if state:
            state.pending_restart = None
            _cancel_crossfade_task(state)
        vc.stop()  # triggers _after -> _play_next
        await ctx.send("Skipped.")
    else:
        await ctx.send("Nothing is playing.")


async def _cmd_stop(ctx):
    vc = ctx.guild.voice_client if ctx.guild else None
    state = _state(ctx.guild.id) if ctx.guild else None
    if vc and state:
        state.pending_restart = None
        _cancel_crossfade_task(state)
        state.queue.clear()
        state.loop_mode = "off"
        state.current = None
        vc.stop()
        await ctx.send("Stopped and cleared the queue.")
    else:
        await ctx.send("I'm not in a voice channel.")


async def _cmd_queue(ctx):
    if not ctx.guild:
        await ctx.send("This only works in a server.")
        return
    state = _state(ctx.guild.id)
    lines = []
    if state.current:
        lines.append(f"**Now playing:** {state.current['title']}")
    if state.queue:
        lines.append("**Up next:**")
        lines += [f"{i + 1}. {t['title']}" for i, t in enumerate(state.queue[:10])]
    if not lines:
        lines = ["Queue is empty."]
    await ctx.send("\n".join(lines))


# (description, handler, required_perm) — merged into bot_commands.BUILTIN_COMMANDS
MUSIC_COMMANDS = {
    "join": ("Joins the server's configured music voice channel.", _cmd_join, None),
    "leave": ("Leaves the voice channel.", _cmd_leave, None),
    "play": ("Plays a song by name (optionally \"by <artist>\"), YouTube link, or Spotify track link — queues if already playing.", _cmd_play, None),
    "menu": ("Shows the interactive now-playing menu.", _cmd_menu, None),
    "pause": ("Pauses the current track.", _cmd_pause, None),
    "resume": ("Resumes the paused track.", _cmd_resume, None),
    "skip": ("Skips to the next track in queue.", _cmd_skip, None),
    "stop": ("Stops playback and clears the queue.", _cmd_stop, None),
    "queue": ("Shows what's playing and queued next.", _cmd_queue, None),
}


# ==================== web UI bridge (called from app.py, off the loop thread) ====================

def get_state_dict(client: discord.Client, guild_id: str) -> dict:
    guild = client.get_guild(int(guild_id)) if client else None
    vc = guild.voice_client if guild else None
    state = _state(int(guild_id))
    track = state.current
    return {
        "available": _unavailable_reason() is None,
        "connected": vc is not None,
        "playing": bool(track) and not state.is_paused,
        "paused": state.is_paused,
        "title": track["title"] if track else None,
        "requester": track.get("requester") if track else None,
        "elapsed": round(_elapsed(state), 1),
        "duration": track.get("duration") if track else None,
        "volume": round(state.volume * 100),
        "loop_mode": state.loop_mode,
        "effect_mode": state.effect_mode,
        "effect_modes": [{"id": m, "label": EFFECT_LABELS[m]} for m in EFFECT_MODES],
        "effect_param_specs": EFFECT_PARAM_SPECS,
        "effect_params": _effect_params_for(state),
        "effect_tied_modes": sorted(EFFECT_TIED_MODES),
        "custom_tied": state.custom_tied,
        "crossfade_seconds": state.crossfade_seconds,
        "crossfade_min": CROSSFADE_MIN_SECONDS,
        "crossfade_max": CROSSFADE_MAX_SECONDS,
        "queue": [t["title"] for t in state.queue[:10]],
        "queue_length": len(state.queue),
    }


def _nudge_menu(client: discord.Client, guild_id: int):
    """Fire-and-forget: refresh the Discord menu embed from off the event loop thread."""
    guild = client.get_guild(guild_id) if client else None
    if not (guild and client and client.loop):
        return
    state = _state(guild_id)
    if state.menu_message is None:
        return
    try:
        asyncio.run_coroutine_threadsafe(_refresh_menu(guild, state), client.loop)
    except RuntimeError:
        pass


def web_pause(client: discord.Client, guild_id: str) -> dict:
    guild = client.get_guild(int(guild_id)) if client else None
    vc = guild.voice_client if guild else None
    state = _state(int(guild_id))
    if not (vc and vc.is_playing()):
        return {"ok": False, "error": "Nothing is playing."}
    vc.pause()
    state.position = _elapsed(state)
    state.is_paused = True
    _nudge_menu(client, int(guild_id))
    return {"ok": True}


def web_resume(client: discord.Client, guild_id: str) -> dict:
    guild = client.get_guild(int(guild_id)) if client else None
    vc = guild.voice_client if guild else None
    state = _state(int(guild_id))
    if not (vc and vc.is_paused()):
        return {"ok": False, "error": "Nothing is paused."}
    vc.resume()
    state.is_paused = False
    state.resumed_at = time.monotonic()
    _nudge_menu(client, int(guild_id))
    return {"ok": True}


def web_skip(client: discord.Client, guild_id: str) -> dict:
    guild = client.get_guild(int(guild_id)) if client else None
    vc = guild.voice_client if guild else None
    if not (vc and (vc.is_playing() or vc.is_paused())):
        return {"ok": False, "error": "Nothing is playing."}
    state = _state(int(guild_id))
    state.pending_restart = None
    _cancel_crossfade_task(state)
    vc.stop()
    return {"ok": True}


def web_stop(client: discord.Client, guild_id: str) -> dict:
    guild = client.get_guild(int(guild_id)) if client else None
    vc = guild.voice_client if guild else None
    state = _state(int(guild_id))
    if not vc:
        return {"ok": False, "error": "I'm not in a voice channel."}
    state.pending_restart = None
    _cancel_crossfade_task(state)
    state.queue.clear()
    state.loop_mode = "off"
    state.current = None
    vc.stop()
    _nudge_menu(client, int(guild_id))
    return {"ok": True}


def web_volume(client: discord.Client, guild_id: str, delta: float) -> dict:
    state = _state(int(guild_id))
    if not state.current:
        return {"ok": False, "error": "Nothing is playing."}
    _adjust_volume(state, delta)
    _nudge_menu(client, int(guild_id))
    return {"ok": True, "volume": round(state.volume * 100)}


def web_loop_cycle(client: discord.Client, guild_id: str) -> dict:
    state = _state(int(guild_id))
    state.loop_mode = LOOP_MODES[(LOOP_MODES.index(state.loop_mode) + 1) % len(LOOP_MODES)]
    _nudge_menu(client, int(guild_id))
    return {"ok": True, "loop_mode": state.loop_mode}


def web_set_effect(client: discord.Client, guild_id: str, mode: str) -> dict:
    """Changes the effect for future tracks, and — if something's playing
    right now — hot-swaps it in immediately (see _apply_effect_live), so
    the change is heard right away without pausing or restarting the
    track."""
    if mode not in EFFECT_MODES:
        return {"ok": False, "error": "Unknown effect mode."}
    guild = client.get_guild(int(guild_id)) if client else None
    state = _state(int(guild_id))
    if mode == state.effect_mode:
        return {"ok": True, "effect_mode": mode}
    state.effect_mode = mode

    vc = guild.voice_client if guild else None
    if vc:
        _apply_effect_live(guild, vc, state)
    return {"ok": True, "effect_mode": mode}


def web_set_effect_params(client: discord.Client, guild_id: str, params: dict, tied=None) -> dict:
    """Updates the currently-active effect's sliders (e.g. how much reverb,
    how fast custom mode's speed is) and, same as web_set_effect, hot-swaps
    the change into the currently playing track immediately. Only ever
    touches the mode that's actually active — sliders for other modes are
    just remembered for next time they're selected, no swap needed."""
    guild = client.get_guild(int(guild_id)) if client else None
    state = _state(int(guild_id))
    mode = state.effect_mode
    if mode == "off":
        return {"ok": False, "error": "No effect is active."}
    specs = EFFECT_PARAM_SPECS.get(mode)
    if not specs:
        return {"ok": False, "error": "This effect has nothing to tweak."}

    clamped = {s["id"]: _param((params or {}).get(s["id"]), s["min"], s["max"], s["default"]) for s in specs}
    state.effect_params[mode] = clamped
    if mode in EFFECT_TIED_MODES and tied is not None:
        state.custom_tied = bool(tied)

    vc = guild.voice_client if guild else None
    if vc:
        _apply_effect_live(guild, vc, state)
    return {"ok": True, "effect_params": clamped, "custom_tied": state.custom_tied}


def web_set_crossfade(client: discord.Client, guild_id: str, seconds) -> dict:
    """Sets how many seconds of overlap to blend between a track ending
    and the next one starting. 0 disables it. Reschedules immediately
    against whatever's currently playing so a mid-song slider change takes
    effect for the very next transition, not just future ones."""
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return {"ok": False, "error": "Bad crossfade value."}
    seconds = max(CROSSFADE_MIN_SECONDS, min(CROSSFADE_MAX_SECONDS, seconds))
    state = _state(int(guild_id))
    state.crossfade_seconds = seconds

    guild = client.get_guild(int(guild_id)) if client else None
    vc = guild.voice_client if guild else None
    if vc and state.current:
        _schedule_crossfade(guild, vc, state, state.current)
    return {"ok": True, "crossfade_seconds": state.crossfade_seconds}


def web_remove_from_queue(client: discord.Client, guild_id: str, index) -> dict:
    """Removes a single queued (not yet playing) track by its 1-indexed
    position, e.g. from the Music tab's queue list."""
    state = _state(int(guild_id))
    try:
        idx = int(index) - 1
    except (TypeError, ValueError):
        return {"ok": False, "error": "Bad queue position."}
    if not (0 <= idx < len(state.queue)):
        return {"ok": False, "error": "No song at that queue position."}
    removed = state.queue.pop(idx)
    _nudge_menu(client, int(guild_id))
    return {"ok": True, "removed": removed["title"]}


def _run_coro_threadsafe(client: discord.Client, coro, timeout: float = 20):
    """Bridges a coroutine from the Flask (non-loop) thread onto the bot's
    own event loop and blocks for the result — mirrors bot_manager.py's
    _run_coro. Every other web_*() function in this module only ever
    touches plain state or calls thread-safe discord.py methods (vc.pause()
    and friends), so this is needed only by web_play, which is the one
    that has to await real async work: connecting to voice and resolving
    the track."""
    if not (client and client.loop):
        coro.close()  # avoid "coroutine was never awaited" — built but unused
        return None
    future = asyncio.run_coroutine_threadsafe(coro, client.loop)
    try:
        return future.result(timeout=timeout)
    except Exception:
        return None


def web_play(client: discord.Client, guild_id: str, query: str) -> dict:
    """Queues (or immediately plays, if nothing's currently playing) a
    song from the web UI — the same resolve-then-queue-or-play logic as
    !play, just without a ctx to reply through (errors come back in the
    JSON response instead) or a text channel to post the interactive
    now-playing menu in — the Music tab's own polling covers that job."""
    reason = _unavailable_reason()
    if reason:
        return {"ok": False, "error": reason}
    guild = client.get_guild(int(guild_id)) if client else None
    if not guild:
        return {"ok": False, "error": "Server not found — is the bot still in it?"}
    query = (query or "").strip()
    if not query:
        return {"ok": False, "error": "Type a song name or paste a link first."}
    if voice_owner.get(guild.id) == "tts":
        return {"ok": False, "error": "TTS is on right now — turn it off first if you want music."}

    async def _do():
        vc = guild.voice_client
        if not vc:
            channel_id = guild_settings.get_music_channel(guild.id)
            channel = guild.get_channel(channel_id) if channel_id else None
            if not channel or not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
                return {"ok": False, "error": "No music voice channel set yet — pick one on the Mod tab."}
            try:
                vc_local = await channel.connect()
            except discord.ClientException:
                return {"ok": False, "error": "Already connected to a voice channel here."}
            voice_owner.claim(guild.id, "music")
        else:
            vc_local = vc

        state = _state(guild.id)
        try:
            resolved_query = await _maybe_resolve_spotify(query)
            track = await _extract(resolved_query)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"Couldn't find that: {exc}"}
        track["requester"] = "Control Deck (web)"
        track["query"] = resolved_query
        state.queue.append(track)

        already_playing = vc_local.is_playing() or vc_local.is_paused()
        if not already_playing:
            await _play_next(guild, vc_local)
        return {
            "ok": True,
            "title": track["title"],
            "queued": already_playing,
            "queue_position": len(state.queue) if already_playing else None,
        }

    result = _run_coro_threadsafe(client, _do())
    return result or {"ok": False, "error": "Bot isn't connected."}
