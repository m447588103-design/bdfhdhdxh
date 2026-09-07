"""Embeds and interactive button/select views."""
from __future__ import annotations

from typing import List

import discord

from . import config
from .sources import Track, format_duration


def base_embed(title: str = "", description: str = "") -> discord.Embed:
    e = discord.Embed(title=title, description=description, color=config.EMBED_COLOR)
    e.set_footer(text=config.FOOTER)
    return e


def progress_bar(position: int, duration: int, size: int = 18) -> str:
    if duration <= 0:
        return "🔴 LIVE"
    ratio = min(1.0, max(0.0, position / duration))
    idx = int(ratio * (size - 1))
    bar = "".join("🔘" if i == idx else "▬" for i in range(size))
    return f"{bar}\n`{format_duration(position)} / {format_duration(duration)}`"


def now_playing_embed(player) -> discord.Embed:
    t: Track = player.current
    e = base_embed("🎶 Now Playing", f"**[{t.title}]({t.url})**")
    e.add_field(name="Duration", value=t.pretty_duration, inline=True)
    e.add_field(name="Requested by", value=t.requester_name or "—", inline=True)
    e.add_field(name="Volume", value=f"{int(player.volume * 100)}%", inline=True)
    e.add_field(name="Loop", value=player.loop_mode, inline=True)
    e.add_field(name="Effect", value=player.effect, inline=True)
    e.add_field(name="Autoplay", value="on" if player.autoplay else "off", inline=True)
    e.add_field(
        name="Progress", value=progress_bar(player.position, t.duration), inline=False
    )
    if player.queue:
        upcoming = "\n".join(
            f"`{i+1}.` {x.title[:52]}" for i, x in enumerate(player.queue[:3])
        )
        e.add_field(name=f"Up next ({len(player.queue)})", value=upcoming, inline=False)
    if t.thumbnail:
        e.set_thumbnail(url=t.thumbnail)
    if t.uploader:
        e.set_author(name=t.uploader)
    return e


def queue_embed(player, page: int = 1, per_page: int = 10) -> discord.Embed:
    total = len(player.queue)
    pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, pages))
    start = (page - 1) * per_page
    chunk = player.queue[start : start + per_page]

    desc = ""
    if player.current:
        desc += f"▶️ **Now:** [{player.current.title}]({player.current.url})\n\n"
    if chunk:
        desc += "\n".join(
            f"`{start+i+1}.` [{t.title[:55]}]({t.url}) — `{t.pretty_duration}`"
            for i, t in enumerate(chunk)
        )
    else:
        desc += "*Queue khali.*"
    e = base_embed("📜 Queue", desc)
    total_secs = sum(t.duration for t in player.queue)
    e.set_footer(text=f"Page {page}/{pages} • {total} tracks • {format_duration(total_secs)} • {config.FOOTER}")
    return e


class PlayerControls(discord.ui.View):
    def __init__(self, player):
        super().__init__(timeout=None)
        self.player = player

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        vc = getattr(interaction.user, "voice", None)
        if not vc or not vc.channel or (
            self.player.voice and vc.channel.id != self.player.voice.channel.id
        ):
            await interaction.response.send_message(
                "❌ Bot er voice channel e join korun.", ephemeral=True
            )
            return False
        return True

    async def _refresh(self, interaction: discord.Interaction) -> None:
        if self.player.current:
            await interaction.response.edit_message(
                embed=now_playing_embed(self.player), view=self
            )
        else:
            await interaction.response.edit_message(
                embed=base_embed("⏹️ Stopped", "Kichu play hocche na."), view=None
            )

    @discord.ui.button(emoji="⏯️", style=discord.ButtonStyle.primary)
    async def toggle(self, interaction: discord.Interaction, _: discord.ui.Button):
        v = self.player.voice
        if v and v.is_playing():
            v.pause()
            self.player.paused_at = __import__("time").time()
        elif v and v.is_paused():
            v.resume()
            if self.player.paused_at:
                self.player.started_at += __import__("time").time() - self.player.paused_at
                self.player.paused_at = 0.0
        await self._refresh(interaction)

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary)
    async def skip(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.player.skip()
        await interaction.response.send_message("⏭️ Skipped.", ephemeral=True)

    @discord.ui.button(emoji="🔁", style=discord.ButtonStyle.secondary)
    async def loop(self, interaction: discord.Interaction, _: discord.ui.Button):
        order = ["off", "track", "queue"]
        self.player.loop_mode = order[(order.index(self.player.loop_mode) + 1) % 3]
        self.player.save()
        await self._refresh(interaction)

    @discord.ui.button(emoji="🔀", style=discord.ButtonStyle.secondary)
    async def shuffle(self, interaction: discord.Interaction, _: discord.ui.Button):
        n = self.player.shuffle()
        await interaction.response.send_message(f"🔀 Shuffled {n} tracks.", ephemeral=True)

    @discord.ui.button(emoji="⏹️", style=discord.ButtonStyle.danger)
    async def stop(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self.player.stop()
        await interaction.response.edit_message(
            embed=base_embed("⏹️ Stopped", "Queue clear kora hoyeche."), view=None
        )


class SearchSelect(discord.ui.View):
    def __init__(self, entries: List[dict], on_pick):
        super().__init__(timeout=60)
        self.on_pick = on_pick
        options = []
        for i, e in enumerate(entries[:10]):
            options.append(
                discord.SelectOption(
                    label=(e.get("title") or "Unknown")[:95],
                    description=f"{format_duration(int(e.get('duration') or 0))} • {(e.get('uploader') or '')[:50]}",
                    value=str(i),
                )
            )
        self.entries = entries
        select = discord.ui.Select(placeholder="Ekta track select korun…", options=options)
        select.callback = self._callback
        self.add_item(select)
        self._select = select

    async def _callback(self, interaction: discord.Interaction):
        idx = int(self._select.values[0])
        await self.on_pick(interaction, self.entries[idx])
        self.stop()
