/**
 * ScoreStream — Stream Manager (On-Demand)
 *
 * Flow:
 *  1. Startup: pre-bake PREROLL_SEGMENTS of loading screen into HLS for every
 *     known slug. VLC gets a valid live playlist instantly on first press.
 *  2. On first VLC request: start live ffmpeg from segment 0 (owns the playlist
 *     fully — no stitching). Start Puppeteer in background.
 *  3. Pre-roll gives ~12s of buffer. Live ffmpeg writes new segments into the
 *     same file names, naturally taking over as the playlist rolls forward.
 *  4. Puppeteer screenshots replace frame.jpg; ffmpeg picks them up each loop.
 */

'use strict';

const http      = require('http');
const fs        = require('fs');
const path      = require('path');
const { spawn, execSync } = require('child_process');
const puppeteer = require('puppeteer-core');
const Database  = require('better-sqlite3');

// ── Config ──────────────────────────────────────────────────────────────────
const DB_PATH      = process.env.DB_PATH                || '/config/scorestream.db';
const HLS_DIR      = process.env.HLS_DIR                || '/hls';
const FRAMES_DIR   = process.env.FRAMES_DIR             || '/tmp/frames';
const WEB_BASE     = process.env.WEB_BASE               || 'http://scorestream-web';
const MANAGER_PORT = parseInt(process.env.MANAGER_PORT  || '3001');
const WIDTH        = parseInt(process.env.STREAM_WIDTH  || '1920');
const HEIGHT       = parseInt(process.env.STREAM_HEIGHT || '1080');
const FPS          = Math.max(1, parseInt(process.env.STREAM_FPS || '1'));
const JPEG_QUALITY = parseInt(process.env.JPEG_QUALITY  || '85');
const SEG_DURATION = parseInt(process.env.HLS_SEGMENT_DURATION || '2');
const PLAYLIST_SIZE= parseInt(process.env.HLS_PLAYLIST_SIZE    || '4');
const IDLE_TIMEOUT = parseInt(process.env.STREAM_IDLE_TIMEOUT  || '60');
const SCREENSHOT_MS= Math.round(1000 / FPS);

// Pre-roll: how many loading-screen segments to encode at startup.
// At 1fps, SEG_DURATION=2: each segment = 2s. 6 segments = 12s of buffer.
// Live ffmpeg takes ~3-4s to write its first segment, so 6 gives plenty of
// headroom before VLC exhausts pre-roll and needs live segments.
const PREROLL_SEGMENTS = 6;

// ── State ───────────────────────────────────────────────────────────────────
const streams = new Map();
const prebaked = new Set();

// ── DB ──────────────────────────────────────────────────────────────────────
function slugExists(slug) {
  try {
    const db  = new Database(DB_PATH, { fileMustExist: true });
    const row = db.prepare('SELECT slug FROM scoreboards WHERE slug = ?').get(slug);
    db.close();
    return !!row;
  } catch (e) { console.error(`[manager] DB error: ${e.message}`); return false; }
}

function getAllSlugs() {
  try {
    const db   = new Database(DB_PATH, { fileMustExist: true });
    const rows = db.prepare('SELECT slug FROM scoreboards').all();
    db.close();
    return rows.map(r => r.slug);
  } catch (e) { console.error(`[manager] DB error: ${e.message}`); return []; }
}

// ── Helpers ─────────────────────────────────────────────────────────────────
function ensureDir(d) { if (!fs.existsSync(d)) fs.mkdirSync(d, { recursive: true }); }
function frameDir(slug)  { return path.join(FRAMES_DIR, slug); }
function framePath(slug) { return path.join(frameDir(slug), 'frame.jpg'); }
function frameTmp(slug)  { return path.join(frameDir(slug), 'frame.tmp.jpg'); }

// ── Static loading frame ─────────────────────────────────────────────────────
function writeStartingFrame(slug) {
  const dest  = framePath(slug);
  const label = slug.replace(/-/g, ' ').toUpperCase();
  ensureDir(frameDir(slug));
  try {
    execSync(
      `ffmpeg -y -loglevel error -f lavfi -i color=c=0x0a0e1a:size=${WIDTH}x${HEIGHT}:rate=1 ` +
      `-vf "drawtext=text='SCORESTREAM':fontcolor=0x00d4ff:fontsize=64:x=(w-text_w)/2:y=(h-text_h)/2-60,` +
      `drawtext=text='${label}':fontcolor=white:fontsize=40:x=(w-text_w)/2:y=(h-text_h)/2+10,` +
      `drawtext=text='LOADING...':fontcolor=0x3d5a78:fontsize=28:x=(w-text_w)/2:y=(h-text_h)/2+70,` +
      `format=yuv420p" -frames:v 1 -q:v 3 "${dest}"`,
      { stdio: 'pipe' }
    );
  } catch (e) {
    try {
      execSync(
        `ffmpeg -y -loglevel error -f lavfi -i color=c=0x0a0e1a:size=${WIDTH}x${HEIGHT}:rate=1 ` +
        `-vf format=yuv420p -frames:v 1 -q:v 3 "${dest}"`,
        { stdio: 'pipe' }
      );
    } catch (e2) { console.warn(`[manager][${slug}] Frame write failed: ${e2.message}`); }
  }
}

// ── Pre-bake ─────────────────────────────────────────────────────────────────
// Encodes PREROLL_SEGMENTS individual .ts files from the loading frame,
// then writes a live-style m3u8 (no ENDLIST) so VLC treats it as a live stream.
// Live ffmpeg starts from segment 0 and naturally overwrites these files as it
// rolls forward — no sequence stitching needed.
function prebakeHLS(slug) {
  const m3u8 = path.join(HLS_DIR, `${slug}.m3u8`);

  // Clean any stale HLS files for this slug
  try {
    fs.readdirSync(HLS_DIR)
      .filter(f => f.startsWith(slug + '_') && f.endsWith('.ts'))
      .forEach(f => fs.unlinkSync(path.join(HLS_DIR, f)));
    if (fs.existsSync(m3u8)) fs.unlinkSync(m3u8);
  } catch (_) {}

  const frame = framePath(slug);
  if (!fs.existsSync(frame)) {
    console.warn(`[manager][${slug}] No frame for pre-bake — skipping`);
    return;
  }

  // Encode each segment individually with mpegts muxer
  for (let i = 0; i < PREROLL_SEGMENTS; i++) {
    const seg = path.join(HLS_DIR, `${slug}_${String(i).padStart(5, '0')}.ts`);
    try {
      execSync(
        `ffmpeg -y -loglevel error -loop 1 -framerate ${FPS} -i "${frame}" ` +
        `-vf format=yuv420p -c:v libx264 -preset ultrafast -tune stillimage ` +
        `-b:v 800k -maxrate 1200k -bufsize 2000k -pix_fmt yuv420p ` +
        `-g ${FPS * SEG_DURATION} -sc_threshold 0 ` +
        `-t ${SEG_DURATION} -f mpegts "${seg}"`,
        { stdio: 'pipe' }
      );
    } catch (e) {
      console.error(`[manager][${slug}] Seg ${i} encode failed: ${e.message}`);
      return;
    }
  }

  // Write live m3u8 — NO ENDLIST so VLC keeps polling
  // Show only the last PLAYLIST_SIZE segments in the window
  const windowStart = Math.max(0, PREROLL_SEGMENTS - PLAYLIST_SIZE);
  const lines = [
    '#EXTM3U',
    '#EXT-X-VERSION:3',
    `#EXT-X-TARGETDURATION:${SEG_DURATION}`,
    `#EXT-X-MEDIA-SEQUENCE:${windowStart}`,
  ];
  for (let i = windowStart; i < PREROLL_SEGMENTS; i++) {
    lines.push(`#EXTINF:${SEG_DURATION}.000000,`);
    lines.push(`${slug}_${String(i).padStart(5, '0')}.ts`);
  }
  fs.writeFileSync(m3u8, lines.join('\n') + '\n');

  prebaked.add(slug);
  console.log(`[manager][${slug}] Pre-baked ${PREROLL_SEGMENTS} segs → ${PREROLL_SEGMENTS * SEG_DURATION}s buffer`);
}

// ── Audio config ──────────────────────────────────────────────────────────────
const AUDIO_DIR = process.env.AUDIO_DIR || '/audio';
const BAKED_AUDIO_DIR = '/audio'; // Kevin MacLeod files baked into stream image

// On startup: copy baked-in tracks into AUDIO_DIR (shared volume) and register in DB
// This makes them accessible to the API container for the audio library UI
function seedBuiltinAudio() {
  if (!fs.existsSync(BAKED_AUDIO_DIR)) return;
  if (AUDIO_DIR === BAKED_AUDIO_DIR) return; // same dir, nothing to do
  try {
    const mp3s = fs.readdirSync(BAKED_AUDIO_DIR)
      .filter(f => f.endsWith('.mp3') && !f.startsWith('loop') && !f.startsWith('silent'))
      .filter(f => { try { return fs.statSync(path.join(BAKED_AUDIO_DIR, f)).size > 10000; } catch(_) { return false; } });
    if (!mp3s.length) return;
    const db = new Database(DB_PATH, { fileMustExist: true });
    const existingFiles = new Set(db.prepare('SELECT filename FROM audio_library').all().map(r => r.filename));
    const newIds = [];
    for (const mp3 of mp3s) {
      const dest = path.join(AUDIO_DIR, mp3);
      if (!fs.existsSync(dest)) {
        fs.copyFileSync(path.join(BAKED_AUDIO_DIR, mp3), dest);
      }
      if (!existingFiles.has(mp3)) {
        const displayName = 'Built-in: ' + mp3.replace('.mp3','').replace(/^\d+-/,'').replace(/-/g,' ').replace(/_/g,' ').replace(/\b\w/g,c=>c.toUpperCase());
        const size = fs.statSync(dest).size;
        const result = db.prepare('INSERT INTO audio_library(filename, display_name, file_size) VALUES(?,?,?)').run(mp3, displayName, size);
        newIds.push(result.lastInsertRowid);
        console.log(`[manager] Registered built-in track: ${displayName}`);
      }
    }
    if (newIds.length > 0) {
      const globalPl = db.prepare('SELECT id, track_ids FROM audio_playlists WHERE is_global=1').get();
      if (globalPl) {
        const existing = JSON.parse(globalPl.track_ids || '[]');
        db.prepare('UPDATE audio_playlists SET track_ids=? WHERE id=?')
          .run(JSON.stringify([...existing, ...newIds]), globalPl.id);
        console.log(`[manager] Added ${newIds.length} built-in tracks to Default playlist`);
      }
    }
    db.close();
  } catch(e) {
    console.warn('[manager] seedBuiltinAudio error:', e.message);
  }
}
seedBuiltinAudio();

function getAudioConfig(slug) {
  try {
    const db  = new Database(DB_PATH, { fileMustExist: true });
    const row = db.prepare(
      "SELECT audio_mode, audio_source_url, audio_playlist_id FROM scoreboards WHERE slug = ?"
    ).get(slug);
    if (!row) {
      db.close();
      console.warn(`[manager][${slug}] getAudioConfig: no DB row found`);
      return { mode: 'none', url: '', tracks: [] };
    }
    // If playlist mode, resolve track filenames from audio_playlists + audio_library tables
    let tracks = [];
    // If no specific playlist assigned, try the global default playlist
    const playlistId = row.audio_playlist_id || (() => {
      const globalPl = db.prepare("SELECT id FROM audio_playlists WHERE is_global=1 LIMIT 1").get();
      return globalPl ? globalPl.id : null;
    })();
    if ((row.audio_mode || 'none') === 'playlist' && playlistId) {
      try {
        const playlist = db.prepare("SELECT track_ids FROM audio_playlists WHERE id = ?").get(playlistId);
        if (playlist && playlist.track_ids) {
          const ids = JSON.parse(playlist.track_ids || '[]');
          if (ids.length > 0) {
            const placeholders = ids.map(() => '?').join(',');
            const libRows = db.prepare(`SELECT id, filename FROM audio_library WHERE id IN (${placeholders})`).all(...ids);
            tracks = ids.map(id => {
              const t = libRows.find(r => r.id === id);
              return t ? path.join(AUDIO_DIR, t.filename) : null;
            }).filter(Boolean);
          }
        }
      } catch(e) {
        console.warn(`[manager][${slug}] playlist lookup error: ${e.message}`);
      }
    }
    db.close();
    console.log(`[manager][${slug}] audio_mode=${row.audio_mode || 'none'} playlist_id=${row.audio_playlist_id} tracks=${tracks.length}`);
    return {
      mode: row.audio_mode || 'none',
      url:  row.audio_source_url || '',
      tracks,
    };
  } catch (e) {
    console.error(`[manager][${slug}] getAudioConfig error: ${e.message}`);
    return { mode: 'none', url: '', tracks: [] };
  }
}

// ── ffmpeg (live) ────────────────────────────────────────────────────────────
// Starts from segment 0. As it writes segments 0,1,2... it naturally overwrites
// the pre-baked files, and its playlist updates roll the window forward.
// VLC transitions from pre-bake to live seamlessly as the sequence advances.
function startFfmpeg(slug) {
  const frame = framePath(slug);
  const audio = getAudioConfig(slug);

  let args = [
    '-loglevel', 'warning',
    '-re', '-loop', '1', '-framerate', String(FPS), '-i', frame,
  ];

  // Add audio input if configured
  if (audio.mode === 'playlist') {
    let audioInput = null;

    // Use tracks resolved from the scoreboard's assigned playlist in DB
    const validTracks = (audio.tracks || []).filter(f => {
      try { return fs.existsSync(f) && fs.statSync(f).size > 1000; } catch(_) { return false; }
    });

    if (validTracks.length > 0) {
      // Write ffmpeg concat file with tracks repeated 200x (~hours of audio)
      // NOTE: -stream_loop does NOT work with concat demuxer — must repeat in file
      const concatPath = path.join(AUDIO_DIR, `loop_${slug}.txt`);
      const singlePass = validTracks.map(f => `file '${f}'`).join('\n') + '\n';
      fs.writeFileSync(concatPath, singlePass.repeat(200));
      audioInput = concatPath;
      args.push('-f', 'concat', '-safe', '0', '-i', audioInput);
      console.log(`[manager][${slug}] Audio: ${validTracks.length} tracks (x200): ${validTracks.map(f=>f.split('/').pop()).join(', ')}`);
    } else {
      // Fallback: use baked-in /audio directory (Kevin MacLeod CC-BY tracks)
      const DEFAULT_AUDIO_DIR = '/audio';
      try {
        const mp3s = fs.readdirSync(DEFAULT_AUDIO_DIR)
          .filter(f => f.endsWith('.mp3'))
          .filter(f => { try { return fs.statSync(path.join(DEFAULT_AUDIO_DIR, f)).size > 1000; } catch(_) { return false; } })
          .sort();
        if (mp3s.length > 0) {
          // Build concat file with all default tracks repeated 200x — loop via file repetition
          // NOTE: -stream_loop does NOT work with concat demuxer
          const concatPath = path.join(DEFAULT_AUDIO_DIR, `loop_default.txt`);
          const singleEntry = mp3s.map(f => `file '${path.join(DEFAULT_AUDIO_DIR, f)}'`).join('\n') + '\n';
          fs.writeFileSync(concatPath, singleEntry.repeat(200));
          audioInput = concatPath;
          args.push('-f', 'concat', '-safe', '0', '-i', audioInput);
          console.log(`[manager][${slug}] Audio: default tracks (x200) from ${DEFAULT_AUDIO_DIR}: ${mp3s.join(', ')}`);
        }
      } catch(e) {
        console.warn(`[manager][${slug}] No fallback audio found`);
      }
    }

    if (!audioInput) {
      console.warn(`[manager][${slug}] No audio files found — streaming silent`);
      audio.mode = 'none';
    }
  } else if (audio.mode === 'stream' && audio.url) {
    // Custom online audio stream URL
    args.push('-reconnect', '1', '-reconnect_streamed', '1',
              '-reconnect_delay_max', '5', '-i', audio.url);
  }

  const hasAudio = (audio.mode === 'playlist' || audio.mode === 'stream') && audio.mode !== 'none';

  args.push(
    '-vf', 'format=yuv420p',
    '-c:v', 'libx264', '-preset', 'veryfast', '-tune', 'stillimage',
    '-b:v', '800k', '-maxrate', '1200k', '-bufsize', '2000k',
    '-pix_fmt', 'yuv420p',
    '-g', String(FPS * SEG_DURATION), '-sc_threshold', '0',
  );

  if (hasAudio) {
    args.push(
      '-c:a', 'aac', '-b:a', '128k', '-ac', '2',
      '-map', '0:v:0', '-map', '1:a:0',
    );
  } else {
    args.push('-an');  // no audio
  }

  args.push(
    '-f', 'hls',
    '-hls_time', String(SEG_DURATION),
    '-hls_list_size', String(PLAYLIST_SIZE),
    '-hls_flags', 'delete_segments+independent_segments',
    '-hls_segment_filename', path.join(HLS_DIR, `${slug}_%05d.ts`),
    path.join(HLS_DIR, `${slug}.m3u8`)
  );

  if (hasAudio) {
    console.log(`[manager][${slug}] Starting live ffmpeg with audio (mode=${audio.mode})`);
  } else {
    console.log(`[manager][${slug}] Starting live ffmpeg (no audio)`);
  }

  const proc = spawn('ffmpeg', args, { stdio: ['ignore', 'ignore', 'pipe'] });
  proc.stderr.on('data', d => {
    const l = d.toString().trim();
    if (l && !l.includes('deprecated pixel format') && !l.includes('[swscaler')) {
      console.error(`[ffmpeg][${slug}] ${l}`);
    }
  });
  proc.on('exit', (code, sig) => {
    const s = streams.get(slug);
    if (s && s.running) {
      console.log(`[manager][${slug}] ffmpeg exited (${code}/${sig}) — restarting in 2s`);
      setTimeout(() => {
        const s2 = streams.get(slug);
        if (s2 && s2.running) s2.ffmpeg = startFfmpeg(slug);
      }, 2000);
    }
  });
  return proc;
}

// ── Puppeteer ────────────────────────────────────────────────────────────────
async function startRenderer(slug) {
  const url = `${WEB_BASE}/?stream&slug=${slug}`;
  console.log(`[manager][${slug}] Launching browser → ${url}`);
  const browser = await puppeteer.launch({
    executablePath: process.env.PUPPETEER_EXECUTABLE_PATH || '/usr/bin/chromium',
    headless: true,
    args: [
      '--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage',
      '--disable-gpu', '--disable-software-rasterizer',
      `--window-size=${WIDTH},${HEIGHT}`
    ],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: WIDTH, height: HEIGHT, deviceScaleFactor: 1 });
  try {
    await page.goto(url, { waitUntil: 'networkidle2', timeout: 30000 });
  } catch (e) {
    console.error(`[manager][${slug}] Nav warning: ${e.message}`);
  }
  console.log(`[manager][${slug}] Page loaded`);
  return { browser, page };
}

// ── Screenshot loop ───────────────────────────────────────────────────────────
function startScreenshotLoop(slug, page) {
  let active = true, count = 0;
  const tmp  = frameTmp(slug);
  const dest = framePath(slug);
  const t0   = Date.now();

  async function loop() {
    while (active && streams.get(slug)?.running) {
      const t = Date.now();
      try {
        await page.screenshot({
          path: tmp, type: 'jpeg', quality: JPEG_QUALITY,
          clip: { x: 0, y: 0, width: WIDTH, height: HEIGHT },
          omitBackground: false
        });
        fs.renameSync(tmp, dest);
        count++;
        if (count === 1) console.log(`[manager][${slug}] First live frame — pre-roll replaced`);
        if (count % 300 === 0) {
          console.log(`[manager][${slug}] ${count} frames in ${((Date.now()-t0)/1000).toFixed(0)}s`);
        }
      } catch (err) {
        console.error(`[manager][${slug}] Screenshot error: ${err.message}`);
      }
      const wait = Math.max(0, SCREENSHOT_MS - (Date.now() - t));
      if (wait > 0) await new Promise(r => setTimeout(r, wait));
    }
    console.log(`[manager][${slug}] Screenshot loop exited`);
  }

  loop().catch(e => console.error(`[manager][${slug}] Loop crash: ${e.message}`));
  return () => { active = false; };
}

// ── Start stream ──────────────────────────────────────────────────────────────
async function startStream(slug) {
  if (streams.has(slug)) return;
  console.log(`[manager][${slug}] Starting live stream`);
  streams.set(slug, { running: true, lastTouch: Date.now() });
  try {
    ensureDir(frameDir(slug));
    if (!fs.existsSync(framePath(slug))) writeStartingFrame(slug);
    if (!prebaked.has(slug)) {
      console.log(`[manager][${slug}] On-demand pre-bake (new slug)`);
      prebakeHLS(slug);
    }

    // Live ffmpeg starts from seg 0 — naturally overwrites pre-baked files
    const ffmpegProc = startFfmpeg(slug);
    streams.get(slug).ffmpeg = ffmpegProc;

    startRenderer(slug).then(({ browser, page }) => {
      const s = streams.get(slug);
      if (!s || !s.running) { browser.close().catch(() => {}); return; }
      const stopLoop = startScreenshotLoop(slug, page);
      s.browser = browser; s.page = page; s.stopLoop = stopLoop;
      console.log(`[manager][${slug}] ✅ Live frames active`);
    }).catch(e => {
      console.error(`[manager][${slug}] Renderer failed: ${e.message}`);
      stopStream(slug);
    });

    console.log(`[manager][${slug}] ✅ Pre-roll playing, Puppeteer loading`);
  } catch (e) {
    console.error(`[manager][${slug}] Failed: ${e.message}`);
    streams.delete(slug);
  }
}

// ── Stop stream ───────────────────────────────────────────────────────────────
async function stopStream(slug) {
  const s = streams.get(slug);
  if (!s) return;
  console.log(`[manager][${slug}] Stopping`);
  s.running = false;
  if (s.stopLoop) s.stopLoop();
  if (s.ffmpeg) {
    await new Promise(resolve => {
      const t = setTimeout(() => { s.ffmpeg.kill('SIGKILL'); resolve(); }, 5000);
      s.ffmpeg.once('exit', () => { clearTimeout(t); resolve(); });
      s.ffmpeg.kill('SIGTERM');
    });
  }
  if (s.browser) { try { await s.browser.close(); } catch (_) {} }
  try { fs.rmSync(frameDir(slug), { recursive: true, force: true }); } catch (_) {}
  streams.delete(slug);
  prebaked.delete(slug);
  console.log(`[manager][${slug}] Stopped`);
}

// ── Touch ─────────────────────────────────────────────────────────────────────
async function touchStream(slug) {
  const s = streams.get(slug);
  if (s) { s.lastTouch = Date.now(); return; }
  if (!slugExists(slug)) { console.log(`[manager][${slug}] Unknown slug`); return; }
  setImmediate(() => startStream(slug));
}

// ── Idle watcher ──────────────────────────────────────────────────────────────
function startIdleWatcher() {
  setInterval(async () => {
    const now = Date.now();
    for (const [slug, s] of streams.entries()) {
      if (!s.running) continue;
      const idle = (now - (s.lastTouch || 0)) / 1000;
      if (idle >= IDLE_TIMEOUT) {
        console.log(`[manager][${slug}] Idle ${idle.toFixed(0)}s — stopping`);
        await stopStream(slug);
      }
    }
  }, 5000);
}

// ── HTTP server ───────────────────────────────────────────────────────────────
function startHttpServer() {
  const server = http.createServer(async (req, res) => {

    // GET /hls/:slug.m3u8
    const m3u8Match = req.url.match(/^\/hls\/([^/.]+)\.m3u8$/);
    if (m3u8Match) {
      const slug     = m3u8Match[1];
      const filePath = path.join(HLS_DIR, `${slug}.m3u8`);
      await touchStream(slug);

      if (fs.existsSync(filePath)) {
        try {
          const data = fs.readFileSync(filePath);
          res.writeHead(200, {
            'Content-Type': 'application/vnd.apple.mpegurl',
            'Cache-Control': 'no-cache, no-store',
            'Access-Control-Allow-Origin': '*'
          });
          res.end(data);
        } catch (e) { res.writeHead(503); res.end('Stream error'); }
        return;
      }

      // New slug — wait for pre-bake to complete
      const deadline = Date.now() + 20000;
      while (!fs.existsSync(filePath) && Date.now() < deadline) {
        await new Promise(r => setTimeout(r, 200));
      }
      try {
        const data = fs.readFileSync(filePath);
        res.writeHead(200, {
          'Content-Type': 'application/vnd.apple.mpegurl',
          'Cache-Control': 'no-cache, no-store',
          'Access-Control-Allow-Origin': '*'
        });
        res.end(data);
      } catch (e) { res.writeHead(503); res.end('Stream starting, retry shortly'); }
      return;
    }

    // GET /hls/:slug_NNNNN.ts
    const tsMatch = req.url.match(/^\/hls\/([^/_]+)_\d+\.ts$/);
    if (tsMatch) {
      const slug = tsMatch[1];
      const s    = streams.get(slug);
      if (s) s.lastTouch = Date.now();
      const tsPath = path.join(HLS_DIR, path.basename(req.url));
      try {
        const data = fs.readFileSync(tsPath);
        res.writeHead(200, {
          'Content-Type': 'video/mp2t',
          'Cache-Control': 'no-cache',
          'Access-Control-Allow-Origin': '*'
        });
        res.end(data);
      } catch (e) { res.writeHead(404); res.end(); }
      return;
    }

    // POST /reload
    if (req.method === 'POST' && req.url === '/reload') {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true }));
      const valid = new Set(getAllSlugs());
      for (const slug of streams.keys()) {
        if (!valid.has(slug)) await stopStream(slug);
      }
      for (const slug of valid) {
        if (!prebaked.has(slug)) {
          writeStartingFrame(slug);
          prebakeHLS(slug);
        }
        // If stream is live, reload the Puppeteer page to pick up new settings.
        // Delay 500ms so the DB write from app.py is fully committed first.
        const s = streams.get(slug);
        if (s && s.running && s.page) {
          console.log(`[manager][${slug}] Settings changed — reloading renderer`);
          setTimeout(() => {
            const s2 = streams.get(slug);
            if (s2 && s2.running && s2.page) {
              s2.page.reload({ waitUntil: 'networkidle2', timeout: 15000 })
                .catch(e => console.warn(`[manager][${slug}] Reload warning: ${e.message}`));
            }
          }, 500);
        }
      }
      return;
    }

    // GET /status
    if (req.method === 'GET' && req.url === '/status') {
      const out = {};
      for (const [slug, s] of streams.entries()) {
        out[slug] = {
          running:  s.running,
          idleSecs: ((Date.now() - (s.lastTouch || 0)) / 1000).toFixed(1),
          hasLive:  !!s.browser
        };
      }
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ streams: out, count: streams.size }));
      return;
    }

    res.writeHead(404); res.end();
  });

  server.listen(MANAGER_PORT, '0.0.0.0', () => {
    console.log(`[manager] Listening on :${MANAGER_PORT}`);
  });
}

// ── Startup pre-bake ─────────────────────────────────────────────────────────
function prebakeAll() {
  const slugs = getAllSlugs();
  if (!slugs.length) { console.log('[manager] No scoreboards — skipping pre-bake'); return; }
  console.log(`[manager] Pre-baking ${slugs.length} scoreboards: ${slugs.join(', ')}`);
  for (const slug of slugs) {
    writeStartingFrame(slug);
    prebakeHLS(slug);
  }
  console.log('[manager] Pre-bake complete — ready for instant playback');
}

// ── Main ──────────────────────────────────────────────────────────────────────
async function main() {
  console.log('[manager] ScoreStream Stream Manager starting');
  console.log(`[manager] ${WIDTH}x${HEIGHT} @ ${FPS}fps | ${SEG_DURATION}s segs x ${PLAYLIST_SIZE} | idle=${IDLE_TIMEOUT}s`);
  ensureDir(HLS_DIR);
  ensureDir(FRAMES_DIR);
  prebakeAll();
  startHttpServer();
  startIdleWatcher();
  console.log('[manager] Ready');
  process.on('SIGTERM', async () => {
    console.log('[manager] Shutting down');
    for (const slug of [...streams.keys()]) await stopStream(slug);
    process.exit(0);
  });
}

main().catch(e => { console.error(`[manager] Fatal: ${e.message}`); process.exit(1); });
