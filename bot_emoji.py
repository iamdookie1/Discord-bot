"""
!copy / !copyp / !addemoji — owner-only emoji utilities.

A custom emoji's image is always publicly fetchable from Discord's CDN
just by knowing its ID (`<a?:name:id>`, embedded in the message content
itself) — Nitro only gates who's allowed to *type* someone else's server
emoji into chat, not whether the underlying image can be read, so both
!copy/!copyp work the same whether or not the emoji is "Nitro-only" from
the caller's perspective.

- !copy grabs the emoji (from a reply, or from the command itself) and
  reposts its actual image/GIF as a file in chat.
- !copyp does the same lookup but adds it as a real custom emoji to the
  server the command was used in.
- !addemoji does the reverse of !copy: attach image files (PNG/JPG/GIF/
  WEBP) — or a .zip full of them — and each becomes a new custom emoji in
  the server the command was used in.

Both !copyp and !addemoji need the bot to have the Manage Expressions
permission in that server.

Hardcoded-owner-ID gated exactly like RP's channel lockdown and TTS's
sound controls — dispatched specially from bot_commands.py, not through
the normal command table, so non-owners get a silent no-op and !cmds
never lists them.
"""
import io
import os
import re
import sys
import traceback
import zipfile

import discord

from owner import OWNER_ID

_EMOJI_RE = re.compile(r"<(a?):(\w+):(\d+)>")
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_NON_NAME_CHARS_RE = re.compile(r"[^A-Za-z0-9_]")


def _sanitize_emoji_name(raw: str) -> str:
    """Discord emoji names are 2-32 characters, letters/digits/underscore
    only — this coerces whatever a file was actually called (an upload's
    filename, or a name entry inside a zip) into something that fits."""
    name = _NON_NAME_CHARS_RE.sub("_", raw).strip("_") or "emoji"
    if len(name) < 2:
        name = (name * 2)[:2]
    return name[:32]


async def _find_emoji(ctx) -> discord.PartialEmoji | None:
    """Looks for a custom emoji in the message being replied to (if any),
    then the command's own message — whichever has one first."""
    texts = []

    ref = ctx.message.reference
    if ref:
        replied = ref.resolved
        if isinstance(replied, discord.Message):
            texts.append(replied.content)
        elif ref.message_id:
            try:
                fetched = await ctx.channel.fetch_message(ref.message_id)
                texts.append(fetched.content)
            except discord.HTTPException:
                pass
    texts.append(ctx.content)

    for text in texts:
        match = _EMOJI_RE.search(text or "")
        if match:
            animated, name, emoji_id = match.groups()
            emoji = discord.PartialEmoji(name=name, animated=bool(animated), id=int(emoji_id))
            # A manually-built PartialEmoji has no ConnectionState attached,
            # so .read() fails with "Invalid state (no ConnectionState
            # provided)" — it needs the client's internal state wired in by
            # hand, same as PartialEmoji.from_str(..., client=...) does.
            emoji._state = ctx.client._connection
            return emoji
    return None


async def _run_guarded(ctx, coro):
    """Runs a handler body and guarantees *something* visible happens on
    failure — both a reply in Discord and a traceback on stderr — instead
    of a crash disappearing silently. discord.py's own default error
    handler normally prints unhandled exceptions to stderr already, but a
    guard here means a real failure is never mistakable for "nothing
    happened", regardless of how the surrounding process is launched."""
    try:
        await coro
    except Exception as exc:  # noqa: BLE001
        print("Error in an emoji command:", file=sys.stderr)
        traceback.print_exc()
        try:
            await ctx.send(f"Something went wrong: {exc}")
        except discord.HTTPException:
            pass


async def handle_copy(ctx):
    """!copy — reply to (or include) a message with a custom emoji and
    this reposts its actual image/GIF as a file in chat."""
    if ctx.author.id != OWNER_ID:
        return
    await _run_guarded(ctx, _do_copy(ctx))


async def _do_copy(ctx):
    emoji = await _find_emoji(ctx)
    if not emoji:
        await ctx.send("Couldn't find a custom emoji there — reply to a message with one, or include it in the command.")
        return
    try:
        data = await emoji.read()
    except discord.HTTPException:
        await ctx.send("Couldn't fetch that emoji's image.")
        return
    ext = "gif" if emoji.animated else "png"
    await ctx.send(file=discord.File(io.BytesIO(data), filename=f"{emoji.name}.{ext}"))


async def handle_copy_paste(ctx):
    """!copyp — same emoji lookup as !copy, but adds it as a real custom
    emoji to the server the command was used in, instead of reposting the
    image."""
    if ctx.author.id != OWNER_ID:
        return
    if not ctx.guild:
        await ctx.send("This only works in a server.")
        return
    await _run_guarded(ctx, _do_copy_paste(ctx))


async def _do_copy_paste(ctx):
    emoji = await _find_emoji(ctx)
    if not emoji:
        await ctx.send("Couldn't find a custom emoji there — reply to a message with one, or include it in the command.")
        return
    try:
        data = await emoji.read()
    except discord.HTTPException:
        await ctx.send("Couldn't fetch that emoji's image.")
        return
    try:
        created = await ctx.guild.create_custom_emoji(name=emoji.name, image=data)
    except discord.Forbidden:
        await ctx.send("I need the **Manage Expressions** permission in this server to do that.")
        return
    except discord.HTTPException as exc:
        await ctx.send(f"Discord rejected that: {exc.text}")
        return
    await ctx.send(f"Added {created} to this server.")


async def _collect_images(ctx) -> list[tuple[str, bytes]]:
    """Returns (name, image bytes) pairs from this message's attachments —
    plain image files as-is (named after the filename), or every image
    found inside a .zip (named after each entry's filename). Anything
    that's not a recognized image extension is skipped rather than
    causing the whole command to fail."""
    images = []
    for attachment in ctx.message.attachments:
        ext = os.path.splitext(attachment.filename)[1].lower()
        if ext == ".zip":
            try:
                archive_bytes = await attachment.read()
            except discord.HTTPException:
                continue
            try:
                with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
                    for info in zf.infolist():
                        if info.is_dir():
                            continue
                        inner_ext = os.path.splitext(info.filename)[1].lower()
                        if inner_ext not in _IMAGE_EXTS:
                            continue
                        base = os.path.splitext(os.path.basename(info.filename))[0]
                        images.append((_sanitize_emoji_name(base), zf.read(info)))
            except zipfile.BadZipFile:
                continue
        elif ext in _IMAGE_EXTS:
            base = os.path.splitext(attachment.filename)[0]
            try:
                images.append((_sanitize_emoji_name(base), await attachment.read()))
            except discord.HTTPException:
                continue
    return images


async def handle_add_emoji(ctx):
    """!addemoji — attach one or more image files (PNG/JPG/GIF/WEBP), or a
    .zip full of them, and each becomes a new custom emoji in the server
    the command was used in. With exactly one image attached, an argument
    (`!addemoji somename`) renames it instead of using the filename."""
    if ctx.author.id != OWNER_ID:
        return
    if not ctx.guild:
        await ctx.send("This only works in a server.")
        return
    if not ctx.message.attachments:
        await ctx.send("Attach one or more images (PNG/JPG/GIF/WEBP), or a .zip full of them.")
        return
    await _run_guarded(ctx, _do_add_emoji(ctx))


async def _do_add_emoji(ctx):
    images = await _collect_images(ctx)
    if not images:
        await ctx.send("Couldn't find any usable images in those attachments.")
        return

    if len(images) == 1 and ctx.args:
        images[0] = (_sanitize_emoji_name(ctx.args[0]), images[0][1])

    added, failed = [], []
    for name, data in images:
        try:
            created = await ctx.guild.create_custom_emoji(name=name, image=data)
        except discord.Forbidden:
            failed.append(f"{name} (missing Manage Expressions permission)")
            break  # every remaining attempt would fail the exact same way
        except discord.HTTPException as exc:
            failed.append(f"{name} ({exc.text})")
        else:
            added.append(str(created))

    lines = []
    if added:
        lines.append(f"Added {len(added)}: " + " ".join(added))
    if failed:
        lines.append(f"Failed {len(failed)}: " + ", ".join(failed))
    await ctx.send("\n".join(lines))
