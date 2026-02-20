#!/bin/sh
set -e

PIPE_PATH="${PIPE_PATH:-/pipes/scoreboard.rawvideo}"
WIDTH="${STREAM_WIDTH:-1920}"
HEIGHT="${STREAM_HEIGHT:-1080}"
FPS="${STREAM_FPS:-30}"
SCOREBOARD_URL="${SCOREBOARD_URL:-http://scorestream-web/}"

echo "[renderer] Starting — ${WIDTH}x${HEIGHT}@${FPS}fps"
echo "[renderer] Scoreboard URL: ${SCOREBOARD_URL}"
echo "[renderer] Output pipe: ${PIPE_PATH}"

# Create named pipe if it doesn't exist
if [ ! -p "$PIPE_PATH" ]; then
  mkfifo "$PIPE_PATH"
  echo "[renderer] Created pipe: ${PIPE_PATH}"
fi

# Use full path to node to avoid PATH issues in slim images
NODE_BIN=$(which node 2>/dev/null || echo "/usr/local/bin/node")
echo "[renderer] Node: ${NODE_BIN}"
exec "$NODE_BIN" /app/render.js
