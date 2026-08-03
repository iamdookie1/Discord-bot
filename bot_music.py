"""
Voice/music commands. Needs PyNaCl (voice encryption), davey (Discord's
mandatory DAVE end-to-end voice encryption protocol, required by Discord
itself since March 2026), the `ffmpeg` binary on PATH, and yt-dlp (stream
extraction) to actually play audio — all optional installs, so these
commands degrade to a clear error instead of crashing if any piece is
missing.
"""
import asyncio
import shutil

import discord

try:
    import nacl  # noqa: F401
    _HAS_NACL = True
except ImportError:
    _HAS_NACL = False

try:
    import davey  # noqa: F401
    _HAS_DAVEY = True
except ImportError:
    _HAS_DAVEY = False

try:
    import yt_dlp
    _HAS_YTDLP = True
except ImportError:
    _HAS_YTDLP = False

_HAS_FFMPEG = shutil.which("ffmpeg") is not None

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}
YTDLP_OPTIONS = {"format": "bestaudio/best", "noplaylist": True, "quiet": True, "default_search": "ytsearch"}

_queues = {}       # guild_id -> list[dict(title, url, requester)]
_now_playing = {}  # guild_id -> dict


def _unavailable_reason():
    missing = []
    if not _HAS_NACL:
        missing.append("PyNaCl")
    if not _HAS_DAVEY:
        missing.append("davey")
    if not _HAS_FFMPEG:
        missing.append("the ffmpeg binary")
    if not _HAS_YTDLP:
        missing.append("yt-dlp")
    if not missing:
        return None
    return "Music needs " + ", ".join(missing) + " installed. Run `bash setup.sh` again, or install manually."


async def _extract(query: str):
    loop = asyncio.get_event_loop()

    def _run():
        with yt_dlp.YoutubeDL(YTDLP_OPTIONS) as ydl:
            info = ydl.extract_info(query, download=False)
            if "entries" in info:
                info = info["entries"][0]
            return {"title": info.get("title", query), "url": info["url"]}

    return await loop.run_in_executor(None, _run)


async def _play_next(guild: discord.Guild, voice_client: discord.VoiceClient):
    queue = _queues.get(guild.id, [])
    if not queue:
        _now_playing.pop(guild.id, None)
        return
    track = queue.pop(0)
    _now_playing[guild.id] = track
    source = discord.FFmpegPCMAudio(track["url"], **FFMPEG_OPTIONS)

    def _after(_error):
        fut = asyncio.run_coroutine_threadsafe(_play_next(guild, voice_client), voice_client.loop)
        try:
            fut.result()
        except Exception:
            pass

    voice_client.play(source, after=_after)


async def _cmd_join(ctx):
    reason = _unavailable_reason()
    if reason:
        await ctx.send(reason)
        return
    if not ctx.guild:
        await ctx.send("This only works in a server.")
        return
    if not ctx.author.voice or not ctx.author.voice.channel:
        await ctx.send("Join a voice channel first.")
        return
    channel = ctx.author.voice.channel
    if ctx.guild.voice_client:
        await ctx.guild.voice_client.move_to(channel)
    else:
        await channel.connect()
    await ctx.send(f"Joined **{channel.name}**.")


async def _cmd_leave(ctx):
    if ctx.guild and ctx.guild.voice_client:
        await ctx.guild.voice_client.disconnect(force=True)
        _queues.pop(ctx.guild.id, None)
        _now_playing.pop(ctx.guild.id, None)
        await ctx.send("Left the voice channel.")
    else:
        await ctx.send("I'm not in a voice channel.")


async def _cmd_play(ctx):
    reason = _unavailable_reason()
    if reason:
        await ctx.send(reason)
        return
    if not ctx.guild:
        await ctx.send("This only works in a server.")
        return
    if not ctx.content:
        await ctx.send("Usage: `!play <song name or URL>`")
        return

    vc = ctx.guild.voice_client
    if not vc:
        if not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.send("Join a voice channel first, or use `!join`.")
            return
        vc = await ctx.author.voice.channel.connect()

    await ctx.send("Looking that up...")
    try:
        track = await _extract(ctx.content)
    except Exception as exc:  # noqa: BLE001
        await ctx.send(f"Couldn't find that: {exc}")
        return
    track["requester"] = str(ctx.author)
    _queues.setdefault(ctx.guild.id, []).append(track)

    if not vc.is_playing() and not vc.is_paused():
        await _play_next(ctx.guild, vc)
        await ctx.send(f"Now playing **{track['title']}**.")
    else:
        await ctx.send(f"Queued **{track['title']}** (position {len(_queues[ctx.guild.id])}).")


async def _cmd_pause(ctx):
    vc = ctx.guild.voice_client if ctx.guild else None
    if vc and vc.is_playing():
        vc.pause()
        await ctx.send("Paused.")
    else:
        await ctx.send("Nothing is playing.")


async def _cmd_resume(ctx):
    vc = ctx.guild.voice_client if ctx.guild else None
    if vc and vc.is_paused():
        vc.resume()
        await ctx.send("Resumed.")
    else:
        await ctx.send("Nothing is paused.")


async def _cmd_skip(ctx):
    vc = ctx.guild.voice_client if ctx.guild else None
    if vc and (vc.is_playing() or vc.is_paused()):
        vc.stop()  # triggers _after -> _play_next
        await ctx.send("Skipped.")
    else:
        await ctx.send("Nothing is playing.")


async def _cmd_stop(ctx):
    vc = ctx.guild.voice_client if ctx.guild else None
    if vc:
        _queues[ctx.guild.id] = []
        vc.stop()
        await ctx.send("Stopped and cleared the queue.")
    else:
        await ctx.send("I'm not in a voice channel.")


async def _cmd_queue(ctx):
    if not ctx.guild:
        await ctx.send("This only works in a server.")
        return
    queue = _queues.get(ctx.guild.id, [])
    now = _now_playing.get(ctx.guild.id)
    lines = []
    if now:
        lines.append(f"**Now playing:** {now['title']}")
    if queue:
        lines.append("**Up next:**")
        lines += [f"{i + 1}. {t['title']}" for i, t in enumerate(queue[:10])]
    if not lines:
        lines = ["Queue is empty."]
    await ctx.send("\n".join(lines))


# (description, handler, required_perm) — merged into bot_commands.BUILTIN_COMMANDS
MUSIC_COMMANDS = {
    "join": ("Joins your current voice channel.", _cmd_join, None),
    "leave": ("Leaves the voice channel.", _cmd_leave, None),
    "play": ("Plays a song by name or URL (queues if already playing).", _cmd_play, None),
    "pause": ("Pauses the current track.", _cmd_pause, None),
    "resume": ("Resumes the paused track.", _cmd_resume, None),
    "skip": ("Skips to the next track in queue.", _cmd_skip, None),
    "stop": ("Stops playback and clears the queue.", _cmd_stop, None),
    "queue": ("Shows what's playing and queued next.", _cmd_queue, None),
}
