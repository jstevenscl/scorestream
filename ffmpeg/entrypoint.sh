#!/bin/sh
set -e

PIPE_PATH="${PIPE_PATH:-/pipes/scoreboard.rawvideo}"
HLS_DIR="${HLS_DIR:-/hls}"
WIDTH="${STREAM_WIDTH:-1920}"
HEIGHT="${STREAM_HEIGHT:-1080}"
FPS="${STREAM_FPS:-10}"
SEGMENT_DURATION="${HLS_SEGMENT_DURATION:-2}"
PLAYLIST_SIZE="${HLS_PLAYLIST_SIZE:-6}"

echo "[ffmpeg] Waiting for renderer pipe: ${PIPE_PATH}"

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

# Input is JPEG frames via image2pipe.
# KEY SETTINGS FOR SMOOTH STREAMING:
# -use_wallclock_as_timestamps 1  — use real time for PTS, not frame count.
#   This is critical: since renderer paces frames at real-time intervals,
#   wall-clock timestamps prevent the player from buffering ahead or stalling.
# -vf fps=fps=${FPS}  — re-timestamp output to exactly FPS, smoothing
#   any jitter in frame delivery from the renderer.
# -g 2*FPS  — keyframe every 2 seconds = one keyframe per HLS segment,
#   allowing clean segment boundaries and fast channel tune-in.
# -hls_flags delete_segments+append_list+independent_segments
#   independent_segments: each segment is self-contained (important for DVR/seek)
exec ffmpeg \
  -f image2pipe \
  -framerate "${FPS}" \
  -use_wallclock_as_timestamps 1 \
  -i "${PIPE_PATH}" \
  -vf "scale=${WIDTH}:${HEIGHT}:force_original_aspect_ratio=decrease,pad=${WIDTH}:${HEIGHT}:(ow-iw)/2:(oh-ih)/2,fps=fps=${FPS}" \
  -c:v libx264 \
  -preset veryfast \
  -tune zerolatency \
  -b:v 2000k \
  -maxrate 2500k \
  -bufsize 4000k \
  -pix_fmt yuv420p \
  -g $((FPS * 2)) \
  -sc_threshold 0 \
  -f hls \
  -hls_time "${SEGMENT_DURATION}" \
  -hls_list_size "${PLAYLIST_SIZE}" \
  -hls_flags delete_segments+append_list+independent_segments \
  -hls_segment_filename "${HLS_DIR}/scorestream_%05d.ts" \
  "${HLS_DIR}/scorestream.m3u8"
