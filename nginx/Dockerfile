FROM nginx:1.25-alpine

RUN apk add --no-cache curl && \
    rm -f /etc/nginx/conf.d/default.conf && \
    mkdir -p /usr/share/nginx/html/hls

COPY nginx/nginx.conf /etc/nginx/nginx.conf
COPY scoreboard.html /usr/share/nginx/html/index.html

EXPOSE 80

HEALTHCHECK --interval=20s --timeout=5s --start-period=60s --retries=5 \
  CMD curl -f http://localhost/health || exit 1

CMD ["nginx", "-g", "daemon off;"]
