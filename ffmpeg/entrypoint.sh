#!/bin/bash
# ScoreStream FFmpeg Encoder
#
# Reads PNG frames from renderer named pipe,
# encodes to H.264, and outputs HLS segments
# for each channel variant (all + per sport).
#
# One FFmpeg process handles everything via stream copy
# to multiple outputs.

set -e

WIDTH="${STREAM_WIDTH:-1920}"
HEIGHT="${STREAM_HEIGHT:-1080}"
FPS="${STREAM_FPS:-30}"
SEG_DUR="${HLS_SEGMENT_DURATION:-2}"
LIST_SIZE="${HLS_PLAYLIST_SIZE:-10}"
PIPE="/pipes/video_raw"

echo "[ffmpeg] Waiting for renderer pipe at ${PIPE}..."
until [ -p "$PIPE" ]; do
  sleep 1
done
echo "[ffmpeg] Pipe found. Starting encoder."

# Common HLS output options
HLS_OPTS="-c:v libx264 \
  -preset veryfast \
  -tune zerolatency \
  -crf 23 \
  -g $((FPS * 2)) \
  -keyint_min ${FPS} \
  -sc_threshold 0 \
  -pix_fmt yuv420p \
  -movflags +faststart"

HLS_FLAGS="-hls_time ${SEG_DUR} \
  -hls_list_size ${LIST_SIZE} \
  -hls_flags delete_segments+append_list \
  -hls_segment_type mpegts"

echo "[ffmpeg] Encoding ${WIDTH}x${HEIGHT} @ ${FPS}fps"
echo "[ffmpeg] Segment duration: ${SEG_DUR}s | Playlist size: ${LIST_SIZE}"

# ----------------------------------------------------------------
# FFmpeg reads PNG frames from the named pipe.
#
# We output to a single "all" stream and 6 sport streams.
#
# NOTE: Individual sport channels use the SAME video as "all"
# because the sport filtering happens in the scoreboard HTML
# via URL parameters. Each channel points Dispatcharr to a
# different stream URL, but the backend API service handles
# serving each sport-filtered page to the renderer per-stream.
#
# For a single-renderer setup (this container), all HLS outputs
# receive the same full-scoreboard video. The per-sport filtering
# is achieved by running separate renderer instances per sport
# (see advanced multi-renderer setup in README), OR by using
# the Dispatcharr EPG + channel name to differentiate, with
# the full board as the stream source.
#
# To enable true per-sport streams, set MULTI_RENDERER=true
# in your .env and uncomment the multi-renderer docker-compose
# service definitions in docker-compose.override.yml
# ----------------------------------------------------------------

exec ffmpeg \
  -f image2pipe \
  -vcodec png \
  -framerate "${FPS}" \
  -i "${PIPE}" \
  \
  -filter_complex "split=7[v0][v1][v2][v3][v4][v5][v6]" \
  \
  ${HLS_OPTS} \
  -map "[v0]" \
  ${HLS_FLAGS} \
  -hls_segment_filename "/hls/all/seg%05d.ts" \
  "/hls/all/stream.m3u8" \
  \
  -c:v libx264 -preset veryfast -tune zerolatency -crf 23 \
  -g $((FPS * 2)) -keyint_min ${FPS} -sc_threshold 0 -pix_fmt yuv420p \
  -map "[v1]" \
  ${HLS_FLAGS} \
  -hls_segment_filename "/hls/nfl/seg%05d.ts" \
  "/hls/nfl/stream.m3u8" \
  \
  -c:v libx264 -preset veryfast -tune zerolatency -crf 23 \
  -g $((FPS * 2)) -keyint_min ${FPS} -sc_threshold 0 -pix_fmt yuv420p \
  -map "[v2]" \
  ${HLS_FLAGS} \
  -hls_segment_filename "/hls/nba/seg%05d.ts" \
  "/hls/nba/stream.m3u8" \
  \
  -c:v libx264 -preset veryfast -tune zerolatency -crf 23 \
  -g $((FPS * 2)) -keyint_min ${FPS} -sc_threshold 0 -pix_fmt yuv420p \
  -map "[v3]" \
  ${HLS_FLAGS} \
  -hls_segment_filename "/hls/mlb/seg%05d.ts" \
  "/hls/mlb/stream.m3u8" \
  \
  -c:v libx264 -preset veryfast -tune zerolatency -crf 23 \
  -g $((FPS * 2)) -keyint_min ${FPS} -sc_threshold 0 -pix_fmt yuv420p \
  -map "[v4]" \
  ${HLS_FLAGS} \
  -hls_segment_filename "/hls/nhl/seg%05d.ts" \
  "/hls/nhl/stream.m3u8" \
  \
  -c:v libx264 -preset veryfast -tune zerolatency -crf 23 \
  -g $((FPS * 2)) -keyint_min ${FPS} -sc_threshold 0 -pix_fmt yuv420p \
  -map "[v5]" \
  ${HLS_FLAGS} \
  -hls_segment_filename "/hls/ncaab/seg%05d.ts" \
  "/hls/ncaab/stream.m3u8" \
  \
  -c:v libx264 -preset veryfast -tune zerolatency -crf 23 \
  -g $((FPS * 2)) -keyint_min ${FPS} -sc_threshold 0 -pix_fmt yuv420p \
  -map "[v6]" \
  ${HLS_FLAGS} \
  -hls_segment_filename "/hls/ncaabase/seg%05d.ts" \
  "/hls/ncaabase/stream.m3u8" \
  2>&1 | while IFS= read -r line; do
    echo "[ffmpeg] $line"
  done
