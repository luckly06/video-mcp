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

const MCP_URL = "http://127.0.0.1:8765/mcp";
const OPEN_OUTPUT_URL = "http://127.0.0.1:8765/local/open-output";

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
const TIER_LABEL = { audit: "audit", warned: "warned", blocked: "blocked", pass: "pass" };

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

  assetSelect: $("asset-select"),
  btnRefreshAssets: $("btn-refresh-assets"),
  btnProbe: $("btn-probe"),
  probeCard: $("probe-card"),
  probeGrid: $("probe-grid"),

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
  dedupDetail: $("dedup-detail"),
  btnDeliver: $("btn-deliver"),
  btnRegen: $("btn-regen"),
  btnOpenOutput: $("btn-open-output"),

  fissionCount: $("fission-count"),
  btnFission: $("btn-fission"),
  fissionCard: $("fission-card"),
  fissionSummary: $("fission-summary"),
  fissionSeparation: $("fission-separation"),
  fissionMatrixWrap: $("fission-matrix-wrap"),
  fissionMatrix: $("fission-matrix"),
  fissionList: $("fission-list"),
  btnOpenOutputFission: $("btn-open-output-fission"),

  timeline: $("timeline"),
  memoryCount: $("memory-count"),
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
let dedupDeliveryReady = false;
let currentWorkflowStep = 1;
let lastModalTrigger = null;

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
  dedupDeliveryReady = false;
  el.btnDeliver.disabled = true;
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
async function callToolWithConfirm(name, args) {
  let res = await callTool(name, args);
  if (res.kind !== "input_required") return res;

  const ok = await showDecisionModal(name, args, res.message);
  if (!ok) {
    addMemory(name, "cancel", "人工在决策点取消了该操作。");
    toast("已取消：未执行 " + name, "warn");
    return { kind: "cancelled" };
  }
  addMemory(name, "human", "人工确认决策点：批准执行。");
  res = await callTool(name, args, { confirmed: true, requestState: res.requestState });
  return res;
}

async function openOutputFolder(button) {
  const original = button.innerHTML;
  button.disabled = true;
  button.textContent = "正在打开...";
  try {
    const resp = await fetch(OPEN_OUTPUT_URL, { method: "POST" });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok || data.ok !== true) throw new Error(data.message || `HTTP ${resp.status}`);
    addMemory("open_output", "human", "人工打开 output/ 文件夹查看成片。");
    toast("已打开输出文件夹。", "ok");
  } catch (e) {
    toast("打开输出文件夹失败：" + (e.message || e), "warn");
  } finally {
    button.disabled = false;
    button.innerHTML = original;
  }
}

/* ---------------------------------------------------------------------------
   连接检查 + 引导
   --------------------------------------------------------------------------- */
function showConnError(msg) {
  el.connBanner.classList.remove("hidden", "ok");
  el.connText.textContent = msg || "无法连接 MCP Server，请先启动：python server/mcp_server.py（监听 127.0.0.1:8765）";
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
    showConnError("请先启动 MCP Server（python server/mcp_server.py，监听 127.0.0.1:8765）。若已启动，可能是 file:// 跨域 —— 该 server 已开放 CORS，直接刷新重试即可。");
    // 连接失败时把清单/素材区标为不可用
    el.whitelist.innerHTML = '<span class="wl-hint">未连接，无法拉取工具白名单</span>';
    el.toolsList.innerHTML = '<div class="loading">未连接 Server，无法加载工具清单。</div>';
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
    el.toolsList.innerHTML = '<div class="loading">工具清单加载失败：' + escapeHtml(e.message) + "</div>";
  }
}

function renderTools(tools) {
  if (!tools.length) {
    el.toolsList.innerHTML = '<div class="loading">Server 未暴露任何工具。</div>';
    return;
  }
  el.toolsList.innerHTML = "";
  tools.forEach((t) => {
    const tier = tierOf(t.name);
    const card = document.createElement("div");
    card.className = "tool-card t-" + tier;
    card.innerHTML =
      '<div class="tool-card-head">' +
        '<span class="tool-name">' + escapeHtml(t.name) + "</span>" +
        '<span class="tool-tier tier-' + tier + '">' + TIER_LABEL[tier] + "</span>" +
      "</div>" +
      '<div class="tool-desc">' + escapeHtml(t.description || "") + "</div>";
    el.toolsList.appendChild(card);
  });
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
    el.assetSelect.innerHTML = '<option value="">video/ 下暂无素材</option>';
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
  setWorkflowStep(3);
  withBusy(el.btnDedup, "开始单条去重", async () => {
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
    setWorkflowStep(4);
    renderDedup(res.data);
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
}

function renderDedup(d) {
  const c = d.checks || {};
  dedupDeliveryReady = c.all_passed === true;
  el.btnDeliver.disabled = !dedupDeliveryReady;
  el.btnDeliver.title = dedupDeliveryReady ? "" : "五项自检全部通过后才可确认交付";
  setCheck(el.chkMd5, c.md5_changed);
  setCheck(el.chkRes, c.resolution_kept);
  setCheck(el.chkDur, c.duration_close);
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

  const src = d.src || {};
  const out = d.output || {};
  const applied = d.applied_params || {};
  const trimLine = applied.trim_skipped
    ? "去头尾   : 跳过（" + (applied.trim_skip_reason || "原时长过短") + "）\n"
    : "";
  const detail =
    "输出文件 : " + (d.output_path || "?") + "\n" +
    "源  MD5  : " + (src.md5 || "?") + "\n" +
    "新  MD5  : " + (out.md5 || "?") + "\n" +
    "分辨率   : " + (src.width || "?") + "×" + (src.height || "?") +
      "  →  " + (out.width || "?") + "×" + (out.height || "?") + "\n" +
    "时长     : " + (src.duration != null ? src.duration : "?") + "s  →  " +
      (out.duration != null ? out.duration : "?") + "s\n" +
    trimLine +
    "帧率     : " + (d.fps != null ? d.fps : "?") + " fps\n" +
    "job_id   : " + (d.job_id || "—") + "\n" +
    "应用参数 : " + JSON.stringify(applied);
  el.dedupDetail.textContent = detail;
  el.dedupCard.classList.remove("hidden");
  el.dedupCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function setCheck(node, ok) {
  node.classList.remove("pass", "fail");
  const mk = node.querySelector(".check-mark");
  if (ok === true) { node.classList.add("pass"); mk.textContent = "✓"; }
  else if (ok === false) { node.classList.add("fail"); mk.textContent = "✕"; }
  else { mk.textContent = "—"; }
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

  const args = { src, count, level, dimensions };
  if (flip_mode) args.flip_mode = flip_mode;
  setWorkflowStep(3);
  withBusy(el.btnFission, "开始裂变", async () => {
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
    setWorkflowStep(4);
    renderFission(res.data);
    const allPass = res.data.matrix && res.data.matrix.all_pass;
    const deliveryReady = res.data.delivery_ready === true;
    addMemory("batch_fission", "warned",
      `裂变 ${res.data.count} 个变体 · MD5唯一${mark(res.data.all_unique)} · 矩阵${mark(allPass)} · 交付门${mark(deliveryReady)}（源：${res.data.src}）`);
    if (deliveryReady) {
      toast("裂变完成且双门通过：生成 " + res.data.count + " 个变体。", "ok");
    } else {
      toast("裂变已生成，但 MD5 唯一性或距离矩阵未通过，当前不可交付。", "warn");
    }
  });
}

function renderFission(d) {
  const uniq = d.all_unique === true;
  const matrix = d.matrix || null;
  const allPass = !!(matrix && matrix.all_pass);

  // 摘要：MD5 唯一 + 矩阵达标
  const badges = [];
  badges.push('<span class="fission-badge ' + (uniq ? "" : "warn") + '">' +
    (uniq ? "MD5 全部互不相同 ✓" : "存在重复 MD5 ✕") + "</span>");
  if (matrix) {
    badges.push('<span class="fission-badge ' + (allPass ? "" : "warn") + '">' +
      (allPass ? "距离矩阵全部达标 ✓" : "存在过近对 ✕") + "</span>");
  }
  el.fissionSummary.innerHTML =
    "源素材 <b>" + escapeHtml(d.src || "?") + "</b> · 共 " + (d.count || 0) + " 个变体 " +
    badges.join(" ");

  // separation 诊断（all_pass=false 时展示卡哪条腿）
  renderSeparation(d.separation, allPass);

  // 距离矩阵表格
  if (matrix && Array.isArray(matrix.matrix) && matrix.count > 1) {
    renderMatrix(matrix);
    el.fissionMatrixWrap.classList.remove("hidden");
  } else {
    el.fissionMatrixWrap.classList.add("hidden");
  }

  const variants = d.variants || [];
  el.fissionList.innerHTML = variants.map((v) =>
    '<div class="fission-item">' +
      '<span class="fission-idx">' + (v.index || "?") + "</span>" +
      '<span class="fission-name">' + escapeHtml(baseName(v.output_path)) + "</span>" +
      '<span class="fission-md5">MD5 ' + escapeHtml(short(v.md5)) + "</span>" +
    "</div>"
  ).join("");
  el.fissionCard.classList.remove("hidden");
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
    '<span class="sep-icon">⚠</span>' +
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
function renderMemory() {
  const list = loadMemory();
  el.memoryCount.textContent = list.length + " 条记录";
  if (!list.length) {
    el.timeline.innerHTML = '<div class="tl-empty">暂无操作记录。发起探测 / 去重后，这里会记录你的每一步。</div>';
    return;
  }
  el.timeline.innerHTML = list.map((r) => {
    const cls = "t-" + (r.kind || "audit");
    return '<div class="tl-item ' + cls + '">' +
      '<span class="tl-dot"></span>' +
      '<div class="tl-head">' +
        '<span class="tl-tool">' + escapeHtml(r.tool || "?") + "</span>" +
        '<span class="tl-time">' + fmtTime(r.t) + "</span>" +
      "</div>" +
      '<div class="tl-summary"><span class="tl-tag">' + kindLabel(r.kind) + "</span>" +
      escapeHtml(r.summary || "") + "</div>" +
    "</div>";
  }).join("");
}
function kindLabel(k) {
  return { audit: "审计", warned: "确认执行", blocked: "阻断", error: "错误", cancel: "已取消", human: "人工决策" }[k] || "记录";
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
function mark(v) { return v === true ? "✓" : v === false ? "✕" : "?"; }
function fmtTime(t) {
  const d = new Date(t);
  const p = (n) => String(n).padStart(2, "0");
  return p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
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
el.btnOpenOutputTop.addEventListener("click", () => openOutputFolder(el.btnOpenOutputTop));
el.btnOpenOutput.addEventListener("click", () => openOutputFolder(el.btnOpenOutput));
el.btnOpenOutputFission.addEventListener("click", () => openOutputFolder(el.btnOpenOutputFission));
el.btnClearMemory.addEventListener("click", clearMemory);

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
  toast("已确认交付。产出保留在 output/ 目录。", "ok");
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
