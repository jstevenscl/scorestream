# ScoreStream Pro — User Guide

**Version:** 0.2.0-beta  
**Last Updated:** 2026-02-19

ScoreStream Pro is a self-hosted live sports scoreboard that streams directly into Dispatcharr as real IPTV channels — automatically registering and numbering them for you.

---

## Table of Contents

1. [Requirements](#1-requirements)
2. [Installation](#2-installation)
3. [Configuration Reference](#3-configuration-reference)
4. [Channel Profiles](#4-channel-profiles)
5. [Choosing Your Image Tag](#5-choosing-your-image-tag)
6. [Updating ScoreStream Pro](#6-updating-scorestream-pro)
7. [Troubleshooting](#7-troubleshooting)
8. [FAQ](#8-faq)

---

## 1. Requirements

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| CPU | 2 cores | 4 cores |
| RAM | 2 GB | 4 GB |
| Disk | 5 GB free | 10 GB free |
| OS | Linux (any) | Ubuntu 22.04+ |
| Docker | 24.x | Latest |
| Docker Compose | v2 | Latest |
| Dispatcharr | 0.15.0+ | Latest |

ScoreStream Pro works on any system that runs Docker: Proxmox LXC, Unraid, a Raspberry Pi 5, a NAS (with Docker support), or a bare Linux box.

The Dispatcharr instance and ScoreStream Pro must be on the same **local network** or otherwise mutually reachable. They do not need to be on the same machine.

---

## 2. Installation

### Step 1 — Create a folder

```bash
mkdir ~/scorestream-pro && cd ~/scorestream-pro
```

### Step 2 — Download the stack files

```bash
# Download the compose file
curl -O https://raw.githubusercontent.com/OWNER/scorestream-pro/main/scorestream/docker-compose.yml

# Download the environment template
curl -O https://raw.githubusercontent.com/OWNER/scorestream-pro/main/scorestream/.env.example
mv .env.example .env

# Download the default channel config
mkdir config
curl -o config/config.json https://raw.githubusercontent.com/OWNER/scorestream-pro/main/scorestream/config/config.json
```

### Step 3 — Edit your `.env` file

Open `.env` in any text editor and fill in your values:

```env
# Your GitHub username (for pulling Docker images)
GITHUB_OWNER=YOURUSERNAME

# Which image version to run (latest, beta, or pinned like v0.2.0-beta)
SCORESTREAM_TAG=latest

# Your Dispatcharr connection
DISPATCHARR_URL=http://192.168.1.100:9191
DISPATCHARR_USER=admin
DISPATCHARR_PASS=yourpassword

# THIS server's IP address — must be reachable FROM Dispatcharr's container
# Do NOT use localhost or 127.0.0.1 here
STREAM_BASE_URL=http://192.168.1.50:8888

# Timezone for the scoreboard clock
TZ=America/Chicago
```

> ⚠️ **Important about STREAM_BASE_URL**
> This is the URL Dispatcharr uses to reach your HLS streams.
> It must be the **LAN IP address** of the machine running ScoreStream Pro.
> `localhost` will not work because Dispatcharr runs in its own container.

### Step 4 — Start everything

```bash
docker compose --env-file .env up -d
```

### Step 5 — Watch the registration happen

```bash
docker logs -f scorestream-api
```

You'll see output like:

```
[10:30:01] [api] ScoreStream Pro API v0.2.0-beta starting
[10:30:02] [api] Authenticated ✅
[10:30:05] [api] HLS live ✅
[10:30:05] [api] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[10:30:05] [api] ScoreStream Pro — Channel Sync
[10:30:06] [api] 📺  ScoreStream — All Sports  ch900
[10:30:07] [api]     ✅ ch900 → http://192.168.1.50:8888/hls/all/stream.m3u8
[10:30:07] [api] 🏈  ScoreStream — NFL  ch901
[10:30:08] [api]     ✅ ch901 → http://192.168.1.50:8888/hls/nfl/stream.m3u8
...
```

### Step 6 — Verify in Dispatcharr

1. Open Dispatcharr in your browser
2. Go to **Channels**
3. In the group dropdown, select **ScoreStream**
4. You should see 6–7 channels listed with HLS stream URLs assigned
5. Click the preview icon on any channel to confirm video is playing

---

## 3. Configuration Reference

### `.env` Variables

These control how the containers start. Changes require a container restart.

| Variable | Default | Description |
|----------|---------|-------------|
| `GITHUB_OWNER` | `yourusername` | GitHub username for ghcr.io image pulls |
| `SCORESTREAM_TAG` | `latest` | Image track: `latest`, `beta`, or pinned version |
| `DISPATCHARR_URL` | required | Full URL to your Dispatcharr instance |
| `DISPATCHARR_USER` | required | Dispatcharr admin username |
| `DISPATCHARR_PASS` | required | Dispatcharr admin password |
| `DISPATCHARR_GROUP` | `ScoreStream` | Channel group name created in Dispatcharr |
| `DISPATCHARR_CHANNEL_START` | `900` | Fallback base channel number if config.json is missing |
| `STREAM_BASE_URL` | required | LAN URL of this server, reachable from Dispatcharr |
| `STREAM_WIDTH` | `1920` | Video resolution width |
| `STREAM_HEIGHT` | `1080` | Video resolution height |
| `STREAM_FPS` | `30` | Video framerate |
| `HLS_SEGMENT_DURATION` | `2` | HLS segment length in seconds (lower = lower latency) |
| `HLS_PLAYLIST_SIZE` | `10` | Number of segments kept in playlist |
| `WEB_PORT` | `8888` | External port for web/stream access |
| `TZ` | `America/Chicago` | Timezone for scoreboard clock display |

---

### `config/config.json` — Channel Settings

This file controls channel numbering, names, which channels are active, and Dispatcharr profile assignment. It is **reloaded automatically** every 6 hours, or immediately when you restart the API container.

#### Channel Numbering

**Auto mode** — channels numbered sequentially starting from `base_number`, skipping disabled channels:

```json
{
  "channel_numbering": {
    "mode": "auto",
    "base_number": 900
  }
}
```

Result: All Sports=900, NFL=901, NBA=902, MLB=903, NHL=904, NCAAB=905, NCAABase=906

**Manual mode** — set each channel's number explicitly:

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

#### Per-Channel Options

Each channel in `channels` supports these properties:

| Property | Type | Description |
|----------|------|-------------|
| `number` | integer | Channel number (used in manual mode, or as override in auto) |
| `name` | string | Display name shown in Dispatcharr |
| `enabled` | boolean | `false` = skip this channel entirely |

**Example — rename NFL and disable NCAA Baseball:**

```json
{
  "channel_numbering": {
    "mode": "auto",
    "base_number": 900,
    "channels": {
      "all":      { "name": "📡 ScoreStream Live",    "enabled": true  },
      "nfl":      { "name": "🏈 NFL Scores 24/7",     "enabled": true  },
      "nba":      { "name": "🏀 NBA Scores 24/7",     "enabled": true  },
      "mlb":      { "name": "⚾ MLB Scores 24/7",     "enabled": true  },
      "nhl":      { "name": "🏒 NHL Scores 24/7",     "enabled": true  },
      "ncaab":    { "name": "🏀 College Basketball",  "enabled": true  },
      "ncaabase": { "enabled": false }
    }
  }
}
```

#### Applying config.json Changes

To apply immediately (without waiting for the 6-hour auto-sync):

```bash
docker restart scorestream-api
```

---

## 4. Channel Profiles

Dispatcharr's **Channel Profiles** let you create different channel lists for different users or outputs (e.g. one profile for kids, one for full access, one for a specific M3U client).

ScoreStream Pro can automatically assign its channels to the right profiles.

### Finding your Profile IDs

The easiest way: check the `scorestream-api` logs right after startup. It always prints all available profiles:

```
[api] Dispatcharr has 3 Channel Profile(s):
[api]     id=1  name='Default'
[api]     id=2  name='Kids'
[api]     id=3  name='Sports'
```

### Configuring Profile Assignment

In `config/config.json`, under the `dispatcharr` section:

**Add to ALL profiles** (default — backward compatible, safest choice if you're unsure):

```json
{
  "dispatcharr": {
    "channel_profiles": {
      "mode": "all"
    }
  }
}
```

**Add to NO profiles** (channels exist in Dispatcharr but won't appear in any profile — you assign them manually):

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

In this example, ScoreStream channels will appear in **Default** (id=1) and **Sports** (id=3), but not in **Kids** (id=2).

If you specify IDs that don't exist in your Dispatcharr, ScoreStream Pro will warn you in the logs and skip those IDs. If none of the IDs are valid, it falls back to adding to all profiles.

---

## 5. Choosing Your Image Tag

Set `SCORESTREAM_TAG` in your `.env`:

| Tag | Description | Who should use it |
|-----|-------------|-------------------|
| `latest` | Last stable release | Most users |
| `beta` | Bleeding edge, auto-built on code changes | Early adopters |
| `v0.2.0-beta` | Pinned to exact version | Reproducible setups |
| `v1.0.0` | Pinned stable version | Production/always-on |

To switch tracks, edit `.env`, then:

```bash
docker compose pull
docker compose up -d
```

---

## 6. Updating ScoreStream Pro

### Check current version

```bash
docker exec scorestream-api cat /proc/1/cmdline 2>/dev/null || \
  docker logs scorestream-api 2>&1 | grep "v0\." | head -1
```

### Update to latest

```bash
# Pull new images
docker compose pull

# Restart containers with new images
docker compose up -d

# Verify
docker logs scorestream-api | tail -20
```

### Pinned version → new version

1. Edit `.env`: change `SCORESTREAM_TAG=v0.2.0-beta` to `SCORESTREAM_TAG=v0.3.0-beta`
2. Run:
   ```bash
   docker compose pull
   docker compose up -d
   ```

### Rollback if something breaks

```bash
# Edit .env to previous version tag, then:
docker compose pull
docker compose up -d
```

### Preserving your config across updates

Your `config/config.json` is stored on your host machine (not inside the container), so it survives all updates automatically.

---

## 7. Troubleshooting

### Channels not appearing in Dispatcharr

**Step 1:** Check API logs for errors:
```bash
docker logs scorestream-api | grep -E "ERROR|Failed|❌"
```

**Step 2:** Verify Dispatcharr is reachable from inside the container:
```bash
docker exec scorestream-api wget -qO- http://YOUR_DISPATCHARR_IP:9191/api/token/
# Should return: {"detail":"Method \"GET\" not allowed."}
# That response means the connection works (405 is expected for GET on this endpoint)
```

**Step 3:** Check credentials:
```bash
# Look for auth errors in the log
docker logs scorestream-api | grep -i "auth\|401\|credential"
```

### Video not playing / Dispatcharr can't reach the stream

**Step 1:** Verify HLS is working from your browser:
```
http://YOUR_SERVER_IP:8888/hls/all/stream.m3u8
```
You should see a text file starting with `#EXTM3U`.

**Step 2:** Confirm `STREAM_BASE_URL` in `.env` uses your server's LAN IP, not localhost.

**Step 3:** Check FFmpeg is running:
```bash
docker logs scorestream-ffmpeg | tail -30
```

### Renderer not starting / blank video

```bash
docker logs scorestream-renderer
```

Common causes:
- `scorestream-web` container isn't healthy yet — renderer waits for it
- Not enough shared memory — ensure `shm_size: 512mb` is in your compose file
- Web container not reachable — check network connectivity between containers

### Channel numbers wrong after changing config.json

Config is reloaded every 6 hours or on container restart. Force immediate apply:

```bash
docker restart scorestream-api
```

### "Invalid profile IDs" warning in logs

The API printed the valid profile IDs at startup. Check those and update `config.json`:

```bash
docker logs scorestream-api | grep "Profile\|id="
```

### Container keeps restarting

```bash
docker logs scorestream-api --tail 50
# Look for the last error message before the restart
```

Common cause: missing required environment variables (DISPATCHARR_URL, STREAM_BASE_URL).

---

## 8. FAQ

**Q: Do I need to be on the same machine as Dispatcharr?**  
No. ScoreStream Pro and Dispatcharr just need to be able to reach each other over the network. Both need to be on your LAN (or VPN).

**Q: What happens during off-season?**  
The scoreboard displays that league's section with an "Off Season" indicator and no games. The video stream continues — it just shows the off-season state of the board.

**Q: Can I use ScoreStream Pro without Dispatcharr?**  
Yes. The scoreboard UI is accessible directly at `http://YOUR_SERVER:8888` and works as a standalone dashboard in any browser. The Dispatcharr integration (channel registration) is an optional layer on top.

**Q: Why are all my sport channels showing the same video?**  
By design in the current version. One Chrome renderer runs the full-scoreboard view, and that video is sent to all 7 HLS channels. The channels are named differently in Dispatcharr (NFL, NBA, etc.) for organization. True per-sport isolated video is planned for a future release.

**Q: How do I add a custom logo to my channels in Dispatcharr?**  
After ScoreStream creates the channels, you can edit them in Dispatcharr → Channels → Edit → Logo. This setting persists — ScoreStream won't overwrite logos you set manually.

**Q: How often does the scoreboard data refresh?**  
The scoreboard pulls live data from ESPN's API every 60 seconds. The video stream is continuous — FFmpeg re-encodes frames in real time regardless of data refresh timing.

**Q: Can I run this on a Raspberry Pi?**  
The Docker images are built for both `linux/amd64` and `linux/arm64`, so a Raspberry Pi 4 or 5 with 4+ GB RAM should work. Headless Chrome is the resource-intensive part — give it at least 2 GB RAM.

**Q: Will updates break my config.json?**  
New fields added in updates always have safe defaults, so your existing `config.json` will continue to work. When new options are added, they'll be documented in the release notes with their default behavior.

**Q: How do I completely remove ScoreStream Pro?**  

```bash
docker compose down -v    # Stops containers and removes volumes
# Then delete the folder:
rm -rf ~/scorestream-pro
```
