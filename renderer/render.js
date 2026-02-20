/**
 * ScoreStream Renderer
 * Captures the scoreboard via Puppeteer and pipes PNG frames to stdout,
 * where ffmpeg reads them via image2pipe.
 */

const puppeteer = require('puppeteer-core');
const fs        = require('fs');

const WIDTH          = parseInt(process.env.STREAM_WIDTH  || '1920');
const HEIGHT         = parseInt(process.env.STREAM_HEIGHT || '1080');
const FPS            = parseInt(process.env.STREAM_FPS    || '30');
const SCOREBOARD_URL = process.env.SCOREBOARD_URL || 'http://scorestream-web/';
const PIPE_PATH      = process.env.PIPE_PATH || '/pipes/scoreboard.rawvideo';
const FRAME_MS       = Math.round(1000 / FPS);

(async () => {
  console.error(`[renderer] Launching Chromium — ${WIDTH}x${HEIGHT} @ ${FPS}fps`);

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

  // Open the pipe for writing PNG frames
  const pipe = fs.createWriteStream(PIPE_PATH);

  let running    = true;
  let frameCount = 0;
  let errCount   = 0;

  process.on('SIGTERM', () => { running = false; });
  process.on('SIGINT',  () => { running = false; });

  while (running) {
    const start = Date.now();
    try {
      const frame = await page.screenshot({
        type: 'png',
        clip: { x: 0, y: 0, width: WIDTH, height: HEIGHT },
        omitBackground: false,
      });
      pipe.write(frame);
      frameCount++;
      errCount = 0;
      if (frameCount % (FPS * 10) === 0) {
        console.error(`[renderer] ${frameCount} frames captured`);
      }
    } catch (err) {
      errCount++;
      console.error(`[renderer] Frame error (${errCount}): ${err.message}`);
      // If we get 30 consecutive errors, try reloading the page
      if (errCount === 30) {
        console.error('[renderer] Too many errors — reloading page');
        try {
          await page.reload({ waitUntil: 'domcontentloaded', timeout: 10000 });
          errCount = 0;
        } catch (e2) {
          console.error(`[renderer] Reload failed: ${e2.message}`);
        }
      }
      // If 100 consecutive errors, exit and let Docker restart us
      if (errCount >= 100) {
        console.error('[renderer] Fatal: too many consecutive errors, exiting');
        process.exit(1);
      }
    }
    const elapsed = Date.now() - start;
    const wait    = Math.max(0, FRAME_MS - elapsed);
    if (wait > 0) await new Promise(r => setTimeout(r, wait));
  }

  console.error('[renderer] Shutting down gracefully');
  pipe.end();
  await browser.close();
  process.exit(0);
})();
