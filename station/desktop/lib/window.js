// lib/window.js — BrowserWindow 工厂
'use strict';

const path = require('node:path');
const { BrowserWindow } = require('electron');

/**
 * 创建主窗口并加载 archive/web/ 或测试目标。
 *
 * 安全配置（contextIsolation: true, nodeIntegration: false, sandbox: true）
 * 是 Electron 当前推荐的安全基线；archive/web/ 的 inline onclick 走 CSP meta 放行，
 * 不依赖 nodeIntegration。
 *
 * @param {object} opts
 * @param {string} opts.apiBase   注入到 URL query，让 renderer 端的 17-23 行兜底逻辑读取
 * @param {string} opts.loadTarget 要加载的本地文件绝对路径
 * @param {string} opts.iconPath   窗口图标路径
 * @param {object} opts.log       logger 实例（仅 console 转发，主进程不走文件）
 * @returns {BrowserWindow}
 */
function createMainWindow({ apiBase, loadTarget, iconPath, log }) {
  const win = new BrowserWindow({
    width: 1280,
    height: 900,
    minWidth: 960,
    minHeight: 640,
    title: '视频去重工位',
    icon: iconPath || undefined,
    backgroundColor: '#0e1014',
    show: false,
    webPreferences: {
      preload: path.join(__dirname, '..', 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      // 关闭 webSecurity 会让 CORS 完全开放，反而引入风险。
      // 我们的 Origin=null + Host 头校验已经走通，不关。
      webSecurity: true,
    },
  });

  // ready-to-show 之前不显示，避免白闪
  win.once('ready-to-show', () => {
    win.show();
    log?.info?.('[window] ready-to-show');
  });

  // 加载目标：本地 file + query 注入
  // file:// 协议下，archive/web/app.js:17-23 的 window.location 派生会得到 "file://"，
  // 需要在 URL 上追加 ?apiBase=... 让兜底逻辑读到正确的远端 MCP 地址。
  const fileUrl = pathToFileUrlWithQuery(loadTarget, { apiBase });
  log?.info?.(`[window] loadURL = ${fileUrl}`);

  win.loadURL(fileUrl).catch((e) => {
    log?.error?.('[window] loadURL failed:', e && e.message ? e.message : e);
  });

  // DevTools：dev 模式开启（通过 process.argv 包含 --remote-debugging-port 隐含 dev 启动）
  if (process.argv.includes('--enable-logging') || !!process.env.VIDEODEDUP_DEV) {
    win.webContents.openDevTools({ mode: 'detach' });
  }

  return win;
}

/**
 * 把本地文件路径转成 file:// URL，并追加 query string。
 *
 * @param {string} absPath
 * @param {object} query kv 对象
 * @returns {string}
 */
function pathToFileUrlWithQuery(absPath, query) {
  // Windows 路径含反斜杠，要先转成正斜杠；冒号也要 escape
  let normalized = absPath.replace(/\\/g, '/');
  if (!normalized.startsWith('/')) normalized = '/' + normalized;
  const encoded = encodeURI(normalized).replace(/#/g, '%23').replace(/\?/g, '%3F');

  const qs = Object.entries(query || {})
    .filter(([, v]) => v !== undefined && v !== null && v !== '')
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
    .join('&');

  return `file://${encoded}${qs ? '?' + qs : ''}`;
}

module.exports = { createMainWindow, pathToFileUrlWithQuery };
