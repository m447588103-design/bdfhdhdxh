# 🎧 Aurora Music — Full Discord Music Bot (Free-Hosting Ready)

Ekta complete, production-ready Discord music bot. YouTube + Spotify support,
audio filters, queue system, buttons, lyrics, favorites — ar **jekono free
hosting** e cholbe (Render / Koyeb / Railway / Fly.io / Replit / VPS).

---

## ✨ Features

| Category | Commands |
|---|---|
| Playback | `/play` `/playnext` `/search` `/pause` `/resume` `/skip` `/stop` `/replay` `/join` `/leave` |
| Queue | `/queue` `/nowplaying` `/shuffle` `/remove` `/move` `/clear` `/loop` `/autoplay` |
| Audio | `/volume` `/effect` `/speed` `/seekinfo` |
| Extra | `/favorite` `/favorites` `/playfavorites` `/history` `/lyrics` `/stats` `/ping` `/invite` `/help` |

- **Sources:** YouTube video/playlist/search, direct audio URL, Spotify track/album/playlist (metadata → YouTube)
- **Effects:** `bassboost`, `bassboost+`, `nightcore`, `vaporwave`, `8d`, `karaoke`, `clearvoice`, `treble`, `lofi`
- **Player panel:** ⏯️ ⏭️ 🔁 🔀 ⏹️ buttons + live progress bar
- **Loop modes:** off / track / queue, plus **autoplay** (related track auto-queue)
- **Persistence:** SQLite — per-guild volume/loop/effect, favorites, history, stats
- **Auto-leave:** khali channel e 30s, idle e `IDLE_TIMEOUT` (default 180s)
- **Free-hosting friendly:** built-in `/healthz` HTTP server + self-ping keepalive

---

## 📁 Structure

```
main.py                 # entrypoint  -> python main.py
musicbot/
  bot.py                # bot class, slash sync, error handler, presence loop
  config.py             # all env vars
  db.py                 # sqlite (settings, favorites, history, stats)
  sources.py            # yt-dlp + Spotify resolution
  player.py             # per-guild queue/playback engine + ffmpeg filters
  ui.py                 # embeds + buttons + search select
  webserver.py          # /healthz + keepalive
  cogs/music.py         # playback & queue commands
  cogs/extras.py        # favorites, history, lyrics, help, stats
Dockerfile render.yaml koyeb.yaml railway.json fly.toml Procfile app.json
legacy/old_bot.py       # purono single-file bot (reference only)
```

---

## 🚀 Setup (2 minutes)

1. https://discord.com/developers/applications → **New Application** → **Bot** → **Reset Token** → copy.
2. Bot page e **Server Members** lagbe na, kintu bot ke invite korar somoy
   `bot` + `applications.commands` scope din, permissions: *Connect, Speak,
   Send Messages, Embed Links, Use Slash Commands*.
3. `.env.example` copy kore `.env` banan, `DISCORD_TOKEN` boshan.

### Local run
```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env           # token boshan
python main.py
```
> `ffmpeg` install na thakleo cholbe — `imageio-ffmpeg` bundled binary use kore.
> Faster slash command sync er jonno `.env` te `GUILD_ID=your_server_id` din.

---

## ☁️ Free Hosting

### 1) Koyeb (recommended — always-on free instance)
1. Repo GitHub e push korun.
2. Koyeb → **Create Service** → GitHub → repo select → Builder: **Dockerfile**.
3. Environment variables: `DISCORD_TOKEN`, (optional) `SPOTIFY_CLIENT_ID/SECRET`.
4. Port `8080`, health check path `/healthz`. Deploy.

### 2) Render (free web service)
1. Render → **New → Blueprint** → repo select (`render.yaml` auto-detect hobe).
2. Dashboard e `DISCORD_TOKEN` add korun.
3. Free instance ghumiye jete pare — deploy hoye gele URL ta `SELF_URL`
   env var e boshan (`https://your-app.onrender.com`), keepalive nijei ping korbe.

### 3) Railway
`railway.json` already ache — repo connect korlei Dockerfile diye build hobe.
Variables e `DISCORD_TOKEN` din.

### 4) Fly.io
```bash
fly launch --no-deploy
fly secrets set DISCORD_TOKEN=xxxxx
fly deploy
```
`fly.toml` e 512MB VM + persistent volume `/data` already configured.

### 5) Replit / Glitch / any VPS
```bash
pip install -r requirements.txt && python main.py
```
Replit e webserver `/healthz` expose kore — UptimeRobot diye 5 min por por
ping korle 24/7 online thakbe.

---

## ⚙️ Environment Variables

| Var | Default | Ki kore |
|---|---|---|
| `DISCORD_TOKEN` | — | **Required** bot token |
| `GUILD_ID` | – | Test server e instant command sync |
| `BOT_NAME` / `EMBED_COLOR` / `FOOTER_TEXT` | Aurora Music | Branding |
| `DEFAULT_VOLUME` | 0.70 | Notun guild er volume |
| `MAX_QUEUE` | 200 | Queue limit |
| `IDLE_TIMEOUT` | 180 | Idle hole auto-leave (sec) |
| `DATABASE_PATH` | data/music.db | SQLite file |
| `FFMPEG_PATH` | auto | ffmpeg binary path |
| `YTDLP_COOKIES` | – | `cookies.txt` path (YouTube bot-check hole) |
| `SPOTIFY_CLIENT_ID/SECRET` | – | Spotify album/playlist er jonno |
| `PORT` | 8080 | Health server port |
| `SELF_URL` | – | Keepalive self-ping URL |

---

## 🛠️ Troubleshooting

- **Slash command dekha jacche na** → global sync e 1 ghonta lage; `GUILD_ID` set korun.
- **"Sign in to confirm you're not a bot"** → browser theke `cookies.txt` export kore repo/volume e rakhun ar `YTDLP_COOKIES=/app/cookies.txt` din.
- **Awaj ashe na** → host e `ffmpeg` + `libopus` ache kina dekhun (Dockerfile e already ache), ar bot er **Speak** permission check korun.
- **Spotify link kaj kore na** → `SPOTIFY_CLIENT_ID`/`SECRET` (developer.spotify.com) set korun.

---

⚡ Made with joy — commit e kokhono `.env` push korben na.
