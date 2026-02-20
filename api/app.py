"""
ScoreStream API — Flask backend
Handles Dispatcharr channel management and config serving.
"""

import os
import json
import logging
from flask import Flask, jsonify, request
from flask_cors import CORS
import requests

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# ── Config ──────────────────────────────────────────────────
CONFIG_PATH       = os.getenv('CONFIG_PATH', '/config/config.json')
DISPATCHARR_URL   = os.getenv('DISPATCHARR_URL', '').rstrip('/')
DISPATCHARR_USER  = os.getenv('DISPATCHARR_USER', '')
DISPATCHARR_PASS  = os.getenv('DISPATCHARR_PASS', '')
DISPATCHARR_GROUP = os.getenv('DISPATCHARR_GROUP', 'ScoreStream')
CHANNEL_START     = int(os.getenv('DISPATCHARR_CHANNEL_START', '900'))
STREAM_BASE_URL   = os.getenv('STREAM_BASE_URL', '')

# ── Helpers ──────────────────────────────────────────────────
def load_config():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except Exception as e:
        log.warning(f'Could not load config: {e}')
        return {}

def save_config(data):
    with open(CONFIG_PATH, 'w') as f:
        json.dump(data, f, indent=2)

def dispatcharr_session():
    """Return an authenticated requests session for Dispatcharr."""
    s = requests.Session()
    if DISPATCHARR_USER and DISPATCHARR_PASS:
        try:
            r = s.post(
                f'{DISPATCHARR_URL}/api/accounts/token/',
                json={'username': DISPATCHARR_USER, 'password': DISPATCHARR_PASS},
                timeout=10
            )
            r.raise_for_status()
            token = r.json().get('access')
            if token:
                s.headers.update({'Authorization': f'Bearer {token}'})
                log.info('Authenticated with Dispatcharr')
        except Exception as e:
            log.error(f'Dispatcharr auth failed: {e}')
    return s

# ── Routes ───────────────────────────────────────────────────

@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'version': 'v0.0.1-beta'})

@app.route('/config', methods=['GET'])
def get_config():
    return jsonify(load_config())

@app.route('/config', methods=['POST'])
def set_config():
    data = request.get_json(force=True)
    save_config(data)
    return jsonify({'status': 'saved'})

@app.route('/dispatcharr/status', methods=['GET'])
def dispatcharr_status():
    """Check if Dispatcharr is reachable."""
    if not DISPATCHARR_URL:
        return jsonify({'connected': False, 'reason': 'DISPATCHARR_URL not set'})
    try:
        r = requests.get(f'{DISPATCHARR_URL}/api/channels/', timeout=5)
        return jsonify({'connected': r.ok, 'status_code': r.status_code})
    except Exception as e:
        return jsonify({'connected': False, 'reason': str(e)})

@app.route('/dispatcharr/channels', methods=['GET'])
def list_channels():
    """List all ScoreStream channels in Dispatcharr."""
    if not DISPATCHARR_URL:
        return jsonify({'error': 'DISPATCHARR_URL not set'}), 400
    try:
        s = dispatcharr_session()
        r = s.get(f'{DISPATCHARR_URL}/api/channels/', timeout=10)
        r.raise_for_status()
        channels = [c for c in r.json() if 'ScoreStream' in c.get('name','')]
        return jsonify(channels)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/dispatcharr/create', methods=['POST'])
def create_channels():
    """
    Create ScoreStream channels in Dispatcharr.
    POST body: { "mode": "combined" | "per_sport" | "both", "sports": [...] }
    """
    if not DISPATCHARR_URL:
        return jsonify({'error': 'DISPATCHARR_URL not configured'}), 400

    body   = request.get_json(force=True)
    mode   = body.get('mode', 'both')
    sports = body.get('sports', ['NFL','NBA','MLB','NHL','NCAA Basketball','NCAA Baseball'])

    s       = dispatcharr_session()
    created = []
    errors  = []
    ch_num  = CHANNEL_START

    def make_channel(name, num, stream_url):
        payload = {
            'name':       name,
            'number':     num,
            'url':        stream_url,
            'group':      DISPATCHARR_GROUP,
            'tvg_name':   name,
            'tvg_id':     name.lower().replace(' ', '-'),
        }
        try:
            r = s.post(f'{DISPATCHARR_URL}/api/channels/', json=payload, timeout=10)
            r.raise_for_status()
            created.append({'name': name, 'number': num})
            log.info(f'Created channel: {name} (#{num})')
        except Exception as e:
            errors.append({'name': name, 'error': str(e)})
            log.error(f'Failed to create {name}: {e}')

    if mode in ('combined', 'both'):
        make_channel(
            'ScoreStream — All Sports',
            ch_num,
            f'{STREAM_BASE_URL}/hls/scorestream.m3u8'
        )
        ch_num += 1

    if mode in ('per_sport', 'both'):
        for sport in sports:
            slug = sport.lower().replace(' ', '-')
            make_channel(
                f'ScoreStream — {sport}',
                ch_num,
                f'{STREAM_BASE_URL}/hls/{slug}.m3u8'
            )
            ch_num += 1

    return jsonify({'created': created, 'errors': errors})

# ── Entry ─────────────────────────────────────────────────────
if __name__ == '__main__':
    log.info('ScoreStream API starting on :5000')
    app.run(host='0.0.0.0', port=5000, debug=False)
