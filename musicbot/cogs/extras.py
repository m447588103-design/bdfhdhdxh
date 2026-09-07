"""Favorites, history, lyrics, help, stats."""
from __future__ import annotations

import platform
import time
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from .. import config
from ..ui import base_embed

START = time.time()


class Extras(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="favorite", description="Current track favorites e add korun")
    async def favorite(self, interaction: discord.Interaction):
        p = self.bot.players.get(interaction.guild.id)
        if not p.current:
            await interaction.response.send_message("❌ Kichu play hocche na.", ephemeral=True)
            return
        self.bot.db.favorite_add(interaction.user.id, p.current.title, p.current.url)
        await interaction.response.send_message(f"⭐ Saved **{p.current.title}**.", ephemeral=True)

    @app_commands.command(name="favorites", description="Tomar favorites dekhao")
    async def favorites(self, interaction: discord.Interaction):
        rows = self.bot.db.favorites(interaction.user.id)
        if not rows:
            await interaction.response.send_message("⭐ Kono favorite nei.", ephemeral=True)
            return
        desc = "\n".join(f"`{i+1}.` [{r['title'][:55]}]({r['url']})" for i, r in enumerate(rows))
        await interaction.response.send_message(embed=base_embed("⭐ Favorites", desc), ephemeral=True)

    @app_commands.command(name="playfavorites", description="Tomar favorites queue e add korun")
    async def playfavorites(self, interaction: discord.Interaction):
        rows = self.bot.db.favorites(interaction.user.id)
        if not rows:
            await interaction.response.send_message("⭐ Kono favorite nei.", ephemeral=True)
            return
        await interaction.response.defer()
        music = self.bot.get_cog("Music")
        added = 0
        for r in rows:
            try:
                await music._enqueue_silent(interaction, r["url"])
                added += 1
            except Exception:
                continue
        await interaction.followup.send(f"⭐ **{added}** favorite track add kora hoyeche.")

    @app_commands.command(name="history", description="Recently played tracks")
    async def history(self, interaction: discord.Interaction):
        rows = self.bot.db.history(interaction.guild.id)
        if not rows:
            await interaction.response.send_message("🕘 History khali.", ephemeral=True)
            return
        desc = "\n".join(f"`{i+1}.` [{r['title'][:55]}]({r['url']})" for i, r in enumerate(rows))
        await interaction.response.send_message(embed=base_embed("🕘 History", desc))

    @app_commands.command(name="lyrics", description="Lyrics dekhao (LRCLIB)")
    async def lyrics(self, interaction: discord.Interaction, query: Optional[str] = None):
        await interaction.response.defer()
        p = self.bot.players.get(interaction.guild.id)
        term = query or (p.current.title if p.current else None)
        if not term:
            await interaction.followup.send("❌ Track naam din.")
            return
        text = await fetch_lyrics(term)
        if not text:
            await interaction.followup.send(f"❌ `{term}` er lyrics pawa jayni.")
            return
        chunks = [text[i : i + 3900] for i in range(0, len(text), 3900)][:4]
        for idx, chunk in enumerate(chunks):
            await interaction.followup.send(
                embed=base_embed(f"🎤 Lyrics — {term[:80]}" if idx == 0 else "…", chunk)
            )

    @app_commands.command(name="help", description="Sob command er list")
    async def help_cmd(self, interaction: discord.Interaction):
        e = base_embed(f"🎧 {config.BOT_NAME} — Help")
        e.add_field(
            name="▶️ Playback",
            value="`/play` `/playnext` `/search` `/pause` `/resume` `/skip` `/stop` `/replay` `/join` `/leave`",
            inline=False,
        )
        e.add_field(
            name="📜 Queue",
            value="`/queue` `/nowplaying` `/shuffle` `/remove` `/move` `/clear` `/loop` `/autoplay`",
            inline=False,
        )
        e.add_field(
            name="🎛️ Audio",
            value="`/volume` `/effect` `/speed` — bassboost, nightcore, vaporwave, 8d, lofi, karaoke…",
            inline=False,
        )
        e.add_field(
            name="✨ Extra",
            value="`/favorite` `/favorites` `/playfavorites` `/history` `/lyrics` `/stats` `/ping` `/invite`",
            inline=False,
        )
        await interaction.response.send_message(embed=e)

    @app_commands.command(name="stats", description="Bot statistics")
    async def stats(self, interaction: discord.Interaction):
        up = int(time.time() - START)
        h, rem = divmod(up, 3600)
        m, s = divmod(rem, 60)
        active = sum(1 for p in self.bot.players.all if p.playing)
        e = base_embed(f"📊 {config.BOT_NAME} Stats")
        e.add_field(name="Servers", value=str(len(self.bot.guilds)))
        e.add_field(name="Voice sessions", value=str(active))
        e.add_field(name="Tracks played", value=str(self.bot.db.stat("tracks_played")))
        e.add_field(name="Latency", value=f"{round(self.bot.latency*1000)} ms")
        e.add_field(name="Uptime", value=f"{h}h {m}m {s}s")
        e.add_field(name="Python", value=platform.python_version())
        await interaction.response.send_message(embed=e)

    @app_commands.command(name="ping", description="Latency check")
    async def ping(self, interaction: discord.Interaction):
        await interaction.response.send_message(f"🏓 Pong — `{round(self.bot.latency*1000)}ms`")

    @app_commands.command(name="invite", description="Bot invite link")
    async def invite(self, interaction: discord.Interaction):
        url = discord.utils.oauth_url(
            self.bot.user.id,
            permissions=discord.Permissions(3196992),
            scopes=("bot", "applications.commands"),
        )
        await interaction.response.send_message(
            embed=base_embed("🔗 Invite", f"[Add {config.BOT_NAME} to your server]({url})")
        )


async def fetch_lyrics(term: str) -> Optional[str]:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                "https://lrclib.net/api/search",
                params={"q": term},
                headers={"User-Agent": "AuroraMusicBot/1.0"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as r:
                if r.status != 200:
                    return None
                data = await r.json()
    except Exception:
        return None
    for item in data or []:
        text = item.get("plainLyrics") or item.get("syncedLyrics")
        if text:
            return text
    return None


async def setup(bot: commands.Bot):
    await bot.add_cog(Extras(bot))
