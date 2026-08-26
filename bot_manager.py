"""
Runs a discord.py Client on its own background thread + event loop, so it
can live alongside Flask (which runs on the main thread). The Flask routes
talk to it through the thread-safe methods below (start, stop, get_guilds,
get_channels, send_message).
"""
import asyncio
import threading

import discord

import bot_backup
import bot_commands

PRESENCE_TYPES = {
    "playing": discord.ActivityType.playing,
    "watching": discord.ActivityType.watching,
    "listening": discord.ActivityType.listening,
    "competing": discord.ActivityType.competing,
}


class BotManager:
    def __init__(self):
        self.client: discord.Client | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread: threading.Thread | None = None

        self.status = "offline"      # offline | connecting | online | error
        self.error_message = ""
        self.user_tag = ""
        self.pending_presence = None  # (activity_type, text) to (re)apply once online, or None

    # ---------- lifecycle ----------

    def start(self, token: str):
        """Start (or restart) the bot with the given token."""
        if self.status in ("connecting", "online"):
            self.stop()

        self.status = "connecting"
        self.error_message = ""
        self.thread = threading.Thread(target=self._run, args=(token,), daemon=True)
        self.thread.start()

    def stop(self):
        if self.loop and self.client:
            try:
                asyncio.run_coroutine_threadsafe(self.client.close(), self.loop)
            except Exception:
                pass
        self.status = "offline"
        self.user_tag = ""

    def _run(self, token: str):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        intents = discord.Intents.default()
        intents.guilds = True
        # Needed to read the text of !commands. Must also be turned on for
        # this bot under "Message Content Intent" in the Developer Portal.
        intents.message_content = True

        self.client = discord.Client(intents=intents)

        @self.client.event
        async def on_ready():
            self.status = "online"
            self.user_tag = str(self.client.user)
            if self.pending_presence:
                activity_type, text = self.pending_presence
                kind = PRESENCE_TYPES.get(activity_type, discord.ActivityType.playing)
                try:
                    await self.client.change_presence(activity=discord.Activity(type=kind, name=text))
                except discord.HTTPException:
                    pass

        @self.client.event
        async def on_message(message):
            await bot_commands.handle_message(message, self.client)

        try:
            self.loop.run_until_complete(self.client.start(token))
        except discord.LoginFailure:
            self.status = "error"
            self.error_message = "Login failed: that token was rejected by Discord."
        except Exception as exc:  # noqa: BLE001
            self.status = "error"
            self.error_message = str(exc)
        finally:
            if self.status != "error":
                self.status = "offline"

    # ---------- data access (thread-safe) ----------

    def _run_coro(self, coro, default=None, timeout=10):
        if not (self.loop and self.client and self.status == "online"):
            coro.close()  # avoid "coroutine was never awaited" — it's already built, just unused
            return default
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        try:
            return future.result(timeout=timeout)
        except Exception:
            return default

    def get_guilds(self):
        if not (self.client and self.status == "online"):
            return []
        return [{"id": str(g.id), "name": g.name} for g in self.client.guilds]

    def get_text_channels(self, guild_id: str):
        if not (self.client and self.status == "online"):
            return []
        guild = discord.utils.get(self.client.guilds, id=int(guild_id))
        if not guild:
            return []
        return [
            {"id": str(c.id), "name": c.name}
            for c in guild.text_channels
            if c.permissions_for(guild.me).send_messages
        ]

    def send_message(self, guild_id: str, channel_id: str, content: str):
        async def _send():
            channel = self.client.get_channel(int(channel_id))
            if channel is None:
                channel = await self.client.fetch_channel(int(channel_id))
            await channel.send(content)
            return True

        ok = self._run_coro(_send(), default=False)
        return ok

    def send_embed(self, guild_id: str, channel_id: str, embed_data: dict):
        async def _send():
            channel = self.client.get_channel(int(channel_id))
            if channel is None:
                channel = await self.client.fetch_channel(int(channel_id))
            await channel.send(embed=_build_embed(embed_data))
            return True

        return self._run_coro(_send(), default=False)

    # ---------- bot profile (name/avatar, straight from Discord) ----------

    def _profile_from_user(self, user) -> dict:
        return {
            "id": str(user.id),
            "name": user.name,
            "discriminator": user.discriminator,
            "display_name": str(user),
            "avatar_url": str(user.display_avatar.url) if user.display_avatar else None,
        }

    def get_bot_profile(self):
        """Cheap, cached profile from the current gateway session."""
        if not (self.client and self.status == "online" and self.client.user):
            return None
        return self._profile_from_user(self.client.user)

    def update_username(self, new_name: str) -> dict:
        async def _update():
            try:
                await self.client.user.edit(username=new_name)
                return {"ok": True, "profile": self._profile_from_user(self.client.user)}
            except discord.HTTPException as exc:
                return {"ok": False, "error": f"Discord rejected that name: {exc.text}"}

        result = self._run_coro(_update(), default=None)
        if result is None:
            return {"ok": False, "error": "Bot isn't connected."}
        if result["ok"]:
            self.user_tag = result["profile"]["display_name"]
        return result

    def update_avatar(self, image_bytes: bytes) -> dict:
        async def _update():
            try:
                await self.client.user.edit(avatar=image_bytes)
                return {"ok": True, "profile": self._profile_from_user(self.client.user)}
            except discord.HTTPException as exc:
                return {"ok": False, "error": f"Discord rejected that image: {exc.text}"}

        result = self._run_coro(_update(), default=None)
        if result is None:
            return {"ok": False, "error": "Bot isn't connected."}
        return result

    # ---------- presence ("Playing X" / "Watching Y" / ...) ----------

    def set_presence(self, activity_type: str, text: str) -> dict:
        """Stores the presence so it (re)applies on every future login, and
        pushes it live immediately if already connected."""
        text = (text or "").strip()
        self.pending_presence = (activity_type, text) if text else None

        async def _set():
            if not self.pending_presence:
                await self.client.change_presence(activity=None)
            else:
                kind = PRESENCE_TYPES.get(self.pending_presence[0], discord.ActivityType.playing)
                await self.client.change_presence(activity=discord.Activity(type=kind, name=self.pending_presence[1]))
            return True

        applied_live = bool(self._run_coro(_set(), default=None))
        return {"ok": True, "applied_live": applied_live}

    # ---------- server backup / restore (web UI only) ----------

    def save_backup(self, guild_id: str) -> dict:
        async def _save():
            guild = self.client.get_guild(int(guild_id))
            if not guild:
                return {"ok": False, "error": "Server not found — is the bot still in it?"}
            return await bot_backup.save_backup(guild)

        result = self._run_coro(_save(), default=None, timeout=60)
        return result or {"ok": False, "error": "Bot isn't connected."}

    def load_backup(self, guild_id: str, backup_id: str, mode: str) -> dict:
        async def _load():
            guild = self.client.get_guild(int(guild_id))
            if not guild:
                return {"ok": False, "error": "Server not found — is the bot still in it?"}
            perms = guild.me.guild_permissions
            if not (perms.manage_roles and perms.manage_channels):
                return {"ok": False, "error": "I need Manage Roles and Manage Channels permission in that server."}
            return await bot_backup.load_backup(guild, backup_id, mode)

        result = self._run_coro(_load(), default=None, timeout=180)
        return result or {"ok": False, "error": "Bot isn't connected."}


def _parse_color(value) -> "discord.Color":
    if not value:
        return discord.Color.default()
    try:
        return discord.Color(int(str(value).strip().lstrip("#"), 16))
    except (ValueError, TypeError):
        return discord.Color.default()


def _build_embed(data: dict) -> "discord.Embed":
    embed = discord.Embed(
        title=(data.get("title") or None),
        description=(data.get("description") or None),
        url=(data.get("url") or None),
        color=_parse_color(data.get("color")),
    )

    if data.get("author_name"):
        embed.set_author(
            name=data["author_name"],
            url=(data.get("author_url") or None),
            icon_url=(data.get("author_icon_url") or None),
        )

    if data.get("footer_text"):
        embed.set_footer(
            text=data["footer_text"],
            icon_url=(data.get("footer_icon_url") or None),
        )

    if data.get("thumbnail_url"):
        embed.set_thumbnail(url=data["thumbnail_url"])

    if data.get("image_url"):
        embed.set_image(url=data["image_url"])

    if data.get("timestamp"):
        embed.timestamp = discord.utils.utcnow()

    for field in (data.get("fields") or [])[:25]:
        name = (field.get("name") or "").strip()
        value = (field.get("value") or "").strip()
        if name and value:
            embed.add_field(name=name, value=value, inline=bool(field.get("inline")))

    return embed


bot_manager = BotManager()
