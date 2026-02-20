# NCAA Team Data — Architecture Decision Record
**Date:** 2026-02-20  
**Status:** Approved — ready to implement  
**Session:** This document exists so if the conversation disconnects, all decisions are preserved and we do not repeat the design discussion.

## Problem Statement
The original NCAA_NAMES object in scoreboard.html was:
- Hardcoded as a JavaScript constant inside the HTML file (not updatable without a code change)
- Only 165 entries — a fraction of even D-I alone (NCAA governs 1,100+ institutions)
- Stored as a flat object with no gender separation (men's and women's programs collapsed)
- No division information (D-I, D-II, D-III)
- No way for a user to add a school or fix a name change without redeployment

## Approved Solution

### Data Source
Pull from all 5 ESPN API endpoints on first container startup:
  college-football/teams?limit=900          sport_id: ncaafb,   gender: mens
  mens-college-basketball/teams?limit=900   sport_id: ncaamb,   gender: mens
  womens-college-basketball/teams?limit=900 sport_id: ncaawb,   gender: womens
  college-baseball/teams?limit=900          sport_id: ncaabase, gender: mens
  college-softball/teams?limit=900          sport_id: ncaasb,   gender: womens

All 5 endpoints fetched and merged by ESPN abbreviation because no single endpoint
gives a complete picture — some schools have no football, some no basketball, etc.
Expected result: ~1,100 unique schools, each with only the programs they actually field.

## Database Schema

### ncaa_schools (one row per institution)
  id           INTEGER PK
  espn_abbr    TEXT UNIQUE NOT NULL   e.g. "TEX", "TENN"
  full_name    TEXT NOT NULL          e.g. "University of Texas"
  location     TEXT                   e.g. "Texas"
  color        TEXT                   primary brand color hex
  alt_color    TEXT                   secondary color hex
  logo_url     TEXT                   ESPN CDN logo URL
  espn_id      TEXT                   ESPN numeric school ID
  slug         TEXT                   ESPN URL slug
  sync_source  TEXT DEFAULT 'fallback' — 'espn', 'fallback', or 'manual'
  last_synced  TEXT
  created_at   TEXT
  updated_at   TEXT

### ncaa_programs (one row per sport program at a school)
  id           INTEGER PK
  school_id    INTEGER FK → ncaa_schools.id
  sport_id     TEXT NOT NULL   'ncaafb','ncaamb','ncaawb','ncaabase','ncaasb'
  gender       TEXT NOT NULL   'mens' or 'womens'
  division     TEXT            'd1', 'd2', 'd3', 'unknown'
  nick         TEXT            "Longhorns", "Lady Volunteers"
  display_name TEXT            "Texas Longhorns"
  short_name   TEXT            "Texas"
  espn_team_id TEXT            ESPN numeric team ID for this program
  last_synced  TEXT
  created_at   TEXT
  updated_at   TEXT
  UNIQUE(school_id, sport_id, division)

UNIQUE includes division (not just school+sport) to handle rare reclassification
periods where a school fields programs in multiple divisions simultaneously.

## Key Design Decisions
- Gender filtered at DB query level: womens programs CANNOT appear in mens tabs
- Division default: D-I (user can toggle All / D-I / D-II / D-III pills)
- Unique constraint: school+sport+division (handles reclassification edge case)
- ESPN unreachable at startup: seed 165-entry fallback, flag espn_sync_status=pending
- Resync: manual only via Coming Soon button (rosters change infrequently)
- CRUD: full endpoints built, UI gated behind NCAA_CRUD_ENABLED=false
- Search: server-side via ?q= param (scales to 1,100 schools)
- Label mode: ABBR (TEX) or NICKNAME (Longhorns) toggle, persisted in localStorage

## Sync Logic
startup_sync() runs in background thread so Flask starts immediately:
  1. Check espn_sync_status in settings table
     - 'complete' AND last_synced < 30 days: skip
     - anything else: attempt ESPN sync
  2. attempt_espn_sync():
     - set status = 'running'
     - fetch all 5 endpoints (8s timeout, limit=900)
     - upsert ncaa_schools ON CONFLICT(espn_abbr) DO UPDATE
     - upsert ncaa_programs ON CONFLICT(school_id, sport_id, division) DO UPDATE
     - set status = 'complete', record last_synced
  3. On failure:
     - set status = 'pending' (retried on next startup)
     - if tables empty: seed FALLBACK_SCHOOLS (165 entries)
     - app continues with fallback data

## API Endpoints
Live:
  GET  /api/ncaa/schools               ?q=search&division=d1
  GET  /api/ncaa/schools/<id>          single school + all programs
  GET  /api/ncaa/programs              ?sport=ncaamb&gender=mens&division=d1&q=search
  GET  /api/ncaa/teams                 legacy compat
  POST /api/ncaa/sync                  trigger ESPN resync
  GET  /api/ncaa/sync/status           {status, last_synced, school_count, program_count}

Coming Soon (NCAA_CRUD_ENABLED=false):
  POST/PUT/DELETE /api/ncaa/schools/<id>
  POST/PUT/DELETE /api/ncaa/programs/<id>

## Frontend Changes
- Division pills: All | D-I | D-II | D-III, default D-I, persisted as ss_tb_division
- Tab query: GET /api/ncaa/programs?sport=ncaamb&gender=mens&division=d1
- Label toggle: ABBR / NICKNAME (already built, persisted as ss_tb_label)
- loadNcaaFromApi() updated to call /api/ncaa/programs
- Search via ?q= is server-side, not client-side filtering

## Files Modified
  api/app.py       — new tables, ESPN sync, new endpoints
  scoreboard.html  — division pills, updated queries, updated loadNcaaFromApi

## Testing Plan
  1. init_db() on fresh SQLite file — no errors
  2. ESPN parse functions against mocked responses (real ESPN JSON structure)
  3. Double-sync: run twice, verify row counts identical
  4. All GET endpoints return correct JSON
  5. Division filter returns only correct division
  6. Gender filter: ncaawb never returns gender=mens rows
  7. Fallback: mock ESPN unreachable, verify seed + status=pending
  8. Sync status transitions: pending -> running -> complete/failed
  9. Legacy /api/ncaa/teams still works
  10. Search "Lady Vols" returns only Tennessee womens programs

NOTE: Actual ESPN network calls cannot be tested in build container (no outbound
internet). Lines needing live verification after deploy marked: # VERIFY: live ESPN
