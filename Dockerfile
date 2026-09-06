# White Wolf Global Music Bot — Docker image
# Used by Render (and works on any Docker host too).
# Native Render builds can't run `apt-get` (read-only system dirs),
# so we install ffmpeg + libopus here as root inside the image.

FROM python:3.11-slim

# ffmpeg  -> audio decode + FFmpeg effects (bass boost, nightcore, etc.)
# libopus0 -> required by discord.py to ENCODE voice to Opus (FFmpegPCMAudio)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libopus0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the bot
COPY . .

# Render injects PORT; bot.py starts the /healthz server when PORT is set
CMD ["python", "bot.py"]
