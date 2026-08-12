# Design: add-desktop-electron

> **硬触发线**（命中任一即强制写，A/B/C 无豁免）：DB 表字段 / 接口定义 / 数据结构 / 迁移路径。
> 本次**未命中**硬触发线。
>
> **软触发（B 级判断参考）**：跨多模块 / 新增外部依赖 / 安全性能复杂度高 / 技术选型存在多个备选。
> 本次**命中软触发**（跨 UI / IPC / CORS / Host 边界 + 新增 electron 依赖 + 7 处取舍） → B 级必写 design.md。

---

## Context

视频去重工位当前**唯一前端**是 Chrome/Edge MV3 扩展（v1.0 验收）。本变更在不改服务端、不动扩展的前提下，新增 Electron 桌面壳作为**第二条**独立入口，复用 `archive/web/` 的完整 Web UI。

决策驱动力：
- 浏览器扩展结构限制（必须开浏览器、popup UX 受限）
- 已冻结的下载瓶颈（详见 `docs/05-扩展功能/decisions/2026-08-12-下载瓶颈冻结与对象存储迁移.md`）——本变更不重启 OSS 优化，只在体验层加原生进度条
- `archive/web/` 是被归档的**完整** UI（95KB app.js，含 TTS / 元宝 / 上传 / 下载 / SSIM），复用而非重写最经济

## Goals / Non-Goals

**Goals**
- 用 Electron 包裹 `archive/web/`，启动一个独立桌面窗口
- 对接远端 MCP `http://124.71.209.36:8765`，调用 10 个 tool 全打通
- 与 Chrome 扩展**并存**（不替换）
- 下载走原生保存对话框 + 系统进度条（不解决带宽）
- 端到端：上传 → 探测 → 元宝改写 → 去重 + TTS → 下载 全链路在桌面端可用

**Non-Goals**
- ❌ electron-builder 打包（占位 F-桌面-005）
- ❌ OSS 迁移（独立决策，独立变更）
- ❌ 抽 `callMcp` 到 `station/shared/`（archive/web 与扩展 background.js 协议不一致，超范围）
- ❌ 重写 archive/web/ UI
- ❌ 替换 Chrome 扩展
- ❌ 自动更新 / 代码签名 / 多窗口 / 托盘

## Decisions

### 决策 1：渲染源 = `archive/web/` 磁盘共享，不复制

- **选择**：`mainWindow.loadFile('../archive/web/index.html')`，与 Chrome 扩展共享磁盘
- **理由**：`archive/web/app.js` 仍在迭代；任何 bugfix 自动双端生效；磁盘零冗余
- **备选方案**：
  | 方案 | 优点 | 缺点 | 为什么不选 |
  |---|---|---|---|
  | 复制 archive/web/ 到 station/desktop/renderer/ | 桌面端完全独立 | bugfix 需手动同步；磁盘冗余；commit 历史分叉 | 失去"共享即同步"的最大价值 |
  | 重写桌面专用 UI | 完全自由的桌面 UX | 1000+ 行 UI 重写，1-2 周工时；与现有用户习惯分叉 | 用户明确"依旧复用 web 端" |
  | 用 station/web/（精简版）| 体积小 | 无 TTS / 元宝 / 下载 / 上传 | 不满足需求 |
- **代价**：archive/web 任何破坏性改动会影响桌面端（但 archive/web 也在被 Chrome 扩展 popup 间接通过相同 service worker 调用，desktop 不是唯一受害方）

### 决策 2：API_BASE 兜底走 URL query 注入

- **选择**：main.js 加载时给 URL 加 `?apiBase=<值>`，renderer 端 6 行补丁读 query
- **理由**：`archive/web/app.js:17-23` 原派生在 `file://` 下得到 `"file://"`（无效）；动业务代码主干风险大，patch IIFE 顶部最小侵入
- **备选方案**：
  | 方案 | 优点 | 缺点 | 为什么不选 |
  |---|---|---|---|
  | main.js 通过 `executeJavaScript` 注入 `window.__API_BASE__` | 完全不改 archive/web | 需要 webContents.executeJavaScript 权限，时机敏感 | 启动 race：DOM 还没加载完就 set 不上 |
  | 改 archive/web/app.js 默认值硬编码 `124.71.209.36:8765` | 一行改动 | 与"动态派生原意"冲突，dev/local 调试时无法切后端 | 失去派生逻辑的灵活性 |
- **代价**：archive/web/app.js 增加 6 行（不破坏现有 run.py 启动路径）

### 决策 3：CSP `script-src 'unsafe-inline'`，不 refactor inline handler

- **选择**：`<meta http-equiv="Content-Security-Policy">` 允许 inline script
- **理由**：inline `onclick/onmouseover` 只在元宝 QR modal（`archive/web/app.js:1904-1907`）与改写预览 modal（`1992-1993`）出现，共 4 行；hover 样式绑定 `style.background`，refactor 需同时拆 CSS，收益小于风险
- **备选方案**：
  | 方案 | 优点 | 缺点 | 为什么不选 |
  |---|---|---|---|
  | refactor 全部 inline handler 到 addEventListener | CSP 可收紧至 `'self'` | 改 4 处 innerHTML + 引入 2 个 modal 的事件委托；可能破 hover 样式 | 本变更不必要 |
  | 关掉 sandbox 让 Node 接管事件 | 0 行改 UI | 完全破坏 Electron 安全基线 | 不可接受 |
- **代价**：CSP 较松（script-src unsafe-inline），后续单独变更清理

### 决策 4：下载接管保留 `<a download>`，由主进程拦截

- **选择**：不改 `archive/web/app.js:503-515` 的 `<a download>` 触发；main 进程 `session.on('will-download')` 拦截
- **理由**：改动最小；renderer 不需要新 API；系统原生保存对话框 + 任务栏进度条**部分缓解**"下载像卡死"
- **备选方案**：
  | 方案 | 优点 | 缺点 | 为什么不选 |
  |---|---|---|---|
  | 改用 IPC：`window.desktop.handleDownload(url, filename)` | 完全可控下载逻辑 | 需改 renderer + preload + 移除 `<a download>`，跨 3 文件 | 收益小（Electron 默认就支持 will-download） |
  | 让 `<a download>` 走浏览器默认（不动） | 0 行主进程代码 | 无原生进度条，与本变更目标冲突 | 没意义 |
- **代价**：依赖 Electron 的 will-download 行为；如未来 Electron 改动需回归测试

### 决策 5：IPC API 表面只暴露 2 条方法

- **选择**：preload 只暴露 `desktop.onDownloadProgress(cb)` + `desktop.openExternal(url)`
- **理由**：90% 能力 Electron 自带（菜单、加载、生命周期、对话框、文件读）；只补一个洞
- **备选方案**：
  | 方案 | 优点 | 缺点 | 为什么不选 |
  |---|---|---|---|
  | 暴露更多 API（文件读、shell、app）| 灵活 | 扩大攻击面 | 安全原则：最小权限 |
  | 不暴露 IPC，全部走 webContents.executeJavaScript | 0 preload | 完全反 contextIsolation 设计意图 | 不可接受 |
- **代价**：未来如需新增能力，需单独变更加 API（每次都是小改动，可控）

### 决策 6：Electron 版本 ^31.7.7

- **选择**：`electron ^31.7.7`（Chrome 126 / Node 20.16 / LTS 到 2025-09+）
- **理由**：当前 stable；安全更新有保障；Electron 31 是 2026-08 时点的合理选择
- **备选方案**：
  | 方案 | 优点 | 缺点 | 为什么不选 |
  |---|---|---|---|
  | Electron 30 / 29 | 略小下载 | 安全更新已过 | LTS 周期 |
  | Electron 32+ | 最新 | 稳定性未充分验证 | 桌面端开发态不追新 |
- **代价**：未来 Electron 32/33 上游需测试兼容性

### 决策 7：userData 隔离到 `app.getPath('appData')/video-dedup-desktop`

- **选择**：`app.setPath('userData', ...)` 在 app ready 之前调用
- **理由**：不污染机器上其他 Electron app 的缓存；卸载干净
- **代价**：用户切换时需手动迁移（暂无 UI 提示，留后续）

## Risks / Trade-offs

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R1 | `archive/web/app.js` 仍在迭代，新功能可能引入 Electron 兼容问题 | 桌面端加载报错 | loadFile 共享磁盘；启动报错时立即定位到具体行；archive/web/index.html 加了 CSP，新功能若加 inline handler 也已放行 |
| R2 | CSP `connect-src` 漏列新后端会导致 MCP 调不通 | 桌面端 fetch 失败 | CSP 已白名单 `124.71.209.36:8765` + `yuanbao.tencent.com`；新增后端需同步更新 meta |
| R3 | 服务端 `VU_ALLOWED_HOSTS` 生产环境是否含 `124.71.209.36` | 首启 403 | `当前状态.md:110` 已确认；验收前用 `curl -I http://124.71.209.36:8765/mcp` 复测 |
| R4 | `<a download>` 跨源触发，Electron `will-download` 能否拦截 | 下载不弹保存对话框 | 文档确认 Electron 默认拦截；测试时观察 `item.getFilename()` 是否正确 |
| R5 | Origin=`null` + POST `Content-Type: application/json` 是否触发预检 CORS | MCP 调用失败 | 服务端 `_cors`（`mcp_server.py:640-647`）只放 POST+OPTIONS；Electron 不发预检（无自定义 Header） |
| R6 | HMAC `requestState` 进程本地：服务端重启会作废进行中确认 | 用户点击"确认"后报 401 | 单窗口影响小；renderer 端在收到 state 失效时引导重试（记入 Open Questions，本轮不实现） |
| R7 | 元宝 QR inline handler 在 CSP `unsafe-inline` 下才能跑 | QR modal 不能用 | 已选 CSP unsafe-inline；后续单独变更清理 |
| R8 | 用户在桌面端清缓存会不会丢 dedupResult | 与扩展 popup 一致 | archive/web 用 localStorage 两把 key；Electron 默认 userData 不丢；如担心可加导出按钮（后续） |
| R9 | npm install 下载 ~150MB electron，首次启动慢 | 用户首次体验 | README 注明；离线环境需提前下载 |
| R10 | 单实例锁冲突（用户双击图标） | 已有窗口不聚焦 | main.js 已注册 `second-instance` 事件聚焦；扩展行为不冲突 |

## Migration Plan

**上线顺序**
1. 桌面端代码就位（已完成 main.js + lib/* + preload.js + archive/web 补丁）
2. 用户本机 `cd station/desktop && npm install && npm start`
3. 验收 §8 全链路
4. 归档 change：合并 delta 进 DD，归档 `changes/add-desktop-electron/` → `changes/archive/2026-08-12-add-desktop-electron/`
5. 更新 `docs/05-扩展功能/README.md` 的「当前活跃变更」表 → 移除该行

**数据迁移**
- 无历史数据迁移；Electron 的 userData 是新建独立目录
- localStorage 是 renderer 内置，与扩展 popup 各自的 userData 隔离（不影响）

**回滚方案**
- 删除 `station/desktop/` 目录即可，archive/web/ 仍可由 `python run.py` 启动（run.py 不读 station/desktop/）
- `archive/web/index.html` + `archive/web/app.js` 的 CSP meta + 6 行补丁是**可逆**的：移除 CSP meta、还原 IIFE 顶部 6 行，archive/web 完全回到 v1.0 状态
- git revert 单 commit 即可

## Open Questions

1. **打包目标平台**（F-桌面-005 占位）：本轮 Win only，package.json 预留 mac/linux 字段；何时真正启用 electron-builder 待 OSS 决策
2. **服务端 HMAC secret 重启**：renderer 需在收到 401/State 失效时引导重试；本变更不实现，记入下一轮
3. **archive/web 是否需文件头注释**：本轮 patch 内已写注释说明，无须额外注释
4. **元宝 QR inline handler 清理**：待 F-桌面-005 打包前可单独提一个清理变更
5. **应用图标**：本轮省略 icon.png；后续可复用 `station/extension/icons/icon128.png`（Electron 需 .ico 格式，需转换）