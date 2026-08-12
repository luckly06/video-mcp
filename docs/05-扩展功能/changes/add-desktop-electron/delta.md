# Delta: add-desktop-electron

> 合并目标：`02-方案设计/02-系统详细设计说明书【DD（Detailed Design）】.md`
> 归档前必须执行合并（见 `05-扩展功能/README.md` 第七节）。

---

## ADDED Features

### F-桌面-001: Electron 主进程骨架

Electron 主进程入口负责应用生命周期、userData 隔离、窗口创建、菜单/下载接管挂载，单实例锁防多开。

- **输入**：`docs/05-扩展功能/decisions/2026-08-12-下载瓶颈冻结与对象存储迁移.md` 已冻结（说明本变更不打通 OSS）；`docs/history/当前状态.md` 已确定远端 MCP 地址 `http://124.71.209.36:8765`
- **核心逻辑**：
  - `app.setPath('userData', path.join(app.getPath('appData'), 'video-dedup-desktop'))` 隔离用户缓存
  - `app.requestSingleInstanceLock()` + `second-instance` 事件聚焦已有窗口
  - `app.whenReady()` → 调 `bootstrap()`：解析 API_BASE → 创建主窗口 → 挂下载接管 → 装菜单
  - `window-all-closed`：`process.platform !== 'darwin'` 时 `app.quit()`；macOS 保持 dock 行为
  - `activate`：无窗口时重建（macOS）
- **预期产出**：
  - `station/desktop/main.js`（约 100 行）
  - `station/desktop/lib/window.js`：`createMainWindow({ apiBase, loadTarget, log })` 工厂 + `pathToFileUrlWithQuery` 工具
  - `station/desktop/lib/menu.js`：`buildMenu({ mainWindow, log })`：File / Edit / View / Help 最小集
  - `station/desktop/lib/logger.js`：`createLogger({ logDir, filename })`，`logs/desktop.log` 同步追加
  - `station/desktop/package.json`：`electron ^31.7.7` devDep + start/dev/test 脚本
  - `station/desktop/.gitignore`：node_modules / dist / logs
  - `station/desktop/README.md`：开发启动说明

---

### F-桌面-002: 渲染 archive/web/

桌面端**不复制** `archive/web/`，磁盘共享加载；通过 query 注入 + CSP meta 解决 file:// 下的派生失败与 inline handler 安全。

- **输入**：F-桌面-001 已创建 BrowserWindow；`archive/web/index.html` + `archive/web/app.js`（既有 UI 完整版）
- **核心逻辑**：
  - main.js 计算 `loadTarget = path.join(__dirname, '..', '..', 'archive', 'web', 'index.html')` 并转 `file://` URL，追加 `?apiBase=<解析结果>`
  - `archive/web/app.js:17-23` 顶部插入 query 兜底：`URLSearchParams(window.location.search).get('apiBase')` 经白名单校验后覆写派生结果
  - `archive/web/index.html:5` 插入 CSP meta：`default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self' http://124.71.209.36:8765 https://yuanbao.tencent.com;` ——inline `onclick/onmouseover` 仅在元宝 QR modal（`archive/web/app.js:1904-1907`）与改写预览 modal（`1992-1993`）出现，refactor 收益小于风险
  - contextIsolation + nodeIntegration: false + sandbox: true 全开
- **预期产出**：
  - `archive/web/index.html` 第 5 行后追加 CSP meta（1 处插入）
  - `archive/web/app.js` 第 17 行 IIFE 顶部加 6 行 query 兜底
  - `station/desktop/lib/window.js`：`pathToFileUrlWithQuery()` 工具

---

### F-桌面-003: 原生下载接管

主进程通过 `session.on('will-download')` 拦截 `<a download>` 触发的下载，弹出系统保存对话框，把进度推到 renderer；不解决带宽瓶颈，只在体验层把"无进度 UI"变成"原生进度条"。

- **输入**：F-桌面-001 已创建 BrowserWindow；服务端 `/local/download/<fname>` 已实现（`station/server/mcp_server.py:680-722`：200 + `Content-Disposition: attachment` + 64KB 流式）
- **核心逻辑**：
  - `session.defaultSession.on('will-download', ...)` 挂拦截器
  - `event.preventDefault()` 后调 `dialog.showSaveDialog(win, { defaultPath, filters })`
  - 用户取消 → `item.cancel()`；否则 `item.setSavePath(filePath)`
  - `item.on('updated', ...)`：计算 `receivedBytes / totalBytes`，调 `win.webContents.send('download-progress', { phase:'progress', percent, ... })`
  - `item.on('done', (_, state) => ...)`：state 为 `completed` / `cancelled` / `interrupted`，推 `phase:'done'`
  - 进度通过 preload 的 `desktop.onDownloadProgress(cb)` 订阅
- **预期产出**：
  - `station/desktop/lib/download.js`：`attachDownloadHandlers({ session, getMainWindow, log })`
  - `station/desktop/preload.js`：暴露 `onDownloadProgress(cb)` 与 `openExternal(url)`

---

### F-桌面-004: desktop.* IPC 桥

通过 `contextBridge.exposeInMainWorld('desktop', {...})` 暴露最小 API 表面给 renderer；不泄漏 Node 能力。

- **输入**：F-桌面-003 需向 renderer 推送下载进度；archive/web 中如有"在系统浏览器打开外链"按钮需求
- **核心逻辑**：
  - `desktop.onDownloadProgress(cb: (payload) => void) => unsubscribe`：
    - 注册 `ipcRenderer.on('download-progress', handler)`；
    - 返回 `() => ipcRenderer.removeListener('download-progress', handler)` 解除订阅
  - `desktop.openExternal(url: string) => Promise<{ok, reason?}>`：
    - 走 `ipcRenderer.invoke('shell:open-external', url)`；
    - 主进程 `ipcMain.handle('shell:open-external', ...)` 校验协议白名单（`^https?://`）后调 `shell.openExternal`
  - preload 不暴露 `ipcRenderer` / `require` / `process` —— 完全沙箱
- **预期产出**：
  - `station/desktop/preload.js`：约 30 行；`onDownloadProgress` / `openExternal` 两条
  - `station/desktop/lib/download.js`：注册 `ipcMain.handle('shell:open-external', ...)`

---

### F-桌面-005: **[占位]** electron-builder 打包（nsis / Windows）

DD 留位，本轮**不实现**；何时启用待 OSS 迁移决策落地后再评估（OSS 影响下载体验，是桌面端最大卖点之一）。

- **输入**：无
- **核心逻辑**：占位
- **预期产出**：无（仅在 DD 留位）

---

## MODIFIED Features

无。

## REMOVED Features

无。