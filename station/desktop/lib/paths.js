// lib/paths.js — 开发态 / 打包态资源路径解析
'use strict';

const path = require('node:path');
const { app } = require('electron');

function repoRoot() {
  return path.join(__dirname, '..', '..', '..');
}

function resourceRoot() {
  return app.isPackaged ? process.resourcesPath : repoRoot();
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
  return app.isPackaged
    ? path.join(process.resourcesPath, 'build', 'icon.ico')
    : path.join(__dirname, '..', 'build', 'icon.ico');
}

module.exports = {
  resolveResource,
  resolveWebIndexPath,
  resolveServerScriptPath,
  resolveExtensionFilePath,
  resolveAppIconPath,
};
