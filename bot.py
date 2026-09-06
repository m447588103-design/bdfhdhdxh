import asyncio
import logging
import os
import random
import re
import sqlite3
import io
import hashlib
from dataclasses import dataclass
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
import imageio_ffmpeg
import yt_dlp
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
DEFAULT_VOLUME = max(0.0, min(1.5, float(os.getenv("DEFAULT_VOLUME", "0.70"))))
MAX_QUEUE = max(1, int(os.getenv("MAX_QUEUE", "100")))
DB_PATH = os.getenv("DATABASE_PATH", "data/xenon_music.db")
COOKIES = os.getenv("YTDLP_COOKIES", "").strip()
CUSTOM_FFMPEG = os.getenv("FFMPEG_PATH", "").strip()
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "").strip()
FFMPEG = CUSTOM_FFMPEG or imageio_ffmpeg.get_ffmpeg_exe()

os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("XenonMusic")

YTDLP_OPTS = {
    "format": "bestaudio[acodec!=none]/bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "extract_flat": False,
    "skip_download": True,
    "geo_bypass": True,
    "nocheckcertificate": True,
    "retries": 5,
    "fragment_retries": 5,
    "socket_timeout": 20,
    "concurrent_fragment_downloads": 1,
}
if COOKIES:
    YTDLP_OPTS["cookiefile"] = COOKIES

@dataclass
class Track:
    title: str
    webpage_url: str
    duration: int
    requester: str
    thumbnail: Optional[str] = None
    source: str = "YouTube"
    stream_url: Optional[str] = None
    search_query: Optional[str] = None

class DB:
    def __init__(self, path):
        self.path = path
        with sqlite3.connect(path) as c:
            c.execute("CREATE TABLE IF NOT EXISTS settings (guild_id INTEGER PRIMARY KEY, request_channel INTEGER, volume REAL, autoplay INTEGER, loop TEXT, mode247 INTEGER DEFAULT 0, effect TEXT DEFAULT 'off', speed REAL DEFAULT 1.0)")
            c.execute("CREATE TABLE IF NOT EXISTS favorites (guild_id INTEGER, user_id INTEGER, title TEXT, url TEXT, thumbnail TEXT, UNIQUE(guild_id,user_id,url))")
            c.execute("CREATE TABLE IF NOT EXISTS history (guild_id INTEGER, user_id INTEGER, title TEXT, url TEXT, played_at INTEGER)")
            c.commit()
            # Upgrade old V4/V5 database safely.
            try: c.execute("ALTER TABLE settings ADD COLUMN mode247 INTEGER DEFAULT 0")
            except sqlite3.OperationalError: pass
            try: c.execute("ALTER TABLE settings ADD COLUMN effect TEXT DEFAULT 'off'")
            except sqlite3.OperationalError: pass
            try: c.execute("ALTER TABLE settings ADD COLUMN speed REAL DEFAULT 1.0")
            except sqlite3.OperationalError: pass
            c.commit()
    def get(self, gid):
        with sqlite3.connect(self.path) as c:
            return c.execute("SELECT request_channel,volume,autoplay,loop,mode247,effect,speed FROM settings WHERE guild_id=?", (gid,)).fetchone()
    def set(self, gid, **kw):
        old = self.get(gid) or (None, DEFAULT_VOLUME, 0, "off", 0, "off", 1.0)
        vals = {"request_channel":old[0],"volume":old[1],"autoplay":old[2],"loop":old[3],"mode247":old[4],"effect":old[5] or "off","speed":float(old[6] or 1.0)}
        vals.update(kw)
        with sqlite3.connect(self.path) as c:
            c.execute("INSERT OR REPLACE INTO settings (guild_id,request_channel,volume,autoplay,loop,mode247,effect,speed) VALUES (?,?,?,?,?,?,?,?)", (gid,vals["request_channel"],vals["volume"],int(vals["autoplay"]),vals["loop"],int(vals["mode247"]),vals["effect"],vals["speed"]))
            c.commit()
    def favorite_add(self,gid,uid,t):
        with sqlite3.connect(self.path) as c:
            c.execute("INSERT OR REPLACE INTO favorites VALUES (?,?,?,?,?)",(gid,uid,t.title,t.webpage_url,t.thumbnail));c.commit()
    def favorite_remove(self,gid,uid,url):
        with sqlite3.connect(self.path) as c:
            c.execute("DELETE FROM favorites WHERE guild_id=? AND user_id=? AND url=?",(gid,uid,url));c.commit()
    def favorites(self,gid,uid):
        with sqlite3.connect(self.path) as c:return c.execute("SELECT title,url,thumbnail FROM favorites WHERE guild_id=? AND user_id=? ORDER BY rowid DESC LIMIT 50",(gid,uid)).fetchall()
    def history_add(self,gid,uid,t):
        with sqlite3.connect(self.path) as c:
            c.execute("INSERT INTO history VALUES (?,?,?,?,strftime('%s','now'))",(gid,uid,t.title,t.webpage_url))
            c.execute("DELETE FROM history WHERE guild_id=? AND rowid NOT IN (SELECT rowid FROM history WHERE guild_id=? ORDER BY played_at DESC LIMIT 100)",(gid,gid));c.commit()
    def history(self,gid):
        with sqlite3.connect(self.path) as c:return c.execute("SELECT title,url,played_at FROM history WHERE guild_id=? ORDER BY played_at DESC LIMIT 20",(gid,)).fetchall()

db=DB(DB_PATH)

@dataclass
class GuildPlayer:
    guild_id: int
    queue: list = None
    current: Optional[Track] = None
    voice: Optional[discord.VoiceClient] = None
    volume: float = DEFAULT_VOLUME
    loop: str = "off"
    autoplay: bool = False
    mode247: bool = False
    effect: str = "off"
    speed: float = 1.0
    paused: bool = False
    panel_message: Optional[discord.Message] = None
    lock: asyncio.Lock = None
    skip_requested: bool = False
    generation: int = 0
    def __post_init__(self):
        self.queue=[]; self.lock=asyncio.Lock()
    async def connect(self, channel):
        if self.voice and self.voice.is_connected():
            if self.voice.channel.id != channel.id: await self.voice.move_to(channel)
            return self.voice
        self.voice=await channel.connect(timeout=25,reconnect=True)
        return self.voice
    async def stop(self):
        self.generation+=1;self.queue.clear();self.current=None;self.skip_requested=True
        if self.voice and (self.voice.is_playing() or self.voice.is_paused()): self.voice.stop()
        self.paused=False
    async def disconnect(self):
        await self.stop()
        if self.voice and self.voice.is_connected(): await self.voice.disconnect(force=True)
        self.voice=None

players={}
def get_player(gid): return players.setdefault(gid,GuildPlayer(gid))

def fmt_time(s):
    if not s:return "LIVE"
    s=int(s);return f"{s//3600}:{(s%3600)//60:02d}:{s%60:02d}" if s>=3600 else f"{s//60}:{s%60:02d}"

def spotify_track_url(url): return bool(re.search(r"open\.spotify\.com/(?:intl-[^/]+/)?track/",url,re.I))
def spotify_kind(url):
    m=re.search(r"open\.spotify\.com/(?:intl-[^/]+/)?(track|album|playlist)/",url,re.I);return m.group(1).lower() if m else None

def extract_info_sync(query):
    opts=dict(YTDLP_OPTS)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info=ydl.extract_info(query,download=False)
        if info and info.get("entries") is not None:
            info=next((x for x in info["entries"] if x),None)
        if not info: raise RuntimeError("No playable result found.")
        return info

async def spotify_token():
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        raise RuntimeError("Spotify API is not configured. Add SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET to .env.")
    import base64, time
    if getattr(spotify_token, "cached", None) and spotify_token.cached[1] > time.time() + 30:
        return spotify_token.cached[0]
    raw = f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}".encode()
    auth = base64.b64encode(raw).decode()
    async with aiohttp.ClientSession() as s:
        async with s.post("https://accounts.spotify.com/api/token", headers={"Authorization": f"Basic {auth}"}, data={"grant_type":"client_credentials"}, timeout=aiohttp.ClientTimeout(total=15)) as r:
            data = await r.json(content_type=None)
            if r.status != 200:
                raise RuntimeError(f"Spotify authentication failed: {data.get('error_description', data)}")
            import time
            spotify_token.cached = (data["access_token"], time.time() + int(data.get("expires_in", 3600)))
            return data["access_token"]

async def spotify_api(path):
    token = await spotify_token()
    async with aiohttp.ClientSession(headers={"Authorization": f"Bearer {token}"}) as s:
        async with s.get("https://api.spotify.com/v1/" + path.lstrip("/"), timeout=aiohttp.ClientTimeout(total=20)) as r:
            data = await r.json(content_type=None)
            if r.status != 200:
                raise RuntimeError(f"Spotify API error {r.status}: {data.get('error', {}).get('message', data)}")
            return data

async def spotify_metadata(url):
    # API first; oEmbed/HTML fallback keeps public track links working without credentials.
    m = re.search(r"open\.spotify\.com/(?:intl-[^/]+/)?track/([A-Za-z0-9]+)", url, re.I)
    if m and SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET:
        try:
            d = await spotify_api(f"tracks/{m.group(1)}")
            artists = ", ".join(a["name"] for a in d.get("artists", []))
            return d.get("name", ""), artists, (d.get("album", {}).get("images") or [{}])[0].get("url")
        except Exception as e:
            log.warning("Spotify track API fallback: %s", e)
    headers={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36"}
    async with aiohttp.ClientSession(headers=headers) as s:
        try:
            async with s.get("https://open.spotify.com/oembed",params={"url":url},timeout=aiohttp.ClientTimeout(total=12)) as r:
                if r.status==200:
                    d=await r.json(content_type=None);title=(d.get("title") or "").strip();author=(d.get("author_name") or "").strip()
                    if title:return title,author,d.get("thumbnail_url")
        except Exception as e: log.warning("Spotify oEmbed: %s",e)
        try:
            async with s.get(url,timeout=aiohttp.ClientTimeout(total=12),allow_redirects=True) as r: html=await r.text(errors="ignore")
            def meta(prop):
                m=re.search(r'<meta[^>]+(?:property|name)=["\']'+re.escape(prop)+r'["\'][^>]+content=["\']([^"\']+)',html,re.I)
                return re.sub(r"\s+"," ",m.group(1)).strip() if m else ""
            title=meta("og:title");thumb=meta("og:image") or None
            if title:
                artist="";mm=re.search(r"(?:by|·|–|-|—)\s*(.+)$",title)
                if mm:artist=mm.group(1).strip()
                return title,artist,thumb
        except Exception as e: log.warning("Spotify HTML: %s",e)
    return None,None,None

async def spotify_collection(url):
    kind = spotify_kind(url)
    if kind not in {"playlist", "album"}:
        raise RuntimeError("Not a Spotify playlist/album URL.")
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        raise RuntimeError("Spotify playlist/album requires API credentials. Add SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET to .env.")
    m = re.search(rf"open\.spotify\.com/(?:intl-[^/]+/)?{kind}/([A-Za-z0-9]+)", url, re.I)
    if not m: raise RuntimeError("Invalid Spotify URL.")
    ident=m.group(1)
    tracks=[]
    if kind=="album":
        d=await spotify_api(f"albums/{ident}")
        name=d.get("name", "Spotify Album")
        artist=", ".join(a["name"] for a in d.get("artists", []))
        image=(d.get("images") or [{}])[0].get("url")
        items=d.get("tracks",{}).get("items",[])
        while d.get("tracks",{}).get("next"):
            token=await spotify_token()
            async with aiohttp.ClientSession(headers={"Authorization":f"Bearer {token}"}) as s:
                async with s.get(d["tracks"]["next"],timeout=aiohttp.ClientTimeout(total=20)) as r:d=await r.json(content_type=None)
            items += d.get("items",[])
    else:
        d=await spotify_api(f"playlists/{ident}")
        name=d.get("name", "Spotify Playlist");image=(d.get("images") or [{}])[0].get("url")
        items=d.get("tracks",{}).get("items",[])
        while d.get("tracks",{}).get("next"):
            token=await spotify_token()
            async with aiohttp.ClientSession(headers={"Authorization":f"Bearer {token}"}) as s:
                async with s.get(d["tracks"]["next"],timeout=aiohttp.ClientTimeout(total=20)) as r:d=await r.json(content_type=None)
            items += d.get("items",[])
    for item in items:
        tr=item.get("track") if kind=="playlist" else item
        if not tr or not tr.get("name"): continue
        artists=", ".join(a["name"] for a in tr.get("artists",[]))
        tracks.append({"title":tr["name"],"artist":artists,"query":f"{tr['name']} {artists}"})
    return name, image, tracks

async def normalize_query(query):
    q=query.strip()
    if spotify_track_url(q):
        title,artist,thumb=await spotify_metadata(q)
        if not title: raise RuntimeError("Spotify track could not be read. Make sure the link is public.")
        return "ytsearch1:"+f"{title} {artist}".strip(),"Spotify → YouTube",thumb,title
    if "open.spotify.com" in q.lower():
        kind=spotify_kind(q)
        if kind in {"playlist","album"}:
            return q,"Spotify Collection",None,None
        raise RuntimeError("Unsupported Spotify URL. Use a public Spotify track, album or playlist link.")
    return q,"YouTube",None,None

async def resolve(query,requester):
    q,source,thumb,external_title=await normalize_query(query)
    info=await asyncio.to_thread(extract_info_sync,q)
    webpage=info.get("webpage_url") or info.get("original_url")
    if not webpage: raise RuntimeError("Could not resolve a playable YouTube result.")
    return Track(info.get("title") or external_title or "Unknown",webpage,info.get("duration") or 0,requester,info.get("thumbnail") or thumb,source,info.get("url"),q)

async def refresh_stream(t):
    info=await asyncio.to_thread(extract_info_sync,t.webpage_url)
    stream=info.get("url")
    if not stream: raise RuntimeError("No audio stream returned by yt-dlp.")
    t.stream_url=stream;t.title=info.get("title") or t.title;t.thumbnail=info.get("thumbnail") or t.thumbnail;t.duration=info.get("duration") or t.duration
    return t


PANEL_GIF_DIR = os.path.join(os.path.dirname(DB_PATH) or ".", "animated_panels")
os.makedirs(PANEL_GIF_DIR, exist_ok=True)

async def build_server_panel_gif(guild: discord.Guild) -> Optional[str]:
    """Create a small animated GIF using the Discord server icon + animated equalizer."""
    path = os.path.join(PANEL_GIF_DIR, f"{guild.id}.gif")
    try:
        icon_url = guild.icon.url if guild.icon else None
        icon_bytes = None
        if icon_url:
            async with aiohttp.ClientSession() as session:
                async with session.get(str(icon_url), timeout=10) as r:
                    if r.status == 200:
                        icon_bytes = await r.read()

        W, H = 900, 260
        frames = []
        for frame_no in range(12):
            im = Image.new("RGB", (W, H), (8, 8, 18))
            d = ImageDraw.Draw(im)

            # subtle animated background glow
            glow_x = 110 + int(18 * __import__("math").sin(frame_no * 0.55))
            for radius in range(115, 15, -10):
                alpha = max(8, int(42 * (1 - radius / 125)))
                overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                od = ImageDraw.Draw(overlay)
                od.ellipse((glow_x-radius, 130-radius, glow_x+radius, 130+radius),
                           fill=(124, 58, 237, alpha))
                im = Image.alpha_composite(im.convert("RGBA"), overlay).convert("RGB")
                d = ImageDraw.Draw(im)

            # server icon
            if icon_bytes:
                try:
                    logo = Image.open(io.BytesIO(icon_bytes)).convert("RGBA")
                    logo.thumbnail((150, 150), Image.LANCZOS)
                    mask = Image.new("L", logo.size, 0)
                    md = ImageDraw.Draw(mask)
                    md.ellipse((0, 0, logo.width, logo.height), fill=255)
                    logo.putalpha(mask)
                    lx, ly = 35 + (150-logo.width)//2, 55 + (150-logo.height)//2
                    im.paste(logo, (lx, ly), logo)
                except Exception:
                    icon_bytes = None

            if not icon_bytes:
                d.rounded_rectangle((35, 55, 185, 205), radius=35, outline=(124,58,237), width=4)
                d.text((72, 105), "X", fill=(245,245,255))

            # title
            d.text((225, 58), "XENON MUSIC", fill=(245,245,255))
            d.text((225, 92), "NOW PLAYING", fill=(190,180,255))

            # animated equalizer
            base_y = 205
            for x in range(235, 830, 32):
                phase = (x // 32) * 0.65
                h = 18 + int(75 * (0.5 + 0.5 * __import__("math").sin(frame_no * 0.75 + phase)))
                d.rounded_rectangle((x, base_y-h, x+16, base_y), radius=5, fill=(124,58,237))

            d.text((225, 225), "✦ OG EDITION • LIVE AUDIO ✦", fill=(150,145,175))
            frames.append(im)

        frames[0].save(path, save_all=True, append_images=frames[1:],
                       duration=90, loop=0, optimize=True)
        return path
    except Exception as exc:
        log.warning("Could not build server panel GIF for guild %s: %s", guild.id, exc)
        return None

def player_embed(p, animated_panel=True):
    e=discord.Embed(title="✦ XENON MUSIC • OG PLAYER ✦",color=0x7C3AED)
    if animated_panel:
        e.set_image(url="attachment://xenon_nowplaying.gif")
    if not p.current:e.description="`Nothing is playing.`\n\nUse **/play <song or link>** to start."
    else:
        e.description=f"## 🎵 {p.current.title}\n`{p.current.source}`"
        if p.current.thumbnail:e.set_thumbnail(url=p.current.thumbnail)
        e.add_field(name="👤 Requester",value=p.current.requester,inline=True);e.add_field(name="⏱️ Length",value=fmt_time(p.current.duration),inline=True);e.add_field(name="🔊 Volume",value=f"{round(p.volume*100)}%",inline=True)
        e.add_field(name="🔁 Loop",value=p.loop.upper(),inline=True);e.add_field(name="🤖 Auto",value="ON" if p.autoplay else "OFF",inline=True);e.add_field(name="📜 Queue",value=str(len(p.queue)),inline=True);e.add_field(name="🎚️ Effect",value=p.effect.upper(),inline=True);e.add_field(name="⚡ Speed",value=f"{p.speed:.2f}x",inline=True)
    e.set_footer(text="🐺 WHITE WOLF GLOBAL • ⚡ Made with Joy")
    return e

class PlayerView(discord.ui.View):
    def __init__(self,gid):super().__init__(timeout=None);self.gid=gid
    async def interaction_check(self,i):
        p=get_player(self.gid)
        if not i.user.voice or not p.voice or not p.voice.channel or i.user.voice.channel.id!=p.voice.channel.id:
            await i.response.send_message("🎧 Join my voice channel first.",ephemeral=True);return False
        return True
    @discord.ui.button(label="Pause",emoji="⏯️",style=discord.ButtonStyle.secondary,custom_id="xenon_pause")
    async def pause(self,i,_):
        p=get_player(self.gid)
        if p.voice and p.voice.is_playing():p.voice.pause();p.paused=True
        elif p.voice and p.voice.is_paused():p.voice.resume();p.paused=False
        else:return await i.response.send_message("Nothing is playing.",ephemeral=True)
        await i.response.edit_message(embed=player_embed(p),view=PlayerView(self.gid))
    @discord.ui.button(label="Skip",emoji="⏭️",style=discord.ButtonStyle.primary,custom_id="xenon_skip")
    async def skip(self,i,_):
        p=get_player(self.gid);p.skip_requested=True
        if p.voice and (p.voice.is_playing() or p.voice.is_paused()):p.voice.stop()
        await i.response.send_message("⏭️ Skipped.",ephemeral=True)
    @discord.ui.button(label="Shuffle",emoji="🔀",style=discord.ButtonStyle.secondary,custom_id="xenon_shuffle")
    async def shuffle(self,i,_):
        p=get_player(self.gid);random.shuffle(p.queue);await i.response.send_message(f"🔀 Shuffled **{len(p.queue)}** tracks.",ephemeral=True)
    @discord.ui.button(label="Queue",emoji="📜",style=discord.ButtonStyle.secondary,custom_id="xenon_queue")
    async def queue(self,i,_):
        p=get_player(self.gid);txt="\n".join(f"`{n}.` {t.title[:75]}" for n,t in enumerate(p.queue[:15],1)) or "Queue empty."
        await i.response.send_message(embed=discord.Embed(title="📜 🐺 WHITE WOLF GLOBAL • QUEUE",description=txt,color=0x7C3AED),ephemeral=True)
    @discord.ui.button(label="Stop",emoji="⏹️",style=discord.ButtonStyle.danger,custom_id="xenon_stop")
    async def stop(self,i,_):
        p=get_player(self.gid);await p.stop();await i.response.edit_message(embed=player_embed(p),view=PlayerView(self.gid))

class SearchView(discord.ui.View):
    def __init__(self,gid,uid,entries,cog):
        super().__init__(timeout=60);self.gid=gid;self.uid=uid;self.entries=entries;self.cog=cog
        for n in range(min(5,len(entries))):
            b=discord.ui.Button(label=str(n+1),style=discord.ButtonStyle.primary);b.callback=self.callback_for(n);self.add_item(b)
    def callback_for(self,n):
        async def cb(i):
            if i.user.id!=self.uid:return await i.response.send_message("❌ This search belongs to another user.",ephemeral=True)
            await i.response.defer(ephemeral=True)
            try:
                if not i.user.voice:raise RuntimeError("Join a voice channel first.")
                p=get_player(self.gid);await p.connect(i.user.voice.channel);x=self.entries[n]
                url=x.get("webpage_url") or x.get("original_url")
                if not url:raise RuntimeError("Selected result has no URL.")
                t=await resolve(url,i.user.display_name);p.queue.append(t)
                if not p.current:await self.cog.play_next(self.gid)
                await i.followup.send(f"🎵 Added **{t.title}**",ephemeral=True)
            except Exception as e:await i.followup.send(f"❌ {e}",ephemeral=True)
        return cb

class Music(commands.Cog):
    def __init__(self,bot):self.bot=bot
    async def ensure_voice(self,i):
        if not i.guild:raise RuntimeError("Server only command.")
        if not i.user.voice or not i.user.voice.channel:raise RuntimeError("🎧 Join a voice channel first.")
        p=get_player(i.guild.id);saved=db.get(i.guild.id)
        if saved:
            p.volume=float(saved[1] if saved[1] is not None else DEFAULT_VOLUME);p.autoplay=bool(saved[2]);p.loop=saved[3] or "off";p.mode247=bool(saved[4]);p.effect=saved[5] or "off";p.speed=float(saved[6] or 1.0)
        try:return await p.connect(i.user.voice.channel)
        except discord.Forbidden:raise RuntimeError("Give me View Channel, Connect and Speak permissions.")
        except Exception as e:raise RuntimeError(f"Voice connection failed: {type(e).__name__}: {e}")

    @app_commands.command(name="play",description="Play YouTube or a public Spotify track")
    async def play(self,i,query:str):
        await i.response.defer(thinking=True)
        try:
            await self.ensure_voice(i);p=get_player(i.guild.id)
            if "open.spotify.com" in query.lower() and spotify_kind(query) in {"playlist","album"}:
                name,image,items=await spotify_collection(query)
                remaining=max(0,MAX_QUEUE-len(p.queue)); items=items[:remaining]
                if not items: raise RuntimeError("Queue is full.")
                added=[]
                for item in items:
                    try:
                        t=await resolve("ytsearch1:"+item["query"],i.user.display_name); t.source="Spotify → YouTube"; added.append(t)
                    except Exception as e: log.warning("Spotify item skipped: %s",e)
                if not added: raise RuntimeError("No playable tracks were found in this Spotify collection.")
                p.queue.extend(added)
                if not p.current: await self.play_next(i.guild.id)
                e=discord.Embed(title="✦ SPOTIFY COLLECTION ADDED ✦",description=f"### 🎧 {name}\nAdded **{len(added)}** playable tracks.\n\nSpotify audio is resolved to matching playable sources for Discord voice.",color=0x1DB954)
                if image:e.set_thumbnail(url=image)
                return await i.followup.send(embed=e)
            if len(p.queue)>=MAX_QUEUE:raise RuntimeError(f"Queue limit reached ({MAX_QUEUE}).")
            t=await resolve(query,i.user.display_name);p.queue.append(t)
            if not p.current:await self.play_next(i.guild.id)
            e=discord.Embed(title="✦ ADDED TO XENON ✦",description=f"### 🎵 {t.title}\n`{t.source}` • `{fmt_time(t.duration)}`\n\nRequested by **{t.requester}**",color=0x7C3AED)
            if t.thumbnail:e.set_thumbnail(url=t.thumbnail)
            await i.followup.send(embed=e)
        except Exception as e:log.exception("/play failed");await i.followup.send(f"❌ **Playback failed**\n`{type(e).__name__}: {e}`")

    @app_commands.command(name="search",description="Search YouTube and choose a result")
    async def search(self,i,query:str):
        await i.response.defer(thinking=True)
        try:
            info=await asyncio.to_thread(extract_info_sync,"ytsearch5:"+query);entries=info.get("entries") or []
            if not entries:raise RuntimeError("No results.")
            desc="\n".join(f"**{n+1}.** {x.get('title','Unknown')[:75]}" for n,x in enumerate(entries))
            await i.followup.send(embed=discord.Embed(title="🔎 🐺 WHITE WOLF GLOBAL • SEARCH",description=desc,color=0x7C3AED),view=SearchView(i.guild.id,i.user.id,entries,self))
        except Exception as e:await i.followup.send(f"❌ Search failed: `{e}`")

    @app_commands.command(name="join",description="Join your voice channel")
    async def join(self,i):
        await i.response.defer(ephemeral=True)
        try:v=await self.ensure_voice(i);await i.followup.send(f"✦ Connected to **{v.channel.name}**.",ephemeral=True)
        except Exception as e:await i.followup.send(f"❌ {e}",ephemeral=True)
    @app_commands.command(name="leave",description="Disconnect and clear queue")
    async def leave(self,i):
        await i.response.defer(ephemeral=True);p=get_player(i.guild.id);await p.disconnect();await i.followup.send("✦ Disconnected.",ephemeral=True)
    @app_commands.command(name="pause",description="Pause playback")
    async def pause(self,i):
        p=get_player(i.guild.id)
        if p.voice and p.voice.is_playing():p.voice.pause();p.paused=True;await i.response.send_message("⏸️ Paused.")
        else:await i.response.send_message("Nothing is playing.",ephemeral=True)
    @app_commands.command(name="resume",description="Resume playback")
    async def resume(self,i):
        p=get_player(i.guild.id)
        if p.voice and p.voice.is_paused():p.voice.resume();p.paused=False;await i.response.send_message("▶️ Resumed.")
        else:await i.response.send_message("Nothing is paused.",ephemeral=True)
    @app_commands.command(name="skip",description="Skip current track")
    async def skip(self,i):
        p=get_player(i.guild.id);p.skip_requested=True
        if p.voice and (p.voice.is_playing() or p.voice.is_paused()):p.voice.stop()
        await i.response.send_message("⏭️ Skipped.")
    @app_commands.command(name="stop",description="Stop and clear queue")
    async def stop(self,i):p=get_player(i.guild.id);await p.stop();await i.response.send_message("⏹️ Stopped.")
    @app_commands.command(name="queue",description="Show queue")
    async def queue(self,i):
        p=get_player(i.guild.id);txt="\n".join(f"`{n:02}` • {t.title[:75]} — `{fmt_time(t.duration)}`" for n,t in enumerate(p.queue[:20],1)) or "Queue is empty."
        e=discord.Embed(title="📜 🐺 WHITE WOLF GLOBAL • QUEUE",description=txt,color=0x7C3AED)
        if p.current:e.add_field(name="▶️ NOW PLAYING",value=p.current.title[:100],inline=False)
        await i.response.send_message(embed=e)
    @app_commands.command(name="nowplaying",description="Open the OG player")
    async def nowplaying(self,i):
        p=get_player(i.guild.id)
        gif_path=await build_server_panel_gif(i.guild)
        if gif_path and os.path.exists(gif_path):
            await i.response.send_message(
                embed=player_embed(p, True),
                view=PlayerView(i.guild.id),
                file=discord.File(gif_path, filename="xenon_nowplaying.gif")
            )
        else:
            await i.response.send_message(embed=player_embed(p, False),view=PlayerView(i.guild.id))
        p.panel_message=await i.original_response()
    @app_commands.command(name="effect",description="Apply a real-time FFmpeg audio effect")
    @app_commands.choices(mode=[
        app_commands.Choice(name="Off",value="off"),
        app_commands.Choice(name="Bass Boost",value="bassboost"),
        app_commands.Choice(name="Bass Boost+",value="bassboost_plus"),
        app_commands.Choice(name="Nightcore",value="nightcore"),
        app_commands.Choice(name="Vaporwave",value="vaporwave"),
        app_commands.Choice(name="8D",value="8d"),
        app_commands.Choice(name="Karaoke",value="karaoke"),
        app_commands.Choice(name="Clear Voice",value="clearvoice"),
    ])
    async def effect(self,i,mode:app_commands.Choice[str]):
        p=get_player(i.guild.id);p.effect=mode.value;db.set(i.guild.id,effect=p.effect)
        if p.current:
            t=p.current;p.generation+=1;p.skip_requested=True
            if p.voice and (p.voice.is_playing() or p.voice.is_paused()):p.voice.stop()
            p.current=None;p.queue.insert(0,t);await self.play_next(i.guild.id)
        await i.response.send_message(f"🎚️ Effect: **{mode.name}**")

    @app_commands.command(name="speed",description="Set playback speed 0.5x-2.0x")
    async def speed(self,i,multiplier:app_commands.Range[float,0.5,2.0]):
        multiplier=round(float(multiplier),2);p=get_player(i.guild.id);p.speed=multiplier;db.set(i.guild.id,speed=multiplier)
        if p.current:
            t=p.current;p.generation+=1;p.skip_requested=True
            if p.voice and (p.voice.is_playing() or p.voice.is_paused()):p.voice.stop()
            p.current=None;p.queue.insert(0,t);await self.play_next(i.guild.id)
        await i.response.send_message(f"⚡ Speed: **{multiplier}x**")

    @app_commands.command(name="volume",description="Set volume 0-150")
    async def volume(self,i,percent:app_commands.Range[int,0,150]):
        p=get_player(i.guild.id);p.volume=percent/100
        if p.voice and p.voice.source and hasattr(p.voice.source,"volume"):p.voice.source.volume=p.volume
        db.set(i.guild.id,volume=p.volume);await i.response.send_message(f"🔊 Volume: **{percent}%**")
    @app_commands.command(name="loop",description="Set loop mode")
    @app_commands.choices(mode=[app_commands.Choice(name="Off",value="off"),app_commands.Choice(name="Track",value="track"),app_commands.Choice(name="Queue",value="queue")])
    async def loop(self,i,mode:app_commands.Choice[str]):
        p=get_player(i.guild.id);p.loop=mode.value;db.set(i.guild.id,loop=p.loop);await i.response.send_message(f"🔁 Loop: **{mode.name}**")
    @app_commands.command(name="autoplay",description="Toggle autoplay")
    async def autoplay(self,i,enabled:Optional[bool]=None):
        p=get_player(i.guild.id);p.autoplay=(not p.autoplay) if enabled is None else enabled;db.set(i.guild.id,autoplay=p.autoplay);await i.response.send_message(f"🤖 Autoplay **{'ON' if p.autoplay else 'OFF'}**")
    @app_commands.command(name="shuffle",description="Shuffle queue")
    async def shuffle(self,i):p=get_player(i.guild.id);random.shuffle(p.queue);await i.response.send_message(f"🔀 Shuffled **{len(p.queue)}** tracks.")
    @app_commands.command(name="remove",description="Remove queue position")
    async def remove(self,i,position:app_commands.Range[int,1,100]):
        p=get_player(i.guild.id)
        if position>len(p.queue):return await i.response.send_message("❌ Invalid queue position.",ephemeral=True)
        t=p.queue.pop(position-1);await i.response.send_message(f"🗑️ Removed **{t.title}**")
    @app_commands.command(name="clear",description="Clear queued tracks")
    async def clear(self,i):p=get_player(i.guild.id);n=len(p.queue);p.queue.clear();await i.response.send_message(f"🧹 Cleared **{n}** tracks.")
    @app_commands.command(name="favorite",description="Add current track to your favorites")
    async def favorite(self,i):
        p=get_player(i.guild.id)
        if not p.current:return await i.response.send_message("❌ Nothing is playing.",ephemeral=True)
        db.favorite_add(i.guild.id,i.user.id,p.current);await i.response.send_message("❤️ Saved to favorites.",ephemeral=True)
    @app_commands.command(name="favorites",description="Show your favorites")
    async def favorites(self,i):
        rows=db.favorites(i.guild.id,i.user.id)
        if not rows:return await i.response.send_message("❤️ No favorites yet.",ephemeral=True)
        await i.response.send_message(embed=discord.Embed(title="❤️ 🐺 WHITE WOLF GLOBAL • FAVORITES",description="\n".join(f"**{n+1}.** [{t[:65]}]({u})" for n,(t,u,_) in enumerate(rows)),color=0x7C3AED),ephemeral=True)
    @app_commands.command(name="history",description="Show recently played tracks")
    async def history(self,i):
        rows=db.history(i.guild.id)
        if not rows:return await i.response.send_message("🕘 No history yet.",ephemeral=True)
        await i.response.send_message(embed=discord.Embed(title="🕘 🐺 WHITE WOLF GLOBAL • HISTORY",description="\n".join(f"**{n+1}.** [{t[:70]}]({u})" for n,(t,u,_) in enumerate(rows)),color=0x7C3AED),ephemeral=True)
    @app_commands.command(name="lyrics",description="Show lyrics for the current track")
    async def lyrics(self,i):
        p=get_player(i.guild.id)
        if not p.current:return await i.response.send_message("🎤 Nothing is playing.",ephemeral=True)
        await i.response.defer(ephemeral=True,thinking=True)
        title=p.current.title
        try:
            # LRCLIB provides free plain/synced lyrics. Search by the exact
            # currently playing title, then choose the closest usable result.
            url="https://lrclib.net/api/search?q="+aiohttp.helpers.quote(title)
            timeout=aiohttp.ClientTimeout(total=12)
            async with aiohttp.ClientSession(timeout=timeout,headers={"User-Agent":"XenonMusic/9.0"}) as session:
                async with session.get(url) as r:
                    if r.status!=200: raise RuntimeError(f"LRCLIB HTTP {r.status}")
                    data=await r.json(content_type=None)
            if not isinstance(data,list) or not data:
                return await i.followup.send(f"🎤 Lyrics not found for **{title}**.\n🔎 https://lrclib.net/search?q={aiohttp.helpers.quote(title)}",ephemeral=True)
            item=next((x for x in data if x.get("plainLyrics") or x.get("syncedLyrics")),data[0])
            lyrics=item.get("plainLyrics") or item.get("syncedLyrics") or ""
            lyrics=re.sub(r"\[[0-9:.]+\]", "", lyrics).strip()
            if not lyrics:
                return await i.followup.send(f"🎤 Lyrics not available for **{title}**.\n🔎 https://lrclib.net/search?q={aiohttp.helpers.quote(title)}",ephemeral=True)
            # Discord embed descriptions are limited to 4096 chars.
            chunks=[lyrics[n:n+3800] for n in range(0,len(lyrics),3800)]
            if len(chunks)>4: chunks=chunks[:4]
            for n,chunk in enumerate(chunks):
                embed=discord.Embed(title=f"🎤 {item.get('trackName') or title}" + (f" • {n+1}/{len(chunks)}" if len(chunks)>1 else ""),description=chunk,color=0x7C3AED)
                artist=item.get("artistName")
                if artist: embed.set_footer(text=f"🐺 WHITE WOLF GLOBAL • ⚡ Made with Joy • {artist}")
                await i.followup.send(embed=embed,ephemeral=True)
        except Exception as e:
            log.warning("Lyrics lookup failed: %s",e)
            await i.followup.send(f"❌ Lyrics service unavailable right now.\n🔎 https://lrclib.net/search?q={aiohttp.helpers.quote(title)}",ephemeral=True)
    @app_commands.command(name="247",description="Toggle persistent 24/7 voice mode")
    async def stay247(self,i,enabled:bool=True):
        p=get_player(i.guild.id)
        if enabled:await self.ensure_voice(i);p.mode247=True
        else:p.mode247=False
        db.set(i.guild.id,mode247=p.mode247);await i.response.send_message(f"♾️ 24/7 **{'ON' if p.mode247 else 'OFF'}**")
    @app_commands.command(name="about",description="About White Wolf Global Music Bot")
    async def about(self,i):
        e=discord.Embed(title="🐺 WHITE WOLF GLOBAL MUSIC",description="**Premium music experience for White Wolf Global**\n\n🎵 Multi-source playback • 🎚️ FFmpeg effects • 🎤 Lyrics • 🎬 Animated Now Playing",color=0x7C3AED)
        e.add_field(name="🎧 Music",value="YouTube • Spotify Track/Album/Playlist • Search",inline=False)
        e.add_field(name="🎚️ Audio",value="Bass Boost • Bass Boost+ • Nightcore • Vaporwave • 8D • Karaoke • Clear Voice • Speed",inline=False)
        e.add_field(name="✨ Player",value="Animated server-logo panel • Progress • Queue • Loop • Autoplay • Favorites • History",inline=False)
        e.add_field(name="🛡️ Server",value="DJ controls • Music request channel • Per-server settings • 24/7 mode",inline=False)
        e.set_footer(text="⚡ Made with Joy • White Wolf Global")
        await i.response.send_message(embed=e)

    @app_commands.command(name="musichelp",description="Show all music bot features and commands")
    async def musichelp(self,i):
        e=discord.Embed(title="🐺 WHITE WOLF GLOBAL • MUSIC HELP",description="Use `/play`, `/search` or the buttons below to control music.",color=0x7C3AED)
        e.add_field(name="🎵 Playback",value="`/play` `/search` `/pause` `/resume` `/skip` `/stop` `/leave` `/replay` `/queue` `/nowplaying`",inline=False)
        e.add_field(name="🎚️ Audio",value="`/effect` `/speed` `/volume` • Bass Boost, Nightcore, 8D, Vaporwave, Karaoke & Clear Voice",inline=False)
        e.add_field(name="🔁 Automation",value="`/loop` `/autoplay` `/shuffle` `/remove` `/clear` `/247`",inline=False)
        e.add_field(name="❤️ Library",value="`/favorite` `/favorites` `/history` `/lyrics`",inline=False)
        e.add_field(name="⚙️ Utility",value="`/about` `/musichelp` `/ping`",inline=False)
        e.set_footer(text="🐺 White Wolf Global • ⚡ Made with Joy")
        await i.response.send_message(embed=e,ephemeral=True)

    @app_commands.command(name="musicstats",description="Show White Wolf Global music statistics")
    async def musicstats(self,i):
        p=get_player(i.guild.id); rows=db.history(i.guild.id)
        total=len(rows); queued=len(p.queue); current=p.current.title[:70] if p.current else "Nothing playing"
        e=discord.Embed(title="📊 WHITE WOLF GLOBAL • MUSIC STATS",color=0x7C3AED)
        e.add_field(name="🎵 Recent Plays",value=str(total),inline=True)
        e.add_field(name="📜 Queue",value=str(queued),inline=True)
        e.add_field(name="▶️ Current",value=current,inline=False)
        e.add_field(name="🎚️ Effect",value=p.effect,inline=True)
        e.add_field(name="⚡ Speed",value=f"{p.speed:.2f}x",inline=True)
        e.set_footer(text="⚡ Made with Joy")
        await i.response.send_message(embed=e)

    @app_commands.command(name="ping",description="Diagnostics")
    async def ping(self,i):
        p=get_player(i.guild.id);voice="Connected" if p.voice and p.voice.is_connected() else "Not connected";await i.response.send_message(f"🏓 **{round(self.bot.latency*1000)}ms** • 🎧 {voice}\nFFmpeg: `{FFMPEG}`\nyt-dlp: `{yt_dlp.version.__version__}`",ephemeral=True)

    def ffmpeg_audio_filter(self,p):
        filters=[]
        if p.effect=="bassboost":
            filters.append("bass=g=10:f=110")
        elif p.effect=="bassboost_plus":
            filters += ["bass=g=16:f=90","equalizer=f=140:t=q:w=1:g=4"]
        elif p.effect=="nightcore":
            filters += ["asetrate=44100*1.25","aresample=44100","atempo=1.25"]
        elif p.effect=="vaporwave":
            filters += ["asetrate=44100*0.80","aresample=44100","atempo=0.80"]
        elif p.effect=="8d":
            filters.append("apulsator=hz=0.08")
        elif p.effect=="karaoke":
            filters.append("pan=stereo|c0=FL-FR|c1=FR-FL")
        elif p.effect=="clearvoice":
            filters += ["highpass=f=120","lowpass=f=11000","equalizer=f=3000:t=q:w=1:g=3"]
        if abs(p.speed-1.0)>0.001 and p.effect not in {"nightcore","vaporwave"}:
            remaining=p.speed
            while remaining>2.0: filters.append("atempo=2.0");remaining/=2.0
            while remaining<0.5: filters.append("atempo=0.5");remaining/=0.5
            filters.append(f"atempo={remaining:.4f}")
        return ",".join(filters)

    async def play_next(self,gid):
        p=get_player(gid)
        async with p.lock:
            if not p.voice or not p.voice.is_connected():return
            if not p.queue:
                if p.autoplay and p.current:
                    try:
                        info=await asyncio.to_thread(extract_info_sync,"ytsearch5:"+p.current.title)
                        choices=[x for x in (info.get("entries") or []) if x.get("webpage_url") and x.get("webpage_url")!=p.current.webpage_url]
                        if choices:
                            x=choices[0];p.queue.append(Track(x.get("title","Unknown"),x["webpage_url"],x.get("duration") or 0,"XENON Autoplay",x.get("thumbnail"),"YouTube",x.get("url")))
                    except Exception as e:log.warning("Autoplay failed: %s",e)
                if not p.queue:p.current=None;return
            t=p.queue.pop(0);p.current=t;p.skip_requested=False;generation=p.generation
            try:
                await refresh_stream(t)
                audio_filter=self.ffmpeg_audio_filter(p)
                ffmpeg_opts={"before_options":"-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -nostdin","options":"-vn -loglevel warning"}
                if audio_filter: ffmpeg_opts["options"] += f" -af \"{audio_filter}\""
                audio=discord.FFmpegPCMAudio(t.stream_url,executable=FFMPEG,**ffmpeg_opts);src=discord.PCMVolumeTransformer(audio,volume=p.volume)
                def after(err):
                    fut=asyncio.run_coroutine_threadsafe(self.after_track(gid,t,err,generation),self.bot.loop)
                    try:fut.result(timeout=0)
                    except Exception:pass
                p.voice.play(src,after=after);db.history_add(gid,0,t)
                if p.panel_message:
                    try:await p.panel_message.edit(embed=player_embed(p),view=PlayerView(gid))
                    except Exception:pass
            except Exception as e:
                log.exception("Audio start failed")
                p.current=None
                if p.queue:await self.play_next(gid)
    async def after_track(self,gid,finished,err,generation):
        p=get_player(gid)
        if generation!=p.generation:return
        if err:log.warning("Playback error guild %s: %s",gid,err)
        if p.loop=="track" and not p.skip_requested:p.queue.insert(0,finished)
        elif p.loop=="queue" and not p.skip_requested:p.queue.append(finished)
        p.current=None;p.paused=False;p.skip_requested=False;await self.play_next(gid)

class Bot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!",intents=discord.Intents(guilds=True,voice_states=True))
    async def setup_hook(self):
        await self.add_cog(Music(self));self.tree.copy_global_to(guild=None) if False else None;await self.tree.sync();log.info("Slash commands synced")
    async def on_ready(self):log.info("Logged in as %s (%s) | FFmpeg=%s",self.user,self.user.id,FFMPEG)

bot=Bot()
@bot.tree.error
async def on_app_error(i,error):
    log.error("Command error",exc_info=error)
    try:
        if i.response.is_done():await i.followup.send("❌ Something went wrong. Check terminal.",ephemeral=True)
        else:await i.response.send_message("❌ Something went wrong. Check terminal.",ephemeral=True)
    except Exception:pass

if __name__=="__main__":
    if not TOKEN:raise SystemExit("DISCORD_TOKEN is missing in .env")
    log.info("Using FFmpeg executable: %s",FFMPEG);bot.run(TOKEN)
