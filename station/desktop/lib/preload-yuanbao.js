// lib/preload-yuanbao.js — 元宝 BrowserWindow 的 preload 脚本
'use strict';

const { contextBridge, ipcRenderer } = require('electron');

// content-yuanbao.js 运行在页面主世界；contextIsolation 下 preload 无法直接改写主世界的
// window.chrome，因此这里通过 contextBridge 暴露 __desktopYuanbao，再由主进程注入的
// 主世界 shim（见 yuanbao-window.js 的 MAIN_WORLD_SHIM）把 chrome.runtime.sendMessage
// 转发到 sendMessage(msg)，最终落到 IPC 'yuanbao-done'。
contextBridge.exposeInMainWorld('__desktopYuanbao', {
  ping() { return 'desktop-yuanbao-preload-ready'; },
  // content-yuanbao.js:48 done() → chrome.runtime.sendMessage(msg) → 主世界 shim → 这里 → IPC
  sendMessage(msg) {
    try { ipcRenderer.send('yuanbao-done', msg); } catch (_) { /* swallow */ }
  },
});
