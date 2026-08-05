"""
Shared NSFW-gating helpers, used by !reddit (bot_commands.py) for any
subreddit Reddit itself flags as over_18. Two independent checks:
- is_owner(ctx): only this Discord user ID is allowed to see NSFW results.
- is_nsfw_channel(ctx): only a channel Discord itself has marked NSFW —
  its own age-gating rule for this kind of content, enforced here
  regardless of who's asking.
"""

OWNER_USER_ID = 1409771422011887678


def is_owner(ctx) -> bool:
    return ctx.author.id == OWNER_USER_ID


def is_nsfw_channel(ctx) -> bool:
    is_nsfw = getattr(ctx.channel, "is_nsfw", None)
    return bool(callable(is_nsfw) and ctx.channel.is_nsfw())
