"""Core music commands."""
from __future__ import annotations

import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from .. import config, sources
from ..player import EFFECTS
from ..ui import PlayerControls, SearchSelect, base_embed, now_playing_embed, queue_embed


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ---------------- helpers ----------------
    async def ensure_voice(self, interaction: discord.Interaction, *, join: bool = True):
        voice_state = getattr(interaction.user, "voice", None)
        if not voice_state or not voice_state.channel:
            raise app_commands.AppCommandError("Age ekta voice channel e join korun.")
        player = self.bot.players.get(interaction.guild.id)
        player.text_channel = interaction.channel
        if join:
            perms = voice_state.channel.permissions_for(interaction.guild.me)
            if not (perms.connect and perms.speak):
                raise app_commands.AppCommandError(
                    "Amar oi voice channel e Connect/Speak permission nei."
                )
            await player.connect(voice_state.channel)
        return player

    async def _enqueue(self, interaction: discord.Interaction, query: str):
        player = await self.ensure_voice(interaction)
        tracks = await sources.resolve(
            query, interaction.user.id, interaction.user.display_name
        )
        added = player.add(tracks)
        if added == 0:
            await interaction.followup.send("❌ Queue full.")
            return
        if len(tracks) == 1:
            t = tracks[0]
            e = base_embed("➕ Added to queue", f"**[{t.title}]({t.url})** — `{t.pretty_duration}`")
            if t.thumbnail:
                e.set_thumbnail(url=t.thumbnail)
        else:
            e = base_embed("➕ Playlist added", f"**{added}** tracks queue e add kora hoyeche.")
        await interaction.followup.send(embed=e)

    async def _enqueue_silent(self, interaction: discord.Interaction, query: str) -> int:
        """Add without sending a message (used by /playfavorites)."""
        player = await self.ensure_voice(interaction)
        tracks = await sources.resolve(
            query, interaction.user.id, interaction.user.display_name
        )
        return player.add(tracks)

    # ---------------- commands ----------------
    @app_commands.command(name="play", description="YouTube/Spotify link ba search text play korun")
    @app_commands.describe(query="Gaaner naam, YouTube link, playlist ba Spotify link")
    async def play(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer()
        await self._enqueue(interaction, query)

    @app_commands.command(name="playnext", description="Next e play korar jonno add korun")
    async def playnext(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer()
        player = await self.ensure_voice(interaction)
        tracks = await sources.resolve(query, interaction.user.id, interaction.user.display_name)
        for t in reversed(tracks[:10]):
            player.queue.insert(0, t)
        await interaction.followup.send(
            embed=base_embed("⏫ Added next", f"**{min(len(tracks),10)}** track next e boshano hoyeche.")
        )

    @app_commands.command(name="search", description="YouTube e search kore result theke select korun")
    async def search(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer()
        entries = await sources.search(query, 10)
        if not entries:
            await interaction.followup.send("❌ Kono result pawa jayni.")
            return

        async def on_pick(inter: discord.Interaction, entry: dict):
            await inter.response.defer()
            url = entry.get("webpage_url") or entry.get("url")
            await self._enqueue(inter, url)

        e = base_embed("🔍 Search results", f"`{query}` er jonno {len(entries)} ta result.")
        await interaction.followup.send(embed=e, view=SearchSelect(entries, on_pick))

    @app_commands.command(name="join", description="Tomar voice channel e join kori")
    async def join(self, interaction: discord.Interaction):
        player = await self.ensure_voice(interaction)
        await interaction.response.send_message(
            f"✅ Joined **{player.voice.channel.name}**."
        )

    @app_commands.command(name="leave", description="Voice channel chere di")
    async def leave(self, interaction: discord.Interaction):
        await self.bot.players.destroy(interaction.guild.id)
        await interaction.response.send_message("👋 Disconnected.")

    @app_commands.command(name="pause", description="Playback pause korun")
    async def pause(self, interaction: discord.Interaction):
        p = self.bot.players.get(interaction.guild.id)
        if p.voice and p.voice.is_playing():
            p.voice.pause()
            p.paused_at = time.time()
            await interaction.response.send_message("⏸️ Paused.")
        else:
            await interaction.response.send_message("❌ Kichu play hocche na.", ephemeral=True)

    @app_commands.command(name="resume", description="Playback resume korun")
    async def resume(self, interaction: discord.Interaction):
        p = self.bot.players.get(interaction.guild.id)
        if p.voice and p.voice.is_paused():
            p.voice.resume()
            if p.paused_at:
                p.started_at += time.time() - p.paused_at
                p.paused_at = 0.0
            await interaction.response.send_message("▶️ Resumed.")
        else:
            await interaction.response.send_message("❌ Paused kichu nei.", ephemeral=True)

    @app_commands.command(name="skip", description="Current track skip korun")
    async def skip(self, interaction: discord.Interaction):
        p = self.bot.players.get(interaction.guild.id)
        if not p.playing:
            await interaction.response.send_message("❌ Kichu play hocche na.", ephemeral=True)
            return
        p.skip()
        await interaction.response.send_message("⏭️ Skipped.")

    @app_commands.command(name="stop", description="Stop kore queue clear korun")
    async def stop(self, interaction: discord.Interaction):
        p = self.bot.players.get(interaction.guild.id)
        await p.stop()
        await interaction.response.send_message("⏹️ Stopped & queue cleared.")

    @app_commands.command(name="queue", description="Queue dekhun")
    async def queue(self, interaction: discord.Interaction, page: Optional[int] = 1):
        p = self.bot.players.get(interaction.guild.id)
        await interaction.response.send_message(embed=queue_embed(p, page or 1))

    @app_commands.command(name="nowplaying", description="Player panel dekhun")
    async def nowplaying(self, interaction: discord.Interaction):
        p = self.bot.players.get(interaction.guild.id)
        if not p.current:
            await interaction.response.send_message("❌ Kichu play hocche na.", ephemeral=True)
            return
        await interaction.response.send_message(embed=now_playing_embed(p), view=PlayerControls(p))

    @app_commands.command(name="shuffle", description="Queue shuffle korun")
    async def shuffle(self, interaction: discord.Interaction):
        n = self.bot.players.get(interaction.guild.id).shuffle()
        await interaction.response.send_message(f"🔀 Shuffled **{n}** tracks.")

    @app_commands.command(name="remove", description="Queue theke ekta position remove korun")
    async def remove(self, interaction: discord.Interaction, position: app_commands.Range[int, 1, 500]):
        p = self.bot.players.get(interaction.guild.id)
        if position > len(p.queue):
            await interaction.response.send_message("❌ Invalid position.", ephemeral=True)
            return
        t = p.queue.pop(position - 1)
        await interaction.response.send_message(f"🗑️ Removed **{t.title}**.")

    @app_commands.command(name="move", description="Queue er track move korun")
    async def move(
        self,
        interaction: discord.Interaction,
        source: app_commands.Range[int, 1, 500],
        target: app_commands.Range[int, 1, 500],
    ):
        p = self.bot.players.get(interaction.guild.id)
        if source > len(p.queue) or target > len(p.queue):
            await interaction.response.send_message("❌ Invalid position.", ephemeral=True)
            return
        t = p.queue.pop(source - 1)
        p.queue.insert(target - 1, t)
        await interaction.response.send_message(f"↕️ **{t.title}** → position {target}.")

    @app_commands.command(name="clear", description="Queue clear korun")
    async def clear(self, interaction: discord.Interaction):
        p = self.bot.players.get(interaction.guild.id)
        n = len(p.queue)
        p.queue.clear()
        await interaction.response.send_message(f"🧹 Cleared **{n}** tracks.")

    @app_commands.command(name="volume", description="Volume 0-150%")
    async def volume(self, interaction: discord.Interaction, percent: app_commands.Range[int, 0, 150]):
        p = self.bot.players.get(interaction.guild.id)
        p.volume = percent / 100
        p.save()
        if p.voice and isinstance(p.voice.source, discord.PCMVolumeTransformer):
            p.voice.source.volume = p.volume
        await interaction.response.send_message(f"🔊 Volume **{percent}%**.")

    @app_commands.command(name="loop", description="Loop mode set korun")
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="off", value="off"),
            app_commands.Choice(name="track", value="track"),
            app_commands.Choice(name="queue", value="queue"),
        ]
    )
    async def loop(self, interaction: discord.Interaction, mode: app_commands.Choice[str]):
        p = self.bot.players.get(interaction.guild.id)
        p.loop_mode = mode.value
        p.save()
        await interaction.response.send_message(f"🔁 Loop: **{mode.value}**.")

    @app_commands.command(name="autoplay", description="Autoplay on/off")
    async def autoplay(self, interaction: discord.Interaction, enabled: Optional[bool] = None):
        p = self.bot.players.get(interaction.guild.id)
        p.autoplay = (not p.autoplay) if enabled is None else enabled
        p.save()
        await interaction.response.send_message(
            f"♾️ Autoplay **{'on' if p.autoplay else 'off'}**."
        )

    @app_commands.command(name="effect", description="Real-time audio effect")
    @app_commands.choices(
        mode=[app_commands.Choice(name=k, value=k) for k in EFFECTS]
    )
    async def effect(self, interaction: discord.Interaction, mode: app_commands.Choice[str]):
        p = self.bot.players.get(interaction.guild.id)
        p.effect = mode.value
        p.save()
        note = ""
        if p.playing:
            p.skip_restart = True
            note = " (next track theke apply hobe — sathe sathe chaile /replay din)"
        await interaction.response.send_message(f"🎛️ Effect: **{mode.value}**{note}.")

    @app_commands.command(name="speed", description="Playback speed 0.5x - 2.0x")
    async def speed(self, interaction: discord.Interaction, multiplier: app_commands.Range[float, 0.5, 2.0]):
        p = self.bot.players.get(interaction.guild.id)
        p.speed = float(multiplier)
        p.save()
        await interaction.response.send_message(f"⏩ Speed **{multiplier}x** (next track theke).")

    @app_commands.command(name="replay", description="Current track abar shuru theke bajao")
    async def replay(self, interaction: discord.Interaction):
        p = self.bot.players.get(interaction.guild.id)
        if not p.current:
            await interaction.response.send_message("❌ Kichu play hocche na.", ephemeral=True)
            return
        p.queue.insert(0, p.current)
        p.skip()
        await interaction.response.send_message("🔂 Replaying.")

    @app_commands.command(name="seekinfo", description="Track progress dekhun")
    async def seekinfo(self, interaction: discord.Interaction):
        p = self.bot.players.get(interaction.guild.id)
        if not p.current:
            await interaction.response.send_message("❌ Kichu play hocche na.", ephemeral=True)
            return
        await interaction.response.send_message(embed=now_playing_embed(p))


async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))
