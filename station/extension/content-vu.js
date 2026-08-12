// content-vu.js — 注入 vu.evenblue.top
// 职责：给网站页面暴露 window.__ybExtension API，网站通过它发送改写请求并接收结果
'use strict';

(function() {

  // 向前端暴露 API
  window.__ybExtension = {
    ready: true,
    version: '1.0',

    // 发送改写请求
    rewrite: function({ apiBase, src, template, topic }) {
      return new Promise((resolve, reject) => {
        const handler = (event) => {
          if (event.source !== window) return;
          if (event.data?.action === 'yb-rewrite-result') {
            window.removeEventListener('message', handler);
            if (event.data.error) reject(new Error(event.data.error));
            else resolve(event.data.data);
          }
        };
        window.addEventListener('message', handler);
        chrome.runtime.sendMessage({
          action: 'yb-rewrite',
          data: { apiBase, src, template, topic },
        });

        // 超时
        setTimeout(() => {
          window.removeEventListener('message', handler);
          reject(new Error('改写超时 (3 分钟)'));
        }, 190000);
      });
    },
  };

  // 接收 background 返回的结果，通过 postMessage 传给页面
  chrome.runtime.onMessage.addListener((msg) => {
    if (msg.action === 'yb-result') {
      window.postMessage({
        action: 'yb-rewrite-result',
        data: msg.data || {},
        error: msg.data?.error || null,
      }, '*');
    }
  });

  // 通知页面扩展已就绪
  window.postMessage({ action: 'yb-extension-ready' }, '*');

  console.log('[yb-ext] 元宝改写桥接已就绪 (vu.evenblue.top)');
})();
