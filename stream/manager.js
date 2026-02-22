/**
 * ScoreStream — Stream Manager
 *
 * Reads scoreboards from SQLite DB and maintains one renderer+ffmpeg
 * pair per scoreboard. Exposes POST /reload so the API can trigger
 * an immediate resync when scoreboards are created, updated, or deleted.
 *
 * Architecture:
 *   - One Puppeteer instance per scoreboard slug → writes JPEG frames to named pipe
 *   - One ffmpeg process per scoreboard slug → reads pipe → writes HLS segments
 *   - Pipes live in PIPES_DIR (container-internal tmpfs, no Docker volume needed)
 *   - HLS output goes to HLS_DIR (shared volume with scorestream-web)
 */

'use strict';

const http       = require('http');
const fs         = require('fs');
const path       = require('path');
const { spawn }  = require('child_process');
const puppeteer  = require('puppeteer-core');
const Database   = require('better-sqlite3');

// ── Config ────────────────────────────────────────────────────────────────────
const DB_PATH        = process.env.DB_PATH        || '/config/scorestream.db';
const PIPES_DIR      = process.env.PIPES_DIR      || '/tmp/pipes';
const HLS_DIR        = process.env.HLS_DIR        || '/hls';
const WEB_BASE       = process.env.WEB_BASE       || 'http://scorestream-web';
const MANAGER_PORT   = parseInt(process.env.MANAGER_PORT || '3001');
const WIDTH          = parseInt(process.env.STREAM_WIDTH  || '1920');
const HEIGHT         = parseInt(process.env.STREAM_HEIGHT || '1080');
const FPS            = parseInt(process.env.STREAM_FPS    || '10');
const JPEG_QUALITY   = parseInt(process.env.JPEG_QUALITY  || '85');
const SEG_DURATION   = parseInt(process.env.HLS_SEGMENT_DURATION || '2');
const PLAYLIST_SIZE  = parseInt(process.env.HLS_PLAYLIST_SIZE    || '10');

// ── State ─────────────────────────────────────────────────────────────────────
// Map of slug → { browser, page, ffmpeg, frameTimer, running }
const streams = new Map();

// ── DB helpers ────────────────────────────────────────────────────────────────
function getScoreboards() {
  try {
    const db = new Database(DB_PATH, { readonly: true, fileMustExist: true });
    const rows = db.prepare('SELECT slug, sport_config FROM scoreboards').all();
    db.close();
    return rows;
  } catch (e) {
    console.error(`[manager] DB read error: ${e.message}`);
    return [];
  }
}

// ── Pipe helpers ──────────────────────────────────────────────────────────────
function ensureDir(dir) {
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
}

function pipePath(slug) {
  return path.join(PIPES_DIR, `${slug}.rawvideo`);
}

function createPipe(slug) {
  const p = pipePath(slug);
  if (!fs.existsSync(p)) {
    // mkfifo via sync child process
    const { spawnSync } = require('child_process');
    const r = spawnSync('mkfifo', [p]);
    if (r.status !== 0) throw new Error(`mkfifo failed for ${p}`);
    console.log(`[manager] Created pipe: ${p}`);
  }
  return p;
}

function removePipe(slug) {
  const p = pipePath(slug);
  try { fs.unlinkSync(p); } catch (_) {}
}

// ── ffmpeg ────────────────────────────────────────────────────────────────────
function startFfmpeg(slug) {
  const pipe = pipePath(slug);
  const args = [
    '-f', 'image2pipe',
    '-framerate', String(FPS),
    '-use_wallclock_as_timestamps', '1',
    '-i', pipe,
    '-vf', `scale=${WIDTH}:${HEIGHT}:force_original_aspect_ratio=decrease,pad=${WIDTH}:${HEIGHT}:(ow-iw)/2:(oh-ih)/2,fps=fps=${FPS}`,
    '-c:v', 'libx264',
    '-preset', 'veryfast',
    '-tune', 'zerolatency',
    '-b:v', '2000k',
    '-maxrate', '2500k',
    '-bufsize', '4000k',
    '-pix_fmt', 'yuv420p',
    '-g', String(FPS * 2),
    '-sc_threshold', '0',
    '-f', 'hls',
    '-hls_time', String(SEG_DURATION),
    '-hls_list_size', String(PLAYLIST_SIZE),
    '-hls_flags', 'delete_segments+append_list+independent_segments',
    '-hls_segment_filename', path.join(HLS_DIR, `${slug}_%05d.ts`),
    path.join(HLS_DIR, `${slug}.m3u8`)
  ];

  console.log(`[manager][${slug}] Starting ffmpeg`);
  const proc = spawn('ffmpeg', args, { stdio: ['ignore', 'pipe', 'pipe'] });

  proc.stderr.on('data', d => {
    // Only log ffmpeg errors, not the noisy progress lines
    const line = d.toString();
    if (line.includes('error') || line.includes('Error')) {
      console.error(`[ffmpeg][${slug}] ${line.trim()}`);
    }
  });

  proc.on('exit', (code, sig) => {
    if (streams.get(slug)?.running) {
      console.log(`[manager][${slug}] ffmpeg exited (${code}/${sig}) — restarting in 3s`);
      setTimeout(() => {
        if (streams.get(slug)?.running) restartStream(slug);
      }, 3000);
    }
  });

  return proc;
}

// ── Puppeteer renderer ────────────────────────────────────────────────────────
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
    console.error(`[manager][${slug}] Navigation warning: ${e.message} — continuing`);
  }

  console.log(`[manager][${slug}] Page loaded`);
  return { browser, page };
}

// ── Frame capture loop ────────────────────────────────────────────────────────
function startFrameLoop(slug, page, pipeStream) {
  const FRAME_MS = Math.round(1000 / FPS);
  let running = true;
  let frameCount = 0, skipped = 0, errCount = 0;
  const startTime = Date.now();
  let nextFrameAt = startTime;

  async function loop() {
    while (running && streams.get(slug)?.running) {
      const now = Date.now();

      // Drift correction
      if (nextFrameAt < now - FRAME_MS * 2) {
        const behind = Math.floor((now - nextFrameAt) / FRAME_MS);
        nextFrameAt += behind * FRAME_MS;
        skipped += behind;
      }

      try {
        if (!pipeStream.writableNeedDrain) {
          const frame = await page.screenshot({
            type: 'jpeg',
            quality: JPEG_QUALITY,
            clip: { x: 0, y: 0, width: WIDTH, height: HEIGHT },
            omitBackground: false,
          });
          pipeStream.write(frame);
          frameCount++;
          errCount = 0;
        } else {
          skipped++;
        }

        if (frameCount % (FPS * 60) === 0 && frameCount > 0) {
          const elapsed = (Date.now() - startTime) / 1000;
          console.log(`[manager][${slug}] ${frameCount} frames | ${(frameCount/elapsed).toFixed(1)}fps | ${skipped} skipped`);
        }
      } catch (err) {
        errCount++;
        if (errCount % 10 === 0) console.error(`[manager][${slug}] Frame error (${errCount}): ${err.message}`);
        if (errCount >= 30) {
          console.error(`[manager][${slug}] Too many errors — reloading page`);
          try {
            await page.reload({ waitUntil: 'domcontentloaded', timeout: 10000 });
            errCount = 0;
          } catch (e2) {
            console.error(`[manager][${slug}] Reload failed: ${e2.message}`);
          }
        }
      }

      nextFrameAt += FRAME_MS;
      const sleepMs = nextFrameAt - Date.now();
      if (sleepMs > 0) await new Promise(r => setTimeout(r, sleepMs));
    }

    running = false;
    console.log(`[manager][${slug}] Frame loop exited`);
  }

  loop().catch(e => console.error(`[manager][${slug}] Frame loop crash: ${e.message}`));
  return () => { running = false; };
}

// ── Start one stream ──────────────────────────────────────────────────────────
async function startStream(slug) {
  if (streams.has(slug)) {
    console.log(`[manager][${slug}] Already running — skipping`);
    return;
  }

  console.log(`[manager][${slug}] Starting stream`);
  streams.set(slug, { running: true });

  try {
    createPipe(slug);
    const ffmpegProc = startFfmpeg(slug);

    // Give ffmpeg a moment to open the pipe for reading
    await new Promise(r => setTimeout(r, 1000));

    const { browser, page } = await startRenderer(slug);
    const pipeStream = fs.createWriteStream(pipePath(slug));
    const stopFrameLoop = startFrameLoop(slug, page, pipeStream);

    streams.set(slug, {
      running: true,
      browser,
      page,
      ffmpeg: ffmpegProc,
      pipeStream,
      stopFrameLoop,
    });

    console.log(`[manager][${slug}] ✅ Stream running → ${HLS_DIR}/${slug}.m3u8`);
  } catch (e) {
    console.error(`[manager][${slug}] Failed to start: ${e.message}`);
    streams.delete(slug);
  }
}

// ── Stop one stream ───────────────────────────────────────────────────────────
async function stopStream(slug) {
  const s = streams.get(slug);
  if (!s) return;

  console.log(`[manager][${slug}] Stopping stream`);
  s.running = false;

  if (s.stopFrameLoop) s.stopFrameLoop();

  // Close pipe stream so ffmpeg gets EOF and finishes current segment
  if (s.pipeStream) {
    try { s.pipeStream.end(); } catch (_) {}
  }

  // Give ffmpeg up to 5s to finish its current segment cleanly
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

  removePipe(slug);
  streams.delete(slug);
  console.log(`[manager][${slug}] Stopped`);
}

// ── Restart one stream ────────────────────────────────────────────────────────
async function restartStream(slug) {
  await stopStream(slug);
  await startStream(slug);
}

// ── Sync streams with DB ──────────────────────────────────────────────────────
async function syncStreams() {
  const rows = getScoreboards();
  const dbSlugs = new Set(rows.map(r => r.slug));
  const runningSlugs = new Set(streams.keys());

  // Start streams for new scoreboards
  for (const slug of dbSlugs) {
    if (!runningSlugs.has(slug)) {
      await startStream(slug);
    }
  }

  // Stop streams for deleted scoreboards
  for (const slug of runningSlugs) {
    if (!dbSlugs.has(slug)) {
      console.log(`[manager] Scoreboard "${slug}" removed from DB — stopping stream`);
      await stopStream(slug);
    }
  }
}

// ── HTTP server for /reload trigger ──────────────────────────────────────────
function startHttpServer() {
  const server = http.createServer(async (req, res) => {
    if (req.method === 'POST' && req.url === '/reload') {
      console.log('[manager] Reload triggered by API');
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true }));
      // Run sync after responding so the API doesn't wait on us
      setImmediate(syncStreams);
    } else {
      res.writeHead(404);
      res.end();
    }
  });

  server.listen(MANAGER_PORT, '0.0.0.0', () => {
    console.log(`[manager] HTTP reload endpoint listening on :${MANAGER_PORT}`);
  });
}

// ── Startup ───────────────────────────────────────────────────────────────────
async function main() {
  console.log('[manager] ScoreStream Stream Manager starting');
  console.log(`[manager] DB: ${DB_PATH}`);
  console.log(`[manager] HLS output: ${HLS_DIR}`);
  console.log(`[manager] Resolution: ${WIDTH}x${HEIGHT} @ ${FPS}fps`);

  ensureDir(PIPES_DIR);
  ensureDir(HLS_DIR);

  startHttpServer();

  // Wait for scorestream-web to be ready before starting streams
  console.log('[manager] Waiting for scorestream-web to be healthy...');
  let ready = false;
  for (let i = 0; i < 60; i++) {
    try {
      await new Promise((resolve, reject) => {
        const req = http.get(`${WEB_BASE}/health`, res => {
          resolve(res.statusCode === 200);
        });
        req.on('error', reject);
        req.setTimeout(2000, () => { req.destroy(); reject(new Error('timeout')); });
      });
      ready = true;
      break;
    } catch (_) {
      await new Promise(r => setTimeout(r, 2000));
    }
  }

  if (!ready) {
    console.error('[manager] scorestream-web never became healthy — starting anyway');
  } else {
    console.log('[manager] scorestream-web is healthy');
  }

  await syncStreams();

  // Graceful shutdown
  process.on('SIGTERM', async () => {
    console.log('[manager] SIGTERM — shutting down all streams');
    for (const slug of [...streams.keys()]) {
      await stopStream(slug);
    }
    process.exit(0);
  });
}

main().catch(e => {
  console.error(`[manager] Fatal: ${e.message}`);
  process.exit(1);
});
