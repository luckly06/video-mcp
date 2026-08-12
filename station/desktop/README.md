# video-dedup-desktop

Electron 桌面壳，**包裹** `archive/web/` 整套 Web UI，对接远端 MCP 服务器（默认 `http://124.71.209.36:8765`），与 Chrome/Edge MV3 扩展**并存**。

本轮**仅开发态**——`npm start` 跑通即可，**不打包、不签名、不发布**。详见 `docs/05-扩展功能/changes/add-desktop-electron/` 提案四件套。

---

## 启动

```bash
cd station/desktop
npm install          # 装 electron ^31.7.7（首次约 150MB，需要几分钟）
npm start            # 等价于 electron .
```

启动后弹出 1280×900 窗口，标题「视频去重工位」，加载 `archive/web/index.html`。

### 覆盖 API_BASE（可选）

```bash
# Windows PowerShell
$env:VIDEODEDUP_API_BASE = "http://localhost:8765"; npm start
```

### DevTools 自动开启

```bash
npm run dev          # 等价于 electron . --enable-logging --remote-debugging-port=9222
# 或
$env:VIDEODEDUP_DEV = "1"; npm start
```

---

## 目录结构

```
station/desktop/
├── package.json           # electron devDep + start/dev/test 脚本
├── main.js                # 主进程入口（生命周期 / userData / 菜单 / 下载）
├── preload.js             # contextBridge：desktop.onDownloadProgress / openExternal
├── README.md              # 本文件
├── .gitignore             # node_modules/, dist/, logs/
└── lib/
    ├── window.js          # BrowserWindow 工厂 + file URL + query 注入
    ├── menu.js            # File / Edit / View / Help 菜单
    ├── download.js        # session.on('will-download') + 保存对话框 + 进度回传
    ├── api-base.js        # 解析 VIDEODEDUP_API_BASE / 默认值
    └── logger.js          # 主进程日志（logs/desktop.log）
```

---

## 与既有前端的关系

| 前端 | 状态 | 用法 |
|---|---|---|
| `station/extension/`（Chrome/Edge MV3 扩展） | **生产** | 浏览器扩展，与本桌面端并存 |
| `archive/web/`（完整 Web UI） | 共享 | **桌面端直接 loadFile**；扩展 v1 之前的主 UI，现被两者复用 |
| `station/web/`（精简 Web UI） | 共享 | 服务端 run.py 内嵌的 _WEB_FILES；与本桌面端无直接关系 |

桌面端**不复制** `archive/web/`，磁盘共享——bugfix 自动双端生效。

---

## 安全策略

- `contextIsolation: true` + `nodeIntegration: false` + `sandbox: true`：renderer 完全沙箱
- preload 只暴露两条 API（`onDownloadProgress` / `openExternal`），不泄漏 Node 能力
- CSP 在 `archive/web/index.html` 内 `<meta>` 设置；`script-src 'unsafe-inline'` 仅为放行 inline `onclick`（元宝 QR modal），后续单独变更清理
- `connect-src` 白名单：远端 MCP + yuanbao.tencent.com

---

## 已知边界

- **下载带宽瓶颈未优化**：服务端 `/local/download` 仍 100 MB ≈ 3-4 分钟。本轮只在体验层加原生进度条；带宽问题须 OSS 迁移（独立决策 `docs/05-扩展功能/decisions/2026-08-12-下载瓶颈冻结与对象存储迁移.md`）。
- **HMAC requestState 进程本地**：服务端重启会作废进行中的确认流。renderer 需在收到 401/State 错误时引导用户重试。
- **打包未实现**：见 `F-桌面-005` 占位 Feature。

---

## 日志位置

- 主进程：`station/desktop/logs/desktop.log`（gitignored）
- renderer console：F12 DevTools（dev 模式自动开启）
- 服务端日志：远端 MCP 的 `station/logs/`（SSH 后查）