"""Yapper build of the bot.

Yapper (yapper.website) is not Discord — it's a separate platform with its
own REST API, `yb_` tokens and long-polling (see the vendored yapper.py).
The discord.py commands in bot_commands.py can't run against it, so this is
a parallel command set written on Yapper's own library.

Run it with `python run.py` and pick Yapper, or directly with
`python yapper_bot.py`. The token comes from the YAPPER_BOT_TOKEN
environment variable, or config.json ("yapper_token"), or a one-time prompt
that offers to save it.
"""
import base64
import hashlib
import json
import os
import random
import re
import threading
import time
import uuid

import yapper

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

PREFIXES = ("!", "/")
def _embed(title=None, description=None, footer=None):
    """Yapper embeds are plain dicts passed straight to the API (the library
    has no Embed builder). Discord-style shape: title/description/footer."""
    e = {}
    if title is not None:
        e["title"] = title
    if description is not None:
        e["description"] = description
    if footer is not None:
        e["footer"] = {"text": footer}
    return e


_EIGHTBALL = [
    "Yes.", "No.", "Definitely.", "Absolutely not.", "Ask again later.",
    "It is certain.", "Very doubtful.", "Signs point to yes.", "Don't count on it.",
    "Most likely.", "Cannot predict now.", "My sources say no.",
]


# ---------------- token handling ----------------

def _load_token():
    token = (os.environ.get("YAPPER_BOT_TOKEN") or "").strip()
    if token:
        return token
    try:
        with open(CONFIG_PATH) as f:
            token = (json.load(f).get("yapper_token") or "").strip()
    except (OSError, ValueError):
        token = ""
    if token:
        return token
    try:
        token = input("Paste your Yapper bot token (starts with yb_): ").strip()
    except EOFError:
        token = ""
    if token.startswith("yb_"):
        try:
            save = input("Save it to config.json so you don't have to paste it again? [y/N]: ").strip().lower()
        except EOFError:
            save = ""
        if save in ("y", "yes"):
            _save_token(token)
    return token


def _save_token(token):
    data = {}
    try:
        with open(CONFIG_PATH) as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    data["yapper_token"] = token
    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass


# ---------------- small command helpers ----------------

def _safe_calc(expr):
    """Evaluate a basic arithmetic expression safely (digits and + - * / . ( ) %)."""
    if not re.fullmatch(r"[0-9+\-*/.() %]+", expr or ""):
        return None
    try:
        return eval(expr, {"__builtins__": {}}, {})  # noqa: S307 - input is whitelisted above
    except Exception:
        return None


def _roll(spec):
    m = re.fullmatch(r"(\d*)d(\d+)", (spec or "1d6").lower().strip())
    if not m:
        return None
    count = int(m.group(1) or 1)
    sides = int(m.group(2))
    if not (1 <= count <= 100) or not (2 <= sides <= 1000):
        return None
    rolls = [random.randint(1, sides) for _ in range(count)]
    return rolls, sum(rolls)


# ---------------- poll (Yapper buttons + ephemeral confirmations) ----------------

_POLL_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣",
                "6️⃣", "7️⃣", "8️⃣", "9️⃣", "\U0001f51f"]
_POLL_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_POLL_RE = re.compile(r"(\d+)\s*([smhd])", re.IGNORECASE)
_POLL_MAX_OPTIONS = 5  # Yapper allows up to 5 buttons per row
_POLL_MIN = 10
_POLL_MAX = 7 * 86400


def _parse_duration(text):
    text = (text or "").strip().lower()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    total, matched = 0, False
    for num, unit in _POLL_RE.findall(text):
        matched = True
        total += int(num) * _POLL_UNITS[unit]
    return total if matched else None


class _Poll:
    """One live poll: tracks votes, renders the embed, and locks itself when
    the timer runs out."""

    def __init__(self, bot, question, options):
        self.bot = bot
        self.question = question
        self.options = options
        self.votes = {}  # user_id -> option index
        self.channel_id = None
        self.message_id = None
        self.closed = False

    def embed(self):
        counts = [0] * len(self.options)
        for idx in self.votes.values():
            counts[idx] += 1
        total = sum(counts)
        lines = []
        for i, opt in enumerate(self.options):
            c = counts[i]
            filled = round((c / total) * 12) if total else 0
            bar = "█" * filled + "░" * (12 - filled)
            pct = round((c / total) * 100) if total else 0
            lines.append(f"{_POLL_EMOJIS[i]} **{opt}**\n`{bar}` {c} ({pct}%)")
        if self.closed:
            top = max(counts) if counts else 0
            winners = [self.options[i] for i, c in enumerate(counts) if c == top and total]
            footer = f"{total} vote(s) - Final" + (f" - Winner: {' & '.join(winners)}" if winners else "")
        else:
            footer = f"{total} vote(s) - tap to vote"
        return _embed(title=f"\U0001f4ca {self.question}", description="\n".join(lines), footer=footer)

    def buttons(self):
        if self.closed:
            return None
        return [yapper.row(*[
            yapper.btn(f"{i + 1}", f"ypoll:{id(self)}:{i}", "secondary")
            for i in range(len(self.options))
        ])]

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.channel_id and self.message_id:
            try:
                self.bot.edit(self.channel_id, self.message_id, None,
                              embeds=[self.embed()], components=[])
            except yapper.YapperError:
                pass


_polls = {}  # id -> _Poll


# ---------------- obfuscator (text result; Yapper's bot API has no file upload) ----------------

def _try_import_obf():
    try:
        import bot_obf
        return bot_obf
    except Exception:
        return None


# ---------------- bot wiring ----------------

def build(token):
    bot = yapper.Bot(token, prefixes=PREFIXES)

    @bot.command("ping", "Is the bot alive?")
    def ping(msg):
        msg.reply("pong")

    @bot.command("say", "Repeat your text.")
    def say(msg):
        if msg.args:
            msg.say(msg.args)

    @bot.command("coinflip", "Flip a coin.")
    def coinflip(msg):
        msg.reply(random.choice(("Heads", "Tails")))

    @bot.command("roll", "Roll dice, e.g. !roll 2d6.")
    def roll(msg):
        result = _roll(msg.argv[0] if msg.argv else "1d6")
        if result is None:
            msg.reply("Use a form like `2d6` (1-100 dice, 2-1000 sides).")
            return
        rolls, total = result
        msg.reply(f"\U0001f3b2 {rolls} = **{total}**" if len(rolls) > 1 else f"\U0001f3b2 **{total}**")

    @bot.command("8ball", "Ask a yes/no question.")
    def eightball(msg):
        if not msg.args:
            msg.reply("Ask me something.")
            return
        msg.reply(random.choice(_EIGHTBALL))

    @bot.command("choose", "Pick one from a | separated list.")
    def choose(msg):
        opts = [p.strip() for p in msg.args.split("|") if p.strip()]
        if len(opts) < 2:
            msg.reply("Give me at least two options separated by `|`.")
            return
        msg.reply(random.choice(opts))

    @bot.command("reverse", "Reverse your text.")
    def reverse(msg):
        if msg.args:
            msg.reply(msg.args[::-1])

    @bot.command("calc", "Evaluate a math expression.")
    def calc(msg):
        result = _safe_calc(msg.args)
        msg.reply(f"= {result}" if result is not None else "Numbers and + - * / % ( ) only.")

    @bot.command("uuid", "Generate a random UUID.")
    def uuid_cmd(msg):
        msg.reply(f"`{uuid.uuid4()}`")

    @bot.command("password", "Generate a random password.")
    def password(msg):
        import secrets
        import string
        length = 16
        if msg.argv and msg.argv[0].isdigit():
            length = max(8, min(int(msg.argv[0]), 128))
        chars = string.ascii_letters + string.digits + "!@#$%^&*"
        msg.reply(f"`{''.join(secrets.choice(chars) for _ in range(length))}`")

    @bot.command("base64", "Encode text: !base64 <text> (prefix d: to decode).")
    def base64_cmd(msg):
        text = msg.args
        if text.startswith("d:"):
            try:
                msg.reply(base64.b64decode(text[2:].strip()).decode("utf-8", "replace"))
            except Exception:
                msg.reply("That's not valid base64.")
        elif text:
            msg.reply(f"`{base64.b64encode(text.encode()).decode()}`")

    @bot.command("hash", "SHA-256 of some text.")
    def hash_cmd(msg):
        if msg.args:
            msg.reply(f"`{hashlib.sha256(msg.args.encode()).hexdigest()}`")

    @bot.command("poll", "Timed button poll: !poll <time> Q? | A | B")
    def poll(msg):
        usage = "Usage: `!poll <time> <Question?> | A | B` - time units s/m/h/d, e.g. `!poll 10m Lunch? | Pizza | Tacos`"
        if not msg.argv:
            msg.reply(usage)
            return
        duration = _parse_duration(msg.argv[0])
        if duration is None:
            msg.reply("Start with a time (e.g. `10m`).\n" + usage)
            return
        duration = max(_POLL_MIN, min(duration, _POLL_MAX))
        rest = msg.args.split(None, 1)[1] if len(msg.args.split(None, 1)) > 1 else ""
        parts = [p.strip() for p in rest.split("|") if p.strip()]
        if len(parts) < 3:
            msg.reply("Need a question and at least 2 options.\n" + usage)
            return
        question, options = parts[0], parts[1:_POLL_MAX_OPTIONS + 1]
        poll_obj = _Poll(bot, question, options)
        _polls[id(poll_obj)] = poll_obj
        sent = msg.say(embeds=[poll_obj.embed()], components=poll_obj.buttons())
        poll_obj.channel_id = msg.channel_id
        poll_obj.message_id = sent.get("id")

        def close_later():
            poll_obj.close()
            _polls.pop(id(poll_obj), None)

        threading.Timer(duration, close_later).start()

    @bot.button("ypoll:")
    def poll_vote(i):
        try:
            _, pid, idx = i.custom_id.split(":")
            poll_obj = _polls.get(int(pid))
            idx = int(idx)
        except (ValueError, KeyError):
            i.ephemeral("That poll is no longer active.")
            return
        if poll_obj is None or poll_obj.closed:
            i.ephemeral("That poll has closed.")
            return
        poll_obj.votes[i.user_id] = idx
        try:
            bot.edit(poll_obj.channel_id, poll_obj.message_id, None,
                     embeds=[poll_obj.embed()], components=poll_obj.buttons())
        except yapper.YapperError:
            pass
        i.ephemeral(f"Your vote: {poll_obj.options[idx]} - only you can see this. Tap another to change it.")

    @bot.command("obf", "Obfuscate a small Lua snippet (inline).")
    def obf(msg):
        bot_obf = _try_import_obf()
        if bot_obf is None:
            msg.reply("Obfuscator isn't available.")
            return
        code = msg.args
        if code.startswith("```"):
            code = code[3:]
            if "\n" in code:
                first, body = code.split("\n", 1)
                if first.strip().lower() in ("lua", "luau", "roblox", ""):
                    code = body
            if code.rstrip().endswith("```"):
                code = code.rstrip()[:-3]
        code = code.strip()
        if not code:
            msg.reply("paste a lua snippet after !obf")
            return
        try:
            out = bot_obf.obfuscate(code, layers=1)
        except ValueError as exc:
            msg.reply(f"Can't obfuscate: {exc}")
            return
        # Yapper's bot API can't upload files, so the result is returned
        # inline. Messages cap at 2000 chars; bigger scripts are Discord-only
        # (the Discord build has the full file + layers configurator).
        if len(out) > 1900:
            msg.reply("too big to send inline on yapper - use the discord build for file output")
            return
        msg.reply(f"```lua\n{out}\n```")

    @bot.command("purge", "Bulk-delete recent messages: !purge <count> (needs Manage messages).")
    def purge(msg):
        if not msg.argv or not msg.argv[0].isdigit():
            msg.reply("Usage: `!purge <count>`")
            return
        count = max(1, min(int(msg.argv[0]), 100))
        # The vendored library exposes single delete, not bulk, so remove the
        # most recent messages one at a time (newest first). api() handles
        # 429 back-off for us.
        try:
            recent = bot.messages(msg.channel_id, limit=min(count + 1, 100))
            ids = [m.get("id") for m in recent if m.get("id")]
            removed = 0
            for mid in reversed(ids):  # newest first
                if removed >= count:
                    break
                try:
                    bot.delete(msg.channel_id, mid)
                    removed += 1
                except yapper.YapperError:
                    pass
        except yapper.YapperError as exc:
            msg.reply(f"Couldn't purge: {exc}")

    @bot.on("ready")
    def ready(me):
        print(f"[yapper] running as {me.get('username') or me.get('id')}")

    return bot


def main():
    token = _load_token()
    if not token.startswith("yb_"):
        print("No valid Yapper token (needs to start with yb_). Get one in Yapper Settings -> Developer.")
        return
    build(token).run()


if __name__ == "__main__":
    main()
