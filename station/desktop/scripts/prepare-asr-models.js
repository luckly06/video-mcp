// 复制离线 ASR 模型到 station/vendor/asr_models，供桌面发行包随包携带。
'use strict';

const fs = require('node:fs');
const path = require('node:path');

const stationDir = path.resolve(__dirname, '..', '..');
const targetRoot = path.join(stationDir, 'vendor', 'asr_models');
const sourceRoot = process.env.VU_ASR_SOURCE || 'F:\\Download\\A-models\\sherpa-onnx';
const modelName = process.env.VU_ASR_MODEL || 'sherpa-onnx-paraformer-zh-small-2024-03-09';
const source = path.join(sourceRoot, modelName);
const target = path.join(targetRoot, modelName);

if (!fs.existsSync(source)) {
  throw new Error(`ASR 模型不存在: ${source}`);
}

fs.rmSync(targetRoot, { recursive: true, force: true });
fs.mkdirSync(targetRoot, { recursive: true });
fs.cpSync(source, target, {
  recursive: true,
  force: true,
  filter: (p) => !/[\\/]test_wavs(?:[\\/]|$)/i.test(p),
});

const bytes = [...walk(target)].reduce((sum, file) => sum + fs.statSync(file).size, 0);
console.log(`[asr-models] ready: ${target}`);
console.log(`[asr-models] size: ${(bytes / 1024 / 1024).toFixed(2)} MiB`);

function* walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) yield* walk(full);
    else if (entry.isFile()) yield full;
  }
}
