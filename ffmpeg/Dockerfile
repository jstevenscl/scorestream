FROM alpine:3.19

RUN apk add --no-cache ffmpeg bash

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Create HLS output directories for each channel
RUN mkdir -p /hls/all /hls/nfl /hls/nba /hls/mlb /hls/nhl /hls/ncaab /hls/ncaabase

ENTRYPOINT ["/entrypoint.sh"]
