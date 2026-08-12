// lib/yuanbao-window.js — 元宝独立浮动 BrowserWindow（替代 BrowserView，避免覆盖主窗口）
'use strict';

const path = require('node:path');
const fs = require('node:fs');
const { BrowserWindow, ipcMain } = require('electron');

const YUANBAO_URL = 'https://yuanbao.tencent.com/';
const CONTENT_YUANBAO_JS_PATH = path.join(
  __dirname, '..', '..', 'extension', 'content-yuanbao.js'
);

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

function createYuanbaoWindow({ mainWindow, log }) {
  const preloadYuanbao = path.join(__dirname, 'preload-yuanbao.js');
  let contentYuanbaoSource = '';
  try { contentYuanbaoSource = fs.readFileSync(CONTENT_YUANBAO_JS_PATH, 'utf8'); }
  catch (e) { log?.error?.('[yuanbao] read content-yuanbao.js failed:', e.message); }

  let win = null;
  let injectedContentYuanbao = false;
  let lastInjectedAt = 0;
  let urlLoaded = false;

  function buildWindow() {
    if (win && !win.isDestroyed()) return win;
    win = new BrowserWindow({
      width: 720, height: 900, x: 100, y: 100,
      title: '元宝 (独立窗口)',
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

  // show/hide/run-rewrite IPC handlers
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

  ipcMain.handle('yuanbao:run-rewrite', async (_evt, args) => {
    if (!args || typeof args !== 'object') return { ok: false, error: 'args must be an object' };
    try {
      const w = await ensureLoaded();
      if (!w || w.isDestroyed()) return { ok: false, error: 'BrowserWindow 创建失败' };
      w.show(); w.focus();
      log?.info?.('[yuanbao] window shown for run-rewrite');

      // 确保主世界 shim 存在（chrome.runtime.sendMessage → __desktopYuanbao 转发桥）
      try { await w.webContents.executeJavaScript(MAIN_WORLD_SHIM, true); } catch (_) {}

      // 轮询等 content-yuanbao.js 注入
      let injected = false;
      for (let i = 0; i < 30; i++) {
        try {
          const t = await w.webContents.executeJavaScript(
            `(() => { try { return typeof window.__ybInject; } catch (_) { return 'error'; } })()`
          );
          if (t === 'function') { injected = true; break; }
        } catch (_) {}
        await new Promise((r) => setTimeout(r, 500));
      }
      if (!injected) {
        try {
          await w.webContents.executeJavaScript(MAIN_WORLD_SHIM, true);
          await w.webContents.executeJavaScript(contentYuanbaoSource, true);
        } catch (_) {}
      }

      const safeArgs = JSON.stringify(args);
      await w.webContents.executeJavaScript(
        `window.__ybInject && window.__ybInject(${safeArgs}); 'started';`, true
      );
      log?.info?.('[yuanbao] run-rewrite dispatched');
      return { ok: true, message: 'started' };
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