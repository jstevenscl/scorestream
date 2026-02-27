'use strict';

const http          = require('http');
const fs            = require('fs');
const path          = require('path');
const { spawn, spawnSync } = require('child_process');
const puppeteer     = require('puppeteer-core');
const Database      = require('better-sqlite3');

// ── Config ───────────────────────────────────────────────────────────────────
const DB_PATH      = process.env.DB_PATH               || '/config/scorestream.db';
const HLS_DIR      = process.env.HLS_DIR               || '/hls';
const PIPES_DIR    = process.env.PIPES_DIR             || '/tmp/pipes';
const WEB_BASE     = process.env.WEB_BASE              || 'http://scorestream-web';
const MANAGER_PORT = parseInt(process.env.MANAGER_PORT || '3001');
const WIDTH        = parseInt(process.env.STREAM_WIDTH  || '1920');
const HEIGHT       = parseInt(process.env.STREAM_HEIGHT || '1080');
const FPS          = parseInt(process.env.STREAM_FPS    || '10');
const SEG_DURATION = parseInt(process.env.HLS_SEGMENT_DURATION || '4');
const PLAYLIST_SIZE= parseInt(process.env.HLS_PLAYLIST_SIZE    || '6');
const IDLE_TIMEOUT = parseInt(process.env.STREAM_IDLE_TIMEOUT  || '60');
const BITRATE      = process.env.STREAM_BITRATE        || '2000k';

// ── State ────────────────────────────────────────────────────────────────────
const streams = new Map();

// ── DB ───────────────────────────────────────────────────────────────────────
function slugExists(slug) {
  try {
    const db  = new Database(DB_PATH, { fileMustExist: true });
    const row = db.prepare('SELECT slug FROM scoreboards WHERE slug = ?').get(slug);
    db.close();
    return !!row;
  } catch (e) {
    console.error(`[manager] DB error: ${e.message}`);
    return false;
  }
}

function getAllSlugs() {
  try {
    const db   = new Database(DB_PATH, { fileMustExist: true });
    const rows = db.prepare('SELECT slug FROM scoreboards').all();
    db.close();
    return rows.map(r => r.slug);
  } catch (e) { return []; }
}

// ── Helpers ──────────────────────────────────────────────────────────────────
function ensureDir(dir) {
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
}

function pipePath(slug) {
  return path.join(PIPES_DIR, `${slug}.webm`);
}

function createPipe(slug) {
  const p = pipePath(slug);
  if (fs.existsSync(p)) try { fs.unlinkSync(p); } catch(_) {}
  const r = spawnSync('mkfifo', [p]);
  if (r.status !== 0) throw new Error(`mkfifo failed: ${r.stderr}`);
  return p;
}

function cleanHlsFiles(slug) {
  try {
    const files = fs.readdirSync(HLS_DIR)
      .filter(f => f.startsWith(slug + '_') || f === slug + '.m3u8');
    files.forEach(f => { try { fs.unlinkSync(path.join(HLS_DIR, f)); } catch(_) {} });
    console.log(`[manager][${slug}] Cleaned ${files.length} old HLS files`);
  } catch(_) {}
}

// ── Start stream ──────────────────────────────────────────────────────────────
async function startStream(slug) {
  if (streams.has(slug)) return;

  console.log(`[manager][${slug}] Starting stream`);
  streams.set(slug, { running: true, lastTouch: Date.now() });

  try {
    cleanHlsFiles(slug);
    ensureDir(PIPES_DIR);
    const pipe = createPipe(slug);

    // Start ffmpeg reading from the named pipe (webm input)
    const ffmpegArgs = [
      '-loglevel', 'warning',
      '-re',
      '-i', pipe,
      '-vf', `fps=${FPS},scale=${WIDTH}:${HEIGHT}:force_original_aspect_ratio=decrease,pad=${WIDTH}:${HEIGHT}:(ow-iw)/2:(oh-ih)/2`,
      '-c:v', 'libx264',
      '-preset', 'veryfast',
      '-tune', 'zerolatency',
      '-b:v', BITRATE,
      '-pix_fmt', 'yuv420p',
      '-g', String(FPS * SEG_DURATION),
      '-sc_threshold', '0',
      '-f', 'hls',
      '-hls_time', String(SEG_DURATION),
      '-hls_list_size', String(PLAYLIST_SIZE),
      '-hls_flags', 'delete_segments+append_list+independent_segments',
      '-hls_segment_filename', path.join(HLS_DIR, `${slug}_%05d.ts`),
      path.join(HLS_DIR, `${slug}.m3u8`),
    ];

    console.log(`[manager][${slug}] Starting ffmpeg`);
    const ffmpeg = spawn('ffmpeg', ffmpegArgs, { stdio: ['ignore', 'ignore', 'pipe'] });

    ffmpeg.stderr.on('data', d => {
      const line = d.toString().trim();
      if (line) console.error(`[ffmpeg][${slug}] ${line}`);
    });

    ffmpeg.on('exit', (code, sig) => {
      if (streams.get(slug)?.running) {
        console.log(`[manager][${slug}] ffmpeg exited (${code}/${sig})`);
      }
    });

    // Launch browser
    console.log(`[manager][${slug}] Launching browser`);
    const browser = await puppeteer.launch({
      executablePath: process.env.PUPPETEER_EXECUTABLE_PATH || '/usr/bin/chromium',
      headless: true,
      args: [
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-dev-shm-usage',
        '--disable-gpu',
        '--disable-software-rasterizer',
        `--window-size=${WIDTH},${HEIGHT}`,
      ],
    });

    const page = await browser.newPage();
    await page.setViewport({ width: WIDTH, height: HEIGHT, deviceScaleFactor: 1 });

    const url = `${WEB_BASE}/?stream&slug=${slug}`;
    console.log(`[manager][${slug}] Loading → ${url}`);
    try {
      await page.goto(url, { waitUntil: 'networkidle2', timeout: 30000 });
    } catch(e) {
      console.error(`[manager][${slug}] Nav warning: ${e.message}`);
    }
    console.log(`[manager][${slug}] Page loaded`);

    // Start screencast writing to named pipe
    // ffmpeg is already waiting to read from the pipe
    console.log(`[manager][${slug}] Starting screencast → ${pipe}`);
    const recorder = await page.screencast({ path: pipe });

    // Wait for first HLS segment
    const m3u8Path = path.join(HLS_DIR, `${slug}.m3u8`);
    const deadline = Date.now() + 20000;
    while (!fs.existsSync(m3u8Path) && Date.now() < deadline) {
      await new Promise(r => setTimeout(r, 500));
    }

    if (!fs.existsSync(m3u8Path)) {
      throw new Error('Timed out waiting for first HLS segment');
    }

    streams.set(slug, {
      running: true,
      lastTouch: Date.now(),
      browser,
      page,
      ffmpeg,
      recorder,
      pipe,
    });

    console.log(`[manager][${slug}] ✅ Stream live → /hls/${slug}.m3u8`);

  } catch (e) {
    console.error(`[manager][${slug}] Failed to start: ${e.message}`);
    const s = streams.get(slug);
    if (s) {
      if (s.recorder) try { await s.recorder.stop(); } catch(_) {}
      if (s.ffmpeg) try { s.ffmpeg.kill(); } catch(_) {}
      if (s.browser) try { await s.browser.close(); } catch(_) {}
      if (s.pipe) try { fs.unlinkSync(s.pipe); } catch(_) {}
    }
    streams.delete(slug);
  }
}

// ── Stop stream ───────────────────────────────────────────────────────────────
async function stopStream(slug) {
  const s = streams.get(slug);
  if (!s) return;

  console.log(`[manager][${slug}] Stopping`);
  s.running = false;
  streams.delete(slug);

  if (s.recorder) try { await s.recorder.stop(); } catch(_) {}

  if (s.ffmpeg) {
    await new Promise(resolve => {
      const t = setTimeout(() => { s.ffmpeg.kill('SIGKILL'); resolve(); }, 5000);
      s.ffmpeg.once('exit', () => { clearTimeout(t); resolve(); });
      s.ffmpeg.kill('SIGTERM');
    });
  }

  if (s.browser) try { await s.browser.close(); } catch(_) {}
  if (s.pipe) try { fs.unlinkSync(s.pipe); } catch(_) {}

  console.log(`[manager][${slug}] Stopped`);
}

// ── Touch ─────────────────────────────────────────────────────────────────────
async function touchStream(slug) {
  const s = streams.get(slug);
  if (s) { s.lastTouch = Date.now(); return; }
  if (!slugExists(slug)) {
    console.log(`[manager][${slug}] Unknown slug — ignoring`);
    return;
  }
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
      const slug = m3u8Match[1];
      console.log(`[manager][${slug}] m3u8 request`);
      await touchStream(slug);

      const filePath = path.join(HLS_DIR, `${slug}.m3u8`);
      const deadline = Date.now() + 20000;
      while (!fs.existsSync(filePath) && Date.now() < deadline) {
        await new Promise(r => setTimeout(r, 300));
      }

      try {
        const data = fs.readFileSync(filePath);
        res.writeHead(200, {
          'Content-Type': 'application/vnd.apple.mpegurl',
          'Cache-Control': 'no-cache',
          'Access-Control-Allow-Origin': '*',
        });
        res.end(data);
      } catch(e) {
        res.writeHead(503);
        res.end('Stream starting, retry shortly');
      }
      return;
    }

    // GET /hls/:slug_NNNNN.ts
    const tsMatch = req.url.match(/^\/hls\/([^/_]+)_\d+\.ts$/);
    if (tsMatch) {
      const slug = tsMatch[1];
      const s = streams.get(slug);
      if (s) { s.lastTouch = Date.now(); }
      const tsPath = path.join(HLS_DIR, path.basename(req.url));
      try {
        const data = fs.readFileSync(tsPath);
        res.writeHead(200, {
          'Content-Type': 'video/mp2t',
          'Cache-Control': 'no-cache',
          'Access-Control-Allow-Origin': '*',
        });
        res.end(data);
      } catch(e) {
        res.writeHead(404);
        res.end();
      }
      return;
    }

    // POST /reload
    if (req.method === 'POST' && req.url === '/reload') {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true }));
      const valid = new Set(getAllSlugs());
      for (const slug of [...streams.keys()]) {
        if (!valid.has(slug)) await stopStream(slug);
      }
      return;
    }

    // GET /status
    if (req.method === 'GET' && req.url === '/status') {
      const out = {};
      for (const [slug, s] of streams.entries()) {
        out[slug] = {
          running: s.running,
          idleSecs: ((Date.now() - (s.lastTouch || 0)) / 1000).toFixed(1),
        };
      }
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ streams: out, count: streams.size }));
      return;
    }

    res.writeHead(404);
    res.end();
  });

  server.listen(MANAGER_PORT, '0.0.0.0', () => {
    console.log(`[manager] Listening on :${MANAGER_PORT}`);
  });
}

// ── Main ──────────────────────────────────────────────────────────────────────
async function main() {
  console.log('[manager] ScoreStream Stream Manager starting');
  console.log(`[manager] Resolution: ${WIDTH}x${HEIGHT} @ ${FPS}fps`);
  console.log(`[manager] Segment: ${SEG_DURATION}s x ${PLAYLIST_SIZE} = ${SEG_DURATION*PLAYLIST_SIZE}s buffer`);
  console.log(`[manager] Idle timeout: ${IDLE_TIMEOUT}s`);

  ensureDir(HLS_DIR);
  ensureDir(PIPES_DIR);

  startHttpServer();
  startIdleWatcher();

  console.log('[manager] Ready — streams start on first player request');

  process.on('SIGTERM', async () => {
    console.log('[manager] Shutting down');
    for (const slug of [...streams.keys()]) await stopStream(slug);
    process.exit(0);
  });
}

main().catch(e => {
  console.error(`[manager] Fatal: ${e.message}`);
  process.exit(1);
});
