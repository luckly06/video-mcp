// lib/python-runtime.js — 桌面发行包 Python 运行时解析
'use strict';

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { resolveResource } = require('./paths');

function bundledPythonPath() {
  return resolveResource('station', 'vendor', 'python', 'python.exe');
}

function resolvePython({ env = process.env, platform = process.platform, homedir = os.homedir() } = {}) {
  const override = String(env.VU_PYTHON || '').trim();
  if (override && fs.existsSync(override)) return override;

  const bundled = bundledPythonPath();
  if (fs.existsSync(bundled)) return bundled;

  const workbuddy = path.join(
    homedir, '.workbuddy', 'binaries', 'python', 'envs', 'default', 'Scripts', 'python.exe'
  );
  if (fs.existsSync(workbuddy)) return workbuddy;

  return platform === 'win32' ? 'python' : 'python3';
}

module.exports = { bundledPythonPath, resolvePython };
