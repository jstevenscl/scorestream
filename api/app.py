"""
ScoreStream API — Flask backend
See NCAA_ARCHITECTURE.md for full design decisions.
"""
import os, sqlite3, logging, threading
from datetime import datetime, timezone
from flask import Flask, jsonify, request
from flask_cors import CORS
import requests as http

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)
app = Flask(__name__)
CORS(app)

DB_PATH         = os.getenv('DB_PATH', '/config/scorestream.db')
STREAM_BASE_URL = os.getenv('STREAM_BASE_URL', '')

_sync_lock = threading.Lock()

# Each tuple: (sport_id, gender, division, url)
# Using ESPN groups parameter to fetch per-division so every team gets correct division tag
ESPN_ENDPOINTS = [
    # Football
    ('ncaafb', 'mens', 'd1', 'https://site.api.espn.com/apis/site/v2/sports/football/college-football/teams?limit=900&groups=80'),   # FBS
    ('ncaafb', 'mens', 'd1', 'https://site.api.espn.com/apis/site/v2/sports/football/college-football/teams?limit=900&groups=81'),   # FCS
    ('ncaafb', 'mens', 'd2', 'https://site.api.espn.com/apis/site/v2/sports/football/college-football/teams?limit=900&groups=82'),   # D-II
    ('ncaafb', 'mens', 'd3', 'https://site.api.espn.com/apis/site/v2/sports/football/college-football/teams?limit=900&groups=83'),   # D-III
    # Mens Basketball
    ('ncaamb', 'mens', 'd1', 'https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/teams?limit=900&groups=50'),
    ('ncaamb', 'mens', 'd2', 'https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/teams?limit=900&groups=49'),
    ('ncaamb', 'mens', 'd3', 'https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/teams?limit=900&groups=48'),
    # Womens Basketball
    ('ncaawb', 'womens', 'd1', 'https://site.api.espn.com/apis/site/v2/sports/basketball/womens-college-basketball/teams?limit=900&groups=50'),
    ('ncaawb', 'womens', 'd2', 'https://site.api.espn.com/apis/site/v2/sports/basketball/womens-college-basketball/teams?limit=900&groups=49'),
    ('ncaawb', 'womens', 'd3', 'https://site.api.espn.com/apis/site/v2/sports/basketball/womens-college-basketball/teams?limit=900&groups=48'),
    # Baseball
    ('ncaabase', 'mens', 'd1', 'https://site.api.espn.com/apis/site/v2/sports/baseball/college-baseball/teams?limit=900&groups=11'),
    ('ncaabase', 'mens', 'd2', 'https://site.api.espn.com/apis/site/v2/sports/baseball/college-baseball/teams?limit=900&groups=10'),
    ('ncaabase', 'mens', 'd3', 'https://site.api.espn.com/apis/site/v2/sports/baseball/college-baseball/teams?limit=900&groups=9'),
    # Softball
    ('ncaasb', 'womens', 'd1', 'https://site.api.espn.com/apis/site/v2/sports/baseball/college-softball/teams?limit=900&groups=11'),
    ('ncaasb', 'womens', 'd2', 'https://site.api.espn.com/apis/site/v2/sports/baseball/college-softball/teams?limit=900&groups=10'),
    ('ncaasb', 'womens', 'd3', 'https://site.api.espn.com/apis/site/v2/sports/baseball/college-softball/teams?limit=900&groups=9'),
]

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

def _attempt_espn_sync_inner():
    db_set('espn_sync_status','running')
    all_ok = True
    for sport_id, gender, division, url in ESPN_ENDPOINTS:
        try:
            log.info(f'ESPN sync: {sport_id} {division}...')
            resp = http.get(url, timeout=8); resp.raise_for_status()
            data = resp.json()
            raw = data.get('sports',[{}])[0].get('leagues',[{}])[0].get('teams',[])
            if not raw: raw = data.get('teams',[])
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
                    div   = division  # division comes from the endpoint tuple, not ESPN response
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
            log.info(f'ESPN sync: {sport_id} {division} done ({len(raw)} teams)')
        except Exception as e:
            log.error(f'ESPN sync error {sport_id}: {e}'); all_ok = False
    if all_ok:
        db_set('espn_sync_status','complete')
        db_set('last_espn_sync', datetime.now(timezone.utc).isoformat())
        log.info('NCAA sync complete')
    else:
        db_set('espn_sync_status','failed')
    return all_ok

def seed_fallback():
    with get_db() as conn:
        for abbr,full_name,location,color in FALLBACK_SCHOOLS:
            conn.execute("INSERT OR IGNORE INTO ncaa_schools(espn_abbr,full_name,location,color,sync_source) VALUES(?,?,?,?,'fallback')",(abbr,full_name,location,color))
        conn.commit()
        for abbr,sport_id,gender,division,nick,display_name,short_name in FALLBACK_PROGRAMS:
            s = conn.execute('SELECT id FROM ncaa_schools WHERE espn_abbr=?',(abbr,)).fetchone()
            if s:
                conn.execute("INSERT OR IGNORE INTO ncaa_programs(school_id,sport_id,gender,division,nick,display_name,short_name) VALUES(?,?,?,?,?,?,?)",(s['id'],sport_id,gender,division,nick,display_name,short_name))
        conn.commit()
    log.info('Fallback seed complete')

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
            'password':db_get('dispatcharr_password') or os.getenv('DISPATCHARR_PASS','')}

def dispatcharr_session(creds=None):
    if creds is None: creds = get_creds()
    url,user,pw = creds.get('url',''),creds.get('username',''),creds.get('password','')
    if not url: return None,'Dispatcharr URL not configured'
    if not user or not pw: return None,'Dispatcharr credentials not configured'
    s = http.Session()
    try:
        r = s.post(f'{url}/api/accounts/token/',json={'username':user,'password':pw},timeout=10)
        r.raise_for_status()
        token = r.json().get('access')
        if not token: return None,'No token returned — check credentials'
        s.headers.update({'Authorization':f'Bearer {token}'})
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

# ── Credentials ───────────────────────────────────────────────────────────────
@app.route('/dispatcharr/credentials', methods=['GET'])
def get_credentials():
    c = get_creds()
    return jsonify({'url':c.get('url',''),'username':c.get('username',''),'has_password':bool(c.get('password',''))})

@app.route('/dispatcharr/credentials', methods=['POST'])
def save_credentials():
    b = request.get_json(force=True)
    url = b.get('url','').strip().rstrip('/')
    if not url: return jsonify({'error':'URL is required'}),400
    try:
        db_set('dispatcharr_url',url)
        if b.get('username'): db_set('dispatcharr_username',b['username'].strip())
        if b.get('password') is not None: db_set('dispatcharr_password',b['password'])
        return jsonify({'status':'saved'})
    except Exception as e: return jsonify({'error':str(e)}),500

@app.route('/dispatcharr/test', methods=['GET'])
def test_connection():
    c = get_creds(); s,err = dispatcharr_session(c)
    if err: return jsonify({'connected':False,'error':err})
    try:
        r = s.get(f'{c["url"]}/api/channels/channels/?limit=1',timeout=5)
        r.raise_for_status(); return jsonify({'connected':True})
    except Exception as e: return jsonify({'connected':False,'error':str(e)})

@app.route('/dispatcharr/groups', methods=['GET'])
def get_groups():
    c = get_creds(); s,err = dispatcharr_session(c)
    if err: return jsonify({'error':err}),400
    try:
        r = s.get(f'{c["url"]}/api/channels/channel-groups/',timeout=8); r.raise_for_status()
        data = r.json(); items = data.get('results',data) if isinstance(data,dict) else data
        return jsonify({'groups':sorted([{'id':g['id'],'name':g['name']} for g in items if 'id' in g],key=lambda x:x['name'])})
    except Exception as e: return jsonify({'error':str(e)}),500

@app.route('/dispatcharr/profiles', methods=['GET'])
def get_profiles():
    c = get_creds(); s,err = dispatcharr_session(c)
    if err: return jsonify({'error':err}),400
    try:
        r = s.get(f'{c["url"]}/api/channels/channel-profiles/',timeout=8); r.raise_for_status()
        data = r.json(); items = data.get('results',data) if isinstance(data,dict) else data
        return jsonify({'profiles':sorted([{'id':p['id'],'name':p['name']} for p in items if 'id' in p],key=lambda x:x['name'])})
    except Exception as e: return jsonify({'error':str(e)}),500

@app.route('/dispatcharr/create', methods=['POST'])
def create_channels():
    c = get_creds(); s,err = dispatcharr_session(c)
    if err: return jsonify({'error':err}),400
    b = request.get_json(force=True)
    mode,sports = b.get('mode','both'),b.get('sports',[])
    num_mode,start = b.get('numberingMode','auto'),int(b.get('startChannel',900))
    group_id,profile_id = b.get('groupId'),b.get('profileId')
    assignments = {a['sportId']:a for a in (b.get('channelAssignments') or [])}
    base,created,errors,ch_num = c['url'],[],[],start
    def make_channel(name,num,stream_url=None,gid=None):
        payload = {'name':name,'channel_number':num,
                   'tvg_id':name.lower().replace(' ','-').replace('—','').strip('-')}
        if gid or group_id: payload['channel_group_id']=int(gid or group_id)
        if profile_id: payload['channel_profile_ids']=[int(profile_id)]
        if stream_url: payload['url']=stream_url
        try:
            r = s.post(f'{base}/api/channels/channels/',json=payload,timeout=10)
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
if __name__ == '__main__':
    init_db()
    log.info('ScoreStream API starting on :5000')
    app.run(host='0.0.0.0', port=5000, debug=False)
