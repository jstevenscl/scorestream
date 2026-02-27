/**
 * ScoreStream — Stream Manager (On-Demand)
 *
 * Architecture:
 * - Puppeteer screenshots saved as JPEG to a temp file (atomic write via rename)
 * - ffmpeg reads the same file repeatedly using -loop 1 / -re pattern
 * - This decouples screenshot speed from ffmpeg completely — no pipe starvation
 * - Streams start on first player m3u8 request, stop after IDLE_TIMEOUT seconds
 *   of no .m3u8 or .ts requests
 */

'use strict';

const http      = require('http');
const fs        = require('fs');
const path      = require('path');
const { spawn } = require('child_process');
const puppeteer = require('puppeteer-core');
const Database  = require('better-sqlite3');

// ── Config ───────────────────────────────────────────────────────────────────
const DB_PATH      = process.env.DB_PATH               || '/config/scorestream.db';
const HLS_DIR      = process.env.HLS_DIR               || '/hls';
const FRAMES_DIR   = process.env.FRAMES_DIR            || '/tmp/frames';
const WEB_BASE     = process.env.WEB_BASE              || 'http://scorestream-web';
const MANAGER_PORT = parseInt(process.env.MANAGER_PORT || '3001');
const WIDTH        = parseInt(process.env.STREAM_WIDTH  || '1920');
const HEIGHT       = parseInt(process.env.STREAM_HEIGHT || '1080');
const FPS          = parseInt(process.env.STREAM_FPS    || '4');
const JPEG_QUALITY = parseInt(process.env.JPEG_QUALITY  || '85');
const SEG_DURATION = parseInt(process.env.HLS_SEGMENT_DURATION || '6');
const PLAYLIST_SIZE= parseInt(process.env.HLS_PLAYLIST_SIZE    || '5');
const IDLE_TIMEOUT = parseInt(process.env.STREAM_IDLE_TIMEOUT  || '60');
const SCREENSHOT_MS= Math.round(1000 / FPS);

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
  } catch (e) {
    console.error(`[manager] DB error: ${e.message}`);
    return [];
  }
}

// ── Helpers ──────────────────────────────────────────────────────────────────
function ensureDir(dir) {
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
}

function frameDir(slug)  { return path.join(FRAMES_DIR, slug); }
function framePath(slug) { return path.join(frameDir(slug), 'frame.jpg'); }
function frameTmp(slug)  { return path.join(frameDir(slug), 'frame.tmp.jpg'); }

// ── ffmpeg ───────────────────────────────────────────────────────────────────
// Reads the same JPEG file on a loop. When Puppeteer updates the file,
// ffmpeg picks up the new frame automatically. No pipe, no starvation.
function startFfmpeg(slug) {
  const frame = framePath(slug);
  const args = [
    '-loglevel', 'warning',
    '-re',
    '-loop', '1',
    '-framerate', String(FPS),
    '-i', frame,
    '-vf', `scale=${WIDTH}:${HEIGHT}:force_original_aspect_ratio=decrease,pad=${WIDTH}:${HEIGHT}:(ow-iw)/2:(oh-ih)/2`,
    '-c:v', 'libx264',
    '-preset', 'veryfast',
    '-tune', 'zerolatency',
    '-b:v', '1500k',
    '-maxrate', '2000k',
    '-bufsize', '3000k',
    '-pix_fmt', 'yuv420p',
    '-g', String(FPS * SEG_DURATION),
    '-sc_threshold', '0',
    '-f', 'hls',
    '-hls_time', String(SEG_DURATION),
    '-hls_list_size', String(PLAYLIST_SIZE),
    '-hls_flags', 'delete_segments+append_list+independent_segments',
    '-hls_segment_filename', path.join(HLS_DIR, `${slug}_%05d.ts`),
    path.join(HLS_DIR, `${slug}.m3u8`)
  ];

  console.log(`[manager][${slug}] Starting ffmpeg (file-loop mode)`);
  const proc = spawn('ffmpeg', args, { stdio: ['ignore', 'ignore', 'pipe'] });

  proc.stderr.on('data', d => {
    const line = d.toString().trim();
    if (line) console.error(`[ffmpeg][${slug}] ${line}`);
  });

  proc.on('exit', (code, sig) => {
    const s = streams.get(slug);
    if (s && s.running) {
      console.log(`[manager][${slug}] ffmpeg exited (${code}/${sig}) — restarting in 2s`);
      setTimeout(() => {
        if (streams.get(slug)?.running) {
          const newProc = startFfmpeg(slug);
          streams.get(slug).ffmpeg = newProc;
        }
      }, 2000);
    }
  });

  return proc;
}

// ── Puppeteer ─────────────────────────────────────────────────────────────────
async function startRenderer(slug) {
  const url = `${WEB_BASE}/?stream&slug=${slug}`;
  console.log(`[manager][${slug}] Launching browser → ${url}`);

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

  try {
    await page.goto(url, { waitUntil: 'networkidle2', timeout: 30000 });
  } catch (e) {
    console.error(`[manager][${slug}] Nav warning: ${e.message}`);
  }

  console.log(`[manager][${slug}] Page loaded`);
  return { browser, page };
}

// ── Screenshot loop ───────────────────────────────────────────────────────────
// Writes frames atomically: screenshot → tmp file → rename to frame.jpg
// ffmpeg reads frame.jpg on its own schedule — completely decoupled
function startScreenshotLoop(slug, page) {
  let active = true;
  let count = 0;
  const tmp  = frameTmp(slug);
  const dest = framePath(slug);
  const startTime = Date.now();

  async function loop() {
    while (active && streams.get(slug)?.running) {
      const t = Date.now();
      try {
        await page.screenshot({
          path: tmp,
          type: 'jpeg',
          quality: JPEG_QUALITY,
          clip: { x: 0, y: 0, width: WIDTH, height: HEIGHT },
          omitBackground: false,
        });
        fs.renameSync(tmp, dest);
        count++;

        if (count % (FPS * 60) === 0) {
          const elapsed = (Date.now() - startTime) / 1000;
          console.log(`[manager][${slug}] ${count} frames | ${(count/elapsed).toFixed(1)}fps avg`);
        }
      } catch (err) {
        console.error(`[manager][${slug}] Screenshot error: ${err.message}`);
      }

      const elapsed = Date.now() - t;
      const wait = Math.max(0, SCREENSHOT_MS - elapsed);
      if (wait > 0) await new Promise(r => setTimeout(r, wait));
    }
    console.log(`[manager][${slug}] Screenshot loop exited`);
  }

  loop().catch(e => console.error(`[manager][${slug}] Screenshot loop crash: ${e.message}`));
  return () => { active = false; };
}

// ── Start stream ──────────────────────────────────────────────────────────────
async function startStream(slug) {
  if (streams.has(slug)) return;

  console.log(`[manager][${slug}] Starting stream (on-demand)`);
  streams.set(slug, { running: true, lastTouch: Date.now() });

  try {
    ensureDir(frameDir(slug));

    // Start browser and get first frame before ffmpeg starts
    const { browser, page } = await startRenderer(slug);

    // Take initial frame so ffmpeg has something to read immediately
    await page.screenshot({
      path: framePath(slug),
      type: 'jpeg',
      quality: JPEG_QUALITY,
      clip: { x: 0, y: 0, width: WIDTH, height: HEIGHT },
    });
    console.log(`[manager][${slug}] Initial frame captured`);

    // Start ffmpeg — it will find the frame file immediately
    const ffmpegProc = startFfmpeg(slug);

    // Start screenshot loop to keep updating the frame
    const stopLoop = startScreenshotLoop(slug, page);

    streams.set(slug, {
      running: true,
      lastTouch: Date.now(),
      browser,
      page,
      ffmpeg: ffmpegProc,
      stopLoop,
    });

    console.log(`[manager][${slug}] ✅ Stream running → ${HLS_DIR}/${slug}.m3u8`);
  } catch (e) {
    console.error(`[manager][${slug}] Failed to start: ${e.message}`);
    streams.delete(slug);
  }
}

// ── Stop stream ───────────────────────────────────────────────────────────────
async function stopStream(slug) {
  const s = streams.get(slug);
  if (!s) return;

  console.log(`[manager][${slug}] Stopping stream`);
  s.running = false;

  if (s.stopLoop) s.stopLoop();

  if (s.ffmpeg) {
    await new Promise(resolve => {
      const timer = setTimeout(() => { s.ffmpeg.kill('SIGKILL'); resolve(); }, 5000);
      s.ffmpeg.once('exit', () => { clearTimeout(timer); resolve(); });
      s.ffmpeg.kill('SIGTERM');
    });
  }

  if (s.browser) {
    try { await s.browser.close(); } catch (_) {}
  }

  // Clean up frame files
  try { fs.rmSync(frameDir(slug), { recursive: true, force: true }); } catch (_) {}

  streams.delete(slug);
  console.log(`[manager][${slug}] Stopped`);
}

// ── Touch ─────────────────────────────────────────────────────────────────────
async function touchStream(slug) {
  const s = streams.get(slug);
  if (s) {
    s.lastTouch = Date.now();
    return;
  }
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

    // GET /hls/:slug.m3u8 — touch stream, wait for file, serve it
    const m3u8Match = req.url.match(/^\/hls\/([^/.]+)\.m3u8$/);
    if (m3u8Match) {
      const slug = m3u8Match[1];
      await touchStream(slug);

      const filePath = path.join(HLS_DIR, `${slug}.m3u8`);
      const deadline = Date.now() + 10000;
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
      } catch (e) {
        res.writeHead(503);
        res.end('Stream starting, retry shortly');
      }
      return;
    }

    // GET /hls/:slug_NNNNN.ts — touch stream to reset idle, serve segment
    const tsMatch = req.url.match(/^\/hls\/([^/_]+)_\d+\.ts$/);
    if (tsMatch) {
      const slug = tsMatch[1];
      const s = streams.get(slug);
      if (s) s.lastTouch = Date.now();

      const tsPath = path.join(HLS_DIR, path.basename(req.url));
      try {
        const data = fs.readFileSync(tsPath);
        res.writeHead(200, {
          'Content-Type': 'video/mp2t',
          'Cache-Control': 'no-cache',
          'Access-Control-Allow-Origin': '*',
        });
        res.end(data);
      } catch (e) {
        res.writeHead(404);
        res.end();
      }
      return;
    }

    // POST /reload
    if (req.method === 'POST' && req.url === '/reload') {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true }));
      // Stop any streams whose slugs no longer exist in DB
      const valid = new Set(getAllSlugs());
      for (const slug of streams.keys()) {
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
  ensureDir(FRAMES_DIR);

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
