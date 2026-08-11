# 提示词包含文案+图片发给元宝（可复用策略）

> **适用场景**：Playwright 自动化将文本提示词 + 多张图片一起发给腾讯元宝（yuanbao.tencent.com），获取 AI 改写/识图回复。
>
> **难度**：高（React/Next.js SPA，自定义上传组件，多步骤菜单交互）
>
> **状态**：✅ 已验证（2026-08-11，station/server/yuanbao_client.py）

---

## 核心陷阱

### 1. 元宝没有原生 `<input type="file">`
元宝是 Next.js SPA，文件上传走自定义 React 组件 `UploadFileSelector_iconContainer`，DOM 中**没有**任何 `<input type="file">` 元素。所有基于 `set_input_files` 或 `MutationObserver` 等 file input 的策略都会失败。

### 2. 上传是**两步操作**，不是一步
- **第 1 步**：点击 "+" 图标 → **弹出下拉菜单**（图片 / 本地文件 / 腾讯文档）
- **第 2 步**：在菜单中点击 "图片" → **触发原生文件对话框**

之前的失败都集中在"点击 + 后直接等 file dialog"——但 + 只是打开菜单，不弹文件框。

### 3. React isTrusted 检查
`element.click()`（JS 原生）触发的事件 `isTrusted=false`，React 事件系统会**忽略**。必须用 Playwright 的真鼠标事件。

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
await asyncio.sleep(2)
```

### 兜底（如果 file_chooser 失败）
某些情况下 React 组件不会弹原生文件对话框，而是创建隐藏 `<input type="file">`：
```python
# 兜底
await asyncio.sleep(1)
inps = page.locator('input[type="file"]')
if await inps.count() > 0:
    await inps.last.set_input_files(frames[:3])
```

---

## 模板系统注意事项

本项目将 Playwright 操作封装在 Python 子进程中，通过 `REWRITE_TEMPLATE`（Python 三引号字符串）动态生成临时脚本。

### `.format()` 转义规则
模板中所有 f-string 变量都必须双花括号转义：

| 模板写法 | `.format()` 后 | 生成脚本中的效果 |
|----------|---------------|----------------|
| `{frames}` | 替换为值 | Python 变量 = 传入值 |
| `{{e}}` | `{e}` | f-string 引用局部变量 |
| `{{len(fs3)}}` | `{len(fs3)}` | f-string 表达式 |

**典型 bug**：`print(f"error: {e}")` → `.format()` 找 `e=` kwarg → `KeyError: 'e'`

### 三引号冲突
模板是 `'''...'''`，内部 JS 字符串用 `"""..."""` 避免提前闭合。

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
# 连已有浏览器
browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9223")
```

**注意**：CDP disconnect（`await p.stop()`）不关浏览器，下次改写会复用同一窗口。

---

## 截图诊断工作流

调试 SPA 交互问题时，在每一步后截图比看代码猜有效 10 倍：

```python
await page.screenshot(path="yb_before_click.png")
await ub.click()
await asyncio.sleep(1)
await page.screenshot(path="yb_menu_open.png")    # 验证菜单是否打开
await pic.click(force=True)
await asyncio.sleep(1)
await page.screenshot(path="yb_after_click.png")  # 验证文件对话框/上传状态
```

同时保存页面 DOM 到文件也很有用：
```python
html = await page.content()
Path("page_dump.html").write_text(html, encoding="utf-8")
```

---

## 相关文件

- `station/server/yuanbao_client.py` — 实际实现（`REWRITE_TEMPLATE` + `vision_and_rewrite()`）
- `station/server/mcp_server.py` — `rewrite_copy` handler（截帧 → 调 yuanbao_client）
- `docs/eval/沉淀失败原因.md` — 历次失败尝试的完整记录

---

## 版本历史

- 2026-08-11：首次攻克，Playwright `get_by_text` + `expect_file_chooser` 方案验证通过
