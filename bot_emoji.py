"""
!copy / !copyp — owner-only emoji utilities.

A custom emoji's image is always publicly fetchable from Discord's CDN
just by knowing its ID (`<a?:name:id>`, embedded in the message content
itself) — Nitro only gates who's allowed to *type* someone else's server
emoji into chat, not whether the underlying image can be read, so both
commands work the same whether or not the emoji is "Nitro-only" from the
caller's perspective.

- !copy grabs the emoji (from a reply, or from the command itself) and
  reposts its actual image/GIF as a file in chat.
- !copyp does the same lookup but adds it as a real custom emoji to the
  server the command was used in (needs the bot to have Manage Expressions
  there).

Hardcoded-owner-ID gated exactly like RP's channel lockdown and TTS's
sound controls — dispatched specially from bot_commands.py, not through
the normal command table, so non-owners get a silent no-op and !cmds
never lists them.
"""
import io
import re
import sys
import traceback

import discord

from owner import OWNER_ID

_EMOJI_RE = re.compile(r"<(a?):(\w+):(\d+)>")


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
            return discord.PartialEmoji(name=name, animated=bool(animated), id=int(emoji_id))
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
        print("Error in !copy/!copyp:", file=sys.stderr)
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
