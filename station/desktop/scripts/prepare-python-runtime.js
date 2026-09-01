// 从当前构建机的 Python 环境生成随包精简运行时。
// 产物位于 station/vendor/python（已被 .gitignore 排除），electron-builder 会随包复制。
//
// 跨平台：本机 Windows 与 GitHub Actions macOS runner 均可执行。
//   - win32 ：复制 python.exe + DLLs + Lib（保持原逻辑）
//   - darwin：复制 bin/python3 + lib（dylib + stdlib + site-packages），
//             二进制内 dylib 引用依赖 @loader_path 相对路径，复制后仍有效；
//             脚本末尾会实跑 playwright driver 验证解释器可用。
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');

const stationDir = path.resolve(__dirname, '..', '..');
const targetDir = path.join(stationDir, 'vendor', 'python');
const configured = String(process.env.VU_BUILD_PYTHON || '').trim();
const isWin = process.platform === 'win32';
const isMac = process.platform === 'darwin';

// 构建机解释器解析：
//   1) VU_BUILD_PYTHON（CI 显式指定，最稳）
//   2) WorkBuddy 托管 venv（仅 Windows 本机存在）
//   3) PATH 中的 python / python3
let python = configured;
if (!python) {
  const workbuddy = path.join(
    process.env.USERPROFILE || '', '.workbuddy', 'binaries', 'python', 'envs', 'default', 'Scripts', 'python.exe'
  );
  if (isWin && fs.existsSync(workbuddy)) python = workbuddy;
  else python = isWin ? 'python' : 'python3';
}

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
import json, pathlib, site, sys, importlib.util
import playwright, pyee, greenlet, typing_extensions
pkgs = {
    "playwright": str(pathlib.Path(playwright.__file__).parent),
    "pyee": str(pathlib.Path(pyee.__file__).parent),
    "greenlet": str(pathlib.Path(greenlet.__file__).parent),
    "typing_extensions": str(pathlib.Path(typing_extensions.__file__))
}
# 离线 ASR：sherpa-onnx + numpy + soundfile/cffi。
# sherpa_onnx_core 必须一起带上：sherpa-onnx 1.13+ 把 onnxruntime 推理引擎并进了这个包，
# 只复制 sherpa_onnx 会导致 import 时找不到核心库。（onnxruntime 1.13+ 不再是独立 pip 包）
for mod in ("sherpa_onnx", "sherpa_onnx_core", "numpy", "soundfile", "cffi", "pycparser", "_soundfile"):
    spec = importlib.util.find_spec(mod)
    if spec and spec.origin:
        p = pathlib.Path(spec.origin)
        pkgs[mod] = str(p if p.is_file() and mod in ("soundfile", "_soundfile") else p.parent)
print(json.dumps({
  "version": sys.version,
  "base": sys.base_prefix,
  "site": next((p for p in site.getsitepackages() + [site.getusersitepackages()] if p.endswith("site-packages")), site.getsitepackages()[-1]),
  "packages": pkgs
}))
`);

const targetResolved = path.resolve(targetDir);
if (!targetResolved.endsWith(path.join('station', 'vendor', 'python'))) {
  throw new Error(`拒绝清理非预期目录: ${targetResolved}`);
}
fs.rmSync(targetResolved, { recursive: true, force: true });
fs.mkdirSync(targetResolved, { recursive: true });

function packageDestination(sitePackages, name, source) {
  const base = path.basename(source);
  if (name === 'typing_extensions') return path.join(sitePackages, 'typing_extensions.py');
  if (name === 'soundfile') return path.join(sitePackages, 'soundfile.py');
  if (name === '_soundfile') return path.join(sitePackages, base);
  return path.join(sitePackages, name);
}

function copyRuntimePackages(sitePackages) {
  fs.rmSync(sitePackages, { recursive: true, force: true });
  fs.mkdirSync(sitePackages, { recursive: true });
  for (const [name, source] of Object.entries(info.packages)) {
    copyEntry(source, packageDestination(sitePackages, name, source));
  }
  for (const entry of fs.readdirSync(info.site)) {
    // 注意 sherpa_onnx_core 必须单独列出：^sherpa_onnx- 匹配不到它（后面跟的是 _core- 而非 -）
    if (/^(playwright|pyee|greenlet|typing_extensions|sherpa_onnx_core|sherpa_onnx|onnxruntime|soundfile|numpy|cffi|pycparser)-.*\.dist-info$/i.test(entry)) {
      copyEntry(path.join(info.site, entry), path.join(sitePackages, entry));
    }
    if (/^(numpy\.libs|_soundfile_data)$/i.test(entry)) {
      copyEntry(path.join(info.site, entry), path.join(sitePackages, entry));
    }
    if (/^_cffi_backend.*\.pyd$/i.test(entry)) {
      copyEntry(path.join(info.site, entry), path.join(sitePackages, entry));
    }
  }
}

if (isWin) {
  // ---------- Windows：python.exe + DLLs + Lib ----------
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
  copyRuntimePackages(sitePackages);
  for (const entry of fs.readdirSync(path.join(targetResolved, 'DLLs'))) {
    if (/^_?test/i.test(entry)) fs.rmSync(path.join(targetResolved, 'DLLs', entry), { force: true });
  }
} else if (isMac) {
  // ---------- macOS：bin/python3 + lib（dylib + stdlib） ----------
  const baseBin = path.join(info.base, 'bin');
  const baseLib = path.join(info.base, 'lib');
  fs.mkdirSync(path.join(targetResolved, 'bin'), { recursive: true });
  fs.mkdirSync(path.join(targetResolved, 'lib'), { recursive: true });

  // 解释器可执行 + 版本软链（python3 -> python3.x）
  const binEntries = fs.existsSync(baseBin)
    ? fs.readdirSync(baseBin).filter((name) => /^python3(\.\d+)?$/.test(name))
    : [];
  if (binEntries.length === 0) {
    throw new Error(`构建机 ${info.base}/bin 下未找到 python3 可执行文件`);
  }
  for (const name of binEntries) {
    const src = path.join(baseBin, name);
    const stat = fs.lstatSync(src);
    if (stat.isSymbolicLink()) {
      // 保留符号链接语义（python3 -> python3.13），复制链接而非内容
      const link = fs.readlinkSync(src);
      fs.symlinkSync(link, path.join(targetResolved, 'bin', name));
    } else {
      copyEntry(src, path.join(targetResolved, 'bin', name));
    }
  }

  // dylib（libpython3.13.dylib 等）+ stdlib 目录
  const libEntries = fs.existsSync(baseLib) ? fs.readdirSync(baseLib) : [];
  const stdlibDir = libEntries.find((name) => /^python3(\.\d+)?$/.test(name));
  if (!stdlibDir) {
    throw new Error(`构建机 ${info.base}/lib 下未找到 python3.x stdlib 目录`);
  }
  for (const name of libEntries) {
    if (name === stdlibDir) continue; // stdlib 稍后单独处理（需要先清 site-packages）
    if (/^libpython3(\.\d+)?\.dylib$/.test(name)) {
      copyEntry(path.join(baseLib, name), path.join(targetResolved, 'lib', name));
    }
  }
  copyEntry(path.join(baseLib, stdlibDir), path.join(targetResolved, 'lib', stdlibDir));

  // 不携带开发/GUI/测试模块
  for (const name of ['test', 'tkinter', 'idlelib', 'ensurepip', 'venv', 'turtledemo']) {
    fs.rmSync(path.join(targetResolved, 'lib', stdlibDir, name), { recursive: true, force: true });
  }

  // site-packages 只保留 Playwright 全家
  const sitePackages = path.join(targetResolved, 'lib', stdlibDir, 'site-packages');
  copyRuntimePackages(sitePackages);
} else {
  throw new Error(`暂不支持在 ${process.platform} 上构建 Python 运行时`);
}

// 平台通用的 playwright 精简（减小体积）
const sitePackagesShared = isWin
  ? path.join(targetResolved, 'Lib', 'site-packages')
  : path.join(targetResolved, 'lib', fs.readdirSync(path.join(targetResolved, 'lib')).find((n) => /^python3(\.\d+)?$/.test(n)), 'site-packages');
fs.rmSync(path.join(sitePackagesShared, 'greenlet', 'tests'), { recursive: true, force: true });
fs.rmSync(path.join(sitePackagesShared, 'playwright', 'sync_api'), { recursive: true, force: true });
fs.rmSync(path.join(sitePackagesShared, 'playwright', 'driver', 'package', 'types'), { recursive: true, force: true });
fs.rmSync(path.join(sitePackagesShared, 'playwright', 'driver', 'package', 'lib', 'tools'), { recursive: true, force: true });
fs.rmSync(path.join(sitePackagesShared, 'playwright', 'driver', 'package', 'lib', 'vite'), { recursive: true, force: true });
removePythonCaches(targetResolved);

// 用生成的运行时实跑 playwright driver 验证（mac 上 dylib 相对引用是否成立也由这一步兜底）
const verifyPy = isWin
  ? path.join(targetResolved, 'python.exe')
  : path.join(targetResolved, 'bin', 'python3');
execFileSync(verifyPy, [
  '-c',
  [
    'import asyncio, sys',
    'from playwright.async_api import async_playwright',
    'import sherpa_onnx, numpy, soundfile',
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
