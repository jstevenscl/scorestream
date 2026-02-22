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

WORKDIR /app

COPY package.json .
RUN npm install --omit=dev

COPY manager.js .
COPY render.js .

ENV PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true \
    PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium \
    PIPES_DIR=/tmp/pipes \
    HLS_DIR=/hls \
    MANAGER_PORT=3001 \
    DB_PATH=/config/scorestream.db

CMD ["node", "manager.js"]
