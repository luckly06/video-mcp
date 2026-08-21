'use strict';

const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const test = require('node:test');

const { launchServerOnPort, startLocalServer } = require('../local-server');

test('后端子进程提前退出时立即返回失败，不等待完整超时', async () => {
  const child = new EventEmitter();
  child.stdout = new EventEmitter();
  child.stderr = new EventEmitter();
  child.kill = () => {};
  const startedAt = Date.now();

  const result = await launchServerOnPort({
    py: 'python',
    serverPy: __filename,
    baseUrl: 'http://127.0.0.1:8765',
    env: {},
    probeFn: async () => false,
    spawnFn: () => {
      setImmediate(() => child.emit('exit', 3, null));
      return child;
    },
    readyTimeoutMs: 5000,
    pollIntervalMs: 10,
  });

  assert.equal(result.ready, false);
  assert.match(result.error, /exit code=3/);
  assert.ok(Date.now() - startedAt < 1000);
});

test('打包态端口启动失败后继续尝试下一候选端口', async () => {
  const launches = [];
  const expectedChild = { kill() {} };
  const result = await startLocalServer({
    preferFresh: true,
    candidatePorts: [8765, 8766],
    probeFn: async () => false,
    launchFn: async ({ baseUrl }) => {
      launches.push(baseUrl);
      if (baseUrl.endsWith(':8765')) {
        return { ready: false, child: null, error: 'exit code=3 sig=null' };
      }
      return { ready: true, child: expectedChild, error: null };
    },
  });

  assert.deepEqual(launches, [
    'http://127.0.0.1:8765',
    'http://127.0.0.1:8766',
  ]);
  assert.equal(result.baseUrl, 'http://127.0.0.1:8766');
  assert.equal(result.child, expectedChild);
  assert.equal(result.reused, false);
});

test('启动 Python 后端时禁止在发布目录生成 pyc 缓存', async () => {
  let capturedEnv = null;
  const result = await startLocalServer({
    candidatePorts: [8765],
    probeFn: async () => false,
    launchFn: async ({ env }) => {
      capturedEnv = env;
      return { ready: true, child: { kill() {} }, error: null };
    },
  });

  assert.equal(result.reused, false);
  assert.equal(capturedEnv.PYTHONDONTWRITEBYTECODE, '1');
});
