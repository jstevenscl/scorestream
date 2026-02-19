#!/bin/bash
# ScoreStream Renderer Entrypoint
# Waits for web container, then starts the renderer

set -e

echo "[entrypoint] Waiting for scoreboard web service..."
until wget -qO- http://scorestream-web/health > /dev/null 2>&1; do
  echo "[entrypoint] Web not ready, retrying in 3s..."
  sleep 3
done
echo "[entrypoint] Web service ready. Starting renderer."

exec node /app/render.js
