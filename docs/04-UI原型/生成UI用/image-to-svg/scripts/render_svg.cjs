#!/usr/bin/env node
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawnSync } = require("child_process");

function redactHome(value) {
  if (typeof value !== "string") return value;
  return value.split(os.homedir()).join("~");
}

function managedRuntimeRoots() {
  const configured = (process.env.IMAGE_TO_SVG_RUNTIME_ROOTS || "")
    .split(path.delimiter)
    .filter(Boolean)
    .map((entry) => entry === "~" || entry.startsWith(`~${path.sep}`)
      ? path.join(os.homedir(), entry.slice(entry === "~" ? 1 : 2))
      : path.resolve(entry));
  return [...new Set([...configured, path.join(os.homedir(), ".cache", "codex-runtimes")])]
    .filter((entry) => fs.existsSync(entry) && fs.statSync(entry).isDirectory());
}

function managedRuntimePaths(relative) {
  const candidates = [];
  for (const root of managedRuntimeRoots()) {
    const direct = path.join(root, relative);
    if (fs.existsSync(direct)) candidates.push(direct);
    for (const entry of fs.readdirSync(root)) {
      const nested = path.join(root, entry, relative);
      if (fs.existsSync(nested)) candidates.push(nested);
    }
  }
  return [...new Set(candidates)];
}

function usage() {
  console.error(`Usage:
  render_svg.cjs input.svg output.png --physical-width N --physical-height N [--dpr N] [--background COLOR]
  render_svg.cjs input.svg output.png --width CSS_PX --height CSS_PX [--dpr N] [--background COLOR]
  render_svg.cjs --doctor`);
  process.exit(2);
}

function finitePositive(value) {
  return Number.isFinite(value) && value > 0;
}

function parseArgs(argv) {
  if (argv.length === 1 && argv[0] === "--doctor") return { doctor: true };
  if (argv.length < 2) usage();
  const result = {
    doctor: false,
    input: argv[0],
    output: argv[1],
    width: null,
    height: null,
    physicalWidth: null,
    physicalHeight: null,
    dpr: 1,
    background: "transparent",
  };
  for (let index = 2; index < argv.length; index += 1) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!["--width", "--height", "--physical-width", "--physical-height", "--dpr", "--background"].includes(key) || value === undefined) usage();
    if (key === "--width") result.width = Number(value);
    if (key === "--height") result.height = Number(value);
    if (key === "--physical-width") result.physicalWidth = Number(value);
    if (key === "--physical-height") result.physicalHeight = Number(value);
    if (key === "--dpr") result.dpr = Number(value);
    if (key === "--background") result.background = value;
    index += 1;
  }

  const hasCss = result.width !== null || result.height !== null;
  const hasPhysical = result.physicalWidth !== null || result.physicalHeight !== null;
  if (hasCss === hasPhysical || !finitePositive(result.dpr)) usage();
  if (hasCss && (!finitePositive(result.width) || !finitePositive(result.height))) usage();
  if (hasPhysical && (!Number.isInteger(result.physicalWidth) || !Number.isInteger(result.physicalHeight) || result.physicalWidth <= 0 || result.physicalHeight <= 0)) usage();

  if (hasPhysical) {
    result.cssWidth = result.physicalWidth / result.dpr;
    result.cssHeight = result.physicalHeight / result.dpr;
    result.expectedPhysicalWidth = result.physicalWidth;
    result.expectedPhysicalHeight = result.physicalHeight;
  } else {
    result.cssWidth = result.width;
    result.cssHeight = result.height;
    result.expectedPhysicalWidth = Math.round(result.width * result.dpr);
    result.expectedPhysicalHeight = Math.round(result.height * result.dpr);
  }

  if (!/^(?:transparent|#[0-9a-fA-F]{3,8}|(?:rgb|rgba|hsl|hsla)\([0-9.,%\s+-]+\)|[a-zA-Z]+)$/.test(result.background)) {
    throw new Error("Unsafe or unsupported --background value. Use transparent, a CSS color name, hex, rgb(), rgba(), hsl(), or hsla().");
  }
  return result;
}

function loadPlaywright() {
  const explicit = process.env.PLAYWRIGHT_PATH;
  const nodePathCandidates = (process.env.NODE_PATH || "")
    .split(path.delimiter)
    .filter(Boolean)
    .flatMap((directory) => [path.join(directory, "playwright"), path.join(directory, "playwright-core")]);
  const managedModuleDirectories = managedRuntimePaths(path.join("dependencies", "node", "node_modules"));
  const managedRuntimeCandidates = managedModuleDirectories
    .flatMap((modules) => [path.join(modules, "playwright"), path.join(modules, "playwright-core")]);
  const candidates = [
    "playwright",
    "playwright-core",
    explicit,
    explicit && path.join(explicit, "playwright"),
    explicit && path.join(explicit, "playwright-core"),
    ...nodePathCandidates,
    ...managedRuntimeCandidates,
  ].filter(Boolean);
  const errors = [];
  for (const candidate of candidates) {
    try {
      const loaded = require(candidate);
      if (loaded && loaded.chromium) return { module: loaded, source: candidate };
    } catch (error) {
      errors.push(`${redactHome(candidate)}: ${redactHome(error.code || error.message)}`);
    }
  }
  throw new Error(`Playwright was not found in the current, configured, or existing agent-managed runtimes. Continue with core SVG reconstruction or set PLAYWRIGHT_PATH/NODE_PATH. Tried: ${errors.join("; ")}`);
}

function findChrome() {
  const home = os.homedir();
  const candidates = [
    process.env.CHROME_BIN,
    process.env.CHROMIUM_BIN,
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    process.env.PROGRAMFILES && path.join(process.env.PROGRAMFILES, "Google/Chrome/Application/chrome.exe"),
    process.env["PROGRAMFILES(X86)"] && path.join(process.env["PROGRAMFILES(X86)"], "Google/Chrome/Application/chrome.exe"),
    process.env.LOCALAPPDATA && path.join(process.env.LOCALAPPDATA, "Google/Chrome/Application/chrome.exe"),
    process.env.PROGRAMFILES && path.join(process.env.PROGRAMFILES, "Microsoft/Edge/Application/msedge.exe"),
    path.join(home, ".cache/ms-playwright"),
  ].filter(Boolean);
  return candidates.find((candidate) => fs.existsSync(candidate) && fs.statSync(candidate).isFile());
}

async function launchBrowser() {
  const loaded = loadPlaywright();
  const chrome = findChrome();
  const launch = { headless: true };
  if (chrome) launch.executablePath = chrome;
  try {
    const browser = await loaded.module.chromium.launch(launch);
    return { browser, playwrightSource: redactHome(loaded.source), browserSource: redactHome(chrome || "playwright-managed chromium") };
  } catch (error) {
    throw new Error(`Enhanced browser QA is unavailable because a real Chromium browser could not be launched. Continue with core SVG reconstruction and available visual checks. Cause: ${redactHome(error.message)}`);
  }
}

function pngDimensions(filePath) {
  const buffer = fs.readFileSync(filePath);
  const signature = "89504e470d0a1a0a";
  if (buffer.length < 24 || buffer.subarray(0, 8).toString("hex") !== signature) {
    throw new Error("Screenshot output is not a valid PNG file");
  }
  return [buffer.readUInt32BE(16), buffer.readUInt32BE(20)];
}

function findPython() {
  const managedRuntimeCandidates = managedRuntimePaths(path.join("dependencies", "python", "bin", "python3"));
  const candidates = [process.env.IMAGE_TO_SVG_PYTHON, process.env.PYTHON_BIN, "python3", ...managedRuntimeCandidates, "/usr/bin/python3", "python"].filter(Boolean);
  for (const candidate of candidates) {
    const result = spawnSync(candidate, ["-c", "import PIL"], { encoding: "utf8" });
    if (result.status === 0) return candidate;
  }
  throw new Error("An existing Python runtime with Pillow is required to crop odd physical dimensions. Set IMAGE_TO_SVG_PYTHON or PYTHON_BIN.");
}

function cropPng(input, output, width, height) {
  const python = findPython();
  const helper = path.join(__dirname, "crop_png.py");
  const result = spawnSync(python, [helper, input, output, String(width), String(height)], { encoding: "utf8" });
  if (result.status !== 0) {
    throw new Error(`PNG crop failed: ${redactHome(result.stderr || result.stdout)}`);
  }
}

async function doctor() {
  const result = { node: process.version, playwright: null, browser: null, launchable: false };
  let launched;
  try {
    launched = await launchBrowser();
    result.playwright = launched.playwrightSource;
    result.browser = launched.browserSource;
    result.launchable = true;
    const page = await launched.browser.newPage();
    await page.setContent("<!doctype html><title>vectorizer doctor</title>");
    await page.close();
    console.log(JSON.stringify(result, null, 2));
  } finally {
    if (launched) await launched.browser.close();
  }
}

async function render(args) {
  const svg = fs.readFileSync(args.input, "utf8");
  if (
    /<(?:image|feImage|foreignObject|script|iframe|object|embed|video|canvas)\b/i.test(svg)
    || /(?:href|src)\s*=\s*["'](?:data:|https?:|file:)/i.test(svg)
    || /background-image\s*:/i.test(svg)
    || /url\(\s*["']?\s*(?:data:|https?:|file:)/i.test(svg)
  ) {
    throw new Error("Refusing to render an SVG that embeds raster/external content. Run svg_audit.py for details.");
  }

  const launched = await launchBrowser();
  try {
    const context = await launched.browser.newContext({
      viewport: { width: Math.ceil(args.cssWidth), height: Math.ceil(args.cssHeight) },
      deviceScaleFactor: args.dpr,
    });
    const page = await context.newPage();
    const transparent = args.background.toLowerCase() === "transparent";
    const background = transparent ? "transparent" : args.background;
    const html = `<!doctype html><meta charset="utf-8"><style>
      *{box-sizing:border-box}html,body{margin:0;width:100%;height:100%;overflow:hidden;background:${background}}
      body{display:block}svg{display:block;width:${args.cssWidth}px;height:${args.cssHeight}px}
    </style>${svg}`;
    await page.setContent(html, { waitUntil: "load" });
    const output = path.resolve(args.output);
    const temporaryOutput = `${output}.capture-${process.pid}.png`;
    await page.screenshot({
      path: temporaryOutput,
      omitBackground: transparent,
      captureBeyondViewport: false,
    });
    await context.close();

    const expected = [args.expectedPhysicalWidth, args.expectedPhysicalHeight];
    const captured = pngDimensions(temporaryOutput);
    if (captured[0] < expected[0] || captured[1] < expected[1]) {
      fs.rmSync(temporaryOutput, { force: true });
      throw new Error(`Renderer size contract failed: expected at least ${expected[0]}x${expected[1]}, got ${captured[0]}x${captured[1]}`);
    }
    if (captured[0] === expected[0] && captured[1] === expected[1]) {
      fs.renameSync(temporaryOutput, output);
    } else {
      cropPng(temporaryOutput, output, expected[0], expected[1]);
      fs.rmSync(temporaryOutput, { force: true });
    }
    const actual = pngDimensions(output);
    if (actual[0] !== expected[0] || actual[1] !== expected[1]) {
      throw new Error(`Renderer size contract failed after crop: expected ${expected[0]}x${expected[1]}, got ${actual[0]}x${actual[1]}`);
    }
    console.log(JSON.stringify({
      input: redactHome(path.resolve(args.input)),
      output: redactHome(path.resolve(args.output)),
      css_size: [args.cssWidth, args.cssHeight],
      dpr: args.dpr,
      physical_size: actual,
      captured_before_crop: captured,
      background: args.background,
      playwright: launched.playwrightSource,
      browser: launched.browserSource,
    }, null, 2));
  } finally {
    await launched.browser.close();
  }
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.doctor) return doctor();
  return render(args);
}

main().catch((error) => {
  console.error(redactHome(error.stack || String(error)));
  process.exit(1);
});
