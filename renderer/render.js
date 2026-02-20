/**
 * ScoreStream Renderer
 * Uses Puppeteer/Chromium to render the scoreboard and pipe raw video frames
 * to a named pipe for FFmpeg to consume.
 */

const puppeteer = require('puppeteer-core');
const fs        = require('fs');

const WIDTH         = parseInt(process.env.STREAM_WIDTH  || '1920');
const HEIGHT        = parseInt(process.env.STREAM_HEIGHT || '1080');
const FPS           = parseInt(process.env.STREAM_FPS    || '30');
const SCOREBOARD_URL = process.env.SCOREBOARD_URL || 'http://scorestream-web/';
const PIPE_PATH     = '/pipes/scoreboard.rawvideo';
const FRAME_MS      = Math.round(1000 / FPS);

(async () => {
  console.log(`[renderer] Launching Chromium — ${WIDTH}x${HEIGHT} @ ${FPS}fps`);

  const browser = await puppeteer.launch({
    executablePath: process.env.PUPPETEER_EXECUTABLE_PATH || '/usr/bin/chromium',
    headless: true,
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-dev-shm-usage',
      '--disable-gpu',
      `--window-size=${WIDTH},${HEIGHT}`,
    ],
  });

  const page = await browser.newPage();
  await page.setViewport({ width: WIDTH, height: HEIGHT });

  console.log(`[renderer] Navigating to: ${SCOREBOARD_URL}`);
  await page.goto(SCOREBOARD_URL, { waitUntil: 'networkidle2', timeout: 30000 });
  console.log('[renderer] Page loaded — starting frame capture');

  // Open the raw video pipe for writing
  const pipe = fs.createWriteStream(PIPE_PATH);

  let running = true;
  process.on('SIGTERM', () => { running = false; });
  process.on('SIGINT',  () => { running = false; });

  while (running) {
    const start = Date.now();
    try {
      const frame = await page.screenshot({
        type: 'raw',
        encoding: 'binary',
        clip: { x: 0, y: 0, width: WIDTH, height: HEIGHT },
      });
      pipe.write(Buffer.from(frame));
    } catch (err) {
      console.error('[renderer] Frame capture error:', err.message);
    }
    const elapsed = Date.now() - start;
    const wait    = Math.max(0, FRAME_MS - elapsed);
    await new Promise(r => setTimeout(r, wait));
  }

  console.log('[renderer] Shutting down');
  pipe.end();
  await browser.close();
})();
