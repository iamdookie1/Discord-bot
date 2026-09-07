"""
!addsound — owner-only: attach an audio file (or a short video) and it
becomes a new Soundboard sound in the server the command was used in.

Discord's soundboard only accepts MP3/OGG clips up to 5.2 seconds long, so
anything else — a longer clip, a different format, a video with audio —
is transcoded and trimmed with ffmpeg first, the same "convert whatever
was given into what Discord actually wants" pattern bot_rp.py already
uses for video-to-GIF uploads.

Hardcoded-owner-ID gated exactly like bot_emoji.py's !copy/!copyp/
!addemoji — dispatched specially from bot_commands.py, not through the
normal command table, so non-owners get a silent no-op and !cmds never
lists them.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback

import discord

from owner import OWNER_ID

_HAS_FFMPEG = shutil.which("ffmpeg") is not None
_AUDIO_EXTS = {".mp3", ".ogg", ".wav", ".m4a", ".flac", ".opus", ".webm", ".mp4", ".mov"}
# Discord's own limit is 5.2s — trimmed a hair under that as a safety
# margin against ffmpeg/encoding rounding landing right on the edge.
_MAX_SOUND_SECONDS = 5.0
_CONVERT_TIMEOUT = 30

_NAME_INVALID_RE = re.compile(r"[^\w \-']")


def _sanitize_sound_name(raw: str) -> str:
    """Soundboard sound names are 2-32 characters — unlike custom emoji
    names (used as :shortcodes:), these are just display text, so spaces
    and basic punctuation are fine; only genuinely odd characters get
    stripped."""
    name = _NAME_INVALID_RE.sub(" ", raw)
    name = " ".join(name.split()) or "sound"
    if len(name) < 2:
        name = (name * 2)[:2]
    return name[:32]


def _convert_to_mp3(data: bytes) -> bytes | None:
    """Transcodes/trims arbitrary audio (or a video's audio track) into a
    short MP3 clip Discord's soundboard will accept. Returns None if the
    conversion fails for any reason — caller decides what to say about it."""
    fd_in, in_path = tempfile.mkstemp(suffix=".src")
    os.close(fd_in)
    fd_out, out_path = tempfile.mkstemp(suffix=".mp3")
    os.close(fd_out)
    try:
        with open(in_path, "wb") as f:
            f.write(data)
        subprocess.run(
            ["ffmpeg", "-y", "-i", in_path, "-t", str(_MAX_SOUND_SECONDS),
             "-vn", "-c:a", "libmp3lame", "-ar", "44100", out_path],
            capture_output=True, timeout=_CONVERT_TIMEOUT, check=True,
        )
        with open(out_path, "rb") as f:
            return f.read()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None
    finally:
        for p in (in_path, out_path):
            if os.path.exists(p):
                os.remove(p)


async def _run_guarded(ctx, coro):
    """Same guard bot_emoji.py's commands use — guarantees a real failure
    is always visible (Discord reply + stderr traceback), never
    indistinguishable from nothing happening."""
    try:
        await coro
    except Exception as exc:  # noqa: BLE001
        print("Error in !addsound:", file=sys.stderr)
        traceback.print_exc()
        try:
            await ctx.send(f"Something went wrong: {exc}")
        except discord.HTTPException:
            pass


async def handle_add_sound(ctx):
    """!addsound [name] — attach an audio file (or a short video) and it
    becomes a new Soundboard sound in the server the command was used in.
    Anything over 5 seconds gets trimmed, and anything not already MP3/
    OGG gets transcoded — both need the ffmpeg binary."""
    if ctx.author.id != OWNER_ID:
        return
    if not ctx.guild:
        await ctx.send("This only works in a server.")
        return
    if not ctx.message.attachments:
        await ctx.send("Attach an audio file (mp3/ogg/wav/m4a/opus/flac), or a short video clip.")
        return

    attachment = ctx.message.attachments[0]
    ext = os.path.splitext(attachment.filename)[1].lower()
    if ext not in _AUDIO_EXTS:
        await ctx.send(f"Unsupported file type ({ext or 'unknown'}) — try mp3, ogg, wav, m4a, or a short video.")
        return

    name = _sanitize_sound_name(ctx.args[0] if ctx.args else os.path.splitext(attachment.filename)[0])
    await _run_guarded(ctx, _do_add_sound(ctx, attachment, ext, name))


async def _do_add_sound(ctx, attachment, ext, name):
    try:
        data = await attachment.read()
    except discord.HTTPException:
        await ctx.send("Couldn't download that attachment.")
        return

    if _HAS_FFMPEG:
        converted = _convert_to_mp3(data)
        if converted is None:
            await ctx.send("Couldn't convert that file to a sound Discord will accept.")
            return
        data = converted
    elif ext not in (".mp3", ".ogg"):
        await ctx.send("That needs converting to mp3/ogg first, which needs the `ffmpeg` binary — it isn't installed.")
        return
    # else: ffmpeg isn't installed but the file's already mp3/ogg — try it
    # as-is; Discord itself will reject it with a clear error if it's too
    # long or otherwise invalid.

    try:
        created = await ctx.guild.create_soundboard_sound(name=name, sound=data)
    except discord.Forbidden:
        await ctx.send("I need the **Manage Expressions** permission in this server to do that.")
        return
    except discord.HTTPException as exc:
        await ctx.send(f"Discord rejected that: {exc.text}")
        return
    await ctx.send(f"Added the sound **{created.name}** to this server.")
