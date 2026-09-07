"""Central configuration loaded from environment variables (.env supported)."""
import os

from dotenv import load_dotenv

load_dotenv()


def _int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, "") or default)
    except ValueError:
        return default


def _float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, "") or default)
    except ValueError:
        return default


TOKEN = os.getenv("DISCORD_TOKEN", "").strip()

# Optional: sync slash commands instantly to one guild while testing.
GUILD_ID = _int("GUILD_ID", 0)

BOT_NAME = os.getenv("BOT_NAME", "Aurora Music")
EMBED_COLOR = int(os.getenv("EMBED_COLOR", "0x5865F2"), 16)
FOOTER = os.getenv("FOOTER_TEXT", "⚡ Aurora Music • free-hosting ready")

DEFAULT_VOLUME = max(0.0, min(2.0, _float("DEFAULT_VOLUME", 0.7)))
MAX_QUEUE = _int("MAX_QUEUE", 200)
IDLE_TIMEOUT = _int("IDLE_TIMEOUT", 180)  # seconds alone/idle before auto-leave

DATABASE_PATH = os.getenv("DATABASE_PATH", "data/music.db")

FFMPEG_PATH = os.getenv("FFMPEG_PATH", "").strip()
YTDLP_COOKIES = os.getenv("YTDLP_COOKIES", "").strip()
YOUTUBE_PLAYER_CLIENT = os.getenv("YOUTUBE_PLAYER_CLIENT", "android,web_embedded,default")

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "").strip()

# Health/keepalive HTTP server (needed by Render/Koyeb/Railway free web services)
PORT = _int("PORT", 8080)
SELF_URL = os.getenv("SELF_URL", "").strip()  # e.g. https://myapp.onrender.com
KEEPALIVE_INTERVAL = _int("KEEPALIVE_INTERVAL", 600)
