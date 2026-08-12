// lib/api-base.js — 解析 API_BASE
//
// 优先级：
//   1) 环境变量 VIDEODEDUP_API_BASE（开发/测试覆盖）
//   2) 默认值 http://124.71.209.36:8765（生产 MCP）
//
// 注：renderer 端的兜底逻辑（archive/web/app.js:17-23 补丁）独立工作；
// 这里只决定 main 进程要不要把这个值注入到 URL query。
'use strict';

const DEFAULT_API_BASE = 'http://124.71.209.36:8765';

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