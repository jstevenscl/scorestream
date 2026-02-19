"""
ScoreStream Pro — Dispatcharr Integration API
==============================================
Version: 0.2.0-beta

Features:
  - Reads config.json for channel numbering (auto or manual per-channel)
  - Fetches available Channel Profiles from Dispatcharr
  - Assigns channels to: all profiles / no profiles / specific profile IDs
  - JWT auth with automatic token refresh
  - Re-syncs every 6 hours to handle Dispatcharr restarts
"""

import os
import sys
import json
import time
import logging
import requests
from pathlib import Path
from datetime import datetime, timedelta

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [api] %(levelname)s %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────────────
CONFIG_PATH = Path(os.environ.get('CONFIG_PATH', '/config/config.json'))

# ── Environment ────────────────────────────────────────────────────────────────
DISPATCHARR_URL  = os.environ['DISPATCHARR_URL'].rstrip('/')
DISPATCHARR_USER = os.environ['DISPATCHARR_USER']
DISPATCHARR_PASS = os.environ['DISPATCHARR_PASS']
STREAM_BASE_URL  = os.environ['STREAM_BASE_URL'].rstrip('/')

# ── Channel definitions (canonical order) ─────────────────────────────────────
CHANNEL_DEFS = [
    ('all',      '📺', 'ScoreStream — All Sports'),
    ('nfl',      '🏈', 'ScoreStream — NFL'),
    ('nba',      '🏀', 'ScoreStream — NBA'),
    ('mlb',      '⚾', 'ScoreStream — MLB'),
    ('nhl',      '🏒', 'ScoreStream — NHL'),
    ('ncaab',    '🏀', 'ScoreStream — NCAA Basketball'),
    ('ncaabase', '⚾', 'ScoreStream — NCAA Baseball'),
]


# ── Config loader ──────────────────────────────────────────────────────────────

def load_config():
    """Load config.json, falling back to safe defaults if missing or malformed."""
    base_number = int(os.environ.get('DISPATCHARR_CHANNEL_START', 900))
    defaults = {
        'channel_numbering': {
            'mode': 'auto',
            'base_number': base_number,
            'channels': {
                ch_id: {'name': name, 'number': base_number + i, 'enabled': True}
                for i, (ch_id, _, name) in enumerate(CHANNEL_DEFS)
            },
        },
        'dispatcharr': {
            'group_name': os.environ.get('DISPATCHARR_GROUP', 'ScoreStream'),
            'channel_profiles': {
                'mode': 'all',
                'profile_ids': [],
            },
        },
    }

    if not CONFIG_PATH.exists():
        log.warning(f"Config not found at {CONFIG_PATH} — using defaults")
        return defaults

    try:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
        log.info(f"Loaded config from {CONFIG_PATH}")
        log.info(f"  Numbering mode : {cfg.get('channel_numbering', {}).get('mode', 'auto')}")
        log.info(f"  Profile mode   : {cfg.get('dispatcharr', {}).get('channel_profiles', {}).get('mode', 'all')}")
        return cfg
    except Exception as e:
        log.error(f"Failed to parse config.json: {e} — using defaults")
        return defaults


def build_channel_list(cfg):
    """
    Build the ordered list of active channels from config.

    Auto mode  → channels numbered sequentially from base_number, skipping disabled ones.
    Manual mode → each channel uses its explicit 'number' from config.

    Returns list of dicts: { id, emoji, name, number, enabled }
    """
    num_cfg      = cfg.get('channel_numbering', {})
    mode         = num_cfg.get('mode', 'auto')
    base         = num_cfg.get('base_number', 900)
    ch_overrides = num_cfg.get('channels', {})

    channels = []
    auto_counter = base

    for ch_id, emoji, default_name in CHANNEL_DEFS:
        override = ch_overrides.get(ch_id, {})
        enabled  = override.get('enabled', True)
        name     = override.get('name', default_name)

        if not enabled:
            log.info(f"  ⊘  Skipping disabled channel: {ch_id}")
            continue

        if mode == 'manual':
            number = override.get('number', auto_counter)
        else:
            number = auto_counter

        channels.append({'id': ch_id, 'emoji': emoji, 'name': name, 'number': number})
        auto_counter += 1

    return channels


# ── Dispatcharr API Client ────────────────────────────────────────────────────

class DispatcharrClient:
    def __init__(self, base_url, username, password):
        self.base_url      = base_url
        self.username      = username
        self.password      = password
        self.token         = None
        self.refresh_token = None
        self.token_expiry  = None
        self.session       = requests.Session()
        self.session.headers.update({'Content-Type': 'application/json'})

    def _url(self, path):
        return f"{self.base_url}{path}"

    # ── Auth ─────────────────────────────────────────────────────────────────

    def authenticate(self):
        log.info(f"Authenticating with Dispatcharr at {self.base_url}")
        try:
            r = self.session.post(
                self._url('/api/token/'),
                json={'username': self.username, 'password': self.password},
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            self.token         = data['access']
            self.refresh_token = data.get('refresh')
            self.token_expiry  = datetime.now() + timedelta(minutes=4, seconds=30)
            self.session.headers['Authorization'] = f'Bearer {self.token}'
            log.info("Authenticated ✅")
            return True
        except requests.RequestException as e:
            log.error(f"Authentication failed: {e}")
            return False

    def refresh_auth(self):
        if not self.refresh_token:
            return self.authenticate()
        try:
            r = self.session.post(
                self._url('/api/token/refresh/'),
                json={'refresh': self.refresh_token},
                timeout=10,
            )
            r.raise_for_status()
            self.token        = r.json()['access']
            self.token_expiry = datetime.now() + timedelta(minutes=4, seconds=30)
            self.session.headers['Authorization'] = f'Bearer {self.token}'
            return True
        except requests.RequestException:
            return self.authenticate()

    def ensure_auth(self):
        if not self.token or datetime.now() >= self.token_expiry:
            return self.refresh_auth()
        return True

    def get(self, path, **kw):
        self.ensure_auth()
        return self.session.get(self._url(path), timeout=10, **kw)

    def post(self, path, **kw):
        self.ensure_auth()
        return self.session.post(self._url(path), timeout=10, **kw)

    def patch(self, path, **kw):
        self.ensure_auth()
        return self.session.patch(self._url(path), timeout=10, **kw)

    # ── Channel Profiles ─────────────────────────────────────────────────────

    def get_channel_profiles(self):
        """Fetch all Channel Profiles from Dispatcharr."""
        try:
            r = self.get('/api/channels/channel-profiles/')
            r.raise_for_status()
            data     = r.json()
            profiles = data.get('results', data) if isinstance(data, dict) else data
            log.info(f"Dispatcharr has {len(profiles)} Channel Profile(s):")
            for p in profiles:
                log.info(f"    id={p['id']}  name='{p.get('name', '?')}'")
            return profiles
        except Exception as e:
            log.warning(f"Could not fetch channel profiles: {e}")
            return []

    def resolve_profile_ids(self, profile_cfg):
        """
        Translate the profile mode from config into the value to pass
        as channel_profile_ids on channel creation/update.

        Returns:
          None  → omit the parameter entirely (Dispatcharr default = ALL profiles)
          []    → empty list (Dispatcharr = NO profiles)
          [int] → specific profile IDs only
        """
        mode = profile_cfg.get('mode', 'all')

        if mode == 'all':
            log.info("Profile assignment: ALL profiles (Dispatcharr default)")
            return None

        if mode == 'none':
            log.info("Profile assignment: NONE — channels won't appear in any profile")
            return []

        if mode == 'specific':
            requested = profile_cfg.get('profile_ids', [])
            if not requested:
                log.warning("Profile mode is 'specific' but profile_ids is empty — defaulting to all")
                return None

            available    = self.get_channel_profiles()
            available_ids = {p['id'] for p in available}
            valid   = [i for i in requested if i in available_ids]
            invalid = [i for i in requested if i not in available_ids]

            if invalid:
                log.warning(f"  Profile IDs not found in Dispatcharr (skipped): {invalid}")
            if not valid:
                log.warning("  No valid profile IDs — defaulting to all profiles")
                return None

            log.info(f"Profile assignment: specific IDs {valid}")
            return valid

        log.warning(f"Unknown profile mode '{mode}' — defaulting to all")
        return None

    # ── Groups ───────────────────────────────────────────────────────────────

    def get_or_create_group(self, name):
        try:
            r = self.get('/api/channels/channel-groups/')
            r.raise_for_status()
            data  = r.json()
            items = data.get('results', data) if isinstance(data, dict) else data
            for g in items:
                if g.get('name') == name:
                    log.info(f"Group exists: '{name}' (id={g['id']})")
                    return g['id']
        except Exception as e:
            log.warning(f"Could not fetch groups: {e}")

        r = self.post('/api/channels/channel-groups/', json={'name': name})
        r.raise_for_status()
        g = r.json()
        log.info(f"Created group: '{name}' (id={g['id']})")
        return g['id']

    # ── Streams ──────────────────────────────────────────────────────────────

    def _all_streams(self):
        try:
            r    = self.get('/api/channels/streams/')
            r.raise_for_status()
            data = r.json()
            return data.get('results', data) if isinstance(data, dict) else data
        except Exception:
            return []

    def get_or_create_stream(self, name, url, group_id):
        for s in self._all_streams():
            if s.get('url') == url:
                log.info(f"    Stream exists (id={s['id']})")
                return s['id']
        r = self.post('/api/channels/streams/', json={
            'name': name, 'url': url,
            'channel_group_id': group_id, 'm3u_account': None,
        })
        r.raise_for_status()
        s = r.json()
        log.info(f"    Created stream (id={s['id']})")
        return s['id']

    # ── Channels ─────────────────────────────────────────────────────────────

    def _all_channels(self):
        try:
            r    = self.get('/api/channels/channels/')
            r.raise_for_status()
            data = r.json()
            return data.get('results', data) if isinstance(data, dict) else data
        except Exception:
            return []

    def upsert_channel(self, name, number, group_id, stream_ids, profile_ids):
        """Create channel if missing, otherwise update streams + number."""
        for ch in self._all_channels():
            if ch.get('name') == name:
                log.info(f"    Channel exists (id={ch['id']}) — updating")
                payload = {'streams': stream_ids, 'channel_number': number}
                if profile_ids is not None:
                    payload['channel_profile_ids'] = profile_ids
                r = self.patch(f"/api/channels/channels/{ch['id']}/", json=payload)
                r.raise_for_status()
                return ch

        payload = {
            'name': name, 'channel_number': number,
            'channel_group_id': group_id, 'streams': stream_ids,
        }
        if profile_ids is not None:
            payload['channel_profile_ids'] = profile_ids

        r = self.post('/api/channels/channels/', json=payload)
        r.raise_for_status()
        ch = r.json()
        log.info(f"    Created channel (id={ch['id']})")
        return ch


# ── Wait helpers ───────────────────────────────────────────────────────────────

def wait_for_dispatcharr(max_attempts=30):
    log.info("Waiting for Dispatcharr to become reachable...")
    for i in range(1, max_attempts + 1):
        try:
            r = requests.get(f"{DISPATCHARR_URL}/api/token/", timeout=5)
            if r.status_code in (200, 400, 401, 405):
                log.info("Dispatcharr reachable ✅")
                return True
        except requests.RequestException:
            pass
        log.info(f"  Not ready ({i}/{max_attempts}) — retrying in 5s")
        time.sleep(5)
    return False


def wait_for_hls(max_attempts=60):
    url = f"{STREAM_BASE_URL}/hls/all/stream.m3u8"
    log.info(f"Waiting for HLS at {url}")
    for i in range(1, max_attempts + 1):
        try:
            if requests.get(url, timeout=5).status_code == 200:
                log.info("HLS live ✅")
                return True
        except requests.RequestException:
            pass
        log.info(f"  Not ready ({i}/{max_attempts}) — retrying in 5s")
        time.sleep(5)
    log.warning("HLS not confirmed ready — proceeding anyway")
    return False


# ── Main sync ──────────────────────────────────────────────────────────────────

def sync_channels(client, cfg):
    d_cfg       = cfg.get('dispatcharr', {})
    group_name  = d_cfg.get('group_name', 'ScoreStream')
    profile_cfg = d_cfg.get('channel_profiles', {'mode': 'all', 'profile_ids': []})
    channels    = build_channel_list(cfg)

    log.info("━" * 52)
    log.info("ScoreStream Pro — Channel Sync")
    log.info(f"  Group      : {group_name}")
    log.info(f"  Numbering  : {cfg.get('channel_numbering', {}).get('mode', 'auto')}")
    log.info(f"  Profiles   : {profile_cfg.get('mode', 'all')}")
    log.info(f"  Channels   : {len(channels)} enabled")
    log.info("━" * 52)

    profile_ids = client.resolve_profile_ids(profile_cfg)
    group_id    = client.get_or_create_group(group_name)

    for ch in channels:
        url         = f"{STREAM_BASE_URL}/hls/{ch['id']}/stream.m3u8"
        stream_name = f"ScoreStream — {ch['id'].upper()}"
        log.info(f"{ch['emoji']}  {ch['name']}  ch{ch['number']}")
        try:
            stream_id = client.get_or_create_stream(stream_name, url, group_id)
            client.upsert_channel(ch['name'], ch['number'], group_id, [stream_id], profile_ids)
            log.info(f"    ✅ ch{ch['number']} → {url}")
        except Exception as e:
            log.error(f"    ❌ {ch['name']}: {e}")

    log.info("━" * 52)
    log.info(f"Sync complete — look for '{group_name}' in Dispatcharr → Channels")
    log.info("━" * 52)


def main():
    log.info("ScoreStream Pro API v0.2.0-beta starting")
    log.info(f"  Dispatcharr : {DISPATCHARR_URL}")
    log.info(f"  Stream base : {STREAM_BASE_URL}")
    log.info(f"  Config      : {CONFIG_PATH}")

    if not wait_for_dispatcharr():
        log.error("Dispatcharr unreachable — exiting")
        sys.exit(1)

    client = DispatcharrClient(DISPATCHARR_URL, DISPATCHARR_USER, DISPATCHARR_PASS)
    for attempt in range(1, 6):
        if client.authenticate():
            break
        log.warning(f"Auth retry {attempt}/5...")
        time.sleep(10)
    else:
        log.error("Authentication failed after 5 attempts — check credentials")
        sys.exit(1)

    wait_for_hls()

    cfg = load_config()
    sync_channels(client, cfg)

    log.info("Running — re-syncing every 6 hours, config reloads each time")
    last_sync = time.time()
    while True:
        time.sleep(60)
        client.ensure_auth()
        if time.time() - last_sync >= 6 * 3600:
            log.info("6-hour re-sync — reloading config.json")
            cfg = load_config()
            try:
                sync_channels(client, cfg)
                last_sync = time.time()
            except Exception as e:
                log.error(f"Re-sync error: {e}")


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        log.info("Shutdown")
        sys.exit(0)
