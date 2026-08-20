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

module.exports = async function beforePack() {
  prepareRuntimeConfig();
};

module.exports.readEnv = readEnv;
module.exports.prepareRuntimeConfig = prepareRuntimeConfig;

if (require.main === module) {
  prepareRuntimeConfig();
}
