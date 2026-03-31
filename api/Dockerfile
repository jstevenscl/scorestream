# Build context is repo root (set in beta.yml: context: .)
# so we can COPY from stream/audio/ into the API image

FROM python:3.11-slim

WORKDIR /app

COPY api/requirements.txt .
RUN pip install -r requirements.txt

COPY api/app.py .

# ── Built-in royalty-free audio — baked directly into the API image ───────────
# Copied from stream/audio/ at build time. On startup, app.py copies these
# into /audio_library (the shared volume) and registers them in the DB.
# This is the ONLY place these files need to live — no cross-container copying,
# no runtime downloads, no race conditions.
COPY stream/audio/*.mp3 /builtin_audio/

ENV DB_PATH=/config/scorestream.db \
    AUDIO_DIR=/audio_library

CMD ["python", "app.py"]
