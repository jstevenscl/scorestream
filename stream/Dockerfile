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
# All tracks are royalty-free. Full attribution details in /audio/ATTRIBUTION.txt
# Files committed to repo under stream/audio/ — no network required at build time.
COPY audio/ /audio/

RUN printf '/audio/bensound-sunsetreverie.mp3\n/audio/Throng by Swoop  Royalty-Free Music on Uppbeat.mp3\n/audio/Damtaro - Vision (freetouse.com).mp3\n/audio/VTEMO - Drifting (freetouse.com).mp3\n/audio/tubebackr & Tetuano - Been Waiting (freetouse.com).mp3\n/audio/Damtaro - Forward (freetouse.com).mp3\n' \
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
