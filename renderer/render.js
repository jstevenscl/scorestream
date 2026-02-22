/**
 * ScoreStream Renderer
 * Captures the scoreboard via Puppeteer and pipes JPEG frames to ffmpeg
 * via image2pipe. Uses sustainable frame pacing to prevent stream jitter.
 *
 * KEY DESIGN DECISIONS:
 * - JPEG not PNG: ~10x faster encode (15-25ms vs 100-200ms per frame)
 * - 10fps default: scoreboard content doesn't need 30fps, and 10fps is
 *   reliably achievable without frame bunching on modest hardware
 * - Drift-correcting loop: measures actual elapsed time and adjusts next
 *   sleep so average FPS stays accurate even if individual frames vary
 * - Pipe drain check: if pipe buffer is full, skip frame rather than
 *   accumulate a backlog that causes HLS segment timing drift
 */

const puppeteer = require('puppeteer-core');
const fs        = require('fs');

const WIDTH          = parseInt(process.env.STREAM_WIDTH  || '1920');
const HEIGHT         = parseInt(process.env.STREAM_HEIGHT || '1080');
const FPS            = parseInt(process.env.STREAM_FPS    || '10');   // 10fps default — sustainable for scoreboard
const JPEG_QUALITY   = parseInt(process.env.JPEG_QUALITY  || '85');   // 85% quality, ~300-600KB per frame
const SCOREBOARD_URL = process.env.SCOREBOARD_URL || 'http://scorestream-web/';
const PIPE_PATH      = process.env.PIPE_PATH || '/pipes/scoreboard.rawvideo';
const FRAME_MS       = Math.round(1000 / FPS);

(async () => {
  console.error(`[renderer] Launching Chromium — ${WIDTH}x${HEIGHT} @ ${FPS}fps (JPEG q${JPEG_QUALITY})`);

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

  console.error(`[renderer] Navigating to: ${SCOREBOARD_URL}`);
  try {
    await page.goto(SCOREBOARD_URL, { waitUntil: 'networkidle2', timeout: 30000 });
  } catch (e) {
    console.error(`[renderer] Navigation warning: ${e.message} — continuing anyway`);
  }
  console.error('[renderer] Page loaded — starting frame capture');

  // Open the pipe for writing
  const pipe = fs.createWriteStream(PIPE_PATH);

  let running    = true;
  let frameCount = 0;
  let skipped    = 0;
  let errCount   = 0;

  // Drift-correcting timing: track when the NEXT frame should fire
  // relative to process start, not relative to each frame's completion.
  // This prevents drift accumulation over time.
  const startTime = Date.now();
  let nextFrameAt = startTime;

  process.on('SIGTERM', () => { running = false; });
  process.on('SIGINT',  () => { running = false; });

  while (running) {
    const now = Date.now();

    // If we're behind schedule by more than 2 frame periods, skip ahead
    // rather than trying to catch up (which would cause a burst of frames)
    if (nextFrameAt < now - FRAME_MS * 2) {
      const skippedFrames = Math.floor((now - nextFrameAt) / FRAME_MS);
      nextFrameAt += skippedFrames * FRAME_MS;
      skipped += skippedFrames;
    }

    try {
      // Skip frame if pipe is backed up (write buffer full)
      // This prevents accumulating a backlog that causes HLS timing drift
      if (!pipe.writableNeedDrain) {
        const frame = await page.screenshot({
          type: 'jpeg',
          quality: JPEG_QUALITY,
          clip: { x: 0, y: 0, width: WIDTH, height: HEIGHT },
          omitBackground: false,
        });
        pipe.write(frame);
        frameCount++;
        errCount = 0;
      } else {
        skipped++;
      }

      if (frameCount % (FPS * 30) === 0 && frameCount > 0) {
        const elapsed = (Date.now() - startTime) / 1000;
        const actualFps = frameCount / elapsed;
        console.error(`[renderer] ${frameCount} frames | ${actualFps.toFixed(1)}fps actual | ${skipped} skipped`);
      }
    } catch (err) {
      errCount++;
      console.error(`[renderer] Frame error (${errCount}): ${err.message}`);
      if (errCount === 30) {
        console.error('[renderer] Too many errors — reloading page');
        try {
          await page.reload({ waitUntil: 'domcontentloaded', timeout: 10000 });
          errCount = 0;
        } catch (e2) {
          console.error(`[renderer] Reload failed: ${e2.message}`);
        }
      }
      if (errCount >= 100) {
        console.error('[renderer] Fatal: exiting for Docker restart');
        process.exit(1);
      }
    }

    // Sleep until the next frame deadline (drift-correcting)
    nextFrameAt += FRAME_MS;
    const sleepMs = nextFrameAt - Date.now();
    if (sleepMs > 0) await new Promise(r => setTimeout(r, sleepMs));
  }

  console.error('[renderer] Shutting down gracefully');
  pipe.end();
  await browser.close();
  process.exit(0);
})();
