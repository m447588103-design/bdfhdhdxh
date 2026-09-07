"""Tiny sqlite wrapper for guild settings, favorites and history."""
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List

_LOCK = threading.Lock()


class Database:
    def __init__(self, path: str):
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self) -> None:
        with _LOCK:
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS guilds (
                    guild_id   INTEGER PRIMARY KEY,
                    volume     REAL    DEFAULT 0.7,
                    loop_mode  TEXT    DEFAULT 'off',
                    autoplay   INTEGER DEFAULT 0,
                    effect     TEXT    DEFAULT 'none',
                    speed      REAL    DEFAULT 1.0,
                    dj_role    INTEGER
                );
                CREATE TABLE IF NOT EXISTS favorites (
                    id       INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id  INTEGER NOT NULL,
                    title    TEXT    NOT NULL,
                    url      TEXT    NOT NULL,
                    added_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS history (
                    id       INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id  INTEGER NOT NULL,
                    title    TEXT    NOT NULL,
                    url      TEXT    NOT NULL,
                    played_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS stats (
                    key   TEXT PRIMARY KEY,
                    value INTEGER NOT NULL DEFAULT 0
                );
                """
            )
            self.conn.commit()

    # ---------------- guild settings ----------------
    def guild(self, guild_id: int) -> Dict[str, Any]:
        with _LOCK:
            row = self.conn.execute(
                "SELECT * FROM guilds WHERE guild_id=?", (guild_id,)
            ).fetchone()
            if row is None:
                self.conn.execute("INSERT INTO guilds(guild_id) VALUES(?)", (guild_id,))
                self.conn.commit()
                row = self.conn.execute(
                    "SELECT * FROM guilds WHERE guild_id=?", (guild_id,)
                ).fetchone()
            return dict(row)

    def set_guild(self, guild_id: int, **fields: Any) -> None:
        if not fields:
            return
        self.guild(guild_id)
        cols = ", ".join(f"{k}=?" for k in fields)
        with _LOCK:
            self.conn.execute(
                f"UPDATE guilds SET {cols} WHERE guild_id=?",
                (*fields.values(), guild_id),
            )
            self.conn.commit()

    # ---------------- favorites ----------------
    def favorite_add(self, user_id: int, title: str, url: str) -> None:
        with _LOCK:
            self.conn.execute(
                "INSERT INTO favorites(user_id,title,url,added_at) VALUES(?,?,?,?)",
                (user_id, title, url, int(time.time())),
            )
            self.conn.commit()

    def favorite_remove(self, user_id: int, url: str) -> int:
        with _LOCK:
            cur = self.conn.execute(
                "DELETE FROM favorites WHERE user_id=? AND url=?", (user_id, url)
            )
            self.conn.commit()
            return cur.rowcount

    def favorites(self, user_id: int, limit: int = 25) -> List[sqlite3.Row]:
        with _LOCK:
            return self.conn.execute(
                "SELECT * FROM favorites WHERE user_id=? ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()

    # ---------------- history ----------------
    def history_add(self, guild_id: int, user_id: int, title: str, url: str) -> None:
        with _LOCK:
            self.conn.execute(
                "INSERT INTO history(guild_id,user_id,title,url,played_at) VALUES(?,?,?,?,?)",
                (guild_id, user_id, title, url, int(time.time())),
            )
            self.conn.execute(
                "INSERT INTO stats(key,value) VALUES('tracks_played',1) "
                "ON CONFLICT(key) DO UPDATE SET value=value+1"
            )
            self.conn.commit()

    def history(self, guild_id: int, limit: int = 15) -> List[sqlite3.Row]:
        with _LOCK:
            return self.conn.execute(
                "SELECT * FROM history WHERE guild_id=? ORDER BY id DESC LIMIT ?",
                (guild_id, limit),
            ).fetchall()

    def stat(self, key: str) -> int:
        with _LOCK:
            row = self.conn.execute("SELECT value FROM stats WHERE key=?", (key,)).fetchone()
            return int(row["value"]) if row else 0
