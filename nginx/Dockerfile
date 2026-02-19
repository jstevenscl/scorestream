FROM nginx:alpine

# Copy nginx config
COPY nginx.conf /etc/nginx/nginx.conf

# Bake scoreboard.html into the image
# (also volume-mountable at runtime for live updates)
COPY ../scoreboard.html /usr/share/nginx/html/index.html

# HLS segments directory (populated at runtime by ffmpeg container)
RUN mkdir -p /usr/share/nginx/html/hls

EXPOSE 80

HEALTHCHECK --interval=15s --timeout=5s --retries=3 \
  CMD wget -qO- http://localhost/health || exit 1
