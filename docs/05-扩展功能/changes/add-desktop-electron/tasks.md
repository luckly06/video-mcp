# Tasks: add-desktop-electron

> WBS 增量。归档时同步回 `docs/03-WBS/01-WBS-开发待办.md`（当前不存在，按项目约定保留增量在该文件，待 WBS 主文件被建立时合并）。
> `[x]` 只能在**端到端验收通过**后标记，不是「代码写完了」。

---

## 1. 桌面端模块（M1 + M2 + M3 + M4 一并落地，按"骨架 → 加载 → 下载 → 菜单"递进验收）

### 1.1 主进程骨架

- [ ] 1.1.1 创建 `station/desktop/package.json`（electron ^31.7.7 devDep + start/dev/test 脚本 + engines node ≥ 20）
  - **依赖**：无
  - **验收**：`npm install --dry-run` 解析 electron 成功；`npm ls electron` 显示 31.7.x
  - **估时**：0.5h

- [ ] 1.1.2 创建 `station/desktop/main.js`（app 生命周期 + userData 隔离 + 单实例锁 + bootstrap）
  - **依赖**：1.1.1
  - **验收**：`node --check main.js` 通过；进程被 signal 中断时不留僵尸
  - **估时**：1h

- [ ] 1.1.3 创建 `station/desktop/lib/window.js`（BrowserWindow 工厂 + pathToFileUrlWithQuery 工具）
  - **依赖**：1.1.2
  - **验收**：contextIsolation / nodeIntegration / sandbox 全部 true；ready-to-show 后再 show
  - **估时**：1h

- [ ] 1.1.4 创建 `station/desktop/lib/logger.js`（同步追加 logs/desktop.log + stderr）
  - **依赖**：1.1.2
  - **验收**：进程崩溃前最后一行日志不丢；磁盘满时不阻塞主进程
  - **估时**：0.5h

- [ ] 1.1.5 创建 `station/desktop/lib/api-base.js`（解析 VIDEODEDUP_API_BASE / 默认 `http://124.71.209.36:8765`）
  - **依赖**：无
  - **验收**：非 http/https 协议回退默认值；尾 `/` 抹平
  - **估时**：0.5h

### 1.2 渲染 archive/web/

- [ ] 1.2.1 改 `archive/web/index.html` 插入 CSP meta（位于 viewport meta 后、color-scheme meta 前）
  - **依赖**：1.1.3
  - **验收**：浏览器加载 `http://127.0.0.1:8765/web/` 无 CSP violation；F12 console 无相关报错
  - **估时**：0.5h

- [ ] 1.2.2 改 `archive/web/app.js:17-23` 加 API_BASE query 兜底（IIFE 顶部插入 6 行）
  - **依赖**：1.2.1
  - **验收**：URL 加 `?apiBase=http://test:1234` 时 API_BASE = `http://test:1234`；无 query 时回退原派生
  - **估时**：0.5h

- [ ] 1.2.3 main.js 用 `loadFile` + query 注入加载 `archive/web/index.html`
  - **依赖**：1.2.1, 1.2.2
  - **验收**：桌面窗口渲染 archive/web/ 完整 UI（含 TTS / 元宝 / 上传区 / SSIM 矩阵）；MCP 连接指示灯亮
  - **估时**：0.5h

### 1.3 原生下载接管

- [ ] 1.3.1 创建 `station/desktop/lib/download.js`（will-download 拦截 + 保存对话框 + 进度回传 + shell:open-external IPC）
  - **依赖**：1.1.3
  - **验收**：触发下载 → 弹保存对话框 → 选路径 → 任务栏/下载栏进度条出现 → 文件落盘 → `done` 事件
  - **估时**：1.5h

- [ ] 1.3.2 创建 `station/desktop/preload.js`（contextBridge 暴露 `desktop.onDownloadProgress` / `desktop.openExternal`）
  - **依赖**：1.3.1
  - **验收**：renderer 端 `window.desktop.onDownloadProgress(cb)` 收到 `{phase, filename, percent, ...}` 序列；返回 unsubscribe 函数
  - **估时**：0.5h

### 1.4 菜单 / 生命周期 / 日志挂载

- [ ] 1.4.1 创建 `station/desktop/lib/menu.js`（File / Edit / View / Help 最小集 + macOS 平台差异）
  - **依赖**：1.1.2
  - **验收**：菜单可见可点；Ctrl+R 触发 reload；Ctrl+Shift+I 触发 DevTools
  - **估时**：1h

- [ ] 1.4.2 main.js 接 `window-all-closed`（非 macOS quit） + `activate`（macOS 重建窗口）
  - **依赖**：1.1.2
  - **验收**：Windows 关窗 → 进程退出；macOS 关窗 → dock 保持 + 点 dock 重建窗口
  - **估时**：0.5h

### 1.5 文档与同步

- [ ] 1.5.1 创建 `station/desktop/README.md`（启动 / 覆盖 API_BASE / DevTools / 安全策略 / 已知边界）
  - **依赖**：1.1.1 ~ 1.4.2
  - **验收**：用户读 README 后能 `npm install && npm start` 直接跑通
  - **估时**：0.5h

- [ ] 1.5.2 创建 `station/desktop/.gitignore`（node_modules / dist / logs / *.log / .DS_Store）
  - **依赖**：无
  - **验收**：`git status station/desktop/` 不报 node_modules 噪音
  - **估时**：0.1h

- [ ] 1.5.3 项目根 `.gitignore` 追加 `station/desktop/{node_modules,dist,logs}` 兜底
  - **依赖**：1.5.2
  - **验收**：同上
  - **估时**：0.1h

- [ ] 1.5.4 DD 同步：在 `docs/02-方案设计/02-系统详细设计说明书【DD（Detailed Design）】.md` 新增 `## 模块 6 · 桌面端（Electron）` 章节 + F-桌面-001 ~ F-桌面-005 完整段落
  - **依赖**：1.1.1 ~ 1.4.2
  - **验收**：DD §6 章节存在；grep 能搜到 5 个 F-桌面-* 编号
  - **估时**：0.5h

- [ ] 1.5.5 更新 `docs/history/当前状态.md`：architecture.frontend 追加 desktop 一行；progress 追加 D1~D5 五项
  - **依赖**：1.5.4
  - **验收**：YAML 块能正确解析
  - **估时**：0.1h

- [ ] 1.5.6 更新 `docs/05-扩展功能/README.md` 的「当前活跃变更」表加 `add-desktop-electron · B · 待验收` 一行
  - **依赖**：1.5.4
  - **验收**：表格 markdown 渲染正常
  - **估时**：0.1h

### 1.6 端到端验收

- [ ] 1.6.1 `npm start` 启动 < 5s 弹窗 + 完整 UI 渲染
  - **依赖**：1.1 ~ 1.4
  - **验收**：任务管理器见 electron.exe；DevTools 无 CSP violation / 404
  - **估时**：0.5h

- [ ] 1.6.2 MCP 10 个工具能调通（手动探测 list_assets / probe_video / list_outputs）
  - **依赖**：1.6.1
  - **验收**：DevTools console 执行 `fetch('/mcp', {...}).then(r=>r.json())` 返回 10 个 tool
  - **估时**：0.5h

- [ ] 1.6.3 全链路 E2E：上传 2.mp4 → 探测 → 元宝改写 → 去重+TTS → 原生下载
  - **依赖**：1.6.2
  - **验收**：产物文件落到用户选定路径，字节数与服务端一致
  - **估时**：1h

- [ ] 1.6.4 持久化：关闭再开 → localStorage 数据保留
  - **依赖**：1.6.3
  - **验收**：上次填的元宝模板 / TTS 文本仍显示
  - **估时**：0.2h

---

## 进度

- 总计：0 / 22
- 代码与文档：全部落地（M1-M5 实施完成；`node --check` 全部 OK；`npm install` 装好 electron 31.7.7）
- 当前 WP：1.6.x 端到端验收（**待用户本机 `cd station/desktop && npm start` 跑通后再逐项打勾**）
- 未归档原因：1.6 E2E 未通过，按 OpenSpec 门禁 `[x]` 不能在「代码写完了」时打勾