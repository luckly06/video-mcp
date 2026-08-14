# 桌面端复用系统浏览器（Edge/Chrome）CDP 驱动网页 —— 实现与踩坑手册

> **适用场景**：桌面应用（Electron/Tauri/任意本机进程）要复用用户**已登录**的第三方网页（例：腾讯元宝）做自动化——发文本 + 图片、取回结果，**不弹内置窗口、不重新扫码登录**。
> **来源**：video-uniqueness 桌面端 2026-08 实战，用户本机验证通过（跳转 → 复用登录态 → 发文案+截图 → 拿回结果，全链路 OK）。
> **可直接套用**：下面第 2 节是成功代码（精简版，完整版见 `station/server/yuanbao_client.py`）。

---

## 0. 一句话方案

用 `--remote-debugging-port=<动态端口>` + `--user-data-dir=<独立持久 profile>` 拉起**系统浏览器**，Playwright `connect_over_cdp` 驱动；登录态靠「**关浏览器 → 复制默认 profile 关键文件到持久调试 profile → 重启浏览器**」复用**一次**，之后长期复用，不用每次重启。

---

## 1. 必须知道的硬事实（决定方案，不可违背）

| # | 事实（实测确认） | 后果 | 对策 |
|---|---|---|---|
| 1 | Chrome/Edge **136+ 禁止在默认 profile 开调试端口**（二进制字符串：`DevTools remote debugging requires a non-default data directory`） | 想给真实 profile 加 `--remote-debugging-port` 这条路**直接堵死** | 必须 `--user-data-dir=<独立 profile>` |
| 2 | 浏览器运行时 **Cookies 库被文件级独占锁死**（`open`/`copy2`/`sqlite ro`/`esentutl` 全失败，只有 `Local State` 可读） | 不关浏览器就复制登录态 = 必失败 | 复制前 `taskkill /F /IM msedge.exe` 关一次；用 sqlite backup 拿快照 |
| 3 | 调试端口**写死**（如 9223）会被残留进程占用 | 新浏览器**静默**绑定失败 → 轮询超时，且看不到原因 | 用 `_free_port()` 动态选空闲端口 |
| 4 | `subprocess.Popen(stdout="路径字符串")` 会抛 `'str' object has no attribute 'fileno'` | 浏览器**根本没启动**（异常在启动前） | `stdout` 必须传真正的**文件对象**（`open()`） |
| 5 | Playwright `fill()` **只支持 `<input>/<textarea>`** | 对 contenteditable 编辑器（元宝就是）**失效** | 用 `el.innerText = text; dispatchEvent('input')` |
| 6 | `expect_file_chooser`（等原生文件对话框）在 CDP 模式下**常不触发** | 图片上传超时/失败 | 直接 `set_input_files` 到 `input[type=file]`（支持 base64 先落盘） |
| 7 | 强杀浏览器会残留 `SingletonLock` 等单例锁 | 下次启动新进程**直接移交并退出** → 端口永不就绪 | 启动前清理 `SingletonLock/SingletonCookie/SingletonSocket` |
| 8 | 复制登录态后不重开用户浏览器 | 用户标签页全丢，体验极差 | 复制完把用户原浏览器重新拉起 |

---

## 2. 能走的路径（成功代码）

### 2.1 准备调试浏览器（核心入口）

```python
# -*- coding: utf-8 -*-
import os, json, socket, time, subprocess, shutil, sqlite3
from pathlib import Path

EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)
DEBUG_PROFILE = Path(__file__).resolve().parent / "logs" / ".edge-debug-profile"  # 持久！不是临时目录
STATE_PATH    = Path(__file__).resolve().parent / "logs" / "edge_debug_state.json"
YUANBAO_URL   = "https://yuanbao.tencent.com/"  # 换成你的目标站点

def _edge_exe():
    for p in EDGE_CANDIDATES:
        if Path(p).exists():
            return p
    return None

def _edge_running():
    r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq msedge.exe", "/FO", "CSV", "/NH"],
                       capture_output=True, text=True, timeout=10)
    return "msedge.exe" in r.stdout.lower()   # 排除 WebView2(msedgewebview2.exe)

def _cdp_alive(port, timeout=2):
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace")).get("Browser")
    except Exception:
        return None

def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    return p

def _read_state():
    try: return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception: return {}

def _write_state(d):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")

def copy_edge_login_state(dst):
    """复制默认 profile 登录态到 dst（只取登录相关最小集，不复制 Cache/History 大文件）。"""
    src = Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "Edge" / "User Data"
    if not (src / "Local State").exists():
        return False
    dst = Path(dst); (dst / "Default" / "Network").mkdir(parents=True, exist_ok=True)
    shutil.copy2(src / "Local State", dst / "Local State")
    shutil.copy2(src / "Default" / "Preferences", dst / "Default" / "Preferences")
    # Cookies 用 sqlite backup 拿一致快照（直接 copy2 可能拿到不一致的半写状态）
    scon = sqlite3.connect(f"file:{src/'Default'/'Network'/'Cookies'}?mode=ro", uri=True, timeout=5)
    dcon = sqlite3.connect(dst / "Default" / "Network" / "Cookies")
    scon.backup(dcon); scon.close(); dcon.close()
    for sub in ("Local Storage", "IndexedDB"):
        if (src / "Default" / sub).exists():
            shutil.copytree(src / "Default" / sub, dst / "Default" / sub, dirs_exist_ok=True)
    return (dst / "Local State").exists() and (dst / "Default" / "Network" / "Cookies").exists()

def ensure_edge_debug_port():
    """返回 dict(ok,msg,profile,port,pid,reused,relaunch_user_edge)。"""
    # 0) 复用上次实例：端口还活着就完全不用碰用户的浏览器
    st = _read_state()
    for cand in [st.get("port"), 9223]:
        if cand and _cdp_alive(cand):
            return dict(ok=True, profile=st.get("profile"), port=cand, pid=st.get("pid"),
                        reused=True, relaunch_user_edge=False)

    edge = _edge_exe()
    if not edge:
        return dict(ok=False, msg="未找到浏览器可执行文件", relaunch_user_edge=False)

    ck = DEBUG_PROFILE / "Default" / "Network" / "Cookies"
    need_seed = not (ck.exists() and ck.stat().st_size > 0)
    was_running = False

    # 1) 仅首次需要：关一次浏览器（释放 Cookies 锁）→ 复制登录态
    if need_seed:
        if _edge_running():
            was_running = True
            subprocess.run(["taskkill", "/F", "/IM", "msedge.exe"], capture_output=True, timeout=60)
            for _ in range(40):
                if not _edge_running(): break
                time.sleep(0.5)
            time.sleep(1.0)  # 等文件句柄真正释放
        copy_edge_login_state(DEBUG_PROFILE)

    # 2) 清残留单例锁（上次强杀会留下，否则新进程移交退出→端口永不就绪）
    for lock in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        try: (DEBUG_PROFILE / lock).unlink()
        except Exception: pass

    # 3) 启动调试浏览器：动态端口 + 独立 profile + 直开目标页（用户能直观看到跳转）
    port = _free_port()
    log_path = DEBUG_PROFILE / "edge_launch.log"
    logf = open(log_path, "w", encoding="utf-8", errors="replace")  # 关键：文件对象，不是字符串路径
    proc = subprocess.Popen(
        [edge, f"--remote-debugging-port={port}", "--remote-debugging-address=127.0.0.1",
         f"--user-data-dir={DEBUG_PROFILE}", "--no-first-run", "--no-default-browser-check",
         "--disable-blink-features=AutomationControlled",
         "--disable-session-crashed-bubble", "--new-window", YUANBAO_URL],
        stdout=logf, stderr=subprocess.STDOUT)

    ver = None
    for _ in range(60):
        time.sleep(0.5)
        ver = _cdp_alive(port, timeout=1)
        if ver or proc.poll() is not None:
            break
    logf.close()
    if not ver:
        diag = log_path.read_text(errors="replace")[:800]
        try: proc.kill()
        except Exception: pass
        return dict(ok=False, msg=f"调试浏览器未就绪。日志:\n{diag}", relaunch_user_edge=was_running)

    _write_state({"port": port, "profile": str(DEBUG_PROFILE), "pid": proc.pid})
    return dict(ok=True, port=port, profile=str(DEBUG_PROFILE), pid=proc.pid,
                reused=False, relaunch_user_edge=was_running)
```

### 2.2 改写结束后的收尾（关键：不误杀用户浏览器）

```python
def finish_rewrite(info):
    """改写完：仅按 PID 树回收我们拉起的调试实例；用户的原浏览器不受影响。"""
    if not info.get("reused") and info.get("pid"):
        subprocess.run(["taskkill", "/F", "/PID", str(info["pid"]), "/T"],
                       capture_output=True, timeout=30)
    if info.get("relaunch_user_edge"):
        subprocess.Popen([_edge_exe()], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # 重开用户浏览器
    # 注意：若要长期复用调试实例，就别 kill，只保留 state 文件；下次 detect 端口失效会自动重走全流程（自愈）
```

### 2.3 驱动网页交互（发文本 + 图片 + 取回结果）

```python
import asyncio, base64
from playwright.async_api import async_playwright

async def drive(port, frames_b64, prompt):
    p = await async_playwright().start()
    browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
    ctx = browser.contexts[0]

    # 精确选目标页（按 URL 关键字），别误驱动用户其它标签页
    page = next((pg for pg in ctx.pages if "目标域名" in (pg.url or "")), None)
    if page is None:
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    await page.bring_to_front()
    if "目标域名" not in (page.url or ""):
        await page.goto("https://目标站点/", wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(2)

    # 检测登录弹窗（元宝未登录时是弹窗/iframe，不跳 URL，必须单独检测）
    if await page.evaluate("() => !!document.querySelector('iframe[class*=login], iframe[src*=qrconnect]')"):
        return {"error": "未登录：请在调试窗口扫码一次（profile 持久，后续免登录）"}

    # ① 找输入框：多选择器 + 可见性校验
    editor = None
    for sel in ['div[contenteditable="true"]', 'textarea[placeholder*="输入"]', 'textarea[placeholder*="描述"]']:
        loc = page.locator(sel)
        for i in range(await loc.count()):
            e = loc.nth(i)
            if await e.is_visible():
                editor = e; break
        if editor: break
    if editor is None:
        return {"error": "找不到输入框"}

    # ② 上传图片：base64 落盘 → 直灌 input[type=file]（绝不依赖原生对话框）
    paths = []
    for i, b64 in enumerate(frames_b64[:3]):
        if b64.startswith('data:'): b64 = b64.split(',', 1)[1]
        fp = f"_tmp_{i}.png"; open(fp, "wb").write(base64.b64decode(b64)); paths.append(fp)
    ub = page.locator('[class*="UploadFileSelector"]').first
    if await ub.count() > 0:
        await ub.click(); await asyncio.sleep(0.8)
    pic = page.locator("xpath=//*[normalize-space(text())='图片']").first  # 精确文本匹配菜单
    if await pic.count() > 0:
        await pic.click(); await asyncio.sleep(0.5)
    fi = page.locator('input[type="file"]').first
    if await fi.count() > 0:
        await fi.set_input_files(paths)   # 关键：直接灌文件，不等原生对话框
        await asyncio.sleep(4)
    for fp in paths:
        try: os.remove(fp)
        except Exception: pass

    # ③ 填提示词：contenteditable 用 innerText，不能用 fill()！
    await editor.evaluate(
        "(el, text) => { if (el.getAttribute('contenteditable')==='true') { el.innerText = text; } else { el.value = text; } el.dispatchEvent(new Event('input', {bubbles:true})); }",
        prompt)
    await asyncio.sleep(0.4)

    # ④ 发送：Enter + 兜底点「发送」按钮
    try: await editor.press("Enter")
    except Exception: pass
    await asyncio.sleep(0.5)
    if not await _is_generating(page):
        btn = page.locator('button:has-text("发送"), [aria-label*="发送"]').first
        if await btn.count() > 0:
            await btn.click()

    # ⑤ 等回复：基线对话数 + 双采样稳定
    reply = await _wait_reply(page, timeout=120)
    await p.stop()   # 注意：保留调试浏览器窗口，下次直接复用
    return {"rewritten": reply}

async def _is_generating(page):
    return await page.evaluate(
        "() => [...document.querySelectorAll('button,[role=button]')].some(b => /停止|stop/i.test(b.textContent||''))")

async def _wait_reply(page, timeout):
    sel = '.hyc-common-markdown,[class*="answer"],[class*="reply"],[class*="bubble"]'
    base = await page.locator(sel).count()
    t0 = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - t0 < timeout:
        if not await _is_generating(page):
            cnt = await page.locator(sel).count()
            if cnt > base:
                t = (await page.locator(sel).nth(cnt - 1).inner_text()).strip()
                if t and not any(k in t for k in ("正在分析", "正在搜索", "正在生成")):
                    await asyncio.sleep(2)
                    if not await _is_generating(page):
                        t2 = (await page.locator(sel).nth((await page.locator(sel).count()) - 1).inner_text()).strip()
                        if t2 == t:
                            return t
        await asyncio.sleep(1.5)
    return ""
```

---

## 3. 不能走的绝路（错误流程，逐条列明）

按真实踩坑时间顺序，每条 = 做法 → 为什么死 → 正解。

| # | 错误流程 | 为什么是绝路 | 正解 |
|---|---|---|---|
| 1 | **Electron 内置 BrowserWindow + 注入 content 脚本**去操作元宝 | 内置窗口的 cookie 与系统浏览器**隔离**，用户已登录的元宝登录态带不进来 → 被迫重新扫码；用户明确否掉「别用内置窗口」 | 驱动**系统浏览器**（CDP），复用其登录态 |
| 2 | **服务端 headless Chromium 自动化**（DeepSeek 后端）跑元宝 | 登录态/QR 不稳定，`yuanbao_login` 无 QR、`rewrite_copy` 报「DeepSeek 未登录」，被冻结 | 客户端本机驱动，不依赖服务器 |
| 3 | 给**真实/默认 profile** 加 `--remote-debugging-port` | Edge 136+ 硬禁止（见硬事实 1），端口根本不开 | 用独立 `--user-data-dir` |
| 4 | **每次改写都新建临时 profile** 复制登录态 | ① 每次都要关用户浏览器；② 临时目录用完即删，万一登录态复制失效、用户手动登录一次，下次又白登录 | 用**持久**调试 profile，只首次播种 |
| 5 | 用 `expect_file_chooser` 等**原生文件对话框**上传图片 | CDP 调试模式下原生对话框常不触发 → 超时 | 直接 `set_input_files` 到 `input[type=file]` |
| 6 | 用 Playwright `fill()` 填 **contenteditable** 编辑器 | `fill()` 仅支持 input/textarea，对元宝编辑器静默失效 → 「提示词没发出去」 | `el.innerText = text; dispatchEvent('input')` |
| 7 | 调试端口**写死 9223** | 残留进程占用 → 新浏览器静默绑定失败 → 30s 超时 | `_free_port()` 动态选空闲端口 |
| 8 | `subprocess.Popen(stdout=str(路径))` 抓日志 | 字符串被当文件对象调 `.fileno()` → `'str' object has no attribute 'fileno'`，浏览器根本没起 | `stdout=open(路径, 'w')` 真正的文件对象 |
| 9 | `taskkill` 杀掉所有 Edge 后**不重开**用户浏览器 | 用户标签页全丢、体验崩 | 复制完登录态后重开用户浏览器；收尾只按 PID 树回收调试实例 |
| 10 | 复制登录态时用 `copy2` 直接拷 **Cookies**（运行中被锁） | 文件级独占锁，读都读不到 | 先关浏览器；用 `sqlite3 backup` 拿一致快照 |

---

## 4. 新项目复用 Checklist（照做即可）

- [ ] 探测浏览器 exe 路径（x86 / x64 两个候选都试）
- [ ] 动态空闲端口（`socket` bind 0 拿端口，别写死）
- [ ] 持久调试 profile 目录（**非临时**；首次播种登录态，之后复用）
- [ ] 复制登录态 = `Local State` + `Default/Network/Cookies`（sqlite backup）+ `Preferences` + `Local Storage`/`IndexedDB`
- [ ] 复制前 `taskkill` 关一次浏览器释放 Cookies 锁；复制后重开用户浏览器
- [ ] 启动参数带 `--new-window <目标URL>`（用户能直观看到跳转）；等 `/json/version` 就绪（不是端口能连就行）
- [ ] 启动前清理 `SingletonLock/SingletonCookie/SingletonSocket`
- [ ] 写 state 文件（port/pid/profile）供复用；检测到端口失效自动重走全流程（自愈）
- [ ] `connect_over_cdp` 后**按 URL 关键字精确选目标页**，别误驱动用户其它标签页
- [ ] 检测登录弹窗（iframe/弹窗，不跳 URL 那种）给可读错误，提示「扫码一次、profile 持久」
- [ ] 交互三件套：contenteditable 用 `innerText`；文件用 `set_input_files` 直灌；发送 `Enter` + 按钮兜底
- [ ] 诊断日志落盘（`edge_launch.log` + 全程日志），出错先看日志
- [ ] 收尾：保留调试实例复用；要回收时只按 PID 树 `taskkill /PID /T`，不 `taskkill /IM` 全杀

---

## 5. 依赖与备注

- Python 侧：`playwright`（仅 `async_api` 的 `connect_over_cdp`，**不需要** `playwright install` 下载浏览器，用的是系统浏览器）。
- 浏览器：系统 Edge/Chrome（CDP 是标准协议，Chrome 同理，把 exe 路径和 profile 默认路径换成 Chrome 的即可）。
- ABE（App-Bound Encryption）：同机同用户复制登录态实测**正常解密**；若个别机器触发拦截（弹登录框），兜底是「调试窗口手动扫码一次」，profile 持久所以只需一次。
- 完整可运行实现参考：`station/server/yuanbao_client.py`（含动态端口、状态复用、异常回显、诊断日志）。
