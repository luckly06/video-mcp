# TTS 配音文案自动生成 · 技术分析与方案

> **归属**：`docs/05-扩展功能/` — 配套扩展能力设计
> **参考项目**：[openteam](https://github.com/afumu/openteam) — 0 API Token 的多 AI 协作工作流
> **核心目标**：不调用付费 API，复用用户浏览器已登录的 AI 会话，自动生成 TTS 配音文案
> **日期**：2026-08-10

---

## 1. openteam 核心技术拆解

openteam 是一个 Chrome Extension，它的核心价值在于「**操控浏览器中已登录的 AI 网站页面，注入 prompt 并抓取回复**」，全程不走 API。

### 1.1 技术栈

| 层面 | 技术 |
|---|---|
| 宿主 | Chrome Manifest V3 Extension |
| 注入层 | Content Script（运行在 AI 网站页面上下文） |
| DOM 操作 | TypeScript + 原生 DOM API |
| 通信 | `chrome.runtime.sendMessage` (content ↔ background) |

### 1.2 核心机制一：Prompt 注入

文件：[`src/content/sites/contentEditable.ts`](https://github.com/afumu/openteam/blob/main/src/content/sites/contentEditable.ts)

```typescript
export function setContentEditableText(editor: HTMLElement, content: string): void {
  editor.focus()
  editor.replaceChildren()                       // 清空输入区

  const block = document.createElement('p')
  block.textContent = content                     // 文本写入 <p>
  editor.append(block)

  // ★ 关键：模拟 InputEvent，让 AI 网站以为用户手动输入
  editor.dispatchEvent(new InputEvent('beforeinput', {
    bubbles: true, inputType: 'insertText', data: content
  }))
  editor.dispatchEvent(new InputEvent('input', {
    bubbles: true, inputType: 'insertText', data: content
  }))
  editor.dispatchEvent(new Event('change', { bubbles: true }))
}
```

**原理**：ChatGPT / Claude / Gemini / DeepSeek 的网页版输入框都是 `contenteditable` 或 `textarea` 元素。这些网站内部监听了 `input` / `beforeinput` 事件来感知用户输入。直接设 `.textContent` 不会触发它们的内部状态更新，必须手工 dispatch 对应的 InputEvent。

### 1.3 核心机制二：发送消息

每个站点 adapter 实现 `fillAndSend(content, autoSend)`：

1. 找到输入框 DOM 元素（每个 AI 网站的 selector 不同）
2. 调用 `setContentEditableText` 填入文本
3. 找到发送按钮并点击（`el.click()`）或模拟 `Enter` 键

### 1.4 核心机制三：回复抓取

文件：[`src/content/sites/types.ts`](https://github.com/afumu/openteam/blob/main/src/content/sites/types.ts)

```typescript
interface ChatSiteAdapter {
  readResponseText(node: Node): string          // 读单个回复节点的纯文本
  getAllAssistantReplies(): string[]             // 读所有 AI 回复
  getResponseContainers(): Element[]             // 找所有回复容器
  isGenerating(): boolean                        // 判断 AI 是否还在生成中
  stopGenerating(): Promise<boolean>             // 中断生成
}
```

**原理**：每个 AI 网站回复区有稳定的 DOM 结构（通常是一组带 `data-message-author-role="assistant"` 属性的 div）。adapter 找到这些容器，遍历子节点提取文本。

---

## 2. 为什么不用 Chrome Extension

openteam 是完整的 Chrome Extension（Manifest V3 + Service Worker + Content Script + iframe 沙箱），架构重量级，适合"通用多模型讨论室"场景。

我们这个项目的需求更窄：**给定视频主题/关键词 → 让一个 AI 模型生成配音文案 → 填入 TTS 输入框**。不需要 iframe、不需要多模型路由、不需要团队 UI。

更轻量的方案：**Playwright**（Python 驱动 Chromium）。

---

## 3. 适配方案：Playwright 文案机

### 3.1 架构

```
┌─ Web UI (去重工位) ─┐
│                       │
│  步骤2 文案区          │
│  ┌──────────────────┐ │
│  │ [主题/关键词输入] │ │  ← 用户输入："游戏打斗集锦 30s"
│  │ [🤖 生成配音文案] │ │  ← 点击触发
│  └──────────────────┘ │
│         │              │
│         ▼              │
│  POST /local/generate-copy          │
│         │              │
└─────────┼──────────────┘
          │
          ▼
┌─ mcp_server ──────────┐
│                        │
│  POST /local/generate-copy          │
│         │               │
│         ▼               │
│  copy_generator.py      │  ← 🆕 新增模块
│         │               │
│         ▼               │
│  Playwright + Chromium  │  ← 操控 AI 网站
│    ├─ 打开 DeepSeek     │  (免费、中文好)
│    ├─ 注入 prompt       │
│    ├─ 等待回复          │
│    └─ 抓取文本          │
│         │               │
│         ▼               │
│  返回文案 → 前端填入 TTS │
└────────────────────────┘
```

### 3.2 为什么选 DeepSeek 作为主目标

| 对比 | DeepSeek | ChatGPT | Gemini |
|---|---|---|---|
| 免费额度 | ✅ 慷慨 | ❌ 有限 | ✅ 有 |
| 中文能力 | ✅ 顶级 | ✅ 好 | ⚠️ 一般 |
| DOM 稳定性 | ✅ 较稳定 | ⚠️ 频繁改版 | ⚠️ 经常改版 |
| 登录态持久 | ✅ 较长 | ⚠️ 短 | ⚠️ 短 |
| 反自动化 | ⚠️ 有 Cloudflare | ⚠️ 有 | ⚠️ 有 |

**首选 DeepSeek**，备选 ChatGPT（用户大概率有账号）。

### 3.3 核心实现：Prompt 注入 + 回复抓取

复用 openteam 的两大核心机制，用 Playwright 的 `page.evaluate()` 执行：

```python
# 1. Prompt 注入（直接访问 contenteditable 元素）
await page.evaluate("""
    (text) => {
        const editor = document.querySelector('[contenteditable="true"]');
        if (!editor) throw new Error('未找到输入框');
        editor.focus();
        editor.innerHTML = '<p>' + text + '</p>';
        editor.dispatchEvent(new InputEvent('input', {
            bubbles: true, inputType: 'insertText', data: text
        }));
    }
""", prompt_text)

# 2. 点击发送按钮（不同网站 selector 不同）
await page.click('button[aria-label="发送"]')   # DeepSeek 的选择器

# 3. 等待回复完成（轮询 isGenerating 直到 false）
while True:
    generating = await page.evaluate("""
        () => !!document.querySelector('.stop-btn, .generating-indicator')
    """)
    if not generating:
        break
    await asyncio.sleep(1)

# 4. 抓取最后一条 AI 回复
copy_text = await page.evaluate("""
    () => {
        const replies = document.querySelectorAll('[data-role="assistant"], .ds-markdown');
        const last = replies[replies.length - 1];
        return last ? last.innerText.trim() : '';
    }
""")
```

### 3.4 需要适配的 AI 站点

| 站点 | 输入框 selector | 发送按钮 selector | 回复容器 selector |
|---|---|---|---|
| DeepSeek | `[contenteditable="true"]` | `[role="button"]` 发送图标 | `.ds-markdown` |
| ChatGPT | `#prompt-textarea` | `[data-testid="send-button"]` | `[data-message-author-role="assistant"]` |

### 3.5 浏览器配置文件

Playwright 使用用户的 Chrome 用户数据目录，保持已登录状态：

```python
context = browser.new_context(
    storage_state=None,           # 不覆盖，用 Chrome 原有 cookie
)
# 或指定 Chrome Profile 路径
browser = p.chromium.launch_persistent_context(
    user_data_dir=r"C:\Users\lucky\AppData\Local\Google\Chrome\User Data",
    channel="chrome",
    headless=False,                # 必须可见：反自动化检测
)
```

---

## 4. 系统提示词设计

```python
SYSTEM_PROMPT = """你是一个专业的短视频配音文案撰写人。

## 任务
根据用户提供的视频主题，生成一条适合 TTS 配音的短视频旁白文案。

## 要求
1. **时长适配**：文案长度应该适合 15-60 秒的短视频，通常在 30-100 字之间
2. **口语化**：用自然的说话语气，不要书面语
3. **有钩子**：开头 2 秒抓注意力（悬念/提问/冲击性陈述）
4. **有行动**：结尾留互动引导（"点赞关注"、"评论区聊聊"等）
5. **纯文案**：只输出最终文案，不要解释、不要标注、不要前缀
6. **一行到底**：不分段、不换行，整段输出

## 示例
输入：游戏打斗集锦
输出：你见过一秒三杀的操作吗？今天这波团战直接封神！来感受一下什么叫天花板级别的操作。喜欢的兄弟点个赞，下期更精彩！

输入：美食探店
输出：这家藏在巷子深处的店，我找了三年才找到！老板说一天只卖 100 份，来晚了真的吃不到。赶紧艾特你那个总说"下次一定"的朋友，这周就安排上！"""

COPY_GEN_PROMPT = """视频主题：{topic}

请生成配音文案："""
```

---

## 5. 与现有项目的集成点

| 集成点 | 改动 |
|---|---|
| `station/server/mcp_server.py` | 新增 `POST /local/generate-copy` 路由 |
| `station/server/copy_generator.py` | 🆕 新增模块：Playwright 驱动的文案生成 |
| `station/web/index.html` | TTS 文案区增 `🤖 AI 生成文案` 按钮 |
| `station/web/app.js` | 按钮点击 → POST /local/generate-copy → 填入 tts-text |
| `station/requirements.txt` | 增 `playwright` |

---

## 6. 安全与边界

| 维度 | 措施 |
|---|---|
| 浏览器会话 | Playwright 使用 `persistent_context` + 用户 Chrome Profile，不存储密码 |
| 反自动化 | `headless=False`（可见模式），避免被 AI 网站检测为 bot |
| 超时保护 | 单次生成最长等待 60s，超时返回错误 |
| 降级 | Playwright 未安装或 Chrome 未登录 → 提示用户手动填入文案 |
| 隔离 | copy_generator 为独立模块，不影响现有去重管线 |

---

## 7. 实现路径（AI 执行订单）

### Feature EX-1 — 单站点文案生成器（DeepSeek）

- **输入**：DeepSeek 已登录的 Chrome 浏览器 + 视频主题/关键词
- **核心逻辑**：
  1. 新增 `station/server/copy_generator.py`：Playwright 启动 Chromium → 打开 DeepSeek → 注入 system prompt + 用户主题 → 等待回复 → 提取文本
  2. `mcp_server.py` 新增 `POST /local/generate-copy` 路由：接收 `{"topic": "...", "site": "deepseek"}` → 调 copy_generator → 返回 `{"text": "..."}`
  3. 前端 TTS 区增 `🤖 AI 生成文案` 按钮：检测 copy_generator 可用性（`GET /local/copy-generator-status`）
- **预期产出**：用户点按钮 → 等 5-15s → TTS 输入框自动填入 AI 生成的文案

### Feature EX-2 — 多站点兜底

- 在当前 site 失败时自动切换备选站点（DeepSeek ↓ → ChatGPT ↓）
- 前端展示当前使用的 AI 来源

### Feature EX-3 — 历史文案库

- 用户对同主题多次生成的文案做对比选择
- 点赞/踩标记好的 prompt 模板

---

## 附录 A：Playwright vs Chrome Extension 对比

| | Playwright 方案 | Chrome Extension 方案 |
|---|---|---|
| 实现语言 | Python（与项目统一） | TypeScript（需独立构建） |
| 部署复杂度 | `pip install playwright` | 加载扩展 + Service Worker |
| 浏览器控制粒度 | 完全控制（DOM/网络/CDP） | 受限于 Content Script 沙箱 |
| 反自动化风险 | 中等（`headless=False`） | 低（真实用户操作） |
| 维护成本 | 低（Python 单一代码库） | 高（独立 TS 项目 + 构建链） |
| 本项目适配 | ✅ 与 mcp_server 同进程 | ❌ 需独立扩展 + IPC 通信 |

**结论**：Playwright 方案更适合本项目的"轻量集成"定位。
