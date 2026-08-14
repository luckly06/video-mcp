// preload.js — 安全桥（contextIsolation 开启）
'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('desktop', {
  // 下载进度
  onDownloadProgress(cb) {
    if (typeof cb !== 'function') throw new TypeError('requires a function');
    const handler = (_evt, payload) => { try { cb(payload); } catch (e) { console.error(e); } };
    ipcRenderer.on('download-progress', handler);
    return () => ipcRenderer.removeListener('download-progress', handler);
  },

  // 外链
  async openExternal(url) {
    if (typeof url !== 'string') return { ok: false, reason: 'url must be a string' };
    try { return await ipcRenderer.invoke('shell:open-external', url); }
    catch (e) { return { ok: false, reason: e.message || String(e) }; }
  },

  // 打开指定文件夹（打包后用于打开「用户自己选的下载目录」）
  async openFolder(dirPath) {
    if (typeof dirPath !== 'string') return { ok: false, reason: 'dirPath must be a string' };
    try { return await ipcRenderer.invoke('app:open-folder', dirPath); }
    catch (e) { return { ok: false, reason: e.message || String(e) }; }
  },

  // 元宝 BrowserWindow — 无 modal，无 confirm
  async openYuanbao() {
    try { return await ipcRenderer.invoke('yuanbao:show'); }
    catch (e) { return { ok: false, reason: e.message || String(e) }; }
  },
  async closeYuanbao() {
    try { return await ipcRenderer.invoke('yuanbao:hide'); }
    catch (e) { return { ok: false, reason: e.message || String(e) }; }
  },
  async isYuanbaoReady() {
    try { return await ipcRenderer.invoke('yuanbao:is-ready'); }
    catch (e) { return { ready: false, reason: e.message || String(e) }; }
  },
  async runYuanbaoRewrite(args) {
    try { return await ipcRenderer.invoke('yuanbao:run-rewrite', args); }
    catch (e) { return { ok: false, error: e.message || String(e) }; }
  },
  onYuanbaoDone(cb) {
    if (typeof cb !== 'function') throw new TypeError('requires a function');
    const handler = (_evt, payload) => { try { cb(payload); } catch (e) { console.error(e); } };
    ipcRenderer.on('yuanbao-done', handler);
    return () => ipcRenderer.removeListener('yuanbao-done', handler);
  },

  // TTS 失败原生弹窗（桌面端）：渲染进程调用，主进程弹系统对话框
  async showTtsWarning(payload) {
    try { return await ipcRenderer.invoke('app:show-tts-warning', payload); }
    catch (e) { return { ok: false, reason: e.message || String(e) }; }
  },
});