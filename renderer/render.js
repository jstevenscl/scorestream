/**
 * ScoreStream Pro — Headless Renderer
 *
 * Launches headless Chromium, loads the scoreboard,
 * and streams raw RGBA frames into a named pipe for FFmpeg.
 *
 * One renderer feeds ALL channels — FFmpeg splits the stream
 * into per-channel HLS outputs.
 */

const puppeteer = require('puppeteer-core');
const { execSync, spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

const SCOREBOARD_URL = process.env.SCOREBOARD_URL || 'http://scorestream-web/';
const WIDTH          = parseInt(process.env.STREAM_WIDTH  || '1920');
const HEIGHT         = parseInt(process.env.STREAM_HEIGHT || '1080');
const FPS            = parseInt(process.env.STREAM_FPS    || '30');
const PIPE_PATH      = '/pipes/video_raw';

// Retry delay helper
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function waitForPipe(pipePath, maxWait = 30000) {
  const start = Date.now();
  while (Date.now() - start < maxWait) {
    if (fs.existsSync(pipePath)) return true;
    await sleep(500);
  }
  return false;
}

async function main() {
  console.log(`[renderer] Starting ScoreStream renderer`);
  console.log(`[renderer] Target: ${SCOREBOARD_URL}`);
  console.log(`[renderer] Resolution: ${WIDTH}x${HEIGHT} @ ${FPS}fps`);

  // Ensure pipe directory exists
  fs.mkdirSync('/pipes', { recursive: true });

  // Create named pipe if it doesn't exist
  if (!fs.existsSync(PIPE_PATH)) {
    try {
      execSync(`mkfifo ${PIPE_PATH}`);
      console.log(`[renderer] Created named pipe: ${PIPE_PATH}`);
    } catch(e) {
      // Pipe may already exist from a previous run
      console.log(`[renderer] Pipe already exists`);
    }
  }

  // Launch browser
  let browser;
  let retries = 0;
  while (retries < 5) {
    try {
      browser = await puppeteer.launch({
        executablePath: process.env.PUPPETEER_EXECUTABLE_PATH || '/usr/bin/chromium-browser',
        headless: true,
        args: [
          '--no-sandbox',
          '--disable-setuid-sandbox',
          '--disable-dev-shm-usage',
          '--disable-gpu',
          '--disable-software-rasterizer',
          '--disable-extensions',
          '--disable-background-timer-throttling',
          '--disable-renderer-backgrounding',
          '--disable-backgrounding-occluded-windows',
          `--window-size=${WIDTH},${HEIGHT}`,
        ],
      });
      console.log(`[renderer] Browser launched`);
      break;
    } catch (e) {
      retries++;
      console.error(`[renderer] Browser launch failed (attempt ${retries}/5): ${e.message}`);
      await sleep(3000);
    }
  }

  if (!browser) {
    console.error('[renderer] Could not launch browser after 5 attempts. Exiting.');
    process.exit(1);
  }

  const page = await browser.newPage();
  await page.setViewport({ width: WIDTH, height: HEIGHT });

  // Load scoreboard — retry until web container is ready
  let loaded = false;
  retries = 0;
  while (!loaded && retries < 20) {
    try {
      await page.goto(SCOREBOARD_URL, {
        waitUntil: 'networkidle0',
        timeout: 15000,
      });
      loaded = true;
      console.log(`[renderer] Scoreboard loaded`);
    } catch (e) {
      retries++;
      console.error(`[renderer] Page load failed (${retries}/20): ${e.message}`);
      await sleep(3000);
    }
  }

  if (!loaded) {
    console.error('[renderer] Could not load scoreboard. Exiting.');
    await browser.close();
    process.exit(1);
  }

  // Wait for scoreboard to fully render
  await sleep(3000);

  // Capture frames in a loop and write to pipe
  console.log(`[renderer] Starting frame capture loop → ${PIPE_PATH}`);

  const frameInterval = Math.floor(1000 / FPS);

  // Open pipe for writing (non-blocking)
  let pipeStream;
  try {
    pipeStream = fs.createWriteStream(PIPE_PATH, { flags: 'w' });
  } catch (e) {
    console.error(`[renderer] Cannot open pipe: ${e.message}`);
    await browser.close();
    process.exit(1);
  }

  pipeStream.on('error', (e) => {
    console.log(`[renderer] Pipe write error (FFmpeg may have restarted): ${e.message}`);
  });

  let frameCount = 0;
  let running = true;

  process.on('SIGTERM', () => { running = false; });
  process.on('SIGINT',  () => { running = false; });

  while (running) {
    const start = Date.now();
    try {
      // Capture screenshot as raw PNG
      const screenshot = await page.screenshot({
        type: 'png',
        clip: { x: 0, y: 0, width: WIDTH, height: HEIGHT },
      });

      if (pipeStream.writable) {
        pipeStream.write(screenshot);
      }

      frameCount++;
      if (frameCount % (FPS * 10) === 0) {
        console.log(`[renderer] ${frameCount} frames captured`);
      }
    } catch (e) {
      console.error(`[renderer] Frame capture error: ${e.message}`);
      await sleep(1000);
    }

    // Maintain target FPS
    const elapsed = Date.now() - start;
    const wait = Math.max(0, frameInterval - elapsed);
    await sleep(wait);
  }

  console.log('[renderer] Shutting down');
  pipeStream.end();
  await browser.close();
}

main().catch(e => {
  console.error('[renderer] Fatal error:', e);
  process.exit(1);
});
