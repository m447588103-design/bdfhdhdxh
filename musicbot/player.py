"""Per-guild audio player: queue, loop, autoplay, effects, auto-disconnect."""
from __future__ import annotations

import asyncio
import random
import time
from typing import Dict, List, Optional

import discord

from . import config, sources
from .sources import Track

FFMPEG_BEFORE = (
    "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 "
    "-nostdin -loglevel error"
)

EFFECTS: Dict[str, str] = {
    "none": "",
    "bassboost": "bass=g=12,dynaudnorm=f=200",
    "bassboost+": "bass=g=20,dynaudnorm=f=150",
    "nightcore": "asetrate=48000*1.25,aresample=48000,atempo=0.95",
    "vaporwave": "asetrate=48000*0.8,aresample=48000,atempo=1.05",
    "8d": "apulsator=hz=0.09",
    "karaoke": "pan=stereo|c0=c0-c1|c1=c1-c0",
    "clearvoice": "highpass=f=200,lowpass=f=3000,dynaudnorm",
    "treble": "treble=g=10",
    "lofi": "lowpass=f=2500,highpass=f=120,dynaudnorm",
}


def ffmpeg_executable() -> str:
    if config.FFMPEG_PATH:
        return config.FFMPEG_PATH
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


class GuildPlayer:
    def __init__(self, bot: discord.Client, guild_id: int, db):
        self.bot = bot
        self.guild_id = guild_id
        self.db = db
        s = db.guild(guild_id)

        self.queue: List[Track] = []
        self.current: Optional[Track] = None
        self.voice: Optional[discord.VoiceClient] = None
        self.text_channel: Optional[discord.abc.Messageable] = None
        self.now_message: Optional[discord.Message] = None

        self.volume: float = float(s.get("volume") or config.DEFAULT_VOLUME)
        self.loop_mode: str = s.get("loop_mode") or "off"   # off | track | queue
        self.autoplay: bool = bool(s.get("autoplay"))
        self.effect: str = s.get("effect") or "none"
        self.speed: float = float(s.get("speed") or 1.0)

        self.started_at: float = 0.0
        self.paused_at: float = 0.0
        self._next = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._idle_since: float = time.time()

    # ---------------- helpers ----------------
    @property
    def playing(self) -> bool:
        return bool(self.voice and (self.voice.is_playing() or self.voice.is_paused()))

    @property
    def position(self) -> int:
        if not self.started_at:
            return 0
        end = self.paused_at or time.time()
        return max(0, int((end - self.started_at) * max(self.speed, 0.1)))

    def save(self) -> None:
        self.db.set_guild(
            self.guild_id,
            volume=self.volume,
            loop_mode=self.loop_mode,
            autoplay=int(self.autoplay),
            effect=self.effect,
            speed=self.speed,
        )

    def filter_chain(self) -> str:
        parts = []
        eff = EFFECTS.get(self.effect, "")
        if eff:
            parts.append(eff)
        if abs(self.speed - 1.0) > 0.01:
            speed = max(0.5, min(2.0, self.speed))
            parts.append(f"atempo={speed:.2f}")
        return ",".join(parts)

    # ---------------- lifecycle ----------------
    async def connect(self, channel: discord.VoiceChannel) -> None:
        if self.voice and self.voice.is_connected():
            if self.voice.channel.id != channel.id:
                await self.voice.move_to(channel)
        else:
            self.voice = await channel.connect(self_deaf=True, reconnect=True)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._runner())

    async def disconnect(self) -> None:
        self.queue.clear()
        self.current = None
        if self._task:
            self._task.cancel()
            self._task = None
        if self.voice:
            try:
                await self.voice.disconnect(force=True)
            except Exception:
                pass
        self.voice = None
        self._cleanup_message()

    def _cleanup_message(self) -> None:
        msg, self.now_message = self.now_message, None
        if msg:
            asyncio.create_task(_safe_delete(msg))

    def skip(self) -> None:
        if self.voice and (self.voice.is_playing() or self.voice.is_paused()):
            self.voice.stop()

    async def stop(self) -> None:
        self.queue.clear()
        self.loop_mode = "off"
        self.current = None
        self.skip()
        self._cleanup_message()

    def shuffle(self) -> int:
        random.shuffle(self.queue)
        return len(self.queue)

    def add(self, tracks: List[Track]) -> int:
        space = max(0, config.MAX_QUEUE - len(self.queue))
        added = tracks[:space]
        self.queue.extend(added)
        return len(added)

    # ---------------- playback loop ----------------
    def _source_for(self, track: Track) -> discord.AudioSource:
        opts = {
            "before_options": FFMPEG_BEFORE,
            "options": "-vn",
            "executable": ffmpeg_executable(),
        }
        chain = self.filter_chain()
        if chain:
            opts["options"] = f'-vn -af "{chain}"'
        raw = discord.FFmpegPCMAudio(track.stream_url, **opts)
        return discord.PCMVolumeTransformer(raw, volume=self.volume)

    async def _runner(self) -> None:
        try:
            while True:
                self._next.clear()
                track = await self._pick_next()
                if track is None:
                    if await self._idle_expired():
                        await self.disconnect()
                        return
                    continue

                if track.expires_at < time.time():
                    try:
                        track = await sources.refresh(track)
                    except Exception:
                        continue

                self.current = track
                if not (self.voice and self.voice.is_connected()):
                    return

                try:
                    self.voice.play(
                        self._source_for(track),
                        after=lambda err: self.bot.loop.call_soon_threadsafe(self._next.set),
                    )
                except Exception as exc:  # playback failure -> skip
                    await self._notify(f"⚠️ `{track.title}` play kora gelo na: `{exc}`")
                    continue

                self.started_at = time.time()
                self.paused_at = 0.0
                self.db.history_add(
                    self.guild_id, track.requester_id, track.title, track.url
                )
                await self._post_now_playing()
                await self._next.wait()
                self.current = None
                self.started_at = 0.0
                self._idle_since = time.time()
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    async def _pick_next(self) -> Optional[Track]:
        if self.loop_mode == "track" and self.current:
            return self.current
        if self.queue:
            nxt = self.queue.pop(0)
            if self.loop_mode == "queue" and self.current:
                self.queue.append(self.current)
            return nxt
        if self.loop_mode == "queue" and self.current:
            return self.current
        if self.autoplay and self.current:
            nxt = await sources.related(
                self.current, self.current.requester_id, self.current.requester_name
            )
            if nxt:
                return nxt
        await asyncio.sleep(2)
        return None

    async def _idle_expired(self) -> bool:
        if self.playing:
            self._idle_since = time.time()
            return False
        if self.voice and self.voice.channel:
            humans = [m for m in self.voice.channel.members if not m.bot]
            if not humans:
                return time.time() - self._idle_since > 30
        return time.time() - self._idle_since > config.IDLE_TIMEOUT

    async def _notify(self, text: str) -> None:
        if self.text_channel:
            try:
                await self.text_channel.send(text)
            except Exception:
                pass

    async def _post_now_playing(self) -> None:
        if not self.text_channel or not self.current:
            return
        from .ui import PlayerControls, now_playing_embed

        self._cleanup_message()
        try:
            self.now_message = await self.text_channel.send(
                embed=now_playing_embed(self), view=PlayerControls(self)
            )
        except Exception:
            self.now_message = None


async def _safe_delete(msg: discord.Message) -> None:
    try:
        await msg.delete()
    except Exception:
        pass


class PlayerManager:
    def __init__(self, bot: discord.Client, db):
        self.bot = bot
        self.db = db
        self._players: Dict[int, GuildPlayer] = {}

    def get(self, guild_id: int) -> GuildPlayer:
        p = self._players.get(guild_id)
        if p is None:
            p = GuildPlayer(self.bot, guild_id, self.db)
            self._players[guild_id] = p
        return p

    def existing(self, guild_id: int) -> Optional[GuildPlayer]:
        return self._players.get(guild_id)

    @property
    def all(self):
        return list(self._players.values())

    async def destroy(self, guild_id: int) -> None:
        p = self._players.pop(guild_id, None)
        if p:
            await p.disconnect()
