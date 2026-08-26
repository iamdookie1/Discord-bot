"""
Roleplay commands (!kiss, !hug, ...). Each one, built-in or custom, posts a
random GIF from up to 10 configured slots — either a remote URL or a file
uploaded from the device (images/GIFs are kept as-is, videos are converted
to a GIF with ffmpeg) — with the accompanying text picked randomly from up
to 10 custom message templates (using {author} and {target} as
placeholders) — or a default "{author} verbs {target}!" line if no custom
messages are set. If no GIFs are configured yet, it sends an error telling
the user to add some from the RP tab.

Uploaded files are stored locally and referenced in the gifs list as
"local:<filename>" rather than a URL, since Discord's servers can't reach
this device to fetch an embed image URL — those get sent as a real file
attachment instead (see handle()).

RP is hidden everywhere by default: it's left out of !cmds/!help, and every
RP command (built-in or custom) is a silent no-op outside the one channel
each server's owner-designated account has allowed via !allowchannelrp.
!rpcmds is the only way to see what's available, and only works in that
same allowed channel. There's no web UI control for the allowed channel —
it's chat-only, gated to OWNER_ID, on purpose.
"""
import json
import os
import random
import shutil
import subprocess
import uuid

import discord

import guild_settings

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RP_COMMANDS_PATH = os.path.join(BASE_DIR, "rp_commands.json")
RP_MEDIA_DIR = os.path.join(BASE_DIR, "rp_media")
os.makedirs(RP_MEDIA_DIR, exist_ok=True)

MAX_GIFS = 10
MAX_MESSAGES = 10
LOCAL_PREFIX = "local:"
OWNER_ID = 1409771422011887678

_IMAGE_EXTS = {"gif", "png", "jpg", "jpeg", "webp"}
_VIDEO_EXTS = {"mp4", "mov", "webm", "mkv", "avi", "m4v"}
_MAX_UPLOAD_BYTES = 30 * 1024 * 1024

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


def save_upload(data: bytes, original_filename: str):
    """Saves an uploaded image/GIF as-is, or converts an uploaded video to a
    GIF with ffmpeg. Returns (ok, url_or_error) — on success url_or_error is
    a "local:<filename>" string suitable for storing in a gifs list."""
    if not data:
        return False, "That file looks empty."
    if len(data) > _MAX_UPLOAD_BYTES:
        return False, "File too large (max 30MB)."

    ext = original_filename.rsplit(".", 1)[-1].lower() if "." in original_filename else ""
    token = uuid.uuid4().hex

    if ext in _IMAGE_EXTS:
        out_name = f"{token}.{ext}"
        with open(os.path.join(RP_MEDIA_DIR, out_name), "wb") as f:
            f.write(data)
        return True, f"{LOCAL_PREFIX}{out_name}"

    if ext in _VIDEO_EXTS:
        if not shutil.which("ffmpeg"):
            return False, "Converting video to a GIF needs the ffmpeg binary, which isn't installed."
        in_path = os.path.join(RP_MEDIA_DIR, f"{token}_src.{ext}")
        out_name = f"{token}.gif"
        out_path = os.path.join(RP_MEDIA_DIR, out_name)
        with open(in_path, "wb") as f:
            f.write(data)
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", in_path, "-t", "8",
                 "-vf", "fps=15,scale=320:-1:flags=lanczos", out_path],
                capture_output=True, timeout=60, check=True,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return False, "Couldn't convert that video to a GIF."
        finally:
            if os.path.exists(in_path):
                os.remove(in_path)
        return True, f"{LOCAL_PREFIX}{out_name}"

    return False, "Unsupported file type — use an image (gif/png/jpg/webp) or video (mp4/mov/webm/mkv)."


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


def _channel_allowed(ctx) -> bool:
    if not ctx.guild:
        return False
    allowed = guild_settings.get_rp_channel(ctx.guild.id)
    return allowed is not None and ctx.channel.id == allowed


async def handle_allow_channel(ctx):
    """!allowchannelrp — owner-only, sets this server's one allowed RP
    channel to the channel it was run in. Silently ignored for anyone else,
    so non-owners get no hint the command exists."""
    if ctx.author.id != OWNER_ID or not ctx.guild:
        return
    guild_settings.set_rp_channel(ctx.guild.id, ctx.channel.id)
    await ctx.send(f"RP commands are now allowed in {ctx.channel.mention} for this server.")


async def handle_list_command(ctx):
    """!rpcmds — the only way to see RP commands; only works in the
    server's allowed channel, silent no-op everywhere else."""
    if not _channel_allowed(ctx):
        return
    cmds = [c for c in list_commands() if c["enabled"]]
    if not cmds:
        await ctx.send("No RP commands are enabled right now.")
        return
    await ctx.send("**RP commands available here:** " + ", ".join(f"!{c['name']}" for c in cmds))


async def handle(name: str, ctx):
    if not _channel_allowed(ctx):
        return  # fully hidden/inert outside the server's allowed RP channel

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

    embed = discord.Embed(description=text, color=discord.Color(0xFFB454))

    if gif.startswith(LOCAL_PREFIX):
        filename = gif[len(LOCAL_PREFIX):]
        path = os.path.join(RP_MEDIA_DIR, filename)
        if not os.path.isfile(path):
            await ctx.send(f"An uploaded GIF for `!{name}` is missing on disk — remove it and re-upload from the RP tab.")
            return
        embed.set_image(url=f"attachment://{filename}")
        await ctx.send(embed=embed, file=discord.File(path, filename=filename))
    else:
        embed.set_image(url=gif)
        await ctx.send(embed=embed)
