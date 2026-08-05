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

try:
    import requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

import bot_music
import bot_nsfw
import bot_rp

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


# ==================== utility commands ====================

async def _cmd_ping(ctx: Ctx):
    await ctx.send(f"Pong! `{round(ctx.client.latency * 1000)}ms`")


async def _cmd_cmds(ctx: Ctx):
    by_category = {}
    for name, spec in BUILTIN_COMMANDS.items():
        by_category.setdefault(spec.category, []).append(name)
    lines = [f"**{cat.title()}:** " + ", ".join(f"!{n}" for n in sorted(names)) for cat, names in by_category.items()]
    rp_names = [c["name"] for c in bot_rp.list_commands()]
    if rp_names:
        lines.append("**Rp:** " + ", ".join(f"!{n}" for n in rp_names))
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
    await ctx.send(str(target.display_avatar.url))


async def _cmd_userinfo(ctx: Ctx):
    target = ctx.message.mentions[0] if ctx.message.mentions else ctx.author
    created = target.created_at.strftime("%Y-%m-%d")
    await ctx.send(f"**{target}**\nID: `{target.id}`\nAccount created: `{created}`")


async def _cmd_serverinfo(ctx: Ctx):
    g = ctx.guild
    if not g:
        await ctx.send("This only works in a server.")
        return
    await ctx.send(f"**{g.name}**\nID: `{g.id}`\nMembers: `{g.member_count}`\nOwner: `{g.owner}`")


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
    await ctx.send(f"Okay, I'll remind you in {minutes:g} minute(s).")

    async def _fire():
        await asyncio.sleep(minutes * 60)
        try:
            await ctx.channel.send(f"{ctx.author.mention} reminder: {text}")
        except discord.HTTPException:
            pass

    asyncio.create_task(_fire())


_SUBREDDIT_RE = re.compile(r"^[A-Za-z0-9_]{2,24}$")
_REDDIT_HEADERS = {"User-Agent": "ControlDeckDiscordBot/1.0 (personal use)"}
_REDDIT_IMAGE_RE = re.compile(r"\.(jpg|jpeg|png|gif)$", re.IGNORECASE)


def _reddit_post_embed(subreddit: str, post: dict) -> discord.Embed:
    title = (post.get("title") or "(no title)")[:256]
    permalink = "https://www.reddit.com" + post.get("permalink", "")
    embed = discord.Embed(title=title, url=permalink, color=discord.Color.orange())
    embed.set_footer(text=f"r/{subreddit} · {post.get('score', 0)} upvotes · u/{post.get('author', '?')}")

    url = post.get("url_overridden_by_dest") or post.get("url") or ""
    if post.get("post_hint") == "image" or _REDDIT_IMAGE_RE.search(url):
        embed.set_image(url=url)
    elif post.get("is_self") and post.get("selftext"):
        text = post["selftext"]
        embed.description = text[:400] + ("…" if len(text) > 400 else "")
    elif url:
        embed.description = url

    return embed


async def _cmd_reddit(ctx: Ctx):
    if not _HAS_REQUESTS:
        await ctx.send("Reddit fetching isn't available — the `requests` package didn't install. Run `bash setup.sh` again.")
        return
    if not ctx.args:
        await ctx.send("Usage: `!reddit <subreddit>`")
        return

    subreddit = ctx.args[0].strip().lstrip("/")
    if subreddit.lower().startswith("r/"):
        subreddit = subreddit[2:]
    if not _SUBREDDIT_RE.match(subreddit):
        await ctx.send("That doesn't look like a valid subreddit name.")
        return

    loop = asyncio.get_event_loop()

    def _fetch_about():
        r = requests.get(f"https://www.reddit.com/r/{subreddit}/about.json", headers=_REDDIT_HEADERS, timeout=8)
        r.raise_for_status()
        return r.json().get("data", {})

    try:
        about = await loop.run_in_executor(None, _fetch_about)
    except Exception:
        await ctx.send(f"Couldn't find r/{subreddit} — check the name and try again.")
        return

    if about.get("over18"):
        if not bot_nsfw.is_owner(ctx):
            return  # silent — doesn't confirm to anyone else that this even ran
        if not bot_nsfw.is_nsfw_channel(ctx):
            await ctx.send(f"r/{subreddit} is NSFW — this only works in a channel marked NSFW in Discord's channel settings.")
            return

    def _fetch_posts():
        r = requests.get(f"https://www.reddit.com/r/{subreddit}/hot.json?limit=25", headers=_REDDIT_HEADERS, timeout=8)
        r.raise_for_status()
        return [c["data"] for c in r.json().get("data", {}).get("children", []) if not c["data"].get("stickied")]

    try:
        posts = await loop.run_in_executor(None, _fetch_posts)
    except Exception:
        await ctx.send(f"Couldn't load posts from r/{subreddit}.")
        return

    # Even in an SFW subreddit, individual posts can be flagged NSFW —
    # apply the same owner + nsfw-channel gate to those unless already cleared above.
    authorized = bot_nsfw.is_owner(ctx) and bot_nsfw.is_nsfw_channel(ctx)
    if not authorized:
        posts = [p for p in posts if not p.get("over_18")]

    if not posts:
        await ctx.send(f"No posts found in r/{subreddit} right now.")
        return

    await ctx.send(embed=_reddit_post_embed(subreddit, random.choice(posts)))


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
    "reddit": CommandSpec(
        "Fetches a random post from a subreddit. NSFW subreddits/posts only work for the owner in an NSFW channel.",
        _cmd_reddit, None, "utility",
    ),
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


async def _cmd_warnings(ctx: Ctx):
    if not await _check_perm(ctx, "manage_messages"):
        return
    target = ctx.message.mentions[0] if ctx.message.mentions else ctx.author
    key = f"{ctx.guild.id}:{target.id}"
    entries = _load_warnings().get(key, [])
    if not entries:
        await ctx.send(f"**{target}** has no warnings.")
        return
    lines = [f"{i + 1}. {e['reason']} (by {e['by']})" for i, e in enumerate(entries[-10:])]
    await ctx.send(f"**{target}**'s warnings ({len(entries)} total):\n" + "\n".join(lines))


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
}


# ==================== combined built-in registry ====================

BUILTIN_COMMANDS = {}
BUILTIN_COMMANDS.update(UTILITY_COMMANDS)
BUILTIN_COMMANDS.update(MODERATION_COMMANDS)
for _name, (_desc, _handler, _perm) in bot_music.MUSIC_COMMANDS.items():
    BUILTIN_COMMANDS[_name] = CommandSpec(_desc, _handler, _perm, "music")


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
