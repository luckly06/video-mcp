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
const { createLogger } = require('./lib/logger');
const { createYuanbaoWindow } = require('./lib/yuanbao-window');
const { startLocalServer, stopLocalServer } = require('./lib/local-server');

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
let localServerChild = null;

async function bootstrap() {
  // 本地 MCP 后端：优先复用已有服务；否则 spawn 本机 venv 的 mcp_server.py（127.0.0.1:8765）。
  // 显式设置 VIDEODEDUP_API_BASE 时尊重外部后端，不拉起本地进程。
  let apiBase;
  const override = (process.env.VIDEODEDUP_API_BASE || '').trim();
  if (override && /^https?:\/\//i.test(override)) {
    apiBase = override.replace(/\/+$/, '');
    log.info(`[main] 使用外部 API_BASE = ${apiBase}（不拉起本地后端）`);
  } else {
    const local = await startLocalServer({ log });
    localServerChild = local.child;
    apiBase = local.baseUrl;
  }
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

app.on('will-quit', () => {
  // 退出时回收本地后端子进程（若复用外部服务则 child 为 null，noop）
  stopLocalServer(localServerChild);
});