// lib/local-server.js — 本地 MCP 后端拉起
//
// 桌面端不依赖远端：启动时 spawn 本机 Python venv 的 station/server/mcp_server.py，
// 监听 127.0.0.1:8765。若该端口已有服务在跑（用户手动起过 / 复用旧实例），直接复用，
// 不重复 spawn。退出时由 main.js 调 stopLocalServer() 杀子进程。
//
// 后端能力（去重 / 探测 / ASR 提取文案）依赖：
//   - Python venv（优先 %USERPROFILE%\.workbuddy\binaries\python\envs\default）
//   - ffmpeg/ffprobe（station/vendor/ffmpeg，pipeline.py 相对锚定，无需本模块处理）
//   - ASR 模型（sherpa-onnx，自动探测常见路径；找不到则 ASR 降级，去重仍可用）
'use strict';

const { spawn } = require('node:child_process');
const http = require('node:http');
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
const { resolveServerScriptPath } = require('./paths');
const { loadRuntimeConfig } = require('./runtime-config');

const SERVER_HOST = '127.0.0.1';
const SERVER_PORT = 8765;
const PACKAGED_FRESH_PORT_TRIES = 10;

// station/server/mcp_server.py（开发态走仓库；打包态走 process.resourcesPath）
const SERVER_PY = resolveServerScriptPath('mcp_server.py');
const RUNTIME_CONFIG = path.join(__dirname, '..', 'build', 'runtime-config.bin');

function injectRuntimeConfig(env, log) {
  if (env.MIMO_API_KEY) return;
  try {
    const config = loadRuntimeConfig(RUNTIME_CONFIG);
    if (config.MIMO_API_KEY) {
      env.MIMO_API_KEY = config.MIMO_API_KEY;
      log?.info?.('[local-server] MiMo TTS 运行配置已加载');
    }
  } catch (e) {
    log?.error?.(`[local-server] TTS 运行配置读取失败: ${e.message}`);
  }
}

/**
 * 解析本机 Python 解释器（顺序即优先级）：
 *   1) WorkBuddy 托管 venv（start_server.bat 同款）
 *   2) 系统 PATH 中的 python / python3
 */
function resolvePython() {
  const venv = path.join(
    os.homedir(), '.workbuddy', 'binaries', 'python', 'envs', 'default', 'Scripts', 'python.exe'
  );
  if (fs.existsSync(venv)) return venv;
  return process.platform === 'win32' ? 'python' : 'python3';
}

/**
 * 探测 ASR 模型目录（sherpa-onnx）：
 *   1) 环境变量 VU_ASR_MODELS（用户显式指定）
 *   2) 常见下载目录（F/E/D/C:\Download\A-models\sherpa-onnx）
 * 找不到返回 null —— 不设环境变量，后端 ASR 自动降级为空文案（去重/探测不受影响）。
 */
function resolveAsrModels(env) {
  if (env.VU_ASR_MODELS && fs.existsSync(env.VU_ASR_MODELS)) return env.VU_ASR_MODELS;
  const drives = process.platform === 'win32' ? ['F:', 'E:', 'D:', 'C:'] : [];
  for (const d of drives) {
    const p = path.join(d, 'Download', 'A-models', 'sherpa-onnx');
    if (fs.existsSync(p)) return p;
  }
  return null;
}

function baseUrlFor(port) {
  return `http://${SERVER_HOST}:${port}`;
}

function resolveAssetsDir(env, assetsDir, log) {
  const configured = (env.VU_ASSETS || assetsDir || '').trim();
  if (!configured) return null;

  const resolved = path.resolve(configured);
  fs.mkdirSync(resolved, { recursive: true });
  env.VU_ASSETS = resolved;
  log?.info?.(`[local-server] VU_ASSETS=${resolved}`);
  return resolved;
}

function resolveYuanbaoProfileDir(env, yuanbaoProfileDir, log) {
  const configured = (env.VU_YUANBAO_DEBUG_PROFILE || yuanbaoProfileDir || '').trim();
  if (!configured) return null;

  const resolved = path.resolve(configured);
  fs.mkdirSync(resolved, { recursive: true });
  env.VU_YUANBAO_DEBUG_PROFILE = resolved;
  log?.info?.(`[local-server] VU_YUANBAO_DEBUG_PROFILE=${resolved}`);
  return resolved;
}

/** 探测后端是否已就绪（POST /mcp server/discover 返回 200）。 */
function probe(baseUrl) {
  return new Promise((resolve) => {
    const req = http.request(`${baseUrl}/mcp`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'MCP-Protocol-Version': '2026-07-28',
        'Mcp-Method': 'server/discover',
      },
      timeout: 2000,
    }, (res) => {
      res.resume();
      resolve(res.statusCode === 200);
    });
    req.on('error', () => resolve(false));
    req.on('timeout', () => { req.destroy(); resolve(false); });
    req.end(JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'server/discover', params: {} }));
  });
}

/**
 * 启动本地 MCP 后端。
 * @param {object} [opts]
 * @param {object} [opts.log] logger 实例（可选）
 * @param {string} [opts.assetsDir] 打包态用户可写素材目录
 * @param {string} [opts.yuanbaoProfileDir] 元宝调试 Edge 持久登录态目录
 * @param {boolean} [opts.preferFresh] 是否避开已存在端口服务，强制拉起新后端
 * @returns {Promise<{baseUrl:string, child:object|null, reused:boolean, error?:string}>}
 */
function launchServerOnPort({
  py,
  serverPy = SERVER_PY,
  baseUrl,
  env,
  log,
  probeFn = probe,
  spawnFn = spawn,
  readyTimeoutMs = 30000,
  pollIntervalMs = 400,
}) {
  return new Promise((resolve) => {
    let settled = false;
    let pollTimer = null;
    let child = null;

    const finish = (result) => {
      if (settled) return;
      settled = true;
      if (pollTimer) clearTimeout(pollTimer);
      resolve(result);
    };

    try {
      child = spawnFn(py, [serverPy], {
        cwd: path.dirname(serverPy),
        env,
        windowsHide: true,
        stdio: ['ignore', 'pipe', 'pipe'],
      });
    } catch (e) {
      const error = e && e.message ? e.message : String(e);
      log?.error?.('[local-server] spawn 失败:', error);
      finish({ ready: false, child: null, error });
      return;
    }

    child.on('error', (e) => {
      const error = e && e.message ? e.message : String(e);
      log?.error?.('[local-server] spawn 失败:', error);
      finish({ ready: false, child: null, error });
    });
    child.stdout?.on('data', (d) => log?.info?.('[mcp]', String(d).trimEnd()));
    child.stderr?.on('data', (d) => log?.info?.('[mcp]', String(d).trimEnd()));
    child.on('exit', (code, sig) => {
      log?.info?.(`[local-server] mcp 退出 code=${code} sig=${sig}`);
      if (!settled) {
        finish({ ready: false, child: null, error: `exit code=${code} sig=${sig}` });
      }
    });

    const deadline = Date.now() + readyTimeoutMs;
    (function poll() {
      if (settled) return;
      Promise.resolve(probeFn(baseUrl)).catch(() => false).then((ready) => {
        if (settled) return;
        if (ready) {
          log?.info?.(`[local-server] MCP 就绪: ${baseUrl}`);
          finish({ ready: true, child, error: null });
        } else if (Date.now() > deadline) {
          try { child.kill(); } catch (_) { /* noop */ }
          finish({ ready: false, child: null, error: 'timeout' });
        } else {
          pollTimer = setTimeout(poll, pollIntervalMs);
        }
      });
    })();
  });
}

async function startLocalServer({
  log,
  assetsDir,
  yuanbaoProfileDir,
  preferFresh = false,
  candidatePorts,
  probeFn = probe,
  launchFn = launchServerOnPort,
} = {}) {
  const ports = candidatePorts || (preferFresh
    ? Array.from({ length: PACKAGED_FRESH_PORT_TRIES }, (_, i) => SERVER_PORT + i)
    : [SERVER_PORT]);
  let firstReusableBaseUrl = null;

  for (const port of ports) {
    const baseUrl = baseUrlFor(port);
    const alreadyUp = await probeFn(baseUrl);
    if (alreadyUp) {
      if (preferFresh) {
        firstReusableBaseUrl = firstReusableBaseUrl || baseUrl;
        log?.info?.(`[local-server] 端口已有 MCP，打包态跳过旧服务: ${baseUrl}`);
        continue;
      }
      log?.info?.(`[local-server] 端口已就绪，复用现有 MCP: ${baseUrl}`);
      return { baseUrl, child: null, reused: true };
    }

    const py = resolvePython();
    const env = { ...process.env, VU_HOST: SERVER_HOST, VU_PORT: String(port) };
    injectRuntimeConfig(env, log);
    const asr = resolveAsrModels(env);
    if (asr) env.VU_ASR_MODELS = asr;
    try {
      resolveAssetsDir(env, assetsDir, log);
      resolveYuanbaoProfileDir(env, yuanbaoProfileDir, log);
    } catch (e) {
      const error = `assets dir unavailable: ${e && e.message ? e.message : String(e)}`;
      log?.error?.(`[local-server] ${error}`);
      return { baseUrl, child: null, error };
    }

    if (!fs.existsSync(SERVER_PY)) {
      const error = `server script not found: ${SERVER_PY}`;
      log?.error?.(`[local-server] ${error}`);
      return { baseUrl, child: null, error };
    }

    log?.info?.(`[local-server] 拉起本地后端: ${py} ${SERVER_PY}`);
    if (asr) log?.info?.(`[local-server] VU_ASR_MODELS=${asr}`);
    else log?.info?.('[local-server] 未找到 ASR 模型目录，ASR 将降级（去重/探测不受影响）');

    const launched = await launchFn({ py, serverPy: SERVER_PY, baseUrl, env, log, probeFn });
    if (launched.ready) {
      return { baseUrl, child: launched.child, reused: false };
    }
    log?.warn?.(`[local-server] ${baseUrl} 启动失败（${launched.error || 'unknown'}），尝试下一端口`);
  }

  if (firstReusableBaseUrl) {
    log?.info?.(`[local-server] 未找到空闲备用端口，回退复用现有 MCP: ${firstReusableBaseUrl}`);
    return { baseUrl: firstReusableBaseUrl, child: null, reused: true };
  }

  const baseUrl = baseUrlFor(SERVER_PORT);
  const error = `no usable port in ${ports.join(',')}`;
  log?.error?.(`[local-server] ${error}`);
  return { baseUrl, child: null, error };
}
/** 停止本地后端子进程。 */
function stopLocalServer(child) {
  if (child && !child.killed) {
    try { child.kill(); } catch (_) { /* noop */ }
  }
}

module.exports = { startLocalServer, stopLocalServer, launchServerOnPort, SERVER_HOST, SERVER_PORT };
