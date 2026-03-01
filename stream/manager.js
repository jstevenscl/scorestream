/**
 * ScoreStream — Stream Manager (On-Demand)
 *
 * Architecture:
 *  - At startup, pre-bakes a "loading" HLS bundle for every known slug.
 *    The pre-bake writes real .ts segments + a live-style m3u8 (no ENDLIST)
 *    so VLC treats it as a live stream from the first request.
 *  - When VLC requests a slug, touchStream() starts live ffmpeg + Puppeteer
 *    in the background. ffmpeg continues segment numbering from where pre-bake
 *    left off (append_list), so VLC sees a seamless rolling playlist.
 *  - FPS=1 by default. The scoreboard clock ticks once/sec — 1fps captures
 *    every meaningful change at minimal CPU cost.
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

// Number of pre-roll segments encoded at startup per slug.
// These are real .ts files VLC can play immediately on first press.
const PREROLL_SEGMENTS = 3;

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

// ── Pre-bake: encode real .ts segments + write live-style m3u8 ───────────────
// Key insight: we do NOT use ffmpeg's HLS muxer here because it always writes
// #EXT-X-ENDLIST which makes VLC treat the stream as finished VOD.
// Instead we encode individual .ts segments with ffmpeg's mpegts muxer,
// then hand-write a live m3u8 (no ENDLIST, rolling window) ourselves.
// The live ffmpeg pass uses append_list starting at segment PREROLL_SEGMENTS,
// so VLC sees a continuous sequence with no gaps or discontinuities.
function prebakeHLS(slug) {
  const frame  = framePath(slug);
  const m3u8   = path.join(HLS_DIR, `${slug}.m3u8`);

  // Clean stale files
  try {
    fs.readdirSync(HLS_DIR)
      .filter(f => f.startsWith(slug + '_') && f.endsWith('.ts'))
      .forEach(f => fs.unlinkSync(path.join(HLS_DIR, f)));
    if (fs.existsSync(m3u8)) fs.unlinkSync(m3u8);
  } catch (_) {}

  // Encode each pre-roll segment individually as raw mpegts
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
      console.error(`[manager][${slug}] Segment ${i} encode failed: ${e.message}`);
      return;
    }
  }

  // Write a live-style m3u8 — no ENDLIST, window = PLAYLIST_SIZE most recent segs.
  // Start window at the last PLAYLIST_SIZE pre-roll segments (or all if fewer).
  const windowStart = Math.max(0, PREROLL_SEGMENTS - PLAYLIST_SIZE);
  const seqNum      = windowStart; // EXT-X-MEDIA-SEQUENCE matches first seg in window
  let playlist = [
    '#EXTM3U',
    '#EXT-X-VERSION:3',
    `#EXT-X-TARGETDURATION:${SEG_DURATION}`,
    `#EXT-X-MEDIA-SEQUENCE:${seqNum}`,
  ];
  for (let i = windowStart; i < PREROLL_SEGMENTS; i++) {
    playlist.push(`#EXTINF:${SEG_DURATION}.000000,`);
    playlist.push(`${slug}_${String(i).padStart(5, '0')}.ts`);
  }
  fs.writeFileSync(m3u8, playlist.join('\n') + '\n');

  console.log(`[manager][${slug}] Pre-baked ${PREROLL_SEGMENTS} segments (live playlist)`);
  prebaked.add(slug);
}

// ── ffmpeg (live) ────────────────────────────────────────────────────────────
// Starts at segment PREROLL_SEGMENTS so numbering is continuous with pre-bake.
// append_list merges with the existing playlist; delete_segments cleans old ones.
function startFfmpeg(slug) {
  const frame = framePath(slug);
  const args = [
    '-loglevel', 'warning',
    '-re', '-loop', '1', '-framerate', String(FPS), '-i', frame,
    '-vf', 'format=yuv420p',
    '-c:v', 'libx264', '-preset', 'veryfast', '-tune', 'stillimage',
    '-b:v', '800k', '-maxrate', '1200k', '-bufsize', '2000k',
    '-pix_fmt', 'yuv420p',
    '-g', String(FPS * SEG_DURATION), '-sc_threshold', '0',
    '-f', 'hls',
    '-hls_time', String(SEG_DURATION),
    '-hls_list_size', String(PLAYLIST_SIZE),
    '-hls_flags', 'delete_segments+append_list+independent_segments',
    '-hls_start_number_source', 'generic',
    '-start_number', String(PREROLL_SEGMENTS),   // continue from pre-bake
    '-hls_segment_filename', path.join(HLS_DIR, `${slug}_%05d.ts`),
    path.join(HLS_DIR, `${slug}.m3u8`)
  ];

  console.log(`[manager][${slug}] Starting live ffmpeg (seg start=${PREROLL_SEGMENTS})`);
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
          const secs = ((Date.now() - t0) / 1000).toFixed(0);
          console.log(`[manager][${slug}] ${count} frames in ${secs}s`);
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
  prebaked.delete(slug); // force re-prebake on next request
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

      // Pre-baked → instant response, no waiting
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

      // Fallback for new slugs created after startup (blocking wait)
      const deadline = Date.now() + 15000;
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
