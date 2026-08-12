// popup.js — 扩展弹窗主逻辑（复用现有 HTML 结构 + style.css）
'use strict';

const $ = (id) => document.getElementById(id);
let currentVideo = null;

// ====== 内联 SVG 图标（彻底不用 Emoji）======
const ICON_YES = '<svg class="ico-14" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:2px"><polyline points="4 12 10 18 20 6"/></svg>';
const ICON_NO = '<svg class="ico-14" viewBox="0 0 24 24" fill="none" stroke="#ef4444" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:2px"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>';
const ICON_DOC = '<svg class="ico-14" viewBox="0 0 24 24" fill="none" stroke="#6EE7B7" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:3px"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg>';

// ====== 状态持久化（弹窗关闭不丢数据）======
async function saveState(key, val) {
  await chrome.storage.local.set({ [key]: val });
}
async function loadState(key) {
  const r = await chrome.storage.local.get(key);
  return r[key];
}

// ====== API 桥接：通过 background.js 调 MCP 服务器 ======
async function callTool(name, args) {
  const resp = await chrome.runtime.sendMessage({ action: 'mcp', name, args: args || {} });
  if (resp && resp.error) throw new Error(resp.error);
  return { kind: 'ok', data: resp || {} };
}

async function toast(msg, type) {
  const el = $('toast-area');
  const colors = { ok: '#10b981', err: '#ef4444', warn: '#f59e0b' };
  el.style.display = '';
  el.textContent = msg;
  el.style.color = colors[type] || '#374151';
  el.style.background = type === 'err' ? '#FDECEC' : type === 'warn' ? '#FFF6E5' : '#E8F7F0';
  clearTimeout(el._timeout);
  el._timeout = setTimeout(() => { el.style.display = 'none'; }, 2500);
}

// ====== 初始化 ======
(async function init() {
  // 上传
  setupUpload();
  // 模板按钮
  document.querySelectorAll('.tts-template-fill').forEach(b => {
    b.addEventListener('click', () => { $('tts-template').value = b.dataset.template; });
  });
  $('btn-template-clear').addEventListener('click', () => { $('tts-template').value = ''; });

  // 改写开关
  $('chk-rewrite').addEventListener('change', () => {
    $('tts-template-wrap').style.display = $('chk-rewrite').checked ? '' : 'none';
  });

  // 改写按钮
  $('btn-rewrite-preview').addEventListener('click', doRewrite);

  // 去重按钮
  $('btn-dedup').addEventListener('click', doDedup);

  // 恢复产物按钮
  $('btn-recover-outputs').addEventListener('click', doRecoverOutputs);

  // 探测
  $('btn-probe').addEventListener('click', doProbe);
  $('btn-refresh-assets').addEventListener('click', loadAssets);

  // 视频下拉
  $('asset-select').addEventListener('change', () => {
    currentVideo = { name: $('asset-select').value };
    saveState('selectedVideo', $('asset-select').value);
  });

  // 探测
  $('btn-probe').addEventListener('click', doProbe);
  $('btn-refresh-assets').addEventListener('click', loadAssets);

  // 模板/topic/tts-text 输入持久化
  $('tts-template').addEventListener('input', () => saveState('template', $('tts-template').value));
  $('tts-topic').addEventListener('input', () => saveState('topic', $('tts-topic').value));
  $('tts-text').addEventListener('input', () => saveState('ttsText', $('tts-text').value));

  // 恢复持久化的状态
  const saved = await chrome.storage.local.get(['selectedVideo', 'probeData', 'template', 'topic', 'ttsText', 'rewriteResult', 'dedupPending', 'dedupSrc', 'dedupResult']);
  if (saved.template) $('tts-template').value = saved.template;
  if (saved.topic) $('tts-topic').value = saved.topic;
  if (saved.ttsText) { $('tts-text').value = saved.ttsText; $('tts-text-wrap').style.display = ''; }
  // 强度档
  document.querySelectorAll('#level-seg .seg-btn').forEach(b => {
    b.addEventListener('click', function() {
      document.querySelectorAll('#level-seg .seg-btn').forEach(x => x.classList.remove('active'));
      this.classList.add('active');
    });
  });

  // 检测连通性
  try {
    const r = await chrome.runtime.sendMessage({ action: 'ping' });
    $('conn-badge').classList.add('is-connected');
    $('conn-badge').classList.remove('is-error');
    $('conn-badge').innerHTML = '<span class="status-dot"></span> 已连接';
  } catch {
    $('conn-badge').classList.add('is-error');
    $('conn-badge').classList.remove('is-connected');
    $('conn-badge').innerHTML = '<span class="status-dot"></span> 离线';
  }

  $('conn-badge-ext').textContent = '扩展已就绪';
  await loadAssets();
  // 恢复上次选的视频和探测结果
  if (saved.selectedVideo) {
    $('asset-select').value = saved.selectedVideo;
    currentVideo = { name: saved.selectedVideo };
    if (saved.probeData) {
      renderProbe(saved.probeData);
    }
  }
  if (saved.rewriteResult) {
    renderRewrite(saved.rewriteResult);
    chrome.action.setBadgeText({ text: '' });
  }
  // 去重进行中状态恢复
  if (saved.dedupPending) {
    // 超时清理：超过 10 分钟还在 pending 说明上次异常（服务器崩溃等）
    if (saved.dedupStartedAt && (Date.now() - saved.dedupStartedAt > 600000)) {
      chrome.storage.local.remove(['dedupPending', 'dedupSrc', 'dedupStartedAt']);
      chrome.action.setBadgeText({ text: '' });
    } else {
      $('btn-dedup').disabled = true;
      $('btn-dedup').textContent = '去重进行中...';
      $('dedup-card').classList.remove('hidden');
      $('dedup-checks').innerHTML = '<div style="font-size:12px;color:#A96700;text-align:center;padding:8px;">去重正在后台运行，完成后扩展图标角标「OK」会亮起</div>';
      $('dedup-artifact').classList.add('hidden');
      $('dedup-fail-hint').classList.add('hidden');
      startDedupTimer('去重处理中: ' + (saved.dedupSrc || ''));
    }
  }
  // 去重结果恢复
  if (saved.dedupResult) {
    showDedupResult(saved.dedupResult);
    chrome.storage.local.remove('dedupResult');
    chrome.action.setBadgeText({ text: '' });
  }

  // 始终显示"查看最近产物"按钮，作为丢失链接的恢复手段
  $('btn-recover-outputs').classList.remove('hidden');
})();

// ====== 上传 ======
function setupUpload() {
  const zone = $('upload-zone');
  const input = $('file-upload');
  $('upload-zone').addEventListener('click', () => input.click());
  input.addEventListener('change', () => { if (input.files.length) doUpload(input.files[0]); });
  zone.addEventListener('dragover', e => { e.preventDefault(); });
  zone.addEventListener('drop', e => {
    e.preventDefault();
    if (e.dataTransfer.files[0]) doUpload(e.dataTransfer.files[0]);
  });
}

const API_BASE = 'http://124.71.209.36:8765';

async function doUpload(file) {
  const prog = $('upload-progress');
  const fill = $('progress-fill');
  const text = $('progress-text');
  prog.classList.remove('hidden');
  fill.style.width = '0%';
  text.textContent = '准备上传... ' + file.name;

  try {
    var result = await new Promise(function(resolve, reject) {
      var xhr = new XMLHttpRequest();
      xhr.open('POST', API_BASE + '/local/upload');

      xhr.upload.onprogress = function(e) {
        if (e.lengthComputable) {
          var pct = Math.round(e.loaded / e.total * 100);
          fill.style.width = pct + '%';
          var loaded = (e.loaded / 1048576).toFixed(1);
          var total = (e.total / 1048576).toFixed(1);
          text.textContent = '上传中 ' + pct + '%  (' + loaded + '/' + total + ' MB)';
        }
      };

      xhr.onload = function() {
        if (xhr.status >= 200 && xhr.status < 300) resolve(xhr.responseText);
        else reject(new Error('HTTP ' + xhr.status));
      };
      xhr.onerror = function() { reject(new Error('网络错误')); };
      xhr.ontimeout = function() { reject(new Error('上传超时')); };
      xhr.timeout = 300000;

      var formData = new FormData();
      formData.append('file', file, file.name);
      xhr.send(formData);
    });

    fill.style.width = '100%';
    text.innerHTML = ICON_YES + file.name + ' 上传完成';
    await loadAssets();
  } catch (e) {
    text.innerHTML = ICON_NO + '上传失败: ' + e.message;
  } finally {
    setTimeout(() => prog.classList.add('hidden'), 2000);
  }
}

// ====== 素材列表 ======
async function loadAssets() {
  try {
    const r = await callTool('list_assets');
    const assets = (r.data && r.data.assets) || [];
    const sel = $('asset-select');
    sel.innerHTML = '<option value="">-- 选择视频 --</option>';
    assets.forEach(a => {
      const opt = document.createElement('option');
      opt.value = a.name;
      opt.textContent = a.name + ' (' + (a.size_mb || '?') + ' MB)';
      sel.appendChild(opt);
    });
    if (!assets.length) sel.innerHTML += '<option disabled>（暂无上传视频）</option>';
  } catch (e) {
    console.error('loadAssets err:', e);
    $('asset-select').innerHTML = '<option value="">加载失败:' + (e.message || '网络') + '</option>';
  }
}

// ====== 探测 ======
function renderProbe(d) {
  const grid = $('probe-grid');
  const rows = [['时长', (d.duration||'?')+' s'], ['分辨率', (d.width||'?')+'x'+(d.height||'?')], ['帧率', (d.frame_rate||'?')+' fps'], ['视频编码', d.video_codec||'?'], ['音频编码', d.audio_codec||'无'], ['音频采样率', d.audio_sample_rate||'?'], ['容器字幕', d.has_subtitle ? ICON_YES + '有' : ICON_NO + '无(需ASR)'], ['大小', (d.size_mb||'?')+' MB'], ['MD5', (d.md5||'?').slice(0,12)+'…']];
  grid.innerHTML = rows.map(([k, v]) => '<div style=\"display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px solid rgba(15,23,42,0.12);color:#0f2a4a\"><span style=\"color:#475569\">' + k + '</span><span style=\"font-weight:600;color:#0f2a4a\">' + v + '</span></div>').join('');
  $('probe-card').classList.remove('hidden');
  // 折叠 toggle
  $('probe-head').onclick = () => {
    const body = $('probe-body');
    const arrow = $('probe-toggle');
    body.classList.toggle('hidden');
    arrow.style.transform = body.classList.contains('hidden') ? 'rotate(180deg)' : 'rotate(0deg)';
  };
}

async function doProbe() {
  const name = $('asset-select').value;
  if (!name) return toast('请先选择视频', 'warn');
  $('btn-probe').textContent = '探测中...';
  try {
    const r = await callTool('probe_video', { src: name });
    currentVideo = { name, ...r.data };
    const d = r.data || {};
    renderProbe(d);
    saveState('probeData', d);
    toast('探测完成', 'ok');
  } catch (e) {
    toast('探测失败: ' + e.message, 'err');
  } finally {
    $('btn-probe').textContent = '探测';
  }
}

// ====== 元宝改写 ======
async function doRewrite() {
  const name = $('asset-select').value;
  if (!name) return toast('请先选择视频', 'warn');
  if (!$('chk-rewrite').checked) return toast('请先勾选「启用元宝改写」', 'warn');
  const template = $('tts-template').value.trim();
  const topic = $('tts-topic').value.trim();

  const btn = $('btn-rewrite-preview');
  const orig = btn.textContent;
  btn.disabled = true;
  btn.textContent = '改写中...';

  // 清掉上一次的改写缓存和角标，避免 popup 重开时显示旧结果
  chrome.storage.local.remove('rewriteResult');
  chrome.action.setBadgeText({ text: '' });
  $('rewrite-preview').style.display = 'none';

  try {
    const r = await chrome.runtime.sendMessage({
      action: 'rewrite',
      src: name, template, topic,
    });
    if (r.error) throw new Error(r.error);
    if (r.rewritten) {
      await chrome.storage.local.set({ rewriteResult: r });
      renderRewrite(r);
      toast('改写完成', 'ok');
    } else {
      toast('未获得改写结果', 'warn');
    }
  } catch (e) {
    toast('改写失败: ' + e.message, 'err');
  } finally {
    btn.disabled = false;
    btn.textContent = orig;
  }
}

function renderRewrite(r) {
  const box = $('rewrite-preview');
  const durText = r.duration ? ' (视频 ' + Math.round(r.duration) + 's，上限 ' + r.max_chars + ' 字)' : '';
  const origText = r.original || '无';
  const sandpaperSvg = '<svg class="sandpaper-svg" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="none"><defs><filter id="sandTexture"><feTurbulence result="sand" seed="20" numOctaves="4" baseFrequency="0.6" type="fractalNoise"></feTurbulence><feColorMatrix values="0.8 0 0 0 0.1  0 0.7 0 0 0.05  0 0 0.6 0 0.02  0 0 0 1 0" type="matrix"></feColorMatrix></filter></defs><rect filter="url(#sandTexture)" width="100%" height="100%"></rect></svg>';
  function spBlock(inner) { return '<div class="sandpaper-pattern">' + sandpaperSvg + inner + '</div>'; }
  var originalHtml = '<div class="rp-original">' + ICON_DOC + '原文 (' + (r.source || '') + ')：' + origText + '</div>';
  var rewrittenHtml = '<div class="rp-rewritten">' + r.rewritten + '<span class="rp-meta">(' + r.rewritten.length + '字)</span></div>';
  box.style.display = '';
  box.setAttribute('data-rewritten', r.rewritten);
  box.innerHTML =
    '<div class="rp-title"><svg class="ico-16" style="color:#A855F7"><use href="#ico-sparkle"/></svg> 元宝改写结果<span class="rp-meta">' + durText + '</span></div>' +
    spBlock(originalHtml) + spBlock(rewrittenHtml) +
    '<div class="rp-actions" style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">' +
    '<button type="button" class="btn btn-mini btn-confirm-rw" style="background:#16845B;color:#fff;"><svg class="ico-16" style="color:#6EE7B7"><use href=\'#ico-check\'/></svg> 确认使用此文案</button>' +
    '<button type="button" class="btn btn-mini btn-copy-rw" style="background:#3B82F6;color:#fff;" title="复制到剪贴板"><svg class="ico-16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="color:#BFDBFE"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg> 复制</button>' +
    '<button type="button" class="btn btn-mini btn-close-rw" style="background:#444;color:#fff;"><svg class="ico-16" style="color:#FFA0A0"><use href=\'#ico-xcircle\'/></svg> 不用</button>' +
    '</div>';
  // CSP 安全：用 addEventListener
  box.querySelector('.btn-confirm-rw').addEventListener('click', function() {
    var txt = (box.getAttribute('data-rewritten') || '').trim();
    if (!txt) { toast('未找到改写文案', 'warn'); return; }
    $('tts-text').value = txt;
    saveState('ttsText', txt);
    $('tts-text-wrap').style.display = '';
    box.style.display = 'none';
    chrome.storage.local.remove('rewriteResult');
    toast('文案已填入，请点击「开始单条去重」', 'ok');
  });
  box.querySelector('.btn-copy-rw').addEventListener('click', function() {
    navigator.clipboard.writeText(r.rewritten).then(function() { toast('已复制到剪贴板', 'ok'); });
  });
  box.querySelector('.btn-close-rw').addEventListener('click', clearRewrite);
}

function clearRewrite() {
  $('rewrite-preview').style.display = 'none';
  chrome.storage.local.remove('rewriteResult');
  chrome.action.setBadgeText({ text: '' });
}

function _phashHint(ph) {
  var isSig = ph.method === 'signature';
  return isSig
    ? '签名兜底仍未通过：变体与原素材过于相似，建议启用翻转或换 seed。'
    : 'pHash 未达标：建议启用更多维度 / 提高档位 / 换 seed。';
}

var _dedupTimer = null;
var _orbCleanup = null;

async function startDedupTimer(label) {
  stopDedupTimer();
  var box = $('dedup-progress');
  var labelNode = $('dedup-progress-label');
  var timeNode = $('dedup-progress-time');

  // 从 storage 读开始时间（popup 重开不重置）
  var stored = await chrome.storage.local.get('dedupStartedAt');
  var startedAt = stored.dedupStartedAt || Date.now();
  if (!stored.dedupStartedAt) {
    await chrome.storage.local.set({ dedupStartedAt: startedAt });
  }

  labelNode.textContent = label || '去重处理中';
  timeNode.textContent = '';
  box.classList.remove('hidden');
  _orbCleanup = setupOrbInteraction(box);

  _dedupTimer = setInterval(function() {
    var elapsed = Math.floor((Date.now() - startedAt) / 1000);
    var m = Math.floor(elapsed / 60);
    var s = elapsed % 60;
    timeNode.textContent = '已处理 ' + (m > 0 ? m + '分' : '') + s + '秒';
  }, 500);
}

function stopDedupTimer() {
  if (_dedupTimer) { clearInterval(_dedupTimer); _dedupTimer = null; }
  if (_orbCleanup) { _orbCleanup(); _orbCleanup = null; }
}

// From: setupOrbInteraction (web project, archive/web/app.js)
function setupOrbInteraction(box) {
  var path = box.querySelector('.orb-path');
  var globe = box.querySelector('.orb-globe');
  if (!path || !globe) return null;
  var state = { position: 0, direction: 1, dragging: false, pointerId: null, lastFrame: performance.now(), frame: 0 };
  function pathScale() { return path.getBoundingClientRect().width / path.offsetWidth || 1; }
  function maxPosition() { return Math.max(0, path.clientWidth - globe.offsetWidth - 4); }
  function renderPosition() {
    var max = maxPosition();
    state.position = Math.max(0, Math.min(max, state.position));
    globe.style.transform = 'translateX(' + state.position + 'px)';
  }
  function animate(now) {
    var max = maxPosition();
    if (!state.dragging && max > 0) {
      var delta = Math.min(40, now - state.lastFrame);
      state.position += state.direction * delta * max / 2000;
      if (state.position >= max) { state.position = max; state.direction = -1; }
      if (state.position <= 0) { state.position = 0; state.direction = 1; }
      renderPosition();
    }
    state.lastFrame = now;
    if (!box.classList.contains('hidden')) state.frame = requestAnimationFrame(animate);
  }
  function beginDrag(event) {
    if (event.button != null && event.button !== 0) return;
    state.dragging = true;
    state.pointerId = event.pointerId;
    state.lastClientX = event.clientX;
    globe.classList.add('is-dragging');
    path.classList.add('is-dragging');
    try { globe.setPointerCapture?.(event.pointerId); } catch (_) {}
    event.preventDefault();
  }
  function moveDrag(event) {
    if (!state.dragging || event.pointerId !== state.pointerId) return;
    state.position += (event.clientX - state.lastClientX) / pathScale();
    state.lastClientX = event.clientX;
    renderPosition();
    event.preventDefault();
  }
  function endDrag(event) {
    if (!state.dragging || event.pointerId !== state.pointerId) return;
    var max = maxPosition();
    state.direction = state.position >= max / 2 ? -1 : 1;
    state.dragging = false;
    state.pointerId = null;
    globe.classList.remove('is-dragging');
    path.classList.remove('is-dragging');
    try { globe.releasePointerCapture?.(event.pointerId); } catch (_) {}
  }
  function moveByKeyboard(event) {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    state.position += event.key === 'ArrowRight' ? 14 : -14;
    state.direction = event.key === 'ArrowRight' ? 1 : -1;
    renderPosition();
    event.preventDefault();
  }
  globe.addEventListener('pointerdown', beginDrag);
  globe.addEventListener('pointermove', moveDrag);
  globe.addEventListener('pointerup', endDrag);
  globe.addEventListener('pointercancel', endDrag);
  globe.addEventListener('keydown', moveByKeyboard);
  renderPosition();
  state.frame = requestAnimationFrame(animate);
  return function() {
    cancelAnimationFrame(state.frame);
    globe.removeEventListener('pointerdown', beginDrag);
    globe.removeEventListener('pointermove', moveDrag);
    globe.removeEventListener('pointerup', endDrag);
    globe.removeEventListener('pointercancel', endDrag);
    globe.removeEventListener('keydown', moveByKeyboard);
    globe.classList.remove('is-dragging');
    path.classList.remove('is-dragging');
  };
}

function showDedupResult(d) {
  var card = $('dedup-card');
  card.classList.remove('hidden');

  var outPath = d.output_path || '';
  var outName = outPath ? outPath.split('/').pop() : '生成完成';
  var checks = d.checks || {};
  var ph = checks.phash || {};
  var phPassed = !!ph.passed;
  var allPassed = checks.all_passed;

  $('dedup-artifact').classList.remove('hidden');
  $('dedup-artifact-name').textContent = outName;
  $('dedup-artifact-name').title = outPath;
  $('btn-download-output').classList.remove('hidden');
  $('btn-download-output').onclick = function() {
    chrome.downloads.download({ url: 'http://124.71.209.36:8765/local/download/' + encodeURIComponent(outName) });
  };

  var phSkipped = ph.skipped;
  var checkItems = [
    ['MD5 已改变', checks.md5_changed],
    ['分辨率保持', checks.resolution_kept],
    ['时长合规', checks.duration_close],
    ['>= 5s', checks.min_duration_ok],
    ['pHash' + (phSkipped ? ' (已跳过)' : ''), phPassed, (ph.passed === false && !phSkipped) ? _phashHint(ph) : '', phSkipped],
  ];
  $('dedup-checks').innerHTML = checkItems.map(function(item) {
    var label = item[0], ok = item[1], hint = item[2] || '', skipped = item[3] || false;
    var cls, mark;
    if (skipped) { cls = 'skip'; mark = '<span class="check-mark" style="color:#6B7280">—</span>'; }
    else if (ok)  { cls = 'pass'; mark = ICON_YES; }
    else          { cls = 'fail'; mark = ICON_NO; }
    return '<div class="check-item ' + cls + '">' +
      mark +
      '<div><b>' + label + '</b>' + (hint ? '<div class="check-hint">' + hint + '</div>' : '') + '</div>' +
      '</div>';
  }).join('');

  $('dedup-fail-hint').classList.toggle('hidden', allPassed);
  if (!allPassed) {
    $('dedup-fail-text').textContent = phPassed
      ? '部分检查未通过，请查看上方具体项目。建议调整参数后重试。'
      : 'pHash 未达标：建议启用更多维度 / 提高档位（重度）/ 换 seed / 开启翻转后重试。';
  }

  // TTS 状态提示
  var tts = d.tts;
  $('dedup-tts-hint').classList.add('hidden');
  if (tts && tts !== 'ok') {
    $('dedup-tts-hint').classList.remove('hidden');
    var hint = '';
    if (tts.indexOf('401') >= 0 || tts.indexOf('Invalid') >= 0 || tts.indexOf('API Key') >= 0) {
      hint = 'MiMo API Key 无效（401），文案已跳过配音。可在服务器更新 Key 或取消勾选「启用元宝改写」以避免此提示。';
    } else if (tts.indexOf('timeout') >= 0 || tts.indexOf('超时') >= 0) {
      hint = 'TTS 请求超时，视频保留原始音轨。可重试或取消勾选「启用元宝改写」。';
    } else {
      hint = 'TTS 处理异常：' + tts + '，视频保留原始音轨。可取消勾选「启用元宝改写」跳过。';
    }
    $('dedup-tts-text').textContent = hint;
  }
}

// ====== 去重 ======
async function doDedup() {
  const name = $('asset-select').value;
  if (!name) return toast('请先选择视频', 'warn');

  const levelBtn = document.querySelector('#level-seg .seg-btn.active');
  const level = levelBtn ? levelBtn.dataset.level : 'medium';
  const dims = {};
  document.querySelectorAll('#dim-grid input:checked').forEach(c => { dims[c.dataset.dim] = true; });
  if (!Object.keys(dims).length) return toast('请至少选一个维度', 'warn');

  const btn = $('btn-dedup');
  btn.disabled = true;
  btn.textContent = '去重中...';

  // 标记进行中，popup 重开时显示状态
  await chrome.storage.local.set({ dedupPending: true, dedupSrc: name, dedupStartedAt: Date.now() });
  chrome.action.setBadgeText({ text: '…' });
  chrome.action.setBadgeBackgroundColor({ color: '#F59E0B' });

  // 进度条
  $('dedup-card').classList.add('hidden');
  startDedupTimer('去重处理中: ' + name);

  try {
    const r = await callTool('dedup_video', {
      src: name, level, dimensions: dims,
      tts_voice: $('tts-voice').value,
      tts_speed: parseFloat($('tts-speed').value),
      tts_text: $('chk-rewrite').checked ? ($('tts-text').value.trim() || null) : null,
      tts_topic: $('tts-topic').value,
      tts_template: $('tts-template').value,
      skip_phash: $('chk-skip-phash') ? $('chk-skip-phash').checked : true,
    });
    const d = r.data || {};
    stopDedupTimer();
    $('dedup-progress').classList.add('hidden');
    showDedupResult(d);
    toast('去重完成', 'ok');
  } catch (e) {
    stopDedupTimer();
    $('dedup-progress').classList.add('hidden');
    toast('去重失败: ' + e.message, 'err');
  } finally {
    chrome.storage.local.remove(['dedupPending', 'dedupSrc', 'dedupStartedAt']);
    // 角标由 background.js 管理（…→OK），不在这里清
    btn.disabled = false;
    btn.textContent = '开始单条去重';
  }
}

// ====== 恢复产物：列出服务器 output 目录，即使 chrome.storage 清空也能下载 ======
async function doRecoverOutputs() {
  var btn = $('btn-recover-outputs');
  var list = $('recover-outputs-list');
  btn.disabled = true;
  btn.textContent = '获取中...';
  list.classList.add('hidden');
  try {
    var r = await callTool('list_outputs');
    var outputs = (r.data && r.data.outputs) || [];
    if (!outputs.length) {
      list.innerHTML = '<div style="color:#9CA3AF;padding:4px;">没有产物</div>';
      list.classList.remove('hidden');
      return;
    }
    list.innerHTML = outputs.map(function(o) {
      return '<div style="display:flex;justify-content:space-between;align-items:center;padding:4px 0;border-bottom:1px solid rgba(15,23,42,0.08);">' +
        '<span style="color:#374151;">' + o.name + '</span>' +
        '<span style="color:#9CA3AF;">' + o.size_mb + ' MB / ' + o.mtime + '</span>' +
        '<button class="btn btn-mini" style="background:#4A90D9;color:#fff;margin-left:6px;" onclick="chrome.downloads.download({url:\'http://124.71.209.36:8765/local/download/' + encodeURIComponent(o.name) + '\'})">下载</button>' +
        '</div>';
    }).join('');
    list.classList.remove('hidden');
  } catch (e) {
    toast('获取产物列表失败: ' + e.message, 'err');
  } finally {
    btn.disabled = false;
    btn.textContent = '查看最近产物';
  }
}
