"""
Runs a discord.py Client on its own background thread + event loop, so it
can live alongside Flask (which runs on the main thread). The Flask routes
talk to it through the thread-safe methods below (start, stop, get_guilds,
get_channels, send_message).
"""
import asyncio
import threading

import discord


class BotManager:
    def __init__(self):
        self.client: discord.Client | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread: threading.Thread | None = None

        self.status = "offline"      # offline | connecting | online | error
        self.error_message = ""
        self.user_tag = ""

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
        intents.message_content = False

        self.client = discord.Client(intents=intents)

        @self.client.event
        async def on_ready():
            self.status = "online"
            self.user_tag = str(self.client.user)
            print(f"[bot] Logged in as {self.client.user}")

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

    def _run_coro(self, coro, default=None):
        if not (self.loop and self.client and self.status == "online"):
            return default
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        try:
            return future.result(timeout=10)
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


bot_manager = BotManager()
