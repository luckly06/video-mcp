'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const { resolvePython } = require('../python-runtime');

test('显式存在的 VU_PYTHON 优先于自动探测', () => {
  assert.equal(
    resolvePython({ env: { VU_PYTHON: process.execPath }, platform: 'win32', homedir: 'Z:\\missing' }),
    process.execPath,
  );
});

test('没有可用运行时时保留系统 Python 作为开发态后备', () => {
  const result = resolvePython({ env: {}, platform: 'win32', homedir: 'Z:\\missing' });
  assert.ok(result.endsWith('python.exe') || result === 'python');
});

test('发布态 Python 使用短资源路径，降低 Windows 解压路径长度', () => {
  const { bundledPythonPath } = require('../python-runtime');
  assert.ok(bundledPythonPath().endsWith(require('node:path').join('py', 'python.exe')));
  assert.equal(bundledPythonPath().includes(require('node:path').join('station', 'vendor', 'python')), false);
});
