"""
Runs a discord.py Client on its own background thread + event loop, so it
can live alongside Flask (which runs on the main thread). The Flask routes
talk to it through the thread-safe methods below (start, stop, get_guilds,
get_channels, send_message).
"""
import asyncio
import datetime
import threading

import discord

import bot_backup
import bot_commands
import bot_tts
import guild_settings

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
            bot_tts.maybe_enqueue(message)

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

    def get_voice_channels(self, guild_id: str):
        if not (self.client and self.status == "online"):
            return []
        guild = discord.utils.get(self.client.guilds, id=int(guild_id))
        if not guild:
            return []
        return [
            {"id": str(c.id), "name": c.name}
            for c in guild.voice_channels
            if c.permissions_for(guild.me).connect
        ]

    def leave_guild(self, guild_id: str) -> dict:
        async def _leave():
            guild = self.client.get_guild(int(guild_id))
            if not guild:
                return {"ok": False, "error": "Server not found — is the bot still in it?"}
            name = guild.name
            await guild.leave()
            return {"ok": True, "name": name}

        result = self._run_coro(_leave(), default=None)
        return result or {"ok": False, "error": "Bot isn't connected."}

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

    # ---------- moderation (Control Deck web panel) ----------
    # Same underlying actions as the chat moderation commands, but triggered
    # from the web UI instead of a Discord message. Split across a few
    # methods by what they need to look up: a member already in the server
    # (moderate_member), just a raw user ID (moderate_user_id — works even
    # for someone who's never joined, e.g. banid/unban), a channel
    # (moderate_channel, pin_message), or neither (roles, ban list).

    def moderate_member(self, guild_id: str, user_id: str, action: str, reason: str = "", minutes=None, extra: str = "") -> dict:
        async def _do():
            guild = self.client.get_guild(int(guild_id))
            if not guild:
                return {"ok": False, "error": "Server not found — is the bot still in it?"}
            try:
                member = guild.get_member(int(user_id)) or await guild.fetch_member(int(user_id))
            except discord.NotFound:
                return {"ok": False, "error": "That user isn't a member of this server."}
            except (discord.HTTPException, ValueError) as exc:
                return {"ok": False, "error": f"Couldn't find that member: {exc}"}

            log_reason = reason or None
            skip_modlog = False
            try:
                if action == "kick":
                    await member.kick(reason=log_reason)
                elif action == "ban":
                    await member.ban(reason=log_reason, delete_message_days=0)
                elif action == "softban":
                    await member.ban(reason=log_reason, delete_message_days=1)
                    await guild.unban(member, reason="Softban cleanup")
                elif action == "timeout":
                    mins = max(1, min(int(minutes or 10), 40320))
                    await member.timeout(datetime.timedelta(minutes=mins), reason=log_reason)
                    log_reason = f"{mins} minute(s)" + (f" — {reason}" if reason else "")
                elif action == "untimeout":
                    await member.timeout(None)
                    skip_modlog = True
                elif action == "clearnick":
                    await member.edit(nick=None)
                elif action == "nick":
                    new_nick = (extra or "").strip()[:32]
                    await member.edit(nick=new_nick or None)
                    log_reason = new_nick or "(cleared)"
                elif action == "addrole":
                    role = guild.get_role(int(extra)) if extra else None
                    if not role:
                        return {"ok": False, "error": "Pick a role first."}
                    await member.add_roles(role, reason=log_reason)
                    log_reason = role.name
                elif action == "removerole":
                    role = guild.get_role(int(extra)) if extra else None
                    if not role:
                        return {"ok": False, "error": "Pick a role first."}
                    await member.remove_roles(role, reason=log_reason)
                    log_reason = role.name
                elif action == "mute":
                    role_id = guild_settings.get_mute_role(guild.id)
                    role = guild.get_role(role_id) if role_id else None
                    if not role:
                        return {"ok": False, "error": "No mute role configured yet — set one below first."}
                    await member.add_roles(role, reason=log_reason)
                elif action == "unmute":
                    role_id = guild_settings.get_mute_role(guild.id)
                    role = guild.get_role(role_id) if role_id else None
                    if not role:
                        return {"ok": False, "error": "No mute role configured yet — set one below first."}
                    await member.remove_roles(role, reason=log_reason)
                    skip_modlog = True
                elif action == "tempban":
                    mins = max(1, min(float(minutes or 10), 44640))
                    user_id_int = member.id
                    await member.ban(reason=log_reason, delete_message_days=0)
                    log_reason = f"{mins:g}m" + (f" — {reason}" if reason else "")

                    async def _unban_later():
                        try:
                            await asyncio.sleep(mins * 60)
                            await guild.unban(discord.Object(id=user_id_int), reason="Tempban expired")
                        except (discord.HTTPException, asyncio.CancelledError):
                            pass

                    asyncio.create_task(_unban_later())
                elif action == "warn":
                    key = f"{guild.id}:{member.id}"
                    data = bot_commands._load_warnings()
                    data.setdefault(key, []).append({
                        "reason": reason or "No reason given.",
                        "by": "Control Deck (web)",
                        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    })
                    bot_commands._save_warnings(data)
                elif action == "clearwarnings":
                    key = f"{guild.id}:{member.id}"
                    data = bot_commands._load_warnings()
                    data.pop(key, None)
                    bot_commands._save_warnings(data)
                elif action == "warnremove":
                    try:
                        index = int(extra)
                    except (TypeError, ValueError):
                        return {"ok": False, "error": "Pick a warning number first."}
                    key = f"{guild.id}:{member.id}"
                    data = bot_commands._load_warnings()
                    entries = data.get(key, [])
                    if not (1 <= index <= len(entries)):
                        return {"ok": False, "error": f"No warning #{index}."}
                    removed = entries.pop(index - 1)
                    data[key] = entries
                    bot_commands._save_warnings(data)
                    log_reason = removed["reason"]
                else:
                    return {"ok": False, "error": "Unknown action."}
            except discord.Forbidden:
                return {"ok": False, "error": "I don't have permission to do that (role hierarchy?)."}
            except discord.HTTPException as exc:
                return {"ok": False, "error": f"Discord rejected that: {exc.text}"}

            if not skip_modlog:
                await _post_modlog(guild, action.title(), member, log_reason)
            return {"ok": True}

        result = self._run_coro(_do(), default=None)
        return result or {"ok": False, "error": "Bot isn't connected."}

    def moderate_user_id(self, guild_id: str, user_id: str, action: str, reason: str = "") -> dict:
        """For actions that work on a raw user ID without the person being
        a current member — banning someone by ID, or unbanning."""
        async def _do():
            guild = self.client.get_guild(int(guild_id))
            if not guild:
                return {"ok": False, "error": "Server not found — is the bot still in it?"}
            try:
                uid = int(user_id)
            except ValueError:
                return {"ok": False, "error": "That doesn't look like a user ID."}

            try:
                if action == "banid":
                    await guild.ban(discord.Object(id=uid), reason=reason or None)
                elif action == "unban":
                    await guild.unban(discord.Object(id=uid), reason=reason or None)
                else:
                    return {"ok": False, "error": "Unknown action."}
            except discord.NotFound:
                return {"ok": False, "error": "That user isn't banned."}
            except discord.Forbidden:
                return {"ok": False, "error": "I don't have permission to do that."}
            except discord.HTTPException as exc:
                return {"ok": False, "error": f"Discord rejected that: {exc.text}"}
            return {"ok": True}

        result = self._run_coro(_do(), default=None)
        return result or {"ok": False, "error": "Bot isn't connected."}

    def moderate_channel(self, guild_id: str, channel_id: str, action: str, extra: str = "") -> dict:
        """Channel-scoped moderation: purge, slowmode, lock, unlock,
        announce. All operate on the given channel, not wherever the
        request happened to come from (there's no "current channel" in the
        web UI)."""
        async def _do():
            guild = self.client.get_guild(int(guild_id))
            if not guild:
                return {"ok": False, "error": "Server not found — is the bot still in it?"}
            channel = guild.get_channel(int(channel_id))
            if not channel:
                return {"ok": False, "error": "Channel not found."}

            try:
                if action == "purge":
                    count = max(1, min(int(extra or 10), 100))
                    deleted = await channel.purge(limit=count)
                    return {"ok": True, "deleted": len(deleted)}
                elif action == "slowmode":
                    seconds = max(0, min(int(extra or 0), 21600))
                    await channel.edit(slowmode_delay=seconds)
                elif action == "lock":
                    await channel.set_permissions(guild.default_role, send_messages=False)
                elif action == "unlock":
                    await channel.set_permissions(guild.default_role, send_messages=None)
                elif action == "announce":
                    text = (extra or "").strip()
                    if not text:
                        return {"ok": False, "error": "Write a message to announce."}
                    await channel.send(text)
                else:
                    return {"ok": False, "error": "Unknown action."}
            except discord.Forbidden:
                return {"ok": False, "error": "I need Manage Channels/Messages here to do that."}
            except discord.HTTPException as exc:
                return {"ok": False, "error": f"Discord rejected that: {exc.text}"}
            return {"ok": True}

        result = self._run_coro(_do(), default=None)
        return result or {"ok": False, "error": "Bot isn't connected."}

    def set_pin(self, guild_id: str, channel_id: str, message_id: str, pin: bool) -> dict:
        async def _do():
            guild = self.client.get_guild(int(guild_id))
            if not guild:
                return {"ok": False, "error": "Server not found — is the bot still in it?"}
            channel = guild.get_channel(int(channel_id))
            if not channel:
                return {"ok": False, "error": "Channel not found."}
            try:
                message = await channel.fetch_message(int(message_id))
                if pin:
                    await message.pin()
                else:
                    await message.unpin()
            except discord.NotFound:
                return {"ok": False, "error": "That message ID wasn't found in that channel."}
            except (discord.Forbidden, discord.HTTPException) as exc:
                return {"ok": False, "error": f"Couldn't do that: {getattr(exc, 'text', exc)}"}
            except ValueError:
                return {"ok": False, "error": "That doesn't look like a message ID."}
            return {"ok": True}

        result = self._run_coro(_do(), default=None)
        return result or {"ok": False, "error": "Bot isn't connected."}

    def purge_user_messages(self, guild_id: str, channel_id: str, user_id: str, count: int) -> dict:
        async def _do():
            guild = self.client.get_guild(int(guild_id))
            if not guild:
                return {"ok": False, "error": "Server not found — is the bot still in it?"}
            channel = guild.get_channel(int(channel_id))
            if not channel:
                return {"ok": False, "error": "Channel not found."}
            try:
                target_id = int(user_id)
            except ValueError:
                return {"ok": False, "error": "That doesn't look like a user ID."}
            capped = max(1, min(count, 100))
            try:
                deleted = await channel.purge(limit=min(capped * 5, 500), check=lambda m: m.author.id == target_id)
            except discord.Forbidden:
                return {"ok": False, "error": "I need Manage Messages here to do that."}
            except discord.HTTPException as exc:
                return {"ok": False, "error": f"Discord rejected that: {exc.text}"}
            return {"ok": True, "deleted": len(deleted)}

        result = self._run_coro(_do(), default=None)
        return result or {"ok": False, "error": "Bot isn't connected."}

    def get_ban_list(self, guild_id: str) -> dict:
        async def _do():
            guild = self.client.get_guild(int(guild_id))
            if not guild:
                return {"ok": False, "error": "Server not found — is the bot still in it?"}
            try:
                bans = [entry async for entry in guild.bans(limit=100)]
            except discord.HTTPException as exc:
                return {"ok": False, "error": f"Couldn't fetch the ban list: {exc.text}"}
            return {"ok": True, "bans": [{"id": str(b.user.id), "name": str(b.user), "reason": b.reason or ""} for b in bans]}

        result = self._run_coro(_do(), default=None)
        return result or {"ok": False, "error": "Bot isn't connected."}

    # ---------- roles ----------

    def list_roles(self, guild_id: str):
        if not (self.client and self.status == "online"):
            return []
        guild = discord.utils.get(self.client.guilds, id=int(guild_id))
        if not guild:
            return []
        return [
            {"id": str(r.id), "name": r.name}
            for r in sorted(guild.roles, key=lambda r: r.position, reverse=True)
            if not r.is_default() and not r.managed
        ]

    def create_role(self, guild_id: str, name: str) -> dict:
        async def _do():
            guild = self.client.get_guild(int(guild_id))
            if not guild:
                return {"ok": False, "error": "Server not found — is the bot still in it?"}
            try:
                role = await guild.create_role(name=name[:100], reason="Created from Control Deck")
            except discord.HTTPException as exc:
                return {"ok": False, "error": f"Couldn't create that role: {exc.text}"}
            return {"ok": True, "role": {"id": str(role.id), "name": role.name}}

        result = self._run_coro(_do(), default=None)
        return result or {"ok": False, "error": "Bot isn't connected."}

    def delete_role(self, guild_id: str, role_id: str) -> dict:
        async def _do():
            guild = self.client.get_guild(int(guild_id))
            if not guild:
                return {"ok": False, "error": "Server not found — is the bot still in it?"}
            role = guild.get_role(int(role_id))
            if not role:
                return {"ok": False, "error": "That role doesn't exist anymore."}
            try:
                await role.delete(reason="Deleted from Control Deck")
            except discord.HTTPException as exc:
                return {"ok": False, "error": f"Couldn't delete that role: {exc.text}"}
            return {"ok": True}

        result = self._run_coro(_do(), default=None)
        return result or {"ok": False, "error": "Bot isn't connected."}

    # ---------- channels & categories ----------

    def list_channels_full(self, guild_id: str) -> dict:
        """Everything the Channels/Categories tabs need in one call:
        every category, plus every text/voice channel with which category
        (if any) it's under."""
        if not (self.client and self.status == "online"):
            return {"categories": [], "channels": []}
        guild = discord.utils.get(self.client.guilds, id=int(guild_id))
        if not guild:
            return {"categories": [], "channels": []}
        categories = [{"id": str(c.id), "name": c.name} for c in guild.categories]
        channels = [
            {
                "id": str(c.id),
                "name": c.name,
                "type": "voice" if isinstance(c, discord.VoiceChannel) else "text",
                "category_id": str(c.category_id) if c.category_id else None,
            }
            for c in list(guild.text_channels) + list(guild.voice_channels)
        ]
        return {"categories": categories, "channels": channels}

    def create_channel(self, guild_id: str, name: str, channel_type: str, category_id: str = "") -> dict:
        async def _do():
            guild = self.client.get_guild(int(guild_id))
            if not guild:
                return {"ok": False, "error": "Server not found — is the bot still in it?"}
            category = None
            if category_id:
                category = guild.get_channel(int(category_id))
                if not isinstance(category, discord.CategoryChannel):
                    return {"ok": False, "error": "That category doesn't exist anymore."}
            try:
                if channel_type == "category":
                    channel = await guild.create_category(name=name[:100], reason="Created from Control Deck")
                elif channel_type == "voice":
                    channel = await guild.create_voice_channel(name=name[:100], category=category, reason="Created from Control Deck")
                else:
                    channel = await guild.create_text_channel(name=name[:100], category=category, reason="Created from Control Deck")
            except discord.HTTPException as exc:
                return {"ok": False, "error": f"Couldn't create that: {exc.text}"}
            return {"ok": True, "channel": {"id": str(channel.id), "name": channel.name}}

        result = self._run_coro(_do(), default=None)
        return result or {"ok": False, "error": "Bot isn't connected."}

    def rename_channel(self, guild_id: str, channel_id: str, name: str) -> dict:
        async def _do():
            guild = self.client.get_guild(int(guild_id))
            if not guild:
                return {"ok": False, "error": "Server not found — is the bot still in it?"}
            channel = guild.get_channel(int(channel_id))
            if not channel:
                return {"ok": False, "error": "That channel doesn't exist anymore."}
            try:
                await channel.edit(name=name[:100], reason="Renamed from Control Deck")
            except discord.HTTPException as exc:
                return {"ok": False, "error": f"Couldn't rename that: {exc.text}"}
            return {"ok": True}

        result = self._run_coro(_do(), default=None)
        return result or {"ok": False, "error": "Bot isn't connected."}

    def move_channel(self, guild_id: str, channel_id: str, category_id: str = "") -> dict:
        async def _do():
            guild = self.client.get_guild(int(guild_id))
            if not guild:
                return {"ok": False, "error": "Server not found — is the bot still in it?"}
            channel = guild.get_channel(int(channel_id))
            if not channel:
                return {"ok": False, "error": "That channel doesn't exist anymore."}
            category = None
            if category_id:
                category = guild.get_channel(int(category_id))
                if not isinstance(category, discord.CategoryChannel):
                    return {"ok": False, "error": "That category doesn't exist anymore."}
            try:
                await channel.edit(category=category, reason="Moved from Control Deck")
            except discord.HTTPException as exc:
                return {"ok": False, "error": f"Couldn't move that: {exc.text}"}
            return {"ok": True}

        result = self._run_coro(_do(), default=None)
        return result or {"ok": False, "error": "Bot isn't connected."}

    def delete_channel(self, guild_id: str, channel_id: str) -> dict:
        async def _do():
            guild = self.client.get_guild(int(guild_id))
            if not guild:
                return {"ok": False, "error": "Server not found — is the bot still in it?"}
            channel = guild.get_channel(int(channel_id))
            if not channel:
                return {"ok": False, "error": "That channel doesn't exist anymore."}
            try:
                await channel.delete(reason="Deleted from Control Deck")
            except discord.HTTPException as exc:
                return {"ok": False, "error": f"Couldn't delete that: {exc.text}"}
            return {"ok": True}

        result = self._run_coro(_do(), default=None)
        return result or {"ok": False, "error": "Bot isn't connected."}


async def _post_modlog(guild, action_name: str, target, reason=None):
    channel_id = guild_settings.get_modlog_channel(guild.id)
    if not channel_id:
        return
    channel = guild.get_channel(channel_id)
    if not channel:
        return
    description = f"**Target:** {target}\n**By:** Control Deck (web)"
    if reason:
        description += f"\n**Reason:** {reason}"
    embed = discord.Embed(title=action_name, description=description, color=discord.Color(0xFFB454))
    try:
        await channel.send(embed=embed)
    except discord.HTTPException:
        pass


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
