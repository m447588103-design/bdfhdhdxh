"""Track resolution: YouTube (yt-dlp), Spotify metadata -> YouTube, direct URLs."""
from __future__ import annotations

import asyncio
import base64
import re
import time
from dataclasses import dataclass, field
from typing import List, Optional

import aiohttp
import yt_dlp

from . import config

YTDL_FORMAT = "bestaudio[ext=m4a]/bestaudio/best"

_BASE_OPTS = {
    "format": YTDL_FORMAT,
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    "nocheckcertificate": True,
    "ignoreerrors": False,
    "skip_download": True,
    "geo_bypass": True,
    "source_address": "0.0.0.0",
    "socket_timeout": 20,
    "retries": 3,
    "extractor_args": {
        "youtube": {"player_client": config.YOUTUBE_PLAYER_CLIENT.split(",")}
    },
    "http_headers": {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        )
    },
}

if config.YTDLP_COOKIES:
    _BASE_OPTS["cookiefile"] = config.YTDLP_COOKIES

URL_RE = re.compile(r"^https?://", re.I)
SPOTIFY_RE = re.compile(
    r"open\.spotify\.com/(?:intl-[^/]+/)?(track|album|playlist)/([A-Za-z0-9]+)", re.I
)


@dataclass
class Track:
    title: str
    url: str                 # page url (youtube watch link)
    stream_url: str          # direct audio url
    duration: int = 0
    thumbnail: str = ""
    uploader: str = ""
    requester_id: int = 0
    requester_name: str = ""
    expires_at: float = field(default_factory=lambda: time.time() + 1800)

    @property
    def pretty_duration(self) -> str:
        return format_duration(self.duration)


def format_duration(seconds: int) -> str:
    seconds = int(seconds or 0)
    if seconds <= 0:
        return "LIVE"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _extract(query: str, *, search: bool = False, count: int = 1) -> dict:
    opts = dict(_BASE_OPTS)
    if search:
        query = f"ytsearch{count}:{query}"
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(query, download=False)


def _entry_to_track(info: dict, requester_id: int, requester_name: str) -> Track:
    return Track(
        title=info.get("title") or "Unknown title",
        url=info.get("webpage_url") or info.get("original_url") or info.get("url", ""),
        stream_url=info.get("url", ""),
        duration=int(info.get("duration") or 0),
        thumbnail=(info.get("thumbnail") or ""),
        uploader=info.get("uploader") or info.get("channel") or "",
        requester_id=requester_id,
        requester_name=requester_name,
    )


async def _run(fn, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))


# --------------------------- Spotify ---------------------------
_spotify_token: dict = {"value": "", "exp": 0.0}


async def _spotify_token() -> Optional[str]:
    if not (config.SPOTIFY_CLIENT_ID and config.SPOTIFY_CLIENT_SECRET):
        return None
    if _spotify_token["value"] and _spotify_token["exp"] > time.time() + 30:
        return _spotify_token["value"]
    auth = base64.b64encode(
        f"{config.SPOTIFY_CLIENT_ID}:{config.SPOTIFY_CLIENT_SECRET}".encode()
    ).decode()
    async with aiohttp.ClientSession() as s:
        async with s.post(
            "https://accounts.spotify.com/api/token",
            data={"grant_type": "client_credentials"},
            headers={"Authorization": f"Basic {auth}"},
        ) as r:
            if r.status != 200:
                return None
            data = await r.json()
    _spotify_token["value"] = data["access_token"]
    _spotify_token["exp"] = time.time() + int(data.get("expires_in", 3600))
    return _spotify_token["value"]


async def _spotify_api(path: str) -> Optional[dict]:
    token = await _spotify_token()
    if not token:
        return None
    async with aiohttp.ClientSession() as s:
        async with s.get(
            f"https://api.spotify.com/v1/{path}",
            headers={"Authorization": f"Bearer {token}"},
        ) as r:
            if r.status != 200:
                return None
            return await r.json()


async def _spotify_oembed_title(url: str) -> Optional[str]:
    """Fallback when no Spotify credentials are configured."""
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                "https://open.spotify.com/oembed", params={"url": url}
            ) as r:
                if r.status != 200:
                    return None
                data = await r.json()
                return data.get("title")
    except Exception:
        return None


async def _spotify_queries(url: str) -> List[str]:
    """Return a list of 'artist - track' search strings for a Spotify link."""
    m = SPOTIFY_RE.search(url)
    if not m:
        return []
    kind, sid = m.group(1).lower(), m.group(2)

    if kind == "track":
        data = await _spotify_api(f"tracks/{sid}")
        if data:
            artists = ", ".join(a["name"] for a in data.get("artists", []))
            return [f"{artists} - {data.get('name','')}".strip(" -")]
        title = await _spotify_oembed_title(url)
        return [title] if title else []

    queries: List[str] = []
    if kind == "album":
        data = await _spotify_api(f"albums/{sid}/tracks?limit=50")
        items = (data or {}).get("items", [])
        for it in items:
            artists = ", ".join(a["name"] for a in it.get("artists", []))
            queries.append(f"{artists} - {it.get('name','')}".strip(" -"))
    else:  # playlist
        data = await _spotify_api(f"playlists/{sid}/tracks?limit=100")
        for it in (data or {}).get("items", []):
            tr = it.get("track") or {}
            if not tr.get("name"):
                continue
            artists = ", ".join(a["name"] for a in tr.get("artists", []))
            queries.append(f"{artists} - {tr['name']}".strip(" -"))
    return [q for q in queries if q]


# --------------------------- public API ---------------------------
async def search(query: str, count: int = 5) -> List[dict]:
    info = await _run(_extract, query, search=True, count=count)
    return [e for e in (info or {}).get("entries", []) if e]


async def resolve(query: str, requester_id: int, requester_name: str) -> List[Track]:
    """Resolve any user input into one or more playable tracks."""
    query = query.strip()

    if SPOTIFY_RE.search(query):
        queries = await _spotify_queries(query)
        if not queries:
            raise RuntimeError(
                "Spotify link resolve kora gelo na. SPOTIFY_CLIENT_ID / SECRET set korun."
            )
        tracks: List[Track] = []
        # First track resolved eagerly so playback can start fast.
        for q in queries[: config.MAX_QUEUE]:
            try:
                entries = await search(q, 1)
                if entries:
                    tracks.append(_entry_to_track(entries[0], requester_id, requester_name))
            except Exception:
                continue
            if len(tracks) >= config.MAX_QUEUE:
                break
        if not tracks:
            raise RuntimeError("Spotify theke kono playable track pawa jayni.")
        return tracks

    if URL_RE.match(query):
        opts_playlist = "list=" in query
        info = await _run(
            lambda: yt_dlp.YoutubeDL({**_BASE_OPTS, "noplaylist": not opts_playlist})
            .extract_info(query, download=False)
        )
        if info and info.get("entries"):
            return [
                _entry_to_track(e, requester_id, requester_name)
                for e in info["entries"]
                if e
            ][: config.MAX_QUEUE]
        if not info:
            raise RuntimeError("Ei link theke audio pawa gelo na.")
        return [_entry_to_track(info, requester_id, requester_name)]

    entries = await search(query, 1)
    if not entries:
        raise RuntimeError("Kono result pawa jayni.")
    return [_entry_to_track(entries[0], requester_id, requester_name)]


async def refresh(track: Track) -> Track:
    """Re-fetch an expired direct stream url."""
    info = await _run(_extract, track.url)
    if info:
        track.stream_url = info.get("url", track.stream_url)
        track.expires_at = time.time() + 1800
    return track


async def related(track: Track, requester_id: int, requester_name: str) -> Optional[Track]:
    """Pick a follow-up track for autoplay."""
    seed = track.title
    try:
        entries = await search(f"{seed} mix", 6)
    except Exception:
        return None
    for e in entries:
        url = e.get("webpage_url") or e.get("url")
        if url and url != track.url:
            try:
                got = await resolve(url, requester_id, requester_name)
                if got:
                    return got[0]
            except Exception:
                continue
    return None
