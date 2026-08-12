// lib/menu.js — 应用菜单（File / Edit / View / Help 最小集）
'use strict';

const { Menu, shell } = require('electron');

/**
 * 构建并安装应用菜单。
 *
 * @param {object} opts
 * @param {BrowserWindow} [opts.mainWindow] 用于 reload / toggleDevTools 的目标窗口
 * @param {object} [opts.log]
 */
function buildMenu({ mainWindow, log }) {
  const isMac = process.platform === 'darwin';

  /** @type {Electron.MenuItemConstructorOptions[]} */
  const template = [
    ...(isMac
      ? [{
          label: app_getName(),
          submenu: [
            { role: 'about' },
            { type: 'separator' },
            { role: 'services' },
            { type: 'separator' },
            { role: 'hide' },
            { role: 'hideOthers' },
            { role: 'unhide' },
            { type: 'separator' },
            { role: 'quit' },
          ],
        }]
      : []),
    {
      label: '文件',
      submenu: [
        isMac ? { role: 'close' } : { role: 'quit', label: '退出' },
      ],
    },
    {
      label: '编辑',
      submenu: [
        { role: 'undo', label: '撤销' },
        { role: 'redo', label: '重做' },
        { type: 'separator' },
        { role: 'cut', label: '剪切' },
        { role: 'copy', label: '复制' },
        { role: 'paste', label: '粘贴' },
        { role: 'selectAll', label: '全选' },
      ],
    },
    {
      label: '视图',
      submenu: [
        {
          label: '重新加载',
          accelerator: 'CmdOrCtrl+R',
          click: () => mainWindow?.webContents?.reload(),
        },
        {
          label: '强制重新加载',
          accelerator: 'CmdOrCtrl+Shift+R',
          click: () => mainWindow?.webContents?.reloadIgnoringCache(),
        },
        { type: 'separator' },
        {
          label: '开发者工具',
          accelerator: isMac ? 'Alt+Cmd+I' : 'Ctrl+Shift+I',
          click: () => mainWindow?.webContents?.toggleDevTools(),
        },
        { type: 'separator' },
        { role: 'resetZoom', label: '实际大小' },
        { role: 'zoomIn', label: '放大' },
        { role: 'zoomOut', label: '缩小' },
        { type: 'separator' },
        { role: 'togglefullscreen', label: '全屏' },
      ],
    },
    {
      label: '帮助',
      submenu: [
        {
          label: '项目说明',
          click: async () => {
            await shell.openExternal('https://github.com/luckly06/video-uniqueness');
          },
        },
        {
          label: 'MCP 服务器',
          click: async () => {
            await shell.openExternal('http://124.71.209.36:8765');
          },
        },
      ],
    },
  ];

  const menu = Menu.buildFromTemplate(template);
  Menu.setApplicationMenu(menu);
  log?.info?.('[menu] application menu installed');
}

/**
 * 取 app 名字（避免循环依赖直接 require electron.app 在 mac 平台之外的 edge case）。
 */
function app_getName() {
  // eslint-disable-next-line global-require
  const { app } = require('electron');
  return app.getName();
}

module.exports = { buildMenu };