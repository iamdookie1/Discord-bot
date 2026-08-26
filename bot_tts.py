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
nothing but a link/emoji/GIF (i.e. nothing left after sanitizing), or
that's over MAX_TTS_CHARS, is silently skipped rather than read.

Because a guild can only have one voice connection, TTS and music (see
bot_music.py) take turns — voice_owner.py is the shared registry that
lets each refuse to start while the other is active, per guild.
"""
import asyncio
import os
import re
import shutil
import subprocess
import tempfile

import discord

import voice_owner

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


def _unavailable_reason():
    if not _HAS_ESPEAK:
        return "Text-to-speech needs the `espeak-ng` binary, which isn't installed. Run `bash setup.sh` again, or install it manually."
    return None


def sanitize_message(message: discord.Message):
    """Returns the text to speak for this message, or None if there's
    nothing speakable left (or it's too long) — auto-detects and drops
    emoji/links/GIFs/spoilers rather than reading them literally."""
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
    text = " ".join(text.split())

    if not text or len(text) > MAX_TTS_CHARS:
        return None
    return text


def _synthesize(text: str):
    """Blocking — run off the event loop. Returns a temp WAV file path on
    success (caller deletes it after playback), or None on failure."""
    fd, path = tempfile.mkstemp(suffix=".wav", prefix="tts_")
    os.close(fd)
    try:
        subprocess.run(
            ["espeak-ng", "-w", path, text],
            capture_output=True, timeout=SYNTHESIZE_TIMEOUT, check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        if os.path.exists(path):
            os.remove(path)
        return None
    return path


async def _synthesize_async(text: str):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _synthesize, text)


class GuildTTSState:
    def __init__(self):
        self.text_channel_id = None
        self.voice_channel_id = None
        self.queue: asyncio.Queue = asyncio.Queue()
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
            path = await _synthesize_async(text)
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
    linked voice channel."""
    if not message.guild or message.author.bot:
        return
    state = _states.get(message.guild.id)
    if not state or message.channel.id != state.text_channel_id:
        return
    if message.content.startswith("!"):
        return  # commands aren't read aloud
    vc = message.guild.voice_client
    if not vc:
        return
    author_voice = getattr(message.author, "voice", None)
    if not author_voice or not author_voice.channel or author_voice.channel.id != vc.channel.id:
        return
    state.queue.put_nowait(message)


# (description, handler, required_perm) — merged into bot_commands.BUILTIN_COMMANDS
TTS_COMMANDS = {
    "tts": ("Toggles reading this channel's messages aloud in your voice channel.", handle_toggle, None),
}
