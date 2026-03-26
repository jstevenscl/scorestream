FROM node:20-slim

# Install Chromium dependencies + ffmpeg + sqlite3 build tools
RUN apt-get update && apt-get install -y \
    chromium \
    fonts-liberation \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcups2 \
    libdbus-1-3 \
    libgdk-pixbuf2.0-0 \
    libnspr4 \
    libnss3 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    xdg-utils \
    ffmpeg \
    python3 \
    make \
    g++ \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# ── Built-in sports music ─────────────────────────────────────────────────────
# Files committed to repo under stream/audio/ — no network required at build time.
# Renamed to clean filenames to avoid ffmpeg concat issues with spaces/special chars.
COPY audio/ /audio-src/

RUN mkdir -p /audio && \
    cp "/audio-src/bensound-sunsetreverie.mp3"                          "/audio/01-sunset-reverie.mp3" && \
    cp "/audio-src/Throng by Swoop  Royalty-Free Music on Uppbeat.mp3" "/audio/02-throng.mp3" && \
    cp "/audio-src/Damtaro - Vision (freetouse.com).mp3"               "/audio/03-vision.mp3" && \
    cp "/audio-src/VTEMO - Drifting (freetouse.com).mp3"               "/audio/04-drifting.mp3" && \
    cp "/audio-src/tubebackr & Tetuano - Been Waiting (freetouse.com).mp3" "/audio/05-been-waiting.mp3" && \
    cp "/audio-src/Damtaro - Forward (freetouse.com).mp3"              "/audio/06-forward.mp3" && \
    printf '/audio/01-sunset-reverie.mp3\n/audio/02-throng.mp3\n/audio/03-vision.mp3\n/audio/04-drifting.mp3\n/audio/05-been-waiting.mp3\n/audio/06-forward.mp3\n' \
      > /audio/playlist.m3u && \
    echo "=== Audio files ===" && ls -lh /audio/

WORKDIR /app

COPY package.json .
RUN npm install --omit=dev

COPY manager.js .

ENV PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true \
    PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium \
    PIPES_DIR=/tmp/pipes \
    HLS_DIR=/hls \
    MANAGER_PORT=3001 \
    DB_PATH=/config/scorestream.db

CMD ["node", "manager.js"]
