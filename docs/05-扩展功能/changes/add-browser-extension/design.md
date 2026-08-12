# Design（技术设计）

## 1. 架构图

```
┌─────────────────────────────────────────────────────────────┐
│                        用户浏览器                            │
│  ┌──────────────────────────┐  ┌──────────────────────────┐ │
│  │   vu.evenblue.top        │  │   yuanbao.tencent.com    │ │
│  │   ┌────────────────┐     │  │                          │ │
│  │   │   app.js        │     │  │   用户正常登录元宝 ✅    │ │
│  │   │   (检测扩展)    │     │  │                          │ │
│  │   └───────┬────────┘     │  │   content-yuanbao.js     │ │
│  │           │ postMessage  │  │   ├─ 收注入指令          │ │
│  │   content-vu.js ◄───────►│  │   ├─ 上传图片(base64)    │ │
│  │           │               │  │   ├─ 填提示词 + Enter   │ │
│  └───────────┼───────────────┘  │   ├─ 等回复稳定         │ │
│              │                   │   └─ 回传结果           │ │
│              │ chrome.runtime    └──────────┬──────────────┘ │
│              │                   │          │                │
│     ┌────────┴───────────────────┴──────────┴────────┐      │
│     │            background.js (Service Worker)       │      │
│     │  ├─ 收 rewrite 请求                             │      │
│     │  ├─ 调 MCP API: POST /mcp tools/call            │      │
│     │  ├─ 打开/复用元宝标签                            │      │
│     │  └─ 路由结果回网站                              │      │
│     └────────────────────┬────────────────────────────┘      │
└──────────────────────────┼──────────────────────────────────┘
                           │ HTTPS
                    ┌──────┴──────┐
                    │  MCP Server │
                    │  vu.evenblue.top:443
                    │  extract_copy_context
                    └─────────────┘
```

## 2. 消息协议

### 2.1 网站 → 扩展

```js
// app.js 通过 content-vu.js 暴露的 API 调用
const result = await window.__ybExtension.rewrite({
  apiBase: 'https://vu.evenblue.top',  // MCP 服务器地址
  src: 'video.mp4',                     // 视频文件名
  template: '带货',                     // 改写模板
  topic: '海边夕阳',                    // 视频主题
});
// result: { rewritten: "文案...", error: null }
```

### 2.2 扩展内部消息

```js
// content-vu → background
{ action: 'yb-rewrite', data: { apiBase, src, template, topic } }

// background → content-yuanbao
{ action: 'yb-inject', data: { frames_b64, raw_text, template, topic, max_chars } }

// content-yuanbao → background
{ action: 'yb-done', data: { rewritten, error } }

// background → content-vu
{ action: 'yb-result', data: { rewritten, error } }
```

### 2.3 网站接收结果

```js
// content-vu 通过 window.postMessage 回传
window.postMessage({ action: 'yb-rewrite-result', data: { rewritten, error } }, '*');
```

## 3. 元宝 DOM 操作（content-yuanbao.js）

### 3.1 选择器策略

| 操作 | 主选择器 | 备选 |
|------|---------|------|
| 输入框 | `div[contenteditable="true"]` | `textarea[placeholder*="输入"]` |
| 发送按钮 | `Enter` 按键 | 遍历含 `→` / `send` 的按钮 |
| 回复区域 | `.hyc-common-markdown` | `[class*="answer"]` / `[class*="reply"]` |
| 图片上传菜单 | `div[class*="UploadFileSelector_iconContainer"]` | — |
| 图片菜单项 | `getByText("图片")` fallback 按钮遍历 | — |

### 3.2 图片上传

```
base64 frame → Uint8Array → Blob → File
  → document.querySelector('input[type="file"]')  + DataTransfer
  → input.dispatchEvent(new Event('change'))
  → React onChange 触发 → 图片进入输入框
```

备选：直接在元宝页面创建 `<input type="file">` 并触发 click → 用 `showOpenFilePicker` API 不可行 → 改用 DataTransfer hack。

### 3.3 回复稳定检测

沿用 yuanbao_client.py 经验：
1. 等 `is_generating` 变为 false（查找「停止」/「stop」按钮）
2. 读最后一个 `.hyc-common-markdown` 的 innerText
3. 排除中间状态关键词（正在分析/正在搜索/正在生成）
4. 等 2 秒再读一次
5. 两次一致 = 完成

### 3.4 提示词构建

复用 `copy_rewriter._build_prompt()` 的逻辑，在 JS 侧实现简化版：

```
## 视频主题
{topic}

## 角色（可选）
{template}

## 系统要求
1. 保持原文核心信息不变
2. 语气自然口语化
3. 适合语音朗读（TTS）
4. 严格不超过 {max_chars} 字，多余的字请删掉

需要改写的原文：{raw_text}

⚠️ 重要：你输出的文案务必控制在 {max_chars} 字以内，不要超出。
```

## 4. MCP API 调用

background.js 通过 `fetch` 直接调 MCP JSON-RPC：

```js
POST https://vu.evenblue.top/mcp
{
  method: 'tools/call',
  params: {
    name: 'extract_copy_context',
    arguments: { src: 'video.mp4' }
  }
}

// 返回
{
  raw_text: '识别到的文字',
  source: 'subtitle' | 'asr',
  duration: 15.2,
  max_chars: 45,
  frames_b64: ['iVBOR...', 'iVBOR...', 'iVBOR...']
}
```

## 5. app.js 改动

在 rewite 按钮点击时：

```js
if (window.__ybExtension?.ready) {
  // 扩展路径
  const result = await window.__ybExtension.rewrite({ apiBase, src, template, topic });
  displayResult(result);
} else {
  // 降级：不走任何改写，只提示用户安装扩展
  showInstallExtensionPrompt();
}
```

移除 `doRewriteViaProxy`（本地代理路径），只保留扩展路径 + 安装提示。

## 6. 错误处理

| 场景 | 处理 |
|------|------|
| 元宝页面未登录 | 显示「请先在元宝页面登录」 |
| 图片上传失败 | 降级为纯文字改写（无图片） |
| 回复超时(180s) | 提示超时，建议重试 |
| MCP 调用失败 | 提示服务器不可达 |
| 帧提取失败 | 提示视频无可用帧 |
