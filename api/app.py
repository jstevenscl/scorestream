"""
ScoreStream API — Flask backend
See NCAA_ARCHITECTURE.md for full design decisions.
"""
import os, sqlite3, logging, threading, json as _json
from datetime import datetime, timezone
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import requests as http

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)
app = Flask(__name__)

# ── Stream manager notify ─────────────────────────────────────────────────────
STREAM_MANAGER_URL = os.getenv('STREAM_MANAGER_URL', 'http://scorestream-stream:3001')

def notify_stream_manager():
    """Fire-and-forget POST /reload to stream manager. Non-blocking."""
    def _notify():
        try:
            http.post(f'{STREAM_MANAGER_URL}/reload', timeout=3)
        except Exception:
            pass  # Manager may not be running in all environments
    threading.Thread(target=_notify, daemon=True).start()
CORS(app)

DB_PATH         = os.getenv('DB_PATH', '/config/scorestream.db')
STREAM_BASE_URL = os.getenv('STREAM_BASE_URL', '')

_sync_lock = threading.Lock()

# Each tuple: (sport_id, gender, teams_url)
# Division is determined via ESPN Core API group/teams endpoints (see CORE_API_DIVISION_GROUPS)
ESPN_ENDPOINTS = [
    ('ncaafb',   'mens',   'https://site.api.espn.com/apis/site/v2/sports/football/college-football/teams?limit=900'),
    ('ncaamb',   'mens',   'https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/teams?limit=900'),
    ('ncaawb',   'womens', 'https://site.api.espn.com/apis/site/v2/sports/basketball/womens-college-basketball/teams?limit=900'),
    ('ncaabase', 'mens',   'https://site.api.espn.com/apis/site/v2/sports/baseball/college-baseball/teams?limit=900'),
    ('ncaasb',   'womens', 'https://site.api.espn.com/apis/site/v2/sports/baseball/college-softball/teams?limit=900'),
]

# ESPN Core API group/teams endpoints that return full paginated team lists per division.
# Discovered by tracing: team.$ref -> team.groups.$ref -> group.parent -> top-level group
# Format: (division, url)
# Football groups confirmed: 80=FBS(d1,146), 81=FCS(d1,131), 57=DII(d2,167), 58=DIII(d3,238)
# Basketball: need to discover - using site API groups as fallback for now
CORE_API_DIVISION_GROUPS = {
    'ncaafb': [
        ('d1', 'http://sports.core.api.espn.com/v2/sports/football/leagues/college-football/seasons/2026/types/1/groups/80/teams?limit=500'),
        ('d1', 'http://sports.core.api.espn.com/v2/sports/football/leagues/college-football/seasons/2026/types/1/groups/81/teams?limit=500'),
        ('d2', 'http://sports.core.api.espn.com/v2/sports/football/leagues/college-football/seasons/2026/types/1/groups/57/teams?limit=500'),
        ('d3', 'http://sports.core.api.espn.com/v2/sports/football/leagues/college-football/seasons/2026/types/1/groups/58/teams?limit=500'),
    ],
    # Basketball/Baseball/Softball: ESPN does not expose clean D-II/D-III group endpoints.
    # Division for these sports is inferred from the school's football division (see sync code).
    # Only D-I group defined here so we can identify D-I teams; rest default via school lookup.
    'ncaamb':   [('d1', 'http://sports.core.api.espn.com/v2/sports/basketball/leagues/mens-college-basketball/seasons/2026/types/2/groups/50/teams?limit=500')],
    'ncaawb':   [('d1', 'http://sports.core.api.espn.com/v2/sports/basketball/leagues/womens-college-basketball/seasons/2026/types/3/groups/50/teams?limit=500')],
    'ncaabase': [('d1', 'http://sports.core.api.espn.com/v2/sports/baseball/leagues/college-baseball/seasons/2026/types/2/groups/50/teams?limit=500')],
    'ncaasb':   [('d1', 'http://sports.core.api.espn.com/v2/sports/baseball/leagues/college-softball/seasons/2026/types/2/groups/50/teams?limit=500')],
}

# Ordered list: d3/d2 checked BEFORE d1 to prevent 'di' matching in 'division-iii' etc.
DIVISION_SLUG_MAP = [
    ('diii','d3'),('div-iii','d3'),('division-iii','d3'),('ncaa-d3','d3'),
    ('dii','d2'),('div-ii','d2'),('division-ii','d2'),('ncaa-d2','d2'),
    ('fbs','d1'),('fcs','d1'),('div-i','d1'),('division-i','d1'),('ncaa-d1','d1'),
    (' d1 ','d1'),(' d2 ','d2'),(' d3 ','d3'),
]

def parse_division(team_data):
    # VERIFY: ESPN group slug values require live API confirmation
    for group in team_data.get('groups', []):
        text = ' '.join([group.get('slug',''), group.get('name',''), group.get('shortName','')]).lower()
        for frag, div in DIVISION_SLUG_MAP:
            if frag in text:
                return div
    combined = (team_data.get('slug','') + ' ' + team_data.get('displayName','')).lower()
    for frag, div in DIVISION_SLUG_MAP:
        if frag in combined:
            return div
    return 'unknown'

FALLBACK_SCHOOLS = [
    ('ALA','University of Alabama','Tuscaloosa','A32638'),
    ('ARK','University of Arkansas','Fayetteville','9D2235'),
    ('AUB','Auburn University','Auburn','0C2340'),
    ('FLA','University of Florida','Gainesville','0021A5'),
    ('UGA','University of Georgia','Athens','BA0C2F'),
    ('UK','University of Kentucky','Lexington','0033A0'),
    ('LSU','Louisiana State University','Baton Rouge','461D7C'),
    ('MISS','University of Mississippi','Oxford','14213D'),
    ('MSST','Mississippi State University','Starkville','660000'),
    ('MO','University of Missouri','Columbia','F1B82D'),
    ('SC','University of South Carolina','Columbia','73000A'),
    ('TAMU','Texas A&M University','College Station','500000'),
    ('TENN','University of Tennessee','Knoxville','FF8200'),
    ('VAN','Vanderbilt University','Nashville','866D4B'),
    ('ILL','University of Illinois','Champaign','E84A27'),
    ('IU','Indiana University','Bloomington','990000'),
    ('IOWA','University of Iowa','Iowa City','FFCD00'),
    ('MD','University of Maryland','College Park','E03A3E'),
    ('MICH','University of Michigan','Ann Arbor','00274C'),
    ('MSU','Michigan State University','East Lansing','18453B'),
    ('MINN','University of Minnesota','Minneapolis','7A0019'),
    ('NEB','University of Nebraska','Lincoln','E41C38'),
    ('NW','Northwestern University','Evanston','4E2683'),
    ('OSU','Ohio State University','Columbus','BB0000'),
    ('PSU','Penn State University','University Park','041E42'),
    ('PUR','Purdue University','West Lafayette','CEB888'),
    ('RUT','Rutgers University','Piscataway','CC0033'),
    ('UCLA','University of California Los Angeles','Los Angeles','2D68C4'),
    ('USC','University of Southern California','Los Angeles','990000'),
    ('UW','University of Washington','Seattle','4B2E83'),
    ('WISC','University of Wisconsin','Madison','C5050C'),
    ('BAYLOR','Baylor University','Waco','003015'),
    ('BYU','Brigham Young University','Provo','002E5D'),
    ('CIN','University of Cincinnati','Cincinnati','E00122'),
    ('COL','University of Colorado','Boulder','CFB87C'),
    ('HOU','University of Houston','Houston','C8102E'),
    ('ISU','Iowa State University','Ames','730028'),
    ('KU','University of Kansas','Lawrence','0051A5'),
    ('KST','Kansas State University','Manhattan','512888'),
    ('OKST','Oklahoma State University','Stillwater','FF6600'),
    ('OU','University of Oklahoma','Norman','841617'),
    ('TCU','Texas Christian University','Fort Worth','4D1979'),
    ('TEX','University of Texas','Austin','BF5700'),
    ('TTU','Texas Tech University','Lubbock','CC0000'),
    ('UCF','University of Central Florida','Orlando','FFC904'),
    ('WVU','West Virginia University','Morgantown','002855'),
    ('BC','Boston College','Chestnut Hill','8A0000'),
    ('CLEM','Clemson University','Clemson','F66733'),
    ('DUKE','Duke University','Durham','003088'),
    ('FSU','Florida State University','Tallahassee','782F40'),
    ('GT','Georgia Institute of Technology','Atlanta','B3A369'),
    ('LOU','University of Louisville','Louisville','AD0000'),
    ('MIAMI','University of Miami','Coral Gables','005030'),
    ('UNC','University of North Carolina','Chapel Hill','4B9CD3'),
    ('NCST','NC State University','Raleigh','CC0000'),
    ('ND','University of Notre Dame','Notre Dame','0C2340'),
    ('PITT','University of Pittsburgh','Pittsburgh','003594'),
    ('SYR','Syracuse University','Syracuse','D44500'),
    ('WAKE','Wake Forest University','Winston-Salem','9E7E38'),
    ('VT','Virginia Tech','Blacksburg','630031'),
    ('UVA','University of Virginia','Charlottesville','232D4B'),
    ('ARIZ','University of Arizona','Tucson','003366'),
    ('ASU','Arizona State University','Tempe','8C1D40'),
    ('CAL','University of California','Berkeley','003262'),
    ('ORE','University of Oregon','Eugene','154733'),
    ('ORST','Oregon State University','Corvallis','DC4405'),
    ('STAN','Stanford University','Stanford','8C1515'),
    ('UTAH','University of Utah','Salt Lake City','CC0000'),
    ('WSU','Washington State University','Pullman','981E32'),
    ('BUTLER','Butler University','Indianapolis','13294B'),
    ('CREI','Creighton University','Omaha','005CA9'),
    ('DEPAUL','DePaul University','Chicago','005EB8'),
    ('GTWN','Georgetown University','Washington','041E42'),
    ('MARQ','Marquette University','Milwaukee','003366'),
    ('NOVA','Villanova University','Villanova','003E7E'),
    ('PROV','Providence College','Providence','002147'),
    ('SETON','Seton Hall University','South Orange','004488'),
    ('XAVI','Xavier University','Cincinnati','0C2340'),
    ('BSU','Boise State University','Boise','0033A0'),
    ('CSU','Colorado State University','Fort Collins','1E4D2B'),
    ('FRES','Fresno State University','Fresno','CC0000'),
    ('HAW','University of Hawaii','Honolulu','024731'),
    ('SDSU','San Diego State University','San Diego','A6192E'),
    ('UNLV','University of Nevada Las Vegas','Las Vegas','CF0A2C'),
    ('UNM','University of New Mexico','Albuquerque','BA0C2F'),
    ('NEV','University of Nevada','Reno','003366'),
    ('USU','Utah State University','Logan','00263A'),
    ('WYO','University of Wyoming','Laramie','492F24'),
    ('APP','Appalachian State University','Boone','FFCF00'),
    ('ARST','Arkansas State University','Jonesboro','CC092F'),
    ('CLSN','Coastal Carolina University','Conway','006F51'),
    ('GASO','Georgia Southern University','Statesboro','002B5C'),
    ('MRSH','Marshall University','Huntington','00B140'),
    ('ODU','Old Dominion University','Norfolk','003057'),
    ('TXST','Texas State University','San Marcos','501214'),
    ('AKR','University of Akron','Akron','041E42'),
    ('BALL','Ball State University','Muncie','BA0C2F'),
    ('BGSU','Bowling Green State University','Bowling Green','FF6600'),
    ('BUFF','University at Buffalo','Buffalo','005BBB'),
    ('CMU','Central Michigan University','Mount Pleasant','6A0032'),
    ('EMU','Eastern Michigan University','Ypsilanti','006633'),
    ('KENT','Kent State University','Kent','002664'),
    ('MIOH','Miami University Ohio','Oxford','B61E2E'),
    ('NIU','Northern Illinois University','DeKalb','CC0000'),
    ('OHIO','Ohio University','Athens','00694E'),
    ('TOL','University of Toledo','Toledo','003B6D'),
    ('WMU','Western Michigan University','Kalamazoo','6C4023'),
    ('FAU','Florida Atlantic University','Boca Raton','003F88'),
    ('FIU','Florida International University','Miami','081E3F'),
    ('MTSU','Middle Tennessee State University','Murfreesboro','0066CC'),
    ('RICE','Rice University','Houston','002469'),
    ('UTEP','University of Texas at El Paso','El Paso','FF8200'),
    ('UTSA','University of Texas at San Antonio','San Antonio','002A5C'),
    ('WKU','Western Kentucky University','Bowling Green','C60C30'),
    ('GONZ','Gonzaga University','Spokane','002469'),
    ('VCU','Virginia Commonwealth University','Richmond','FDBA31'),
    ('DAY','University of Dayton','Dayton','CE1141'),
    ('DRKE','Drake University','Des Moines','004B8D'),
    ('MURR','Murray State University','Murray','002144'),
    ('NDSU','North Dakota State University','Fargo','0A5640'),
    ('RICH','University of Richmond','Richmond','A6192E'),
    ('SDST','South Dakota State University','Brookings','003E7E'),
    ('SMC',"Saint Mary's College of California",'Moraga','13294B'),
    ('UCONN','University of Connecticut','Storrs','000E2F'),
]

FALLBACK_PROGRAMS = [
    ('ALA','ncaafb','mens','d1','Crimson Tide','Alabama Crimson Tide','Alabama'),
    ('ARK','ncaafb','mens','d1','Razorbacks','Arkansas Razorbacks','Arkansas'),
    ('AUB','ncaafb','mens','d1','Tigers','Auburn Tigers','Auburn'),
    ('FLA','ncaafb','mens','d1','Gators','Florida Gators','Florida'),
    ('UGA','ncaafb','mens','d1','Bulldogs','Georgia Bulldogs','Georgia'),
    ('LSU','ncaafb','mens','d1','Tigers','LSU Tigers','LSU'),
    ('MISS','ncaafb','mens','d1','Rebels','Ole Miss Rebels','Ole Miss'),
    ('MSST','ncaafb','mens','d1','Bulldogs','Mississippi St Bulldogs','Mississippi St'),
    ('MO','ncaafb','mens','d1','Tigers','Missouri Tigers','Missouri'),
    ('SC','ncaafb','mens','d1','Gamecocks','South Carolina Gamecocks','South Carolina'),
    ('TAMU','ncaafb','mens','d1','Aggies','Texas A&M Aggies','Texas A&M'),
    ('TENN','ncaafb','mens','d1','Volunteers','Tennessee Volunteers','Tennessee'),
    ('VAN','ncaafb','mens','d1','Commodores','Vanderbilt Commodores','Vanderbilt'),
    ('TEX','ncaafb','mens','d1','Longhorns','Texas Longhorns','Texas'),
    ('OU','ncaafb','mens','d1','Sooners','Oklahoma Sooners','Oklahoma'),
    ('OSU','ncaafb','mens','d1','Buckeyes','Ohio State Buckeyes','Ohio State'),
    ('MICH','ncaafb','mens','d1','Wolverines','Michigan Wolverines','Michigan'),
    ('ND','ncaafb','mens','d1','Fighting Irish','Notre Dame Fighting Irish','Notre Dame'),
    ('CLEM','ncaafb','mens','d1','Tigers','Clemson Tigers','Clemson'),
    ('ALA','ncaamb','mens','d1','Crimson Tide','Alabama Crimson Tide','Alabama'),
    ('UK','ncaamb','mens','d1','Wildcats','Kentucky Wildcats','Kentucky'),
    ('KU','ncaamb','mens','d1','Jayhawks','Kansas Jayhawks','Kansas'),
    ('DUKE','ncaamb','mens','d1','Blue Devils','Duke Blue Devils','Duke'),
    ('UNC','ncaamb','mens','d1','Tar Heels','North Carolina Tar Heels','North Carolina'),
    ('GONZ','ncaamb','mens','d1','Bulldogs','Gonzaga Bulldogs','Gonzaga'),
    ('TENN','ncaamb','mens','d1','Volunteers','Tennessee Volunteers','Tennessee'),
    ('TEX','ncaamb','mens','d1','Longhorns','Texas Longhorns','Texas'),
    ('UCLA','ncaamb','mens','d1','Bruins','UCLA Bruins','UCLA'),
    ('MSU','ncaamb','mens','d1','Spartans','Michigan State Spartans','Michigan St'),
    ('MARQ','ncaamb','mens','d1','Golden Eagles','Marquette Golden Eagles','Marquette'),
    ('CREI','ncaamb','mens','d1','Bluejays','Creighton Bluejays','Creighton'),
    ('TENN','ncaawb','womens','d1','Lady Volunteers','Tennessee Lady Volunteers','Tennessee'),
    ('SC','ncaawb','womens','d1','Gamecocks','South Carolina Gamecocks','South Carolina'),
    ('UNC','ncaawb','womens','d1','Tar Heels','North Carolina Tar Heels','North Carolina'),
    ('LSU','ncaawb','womens','d1','Tigers','LSU Tigers','LSU'),
    ('IOWA','ncaawb','womens','d1','Hawkeyes','Iowa Hawkeyes','Iowa'),
    ('TEX','ncaawb','womens','d1','Longhorns','Texas Longhorns','Texas'),
    ('UCLA','ncaawb','womens','d1','Bruins','UCLA Bruins','UCLA'),
    ('STAN','ncaawb','womens','d1','Cardinal','Stanford Cardinal','Stanford'),
    ('UGA','ncaawb','womens','d1','Bulldogs','Georgia Bulldogs','Georgia'),
    ('UCONN','ncaawb','womens','d1','Huskies','UConn Huskies','UConn'),
    ('LSU','ncaabase','mens','d1','Tigers','LSU Tigers','LSU'),
    ('FLA','ncaabase','mens','d1','Gators','Florida Gators','Florida'),
    ('TAMU','ncaabase','mens','d1','Aggies','Texas A&M Aggies','Texas A&M'),
    ('VAN','ncaabase','mens','d1','Commodores','Vanderbilt Commodores','Vanderbilt'),
    ('TEX','ncaabase','mens','d1','Longhorns','Texas Longhorns','Texas'),
    ('ARK','ncaabase','mens','d1','Razorbacks','Arkansas Razorbacks','Arkansas'),
    ('OU','ncaasb','womens','d1','Sooners','Oklahoma Sooners','Oklahoma'),
    ('UCLA','ncaasb','womens','d1','Bruins','UCLA Bruins','UCLA'),
    ('ALA','ncaasb','womens','d1','Crimson Tide','Alabama Crimson Tide','Alabama'),
    ('FLA','ncaasb','womens','d1','Gators','Florida Gators','Florida'),
    ('TAMU','ncaasb','womens','d1','Aggies','Texas A&M Aggies','Texas A&M'),
    ('FSU','ncaasb','womens','d1','Seminoles','Florida State Seminoles','Florida State'),
    ('TEX','ncaasb','womens','d1','Longhorns','Texas Longhorns','Texas'),
    ('UGA','ncaasb','womens','d1','Bulldogs','Georgia Bulldogs','Georgia'),
]

# ── DB helpers ────────────────────────────────────────────────────────────────
def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute('PRAGMA journal_mode = WAL')
    return conn

def db_get(key, default=None):
    try:
        with get_db() as conn:
            row = conn.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
            return row['value'] if row else default
    except Exception as e:
        log.error(f'db_get({key}): {e}'); return default

def db_set(key, value):
    with get_db() as conn:
        conn.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,str(value)))
        conn.commit()

# ── Schema ────────────────────────────────────────────────────────────────────
def init_db():
    with get_db() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("""CREATE TABLE IF NOT EXISTS ncaa_schools(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            espn_abbr TEXT NOT NULL UNIQUE,
            full_name TEXT NOT NULL,
            location TEXT, color TEXT, alt_color TEXT,
            logo_url TEXT, espn_id TEXT, slug TEXT,
            sync_source TEXT NOT NULL DEFAULT 'fallback',
            last_synced TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS ncaa_programs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            school_id INTEGER NOT NULL REFERENCES ncaa_schools(id) ON DELETE CASCADE,
            sport_id TEXT NOT NULL, gender TEXT NOT NULL,
            division TEXT NOT NULL DEFAULT 'unknown',
            nick TEXT, display_name TEXT, short_name TEXT, espn_team_id TEXT,
            last_synced TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(school_id, sport_id, division))""")
        conn.execute('CREATE INDEX IF NOT EXISTS idx_prog_sport_gender ON ncaa_programs(sport_id,gender)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_prog_division ON ncaa_programs(division)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_school_abbr ON ncaa_schools(espn_abbr)')
        conn.execute("""CREATE TABLE IF NOT EXISTS scoreboards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            slug TEXT NOT NULL UNIQUE,
            sport_config TEXT NOT NULL DEFAULT '{}',
            motor_config TEXT NOT NULL DEFAULT '{}',
            use_default_fonts INTEGER NOT NULL DEFAULT 1,
            use_default_colors INTEGER NOT NULL DEFAULT 1,
            use_default_card_size INTEGER NOT NULL DEFAULT 1,
            team_config TEXT NOT NULL DEFAULT '{}',
            display_config TEXT NOT NULL DEFAULT '{}',
            dispatcharr_channel_id TEXT,
            dispatcharr_stream_id TEXT,
            dispatcharr_channel_number INTEGER,
            dispatcharr_group_id TEXT,
            dispatcharr_profile_ids TEXT,
            dispatcharr_stream_profile_id TEXT,
            dispatcharr_logo_id TEXT,
            dispatcharr_logo_url TEXT,
            is_default INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')))""")
        # Migrate existing DBs: add dispatcharr_stream_id if missing
        try:
            conn.execute('ALTER TABLE scoreboards ADD COLUMN dispatcharr_stream_id TEXT')
        except Exception:
            pass  # column already exists
        # Migrate existing DBs: add audio columns if missing
        try:
            conn.execute("ALTER TABLE scoreboards ADD COLUMN audio_mode TEXT NOT NULL DEFAULT 'none'")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE scoreboards ADD COLUMN motor_config TEXT NOT NULL DEFAULT '{}'")
        except Exception:
            pass
        for col in ["use_default_fonts INTEGER NOT NULL DEFAULT 1",
                    "use_default_colors INTEGER NOT NULL DEFAULT 1",
                    "use_default_card_size INTEGER NOT NULL DEFAULT 1"]:
            try: conn.execute(f"ALTER TABLE scoreboards ADD COLUMN {col}")
            except Exception: pass
        try: conn.execute("ALTER TABLE scoreboards ADD COLUMN audio_playlist_id INTEGER DEFAULT NULL")
        except Exception: pass
        try:
            conn.execute('ALTER TABLE scoreboards ADD COLUMN audio_source_url TEXT')
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE scoreboards ADD COLUMN ticker_config TEXT NOT NULL DEFAULT '{}'")
        except Exception:
            pass
        try:
            conn.execute('ALTER TABLE scoreboards ADD COLUMN dispatcharr_logo_url TEXT')
        except Exception:
            pass
        conn.execute('''CREATE TABLE IF NOT EXISTS ticker_profile_backup(
            scoreboard_id INTEGER PRIMARY KEY,
            channel_id    INTEGER NOT NULL,
            original_profile_id INTEGER NOT NULL,
            ticker_profile_id   INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')))''')
        # Audio library table for uploaded music files
        conn.execute('''CREATE TABLE IF NOT EXISTS audio_library(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            display_name TEXT NOT NULL,
            file_size INTEGER DEFAULT 0,
            duration_secs INTEGER DEFAULT 0,
            uploaded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )''')
        # Audio playlists
        conn.execute('''CREATE TABLE IF NOT EXISTS audio_playlists(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            is_global INTEGER NOT NULL DEFAULT 0,
            track_ids TEXT NOT NULL DEFAULT '[]',
            shuffle INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )''')
        conn.commit()

        # Seed a default global playlist if none exists
        existing = conn.execute("SELECT id FROM audio_playlists WHERE is_global=1").fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO audio_playlists(name, is_global, track_ids, shuffle) VALUES(?,?,?,?)",
                ('Default (Built-in)', 1, '[]', 0)
            )
            conn.commit()
        
        conn.commit()

    # Seed built-in audio tracks from /builtin_audio (baked into API image via Dockerfile)
    # This is simple, reliable, and needs no cross-container communication
    BUILTIN_AUDIO_SRC = '/builtin_audio'
    if _os.path.isdir(BUILTIN_AUDIO_SRC):
        try:
            import json as _json
            mp3s = sorted([f for f in _os.listdir(BUILTIN_AUDIO_SRC)
                           if f.endswith('.mp3') and not f.startswith('loop')])
            existing_files = {r[0] for r in conn.execute('SELECT filename FROM audio_library').fetchall()}
            new_ids = []
            for mp3 in mp3s:
                src = _os.path.join(BUILTIN_AUDIO_SRC, mp3)
                dst = _os.path.join(AUDIO_DIR, mp3)
                if not _os.path.exists(dst):
                    import shutil as _shutil
                    _shutil.copy2(src, dst)
                if mp3 not in existing_files:
                    display = 'Built-in: ' + mp3.replace('.mp3','').replace('-',' ').replace('_',' ').title()
                    size = _os.path.getsize(dst)
                    cur = conn.execute(
                        'INSERT INTO audio_library(filename,display_name,file_size) VALUES(?,?,?)',
                        (mp3, display, size))
                    new_ids.append(cur.lastrowid)
            if new_ids:
                gpl = conn.execute("SELECT id,track_ids FROM audio_playlists WHERE is_global=1").fetchone()
                if gpl:
                    existing_ids = _json.loads(gpl['track_ids'] or '[]')
                    all_ids = list(dict.fromkeys(existing_ids + new_ids))
                    conn.execute("UPDATE audio_playlists SET track_ids=? WHERE id=?",
                                 (_json.dumps(all_ids), gpl['id']))
            conn.commit()
            if new_ids:
                print(f'[api] Seeded {len(new_ids)} built-in audio tracks into library')
        except Exception as e:
            print(f'[api] Built-in audio seed error: {e}')

        # Seed motor cache from GitHub data branch (auto-updated by GitHub Actions)
        # Fetches from dedicated data branch — never conflicts with code pushes to dev/beta/latest
        try:
            import json as _jg
            DATA_URL = 'https://raw.githubusercontent.com/jstevenscl/scorestream/data/motor_cache.json'
            rg = http.get(DATA_URL, timeout=15)
            if rg.ok:
                gdata = rg.json()
                updated_count = 0
                for key, value in gdata.items():
                    existing = conn.execute("SELECT data FROM motor_cache WHERE key=?", (key,)).fetchone()
                    new_data = _jg.dumps(value.get('data', value))
                    # Always overwrite from data branch to ensure fresh data
                    gh_updated = value.get('_updated', '2026-01-01')
                    conn.execute(
                        "INSERT OR REPLACE INTO motor_cache(key,data,updated_at) VALUES(?,?,?)",
                        (key, new_data, gh_updated + ' 00:00:00')
                    )
                    updated_count += 1
                conn.commit()
                print(f'[api] Motor cache seeded from data branch: {updated_count}/{len(gdata)} keys updated')
            else:
                print(f'[api] Motor cache fetch failed: {rg.status_code}')
        except Exception as e:
            print(f'[api] Motor cache seed error: {e}')

        # Seed nascar-history from nascar-last if history doesn't exist yet
        try:
            import json as _jh
            hist = conn.execute("SELECT data FROM motor_cache WHERE key='nascar-history'").fetchone()
            if not hist:
                last = conn.execute("SELECT data FROM motor_cache WHERE key='nascar-last'").fetchone()
                if last:
                    ld = _jh.loads(last['data'])
                    if isinstance(ld, str): ld = _jh.loads(ld)
                    if ld.get('drivers'):
                        hist_data = {'races': [ld], 'updated': ld.get('race_date','2026-01-01')}
                        conn.execute("INSERT OR REPLACE INTO motor_cache(key,data,updated_at) VALUES(?,?,datetime('now'))",
                                     ('nascar-history', _jh.dumps(hist_data)))
                        conn.commit()
                        print('[api] Seeded nascar-history from nascar-last')
        except Exception as e:
            print(f'[api] nascar-history seed error: {e}')

        # Strip cross-contaminated keys from motor scoreboard configs
        try:
            import json as _jmc
            _motor_valid_keys = {
                'nascar': {'nascar_next_race','nascar_live','nascar_last','nascar_standings','nascar_max_races'},
                'f1':     {'f1_next_race','f1_last_race','f1_standings','f1_max_races'},
                'pga':    {'pga_live','pga_history','pga_max_events'},
            }
            for slug, valid_keys in _motor_valid_keys.items():
                row = conn.execute("SELECT id, motor_config FROM scoreboards WHERE slug=?", (slug,)).fetchone()
                if not row: continue
                mc = _jmc.loads(row['motor_config'] or '{}')
                cleaned = {k: v for k, v in mc.items() if k in valid_keys}
                if cleaned != mc:
                    conn.execute("UPDATE scoreboards SET motor_config=? WHERE id=?", (_jmc.dumps(cleaned), row['id']))
                    removed = set(mc.keys()) - set(cleaned.keys())
                    print(f'[api] Cleaned motor_config for {slug}: removed {removed}')
            conn.commit()
        except Exception as e:
            print(f'[api] motor_config cleanup error: {e}')

        # Validate audio library on every startup:
        # 1. Mark tracks whose files are missing from disk (file_size=0)
        # 2. Remove ghost track IDs from playlists (IDs that no longer exist in library)
        try:
            import json as _jv
            all_tracks = conn.execute('SELECT id, filename, file_size FROM audio_library').fetchall()
            all_track_ids = {t['id'] for t in all_tracks}
            for t in all_tracks:
                full_path = _os.path.join(AUDIO_DIR, t['filename'])
                on_disk = _os.path.exists(full_path)
                if not on_disk and (t['file_size'] or 0) != 0:
                    conn.execute('UPDATE audio_library SET file_size=0 WHERE id=?', (t['id'],))
                    print(f'[api] Audio validation: file missing on disk — {t["filename"]}')
                elif on_disk and (t['file_size'] or 0) == 0:
                    # File now exists (was re-uploaded) — update size
                    conn.execute('UPDATE audio_library SET file_size=? WHERE id=?',
                                 (_os.path.getsize(full_path), t['id']))
            playlists = conn.execute('SELECT id, name, track_ids FROM audio_playlists').fetchall()
            for pl in playlists:
                ids = _jv.loads(pl['track_ids'] or '[]')
                valid = [i for i in ids if i in all_track_ids]
                if len(valid) != len(ids):
                    removed = [i for i in ids if i not in all_track_ids]
                    conn.execute('UPDATE audio_playlists SET track_ids=? WHERE id=?',
                                 (_jv.dumps(valid), pl['id']))
                    print(f'[api] Audio validation: removed ghost IDs {removed} from playlist "{pl["name"]}"')
            conn.commit()
        except Exception as e:
            print(f'[api] Audio validation error: {e}')

        # Purge motor cache entries older than 30 days — but preserve nascar-2026-season
        try:
            conn.execute("DELETE FROM motor_cache WHERE key NOT IN ('nascar-2026-season','pga-2026-tournaments') AND updated_at < datetime('now','-30 days')")
            conn.commit()
        except Exception: pass

        # Global settings table (display defaults etc)
        conn.execute('''CREATE TABLE IF NOT EXISTS global_settings(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )''')

        # Motor cache table for NASCAR/F1 results (no external history API)
        conn.execute('''CREATE TABLE IF NOT EXISTS motor_cache(
            key TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )''')
        # Purge stale motor cache entries older than 30 days
        conn.execute("DELETE FROM motor_cache WHERE updated_at < datetime('now','-30 days')")
        conn.commit()

        # Seed default scoreboard if none exists
        existing = conn.execute('SELECT COUNT(*) FROM scoreboards').fetchone()[0]
        if existing == 0:
            conn.execute("""INSERT INTO scoreboards(name,slug,is_default,sport_config,team_config,display_config)
                VALUES('ScoreStream','scorestream',1,'{}','[]','{}')""")
        conn.commit()
    log.info(f'DB ready: {DB_PATH}')
    threading.Thread(target=startup_sync, daemon=True, name='ncaa-sync').start()

# ── Sync ──────────────────────────────────────────────────────────────────────
def startup_sync():
    status = db_get('espn_sync_status','never')
    last   = db_get('last_espn_sync','')
    if status == 'complete' and last:
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).days
            if age < 30:
                log.info(f'NCAA sync skipped — {age}d old'); return
        except Exception: pass
    log.info('NCAA sync starting...')
    if not attempt_espn_sync():
        db_set('espn_sync_status','pending')
        with get_db() as conn:
            count = conn.execute('SELECT COUNT(*) FROM ncaa_schools').fetchone()[0]
        if count == 0:
            log.warning('ESPN unreachable — seeding fallback')
            seed_fallback()
        else:
            log.warning(f'ESPN unreachable — keeping {count} existing schools')

def attempt_espn_sync():
    # VERIFY: ESPN JSON path + group slugs require live API confirmation
    if not _sync_lock.acquire(blocking=False):
        log.info('ESPN sync already running — skipping')
        return False
    try:
     return _attempt_espn_sync_inner()
    finally:
     _sync_lock.release()

def build_division_map(sport_id):
    """Use ESPN Core API group/teams endpoints to build {espn_team_id: division} map.
    These endpoints return full paginated team lists per division group.
    Falls back to empty map (all teams default to d1) if core API unavailable.
    """
    div_map = {}
    groups = CORE_API_DIVISION_GROUPS.get(sport_id, [])
    if not groups:
        log.warning(f'No core API groups defined for {sport_id} — all teams will be d1')
        return div_map
    for division, url in groups:
        try:
            resp = http.get(url, timeout=15); resp.raise_for_status()
            data = resp.json()
            items = data.get('items', [])
            count = data.get('count', 0)
            for item in items:
                # Each item is a $ref URL like .../teams/123
                ref = item.get('$ref', '')
                if ref:
                    tid = ref.rstrip('/').split('/')[-1].split('?')[0]
                    if tid.isdigit():
                        div_map[tid] = division
            log.info(f'  {sport_id} {division}: {len(items)}/{count} teams mapped from core API')
        except Exception as e:
            log.warning(f'Core API groups fetch failed ({sport_id} {division}): {e}')
    log.info(f'Division map built for {sport_id}: {len(div_map)} total teams')
    return div_map

def _attempt_espn_sync_inner():
    db_set('espn_sync_status','running')
    all_ok = True
    for sport_id, gender, teams_url in ESPN_ENDPOINTS:
        try:
            log.info(f'ESPN sync: {sport_id}...')
            # Step 1: build espn_id → division map from core API
            div_map = build_division_map(sport_id)
            # Step 2: fetch all teams from site API
            resp = http.get(teams_url, timeout=15); resp.raise_for_status()
            data = resp.json()
            raw = data.get('sports',[{}])[0].get('leagues',[{}])[0].get('teams',[])
            if not raw: raw = data.get('teams',[])
            counts = {'d1':0,'d2':0,'d3':0,'unknown':0}
            with get_db() as conn:
                for entry in raw:
                    t     = entry.get('team', entry)
                    abbr  = (t.get('abbreviation') or '').strip().upper()
                    name  = (t.get('displayName')  or '').strip()
                    if not abbr or not name: continue
                    loc   = (t.get('location')       or '').strip()
                    slug  = (t.get('slug')            or '').strip()
                    color = (t.get('color')           or '333333').strip()
                    alt   = (t.get('alternateColor')  or '').strip()
                    eid   = str(t.get('id',''))
                    nick  = (t.get('nickname') or t.get('shortDisplayName') or '').strip()
                    short = (t.get('shortDisplayName') or name).strip()
                    logos = t.get('logos',[])
                    logo  = logos[0].get('href','') if logos else ''
                    # Determine division:
                    # 1. If core API map has the team ID → use it (football always has this)
                    # 2. For non-football: if team is in D-I group map → d1
                    #    else look up the school's division from their football program
                    #    else default d1
                    if eid in div_map:
                        div = div_map[eid]
                    elif sport_id != 'ncaafb':
                        # Look up school division from ANY sport already synced for this school.
                        # Football is most reliable (synced first), but any sport works.
                        # d2/d3 take priority over d1 in case of mixed programs.
                        row = conn.execute(
                            '''SELECT p.division FROM ncaa_programs p
                               JOIN ncaa_schools s ON s.id=p.school_id
                               WHERE s.espn_abbr=?
                               ORDER BY CASE p.division WHEN 'd3' THEN 0 WHEN 'd2' THEN 1 ELSE 2 END
                               LIMIT 1''',
                            (abbr,)
                        ).fetchone()
                        div = row[0] if row else 'd1'
                    else:
                        div = 'd1'
                    counts[div] = counts.get(div, 0) + 1
                    conn.execute("""
                        INSERT INTO ncaa_schools(espn_abbr,full_name,location,color,alt_color,
                            logo_url,espn_id,slug,sync_source,last_synced,updated_at)
                        VALUES(?,?,?,?,?,?,?,?,'espn',datetime('now'),datetime('now'))
                        ON CONFLICT(espn_abbr) DO UPDATE SET
                            full_name=excluded.full_name, location=excluded.location,
                            color=excluded.color, alt_color=excluded.alt_color,
                            logo_url=excluded.logo_url, espn_id=excluded.espn_id,
                            slug=excluded.slug, sync_source='espn',
                            last_synced=datetime('now'), updated_at=datetime('now')
                    """, (abbr,name,loc,color,alt,logo,eid,slug))
                    sid = conn.execute('SELECT id FROM ncaa_schools WHERE espn_abbr=?',(abbr,)).fetchone()
                    if not sid: continue
                    conn.execute("""
                        INSERT INTO ncaa_programs(school_id,sport_id,gender,division,
                            nick,display_name,short_name,espn_team_id,last_synced,updated_at)
                        VALUES(?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))
                        ON CONFLICT(school_id,sport_id,division) DO UPDATE SET
                            gender=excluded.gender, nick=excluded.nick,
                            display_name=excluded.display_name, short_name=excluded.short_name,
                            espn_team_id=excluded.espn_team_id,
                            last_synced=datetime('now'), updated_at=datetime('now')
                    """, (sid['id'],sport_id,gender,div,nick,name,short,eid))
                conn.commit()
            log.info(f'ESPN sync: {sport_id} done ({len(raw)} teams — {counts})')
        except Exception as e:
            log.error(f'ESPN sync error {sport_id}: {e}'); all_ok = False
    # Pass 2: fix non-football divisions using cross-sport school lookup.
    # Handles schools that play D-II/D-III basketball/baseball/softball but have no football.
    if all_ok:
        try:
            with get_db() as conn:
                updated = 0
                rows = conn.execute(
                    "SELECT DISTINCT s.id FROM ncaa_schools s "
                    "JOIN ncaa_programs p ON p.school_id = s.id "
                    "WHERE p.sport_id != 'ncaafb' AND p.division = 'd1' "
                    "AND s.id IN (SELECT school_id FROM ncaa_programs WHERE division != 'd1')"
                ).fetchall()
                for row in rows:
                    best = conn.execute(
                        "SELECT division FROM ncaa_programs WHERE school_id=? "
                        "ORDER BY CASE division WHEN 'd3' THEN 0 WHEN 'd2' THEN 1 ELSE 2 END LIMIT 1",
                        (row[0],)
                    ).fetchone()
                    if best and best[0] != 'd1':
                        # For each non-football sport where this school has a d1 record,
                        # delete the d1 record if a better division record already exists,
                        # otherwise update it. This avoids UNIQUE constraint violations.
                        sports = conn.execute(
                            "SELECT DISTINCT sport_id FROM ncaa_programs WHERE school_id=? AND sport_id != 'ncaafb' AND division='d1'",
                            (row[0],)
                        ).fetchall()
                        for sport_row in sports:
                            sid = sport_row[0]
                            existing = conn.execute(
                                "SELECT id FROM ncaa_programs WHERE school_id=? AND sport_id=? AND division=?",
                                (row[0], sid, best[0])
                            ).fetchone()
                            if existing:
                                # Better division record already exists — just delete the d1 duplicate
                                conn.execute(
                                    "DELETE FROM ncaa_programs WHERE school_id=? AND sport_id=? AND division='d1'",
                                    (row[0], sid)
                                )
                            else:
                                # No conflict — safe to update
                                conn.execute(
                                    "UPDATE ncaa_programs SET division=? WHERE school_id=? AND sport_id=? AND division='d1'",
                                    (best[0], row[0], sid)
                                )
                        updated += 1
                conn.commit()
            log.info(f'Division pass 2: corrected {updated} schools non-football divisions')
        except Exception as e:
            log.error(f'Division pass 2 error: {e}')
        db_set('espn_sync_status','complete')
        db_set('last_espn_sync', datetime.now(timezone.utc).isoformat())
        log.info('NCAA sync complete')
        # Persist current DB contents as updated fallback so offline mode stays current
        cache_espn_to_fallback()
    else:
        db_set('espn_sync_status','failed')
    return all_ok

def cache_espn_to_fallback():
    """Write current ESPN-synced DB contents into the settings table as a JSON cache.
    This cache is used by seed_fallback() instead of the hardcoded list when available,
    keeping the offline fallback complete and up-to-date automatically."""
    try:
        with get_db() as conn:
            schools = conn.execute(
                "SELECT espn_abbr,full_name,location,color,alt_color,logo_url,espn_id,slug "                "FROM ncaa_schools WHERE sync_source='espn' ORDER BY espn_abbr"
            ).fetchall()
            programs = conn.execute(
                "SELECT s.espn_abbr,p.sport_id,p.gender,p.division,p.nick,p.display_name,p.short_name "                "FROM ncaa_programs p JOIN ncaa_schools s ON s.id=p.school_id "                "WHERE s.sync_source='espn' ORDER BY s.espn_abbr,p.sport_id"
            ).fetchall()
            import json
            db_set('fallback_schools_cache', json.dumps([list(r) for r in schools]))
            db_set('fallback_programs_cache', json.dumps([list(r) for r in programs]))
        log.info(f'Fallback cache updated: {len(schools)} schools, {len(programs)} programs')
    except Exception as e:
        log.error(f'cache_espn_to_fallback error: {e}')

def seed_fallback():
    import json
    # Prefer the ESPN-synced cache (written after each successful sync) over the hardcoded list
    schools_raw = db_get('fallback_schools_cache','')
    programs_raw = db_get('fallback_programs_cache','')
    if schools_raw and programs_raw:
        try:
            schools_data = json.loads(schools_raw)
            programs_data = json.loads(programs_raw)
            with get_db() as conn:
                for r in schools_data:
                    abbr,full_name,loc,color,alt,logo,eid,slug = r
                    conn.execute(
                        "INSERT OR IGNORE INTO ncaa_schools(espn_abbr,full_name,location,color,alt_color,logo_url,espn_id,slug,sync_source) "                        "VALUES(?,?,?,?,?,?,?,?,'fallback')",
                        (abbr,full_name,loc,color,alt,logo,eid,slug))
                conn.commit()
                for r in programs_data:
                    abbr,sport_id,gender,division,nick,display_name,short_name = r
                    s = conn.execute('SELECT id FROM ncaa_schools WHERE espn_abbr=?',(abbr,)).fetchone()
                    if s:
                        conn.execute(
                            "INSERT OR IGNORE INTO ncaa_programs(school_id,sport_id,gender,division,nick,display_name,short_name) "                            "VALUES(?,?,?,?,?,?,?)",
                            (s['id'],sport_id,gender,division,nick,display_name,short_name))
                conn.commit()
            log.info(f'Fallback seed complete from cache: {len(schools_data)} schools')
            return
        except Exception as e:
            log.warning(f'Fallback cache load failed, using hardcoded list: {e}')
    # Hardcoded fallback (legacy — used only if no cache exists yet)
    with get_db() as conn:
        for abbr,full_name,location,color in FALLBACK_SCHOOLS:
            conn.execute("INSERT OR IGNORE INTO ncaa_schools(espn_abbr,full_name,location,color,sync_source) VALUES(?,?,?,?,'fallback')",(abbr,full_name,location,color))
        conn.commit()
        for abbr,sport_id,gender,division,nick,display_name,short_name in FALLBACK_PROGRAMS:
            s = conn.execute('SELECT id FROM ncaa_schools WHERE espn_abbr=?',(abbr,)).fetchone()
            if s:
                conn.execute("INSERT OR IGNORE INTO ncaa_programs(school_id,sport_id,gender,division,nick,display_name,short_name) VALUES(?,?,?,?,?,?,?)",(s['id'],sport_id,gender,division,nick,display_name,short_name))
        conn.commit()
    log.info('Fallback seed complete from hardcoded list')

# ── Serialisers ────────────────────────────────────────────────────────────────
def school_to_dict(r):
    return {'id':r['id'],'espn_abbr':r['espn_abbr'],'full_name':r['full_name'],
            'location':r['location'] or '','color':r['color'] or '333333',
            'alt_color':r['alt_color'] or '','logo_url':r['logo_url'] or '',
            'espn_id':r['espn_id'] or '','slug':r['slug'] or '',
            'sync_source':r['sync_source'] or 'fallback','last_synced':r['last_synced'] or ''}

def program_to_dict(r):
    return {'id':r['id'],'school_id':r['school_id'],
            'espn_abbr':r['espn_abbr'],'full_name':r['full_name'],
            'location':r['location'] or '','color':r['color'] or '333333',
            'alt_color':r['alt_color'] or '','logo_url':r['logo_url'] or '',
            'slug':r['slug'] or '','sport_id':r['sport_id'],'gender':r['gender'],
            'division':r['division'],'nick':r['nick'] or '',
            'display_name':r['display_name'] or '','short_name':r['short_name'] or '',
            'espn_team_id':r['espn_team_id'] or ''}

# ── Dispatcharr helpers ────────────────────────────────────────────────────────
def get_creds():
    return {'url':(db_get('dispatcharr_url') or os.getenv('DISPATCHARR_URL','')).rstrip('/'),
            'username':db_get('dispatcharr_username') or os.getenv('DISPATCHARR_USER',''),
            'password':db_get('dispatcharr_password') or os.getenv('DISPATCHARR_PASS',''),
            'api_token':db_get('dispatcharr_api_token') or os.getenv('DISPATCHARR_API_TOKEN','')}

def dispatcharr_session(creds=None):
    if creds is None: creds = get_creds()
    url = creds.get('url','')
    if not url: return None,'Dispatcharr URL not configured'
    s = http.Session()
    s.headers.update({'Accept':'application/json','Content-Type':'application/json'})

    # Method 1: API token (Dispatcharr v0.20.0+ — ApiKey header)
    api_token = creds.get('api_token','')
    if api_token:
        s.headers.update({'Authorization': f'ApiKey {api_token}'})
        try:
            r = s.get(f'{url}/api/accounts/profile/', timeout=10)
            r.raise_for_status()
            return s, None
        except http.exceptions.ConnectionError:
            return None, f'Cannot connect to Dispatcharr at {url}'
        except http.exceptions.HTTPError as e:
            code = e.response.status_code if e.response is not None else 0
            if code in (401, 403): return None,'Invalid API token'
            return None, f'Auth error: {e}'
        except Exception as e:
            return None, str(e)

    # Method 2: Username/password → JWT (legacy)
    user, pw = creds.get('username',''), creds.get('password','')
    if not user or not pw: return None,'No API token or username/password configured'
    try:
        r = s.post(f'{url}/api/accounts/token/',json={'username':user,'password':pw},timeout=10)
        r.raise_for_status()
        token = r.json().get('access')
        if not token: return None,'No token returned — check credentials'
        s.headers.update({'Authorization': f'Bearer {token}'})
        return s, None
    except http.exceptions.ConnectionError:
        return None, f'Cannot connect to Dispatcharr at {url}'
    except http.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code in (401,403):
            return None,'Invalid username or password'
        return None, f'Auth error: {e}'
    except Exception as e:
        return None, str(e)

# ══════════════════════════════════════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/health')
def health():
    return jsonify({'status':'ok','version':'v0.3.0'})

# ── Audio ──────────────────────────────────────────────────────────────────────
@app.route('/audio/test', methods=['POST'])
def audio_test():
    import requests as _requests
    b = request.get_json(force=True)
    url = (b.get('url') or '').strip()
    if not url:
        return jsonify({'ok': False, 'error': 'No URL provided'}), 400
    try:
        r = _requests.head(url, timeout=8, allow_redirects=True)
        ct = r.headers.get('Content-Type', '')
        if r.status_code < 400:
            return jsonify({'ok': True, 'content_type': ct.split(';')[0].strip()})
        # HEAD not supported — try GET with stream
        r2 = _requests.get(url, timeout=8, stream=True)
        ct = r2.headers.get('Content-Type', '')
        r2.close()
        if r2.status_code < 400:
            return jsonify({'ok': True, 'content_type': ct.split(';')[0].strip()})
        return jsonify({'ok': False, 'error': f'HTTP {r2.status_code}'}), 200
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 200


@app.route('/dispatcharr/credentials', methods=['GET'])
def get_credentials():
    c = get_creds()
    return jsonify({'url':c.get('url',''),'username':c.get('username',''),'has_password':bool(c.get('password','')),'has_api_token':bool(c.get('api_token',''))})

@app.route('/dispatcharr/credentials', methods=['POST'])
def save_credentials():
    b = request.get_json(force=True)
    url = b.get('url','').strip().rstrip('/')
    if not url: return jsonify({'error':'URL is required'}),400
    try:
        db_set('dispatcharr_url',url)
        if b.get('username'): db_set('dispatcharr_username',b['username'].strip())
        if b.get('password') is not None: db_set('dispatcharr_password',b['password'])
        if b.get('api_token') is not None: db_set('dispatcharr_api_token',b['api_token'].strip())
        return jsonify({'status':'saved'})
    except Exception as e: return jsonify({'error':str(e)}),500

@app.route('/dispatcharr/test', methods=['GET'])
def test_connection():
    c = get_creds(); s,err = dispatcharr_session(c)
    if err: return jsonify({'connected':False,'error':err})
    # Auth succeeded (JWT token obtained) - treat that as confirmed connection.
    # The channels endpoint can be slow on remote/cloud Dispatcharr instances.
    try:
        r = s.get(c['url']+'/api/accounts/profile/',timeout=15)
        r.raise_for_status()
        data = r.json()
        username = data.get('username') or data.get('user','')
        return jsonify({'connected':True,'username':username})
    except Exception:
        return jsonify({'connected':True})

def dispatcharr_get(endpoint, params=None, timeout=30):
    """Get a fresh session and make a single authenticated GET. Returns (data, error)."""
    c = get_creds(); s,err = dispatcharr_session(c)
    if err: return None, err
    try:
        r = s.get(f'{c["url"]}{endpoint}', params=params, timeout=timeout)
        if r.headers.get('content-type','').startswith('text/html'):
            return None, 'Dispatcharr returned HTML — token may have expired. Try reconnecting.'
        r.raise_for_status()
        return r.json(), None
    except Exception as e:
        return None, str(e)

@app.route('/dispatcharr/groups', methods=['GET'])
def get_groups():
    data,err = dispatcharr_get('/api/channels/groups/', timeout=45)
    if err: return jsonify({'error':err}),400
    items = data.get('results',data) if isinstance(data,dict) else data
    if not isinstance(items, list): items = []
    return jsonify({'groups':sorted([{'id':g['id'],'name':g['name'],'m3u_count':g.get('m3u_account_count',0)} for g in items if 'id' in g and 'name' in g],key=lambda x:x['name'])})

@app.route('/dispatcharr/profiles', methods=['GET'])
def get_profiles():
    data,err = dispatcharr_get('/api/channels/profiles/', timeout=45)
    if err: return jsonify({'error':err}),400
    items = data.get('results',data) if isinstance(data,dict) else data
    if not isinstance(items, list): items = []
    return jsonify({'profiles':sorted([{'id':p['id'],'name':p['name']} for p in items if 'id' in p and 'name' in p],key=lambda x:x['name'])})

@app.route('/dispatcharr/streamprofiles', methods=['GET'])
def get_stream_profiles():
    data,err = dispatcharr_get('/api/core/streamprofiles/', timeout=20)
    if err: return jsonify({'error':err}),400
    items = data.get('results',data) if isinstance(data,dict) else data
    if not isinstance(items, list): items = []
    profiles = [{'id':p['id'],'name':p['name'],'command':p.get('command',''),
                 'parameters':p.get('parameters',''),'locked':p.get('locked',False)}
                for p in items if 'id' in p and 'name' in p]
    return jsonify({'profiles':sorted(profiles, key=lambda x:(x['locked'],x['name']), reverse=True)})

@app.route('/dispatcharr/streamprofiles', methods=['POST'])
def create_stream_profile():
    b = request.get_json(force=True)
    name = b.get('name','').strip()
    parameters = b.get('parameters','').strip()
    if not name or not parameters:
        return jsonify({'error':'name and parameters required'}),400
    c = get_creds(); s,err = dispatcharr_session(c)
    if err: return jsonify({'error':err}),400
    try:
        payload = {'name':name,'command':'ffmpeg','parameters':parameters,'is_active':True}
        r = s.post(f'{c["url"]}/api/core/streamprofiles/',json=payload,timeout=15)
        r.raise_for_status()
        d = r.json()
        return jsonify({'id':d['id'],'name':d['name'],'parameters':d.get('parameters','')})
    except Exception as e: return jsonify({'error':str(e)}),500

@app.route('/dispatcharr/streamprofiles/<int:profile_id>', methods=['GET'])
def get_stream_profile(profile_id):
    data,err = dispatcharr_get(f'/api/core/streamprofiles/{profile_id}/', timeout=15)
    if err: return jsonify({'error':err}),400
    return jsonify({'id':data['id'],'name':data['name'],'command':data.get('command',''),
                    'parameters':data.get('parameters',''),'locked':data.get('locked',False)})

@app.route('/dispatcharr/channels', methods=['GET'])
def get_channels():
    c = get_creds(); s,err = dispatcharr_session(c)
    if err: return jsonify({'error':err}),400
    try:
        # Fetch groups first for name lookup
        group_names = {}
        try:
            gr = s.get(f'{c["url"]}/api/channels/groups/', params={'page_size':500}, timeout=20)
            if gr.ok:
                gdata = gr.json()
                gitems = gdata.get('results', gdata) if isinstance(gdata, dict) else gdata
                if isinstance(gitems, list):
                    for g in gitems:
                        if isinstance(g, dict) and 'id' in g:
                            group_names[g['id']] = g.get('name', '')
        except Exception:
            pass
        channels,page = [],1
        while True:
            r = s.get(f'{c["url"]}/api/channels/channels/',params={'page':page,'page_size':100},timeout=30)
            r.raise_for_status()
            data = r.json()
            items = data.get('results',data) if isinstance(data,dict) else data
            if not isinstance(items,list): break
            channels.extend(items)
            if not (isinstance(data,dict) and data.get('next')): break
            page += 1
        result = []
        for ch in channels:
            if 'id' not in ch: continue
            gid = ch.get('channel_group_id') or ch.get('channel_group')
            result.append({
                'id': ch['id'],
                'name': ch.get('name', ''),
                'channel_number': ch.get('channel_number'),
                'stream_profile_id': ch.get('stream_profile_id'),
                'channel_group_id': gid,
                'channel_group_name': group_names.get(gid, '') if gid else '',
            })
        return jsonify({'channels':sorted(result,key=lambda x:x['name'])})
    except Exception as e: return jsonify({'error':str(e)}),500

@app.route('/dispatcharr/channels/<int:channel_id>', methods=['GET'])
def get_channel(channel_id):
    data,err = dispatcharr_get(f'/api/channels/channels/{channel_id}/',timeout=15)
    if err: return jsonify({'error':err}),400
    return jsonify({'id':data['id'],'name':data.get('name',''),
                    'stream_profile_id':data.get('stream_profile_id')})

# ── Ticker Overlay ────────────────────────────────────────────────────────────

TICKER_FILE = '/ticker/scores.txt'

def _build_ticker_params(original_params, font_size=24, position='bottom', bg_opacity=0.75, test_text=None, scroll_speed=0):
    import re
    params = original_params.strip()
    y_expr = 'H-th-6' if position == 'bottom' else '6'
    x_expr = f'w-mod(t*{scroll_speed}\\,w+tw)' if scroll_speed > 0 else '0'
    if test_text:
        safe = test_text.replace('\\','\\\\').replace(':','\\:').replace("'",'\\u0027')
        drawtext = (
            f"drawtext=text='{safe}'"
            f':fontsize={font_size}:fontcolor=white'
            f':box=1:boxcolor=black@{bg_opacity}:boxborderw=10'
            f':x={x_expr}:y={y_expr}'
        )
    else:
        drawtext = (
            f'drawtext=textfile={TICKER_FILE}:reload=1'
            f':fontsize={font_size}:fontcolor=white'
            f':box=1:boxcolor=black@{bg_opacity}:boxborderw=10'
            f':x={x_expr}:y={y_expr}'
        )
    if '-c:v copy' in params:
        params = params.replace(
            '-c:v copy',
            f'-vf "{drawtext}" -c:v libx264 -preset ultrafast -tune zerolatency'
        )
    elif '-vf ' in params:
        params = re.sub(r'-vf\s+"([^"]+)"', f'-vf "\\1,{drawtext}"', params)
    else:
        params = re.sub(r'(-f\s+\S+\s*(?:pipe:\d+)?)\s*$', f'-vf "{drawtext}" \\1', params.rstrip())
    return params

@app.route('/ticker/preview-params', methods=['POST'])
def ticker_preview_params():
    b = request.get_json(force=True) or {}
    original = b.get('parameters','').strip()
    if not original: return jsonify({'error':'parameters required'}),400
    modified = _build_ticker_params(original, int(b.get('font_size',24)),
                                    b.get('position','bottom'), float(b.get('bg_opacity',0.75)))
    return jsonify({'original':original,'modified':modified})

@app.route('/ticker/config', methods=['GET'])
def get_ticker_config_global():
    try:
        with get_db() as conn:
            row    = conn.execute("SELECT value FROM global_settings WHERE key='ticker_config'").fetchone()
            backup = conn.execute('SELECT channel_id,ticker_profile_id FROM ticker_profile_backup WHERE scoreboard_id=0').fetchone()
        cfg = _json.loads(row['value']) if row else {}
        cfg['ticker_active'] = backup is not None
        if backup:
            cfg['active_ticker_profile_id'] = backup['ticker_profile_id']
        return jsonify(cfg)
    except Exception as e: return jsonify({'error':str(e)}),500

@app.route('/ticker/config', methods=['POST'])
def save_ticker_config_global():
    b = request.get_json(force=True) or {}
    # Strip runtime-only fields before storing
    cfg = {k:v for k,v in b.items() if k not in ('ticker_active','active_ticker_profile_id')}
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO global_settings(key,value,updated_at) VALUES('ticker_config',?,datetime('now')) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                (_json.dumps(cfg),))
            conn.commit()
        return jsonify({'ok':True})
    except Exception as e: return jsonify({'error':str(e)}),500

# Keep per-scoreboard endpoints for backward compat (scoreboard editor still uses them)
@app.route('/ticker/config/<int:sb_id>', methods=['GET'])
def get_ticker_config(sb_id):
    try:
        with get_db() as conn:
            row = conn.execute('SELECT ticker_config FROM scoreboards WHERE id=?',(sb_id,)).fetchone()
            if not row: return jsonify({'error':'not found'}),404
            cfg = _json.loads(row['ticker_config'] or '{}')
            backup = conn.execute(
                'SELECT channel_id,ticker_profile_id FROM ticker_profile_backup WHERE scoreboard_id=?',
                (sb_id,)).fetchone()
            cfg['ticker_active'] = backup is not None
            if backup: cfg['active_ticker_profile_id'] = backup['ticker_profile_id']
            return jsonify(cfg)
    except Exception as e: return jsonify({'error':str(e)}),500

@app.route('/ticker/config/<int:sb_id>', methods=['POST'])
def save_ticker_config(sb_id):
    b = request.get_json(force=True) or {}
    try:
        with get_db() as conn:
            if not conn.execute('SELECT id FROM scoreboards WHERE id=?',(sb_id,)).fetchone():
                return jsonify({'error':'not found'}),404
            conn.execute('UPDATE scoreboards SET ticker_config=? WHERE id=?',(_json.dumps(b),sb_id))
            conn.commit()
        return jsonify({'ok':True})
    except Exception as e: return jsonify({'error':str(e)}),500

@app.route('/ticker/enable', methods=['POST'])
def ticker_enable():
    b = request.get_json(force=True) or {}
    # scoreboard_id is optional — default 0 means global ticker
    sb_id      = int(b.get('scoreboard_id', 0))
    channel_id = b.get('channel_id')
    font_size  = int(b.get('font_size', 24))
    position   = b.get('position', 'bottom')
    bg_opacity = float(b.get('bg_opacity', 0.75))
    test_text  = b.get('test_text', '').strip() or None
    scroll_speed = int(b.get('scroll_speed', 0))
    if not channel_id:
        return jsonify({'error':'channel_id required'}),400
    c = get_creds(); s,err = dispatcharr_session(c)
    if err: return jsonify({'error':err}),400
    try:
        # Get channel's current stream profile
        r = s.get(f'{c["url"]}/api/channels/channels/{channel_id}/',timeout=15)
        r.raise_for_status()
        channel = r.json()
        original_profile_id = channel.get('stream_profile_id')
        if not original_profile_id:
            return jsonify({'error':'Channel has no stream profile assigned. Assign one in Dispatcharr first.'}),400
        # Get the profile
        r = s.get(f'{c["url"]}/api/core/streamprofiles/{original_profile_id}/',timeout=15)
        r.raise_for_status()
        profile = r.json()
        if profile.get('locked'):
            return jsonify({'error':f'Profile "{profile["name"]}" is locked. Duplicate it in Dispatcharr first.'}),400
        original_params = profile.get('parameters','')
        if not original_params:
            return jsonify({'error':'Stream profile has no parameters'}),400
        # Strip any existing drawtext filters to prevent stacking
        import re as _re
        clean_params = _re.sub(r',?drawtext=[^\s"]*', '', original_params)
        if clean_params != original_params:
            # Profile already had drawtext — restore the -c:v copy if we injected libx264 before
            clean_params = _re.sub(
                r'-vf\s+""\s+-c:v\s+libx264\s+-preset\s+ultrafast\s+-tune\s+zerolatency',
                '-c:v copy', clean_params)
            # Clean up empty -vf ""
            clean_params = _re.sub(r'-vf\s+""\s*', '', clean_params)
        modified_params = _build_ticker_params(clean_params,font_size,position,bg_opacity,test_text,scroll_speed)
        if modified_params == clean_params:
            return jsonify({'error':'Could not inject ticker — "-c:v copy" not found in profile parameters.'}),400
        # Create ticker profile — avoid double-suffixing
        base_name = profile['name'].removesuffix(' (Ticker)')
        ticker_name = f'{base_name} (Ticker)'
        r = s.post(f'{c["url"]}/api/core/streamprofiles/',
                   json={'name':ticker_name,'command':profile.get('command','ffmpeg'),
                         'parameters':modified_params,'is_active':True},timeout=15)
        r.raise_for_status()
        ticker_profile_id = r.json()['id']
        # Assign ticker profile to channel
        r = s.patch(f'{c["url"]}/api/channels/channels/{channel_id}/',
                    json={'stream_profile_id':ticker_profile_id},timeout=15)
        r.raise_for_status()
        # Store backup
        with get_db() as conn:
            conn.execute('''INSERT OR REPLACE INTO ticker_profile_backup
                            (scoreboard_id,channel_id,original_profile_id,ticker_profile_id)
                            VALUES(?,?,?,?)''',
                         (sb_id,channel_id,original_profile_id,ticker_profile_id))
            conn.commit()
        return jsonify({'ok':True,'ticker_profile_id':ticker_profile_id,
                        'ticker_profile_name':ticker_name,
                        'original_profile_id':original_profile_id,
                        'modified_params':modified_params})
    except Exception as e: return jsonify({'error':str(e)}),500

def _disable_ticker_row(backup_row, session, creds):
    """Shared logic to disable a single ticker backup entry. Returns list of warnings."""
    warnings = []
    try:
        r = session.patch(f'{creds["url"]}/api/channels/channels/{backup_row["channel_id"]}/',
                          json={'stream_profile_id':backup_row['original_profile_id']},timeout=15)
        r.raise_for_status()
    except Exception as e:
        warnings.append(f'Restore channel profile: {e}')
    try:
        r = session.delete(f'{creds["url"]}/api/core/streamprofiles/{backup_row["ticker_profile_id"]}/',timeout=15)
        if r.status_code not in (200,204):
            warnings.append(f'Delete ticker profile: HTTP {r.status_code}')
    except Exception as e:
        warnings.append(f'Delete ticker profile: {e}')
    return warnings

@app.route('/ticker/disable', methods=['POST'])
def ticker_disable():
    b = request.get_json(force=True) or {}
    sb_id = int(b.get('scoreboard_id', 0))
    with get_db() as conn:
        backup = conn.execute(
            'SELECT channel_id,original_profile_id,ticker_profile_id FROM ticker_profile_backup WHERE scoreboard_id=?',
            (sb_id,)).fetchone()
    if not backup: return jsonify({'error':'No active ticker found'}),404
    c = get_creds(); s,err = dispatcharr_session(c)
    if err: return jsonify({'error':err}),400
    warnings = _disable_ticker_row(backup, s, c)
    with get_db() as conn:
        conn.execute('DELETE FROM ticker_profile_backup WHERE scoreboard_id=?',(sb_id,))
        conn.commit()
    return jsonify({'ok':True,'warnings':warnings} if warnings else {'ok':True})

@app.route('/ticker/disable-all', methods=['POST'])
def ticker_disable_all():
    """Kill all active tickers at once."""
    with get_db() as conn:
        rows = conn.execute(
            'SELECT scoreboard_id,channel_id,original_profile_id,ticker_profile_id FROM ticker_profile_backup').fetchall()
    if not rows: return jsonify({'error':'No active tickers found'}),404
    c = get_creds(); s,err = dispatcharr_session(c)
    if err: return jsonify({'error':err}),400
    all_warnings = []
    for row in rows:
        all_warnings.extend(_disable_ticker_row(row, s, c))
    with get_db() as conn:
        conn.execute('DELETE FROM ticker_profile_backup')
        conn.commit()
    return jsonify({'ok':True,'disabled':len(rows),'warnings':all_warnings} if all_warnings
                   else {'ok':True,'disabled':len(rows)})

@app.route('/ticker/status', methods=['GET'])
def ticker_status():
    try:
        with get_db() as conn:
            rows = conn.execute(
                'SELECT scoreboard_id,channel_id,original_profile_id,ticker_profile_id,created_at '
                'FROM ticker_profile_backup').fetchall()
        if not rows:
            return jsonify({'active':[]})
        # Enrich with channel names from Dispatcharr
        active = []
        c = get_creds(); s, err = dispatcharr_session(c)
        for r in rows:
            entry = {'scoreboard_id':r['scoreboard_id'],'channel_id':r['channel_id'],
                     'original_profile_id':r['original_profile_id'],
                     'ticker_profile_id':r['ticker_profile_id'],
                     'created_at':r['created_at'],
                     'channel_name':f'Channel {r["channel_id"]}'}
            if s and not err:
                try:
                    cr = s.get(f'{c["url"]}/api/channels/channels/{r["channel_id"]}/',timeout=8)
                    if cr.ok:
                        entry['channel_name'] = cr.json().get('name', entry['channel_name'])
                except Exception:
                    pass
            active.append(entry)
        return jsonify({'active':active})
    except Exception as e: return jsonify({'error':str(e)}),500

@app.route('/ticker/text', methods=['GET'])
def ticker_text_global():
    """Global ticker text — reads sports config from global_settings."""
    import json as _jg
    try:
        with get_db() as conn:
            row = conn.execute("SELECT value FROM global_settings WHERE key='ticker_config'").fetchone()
        if not row: return jsonify({'text':''}),200
        cfg = _jg.loads(row['value'])
        sports = cfg.get('sports', [])
    except Exception as e:
        return jsonify({'error':str(e)}),500
    # Reuse per-sb logic by injecting sports into a mock config and calling the shared helper
    from flask import g as _fg
    _fg._ticker_sports_override = sports
    return _ticker_text_for_sports(sports)

def _ticker_text_for_sports(sports):
    """Shared ticker text builder — called by both global and per-sb endpoints."""
    import json as _jt
    from datetime import date as _date
    today = str(_date.today())
    ESPN_PATHS = {
        'nfl':'sports/football/nfl','nba':'sports/basketball/nba',
        'mlb':'sports/baseball/mlb','nhl':'sports/hockey/nhl',
        'wnba':'sports/basketball/wnba','cfl':'sports/football/cfl',
        'xfl':'sports/football/xfl','ufl':'sports/football/ufl',
        'mls':'sports/soccer/usa.1','nwsl':'sports/soccer/usa.nwsl',
        'atp':'sports/tennis/atp','wta':'sports/tennis/wta',
        'epl':'sports/soccer/eng.1','ucl':'sports/soccer/uefa.champions',
        'laliga':'sports/soccer/esp.1','bundesliga':'sports/soccer/ger.1',
        'seriea':'sports/soccer/ita.1','ligue1':'sports/soccer/fra.1',
        'ncaafb':'sports/football/college-football',
        'ncaamb':'sports/basketball/mens-college-basketball',
        'ncaabase':'sports/baseball/college-baseball',
        'ncaawb':'sports/basketball/womens-college-basketball',
        'ncaasb':'sports/baseball/college-softball',
        'ncaavb':'sports/volleyball/womens-college-volleyball',
        'ncaalax':'sports/lacrosse/womens-college-lacrosse',
    }
    LABELS = {
        'nfl':'NFL','nba':'NBA','mlb':'MLB','nhl':'NHL','wnba':'WNBA',
        'cfl':'CFL','xfl':'XFL','ufl':'UFL','mls':'MLS','nwsl':'NWSL',
        'atp':'ATP','wta':'WTA','epl':'EPL','ucl':'UCL','laliga':'La Liga',
        'bundesliga':'Bundesliga','seriea':'Serie A','ligue1':'Ligue 1',
        'ncaafb':'NCAAF','ncaamb':'NCAAB','ncaabase':'NCAA Baseball',
        'ncaawb':'NCAA WBB','ncaasb':'NCAA Softball',
        'ncaavb':'NCAA VB','ncaalax':'NCAA Lax',
        'f1':'F1','nascar':'NASCAR','nascar-noaps':'NOAPS',
        'nascar-trucks':'Trucks','pga':'PGA',
    }
    live_parts=[]; final_parts=[]
    for sport_id in sports:
        label=LABELS.get(sport_id,sport_id.upper())
        try:
            if sport_id in ESPN_PATHS:
                url=f'https://site.api.espn.com/apis/site/v2/{ESPN_PATHS[sport_id]}/scoreboard'
                r=http.get(url,timeout=8)
                if not r.ok: continue
                events=r.json().get('events',[])
                if not events: continue
                live_strs=[]; final_strs=[]
                for ev in events[:12]:
                    comp=ev.get('competitions',[{}])[0]
                    competitors=comp.get('competitors',[])
                    if len(competitors)<2: continue
                    home=next((c for c in competitors if c.get('homeAway')=='home'),competitors[0])
                    away=next((c for c in competitors if c.get('homeAway')=='away'),competitors[1])
                    away_abbr=away.get('team',{}).get('abbreviation','?')
                    home_abbr=home.get('team',{}).get('abbreviation','?')
                    away_score=away.get('score',''); home_score=home.get('score','')
                    st=comp.get('status',{}).get('type',{})
                    state=st.get('state',''); detail=st.get('shortDetail','')
                    if state=='in':
                        live_strs.append(f'{away_abbr} {away_score} @ {home_abbr} {home_score} ({detail})')
                    elif state=='post':
                        final_strs.append(f'{away_abbr} {away_score} @ {home_abbr} {home_score} FINAL')
                if live_strs: live_parts.append(f'{label}: {"  ".join(live_strs)}')
                if final_strs: final_parts.append(f'{label}: {"  ".join(final_strs)}')
            elif sport_id=='nascar':
                nascar_headers={'User-Agent':'Mozilla/5.0','Accept':'application/json',
                                'Referer':'https://www.nascar.com/','Origin':'https://www.nascar.com'}
                live_data=None
                try:
                    lr=http.get('https://cf.nascar.com/live/feeds/live-feed.json',headers=nascar_headers,timeout=8)
                    if lr.ok:
                        lf=lr.json()
                        if lf.get('series_id')==1 and 1<=lf.get('flag_state',0)<=8: live_data=lf
                except Exception: pass
                if live_data:
                    run_name=live_data.get('run_name','')
                    lap=live_data.get('lap_number',0); total=live_data.get('laps_in_race',0)
                    vehicles=sorted(live_data.get('vehicles',[]),key=lambda v:v.get('running_position',99))
                    d_strs=[]
                    for v in vehicles[:5]:
                        pos=v.get('running_position','')
                        name=(v.get('driver',{}).get('full_name') or
                              f"{v.get('driver',{}).get('first_name','')} {v.get('driver',{}).get('last_name','')}").strip()
                        d_strs.append(f"P{pos} {name.split()[-1] if name else '?'}")
                    lap_str=f'Lap {lap}/{total}' if total else ''
                    live_parts.append(f"NASCAR ({run_name}): {'  '.join(d_strs)}  {lap_str}")
                else:
                    with get_db() as conn:
                        mc=conn.execute("SELECT data FROM motor_cache WHERE key='nascar-last'").fetchone()
                    if not mc: continue
                    nd=_jt.loads(mc['data'])
                    if isinstance(nd,str): nd=_jt.loads(nd)
                    if nd.get('race_date','')==today:
                        drivers=nd.get('drivers',[])[:5]; race=nd.get('run_name','')
                        d_strs=[f"P{d['pos']} {d['driver'].split()[-1]}" for d in drivers if d.get('pos') and d.get('driver')]
                        if d_strs: final_parts.append(f"NASCAR ({race}): {'  '.join(d_strs)}  FINAL")
            elif sport_id=='f1':
                with get_db() as conn:
                    mc=conn.execute("SELECT data FROM motor_cache WHERE key='f1-history'").fetchone()
                if not mc: continue
                fd=_jt.loads(mc['data'])
                if isinstance(fd,str): fd=_jt.loads(fd)
                races=fd.get('races',[])
                if not races: continue
                last=races[-1]
                if last.get('date','')==today:
                    race_name=last.get('raceName',''); results=last.get('results',[])[:5]
                    d_strs=[f"P{r['pos']} {r['driver'].split()[-1]}" for r in results if r.get('pos') and r.get('driver')]
                    if d_strs: final_parts.append(f"F1 ({race_name}): {'  '.join(d_strs)}  FINAL")
            elif sport_id=='pga':
                with get_db() as conn:
                    mc=conn.execute("SELECT data FROM motor_cache WHERE key='pga-2026-tournaments'").fetchone()
                if not mc: continue
                pd=_jt.loads(mc['data'])
                if isinstance(pd,str): pd=_jt.loads(pd)
                tours=pd.get('tournaments',[])
                active=next((t for t in tours if not t.get('is_complete') and t.get('players')),None)
                if not active: continue
                name=active.get('name',''); players=active.get('players',[])
                p_strs=[f"{p.get('player','').split()[-1]} {p.get('total','E')}" for p in players[:5] if p.get('player')]
                if p_strs: live_parts.append(f"PGA ({name}): {'  '.join(p_strs)}")
        except Exception as e:
            log.warning(f'[ticker/text] sport={sport_id} error: {e}')
    all_parts=live_parts+final_parts
    if not all_parts:
        return jsonify({'text':''})
    raw = '    ·    '.join(all_parts)
    # Pad with trailing spaces so the ticker scrolls fully off-screen before
    # repeating. This keeps text width (tw) large and stable between updates,
    # preventing the visible scroll-position jump when scores refresh.
    PAD_WIDTH = 300  # characters — wide enough for ~1920px at font 28
    padded = raw + ' ' * max(PAD_WIDTH - len(raw) % PAD_WIDTH, PAD_WIDTH)
    return jsonify({'text': padded})

@app.route('/ticker/text/<int:sb_id>', methods=['GET'])
def ticker_text(sb_id):
    """Per-scoreboard ticker text (backward compat). Delegates to shared helper."""
    import json as _jt
    try:
        with get_db() as conn:
            row = conn.execute('SELECT ticker_config FROM scoreboards WHERE id=?',(sb_id,)).fetchone()
        if not row: return jsonify({'error':'scoreboard not found'}),404
        sports = _jt.loads(row['ticker_config'] or '{}').get('sports',[])
    except Exception as e:
        return jsonify({'error':str(e)}),500
    return _ticker_text_for_sports(sports)

@app.route('/dispatcharr/groups', methods=['POST'])
def create_group():
    b = request.get_json(force=True)
    name = b.get('name','').strip()
    if not name: return jsonify({'error':'name required'}),400
    c = get_creds(); s,err = dispatcharr_session(c)
    if err: return jsonify({'error':err}),400
    try:
        r = s.post(f'{c["url"]}/api/channels/groups/',json={'name':name},timeout=15)
        r.raise_for_status()
        d = r.json()
        return jsonify({'id':d['id'],'name':d['name'],'m3u_count':0})
    except Exception as e: return jsonify({'error':str(e)}),500

@app.route('/dispatcharr/create', methods=['POST'])
def create_channels():
    c = get_creds(); s,err = dispatcharr_session(c)
    if err: return jsonify({'error':err}),400
    b = request.get_json(force=True)
    mode,sports = b.get('mode','both'),b.get('sports',[])
    num_mode,start = b.get('numberingMode','auto'),int(b.get('startChannel',900))
    group_id,profile_ids_raw = b.get('groupId'),b.get('profileIds')
    stream_profile_id = b.get('streamProfileId')
    assignments = {a['sportId']:a for a in (b.get('channelAssignments') or [])}
    base,created,errors,ch_num = c['url'],[],[],start
    def make_channel(name,num,stream_url=None,gid=None):
        payload = {'name':name,'channel_number':num,
                   'tvg_id':name.lower().replace(' ','-').replace('—','').strip('-')}
        if gid or group_id: payload['channel_group_id']=int(gid or group_id)
        # profileIds can be 'all', a list of ints, or None/empty
        if profile_ids_raw == 'all':
            # Fetch all profile IDs and assign to all
            pdata,perr = dispatcharr_get('/api/channels/profiles/', timeout=45)
            if not perr and pdata:
                items = pdata.get('results',pdata) if isinstance(pdata,dict) else pdata
                all_ids = [p['id'] for p in items if 'id' in p]
                if all_ids: payload['channel_profile_ids'] = all_ids
        elif profile_ids_raw:
            ids = [int(x) for x in profile_ids_raw if str(x).isdigit()]
            if ids: payload['channel_profile_ids'] = ids
        if stream_url: payload['url']=stream_url
        if stream_profile_id: payload['stream_profile_id']=int(stream_profile_id)
        try:
            r = s.post(f'{base}/api/channels/channels/',json=payload,timeout=15)
            r.raise_for_status(); created.append({'name':name,'number':num})
        except http.exceptions.HTTPError as e:
            try: detail=e.response.json()
            except Exception: detail=e.response.text[:300] if e.response else str(e)
            errors.append(f'{name}: {detail}')
        except Exception as e: errors.append(f'{name}: {str(e)}')
    if mode in ('combined','both'):
        make_channel('ScoreStream — All Sports',ch_num,f'{STREAM_BASE_URL}/hls/scorestream.m3u8' if STREAM_BASE_URL else None)
        ch_num+=1
    if mode in ('per_sport','both'):
        for sport in sports:
            sid,sname = sport.get('id',''),sport.get('name',sport.get('id',''))
            slug=sname.lower().replace(' ','-').replace("'",'')
            if num_mode=='manual' and sid in assignments:
                a=assignments[sid]; num=int(a.get('channelNumber') or ch_num); gid=a.get('groupId')
            else: num=ch_num; gid=None
            make_channel(f'ScoreStream — {sname}',num,f'{STREAM_BASE_URL}/hls/{slug}.m3u8' if STREAM_BASE_URL else None,gid=gid)
            ch_num+=1
    return jsonify({'created':created,'errors':errors})

# ── Scoreboard CRUD ───────────────────────────────────────────────────────────
def scoreboard_to_dict(r):
    import json as _json
    return {
        'id': r['id'], 'name': r['name'], 'slug': r['slug'],
        'sport_config': _json.loads(r['sport_config'] or '{}'),
        'motor_config': _json.loads(r['motor_config'] if 'motor_config' in r.keys() else '{}'),
        'use_default_fonts': bool(r['use_default_fonts'] if 'use_default_fonts' in r.keys() else 1),
        'use_default_colors': bool(r['use_default_colors'] if 'use_default_colors' in r.keys() else 1),
        'use_default_card_size': bool(r['use_default_card_size'] if 'use_default_card_size' in r.keys() else 1),
        'audio_playlist_id': r['audio_playlist_id'] if 'audio_playlist_id' in r.keys() else None,
        'team_config': _json.loads(r['team_config'] or '{}'),
        'display_config': _json.loads(r['display_config'] or '{}'),
        'dispatcharr_channel_id': r['dispatcharr_channel_id'],
        'dispatcharr_stream_id': r['dispatcharr_stream_id'],
        'dispatcharr_channel_number': r['dispatcharr_channel_number'],
        'dispatcharr_group_id': r['dispatcharr_group_id'],
        'dispatcharr_profile_ids': _json.loads(r['dispatcharr_profile_ids'] or 'null'),
        'dispatcharr_stream_profile_id': r['dispatcharr_stream_profile_id'],
        'dispatcharr_logo_id': r['dispatcharr_logo_id'],
        'dispatcharr_logo_url': r['dispatcharr_logo_url'] if 'dispatcharr_logo_url' in r.keys() else None,
        'is_default': bool(r['is_default']),
        'audio_mode': r['audio_mode'] if r['audio_mode'] else 'none',
        'audio_source_url': r['audio_source_url'] or '',
        'created_at': r['created_at'], 'updated_at': r['updated_at']
    }

@app.route('/scoreboards', methods=['GET'])
def scoreboards_list():
    with get_db() as conn:
        rows = conn.execute('SELECT * FROM scoreboards ORDER BY is_default DESC, name').fetchall()
    return jsonify({'scoreboards': [scoreboard_to_dict(r) for r in rows]})

@app.route('/scoreboards', methods=['POST'])
def scoreboard_create():
    import json as _json, re
    b = request.get_json(force=True)
    name = b.get('name','').strip()
    if not name: return jsonify({'error':'name required'}),400
    slug = re.sub(r'[^a-z0-9]+','-', name.lower()).strip('-') or 'scoreboard'
    # Ensure slug uniqueness
    with get_db() as conn:
        base_slug, i = slug, 1
        while conn.execute('SELECT id FROM scoreboards WHERE slug=?',(slug,)).fetchone():
            slug = f'{base_slug}-{i}'; i += 1
        conn.execute(
            'INSERT INTO scoreboards(name,slug,sport_config,team_config,display_config,motor_config) VALUES(?,?,?,?,?,?)',
            (name, slug,
             _json.dumps(b.get('sport_config',{})),
             _json.dumps(b.get('team_config',{})),
             _json.dumps(b.get('display_config',{})),
             _json.dumps(b.get('motor_config',{}))))
        conn.commit()
        row = conn.execute('SELECT * FROM scoreboards WHERE slug=?',(slug,)).fetchone()
    notify_stream_manager()
    return jsonify(scoreboard_to_dict(row)), 201

@app.route('/scoreboards/active', methods=['GET'])
def scoreboard_active():
    with get_db() as conn:
        row = conn.execute('SELECT * FROM scoreboards WHERE is_default=1').fetchone()
    if not row: return jsonify({'error':'no active scoreboard'}),404
    return jsonify(scoreboard_to_dict(row))

@app.route('/scoreboards/by-slug/<slug>', methods=['GET'])
def scoreboard_by_slug(slug):
    with get_db() as conn:
        row = conn.execute('SELECT * FROM scoreboards WHERE slug=?',(slug,)).fetchone()
    if not row: return jsonify({'error':'not found'}),404
    return jsonify(scoreboard_to_dict(row))

@app.route('/scoreboards/<int:sid>', methods=['GET'])
def scoreboard_get(sid):
    with get_db() as conn:
        row = conn.execute('SELECT * FROM scoreboards WHERE id=?',(sid,)).fetchone()
    if not row: return jsonify({'error':'not found'}),404
    return jsonify(scoreboard_to_dict(row))

@app.route('/scoreboards/<int:sid>', methods=['PUT','PATCH'])
def scoreboard_update(sid):
    import json as _json, re
    b = request.get_json(force=True)
    with get_db() as conn:
        row = conn.execute('SELECT * FROM scoreboards WHERE id=?',(sid,)).fetchone()
        if not row: return jsonify({'error':'not found'}),404
        fields, vals = [], []
        for col in ['name','sport_config','team_config','display_config',
                    'dispatcharr_channel_id','dispatcharr_channel_number',
                    'dispatcharr_group_id','dispatcharr_stream_profile_id','dispatcharr_logo_id',
                    'audio_mode','audio_source_url','motor_config','use_default_fonts','use_default_colors','use_default_card_size','audio_playlist_id']:
            if col in b:
                fields.append(f'{col}=?')
                val = b[col]
                if col in ('sport_config','team_config','display_config','motor_config'):
                    val = _json.dumps(val)
                vals.append(val)
        if 'dispatcharr_profile_ids' in b:
            fields.append('dispatcharr_profile_ids=?')
            vals.append(_json.dumps(b['dispatcharr_profile_ids']))
        # Auto-update slug if name changed and not default
        if 'name' in b and not row['is_default']:
            new_slug = re.sub(r'[^a-z0-9]+','-', b['name'].lower()).strip('-')
            base_slug, i = new_slug, 1
            while True:
                existing = conn.execute('SELECT id FROM scoreboards WHERE slug=? AND id!=?',(new_slug,sid)).fetchone()
                if not existing: break
                new_slug = f'{base_slug}-{i}'; i += 1
            fields.append('slug=?'); vals.append(new_slug)
        if not fields: return jsonify(scoreboard_to_dict(row))
        fields.append("updated_at=datetime('now')")
        vals.append(sid)
        conn.execute(f'UPDATE scoreboards SET {",".join(fields)} WHERE id=?', vals)
        conn.commit()
        row = conn.execute('SELECT * FROM scoreboards WHERE id=?',(sid,)).fetchone()
    notify_stream_manager()
    return jsonify(scoreboard_to_dict(row))

@app.route('/scoreboards/<int:sid>', methods=['DELETE'])
def scoreboard_delete(sid):
    with get_db() as conn:
        row = conn.execute('SELECT * FROM scoreboards WHERE id=?',(sid,)).fetchone()
        if not row: return jsonify({'error':'not found'}),404
        if row['is_default']: return jsonify({'error':'cannot delete default scoreboard'}),400
        # Clean up Dispatcharr channel and stream before removing from DB
        channel_id = row['dispatcharr_channel_id']
        stream_id  = row['dispatcharr_stream_id']
        if channel_id or stream_id:
            try:
                c = get_creds()
                s, err = dispatcharr_session(c)
                if not err:
                    base = c['url']
                    if channel_id:
                        s.delete(f'{base}/api/channels/channels/{channel_id}/', timeout=10)
                    if stream_id:
                        s.delete(f'{base}/api/channels/streams/{stream_id}/', timeout=10)
            except Exception as e:
                log.warning(f'Dispatcharr cleanup failed for scoreboard {sid}: {e}')
        conn.execute('DELETE FROM scoreboards WHERE id=?',(sid,))
        conn.commit()
    notify_stream_manager()
    return jsonify({'deleted':sid})

@app.route('/scoreboards/<int:sid>/duplicate', methods=['POST'])
def scoreboard_duplicate(sid):
    import json as _json, re
    b = request.get_json(force=True) or {}
    with get_db() as conn:
        src = conn.execute('SELECT * FROM scoreboards WHERE id=?',(sid,)).fetchone()
        if not src: return jsonify({'error':'not found'}),404
        new_name = b.get('name', src['name'] + ' (Copy)')
        slug = re.sub(r'[^a-z0-9]+','-', new_name.lower()).strip('-')
        base_slug, i = slug, 1
        while conn.execute('SELECT id FROM scoreboards WHERE slug=?',(slug,)).fetchone():
            slug = f'{base_slug}-{i}'; i += 1
        conn.execute(
            'INSERT INTO scoreboards(name,slug,sport_config,team_config,display_config,motor_config) VALUES(?,?,?,?,?,?)',
            (new_name, slug, src['sport_config'], src['team_config'], src['display_config'], src.get('motor_config','{}')))
        conn.commit()
        row = conn.execute('SELECT * FROM scoreboards WHERE slug=?',(slug,)).fetchone()
    notify_stream_manager()
    return jsonify(scoreboard_to_dict(row)), 201

# ── Dispatcharr channel push / update per scoreboard ─────────────────────────
@app.route('/scoreboards/<int:sid>/stream_url', methods=['GET'])
def scoreboard_stream_url(sid):
    with get_db() as conn:
        sb = conn.execute('SELECT slug FROM scoreboards WHERE id=?',(sid,)).fetchone()
    if not sb: return jsonify({'error':'not found'}),404
    url = f'{STREAM_BASE_URL}/hls/{sb["slug"]}.m3u8' if STREAM_BASE_URL else None
    return jsonify({'stream_url': url, 'base_url': STREAM_BASE_URL})

@app.route('/backup/export', methods=['GET'])
def backup_export():
    import json as _json, datetime
    with get_db() as conn:
        scoreboards = [scoreboard_to_dict(r) for r in conn.execute('SELECT * FROM scoreboards').fetchall()]
        settings = {r['key']: r['value'] for r in conn.execute(
            "SELECT key,value FROM settings WHERE key NOT IN ('dispatcharr_password','dispatcharr_api_token')"
        ).fetchall()}
    payload = {'version':'1','exported_at':datetime.datetime.utcnow().isoformat(),
               'scoreboards':scoreboards,'settings':settings}
    from flask import Response
    return Response(_json.dumps(payload,indent=2), mimetype='application/json',
        headers={'Content-Disposition':'attachment; filename=scorestream-backup.json'})

@app.route('/backup/restore', methods=['POST'])
def backup_restore():
    import json as _json
    b = request.get_json(force=True)
    if not b or b.get('version') != '1':
        return jsonify({'error':'Invalid backup file — missing version field'}),400
    sb_count = 0; settings_count = 0
    skip_keys = {'dispatcharr_password','dispatcharr_api_token'}
    try:
        with get_db() as conn:
            for key,value in (b.get('settings') or {}).items():
                if key not in skip_keys:
                    conn.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,str(value)))
                    settings_count += 1
            for sb in (b.get('scoreboards') or []):
                slug = sb.get('slug')
                if not slug: continue
                existing = conn.execute('SELECT id FROM scoreboards WHERE slug=?',(slug,)).fetchone()
                sc = _json.dumps(sb.get('sport_config',{}))
                tc = _json.dumps(sb.get('team_config',{}))
                dc = _json.dumps(sb.get('display_config',{}))
                if existing:
                    conn.execute(
                        "UPDATE scoreboards SET name=?,is_default=?,sport_config=?,team_config=?,display_config=?,updated_at=datetime('now') WHERE slug=?",
                        (sb.get('name'),sb.get('is_default',0),sc,tc,dc,slug))
                else:
                    conn.execute(
                        "INSERT INTO scoreboards(name,slug,is_default,sport_config,team_config,display_config,created_at,updated_at) VALUES(?,?,?,?,?,?,datetime('now'),datetime('now'))",
                        (sb.get('name'),slug,sb.get('is_default',0),sc,tc,dc))
                sb_count += 1
            conn.commit()
        return jsonify({'status':'restored','scoreboards':sb_count,'settings':settings_count})
    except Exception as e:
        return jsonify({'error':str(e)}),500

@app.route('/scoreboards/<int:sid>/unlink', methods=['POST'])
def scoreboard_unlink(sid):
    """Clear stored Dispatcharr IDs from a scoreboard without deleting the channel."""
    with get_db() as conn:
        sb = conn.execute('SELECT * FROM scoreboards WHERE id=?',(sid,)).fetchone()
        if not sb: return jsonify({'error':'scoreboard not found'}),404
        conn.execute("""UPDATE scoreboards SET
            dispatcharr_channel_id=NULL, dispatcharr_stream_id=NULL,
            dispatcharr_channel_number=NULL, dispatcharr_group_id=NULL,
            dispatcharr_profile_ids=NULL, dispatcharr_stream_profile_id=NULL,
            dispatcharr_logo_id=NULL, updated_at=datetime('now')
            WHERE id=?""", (sid,))
        conn.commit()
        row = conn.execute('SELECT * FROM scoreboards WHERE id=?',(sid,)).fetchone()
    return jsonify({'status':'unlinked','scoreboard':scoreboard_to_dict(row)})

@app.route('/scoreboards/<int:sid>/push', methods=['POST'])
def scoreboard_push(sid):
    """Push or update a scoreboard as a Dispatcharr channel.

    Dispatcharr architecture (confirmed from API schema):
      - Stream  = the URL source  → POST/PATCH /api/channels/streams/
      - Channel = the container   → POST/PATCH /api/channels/channels/
      - Channel references Stream via  streams: [stream_id]
      - stream_profile_id lives on BOTH stream and channel (stream takes precedence)

    Create flow  (no existing channel_id):
      1. POST /api/channels/streams/  → get stream_id
      2. POST /api/channels/channels/ with streams:[stream_id]

    Update flow (existing channel_id stored):
      1. PATCH /api/channels/streams/{stream_id}/  → update URL if changed
      2. PATCH /api/channels/channels/{channel_id}/ → update metadata
    """
    import json as _json
    b = request.get_json(force=True)
    with get_db() as conn:
        sb = conn.execute('SELECT * FROM scoreboards WHERE id=?',(sid,)).fetchone()
    if not sb: return jsonify({'error':'scoreboard not found'}),404

    c = get_creds(); s,err = dispatcharr_session(c)
    if err: return jsonify({'error':err}),400

    # Wizard values override stored values; stored values are fallback
    channel_name    = b.get('channelName') or sb['name']
    channel_number  = b.get('channelNumber') or sb['dispatcharr_channel_number']
    group_id        = b.get('groupId') or sb['dispatcharr_group_id']
    profile_ids_raw = b.get('profileIds', _json.loads(sb['dispatcharr_profile_ids'] or 'null'))
    stream_prof_id  = b.get('streamProfileId') or sb['dispatcharr_stream_profile_id']
    logo_id         = b.get('logoId') or sb['dispatcharr_logo_id']
    logo_url        = b.get('logoUrl')  # URL-based logo to upload to Dispatcharr
    # Rewrite localhost/127.0.0.1 to STREAM_BASE_URL so Dispatcharr can fetch the logo.
    # Inside Dispatcharr's container, 'localhost' resolves to Dispatcharr itself — never nginx.
    # STREAM_BASE_URL (the LAN IP) is reachable from any container regardless of stack topology.
    if logo_url and STREAM_BASE_URL:
        from urllib.parse import urlparse, urlunparse
        _lp = urlparse(logo_url)
        if _lp.hostname in ('localhost', '127.0.0.1'):
            _bp = urlparse(STREAM_BASE_URL)
            logo_url = urlunparse(_lp._replace(scheme=_bp.scheme, netloc=_bp.netloc))
    stream_url      = f'{STREAM_BASE_URL}/hls/{sb["slug"]}.m3u8' if STREAM_BASE_URL else None

    existing_channel_id = sb['dispatcharr_channel_id']
    existing_stream_id  = sb['dispatcharr_stream_id']
    base = c['url']

    # If a new logoUrl is provided, upload it — or find existing entry if Dispatcharr
    # already has a logo for that URL (it enforces URL uniqueness and returns 400).
    logo_debug = {}
    if logo_url:
        try:
            logo_r = s.post(f'{base}/api/channels/logos/', json={'url': logo_url, 'name': channel_name}, timeout=15)
            logo_debug['upload_status'] = logo_r.status_code
            if logo_r.ok:
                logo_id = str(logo_r.json().get('id',''))
                logo_debug['logo_id'] = logo_id
                log.info(f'Logo uploaded for scoreboard {sid}: id={logo_id}')
            elif logo_r.status_code == 400 and 'already exists' in logo_r.text.lower():
                # Dispatcharr enforces URL uniqueness — bust the collision with a version param.
                # nginx ignores unknown query params so the same image file is served.
                import time as _t
                bust_url = logo_url + ('&' if '?' in logo_url else '?') + f'_v={int(_t.time())}'
                try:
                    retry_r = s.post(f'{base}/api/channels/logos/',
                                     json={'url': bust_url, 'name': channel_name}, timeout=15)
                    if retry_r.ok:
                        logo_id = str(retry_r.json().get('id',''))
                        logo_debug['logo_id'] = logo_id
                        log.info(f'Logo created with busted URL id={logo_id}')
                    else:
                        logo_debug['retry_status'] = retry_r.status_code
                        logo_debug['retry_error'] = retry_r.text[:200]
                        log.warning(f'Logo retry failed: {retry_r.status_code}')
                except Exception as se:
                    logo_debug['retry_error'] = str(se)
                    log.warning(f'Logo retry exception: {se}')
            else:
                logo_debug['upload_response'] = logo_r.text[:200]
                log.warning(f'Logo upload failed ({logo_r.status_code}): {logo_r.text[:200]}')
        except Exception as e:
            logo_debug['upload_error'] = str(e)
            log.warning(f'Logo upload exception: {e}')

    # Resolve profile_ids to a list of ints (or None)
    resolved_profile_ids = None
    if profile_ids_raw == 'all':
        pdata,perr = dispatcharr_get('/api/channels/profiles/', timeout=45)
        if not perr and pdata:
            items = pdata.get('results',pdata) if isinstance(pdata,dict) else pdata
            resolved_profile_ids = [p['id'] for p in items if 'id' in p] or None
    elif profile_ids_raw:
        ids = [int(x) for x in (profile_ids_raw if isinstance(profile_ids_raw,list) else []) if str(x).isdigit()]
        resolved_profile_ids = ids or None

    try:
        # ── STEP 1: Stream (the HLS URL source) ──────────────────────────────
        stream_payload = {
            'name': channel_name,
            'is_custom': True,
        }
        if stream_url:      stream_payload['url']              = stream_url
        if group_id:        stream_payload['channel_group']    = int(group_id)
        if stream_prof_id:  stream_payload['stream_profile_id']= int(stream_prof_id)

        if existing_stream_id:
            # Update existing stream
            r = s.patch(f'{base}/api/channels/streams/{existing_stream_id}/',
                        json=stream_payload, timeout=15)
            r.raise_for_status()
            stream_id = existing_stream_id
            log.info(f'Stream {stream_id} updated for scoreboard {sid}')
        else:
            # Create new stream
            r = s.post(f'{base}/api/channels/streams/', json=stream_payload, timeout=15)
            r.raise_for_status()
            stream_id = str(r.json().get('id',''))
            log.info(f'Stream {stream_id} created for scoreboard {sid}')

        # ── STEP 2: Channel (the container) ──────────────────────────────────
        channel_payload = {'name': channel_name, 'streams': [int(stream_id)]}
        if channel_number:          channel_payload['channel_number']  = float(channel_number)
        if group_id:                channel_payload['channel_group_id']= int(group_id)
        if stream_prof_id:          channel_payload['stream_profile_id']= int(stream_prof_id)
        if logo_id:
            channel_payload['logo_id'] = int(logo_id)  # some Dispatcharr versions
            channel_payload['logo']    = int(logo_id)  # other Dispatcharr versions
        if resolved_profile_ids:    channel_payload['channel_profile_ids'] = resolved_profile_ids

        if existing_channel_id:
            # Update existing channel
            r = s.patch(f'{base}/api/channels/channels/{existing_channel_id}/',
                        json=channel_payload, timeout=15)
            r.raise_for_status()
            channel_id = existing_channel_id
            action = 'updated'
            log.info(f'Channel {channel_id} updated for scoreboard {sid}')
        else:
            # Create new channel
            r = s.post(f'{base}/api/channels/channels/', json=channel_payload, timeout=15)
            r.raise_for_status()
            channel_id = str(r.json().get('id',''))
            action = 'created'
            log.info(f'Channel {channel_id} created for scoreboard {sid}')

        # ── STEP 3: Persist all Dispatcharr IDs back to scoreboard ───────────
        with get_db() as conn:
            conn.execute("""UPDATE scoreboards SET
                dispatcharr_channel_id=?, dispatcharr_stream_id=?,
                dispatcharr_channel_number=?, dispatcharr_group_id=?,
                dispatcharr_profile_ids=?, dispatcharr_stream_profile_id=?,
                dispatcharr_logo_id=?, dispatcharr_logo_url=?,
                updated_at=datetime('now')
                WHERE id=?""",
                (channel_id, stream_id,
                 int(channel_number) if channel_number else None,
                 str(group_id) if group_id else None,
                 _json.dumps(profile_ids_raw) if profile_ids_raw else None,
                 str(stream_prof_id) if stream_prof_id else None,
                 str(logo_id) if logo_id else None,
                 logo_url if logo_url else None,
                 sid))
            conn.commit()
            row = conn.execute('SELECT * FROM scoreboards WHERE id=?',(sid,)).fetchone()

        return jsonify({
            'action': action,
            'channel_id': channel_id,
            'stream_id': stream_id,
            'channel_number': channel_number,
            'channel_name': channel_name,
            'stream_url': stream_url,
            'logo_id': logo_id,
            'logo_debug': logo_debug,
            'scoreboard': scoreboard_to_dict(row)
        })

    except http.exceptions.HTTPError as e:
        try: detail = e.response.json()
        except Exception: detail = e.response.text[:300] if e.response else str(e)
        log.error(f'Dispatcharr push error for scoreboard {sid}: {detail}')
        return jsonify({'error': str(detail)}), 500
    except Exception as e:
        log.error(f'Push exception for scoreboard {sid}: {e}')
        return jsonify({'error': str(e)}), 500

# ── NCAA Read endpoints ────────────────────────────────────────────────────────
@app.route('/ncaa/schools', methods=['GET'])
def ncaa_schools_list():
    q=request.args.get('q','').strip().lower()
    division=request.args.get('division','').strip()
    try:
        with get_db() as conn:
            if q:
                rows=conn.execute("""SELECT DISTINCT s.* FROM ncaa_schools s
                    WHERE LOWER(s.espn_abbr) LIKE ? OR LOWER(s.full_name) LIKE ? OR LOWER(s.location) LIKE ?
                    ORDER BY s.full_name""",('%'+q+'%','%'+q+'%','%'+q+'%')).fetchall()
            elif division:
                rows=conn.execute("""SELECT DISTINCT s.* FROM ncaa_schools s
                    JOIN ncaa_programs p ON p.school_id=s.id
                    WHERE p.division=? ORDER BY s.full_name""",(division,)).fetchall()
            else:
                rows=conn.execute('SELECT * FROM ncaa_schools ORDER BY full_name').fetchall()
        schools=[school_to_dict(r) for r in rows]
        return jsonify({'schools':schools,'total':len(schools)})
    except Exception as e: return jsonify({'error':str(e)}),500

@app.route('/ncaa/schools/<int:sid>', methods=['GET'])
def ncaa_school_get(sid):
    try:
        with get_db() as conn:
            s=conn.execute('SELECT * FROM ncaa_schools WHERE id=?',(sid,)).fetchone()
            if not s: return jsonify({'error':'School not found'}),404
            progs=conn.execute("""SELECT p.*,s.espn_abbr,s.full_name,s.location,
                s.color,s.alt_color,s.logo_url,s.slug
                FROM ncaa_programs p JOIN ncaa_schools s ON s.id=p.school_id
                WHERE p.school_id=? ORDER BY p.sport_id""",(sid,)).fetchall()
        d=school_to_dict(s); d['programs']=[program_to_dict(p) for p in progs]
        return jsonify(d)
    except Exception as e: return jsonify({'error':str(e)}),500

@app.route('/ncaa/programs', methods=['GET'])
def ncaa_programs_list():
    sport=request.args.get('sport','').strip()
    gender=request.args.get('gender','').strip()
    division=request.args.get('division','').strip()
    q=request.args.get('q','').strip().lower()
    clauses,params=[],[]
    if sport: clauses.append('p.sport_id=?'); params.append(sport)
    if gender: clauses.append('p.gender=?'); params.append(gender)
    if division: clauses.append('p.division=?'); params.append(division)
    if q:
        clauses.append('(LOWER(p.nick) LIKE ? OR LOWER(p.display_name) LIKE ? OR LOWER(p.short_name) LIKE ? OR LOWER(s.espn_abbr) LIKE ? OR LOWER(s.full_name) LIKE ? OR LOWER(s.location) LIKE ?)')
        params.extend(['%'+q+'%']*6)
    where=('WHERE '+' AND '.join(clauses)) if clauses else ''
    try:
        with get_db() as conn:
            rows=conn.execute(f"""SELECT p.*,s.espn_abbr,s.full_name,s.location,
                s.color,s.alt_color,s.logo_url,s.slug
                FROM ncaa_programs p JOIN ncaa_schools s ON s.id=p.school_id
                {where} ORDER BY s.full_name,p.sport_id""",params).fetchall()
        programs=[program_to_dict(r) for r in rows]
        return jsonify({'programs':programs,'total':len(programs)})
    except Exception as e: return jsonify({'error':str(e)}),500

@app.route('/ncaa/programs/<int:pid>', methods=['GET'])
def ncaa_program_get(pid):
    try:
        with get_db() as conn:
            row=conn.execute("""SELECT p.*,s.espn_abbr,s.full_name,s.location,
                s.color,s.alt_color,s.logo_url,s.slug
                FROM ncaa_programs p JOIN ncaa_schools s ON s.id=p.school_id
                WHERE p.id=?""",(pid,)).fetchone()
        if not row: return jsonify({'error':'Program not found'}),404
        return jsonify(program_to_dict(row))
    except Exception as e: return jsonify({'error':str(e)}),500

@app.route('/ncaa/teams', methods=['GET'])
def ncaa_teams_legacy():
    return ncaa_programs_list()

@app.route('/ncaa/sync/status', methods=['GET'])
def ncaa_sync_status():
    try:
        with get_db() as conn:
            sc=conn.execute('SELECT COUNT(*) FROM ncaa_schools').fetchone()[0]
            pc=conn.execute('SELECT COUNT(*) FROM ncaa_programs').fetchone()[0]
        return jsonify({'status':db_get('espn_sync_status','never'),'last_synced':db_get('last_espn_sync',''),'school_count':sc,'program_count':pc})
    except Exception as e: return jsonify({'error':str(e)}),500

@app.route('/ncaa/sync', methods=['POST'])
def ncaa_sync_trigger():
    threading.Thread(target=attempt_espn_sync, daemon=True, name='ncaa-manual-sync').start()
    return jsonify({'status':'accepted','message':'Sync started in background'})

# ── NCAA CRUD — Coming Soon (endpoints live, UI gated) ─────────────────────────
@app.route('/ncaa/schools', methods=['POST'])
def ncaa_school_create():
    b=request.get_json(force=True)
    abbr=(b.get('espn_abbr') or '').strip().upper()
    name=(b.get('full_name') or '').strip()
    if not abbr or not name: return jsonify({'error':'espn_abbr and full_name required'}),400
    try:
        with get_db() as conn:
            conn.execute("INSERT INTO ncaa_schools(espn_abbr,full_name,location,color,sync_source) VALUES(?,?,?,?,'manual')",
                (abbr,name,(b.get('location') or '').strip(),(b.get('color') or '333333').strip()))
            conn.commit()
            row=conn.execute('SELECT * FROM ncaa_schools WHERE espn_abbr=?',(abbr,)).fetchone()
        return jsonify(school_to_dict(row)),201
    except sqlite3.IntegrityError: return jsonify({'error':f'"{abbr}" already exists'}),409
    except Exception as e: return jsonify({'error':str(e)}),500

@app.route('/ncaa/schools/<int:sid>', methods=['PUT'])
def ncaa_school_update(sid):
    b=request.get_json(force=True)
    try:
        with get_db() as conn:
            ex=conn.execute('SELECT * FROM ncaa_schools WHERE id=?',(sid,)).fetchone()
            if not ex: return jsonify({'error':'School not found'}),404
            e=school_to_dict(ex)
            conn.execute("UPDATE ncaa_schools SET espn_abbr=?,full_name=?,location=?,color=?,updated_at=datetime('now') WHERE id=?",
                ((b.get('espn_abbr') or e['espn_abbr']).strip().upper(),
                 (b.get('full_name') or e['full_name']).strip(),
                 (b.get('location')  or e['location']  or '').strip(),
                 (b.get('color')     or e['color']     or '333333').strip(),sid))
            conn.commit()
            updated=conn.execute('SELECT * FROM ncaa_schools WHERE id=?',(sid,)).fetchone()
        return jsonify(school_to_dict(updated))
    except sqlite3.IntegrityError: return jsonify({'error':'Abbreviation already in use'}),409
    except Exception as e: return jsonify({'error':str(e)}),500

@app.route('/ncaa/schools/<int:sid>', methods=['DELETE'])
def ncaa_school_delete(sid):
    try:
        with get_db() as conn:
            row=conn.execute('SELECT espn_abbr FROM ncaa_schools WHERE id=?',(sid,)).fetchone()
            if not row: return jsonify({'error':'School not found'}),404
            conn.execute('DELETE FROM ncaa_schools WHERE id=?',(sid,)); conn.commit()
        return jsonify({'status':'deleted','id':sid})
    except Exception as e: return jsonify({'error':str(e)}),500

@app.route('/ncaa/programs', methods=['POST'])
def ncaa_program_create():
    b=request.get_json(force=True)
    school_id,sport_id,gender = b.get('school_id'),(b.get('sport_id') or '').strip(),(b.get('gender') or '').strip()
    if not school_id or not sport_id or not gender:
        return jsonify({'error':'school_id, sport_id, gender required'}),400
    try:
        div=(b.get('division') or 'unknown').strip()
        with get_db() as conn:
            if not conn.execute('SELECT id FROM ncaa_schools WHERE id=?',(school_id,)).fetchone():
                return jsonify({'error':'School not found'}),404
            conn.execute("INSERT INTO ncaa_programs(school_id,sport_id,gender,division,nick,display_name,short_name) VALUES(?,?,?,?,?,?,?)",
                (school_id,sport_id,gender,div,(b.get('nick') or '').strip(),(b.get('display_name') or '').strip(),(b.get('short_name') or '').strip()))
            conn.commit()
            row=conn.execute("""SELECT p.*,s.espn_abbr,s.full_name,s.location,s.color,s.alt_color,s.logo_url,s.slug
                FROM ncaa_programs p JOIN ncaa_schools s ON s.id=p.school_id
                WHERE p.school_id=? AND p.sport_id=? AND p.division=?""",(school_id,sport_id,div)).fetchone()
        return jsonify(program_to_dict(row)),201
    except sqlite3.IntegrityError: return jsonify({'error':'Program already exists for this school/sport/division'}),409
    except Exception as e: return jsonify({'error':str(e)}),500

@app.route('/ncaa/programs/<int:pid>', methods=['PUT'])
def ncaa_program_update(pid):
    b=request.get_json(force=True)
    try:
        with get_db() as conn:
            ex=conn.execute("""SELECT p.*,s.espn_abbr,s.full_name,s.location,s.color,s.alt_color,s.logo_url,s.slug
                FROM ncaa_programs p JOIN ncaa_schools s ON s.id=p.school_id WHERE p.id=?""",(pid,)).fetchone()
            if not ex: return jsonify({'error':'Program not found'}),404
            e=program_to_dict(ex)
            conn.execute("UPDATE ncaa_programs SET sport_id=?,gender=?,division=?,nick=?,display_name=?,short_name=?,updated_at=datetime('now') WHERE id=?",
                (b.get('sport_id',e['sport_id']),b.get('gender',e['gender']),b.get('division',e['division']),
                 b.get('nick',e['nick'] or ''),b.get('display_name',e['display_name'] or ''),b.get('short_name',e['short_name'] or ''),pid))
            conn.commit()
            updated=conn.execute("""SELECT p.*,s.espn_abbr,s.full_name,s.location,s.color,s.alt_color,s.logo_url,s.slug
                FROM ncaa_programs p JOIN ncaa_schools s ON s.id=p.school_id WHERE p.id=?""",(pid,)).fetchone()
        return jsonify(program_to_dict(updated))
    except sqlite3.IntegrityError: return jsonify({'error':'Program already exists for this school/sport/division'}),409
    except Exception as e: return jsonify({'error':str(e)}),500

@app.route('/ncaa/programs/<int:pid>', methods=['DELETE'])
def ncaa_program_delete(pid):
    try:
        with get_db() as conn:
            if not conn.execute('SELECT id FROM ncaa_programs WHERE id=?',(pid,)).fetchone():
                return jsonify({'error':'Program not found'}),404
            conn.execute('DELETE FROM ncaa_programs WHERE id=?',(pid,)); conn.commit()
        return jsonify({'status':'deleted','id':pid})
    except Exception as e: return jsonify({'error':str(e)}),500

# ── Config ────────────────────────────────────────────────────────────────────
@app.route('/config', methods=['GET'])
def get_config_route():
    try:
        with get_db() as conn:
            rows=conn.execute('SELECT key,value FROM settings').fetchall()
            data={r['key']:r['value'] for r in rows}
            if 'dispatcharr_password' in data: data['dispatcharr_password']='***'
            return jsonify(data)
    except Exception as e: return jsonify({'error':str(e)}),500

@app.route('/config', methods=['POST'])
def set_config_route():
    body=request.get_json(force=True)
    for k,v in body.items(): db_set(k,str(v))
    return jsonify({'status':'saved'})

# ── Entry ─────────────────────────────────────────────────────────────────────
# ── Audio Library ────────────────────────────────────────────────────────────
import os as _os, uuid as _uuid
from werkzeug.utils import secure_filename as _secure_filename

AUDIO_DIR      = _os.environ.get('AUDIO_DIR', '/audio_library')
_os.makedirs(AUDIO_DIR, exist_ok=True)

CUSTOM_LOGOS_DIR = _os.path.join(_os.path.dirname(DB_PATH), 'logos', 'custom')
_os.makedirs(CUSTOM_LOGOS_DIR, exist_ok=True)
_LOGO_ALLOWED_EXT = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg'}

@app.route('/upload-logo', methods=['POST'])
def upload_logo():
    """Accept a multipart logo file, save it, return the public URL."""
    if 'file' not in request.files:
        return jsonify({'error': 'no file field'}), 400
    f = request.files['file']
    if not f.filename:
        return jsonify({'error': 'empty filename'}), 400
    ext = _os.path.splitext(_secure_filename(f.filename))[1].lower()
    if ext not in _LOGO_ALLOWED_EXT:
        return jsonify({'error': f'unsupported type: {ext}'}), 400
    fname = f'{_uuid.uuid4().hex}{ext}'
    f.save(_os.path.join(CUSTOM_LOGOS_DIR, fname))
    url = f'{STREAM_BASE_URL}/api/logos/custom/{fname}' if STREAM_BASE_URL else f'/api/logos/custom/{fname}'
    return jsonify({'url': url})

@app.route('/logos/custom/<filename>', methods=['GET'])
def serve_custom_logo(filename):
    safe = _secure_filename(filename)
    return send_from_directory(CUSTOM_LOGOS_DIR, safe)

@app.route('/audio/library/register', methods=['POST'])
def audio_library_register():
    """Register a file into the library. Use force=true to skip file existence check (for built-in tracks)."""
    try:
        b = request.get_json(force=True) or {}
        filename = b.get('filename','').strip()
        display_name = b.get('display_name', filename).strip()
        force = b.get('force', False)
        if not filename:
            return jsonify({'error':'filename required'}), 400
        full_path = _os.path.join(AUDIO_DIR, filename)
        if not force and not _os.path.exists(full_path):
            return jsonify({'error':f'File not found in audio library: {filename}'}), 404
        file_size = _os.path.getsize(full_path) if _os.path.exists(full_path) else 0
        with get_db() as conn:
            existing = conn.execute('SELECT id FROM audio_library WHERE filename=?',(filename,)).fetchone()
            if existing:
                return jsonify({'ok':True,'id':existing['id'],'status':'already_registered'})
            cur = conn.execute('INSERT INTO audio_library(filename,display_name,file_size) VALUES(?,?,?)',
                               (filename, display_name, file_size))
            conn.commit()
            return jsonify({'ok':True,'id':cur.lastrowid,'status':'registered'})
    except Exception as e:
        return jsonify({'error':str(e)}),500

@app.route('/audio/library', methods=['GET'])
def audio_library_list():
    try:
        with get_db() as conn:
            rows = conn.execute('SELECT * FROM audio_library ORDER BY uploaded_at DESC').fetchall()
            return jsonify({'tracks': [dict(r) for r in rows]})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/audio/library', methods=['POST'])
def audio_library_upload():
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        f = request.files['file']
        if not f.filename:
            return jsonify({'error': 'Empty filename'}), 400
        # Only allow audio files
        allowed = {'.mp3','.ogg','.aac','.m4a','.flac','.wav','.opus'}
        ext = _os.path.splitext(f.filename)[1].lower()
        if ext not in allowed:
            return jsonify({'error': f'File type {ext} not allowed. Use: {", ".join(allowed)}'}), 400
        # Save with unique name
        safe_name = f'{_uuid.uuid4().hex}{ext}'
        save_path = _os.path.join(AUDIO_DIR, safe_name)
        f.save(save_path)
        file_size = _os.path.getsize(save_path)
        display_name = _os.path.splitext(f.filename)[0]
        with get_db() as conn:
            cur = conn.execute(
                'INSERT INTO audio_library(filename, display_name, file_size) VALUES(?,?,?)',
                (safe_name, display_name, file_size))
            conn.commit()
            track_id = cur.lastrowid
        return jsonify({'ok': True, 'id': track_id, 'filename': safe_name, 'display_name': display_name, 'file_size': file_size})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/audio/library/<int:tid>', methods=['DELETE'])
def audio_library_delete(tid):
    try:
        with get_db() as conn:
            row = conn.execute('SELECT filename FROM audio_library WHERE id=?', (tid,)).fetchone()
            if not row: return jsonify({'error': 'Not found'}), 404
            path = _os.path.join(AUDIO_DIR, row['filename'])
            if _os.path.exists(path): _os.remove(path)
            conn.execute('DELETE FROM audio_library WHERE id=?', (tid,))
            conn.commit()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/audio/library/<int:tid>/rename', methods=['PATCH'])
def audio_library_rename(tid):
    try:
        b = request.get_json(force=True) or {}
        name = (b.get('display_name') or '').strip()
        if not name: return jsonify({'error': 'Name required'}), 400
        with get_db() as conn:
            conn.execute('UPDATE audio_library SET display_name=? WHERE id=?', (name, tid))
            conn.commit()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/audio/library/<path:filename>', methods=['GET'])
def audio_library_serve(filename):
    return send_from_directory(AUDIO_DIR, filename)

# ── Audio Playlists ───────────────────────────────────────────────────────────
@app.route('/audio/playlists', methods=['GET'])
def audio_playlists_list():
    try:
        with get_db() as conn:
            rows = conn.execute('SELECT * FROM audio_playlists ORDER BY is_global DESC, name').fetchall()
            playlists = []
            for r in rows:
                p = dict(r)
                p['track_ids'] = _json.loads(p['track_ids'] or '[]')
                playlists.append(p)
            return jsonify({'playlists': playlists})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/audio/playlists', methods=['POST'])
def audio_playlists_create():
    try:
        b = request.get_json(force=True) or {}
        name = (b.get('name') or '').strip()
        if not name: return jsonify({'error': 'Name required'}), 400
        is_global = 1 if b.get('is_global') else 0
        track_ids = _json.dumps(b.get('track_ids') or [])
        shuffle = 1 if b.get('shuffle') else 0
        with get_db() as conn:
            cur = conn.execute(
                'INSERT INTO audio_playlists(name, is_global, track_ids, shuffle) VALUES(?,?,?,?)',
                (name, is_global, track_ids, shuffle))
            conn.commit()
            pid = cur.lastrowid
        return jsonify({'ok': True, 'id': pid})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/audio/playlists/<int:pid>', methods=['PATCH'])
def audio_playlists_update(pid):
    try:
        b = request.get_json(force=True) or {}
        fields, vals = [], []
        if 'name' in b: fields.append('name=?'); vals.append(b['name'])
        if 'is_global' in b: fields.append('is_global=?'); vals.append(1 if b['is_global'] else 0)
        if 'track_ids' in b: fields.append('track_ids=?'); vals.append(_json.dumps(b['track_ids']))
        if 'shuffle' in b: fields.append('shuffle=?'); vals.append(1 if b['shuffle'] else 0)
        if not fields: return jsonify({'ok': True})
        vals.append(pid)
        with get_db() as conn:
            conn.execute(f'UPDATE audio_playlists SET {", ".join(fields)} WHERE id=?', vals)
            conn.commit()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/audio/playlists/<int:pid>', methods=['DELETE'])
def audio_playlists_delete(pid):
    try:
        with get_db() as conn:
            conn.execute('DELETE FROM audio_playlists WHERE id=?', (pid,))
            conn.commit()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ── Global Settings (display defaults) ──────────────────────────────────────────
@app.route('/settings/display-defaults', methods=['GET'])
def display_defaults_get():
    try:
        with get_db() as conn:
            row = conn.execute("SELECT value FROM global_settings WHERE key='display_defaults'").fetchone()
            defaults = _json.loads(row['value']) if row else {}
            return jsonify({'defaults': defaults})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/settings/display-defaults', methods=['POST'])
def display_defaults_set():
    try:
        b = request.get_json(force=True) or {}
        value_str = _json.dumps(b.get('defaults', {}))
        with get_db() as conn:
            conn.execute('''INSERT INTO global_settings(key,value,updated_at) VALUES('display_defaults',?,CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=CURRENT_TIMESTAMP''', (value_str,))
            conn.commit()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ── System Theme (global-settings generic key/value) ─────────────────────────
@app.route('/global-settings', methods=['POST'])
def global_settings_post():
    try:
        b = request.get_json(force=True) or {}
        with get_db() as conn:
            for key, value in b.items():
                value_str = _json.dumps(value) if not isinstance(value, str) else value
                conn.execute('''INSERT INTO global_settings(key,value,updated_at) VALUES(?,?,CURRENT_TIMESTAMP)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=CURRENT_TIMESTAMP''', (key, value_str))
            conn.commit()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/global-settings/<key>', methods=['GET'])
def global_settings_get(key):
    try:
        with get_db() as conn:
            row = conn.execute("SELECT value FROM global_settings WHERE key=?", (key,)).fetchone()
            if row:
                try:
                    return jsonify({'value': _json.loads(row['value'])})
                except Exception:
                    return jsonify({'value': row['value']})
            return jsonify({'value': None})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ── Motor Cache (NASCAR/F1 result storage) ──────────────────────────────────
@app.route('/motor/reseed', methods=['POST'])
def motor_reseed():
    """Re-fetch motor_cache.json from GitHub data branch and repopulate DB."""
    try:
        import json as _jr
        DATA_URL = 'https://raw.githubusercontent.com/jstevenscl/scorestream/data/motor_cache.json'
        rg = http.get(DATA_URL, timeout=20)
        if not rg.ok:
            return jsonify({'error': f'GitHub fetch failed: {rg.status_code}'}), 502
        gdata = rg.json()
        updated_count = 0
        with get_db() as conn:
            for key, value in gdata.items():
                new_data = _jr.dumps(value.get('data', value))
                gh_updated = value.get('_updated', '2026-01-01')
                conn.execute(
                    "INSERT OR REPLACE INTO motor_cache(key,data,updated_at) VALUES(?,?,?)",
                    (key, new_data, gh_updated + ' 00:00:00')
                )
                updated_count += 1
            conn.commit()
        log.info(f'[reseed] Motor cache refreshed: {updated_count}/{len(gdata)} keys')
        return jsonify({'ok': True, 'keys_updated': updated_count})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/motor/cache/<key>', methods=['GET'])
def motor_cache_get(key):
    try:
        with get_db() as conn:
            row = conn.execute('SELECT data, updated_at FROM motor_cache WHERE key=?', (key,)).fetchone()
            if not row: return jsonify({'error': 'not found'}), 404
            import json as _json2
            return jsonify({'key': key, 'data': _json2.loads(row['data']), 'updated_at': row['updated_at']})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/motor/cache/<key>', methods=['POST'])
def motor_cache_set(key):
    try:
        b = request.get_json(force=True) or {}
        data_str = _json.dumps(b.get('data', {}))
        with get_db() as conn:
            conn.execute('''INSERT INTO motor_cache(key, data, updated_at) VALUES(?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET data=excluded.data, updated_at=CURRENT_TIMESTAMP''', (key, data_str))
            conn.commit()
        return jsonify({'ok': True, 'key': key})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/motor/nascar/fetch', methods=['GET'])
def motor_nascar_fetch():
    """Server-side proxy to fetch NASCAR data that blocks browser requests."""
    import time
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/json',
        'Referer': 'https://www.nascar.com/',
        'Origin': 'https://www.nascar.com',
    }
    result = {}
    # Fetch last race results - try race results endpoint
    try:
        r = http.get('https://cf.nascar.com/live/feeds/live-feed.json', headers=headers, timeout=8)
        if r.ok:
            d = r.json()
            result['live'] = {
                'flag_state': d.get('flag_state', 0),
                'run_name': d.get('run_name', ''),
                'run_type': d.get('run_type', 0),
                'track_name': d.get('track_name', ''),
                'laps_to_go': d.get('laps_to_go', 0),
                'laps_in_race': d.get('laps_in_race', 0),
                'lap_number': d.get('lap_number', 0),
                'race_id': d.get('race_id'),
                'series_id': d.get('series_id'),
                'vehicles': [{
                    'pos': v.get('running_position'),
                    'car': v.get('vehicle_number', '?'),
                    'driver': v.get('driver', {}).get('full_name') or (v.get('driver', {}).get('first_name','') + ' ' + v.get('driver', {}).get('last_name','')).strip(),
                    'manufacturer': v.get('vehicle_manufacturer', ''),
                    'laps': v.get('laps_completed', 0),
                    'laps_led': v.get('laps_led', 0),
                    'delta': v.get('delta'),
                    'status': v.get('status', 'Running'),
                    'best_lap_time': v.get('best_lap_time'),
                    'last_lap_speed': v.get('last_lap_speed'),
                } for v in sorted(d.get('vehicles', []), key=lambda x: x.get('running_position', 99))]
            }
    except Exception as e:
        result['live_error'] = str(e)

    # Try to get standings from NASCAR cacher
    standings_urls = [
        'https://cf.nascar.com/cacher/2026/1/standings/driver-standings.json',
        'https://cf.nascar.com/cacher/2025/1/standings/driver-standings.json',
    ]
    for url in standings_urls:
        try:
            r = http.get(url, headers=headers, timeout=8)
            if r.ok:
                result['standings'] = r.json()
                result['standings_url'] = url
                break
        except Exception:
            continue

    # Try to get last race results
    race_id = result.get('live', {}).get('race_id')
    if race_id:
        for rid in [race_id - 1, race_id - 2, race_id]:
            try:
                url = f'https://cf.nascar.com/cacher/2026/1/race-results/{rid}.json'
                r = http.get(url, headers=headers, timeout=8)
                if r.ok:
                    result['last_race'] = r.json()
                    result['last_race_url'] = url
                    break
            except Exception:
                continue

    return jsonify(result)

if __name__ == '__main__':
    init_db()

    # ── Background motor sport data refresh ──────────────────────────────────
    def _refresh_motor_data():
        """Fetch NASCAR and PGA data from live APIs and store in motor_cache.
        Also re-seeds player caches (PGA/ATP/WTA headshots) from GitHub data branch.
        Runs once on startup then every 6 hours automatically."""
        import time as _time
        import json as _jr2
        _time.sleep(10)  # Wait for DB to be fully ready
        while True:
            try:
                _auto_refresh_nascar()
            except Exception as e:
                log.warning(f'[auto] NASCAR refresh error: {e}')
            try:
                _auto_refresh_pga()
            except Exception as e:
                log.warning(f'[auto] PGA refresh error: {e}')
            # Re-seed player caches (headshots) from GitHub data branch
            try:
                DATA_URL = 'https://raw.githubusercontent.com/jstevenscl/scorestream/data/motor_cache.json'
                rg = http.get(DATA_URL, timeout=20)
                if rg.ok:
                    gdata = rg.json()
                    player_keys = {k for k in gdata if k.endswith('-players') or k.endswith('-rankings')}
                    if player_keys:
                        with get_db() as conn:
                            for key in player_keys:
                                value = gdata[key]
                                new_data = _jr2.dumps(value.get('data', value))
                                gh_updated = value.get('_updated', '2026-01-01')
                                conn.execute(
                                    "INSERT OR REPLACE INTO motor_cache(key,data,updated_at) VALUES(?,?,?)",
                                    (key, new_data, gh_updated + ' 00:00:00')
                                )
                            conn.commit()
                        log.info(f'[auto] Player caches refreshed from GitHub: {len(player_keys)} keys')
            except Exception as e:
                log.warning(f'[auto] Player cache refresh error: {e}')
            _time.sleep(6 * 3600)  # Refresh every 6 hours

    def _auto_refresh_nascar():
        """Fetch NASCAR standings and last 5 race results from NASCAR APIs."""
        import json as _j
        from datetime import date as _date
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json',
            'Referer': 'https://www.nascar.com/',
            'Origin': 'https://www.nascar.com',
        }
        try:
            r = http.get('https://cf.nascar.com/live/feeds/live-feed.json', headers=headers, timeout=10)
            if not r.ok: return
            live = r.json()
            series_id = live.get('series_id', 0)
            flag_state = live.get('flag_state', 0)
            run_name = live.get('run_name', '')
            race_id = live.get('race_id')
            if series_id != 1 or not run_name: return

            def _parse_vehicles(vehicles):
                def _norm_mfr(m):
                    s = str(m or '').lstrip(',').strip()
                    low = s.lower()
                    if not low: return s
                    if low.startswith('ch') or 'chev' in low: return 'Chevy'
                    if low.startswith('fo') or low.startswith('fr') or 'ford' in low: return 'Ford'
                    if low.startswith('to') or low.startswith('ty') or 'toyota' in low: return 'Toyota'
                    return s
                def _points(v):
                    # Live feed: points_earned or points; race-results JSON: points_earned, total_points, points
                    for f in ('points_earned','total_points','points'):
                        val = v.get(f)
                        if val is not None:
                            try: return int(str(val).replace(',','').strip())
                            except (ValueError, TypeError): pass
                    return 0
                def _pos(v):
                    for f in ('running_position','finishing_position','finish_position','pos'):
                        val = v.get(f)
                        if val is not None:
                            try: return int(val)
                            except (ValueError, TypeError): pass
                    return 99
                return [{
                    'pos': _pos(v),
                    'car': '#' + str(v.get('vehicle_number', v.get('car_number', '?'))).lstrip('#').lstrip(','),
                    'driver': (v.get('driver', {}).get('full_name') if isinstance(v.get('driver'), dict) else None) or
                               ((v.get('driver', {}).get('first_name','') + ' ' +
                                 v.get('driver', {}).get('last_name','')).strip() if isinstance(v.get('driver'), dict) else '') or
                               v.get('driver_name') or '?',
                    'manufacturer': _norm_mfr(v.get('vehicle_manufacturer', v.get('manufacturer', ''))),
                    'laps': v.get('laps_completed', v.get('laps', 0)),
                    'status': v.get('status', 'Running'),
                    'delta': v.get('delta'),
                    'points': _points(v),
                } for v in sorted(vehicles, key=lambda x: _pos(x))
                  if _pos(v) < 99 or v.get('driver_name') or (isinstance(v.get('driver'),dict) and v['driver'].get('full_name'))]

            # Build current/last race from live feed
            drivers = _parse_vehicles(live.get('vehicles', []))
            if drivers:
                last_data = {
                    'run_name': run_name,
                    'track_name': live.get('track_name', ''),
                    'race_date': str(_date.today()),
                    'flag_state': flag_state,
                    'laps_in_race': live.get('laps_in_race', 0),
                    'series_id': series_id,
                    'race_id': race_id,
                    'drivers': drivers,
                }
                with get_db() as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO motor_cache(key,data,updated_at) VALUES(?,?,datetime('now'))",
                        ('nascar-last', _j.dumps(last_data))
                    )
                log.info(f'[auto] NASCAR last race updated: {run_name} ({len(drivers)} drivers)')

            # Fetch last 5 race results by ID and store as history
            if race_id:
                history = []
                for rid in range(race_id, max(race_id - 6, 0), -1):
                    try:
                        url = f'https://cf.nascar.com/cacher/2026/1/race-results/{rid}.json'
                        rr = http.get(url, headers=headers, timeout=8)
                        if not rr.ok: continue
                        rd = rr.json()
                        vehicles = rd if isinstance(rd, list) else rd.get('vehicles', rd.get('results', []))
                        if not vehicles: continue
                        race_name = rd.get('run_name','') if isinstance(rd, dict) else ''
                        track = rd.get('track_name','') if isinstance(rd, dict) else ''
                        race_date = rd.get('race_date','') if isinstance(rd, dict) else ''
                        parsed = _parse_vehicles(vehicles) if isinstance(vehicles[0], dict) else []
                        if parsed:
                            history.append({
                                'race_id': rid,
                                'run_name': race_name or run_name,
                                'track_name': track,
                                'race_date': race_date,
                                'flag_state': 9,
                                'drivers': parsed,
                            })
                        if len(history) >= 5: break
                    except Exception: continue
                if history:
                    with get_db() as conn:
                        conn.execute(
                            "INSERT OR REPLACE INTO motor_cache(key,data,updated_at) VALUES(?,?,datetime('now'))",
                            ('nascar-history', _j.dumps({'races': history, 'updated': str(_date.today())}))
                        )
                    log.info(f'[auto] NASCAR history updated: {len(history)} races')

            # Standings
            for url in [
                'https://cf.nascar.com/cacher/2026/1/standings/driver-standings.json',
                'https://cf.nascar.com/cacher/2025/1/standings/driver-standings.json',
            ]:
                try:
                    rs = http.get(url, headers=headers, timeout=10)
                    if rs.ok:
                        raw = rs.json()
                        entries = raw if isinstance(raw, list) else raw.get('response', [])
                        def _norm_mfr(m):
                            s = str(m or '').lstrip(',').strip()
                            low = s.lower()
                            if not low: return s
                            if low.startswith('ch') or 'chev' in low: return 'Chevy'
                            if low.startswith('fo') or low.startswith('fr') or 'ford' in low: return 'Ford'
                            if low.startswith('to') or low.startswith('ty') or 'toyota' in low: return 'Toyota'
                            return s
                        def _safe_int(v, default=0):
                            try: return int(str(v).replace(',','').strip())
                            except (ValueError, TypeError): return default
                        standings = [{
                            'pos': e.get('points_position') or e.get('rank') or (i+1),
                            'driver': ((e.get('driver', {}).get('full_name') if isinstance(e.get('driver'), dict) else None) or
                                       ((e.get('driver', {}).get('first_name','') + ' ' +
                                         e.get('driver', {}).get('last_name','')).strip() if isinstance(e.get('driver'), dict) else '') or
                                       e.get('driver_name') or 'Unknown'),
                            'car': '#' + str(e.get('car_number', '?')).lstrip('#').lstrip(','),
                            'manufacturer': _norm_mfr(e.get('manufacturer', '')),
                            'points': _safe_int(e.get('points', 0)),
                            'wins': _safe_int(e.get('wins', 0)),
                            'top5': _safe_int(e.get('top5', e.get('top_5', 0))),
                            'races': _safe_int(e.get('races', e.get('starts', 0))),
                        } for i, e in enumerate(entries[:40])]
                        if standings:
                            with get_db() as conn:
                                existing = conn.execute("SELECT data FROM motor_cache WHERE key='nascar-2026-season'").fetchone()
                                ex_data = _j.loads(existing['data']) if existing else {}
                                ex_data['standings'] = standings
                                ex_data['updated'] = str(_date.today())
                                conn.execute(
                                    "INSERT OR REPLACE INTO motor_cache(key,data,updated_at) VALUES(?,?,datetime('now'))",
                                    ('nascar-2026-season', _j.dumps(ex_data))
                                )
                            log.info(f'[auto] NASCAR standings updated: {len(standings)} drivers')
                        break
                except Exception:
                    continue
        except Exception as e:
            log.warning(f'[auto] NASCAR live feed error: {e}')

    def _auto_refresh_pga():
        """Fetch PGA Tour data from ESPN API and store in motor_cache."""
        import json as _j
        from datetime import date as _date, timedelta as _td
        # ESPN golf scoreboard API - gets current/recent tournament
        espn_urls = [
            'https://site.api.espn.com/apis/site/v2/sports/golf/pga/scoreboard',
            'https://site.web.api.espn.com/apis/v2/scoreboard/header?sport=golf&league=pga',
        ]
        try:
            r = http.get(espn_urls[0], timeout=10)
            if not r.ok: return
            data = r.json()
            events = data.get('events', [])
            if not events: return

            with get_db() as conn:
                existing = conn.execute("SELECT data FROM motor_cache WHERE key='pga-2026-tournaments'").fetchone()
                ex = _j.loads(existing['data']) if existing else {'tournaments': []}
                tournaments = ex.get('tournaments', [])

            for event in events:
                name = event.get('name', '')
                date_str = (event.get('date') or '')[:10]
                status_type = event.get('status', {}).get('type', {})
                is_complete = status_type.get('completed', False)
                is_live = status_type.get('name') in ('in', 'STATUS_IN_PROGRESS')

                # Get competitors/leaderboard
                competitors = []
                for comp in (event.get('competitions', [{}])[0].get('competitors', []) if event.get('competitions') else []):
                    athlete = comp.get('athlete', {})
                    stats = {s.get('name',''):s.get('displayValue','') for s in comp.get('statistics', [])}
                    competitors.append({
                        'pos': comp.get('status', {}).get('position', {}).get('displayName', ''),
                        'name': athlete.get('displayName', ''),
                        'score': stats.get('scoreToPar', comp.get('score', '')),
                        'r1': stats.get('round1', ''),
                        'r2': stats.get('round2', ''),
                        'r3': stats.get('round3', ''),
                        'r4': stats.get('round4', ''),
                        'total': stats.get('totalScore', ''),
                    })

                winner = competitors[0]['name'] if is_complete and competitors else None
                winner_score = competitors[0]['score'] if is_complete and competitors else None

                # Update or insert tournament
                existing_t = next((t for t in tournaments if t['name'] == name or t['date'][:10] == date_str[:10]), None)
                if existing_t:
                    existing_t['is_complete'] = is_complete
                    existing_t['is_live'] = is_live
                    if winner: existing_t['winner'] = winner
                    if winner_score: existing_t['winner_score'] = winner_score
                    if competitors: existing_t['players'] = competitors
                else:
                    tournaments.append({
                        'name': name,
                        'date': date_str,
                        'is_complete': is_complete,
                        'is_live': is_live,
                        'winner': winner,
                        'winner_score': winner_score,
                        'players': competitors,
                    })

            with get_db() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO motor_cache(key,data,updated_at) VALUES(?,?,datetime('now'))",
                    ('pga-2026-tournaments', _j.dumps({'tournaments': tournaments, 'updated': str(_date.today())}))
                )
            log.info(f'[auto] PGA updated: {len(events)} events from ESPN')
        except Exception as e:
            log.warning(f'[auto] PGA ESPN error: {e}')

    _t = threading.Thread(target=_refresh_motor_data, daemon=True)
    _t.start()
    log.info('[auto] Motor sport background refresh started (NASCAR + PGA every 6h)')

    log.info('ScoreStream API starting on :5000')
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
