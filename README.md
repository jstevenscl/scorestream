# ScoreStream

A self-hosted live sports scoreboard that generates HLS streams and pushes them to Dispatcharr as real TV channels — automatically.

ScoreStream pulls live scores from ESPN and other public APIs, renders them as a full 1080p scoreboard, and produces an HLS video stream for every scoreboard you create. Each stream updates every 30 seconds without any user interaction.

---

## Contents

- [Requirements](#requirements)
- [Quick Start](#quick-start)
- [Environment Variables](#environment-variables)
- [Finding Your STREAM\_BASE\_URL](#finding-your-stream_base_url)
- [Volumes](#volumes)
- [Using ScoreStream](#using-scorestream)
  - [Accessing the UI](#accessing-the-ui)
  - [Creating a Scoreboard](#creating-a-scoreboard)
  - [Selecting Sports](#selecting-sports)
  - [Selecting Teams and Favorites](#selecting-teams-and-favorites)
  - [Display Settings Per Scoreboard](#display-settings-per-scoreboard)
  - [Previewing Your Scoreboard](#previewing-your-scoreboard)
  - [Global Display Defaults](#global-display-defaults)
  - [Themes](#themes)
  - [Audio Library and Playlists](#audio-library-and-playlists)
  - [Assigning Audio to a Scoreboard](#assigning-audio-to-a-scoreboard)
  - [Pushing to Dispatcharr](#pushing-to-dispatcharr)
  - [Updating After Pushing to Dispatcharr](#updating-after-pushing-to-dispatcharr)
- [Supported Sports](#supported-sports)
- [Troubleshooting](#troubleshooting)

---

## Requirements

- Docker and Docker Compose
- [Dispatcharr](https://github.com/Dispatcharr/Dispatcharr) (v0.20.0+ recommended for API token support)
- A server with a static LAN IP reachable from Dispatcharr's container

---

## Quick Start

### 1. Create a folder and download the compose file

```bash
mkdir scorestream && cd scorestream
curl -O https://raw.githubusercontent.com/jstevenscl/scorestream/main/docker-compose.yml
```

### 2. Create your `.env` file

```bash
cat > .env << 'EOF'
GITHUB_OWNER=jstevenscl
SCORESTREAM_TAG=latest

TZ=America/New_York

# The URL of THIS server as seen from Dispatcharr's container (use LAN IP, not localhost)
STREAM_BASE_URL=http://192.168.1.100:7777

# Port ScoreStream's web UI and HLS streams are exposed on
WEB_PORT=7777

# Optional — pre-configure Dispatcharr connection (can also be set in the UI)
DISPATCHARR_URL=http://192.168.1.100:9191
EOF
```

> **Important:** Replace `192.168.1.100` with your server's actual LAN IP address.

### 3. Start the stack

```bash
docker compose up -d
```

### 4. Open the UI

Navigate to `http://YOUR_SERVER_IP:7777` in your browser.

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `GITHUB_OWNER` | `jstevenscl` | GitHub username for pulling images from ghcr.io |
| `SCORESTREAM_TAG` | `latest` | Image tag: `latest`, `beta`, or a pinned version like `v1.0.0` |
| `TZ` | `America/New_York` | Timezone used for game times and stream clock |
| `STREAM_BASE_URL` | *(required)* | LAN URL of this server, reachable from Dispatcharr's container |
| `WEB_PORT` | `7777` | Port exposed for the web UI and HLS streams |
| `DISPATCHARR_URL` | *(optional)* | Pre-fill Dispatcharr URL (can be set in UI instead) |
| `STREAM_WIDTH` | `1920` | HLS stream width in pixels |
| `STREAM_HEIGHT` | `1080` | HLS stream height in pixels |
| `STREAM_FPS` | `1` | Stream framerate (1 fps is sufficient for score updates) |
| `HLS_SEGMENT_DURATION` | `2` | HLS segment length in seconds |
| `HLS_PLAYLIST_SIZE` | `4` | Number of HLS segments to keep in the live playlist |

---

## Finding Your STREAM\_BASE\_URL

`STREAM_BASE_URL` is the address Dispatcharr will use to reach ScoreStream's HLS streams. It must be:

- Your **server's LAN IP** — not `localhost` or `127.0.0.1` (Dispatcharr runs in its own container and cannot reach those)
- Including the **port** (`WEB_PORT`, default `7777`)
- **Without** a trailing slash

**Example:**
```
STREAM_BASE_URL=http://192.168.1.50:7777
```

To find your server's LAN IP:

```bash
# Linux / Mac
ip addr show | grep 'inet ' | grep -v 127.0.0.1

# Windows (PowerShell)
Get-NetIPAddress -AddressFamily IPv4 | Where InterfaceAlias -notlike '*Loopback*'
```

You can verify it's working by opening `http://YOUR_IP:7777/hls/` in a browser — you should see a directory listing of active HLS playlists.

---

## Volumes

ScoreStream uses three named Docker volumes. You do not need to configure them — they are created automatically on first start.

| Volume | Contents |
|---|---|
| `scorestream_config` | SQLite database (`scorestream.db`) — stores all scoreboard settings |
| `scorestream_hls` | HLS stream segments — shared between the stream container and nginx |
| `scorestream_audio` | Uploaded audio files for background music |

**To back up your configuration:**

```bash
docker run --rm -v scorestream_config:/data -v $(pwd):/backup alpine \
  tar czf /backup/scorestream-config-backup.tar.gz -C /data .
```

**To restore:**

```bash
docker run --rm -v scorestream_config:/data -v $(pwd):/backup alpine \
  tar xzf /backup/scorestream-config-backup.tar.gz -C /data
```

---

## Using ScoreStream

### Accessing the UI

Open `http://YOUR_SERVER_IP:7777` in your browser. The ScoreStream interface loads as a single-page app. The left sidebar contains navigation between sections.

---

### Creating a Scoreboard

A **Scoreboard** is a named configuration that defines which sports, teams, and display settings are used for one HLS stream channel.

1. In the left sidebar, click **My Scoreboards**
2. Click **+ New Scoreboard**
3. The editor opens with three steps:

**Step 1 — Display Settings**
- Give the scoreboard a name (this becomes the channel name in Dispatcharr)
- Set the timezone for game times
- Adjust card scale, rotation speed, layout, and typography
- Choose a theme or leave it on the global default

**Step 2 — Sports**
- Toggle on which sports to include
- For standard team sports (NFL, NBA, etc.), choose whether to show recent final scores and how many days back to show them
- For motorsports (F1, NASCAR, PGA), a dedicated settings panel appears with options specific to each series

**Step 3 — Teams** *(optional)*
- If you want the scoreboard to show only specific teams, use the team browser to select them
- Leave all teams deselected to show all games for the enabled sports
- Star (⭐) specific teams to pin their games to the top of the scoreboard

4. Click **Save** — ScoreStream immediately begins generating an HLS stream for this scoreboard

---

### Selecting Sports

In the editor Step 2, you can enable any combination of:

- **Pro leagues:** NFL, NBA, MLB, NHL, WNBA, CFL, XFL, UFL, MLS, NWSL, PGA Tour
- **Motorsport:** Formula 1, NASCAR Cup Series
- **International soccer:** Premier League, Champions League, La Liga, Bundesliga, Serie A, Ligue 1, MLS, Liga MX, NWSL, and more
- **NCAA:** Men's basketball, Women's basketball, Football, Baseball, Softball, Men's soccer, Women's soccer, Men's hockey

When only motorsport/golf sports are enabled, a dedicated settings panel appears for that scoreboard instead of the standard team-sport settings.

---

### Selecting Teams and Favorites

In editor Step 3, the team browser lets you filter which teams appear on the scoreboard:

- **No teams selected:** All games for enabled sports are shown
- **Teams selected:** Only games involving those teams are shown
- **Starred teams (⭐):** Those games are pinned to the top of the scoreboard above all others

To select teams:
1. Click the sport tab at the top to switch sports
2. Use the search box or browse by division/conference
3. Click a team card to select it (highlighted border)
4. Click the star icon to also favorite it

---

### Display Settings Per Scoreboard

In editor Step 1, each scoreboard has independent control over:

| Setting | Description |
|---|---|
| **Card Scale** | Scales all game cards larger or smaller (50–300%) |
| **Rotation Timer** | How long each page shows before advancing (0 = no rotation) |
| **Team Logo Size** | Size of team logos on cards |
| **Abbreviation Size** | Font size of team abbreviations |
| **Score Size** | Font size of the score numbers |
| **Team Name Size** | Font size of the full team name subtitle |
| **Team Name Color** | Color of the team name subtitle text |
| **Stream Layout** | Grid (multi-column), Fullscreen (one wide column), or Ticker (compact rows) |
| **Theme Override** | Use a different color theme just for this scoreboard |
| **Stream Audio** | None, built-in playlist music, or a custom audio stream URL |

Each setting can also inherit from **Display Defaults** (the global defaults) by leaving the "Use Default" toggles on.

---

### Previewing Your Scoreboard

The editor Step 1 includes a **Card Scale Preview** — a live two-card preview that updates as you adjust typography and scale settings.

To preview the full scoreboard stream output:
1. Save the scoreboard
2. In **My Scoreboards**, find the scoreboard card and click **Preview** (opens the stream page in a new tab)
3. The stream page shows the live scoreboard exactly as it appears in the HLS output

---

### Global Display Defaults

**Display Defaults** (found in the sidebar under Settings) let you set values that apply to all scoreboards unless individually overridden.

Sections:
- **Theme** — global color scheme applied to both the UI and stream output
- **Font Sizes** — default Abbreviation, Score, and Team Name sizes
- **Colors** — default Team Name color
- **Card Size** — default Card Scale, Rotation Timer, Logo Size, and Layout

As you adjust sliders and pickers, two live preview cards update in real time at the bottom of the panel so you can see the effect immediately.

Click **Save Defaults** to apply. Any scoreboard using defaults will pick up the new values immediately.

---

### Themes

ScoreStream includes five built-in themes:

| Theme | Description |
|---|---|
| **Dark Blue** | Deep navy with cyan accent (default) |
| **Carbon** | Pure black with white accent |
| **Dark Red** | Deep crimson with red/pink accent |
| **Dark Green** | Dark forest with green accent |
| **Light** | Light grey with blue accent |

**Applying a theme globally:**
1. Go to **Display Defaults** → Theme section
2. Click any swatch to apply it instantly
3. Save Defaults

**Creating a custom theme:**
1. Click **Edit Current** to open the customizer for the active theme, or **+ New Custom Theme** to start from scratch
2. Adjust the 9 color pickers (backgrounds, borders, accents, text)
3. Enter a name and click **Save Theme** — the theme is saved and appears in the swatch row

**Per-scoreboard theme override:**
In the editor Step 1, the **Theme Override** section lets you choose a different theme for just that scoreboard. Select **Use Global** to follow the global default.

Custom themes are stored in your display defaults and are available across all scoreboards.

---

### Audio Library and Playlists

ScoreStream can mix background music into the HLS stream output.

**Uploading audio files:**
1. Go to **Audio Library** in the sidebar
2. Drag and drop audio files onto the upload zone, or click to browse
3. Supported formats: MP3, AAC, OGG, FLAC, WAV, M4A (max 50 MB per file)
4. Uploaded files appear in **My Tracks**

**Creating a playlist:**
1. In the **Playlists** section, click **+ New**
2. Name the playlist and optionally mark it as the **Global Default**
3. Add tracks from your library to the playlist

The **Global Default** playlist is used by any scoreboard that has audio enabled but no specific playlist assigned.

---

### Assigning Audio to a Scoreboard

In the scoreboard editor Step 1, scroll to **Stream Audio**:

- **None** — stream is silent (default)
- **Built-in Music** — plays royalty-free sports/hype instrumentals bundled in the stream container, no setup needed
- **Custom Stream** — paste any direct audio stream URL (MP3, AAC, Icecast, Shoutcast)

For Custom Stream or Playlist modes, you can also select a specific playlist from the dropdown. If left on "Use Global Default," the global default playlist is used.

---

### Pushing to Dispatcharr

Before pushing, configure your Dispatcharr connection:

1. Go to **Integrations** in the sidebar
2. Enter your Dispatcharr URL (e.g. `http://192.168.1.100:9191`)
3. Enter your **API Token** (Dispatcharr → Profile → API Keys → Generate)
   - Or use legacy Username / Password for older Dispatcharr versions
4. Click **Test Connection** to verify, then **Save**

**To push a scoreboard as a Dispatcharr channel:**
1. Go to **My Scoreboards**
2. Find the scoreboard and click **Push to Dispatcharr** (or the Dispatcharr icon)
3. ScoreStream creates or updates a channel in Dispatcharr with:
   - The scoreboard's name as the channel name
   - The HLS stream URL pointing to this server
   - The channel group "ScoreStream"

**Channel group and numbering:**
Channels are placed in a group called **ScoreStream** by default. Channel numbers are assigned automatically starting from 900. Both can be customized in the Integrations settings.

---

### Updating After Pushing to Dispatcharr

When you change a scoreboard's name or settings after it has already been pushed:

1. Make your changes in the editor and **Save**
2. Click **Push to Dispatcharr** again — ScoreStream will **update** the existing channel (it does not create a duplicate)

The HLS stream URL does not change between updates (it is based on the scoreboard's slug), so Dispatcharr does not need to re-import anything. The stream content updates automatically every 30 seconds regardless.

**If you delete a scoreboard:** The channel remains in Dispatcharr. Remove it manually in Dispatcharr's channel list if you no longer want it.

---

## Supported Sports

| League | Type | Live Scores | Schedules | Final Scores |
|---|---|---|---|---|
| NFL | Team | ✓ | ✓ | ✓ |
| NBA | Team | ✓ | ✓ | ✓ |
| MLB | Team | ✓ | ✓ | ✓ |
| NHL | Team | ✓ | ✓ | ✓ |
| WNBA | Team | ✓ | ✓ | ✓ |
| CFL | Team | ✓ | ✓ | ✓ |
| MLS | Team | ✓ | ✓ | ✓ |
| NWSL | Team | ✓ | ✓ | ✓ |
| Premier League | Team | ✓ | ✓ | ✓ |
| Champions League | Team | ✓ | ✓ | ✓ |
| + 10 more soccer | Team | ✓ | ✓ | ✓ |
| NCAA Basketball (M/W) | Team | ✓ | ✓ | ✓ |
| NCAA Football | Team | ✓ | ✓ | ✓ |
| NCAA Baseball/Softball | Team | ✓ | ✓ | ✓ |
| NCAA Soccer (M/W) | Team | ✓ | ✓ | ✓ |
| NCAA Hockey | Team | ✓ | ✓ | ✓ |
| PGA Tour | Individual | ✓ | ✓ | ✓ |
| Formula 1 | Individual | ✓ | ✓ | ✓ |
| NASCAR Cup Series | Individual | ✓ | ✓ | ✓ |

---

## Troubleshooting

**Stream not playing in Dispatcharr / VLC:**
- Confirm `STREAM_BASE_URL` is your server's **LAN IP**, not `localhost`
- Test directly: `http://YOUR_IP:7777/hls/YOUR_SLUG/stream.m3u8`
- Check the stream container is running: `docker logs scorestream-stream`

**No scores showing:**
- ScoreStream fetches from ESPN's public APIs — if there are no live or scheduled games today, the scoreboard will show a "No Games" message
- Check the API container logs: `docker logs scorestream-api`

**Scoreboard shows "Not Configured":**
- The scoreboard has no sports enabled. Edit it and enable at least one sport in Step 2.

**Dispatcharr connection fails:**
- Verify the API token is correct (Dispatcharr → Profile → API Keys)
- Ensure the Dispatcharr URL is reachable from the ScoreStream container (use LAN IP, not localhost)

**Changes not appearing on stream:**
- The stream refreshes every 30 seconds. Wait up to 30 seconds after saving.
- For display setting changes (fonts, colors, scale), the stream container picks these up on next page render

**Updating ScoreStream:**
```bash
docker compose pull
docker compose up -d
```

---

## See Also

- [CHANGELOG.md](./CHANGELOG.md) — version history
- [Dispatcharr](https://github.com/Dispatcharr/Dispatcharr) — the IPTV manager this integrates with
