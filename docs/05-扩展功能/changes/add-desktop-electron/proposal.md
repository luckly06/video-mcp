# Change: add-desktop-electron · Electron 桌面壳（包裹 archive/web/）

> Change ID: `add-desktop-electron`
> 影响级别: **B**（新增模块 + 新增外部依赖 + 跨多边界：UI / IPC / CORS / Host 白名单）
> 创建日期: 2026-08-12

---

## Why

视频去重工位当前**唯一前端**是 Chrome/Edge MV3 扩展（v1.0，2026-08-11 验收）。浏览器扩展有结构性限制：

1. 必须打开 Edge/Chrome 才能用，无法独立启动；用户已多次提出"能不能直接打开一个桌面 app"
2. popup 窗口 UX 受 chrome-extension API 限制（尺寸、菜单、托盘皆无）
3. 已冻结的下载瓶颈（详见 `docs/05-扩展功能/decisions/2026-08-12-下载瓶颈冻结与对象存储迁移.md`）当前用浏览器 `<a download>`，UI 无可见进度——3-4 分钟体感像"卡死"，Electron 的 `session.on('will-download')` 可在体验层把"无进度"变成"原生进度条 + 保存对话框"

`archive/web/` 是被归档的**完整** Web UI（`app.js` 95KB）：含 TTS / 元宝 QR 改写 / 上传 / 下载 / SSIM 矩阵 / 决策确认流；MCP/Yuanbao 调用链已全打通（详见 `archive/web/app.js:396-413` `rpc()` + `:1815-2029` 元宝流程）。**复用**这套 UI 是最经济的路径，不重写。

桌面端**不替换** Chrome 扩展（用户明确并存），只提供第二条独立入口。

## What Changes

### 阶段 A：Electron 桌面壳（M1-M4 一次性交付）

- **ADDED** `F-桌面-001`：Electron 主进程骨架（main.js + 窗口 + 菜单 + 生命周期 + userData 隔离）
- **ADDED** `F-桌面-002`：渲染 archive/web/（loadFile + API_BASE 兜底 + CSP meta）
- **ADDED** `F-桌面-003`：原生下载接管（`session.on('will-download')` + 保存对话框 + 进度回传）
- **ADDED** `F-桌面-004`：`desktop.*` IPC 桥（contextBridge：onDownloadProgress / openExternal）
- **ADDED** `F-桌面-005` **[占位]**：electron-builder 打包（nsis / Windows）——**本轮不实现**，DD 留位提醒后续

### 阶段 B：不适用

无 REMOVED / 无 MODIFIED 既有 Feature。

## Impact

- **Affected Features**：
  - `F-配音-009` 浏览器扩展元宝改写桥接（archive/2026-08-12-add-browser-extension/delta.md）——**不变**，只是新增并行的桌面入口
  - 新增 `F-桌面-001` ~ `F-桌面-005`（见上）
- **Affected code**：
  - `station/desktop/{package.json, main.js, preload.js, README.md, .gitignore}`（新增全套）
  - `station/desktop/lib/{window,menu,download,api-base,logger}.js`（新增模块化拆分）
  - `archive/web/index.html:5`（新增 CSP meta）
  - `archive/web/app.js:17-23`（新增 API_BASE query 兜底）
  - `docs/02-方案设计/02-系统详细设计说明书【DD（Detailed Design）】.md`（新增「模块 6 · 桌面端」章节与 5 个 Feature）
  - `docs/history/当前状态.md`（architecture + progress 同步）
  - `docs/05-扩展功能/README.md`（「当前活跃变更」表加一行）
  - 项目根 `.gitignore`（新增 `station/desktop/{node_modules,dist,logs}/`）
- **新增依赖**：
  - `electron ^31.7.7`（devDependencies，仅开发态；不打包时不进产物）
  - 二进制大小：~150 MB（npm install 时下载）；运行时 Node 20.16 + Chromium 126 嵌入
  - License：MIT（与项目同）
  - 部署要求：用户本机有 Node.js ≥ 20.0.0；服务端 MCP 地址在 `lib/api-base.js` 默认值中（当前 `http://124.71.209.36:8765`，可通过 `VIDEODEDUP_API_BASE` 覆盖）
- **向后兼容性**：
  - `archive/web/app.js` 补丁**不破坏**现有 run.py 启动路径——query 兜底只在 `?apiBase=…` 存在时生效，其余情形走原派生逻辑
  - `archive/web/index.html` CSP meta 在浏览器中访问（`http://127.0.0.1:8765/web/` 等）也生效，但因为内联 handler 是同一文件原有逻辑，**无新增 inline 行为**——安全性影响中性
  - 老数据 / 老客户端无需迁移
- **回归范围**：
  - Chrome 扩展 v1.0 全链路（上传/探测/改写/去重/TTS/下载）——**无影响**，共享 disk 不修改
  - 服务端 MCP 10 个 tool —**无影响**
  - 服务端 `/local/download` 流式 —**无影响**

### 五项必查清单

| # | 检查项 | 本次情况 | 命中后果 |
|---|---|---|---|
| 1 | 接口定义变更 | 无（服务端 MCP 接口未动；Electron 主↔渲染通过 contextBridge 自有新 IPC，与服务端无关） | — |
| 2 | DB 表字段变更 | 无 | — |
| 3 | 异步消息变更 | 无（renderer↔main 通过 `ipcRenderer.on` + `webContents.send` 是新建通道，不复用任何旧消息结构） | — |
| 4 | 配置项变更 | 有（新增环境变量 `VIDEODEDUP_API_BASE`，默认 `http://124.71.209.36:8765`） | 记入 Impact；同步 `.cursorrules/开发环境配置.md`（待补；本变更仅新建变量，无既有值变更） |
| 5 | 定时任务变更 | 无 | — |

> 本变更未命中前三项硬触发线，但 B 级 + 跨多边界 + 新增外部依赖 → 命中软触发线 → 必写 design.md（参考 `05-扩展功能/README.md` 第五节）。

## Open Questions

1. **打包时机未定**：F-桌面-005 占位；何时启用 electron-builder 待 OSS 迁移决策落地后再评估（OSS 影响下载体验，是桌面端最大卖点之一）。（需项目主理 / 待 OSS 决策落地后）
2. **服务端 HMAC requestState 进程本地重启失效**：renderer 需在收到 401/State 错误时引导用户重试；本轮先记入 `design.md` Open Questions，本变更不实现自动重试逻辑。（需后续变更）
3. **服务端是否需要在生产 systemd 加 `VU_ALLOWED_HOSTS=...,124.71.209.36` 兜底**：`当前状态.md:110` 显示已含；首次端到端验收前用 `curl -I http://124.71.209.36:8765/mcp` 复测一次。（需主理 / 验收前）