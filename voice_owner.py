"""
Tracks which feature — music or TTS — currently owns each guild's single
voice connection. discord.py only allows one voice connection per guild,
so music and TTS have to take turns instead of fighting over it; this is
the tiny shared registry both bot_music.py and bot_tts.py check before
connecting, to refuse cleanly instead of colliding.
"""
_owner: dict[int, str] = {}  # guild_id -> "music" | "tts"


def get(guild_id: int):
    return _owner.get(guild_id)


def claim(guild_id: int, owner: str):
    _owner[guild_id] = owner


def release(guild_id: int):
    _owner.pop(guild_id, None)
