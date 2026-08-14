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

const SERVER_HOST = '127.0.0.1';
const SERVER_PORT = 8765;
const PACKAGED_FRESH_PORT_TRIES = 10;

// station/server/mcp_server.py（开发态走仓库；打包态走 process.resourcesPath）
const SERVER_PY = resolveServerScriptPath('mcp_server.py');

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
 * @param {boolean} [opts.preferFresh] 是否避开已存在端口服务，强制拉起新后端
 * @returns {Promise<{baseUrl:string, child:object|null, reused:boolean, error?:string}>}
 */
async function startLocalServer({ log, assetsDir, preferFresh = false } = {}) {
  const ports = preferFresh
    ? Array.from({ length: PACKAGED_FRESH_PORT_TRIES }, (_, i) => SERVER_PORT + i)
    : [SERVER_PORT];
  let firstReusableBaseUrl = null;

  for (const port of ports) {
    const baseUrl = baseUrlFor(port);
    const alreadyUp = await probe(baseUrl);
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
    const asr = resolveAsrModels(env);
    if (asr) env.VU_ASR_MODELS = asr;
    try {
      resolveAssetsDir(env, assetsDir, log);
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

    return new Promise((resolve) => {
      let settled = false;
      const child = spawn(py, [SERVER_PY], {
        cwd: path.dirname(SERVER_PY),
        env,
        windowsHide: true,
        stdio: ['ignore', 'pipe', 'pipe'],
      });

      child.on('error', (e) => {
        if (settled) return;
        settled = true;
        log?.error?.('[local-server] spawn 失败:', e.message);
        resolve({ baseUrl, child: null, error: e.message });
      });
      child.stdout?.on('data', (d) => log?.info?.('[mcp]', String(d).trimEnd()));
      child.stderr?.on('data', (d) => log?.info?.('[mcp]', String(d).trimEnd()));
      child.on('exit', (code, sig) =>
        log?.info?.(`[local-server] mcp 退出 code=${code} sig=${sig}`));

      // 轮询就绪（最多 30s；ASR 模型惰性加载，不影响 server 起端口）
      const deadline = Date.now() + 30000;
      (function poll() {
        if (settled) return;
        probe(baseUrl).then((ready) => {
          if (settled) return;
          if (ready) {
            settled = true;
            log?.info?.(`[local-server] MCP 就绪: ${baseUrl}`);
            resolve({ baseUrl, child, reused: false });
          } else if (Date.now() > deadline) {
            settled = true;
            log?.error?.('[local-server] 30s 内未就绪');
            resolve({ baseUrl, child, error: 'timeout' });
          } else {
            setTimeout(poll, 400);
          }
        });
      })();
    });
  }

  if (firstReusableBaseUrl) {
    log?.info?.(`[local-server] 未找到空闲备用端口，回退复用现有 MCP: ${firstReusableBaseUrl}`);
    return { baseUrl: firstReusableBaseUrl, child: null, reused: true };
  }

  const baseUrl = baseUrlFor(SERVER_PORT);
  const error = `no free port in ${SERVER_PORT}-${SERVER_PORT + PACKAGED_FRESH_PORT_TRIES - 1}`;
  log?.error?.(`[local-server] ${error}`);
  return { baseUrl, child: null, error };
}

/** 停止本地后端子进程。 */
function stopLocalServer(child) {
  if (child && !child.killed) {
    try { child.kill(); } catch (_) { /* noop */ }
  }
}

module.exports = { startLocalServer, stopLocalServer, SERVER_HOST, SERVER_PORT };
