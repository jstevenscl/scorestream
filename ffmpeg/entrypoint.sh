#!/bin/sh
set -e

PIPE_PATH="/pipes/scoreboard.rawvideo"
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

# Start the renderer
exec node /app/render.js
