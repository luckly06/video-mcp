// background.js — 浏览器扩展后台 Service Worker
// 职责：作为扩展 popup 和 MCP 服务器之间的网关
'use strict';

const API_BASE = 'http://124.71.209.36:8765';
const YUANBAO_URL = 'https://yuanbao.tencent.com/';
let rewriteCallbacks = new Map();

// ====== 通用 MCP 调用 ======
async function callMcp(toolName, args) {
  const url = API_BASE + '/mcp';
  const body = JSON.stringify({
    method: 'tools/call',
    params: { name: toolName, arguments: args },
  });
  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body,
  });
  if (!resp.ok) throw new Error('MCP HTTP ' + resp.status);
  const json = await resp.json();
  // 处理标准 JSON-RPC 错误（error 字段）
  if (json.error) {
    throw new Error(json.error.message || JSON.stringify(json.error));
  }
  const result = json.result || {};
  if (result.isError) {
    const errText = (result.content && result.content[0] && result.content[0].text) || JSON.stringify(result);
    throw new Error(errText);
  }
  const content = (result.content && result.content[0] && result.content[0].text);
  if (!content) {
    console.error('[callMcp] no content in result:', JSON.stringify(json).slice(0, 500));
    throw new Error('MCP empty response (server returned no content)');
  }
  // 服务端的返回结果装在 content text 里，是 JSON 字符串
  try { return JSON.parse(content); }
  catch { return { text: content }; }
}

// ====== Base64 上传 ======
async function uploadFromBase64(name, b64) {
  const byteChars = atob(b64);
  const byteArr = new Uint8Array(byteChars.length);
  for (let i = 0; i < byteChars.length; i++) byteArr[i] = byteChars.charCodeAt(i);
  const blob = new Blob([byteArr], { type: 'video/mp4' });
  const formData = new FormData();
  formData.append('file', blob, name);
  const resp = await fetch(API_BASE + '/local/upload', { method: 'POST', body: formData });
  if (!resp.ok) throw new Error('upload HTTP ' + resp.status);
  const text = await resp.text();
  try { return JSON.parse(text); } catch { return { raw: text }; }
}

// ====== 列表 / 探测 ======
async function listVideos() {
  return await callMcp('list_assets');
}
async function probeVideo(name) {
  return await callMcp('probe_video', { src: name });
}

// ====== 元宝改写流程 ======
async function doRewriteFlow(src, template, topic) {
  const ctx = await callMcp('extract_copy_context', { src });
  if (!ctx || (!ctx.raw_text && !(ctx.frames_b64 && ctx.frames_b64.length))) {
    return { error: '无法提取视频信息（无字幕且无音频）' };
  }

  const ybTabs = await chrome.tabs.query({ url: YUANBAO_URL + '*' });
  let ybTab;
  if (ybTabs.length > 0) {
    ybTab = ybTabs[0];
    await chrome.tabs.update(ybTab.id, { active: true });
  } else {
    ybTab = await chrome.tabs.create({ url: YUANBAO_URL, active: true });
  }
  await waitForTabLoad(ybTab.id);

  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      rewriteCallbacks.delete(ybTab.id);
      reject(new Error('rewrite timeout (180s)'));
    }, 180000);
    rewriteCallbacks.set(ybTab.id, {
      resolve: (data) => {
        const result = {
          rewritten: data.rewritten, original: ctx.raw_text, source: ctx.source,
          duration: ctx.duration, max_chars: ctx.max_chars, error: data.error,
        };
        // 关键：popup 关掉也照存，Service Worker 不受 popup 关闭影响
        chrome.storage.local.set({ rewriteResult: result });
        // 扩展图标角标提示用户「改写完成，点开查看」
        chrome.action.setBadgeText({ text: 'OK' });
        chrome.action.setBadgeBackgroundColor({ color: '#10b981' });
        resolve(result);
      },
      reject, timer,
    });

    // 直接用 executeScript 注入改写代码 + 数据，不依赖 content_scripts
    const injectCode = `(function(){window.__ybInject(${JSON.stringify({
      frames_b64: ctx.frames_b64 || [],
      raw_text: ctx.raw_text || '',
      template: template || '',
      topic: topic || '',
      max_chars: ctx.max_chars || 30,
    })});})();`;

    chrome.scripting.executeScript({
      target: { tabId: ybTab.id },
      files: ['content-yuanbao.js'],
    }).then(() => {
      // content-yuanbao.js 加载后，调用其导出的 __ybInject
      return chrome.scripting.executeScript({
        target: { tabId: ybTab.id },
        func: (data) => { if (window.__ybInject) window.__ybInject(data); },
        args: [{
          frames_b64: ctx.frames_b64 || [],
          raw_text: ctx.raw_text || '',
          template: template || '',
          topic: topic || '',
          max_chars: ctx.max_chars || 30,
        }],
      });
    }).catch(reject);
  });
}

// ====== 消息路由 ======
chrome.runtime.onMessage.addListener(function(msg, sender, sendResponse) {
  handle(msg, sender).then(sendResponse).catch(function(e) { sendResponse({ error: e.message }); });
  return true;
});

async function handle(msg, sender) {
  // 元宝 content script 回传结果
  if (msg.action === 'yb-done' && sender.tab && sender.tab.id != null) {
    const cb = rewriteCallbacks.get(sender.tab.id);
    if (cb) { clearTimeout(cb.timer); rewriteCallbacks.delete(sender.tab.id); cb.resolve(msg.data || {}); }
    return { ok: true };
  }

  // 扩展 popup API
  if (msg.action === 'ping') return { ok: true, base: API_BASE };
  if (msg.action === 'mcp') {
    if (msg.name === 'dedup_video') {
      // 去重开始：popup 关掉也能保留角标（Service Worker 不受 popup 关闭影响）
      chrome.storage.local.set({ dedupPending: true, dedupStartedAt: Date.now() });
      chrome.action.setBadgeText({ text: '…' });
      chrome.action.setBadgeBackgroundColor({ color: '#F59E0B' });
    }
    let _r, _err;
    try {
      _r = await callMcp(msg.name, msg.args || {});
    } catch (e) {
      _err = e;
    }
    if (msg.name === 'dedup_video') {
      // 不管成功还是失败都要清 pending：异常路径不清 → popup 永远卡在「进行中」+ 按钮禁用
      const finalize = () => {
        chrome.storage.local.set({ dedupPending: false });
        chrome.storage.local.remove(['dedupSrc', 'dedupStartedAt']);
        chrome.action.setBadgeText({ text: _err ? '!' : 'OK' });
        chrome.action.setBadgeBackgroundColor({ color: _err ? '#ef4444' : '#10b981' });
        if (_err) {
          // 把失败结果也存进 dedupResult，让 popup 能渲染错误提示
          chrome.storage.local.set({ dedupResult: { error: _err.message || String(_err) } });
        }
      };
      finalize();
      if (!_err) {
        chrome.storage.local.set({ dedupResult: _r });
      }
    }
    if (_err) throw _err;
    const r = _r;
    return r;
  }
  if (msg.action === 'upload64') return await uploadFromBase64(msg.name, msg.data);
  if (msg.action === 'list') return await listVideos();
  if (msg.action === 'probe') return await probeVideo(msg.name);
  if (msg.action === 'rewrite') return await doRewriteFlow(msg.src, msg.template, msg.topic);

  if (msg.action === 'yb-rewrite') {
    const tabId = sender.tab && sender.tab.id;
    try {
      const r = await doRewriteFlow(msg.data && msg.data.src, msg.data && msg.data.template, msg.data && msg.data.topic);
      if (tabId != null) {
        chrome.tabs.sendMessage(tabId, { action: 'yb-result', data: r }).catch(function() {});
      }
      return r;
    } catch (e) {
      const r = { error: e.message };
      if (tabId != null) {
        chrome.tabs.sendMessage(tabId, { action: 'yb-result', data: r }).catch(function() {});
      }
      return r;
    }
  }

  return { error: 'unknown action: ' + msg.action };
}

function waitForTabLoad(tabId) {
  return new Promise(function(resolve) {
    const listener = function(updatedId, info) {
      if (updatedId === tabId && info.status === 'complete') {
        chrome.tabs.onUpdated.removeListener(listener);
        setTimeout(resolve, 1500);
      }
    };
    chrome.tabs.onUpdated.addListener(listener);
    setTimeout(function() { chrome.tabs.onUpdated.removeListener(listener); resolve(); }, 15000);
  });
}

chrome.runtime.onInstalled.addListener(function() {
  console.log('[vu-ext] 视频去重工位扩展已安装');
});