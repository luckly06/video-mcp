// lib/download.js — 拦截 <a download> 触发的下载，接管到原生保存对话框 + 进度回传
//
// 设计要点：
//   - 不改 archive/web/app.js 的下载触发（保留 <a download href=...>）
//   - 主进程通过 session.on('will-download') 拦截，弹出系统保存对话框
//   - 进度通过 webContents.send('download-progress', ...) 推到 renderer
//   - 不解决带宽瓶颈（OSS 迁移是后续独立决策），只把"无进度 UI"变成"原生进度条"
'use strict';

const path = require('node:path');
const { app, dialog } = require('electron');

/**
 * 把 will-download 拦截器挂到指定 session 上。
 *
 * @param {object} opts
 * @param {Electron.Session} opts.session
 * @param {() => Electron.BrowserWindow | null} opts.getMainWindow
 * @param {object} [opts.log]
 */
function attachDownloadHandlers({ session, getMainWindow, log }) {
  if (!session || typeof session.on !== 'function') {
    throw new TypeError('attachDownloadHandlers requires an Electron Session');
  }

  session.on('will-download', (event, item, webContents) => {
    handleOne(event, item, webContents, { getMainWindow, log });
  });

  // shell:open-external — preload 里调用的入口（contextBridge 不会泄露 shell）
  // 这里挂在 defaultSession 上；
  // 注意：attachDownloadHandlers 是 main.js 在 mainWindow 创建之后调用一次，
  // ipcMain.handle 在此注册即可被 preload 触发。
  // eslint-disable-next-line global-require
  const { ipcMain, shell } = require('electron');
  ipcMain.handle('shell:open-external', async (_evt, url) => {
    if (typeof url !== 'string') return { ok: false, reason: 'url must be a string' };
    if (!/^https?:\/\//i.test(url)) return { ok: false, reason: 'only http/https allowed' };
    try {
      await shell.openExternal(url);
      return { ok: true };
    } catch (e) {
      return { ok: false, reason: e && e.message ? e.message : String(e) };
    }
  });

  // 打开指定文件夹（打包后用于打开「用户自己选的下载目录」）
  ipcMain.handle('app:open-folder', async (_evt, dirPath) => {
    if (typeof dirPath !== 'string' || !dirPath) return { ok: false, reason: 'dirPath required' };
    try {
      await shell.openPath(dirPath);
      return { ok: true };
    } catch (e) {
      return { ok: false, reason: e && e.message ? e.message : String(e) };
    }
  });

  log?.info?.('[download] handlers attached');
}

/**
 * 处理单个下载项：保存对话框 → setSavePath → 进度监听 → 完成事件。
 */
function handleOne(event, item, webContents, { getMainWindow, log }) {
  const filename = item.getFilename() || 'download';
  const downloadsDir = safeDownloadsDir();
  const defaultPath = path.join(downloadsDir, filename);

  // 关键：preventDefault 必须先调，再异步弹保存对话框；否则下载会被取消
  event.preventDefault();

  const win = getMainWindow() || (webContents && webContents.getOwnerBrowserWindow && webContents.getOwnerBrowserWindow()) || null;

  const send = (payload) => {
    try {
      if (win && !win.isDestroyed()) {
        win.webContents.send('download-progress', payload);
      }
    } catch (e) { /* swallow */ }
  };

  send({ phase: 'start', filename, url: item.getURL(), totalBytes: item.getTotalBytes() });

  dialog.showSaveDialog(win, {
    title: '保存去重产物',
    defaultPath,
    filters: guessFilters(filename),
  }).then(({ canceled, filePath }) => {
    if (canceled || !filePath) {
      log?.info?.(`[download] canceled: ${filename}`);
      send({ phase: 'canceled', filename });
      item.cancel();
      return;
    }

    item.setSavePath(filePath);
    log?.info?.(`[download] save → ${filePath}`);

    item.on('updated', () => {
      const received = item.getReceivedBytes();
      const total = item.getTotalBytes() || 0;
      send({
        phase: 'progress',
        filename,
        receivedBytes: received,
        totalBytes: total,
        percent: total > 0 ? received / total : 0,
        savePath: filePath,
      });
    });

    item.on('done', (_e, state) => {
      const ok = state === 'completed';
      log?.info?.(`[download] done: ${filename} → ${state}`);
      send({
        phase: 'done',
        filename,
        state,
        ok,
        savePath: ok ? filePath : null,
      });
    });
  }).catch((e) => {
    log?.error?.('[download] dialog error:', e && e.message ? e.message : e);
    send({ phase: 'error', filename, message: String(e) });
    item.cancel();
  });
}

function safeDownloadsDir() {
  try { return app.getPath('downloads'); }
  catch (_) { return app.getPath('home'); }
}

function guessFilters(filename) {
  const lower = (filename || '').toLowerCase();
  if (lower.endsWith('.mp4')) return [{ name: 'Video (MP4)', extensions: ['mp4'] }];
  if (lower.endsWith('.json')) return [{ name: 'JSON', extensions: ['json'] }];
  if (lower.endsWith('.txt')) return [{ name: 'Text', extensions: ['txt'] }];
  return [{ name: 'All Files', extensions: ['*'] }];
}

module.exports = { attachDownloadHandlers };