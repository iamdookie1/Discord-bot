"""
!tts — reads chat messages aloud in a voice channel, for people who'd
rather type than talk. Toggled on/off per text channel with !tts: turning
it on joins the voice channel the caller is currently in, and links it to
the text channel the command was run in. From then on, any message posted
in that text channel by someone currently sitting in that voice channel
gets synthesized and played — not the message author actually speaking,
just their typed words read aloud.

Uses espeak-ng (offline, installed via Termux's package manager) rather
than an unofficial web TTS API — this project already got burned once by
building a feature on an unofficial third-party endpoint (see the !reddit
saga in git history), so a local, no-network engine is the deliberate
choice here even though the voice is more robotic for it.

Messages are sanitized before being spoken: URLs, custom/unicode emoji,
spoiler-tagged text, and markdown syntax are stripped; mentions are
replaced with the mentioned person's display name. A message that's
nothing but a link/emoji/GIF (i.e. nothing left after sanitizing) is
silently skipped rather than read; so is one over MAX_TTS_CHARS, unless
it's from the owner — their own messages have no length cap.

Because a guild can only have one voice connection, TTS and music (see
bot_music.py) take turns — voice_owner.py is the shared registry that
lets each refuse to start while the other is active, per guild.

A handful of owner-only commands (!tone, !pitch, !onlytm, !voiceselection,
!volume) tune how it all sounds — see the "owner controls" section below.
They're dispatched specially from bot_commands.py (like RP's
!allowchannelrp) rather than through the normal TTS_COMMANDS table, so
they're silent no-ops for anyone but the owner and don't show up in !cmds.
"""
import asyncio
import collections
import functools
import json
import os
import re
import shutil
import subprocess
import tempfile
import xml.sax.saxutils

import discord

import voice_owner
from owner import OWNER_ID

try:
    import emoji as _emoji_lib
except ImportError:
    _emoji_lib = None

_HAS_ESPEAK = shutil.which("espeak-ng") is not None

MAX_TTS_CHARS = 300
SYNTHESIZE_TIMEOUT = 15

_SPOILER_RE = re.compile(r"\|\|.*?\|\|", re.DOTALL)
_URL_RE = re.compile(r"https?://\S+")
_CUSTOM_EMOJI_RE = re.compile(r"<a?:\w+:\d+>")
_MD_CHARS_RE = re.compile(r"[*_~`]")
# espeak-ng keeps a fixed internal list of short all-caps tokens it always
# spells out letter-by-letter as if they were abbreviations — "IT" becomes
# "I. T.", "HI" becomes "H. I.", etc — even though most other equally-short
# all-caps words (AT, AN, GO, IS, ...) read fine as themselves. Lowercasing
# 1-2 letter all-caps words sidesteps that list entirely without touching
# genuine 3+ letter acronyms (FBI, NASA, USA, ...), which espeak-ng spells
# out correctly on purpose.
_SHORT_CAPS_RE = re.compile(r"\b[A-Z]{1,2}\b")


# ==================== owner controls ====================
# Persisted preferences for how TTS sounds, set via the owner-only chat
# commands at the bottom of this file. All are global (not per-guild) since
# they're the owner's personal settings for how the bot talks everywhere.

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TTS_SETTINGS_PATH = os.path.join(BASE_DIR, "tts_settings.json")

_TTS_SETTINGS_DEFAULTS = {
    "voice_slot": 1,     # !voiceselection — which VOICE_SLOTS entry, applies to everyone
    "volume": 100,       # !volume — espeak-ng amplitude (0-500, 100 = normal), applies to everyone
    "rate": 175,         # speaking speed in words/minute (80-400, 175 = espeak-ng default), applies to everyone
    "tone": 5,           # !tone — pitch range/expressiveness (1-10), owner's own messages only
    "pitch": 0,          # !pitch — pitch offset (-100 to 100), owner's own messages only
    "only_me": False,    # !onlytm — when on, only the owner's messages get read at all
    # Owner-only overrides, web UI only (no chat command) — when enabled,
    # replaces "voice_slot"/"volume" above just for the owner's own
    # messages, on top of the tone/pitch personalization they already get.
    "owner_voice_override": False,
    "owner_voice_slot": 1,
    "owner_volume_override": False,
    "owner_volume": 100,
}

# All 20 of these ship inside espeak-ng-data already, so !voiceselection
# works immediately — no extra download or package needed.
VOICE_SLOTS = {
    1: ("en-us", "Default"),
    2: ("en-us+m1", "Male 1"),
    3: ("en-us+m3", "Deep Male"),
    4: ("en-us+m5", "Male 5"),
    5: ("en-us+m7", "Male 7"),
    6: ("en-us+f1", "Female 1"),
    7: ("en-us+f3", "Female 3"),
    8: ("en-us+f4", "Female 4"),
    9: ("en-us+croak", "Croaky"),
    10: ("en-us+whisper", "Whisper"),
    11: ("en-us+whisperf", "Whisper (female)"),
    12: ("en-us+klatt", "Robotic"),
    13: ("en-us+klatt3", "Robotic 3"),
    14: ("en-us+announcer", "Announcer"),
    15: ("en-us+grandma", "Grandma"),
    16: ("en-us+grandpa", "Grandpa"),
    17: ("en-us+robosoft2", "Robot"),
    18: ("en-gb", "British"),
    19: ("en-gb-scotland", "Scottish"),
    20: ("en-gb-x-rp", "Posh British"),
}


def _load_tts_settings() -> dict:
    if not os.path.exists(TTS_SETTINGS_PATH):
        return dict(_TTS_SETTINGS_DEFAULTS)
    try:
        with open(TTS_SETTINGS_PATH, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return dict(_TTS_SETTINGS_DEFAULTS)
    return {**_TTS_SETTINGS_DEFAULTS, **data}


def _save_tts_settings(data: dict):
    with open(TTS_SETTINGS_PATH, "w") as f:
        json.dump(data, f, indent=2)


def _update_tts_setting(key: str, value):
    data = _load_tts_settings()
    data[key] = value
    _save_tts_settings(data)


def _unavailable_reason():
    if not _HAS_ESPEAK:
        return "Text-to-speech needs the `espeak-ng` binary, which isn't installed. Run `bash setup.sh` again, or install it manually."
    return None


def sanitize_message(message: discord.Message):
    """Returns the text to speak for this message, or None if there's
    nothing speakable left — auto-detects and drops emoji/links/GIFs/
    spoilers rather than reading them literally. Too-long messages are
    dropped too, except for the owner's own — see MAX_TTS_CHARS."""
    text = message.content
    text = _SPOILER_RE.sub("", text)
    text = _URL_RE.sub("", text)
    text = _CUSTOM_EMOJI_RE.sub("", text)

    for user in message.mentions:
        text = text.replace(f"<@{user.id}>", user.display_name).replace(f"<@!{user.id}>", user.display_name)
    for role in getattr(message, "role_mentions", []):
        text = text.replace(f"<@&{role.id}>", f"the {role.name} role")

    if _emoji_lib is not None:
        text = _emoji_lib.replace_emoji(text, replace="")

    text = _MD_CHARS_RE.sub("", text)
    text = _SHORT_CAPS_RE.sub(lambda m: m.group(0).lower(), text)
    text = " ".join(text.split())

    if not text:
        return None
    if len(text) > MAX_TTS_CHARS and message.author.id != OWNER_ID:
        return None
    return text


def _synthesize(text: str, *, voice: str = None, amplitude: int = 100, wpm: int = None,
                 pitch_pct: int = None, range_pct: int = None):
    """Blocking — run off the event loop. Returns a temp WAV file path on
    success (caller deletes it after playback), or None on failure.

    `pitch_pct`/`range_pct` (relative pitch offset / intonation range, as a
    percent) are only ever passed for the owner's own messages — everyone
    else just gets the plain text at the configured voice + amplitude. They
    go through SSML's <prosody> tag rather than espeak-ng's own -p flag
    since -p sets an absolute baseline while prosody's pitch/range apply on
    top of whatever the voice's own default already is."""
    fd, path = tempfile.mkstemp(suffix=".wav", prefix="tts_")
    os.close(fd)
    args = ["espeak-ng", "-w", path, "-a", str(amplitude)]
    if voice:
        args += ["-v", voice]
    if wpm is not None:
        args += ["-s", str(wpm)]
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
        subprocess.run(
            args,
            capture_output=True, timeout=SYNTHESIZE_TIMEOUT, check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        if os.path.exists(path):
            os.remove(path)
        return None
    return path


async def _synthesize_async(text: str, **kwargs):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, functools.partial(_synthesize, text, **kwargs))


class _PriorityQueue:
    """A deque-backed async queue that lets the owner's messages jump to
    the front instead of waiting in line — see maybe_enqueue, which also
    interrupts whatever's currently playing so an owner message is heard
    right away instead of after everyone already ahead of them."""

    def __init__(self):
        self._items = collections.deque()
        self._event = asyncio.Event()

    def put_back(self, item):
        self._items.append(item)
        self._event.set()

    def put_front(self, item):
        self._items.appendleft(item)
        self._event.set()

    async def get(self):
        while not self._items:
            self._event.clear()
            await self._event.wait()
        return self._items.popleft()


class GuildTTSState:
    def __init__(self):
        self.text_channel_id = None
        self.voice_channel_id = None
        self.queue = _PriorityQueue()
        self.worker_task = None


_states: dict[int, GuildTTSState] = {}


def is_active(guild_id: int) -> bool:
    return guild_id in _states


async def _worker(guild: discord.Guild, state: GuildTTSState):
    loop = asyncio.get_event_loop()
    try:
        while True:
            message = await state.queue.get()
            text = sanitize_message(message)
            if not text:
                continue
            vc = guild.voice_client
            if not vc:
                break

            settings = _load_tts_settings()
            is_owner = message.author.id == OWNER_ID

            voice_key = settings["voice_slot"]
            if is_owner and settings.get("owner_voice_override"):
                voice_key = settings["owner_voice_slot"]
            voice_slot = VOICE_SLOTS.get(voice_key, VOICE_SLOTS[1])

            amplitude = settings["volume"]
            if is_owner and settings.get("owner_volume_override"):
                amplitude = settings["owner_volume"]

            synth_kwargs = {"voice": voice_slot[0], "amplitude": amplitude, "wpm": settings["rate"]}
            if is_owner:
                synth_kwargs["pitch_pct"] = settings["pitch"]
                synth_kwargs["range_pct"] = settings["tone"] * 20

            path = await _synthesize_async(text, **synth_kwargs)
            if not path:
                continue

            done = asyncio.Event()

            def _after(_error, path=path, done=done):
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass
                loop.call_soon_threadsafe(done.set)

            try:
                vc.play(discord.FFmpegPCMAudio(path), after=_after)
            except discord.ClientException:
                if os.path.exists(path):
                    os.remove(path)
                continue
            await done.wait()
    except asyncio.CancelledError:
        pass


async def _stop(guild: discord.Guild):
    state = _states.pop(guild.id, None)
    if state and state.worker_task:
        state.worker_task.cancel()
    if guild.voice_client:
        await guild.voice_client.disconnect(force=True)
    voice_owner.release(guild.id)


async def handle_toggle(ctx):
    if not ctx.guild:
        await ctx.send("This only works in a server.")
        return

    guild = ctx.guild

    if is_active(guild.id):
        await _stop(guild)
        await ctx.send("TTS turned off.")
        return

    reason = _unavailable_reason()
    if reason:
        await ctx.send(reason)
        return

    if voice_owner.get(guild.id) == "music":
        await ctx.send("Sorry, music's playing right now — stop it first if you want TTS instead.")
        return

    author_voice = getattr(ctx.author, "voice", None)
    if not author_voice or not author_voice.channel:
        await ctx.send("Join a voice channel first, then use `!tts` in the text channel you want read aloud.")
        return

    channel = author_voice.channel
    try:
        vc = await channel.connect()
    except discord.ClientException:
        await ctx.send("I'm already connected to a voice channel here.")
        return

    voice_owner.claim(guild.id, "tts")
    state = GuildTTSState()
    state.text_channel_id = ctx.channel.id
    state.voice_channel_id = channel.id
    _states[guild.id] = state
    state.worker_task = asyncio.create_task(_worker(guild, state))
    await ctx.send(
        f"TTS on — I'll read messages sent in this channel by whoever's in {channel.mention}. "
        "Use `!tts` again to stop."
    )
    _ = vc  # connection is tracked via guild.voice_client from here on


def maybe_enqueue(message: discord.Message):
    """Called from bot_manager.py's on_message for every message (not just
    commands) — enqueues it for TTS if this guild has TTS on, the message
    is in the linked text channel, and the author is currently in the
    linked voice channel.

    The owner's own messages jump straight to the front of the queue and,
    if something's already playing, interrupt it right away instead of
    waiting their turn — vc.stop() fires the current source's `after`
    callback, which is what actually unblocks _worker to pick up the next
    (now front-of-queue) item immediately."""
    if not message.guild or message.author.bot:
        return
    state = _states.get(message.guild.id)
    if not state or message.channel.id != state.text_channel_id:
        return
    if message.content.startswith("!"):
        return  # commands aren't read aloud
    is_owner = message.author.id == OWNER_ID
    if _load_tts_settings()["only_me"] and not is_owner:
        return  # !onlytm is on — only the owner's own messages get read
    vc = message.guild.voice_client
    if not vc:
        return
    author_voice = getattr(message.author, "voice", None)
    if not author_voice or not author_voice.channel or author_voice.channel.id != vc.channel.id:
        return

    if is_owner:
        state.queue.put_front(message)
        if vc.is_playing():
            vc.stop()
    else:
        state.queue.put_back(message)


# ==================== owner commands ====================
# !tone, !pitch, !onlytm, !voiceselection, !volume, !ttsrate, !myvoice,
# !myvolume, !ttsstatus, !ttsreset, !ttstest — dispatched specially from
# bot_commands.handle_message() (like RP's !allowchannelrp), not through
# TTS_COMMANDS below, so non-owners get silent no-ops and !cmds never
# lists them.

def _voice_list_text(current_slot: int) -> str:
    lines = [
        f"{n}. {label}" + (" *(current)*" if n == current_slot else "")
        for n, (_voice_id, label) in sorted(VOICE_SLOTS.items())
    ]
    return "\n".join(lines)


async def handle_tone(ctx):
    """!tone <1-10> — how much pitch variation ("sing-songy" vs flat) the
    voice uses, but only when reading a message the owner themselves sent;
    everyone else is unaffected."""
    if ctx.author.id != OWNER_ID:
        return
    settings = _load_tts_settings()
    if not ctx.args:
        await ctx.send(f"Current tone: **{settings['tone']}**/10. Usage: `!tone <1-10>` — only changes how *your own* messages sound.")
        return
    try:
        value = int(ctx.args[0])
    except ValueError:
        await ctx.send("Tone must be a whole number from 1 to 10.")
        return
    if not (1 <= value <= 10):
        await ctx.send("Tone must be between 1 and 10.")
        return
    _update_tts_setting("tone", value)
    await ctx.send(f"Tone set to **{value}**/10 — only your own messages will sound like this.")


async def handle_pitch(ctx):
    """!pitch <-100 to 100> — pitch offset, but only for the owner's own
    messages, same scoping as !tone."""
    if ctx.author.id != OWNER_ID:
        return
    settings = _load_tts_settings()
    if not ctx.args:
        await ctx.send(f"Current pitch: **{settings['pitch']:+d}**. Usage: `!pitch <-100 to 100>` — only changes how *your own* messages sound.")
        return
    try:
        value = int(ctx.args[0])
    except ValueError:
        await ctx.send("Pitch must be a whole number from -100 to 100.")
        return
    if not (-100 <= value <= 100):
        await ctx.send("Pitch must be between -100 and 100.")
        return
    _update_tts_setting("pitch", value)
    await ctx.send(f"Pitch set to **{value:+d}** — only your own messages will sound like this.")


async def handle_only_me(ctx):
    """!onlytm — toggles reading only the owner's own messages, ignoring
    everyone else currently in the linked voice channel."""
    if ctx.author.id != OWNER_ID:
        return
    settings = _load_tts_settings()
    new_value = not settings["only_me"]
    _update_tts_setting("only_me", new_value)
    if new_value:
        await ctx.send("Only-me mode on — TTS will only read messages you send from now on.")
    else:
        await ctx.send("Only-me mode off — TTS reads everyone in the linked voice channel again.")


async def handle_voice_selection(ctx):
    """!voiceselection <1-20> — which built-in espeak-ng voice TTS reads
    with. Applies to everyone, not just the owner."""
    if ctx.author.id != OWNER_ID:
        return
    settings = _load_tts_settings()
    if not ctx.args:
        current = VOICE_SLOTS.get(settings["voice_slot"], VOICE_SLOTS[1])
        await ctx.send(
            f"Current voice: **{current[1]}** (#{settings['voice_slot']}). "
            "Usage: `!voiceselection <number>` — applies to everyone.\n" + _voice_list_text(settings["voice_slot"])
        )
        return
    try:
        slot = int(ctx.args[0])
    except ValueError:
        await ctx.send("Pick a voice number — run `!voiceselection` with no number to see the list.")
        return
    if slot not in VOICE_SLOTS:
        await ctx.send(f"No voice #{slot}. Run `!voiceselection` with no number to see the list (1-{len(VOICE_SLOTS)}).")
        return
    _update_tts_setting("voice_slot", slot)
    await ctx.send(f"Voice set to **{VOICE_SLOTS[slot][1]}** — applies to everyone TTS reads from now on.")


async def handle_volume(ctx):
    """!volume <0-500> — espeak-ng amplitude, applies to everyone (100 is
    normal; above 200 gets loud/distorted fast, but espeak-ng doesn't
    reject it). Not to be confused with music's separate !volume-equivalent
    controls in the web UI — this is TTS-only."""
    if ctx.author.id != OWNER_ID:
        return
    settings = _load_tts_settings()
    if not ctx.args:
        await ctx.send(f"Current TTS volume: **{settings['volume']}**. Usage: `!volume <0-500>` — applies to everyone (100 is normal).")
        return
    try:
        value = int(ctx.args[0])
    except ValueError:
        await ctx.send("Volume must be a whole number from 0 to 500.")
        return
    if not (0 <= value <= 500):
        await ctx.send("Volume must be between 0 and 500 (100 is normal).")
        return
    _update_tts_setting("volume", value)
    await ctx.send(f"TTS volume set to **{value}** — applies to everyone.")


async def handle_rate(ctx):
    """!ttsrate <80-400> — speaking speed in words/minute, applies to
    everyone (175 is espeak-ng's own default)."""
    if ctx.author.id != OWNER_ID:
        return
    settings = _load_tts_settings()
    if not ctx.args:
        await ctx.send(f"Current TTS speed: **{settings['rate']}** wpm. Usage: `!ttsrate <80-400>` — applies to everyone (175 is normal).")
        return
    try:
        value = int(ctx.args[0])
    except ValueError:
        await ctx.send("Speed must be a whole number from 80 to 400.")
        return
    if not (80 <= value <= 400):
        await ctx.send("Speed must be between 80 and 400 (175 is normal).")
        return
    _update_tts_setting("rate", value)
    await ctx.send(f"TTS speed set to **{value}** wpm — applies to everyone.")


async def handle_my_voice(ctx):
    """!myvoice <1-20|off> — use a different voice just for the owner's own
    messages, layered on top of the base voice everyone else hears (set
    with !voiceselection). Same list, same numbers."""
    if ctx.author.id != OWNER_ID:
        return
    settings = _load_tts_settings()
    if not ctx.args:
        if settings.get("owner_voice_override"):
            current = VOICE_SLOTS.get(settings["owner_voice_slot"], VOICE_SLOTS[1])
            await ctx.send(
                f"Using a different voice for you: **{current[1]}** (#{settings['owner_voice_slot']}). "
                "Usage: `!myvoice <number>` or `!myvoice off`."
            )
        else:
            await ctx.send("Not using a different voice for you right now — same as everyone. Usage: `!myvoice <number>` or `!myvoice off`.")
        return
    if ctx.args[0].lower() == "off":
        _update_tts_setting("owner_voice_override", False)
        await ctx.send("Back to the same voice as everyone else for your messages.")
        return
    try:
        slot = int(ctx.args[0])
    except ValueError:
        await ctx.send("Pick a voice number, or `off` — run `!voiceselection` with no number to see the list.")
        return
    if slot not in VOICE_SLOTS:
        await ctx.send(f"No voice #{slot}. Run `!voiceselection` with no number to see the list (1-{len(VOICE_SLOTS)}).")
        return
    settings["owner_voice_slot"] = slot
    settings["owner_voice_override"] = True
    _save_tts_settings(settings)
    await ctx.send(f"Your messages will now sound like **{VOICE_SLOTS[slot][1]}** — everyone else still hears the normal voice.")


async def handle_my_volume(ctx):
    """!myvolume <0-500|off> — use a different volume just for the owner's
    own messages, layered on top of the base volume everyone else hears."""
    if ctx.author.id != OWNER_ID:
        return
    settings = _load_tts_settings()
    if not ctx.args:
        if settings.get("owner_volume_override"):
            await ctx.send(f"Using a different volume for you: **{settings['owner_volume']}**. Usage: `!myvolume <0-500>` or `!myvolume off`.")
        else:
            await ctx.send("Not using a different volume for you right now — same as everyone. Usage: `!myvolume <0-500>` or `!myvolume off`.")
        return
    if ctx.args[0].lower() == "off":
        _update_tts_setting("owner_volume_override", False)
        await ctx.send("Back to the same volume as everyone else for your messages.")
        return
    try:
        value = int(ctx.args[0])
    except ValueError:
        await ctx.send("Volume must be a whole number from 0 to 500, or `off`.")
        return
    if not (0 <= value <= 500):
        await ctx.send("Volume must be between 0 and 500, or `off`.")
        return
    settings["owner_volume"] = value
    settings["owner_volume_override"] = True
    _save_tts_settings(settings)
    await ctx.send(f"Your messages will now play at volume **{value}** — everyone else still hears the normal volume.")


async def handle_status(ctx):
    """!ttsstatus — every current TTS setting in one place, instead of
    checking !tone/!pitch/!volume/etc one at a time."""
    if ctx.author.id != OWNER_ID:
        return
    s = _load_tts_settings()
    voice = VOICE_SLOTS.get(s["voice_slot"], VOICE_SLOTS[1])[1]
    lines = [
        f"Voice: **{voice}** (#{s['voice_slot']})",
        f"Volume: **{s['volume']}**",
        f"Speed: **{s['rate']}** wpm",
        f"Tone (your messages only): **{s['tone']}**/10",
        f"Pitch (your messages only): **{s['pitch']:+d}**",
        f"Only-me mode: **{'on' if s['only_me'] else 'off'}**",
    ]
    if s.get("owner_voice_override"):
        owner_voice = VOICE_SLOTS.get(s["owner_voice_slot"], VOICE_SLOTS[1])[1]
        lines.append(f"Your voice override: **{owner_voice}** (#{s['owner_voice_slot']})")
    if s.get("owner_volume_override"):
        lines.append(f"Your volume override: **{s['owner_volume']}**")
    await ctx.send("\n".join(lines))


async def handle_reset(ctx):
    """!ttsreset — resets every TTS setting (voice, volume, speed, tone,
    pitch, only-me, and both owner-only overrides) back to default."""
    if ctx.author.id != OWNER_ID:
        return
    _save_tts_settings(dict(_TTS_SETTINGS_DEFAULTS))
    await ctx.send("All TTS settings reset to default.")


class _SyntheticMessage:
    """Just enough of a discord.Message's shape for sanitize_message()/the
    worker to process — used by !ttstest to speak a preview phrase without
    a real chat message existing."""

    def __init__(self, author_id: int, content: str):
        self.author = type("_Author", (), {"id": author_id, "bot": False})()
        self.content = content
        self.mentions = []
        self.role_mentions = []


async def handle_test(ctx):
    """!ttstest — speaks a short test phrase right now, using your current
    settings (including any !myvoice/!myvolume overrides), so you can
    preview a change without waiting for a real message to trigger it."""
    if ctx.author.id != OWNER_ID:
        return
    if not ctx.guild:
        await ctx.send("This only works in a server.")
        return
    state = _states.get(ctx.guild.id)
    if not state:
        await ctx.send("TTS isn't on in this server — run `!tts` first.")
        return
    vc = ctx.guild.voice_client
    test_message = _SyntheticMessage(OWNER_ID, "This is a test of your current text to speech settings.")
    state.queue.put_front(test_message)
    if vc and vc.is_playing():
        vc.stop()
    await ctx.send("Testing your current settings now.")


# (description, handler, required_perm) — merged into bot_commands.BUILTIN_COMMANDS
TTS_COMMANDS = {
    "tts": ("Toggles reading this channel's messages aloud in your voice channel.", handle_toggle, None),
}


# ==================== web bridge ====================
# Same settings as the owner-only chat commands above, but reachable from the
# web control panel with no owner-ID check — the panel is a single-operator
# surface (unlike open Discord chat), so anyone with access to it can already
# do everything else these settings affect.

TTS_SETTING_SPECS = [
    {"id": "volume", "label": "Volume", "min": 0, "max": 500, "step": 1, "unit": "%"},
    {"id": "rate", "label": "Speed", "min": 80, "max": 400, "step": 5, "unit": " wpm"},
    {"id": "tone", "label": "Tone (your messages only)", "min": 1, "max": 10, "step": 1, "unit": ""},
    {"id": "pitch", "label": "Pitch (your messages only)", "min": -100, "max": 100, "step": 5, "unit": ""},
]

# Owner-only overrides — separate from the specs above since each one is
# paired with its own on/off toggle in the web UI (owner_voice_slot only
# applies when owner_voice_override is true, same for the volume pair).
OWNER_TTS_SETTING_SPECS = [
    {"id": "owner_volume", "label": "My volume", "min": 0, "max": 500, "step": 1, "unit": "%"},
]


def web_get_tts_settings() -> dict:
    return {
        "ok": True,
        "available": _HAS_ESPEAK,
        "unavailable_reason": _unavailable_reason(),
        "settings": _load_tts_settings(),
        "specs": TTS_SETTING_SPECS,
        "owner_specs": OWNER_TTS_SETTING_SPECS,
        "voices": [{"slot": n, "label": label} for n, (_id, label) in sorted(VOICE_SLOTS.items())],
    }


def web_update_tts_settings(updates: dict) -> dict:
    settings = _load_tts_settings()
    bounds = {spec["id"]: (spec["min"], spec["max"]) for spec in TTS_SETTING_SPECS + OWNER_TTS_SETTING_SPECS}

    for key, (lo, hi) in bounds.items():
        if key in updates:
            try:
                value = int(updates[key])
            except (TypeError, ValueError):
                return {"ok": False, "error": f"{key} must be a whole number."}
            settings[key] = max(lo, min(hi, value))

    for key in ("voice_slot", "owner_voice_slot"):
        if key in updates:
            try:
                slot = int(updates[key])
            except (TypeError, ValueError):
                return {"ok": False, "error": f"{key} must be a whole number."}
            if slot not in VOICE_SLOTS:
                return {"ok": False, "error": f"No voice #{slot}."}
            settings[key] = slot

    for key in ("only_me", "owner_voice_override", "owner_volume_override"):
        if key in updates:
            settings[key] = bool(updates[key])

    _save_tts_settings(settings)
    return {"ok": True, "settings": settings}
