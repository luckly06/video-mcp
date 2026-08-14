# video-dedup-desktop

Electron 桌面壳，**包裹** `archive/web/` 整套 Web UI，**本地拉起 MCP 后端**（`station/server/mcp_server.py`，监听 `http://127.0.0.1:8765`），并复用 `station/extension/content-yuanbao.js` 作为元宝页面驱动。不依赖远端服务器。

当前支持 Windows portable exe 打包；图标来自 `station/desktop/build/icon.ico`，运行时资源通过 `process.resourcesPath` 读取。

---

## 启动

```bash
cd station/desktop
npm install          # 装 electron ^31.7.7（首次约 150MB，需要几分钟）
npm start            # 等价于 electron .
```

启动后弹出 1280×900 窗口，标题「视频去重工位」，加载 `archive/web/index.html`。

**本地后端**：`npm start` 会自动 spawn 本机 Python venv 的 `station/server/mcp_server.py`（监听 `127.0.0.1:8765`），就绪后再建窗口；退出时自动回收子进程。若端口已有一个 server 在跑，直接复用不重复 spawn。

本地后端依赖（缺失时按能力降级，不影响启动）：
- Python venv：优先 `%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe`，回退系统 `python`
- ffmpeg/ffprobe：`station/vendor/ffmpeg/`（相对锚定，自动定位）
- ASR 模型：自动探测 `F/E/D/C:\Download\A-models\sherpa-onnx`；找不到则「提取文案」降级为空（去重/探测不受影响）

### 覆盖 API_BASE（可选，指定外部后端）

```bash
# Windows PowerShell
$env:VIDEODEDUP_API_BASE = "http://localhost:8765"; npm start
```

设置 `VIDEODEDUP_API_BASE` 后**不再拉起本地后端**，直接连外部地址。

### DevTools 自动开启

```bash
npm run dev          # 等价于 electron . --enable-logging --remote-debugging-port=9222
# 或
$env:VIDEODEDUP_DEV = "1"; npm start
```

---

## 打包

```bash
cd station/desktop
npm install
npm run dist
```

产物默认生成到：

- `station/desktop/dist/视频去重工位-0.1.0-x64.exe`
- `station/desktop/dist/win-unpacked/`

打包时会随包携带：

- `archive/web/`：桌面端 UI
- `station/server/`：本机 MCP 后端
- `station/extension/content-yuanbao.js`：元宝页面驱动
- `station/vendor/`：ffmpeg/ffprobe 等本地依赖
- `station/desktop/build/icon.ico`：Windows exe / 窗口图标

---

## 目录结构

```
station/desktop/
├── package.json           # electron + electron-builder 脚本与打包配置
├── build/                 # icon.png / icon.ico
├── main.js                # 主进程入口（生命周期 / userData / 菜单 / 下载）
├── preload.js             # contextBridge：desktop.onDownloadProgress / openExternal
├── README.md              # 本文件
├── .gitignore             # node_modules/, dist/, logs/
└── lib/
    ├── window.js          # BrowserWindow 工厂 + file URL + query 注入
    ├── menu.js            # File / Edit / View / Help 菜单
    ├── download.js        # session.on('will-download') + 保存对话框 + 进度回传
    ├── api-base.js        # 解析 VIDEODEDUP_API_BASE / 默认值
    ├── paths.js           # 开发态 / 打包态资源路径解析
    └── logger.js          # 主进程日志（userData/logs/desktop.log）
```

---

## 与既有前端的关系

| 前端 | 状态 | 用法 |
|---|---|---|
| `station/extension/`（Chrome/Edge MV3 扩展） | 历史/复用 | 当前桌面端只随包复用 `content-yuanbao.js` |
| `archive/web/`（完整 Web UI） | 共享 | **桌面端直接 loadFile**；扩展 v1 之前的主 UI，现被两者复用 |
| `station/web/`（精简 Web UI） | 共享 | 服务端 run.py 内嵌的 _WEB_FILES；与本桌面端无直接关系 |

开发态桌面端直接读取仓库内 `archive/web/`；打包态随包复制到 `resources/archive/web/`。

---

## 安全策略

- `contextIsolation: true` + `nodeIntegration: false` + `sandbox: true`：renderer 完全沙箱
- preload 只暴露两条 API（`onDownloadProgress` / `openExternal`），不泄漏 Node 能力
- CSP 在 `archive/web/index.html` 内 `<meta>` 设置；`script-src 'unsafe-inline'` 仅为放行 inline `onclick`（元宝 QR modal），后续单独变更清理
- `connect-src` 白名单：本地 MCP（`127.0.0.1:8765` / `localhost:8765`）+ yuanbao.tencent.com

---

## 已知边界

- **下载带宽瓶颈未优化**：服务端 `/local/download` 仍 100 MB ≈ 3-4 分钟。本轮只在体验层加原生进度条；带宽问题须 OSS 迁移（独立决策 `docs/05-扩展功能/decisions/2026-08-12-下载瓶颈冻结与对象存储迁移.md`）。
- **HMAC requestState 进程本地**：服务端重启会作废进行中的确认流。renderer 需在收到 401/State 错误时引导用户重试。
- **未签名**：当前产物为 Windows portable exe，未做代码签名，首次运行可能触发系统安全提示。

---

## 日志位置

- 主进程：`%APPDATA%/video-dedup-desktop/logs/desktop.log`
- renderer console：F12 DevTools（dev 模式自动开启）
- 服务端日志：本地后端 `station/logs/`（audit.jsonl / jobs.json）；后端 stdout/stderr 转发到主进程日志
