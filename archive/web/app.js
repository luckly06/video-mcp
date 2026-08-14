/* =========================================================================
   视频去重数字员工 · Agent 工位  ——  前端逻辑（原生 JS，零依赖）

   对接 MCP 2026-07-28 无状态 HTTP Server：
     - server/discover  能力发现（替代握手）
     - tools/list       工具清单（带 ttlMs / cacheScope）
     - tools/call       工具调用（warned/blocked 触发 input_required 人工决策）

   人工决策流（SEP-2322 多轮请求）：
     首次调用 warned/blocked 工具 → result.resultType === "input_required"
     前端弹自定义模态框 → 用户确认 → 重发同一请求并附
       params.inputResponses = {confirm:true}，params.requestState 原样回传。
   ========================================================================= */

"use strict";

const API_BASE = (() => {
  // desktop 注入：station/desktop/main.js 通过 file://?...&apiBase=... 加载本页时，
  // 取 query 里的 apiBase 作为 MCP/UPLOAD/DOWNLOAD 的基础地址（绕过 file:// 协议派生失败）。
  const queryBase = new URLSearchParams(window.location.search).get("apiBase");
  if (queryBase && /^https?:\/\//i.test(queryBase)) return queryBase.replace(/\/+$/, "");
  const h = window.location.hostname;
  const p = window.location.port;
  const proto = window.location.protocol;
  if (p && p !== "80" && p !== "443") return `${proto}//${h}:${p}`;
  return `${proto}//${h}`;
})();
const MCP_URL = API_BASE + "/mcp";
const OPEN_OUTPUT_URL = API_BASE + "/local/open-output";
const GET_OUTPUT_DIR_URL = API_BASE + "/local/get-output-dir";
const SET_OUTPUT_DIR_URL = API_BASE + "/local/set-output-dir";
const CANCEL_FISSION_URL = API_BASE + "/local/cancel-fission";
const UPLOAD_URL = API_BASE + "/local/upload";
const DOWNLOAD_BASE = API_BASE + "/local/download/";

/* 工具四级安全分级（按工具名硬编码映射，与 shared/rules.json 对齐）。
   list_* / probe / get_job = audit，dedup / batch_fission / remove_watermark = warned，
   delete_output = blocked，check_env = pass。 */
function tierOf(name) {
  if (name === "delete_output") return "blocked";
  if (name === "dedup_video" || name === "batch_fission" || name === "remove_watermark") return "warned";
  if (name === "check_env") return "pass";
  if (name.startsWith("list_") || name.startsWith("probe") || name === "get_job") return "audit";
  return "audit";
}
const TIER_LABEL = { audit: "审计", warned: "需确认", blocked: "阻断", pass: "放行" };
const TIER_ORDER = ["audit", "warned", "blocked", "pass"];
const TOOL_AXIS_COLORS = {
  audit: { fill: "#B9E3CF", stroke: "#16845B" },
  warned: { fill: "#F8D894", stroke: "#A96700" },
  blocked: { fill: "#F2B6B6", stroke: "#C73535" },
  pass: { fill: "#D8DEE7", stroke: "#64748B" },
};
const TOOL_CONFIRMATION = {
  audit: "无需确认，可直接读取或查询。",
  warned: "执行前必须经过人工确认。",
  blocked: "危险操作，当前策略硬阻断。",
  pass: "无需确认，策略允许直接执行。",
};

/* ---------------------------------------------------------------------------
   DOM 引用
   --------------------------------------------------------------------------- */
const $ = (id) => document.getElementById(id);
const el = {
  connBanner: $("conn-banner"),
  connText: $("conn-text"),
  connRetry: $("conn-retry"),
  serverName: $("server-name"),
  serverProto: $("server-proto"),
  badgeStatus: $("badge-status"),
  workflowSteps: $("workflow-steps"),
  btnOpenOutputTop: $("btn-open-output-top"),

  whitelist: $("whitelist"),
  toolsList: $("tools-list"),
  toolsCount: $("tools-count"),
  toolFilters: $("tool-filters"),
  toolAxis: $("tool-axis"),
  toolAxisNote: $("tool-axis-note"),
  toolDetail: $("tool-detail"),

  assetSelect: $("asset-select"),
  btnRefreshAssets: $("btn-refresh-assets"),
  btnProbe: $("btn-probe"),
  probeCard: $("probe-card"),
  probeGrid: $("probe-grid"),

  uploadZone: $("upload-zone"),
  fileUpload: $("file-upload"),
  uploadProgress: $("upload-progress"),
  progressFill: $("progress-fill"),
  progressText: $("progress-text"),

  levelSeg: $("level-seg"),
  dimGrid: $("dim-grid"),
  flipMode: $("flip-mode"),
  flipModeRow: $("flip-mode-row"),

  btnDedup: $("btn-dedup"),
  dedupCard: $("dedup-card"),
  chkMd5: $("chk-md5"),
  chkRes: $("chk-res"),
  chkDur: $("chk-dur"),
  chkMinDur: $("chk-min-dur"),
  chkPhash: $("chk-phash"),
  phashDetail: $("phash-detail"),
  phashHint: $("phash-hint"),
  dedupArtifact: $("dedup-artifact"),
  dedupArtifactName: $("dedup-artifact-name"),
  dedupDetail: $("dedup-detail"),
  btnDeliver: $("btn-deliver"),
  btnRegen: $("btn-regen"),
  btnOpenOutput: $("btn-open-output"),
  dedupProgress: $("dedup-progress"),
  dedupProgressLabel: $("dedup-progress-label"),
  dedupProgressTime: $("dedup-progress-time"),

  fissionCount: $("fission-count"),
  btnFission: $("btn-fission"),
  btnCancelFission: $("btn-cancel-fission"),
  fissionCard: $("fission-card"),
  fissionSummary: $("fission-summary"),
  fissionExplainer: $("fission-explainer"),
  fissionSeparation: $("fission-separation"),
  fissionMatrixWrap: $("fission-matrix-wrap"),
  fissionMatrix: $("fission-matrix"),
  ssimMatrixWrap: $("ssim-matrix-wrap"),
  ssimMatrix: $("ssim-matrix"),
  fissionList: $("fission-list"),
  btnOpenOutputFission: $("btn-open-output-fission"),
  fissionProgress: $("fission-progress"),
  fissionProgressLabel: $("fission-progress-label"),
  fissionProgressTime: $("fission-progress-time"),

  timeline: $("timeline"),
  memoryCount: $("memory-count"),
  memoryDate: $("memory-date"),
  memorySearch: $("memory-search"),
  memoryArc: $("memory-arc"),
  memoryUnit: $("memory-unit"),
  btnClearMemory: $("btn-clear-memory"),

  modalOverlay: $("modal-overlay"),
  modalMsg: $("modal-msg"),
  modalOpName: $("modal-op-name"),
  modalOpParams: $("modal-op-params"),
  modalCancel: $("modal-cancel"),
  modalConfirm: $("modal-confirm"),
};

/* 记住最近一次 probe 的素材名和当前去重交付门状态 */
let lastProbedAsset = null;
let lastProbeInfo = null;
let dedupDeliveryReady = false;
let currentDedupArtifact = null;
let currentFissionArtifacts = [];
let selectedFissionArtifact = null;
let currentWorkflowStep = 1;
let lastModalTrigger = null;
let activeProgress = new Map();
let activeFissionTaskId = null;
let fissionCancelPending = false;

const PROGRESS_HISTORY_KEY = "video-dedup-progress-history-v1";
const PROGRESS_SAMPLE_LIMIT = 8;

function formatDuration(seconds) {
  const total = Math.max(0, Math.ceil(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  const mm = String(minutes).padStart(2, "0");
  const ss = String(secs).padStart(2, "0");
  return hours > 0 ? `${hours}:${mm}:${ss}` : `${mm}:${ss}`;
}

function taskWorkUnits(kind, context) {
  const duration = Math.max(1, Number(context.duration) || 10);
  const count = kind === "fission" ? Math.max(1, Number(context.count) || 1) : 1;
  const enabled = Object.values(context.dimensions || {}).filter(Boolean).length;
  const levelFactor = { light: 0.9, medium: 1, heavy: 1.18 }[context.level] || 1;
  const dimensionFactor = 0.82 + Math.min(enabled, 6) * 0.06;
  return duration * count * levelFactor * dimensionFactor;
}

function readProgressHistory() {
  try {
    const value = JSON.parse(localStorage.getItem(PROGRESS_HISTORY_KEY) || "{}");
    return value && typeof value === "object" ? value : {};
  } catch (_) {
    return {};
  }
}

function estimateProgress(kind, context) {
  const units = taskWorkUnits(kind, context);
  const history = readProgressHistory();
  const samples = Array.isArray(history[kind]) ? history[kind] : [];
  let secondsPerUnit = 0.72;
  let calibrated = false;
  if (samples.length) {
    const recent = samples.slice(-PROGRESS_SAMPLE_LIMIT);
    const weighted = recent.reduce((sum, sample, index) => {
      const weight = index + 1;
      return { total: sum.total + sample.rate * weight, weight: sum.weight + weight };
    }, { total: 0, weight: 0 });
    secondsPerUnit = weighted.total / weighted.weight;
    calibrated = true;
  }
  const count = Math.max(1, Number(context.count) || 1);
  const overhead = kind === "fission" ? Math.max(2, count * (count - 1) * 0.225) : 3;
  const seconds = Math.max(8, Math.min(21600, units * secondsPerUnit + overhead));
  return { seconds, units, overhead, calibrated };
}

function setupOrbInteraction(box) {
  const path = box.querySelector(".orb-path");
  const globe = box.querySelector(".orb-globe");
  if (!path || !globe) return null;
  const state = {
    position: 0,
    direction: 1,
    dragging: false,
    pointerId: null,
    lastFrame: performance.now(),
    frame: 0,
  };

  function pathScale() {
    return path.getBoundingClientRect().width / path.offsetWidth || 1;
  }
  function maxPosition() {
    return Math.max(0, path.clientWidth - globe.offsetWidth - 4);
  }
  function renderPosition() {
    const max = maxPosition();
    state.position = Math.max(0, Math.min(max, state.position));
    globe.style.transform = `translateX(${state.position}px)`;
    globe.setAttribute("aria-valuenow", max ? String(Math.round(state.position / max * 100)) : "0");
  }
  function animate(now) {
    const max = maxPosition();
    if (!state.dragging && max > 0) {
      const delta = Math.min(40, now - state.lastFrame);
      state.position += state.direction * delta * max / 2000;
      if (state.position >= max) { state.position = max; state.direction = -1; }
      if (state.position <= 0) { state.position = 0; state.direction = 1; }
      renderPosition();
    }
    state.lastFrame = now;
    if (!box.classList.contains("hidden")) state.frame = requestAnimationFrame(animate);
  }
  function beginDrag(event) {
    if (event.button != null && event.button !== 0) return;
    state.dragging = true;
    state.pointerId = event.pointerId;
    state.lastClientX = event.clientX;
    globe.classList.add("is-dragging");
    path.classList.add("is-dragging");
    try { globe.setPointerCapture?.(event.pointerId); } catch (_) { /* 合成事件或旧浏览器无需捕获 */ }
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
    const max = maxPosition();
    state.direction = state.position >= max / 2 ? -1 : 1;
    state.dragging = false;
    state.pointerId = null;
    globe.classList.remove("is-dragging");
    path.classList.remove("is-dragging");
    try { globe.releasePointerCapture?.(event.pointerId); } catch (_) { /* 未捕获时无需释放 */ }
  }
  function moveByKeyboard(event) {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    state.position += event.key === "ArrowRight" ? 14 : -14;
    state.direction = event.key === "ArrowRight" ? 1 : -1;
    renderPosition();
    event.preventDefault();
  }
  globe.addEventListener("pointerdown", beginDrag);
  globe.addEventListener("pointermove", moveDrag);
  globe.addEventListener("pointerup", endDrag);
  globe.addEventListener("pointercancel", endDrag);
  globe.addEventListener("keydown", moveByKeyboard);
  renderPosition();
  state.frame = requestAnimationFrame(animate);
  return () => {
    cancelAnimationFrame(state.frame);
    globe.removeEventListener("pointerdown", beginDrag);
    globe.removeEventListener("pointermove", moveDrag);
    globe.removeEventListener("pointerup", endDrag);
    globe.removeEventListener("pointercancel", endDrag);
    globe.removeEventListener("keydown", moveByKeyboard);
    globe.classList.remove("is-dragging");
    path.classList.remove("is-dragging");
  };
}

function startProgress(kind, label, context) {
  if (activeProgress.has(kind)) stopProgress(kind);
  const isFission = kind === "fission";
  const box = isFission ? el.fissionProgress : el.dedupProgress;
  const labelNode = isFission ? el.fissionProgressLabel : el.dedupProgressLabel;
  const timeNode = isFission ? el.fissionProgressTime : el.dedupProgressTime;
  const estimate = estimateProgress(kind, context || {});
  const startedAt = Date.now();
  labelNode.textContent = label;
  timeNode.textContent = "预计剩余 " + formatDuration(estimate.seconds);
  timeNode.title = estimate.calibrated ? "已参考本机近期同类任务耗时" : "暂无历史样本，使用素材与参数估算";
  box.classList.remove("hidden");
  const cleanupOrb = setupOrbInteraction(box);
  const timer = setInterval(() => {
    const elapsed = (Date.now() - startedAt) / 1000;
    const remaining = estimate.seconds - elapsed;
    timeNode.textContent = remaining > 0 ? "预计剩余 " + formatDuration(remaining) : "已超预计，仍在处理";
    if (elapsed >= estimate.seconds * 0.78) {
      labelNode.textContent = isFission ? "正在完成变体并计算距离矩阵" : "正在完成输出并执行五项自检";
    }
  }, 500);
  activeProgress.set(kind, { box, timer, startedAt, units: estimate.units, overhead: estimate.overhead, cleanupOrb });
}

function learnProgressDuration(kind) {
  const current = activeProgress.get(kind);
  if (!current || !current.units) return;
  const elapsed = Math.max(1, (Date.now() - current.startedAt) / 1000);
  const rate = Math.max(0.01, elapsed - current.overhead) / current.units;
  if (!Number.isFinite(rate) || rate <= 0 || rate > 60) return;
  const history = readProgressHistory();
  const samples = Array.isArray(history[kind]) ? history[kind] : [];
  history[kind] = samples.concat({ rate, at: Date.now() }).slice(-PROGRESS_SAMPLE_LIMIT);
  try { localStorage.setItem(PROGRESS_HISTORY_KEY, JSON.stringify(history)); } catch (_) { /* 本地存储不可用时跳过校准 */ }
}

function stopProgress(kind) {
  const current = activeProgress.get(kind);
  if (!current) return;
  clearInterval(current.timer);
  if (typeof current.cleanupOrb === "function") current.cleanupOrb();
  current.box.classList.add("hidden");
  activeProgress.delete(kind);
}

function setWorkflowStep(step, failedStep = null) {
  currentWorkflowStep = Math.max(1, Math.min(4, step));
  if (!el.workflowSteps) return;
  el.workflowSteps.querySelectorAll(".workflow-step").forEach((node) => {
    const n = Number(node.getAttribute("data-step"));
    node.classList.toggle("is-complete", n < currentWorkflowStep);
    node.classList.toggle("is-current", n === currentWorkflowStep && failedStep !== n);
    node.classList.toggle("is-failed", failedStep === n);
    if (n === currentWorkflowStep && failedStep !== n) node.setAttribute("aria-current", "step");
    else node.removeAttribute("aria-current");
  });
}

function setServiceState(state, text) {
  if (!el.badgeStatus) return;
  el.badgeStatus.classList.toggle("is-connected", state === "connected");
  el.badgeStatus.classList.toggle("is-error", state === "error");
  const label = el.badgeStatus.querySelector(".service-state-text");
  if (label) label.textContent = text;
}

function resetResultsForAssetChange() {
  lastProbedAsset = null;
  lastProbeInfo = null;
  dedupDeliveryReady = false;
  currentDedupArtifact = null;
  currentFissionArtifacts = [];
  selectedFissionArtifact = null;
  el.btnDeliver.disabled = true;
  el.btnOpenOutput.classList.add("is-disabled");
  el.btnOpenOutput.setAttribute("aria-disabled", "true");
  { const _c = el.btnOpenOutput.querySelector(".dl-toggle-input"); if (_c) _c.checked = false; }
  el.btnOpenOutputTop.disabled = true;
  el.btnOpenOutputFission.disabled = true;
  el.probeCard.classList.add("hidden");
  el.dedupCard.classList.add("hidden");
  el.fissionCard.classList.add("hidden");
  setWorkflowStep(1);
}

function requireProbedAsset(src) {
  if (lastProbedAsset === src) return true;
  toast("请先探测当前素材，再开始生成。", "warn");
  setWorkflowStep(1, 1);
  el.btnProbe.focus();
  return false;
}

/* ---------------------------------------------------------------------------
   JSON-RPC / MCP 传输
   --------------------------------------------------------------------------- */
let _rpcId = 0;

async function rpc(method, params) {
  const body = { jsonrpc: "2.0", id: ++_rpcId, method };
  if (params !== undefined) body.params = params;
  const resp = await fetch(MCP_URL, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      // 无状态协议：每请求携带协议版本头（SEP-2243 路由 / SEP-2575）
      "MCP-Protocol-Version": "2026-07-28",
      "Mcp-Method": method,
    },
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const data = await resp.json();
  if (data.error) throw new Error(data.error.message || "RPC error");
  return data.result;
}

/* tools/call 封装。返回归一化结构：
   - {kind:"input_required", requestState, message}
   - {kind:"ok", data}          data 为解析后的结果对象（或原始文本）
   - {kind:"text", text, isError} 无法解析为 JSON 的纯文本（取消 / 拦截 / 错误） */
async function callTool(name, args, opts = {}) {
  const params = { name, arguments: args };
  if (opts.confirmed) {
    params.inputResponses = { confirm: true };
    if (opts.requestState) params.requestState = opts.requestState;
  }
  const result = await rpc("tools/call", params);

  if (result && result.resultType === "input_required") {
    const conf = (result.inputRequests && result.inputRequests.confirm) || {};
    return { kind: "input_required", requestState: result.requestState, message: conf.message || "需要人工确认。" };
  }

  const text = result && result.content && result.content[0] ? (result.content[0].text || "") : "";
  const isError = !!(result && result.isError);
  if (isError) return { kind: "text", text, isError: true };
  // 正常结果：content[0].text 是结果 JSON 字符串
  try {
    return { kind: "ok", data: JSON.parse(text) };
  } catch (_) {
    // 非 JSON（如"用户取消了 xxx"、被 Hook 拦截提示）
    return { kind: "text", text, isError: false };
  }
}

/* 走完整人工决策流的工具调用：首次触发 input_required 时弹模态框，
   用户确认后带 inputResponses 重发。返回最终归一化结果或 {kind:"cancelled"}。 */
async function callToolWithConfirm(name, args, onExecute) {
  let res = await callTool(name, args);
  if (res.kind !== "input_required") return res;

  const ok = await showDecisionModal(name, args, res.message);
  if (!ok) {
    addMemory(name, "cancel", "人工在决策点取消了该操作。");
    toast("已取消：未执行 " + name, "warn");
    return { kind: "cancelled" };
  }
  addMemory(name, "human", "人工确认决策点：批准执行。");
  if (typeof onExecute === "function") onExecute();
  res = await callTool(name, args, { confirmed: true, requestState: res.requestState });
  return res;
}

function newFissionTaskId() {
  if (globalThis.crypto && typeof globalThis.crypto.randomUUID === "function") {
    return "fission_" + globalThis.crypto.randomUUID().replace(/-/g, "");
  }
  return "fission_" + Date.now().toString(36) + Math.random().toString(36).slice(2);
}

async function cancelFission() {
  if (!activeFissionTaskId || fissionCancelPending) return;
  fissionCancelPending = true;
  el.btnCancelFission.disabled = true;
  el.btnCancelFission.textContent = "正在取消...";
  el.fissionProgressLabel.textContent = "正在停止当前处理";
  try {
    const resp = await fetch(CANCEL_FISSION_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task_id: activeFissionTaskId }),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok || data.ok !== true) throw new Error(data.message || `HTTP ${resp.status}`);
    if (data.found) {
      addMemory("batch_fission", "cancel", "人工取消运行中的批量裂变，正在停止当前处理。");
      toast("已发送取消请求，正在停止当前处理。", "warn");
    } else {
      // 极快点击时，取消请求可能早于后端登记任务。恢复按钮，允许再次取消；
      // 若任务确已结束，主请求的 finally 也会立即收起整个进度区。
      fissionCancelPending = false;
      el.btnCancelFission.disabled = false;
      el.btnCancelFission.textContent = "取消裂变";
      el.fissionProgressLabel.textContent = "任务仍在启动，可再次取消";
      toast("任务仍在启动或已经结束；如仍在处理，可再次点击取消。", "warn");
    }
  } catch (e) {
    fissionCancelPending = false;
    el.btnCancelFission.disabled = false;
    el.btnCancelFission.textContent = "取消裂变";
    toast("取消裂变失败：" + (e.message || e), "err");
  }
}

function downloadArtifact(filename) {
  if (!filename) { toast("请先生成产物。", "warn"); return; }
  const url = DOWNLOAD_BASE + encodeURIComponent(filename);
  // 创建隐藏 a 标签触发下载
  const a = document.createElement("a");
  a.href = url;
  a.download = filename.split("/").pop();
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  toast("开始下载：" + filename.split("/").pop());
  addMemory("download", "human", "下载产物文件：" + filename);
}

/* ---------------------------------------------------------------------------
   连接检查 + 引导
   --------------------------------------------------------------------------- */
function showConnError(msg) {
  el.connBanner.classList.remove("hidden", "ok");
  el.connText.textContent = msg || "无法连接 MCP Server，请检查后端是否运行并刷新页面";
  el.connRetry.classList.remove("hidden");
  setServiceState("error", "连接失败");
}
function showConnOk(info) {
  el.connBanner.classList.remove("hidden");
  el.connBanner.classList.add("ok");
  const name = (info && info.serverInfo && info.serverInfo.name) || "video-dedup-station";
  const proto = (info && info.protocolVersion) || "2026-07-28";
  el.connText.textContent = `已连接 ${name} · 协议 ${proto} · 无状态在线`;
  el.connRetry.classList.add("hidden");
  setServiceState("connected", "已连接");
  el.serverName.textContent = name;
  el.serverProto.textContent = "MCP " + proto;
  // 3 秒后淡出连接横幅（保持界面干净）
  setTimeout(() => el.connBanner.classList.add("hidden"), 3000);
}

async function connectAndBootstrap() {
  el.connBanner.classList.remove("hidden", "ok");
  el.connText.textContent = "正在连接 MCP Server…";
  setServiceState("connecting", "连接中");
  el.connRetry.classList.add("hidden");
  try {
    const info = await rpc("server/discover");
    showConnOk(info);
  } catch (e) {
    showConnError("无法连接 MCP Server。请确认服务已启动，可尝试刷新页面重试。");
    // 连接失败时把清单/素材区标为不可用
    el.whitelist.innerHTML = '<span class="wl-hint">未连接，无法拉取工具白名单</span>';
    renderTools([]);
    el.toolsList.innerHTML = '<div class="loading">未连接 Server，无法加载工具清单。</div>';
    el.toolDetail.innerHTML = "<strong>服务未连接</strong><p>恢复连接后会自动加载能力清单。</p>";
    el.assetSelect.innerHTML = '<option value="">未连接 Server</option>';
    return;
  }
  // 连接成功 → 并行拉工具清单与素材
  await Promise.all([loadTools(), loadAssets()]);
}

/* ---------------------------------------------------------------------------
   工具清单（tools/list）+ 权限白名单
   --------------------------------------------------------------------------- */
async function loadTools() {
  el.toolsList.innerHTML = '<div class="loading">加载工具中…</div>';
  try {
    const result = await rpc("tools/list");
    const tools = (result && result.tools) || [];
    renderTools(tools);
    renderWhitelist(tools);
  } catch (e) {
    renderTools([]);
    el.toolsList.innerHTML = '<div class="loading">工具清单加载失败：' + escapeHtml(e.message) + "</div>";
    el.toolDetail.innerHTML = "<strong>工具清单加载失败</strong><p>请检查服务连接后重试。</p>";
  }
}

let currentTools = [];
let activeToolFilter = "all";

function renderTools(tools) {
  currentTools = tools
    .map((tool, sourceIndex) => ({ ...tool, tier: tierOf(tool.name), sourceIndex }))
    .sort((a, b) => TIER_ORDER.indexOf(a.tier) - TIER_ORDER.indexOf(b.tier) || a.sourceIndex - b.sourceIndex);
  if (!currentTools.length) {
    el.toolsCount.textContent = "0 个工具";
    el.toolsList.innerHTML = '<div class="loading">Server 未暴露任何工具。</div>';
    el.toolDetail.innerHTML = "<strong>暂无服务能力</strong><p>当前 Server 没有返回可调用工具。</p>";
    renderToolAxis([]);
    updateToolFilters();
    return;
  }
  el.toolsList.innerHTML = currentTools.map((tool, index) => {
    const colors = TOOL_AXIS_COLORS[tool.tier];
    return '<article class="tool-axis-item" data-tool-id="' + index + '" data-tool-tier="' + tool.tier + '" tabindex="0" ' +
      'style="--tool-tier:' + colors.stroke + ";--tool-fill:" + colors.fill + '">' +
      '<span class="tool-axis-index">' + (index + 1) + "</span>" +
      '<div><div class="tool-name">' + escapeHtml(toolLabel(tool.name)) + '</div><div class="tool-desc">' + escapeHtml(tool.description || "暂无说明") + "</div></div>" +
      '<span class="tool-axis-tag">' + TIER_LABEL[tool.tier] + "</span>" +
    "</article>";
  }).join("");
  renderToolAxis(currentTools);
  updateToolFilters();
  if (activeToolFilter !== "all" && !currentTools.some((tool) => tool.tier === activeToolFilter)) activeToolFilter = "all";
  applyToolFilter(activeToolFilter, false);
}

function renderToolAxis(tools) {
  const svg = el.toolAxis;
  svg.replaceChildren();
  const title = toolAxisNode("title");
  title.textContent = "工具与安全分级右半圆";
  svg.appendChild(title);
  const desc = toolAxisNode("desc");
  desc.textContent = tools.length ? tools.length + " 个工具按安全等级着色并等宽排列。" : "当前没有可展示工具。";
  svg.appendChild(desc);
  const center = { x: 17, y: 165 };
  const outer = 140;
  const bandOuter = 128;
  const bandInner = 91;
  const labelRadius = 109;
  const point = (radius, ratio) => {
    const angle = -Math.PI / 2 + ratio * Math.PI;
    return { x: center.x + radius * Math.cos(angle), y: center.y + radius * Math.sin(angle) };
  };
  const arcPath = (radius) => {
    const start = point(radius, 0);
    const end = point(radius, 1);
    return "M " + start.x + " " + start.y + " A " + radius + " " + radius + " 0 0 1 " + end.x + " " + end.y;
  };
  const bandPath = (from, to) => {
    const a = point(bandOuter, from);
    const b = point(bandOuter, to);
    const c = point(bandInner, to);
    const d = point(bandInner, from);
    return "M " + a.x + " " + a.y + " A " + bandOuter + " " + bandOuter + " 0 0 1 " + b.x + " " + b.y +
      " L " + c.x + " " + c.y + " A " + bandInner + " " + bandInner + " 0 0 0 " + d.x + " " + d.y + " Z";
  };
  svg.appendChild(toolAxisNode("path", { d: arcPath(outer), class: "tool-axis-track" }));
  svg.appendChild(toolAxisNode("path", { d: arcPath(66), class: "tool-axis-guide" }));
  if (!tools.length) {
    const empty = toolAxisNode("text", { x: 81, y: 168, class: "tool-axis-center" });
    empty.textContent = "等待工具清单";
    svg.appendChild(empty);
    return;
  }
  tools.forEach((tool, index) => {
    const from = index / tools.length;
    const to = (index + 1) / tools.length;
    const colors = TOOL_AXIS_COLORS[tool.tier];
    const boundary = point(bandInner, from);
    svg.appendChild(toolAxisNode("line", { x1: center.x, y1: center.y, x2: boundary.x, y2: boundary.y, class: "tool-axis-boundary" }));
    svg.appendChild(toolAxisNode("path", {
      d: bandPath(from, to), fill: colors.fill, stroke: colors.stroke, "stroke-width": 1,
      class: "tool-axis-segment", tabindex: 0, "data-tool-id": index, "data-tool-tier": tool.tier,
      "aria-label": (index + 1) + "，" + toolLabel(tool.name) + "，" + TIER_LABEL[tool.tier],
    }));
    const labelPoint = point(labelRadius, (from + to) / 2);
    const number = toolAxisNode("text", { x: labelPoint.x, y: labelPoint.y + 3, fill: colors.stroke, class: "tool-axis-number", "data-tool-id": index, "data-tool-tier": tool.tier });
    number.textContent = index + 1;
    svg.appendChild(number);
  });
  const end = point(bandInner, 1);
  svg.appendChild(toolAxisNode("line", { x1: center.x, y1: center.y, x2: end.x, y2: end.y, class: "tool-axis-boundary" }));
  svg.appendChild(toolAxisNode("circle", { cx: center.x, cy: center.y, r: 3, fill: "#64748B" }));
  const centerTitle = toolAxisNode("text", { x: 79, y: 160, class: "tool-axis-center" });
  centerTitle.textContent = "全部工具";
  svg.appendChild(centerTitle);
  const centerCount = toolAxisNode("text", { x: 79, y: 174, class: "tool-axis-center tool-axis-center-count" });
  centerCount.textContent = tools.length + " 个";
  svg.appendChild(centerCount);
  let start = 0;
  TIER_ORDER.forEach((tier) => {
    const count = tools.filter((tool) => tool.tier === tier).length;
    if (!count) return;
    const ratio = (start + count / 2) / tools.length;
    const p = point(151, ratio);
    const label = toolAxisNode("text", { x: p.x, y: p.y + 3, fill: TOOL_AXIS_COLORS[tier].stroke, class: "tool-axis-group" });
    label.textContent = TIER_LABEL[tier] + " · " + count;
    svg.appendChild(label);
    start += count;
  });
}

function toolAxisNode(tag, attrs = {}) {
  const node = document.createElementNS(MEMORY_ARC_NS, tag);
  Object.entries(attrs).forEach(([name, value]) => node.setAttribute(name, value));
  return node;
}

function updateToolFilters() {
  const counts = { all: currentTools.length, audit: 0, warned: 0, blocked: 0, pass: 0 };
  currentTools.forEach((tool) => { counts[tool.tier] += 1; });
  el.toolFilters.querySelectorAll("[data-tool-filter]").forEach((button) => {
    const tier = button.getAttribute("data-tool-filter");
    button.textContent = (tier === "all" ? "全部" : TIER_LABEL[tier]) + " " + counts[tier];
    button.hidden = tier !== "all" && counts[tier] === 0;
  });
}

function applyToolFilter(tier, focus = true) {
  activeToolFilter = tier;
  const visible = currentTools.filter((tool) => tier === "all" || tool.tier === tier);
  el.toolsCount.textContent = visible.length === currentTools.length ? currentTools.length + " 个工具" : visible.length + " / " + currentTools.length + " 个";
  el.toolFilters.querySelectorAll("[data-tool-filter]").forEach((button) => {
    const active = button.getAttribute("data-tool-filter") === tier;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
  });
  document.querySelectorAll("[data-tool-tier]").forEach((node) => {
    const hidden = tier !== "all" && node.getAttribute("data-tool-tier") !== tier;
    if (node.classList.contains("tool-axis-item")) node.classList.toggle("hidden", hidden);
    else node.classList.toggle("is-dimmed", hidden);
    node.classList.remove("is-active");
  });
  el.toolAxis.querySelectorAll(".tool-axis-center").forEach((node, index) => {
    node.textContent = index === 0 ? (tier === "all" ? "全部工具" : TIER_LABEL[tier]) : visible.length + " 个";
  });
  el.toolDetail.innerHTML = '<strong>' + (tier === "all" ? "全部工具" : TIER_LABEL[tier]) + " · " + visible.length + "</strong><p>点击半圆色段或工具列表，可查看调用边界并筛选上方验收记录。</p>";
  if (focus) el.toolAxis.focus({ preventScroll: true });
}

function selectTool(toolId) {
  const index = Number(toolId);
  const tool = currentTools[index];
  if (!tool) return;
  document.querySelectorAll("[data-tool-id]").forEach((node) => {
    const active = Number(node.getAttribute("data-tool-id")) === index;
    node.classList.toggle("is-active", active);
    if (node.classList.contains("tool-axis-segment") || node.classList.contains("tool-axis-number")) node.classList.toggle("is-dimmed", !active);
  });
  const colors = TOOL_AXIS_COLORS[tool.tier];
  el.toolDetail.innerHTML = '<div class="tool-detail-title"><span class="tool-axis-index" style="--tool-tier:' + colors.stroke + ";--tool-fill:" + colors.fill + '">' + (index + 1) + "</span>" + escapeHtml(tool.name) + " · " + TIER_LABEL[tool.tier] + '</div><p>' + escapeHtml(tool.description || "暂无说明") + "<br>" + TOOL_CONFIRMATION[tool.tier] + "<br>已联动筛选上方验收记录。</p>";
  el.memorySearch.value = tool.name;
  renderMemory();
  const item = el.toolsList.querySelector('[data-tool-id="' + index + '"]');
  if (item) item.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

function renderWhitelist(tools) {
  if (!tools.length) {
    el.whitelist.innerHTML = '<span class="wl-hint">无</span>';
    return;
  }
  el.whitelist.innerHTML = "";
  tools.forEach((t) => {
    const tag = document.createElement("span");
    tag.className = "wl-tag";
    tag.textContent = t.name;
    tag.title = t.description || "";
    el.whitelist.appendChild(tag);
  });
}

/* ---------------------------------------------------------------------------
   素材列表（list_assets）
   --------------------------------------------------------------------------- */
async function loadAssets() {
  el.assetSelect.innerHTML = '<option value="">加载素材中…</option>';
  const res = await callTool("list_assets", {});
  if (res.kind !== "ok") {
    el.assetSelect.innerHTML = '<option value="">素材加载失败</option>';
    return;
  }
  const assets = (res.data && res.data.assets) || [];
  if (!assets.length) {
    el.assetSelect.innerHTML = '<option value="">input/ 下暂无素材，请先放入视频</option>';
    return;
  }
  el.assetSelect.innerHTML = "";
  assets.forEach((a) => {
    const opt = document.createElement("option");
    opt.value = a.name;
    opt.textContent = a.name + "  (" + (a.size_mb != null ? a.size_mb + " MB" : "?") + ")";
    el.assetSelect.appendChild(opt);
  });
}

/* ---------------------------------------------------------------------------
   文件上传
   --------------------------------------------------------------------------- */
function setupUpload() {
  const zone = el.uploadZone;
  const input = el.fileUpload;

  // 点击选择
  input.addEventListener("change", () => {
    if (input.files.length) uploadFile(input.files[0]);
  });

  // 拖拽
  zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("drag-over"); });
  zone.addEventListener("dragleave", () => { zone.classList.remove("drag-over"); });
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    zone.classList.remove("drag-over");
    const f = e.dataTransfer.files[0];
    if (f) uploadFile(f);
  });
}

async function uploadFile(file) {
  if (!file || !file.type.startsWith("video/")) {
    toast("请选择视频文件。", "warn");
    return;
  }

  el.uploadProgress.classList.remove("hidden");
  el.fileUpload.disabled = true;
  el.uploadZone.style.pointerEvents = "none";

  const formData = new FormData();
  formData.append("file", file);

  try {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", UPLOAD_URL);

    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) {
        const pct = Math.round(e.loaded / e.total * 100);
        el.progressFill.style.width = pct + "%";
        el.progressText.textContent = `上传中 ${pct}% (${(e.loaded/1024/1024).toFixed(1)} MB / ${(e.total/1024/1024).toFixed(1)} MB)`;
      }
    };

    await new Promise((resolve, reject) => {
      xhr.onload = () => {
        if (xhr.status === 200) {
          try { resolve(JSON.parse(xhr.responseText)); } catch (_) { reject(new Error("响应格式错误")); }
        } else {
          try { const e = JSON.parse(xhr.responseText); reject(new Error(e.message)); } catch (_) { reject(new Error("上传失败 " + xhr.status)); }
        }
      };
      xhr.onerror = () => reject(new Error("网络错误"));
      xhr.send(formData);
    });

    toast("上传成功：" + file.name);
    // 刷新素材列表，自动选中并探测
    await loadAssets();
    el.assetSelect.value = file.name;
    // 自动触发探测
    if (file.name) doProbe();
  } catch (err) {
    toast("上传失败：" + err.message, "err");
  } finally {
    el.uploadProgress.classList.add("hidden");
    el.progressFill.style.width = "0%";
    el.fileUpload.disabled = false;
    el.fileUpload.value = "";
    el.uploadZone.style.pointerEvents = "";
  }
}

function currentAsset() {
  const v = el.assetSelect.value;
  if (!v) toast("请先选择一个素材。", "warn");
  return v;
}

/* ---------------------------------------------------------------------------
   探测（probe_video）
   --------------------------------------------------------------------------- */
async function doProbe() {
  const src = currentAsset();
  if (!src) return;
  withBusy(el.btnProbe, "探测素材", async () => {
    const res = await callTool("probe_video", { src });
    if (res.kind === "text") {
      setWorkflowStep(1, 1);
      addMemory("probe_video", "error", res.text);
      toast("探测失败：" + res.text, "err");
      return;
    }
    if (res.kind !== "ok") return;
    const p = res.data;
    lastProbedAsset = src;
    lastProbeInfo = p;
    setWorkflowStep(2);
    renderProbe(p);
    addMemory("probe_video", "audit",
      `${p.name} · ${p.width}×${p.height} · ${p.fps}fps · ${p.duration}s · MD5 ${short(p.md5)}`);
  });
}

function renderProbe(p) {
  const cells = [
    ["文件名", p.name],
    ["分辨率", (p.width || "?") + " × " + (p.height || "?")],
    ["帧率", (p.fps != null ? p.fps : "?") + " fps"],
    ["时长", (p.duration != null ? p.duration : "?") + " s"],
    ["视频编码", p.video_codec || "?"],
    ["音频编码", p.audio_codec || "—"],
    ["字幕轨道", p.has_subtitle ? ("内嵌 · " + (p.subtitle_codec || "?")) : "无"],
    ["码率", p.bit_rate ? Math.round(p.bit_rate / 1000) + " kbps" : "?"],
    ["体积", (p.size_mb != null ? p.size_mb : "?") + " MB"],
    ["MD5", p.md5 || "?"],
  ];
  el.probeGrid.innerHTML = cells.map(([k, v]) =>
    '<div class="probe-cell"><div class="probe-key">' + escapeHtml(k) +
    '</div><div class="probe-val">' + escapeHtml(String(v)) + "</div></div>"
  ).join("");
  el.probeCard.classList.remove("hidden");
}

/* ---------------------------------------------------------------------------
   参数控件读取（F4.1）：强度档 + 维度勾选 + flip_mode
   --------------------------------------------------------------------------- */
const DIM_KEYS = ["picture", "rotate", "crop", "speed", "trim", "flip"];

function readDimensions() {
  // 强度档
  const activeBtn = el.levelSeg.querySelector(".seg-btn.active");
  const level = activeBtn ? activeBtn.getAttribute("data-level") : "medium";
  // 维度
  const dimensions = {};
  el.dimGrid.querySelectorAll("input[data-dim]").forEach((inp) => {
    dimensions[inp.getAttribute("data-dim")] = !!inp.checked;
  });
  // flip_mode（仅 flip 开时启用并带回传）
  const flipOn = !!dimensions.flip;
  const flip_mode = flipOn ? (el.flipMode.value || "h") : null;
  return { level, dimensions, flip_mode };
}

function anyDimOn(dims) {
  return DIM_KEYS.some((k) => dims[k] === true);
}

function syncFlipModeState() {
  const flipOn = !!el.dimGrid.querySelector('input[data-dim="flip"]').checked;
  el.flipMode.disabled = !flipOn;
  el.flipModeRow.classList.toggle("is-active", flipOn);
}

/* ---------------------------------------------------------------------------
   去重（dedup_video）—— 走人工决策流 + 自检报告
   --------------------------------------------------------------------------- */
/** 🆕 读取 TTS 参数（自动模式=提取+改写，手动模式=用户文案） */
function readTTS() {
  const modeBtn = document.querySelector("#tts-mode-switch .tts-mode-btn.active");
  const isAuto = modeBtn && modeBtn.getAttribute("data-mode") === "auto";
  const elVoice = document.getElementById("tts-voice");
  const elSpeed = document.getElementById("tts-speed");

  if (isAuto) {
    // 自动模式：checkbox 控制是否启用改写
    var chkRewrite = document.getElementById("chk-rewrite");
    var enabled = chkRewrite && chkRewrite.checked;
    var template = "";
    if (enabled) {
      var elTemplate = document.getElementById("tts-template");
      template = elTemplate ? elTemplate.value.trim() : "";
    }
    return {
      tts_text: null,
      rewrite_template: template,  // 空 = 不改写，非空 =元宝改写
      rewrite_topic: (document.getElementById("tts-topic") || {}).value || "",
      tts_voice: elVoice ? elVoice.value : "冰糖",
      tts_speed: elSpeed ? parseFloat(elSpeed.value) || 1.0 : 1.0,
    };
  } else {
    // 手动模式：必须用户填了文案才启用 TTS
    const elText = document.getElementById("tts-text");
    const text = (elText && elText.value || "").trim();
    if (!text) return null;
    return {
      tts_text: text,
      tts_voice: elVoice ? elVoice.value : "冰糖",
      tts_speed: elSpeed ? parseFloat(elSpeed.value) || 1.0 : 1.0,
    };
  }
}

/**
 * TTS 配音未生效时，弹出原生对话框提示用户（桌面端）；
 * 非桌面端（纯网页）降级为页内 toast。
 * 触发条件：用户「启用了 TTS」但产物没有成功配音。两种意图都算“启用”：
 *   - 手动填文案：args.tts_text 有值，但产物 applied.tts_applied 为 false → 展示失败原因。
 *   - 改写+配音：args.rewrite_template 有值，但产物无 tts_applied →
 *     改写失败（元宝未登录/超时）或改写成功但 TTS 混音失败。
 * @param {object} args 本次请求透传的 TTS 参数（含 tts_text / rewrite_template）
 * @param {object} applied 产物 applied_params（用于取失败原因）
 * @param {string} label 场景标签（去重 / 裂变）
 */
function notifyTtsFailure(args, applied, label) {
  args = args || {};
  applied = applied || {};
  const isAuto = !args.tts_text && !!args.rewrite_template;
  const title = "AI 配音（TTS）未生效";
  let message;
  let detail;
  if (isAuto) {
    const rewriteErr = applied.rewrite_error;
    if (applied.tts_text && !applied.tts_applied) {
      // 改写成功，但配音混音失败
      const warning = (applied.tts_warning || "请检查 MIMO_API_KEY / openai 是否配置").trim();
      message = (label ? label + "：" : "") + "改写文案已生成，但配音未替换进产物。";
      detail =
        "改写来源：" + (applied.rewrite_source || "元宝改写") + "\n" +
        "失败原因：" + warning + "\n\n" +
        "排查：检查 station/server/.env 的 MIMO_API_KEY 是否正确、openai 库是否已安装（pip install openai）。";
    } else if (rewriteErr) {
      // 改写本身失败（元宝未登录 / 超时 / 未返回）
      message = (label ? label + "：" : "") + "「改写 + 配音」模式未生成配音：改写未返回文案。";
      detail =
        "改写错误：" + rewriteErr + "\n\n" +
        "可用方案：\n" +
        "· 确认元宝已登录（检查调试 Edge 窗口是否跳登录页）；\n" +
        "· 或切到「手动填文案」模式，直接粘贴文案即可生成配音。";
    } else {
      message = (label ? label + "：" : "") + "「改写 + 配音」模式未生成配音。";
      detail =
        "未生成旁白文案，TTS 未触发。\n\n" +
        "可用方案：切到「手动填文案」模式，直接粘贴文案即可生成配音。";
    }
  } else {
    const warning = (applied.tts_warning || "请检查 MIMO_API_KEY / openai 是否配置").trim();
    const text = (args.tts_text || "").toString();
    message = (label ? label + "：" : "") + "你启用了 AI 配音，但配音没有替换进产物。";
    detail =
      "配音文案：" + (text.length > 80 ? text.slice(0, 80) + "…" : text) + "\n" +
      "失败原因：" + warning + "\n\n" +
      "排查：检查 station/server/.env 的 MIMO_API_KEY 是否正确、openai 库是否已安装（pip install openai）。";
  }
  if (window.desktop && window.desktop.showTtsWarning) {
    window.desktop.showTtsWarning({ title, message, detail });
  } else {
    toast(message, "warn");
  }
}

async function doDedup() {
  const src = currentAsset();
  if (!src || !requireProbedAsset(src)) return;
  const { level, dimensions, flip_mode } = readDimensions();
  if (!anyDimOn(dimensions)) {
    toast("请至少启用一个维度再去重。", "warn");
    return;
  }
  const args = { src, level, dimensions };
  if (flip_mode) args.flip_mode = flip_mode;
  // 🆕 TTS
  const tts = readTTS();
  if (tts) Object.assign(args, tts);
  // 🆕 确保已配置输出目录：未配置则弹系统文件夹选择并持久化；已配置直接返回
  const od = await ensureOutputDir();
  if (!od) return; // 用户取消选择输出目录 → 中止本次去重
  args.output_dir = od;
  setWorkflowStep(3);
  try {
    await withBusy(el.btnDedup, "开始单条去重", async () => {
      // orb 必须在 callToolWithConfirm 之前就显示（后者首次调用若直接返回结果则回调永不执行）
      startProgress("dedup", "正在生成单条变体并执行五项自检", {
        duration: lastProbeInfo && lastProbeInfo.duration,
        level,
        dimensions,
      });
      const res = await callToolWithConfirm("dedup_video", args);
      if (res.kind === "cancelled") {
        setWorkflowStep(2);
        return;
      }
      if (res.kind === "text") {
        setWorkflowStep(3, 3);
        addMemory("dedup_video", "error", res.text);
        toast("去重失败：" + res.text, "err");
        return;
      }
      if (res.kind !== "ok") return;
      learnProgressDuration("dedup");
      setWorkflowStep(4);
      renderDedup(res.data);
      const _ap = res.data.applied_params || {};
      // 用户启用了 TTS（手动文案或改写模式）但产物未成功配音 → 原生弹窗提示
      const _ttsRequested = !!(args.tts_text) || !!(args.rewrite_template);
      if (_ttsRequested && !_ap.tts_applied) {
        notifyTtsFailure(args, _ap, "去重");
      }
      const c = res.data.checks || {};
      const ph = c.phash || {};
      addMemory("dedup_video", "warned",
        `去重完成 → ${baseName(res.data.output_path)} · MD5${mark(c.md5_changed)} 分辨率${mark(c.resolution_kept)} 时长${mark(c.duration_close)} 5s${mark(c.min_duration_ok)} phash${mark(ph.passed)}`);
      if (c.all_passed === true) {
        toast("去重自检通过，请人工决策是否交付。", "ok");
      } else {
        toast("去重已生成，但自检未全部通过，当前不可交付。", "warn");
      }
    });
  } finally {
    stopProgress("dedup");
  }
}

function renderDedup(d) {
  const c = d.checks || {};
  dedupDeliveryReady = c.all_passed === true;
  el.btnDeliver.disabled = !dedupDeliveryReady;
  el.btnDeliver.title = dedupDeliveryReady ? "" : "五项自检全部通过后才可确认交付";
  setCheck(el.chkMd5, c.md5_changed);
  setCheck(el.chkRes, c.resolution_kept);

  // 时长合规：动态标签（TTS 生效时成片=配音时长，比原视频短是预期，按中性说明而非报错）
  const srcDur = (d.src && d.src.duration) || 0;
  const outDur = (d.output && d.output.duration) || 0;
  const ap = d.applied_params || {};
  const ttsOn = !!(ap.tts_applied && (ap.tts_duration || outDur));
  // 原视频 - 成片 = 缩短量；TTS 场景下为正且属正常（混音 -shortest 把视频截到配音长度）
  const shorten = srcDur - outDur;
  if (ttsOn) {
    if (shorten > 0.5) {
      setCheck(el.chkDur, true,
        "TTS 输出全部文案，但视频时长缩短了 " + shorten.toFixed(2) + "s（配音 " + outDur.toFixed(1) + "s）");
    } else {
      setCheck(el.chkDur, true,
        "配音时长匹配（" + outDur.toFixed(1) + "s ≈ TTS " + (ap.tts_duration || outDur).toFixed(1) + "s）");
    }
  } else if (c.duration_close === true) {
    var delta = Math.abs(outDur - srcDur);
    setCheck(el.chkDur, true, "变化处于允许范围（" + delta.toFixed(2) + "s）");
  } else {
    var delta2 = Math.abs(outDur - srcDur);
    setCheck(el.chkDur, false, "变化超出允许范围（Δ" + delta2.toFixed(2) + "s）");
  }

  setCheck(el.chkMinDur, c.min_duration_ok);

  // phash 行：avg / min / weak_frame_ratio / method
  const ph = c.phash || {};
  setCheck(el.chkPhash, ph.passed);
  const parts = [];
  if (ph.phash_avg != null) parts.push("avg " + (+ph.phash_avg).toFixed(2));
  if (ph.phash_min != null) parts.push("min " + ph.phash_min);
  if (ph.weak_frame_ratio != null) parts.push("弱帧 " + (+ph.weak_frame_ratio).toFixed(2));
  if (ph.method) parts.push(ph.method === "signature" ? "签名兜底" : ph.method);
  el.phashDetail.textContent = parts.length ? "（" + parts.join(" · ") + "）" : "";

  // phash 未达标 hint
  if (ph.passed === false) {
    const isSig = ph.method === "signature";
    const tip = isSig
      ? "签名兜底仍未通过：变体与原素材过于相似，建议启用 flip 或换 seed。"
      : "pHash 未达标：建议启用更多维度 / 提高档位 / 换 seed（avg 阈值 12，弱帧占比阈值 0.10）。";
    el.phashHint.textContent = tip;
    el.phashHint.classList.remove("hidden");
  } else {
    el.phashHint.classList.add("hidden");
  }

  currentDedupArtifact = d.output_path ? baseName(d.output_path) : null;
  if (currentDedupArtifact) {
    el.dedupArtifactName.textContent = currentDedupArtifact;
    el.dedupArtifact.title = d.output_path;
    el.dedupArtifact.classList.remove("hidden");
    el.btnOpenOutput.classList.remove("is-disabled");
    el.btnOpenOutput.setAttribute("aria-disabled", "false");
    el.btnOpenOutputTop.disabled = false;
  } else {
    el.dedupArtifact.classList.add("hidden");
    el.btnOpenOutput.classList.add("is-disabled");
    el.btnOpenOutput.setAttribute("aria-disabled", "true");
    { const _c = el.btnOpenOutput.querySelector(".dl-toggle-input"); if (_c) _c.checked = false; }
    el.btnOpenOutputTop.disabled = true;
  }

  const src = d.src || {};
  const out = d.output || {};
  const applied = d.applied_params || {};
  const trimLine = applied.trim_skipped
    ? "去头尾   : 跳过（" + (applied.trim_skip_reason || "原时长过短") + "）\n"
    : "";
  const sourceLabels = { user: "手动输入", subtitle: "字幕提取", asr: "ASR 识别", subtitle_rewrite: "字幕+改写", asr_rewrite: "ASR+改写", subtitle_fallback: "字幕(改写失败)", asr_fallback: "ASR(改写失败)" };
  const ttsProcess = applied.tts_process
    ? ("TTS 流程  : " + applied.tts_process + "\n")
    : "";
  let ttsLine = "";
  if (applied.tts_text) {
    let line = "TTS 配音 : " + (applied.tts_applied ? "[已替换]" : "[失败]") +
       " 音色=" + (applied.tts_voice || "冰糖") +
       " 文本=" + (applied.tts_text || "—");
    if (applied.rewrite_requested) {
      line += " 来源=" + (applied.rewrite_source ? "元宝改写(" + applied.rewrite_source + ")" : "元宝改写");
    }
    if (applied.tts_warning) line += " 原因=" + applied.tts_warning;
    ttsLine = line + "\n";
  } else if (applied.rewrite_requested && applied.rewrite_error) {
    ttsLine = "TTS 配音 : [改写失败] 原因=" + applied.rewrite_error + "\n";
  }
  const detail =
    "输出文件 : " + (d.output_path || "?") + "\n" +
    "源  MD5  : " + (src.md5 || "?") + "\n" +
    "新  MD5  : " + (out.md5 || "?") + "\n" +
    "分辨率   : " + (src.width || "?") + "×" + (src.height || "?") +
      "  →  " + (out.width || "?") + "×" + (out.height || "?") + "\n" +
    "时长     : " + (src.duration != null ? src.duration : "?") + "s  →  " +
      (out.duration != null ? out.duration : "?") + "s\n" +
    trimLine +
    ttsProcess +
    ttsLine +
    "帧率     : " + (d.fps != null ? d.fps : "?") + " fps\n" +
    "job_id   : " + (d.job_id || "—") + "\n" +
    "应用参数 : " + JSON.stringify(applied);
  el.dedupDetail.textContent = detail;
  el.dedupCard.classList.remove("hidden");
  el.dedupCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function setCheck(node, ok, label) {
  node.classList.remove("pass", "fail");
  const mk = node.querySelector(".check-mark");
  const sm = node.querySelector("small");
  if (ok === true) { node.classList.add("pass"); mk.innerHTML = '<svg class="ico-14" style="color:var(--audit)" aria-label="通过"><use href="#ico-checkmark"/></svg>'; }
  else if (ok === false) { node.classList.add("fail"); mk.innerHTML = '<svg class="ico-14" style="color:var(--warned)" aria-label="未通过"><use href="#ico-xmark"/></svg>'; }
  else { mk.textContent = "—"; }
  if (label !== undefined && sm) sm.textContent = label;
}

/* ---------------------------------------------------------------------------
   裂变（batch_fission）—— 走人工决策流
   --------------------------------------------------------------------------- */
async function doFission() {
  const src = currentAsset();
  if (!src || !requireProbedAsset(src)) return;
  const { level, dimensions, flip_mode } = readDimensions();
  if (!anyDimOn(dimensions)) {
    toast("请至少启用一个维度再裂变。", "warn");
    return;
  }
  let count = parseInt(el.fissionCount.value, 10);
  if (!Number.isFinite(count) || count < 1 || count > 20) {
    toast("裂变数量必须是 1 到 20 之间的整数。", "warn");
    el.fissionCount.focus();
    return;
  }
  el.fissionCount.value = count;

  const args = { src, count, level, dimensions, task_id: newFissionTaskId() };
  if (flip_mode) args.flip_mode = flip_mode;
  const tts = readTTS();
  if (tts) Object.assign(args, tts);
  setWorkflowStep(3);
  try {
    await withBusy(el.btnFission, "开始裂变", async () => {
      // orb + 取消状态必须在 callToolWithConfirm 之前就就位（后者首次调用若直接返回结果则回调永不执行）
      activeFissionTaskId = args.task_id;
      fissionCancelPending = false;
      el.btnCancelFission.disabled = false;
      el.btnCancelFission.textContent = "取消裂变";
      startProgress("fission", "正在逐个生成变体并计算距离矩阵", {
        duration: lastProbeInfo && lastProbeInfo.duration,
        count,
        level,
        dimensions,
      });
      const res = await callToolWithConfirm("batch_fission", args);
      if (res.kind === "cancelled") {
        setWorkflowStep(2);
        return;
      }
      if (res.kind === "text") {
        setWorkflowStep(3, 3);
        addMemory("batch_fission", "error", res.text);
        toast("裂变失败：" + res.text, "err");
        return;
      }
      if (res.kind !== "ok") return;
      if (!res.data.cancelled) learnProgressDuration("fission");
      setWorkflowStep(res.data.cancelled ? 3 : 4);
      renderFission(res.data);
      // 用户启用了 TTS（手动文案或改写模式）但产物未成功配音 → 原生弹窗提示
      const _ttsRequested = !!(args.tts_text) || !!(args.rewrite_template);
      if (_ttsRequested) {
        const _variants = res.data.variants || [];
        const _anyTts = _variants.some((v) => v.applied_params && v.applied_params.tts_applied);
        if (!_anyTts) {
          const _rep = (_variants.find((v) => v.applied_params && v.applied_params.tts_text) || {}).applied_params || {};
          notifyTtsFailure(args, _rep, "裂变");
        }
      }
      const allPass = res.data.matrix && res.data.matrix.all_pass;
      const deliveryReady = res.data.delivery_ready === true;
      if (res.data.cancelled) {
        addMemory("batch_fission", "cancel",
          `裂变已取消 · 保留 ${res.data.count || 0}/${res.data.requested_count || count} 个已完成产物（源：${res.data.src}）`);
        toast(`裂变已取消，保留 ${res.data.count || 0} 个已完成产物。`, "warn");
      } else {
        addMemory("batch_fission", "warned",
          `裂变 ${res.data.count} 个变体 · MD5唯一${mark(res.data.all_unique)} · 矩阵${mark(allPass)} · 交付门${mark(deliveryReady)}（源：${res.data.src}）`);
        if (deliveryReady) {
          toast("裂变完成且双门通过：生成 " + res.data.count + " 个变体。", "ok");
        } else {
          toast("裂变已生成，但 MD5 唯一性或距离矩阵未通过，当前不可交付。", "warn");
        }
      }
    });
  } finally {
    stopProgress("fission");
    activeFissionTaskId = null;
    fissionCancelPending = false;
    el.btnCancelFission.disabled = false;
    el.btnCancelFission.textContent = "取消裂变";
  }
}

function renderFission(d) {
  const cancelled = d.cancelled === true;
  const uniq = d.all_unique === true;
  const matrix = d.matrix || null;
  const allPass = !!(matrix && matrix.all_pass);
  const ssim = d.ssim || null;
  const ssimAllPass = !!(ssim && ssim.all_pass);

  // 摘要：正常完成展示三门；取消任务展示实际保留数量
  const badges = [];
  if (cancelled) {
    badges.push('<span class="fission-badge warn">任务已取消</span>');
  } else {
    badges.push('<span class="fission-badge ' + (uniq ? "" : "warn") + '">' +
      (uniq ? 'MD5 全部互不相同 <svg class="ico-14" style="color:var(--audit)"><use href="#ico-checkmark"/></svg>' : '存在重复 MD5 <svg class="ico-14" style="color:var(--warned)"><use href="#ico-xmark"/></svg>') + "</span>");
    if (matrix) {
      badges.push('<span class="fission-badge ' + (allPass ? "" : "warn") + '">' +
        (allPass ? '距离矩阵全部达标 <svg class="ico-14" style="color:var(--audit)"><use href="#ico-checkmark"/></svg>' : '存在过近对 <svg class="ico-14" style="color:var(--warned)"><use href="#ico-xmark"/></svg>') + "</span>");
    }
    if (ssim && ssim.available !== false) {
      badges.push('<span class="fission-badge ' + (ssimAllPass ? "" : "warn") + '">' +
        (ssimAllPass ? 'SSIM 全部达标 <svg class="ico-14" style="color:var(--audit)"><use href="#ico-checkmark"/></svg>' : '存在 SSIM 过近对 <svg class="ico-14" style="color:var(--warned)"><use href="#ico-xmark"/></svg>') + "</span>");
    }
  }
  const countText = cancelled
    ? "已保留 " + (d.count || 0) + " / " + (d.requested_count || 0) + " 个完整变体 "
    : "共 " + (d.count || 0) + " 个变体 ";
  el.fissionSummary.innerHTML =
    "源素材 <b>" + escapeHtml(d.src || "?") + "</b> · " + countText + badges.join(" ");

  // separation 诊断（all_pass=false 时展示卡哪条腿）
  renderSeparation(cancelled ? null : d.separation, allPass);
  renderFissionExplainer(d, allPass);

  // 距离矩阵表格
  if (matrix && Array.isArray(matrix.matrix) && matrix.count > 1) {
    renderMatrix(matrix);
    el.fissionMatrixWrap.classList.remove("hidden");
  } else {
    el.fissionMatrixWrap.classList.add("hidden");
  }
  // SSIM 矩阵表格
  if (ssim && ssim.available !== false && Array.isArray(ssim.matrix) && ssim.count > 1) {
    renderSsimMatrix(ssim);
    el.ssimMatrixWrap.classList.remove("hidden");
  } else {
    el.ssimMatrixWrap.classList.add("hidden");
  }

  const variants = d.variants || [];
  currentFissionArtifacts = variants
    .map((variant) => ({ ...variant, filename: baseName(variant.output_path) }))
    .filter((variant) => variant.filename && variant.filename !== "?");
  selectedFissionArtifact = currentFissionArtifacts.length ? currentFissionArtifacts[0].filename : null;
  el.fissionList.innerHTML = currentFissionArtifacts.map((v, index) =>
    '<button class="fission-item artifact-item' + (index === 0 ? " is-selected" : "") + '" type="button" data-artifact-name="' + escapeHtml(v.filename) + '" aria-pressed="' + (index === 0 ? "true" : "false") + '">' +
      '<span class="fission-idx">' + (v.index || index + 1) + "</span>" +
      '<span class="fission-name">' + escapeHtml(v.filename) + "</span>" +
      '<span class="fission-md5">MD5 ' + escapeHtml(short(v.md5)) + "</span>" +
    "</button>"
  ).join("");
  el.btnOpenOutputFission.disabled = !selectedFissionArtifact;
  el.fissionCard.classList.remove("hidden");
}

function renderFissionExplainer(d, allPass) {
  const matrix = d.matrix || {};
  const sep = d.separation || {};
  const tooClose = Array.isArray(matrix.too_close_pairs) ? matrix.too_close_pairs : [];
  const pairCount = tooClose.length;
  const minPair = matrix.min_pair || null;
  const minDistance = minPair && minPair.phash_avg != null ? (+minPair.phash_avg).toFixed(1) : null;
  const ready = d.delivery_ready === true;

  if (d.cancelled === true) {
    const kept = Number(d.count) || 0;
    el.fissionExplainer.innerHTML =
      '<div class="explainer-status is-warn">' +
        '<span class="explainer-status-mark" aria-hidden="true">!</span>' +
        '<div><strong>批量裂变已取消</strong><p>当前处理已停止，后续变体不会继续生成；已完成并通过单条自检的 ' + kept + ' 个产物已保留。</p></div>' +
      "</div>" +
      '<div class="explainer-grid"><div class="explainer-action"><strong>当前结果</strong><p>' +
        (kept ? "可在下方选择并定位已完成产物；取消批次未计算完整距离矩阵，因此不可直接交付。" : "本次取消前没有产生可保留的完整产物。") +
      "</p></div></div>";
    return;
  }

  let statusTitle = "这批变体可以交付";
  let statusText = "MD5 已区分文件，任意两个变体的 pHash 平均距离也都达到 12。";
  let statusClass = "is-pass";
  if (!ready) {
    statusTitle = "这批变体暂不建议交付";
    statusClass = "is-warn";
    if (!d.all_unique && !allPass) {
      statusText = "既有重复文件，也有画面过近的变体，需要重新生成。";
    } else if (!d.all_unique) {
      statusText = "至少两个输出文件内容完全相同，需要重新生成。";
    } else {
      const minNote = minDistance ? "，最低距离只有 " + minDistance : "";
      statusText = "文件指纹虽然不同，但仍有 " + pairCount + " 对画面过于相似" + minNote + "。";
    }
  }

  const actions = [];
  if (!d.all_unique) actions.push("重新生成这批变体，确保每个输出的 MD5 都不同。");
  if (!allPass) {
    if (!sep.flip_spread) actions.push("勾选“翻转/旋转”后重新裂变；系统会自动在各变体间轮换水平、垂直和旋转 90°。");
    if (sep.time_leg !== "present") actions.push("源视频可裁空间不足时，换用更长的素材，再利用不同起止点拉开时间错位。");
    else actions.push("保留时间错位，同时提高处理强度或增加画面、裁切、旋转维度后重新裂变。");
  }
  if (ready) actions.push("抽查各变体播放效果后，即可打开输出文件夹交付。");

  el.fissionExplainer.innerHTML =
    '<div class="explainer-status ' + statusClass + '">' +
      '<span class="explainer-status-mark" aria-hidden="true">' + (ready ? '<svg class="ico-14" style="color:var(--audit)"><use href="#ico-checkmark"/></svg>' : '<svg class="ico-14" style="color:var(--warned)"><use href="#ico-alert"/></svg>') + "</span>" +
      '<div><strong>' + statusTitle + '</strong><p>' + statusText + "</p></div>" +
    "</div>" +
    '<div class="explainer-grid">' +
      '<div><strong>这张表怎么看</strong><p>横纵坐标 0、1、2… 代表不同变体。交叉格数字越大，两个视频越不相似；同一个视频与自身用“—”表示。</p></div>' +
      '<div><strong>通过标准</strong><p>三重门：MD5 全部互不相同；pHash 距离矩阵 avg ≥ 12 且弱帧 ≤ 10%；SSIM 结构相似度 avg ≤ 0.92 且过近帧 ≤ 10%。任一红色格即为不达标。</p></div>' +
      '<div class="explainer-action"><strong>现在怎么做</strong><ol>' +
        actions.map((item) => "<li>" + escapeHtml(item) + "</li>").join("") +
      "</ol></div>" +
    "</div>";
}

function renderSeparation(sep, allPass) {
  if (!sep) {
    el.fissionSeparation.classList.add("hidden");
    return;
  }
  // 仅在矩阵不达标时展示 hint；达标时静默
  if (allPass || !sep.hint) {
    el.fissionSeparation.classList.add("hidden");
    return;
  }
  const legs = [];
  legs.push("时间错位：" + (sep.time_leg === "present" ? "有" : "无（trim 全部跳过）"));
  legs.push("flip 分散：" + (sep.flip_spread ? "是" : "否"));
  el.fissionSeparation.innerHTML =
    '<span class="sep-icon"><svg class="ico-14" style="color:var(--warned)"><use href="#ico-alert"/></svg></span>' +
    '<span class="sep-text">' + escapeHtml(sep.hint) + "（" + legs.join(" · ") + "）</span>";
  el.fissionSeparation.classList.remove("hidden");
}

function renderMatrix(m) {
  const n = m.count || 0;
  if (n < 2) { el.fissionMatrix.innerHTML = ""; return; }
  const tooClose = new Set();
  (m.too_close_pairs || []).forEach((p) => {
    tooClose.add((p.i < p.j ? p.i : p.j) + "-" + (p.i < p.j ? p.j : p.i));
  });
  // 表头行 + n 行
  let html = '<table class="matrix-table"><thead><tr><th></th>';
  for (let j = 0; j < n; j++) html += "<th>" + j + "</th>";
  html += "</tr></thead><tbody>";
  for (let i = 0; i < n; i++) {
    html += "<tr><th>" + i + "</th>";
    for (let j = 0; j < n; j++) {
      if (i === j) {
        html += '<td class="diag">—</td>';
      } else {
        const v = m.matrix[i] && m.matrix[i][j] != null ? m.matrix[i][j] : null;
        const a = Math.min(i, j), b = Math.max(i, j);
        const close = tooClose.has(a + "-" + b);
        const cls = v == null ? "" : (close ? "warn" : "ok");
        const txt = v == null ? "?" : (+v).toFixed(1);
        html += '<td class="' + cls + '">' + escapeHtml(txt) + "</td>";
      }
    }
    html += "</tr>";
  }
  html += "</tbody></table>";
  el.fissionMatrix.innerHTML = html;
}

function renderSsimMatrix(m) {
  const n = m.count || 0;
  if (n < 2) { el.ssimMatrix.innerHTML = ""; return; }
  const tooClose = new Set();
  (m.too_close_pairs || []).forEach((p) => {
    tooClose.add((p.i < p.j ? p.i : p.j) + "-" + (p.i < p.j ? p.j : p.i));
  });
  let html = '<table class="matrix-table"><thead><tr><th></th>';
  for (let j = 0; j < n; j++) html += "<th>" + j + "</th>";
  html += "</tr></thead><tbody>";
  for (let i = 0; i < n; i++) {
    html += "<tr><th>" + i + "</th>";
    for (let j = 0; j < n; j++) {
      if (i === j) {
        html += '<td class="diag">—</td>';
      } else {
        const v = m.matrix[i] && m.matrix[i][j] != null ? m.matrix[i][j] : null;
        const a = Math.min(i, j), b = Math.max(i, j);
        const close = tooClose.has(a + "-" + b);
        const cls = v == null ? "" : (close ? "warn" : "ok");
        const txt = v == null ? "?" : (+v).toFixed(4);
        html += '<td class="' + cls + '">' + escapeHtml(txt) + "</td>";
      }
    }
    html += "</tr>";
  }
  html += "</tbody></table>";
  el.ssimMatrix.innerHTML = html;
}

/* ---------------------------------------------------------------------------
   人工决策模态框（Promise 化）
   --------------------------------------------------------------------------- */
let _modalResolve = null;
function showDecisionModal(name, args, message) {
  lastModalTrigger = document.activeElement;
  el.modalMsg.textContent = message || ("即将执行 " + name + "，是否继续？");
  el.modalOpName.textContent = name;
  el.modalOpParams.textContent = JSON.stringify(args, null, 2);
  el.modalOverlay.classList.remove("hidden");
  document.body.style.overflow = "hidden";
  requestAnimationFrame(() => el.modalCancel.focus());
  return new Promise((resolve) => { _modalResolve = resolve; });
}
function closeModal(result) {
  el.modalOverlay.classList.add("hidden");
  document.body.style.overflow = "";
  if (_modalResolve) { _modalResolve(result); _modalResolve = null; }
  if (lastModalTrigger && typeof lastModalTrigger.focus === "function") lastModalTrigger.focus();
  lastModalTrigger = null;
}

/* ---------------------------------------------------------------------------
   记忆 / 审计流（localStorage）
   --------------------------------------------------------------------------- */
const MEM_KEY = "vds_memory_timeline_v1";

function loadMemory() {
  try { return JSON.parse(localStorage.getItem(MEM_KEY) || "[]"); }
  catch (_) { return []; }
}
function saveMemory(list) {
  try { localStorage.setItem(MEM_KEY, JSON.stringify(list.slice(0, 200))); } catch (_) {}
}
/* kind: audit | warned | blocked | error | cancel | human */
function addMemory(tool, kind, summary) {
  const list = loadMemory();
  list.unshift({ t: Date.now(), tool, kind, summary });
  saveMemory(list);
  renderMemory();
}
let memoryFiltersInitialized = false;
const MEMORY_ARC_NS = "http://www.w3.org/2000/svg";
const MEMORY_ARC_COLORS = {
  audit: { fill: "#B9E3CF", stroke: "#16845B" },
  warned: { fill: "#F8D894", stroke: "#A96700" },
  blocked: { fill: "#F2B6B6", stroke: "#C73535" },
  error: { fill: "#F2B6B6", stroke: "#C73535" },
  cancel: { fill: "#D8DEE7", stroke: "#64748B" },
  human: { fill: "#AFE2DF", stroke: "#248E87" },
};

function memoryDateKey(t) {
  const d = new Date(t);
  const p = (n) => String(n).padStart(2, "0");
  return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate());
}
function memoryMatches(r) {
  const date = el.memoryDate.value;
  const query = el.memorySearch.value.trim().toLocaleLowerCase("zh-CN");
  const text = [toolLabel(r.tool), r.tool, r.summary, kindLabel(r.kind), r.kind].join(" ").toLocaleLowerCase("zh-CN");
  return (!date || memoryDateKey(r.t) === date) && (!query || text.includes(query));
}
function renderMemory() {
  const list = loadMemory();
  if (!memoryFiltersInitialized) {
    if (list.length) el.memoryDate.value = memoryDateKey(list[0].t);
    memoryFiltersInitialized = true;
  }
  const visible = list
    .map((record, sourceIndex) => ({ record, sourceIndex }))
    .filter((item) => memoryMatches(item.record))
    .sort((a, b) => a.record.t - b.record.t);
  // 🆕 合并连续周期：探测之后紧跟着去重 → 吞掉探测只留去重结果
  const merged = [];
  for (let i = 0; i < visible.length; i++) {
    const cur = visible[i];
    const next = visible[i + 1];
    if (cur.record.tool === "probe_video" && next && next.record.tool === "dedup_video" && next.record.t - cur.record.t < 60000) {
      continue; // 跳过探测条目，仅保留去重
    }
    if (cur.record.tool === "probe_video" && next && next.record.tool === "batch_fission" && next.record.t - cur.record.t < 60000) {
      continue; // 探测→裂变同理
    }
    merged.push(cur);
  }
  el.memoryCount.textContent = merged.length === list.length
    ? list.length + " 条记录"
    : merged.length + " / " + list.length + " 条";
  const dateContext = list
    .map((record, sourceIndex) => ({ record, sourceIndex }))
    .filter((item) => !el.memoryDate.value || memoryDateKey(item.record.t) === el.memoryDate.value)
    .sort((a, b) => a.record.t - b.record.t);
  renderMemoryArc(visible, dateContext);
  if (!merged.length) {
    el.timeline.innerHTML = '<div class="tl-empty">' + (list.length ? "没有符合筛选条件的操作记录。" : "暂无操作记录。完成探测或生成后，这里会记录关键步骤。") + "</div>";
    return;
  }
  el.timeline.innerHTML = merged.map((item, index) => {
    const r = item.record;
    const cls = "t-" + (r.kind || "audit");
    const memoryId = r.t + "-" + item.sourceIndex;
    return '<div class="tl-item ' + cls + '" data-memory-id="' + memoryId + '" tabindex="0">' +
      '<div class="tl-head"><span class="tl-tool"><span class="tl-index">' + (index + 1) + "</span>" + escapeHtml(toolLabel(r.tool)) + "</span>" +
        '<span class="tl-time">' + fmtTime(r.t) + "</span></div>" +
      '<div class="tl-summary"><span class="tl-tag">' + kindLabel(r.kind) + "</span>" + escapeHtml(r.summary || "") + "</div>" +
    "</div>";
  }).join("");
}
function renderMemoryArc(visible, dateContext = visible) {
  const svg = el.memoryArc;
  svg.replaceChildren();
  const title = memoryArcNode("title");
  title.textContent = "操作记录右半圆时间轴";
  svg.appendChild(title);
  const desc = memoryArcNode("desc");
  desc.textContent = "所选会话的操作从上到下按时间排列。";
  svg.appendChild(desc);
  const center = { x: 18, y: 170 };
  const outerR = 142;
  const bandOuter = 130;
  const bandInner = 92;
  const labelR = 151;
  const context = visible.length ? visible : dateContext;
  const placeholderStart = memoryPlaceholderStart();
  const firstTime = context.length ? context[0].record.t : placeholderStart;
  const lastTime = context.length ? context[context.length - 1].record.t : placeholderStart + 40 * 60000;
  const elapsedMinutes = Math.max(0, (lastTime - firstTime) / 60000);
  const unit = [5, 15, 60, 360, 1440, 10080].find((candidate) => elapsedMinutes / candidate <= 12) || 10080;
  const start = Math.floor(firstTime / (unit * 60000)) * unit * 60000;
  const naturalEnd = Math.ceil(lastTime / (unit * 60000)) * unit * 60000 + unit * 60000;
  const end = Math.max(naturalEnd, start + unit * 8 * 60000);
  const span = end - start;
  const point = (radius, timestamp) => {
    const ratio = Math.max(0, Math.min(1, (timestamp - start) / span));
    const angle = -Math.PI / 2 + ratio * Math.PI;
    return { x: center.x + radius * Math.cos(angle), y: center.y + radius * Math.sin(angle) };
  };
  const arcPath = (radius, from, to) => {
    const a = point(radius, from);
    const b = point(radius, to);
    return "M " + a.x + " " + a.y + " A " + radius + " " + radius + " 0 0 1 " + b.x + " " + b.y;
  };
  const bandPath = (from, to) => {
    const a = point(bandOuter, from);
    const b = point(bandOuter, to);
    const c = point(bandInner, to);
    const d = point(bandInner, from);
    return "M " + a.x + " " + a.y + " A " + bandOuter + " " + bandOuter + " 0 0 1 " + b.x + " " + b.y +
      " L " + c.x + " " + c.y + " A " + bandInner + " " + bandInner + " 0 0 0 " + d.x + " " + d.y + " Z";
  };
  svg.appendChild(memoryArcNode("path", { d: arcPath(outerR, start, end), class: "memory-arc-track" }));
  svg.appendChild(memoryArcNode("path", { d: arcPath(65, start, end), class: "memory-arc-guide" }));
  const boundaries = visible.map((item, index) => {
    if (index === 0) return start;
    return (visible[index - 1].record.t + item.record.t) / 2;
  });
  boundaries.push(end);
  visible.forEach((item, index) => {
    const r = item.record;
    const colors = MEMORY_ARC_COLORS[r.kind] || MEMORY_ARC_COLORS.audit;
    const from = boundaries[index];
    const to = boundaries[index + 1];
    const memoryId = r.t + "-" + item.sourceIndex;
    const boundary = point(bandInner, from);
    svg.appendChild(memoryArcNode("line", { x1: center.x, y1: center.y, x2: boundary.x, y2: boundary.y, class: "memory-arc-boundary" }));
    svg.appendChild(memoryArcNode("path", { d: bandPath(from, to), fill: colors.fill, stroke: colors.stroke, "stroke-width": 1, class: "memory-arc-segment", tabindex: 0, "data-memory-id": memoryId }));
    const labelPoint = point((bandOuter + bandInner) / 2, (from + to) / 2);
    const label = memoryArcNode("text", { x: labelPoint.x, y: labelPoint.y + 3, fill: colors.stroke, class: "memory-arc-label", "data-memory-id": memoryId });
    label.textContent = index + 1;
    svg.appendChild(label);
  });
  const lastBoundary = point(bandInner, end);
  svg.appendChild(memoryArcNode("line", { x1: center.x, y1: center.y, x2: lastBoundary.x, y2: lastBoundary.y, class: "memory-arc-boundary" }));
  for (let t = start; t <= end; t += unit * 60000) {
    const a = point(outerR, t);
    const b = point(outerR - 8, t);
    svg.appendChild(memoryArcNode("line", { x1: a.x, y1: a.y, x2: b.x, y2: b.y, stroke: "#64748B", "stroke-width": 0.8 }));
    const lp = point(labelR, t);
    const label = memoryArcNode("text", { x: lp.x, y: lp.y + 3, "text-anchor": "middle", class: "memory-arc-tick" });
    label.textContent = fmtHourMinute(t);
    svg.appendChild(label);
  }
  svg.appendChild(memoryArcNode("circle", { cx: center.x, cy: center.y, r: 3, fill: "#64748B" }));
  const sessionLabel = memoryArcNode("text", { x: 76, y: 166, class: "memory-arc-center" });
  sessionLabel.textContent = visible.length ? "当前会话" : (dateContext.length ? "无匹配记录" : "等待操作记录");
  svg.appendChild(sessionLabel);
  const rangeLabel = memoryArcNode("text", { x: 76, y: 178, class: "memory-arc-center" });
  rangeLabel.textContent = fmtHourMinute(start) + "–" + fmtHourMinute(end);
  svg.appendChild(rangeLabel);
  el.memoryUnit.textContent = "单位：每格 " + memoryUnitLabel(unit);
}
function memoryPlaceholderStart() {
  const selected = el.memoryDate.value;
  const d = selected ? new Date(selected + "T00:00:00") : new Date();
  const now = new Date();
  if (!selected || memoryDateKey(now.getTime()) === selected) {
    d.setHours(now.getHours(), Math.floor(now.getMinutes() / 5) * 5, 0, 0);
  } else {
    d.setHours(9, 0, 0, 0);
  }
  return d.getTime();
}
function memoryUnitLabel(minutes) {
  if (minutes < 60) return minutes + " 分钟";
  if (minutes < 1440) return (minutes / 60) + " 小时";
  if (minutes < 10080) return (minutes / 1440) + " 天";
  return (minutes / 10080) + " 周";
}
function memoryArcNode(tag, attrs = {}) {
  const node = document.createElementNS(MEMORY_ARC_NS, tag);
  Object.entries(attrs).forEach(([name, value]) => node.setAttribute(name, value));
  return node;
}
function selectMemory(memoryId) {
  document.querySelectorAll("[data-memory-id]").forEach((node) => {
    const active = node.getAttribute("data-memory-id") === memoryId;
    node.classList.toggle("is-active", active);
    if (node.classList.contains("memory-arc-segment") || node.classList.contains("memory-arc-label")) {
      node.classList.toggle("is-dimmed", !active);
    }
  });
  const row = el.timeline.querySelector('[data-memory-id="' + memoryId + '"]');
  if (row) row.scrollIntoView({ block: "nearest", behavior: "smooth" });
}
function clearMemorySelection() {
  document.querySelectorAll("[data-memory-id]").forEach((node) => node.classList.remove("is-active", "is-dimmed"));
}
function kindLabel(k) {
  return { audit: "审计", warned: "确认执行", blocked: "阻断", error: "错误", cancel: "已取消", human: "人工决策" }[k] || "记录";
}
function toolLabel(t) {
  const map = {
    dedup_video: "单条去重",
    batch_fission: "批量裂变",
    probe_video: "素材探测",
    list_assets: "刷新素材",
    list_voices: "查看音色",
    list_watermark_templates: "查看水印",
    remove_watermark: "去除水印",
    download: "下载产物",
    extract_copy_context: "提取改写上下文",
    get_job: "查询任务",
    delete_output: "删除产物",
  };
  return map[t] || t.replace(/_/g, " ");
}
function clearMemory() {
  saveMemory([]);
  renderMemory();
  toast("会话记忆已清空。", "ok");
}

/* ---------------------------------------------------------------------------
   小工具
   --------------------------------------------------------------------------- */
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function short(s) { return s ? String(s).slice(0, 10) + "…" : "?"; }
function baseName(p) { return p ? String(p).replace(/\\/g, "/").split("/").pop() : "?"; }
function mark(v) {
  if (v === true) return '<svg class="ico-14" style="color:var(--audit)" aria-label="通过"><use href="#ico-checkmark"/></svg>';
  if (v === false) return '<svg class="ico-14" style="color:var(--warned)" aria-label="未通过"><use href="#ico-xmark"/></svg>';
  return "?";
}
function fmtTime(t) {
  const d = new Date(t);
  const p = (n) => String(n).padStart(2, "0");
  return p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
}
function fmtHourMinute(t) {
  const d = new Date(t);
  const p = (n) => String(n).padStart(2, "0");
  return p(d.getHours()) + ":" + p(d.getMinutes());
}
function toast(msg, kind) {
  let wrap = document.querySelector(".toast-wrap");
  if (!wrap) { wrap = document.createElement("div"); wrap.className = "toast-wrap"; document.body.appendChild(wrap); }
  const t = document.createElement("div");
  t.className = "toast " + (kind || "");
  t.textContent = msg;
  wrap.appendChild(t);
  setTimeout(() => { t.style.opacity = "0"; setTimeout(() => t.remove(), 300); }, 3600);
}
/* 按钮忙碌态：禁用 + spinner，结束恢复原文案 */
async function withBusy(btn, label, fn) {
  btn.disabled = true;
  const orig = btn.innerHTML;
  btn.innerHTML = '<span class="spinner"></span>处理中…';
  try { await fn(); }
  catch (e) {
    toast("请求出错：" + (e.message || e), "err");
    // fetch 失败很可能是 server 掉线
    if (String(e.message || "").includes("Failed to fetch") || String(e).includes("NetworkError")) {
      showConnError("与 MCP Server 的连接中断，请确认 server/mcp_server.py 仍在运行。");
    }
  }
  finally { btn.disabled = false; btn.innerHTML = label !== undefined ? label : orig; }
}

/* ---------------------------------------------------------------------------
   事件绑定
   --------------------------------------------------------------------------- */
el.connRetry.addEventListener("click", connectAndBootstrap);
el.btnRefreshAssets.addEventListener("click", () => loadAssets());
el.assetSelect.addEventListener("change", resetResultsForAssetChange);
el.btnProbe.addEventListener("click", doProbe);
el.btnDedup.addEventListener("click", doDedup);
el.btnFission.addEventListener("click", doFission);
el.btnCancelFission.addEventListener("click", cancelFission);
el.btnOpenOutputTop.addEventListener("click", () => {
  if (el.btnOpenOutputTop.disabled) { toast("请先生成产物", "warn"); return; }
  if (!currentDedupArtifact) { toast("请先生成产物", "warn"); return; }
  downloadArtifact(currentDedupArtifact);
});
el.btnOpenOutputFission.addEventListener("click", () => downloadArtifact(selectedFissionArtifact));

// 下载产物（Uiverse 动画按钮）：未下载 → 点击下载并播放动画；已下载(变绿) → 点击打开产物文件夹
el.btnOpenOutput.addEventListener("click", () => {
  if (el.btnOpenOutput.classList.contains("is-disabled")) return;
  const check = el.btnOpenOutput.querySelector(".dl-toggle-input");
  if (!check) return;
  if (!check.checked) {
    check.checked = true; // 触发 CSS 下载动画
    if (currentDedupArtifact) downloadArtifact(currentDedupArtifact);
    else toast("暂无可下载的产物", "warn");
  } else {
    openOutputFolderDl(currentDedupArtifact);
  }
});
el.btnOpenOutput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") { e.preventDefault(); el.btnOpenOutput.click(); }
});

// 打开产物文件夹：打开固定产物输出目录 OUTPUT_DIR（/local/open-output）。
// 不再绑定「另存为」目录 —— 产物统一落在用户设置的输出目录，
// 「下载产物」仅作为把文件复制一份到别处的可选动作。
async function openOutputFolderDl(filename) {
  try {
    const resp = await fetch(OPEN_OUTPUT_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(filename ? { filename } : {}),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok || data.ok !== true) throw new Error(data.message || "HTTP " + resp.status);
    toast(filename ? "已在输出文件夹中定位：" + filename : "已打开输出文件夹。", "ok");
  } catch (e) {
    toast("打开输出文件夹失败：" + (e.message || e), "warn");
  }
}

// 用户取消「另存为」→ 退回蓝色「下载产物」，避免停留在误导的绿色态
if (window.desktop && typeof window.desktop.onDownloadProgress === "function") {
  window.desktop.onDownloadProgress((p) => {
    if (p && p.phase === "canceled") {
      const t = el.btnOpenOutput;
      const c = t && t.querySelector(".dl-toggle-input");
      if (c) c.checked = false;
    }
  });
}

// 点击「开始单条去重」时确保已配置输出目录：
//  - 已配置（config.json 含 output_dir）→ 直接返回该目录
//  - 未配置 → 弹系统文件夹选择，保存到配置（立即生效），返回所选目录
// 返回目录字符串；用户取消选择则返回 null（调用方中止去重）。
async function ensureOutputDir() {
  try {
    const resp = await fetch(GET_OUTPUT_DIR_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const data = await resp.json().catch(() => ({}));
    if (resp.ok && data.ok && data.configured && data.output_dir) {
      return data.output_dir;
    }
  } catch (e) { /* 落到下面的选择流程 */ }
  if (!window.desktop || typeof window.desktop.chooseDirectory !== "function") {
    toast("当前环境不支持选择目录，请在设置中配置输出目录", "warn");
    return null;
  }
  const r = await window.desktop.chooseDirectory().catch(() => ({ ok: false }));
  if (!r || r.canceled || !r.ok || !r.dir) {
    toast("未选择输出目录，已取消去重", "warn");
    return null;
  }
  try {
    const resp = await fetch(SET_OUTPUT_DIR_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dir: r.dir }),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok || data.ok !== true) throw new Error(data.message || "HTTP " + resp.status);
    toast("输出目录已设为：" + data.output_dir, "ok");
    return data.output_dir;
  } catch (e) {
    toast("保存输出目录失败：" + (e.message || e), "err");
    return null;
  }
}
el.fissionList.addEventListener("click", (e) => {
  const item = e.target.closest("[data-artifact-name]");
  if (!item) return;
  selectedFissionArtifact = item.getAttribute("data-artifact-name");
  el.fissionList.querySelectorAll("[data-artifact-name]").forEach((node) => {
    const selected = node === item;
    node.classList.toggle("is-selected", selected);
    node.setAttribute("aria-pressed", selected ? "true" : "false");
  });
  el.btnOpenOutputFission.disabled = false;
});
// 双击裂变产物项直接下载
el.fissionList.addEventListener("dblclick", (e) => {
  const item = e.target.closest("[data-artifact-name]");
  if (item) downloadArtifact(item.getAttribute("data-artifact-name"));
});
setupUpload();
el.btnClearMemory.addEventListener("click", clearMemory);
el.memoryDate.addEventListener("input", renderMemory);
el.memorySearch.addEventListener("input", renderMemory);
el.toolFilters.addEventListener("click", (e) => {
  const button = e.target.closest("[data-tool-filter]");
  if (button) applyToolFilter(button.getAttribute("data-tool-filter"));
});
[el.toolsList, el.toolAxis].forEach((container) => container.addEventListener("click", (e) => {
  const item = e.target.closest("[data-tool-id]");
  if (item) selectTool(item.getAttribute("data-tool-id"));
}));
[el.toolsList, el.toolAxis].forEach((container) => container.addEventListener("keydown", (e) => {
  if (e.key !== "Enter" && e.key !== " ") return;
  const item = e.target.closest("[data-tool-id]");
  if (!item) return;
  e.preventDefault();
  selectTool(item.getAttribute("data-tool-id"));
}));
el.timeline.addEventListener("click", (e) => {
  const item = e.target.closest("[data-memory-id]");
  if (item) selectMemory(item.getAttribute("data-memory-id"));
});
el.memoryArc.addEventListener("click", (e) => {
  const item = e.target.closest("[data-memory-id]");
  if (item) selectMemory(item.getAttribute("data-memory-id"));
  else clearMemorySelection();
});
[el.timeline, el.memoryArc].forEach((container) => container.addEventListener("keydown", (e) => {
  if (e.key !== "Enter" && e.key !== " ") return;
  const item = e.target.closest("[data-memory-id]");
  if (!item) return;
  e.preventDefault();
  selectMemory(item.getAttribute("data-memory-id"));
}));

/* F4.1 参数控件交互 */
// 强度档单选切换
el.levelSeg.addEventListener("click", (e) => {
  const btn = e.target.closest(".seg-btn");
  if (!btn) return;
  el.levelSeg.querySelectorAll(".seg-btn").forEach((b) => {
    const active = b === btn;
    b.classList.toggle("active", active);
    b.setAttribute("aria-checked", active ? "true" : "false");
  });
});
// flip 勾选 → 启用 flip_mode 下拉
el.dimGrid.querySelector('input[data-dim="flip"]').addEventListener("change", syncFlipModeState);
syncFlipModeState();

// 🆕 TTS 预设文案快捷填入（手动模式用）
document.querySelectorAll(".tts-preset-btn").forEach(function (btn) {
  btn.addEventListener("click", function () {
    var text = this.getAttribute("data-text") || "";
    var elText = document.getElementById("tts-text");
    if (elText) { elText.value = text; elText.focus(); }
  });
});

// 🆕 TTS 模式切换（自动提取+AI 改写 ↔ 手动输入）
(function initTtsMode() {
  var autoGroup = document.getElementById("tts-auto-group");
  var manualGroup = document.getElementById("tts-manual-group");
  var modeBtns = document.querySelectorAll("#tts-mode-switch .tts-mode-btn");

  modeBtns.forEach(function (btn) {
    btn.addEventListener("click", function () {
      modeBtns.forEach(function (b) { b.classList.remove("active"); });
      this.classList.add("active");

      var mode = this.getAttribute("data-mode");
      if (autoGroup) autoGroup.style.display = mode === "auto" ? "" : "none";
      if (manualGroup) manualGroup.style.display = mode === "manual" ? "" : "none";
    });
  });

  // 🆕元宝改写开关：勾选 → 显示模板 textarea，不勾 → 隐藏并清空
  var chkRewrite = document.getElementById("chk-rewrite");
  var templateWrap = document.getElementById("tts-template-wrap");
  var rewriteHint = document.getElementById("tts-rewrite-hint");
  function syncRewriteUI() {
    var on = chkRewrite && chkRewrite.checked;
    if (templateWrap) templateWrap.style.display = on ? "" : "none";
    //元宝登录按钮和预览按钮：勾上就显示，不做假登录检测
    var elActions = document.getElementById("tts-rewrite-actions");
    if (elActions) elActions.style.display = on ? "" : "none";
    if (rewriteHint) rewriteHint.textContent = on
      ? "已启用改写。改写复用你 Edge 里已登录的元宝（免扫码，自动切换调试模式）。"
      : "未启用改写。系统会用字幕/ASR 原文直接生成配音。";
  }
  if (chkRewrite) {
    chkRewrite.addEventListener("change", syncRewriteUI);
    syncRewriteUI();  // 初始状态
  }

  // 🆕 改写模板：预设按钮把示例文本填入 textarea（用户可自由编辑）
  var TEMPLATE_PRESETS = {
    "带货": "你是带货主播。把原文改写为口播带货文案：突出产品卖点、制造紧迫感、引导下单。语气热情有感染力。",
    "解说": "你是知识解说博主。把原文改写为解说旁白：逻辑清晰、深入浅出、善用设问引导。语气沉稳专业。",
    "Vlog": "你是生活 Vlog 博主。把原文改写为 Vlog 口播：自然随性、像在跟朋友聊天。语气轻松真实。",
  };
  document.querySelectorAll(".tts-template-fill").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var key = this.getAttribute("data-template");
      var text = TEMPLATE_PRESETS[key];
      var elTemplate = document.getElementById("tts-template");
      if (elTemplate && text) {
        elTemplate.value = text;
        elTemplate.focus();
      }
    });
  });
  // 🆕 清空模板按钮
  var btnClear = document.getElementById("btn-template-clear");
  if (btnClear) {
    btnClear.addEventListener("click", function () {
      var elTemplate = document.getElementById("tts-template");
      if (elTemplate) { elTemplate.value = ""; elTemplate.focus(); }
    });
  }
  // 🆕元宝登录按钮 — 用系统 Edge 打开元宝网页（复用 Edge 已登录态，无需内置窗口扫码）
  var ybBtn = document.getElementById("btn-yuanbao-login");
  if (ybBtn) {
    ybBtn.addEventListener("click", async function () {
      if (!window.desktop || !window.desktop.openExternal) {
        toast("桌面端 IPC 未启用", "err"); return;
      }
      var r = await window.desktop.openExternal("https://yuanbao.tencent.com/");
      if (!r || !r.ok) { toast("打开元宝失败：" + (r && r.reason || "未知"), "err"); return; }
      toast("已用系统 Edge 打开元宝；请确认已登录，改写时会自动复用该登录态", "ok");
    });
  }

  // 🆕元宝改写预览按钮
  var btnPreview = document.getElementById("btn-rewrite-preview");
  var previewBox = document.getElementById("rewrite-preview");
  var _rewriteInFlight = false; // 防止重复点击导致多路 yuanbao-done 串台（加载旧文案）
  if (btnPreview) {
    btnPreview.addEventListener("click", async function () {
      var src = currentAsset();
      if (!src) { toast("请先上传并探测视频素材", "warn"); return; }
      if (_rewriteInFlight) { toast("改写进行中，请等待本次完成", "warn"); return; }
      _rewriteInFlight = true;
      // 清空上一次残留的改写结果，避免失败/串台时旧文案被「确认使用」误填
      if (previewBox) previewBox.dataset.rewritten = "";
      var elTemplate = document.getElementById("tts-template");
      var template = elTemplate ? elTemplate.value.trim() : "";
      var elTopic = document.getElementById("tts-topic");
      var topic = elTopic ? elTopic.value.trim() : "";
      var origLabel = this.textContent;
      this.disabled = true;
      this.textContent = "等待元宝改写...";
      try {
        if (!window.desktop || !window.desktop.runYuanbaoRewrite) {
          previewBox.innerHTML = '<div style="font-size:12px;color:var(--warned);font-weight:600;margin-bottom:6px;">桌面端未启用元宝 IPC</div><div style="font-size:13px;color:var(--gray-300);">仅 Electron 桌面壳支持。</div>';
          previewBox.style.display = "";
          toast("桌面端 IPC 未启用", "err"); return;
        }

        // 阶段1：提取视频文案 + 关键帧（extract_copy_context，与 Chrome 扩展 doRewriteFlow 一致）
        this.textContent = "提取视频文案与帧图...";
        var ctx = null;
        try {
          var ctxRes = await callTool("extract_copy_context", { src: src });
          ctx = (ctxRes && ctxRes.kind === "ok" && ctxRes.data) ? ctxRes.data : null;
        } catch (e) { ctx = null; }
        if (!ctx || (!ctx.raw_text && !(ctx.frames_b64 && ctx.frames_b64.length))) {
          previewBox.innerHTML = '<div style="font-size:12px;color:var(--warned);font-weight:600;margin-bottom:6px;">无法提取视频信息</div><div style="font-size:13px;color:var(--gray-300);">视频无字幕且无音频，无法提取改写文案。请切到手动模式输入文案。</div>';
          previewBox.style.display = "";
          toast("无法提取视频信息（无字幕且无音频）", "warn");
          return;
        }

        this.textContent = "等待元宝改写...";

        // 一次性监听 done 事件（content-yuanbao.js:48 chrome.runtime.sendMessage → 主世界 shim → __desktopYuanbao → IPC yuanbao-done）
        var donePayload = await new Promise(function (resolve) {
          var resolved = false;
          var reqId = null;
          var unsub = window.desktop.onYuanbaoDone(function (payload) {
            if (resolved) return;
            // 只接受本次请求的回包；上一次/并发请求的旧回包会被忽略，杜绝加载旧文案
            var rid = payload && payload.data && payload.data.request_id;
            if (reqId && rid && rid !== reqId) return;
            resolved = true; try { unsub && unsub(); } catch (_) {}
            resolve(payload);
          });
          window.desktop.runYuanbaoRewrite({
            frames_b64: ctx.frames_b64 || [],
            raw_text: ctx.raw_text || "",
            template: template,
            topic: topic,
            max_chars: ctx.max_chars || 30,
          }).then(function (r) {
            if (r && r.request_id) reqId = r.request_id;
            if (!r || !r.ok) {
              if (resolved) return;
              resolved = true; try { unsub && unsub(); } catch (_) {}
              resolve({ action: "yb-done", data: { error: (r && r.error) || "元宝窗口未就绪" } });
            }
          });
          // 安全兜底：长时间无回包时释放监听（避免监听器泄漏），按改写超时上限放宽
          setTimeout(function () {
            if (resolved) return;
            resolved = true; try { unsub && unsub(); } catch (_) {}
            resolve({ action: "yb-done", data: { error: "改写等待超时（无回包），请重试" } });
          }, 180000);
        });

        var d = (donePayload && donePayload.data) || {};
        if (d.rewritten && previewBox) {
          var sandpaperSvg =
            '<svg class="sandpaper-svg" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="none">' +
              '<defs><filter id="sandTexture">' +
                '<feTurbulence result="sand" seed="20" numOctaves="4" baseFrequency="0.6" type="fractalNoise"></feTurbulence>' +
                '<feColorMatrix values="0.8 0 0 0 0.1  0 0.7 0 0 0.05  0 0 0 0.6 0 0 0.02  0 0 0 1 0" type="matrix"></feColorMatrix>' +
              '</filter></defs>' +
              '<rect filter="url(#sandTexture)" width="100%" height="100%"></rect>' +
            '</svg>';
          function spBlock(inner) { return '<div class="sandpaper-pattern">' + sandpaperSvg + inner + '</div>'; }
          var rewrittenHtml = '<div class="rp-rewritten">' + escapeHtml(d.rewritten) + '<span class="rp-meta">(' + d.rewritten.length + '字)</span></div>';
          previewBox.dataset.rewritten = d.rewritten;
          previewBox.innerHTML =
            '<div class="rp-title">✦元宝改写结果</div>' +
            spBlock(rewrittenHtml) +
            '<div class="rp-actions">' +
            '<button type="button" id="btn-confirm-rewrite" class="btn btn-mini" style="background:#16845B;color:#fff;"><svg class="ico-16" style="color:#6EE7B7"><use href="#ico-check"/></svg> 确认使用此文案</button>' +
            '<button type="button" id="btn-cancel-rewrite" class="btn btn-mini" style="background:#444;color:#fff;"><svg class="ico-16" style="color:#FFA0A0"><use href="#ico-xcircle"/></svg> 不用</button>' +
            '</div>';
          previewBox.style.display = "";
          // addEventListener 绑定（避开 CSP inline onclick）
          setTimeout(function () {
            var btnOk = document.getElementById("btn-confirm-rewrite");
            var btnCancel = document.getElementById("btn-cancel-rewrite");
            if (btnOk) btnOk.addEventListener("click", window.confirmRewriteText);
            if (btnCancel) btnCancel.addEventListener("click", window.cancelRewritePreview);
          }, 50);
          toast("元宝改写完成", "ok");
        } else {
          previewBox.dataset.rewritten = ""; // 失败即清空，绝不保留旧文案
          var diagMsg = d.error || "未获得回复";
          previewBox.innerHTML = '<div style="font-size:12px;color:var(--warned);font-weight:600;margin-bottom:6px;">未获得改写结果</div>' +
            '<div style="font-size:11px;color:var(--gray-300);margin-bottom:6px;max-height:240px;overflow-y:auto;white-space:pre-wrap;font-family:Consolas,monospace;background:rgba(255,255,255,.04);padding:6px 8px;border-radius:4px;">' + escapeHtml(diagMsg) + '</div>' +
            '<div style="font-size:12px;color:var(--gray-400);">改写复用你 Edge 里已登录的元宝；会自动拉起 Edge 调试实例（你的原 Edge 浏览器随后自动回归，不受影响）。</div>';
          previewBox.style.display = "";
          toast("元宝未返回改写结果", "warn");
        }
      } catch (e) {
        previewBox.innerHTML = '<div style="font-size:12px;color:var(--warned);font-weight:600;margin-bottom:6px;">请求失败</div><div style="font-size:13px;color:var(--gray-300);">' + (e.message || '网络错误') + '</div>';
        previewBox.style.display = "";
        toast("改写请求失败：" + (e.message || e), "err");
      } finally {
        _rewriteInFlight = false;
        this.textContent = origLabel;
        this.disabled = false;
      }
    });
  }
  // 🆕 确认/丢弃改写文案（全局函数，由动态生成的按钮调用）
  window.confirmRewriteText = function () {
    var previewBox = document.getElementById("rewrite-preview");
    var content = (previewBox.dataset.rewritten || "").trim();
    if (!content) { toast("未找到改写文案", "warn"); return; }
    // 切到手动模式
    document.querySelector('.tts-mode-btn[data-mode="manual"]').click();
    var elText = document.getElementById("tts-text");
    if (elText) { elText.value = content; elText.focus(); }
    previewBox.style.display = "none";
    toast("文案已填入，请点击「开始单条去重」", "ok");
  };
  window.cancelRewritePreview = function () {
    document.getElementById("rewrite-preview").style.display = "none";
  };
})();

el.modalConfirm.addEventListener("click", () => closeModal(true));
el.modalCancel.addEventListener("click", () => closeModal(false));
el.modalOverlay.addEventListener("click", (e) => { if (e.target === el.modalOverlay) closeModal(false); });
document.addEventListener("keydown", (e) => {
  if (el.modalOverlay.classList.contains("hidden")) return;
  if (e.key === "Escape") {
    e.preventDefault();
    closeModal(false);
    return;
  }
  if (e.key === "Tab") {
    const focusable = [el.modalCancel, el.modalConfirm].filter((node) => !node.disabled);
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  }
});

/* 人机接力：交付 / 再生成变体（均为前端记忆动作） */
el.btnDeliver.addEventListener("click", () => {
  if (!dedupDeliveryReady) {
    toast("五项自检未全部通过，不能确认交付。", "warn");
    return;
  }
  addMemory("dedup_video", "human", "人工决策：确认交付去重成品。");
  toast("已确认交付。请点击\"下载产物\"保存到本地。", "ok");
});
el.btnRegen.addEventListener("click", () => {
  addMemory("dedup_video", "human", "人工决策：不满意，触发再生成变体。");
  el.dedupCard.classList.add("hidden");
  doFission();
});

/* ---------------------------------------------------------------------------
   启动
   --------------------------------------------------------------------------- */
renderMemory();
connectAndBootstrap();

/* 右侧边栏面板折叠（验收记录 / 工具与分级 可收纳） */
(function initAuditCollapse() {
  document.querySelectorAll(".audit-sidebar .collapse-toggle").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var card = btn.closest(".audit-card");
      if (!card) return;
      var collapsed = card.classList.toggle("collapsed");
      btn.setAttribute("aria-expanded", String(!collapsed));
    });
  });
})();

// 右侧边栏面板级收纳/展开
(function initPanelCollapse() {
  var panel = document.getElementById("audit-panel");
  var toggleBtn = document.getElementById("panel-collapse-toggle");
  var expandFab = document.getElementById("panel-expand-fab");
  if (!panel || !toggleBtn || !expandFab) return;

  function collapsePanel() {
    panel.classList.add("panel-collapsed");
    toggleBtn.setAttribute("aria-expanded", "false");
  }
  function expandPanel() {
    panel.classList.remove("panel-collapsed");
    toggleBtn.setAttribute("aria-expanded", "true");
  }

  toggleBtn.addEventListener("click", collapsePanel);
  expandFab.addEventListener("click", expandPanel);
})();
