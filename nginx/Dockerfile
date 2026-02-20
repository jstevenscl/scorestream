FROM nginx:1.25-alpine

# Remove default nginx config
RUN rm /etc/nginx/conf.d/default.conf

# Copy our nginx config
COPY nginx.conf /etc/nginx/nginx.conf

# Create HLS output directory
RUN mkdir -p /usr/share/nginx/html/hls

# scoreboard.html is mounted at runtime via docker-compose volume
# so we just need a placeholder
RUN echo "ScoreStream" > /usr/share/nginx/html/index.html

# Health check endpoint — nginx returns 200 on /health via config
EXPOSE 80

HEALTHCHECK --interval=20s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -qO- http://localhost/health || exit 1

CMD ["nginx", "-g", "daemon off;"]
