# Playwright 自动化给元宝发送"图片+文案"提示词（完整实战笔记）

> 踩坑 2026-08-11，历时约 3 小时 | 最终方案已验证通过  
> 适用：任何需要用 Playwright 给腾讯元宝（yuanbao.tencent.com）自动发送带图片的消息的场景

---

## 目录

1. [完整工作流程](#完整工作流程)
2. [踩坑清单（5 次失败）](#踩坑清单5-次失败)
3. [正确实现代码](#正确实现代码)
4. [流式回复提取](#流式回复提取)
5. [字数约束提示词](#字数约束提示词)
6. [浏览器复用](#浏览器复用)
7. [`str.format()` 模板转义](#strformat-模板转义)
8. [截图诊断法](#截图诊断法)
9. [集成备忘](#集成备忘)

---

## 完整工作流程

```
导航到 yuanbao.tencent.com
    → 等页面加载 (wait_for_selector contenteditable)
    → 检查登录态
    → 点击 "+" 图标（UploadFileSelector_iconContainer）
    → 菜单弹出（图片/本地文件/腾讯文档 三选项）
    → 点击 "图片"
    → React 触发原生文件对话框
    → Playwright file_chooser 拦截 → set_files 批量传图
    → 等 5 秒（图片在输入框显示缩略图）
    → 填改写提示词（含字幕/ASR 原文 + 角色模板 + 字数约束）
    → 按 Enter 发送
    → 等回复（稳定检测 + 过滤中间状态）
    → 读完整回复内容
    → 返回结果
```

---

## 踩坑清单（5 次失败）

### 坑 1：`set_input_files` 找不到元素

| 项目 | 内容 |
|------|------|
| **尝试** | `page.locator('input[type="file"]').set_input_files(frames)` |
| **结果** | 找到 0 个元素 |
| **原因** | 元宝是 Next.js SPA，**页面 DOM 里根本没有 `<input type="file">`**。文件上传走自定义 React 组件。 |
| **教训** | 动手前先 dump 页面 DOM 看实际结构，别假设标准 HTML 元素一定存在。 |

### 坑 2：`file_chooser` 拦截不到文件对话框

| 项目 | 内容 |
|------|------|
| **尝试** | `page.expect_file_chooser()` + 点击 `UploadFileSelector_iconContainer` |
| **结果** | file_chooser 超时 |
| **原因** | 点击 "+" 只是**弹出下拉菜单**（图片/本地文件/腾讯文档），**不直接弹出文件对话框**。点菜单里的"图片"才触发。 |
| **教训** | SPA 组件的交互可能比你想象的多一层——先检查 UI 是不是多步骤。 |

### 坑 3：`element.click()` 被 React 忽略

| 项目 | 内容 |
|------|------|
| **尝试** | `page.evaluate("document.querySelector('...').click()")` 点 "图片" |
| **结果** | 菜单没反应，文件对话框没出来 |
| **原因** | JS 原生 `click()` 触发的 `MouseEvent.isTrusted=false`。React 的事件系统 **只处理 isTrusted=true 的真实用户事件**。 |
| **教训** | 在 React/Vue 等现代框架上自动化，**必须用 Playwright 的真鼠标事件**（`.click()` / `.fill()` / `.press()`），别用 `page.evaluate` 里的 `el.click()`。 |

### 坑 4：`MutationObserver` 等不到 file input

| 项目 | 内容 |
|------|------|
| **尝试** | MutationObserver 监听 DOM，点击 "+" 后等 `<input type="file">` 出现 |
| **结果** | 8 秒超时，没出现 |
| **原因** | file input 只在点击**子菜单的"图片"**后才动态创建，点击 "+" 只弹出菜单。 |
| **教训** | Observer 的时机要和触发动作对齐——监听的是点击"图片"后，不是点击"+"后。 |

### 坑 5：`text="图片"` 选择器匹配失败

| 项目 | 内容 |
|------|------|
| **尝试** | `page.locator('text="图片"').first` |
| **结果** | 找到 1 个元素但 `.click()` 后菜单还在（没点中） |
| **原因** | `text="图片"` 可能匹配到了非菜单项的文本节点，或者元素路径不对。 |
| **教训** | 用 `page.get_by_text("图片", exact=True)` 做精确文本匹配，`force=True` 跳过可见性检查。 |

---

## 正确实现代码

```python
import asyncio, socket, subprocess
from playwright.async_api import async_playwright

async def upload_and_rewrite(frames, raw_text, topic, rewrite_template, max_chars, timeout=120):
    """完整的元宝图片+文案发送 + 回复提取流程。"""

    # ---- 浏览器启动（复用已有 CDP） ----
    CDP = "http://127.0.0.1:9223"
    need_launch = False
    try:
        s = socket.create_connection(("127.0.0.1", 9223), timeout=2)
        s.close()
    except:
        need_launch = True
    if need_launch:
        subprocess.Popen([
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            "--remote-debugging-port=9223",
            "--user-data-dir=<your-profile-dir>",
            "--no-first-run",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        await asyncio.sleep(3)

    p = await async_playwright().start()
    browser = await p.chromium.connect_over_cdp(CDP)
    ctx = browser.contexts[0]
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    await page.goto("https://yuanbao.tencent.com/", wait_until="domcontentloaded")
    await asyncio.sleep(3)

    # 等输入框就绪
    await page.wait_for_selector('div[contenteditable="true"]', timeout=15000)

    # ---- Step 1: 点击 "+" → 打开下拉菜单 ----
    ub = page.locator('div[class*="UploadFileSelector_iconContainer"]').first
    await ub.click()
    await asyncio.sleep(1)

    # ---- Step 2: 点击 "图片" → 拦截文件对话框 ----
    pic = page.get_by_text("图片", exact=True).first
    if await pic.count() == 0:
        pic = page.locator('div').filter(has_text="图片").first

    async with page.expect_file_chooser(timeout=10000) as fc:
        await pic.click(force=True)
    chooser = await fc.value
    await chooser.set_files(frames[:3])   # 一次传多张
    await asyncio.sleep(5)                # 等缩略图出现

    # ---- Step 3: 填提示词并发送 ----
    prompt = build_prompt(raw_text, rewrite_template, max_chars, topic)
    editor = page.locator('div[contenteditable="true"]').first
    await editor.click()
    await asyncio.sleep(0.3)
    await editor.fill(prompt)
    await asyncio.sleep(0.3)
    bl = await page.locator(
        '.hyc-common-markdown,[class*="answer"],[class*="reply"],[class*="bubble"]'
    ).count()
    await page.keyboard.press("Enter")

    # ---- Step 4: 等回复（稳定检测） ----
    rw = ""
    t0 = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - t0 < timeout:
        if not await is_generating(page):
            t = read_last_reply(page, bl)
            if t:
                await asyncio.sleep(2)
                if not await is_generating(page):
                    t2 = read_last_reply(page, bl)
                    if t2 == t:           # 文本稳定不变 → 生成完成
                        rw = clean_reply(t)
                        break
                    t = t2
        await asyncio.sleep(1.5)

    # 清理（不关浏览器，下次复用）
    await p.stop()
    return rw
```

---

## 流式回复提取

### 坑：抓到半句话

元宝是流式输出（类似 ChatGPT），`is_generating` 在两个字之间会短暂返回 `false`，导致抓到不完整的文本。比如"电梯里遇到这种情况，你慌不"(13 字)，实际完整输出 40+ 字。

### 解决方案：双采样稳定检测

```python
if not await is_generating(page):
    t = read_last_reply(page, bl)
    if t:
        await asyncio.sleep(2)                 # 等 2 秒
        if not await is_generating(page):       # 确认没在生成
            t2 = read_last_reply(page, bl)
            if t2 == t:                         # 文本不变 → 已写完
                rw = t
                break
            t = t2                              # 还在变，继续等
```

### 坑：中间状态文本

元宝生成时会先显示"正在分析图片"、"正在搜索"等中间状态，被误当成回复。

```python
def read_last_reply(page, bl):
    a = page.locator('.hyc-common-markdown,[class*="answer"],[class*="reply"],[class*="bubble"]')
    cnt = await a.count()
    if cnt > bl:
        t = (await a.nth(cnt - 1).inner_text()).strip()
        skip = ["正在分析", "正在搜索", "正在生成", "正在思考", "正在处理",
                "analyzing", "searching", "generating"]
        if t and len(t) > 2 and not any(kw in t for kw in skip):
            return t
    return ""
```

---

## 字数约束提示词

### 坑：元宝不严格遵守字数限制

提示词里写"不超过 47 字"，元宝经常输出 50-60 字。语言模型对数值约束是概率性的，需要在 prompt 设计上加强。

### 解决方案

1. 中间要求用**强制性**措辞："严格不超过 XX 字，多余的字请删掉"
2. 在 prompt **结尾**再强调一次（模型对最后一句约束最敏感）：

```
⚠️ 重要：你输出的文案务必控制在 47 字以内，不要超出。
```

完整 prompt 结构：
```
## 视频主题
这个视频的内容是：海边夕阳慢动作

## 角色（可选）
你是带货主播，改写为口播文案

## 系统要求
1. 保持原文核心信息不变
2. 语气自然口语化
3. 适合语音朗读（TTS）
4. **时长适配**：严格不超过 47 字（视频仅 16 秒），多余的字请删掉。

需要改写的原文：哎你等一下这好吗什么了电梯你家开了没有意思

⚠️ 重要：你输出的文案务必控制在 47 字以内，不要超出。
```

---

## 浏览器复用

### 坑：每次启新 Edge，浏览器越堆越多

子进程模式下，每次调用 `subprocess.Popen(msedge)` 开新浏览器窗口，不杀旧的就越积越多。

### 解决方案

```python
import socket

def connect_browser():
    CDP = "http://127.0.0.1:9223"
    need_launch = False
    try:
        s = socket.create_connection(("127.0.0.1", 9223), timeout=2)
        s.close()
    except:
        need_launch = True
    
    if need_launch:
        subprocess.Popen([
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            "--remote-debugging-port=9223",
            "--user-data-dir=<profile>",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        asyncio.sleep(3)
    
    browser = await async_playwright().start().chromium.connect_over_cdp(CDP)
    return browser

# 结束时只 disconnect playwright，不关浏览器
# await p.stop()  ← 不调 browser.close()
```

---

## `str.format()` 模板转义

### 坑：`KeyError: 'e'`

如果你把 Playwright 脚本放在 Python 模板字符串里，通过 `.format()` 填充参数（如本项目的 `REWRITE_TEMPLATE`），f-string 里的花括号会被 `.format()` 误当成占位符。

### 规则

| 你想在生成脚本里写 | 模板里应写 | 说明 |
|-------------------|-----------|------|
| `frames = [...值...]` | `frames = {frames}` | 占位符 → 放值 |
| f-string: `{e}` | `{{e}}` | 双花 → 单花 |
| f-string: `{len(fs)}` | `{{len(fs)}}` | 表达式同理 |
| JS 对象 `{key: val}` | `{{key: val}}` | JS 花括号也要转 |

### 三引号冲突

如果模板用 `'''...'''`，内部多行 JS 字符串用 `"""..."""` 避免提前闭合模板。永远不要在 `'''` 模板内再用 `'''`。

---

## 截图诊断法

**踩坑数小时的根因：一直在猜页面状态。**

每步操作后截图 dump，5 分钟就能发现问题，比看代码猜 2 小时有效得多：

```python
await page.screenshot(path="01_before.png")
await upload_btn.click()
await asyncio.sleep(1)
await page.screenshot(path="02_menu_open.png")  # 这步发现了菜单存在！
await pic_option.click()
await page.screenshot(path="03_after_pic.png")
```

同时 dump 页面 DOM 到文件：

```python
html = await page.content()
Path("page_dump.html").write_text(html, encoding="utf-8")
# 然后搜索 "input" "upload" "file" 等关键词找真实元素
```

---

## 集成备忘

- **CDP 模式**：通过 `chromium.connect_over_cdp` 连已有浏览器，不被 Python 进程绑定生命周期
- **profile 隔离**：CDP 浏览器用专用 `--user-data-dir`，不和用户日常 Edge 冲突
- **fill vs type**：`contenteditable` 的 div 用 `.fill()` 而不是 `.type()`（React onChange 只监听 input 事件）
- **重试不要清浏览器**：CDP disconnect 后不关 browser → 下次直连复用 → 登录态保持
- **兜底**：如果 `expect_file_chooser` 失败，等 1 秒后搜 `input[type="file"]`，找到了就 `set_input_files`（兜底策略）
