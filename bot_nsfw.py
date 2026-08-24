"""
NSFW image commands. Each one has the bot's own code fetch a fresh image
URL from api.waifu.pics (a free, public, no-key-required image API) at
the moment the command runs — nothing is bundled, cached, or picked ahead
of time. Every command refuses to run outside a channel Discord itself
has flagged NSFW (Channel Settings > Age-Restricted Channel) — that flag,
not this bot, is the source of truth for where it's allowed to post.
"""
import json
import urllib.error
import urllib.request

API_BASE = "https://api.waifu.pics/nsfw"
REQUEST_TIMEOUT = 8

# name -> upstream api.waifu.pics category
CATEGORIES = {
    "waifu": "waifu",
    "neko": "neko",
    "trap": "trap",
    "blowjob": "blowjob",
}


def _fetch(category: str) -> str | None:
    req = urllib.request.Request(
        f"{API_BASE}/{category}",
        headers={"User-Agent": "discord-bot-control-panel"},
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None
    url = data.get("url")
    return url if isinstance(url, str) and url.startswith("http") else None


def make_handler(category: str):
    async def _handler(ctx):
        if not getattr(ctx.channel, "nsfw", False):
            await ctx.send(
                "This command only works in a channel marked NSFW "
                "(Discord channel settings > Age-Restricted Channel)."
            )
            return

        url = _fetch(category)
        if not url:
            await ctx.send("Couldn't fetch anything right now — try again in a bit.")
            return

        await ctx.send(url)

    return _handler


# name -> (description, handler, required_perm) — merged into
# bot_commands.BUILTIN_COMMANDS under category "nsfw".
NSFW_COMMANDS = {
    name: (f"Fetches a random NSFW {name} image. NSFW channels only.", make_handler(category), None)
    for name, category in CATEGORIES.items()
}
