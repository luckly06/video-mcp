# Tasks（WBS 增量）

## 1. 浏览器扩展模块

### 1.1 扩展骨架

- [x] 1.1.1 创建 `manifest.json`（MV3，host_permissions 含 vu.evenblue.top + yuanbao.tencent.com）
  - **依赖**：无
  - **验收**：能通过 Chrome `加载已解压的扩展` 加载，无报错
  - **估时**：0.5h

- [x] 1.1.2 创建扩展图标（16/48/128 px PNG 占位）
  - **依赖**：无
  - **验收**：扩展管理页显示图标
  - **估时**：0.2h

### 1.2 Background Service Worker

- [x] 1.2.1 `background.js` — 消息路由 + MCP API 调用
  - **依赖**：1.1.1
  - **验收**：收到 yb-rewrite 消息后能正确调用 MCP extract_copy_context 并返回数据
  - **估时**：1h

- [x] 1.2.2 `background.js` — 元宝标签管理（打开/复用/超时处理）
  - **依赖**：1.2.1
  - **验收**：元宝标签打开后 content-yuanbao.js 能收到注入消息
  - **估时**：0.5h

### 1.3 Content Scripts

- [x] 1.3.1 `content-vu.js` — 网站桥接（expose `window.__ybExtension.rewrite()`）
  - **依赖**：1.2.1
  - **验收**：vu.evenblue.top 页面能调用 rewrite() 并收到结果
  - **估时**：0.5h

- [ ] 1.3.2 `content-yuanbao.js` — 元宝页面 DOM 操作（输入框注入、图片上传、回复抓取）
  - **依赖**：1.2.2
  - **验收**：端到端验证：点改写 → 元宝标签打开 → 图片出现 → 文本填入 → 发送 → 回复被抓取回来
  - **估时**：2h
  - **风险**：最高风险项，元宝 DOM 结构可能变化

### 1.4 端到端验证

- [ ] 1.4.1 扩展加载 → 访问 vu.evenblue.top → 上传视频 → 点改写 → 验证完整流程
  - **依赖**：1.3.2
  - **验收**：改写结果正确显示在网站上
  - **估时**：1h

## 2. 网站前端模块

### 2.1 app.js 改写路由

- [x] 2.1.1 检测 `window.__ybExtension` 代替 `ybProxyReady`
  - **依赖**：1.1.1
  - **验收**：扩展已安装时显示 🟢 扩展已连接，未安装时显示安装提示
  - **估时**：0.5h

- [x] 2.1.2 `doRewriteViaExtension()` 函数替代 `doRewriteViaProxy()`
  - **依赖**：1.3.1
  - **验收**：调用 `window.__ybExtension.rewrite()`，成功时展示结果
  - **估时**：0.5h

### 2.2 index.html

- [x] 2.2.1 扩展状态指示器（替换原来 proxy indicator）
  - **依赖**：2.1.1
  - **验收**：连接状态正确显示
  - **估时**：0.2h

## 3. 部署

- [ ] 3.1 部署扩展相关静态文件到服务器
  - **依赖**：1.3.2
  - **验收**：服务器上有完整的 extension/ 目录
  - **估时**：0.2h

- [ ] 3.2 提供用户安装指南（加载已解压扩展 / 打包 .crx）
  - **依赖**：3.1
  - **验收**：用户能按指南安装
  - **估时**：0.3h
