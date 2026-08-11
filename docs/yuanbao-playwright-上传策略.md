# 元宝 Playwright 上传策略（可复用）

> **适用场景**：Playwright 自动化将文本提示词 + 多张图片一起发给腾讯元宝（yuanbao.tencent.com），获取 AI 改写/识图回复。
>
> **难度**：高（React/Next.js SPA，自定义上传组件，多步骤菜单交互）
>
> **验证**：2026-08-11，station/server/yuanbao_client.py 端到端通过。

---

## 核心陷阱

### 1. 元宝没有原生 `<input type="file">`
元宝是 Next.js SPA，上传走自定义组件 `UploadFileSelector_iconContainer`。DOM 中**没有**任何 `<input type="file">` 元素。所有基于 `set_input_files` 或 `MutationObserver` 监听 file input 的策略都会失败。

### 2. 上传是两步，不是一步
- 第 1 步：点击 "+" 图标 → **弹出下拉菜单**（图片 / 本地文件 / 腾讯文档）
- 第 2 步：在菜单中点击 "图片" → **触发原生文件对话框**

之前所有失败的根本原因：点 "+" 后直接等 file dialog，但 "+" 只是打开菜单。

### 3. React isTrusted 检查
`element.click()`（JS 原生）触发 `isTrusted=false`，React 会忽略。必须用 Playwright 真鼠标事件。

### 4. 回复内容过滤
元宝生成过程中会先显示"正在分析图片""正在搜索"等中间状态文案。提取回复时需要过滤。

---

## 正确实现

```python
# === step 1: 点击 '+' 打开菜单 ===
ub = page.locator('div[class*="UploadFileSelector_iconContainer"]').first
await ub.click()
await asyncio.sleep(1)

# === step 2: Playwright 真鼠标点击 "图片" 菜单项 + 拦截文件对话框 ===
pic = page.get_by_text("图片", exact=True).first
if await pic.count() == 0:
    pic = page.locator('div').filter(has_text="图片").first

async with page.expect_file_chooser(timeout=10000) as fc:
    await pic.click(force=True)
chooser = await fc.value
await chooser.set_files(frames[:3])  # 批量传多张图
await asyncio.sleep(5)               # 等图片在输入框显示缩略图

# === step 3: 填提示词（文案，含字幕/ASR 原文）并发送 ===
await editor.click()
await editor.fill(prompt)
await page.keyboard.press("Enter")

# === step 4: 等回复，过滤中间状态 ===
while True:
    if not await is_generating(page):
        t = await read_last_reply(page, bl)
        if t:
            rw = t
            break
    await asyncio.sleep(1.5)
```

### 读回复时过滤中间状态
```python
async def read_last_reply(page, bl):
    a = page.locator('.hyc-common-markdown,[class*="answer"],[class*="reply"],[class*="bubble"]')
    cnt = await a.count()
    if cnt > bl:
        t = (await a.nth(cnt - 1).inner_text()).strip()
        # 过滤元宝生成中的中间状态
        skip = ["正在分析", "正在搜索", "正在生成", "正在思考", "正在处理",
                "analyzing", "searching", "generating"]
        if t and len(t) > 2 and not any(kw in t for kw in skip):
            return t
    return ""
```

---

## 模板系统注意事项

如果 Playwright 操作封装在子进程的 Python 模板字符串中（通过 `.format()` 动态生成脚本）：

### `.format()` 转义规则
模板中所有 f-string 变量必须双花括号：

| 模板写法 | `.format()` 后 | 生成脚本效果 |
|----------|---------------|------------|
| `{frames}` | 替换为值 | Python 变量赋值 |
| `{{e}}` | `{e}` | f-string 变量引用 |
| `{{len(fs3)}}` | `{len(fs3)}` | f-string 表达式 |

**典型 bug**：`print(f"error: {e}")` → `.format()` 找 `e=` kwarg → `KeyError('e')`

### 三引号冲突
模板是 `'''...'''`，内部 JS/多行字符串用 `"""..."""` 避免提前闭合模板。

---

## 浏览器复用

```python
import socket
# 检测已有 CDP → 复用，不启新浏览器
need_launch = False
try:
    s = socket.create_connection(("127.0.0.1", 9223), timeout=2)
    s.close()
except:
    need_launch = True
if need_launch:
    subprocess.Popen([...msedge.exe, "--remote-debugging-port=9223", ...])
# 连已有
browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9223")
```

CDP disconnect 后不关 browser，下次复用同一窗口。

---

## 截图诊断工作流

调试 SPA 交互时，每一步后截图比看代码猜有效 10 倍：

```python
await page.screenshot(path="yb_before.png")     # 点击前
await ub.click()
await page.screenshot(path="yb_menu_open.png")   # 菜单打开
await pic.click()
await page.screenshot(path="yb_after.png")       # 文件对话框/上传后
```

---

## 相关文件

- `station/server/yuanbao_client.py` — 本项目的实际实现
- `docs/eval/沉淀失败原因.md` — 历次失败尝试的完整记录
