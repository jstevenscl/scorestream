# ScoreStream Pro 📡

A self-hosted live sports scoreboard that streams to Dispatcharr as real channels — automatically.

[![Beta Build](https://github.com/OWNER/scorestream-pro/actions/workflows/beta.yml/badge.svg)](https://github.com/OWNER/scorestream-pro/actions)
[![Latest Release](https://img.shields.io/github/v/release/OWNER/scorestream-pro)](https://github.com/OWNER/scorestream-pro/releases)

---

## What You Get

Dispatcharr gets a **ScoreStream** group with channels like:

| Ch | Name | Source |
|----|------|--------|
| 900 | 📺 ScoreStream — All Sports | All leagues |
| 901 | 🏈 ScoreStream — NFL | NFL only |
| 902 | 🏀 ScoreStream — NBA | NBA only |
| 903 | ⚾ ScoreStream — MLB | MLB only |
| 904 | 🏒 ScoreStream — NHL | NHL only |
| 905 | 🏀 ScoreStream — NCAA Basketball | NCAAB only |
| 906 | ⚾ ScoreStream — NCAA Baseball | NCAAB only |

Channel numbers, names, and which profiles they appear in are all configurable.

---

## Quick Start

### 1. Pull the stack files

```bash
mkdir scorestream && cd scorestream
curl -O https://raw.githubusercontent.com/OWNER/scorestream-pro/main/scorestream/docker-compose.yml
curl -O https://raw.githubusercontent.com/OWNER/scorestream-pro/main/scorestream/.env
mkdir config
curl -o config/config.json https://raw.githubusercontent.com/OWNER/scorestream-pro/main/scorestream/config/config.json
```

### 2. Configure your .env

```bash
cp .env .env.bak
nano .env
```

Required values:

```env
GITHUB_OWNER=OWNER                          # GitHub username/org for ghcr.io images
SCORESTREAM_TAG=latest                       # latest | beta | v0.2.0-beta

DISPATCHARR_URL=http://192.168.1.100:9191
DISPATCHARR_USER=admin
DISPATCHARR_PASS=yourpassword

# Must be THIS server's LAN IP — reachable FROM Dispatcharr's container
STREAM_BASE_URL=http://192.168.1.100:8888
```

### 3. Configure channels (optional)

Edit `config/config.json` before first launch. See [Channel Configuration](#channel-configuration) below.

### 4. Deploy

**Via Portainer Stack:**
1. Stacks → Add Stack → paste `docker-compose.yml`
2. Set environment variables
3. Deploy

**Via command line:**
```bash
docker compose --env-file .env up -d
```

### 5. Watch it register

```bash
docker logs -f scorestream-api
```

---

## Choosing Your Image Tag

Set `SCORESTREAM_TAG` in your `.env`:

| Tag | Description | Recommended for |
|-----|-------------|-----------------|
| `latest` | Last stable release | Most users |
| `beta` | Built on every main push | Early adopters |
| `v0.2.0-beta` | Pinned version | Reproducible setups |

```env
SCORESTREAM_TAG=latest
```

---

## Channel Configuration

All channel settings live in `config/config.json`. The API container reloads this file on every 6-hour re-sync, so changes apply without a restart.

### Channel Numbering

**Auto mode** (default) — channels are numbered sequentially from `base_number`:

```json
{
  "channel_numbering": {
    "mode": "auto",
    "base_number": 900
  }
}
```

**Manual mode** — set each channel's number individually:

```json
{
  "channel_numbering": {
    "mode": "manual",
    "channels": {
      "all":      { "number": 500 },
      "nfl":      { "number": 510 },
      "nba":      { "number": 520 },
      "mlb":      { "number": 530 },
      "nhl":      { "number": 540 },
      "ncaab":    { "number": 550 },
      "ncaabase": { "number": 560 }
    }
  }
}
```

**Disable a channel** — set `"enabled": false` to skip it entirely:

```json
"ncaabase": { "number": 560, "enabled": false }
```

**Rename a channel:**

```json
"nfl":  { "number": 510, "name": "📡 NFL Live Scores" }
```

---

## Channel Profile Assignment

Dispatcharr's Channel Profiles control which channels each user or M3U playlist sees.
ScoreStream can automatically assign its channels to the right profiles.

### Finding your Profile IDs

In Dispatcharr, go to **Channels** and open the Channel Profile dropdown. The IDs can be found via the API:

```bash
curl -s http://YOUR_DISPATCHARR:9191/api/channels/channel-profiles/ \
  -H "Authorization: Bearer YOUR_TOKEN" | python3 -m json.tool
```

Or check the `scorestream-api` logs on startup — it prints all available profiles automatically:

```
[api] Dispatcharr has 3 Channel Profile(s):
[api]     id=1  name='Default'
[api]     id=2  name='Kids'
[api]     id=3  name='Sports'
```

### Profile Mode Options

**Add to ALL profiles** (default — backward compatible):

```json
{
  "dispatcharr": {
    "channel_profiles": {
      "mode": "all"
    }
  }
}
```

**Add to NO profiles** (manual assignment in Dispatcharr UI):

```json
{
  "dispatcharr": {
    "channel_profiles": {
      "mode": "none"
    }
  }
}
```

**Add to specific profiles only:**

```json
{
  "dispatcharr": {
    "channel_profiles": {
      "mode": "specific",
      "profile_ids": [1, 3]
    }
  }
}
```

Channels will appear only in "Default" (id=1) and "Sports" (id=3) in the example above.
Invalid IDs are warned and skipped; if all IDs are invalid it falls back to `all`.

---

## Applying Config Changes

Changes to `config.json` are picked up automatically on the next 6-hour re-sync.
To apply immediately:

```bash
docker restart scorestream-api
```

---

## Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `GITHUB_OWNER` | `yourusername` | GitHub user/org for ghcr.io image pulls |
| `SCORESTREAM_TAG` | `latest` | Image tag: `latest`, `beta`, or pinned version |
| `DISPATCHARR_URL` | required | Full URL to Dispatcharr |
| `DISPATCHARR_USER` | required | Admin username |
| `DISPATCHARR_PASS` | required | Admin password |
| `DISPATCHARR_GROUP` | `ScoreStream` | Channel group name (overridden by config.json) |
| `DISPATCHARR_CHANNEL_START` | `900` | Base channel number fallback if config.json missing |
| `STREAM_BASE_URL` | required | LAN URL of this server (reachable from Dispatcharr) |
| `STREAM_WIDTH` | `1920` | Video width |
| `STREAM_HEIGHT` | `1080` | Video height |
| `STREAM_FPS` | `30` | Framerate |
| `HLS_SEGMENT_DURATION` | `2` | HLS segment length in seconds |
| `HLS_PLAYLIST_SIZE` | `10` | Segments to keep in playlist |
| `WEB_PORT` | `8888` | Exposed port for streams and scoreboard UI |
| `TZ` | `America/Chicago` | Timezone |

---

## Troubleshooting

**Channels not appearing:**
```bash
docker logs scorestream-api       # Check auth and sync output
docker logs scorestream-ffmpeg    # Check encoding
curl http://YOUR_IP:8888/hls/all/stream.m3u8   # Verify HLS is live
```

**Wrong channel numbers after config change:**
```bash
docker restart scorestream-api    # Forces immediate config reload + re-sync
```

**Profile IDs rejected:**
Check the API log — it prints all available profile IDs at sync time and warns about any that don't match.

**Video not playing in Dispatcharr:**
Make sure `STREAM_BASE_URL` is the server's **LAN IP**, not `localhost`.
Dispatcharr reaches this URL from inside its own container.

---

## See Also

- [CHANGELOG.md](./CHANGELOG.md) — version history
- [Dispatcharr](https://github.com/Dispatcharr/Dispatcharr) — the IPTV manager this integrates with
