"""
Everything related to chat commands: utility + moderation built-ins,
storage for user-defined custom commands, the sandbox that runs custom
command code, and the dispatcher that ties in music (bot_music.py) and
roleplay (bot_rp.py) commands.

Custom commands run with real Python `exec` — by design. This app is a
single-user control panel for a bot the user owns and runs on their own
device, so a custom command is closer to a saved macro than untrusted
remote code. A wide set of modules is pre-imported (see _SANDBOX_MODULES)
so commands that use them work immediately without a separate install step.
"""
import ast
import asyncio
import base64
import collections
import csv
import datetime
import hashlib
import io
import itertools
import json
import math
import operator
import os
import random
import re
import secrets
import statistics
import string
import subprocess
import sys
import textwrap
import time
import urllib.parse
import urllib.request
import uuid
from collections import namedtuple

import discord

import bot_music
import bot_rp
import bot_tts
import guild_settings
import voice_owner

# Optional: only needed for !qr / !ascii. Both are pure-Python (no native
# build step, so they install cleanly on Termux), and the commands tell
# the user what's missing instead of crashing if either isn't installed.
try:
    import qrcode
except ImportError:
    qrcode = None

try:
    import pyfiglet
except ImportError:
    pyfiglet = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CUSTOM_COMMANDS_PATH = os.path.join(BASE_DIR, "custom_commands.json")
COMMAND_SETTINGS_PATH = os.path.join(BASE_DIR, "command_settings.json")
WARNINGS_PATH = os.path.join(BASE_DIR, "warnings.json")

COMMAND_PREFIX = "!"
COMMAND_NAME_RE = re.compile(r"^[a-z0-9_-]{1,32}$")

_START_TIME = time.monotonic()

# Per-user-per-command cooldown, applied to every command type (built-in,
# RP, custom) uniformly. Silent when triggered — no extra reply — so a
# spammed command can't itself become the spam.
COOLDOWN_SECONDS = 3.0
_last_used: dict[tuple[int, str], float] = {}


def _is_on_cooldown(user_id: int, name: str) -> bool:
    key = (user_id, name)
    now = time.monotonic()
    last = _last_used.get(key)
    if last is not None and (now - last) < COOLDOWN_SECONDS:
        return True
    _last_used[key] = now
    return False


CommandSpec = namedtuple("CommandSpec", ["description", "handler", "required_perm", "category"])

PERM_LABELS = {
    "kick_members": "Kick Members",
    "ban_members": "Ban Members",
    "moderate_members": "Timeout Members",
    "manage_messages": "Manage Messages",
    "manage_channels": "Manage Channels",
    "manage_nicknames": "Manage Nicknames",
    "manage_roles": "Manage Roles",
    "manage_guild": "Manage Server",
}


class Ctx:
    """Passed into every command: built-in, RP, or custom."""

    def __init__(self, message: discord.Message, args: list, content: str, client: discord.Client):
        self.message = message
        self.channel = message.channel
        self.author = message.author
        self.guild = message.guild
        self.args = args
        self.content = content
        self.client = client

    async def send(self, *args, **kwargs):
        return await self.channel.send(*args, **kwargs)


async def _check_perm(ctx: Ctx, perm_name: str) -> bool:
    if not ctx.guild:
        await ctx.send("This only works in a server.")
        return False
    author_perms = getattr(ctx.author, "guild_permissions", None)
    if not author_perms or not getattr(author_perms, perm_name, False):
        label = PERM_LABELS.get(perm_name, perm_name)
        await ctx.send(f"You need the **{label}** permission to use this.")
        return False
    bot_perms = ctx.guild.me.guild_permissions
    if not getattr(bot_perms, perm_name, False):
        label = PERM_LABELS.get(perm_name, perm_name)
        await ctx.send(f"I need the **{label}** permission to do that.")
        return False
    return True


def _resolve_role(ctx: Ctx, text: str):
    if ctx.message.role_mentions:
        return ctx.message.role_mentions[0]
    if not text:
        return None
    return discord.utils.find(lambda r: r.name.lower() == text.lower(), ctx.guild.roles)


# Shared brand color for embeds — matches the web UI's --accent.
EMBED_COLOR = discord.Color(0xFFB454)


def _embed(*, title=None, description=None, **kwargs) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=EMBED_COLOR, **kwargs)


# ==================== utility commands ====================

async def _cmd_ping(ctx: Ctx):
    await ctx.send(f"Pong! `{round(ctx.client.latency * 1000)}ms`")


async def _cmd_cmds(ctx: Ctx):
    by_category = {}
    for name, spec in BUILTIN_COMMANDS.items():
        by_category.setdefault(spec.category, []).append(name)
    lines = [f"**{cat.title()}:** " + ", ".join(f"!{n}" for n in sorted(names)) for cat, names in by_category.items()]
    custom = load_custom_commands()
    if custom:
        lines.append("**Custom:** " + ", ".join(f"!{n}" for n in custom))
    await ctx.send("\n".join(lines))


async def _cmd_uptime(ctx: Ctx):
    seconds = int(time.monotonic() - _START_TIME)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    await ctx.send(f"Up for `{h}h {m}m {s}s`")


async def _cmd_avatar(ctx: Ctx):
    target = ctx.message.mentions[0] if ctx.message.mentions else ctx.author
    embed = _embed(title=f"{target.display_name}'s avatar")
    embed.set_image(url=target.display_avatar.url)
    await ctx.send(embed=embed)


async def _cmd_userinfo(ctx: Ctx):
    target = ctx.message.mentions[0] if ctx.message.mentions else ctx.author
    embed = _embed(title=str(target))
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="ID", value=f"`{target.id}`", inline=True)
    embed.add_field(name="Account created", value=target.created_at.strftime("%Y-%m-%d"), inline=True)
    joined_at = getattr(target, "joined_at", None)
    if joined_at:
        embed.add_field(name="Joined this server", value=joined_at.strftime("%Y-%m-%d"), inline=True)
    await ctx.send(embed=embed)


async def _cmd_serverinfo(ctx: Ctx):
    g = ctx.guild
    if not g:
        await ctx.send("This only works in a server.")
        return
    embed = _embed(title=g.name)
    if g.icon:
        embed.set_thumbnail(url=g.icon.url)
    embed.add_field(name="ID", value=f"`{g.id}`", inline=True)
    embed.add_field(name="Members", value=f"`{g.member_count}`", inline=True)
    embed.add_field(name="Owner", value=str(g.owner), inline=True)
    await ctx.send(embed=embed)


async def _cmd_say(ctx: Ctx):
    if not ctx.content:
        await ctx.send("Usage: `!say <message>`")
        return
    await ctx.send(ctx.content)
    try:
        await ctx.message.delete()
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        pass


async def _cmd_coinflip(ctx: Ctx):
    await ctx.send(random.choice(["Heads!", "Tails!"]))


async def _cmd_roll(ctx: Ctx):
    spec = ctx.args[0] if ctx.args else "1d6"
    m = re.match(r"^(\d*)d(\d+)$", spec, re.IGNORECASE)
    if not m:
        await ctx.send("Usage: `!roll [NdM]`, e.g. `!roll 2d6`")
        return
    count = max(1, min(int(m.group(1) or 1), 20))
    sides = max(2, min(int(m.group(2)), 1000))
    rolls = [random.randint(1, sides) for _ in range(count)]
    await ctx.send(f"{', '.join(map(str, rolls))} (total: {sum(rolls)})")


async def _cmd_8ball(ctx: Ctx):
    answers = [
        "Yes.", "No.", "Maybe.", "Definitely.", "Ask again later.",
        "Very doubtful.", "It is certain.", "Absolutely not.",
    ]
    await ctx.send(random.choice(answers))


async def _cmd_time(ctx: Ctx):
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    await ctx.send(f"Server time (UTC): `{now}`")


_SAFE_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv, ast.USub: operator.neg, ast.UAdd: operator.pos,
}


# Caps the size of any intermediate/final result — without this, something
# like `92 ** 35 ** 99` (== 92 ** (35 ** 99), since ** is right-associative)
# tries to build a number with ~10^152 digits: Python has no integer size
# limit, so it just hangs burning CPU and memory, freezing the whole bot
# (and on a phone, can get the whole process killed for using too much RAM).
_MAX_CALC_BITS = 4096  # ~1233 decimal digits — generous, but bounded


def _check_calc_size(value):
    if isinstance(value, int) and abs(value).bit_length() > _MAX_CALC_BITS:
        raise ValueError("Result too large")
    return value


def _safe_eval(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _SAFE_OPS:
        left = _safe_eval(node.left)
        right = _safe_eval(node.right)
        if isinstance(node.op, ast.Pow) and isinstance(left, int) and isinstance(right, int) and right > 0:
            # Estimate the result's bit length *before* computing it — for
            # `**` the actual computation is what hangs, so the check has
            # to happen first, not after.
            if right * max(abs(left).bit_length(), 1) > _MAX_CALC_BITS:
                raise ValueError("Result too large")
        return _check_calc_size(_SAFE_OPS[type(node.op)](left, right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _SAFE_OPS:
        return _check_calc_size(_SAFE_OPS[type(node.op)](_safe_eval(node.operand)))
    raise ValueError("unsupported expression")


async def _cmd_calc(ctx: Ctx):
    if not ctx.content:
        await ctx.send("Usage: `!calc <expression>`, e.g. `!calc (2 + 3) * 4`")
        return
    try:
        result = _safe_eval(ast.parse(ctx.content, mode="eval").body)
        await ctx.send(f"`{result}`")
    except ValueError:
        await ctx.send("That result is too large to compute (max ~1233 digits).")
    except Exception:
        await ctx.send("Couldn't evaluate that. Only numbers and + - * / // % ** are allowed.")


async def _cmd_choose(ctx: Ctx):
    options = [o.strip() for o in ctx.content.split("|") if o.strip()]
    if len(options) < 2:
        await ctx.send("Usage: `!choose option1 | option2 | option3`")
        return
    await ctx.send(f"I choose: **{random.choice(options)}**")


async def _cmd_reverse(ctx: Ctx):
    if not ctx.content:
        await ctx.send("Usage: `!reverse <text>`")
        return
    await ctx.send(ctx.content[::-1])


# In-memory only — reminders don't survive a restart, same as before
# !remindlist/!remindcancel existed. id -> {user_id, text, fire_at, task}.
_active_reminders = {}
_reminder_ids = itertools.count(1)


async def _cmd_remind(ctx: Ctx):
    if not ctx.args:
        await ctx.send("Usage: `!remind <minutes> <text>`")
        return
    try:
        minutes = float(ctx.args[0])
    except ValueError:
        await ctx.send("Usage: `!remind <minutes> <text>`")
        return
    minutes = max(0.1, min(minutes, 1440))
    text = " ".join(ctx.args[1:]) or "⏰"
    reminder_id = next(_reminder_ids)
    fire_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=minutes)
    await ctx.send(f"Okay, I'll remind you in {minutes:g} minute(s). (id `{reminder_id}`, see `!remindlist`/`!remindcancel`)")

    async def _fire():
        try:
            await asyncio.sleep(minutes * 60)
            await ctx.channel.send(f"{ctx.author.mention} reminder: {text}")
        except asyncio.CancelledError:
            pass
        except discord.HTTPException:
            pass
        finally:
            _active_reminders.pop(reminder_id, None)

    task = asyncio.create_task(_fire())
    _active_reminders[reminder_id] = {"user_id": ctx.author.id, "text": text, "fire_at": fire_at, "task": task}


async def _cmd_remindlist(ctx: Ctx):
    mine = sorted((rid, r) for rid, r in _active_reminders.items() if r["user_id"] == ctx.author.id)
    if not mine:
        await ctx.send("You have no active reminders.")
        return
    lines = [f"`{rid}` — {r['text']} (<t:{int(r['fire_at'].timestamp())}:R>)" for rid, r in mine]
    embed = _embed(title="Your reminders", description="\n".join(lines))
    await ctx.send(embed=embed)


async def _cmd_remindcancel(ctx: Ctx):
    if not ctx.args:
        await ctx.send("Usage: `!remindcancel <id>` — see `!remindlist` for IDs")
        return
    try:
        reminder_id = int(ctx.args[0])
    except ValueError:
        await ctx.send("That doesn't look like a reminder ID.")
        return
    entry = _active_reminders.get(reminder_id)
    if not entry or entry["user_id"] != ctx.author.id:
        await ctx.send("No reminder with that ID belongs to you.")
        return
    entry["task"].cancel()
    _active_reminders.pop(reminder_id, None)
    await ctx.send(f"Cancelled reminder `{reminder_id}`.")


async def _cmd_password(ctx: Ctx):
    length = 16
    if ctx.args:
        try:
            length = int(ctx.args[0])
        except ValueError:
            await ctx.send("Usage: `!password [length]` (default 16, max 64)")
            return
    length = max(4, min(length, 64))
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*()-_=+"
    pwd = "".join(secrets.choice(alphabet) for _ in range(length))
    await ctx.send(f"||`{pwd}`||")


async def _cmd_uuid(ctx: Ctx):
    await ctx.send(f"`{uuid.uuid4()}`")


async def _cmd_base64(ctx: Ctx):
    if len(ctx.args) < 2:
        await ctx.send("Usage: `!base64 <encode|decode> <text>`")
        return
    mode = ctx.args[0].lower()
    text = " ".join(ctx.args[1:])
    try:
        if mode == "encode":
            result = base64.b64encode(text.encode()).decode()
        elif mode == "decode":
            result = base64.b64decode(text.encode()).decode()
        else:
            await ctx.send("Usage: `!base64 <encode|decode> <text>`")
            return
    except Exception:
        await ctx.send("Couldn't do that — is it valid base64?")
        return
    await ctx.send(f"`{result}`")


async def _cmd_hash(ctx: Ctx):
    if not ctx.content:
        await ctx.send("Usage: `!hash <text>`")
        return
    await ctx.send(f"`{hashlib.sha256(ctx.content.encode()).hexdigest()}`")


async def _cmd_color(ctx: Ctx):
    if not ctx.args:
        await ctx.send("Usage: `!color <hex>`, e.g. `!color #ff8800`")
        return
    hex_str = ctx.args[0].lstrip("#")
    try:
        color_val = int(hex_str, 16)
        if not (0 <= color_val <= 0xFFFFFF):
            raise ValueError
    except ValueError:
        await ctx.send("That doesn't look like a valid hex color, e.g. `#ff8800`.")
        return
    embed = discord.Embed(color=discord.Color(color_val), description=f"`#{hex_str.upper()}`")
    await ctx.send(embed=embed)


async def _cmd_timestamp(ctx: Ctx):
    if ctx.args:
        try:
            ts = int(ctx.args[0])
        except ValueError:
            await ctx.send("Usage: `!timestamp [unix_seconds]` (omit for right now)")
            return
    else:
        ts = int(time.time())
    await ctx.send(f"`{ts}` → <t:{ts}:F> (<t:{ts}:R>)")


async def _cmd_invite(ctx: Ctx):
    perms = discord.Permissions(
        send_messages=True, manage_messages=True, manage_roles=True, manage_channels=True,
        manage_nicknames=True, moderate_members=True, kick_members=True, ban_members=True,
        connect=True, speak=True, read_message_history=True, embed_links=True, attach_files=True,
        add_reactions=True, view_channel=True, use_external_emojis=True,
    )
    url = discord.utils.oauth_url(ctx.client.user.id, permissions=perms)
    await ctx.send(f"Invite me to a server: {url}")


_POLL_NUMBER_EMOJIS = ["1\N{combining enclosing keycap}", "2\N{combining enclosing keycap}", "3\N{combining enclosing keycap}",
                       "4\N{combining enclosing keycap}", "5\N{combining enclosing keycap}", "6\N{combining enclosing keycap}",
                       "7\N{combining enclosing keycap}", "8\N{combining enclosing keycap}", "9\N{combining enclosing keycap}", "\U0001F51F"]


async def _cmd_poll(ctx: Ctx):
    if not ctx.content or "|" not in ctx.content:
        await ctx.send("Usage: `!poll Question? | Option 1 | Option 2 | ...` (up to 10 options)")
        return
    parts = [p.strip() for p in ctx.content.split("|") if p.strip()]
    if len(parts) < 3:
        await ctx.send("Usage: `!poll Question? | Option 1 | Option 2 | ...` (need a question and at least 2 options)")
        return
    question, options = parts[0], parts[1:11]
    lines = [f"{_POLL_NUMBER_EMOJIS[i]} {opt}" for i, opt in enumerate(options)]
    embed = _embed(title=question, description="\n".join(lines))
    msg = await ctx.send(embed=embed)
    for i in range(len(options)):
        try:
            await msg.add_reaction(_POLL_NUMBER_EMOJIS[i])
        except discord.HTTPException:
            pass


async def _cmd_channelinfo(ctx: Ctx):
    ch = ctx.channel
    embed = _embed(title=f"#{getattr(ch, 'name', 'this channel')}")
    embed.add_field(name="ID", value=f"`{ch.id}`", inline=True)
    embed.add_field(name="Type", value=f"`{ch.type}`", inline=True)
    if getattr(ch, "topic", None):
        embed.add_field(name="Topic", value=ch.topic, inline=False)
    if hasattr(ch, "nsfw"):
        embed.add_field(name="NSFW", value=f"`{ch.nsfw}`", inline=True)
    if hasattr(ch, "slowmode_delay"):
        embed.add_field(name="Slowmode", value=f"`{ch.slowmode_delay}s`", inline=True)
    await ctx.send(embed=embed)


async def _cmd_roleinfo(ctx: Ctx):
    if not ctx.guild:
        await ctx.send("This only works in a server.")
        return
    role = ctx.message.role_mentions[0] if ctx.message.role_mentions else _resolve_role(ctx, ctx.content)
    if not role:
        await ctx.send("Usage: `!roleinfo <role name or @role>`")
        return
    embed = discord.Embed(title=role.name, color=role.color if role.color.value else EMBED_COLOR)
    embed.add_field(name="ID", value=f"`{role.id}`", inline=True)
    embed.add_field(name="Color", value=f"`{role.color}`", inline=True)
    embed.add_field(name="Members", value=f"`{len(role.members)}`", inline=True)
    embed.add_field(name="Mentionable", value=f"`{role.mentionable}`", inline=True)
    embed.add_field(name="Hoisted", value=f"`{role.hoist}`", inline=True)
    await ctx.send(embed=embed)


async def _cmd_permissions(ctx: Ctx):
    if not ctx.guild:
        await ctx.send("This only works in a server.")
        return
    target = ctx.message.mentions[0] if ctx.message.mentions else ctx.author
    granted = [name.replace("_", " ").title() for name, value in target.guild_permissions if value]
    embed = _embed(title=f"{target.display_name}'s key permissions", description=", ".join(granted[:20]) or "None")
    await ctx.send(embed=embed)


_DISCORD_EPOCH_MS = 1420070400000


async def _cmd_snowflake(ctx: Ctx):
    if not ctx.args:
        await ctx.send("Usage: `!snowflake <id>`")
        return
    try:
        snowflake = int(ctx.args[0])
    except ValueError:
        await ctx.send("That doesn't look like a snowflake ID.")
        return
    ts = ((snowflake >> 22) + _DISCORD_EPOCH_MS) // 1000
    embed = _embed(title=f"Snowflake `{snowflake}`", description=f"<t:{ts}:F> (<t:{ts}:R>)")
    await ctx.send(embed=embed)


async def _cmd_membercount(ctx: Ctx):
    if not ctx.guild:
        await ctx.send("This only works in a server.")
        return
    embed = _embed(title=ctx.guild.name, description=f"**{ctx.guild.member_count}** members")
    await ctx.send(embed=embed)


async def _cmd_servericon(ctx: Ctx):
    if not ctx.guild:
        await ctx.send("This only works in a server.")
        return
    if not ctx.guild.icon:
        await ctx.send("This server doesn't have an icon set.")
        return
    embed = _embed(title=f"{ctx.guild.name}'s icon")
    embed.set_image(url=ctx.guild.icon.url)
    await ctx.send(embed=embed)


async def _cmd_emojis(ctx: Ctx):
    if not ctx.guild:
        await ctx.send("This only works in a server.")
        return
    emojis = ctx.guild.emojis
    if not emojis:
        await ctx.send("This server has no custom emoji.")
        return
    parts, total = [], 0
    for e in emojis:
        s = str(e)
        if total + len(s) + 1 > 4000:
            break
        parts.append(s)
        total += len(s) + 1
    embed = _embed(title=f"{ctx.guild.name}'s emoji", description=" ".join(parts))
    footer = f"{len(emojis)} total"
    if len(parts) < len(emojis):
        footer += f" (showing {len(parts)})"
    embed.set_footer(text=footer)
    await ctx.send(embed=embed)


async def _cmd_qr(ctx: Ctx):
    if qrcode is None:
        await ctx.send("QR codes need the `qrcode` package, which isn't installed.")
        return
    if not ctx.content:
        await ctx.send("Usage: `!qr <text>`")
        return
    if len(ctx.content) > 300:
        await ctx.send("That's too long for a QR code here (max 300 characters).")
        return
    qr = qrcode.QRCode(border=1)
    qr.add_data(ctx.content)
    qr.make(fit=True)
    art = "\n".join("".join("██" if cell else "  " for cell in row) for row in qr.get_matrix())
    if len(art) > 1900:
        await ctx.send("That QR code came out too large to display here — try shorter text.")
        return
    await ctx.send(f"```\n{art}\n```")


async def _cmd_ascii(ctx: Ctx):
    if not ctx.content:
        await ctx.send("Usage: `!ascii <text>`")
        return
    if pyfiglet is None:
        await ctx.send("ASCII art needs the `pyfiglet` package, which isn't installed.")
        return
    try:
        art = pyfiglet.figlet_format(ctx.content[:20])
    except Exception:
        await ctx.send("Couldn't render that as ASCII art.")
        return
    if len(art) > 1900:
        await ctx.send("That's too long to render — try shorter text.")
        return
    await ctx.send(f"```\n{art}\n```")


UTILITY_COMMANDS = {
    "ping": CommandSpec("Checks the bot's latency to Discord.", _cmd_ping, None, "utility"),
    "cmds": CommandSpec("Lists every available command.", _cmd_cmds, None, "utility"),
    "help": CommandSpec("Same as !cmds.", _cmd_cmds, None, "utility"),
    "uptime": CommandSpec("How long the bot has been connected.", _cmd_uptime, None, "utility"),
    "avatar": CommandSpec("Shows your avatar, or @mention someone else's.", _cmd_avatar, None, "utility"),
    "userinfo": CommandSpec("Shows account info for you or @mention.", _cmd_userinfo, None, "utility"),
    "serverinfo": CommandSpec("Shows info about the current server.", _cmd_serverinfo, None, "utility"),
    "say": CommandSpec("Repeats your message, then deletes your original.", _cmd_say, None, "utility"),
    "coinflip": CommandSpec("Flips a coin.", _cmd_coinflip, None, "utility"),
    "roll": CommandSpec("Rolls dice, e.g. !roll 2d6.", _cmd_roll, None, "utility"),
    "8ball": CommandSpec("Ask it a yes/no question.", _cmd_8ball, None, "utility"),
    "time": CommandSpec("Shows the current server (UTC) time.", _cmd_time, None, "utility"),
    "calc": CommandSpec("Evaluates a math expression.", _cmd_calc, None, "utility"),
    "choose": CommandSpec("Picks one option from a | separated list.", _cmd_choose, None, "utility"),
    "reverse": CommandSpec("Reverses your text.", _cmd_reverse, None, "utility"),
    "remind": CommandSpec("Reminds you in the channel after N minutes.", _cmd_remind, None, "utility"),
    "password": CommandSpec("Generates a random password.", _cmd_password, None, "utility"),
    "uuid": CommandSpec("Generates a random UUID.", _cmd_uuid, None, "utility"),
    "base64": CommandSpec("Encodes/decodes text as base64.", _cmd_base64, None, "utility"),
    "hash": CommandSpec("SHA-256 hashes some text.", _cmd_hash, None, "utility"),
    "color": CommandSpec("Shows a color swatch for a hex code.", _cmd_color, None, "utility"),
    "timestamp": CommandSpec("Converts a unix timestamp to a Discord date/time tag.", _cmd_timestamp, None, "utility"),
    "invite": CommandSpec("Gives you a link to invite this bot to another server.", _cmd_invite, None, "utility"),
    "poll": CommandSpec("Posts a quick reaction poll.", _cmd_poll, None, "utility"),
    "channelinfo": CommandSpec("Shows info about the current channel.", _cmd_channelinfo, None, "utility"),
    "roleinfo": CommandSpec("Shows info about a role.", _cmd_roleinfo, None, "utility"),
    "permissions": CommandSpec("Shows your (or @mention's) key permissions.", _cmd_permissions, None, "utility"),
    "snowflake": CommandSpec("Decodes a Discord ID into its creation timestamp.", _cmd_snowflake, None, "utility"),
    "membercount": CommandSpec("Shows this server's member count.", _cmd_membercount, None, "utility"),
    "servericon": CommandSpec("Shows this server's icon full-size.", _cmd_servericon, None, "utility"),
    "emojis": CommandSpec("Lists this server's custom emoji.", _cmd_emojis, None, "utility"),
    "qr": CommandSpec("Generates a QR code for some text.", _cmd_qr, None, "utility"),
    "ascii": CommandSpec("Turns short text into an ASCII art banner.", _cmd_ascii, None, "utility"),
    "remindlist": CommandSpec("Lists your active reminders.", _cmd_remindlist, None, "utility"),
    "remindcancel": CommandSpec("Cancels one of your active reminders.", _cmd_remindcancel, None, "utility"),
}


# ==================== moderation commands ====================

async def _cmd_kick(ctx: Ctx):
    if not await _check_perm(ctx, "kick_members"):
        return
    if not ctx.message.mentions:
        await ctx.send("Usage: `!kick @member [reason]`")
        return
    target = ctx.message.mentions[0]
    reason = " ".join(ctx.args[1:]) or None
    try:
        await target.kick(reason=reason)
        await ctx.send(f"Kicked **{target}**." + (f" Reason: {reason}" if reason else ""))
        await _log_mod_action(ctx, "Kick", target, reason)
    except discord.Forbidden:
        await ctx.send("I can't kick that member (role hierarchy?).")
    except discord.HTTPException as exc:
        await ctx.send(f"Couldn't kick: {exc.text}")


async def _cmd_ban(ctx: Ctx):
    if not await _check_perm(ctx, "ban_members"):
        return
    if not ctx.message.mentions:
        await ctx.send("Usage: `!ban @member [reason]`")
        return
    target = ctx.message.mentions[0]
    reason = " ".join(ctx.args[1:]) or None
    try:
        await target.ban(reason=reason, delete_message_days=0)
        await ctx.send(f"Banned **{target}**." + (f" Reason: {reason}" if reason else ""))
        await _log_mod_action(ctx, "Ban", target, reason)
    except discord.Forbidden:
        await ctx.send("I can't ban that member (role hierarchy?).")
    except discord.HTTPException as exc:
        await ctx.send(f"Couldn't ban: {exc.text}")


async def _cmd_softban(ctx: Ctx):
    if not await _check_perm(ctx, "ban_members"):
        return
    if not ctx.message.mentions:
        await ctx.send("Usage: `!softban @member [reason]`")
        return
    target = ctx.message.mentions[0]
    reason = " ".join(ctx.args[1:]) or None
    try:
        await target.ban(reason=reason, delete_message_days=1)
        await ctx.guild.unban(target, reason="Softban cleanup")
        await ctx.send(f"Softbanned **{target}** (kicked + recent messages purged).")
        await _log_mod_action(ctx, "Softban", target, reason)
    except discord.Forbidden:
        await ctx.send("I can't do that (role hierarchy?).")
    except discord.HTTPException as exc:
        await ctx.send(f"Couldn't softban: {exc.text}")


async def _cmd_unban(ctx: Ctx):
    if not await _check_perm(ctx, "ban_members"):
        return
    if not ctx.args:
        await ctx.send("Usage: `!unban <user_id>`")
        return
    try:
        user_id = int(ctx.args[0])
    except ValueError:
        await ctx.send("That doesn't look like a user ID.")
        return
    try:
        await ctx.guild.unban(discord.Object(id=user_id))
        await ctx.send(f"Unbanned user `{user_id}`.")
    except discord.NotFound:
        await ctx.send("That user isn't banned.")
    except discord.HTTPException as exc:
        await ctx.send(f"Couldn't unban: {exc.text}")


async def _cmd_timeout(ctx: Ctx):
    if not await _check_perm(ctx, "moderate_members"):
        return
    if not ctx.message.mentions or len(ctx.args) < 2:
        await ctx.send("Usage: `!timeout @member <minutes> [reason]`")
        return
    target = ctx.message.mentions[0]
    try:
        minutes = int(ctx.args[1])
    except ValueError:
        await ctx.send("Minutes must be a number.")
        return
    minutes = max(1, min(minutes, 40320))
    reason = " ".join(ctx.args[2:]) or None
    try:
        await target.timeout(datetime.timedelta(minutes=minutes), reason=reason)
        await ctx.send(f"Timed out **{target}** for {minutes} minute(s).")
        await _log_mod_action(ctx, "Timeout", target, reason)
    except discord.Forbidden:
        await ctx.send("I don't have permission to timeout that member.")
    except discord.HTTPException as exc:
        await ctx.send(f"Couldn't timeout: {exc.text}")


async def _cmd_untimeout(ctx: Ctx):
    if not await _check_perm(ctx, "moderate_members"):
        return
    if not ctx.message.mentions:
        await ctx.send("Usage: `!untimeout @member`")
        return
    target = ctx.message.mentions[0]
    try:
        await target.timeout(None)
        await ctx.send(f"Removed timeout from **{target}**.")
    except discord.Forbidden:
        await ctx.send("I don't have permission to do that.")
    except discord.HTTPException as exc:
        await ctx.send(f"Couldn't remove timeout: {exc.text}")


def _load_warnings() -> dict:
    if not os.path.exists(WARNINGS_PATH):
        return {}
    try:
        with open(WARNINGS_PATH, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_warnings(data: dict):
    with open(WARNINGS_PATH, "w") as f:
        json.dump(data, f, indent=2)


async def _log_mod_action(ctx: Ctx, action: str, target, reason=None):
    """Posts an embed to this server's configured mod-log channel, if any
    (set with !setmodlog). Does nothing if none is configured."""
    if not ctx.guild:
        return
    channel_id = guild_settings.get_modlog_channel(ctx.guild.id)
    if not channel_id:
        return
    channel = ctx.guild.get_channel(channel_id)
    if not channel:
        return
    description = f"**Target:** {target}\n**By:** {ctx.author}"
    if reason:
        description += f"\n**Reason:** {reason}"
    embed = _embed(title=action, description=description)
    try:
        await channel.send(embed=embed)
    except discord.HTTPException:
        pass


async def _cmd_warn(ctx: Ctx):
    if not await _check_perm(ctx, "kick_members"):
        return
    if not ctx.message.mentions:
        await ctx.send("Usage: `!warn @member [reason]`")
        return
    target = ctx.message.mentions[0]
    reason = " ".join(ctx.args[1:]) or "No reason given."
    key = f"{ctx.guild.id}:{target.id}"
    data = _load_warnings()
    data.setdefault(key, []).append({
        "reason": reason,
        "by": str(ctx.author),
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    })
    _save_warnings(data)
    await ctx.send(f"Warned **{target}**. ({len(data[key])} total) Reason: {reason}")
    await _log_mod_action(ctx, "Warn", target, reason)


async def _cmd_warnings(ctx: Ctx):
    if not await _check_perm(ctx, "manage_messages"):
        return
    target = ctx.message.mentions[0] if ctx.message.mentions else ctx.author
    key = f"{ctx.guild.id}:{target.id}"
    entries = _load_warnings().get(key, [])
    if not entries:
        await ctx.send(f"**{target}** has no warnings.")
        return
    # Numbers shown are absolute positions in the full list (not just the
    # last-10 slice), so they line up with what !warnremove expects.
    start = max(0, len(entries) - 10)
    shown = entries[start:]
    lines = [f"**{start + i + 1}.** {e['reason']} — by {e['by']}" for i, e in enumerate(shown)]
    embed = _embed(title=f"{target}'s warnings", description="\n".join(lines))
    embed.set_footer(text=f"{len(entries)} total")
    await ctx.send(embed=embed)


async def _cmd_warnremove(ctx: Ctx):
    if not await _check_perm(ctx, "manage_messages"):
        return
    if not ctx.message.mentions or len(ctx.args) < 2:
        await ctx.send("Usage: `!warnremove @member <#>` — see `!warnings @member` for numbers")
        return
    target = ctx.message.mentions[0]
    try:
        index = int(ctx.args[1])
    except ValueError:
        await ctx.send("That doesn't look like a warning number.")
        return
    key = f"{ctx.guild.id}:{target.id}"
    data = _load_warnings()
    entries = data.get(key, [])
    if not (1 <= index <= len(entries)):
        await ctx.send(f"**{target}** doesn't have a warning #{index}.")
        return
    removed = entries.pop(index - 1)
    data[key] = entries
    _save_warnings(data)
    await ctx.send(f"Removed warning #{index} from **{target}**: {removed['reason']}")


async def _cmd_clearwarnings(ctx: Ctx):
    if not await _check_perm(ctx, "manage_messages"):
        return
    if not ctx.message.mentions:
        await ctx.send("Usage: `!clearwarnings @member`")
        return
    target = ctx.message.mentions[0]
    key = f"{ctx.guild.id}:{target.id}"
    data = _load_warnings()
    if key in data:
        del data[key]
        _save_warnings(data)
    await ctx.send(f"Cleared warnings for **{target}**.")


async def _cmd_purge(ctx: Ctx):
    if not await _check_perm(ctx, "manage_messages"):
        return
    if not ctx.args:
        await ctx.send("Usage: `!purge <count>` (max 100)")
        return
    try:
        count = int(ctx.args[0])
    except ValueError:
        await ctx.send("Count must be a number.")
        return
    count = max(1, min(count, 100))
    try:
        deleted = await ctx.channel.purge(limit=count + 1)
        note = await ctx.channel.send(f"Deleted {max(len(deleted) - 1, 0)} message(s).")
        await asyncio.sleep(4)
        await note.delete()
    except discord.Forbidden:
        await ctx.send("I need Manage Messages here to do that.")
    except discord.HTTPException as exc:
        await ctx.send(f"Couldn't purge: {exc.text}")


async def _cmd_slowmode(ctx: Ctx):
    if not await _check_perm(ctx, "manage_channels"):
        return
    if not ctx.args:
        await ctx.send("Usage: `!slowmode <seconds>` (0 disables it)")
        return
    try:
        seconds = int(ctx.args[0])
    except ValueError:
        await ctx.send("Seconds must be a number.")
        return
    seconds = max(0, min(seconds, 21600))
    try:
        await ctx.channel.edit(slowmode_delay=seconds)
        await ctx.send(f"Slowmode set to {seconds}s." if seconds else "Slowmode disabled.")
    except discord.Forbidden:
        await ctx.send("I need Manage Channels here to do that.")


async def _cmd_lock(ctx: Ctx):
    if not await _check_perm(ctx, "manage_channels"):
        return
    try:
        await ctx.channel.set_permissions(ctx.guild.default_role, send_messages=False)
        await ctx.send("Channel locked.")
    except discord.Forbidden:
        await ctx.send("I need Manage Channels here to do that.")


async def _cmd_unlock(ctx: Ctx):
    if not await _check_perm(ctx, "manage_channels"):
        return
    try:
        await ctx.channel.set_permissions(ctx.guild.default_role, send_messages=None)
        await ctx.send("Channel unlocked.")
    except discord.Forbidden:
        await ctx.send("I need Manage Channels here to do that.")


async def _cmd_nick(ctx: Ctx):
    if not await _check_perm(ctx, "manage_nicknames"):
        return
    if not ctx.message.mentions or len(ctx.args) < 2:
        await ctx.send("Usage: `!nick @member <new nickname>`")
        return
    target = ctx.message.mentions[0]
    new_nick = " ".join(ctx.args[1:])[:32]
    try:
        await target.edit(nick=new_nick)
        await ctx.send(f"Renamed **{target}** to **{new_nick}**.")
    except discord.Forbidden:
        await ctx.send("I can't rename that member (role hierarchy?).")


async def _cmd_addrole(ctx: Ctx):
    if not await _check_perm(ctx, "manage_roles"):
        return
    if not ctx.message.mentions or len(ctx.args) < 2:
        await ctx.send("Usage: `!addrole @member <role name>`")
        return
    target = ctx.message.mentions[0]
    role = _resolve_role(ctx, " ".join(ctx.args[1:]))
    if not role:
        await ctx.send("Couldn't find that role.")
        return
    try:
        await target.add_roles(role)
        await ctx.send(f"Gave **{role.name}** to **{target}**.")
    except discord.Forbidden:
        await ctx.send("I can't manage that role (role hierarchy?).")


async def _cmd_removerole(ctx: Ctx):
    if not await _check_perm(ctx, "manage_roles"):
        return
    if not ctx.message.mentions or len(ctx.args) < 2:
        await ctx.send("Usage: `!removerole @member <role name>`")
        return
    target = ctx.message.mentions[0]
    role = _resolve_role(ctx, " ".join(ctx.args[1:]))
    if not role:
        await ctx.send("Couldn't find that role.")
        return
    try:
        await target.remove_roles(role)
        await ctx.send(f"Removed **{role.name}** from **{target}**.")
    except discord.Forbidden:
        await ctx.send("I can't manage that role (role hierarchy?).")


async def _cmd_createrole(ctx: Ctx):
    if not await _check_perm(ctx, "manage_roles"):
        return
    if not ctx.content:
        await ctx.send("Usage: `!createrole <name>`")
        return
    try:
        role = await ctx.guild.create_role(name=ctx.content[:100], reason=f"Created by {ctx.author}")
        await ctx.send(f"Created role **{role.name}**.")
    except discord.HTTPException as exc:
        await ctx.send(f"Couldn't create that role: {exc.text}")


async def _cmd_deleterole(ctx: Ctx):
    if not await _check_perm(ctx, "manage_roles"):
        return
    role = ctx.message.role_mentions[0] if ctx.message.role_mentions else _resolve_role(ctx, ctx.content)
    if not role:
        await ctx.send("Usage: `!deleterole <role name or @role>`")
        return
    try:
        name = role.name
        await role.delete(reason=f"Deleted by {ctx.author}")
        await ctx.send(f"Deleted role **{name}**.")
    except discord.HTTPException as exc:
        await ctx.send(f"Couldn't delete that role: {exc.text}")


async def _cmd_purgeuser(ctx: Ctx):
    if not await _check_perm(ctx, "manage_messages"):
        return
    if not ctx.message.mentions or len(ctx.args) < 2:
        await ctx.send("Usage: `!purgeuser @member <count>`")
        return
    target = ctx.message.mentions[0]
    try:
        count = int(ctx.args[1])
    except ValueError:
        await ctx.send("Count must be a number.")
        return
    count = max(1, min(count, 100))
    try:
        deleted = await ctx.channel.purge(limit=min(count * 5, 500), check=lambda m: m.author.id == target.id)
        note = await ctx.channel.send(f"Deleted {len(deleted)} message(s) from **{target}**.")
        await asyncio.sleep(4)
        await note.delete()
    except discord.Forbidden:
        await ctx.send("I need Manage Messages here to do that.")
    except discord.HTTPException as exc:
        await ctx.send(f"Couldn't purge: {exc.text}")


async def _cmd_banid(ctx: Ctx):
    if not await _check_perm(ctx, "ban_members"):
        return
    if not ctx.args:
        await ctx.send("Usage: `!banid <user_id> [reason]`")
        return
    try:
        user_id = int(ctx.args[0])
    except ValueError:
        await ctx.send("That doesn't look like a user ID.")
        return
    reason = " ".join(ctx.args[1:]) or None
    try:
        await ctx.guild.ban(discord.Object(id=user_id), reason=reason)
        await ctx.send(f"Banned user ID `{user_id}`." + (f" Reason: {reason}" if reason else ""))
    except discord.HTTPException as exc:
        await ctx.send(f"Couldn't ban: {exc.text}")


async def _cmd_announce(ctx: Ctx):
    if not await _check_perm(ctx, "manage_messages"):
        return
    if not ctx.message.channel_mentions or len(ctx.args) < 2:
        await ctx.send("Usage: `!announce #channel <message>`")
        return
    channel = ctx.message.channel_mentions[0]
    text = ctx.content
    for cm in ctx.message.channel_mentions:
        text = text.replace(cm.mention, "").strip()
    if not text:
        await ctx.send("Write a message to announce.")
        return
    try:
        await channel.send(text)
        await ctx.send(f"Announced in {channel.mention}.")
    except discord.Forbidden:
        await ctx.send(f"I can't send messages in {channel.mention}.")


async def _cmd_pin(ctx: Ctx):
    if not await _check_perm(ctx, "manage_messages"):
        return
    ref = ctx.message.reference
    if not ref or not ref.message_id:
        await ctx.send("Reply to the message you want to pin, then use `!pin`.")
        return
    try:
        msg = ref.resolved or await ctx.channel.fetch_message(ref.message_id)
        await msg.pin()
        await ctx.send("Pinned.")
    except discord.HTTPException as exc:
        await ctx.send(f"Couldn't pin that: {exc.text}")


async def _cmd_unpin(ctx: Ctx):
    if not await _check_perm(ctx, "manage_messages"):
        return
    ref = ctx.message.reference
    if not ref or not ref.message_id:
        await ctx.send("Reply to the pinned message you want to unpin, then use `!unpin`.")
        return
    try:
        msg = ref.resolved or await ctx.channel.fetch_message(ref.message_id)
        await msg.unpin()
        await ctx.send("Unpinned.")
    except discord.HTTPException as exc:
        await ctx.send(f"Couldn't unpin that: {exc.text}")


async def _cmd_clearnick(ctx: Ctx):
    if not await _check_perm(ctx, "manage_nicknames"):
        return
    if not ctx.message.mentions:
        await ctx.send("Usage: `!clearnick @member`")
        return
    target = ctx.message.mentions[0]
    try:
        await target.edit(nick=None)
        await ctx.send(f"Reset **{target}**'s nickname.")
    except discord.Forbidden:
        await ctx.send("I can't do that (role hierarchy?).")


async def _cmd_banlist(ctx: Ctx):
    if not await _check_perm(ctx, "ban_members"):
        return
    try:
        bans = [entry async for entry in ctx.guild.bans(limit=25)]
    except discord.HTTPException as exc:
        await ctx.send(f"Couldn't fetch the ban list: {exc.text}")
        return
    if not bans:
        await ctx.send("No bans in this server.")
        return
    lines = [f"{b.user} (`{b.user.id}`)" for b in bans[:25]]
    embed = _embed(title="Banned users", description="\n".join(lines))
    embed.set_footer(text=f"{len(bans)} shown")
    await ctx.send(embed=embed)


async def _cmd_setmodlog(ctx: Ctx):
    if not await _check_perm(ctx, "manage_guild"):
        return
    if not ctx.message.channel_mentions:
        await ctx.send("Usage: `!setmodlog #channel`")
        return
    channel = ctx.message.channel_mentions[0]
    guild_settings.set_modlog_channel(ctx.guild.id, channel.id)
    await ctx.send(f"Moderation actions will now be logged in {channel.mention}.")


async def _cmd_muterole(ctx: Ctx):
    if not await _check_perm(ctx, "manage_roles"):
        return
    role = ctx.message.role_mentions[0] if ctx.message.role_mentions else _resolve_role(ctx, ctx.content)
    if not role:
        await ctx.send("Usage: `!muterole <role name or @role>` — sets the role !mute/!unmute apply.")
        return
    guild_settings.set_mute_role(ctx.guild.id, role.id)
    await ctx.send(f"Mute role set to **{role.name}**.")


async def _cmd_mute(ctx: Ctx):
    if not await _check_perm(ctx, "manage_roles"):
        return
    if not ctx.message.mentions:
        await ctx.send("Usage: `!mute @member [reason]`")
        return
    role_id = guild_settings.get_mute_role(ctx.guild.id)
    role = ctx.guild.get_role(role_id) if role_id else None
    if not role:
        await ctx.send("No mute role configured yet — set one with `!muterole <role>` first.")
        return
    target = ctx.message.mentions[0]
    reason = " ".join(ctx.args[1:]) or None
    try:
        await target.add_roles(role, reason=reason)
        await ctx.send(f"Muted **{target}**." + (f" Reason: {reason}" if reason else ""))
        await _log_mod_action(ctx, "Mute", target, reason)
    except discord.Forbidden:
        await ctx.send("I can't manage that role (role hierarchy?).")


async def _cmd_unmute(ctx: Ctx):
    if not await _check_perm(ctx, "manage_roles"):
        return
    if not ctx.message.mentions:
        await ctx.send("Usage: `!unmute @member`")
        return
    role_id = guild_settings.get_mute_role(ctx.guild.id)
    role = ctx.guild.get_role(role_id) if role_id else None
    if not role:
        await ctx.send("No mute role configured yet — set one with `!muterole <role>` first.")
        return
    target = ctx.message.mentions[0]
    try:
        await target.remove_roles(role)
        await ctx.send(f"Unmuted **{target}**.")
        await _log_mod_action(ctx, "Unmute", target)
    except discord.Forbidden:
        await ctx.send("I can't manage that role (role hierarchy?).")


async def _cmd_tempban(ctx: Ctx):
    if not await _check_perm(ctx, "ban_members"):
        return
    if not ctx.message.mentions or len(ctx.args) < 2:
        await ctx.send("Usage: `!tempban @member <minutes> [reason]`")
        return
    try:
        minutes = float(ctx.args[1])
    except ValueError:
        await ctx.send("Minutes must be a number.")
        return
    minutes = max(1, min(minutes, 44640))  # capped at 31 days
    target = ctx.message.mentions[0]
    reason = " ".join(ctx.args[2:]) or None
    guild = ctx.guild
    user_id = target.id
    try:
        await target.ban(reason=reason, delete_message_days=0)
    except discord.Forbidden:
        await ctx.send("I can't ban that member (role hierarchy?).")
        return
    except discord.HTTPException as exc:
        await ctx.send(f"Couldn't ban: {exc.text}")
        return
    await ctx.send(f"Temp-banned **{target}** for {minutes:g} minute(s)." + (f" Reason: {reason}" if reason else ""))
    await _log_mod_action(ctx, "Tempban", target, f"{minutes:g}m" + (f" — {reason}" if reason else ""))

    async def _unban_later():
        try:
            await asyncio.sleep(minutes * 60)
            await guild.unban(discord.Object(id=user_id), reason="Tempban expired")
        except (discord.HTTPException, asyncio.CancelledError):
            pass

    asyncio.create_task(_unban_later())


MODERATION_COMMANDS = {
    "kick": CommandSpec("Kicks a member from the server.", _cmd_kick, "kick_members", "moderation"),
    "ban": CommandSpec("Bans a member from the server.", _cmd_ban, "ban_members", "moderation"),
    "softban": CommandSpec("Bans then unbans to purge recent messages.", _cmd_softban, "ban_members", "moderation"),
    "unban": CommandSpec("Unbans a user by ID.", _cmd_unban, "ban_members", "moderation"),
    "timeout": CommandSpec("Times out a member for N minutes.", _cmd_timeout, "moderate_members", "moderation"),
    "untimeout": CommandSpec("Removes a member's timeout.", _cmd_untimeout, "moderate_members", "moderation"),
    "warn": CommandSpec("Logs a warning against a member.", _cmd_warn, "kick_members", "moderation"),
    "warnings": CommandSpec("Shows a member's warnings.", _cmd_warnings, "manage_messages", "moderation"),
    "clearwarnings": CommandSpec("Clears a member's warnings.", _cmd_clearwarnings, "manage_messages", "moderation"),
    "purge": CommandSpec("Bulk-deletes recent messages in this channel.", _cmd_purge, "manage_messages", "moderation"),
    "slowmode": CommandSpec("Sets this channel's slowmode delay.", _cmd_slowmode, "manage_channels", "moderation"),
    "lock": CommandSpec("Stops @everyone sending in this channel.", _cmd_lock, "manage_channels", "moderation"),
    "unlock": CommandSpec("Reallows @everyone to send in this channel.", _cmd_unlock, "manage_channels", "moderation"),
    "nick": CommandSpec("Changes a member's nickname.", _cmd_nick, "manage_nicknames", "moderation"),
    "addrole": CommandSpec("Gives a member a role.", _cmd_addrole, "manage_roles", "moderation"),
    "removerole": CommandSpec("Removes a role from a member.", _cmd_removerole, "manage_roles", "moderation"),
    "createrole": CommandSpec("Creates a new role.", _cmd_createrole, "manage_roles", "moderation"),
    "deleterole": CommandSpec("Deletes a role.", _cmd_deleterole, "manage_roles", "moderation"),
    "purgeuser": CommandSpec("Deletes recent messages from a specific member.", _cmd_purgeuser, "manage_messages", "moderation"),
    "banid": CommandSpec("Bans a user by ID, even if not in the server.", _cmd_banid, "ban_members", "moderation"),
    "announce": CommandSpec("Sends a message to another channel.", _cmd_announce, "manage_messages", "moderation"),
    "pin": CommandSpec("Pins the replied-to message.", _cmd_pin, "manage_messages", "moderation"),
    "unpin": CommandSpec("Unpins the replied-to message.", _cmd_unpin, "manage_messages", "moderation"),
    "clearnick": CommandSpec("Resets a member's nickname.", _cmd_clearnick, "manage_nicknames", "moderation"),
    "banlist": CommandSpec("Lists banned users.", _cmd_banlist, "ban_members", "moderation"),
    "warnremove": CommandSpec("Removes a single warning by number.", _cmd_warnremove, "manage_messages", "moderation"),
    "setmodlog": CommandSpec("Sets the channel moderation actions are logged to.", _cmd_setmodlog, "manage_guild", "moderation"),
    "muterole": CommandSpec("Sets the role !mute/!unmute apply.", _cmd_muterole, "manage_roles", "moderation"),
    "mute": CommandSpec("Applies the configured mute role to a member.", _cmd_mute, "manage_roles", "moderation"),
    "unmute": CommandSpec("Removes the configured mute role from a member.", _cmd_unmute, "manage_roles", "moderation"),
    "tempban": CommandSpec("Bans a member, then auto-unbans them after N minutes.", _cmd_tempban, "ban_members", "moderation"),
}


# ==================== combined built-in registry ====================

BUILTIN_COMMANDS = {}
BUILTIN_COMMANDS.update(UTILITY_COMMANDS)
BUILTIN_COMMANDS.update(MODERATION_COMMANDS)
for _name, (_desc, _handler, _perm) in bot_music.MUSIC_COMMANDS.items():
    BUILTIN_COMMANDS[_name] = CommandSpec(_desc, _handler, _perm, "music")
for _name, (_desc, _handler, _perm) in bot_tts.TTS_COMMANDS.items():
    BUILTIN_COMMANDS[_name] = CommandSpec(_desc, _handler, _perm, "tts")


def name_taken(name: str) -> bool:
    return name in BUILTIN_COMMANDS or name in load_custom_commands() or bot_rp.has_command(name)


# ==================== built-in on/off toggles ====================

def _load_settings() -> dict:
    if not os.path.exists(COMMAND_SETTINGS_PATH):
        return {"disabled": []}
    try:
        with open(COMMAND_SETTINGS_PATH, "r") as f:
            data = json.load(f)
            data.setdefault("disabled", [])
            return data
    except (json.JSONDecodeError, OSError):
        return {"disabled": []}


def _save_settings(data: dict):
    with open(COMMAND_SETTINGS_PATH, "w") as f:
        json.dump(data, f, indent=2)


def is_builtin_enabled(name: str) -> bool:
    return name not in _load_settings()["disabled"]


def set_builtin_enabled(name: str, enabled: bool):
    data = _load_settings()
    disabled = set(data["disabled"])
    if enabled:
        disabled.discard(name)
    else:
        disabled.add(name)
    data["disabled"] = sorted(disabled)
    _save_settings(data)


# ==================== custom text command storage ====================

def load_custom_commands() -> dict:
    if not os.path.exists(CUSTOM_COMMANDS_PATH):
        return {}
    try:
        with open(CUSTOM_COMMANDS_PATH, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_custom_commands(data: dict):
    with open(CUSTOM_COMMANDS_PATH, "w") as f:
        json.dump(data, f, indent=2)


def set_custom_command(name: str, code: str, description: str = ""):
    data = load_custom_commands()
    existing = data.get(name, {})
    data[name] = {"code": code, "description": description, "enabled": existing.get("enabled", True)}
    save_custom_commands(data)


def set_custom_command_enabled(name: str, enabled: bool) -> bool:
    data = load_custom_commands()
    if name not in data:
        return False
    data[name]["enabled"] = enabled
    save_custom_commands(data)
    return True


def delete_custom_command(name: str) -> bool:
    data = load_custom_commands()
    if name in data:
        del data[name]
        save_custom_commands(data)
        return True
    return False


# ==================== custom command execution ====================

# Pre-imported so custom command code can use them without a separate
# install step. Stdlib modules are free (no extra package cost); the
# third-party ones below are declared in requirements.txt and installed
# by run.py alongside flask/discord.py.
_SANDBOX_MODULES = {
    "discord": discord,
    "asyncio": asyncio,
    "random": random,
    "math": math,
    "statistics": statistics,
    "re": re,
    "json": json,
    "csv": csv,
    "time": time,
    "datetime": datetime,
    "textwrap": textwrap,
    "os": os,
    "sys": sys,
    "subprocess": subprocess,
    "string": string,
    "collections": collections,
    "itertools": itertools,
    "hashlib": hashlib,
    "base64": base64,
    "uuid": uuid,
    "io": io,
    "urllib": urllib,
    "urllib_request": urllib.request,
    "urllib_parse": urllib.parse,
}

for _pip_name, _import_name in [
    ("requests", "requests"),
    ("pytz", "pytz"),
    ("python-dateutil", "dateutil"),
    ("humanize", "humanize"),
    ("emoji", "emoji"),
    ("beautifulsoup4", "bs4"),
    ("PyYAML", "yaml"),
    ("colorama", "colorama"),
    ("tabulate", "tabulate"),
    ("validators", "validators"),
]:
    try:
        _SANDBOX_MODULES[_import_name] = __import__(_import_name)
    except ImportError:
        pass


def _build_source(code: str) -> str:
    body = textwrap.indent(code, "    ") if code.strip() else "    pass"
    return f"async def __custom_command__(ctx):\n{body}\n"


def validate_custom_code(code: str):
    """Raises SyntaxError if the code doesn't compile."""
    compile(_build_source(code), "<custom_command>", "exec")


async def run_custom_command(code: str, ctx: Ctx):
    namespace = dict(_SANDBOX_MODULES)
    exec(compile(_build_source(code), "<custom_command>", "exec"), namespace)
    await namespace["__custom_command__"](ctx)


# ==================== dispatch ====================

def _music_channel_block_reason(ctx: Ctx):
    """None if music commands are allowed to run here; otherwise the
    message to send instead. Unlike RP, this isn't hidden — a server
    without a music voice channel configured gets a clear pointer rather
    than silence. Which voice channel to actually join is resolved by
    bot_music.py itself (!play/!join always target the configured
    channel, regardless of which text channel the command was typed in)."""
    if not ctx.guild:
        return "This only works in a server."
    if voice_owner.get(ctx.guild.id) == "tts":
        return "Sorry, TTS is on right now — turn it off with `!tts` first if you want music."
    if not guild_settings.get_music_channel(ctx.guild.id):
        return "No music voice channel has been set for this server yet — pick one from the Control Deck web UI."
    return None


async def handle_message(message: discord.Message, client: discord.Client):
    if message.author.bot or not message.content.startswith(COMMAND_PREFIX):
        return

    parts = message.content[len(COMMAND_PREFIX):].split()
    if not parts:
        return

    name = parts[0].lower()
    args = parts[1:]
    content = message.content[len(COMMAND_PREFIX) + len(parts[0]):].strip()
    ctx = Ctx(message, args, content, client)

    # RP's gating commands live outside the normal built-in/RP/custom
    # dispatch below since their access rules (a hardcoded owner ID; a
    # per-server allowed channel) don't fit either pattern.
    if name == "allowchannelrp":
        await bot_rp.handle_allow_channel(ctx)
        return
    if name == "rpcmds":
        await bot_rp.handle_list_command(ctx)
        return

    is_builtin = name in BUILTIN_COMMANDS
    is_rp = (not is_builtin) and bot_rp.has_command(name)
    custom = None
    if not (is_builtin or is_rp):
        custom = load_custom_commands()
        if name not in custom:
            return  # not a recognized command — nothing to cool down or run

    if _is_on_cooldown(message.author.id, name):
        return

    if is_builtin:
        if not is_builtin_enabled(name):
            return
        spec = BUILTIN_COMMANDS[name]
        try:
            if spec.category == "music":
                blocked = _music_channel_block_reason(ctx)
                if blocked:
                    await ctx.send(blocked)
                    return
            if spec.required_perm and not await _check_perm(ctx, spec.required_perm):
                return
            await spec.handler(ctx)
        except Exception as exc:  # noqa: BLE001
            await ctx.send(f"Command error: `{exc}`")
        return

    if is_rp:
        try:
            await bot_rp.handle(name, ctx)
        except Exception as exc:  # noqa: BLE001
            await ctx.send(f"Command error: `{exc}`")
        return

    entry = custom[name]
    if not entry.get("enabled", True):
        return
    try:
        await run_custom_command(entry["code"], ctx)
    except Exception as exc:  # noqa: BLE001
        await ctx.send(f"Custom command error: `{exc}`")
