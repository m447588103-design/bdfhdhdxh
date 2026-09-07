"""Tiny aiohttp keepalive/health server.

Free hosts (Render, Koyeb, Railway, Replit, Cyclic) expect an HTTP port to be
bound, and free web services sleep without traffic — so we also self-ping.
"""
from __future__ import annotations

import asyncio
import logging
import time

import aiohttp
from aiohttp import web

from . import config

log = logging.getLogger("webserver")
START = time.time()


def build_app(bot) -> web.Application:
    async def index(_req):
        return web.json_response(
            {
                "name": config.BOT_NAME,
                "status": "online" if bot.is_ready() else "starting",
                "guilds": len(bot.guilds),
                "uptime_seconds": int(time.time() - START),
            }
        )

    async def health(_req):
        return web.Response(text="OK")

    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/healthz", health)
    app.router.add_get("/health", health)
    app.router.add_get("/ping", health)
    return app


async def start_webserver(bot) -> web.AppRunner:
    runner = web.AppRunner(build_app(bot))
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", config.PORT)
    await site.start()
    log.info("Health server listening on 0.0.0.0:%s", config.PORT)
    if config.SELF_URL:
        asyncio.create_task(_keepalive())
    return runner


async def _keepalive() -> None:
    url = config.SELF_URL.rstrip("/") + "/healthz"
    await asyncio.sleep(30)
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as r:
                    log.debug("keepalive %s -> %s", url, r.status)
            except Exception as exc:
                log.debug("keepalive failed: %s", exc)
            await asyncio.sleep(max(60, config.KEEPALIVE_INTERVAL))
