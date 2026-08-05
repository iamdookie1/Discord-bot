"""
NSFW commands — locked to a single Discord user ID, and only usable in a
channel Discord itself has marked NSFW (Discord's own age-gating rule for
explicit content, enforced here regardless of who's allowed to trigger
the command).

This module never ships with any pre-built commands or content: every
!command here is one you create yourself from the NSFW tab, including
whatever media URLs you attach to it — this app doesn't fetch, generate,
or bundle any content of its own. Structurally identical to bot_rp.py,
just locked down harder.
"""
import json
import os
import random

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
NSFW_COMMANDS_PATH = os.path.join(BASE_DIR, "nsfw_commands.json")

MAX_MEDIA = 5
OWNER_USER_ID = 1409771422011887678


def _load() -> dict:
    if not os.path.exists(NSFW_COMMANDS_PATH):
        return {}
    try:
        with open(NSFW_COMMANDS_PATH, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict):
    with open(NSFW_COMMANDS_PATH, "w") as f:
        json.dump(data, f, indent=2)


def _clean_media(urls) -> list:
    if not isinstance(urls, list):
        return []
    return [u.strip() for u in urls if isinstance(u, str) and u.strip()][:MAX_MEDIA]


def has_command(name: str) -> bool:
    return name in _load()


def create_custom(name: str, description: str, media: list):
    data = _load()
    data[name] = {"media": _clean_media(media), "description": description, "enabled": True}
    _save(data)


def set_media(name: str, media: list):
    data = _load()
    entry = data.get(name, {"description": "", "enabled": True})
    entry["media"] = _clean_media(media)
    data[name] = entry
    _save(data)


def set_enabled(name: str, enabled: bool) -> bool:
    data = _load()
    if name not in data:
        return False
    data[name]["enabled"] = enabled
    _save(data)
    return True


def delete_custom(name: str) -> bool:
    data = _load()
    if name in data:
        del data[name]
        _save(data)
        return True
    return False


def list_commands() -> list:
    data = _load()
    return [
        {
            "name": name,
            "description": entry.get("description", ""),
            "media": entry.get("media", []),
            "enabled": entry.get("enabled", True),
        }
        for name, entry in sorted(data.items())
    ]


async def handle(name: str, ctx):
    entry = _load().get(name)
    if not entry or not entry.get("enabled", True):
        return

    if ctx.author.id != OWNER_USER_ID:
        return  # silent on purpose — doesn't confirm to anyone else that this command exists

    channel = ctx.channel
    is_nsfw = getattr(channel, "is_nsfw", None)
    if not (callable(is_nsfw) and channel.is_nsfw()):
        await ctx.send("This only works in a channel marked NSFW in Discord's channel settings.")
        return

    media = entry.get("media", [])
    if not media:
        await ctx.send(f"No media set for `!{name}` yet — add up to {MAX_MEDIA} from the NSFW tab.")
        return

    await ctx.send(random.choice(media))
