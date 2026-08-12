# add-browser-extension · 浏览器扩展元宝改写桥接

> 提案日期：2026-08-11 | 影响级别：**B**（新增功能模块，跨模块）

## 动机

服务器端无头 Chromium 元宝扫码登录经完整 E2E 验证**不可行**（详见 `docs/eval/沉淀失败原因.md#F-配音-008`）：
- 元宝 QR 登录走 `open.weixin.qq.com` iframe OAuth redirect 链
- headless Chromium 跨域 iframe cookie 写入不可靠
- 扫码成功但 cookies 只有匿名指纹，无 auth token

用户践行「零 token」理念，拒绝 API Key 方案。同时用户面向的终端用户不懂命令行，`.exe` 打包也有毒报/信任问题。

## 方案

浏览器扩展（Chrome/Edge MV3），安装后零配置零操作：

```
扩展安装（一次）
  → vu.evenblue.top 检测扩展就绪
  → 用户点「改写预览」
  → 网站 → 扩展 (postMessage) → background.js → MCP extract_copy_context
  → background.js 打开元宝标签页 → content-yuanbao.js 注入 prompt + 图片
  → 读取元宝回复 → 回传 background → 回传网站 → 展示结果
```

## 影响面

| 检查项 | 状态 |
|--------|------|
| 接口定义变更 | 否（新增 content script 之间消息通道，不改 MCP 接口） |
| DB 表字段变更 | 否 |
| 异步消息变更 | 否 |
| 配置项变更 | 否 |
| 定时任务变更 | 否 |

**新增模块**：`station/extension/` — manifest.json + background.js + content-vu.js + content-yuanbao.js + icons

**修改文件**：`station/web/app.js` — 扩展检测 + 改写路由逻辑

**不影响**：服务器端所有现有功能（上传/ASR/去重/TTS）保持原样。

## 风险

- 元宝 DOM class name 变更会导致 `content-yuanbao.js` 选择器失效 → 需要观察性维护
- 图片上传依赖 DataTransfer API，元宝 React 组件可能不响应 → 备选方案：FileReader + input[type=file]
- 回复抓取可能拿到中间状态文本 → 需要稳定检测（沿用已有经验：双采样等文本不变）

## Open Questions

- [x] 元宝输入框选择器是否稳定？→ 用 `div[contenteditable="true"]` 兜底
- [ ] 图片上传 API 在真实元宝页面上是否可用？→ 待端到端验证
- [x] 元宝回复抓取选择器？→ 沿用 `.hyc-common-markdown` + 排除中间状态关键词
