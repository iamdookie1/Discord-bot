"""
Per-server settings that persist across restarts — e.g. which channel RP
commands are restricted to, or which channel music commands are restricted
to. Keyed by Discord guild ID so each server the bot is in has its own
independent config.
"""
import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GUILD_SETTINGS_PATH = os.path.join(BASE_DIR, "guild_settings.json")

_DEFAULTS = {
    "rp_channel": None,
    "music_channel": None,
}


def _load() -> dict:
    if not os.path.exists(GUILD_SETTINGS_PATH):
        return {}
    try:
        with open(GUILD_SETTINGS_PATH, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict):
    with open(GUILD_SETTINGS_PATH, "w") as f:
        json.dump(data, f, indent=2)


def get_settings(guild_id) -> dict:
    data = _load()
    entry = data.get(str(guild_id), {})
    return {**_DEFAULTS, **entry}


def set_setting(guild_id, key: str, value):
    if key not in _DEFAULTS:
        raise ValueError(f"Unknown guild setting: {key}")
    data = _load()
    guild_key = str(guild_id)
    entry = {**_DEFAULTS, **data.get(guild_key, {})}
    entry[key] = value
    data[guild_key] = entry
    _save(data)


def get_rp_channel(guild_id):
    return get_settings(guild_id).get("rp_channel")


def set_rp_channel(guild_id, channel_id):
    set_setting(guild_id, "rp_channel", channel_id)


def get_music_channel(guild_id):
    return get_settings(guild_id).get("music_channel")


def set_music_channel(guild_id, channel_id):
    set_setting(guild_id, "music_channel", channel_id)
