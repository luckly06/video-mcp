// lib/auto-updater.js — Windows/macOS 桌面版自动更新
'use strict';

const { app } = require('electron');
const { autoUpdater } = require('electron-updater');

function setupAutoUpdater({ getMainWindow, log }) {
  let checking = false;

  autoUpdater.autoDownload = false;
  autoUpdater.autoInstallOnAppQuit = true;
  autoUpdater.autoRunAppAfterInstall = true;

  const send = (payload) => {
    const win = getMainWindow?.();
    try {
      if (win && !win.isDestroyed()) win.webContents.send('app-update-status', payload);
    } catch (_) { /* noop */ }
  };

  autoUpdater.on('checking-for-update', () => {
    log?.info?.('[updater] checking-for-update');
    send({ state: 'checking' });
  });
  autoUpdater.on('update-available', (info) => {
    log?.info?.('[updater] update-available', info);
    send({ state: 'available', version: info?.version || '', releaseDate: info?.releaseDate || '' });
  });
  autoUpdater.on('update-not-available', (info) => {
    log?.info?.('[updater] update-not-available', info);
    send({ state: 'not-available', version: app.getVersion() });
  });
  autoUpdater.on('download-progress', (progress) => {
    send({
      state: 'downloading',
      percent: Number(progress?.percent || 0),
      transferred: progress?.transferred || 0,
      total: progress?.total || 0,
    });
  });
  autoUpdater.on('update-downloaded', (info) => {
    log?.info?.('[updater] update-downloaded', info);
    send({ state: 'downloaded', version: info?.version || '' });
  });
  autoUpdater.on('error', (error) => {
    log?.warn?.('[updater] error', error?.message || error);
    checking = false;
    send({ state: 'error', message: error?.message || String(error) });
  });

  async function check() {
    if (!app.isPackaged) return { ok: false, reason: '开发态不检查更新' };
    if (checking) return { ok: false, reason: '正在检查更新' };
    checking = true;
    try {
      const result = await autoUpdater.checkForUpdates();
      return { ok: true, updateInfo: result?.updateInfo || null };
    } catch (e) {
      log?.warn?.('[updater] check failed', e?.message || e);
      send({ state: 'error', message: e?.message || String(e) });
      return { ok: false, reason: e?.message || String(e) };
    } finally {
      checking = false;
    }
  }

  async function download() {
    if (!app.isPackaged) return { ok: false, reason: '开发态不下载更新' };
    try {
      await autoUpdater.downloadUpdate();
      return { ok: true };
    } catch (e) {
      log?.warn?.('[updater] download failed', e?.message || e);
      send({ state: 'error', message: e?.message || String(e) });
      return { ok: false, reason: e?.message || String(e) };
    }
  }

  function install() {
    if (!app.isPackaged) return { ok: false, reason: '开发态不安装更新' };
    try {
      autoUpdater.quitAndInstall(false, true);
      return { ok: true };
    } catch (e) {
      return { ok: false, reason: e?.message || String(e) };
    }
  }

  return { check, download, install };
}

module.exports = { setupAutoUpdater };
