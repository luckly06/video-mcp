// scripts/prepare-ffmpeg.js — 把 ffmpeg-static / ffprobe-static 提供的当前平台静态
// 二进制复制到 ../vendor/ffmpeg/，供 electron-builder 随包分发。
//
// 为什么独立成脚本而不是写在 workflow 里：
//   1) ffmpeg-static 5.x 在 Node 22 下 require() 可能返回 { default: '...' } 之类的
//      对象结构（不同 Node/包版本行为不一），直接 process.stdout.write(obj) 会抛
//      ERR_INVALID_ARG_TYPE。这里统一做容错解析。
//   2) 以后修 ffmpeg 逻辑只改这个 .js（普通文件，push 不需要 workflow scope）。
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');

/** 解析包导出，兼容 string / {default} / {path} 三种形态；取不到返回 ''。 */
function resolveBinaryPath(modName) {
  let r;
  try {
    r = require(modName);
  } catch (e) {
    console.error(`[prepare-ffmpeg] require(${modName}) 失败: ${e.message}`);
    return '';
  }
  if (typeof r === 'string' && r) return r;
  if (r && typeof r === 'object') {
    for (const k of ['default', 'path', 'ffmpeg', 'ffprobe']) {
      if (typeof r[k] === 'string' && r[k]) return r[k];
    }
  }
  return '';
}

const vendorDir = path.resolve(__dirname, '..', '..', 'vendor', 'ffmpeg');
fs.mkdirSync(vendorDir, { recursive: true });

const isWin = process.platform === 'win32';
const entries = [
  ['ffmpeg-static', isWin ? 'ffmpeg.exe' : 'ffmpeg'],
  ['ffprobe-static', isWin ? 'ffprobe.exe' : 'ffprobe'],
];

for (const [modName, binName] of entries) {
  const src = resolveBinaryPath(modName);
  if (!src || !fs.existsSync(src)) {
    console.error(`[prepare-ffmpeg] ${modName} 未提供当前平台二进制（ARCH=${process.arch}）`);
    process.exit(1);
  }
  const dst = path.join(vendorDir, binName);
  fs.copyFileSync(src, dst);
  fs.chmodSync(dst, 0o755);
  console.log(`[prepare-ffmpeg] ${binName} <- ${src}`);
}

try {
  const out = execFileSync(path.join(vendorDir, isWin ? 'ffmpeg.exe' : 'ffmpeg'), ['-version'], {
    encoding: 'utf8',
  });
  console.log('[prepare-ffmpeg]', out.split('\n')[0]);
} catch (e) {
  console.error('[prepare-ffmpeg] ffmpeg 无法执行:', e.message);
  process.exit(1);
}
