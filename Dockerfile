# White Wolf Global Music Bot — Docker image
# ffmpeg + libopus0 for Discord voice; Node.js + bgutil PO-token provider so
# YouTube doesn't block Render's datacenter IP.

FROM python:3.11-slim

# System deps:
#   ffmpeg / libopus0  -> Discord voice playback
#   git / curl / gnupg -> install Node.js and clone the PO token provider
#   build-essential    -> fallback if a native module must be compiled
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg libopus0 curl ca-certificates gnupg git build-essential \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Build the bgutil PO Token provider (generates YouTube Proof-of-Origin tokens,
# required for datacenter IPs to fetch audio formats). Output: server/build/
RUN git clone --depth 1 --branch 1.3.2 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git /opt/bgutil-pot \
    && cd /opt/bgutil-pot/server \
    && npm ci --no-audit --no-fund \
    && npx tsc

WORKDIR /app

# Install Python deps (includes yt-dlp[default] + the bgutil plugin)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the bot
COPY . .

# start.sh launches the PO token provider in the background, then the bot
CMD ["bash", "start.sh"]
