"""
!as — owner-only: turns an attached image/GIF/video, a replied-to message's
attachment, or a custom emoji (from a reply or the command itself) into a
real sticker for the server the command was used in.

Discord stickers must be PNG/APNG, at most 320x320, and at most 512KB, so
whatever was given gets resized (and, for GIFs/videos, re-encoded to a
short animated APNG) with ffmpeg — the same "convert whatever was given
into what Discord actually wants" pattern bot_rp.py (video->GIF) and
bot_sounds.py (audio->soundboard clip) already use. If an animated result
can't be squeezed under the size limit, it falls back to a single static
frame instead of failing outright.

Hardcoded-owner-ID gated exactly like bot_emoji.py's !copy/!copyp/
!addemoji and bot_sounds.py's !addsound — dispatched specially from
bot_commands.py, not through the normal command table, so non-owners get
a silent no-op and !cmds never lists them.
"""
import io
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

_STATIC_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
_ANIMATED_EXTS = {".gif", ".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"}
_ALL_EXTS = _STATIC_EXTS | _ANIMATED_EXTS

_MAX_STICKER_BYTES = 512 * 1024
_MAX_ANIMATED_SECONDS = 3.0
_CONVERT_TIMEOUT = 30
_DEFAULT_EMOJI_TAG = "\U0001F642"  # slightly-smiling-face — just autocomplete metadata, not shown on the sticker

# Largest-first: each step is tried until one lands under Discord's 512KB cap.
_STATIC_SCALE_STEPS = [320, 256, 192, 128, 96, 64]
_ANIMATED_SCALE_STEPS = [(160, 10), (128, 8), (96, 6), (64, 5)]  # (max dimension, fps)

_EMOJI_RE = re.compile(r"<(a?):(\w+):(\d+)>")
_NAME_INVALID_RE = re.compile(r"[^\w \-']")


def _sanitize_sticker_name(raw: str) -> str:
    """Sticker names are 2-30 characters — like Soundboard names, these are
    just display text, so spaces and basic punctuation are fine."""
    name = _NAME_INVALID_RE.sub(" ", raw)
    name = " ".join(name.split()) or "sticker"
    if len(name) < 2:
        name = (name * 2)[:2]
    return name[:30]


def _convert_static(data: bytes) -> bytes | None:
    """Grabs a single frame (already the only frame, for a plain image) and
    scales it down until the PNG fits under Discord's size limit."""
    fd_in, in_path = tempfile.mkstemp(suffix=".src")
    os.close(fd_in)
    fd_out, out_path = tempfile.mkstemp(suffix=".png")
    os.close(fd_out)
    try:
        with open(in_path, "wb") as f:
            f.write(data)
        for size in _STATIC_SCALE_STEPS:
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", in_path, "-frames:v", "1",
                     "-vf", f"scale='min({size},iw)':'min({size},ih)':force_original_aspect_ratio=decrease",
                     out_path],
                    capture_output=True, timeout=_CONVERT_TIMEOUT, check=True,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                continue
            if os.path.exists(out_path) and os.path.getsize(out_path) <= _MAX_STICKER_BYTES:
                with open(out_path, "rb") as f:
                    return f.read()
        return None
    except OSError:
        return None
    finally:
        for p in (in_path, out_path):
            if os.path.exists(p):
                os.remove(p)


def _convert_animated(data: bytes) -> bytes | None:
    """Re-encodes a GIF/video into a short looping APNG, trying smaller
    sizes/frame rates until it fits under Discord's size limit."""
    fd_in, in_path = tempfile.mkstemp(suffix=".src")
    os.close(fd_in)
    fd_out, out_path = tempfile.mkstemp(suffix=".png")
    os.close(fd_out)
    try:
        with open(in_path, "wb") as f:
            f.write(data)
        for size, fps in _ANIMATED_SCALE_STEPS:
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", in_path, "-t", str(_MAX_ANIMATED_SECONDS),
                     "-vf", f"fps={fps},scale='min({size},iw)':'min({size},ih)':force_original_aspect_ratio=decrease",
                     "-plays", "0", "-f", "apng", out_path],
                    capture_output=True, timeout=_CONVERT_TIMEOUT, check=True,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                continue
            if os.path.exists(out_path) and os.path.getsize(out_path) <= _MAX_STICKER_BYTES:
                with open(out_path, "rb") as f:
                    return f.read()
        return None
    except OSError:
        return None
    finally:
        for p in (in_path, out_path):
            if os.path.exists(p):
                os.remove(p)


def _convert_to_sticker(data: bytes, ext: str):
    """Returns (sticker_bytes, was_animated) for whatever was given, or
    (None, False) if it couldn't be squeezed into what Discord accepts.
    Animated sources try for an animated sticker first, falling back to a
    single static frame if the APNG can't be made small enough."""
    if ext in _ANIMATED_EXTS:
        converted = _convert_animated(data)
        if converted is not None:
            return converted, True
    converted = _convert_static(data)
    if converted is not None:
        return converted, False
    return None, False


async def _resolved_reply(ctx) -> discord.Message | None:
    ref = ctx.message.reference
    if not ref:
        return None
    if isinstance(ref.resolved, discord.Message):
        return ref.resolved
    if ref.message_id:
        try:
            return await ctx.channel.fetch_message(ref.message_id)
        except discord.HTTPException:
            return None
    return None


async def _resolve_source(ctx):
    """Finds something to turn into a sticker, in order: an attachment on
    this message, a custom emoji (from a reply or the command itself), or
    an attachment on the message being replied to. Returns (bytes,
    filename) or None."""
    if ctx.message.attachments:
        attachment = ctx.message.attachments[0]
        try:
            return await attachment.read(), attachment.filename
        except discord.HTTPException:
            return None

    replied = await _resolved_reply(ctx)
    texts = [replied.content] if replied else []
    texts.append(ctx.content)
    for text in texts:
        match = _EMOJI_RE.search(text or "")
        if match:
            animated, name, emoji_id = match.groups()
            emoji = discord.PartialEmoji(name=name, animated=bool(animated), id=int(emoji_id))
            # See bot_emoji.py's _find_emoji — a manually-built PartialEmoji
            # has no ConnectionState attached, so .read() would fail with
            # "Invalid state (no ConnectionState provided)" without this.
            emoji._state = ctx.client._connection
            try:
                data = await emoji.read()
            except discord.HTTPException:
                continue
            return data, f"{name}.{'gif' if animated else 'png'}"

    if replied and replied.attachments:
        attachment = replied.attachments[0]
        try:
            return await attachment.read(), attachment.filename
        except discord.HTTPException:
            return None

    return None


async def _run_guarded(ctx, coro):
    """Same guard bot_emoji.py/bot_sounds.py's commands use — guarantees a
    real failure is always visible (Discord reply + stderr traceback)."""
    try:
        await coro
    except Exception as exc:  # noqa: BLE001
        print("Error in !as:", file=sys.stderr)
        traceback.print_exc()
        try:
            await ctx.send(f"Something went wrong: {exc}")
        except discord.HTTPException:
            pass


async def handle_add_sticker(ctx):
    """!as [name] — turns an attached image/GIF/video, a replied-to
    message's attachment, or a custom emoji (from a reply or the command
    itself) into a real sticker for the server the command was used in."""
    if ctx.author.id != OWNER_ID:
        return
    if not ctx.guild:
        await ctx.send("This only works in a server.")
        return
    if not _HAS_FFMPEG:
        await ctx.send("This needs the `ffmpeg` binary to convert/resize things into what Discord's stickers require — it isn't installed.")
        return
    await _run_guarded(ctx, _do_add_sticker(ctx))


async def _do_add_sticker(ctx):
    source = await _resolve_source(ctx)
    if not source:
        await ctx.send("Attach an image/GIF/video, reply to a message that has one, or reply to (or include) a custom emoji.")
        return
    data, filename = source
    ext = os.path.splitext(filename)[1].lower()
    if ext not in _ALL_EXTS:
        await ctx.send(f"Unsupported file type ({ext or 'unknown'}) — try png, jpg, webp, gif, or a short video.")
        return

    name = _sanitize_sticker_name(ctx.args[0] if ctx.args else os.path.splitext(filename)[0])
    converted, animated = _convert_to_sticker(data, ext)
    if converted is None:
        await ctx.send("Couldn't convert that into something Discord's stickers will accept.")
        return

    file = discord.File(io.BytesIO(converted), filename=f"{name}.png")
    try:
        created = await ctx.guild.create_sticker(name=name, description="", emoji=_DEFAULT_EMOJI_TAG, file=file)
    except discord.Forbidden:
        await ctx.send("I need the **Manage Expressions** permission in this server to do that.")
        return
    except discord.HTTPException as exc:
        await ctx.send(f"Discord rejected that: {exc.text}")
        return
    kind = "animated" if animated else "static"
    await ctx.send(f"Added the {kind} sticker **{created.name}** to this server.")
