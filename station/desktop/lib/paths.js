// lib/paths.js — 开发态 / 打包态资源路径解析
'use strict';

const path = require('node:path');
const { app } = require('electron');

function repoRoot() {
  return path.join(__dirname, '..', '..', '..');
}

function resourceRoot() {
  return app && app.isPackaged ? process.resourcesPath : repoRoot();
}

function resolveResource(...segments) {
  return path.join(resourceRoot(), ...segments);
}

function resolveWebIndexPath() {
  return resolveResource('archive', 'web', 'index.html');
}

function resolveServerScriptPath(scriptName) {
  return resolveResource('station', 'server', scriptName);
}

function resolveExtensionFilePath(filename) {
  return resolveResource('station', 'extension', filename);
}

function resolveAppIconPath() {
  if (app && app.isPackaged) {
    // macOS 上 .ico 无法用作窗口图标，回退 build/icon.png（electron-builder 也会用它生成 .icns）
    return process.platform === 'darwin'
      ? path.join(process.resourcesPath, 'build', 'icon.png')
      : path.join(process.resourcesPath, 'build', 'icon.ico');
  }
  return path.join(__dirname, '..', 'build', 'icon.ico');
}

function resolveDesktopAssetsDir() {
  return path.join(app.getPath('videos'), '视频去重素材');
}

module.exports = {
  resolveResource,
  resolveWebIndexPath,
  resolveServerScriptPath,
  resolveExtensionFilePath,
  resolveAppIconPath,
  resolveDesktopAssetsDir,
};
