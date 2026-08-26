"""
Voice/music commands. Needs PyNaCl (voice encryption), davey (Discord's
mandatory DAVE end-to-end voice encryption protocol, required by Discord
itself since March 2026), the `ffmpeg` binary on PATH, and yt-dlp (stream
extraction) to actually play audio — all optional installs, so these
commands degrade to a clear error instead of crashing if any piece is
missing.

Playback state lives in GuildMusicState, one per guild. The Discord-side
"now playing" menu (an embed + buttons on a single message, reused across
the whole queue) is edited immediately on any state change and otherwise
ticks every MENU_REFRESH_SECONDS for the elapsed-time counter — that
interval is deliberately conservative (Discord rate-limits message edits
to roughly 5 per 5 seconds). The equivalent web controls in the Flask UI
don't go through Discord's API at all, so they can poll far more often;
see get_state_dict()/web_*() below, called from app.py.
"""
import asyncio
import shutil
import time

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

MENU_REFRESH_SECONDS = 5
IDLE_DISCONNECT_SECONDS = 5 * 60
VOLUME_STEP = 0.1
MIN_VOLUME = 0.0
MAX_VOLUME = 2.0
PROGRESS_BAR_LENGTH = 18
LOOP_MODES = ["off", "track", "queue"]


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


# ==================== per-guild playback state ====================

class GuildMusicState:
    def __init__(self):
        self.queue = []            # list[dict(title, url, duration, requester)]
        self.current = None        # dict, same shape as a queue entry, or None
        self.source = None         # discord.PCMVolumeTransformer of the current track
        self.volume = 1.0          # 0.0 - 2.0
        self.loop_mode = "off"     # off | track | queue
        self.position = 0.0        # seconds played before the current "run" started
        self.resumed_at = None     # time.monotonic() when playback last (re)started
        self.is_paused = False
        self.menu_message = None   # discord.Message — the persistent now-playing menu
        self.refresh_task = None   # asyncio.Task ticking the menu's elapsed time
        self.idle_task = None      # asyncio.Task that disconnects after IDLE_DISCONNECT_SECONDS


_states: dict[int, GuildMusicState] = {}


def _state(guild_id: int) -> GuildMusicState:
    return _states.setdefault(guild_id, GuildMusicState())


def _elapsed(state: GuildMusicState) -> float:
    if state.current is None:
        return 0.0
    if state.is_paused or state.resumed_at is None:
        return state.position
    return state.position + (time.monotonic() - state.resumed_at)


def _reset_position(state: GuildMusicState):
    state.position = 0.0
    state.resumed_at = time.monotonic()
    state.is_paused = False


def _adjust_volume(state: GuildMusicState, delta: float):
    state.volume = round(min(max(state.volume + delta, MIN_VOLUME), MAX_VOLUME), 2)
    if state.source is not None:
        state.source.volume = state.volume


# ==================== formatting ====================

def _fmt_time(seconds) -> str:
    seconds = max(0, int(seconds or 0))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _progress_bar(elapsed: float, duration) -> str | None:
    if not duration:
        return None
    ratio = min(max(elapsed / duration, 0.0), 1.0)
    filled = min(int(ratio * PROGRESS_BAR_LENGTH), PROGRESS_BAR_LENGTH - 1)
    return "▬" * filled + "🔘" + "▬" * (PROGRESS_BAR_LENGTH - filled - 1)


def _build_embed(state: GuildMusicState) -> discord.Embed:
    track = state.current
    if not track:
        return discord.Embed(
            title="Nothing playing",
            description="Use `!play <song>` to start something.",
            color=discord.Color.dark_grey(),
        )

    elapsed = _elapsed(state)
    duration = track.get("duration")
    embed = discord.Embed(title="🎵 Now Playing", description=f"**{track['title']}**", color=discord.Color(0xFFB454))

    bar = _progress_bar(elapsed, duration)
    if bar:
        embed.add_field(name="Progress", value=f"{bar}\n`{_fmt_time(elapsed)} / {_fmt_time(duration)}`", inline=False)
    else:
        embed.add_field(name="Elapsed", value=f"`{_fmt_time(elapsed)}`", inline=False)

    embed.add_field(name="Status", value="⏸️ Paused" if state.is_paused else "▶️ Playing", inline=True)
    embed.add_field(name="Volume", value=f"{round(state.volume * 100)}%", inline=True)
    embed.add_field(name="Loop", value=state.loop_mode.capitalize(), inline=True)

    if state.queue:
        upcoming = ", ".join(t["title"] for t in state.queue[:3])
        more = f" (+{len(state.queue) - 3} more)" if len(state.queue) > 3 else ""
        embed.add_field(name="Up next", value=f"{upcoming}{more}", inline=False)

    embed.set_footer(text=f"Requested by {track.get('requester', '?')}")
    return embed


# ==================== the interactive menu ====================

async def _same_voice_channel(interaction: discord.Interaction, guild: discord.Guild) -> bool:
    vc = guild.voice_client if guild else None
    user_voice = getattr(interaction.user, "voice", None)
    if not vc or not user_voice or not user_voice.channel or user_voice.channel.id != vc.channel.id:
        await interaction.response.send_message("Join the same voice channel to control playback.", ephemeral=True)
        return False
    return True


class MusicMenuView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self._sync_labels()

    def _sync_labels(self):
        state = _state(self.guild_id)
        self.pause_btn.label = "▶️ Resume" if state.is_paused else "⏸️ Pause"
        self.loop_btn.label = f"🔁 Loop: {state.loop_mode.capitalize()}"

    @discord.ui.button(label="⏸️ Pause", style=discord.ButtonStyle.secondary, row=0)
    async def pause_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if not await _same_voice_channel(interaction, guild):
            return
        state = _state(guild.id)
        vc = guild.voice_client
        if not vc or not state.current:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)
            return
        if state.is_paused:
            vc.resume()
            state.is_paused = False
            state.resumed_at = time.monotonic()
        else:
            vc.pause()
            state.position = _elapsed(state)
            state.is_paused = True
        self._sync_labels()
        await interaction.response.edit_message(embed=_build_embed(state), view=self)

    @discord.ui.button(label="⏭️ Skip", style=discord.ButtonStyle.secondary, row=0)
    async def skip_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if not await _same_voice_channel(interaction, guild):
            return
        vc = guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()
            await interaction.response.send_message("Skipped.", ephemeral=True)
        else:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)

    @discord.ui.button(label="⏹️ Stop", style=discord.ButtonStyle.danger, row=0)
    async def stop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if not await _same_voice_channel(interaction, guild):
            return
        state = _state(guild.id)
        vc = guild.voice_client
        state.queue.clear()
        state.loop_mode = "off"
        state.current = None
        if vc:
            vc.stop()
        await interaction.response.send_message("Stopped and cleared the queue.", ephemeral=True)

    @discord.ui.button(label="🔉 Vol -", style=discord.ButtonStyle.secondary, row=1)
    async def vol_down_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if not await _same_voice_channel(interaction, guild):
            return
        state = _state(guild.id)
        _adjust_volume(state, -VOLUME_STEP)
        await interaction.response.edit_message(embed=_build_embed(state), view=self)

    @discord.ui.button(label="🔊 Vol +", style=discord.ButtonStyle.secondary, row=1)
    async def vol_up_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if not await _same_voice_channel(interaction, guild):
            return
        state = _state(guild.id)
        _adjust_volume(state, VOLUME_STEP)
        await interaction.response.edit_message(embed=_build_embed(state), view=self)

    @discord.ui.button(label="🔁 Loop: Off", style=discord.ButtonStyle.secondary, row=1)
    async def loop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if not await _same_voice_channel(interaction, guild):
            return
        state = _state(guild.id)
        state.loop_mode = LOOP_MODES[(LOOP_MODES.index(state.loop_mode) + 1) % len(LOOP_MODES)]
        self._sync_labels()
        await interaction.response.edit_message(embed=_build_embed(state), view=self)

    @discord.ui.button(label="📋 Queue", style=discord.ButtonStyle.secondary, row=1)
    async def queue_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = _state(interaction.guild.id)
        if not state.queue:
            await interaction.response.send_message("Queue is empty.", ephemeral=True)
            return
        lines = [f"{i + 1}. {t['title']}" for i, t in enumerate(state.queue[:10])]
        more = f"\n...and {len(state.queue) - 10} more" if len(state.queue) > 10 else ""
        await interaction.response.send_message("**Up next:**\n" + "\n".join(lines) + more, ephemeral=True)


async def _refresh_menu(guild: discord.Guild, state: GuildMusicState):
    if state.menu_message is None:
        return
    try:
        await state.menu_message.edit(embed=_build_embed(state), view=MusicMenuView(guild.id))
    except (discord.NotFound, discord.HTTPException):
        state.menu_message = None
        _cancel_refresh_task(state)


def _ensure_refresh_task(guild: discord.Guild, state: GuildMusicState):
    if state.refresh_task and not state.refresh_task.done():
        return
    state.refresh_task = asyncio.create_task(_refresh_loop(guild, state))


def _cancel_refresh_task(state: GuildMusicState):
    if state.refresh_task and not state.refresh_task.done():
        state.refresh_task.cancel()
    state.refresh_task = None


async def _refresh_loop(guild: discord.Guild, state: GuildMusicState):
    try:
        while state.current is not None and state.menu_message is not None:
            await asyncio.sleep(MENU_REFRESH_SECONDS)
            if state.current is None or state.menu_message is None:
                break
            await _refresh_menu(guild, state)
    except asyncio.CancelledError:
        pass


async def _show_menu(channel, guild: discord.Guild, state: GuildMusicState):
    state.menu_message = await channel.send(embed=_build_embed(state), view=MusicMenuView(guild.id))
    _ensure_refresh_task(guild, state)


def _cancel_idle_disconnect(state: GuildMusicState):
    if state.idle_task and not state.idle_task.done():
        state.idle_task.cancel()
    state.idle_task = None


def _schedule_idle_disconnect(guild: discord.Guild, voice_client: discord.VoiceClient):
    state = _state(guild.id)
    _cancel_idle_disconnect(state)

    async def _watch():
        try:
            await asyncio.sleep(IDLE_DISCONNECT_SECONDS)
            if state.current is None and guild.voice_client:
                await guild.voice_client.disconnect(force=True)
                _cancel_refresh_task(state)
                if state.menu_message:
                    try:
                        idle_embed = discord.Embed(
                            title="Disconnected",
                            description=f"Left voice after {IDLE_DISCONNECT_SECONDS // 60} minutes of inactivity.",
                            color=discord.Color.dark_grey(),
                        )
                        await state.menu_message.edit(embed=idle_embed, view=None)
                    except (discord.NotFound, discord.HTTPException):
                        pass
                state.menu_message = None
        except asyncio.CancelledError:
            pass

    state.idle_task = asyncio.create_task(_watch())


# ==================== extraction & playback ====================

async def _extract(query: str):
    loop = asyncio.get_event_loop()

    def _run():
        with yt_dlp.YoutubeDL(YTDLP_OPTIONS) as ydl:
            info = ydl.extract_info(query, download=False)
            if "entries" in info:
                info = info["entries"][0]
            return {"title": info.get("title", query), "url": info["url"], "duration": info.get("duration")}

    return await loop.run_in_executor(None, _run)


async def _play_next(guild: discord.Guild, voice_client: discord.VoiceClient):
    state = _state(guild.id)

    if state.loop_mode == "track" and state.current:
        state.queue.insert(0, state.current)
    elif state.loop_mode == "queue" and state.current:
        state.queue.append(state.current)

    if not state.queue:
        state.current = None
        state.source = None
        await _refresh_menu(guild, state)
        _schedule_idle_disconnect(guild, voice_client)
        return

    _cancel_idle_disconnect(state)
    track = state.queue.pop(0)
    state.current = track
    _reset_position(state)

    source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(track["url"], **FFMPEG_OPTIONS), volume=state.volume)
    state.source = source

    def _after(_error):
        fut = asyncio.run_coroutine_threadsafe(_play_next(guild, voice_client), voice_client.loop)
        try:
            fut.result()
        except Exception:
            pass

    voice_client.play(source, after=_after)
    await _refresh_menu(guild, state)


# ==================== chat commands ====================

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
        vc = await channel.connect()
        _schedule_idle_disconnect(ctx.guild, vc)
    await ctx.send(f"Joined **{channel.name}**.")


async def _cmd_leave(ctx):
    state = _state(ctx.guild.id) if ctx.guild else None
    if ctx.guild and ctx.guild.voice_client:
        await ctx.guild.voice_client.disconnect(force=True)
        if state:
            state.queue.clear()
            state.current = None
            state.source = None
            state.loop_mode = "off"
            _cancel_refresh_task(state)
            _cancel_idle_disconnect(state)
            state.menu_message = None
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

    state = _state(ctx.guild.id)
    await ctx.send("Looking that up...")
    try:
        track = await _extract(ctx.content)
    except Exception as exc:  # noqa: BLE001
        await ctx.send(f"Couldn't find that: {exc}")
        return
    track["requester"] = str(ctx.author)
    state.queue.append(track)

    if not vc.is_playing() and not vc.is_paused():
        await _play_next(ctx.guild, vc)
        await _show_menu(ctx.channel, ctx.guild, state)
    else:
        await ctx.send(f"Queued **{track['title']}** (position {len(state.queue)}).")


async def _cmd_menu(ctx):
    if not ctx.guild:
        await ctx.send("This only works in a server.")
        return
    await _show_menu(ctx.channel, ctx.guild, _state(ctx.guild.id))


async def _cmd_pause(ctx):
    vc = ctx.guild.voice_client if ctx.guild else None
    state = _state(ctx.guild.id) if ctx.guild else None
    if vc and state and vc.is_playing():
        vc.pause()
        state.position = _elapsed(state)
        state.is_paused = True
        await _refresh_menu(ctx.guild, state)
        await ctx.send("Paused.")
    else:
        await ctx.send("Nothing is playing.")


async def _cmd_resume(ctx):
    vc = ctx.guild.voice_client if ctx.guild else None
    state = _state(ctx.guild.id) if ctx.guild else None
    if vc and state and vc.is_paused():
        vc.resume()
        state.is_paused = False
        state.resumed_at = time.monotonic()
        await _refresh_menu(ctx.guild, state)
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
    state = _state(ctx.guild.id) if ctx.guild else None
    if vc and state:
        state.queue.clear()
        state.loop_mode = "off"
        state.current = None
        vc.stop()
        await ctx.send("Stopped and cleared the queue.")
    else:
        await ctx.send("I'm not in a voice channel.")


async def _cmd_queue(ctx):
    if not ctx.guild:
        await ctx.send("This only works in a server.")
        return
    state = _state(ctx.guild.id)
    lines = []
    if state.current:
        lines.append(f"**Now playing:** {state.current['title']}")
    if state.queue:
        lines.append("**Up next:**")
        lines += [f"{i + 1}. {t['title']}" for i, t in enumerate(state.queue[:10])]
    if not lines:
        lines = ["Queue is empty."]
    await ctx.send("\n".join(lines))


# (description, handler, required_perm) — merged into bot_commands.BUILTIN_COMMANDS
MUSIC_COMMANDS = {
    "join": ("Joins your current voice channel.", _cmd_join, None),
    "leave": ("Leaves the voice channel.", _cmd_leave, None),
    "play": ("Plays a song by name or URL (queues if already playing).", _cmd_play, None),
    "menu": ("Shows the interactive now-playing menu.", _cmd_menu, None),
    "pause": ("Pauses the current track.", _cmd_pause, None),
    "resume": ("Resumes the paused track.", _cmd_resume, None),
    "skip": ("Skips to the next track in queue.", _cmd_skip, None),
    "stop": ("Stops playback and clears the queue.", _cmd_stop, None),
    "queue": ("Shows what's playing and queued next.", _cmd_queue, None),
}


# ==================== web UI bridge (called from app.py, off the loop thread) ====================

def get_state_dict(client: discord.Client, guild_id: str) -> dict:
    guild = client.get_guild(int(guild_id)) if client else None
    vc = guild.voice_client if guild else None
    state = _state(int(guild_id))
    track = state.current
    return {
        "available": _unavailable_reason() is None,
        "connected": vc is not None,
        "playing": bool(track) and not state.is_paused,
        "paused": state.is_paused,
        "title": track["title"] if track else None,
        "requester": track.get("requester") if track else None,
        "elapsed": round(_elapsed(state), 1),
        "duration": track.get("duration") if track else None,
        "volume": round(state.volume * 100),
        "loop_mode": state.loop_mode,
        "queue": [t["title"] for t in state.queue[:10]],
        "queue_length": len(state.queue),
    }


def _nudge_menu(client: discord.Client, guild_id: int):
    """Fire-and-forget: refresh the Discord menu embed from off the event loop thread."""
    guild = client.get_guild(guild_id) if client else None
    if not (guild and client and client.loop):
        return
    state = _state(guild_id)
    if state.menu_message is None:
        return
    try:
        asyncio.run_coroutine_threadsafe(_refresh_menu(guild, state), client.loop)
    except RuntimeError:
        pass


def web_pause(client: discord.Client, guild_id: str) -> dict:
    guild = client.get_guild(int(guild_id)) if client else None
    vc = guild.voice_client if guild else None
    state = _state(int(guild_id))
    if not (vc and vc.is_playing()):
        return {"ok": False, "error": "Nothing is playing."}
    vc.pause()
    state.position = _elapsed(state)
    state.is_paused = True
    _nudge_menu(client, int(guild_id))
    return {"ok": True}


def web_resume(client: discord.Client, guild_id: str) -> dict:
    guild = client.get_guild(int(guild_id)) if client else None
    vc = guild.voice_client if guild else None
    state = _state(int(guild_id))
    if not (vc and vc.is_paused()):
        return {"ok": False, "error": "Nothing is paused."}
    vc.resume()
    state.is_paused = False
    state.resumed_at = time.monotonic()
    _nudge_menu(client, int(guild_id))
    return {"ok": True}


def web_skip(client: discord.Client, guild_id: str) -> dict:
    guild = client.get_guild(int(guild_id)) if client else None
    vc = guild.voice_client if guild else None
    if not (vc and (vc.is_playing() or vc.is_paused())):
        return {"ok": False, "error": "Nothing is playing."}
    vc.stop()
    return {"ok": True}


def web_stop(client: discord.Client, guild_id: str) -> dict:
    guild = client.get_guild(int(guild_id)) if client else None
    vc = guild.voice_client if guild else None
    state = _state(int(guild_id))
    if not vc:
        return {"ok": False, "error": "I'm not in a voice channel."}
    state.queue.clear()
    state.loop_mode = "off"
    state.current = None
    vc.stop()
    _nudge_menu(client, int(guild_id))
    return {"ok": True}


def web_volume(client: discord.Client, guild_id: str, delta: float) -> dict:
    state = _state(int(guild_id))
    if not state.current:
        return {"ok": False, "error": "Nothing is playing."}
    _adjust_volume(state, delta)
    _nudge_menu(client, int(guild_id))
    return {"ok": True, "volume": round(state.volume * 100)}


def web_loop_cycle(client: discord.Client, guild_id: str) -> dict:
    state = _state(int(guild_id))
    state.loop_mode = LOOP_MODES[(LOOP_MODES.index(state.loop_mode) + 1) % len(LOOP_MODES)]
    _nudge_menu(client, int(guild_id))
    return {"ok": True, "loop_mode": state.loop_mode}
