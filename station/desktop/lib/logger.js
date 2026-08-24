// lib/logger.js — 极简文件日志（主进程 console + 同步写入 logs/desktop.log）
//
// Electron 主进程 console 默认走 stderr；想要保留历史日志用于本地诊断，
// 这里追加一份带时间戳的文件日志。不做异步队列（避免崩溃前丢日志），
// 单次 fs.appendFileSync 在 desktop 这种量级（< 1MB/天）下完全够用。
'use strict';

const fs = require('node:fs');
const path = require('node:path');

/**
 * @param {object} opts
 * @param {string} opts.logDir    日志目录
 * @param {string} opts.filename  日志文件名
 * @returns {{ info: Function, warn: Function, error: Function }}
 */
function createLogger({ logDir, filename }) {
  try {
    fs.mkdirSync(logDir, { recursive: true });
  } catch (_) {
    // 静默：日志目录创建失败就只在 console 输出，不抛
  }
  const file = path.join(logDir || '.', filename || 'desktop.log');

  function write(level, args) {
    const ts = new Date().toISOString();
    const line = `[${ts}] [${level}] ` +
      args.map((a) => {
        if (a instanceof Error) return a.stack || (a.message + '\n' + a.stack);
        if (typeof a === 'string') return a;
        try { return JSON.stringify(a); } catch (_) { return String(a); }
      }).join(' ') + '\n';
    try {
      fs.appendFileSync(file, line, 'utf8');
    } catch (_) { /* 静默：磁盘满 / 只读等情况不阻塞主进程 */ }
    // 同时打 stderr（dev 模式显眼）
    try { process.stderr.write(line); } catch (_) { /* ignore */ }
  }

  return {
    info:  (...a) => write('info', a),
    warn:  (...a) => write('warn', a),
    error: (...a) => write('error', a),
  };
}

module.exports = { createLogger };