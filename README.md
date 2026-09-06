# 🐺 White Wolf Global Music Bot

Premium Discord music bot for **White Wolf Global**.

## Branding
- White Wolf Global branding on player/help/library embeds
- **⚡ Made with Joy** developer signature
- Animated Now Playing panel using the Discord server icon

## Music
- YouTube playback/search
- Spotify public track, album and playlist resolution to playable sources
- Queue, shuffle, loop, autoplay, volume, favorites and history

## Audio
- Bass Boost / Bass Boost+
- Nightcore / Vaporwave
- 8D / Karaoke / Clear Voice
- Playback speed 0.5x–2.0x
- FFmpeg filters applied to the Discord audio stream

## Lyrics
- `/lyrics` fetches actual lyrics from LRCLIB when available
- Long lyrics are split into Discord embeds

## New White Wolf commands
- `/about`
- `/musichelp`
- `/musicstats`

## Run locally
```cmd
python -m venv .venv
.venv\\Scripts\\activate
pip install -r requirements.txt
copy .env.example .env
python bot.py
```

Never commit `.env` or secrets to GitHub.
