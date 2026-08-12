// main.js — Electron 主进程入口
//
// 职责：
//   1) 应用生命周期（single-instance / window-all-closed / activate）
//   2) userData 隔离（不污染机器上其他 Electron app 缓存）
//   3) 创建主窗口（默认加载 archive/web/index.html；M1 阶段先 about:blank 验骨架）
//   4) 接入菜单 + 下载接管（lib/* 模块化）
//
// 关联文档：docs/05-扩展功能/changes/add-desktop-electron/{proposal,delta,design,tasks}.md
'use strict';

const path = require('node:path');
const { app, BrowserWindow } = require('electron');

const { createMainWindow } = require('./lib/window');
const { buildMenu } = require('./lib/menu');
const { attachDownloadHandlers } = require('./lib/download');
const { parseApiBase } = require('./lib/api-base');
const { createLogger } = require('./lib/logger');
const { createYuanbaoWindow } = require('./lib/yuanbao-window');

// ---------- 1. userData 隔离 ----------
// 避免与机器上其他 Electron 应用共享缓存目录
const USER_DATA = path.join(app.getPath('appData'), 'video-dedup-desktop');
app.setPath('userData', USER_DATA);

// ---------- 2. 单实例锁 ----------
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  // 第二个实例直接退出；用户重启时聚焦已有窗口
  app.quit();
} else {
  app.on('second-instance', () => {
    const wins = BrowserWindow.getAllWindows();
    if (wins.length > 0) {
      const w = wins[0];
      if (w.isMinimized()) w.restore();
      w.focus();
    }
  });
}

// ---------- 3. 日志 ----------
const log = createLogger({
  logDir: path.join(__dirname, 'logs'),
  filename: 'desktop.log',
});

// ---------- 4. 主窗口工厂 + 下载接管 ----------
let mainWindow = null;

function bootstrap() {
  const apiBase = parseApiBase({
    envVar: process.env.VIDEODEDUP_API_BASE,
    defaultBase: 'http://124.71.209.36:8765',
  });
  log.info(`[main] API_BASE = ${apiBase}`);

  mainWindow = createMainWindow({
    apiBase,
    loadTarget: process.env.VIDEODEDUP_LOAD_TARGET
      || path.join(__dirname, '..', '..', 'archive', 'web', 'index.html'),
    log,
  });

  attachDownloadHandlers({
    session: mainWindow.webContents.session,
    getMainWindow: () => mainWindow,
    log,
  });

  // 元宝独立浮动 BrowserWindow（与 Chrome 扩展 content-yuanbao.js 复用同一份 DOM driver）
  const yuanbao = createYuanbaoWindow({ mainWindow, log });
  log.info('[main] yuanbao window attached (independent floating)');

  buildMenu({ mainWindow, log });
}

// ---------- 5. 生命周期 ----------
app.whenReady().then(() => {
  bootstrap();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) bootstrap();
  });
});

app.on('window-all-closed', () => {
  // 非 macOS：所有窗口关闭后退出；macOS：保持 dock 行为
  if (process.platform !== 'darwin') app.quit();
});