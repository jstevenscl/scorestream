# ScoreStream

A self-hosted live sports scoreboard that generates HLS streams and pushes them to Dispatcharr as real TV channels — automatically.

ScoreStream pulls live scores from ESPN and other public APIs, renders them as a full 1080p scoreboard, and produces an HLS video stream for every scoreboard you create. Each stream updates every 30 seconds without any user interaction.

---

## Contents

- [Requirements](#requirements)
- [Quick Start](#quick-start)
- [Environment Variables](#environment-variables)
- [Stream Quality Tiers](#stream-quality-tiers)
- [Finding Your STREAM\_BASE\_URL](#finding-your-stream_base_url)
- [Volumes](#volumes)
- [Using ScoreStream](#using-scorestream)
  - [Accessing the UI](#accessing-the-ui)
  - [Creating a Scoreboard](#creating-a-scoreboard)
  - [Selecting Sports](#selecting-sports)
  - [Selecting Teams and Favorites](#selecting-teams-and-favorites)
  - [Settings Pages Overview](#settings-pages-overview)
  - [Display Settings Per Scoreboard](#display-settings-per-scoreboard)
  - [Previewing Your Scoreboard](#previewing-your-scoreboard)
  - [Default Stream Settings](#default-stream-settings)
  - [Themes](#themes)
  - [System Theme (App UI)](#system-theme-app-ui)
  - [Stream Layout and Pagination](#stream-layout-and-pagination)
  - [Per-Sport Display Settings (Headshots)](#per-sport-display-settings-headshots)
  - [Sharing Scoreboards (Export / Import JSON)](#sharing-scoreboards-export--import-json)
  - [Audio Library and Playlists](#audio-library-and-playlists)
  - [Assigning Audio to a Scoreboard](#assigning-audio-to-a-scoreboard)
  - [Pushing to Dispatcharr](#pushing-to-dispatcharr)
  - [Updating After Pushing to Dispatcharr](#updating-after-pushing-to-dispatcharr)
- [Ticker Overlay](#ticker-overlay)
  - [How It Works](#how-it-works)
  - [Shared Volume Setup](#shared-volume-setup)
  - [Using the Ticker Overlay UI](#using-the-ticker-overlay-ui)
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
| `STREAM_QUALITY` | `balanced` | Stream quality tier — `low`, `balanced`, or `high`. See [Stream Quality Tiers](#stream-quality-tiers) below. |
| `STREAM_BITRATE` | *(tier default)* | Override video bitrate (e.g. `1500k`). Leave unset to use the tier default. |
| `STREAM_MAXRATE` | *(tier default)* | Override max bitrate (e.g. `2500k`) |
| `STREAM_BUFSIZE` | *(tier default)* | Override VBV buffer size (e.g. `4000k`) |
| `STREAM_PRESET` | *(tier default)* | Override x264 preset (e.g. `veryfast`, `medium`, `slow`) |
| `JPEG_QUALITY` | *(tier default)* | Override frame screenshot quality (1–100) |
| `STREAM_DPR` | `1` | Browser supersampling — `1` = native 1080p, `2` = render at 4K then downscale (sharper text, ~4x browser RAM) |
| `HLS_SEGMENT_DURATION` | `2` | HLS segment length in seconds |
| `HLS_PLAYLIST_SIZE` | `4` | Number of HLS segments to keep in the live playlist |

---

## Stream Quality Tiers

ScoreStream encodes the rendered scoreboard to HLS via Chromium screenshots + x264. Three quality tiers are available via `STREAM_QUALITY`:

| Tier | Bitrate | Max Rate | Buffer | Preset | JPEG Q | Best for |
|---|---|---|---|---|---|---|
| `low` | 800k | 1200k | 2000k | veryfast | 85 | Many concurrent streams or constrained server |
| **`balanced`** *(default)* | **1500k** | **2500k** | **4000k** | **veryfast** | **92** | Most users — noticeably crisper text, **same CPU as `low`** |
| `high` | 3000k | 4500k | 6000k | medium | 95 | Single stream, low FPS, want maximum text clarity |

**The counter-intuitive part:** higher bitrate is actually NEUTRAL or slightly cheaper for CPU. The encoder is given more bits to work with, so it has to do less compression searching. The CPU cost driver is **FPS** and **preset**, not bitrate.

### What changes between tiers

- **Text clarity in the stream output** improves as bitrate goes up (less JPEG/H.264 compression artifacts on text edges)
- **Per-viewer bandwidth** scales with bitrate (≈800 kbps → 1500 kbps → 3000 kbps)
- **Server CPU** stays the same between `low` and `balanced` (only the preset and JPEG quality change CPU). `high` adds ~3-4x more CPU per encoded frame because of the slower preset.

### When to upgrade or downgrade

- **Default `balanced`** — recommended for everyone, including multi-stream setups. Same CPU as the old `low` defaults but ~2x text clarity.
- **Upgrade to `high`** when:
  - You have one or two streams running at most
  - Your server has CPU headroom (Oracle A1 4 vCPU is fine for 1-2 streams at 1 FPS)
  - You want the cleanest possible text on TV displays
- **Downgrade to `low`** when:
  - You're running 5+ concurrent streams
  - Your viewers have limited downstream bandwidth (<3 Mbps IPTV connections)
  - You're seeing stream stuttering or buffering

### Fine-tuning individual settings

If a tier is close to what you want but you'd like to adjust one specific setting, use the individual override env vars (`STREAM_BITRATE`, `STREAM_PRESET`, etc.). Any individual override beats the tier setting. Example:

```yaml
- STREAM_QUALITY=balanced     # Tier baseline
- STREAM_BITRATE=2000k        # Override just the bitrate
- JPEG_QUALITY=95             # Override just the screenshot quality
```

### Browser supersampling (`STREAM_DPR`)

The biggest single improvement for text crispness is supersampling — render the scoreboard at 2x resolution (3840×2160) then downsample to 1080p. The downsampling step naturally smooths text edges.

```yaml
- STREAM_DPR=2
```

**Cost:** ~4x browser RAM (Chromium uses ~150 MB at 1x, ~600 MB at 2x). At 4K rendering on a 4 vCPU server you can comfortably run 1-2 concurrent streams. For 3+ streams, leave `STREAM_DPR=1`.

---

## Finding Your STREAM\_BASE\_URL

`STREAM_BASE_URL` is the address Dispatcharr will use to reach ScoreStream's HLS streams. The correct value depends on how your stacks are arranged.

---

**Option A — ScoreStream and Dispatcharr in the same Docker stack / network**

They share a Docker network and can reach each other by container name. Use the ScoreStream web container name and its internal port:

```
STREAM_BASE_URL=http://scorestream-web:80
```

Or if you have defined a custom network alias, use that instead.

---

**Option B — ScoreStream and Dispatcharr in different stacks on the same host**

Use your server's **LAN IP** and the exposed `WEB_PORT`. Do not use `localhost` — Dispatcharr runs in its own container and cannot reach `localhost` on the host.

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

To find the internal Docker IP of the ScoreStream web container (alternative to LAN IP):

```bash
docker inspect scorestream-web | grep '"IPAddress"'
```

You can then use that IP directly:
```
STREAM_BASE_URL=http://172.18.0.5:80
```

---

**Option C — ScoreStream and Dispatcharr on different hosts / VMs**

Use the LAN IP (or hostname) of the machine running ScoreStream, with the exposed `WEB_PORT`:

```
STREAM_BASE_URL=http://192.168.1.50:7777
```

---

You can verify the URL is reachable from Dispatcharr's container by running:

```bash
docker exec dispatcharr curl -s http://YOUR_STREAM_BASE_URL/health
```

It should return `OK`.

---

## Volumes

ScoreStream uses named Docker volumes. The first three are created automatically on first start.

| Volume | Contents |
|---|---|
| `scorestream_config` | SQLite database (`scorestream.db`) — stores all scoreboard settings |
| `scorestream_hls` | HLS stream segments — shared between the stream container and nginx |
| `scorestream_audio` | Uploaded audio files for background music |
| `scorestream_ticker` | Ticker text file shared with Dispatcharr — **required only for Ticker Overlay** (see [Ticker Overlay](#ticker-overlay)) |

> The `scorestream_ticker` volume is **not created automatically**. You must create it manually and add it to both stacks before using the Ticker Overlay feature. See [Shared Volume Setup](#shared-volume-setup).

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

### Settings Pages Overview

Click the gear icon in the sidebar to open Settings. The navigation is grouped:

**📺 My Scoreboards** — Create, edit, delete, and push scoreboards. Each scoreboard is one HLS stream channel with its own sports, teams, and display overrides.

**Appearance** *(group — controls how things look)*

| Page | What It Controls | Affects |
|---|---|---|
| **Stream Card Style** | Card typography for the sidebar preview and stream output — font sizes for abbreviations, scores, team names, channel names, header/status bar, period/time text; team name color; header status colors (LIVE/FINAL/period); logo size; refresh rate. | Sidebar preview + stream cards |
| **Default Stream Settings** | Global defaults that all scoreboards inherit: theme, font sizes, colors, card scale, rotation timer, layout. Scoreboards use these unless they override individually. | Stream output (all scoreboards) |
| **System Theme** | Customize the **app UI itself** — background colors, text colors, accent colors, borders, and base font size. Does **not** affect stream output or game cards. | Settings UI + sidebar appearance |

**Data & Media** *(group — content sources and overlays)*

| Page | What It Controls | Affects |
|---|---|---|
| **Sports Library** | Enable or disable sports globally. Disabled sports are hidden from the sidebar and all scoreboard editors. Individual sports (PGA, F1, NASCAR, ATP, WTA) can be expanded ▸ to set per-sport headshot size for the main browsing view. | Entire app |
| **Audio Library** | Upload audio files, create playlists, set a global default playlist for stream background music. | Stream audio |
| **Ticker Overlay** | Overlay a live scrolling score ticker onto a Dispatcharr channel. Select sports, channel, and appearance (font size, scroll speed, opacity, position). Includes a live preview. | Dispatcharr channel video |

**System** *(group — connection and config)*

| Page | What It Controls | Affects |
|---|---|---|
| **Integrations** | Connect to Dispatcharr — URL, API token, channel group name, starting channel number. | Dispatcharr push |
| **Backup & Restore** | Export all settings to a JSON file or restore from a previous backup. | Configuration |

> **Key distinction:** *Stream Card Style* and *Default Stream Settings* control how game cards look in the **stream output** (the video). *System Theme* controls how the **ScoreStream app interface** looks in your browser.

---

### Creating a Scoreboard

A **Scoreboard** is a named configuration that defines which sports, teams, and display settings are used for one HLS stream channel.

1. In the left sidebar, click **My Scoreboards**
2. Click **+ New Scoreboard**
3. The editor opens with three steps:

**Step 1 — Sports & Leagues**
- Toggle on which sports appear on this scoreboard. Available groups:
  - **Pro Leagues** — NFL, NBA, MLB, NHL, WNBA, CFL, XFL, UFL, MLS, NWSL, PGA Tour
  - **Motorsport** — Formula 1, NASCAR Cup, NASCAR NOAPS, NASCAR Trucks
  - **Tennis** — ATP Tour, WTA Tour
  - **International Soccer** — Premier League, Champions League, La Liga, Bundesliga, Serie A, Ligue 1
  - **NCAA Men** — Football, Men's Basketball, Men's Baseball
  - **NCAA Women** — Women's Basketball, Softball, Women's Volleyball, Women's Lacrosse
- For standard team sports (NFL, NBA, MLB, NHL, soccer, NCAA, etc.), choose whether to show recent final scores and how many days back to show them
- Sport selection drives what's available in Step 3 — a PGA-only scoreboard sees PGA-specific display settings, a mixed scoreboard sees all relevant controls

**Step 2 — Teams** *(optional)*
- If you want the scoreboard to show only specific teams, use the team browser to select them
- Leave all teams deselected to show all games for the enabled sports
- Star (⭐) specific teams to pin their games to the top of the scoreboard

**Step 3 — Display & Layout** *(sport-aware)*
- Give the scoreboard a name (this becomes the channel name in Dispatcharr)
- Set the timezone for game times
- Adjust card scale, rotation speed, layout, and typography
- Choose a theme or leave it on the global default
- **Card Header & Status** controls — header/status text size, period/time text size, LIVE color, FINAL color, period/time color
- **Section Headers** controls — size, stripe width, and color of the headers above each card group (e.g. "🏎️ FORMULA 1 — UPCOMING")
- For motor sports / golf / tennis, a **Per-Sport Display & Data** section appears with content options (which races, standings, etc.) and per-sport headshot size sliders

> **All Step 3 settings are per-scoreboard.** Each scoreboard has its own copy in the database, so changing settings on one scoreboard never affects another. Headshot sizes are additionally per-sport within a scoreboard.

4. Click **Save & Close** — ScoreStream immediately begins generating an HLS stream for this scoreboard

> **Why this order?** Display settings are tailored to the sports you've enabled. Picking sports first means Step 3 only shows the controls relevant to your scoreboard.

---

### Selecting Sports

In the editor Step 1 (Sports & Leagues), you can enable any combination of:

- **Pro leagues:** NFL, NBA, MLB, NHL, WNBA, CFL, XFL, UFL, MLS, NWSL, PGA Tour
- **Motorsport:** Formula 1, NASCAR Cup Series, NASCAR O'Reilly Auto Parts Series (NOAPS), NASCAR Craftsman Truck Series
- **Tennis:** ATP Tour, WTA Tour
- **International soccer:** Premier League, Champions League, La Liga, Bundesliga, Serie A, Ligue 1
- **NCAA Men:** Football, Basketball, Baseball
- **NCAA Women:** Basketball, Softball, Volleyball, Lacrosse

For motor sports (F1, NASCAR, PGA) and tennis (ATP, WTA), a per-sport configuration panel appears in **Step 3 — Display & Layout** with options for which races/standings/tournaments to show, how many to show, headshot size on the cards, and more. Each enabled motor/tennis sport gets its own labeled section.

---

### Selecting Teams and Favorites

In editor Step 2 (Teams), the team browser lets you filter which teams appear on the scoreboard:

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

In editor Step 3 (Display & Layout), each scoreboard has independent **per-scoreboard** control over (these settings only affect that scoreboard's stream output, not other scoreboards):

| Setting | Description |
|---|---|
| **Card Scale** | Scales all game cards larger or smaller (50–300%) |
| **Rotation Timer** | How long each page shows before advancing (0 = no rotation) |
| **Team Logo Size** | Size of team logos on cards |
| **Abbreviation Size** | Font size of team abbreviations (e.g., LAL, BOS) |
| **Score Size** | Font size of the score numbers |
| **Team Name Size** | Font size of the full team name subtitle |
| **Channel Name Size** | Font size of the channel/event name at the top of cards |
| **Header / Status Size** | Font size of the status text (LIVE, FINAL, upcoming time) |
| **Period / Time Size** | Font size of the period/game-time text (Q4 2:15, 7th, etc.) |
| **Section Header Size** | Font size of the section headers above each card group (e.g. "🏎️ FORMULA 1 — UPCOMING") |
| **Section Stripe Width** | Width of the colored vertical stripe before each section header (0 to hide) |
| **Section Header Color** | Color of the section header text |
| **Headshot Size** | Size of player headshot circles on individual-sport cards |
| **Team Name Color** | Color of the team name subtitle text |
| **LIVE Status Color** | Color of the pulsing LIVE indicator on in-progress games |
| **FINAL Status Color** | Color of the FINAL label on completed games |
| **Period / Time Color** | Color of the game time/period text |
| **Stream Layout** | Grid (multi-column), Fullscreen (one wide column), or Ticker (compact rows) |
| **Theme Override** | Use a different color theme just for this scoreboard |
| **Stream Audio** | None, built-in playlist music, or a custom audio stream URL |

Each setting can also inherit from **Default Stream Settings** (the global defaults) by leaving the "Use Default" toggles on.

---

### Previewing Your Scoreboard

The editor Step 3 (Display & Layout) includes a **Live Preview** panel — a floating card stack anchored to the right side of the screen that updates in real time as you adjust typography, scale, colors, and section header settings. The preview renders representative cards based on the sports enabled in Step 1.

**Resizing the preview panel:**
- Drag the **⤡** grip in the top-left corner of the panel to resize both width and height simultaneously
- The panel size is saved per-browser so it persists across editor sessions
- The panel automatically counter-scales when the UI Text Size setting (S / M / L / XL) is changed, so it stays at a readable size regardless of zoom level

To preview the full scoreboard stream output:
1. Save the scoreboard
2. In **My Scoreboards**, find the scoreboard card and click **Preview** (opens the stream page in a new tab)
3. The stream page shows the live scoreboard exactly as it appears in the HLS output

---

### Default Stream Settings

**Default Stream Settings** (found in the sidebar under Settings) let you set values that apply to all scoreboards unless individually overridden. These control **stream output only** — the video that viewers see.

Sections:
- **Theme** — color scheme applied to stream cards (Dark Blue, Carbon, Dark Red, Dark Green, Light, or custom)
- **Font Sizes** — default Abbreviation, Score, and Team Name sizes for game cards
- **Colors** — default Team Name color on game cards
- **Card Size** — default Card Scale, Rotation Timer, Logo Size, and Layout (Grid/Fullscreen/Ticker)

As you adjust sliders and pickers, two live preview cards update in real time at the bottom of the panel so you can see the effect immediately.

Click **Save Defaults** to apply. Any scoreboard using defaults will pick up the new values immediately.

> **Note:** Default Stream Settings control the *stream output* appearance. To change the *app UI* appearance (the settings pages, sidebar, and navigation), use **System Theme** instead.

---

### Themes

ScoreStream includes 5 built-in themes. The default is **Dark Blue** (`#00d4ff` accent).

| Theme | Accent Color | Style |
|---|---|---|
| **Dark Blue** *(default)* | `#00d4ff` | Dark navy with cyan accent |
| **Carbon** | `#e0e0e0` | Pure black with silver/white accent |
| **Dark Red** | `#ff4466` | Deep crimson with red accent |
| **Dark Green** | `#00e676` | Dark forest with neon green accent |
| **Light** | `#0066cc` | Light grey/white with blue accent |

**Applying a theme globally:**
1. Go to **Default Stream Settings** → Theme section
2. Click any swatch to apply it instantly
3. Save Defaults

**Creating a custom theme:**
1. Click **Edit Current** to open the customizer for the active theme, or **+ New Custom Theme** to start from scratch
2. Adjust the 9 color pickers (backgrounds, borders, accents, text)
3. Enter a name and click **Save Theme** — the theme is saved and appears in the swatch row

**Per-scoreboard theme override:**
In editor Step 3 (Display & Layout), the **Theme Override** section lets you choose a different theme for just that scoreboard. Select **Use Global** to follow the global default.

Custom themes are stored in your display defaults and are available across all scoreboards.

---

### System Theme (App UI)

**System Theme** controls the appearance of the ScoreStream **interface itself** — the settings pages, sidebar, navigation buttons, section headers, and all UI controls. This is completely separate from stream themes.

| Setting | Description |
|---|---|
| **Background Primary** | Main page background color |
| **Background Secondary** | Sidebar and secondary panel background |
| **Card Background** | Background of setting cards and input groups |
| **Border / Border Highlight** | Border colors for panels, inputs, and dividers |
| **Accent** | Primary accent color (active buttons, links, highlights) |
| **Accent Secondary** | Secondary accent (warnings, secondary highlights) |
| **Accent Green / Red** | Status colors (success/live indicators, error/danger) |
| **Text Primary / Secondary / Dim** | Text brightness levels throughout the UI |
| **Global UI Scale** | Scales all app UI globally — settings pages, navigation, and the main scoreboard sidebar (12–20px) |
| **Scoreboard Sidebar Scale** | Adds extra zoom to the main scoreboard's left sidebar only (sport toggles, section labels, refresh button) — stacks on top of Global UI Scale (0.8×–1.6×) |
| **Topbar Meta Size** | Font size of "Live: # / Total: # / clock" in the top bar (9–22px) |

**Presets:** Five built-in presets (Dark Blue, Carbon, Dark Green, Dark Red, Light) let you quick-apply a full color scheme and customize from there.

**Saving:** Slider drags **auto-save** to local storage on release AND push to the server database (debounced ~600ms). The active stream's puppeteer browser also re-fetches the system theme on every config refresh (every 30s), so changes propagate to live streams without restarting any container. The **Save System Theme** button is now optional — it just forces an immediate server push.

**Resetting:** Click **Reset** to return to the default Dark Blue theme.

> **Key distinction:** System Theme affects only what you see in your browser. Stream viewers never see these colors — they see the stream theme set in *Default Stream Settings* or per-scoreboard overrides.

---

### Per-Sport Display Settings (Headshots)

Individual-sport cards (PGA, F1, NASCAR Cup, NASCAR NOAPS, NASCAR Trucks, ATP, WTA) render player or driver headshot circles. The size of those circles is **per-sport** and lives in two places depending on which view you're configuring:

| Where | Affects | Saved to |
|---|---|---|
| **Settings → Sports Library** → expand a sport row (▸) → Headshot Size | The main scoreboard.html browsing view (the sidebar live-scores cards) | Browser local storage (`state.display.sportHeadshots[sportId]`) |
| **Scoreboard editor → Step 3 (Display & Layout) → Per-Sport Display & Data** → headshot slider in each sport's section | A specific scoreboard's HLS stream output | Database (`motor_config.{sport}_headshot_size`) |

**Why two places?** The Sports Library setting controls the *browsing view* you see in your browser when you have ScoreStream open. The scoreboard editor setting controls what *viewers of a Dispatcharr channel see* when they tune into that scoreboard's stream. They're independent — you can have a 32px headshot for browsing and a 28px headshot in the actual stream.

**Priority for stream output:** Each card builder reads its sport's headshot size in this order:
1. Stream mode → the scoreboard's `motor_config.{sport}_headshot_size`
2. Editor preview → `_editorDraft.motor_config.{sport}_headshot_size` (live preview while editing)
3. Browsing mode → `state.display.sportHeadshots[sportId]`
4. Default → 28px

The size is set on each card element via `--card-headshot-size`, so multiple sports on the same page can have different sizes.

**Sports Library row layout:**
- Click the **▸** chevron next to PGA, F1, any NASCAR series, ATP, or WTA to expand
- Drag the Headshot Size slider (range 20–64px)
- Changes apply instantly to the main scoreboard.html view
- Tennis ATP and WTA are separate rows so you can size them independently

**Scoreboard editor location:**
- Open the scoreboard, navigate to **Step 3 — Display & Layout**
- Scroll to **🏁 Per-Sport Display & Data**
- Each enabled motor/tennis sport has its own labeled card with options including a **🖼️ Headshot Size** slider
- For tennis, the size applies to both ATP and WTA on that scoreboard (one shared `tennis_headshot_size`)

---

### Sharing Scoreboards (Export / Import JSON)

You can export any scoreboard's complete configuration to a JSON file and share it with other ScoreStream users. The export includes the scoreboard's name, sports, teams, display settings, theme, motor config, and audio settings — but **excludes** install-specific things like the Dispatcharr channel binding, IDs, and slug.

**To export a scoreboard:**
1. Go to **My Scoreboards**
2. Find the scoreboard card and click **📤 Export JSON**
3. A `.json` file downloads to your computer (named after the scoreboard's slug)
4. Share the file directly, paste its contents in chat, post to a forum, etc.

**To import a scoreboard:**
1. Go to **My Scoreboards**
2. Click **📥 Import from JSON** below the scoreboard list
3. Choose **OK** to upload a `.json` file, or **Cancel** to paste JSON text
4. Confirm or change the scoreboard name (auto-suffixed with "(Imported)" if a name collision exists)
5. The imported scoreboard appears in your list — review it in the editor and click **🚀 Push to Dispatcharr** when ready

> **Validation:** The importer rejects anything that isn't a valid ScoreStream scoreboard export (`scorestream_export_version: 1, type: "scoreboard"`). Invalid JSON or missing fields show a clear error.

> **Channel binding excluded:** Imports never bring over Dispatcharr channel IDs, channel numbers, group IDs, or stream profile IDs. You configure those fresh on your install via the Push to Dispatcharr wizard.

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

**Built-in music:**
The stream container ships with 6 royalty-free instrumental tracks that play when you select the **Built-in Music** option. No upload or configuration is needed. Full attribution for each track is included in [`stream/audio/ATTRIBUTION.txt`](./stream/audio/ATTRIBUTION.txt). Tracks are sourced from [Bensound](https://www.bensound.com), [Uppbeat](https://uppbeat.io), and [freetouse.com](https://freetouse.com/music).

---

### Assigning Audio to a Scoreboard

In editor Step 3 (Display & Layout), scroll to **Stream Audio**:

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

**Live streams update automatically:**
If the stream is already playing in VLC, an IPTV player, or any HLS-compatible app, you do not need to do anything after saving changes. The stream re-renders every 30 seconds in the background — new scores, typography changes, theme changes, and layout changes all appear on the live feed within that window. The player continues playing uninterrupted; it simply receives updated HLS segments as they are generated.

**If you delete a scoreboard:** The channel remains in Dispatcharr. Remove it manually in Dispatcharr's channel list if you no longer want it.

---

## Ticker Overlay

The Ticker Overlay feature overlays a live scrolling score ticker onto any channel that Dispatcharr is streaming. It works by injecting an ffmpeg `drawtext` filter into a copy of the channel's stream profile, re-encoding the video on the fly, and reading score text from a shared file that ScoreStream writes continuously.

### How It Works

1. You select a Dispatcharr channel, choose your sports sources, and configure appearance in ScoreStream's **Ticker Overlay** settings panel
2. When you click **Enable Ticker**, ScoreStream:
   - Reads the channel's current stream profile from Dispatcharr via API
   - Creates a new profile named `"[Original Name] (Ticker)"` with a modified ffmpeg command that injects a scrolling `drawtext` overlay
   - Assigns that new profile to the channel via the Dispatcharr API
   - Begins writing live score text to `/ticker/scores.txt` every 30 seconds
3. ffmpeg in Dispatcharr reads the text file on every frame (`reload=1`) — no stream restart needed when scores update
4. When you click **Disable Ticker**, ScoreStream restores the original profile ID and deletes the ticker copy

> **The original stream profile is never modified.** ScoreStream creates a copy and assigns it. On disable, the original is restored cleanly.

> **Re-encoding required:** Adding a drawtext overlay requires re-encoding the video. The ticker uses `-c:v libx264 -preset ultrafast -tune zerolatency` which adds minimal latency. Profiles already using `-c:v copy` (pass-through) are automatically switched to encode mode while the ticker is active.

> **Live data only:** The ticker only shows games or races that are currently in progress or finished today. Games from prior days are excluded. If there is nothing live or completed today for a selected sport, that sport is silently omitted from the ticker.

---

### Shared Volume Setup

The ticker text file (`/ticker/scores.txt`) must be accessible to both the ScoreStream stream container (writer) and Dispatcharr's ffmpeg process (reader). This requires a shared Docker named volume.

> **This setup is required once.** After the volume is created and both stacks are updated, the ticker feature will work for all future use.

---

**Step 1 — Create the volume on your host (once)**

Run this from the host machine (SSH or Portainer console), not inside any container:

```bash
docker volume create scorestream_ticker
```

Or in Portainer: **Volumes → Add volume**, name it `scorestream_ticker`, leave everything else default, click **Create**.

---

**Step 2 — Add the volume to your ScoreStream stack**

In your ScoreStream `docker-compose.yml`, add the volume mount to the `scorestream-stream` service and declare it at the bottom:

```yaml
services:
  scorestream-stream:
    # ... existing config ...
    volumes:
      - scorestream_ticker:/ticker    # add this line

volumes:
  scorestream_ticker:
    external: true                    # add this block
```

---

**Step 3 — Add the volume to your Dispatcharr stack**

In your Dispatcharr `docker-compose.yml`, add the same volume to the Dispatcharr service:

```yaml
services:
  dispatcharr:
    # ... existing config ...
    volumes:
      - scorestream_ticker:/ticker    # add this line

volumes:
  scorestream_ticker:
    external: true                    # add this block
```

> **Same stack:** If ScoreStream and Dispatcharr are in the same `docker-compose.yml`, declare `scorestream_ticker` as a regular named volume (no `external: true`) once under `volumes:` and mount it in both services.

---

**Step 4 — Redeploy both stacks**

In Portainer, click **Update the stack** (or **Stop → Remove → Redeploy**) for both stacks. The containers must be recreated — a simple restart does not apply new volume mounts.

**Verify the volume is mounted:**

```bash
docker exec scorestream-stream ls /ticker
docker exec dispatcharr ls /ticker
```

Both should return an empty directory with no error. If you get `No such file or directory`, the container was not recreated with the new mount.

---

### Using the Ticker Overlay UI

Navigate to **Ticker Overlay** in the ScoreStream settings sidebar.

**Active Ticker Status Panel** — shows all active tickers with channel names, profile IDs, and start times. Each active ticker has a **KILL** button to disable it individually. When multiple tickers are active, a **KILL ALL** button appears to disable them all at once.

**1. Select a Dispatcharr channel**

Choose the channel from the dropdown. ScoreStream fetches the channel's current stream profile from Dispatcharr and validates it:
- **"✓ FFmpeg ready"** — profile is compatible with the ticker overlay
- **"⚠ not an FFmpeg profile"** — ticker requires an FFmpeg stream profile; change the channel's profile in Dispatcharr
- **"[locked]"** — profile cannot be modified; duplicate it in Dispatcharr first

**2. Choose ticker sources**

Sports are organized into groups: Pro Leagues, Motorsport, Tennis, International Soccer, NCAA Men, NCAA Women.

- Check the sports you want to include in the ticker
- Toggle the **FAVS** pill button on any sport to restrict that sport's ticker output to only games or races involving your favorited teams (favorites are set per sport in each scoreboard's team config)

**Import from scoreboard shortcut:** Use the **Import from scoreboard** dropdown to pre-fill the sport checkboxes from an existing scoreboard's enabled sports. This is a one-time copy — you can modify the selection afterward.

**3. Appearance**

| Setting | Description |
|---|---|
| **Position** | Bottom (default) or Top of the video frame |
| **Font size** | 16–48px. Recommended: 24–32px for 1080p streams |
| **Scroll speed** | 0 = static text, 1–400 px/s for scrolling marquee. Recommended: 100–200 px/s |
| **Background opacity** | Darkness of the black bar behind the ticker text (0–100%) |

**4. Enable / Disable**

- **ENABLE TICKER** — saves config, creates the ticker stream profile in Dispatcharr, and assigns it to the channel. ScoreStream begins writing `/ticker/scores.txt` immediately. Re-enabling on a channel that already has a ticker safely replaces the existing filter (no stacking).
- **DISABLE TICKER** — restores the channel's original stream profile and deletes the ticker copy.
- **KILL (per-ticker)** — in the status panel, instantly disables a single active ticker and restores its original profile.
- **KILL ALL** — disables every active ticker across all channels in one click.
- **RESET TO DEFAULTS** — restores appearance settings (28px font, 150 px/s scroll, 75% opacity, bottom position) and clears all sport selections.

> After enabling, **restart the channel in Dispatcharr** to pick up the new stream profile. Existing ffmpeg processes use the old profile until restarted.

> Score text updates every 30 seconds automatically. No stream restart is needed when scores change — ffmpeg re-reads the file on each frame.

**⚠ Performance Note — GPU Encoding Recommended**

The ticker overlay requires FFmpeg to decode and re-encode the video stream in real time. This works with CPU-only transcoding (`libx264 ultrafast`), but results may not be optimal — especially on lower-powered hardware or with high-resolution source streams. For the best experience, a system with GPU hardware encoding (NVENC, VAAPI, or QSV) is recommended. Without a GPU, you may experience intermittent stuttering or frame drops during playback.

---

## Stream Layout and Pagination

### Page Composition

ScoreStream organizes stream cards into **pages** — full-canvas views that rotate on the configured timer. Each page is filled dynamically based on the available resolution and card sizes.

**Key rules:**
- Live/upcoming games and final scores for the same sport are always on **separate pages**. A page showing live NBA games will never also contain final MLB scores.
- When a sport has more games than fit on one page, that sport continues onto additional pages automatically (e.g. MLB Finals Apr 12 · Page 1, MLB Finals Apr 12 · Page 2)
- Motor sports (F1, NASCAR, PGA), tennis rankings, and other individual-sport card types each get their own dedicated page(s)
- Favorited teams are pinned to a "My Teams" section that appears first in the rotation

### Team Logo Rendering

Team logos sourced from ESPN are PNG images with transparent backgrounds, designed to display on white/light backgrounds. ScoreStream renders them on a **white box with a team-color border** so every logo is clearly legible against the dark stream background — including teams whose primary color closely matches their own logo (e.g. Yankees navy, Cardinals red).

If a logo fails to load, the box falls back to a solid team-color background with the team abbreviation in white text.

### Favorited Player Stars

Individual-sport cards (PGA, F1, NASCAR, ATP, WTA) show a gold star next to favorited players/drivers. The star is rendered as an inline SVG polygon, not a Unicode glyph, to ensure correct rendering in the Chromium-based HLS stream renderer on all systems.

---

## Supported Sports

### Scoreboard (stream cards)

| League | Type | Live Scores | Schedules | Final Scores |
|---|---|---|---|---|
| NFL | Team | ✓ | ✓ | ✓ |
| NBA | Team | ✓ | ✓ | ✓ |
| MLB | Team | ✓ | ✓ | ✓ |
| NHL | Team | ✓ | ✓ | ✓ |
| WNBA | Team | ✓ | ✓ | ✓ |
| CFL | Team | ✓ | ✓ | ✓ |
| XFL | Team | ✓ | ✓ | ✓ |
| UFL | Team | ✓ | ✓ | ✓ |
| MLS | Team | ✓ | ✓ | ✓ |
| NWSL | Team | ✓ | ✓ | ✓ |
| Premier League | Team | ✓ | ✓ | ✓ |
| Champions League | Team | ✓ | ✓ | ✓ |
| La Liga | Team | ✓ | ✓ | ✓ |
| Bundesliga | Team | ✓ | ✓ | ✓ |
| Serie A | Team | ✓ | ✓ | ✓ |
| Ligue 1 | Team | ✓ | ✓ | ✓ |
| NCAA Football | Team | ✓ | ✓ | ✓ |
| NCAA Men's Basketball | Team | ✓ | ✓ | ✓ |
| NCAA Women's Basketball | Team | ✓ | ✓ | ✓ |
| NCAA Baseball | Team | ✓ | ✓ | ✓ |
| NCAA Softball | Team | ✓ | ✓ | ✓ |
| NCAA Women's Volleyball | Team | ✓ | ✓ | ✓ |
| NCAA Women's Lacrosse | Team | ✓ | ✓ | ✓ |
| PGA Tour | Individual | ✓ | ✓ | ✓ |
| Formula 1 | Individual | ✓ | ✓ | ✓ |
| NASCAR Cup Series | Individual | ✓ | ✓ | ✓ |
| NASCAR O'Reilly Auto Parts Series | Individual | ✓ | ✓ | ✓ |
| NASCAR Craftsman Truck Series | Individual | ✓ | ✓ | ✓ |
| ATP Tour | Individual | — | ✓ | ✓ |
| WTA Tour | Individual | — | ✓ | ✓ |

### Ticker Overlay

The ticker overlay supports all sports in the table above. Data sources:

| Sport group | Live data source |
|---|---|
| NFL, NBA, MLB, NHL, WNBA, CFL, XFL, UFL, MLS, NWSL | ESPN Scoreboard API (live + today's finals) |
| Premier League, Champions League, La Liga, Bundesliga, Serie A, Ligue 1 | ESPN Scoreboard API (live + today's finals) |
| ATP, WTA | ESPN Scoreboard API (live + today's finals) |
| NCAA Football, Basketball (M/W), Baseball, Softball, Volleyball, Lacrosse | ESPN Scoreboard API (live + today's finals) |
| NASCAR Cup | NASCAR live feed API (live positions + lap count); falls back to motor cache if race was today |
| Formula 1 | Motor cache (race results — shown only on race day) |
| PGA Tour | Motor cache (leaderboard — shown only when a tournament is actively in progress with scores) |

---

## Player Headshots

ScoreStream displays player headshots on individual-sport cards (PGA, F1, NASCAR, ATP, WTA) when the **Player Headshots** toggle is enabled in the scoreboard editor's display settings.

### How headshots are sourced

A GitHub Actions workflow (`update-motor-cache.yml`) runs on a schedule and populates the headshot cache on the `data` branch. The API container seeds from this cache on startup.

| Sport | Source | Coverage |
|---|---|---|
| PGA Tour | ESPN CDN (`a.espncdn.com/i/headshots/golf/players/full/{id}.png`) | ~300 players via scoreboard IDs + ESPN name search backfill |
| ATP Tour | ESPN CDN (`a.espncdn.com/i/headshots/tennis/players/full/{id}.png`) | 150 ranked players (~80% have images) |
| WTA Tour | ESPN CDN (same pattern as ATP) | 150 ranked players (~75% have images) |
| NASCAR | Fox Sports CDN (scraped from Cup/Xfinity/Truck standings pages) | ~100 active drivers |
| Formula 1 | formula1.com (official driver images with Cloudinary face crop) | All 22 current drivers |

### Lookup behavior

- **PGA:** Looks up by ESPN athlete ID first, then falls back to **player name** matching (needed because historical tournament data often lacks athlete IDs)
- **NASCAR / F1:** Looks up by **full name**, then **last name** only (handles abbreviated names like "C. Elliott" → "Elliott" → matches "Chase Elliott")
- **ATP / WTA:** Looks up by ESPN athlete ID from the live scoreboard data
- All headshot `<img>` tags include `onerror="this.style.display='none'"` so missing images are hidden silently

### Refreshing the cache

The workflow runs automatically on schedule. To manually refresh:
1. Go to GitHub → Actions → **Update Motor Cache Data** → Run workflow
2. After completion, restart the `scorestream-api` container to pick up the new data

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
- The scoreboard has no sports enabled. Edit it and enable at least one sport in Step 1 (Sports & Leagues).

**Dispatcharr connection fails:**
- Verify the API token is correct (Dispatcharr → Profile → API Keys)
- Ensure the Dispatcharr URL is reachable from the ScoreStream container (use LAN IP, not localhost)

**Changes not appearing on stream:**
- The stream refreshes every 30 seconds. Wait up to 30 seconds after saving.
- For display setting changes (fonts, colors, scale), the stream container picks these up on next page render

**Text in stream looks pixelated, grainy, or fuzzy (especially when enlarged):**
- Set `STREAM_QUALITY=balanced` or `STREAM_QUALITY=high` in your stream container env. Default `balanced` already gives noticeably crisper text than the old `low` setting at the same CPU cost.
- For maximum text clarity, also set `STREAM_DPR=2` (renders at 4K then downsamples). Uses ~4x browser RAM, recommended only for 1-2 concurrent streams.
- See [Stream Quality Tiers](#stream-quality-tiers) for details.
- Restart the `scorestream-stream` container after changing quality env vars: `docker restart scorestream-stream`

**Stream stuttering, buffering, or high CPU with multiple streams:**
- Set `STREAM_QUALITY=low` to drop back to original behavior (~800 kbps, lowest CPU).
- Confirm `STREAM_FPS=1` (the default). Higher FPS values multiply CPU cost — 30 FPS uses ~30x more CPU than 1 FPS.
- If using `STREAM_DPR=2`, drop back to `STREAM_DPR=1` to save browser memory.

**Ticker overlay not appearing after enabling:**
- Restart the channel in Dispatcharr — existing ffmpeg processes use the old stream profile until restarted
- Confirm the `scorestream_ticker` volume is mounted in both containers: `docker exec scorestream-stream ls /ticker` and `docker exec dispatcharr ls /ticker` — both should return an empty directory or `scores.txt` with no error
- Check that at least one enabled sport has live or today's completed data — the ticker writes an empty file if no data is available
- Check stream container logs: `docker logs scorestream-stream`

**Ticker text file not updating:**
- The stream container writes `/ticker/scores.txt` every 30 seconds when a ticker is active
- Verify a ticker is active: check the **Ticker Overlay** status bar in the UI
- The file is only written when there is at least one active ticker profile in the database

**Updating ScoreStream:**
```bash
docker compose pull
docker compose up -d
```

---

## See Also

- [CHANGELOG.md](./CHANGELOG.md) — version history
- [Dispatcharr](https://github.com/Dispatcharr/Dispatcharr) — the IPTV manager this integrates with
