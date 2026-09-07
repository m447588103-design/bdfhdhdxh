"""Aurora Music — a full-featured, free-hosting-ready Discord music bot."""
from __future__ import annotations

import asyncio
import logging
import sys

import discord
from discord import app_commands
from discord.ext import commands, tasks

from . import config
from .db import Database
from .player import PlayerManager
from .ui import base_embed
from .webserver import start_webserver

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
log = logging.getLogger("aurora")

INTENTS = discord.Intents.default()
INTENTS.voice_states = True
INTENTS.guilds = True
INTENTS.message_content = False

EXTENSIONS = ("musicbot.cogs.music", "musicbot.cogs.extras")


class MusicBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix=commands.when_mentioned, intents=INTENTS, help_command=None)
        self.db = Database(config.DATABASE_PATH)
        self.players = PlayerManager(self, self.db)
        self._runner = None

    async def setup_hook(self) -> None:
        for ext in EXTENSIONS:
            await self.load_extension(ext)
            log.info("Loaded %s", ext)

        if config.GUILD_ID:
            guild = discord.Object(id=config.GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            log.info("Slash commands synced to guild %s", config.GUILD_ID)
        else:
            await self.tree.sync()
            log.info("Slash commands synced globally (may take up to 1 hour)")

        self._runner = await start_webserver(self)
        self.presence_loop.start()

    async def on_ready(self):
        log.info("Logged in as %s (%s) — %d guilds", self.user, self.user.id, len(self.guilds))

    async def close(self):
        for p in self.players.all:
            await p.disconnect()
        if self._runner:
            await self._runner.cleanup()
        await super().close()

    @tasks.loop(minutes=2)
    async def presence_loop(self):
        active = sum(1 for p in self.players.all if p.playing)
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.listening,
                name=f"/play • {active} session" + ("s" if active != 1 else ""),
            )
        )

    @presence_loop.before_loop
    async def _before_presence(self):
        await self.wait_until_ready()

    async def on_voice_state_update(self, member, before, after):
        if member.id != self.user.id:
            # leave when alone
            player = self.players.existing(member.guild.id)
            if player and player.voice and player.voice.channel:
                humans = [m for m in player.voice.channel.members if not m.bot]
                if not humans:
                    await asyncio.sleep(30)
                    ch = player.voice.channel if player.voice else None
                    if ch and not [m for m in ch.members if not m.bot]:
                        await self.players.destroy(member.guild.id)
            return
        if before.channel and not after.channel:
            await self.players.destroy(member.guild.id)


bot = MusicBot()


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: Exception):
    msg = str(getattr(error, "original", error)) or "Unknown error"
    log.warning("Command error: %s", msg, exc_info=False)
    embed = base_embed("⚠️ Error", msg[:1900])
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception:
        pass


def main() -> None:
    if not config.TOKEN:
        log.error("DISCORD_TOKEN set kora nei. .env ba hosting env vars e din.")
        sys.exit(1)
    try:
        bot.run(config.TOKEN, log_handler=None)
    except discord.LoginFailure:
        log.error("Invalid DISCORD_TOKEN.")
        sys.exit(1)


if __name__ == "__main__":
    main()
