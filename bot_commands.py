"""
Everything related to chat commands: the built-in set (!ping, !cmds, ...),
storage for user-defined custom commands, and the sandbox that runs custom
command code.

Custom commands run with real Python `exec` — by design. This app is a
single-user control panel for a bot the user owns and runs on their own
device, so a custom command is closer to a saved macro than untrusted
remote code. A wide set of modules is pre-imported (see _SANDBOX_MODULES)
so commands that use them work immediately without a separate install step.
"""
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

import discord

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CUSTOM_COMMANDS_PATH = os.path.join(BASE_DIR, "custom_commands.json")

COMMAND_PREFIX = "!"
COMMAND_NAME_RE = re.compile(r"^[a-z0-9_-]{1,32}$")

_START_TIME = time.monotonic()


class Ctx:
    """Passed into every command, built-in or custom."""

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


# ==================== built-in commands ====================

async def _cmd_ping(ctx: Ctx):
    await ctx.send(f"Pong! `{round(ctx.client.latency * 1000)}ms`")


async def _cmd_cmds(ctx: Ctx):
    lines = ["**Built-in:** " + ", ".join(f"!{name}" for name in BUILTIN_COMMANDS)]
    custom = load_custom_commands()
    if custom:
        lines.append("**Custom:** " + ", ".join(f"!{name}" for name in custom))
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


BUILTIN_COMMANDS = {
    "ping": ("Checks the bot's latency to Discord.", _cmd_ping),
    "cmds": ("Lists every available command.", _cmd_cmds),
    "help": ("Same as !cmds.", _cmd_cmds),
    "uptime": ("How long the bot has been connected.", _cmd_uptime),
    "avatar": ("Shows your avatar, or @mention someone else's.", _cmd_avatar),
    "userinfo": ("Shows account info for you or @mention.", _cmd_userinfo),
    "serverinfo": ("Shows info about the current server.", _cmd_serverinfo),
    "say": ("Repeats back whatever you type after it.", _cmd_say),
    "coinflip": ("Flips a coin.", _cmd_coinflip),
    "roll": ("Rolls dice, e.g. !roll 2d6.", _cmd_roll),
    "8ball": ("Ask it a yes/no question.", _cmd_8ball),
    "time": ("Shows the current server (UTC) time.", _cmd_time),
}


# ==================== custom command storage ====================

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
    data[name] = {"code": code, "description": description}
    save_custom_commands(data)


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

    if name in BUILTIN_COMMANDS:
        _, handler = BUILTIN_COMMANDS[name]
        try:
            await handler(ctx)
        except Exception as exc:  # noqa: BLE001
            await ctx.send(f"Command error: `{exc}`")
        return

    custom = load_custom_commands()
    if name in custom:
        try:
            await run_custom_command(custom[name]["code"], ctx)
        except Exception as exc:  # noqa: BLE001
            await ctx.send(f"Custom command error: `{exc}`")
