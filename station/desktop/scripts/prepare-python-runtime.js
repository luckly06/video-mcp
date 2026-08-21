// 从当前构建机的 Python 环境生成随包精简运行时。
// 产物位于 station/vendor/python（已被 .gitignore 排除），electron-builder 会随包复制。
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');

const stationDir = path.resolve(__dirname, '..', '..');
const targetDir = path.join(stationDir, 'vendor', 'python');
const configured = String(process.env.VU_BUILD_PYTHON || '').trim();
const workbuddy = path.join(
  process.env.USERPROFILE || '', '.workbuddy', 'binaries', 'python', 'envs', 'default', 'Scripts', 'python.exe'
);
const python = configured || (fs.existsSync(workbuddy) ? workbuddy : 'python');

function pythonJson(code) {
  return JSON.parse(execFileSync(python, ['-c', code], { encoding: 'utf8' }).trim());
}

function copyEntry(source, destination) {
  if (!fs.existsSync(source)) return;
  fs.cpSync(source, destination, { recursive: true, force: true });
}

function removePythonCaches(root) {
  if (!fs.existsSync(root)) return;
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    const full = path.join(root, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === '__pycache__') fs.rmSync(full, { recursive: true, force: true });
      else removePythonCaches(full);
    } else if (/\.py[co]$/i.test(entry.name)) {
      fs.rmSync(full, { force: true });
    }
  }
}

const info = pythonJson(`
import json, pathlib, site, sys
import playwright, pyee, greenlet, typing_extensions
print(json.dumps({
  "version": sys.version,
  "base": sys.base_prefix,
  "site": site.getsitepackages()[0],
  "packages": {
    "playwright": str(pathlib.Path(playwright.__file__).parent),
    "pyee": str(pathlib.Path(pyee.__file__).parent),
    "greenlet": str(pathlib.Path(greenlet.__file__).parent),
    "typing_extensions": str(pathlib.Path(typing_extensions.__file__))
  }
}))
`);

const targetResolved = path.resolve(targetDir);
if (!targetResolved.endsWith(path.join('station', 'vendor', 'python'))) {
  throw new Error(`拒绝清理非预期目录: ${targetResolved}`);
}
fs.rmSync(targetResolved, { recursive: true, force: true });
fs.mkdirSync(targetResolved, { recursive: true });

for (const name of ['DLLs', 'Lib']) {
  copyEntry(path.join(info.base, name), path.join(targetResolved, name));
}
const runtimeFiles = fs.readdirSync(info.base).filter((name) =>
  /^(python(?:w|3)?\.exe|python3\.dll|python\d+\.dll|vcruntime\d+(?:_\d+)?\.dll|LICENSE\.txt)$/i.test(name)
);
for (const name of runtimeFiles) {
  copyEntry(path.join(info.base, name), path.join(targetResolved, name));
}

// 不携带开发/GUI/测试模块；本项目后端只需要标准库、Playwright CDP 客户端。
for (const name of ['test', 'tkinter', 'idlelib', 'ensurepip', 'venv']) {
  fs.rmSync(path.join(targetResolved, 'Lib', name), { recursive: true, force: true });
}

const sitePackages = path.join(targetResolved, 'Lib', 'site-packages');
fs.rmSync(sitePackages, { recursive: true, force: true });
fs.mkdirSync(sitePackages, { recursive: true });
for (const [name, source] of Object.entries(info.packages)) {
  copyEntry(source, path.join(sitePackages, name === 'typing_extensions' ? 'typing_extensions.py' : name));
}

for (const prefix of ['playwright-', 'pyee-', 'greenlet-', 'typing_extensions-']) {
  const entry = fs.readdirSync(info.site).find((name) => name.startsWith(prefix) && name.endsWith('.dist-info'));
  if (entry) copyEntry(path.join(info.site, entry), path.join(sitePackages, entry));
}

fs.rmSync(path.join(sitePackages, 'greenlet', 'tests'), { recursive: true, force: true });
fs.rmSync(path.join(sitePackages, 'playwright', 'sync_api'), { recursive: true, force: true });
fs.rmSync(path.join(sitePackages, 'playwright', 'driver', 'package', 'types'), { recursive: true, force: true });
fs.rmSync(path.join(sitePackages, 'playwright', 'driver', 'package', 'lib', 'tools'), { recursive: true, force: true });
fs.rmSync(path.join(sitePackages, 'playwright', 'driver', 'package', 'lib', 'vite'), { recursive: true, force: true });
for (const entry of fs.readdirSync(path.join(targetResolved, 'DLLs'))) {
  if (/^_?test/i.test(entry)) fs.rmSync(path.join(targetResolved, 'DLLs', entry), { force: true });
}
removePythonCaches(targetResolved);

execFileSync(path.join(targetResolved, 'python.exe'), [
  '-c',
  [
    'import asyncio, sys',
    'from playwright.async_api import async_playwright',
    'async def verify():',
    '    driver = await async_playwright().start()',
    '    print("playwright_driver=ok")',
    '    await driver.stop()',
    'asyncio.run(verify())',
    'print(sys.version)',
  ].join('\n'),
], { stdio: 'inherit', env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' } });

fs.writeFileSync(path.join(targetResolved, 'RUNTIME-INFO.txt'),
  `Built from ${python}\r\n${info.version}\r\n`, 'utf8');
console.log(`[python-runtime] ready: ${targetResolved}`);
