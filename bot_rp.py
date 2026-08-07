"""
Roleplay commands (!kiss, !hug, ...). Each one, built-in or custom, posts a
random GIF from up to 10 configured URLs, with the accompanying text
picked randomly from up to 10 custom message templates (using {author}
and {target} as placeholders) — or a default "{author} verbs {target}!"
line if no custom messages are set. If no GIFs are configured yet, it
sends an error telling the user to add some from the RP tab.
"""
import json
import os
import random

import discord

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RP_COMMANDS_PATH = os.path.join(BASE_DIR, "rp_commands.json")

MAX_GIFS = 10
MAX_MESSAGES = 10

# name -> (verb used in the sentence, default description)
BUILTIN_RP_ACTIONS = {
    "kiss": ("kisses", "Kiss someone."),
    "hug": ("hugs", "Hug someone."),
    "slap": ("slaps", "Slap someone."),
    "pat": ("pats", "Pat someone on the head."),
    "cuddle": ("cuddles", "Cuddle with someone."),
    "poke": ("pokes", "Poke someone."),
    "bonk": ("bonks", "Bonk someone."),
    "highfive": ("high-fives", "High-five someone."),
    "tickle": ("tickles", "Tickle someone."),
    "wave": ("waves at", "Wave at someone."),
}


def _load() -> dict:
    if not os.path.exists(RP_COMMANDS_PATH):
        return {}
    try:
        with open(RP_COMMANDS_PATH, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict):
    with open(RP_COMMANDS_PATH, "w") as f:
        json.dump(data, f, indent=2)


def _clean_list(items, limit: int) -> list:
    if not isinstance(items, list):
        return []
    return [i.strip() for i in items if isinstance(i, str) and i.strip()][:limit]


def _entry(name: str) -> dict:
    data = _load()
    return data.get(name, {
        "gifs": [],
        "messages": [],
        "enabled": True,
        "description": "",
        "custom": name not in BUILTIN_RP_ACTIONS,
    })


def has_command(name: str) -> bool:
    return name in BUILTIN_RP_ACTIONS or name in _load()


def set_enabled(name: str, enabled: bool):
    data = _load()
    entry = data.get(name, {"gifs": [], "messages": [], "description": "", "custom": name not in BUILTIN_RP_ACTIONS})
    entry["enabled"] = enabled
    data[name] = entry
    _save(data)


def set_gifs(name: str, gifs: list):
    data = _load()
    entry = data.get(name, {"enabled": True, "messages": [], "description": "", "custom": name not in BUILTIN_RP_ACTIONS})
    entry["gifs"] = _clean_list(gifs, MAX_GIFS)
    data[name] = entry
    _save(data)


def set_messages(name: str, messages: list):
    data = _load()
    entry = data.get(name, {"enabled": True, "gifs": [], "description": "", "custom": name not in BUILTIN_RP_ACTIONS})
    entry["messages"] = _clean_list(messages, MAX_MESSAGES)
    data[name] = entry
    _save(data)


def create_custom(name: str, description: str, gifs: list):
    data = _load()
    data[name] = {
        "gifs": _clean_list(gifs, MAX_GIFS),
        "messages": [],
        "description": description,
        "enabled": True,
        "custom": True,
    }
    _save(data)


def delete_custom(name: str) -> bool:
    data = _load()
    entry = data.get(name)
    if not entry or not entry.get("custom"):
        return False
    del data[name]
    _save(data)
    return True


def list_commands() -> list:
    data = _load()
    names = set(BUILTIN_RP_ACTIONS) | set(data)
    out = []
    for name in sorted(names):
        entry = data.get(name, {})
        default_desc = BUILTIN_RP_ACTIONS.get(name, ("", ""))[1]
        out.append({
            "name": name,
            "description": entry.get("description") or default_desc,
            "gifs": entry.get("gifs", []),
            "messages": entry.get("messages", []),
            "enabled": entry.get("enabled", True),
            "custom": entry.get("custom", name not in BUILTIN_RP_ACTIONS),
        })
    return out


async def handle(name: str, ctx):
    entry = _entry(name)
    if not entry.get("enabled", True):
        return

    if not ctx.message.mentions:
        await ctx.send(f"Mention someone: `!{name} @user`")
        return
    target = ctx.message.mentions[0]

    gifs = entry.get("gifs", [])
    if not gifs:
        await ctx.send(f"No GIFs set for `!{name}` yet — add up to {MAX_GIFS} from the RP tab.")
        return
    gif = random.choice(gifs)

    messages = entry.get("messages", [])
    if messages:
        text = random.choice(messages).replace("{author}", ctx.author.display_name).replace("{target}", target.display_name)
    else:
        verb = BUILTIN_RP_ACTIONS.get(name, (name + "s",))[0]
        text = f"**{ctx.author.display_name}** {verb} **{target.display_name}**!"

    embed = discord.Embed(description=text)
    embed.set_image(url=gif)
    await ctx.send(embed=embed)
