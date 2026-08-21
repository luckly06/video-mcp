// lib/yuanbao-window.js — 元宝独立浮动 BrowserWindow（替代 BrowserView，避免覆盖主窗口）
'use strict';

const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
const { spawn } = require('node:child_process');
const { BrowserWindow, ipcMain } = require('electron');
const { resolveExtensionFilePath, resolveServerScriptPath } = require('./paths');
const { resolvePython } = require('./python-runtime');

const YUANBAO_URL = 'https://yuanbao.tencent.com/';
const CONTENT_YUANBAO_JS_PATH = resolveExtensionFilePath('content-yuanbao.js');

// content-yuanbao.js 跑在页面主世界；contextIsolation 下 preload 无法直接给它塞
// chrome.runtime.sendMessage，所以先在主世界注入这个 shim，把 sendMessage 转发到
// preload 通过 contextBridge 暴露的 __desktopYuanbao.sendMessage（→ IPC 'yuanbao-done'）。
const MAIN_WORLD_SHIM = `(function () {
  window.chrome = window.chrome || {};
  window.chrome.runtime = window.chrome.runtime || {};
  window.chrome.runtime.sendMessage = function (msg) {
    try {
      if (window.__desktopYuanbao && typeof window.__desktopYuanbao.sendMessage === 'function') {
        window.__desktopYuanbao.sendMessage(msg);
      }
    } catch (_) { /* swallow */ }
  };
})();`;

// 元宝改写 CLI（复用用户 Edge 登录态驱动 msedge.exe + CDP）
const YUANBAO_CLI = resolveServerScriptPath('yuanbao_client.py');

function sendYuanbaoDone(target, data) {
  try {
    if (target && !target.isDestroyed()) {
      target.webContents.send('yuanbao-done', { action: 'yb-done', data });
    }
  } catch (_) { /* swallow */ }
}

function cleanupDir(dir) {
  if (!dir) return;
  try { fs.rmSync(dir, { recursive: true, force: true }); } catch (_) { /* noop */ }
}

function parseLastJsonLine(raw) {
  const lines = String(raw || '').trim().split(/\r?\n/).filter(Boolean).reverse();
  for (const line of lines) {
    const s = line.trim();
    if (!s.startsWith('{') || !s.endsWith('}')) continue;
    try { return JSON.parse(s); } catch (_) { /* try previous */ }
  }
  return null;
}

function createYuanbaoWindow({ mainWindow, iconPath, yuanbaoProfileDir, log }) {
  const preloadYuanbao = path.join(__dirname, 'preload-yuanbao.js');
  let contentYuanbaoSource = '';
  try { contentYuanbaoSource = fs.readFileSync(CONTENT_YUANBAO_JS_PATH, 'utf8'); }
  catch (e) { log?.error?.('[yuanbao] read content-yuanbao.js failed:', e.message); }

  let win = null;
  let injectedContentYuanbao = false;
  let lastInjectedAt = 0;
  let urlLoaded = false;

  function childEnv(extra = {}) {
    const env = { ...process.env, PYTHONDONTWRITEBYTECODE: '1', ...extra };
    if (yuanbaoProfileDir) env.VU_YUANBAO_DEBUG_PROFILE = yuanbaoProfileDir;
    return env;
  }

  function buildWindow() {
    if (win && !win.isDestroyed()) return win;
    win = new BrowserWindow({
      width: 720, height: 900, x: 100, y: 100,
      title: '元宝 (独立窗口)',
      icon: iconPath || undefined,
      backgroundColor: '#0e1014',
      parent: mainWindow || undefined, modal: false, show: false,
      webPreferences: {
        preload: preloadYuanbao,
        contextIsolation: true, nodeIntegration: false, sandbox: false,
        webSecurity: true,
        partition: 'persist:yuanbao',
      },
    });

    win.webContents.on('did-finish-load', async () => {
      const url = win.webContents.getURL();
      log?.info?.(`[yuanbao] did-finish-load: ${url}`);
      if (!/^https:\/\/(?:[\w-]+\.)?yuanbao\.tencent\.com\//i.test(url)) return;
      if (Date.now() - lastInjectedAt < 1000) return;
      try {
        await win.webContents.executeJavaScript(MAIN_WORLD_SHIM, true);
        await win.webContents.executeJavaScript(contentYuanbaoSource, true);
        injectedContentYuanbao = true; lastInjectedAt = Date.now();
        log?.info?.('[yuanbao] content-yuanbao.js injected');
      } catch (e) { log?.error?.('[yuanbao] inject failed:', e.message); }
    });

    win.on('closed', () => { log?.info?.('[yuanbao] window closed'); win = null; });
    return win;
  }

  function ensureLoaded() {
    const w = buildWindow();
    if (urlLoaded) return Promise.resolve(w);
    urlLoaded = true;
    log?.info?.('[yuanbao] first-time loadURL → ' + YUANBAO_URL);
    return w.webContents.loadURL(YUANBAO_URL)
      .then(() => w)
      .catch((e) => { urlLoaded = false; log?.error?.('[yuanbao] loadURL failed:', e.message); throw e; });
  }

  // BrowserWindow → main → mainWindow.renderer
  ipcMain.on('yuanbao-done', (_evt, payload) => {
    log?.info?.(`[yuanbao] done: ${JSON.stringify(payload).slice(0, 200)}`);
    try { if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send('yuanbao-done', payload); }
    catch (_) { /* swallow */ }
  });

  // show/hide/login/run-rewrite IPC handlers
  ipcMain.handle('yuanbao:show', async () => {
    try {
      if (!mainWindow || mainWindow.isDestroyed()) return { ok: false, reason: 'main window gone' };
      const w = await ensureLoaded();
      w.show(); w.focus();
      return { ok: true };
    } catch (e) { return { ok: false, reason: e.message || String(e) }; }
  });

  ipcMain.handle('yuanbao:hide', () => {
    try { if (win && !win.isDestroyed() && win.isVisible()) win.hide(); } catch (_) {}
    return { ok: true };
  });

  ipcMain.handle('yuanbao:toggle', async () => {
    try {
      if (!mainWindow || mainWindow.isDestroyed()) return { shown: false };
      if (win && !win.isDestroyed() && win.isVisible()) {
        win.hide(); return { shown: false };
      }
      const w = await ensureLoaded();
      w.show(); w.focus();
      return { shown: true };
    } catch (e) { return { shown: false, error: e.message || String(e) }; }
  });

  ipcMain.handle('yuanbao:is-ready', () => ({ ready: injectedContentYuanbao }));

  ipcMain.handle('yuanbao:login-debug', async () => {
    const py = resolvePython();
    const cmdArgs = [YUANBAO_CLI, '--login-debug'];
    log?.info?.(`[yuanbao] spawn debug login: ${py} ${cmdArgs.join(' ')}`);
    return await new Promise((resolve) => {
      let settled = false;
      let child = null;
      let timer = null;
      const finish = (payload) => {
        if (settled) return;
        settled = true;
        if (timer) clearTimeout(timer);
        resolve(payload);
      };

      try {
        child = spawn(py, cmdArgs, { env: childEnv(), windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
      } catch (e) {
        finish({ ok: false, reason: '启动元宝登录进程失败: ' + (e.message || String(e)) });
        return;
      }

      let out = '', err = '';
      child.stdout?.on('data', (d) => { out += d.toString('utf8'); });
      child.stderr?.on('data', (d) => { err += d.toString('utf8'); });
      child.on('error', (e) => {
        finish({ ok: false, reason: '元宝登录进程异常: ' + (e.message || String(e)) });
      });
      child.on('close', (code) => {
        const parsed = parseLastJsonLine(out);
        if (parsed && parsed.ok) {
          finish(parsed);
          return;
        }
        const reason = (parsed && (parsed.reason || parsed.error || parsed.msg))
          || err.trim()
          || out.trim()
          || `元宝登录进程退出，code=${code}`;
        finish({ ok: false, reason: String(reason).slice(0, 500) });
      });

      timer = setTimeout(() => {
        try { child?.kill?.(); } catch (_) { /* noop */ }
        finish({ ok: false, reason: '打开元宝登录窗口超时，请重试' });
      }, 45000);
    });
  });

  ipcMain.handle('yuanbao:run-rewrite', async (_evt, args) => {
    if (!args || typeof args !== 'object') return { ok: false, error: 'args must be an object' };
    // 前端预生成 request_id；主进程原样贯穿回包。缺省时兜底生成。
    const requestId = String(args.request_id || (Date.now().toString(36) + Math.random().toString(36).slice(2, 8)));
    try {
      // 1) 帧图 base64 → 临时 jpg 文件（供 yuanbao_client 上传）
      let tmpDir = null;
      const frameFiles = [];
      const frames = Array.isArray(args.frames_b64) ? args.frames_b64 : [];
      if (frames.length) {
        tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'vu_frames_'));
        frames.forEach((b64, i) => {
          if (typeof b64 !== 'string' || !b64) return;
          try {
            const fp = path.join(tmpDir, `frame_${i}.jpg`);
            fs.writeFileSync(fp, Buffer.from(b64, 'base64'));
            frameFiles.push(fp);
          } catch (_) { /* skip */ }
        });
      }
      log?.info?.(`[yuanbao] rewrite request_id=${requestId} frames_b64=${frames.length} frame_files=${frameFiles.length}`);

      // 2) spawn 本机 venv python 跑 yuanbao_client --rewrite（用系统 msedge.exe + 复用 Edge 登录态）
      const py = resolvePython();
      const cmdArgs = [
        YUANBAO_CLI, '--rewrite',
        '--frames', frameFiles.join(','),
        '--raw_text', args.raw_text || '',
        '--template', args.template || '',
        '--topic', args.topic || '',
      ];
      if (args.max_chars) cmdArgs.push('--max_chars', String(args.max_chars));

      log?.info?.(`[yuanbao] spawn msedge 改写: ${py} ${cmdArgs.join(' ')}`);

      const child = spawn(py, cmdArgs, { env: childEnv(), windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
      let out = '', err = '';
      child.stdout?.on('data', (d) => { out += d; });
      child.stderr?.on('data', (d) => { err += d; });
      child.on('error', (e) => {
        sendYuanbaoDone(mainWindow, { rewritten: null, error: 'spawn 失败: ' + e.message, request_id: requestId });
        cleanupDir(tmpDir);
      });
      child.on('close', () => {
        let parsed = { rewritten: null, error: '' };
        try { parsed = JSON.parse(out.trim()); } catch (_) {
          parsed = { rewritten: null, error: (out.trim() || err.trim() || '无输出').slice(0, 500) };
        }
        parsed.request_id = requestId;
        sendYuanbaoDone(mainWindow, parsed);
        cleanupDir(tmpDir);
      });

      return { ok: true, message: 'started', request_id: requestId };
    } catch (e) {
      log?.error?.('[yuanbao] run-rewrite failed:', e.message);
      return { ok: false, error: e.message || String(e) };
    }
  });

  return {
    show() { /* noop, renderer uses invoke */ },
    hide() {},
    toggle() {},
    isReady() { return injectedContentYuanbao; },
  };
}

module.exports = { createYuanbaoWindow };
