'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const { encodeRuntimeConfig, decodeRuntimeConfig } = require('../runtime-config');

test('runtime config encrypts and restores MIMO_API_KEY', () => {
  const key = 'sk-test-not-a-real-key';
  const encoded = encodeRuntimeConfig({ MIMO_API_KEY: key });
  assert.equal(encoded.includes(Buffer.from(key)), false);
  assert.deepEqual(decodeRuntimeConfig(encoded), { MIMO_API_KEY: key });
});

test('runtime config rejects tampered payload', () => {
  const encoded = encodeRuntimeConfig({ MIMO_API_KEY: 'sk-test' });
  const payload = JSON.parse(encoded.toString('utf8'));
  payload.data = Buffer.from('tampered').toString('base64');
  assert.throws(() => decodeRuntimeConfig(Buffer.from(JSON.stringify(payload))));
});
