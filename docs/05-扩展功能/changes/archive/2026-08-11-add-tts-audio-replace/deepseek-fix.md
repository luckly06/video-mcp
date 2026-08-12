# DeepSeek 改写修复 · 参照 OpenTeam 技术

## 目标

修 `station/server/copy_rewriter.py`，使 Playwright 能正确打开 DeepSeek 网页、注入文案、发送、等待回复、抓取结果。

## 当前问题

1. **浏览器打开显示 about:blank** — `page.goto("https://chat.deepseek.com/")` 失败（网络/超时），但错误被吞掉
2. **文本注入方式错了** — 当前用 `contentEditable` + `document.querySelector('[contenteditable="true"]')` 往 div 写入，但 **DeepSeek 实际用的是 `<textarea>`**
3. **发送按钮查找太粗糙** — 当前用 `[role="button"]:has(svg)` 这种，容易找不到
4. **回复检测不靠谱** — 当前轮询 `loading/spinner` 类名，不够准确
5. **profile 锁问题** — Edge/Chrome 开着时 `launch_persistent_context` 报错退出

## OpenTeam 的做法（来源：afumu/openteam/src/content/sites/deepseek.ts）

### DeepSeek 的输入框是 TEXTAREA，不是 contenteditable！

```typescript
// OpenTeam 的 selector（重要！）
const DEEPSEEK_SELECTORS = {
  editor: 'textarea[name="search"], textarea[placeholder*="DeepSeek"], textarea[placeholder*="发送消息"]',
  response: '[data-virtual-list-item-key] .ds-message .ds-markdown:not(.ds-think-content .ds-markdown)',
  responseContainer: '[data-virtual-list-item-key]',
  composer: '.aaff8b8f, ._77cefa5, [class*="composer"]',
  sendButton: '.bf38813a [role="button"], .bf38813a button, [role="button"]._52c986b, button._52c986b, [role="button"].ds-icon-button, button.ds-icon-button, [role="button"].ds-button--primary.ds-button--filled.ds-button--circle, button.ds-button--primary.ds-button--filled.ds-button--circle',
}
```

### 文本注入方式（textarea.value + InputEvent dispatch）

```typescript
function setTextareaText(textarea, content) {
  textarea.focus()
  textarea.value = content
  textarea.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: content }))
  textarea.dispatchEvent(new Event('change', { bubbles: true }))
}
```

**不是** `editor.innerHTML = '<p>...</p>'`，**不是** `contentEditable` 方式。

### 发送按钮检测（轮询直到出现可点击的按钮）

```typescript
async function waitForDeepSeekSendButton(editor, timeoutMs) {
  const startedAt = Date.now()
  while (Date.now() - startedAt <= timeoutMs) {
    const button = findDeepSeekSendButton(editor)
    if (button) return button
    await new Promise(resolve => setTimeout(resolve, 50))
  }
  throw new Error('DeepSeek 发送按钮暂不可用')
}
```

### 发送按钮判定（排除禁用/附件/语音按钮）

```typescript
function isDeepSeekSendButton(element) {
  if (element.getAttribute('aria-disabled') === 'true') return false
  if (element instanceof HTMLButtonElement && element.disabled) return false
  if (element.classList.contains('ds-toggle-button')) return false
  if (buttonLabelMatches(element, /attach|upload|file|camera|image|voice|microphone|附件|上传|图片|语音/)) return false
  if (!相关class匹配) return false
  return isVisibleInteractiveElement(element)
}
```

### 回复检测（找「停止」按钮是否存在）

```typescript
function isDeepSeekGenerating() {
  return Boolean(findDeepSeekStopButton())  // 存在"停止"按钮 = 还在生成
}

function findDeepSeekStopButton() {
  return [...document.querySelectorAll('[role="button"], button')]
    .find(btn => buttonLabelMatches(btn, /stop|stopping|停止|中止/) && isClickableDeepSeekButton(btn))
}
```

### 抓取回复

```typescript
function getResponseContainers() {
  return [...document.querySelectorAll('.ds-markdown')]  // 不是 .ds-markdown 前面的 data-virtual-list-item-key
}
```

## 具体要改的代码

### 1. 修改 `_rewrite_async` 中的文本注入

**删掉**当前的内容：
```python
await page.evaluate("""
    (text) => {
        const editor = document.querySelector('[contenteditable="true"]');
        if (!editor) throw new Error('未找到 DeepSeek 输入框');
        editor.focus();
        editor.innerHTML = '';
        const lines = text.split('\\n');
        lines.forEach(line => {
            const p = document.createElement('p');
            p.textContent = line;
            editor.appendChild(p);
        });
        editor.dispatchEvent(new InputEvent('input', {...}));
        editor.dispatchEvent(new Event('change', {...}));
    }
""", prompt)
```

**替换为**（参照 OpenTeam 的 textarea 方式）：
```python
# 等待 textarea 出现
await page.wait_for_selector('textarea[name="search"]', timeout=10000)

# 注入文本到 textarea（参照 OpenTeam setTextareaText）
await page.evaluate("""
    (text) => {
        const editor = document.querySelector('textarea[name="search"]');
        if (!editor) throw new Error('未找到输入框');
        editor.focus();
        editor.value = text;
        editor.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: text }));
        editor.dispatchEvent(new Event('change', { bubbles: true }));
    }
""", prompt)
```

### 2. 修改发送按钮查找

**删掉**当前：
```python
send_btn = await page.query_selector('[role="button"]:has(svg)')
# fallback...
```

**替换为**（参照 OpenTeam 的 selector）：
```python
# 用 OpenTeam 的 sendButton selector 找发送按钮
send_btn = await page.wait_for_selector(
    '.bf38813a [role="button"], .bf38813a button, '
    '[role="button"]._52c986b, button._52c986b, '
    '[role="button"].ds-icon-button, button.ds-icon-button',
    timeout=10000
)
if send_btn:
    await send_btn.click()
else:
    raise Exception("找不到 DeepSeek 发送按钮")
```

### 3. 修改回复等待逻辑

**删掉**当前的 loading/spinner 轮询。

**替换为**（参照 OpenTeam 的 isGenerating — 找停止按钮）：
```python
# 等待回复完成（参照 OpenTeam isDeepSeekGenerating：存在停止按钮 = 还在生成）
start = asyncio.get_event_loop().time()
while True:
    still_generating = await page.evaluate("""
        () => {
            const stopBtn = [...document.querySelectorAll('[role="button"], button')]
                .find(btn => /停止|stop/i.test(btn.textContent || btn.getAttribute('aria-label') || ''));
            return !!stopBtn;
        }
    """)
    if not still_generating:
        break
    if asyncio.get_event_loop().time() - start > timeout:
        raise Exception("DeepSeek 回复超时")
    await asyncio.sleep(1.5)
```

### 4. 修改回复抓取

```python
# 参照 OpenTeam: '.ds-message .ds-markdown' 但排除思考内容
reply_text = await page.evaluate("""
    () => {
        const containers = document.querySelectorAll('.ds-markdown');
        const last = containers[containers.length - 1];
        return last ? last.innerText.trim() : '';
    }
""")
```

### 5. goto 失败处理

当前已有 try/except 包裹，确认 `get_last_error()` 能传回前端。

### 6. 验证文本已注入

参照 OpenTeam 的做法，注入后校验：
```python
actual_val = await page.evaluate("""
    () => {
        const editor = document.querySelector('textarea[name="search"]');
        return editor ? editor.value.trim() : '';
    }
""")
if actual_val.replace('\r\n', '\n').strip() != prompt.replace('\r\n', '\n').strip():
    raise Exception("文本未成功注入 DeepSeek 输入框")
```

## 关键依赖

- `playwright`（已安装于 venv）
- DeepSeek 网页 chat.deepseek.com 可访问
- 使用前**关闭 Edge/Chrome**（profile 锁问题）
