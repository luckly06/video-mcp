// lib/api-base.js — 解析 API_BASE
//
// 优先级：
//   1) 环境变量 VIDEODEDUP_API_BASE（开发/测试覆盖，指定外部后端）
//   2) 默认值 http://127.0.0.1:8765（本地打包的 MCP 后端）
//
// 注：renderer 端的兜底逻辑（archive/web/app.js:17-23 补丁）独立工作；
// 这里只决定 main 进程要不要把这个值注入到 URL query。
'use strict';

const DEFAULT_API_BASE = 'http://127.0.0.1:8765';

function parseApiBase({ envVar, defaultBase = DEFAULT_API_BASE } = {}) {
  const raw = (envVar && typeof envVar === 'string') ? envVar.trim() : '';
  if (!raw) return defaultBase.replace(/\/+$/, '');
  // 防止注入：只允许 http/https
  if (!/^https?:\/\//i.test(raw)) {
    return defaultBase.replace(/\/+$/, '');
  }
  return raw.replace(/\/+$/, '');
}

module.exports = { parseApiBase, DEFAULT_API_BASE };