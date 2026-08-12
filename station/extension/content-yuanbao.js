// content-yuanbao.js — 注入 yuanbao.tencent.com
// 职责：接收 background 注入指令，在元宝页面 DOM 上完成 上传图片 -> 填提示词 -> 发送 -> 读回复 -> 回传
'use strict';

// 导出为全局变量，供 chrome.scripting.executeScript 的 func 使用
window.__ybInject = doRewrite;

async function doRewrite({ frames_b64, raw_text, template, topic, max_chars }) {
  try {
    // 1. 等待输入框就绪
    const editor = await waitForEditor(15000);
    if (!editor) return done({ error: '元宝页面加载超时' });

    _baseline = countReplies();
    _lastText = '';
    _stableCount = 0;

    // 2. 上传图片
    if (frames_b64 && frames_b64.length > 0) {
      await uploadImages(frames_b64, editor);
    }

    // 3. 构建提示词并填入
    const prompt = buildPrompt(raw_text, template, topic, max_chars || 30);
    await fillEditor(editor, prompt);
    await sleep(300);

    // 4. 发送
    editor.focus();
    editor.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, composed: true }));
    editor.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', bubbles: true, composed: true }));
    await sleep(500);

    if (!await isGenerating()) {
      await clickSendButton();
    }

    // 5. 等待回复
    const reply = await waitForReply(180000);
    done(reply ? { rewritten: reply } : { error: '未获得回复' });

  } catch (e) {
    done({ error: e.message || '改写异常' });
  }
}

function done(data) {
  chrome.runtime.sendMessage({ action: 'yb-done', data });
}

// ---- 常量 ----
const SEL_EDITOR = ['div[contenteditable="true"]', 'textarea[placeholder*="输入"]', 'textarea[placeholder*="描述"]'];
const SEL_REPLY = ['.hyc-common-markdown', '[class*="answer"]:not([class*="question"])', '[class*="reply"]:not([class*="question"])', '[class*="bubble"]:not([class*="question"])'];
const SKIP_KEYWORDS = ['正在分析', '正在搜索', '正在生成', '正在思考', '正在处理', '图片识别中', '文件拖动'];
let _baseline = 0, _lastText = '', _stableCount = 0;

// ---- 等待输入框 ----
async function waitForEditor(timeout) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    for (const sel of SEL_EDITOR) {
      const el = document.querySelector(sel);
      if (el && el.offsetParent !== null) return el;
    }
    await sleep(500);
  }
  return null;
}

// ---- 图片上传 ----
async function uploadImages(b64List, editor) {
  try {
    editor.focus(); editor.click(); await sleep(500);
    const ub = document.querySelector('[class*="UploadFileSelector_iconContainer"]');
    if (ub) { ub.click(); await sleep(800); }
    const picBtn = findElementByText('图片');
    if (picBtn) { picBtn.click(); await sleep(500); }
    await sleep(800);
    const fileInput = document.querySelector('input[type="file"]');
    if (!fileInput) return false;
    const dt = new DataTransfer();
    b64List.forEach((b64, i) => { const f = b64ToFile(b64, 'frame_' + i + '.png'); dt.items.add(f); });
    fileInput.files = dt.files;
    fileInput.dispatchEvent(new Event('input', { bubbles: true }));
    fileInput.dispatchEvent(new Event('change', { bubbles: true }));
    await sleep(4000);
    return true;
  } catch (e) { return false; }
}

function b64ToFile(b64, name) {
  const arr = b64.split(','), bstr = atob(arr.length > 1 ? arr[1] : arr[0]), n = bstr.length, u8 = new Uint8Array(n);
  for (let i = 0; i < n; i++) u8[i] = bstr.charCodeAt(i);
  return new File([u8], name, { type: 'image/png' });
}

function findElementByText(text) {
  const result = document.evaluate('//*[normalize-space(text())="' + text + '"]', document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
  return result.singleNodeValue;
}

// ---- 填入发送 ----
async function fillEditor(editor, text) {
  editor.focus();
  if (editor.getAttribute('contenteditable') === 'true') {
    editor.innerText = text;
    editor.dispatchEvent(new Event('input', { bubbles: true }));
  } else {
    editor.value = text;
    editor.dispatchEvent(new Event('input', { bubbles: true }));
  }
  await sleep(400);
}

async function clickSendButton() {
  const btns = document.querySelectorAll('button, [role="button"]');
  for (const btn of btns) {
    if (!btn.offsetParent) continue;
    const label = (btn.textContent || '').trim(), aria = (btn.getAttribute('aria-label') || '');
    if (label.includes('发送') || label.includes('Send') || aria.includes('发送') || label === '\u2192') { btn.click(); await sleep(500); return; }
  }
}

// ---- 提示词（复用 copy_rewriter.py 的 _build_prompt 逻辑）----
var REWRITE_TEMPLATES = {
  '带货': '你是带货主播。改写为口播带货文案：突出产品卖点、制造紧迫感、引导下单。语气热情有感染力。',
  '解说': '你是知识解说博主。改写为解说旁白：逻辑清晰、深入浅出、善用设问引导。语气沉稳专业。',
  'Vlog': '你是生活 Vlog 博主。改写为 Vlog 口播：自然随性、像在跟朋友聊天。语气轻松真实。',
};
var SYSTEM_PROMPT = '你是短视频配音文案优化师。\n\n' +
  '## 任务\n将输入的原始对白/字幕，改写为一条适合 TTS 配音的短视频旁白。\n\n' +
  '## 要求\n1. **口语化**：自然说话语气，不要书面语\n2. **有钩子**：开头 3 秒抓注意力（悬念/提问/冲击性陈述）\n3. **有行动**：结尾留互动引导（"点赞关注"、"评论区聊聊"等）\n4. **时长适配**：30-100 字，适合 15-60 秒短视频\n5. **纯文案**：只输出最终文案，不要解释、前缀、标注\n\n' +
  '## 示例\n输入：这段打斗太精彩了\n输出：你见过这么炸裂的打斗吗？三秒之内反转三次，这操作你学不来！评论区告诉我你看到了第几遍，点赞关注不迷路！';

function buildPrompt(rawText, template, topic, maxChars) {
  var parts = [];
  if (topic && topic.trim()) parts.push('## 视频主题\n这个视频的内容是：' + topic.trim());
  if (template) {
    if (REWRITE_TEMPLATES[template]) {
      parts.push('## 角色\n' + REWRITE_TEMPLATES[template]);
    } else {
      parts.push('## 自定义指令\n' + template);
    }
  }
  var sysLines = SYSTEM_PROMPT.trim().split('\n');
  if (maxChars) {
    var newLines = [];
    for (var i = 0; i < sysLines.length; i++) {
      if (sysLines[i].indexOf('30-100 字') >= 0) {
        newLines.push('4. **时长适配**：严格不超过 ' + maxChars + ' 字（视频仅 ' + Math.round(maxChars/3) + ' 秒），精炼表达核心信息。多余的字请删掉。');
      } else { newLines.push(sysLines[i]); }
    }
    parts.push(newLines.join('\n'));
  } else {
    parts.push(SYSTEM_PROMPT);
  }
  parts.push('需要改写的原文：' + (rawText || ''));
  if (maxChars) parts.push('【重要】你输出的文案务必控制在 ' + maxChars + ' 字以内，不要超出。');
  return parts.join('\n\n');
}

// ---- 等回复（双采样稳定检测）----
async function waitForReply(timeout) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    if (!await isGenerating()) {
      const text = readLastReply();
      if (text) {
        await sleep(2000);
        if (!await isGenerating()) {
          const text2 = readLastReply();
          if (text2 === text) {
            _stableCount++;
            if (_stableCount >= 2) return text;
          } else { _stableCount = 0; _lastText = text2; }
        }
      }
    } else { _stableCount = 0; }
    await sleep(1500);
  }
  return readLastReply();
}

function readLastReply() {
  const containers = document.querySelectorAll(SEL_REPLY.join(','));
  if (containers.length <= _baseline) return '';
  const last = containers[containers.length - 1];
  const text = (last.innerText || last.textContent || '').trim();
  if (!text || text.length < 2) return '';
  if (SKIP_KEYWORDS.some(kw => text.includes(kw))) return '';
  return text;
}

function countReplies() { return document.querySelectorAll(SEL_REPLY.join(',')).length; }

async function isGenerating() {
  const btns = document.querySelectorAll('button, [role="button"]');
  for (const b of btns) { if (/停止|stop|暂停|pause/i.test((b.textContent || '').trim())) return true; }
  return false;
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
