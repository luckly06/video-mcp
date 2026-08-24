// 发布态运行配置：构建时加密、运行时仅在主进程内解密。
// 这能避免把 API Key 作为明文 .env 交付，但纯本地应用仍无法做到绝对防提取。
'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');

const FORMAT_VERSION = 1;
const PRODUCT_CONTEXT = 'video-dedup-desktop/runtime-config/v1';

function deriveKey(salt) {
  return crypto.scryptSync(PRODUCT_CONTEXT, salt, 32);
}

function encodeRuntimeConfig(config) {
  const salt = crypto.randomBytes(16);
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv('aes-256-gcm', deriveKey(salt), iv);
  const plaintext = Buffer.from(JSON.stringify(config), 'utf8');
  const ciphertext = Buffer.concat([cipher.update(plaintext), cipher.final()]);
  return Buffer.from(JSON.stringify({
    version: FORMAT_VERSION,
    salt: salt.toString('base64'),
    iv: iv.toString('base64'),
    tag: cipher.getAuthTag().toString('base64'),
    data: ciphertext.toString('base64'),
  }), 'utf8');
}

function decodeRuntimeConfig(buffer) {
  const payload = JSON.parse(Buffer.from(buffer).toString('utf8'));
  if (payload.version !== FORMAT_VERSION) throw new Error('unsupported runtime config');
  const salt = Buffer.from(payload.salt, 'base64');
  const decipher = crypto.createDecipheriv(
    'aes-256-gcm', deriveKey(salt), Buffer.from(payload.iv, 'base64')
  );
  decipher.setAuthTag(Buffer.from(payload.tag, 'base64'));
  const plaintext = Buffer.concat([
    decipher.update(Buffer.from(payload.data, 'base64')),
    decipher.final(),
  ]);
  return JSON.parse(plaintext.toString('utf8'));
}

function loadRuntimeConfig(filePath) {
  if (!filePath || !fs.existsSync(filePath)) return {};
  return decodeRuntimeConfig(fs.readFileSync(filePath));
}

module.exports = { encodeRuntimeConfig, decodeRuntimeConfig, loadRuntimeConfig };
