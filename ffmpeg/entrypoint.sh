#!/bin/sh
set -e

PIPE_PATH="${PIPE_PATH:-/pipes/scoreboard.rawvideo}"
HLS_DIR="${HLS_DIR:-/hls}"
WIDTH="${STREAM_WIDTH:-1920}"
HEIGHT="${STREAM_HEIGHT:-1080}"
FPS="${STREAM_FPS:-30}"
SEGMENT_DURATION="${HLS_SEGMENT_DURATION:-2}"
PLAYLIST_SIZE="${HLS_PLAYLIST_SIZE:-10}"

echo "[ffmpeg] Waiting for renderer pipe: ${PIPE_PATH}"

# Wait up to 120s for the renderer to create the pipe
waited=0
until [ -p "$PIPE_PATH" ]; do
  sleep 1
  waited=$((waited + 1))
  if [ $waited -ge 120 ]; then
    echo "[ffmpeg] ERROR: Pipe never appeared after 120s"
    exit 1
  fi
done

echo "[ffmpeg] Pipe ready — starting HLS encoding"
echo "[ffmpeg] Output directory: ${HLS_DIR}"
echo "[ffmpeg] Resolution: ${WIDTH}x${HEIGHT} @ ${FPS}fps"

mkdir -p "$HLS_DIR"

# Input is a stream of PNG images via image2pipe
exec ffmpeg \
  -f image2pipe \
  -framerate "${FPS}" \
  -i "${PIPE_PATH}" \
  -vf "scale=${WIDTH}:${HEIGHT}:force_original_aspect_ratio=decrease,pad=${WIDTH}:${HEIGHT}:(ow-iw)/2:(oh-ih)/2" \
  -c:v libx264 \
  -preset veryfast \
  -tune zerolatency \
  -b:v 4000k \
  -maxrate 4500k \
  -bufsize 8000k \
  -pix_fmt yuv420p \
  -g $((FPS * 2)) \
  -sc_threshold 0 \
  -f hls \
  -hls_time "${SEGMENT_DURATION}" \
  -hls_list_size "${PLAYLIST_SIZE}" \
  -hls_flags delete_segments+append_list \
  -hls_segment_filename "${HLS_DIR}/scorestream_%05d.ts" \
  "${HLS_DIR}/scorestream.m3u8"
