# Delta（增量设计变更）

## ADDED Features

### F-配音-009: 浏览器扩展元宝改写桥接

通过 Chrome/Edge 浏览器扩展，利用用户已登录元宝的浏览器标签页完成改写，替代不可行的服务器端 headless Chromium 方案。

- **输入**：`extract_copy_context` MCP 工具（`F-配音-007` 已实现帧提取+ASR）；用户本地浏览器已有元宝登录态
- **核心逻辑**：
  1. content-vu.js 注入 vu.evenblue.top，暴露 `window.__ybExtension.rewrite()` API
  2. 网站 app.js 检测扩展就绪 → 发送改写请求（src + template + topic）
  3. background.js 接收请求 → 调 MCP `extract_copy_context` 获取帧图(base64)+文案
  4. background.js 打开/复用 yuanbao.tencent.com 标签页
  5. content-yuanbao.js 接收注入指令 → 构建 prompt → DataTransfer 上传图片 → fill 输入框 → Enter 发送 → 双采样等回复稳定 → 提取回复
  6. 回复通过 background → content-vu → 网站 page → 展示结果
- **预期产出**：
  - `station/extension/manifest.json` — MV3 扩展定义
  - `station/extension/background.js` — Service Worker，MCP 调用 + 消息路由
  - `station/extension/content-vu.js` — vu.evenblue.top 桥接脚本
  - `station/extension/content-yuanbao.js` — yuanbao.tencent.com DOM 操作脚本
  - `station/extension/icons/` — 扩展图标（16/48/128 px）
  - `station/web/app.js` — 修改：扩展检测 + 改写路由逻辑

## MODIFIED Features

### F-配音-007: 元宝识图 + 改写（服务器端）

- **变更原因**：服务器端 headless QR 登录已判不可行（`docs/eval/沉淀失败原因.md#F-配音-008`），改写能力迁移至浏览器扩展
- **输入**：`yuanbao_client.py` (REWRITE_TEMPLATE) 保留但默认不再通过服务器调用
- **核心逻辑**：`rewrite_copy` MCP 工具保留作为降级路径；优先走浏览器扩展
- **预期产出**：无新增文件；`app.js` 中改写路由优先检测扩展，降级为原 MCP rewrite_copy

## REMOVED Features

无。`yuanbao_local_proxy.py` 保留但不作为推荐路径（已被浏览器扩展替代）。
