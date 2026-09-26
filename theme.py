"""
Shared look for everything the bot posts in Discord, so embeds match the
web UI's void-black theme (static/style.css --accent) from one place.
"""
import discord

# Void violet — same hue as the web UI's --accent.
ACCENT = 0x8B5CF6
EMBED_COLOR = discord.Color(ACCENT)
