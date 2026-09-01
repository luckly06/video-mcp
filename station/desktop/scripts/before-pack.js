'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { encodeRuntimeConfig } = require('../lib/runtime-config');

function readEnv(filePath) {
  const result = {};
  if (!fs.existsSync(filePath)) return result;
  for (const raw of fs.readFileSync(filePath, 'utf8').split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith('#') || !line.includes('=')) continue;
    const pos = line.indexOf('=');
    const key = line.slice(0, pos).trim();
    const value = line.slice(pos + 1).trim().replace(/^(['"])(.*)\1$/, '$2');
    if (key) result[key] = value;
  }
  return result;
}

function prepareRuntimeConfig() {
  const envPath = path.resolve(__dirname, '..', '..', 'server', '.env');
  const outputPath = path.resolve(__dirname, '..', 'build', 'runtime-config.bin');
  const env = readEnv(envPath);
  if (!env.MIMO_API_KEY) {
    throw new Error(`MIMO_API_KEY missing in ${envPath}`);
  }
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  fs.writeFileSync(outputPath, encodeRuntimeConfig({ MIMO_API_KEY: env.MIMO_API_KEY }));
  console.log('[before-pack] runtime config prepared (MIMO_API_KEY hidden)');
}

// 离线 ASR 模型（paraformer-zh-small，约 78 MiB）随桌面发行包携带。
// Windows / macOS 发布运行时都已内置 sherpa_onnx，保留模型才能开箱即用离线转写。
const ASR_RESOURCE_TO = 'station/vendor/asr_models';

function platformOf(context) {
  const fromContext = context && context.electronPlatformName;
  if (fromContext) return String(fromContext);
  return process.platform;
}

function pruneAsrModels(context) {
  const platform = platformOf(context);
  console.log(`[before-pack] keep ASR models (${platform})`);
  return false;
}

module.exports = async function beforePack(context) {
  prepareRuntimeConfig();
  pruneAsrModels(context);
};

module.exports.readEnv = readEnv;
module.exports.prepareRuntimeConfig = prepareRuntimeConfig;
module.exports.pruneAsrModels = pruneAsrModels;
module.exports.ASR_RESOURCE_TO = ASR_RESOURCE_TO;

if (require.main === module) {
  prepareRuntimeConfig();
}
