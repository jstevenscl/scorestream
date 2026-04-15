# Changelog

All notable changes to ScoreStreamArr are documented here.

Versioning follows [Semantic Versioning](https://semver.org/):
- `MAJOR.MINOR.PATCH` for stable releases (e.g. `v1.0.0`)
- `-beta` suffix for pre-release builds (e.g. `v0.2.0-beta`)

Docker images are published to [GitHub Container Registry](https://ghcr.io):
```
ghcr.io/OWNER/scorestreamarr-pro-api:latest    # stable
ghcr.io/OWNER/scorestreamarr-pro-api:beta      # bleeding edge
ghcr.io/OWNER/scorestreamarr-pro-api:v0.2.0-beta  # pinned version
```

---

## [Unreleased] — 2026-04-14

### Fixed
- **NASCAR driver names** — standings for NOAPS (O'Reilly Auto Parts Series) and Craftsman Truck Series were showing abbreviated names (T. Reddick, J. Allgaier). Root cause: `cf.nascar.com/cacher/` standings endpoints return 403 from GitHub Actions CI. All three NASCAR series (Cup, NOAPS, Trucks) now scrape full driver names, car numbers, points, wins, and headshots directly from Fox Sports standings pages.
- **NASCAR headshot cache format** — the motor cache workflow was writing a mixed-key format that caused the frontend to silently fall back to the legacy name-map path. The nascar-drivers cache now always writes clean slug-keyed entries.
- **Tennis headshot 404 errors** — ESPN CDN (`espncdn.com/i/headshots/tennis/players/full/{id}.png`) does not host tennis player images and returned 404 for every request. Removed the CDN fallback from the motor cache workflow; tennis players are now fetched via ESPN Core API (which provides limited coverage — ESPN does not publish headshots for most tennis players).
- **`/api/stream/status` returning 404** — added a proxy route in `api/app.py` that forwards `GET /stream/status` to the stream manager. This endpoint is polled by the frontend to show live stream badges; it was silently failing on every page load.

### Changed
- Motor cache reseed no longer requires a container restart — call `POST /api/motor/reseed` to force the API to re-read the data branch into SQLite immediately.

---

## [v0.2.0-beta] — 2026-02-19

### Added
- **Channel numbering modes** — `auto` (sequential from base number) or `manual` (per-channel override in `config.json`)
- **Channel Profiles integration** — assign ScoreStreamArr channels to Dispatcharr Channel Profiles:
  - `all` — added to every profile (default, backward-compatible)
  - `none` — no profile assignment
  - `specific` — list explicit profile IDs; invalid IDs are warned and skipped
- **`config/config.json`** — persistent config file mounted into the API container; controls numbering mode, per-channel numbers/names/enabled flags, group name, and profile assignment; reloaded automatically on each 6-hour re-sync
- **GitHub Actions CI/CD** — two workflows:
  - `release.yml` — triggered by version tags (`v*.*.*`); builds multi-arch images (amd64 + arm64), pushes to ghcr.io with `:latest`/`:beta`/`:vX.Y.Z` tags, creates GitHub Release with notes
  - `beta.yml` — triggered on every push to `main`; auto-publishes `:beta` and `:beta-<sha>` images
- **nginx Dockerfile** — web service now has its own Dockerfile; scoreboard HTML baked in at build time with optional runtime volume override
- **Per-channel `enabled` flag** — individual channels can be disabled in `config.json` without removing them from the config

### Changed
- `docker-compose.yml` now pulls images from `ghcr.io` by default; local build retained as commented fallback
- `SCORESTREAMARR_TAG` env var controls which image tag to use (`latest`, `beta`, or pinned version)
- `GITHUB_OWNER` env var sets the ghcr.io namespace
- API container now mounts `./config` volume for persistent `config.json`
- Token refresh proactively runs every minute loop; refresh + re-auth fallback chain unchanged

### Fixed
- Config reload now happens on every 6-hour re-sync (not just startup)
- Profile ID validation warns and skips invalid IDs rather than crashing

---

## [v0.1.0-beta] — 2026-02-18 — Initial Release

### Added
- **Live sports scoreboard** — headless Chromium renders scoreboard HTML; FFmpeg encodes to HLS
- **7 channel variants** — All Sports, NFL, NBA, MLB, NHL, NCAA Basketball, NCAA Baseball
- **Dispatcharr auto-registration** — Python API authenticates via JWT, creates group + streams + channels on startup
- **6-hour re-sync** — channels re-registered automatically to survive Dispatcharr restarts
- **Docker Compose stack** — 4 containers: web (nginx), renderer (Chrome), ffmpeg, api
- **Named pipe architecture** — renderer feeds FFmpeg directly without temp files
- **HLS multi-output** — single FFmpeg process splits one video source to 7 simultaneous HLS streams
- **Mock ESPN data** — scoreboard falls back to rich mock data when ESPN API is blocked (CORS/sandbox)
- **Team browser** — sidebar modal with 2-column team grid, league tabs, live search, star toggles
- **Season awareness** — off-season dimming, preseason/postseason badges, empty off-season boards hidden
- **Per-sport ended games filters** — All / ⭐ First / ⭐ Only per league
- **3 layout modes** — Full, Grid, Ticker
- **Global filters** — All / Live / ⭐ Mine
- **Auto-refresh** — 60-second data refresh with live clock
- **Favorites system** — teams and leagues, persisted to localStorage with in-memory fallback

---

## Roadmap

### Planned for v0.3.0-beta
- Web-based config UI — edit channel numbers and profile assignment without touching JSON
- Off-season placeholder screens — custom "Season starts in X days" screen per sport
- Per-sport renderer instances — true isolated video per channel (opt-in, multi-Chrome mode)
- EPG injection — push XMLTV program guide data to Dispatcharr alongside channels

### Planned for v1.0.0 (stable)
- Full installation wizard
- Automated update detection and pull
- Health dashboard endpoint
- Unraid Community App template
- Portainer App template
