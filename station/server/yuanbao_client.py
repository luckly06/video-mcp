# -*- coding: utf-8 -*-
"""yuanbao_client.py — 子进程隔离模式（独立 Python 进程跑浏览器操作）"""

import asyncio
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

logger = logging.getLogger("yuanbao")
_HERE = Path(__file__).resolve().parent
PROFILE_DIR = os.environ.get("VU_YUANBAO_PROFILE",
                             str(_HERE / "logs" / ".yuanbao-profile"))

# 持久诊断日志：改写全过程都写这里，出问题直接看这个文件
LOG_PATH = _HERE / "logs" / "yuanbao_debug.log"
# 调试 Edge 实例状态（端口 / profile / pid），供下次改写复用，避免反复重启用户 Edge
STATE_PATH = _HERE / "logs" / "edge_debug_state.json"
# 调试专用 Edge profile：持久化（不是临时目录），这样万一登录态复制不生效，
# 用户在该窗口手动登录一次即可长期有效，不会每次都要重新登录
DEBUG_PROFILE = _HERE / "logs" / ".edge-debug-profile"

EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)
YUANBAO_URL = "https://yuanbao.tencent.com/"


def _dlog(msg):
    """追加写持久诊断日志（超过 512KB 自动保留尾部）。"""
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > 512 * 1024:
            tail = LOG_PATH.read_text(encoding="utf-8", errors="replace")[-100_000:]
            LOG_PATH.write_text(tail, encoding="utf-8")
        with open(LOG_PATH, "a", encoding="utf-8", errors="replace") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def _edge_exe():
    for p in EDGE_CANDIDATES:
        if Path(p).exists():
            return p
    return None


def _cdp_alive(port, timeout=2):
    """真正确认是 CDP 端点（不只是端口能连），返回浏览器版本串或 None。"""
    import urllib.request
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json/version", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace")).get("Browser") or "unknown"
    except Exception:
        return None


def _read_state():
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_state(d):
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _sweep_stale_profiles():
    """后台清理历史遗留的 vu_edge_* 临时 profile（每个可达数十 MB，实测串行删 5 个要 69s，
    因此必须放到守护线程里做，绝不能阻塞改写主流程）。"""
    def _work():
        import shutil as _sh
        tmp = Path(os.environ.get("TEMP") or tempfile.gettempdir())
        n = 0
        for d in list(tmp.glob("vu_edge_*")) + list(tmp.glob("vu_probe_*")) + list(tmp.glob("vu_cp_*")):
            try:
                _sh.rmtree(str(d), ignore_errors=True)
                if not d.exists():
                    n += 1
            except Exception:
                pass
        if n:
            _dlog(f"[后台] 清理历史临时 profile {n} 个")
    try:
        import threading
        threading.Thread(target=_work, daemon=True).start()
    except Exception:
        pass


def has_profile():
    return (Path(PROFILE_DIR) / "Local State").exists()


def _edge_default_profile():
    """用户 Edge 默认 profile 路径（复用已登录元宝的登录态）。"""
    local = os.environ.get("LOCALAPPDATA", "")
    return Path(local) / "Microsoft" / "Edge" / "User Data"


def _copy_file(src, dst):
    import shutil
    try:
        shutil.copy2(str(src), str(dst))
    except Exception:
        pass


def _copy_cookie_db(src, dst):
    """复制 Cookies 数据库（Edge 运行中也能尽量拿一致快照）。"""
    if not src.exists():
        return False
    import shutil
    try:
        import sqlite3
        scon = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=5)
        dcon = sqlite3.connect(str(dst))
        scon.backup(dcon)
        scon.close()
        dcon.close()
        return dst.exists() and dst.stat().st_size > 0
    except Exception:
        try:
            shutil.copy2(str(src), str(dst))
            for ext in ("-wal", "-shm"):
                p = Path(str(src) + ext)
                if p.exists():
                    shutil.copy2(str(p), str(dst) + ext)
            return True
        except Exception:
            return False


def copy_edge_login_state(dst):
    """把用户 Edge 默认 profile 的登录态关键文件复制到 dst（避免与运行中的 Edge 抢单例锁）。

    只复制元宝登录态相关的最小集合（Local State / Cookies / Preferences / Local Storage / IndexedDB），
    不复制 Cache/History 等大文件。返回是否成功（至少 Local State + Cookies 就位）。
    """
    src = _edge_default_profile()
    if not (src / "Local State").exists():
        return False
    dst = Path(dst)
    dst.mkdir(parents=True, exist_ok=True)

    _copy_file(src / "Local State", dst / "Local State")

    default_src = src / "Default"
    default_dst = dst / "Default"
    default_dst.mkdir(parents=True, exist_ok=True)
    _copy_file(default_src / "Preferences", default_dst / "Preferences")

    net_dst = default_dst / "Network"
    net_dst.mkdir(parents=True, exist_ok=True)
    _copy_cookie_db(default_src / "Network" / "Cookies", net_dst / "Cookies")

    import shutil
    for sub in ("Local Storage", "IndexedDB"):
        s = default_src / sub
        if s.exists():
            try:
                shutil.copytree(str(s), str(default_dst / sub), dirs_exist_ok=True)
            except Exception:
                pass

    return (dst / "Local State").exists() and (net_dst / "Cookies").exists()


def _edge_running():
    """检测 Edge 主浏览器（msedge.exe）是否在运行（排除 WebView2：msedgewebview2.exe）。"""
    try:
        r = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq msedge.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10,
        )
        return "msedge.exe" in r.stdout.lower()
    except Exception:
        return False


def ensure_edge_debug_port():
    """准备一个「带调试端口 + 复用用户元宝登录态」的 Edge 实例。

    Edge 136+ 起明确禁止在默认 profile 上开远程调试
    （二进制内字符串：DevTools remote debugging requires a non-default data directory），
    因此只能：关闭 Edge → 复制登录态到独立临时 profile → 用它启动调试版 Edge。

    返回 dict：
      ok / msg / profile / port / pid / reused(是否复用上次实例) / relaunch_user_edge(是否需在收尾重开用户 Edge)
    """
    import socket
    import tempfile as _tempfile
    import shutil as _shutil

    def _free_port():
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
        s.close()
        return p

    def fail(msg, relaunch):
        _dlog("FAIL: " + msg.replace("\n", " | "))
        return {"ok": False, "msg": msg, "profile": None, "port": None,
                "pid": None, "reused": False, "relaunch_user_edge": relaunch}

    _dlog("=== ensure_edge_debug_port 开始 ===")

    # 0) 复用上次拉起的调试实例（端口还活着就完全不用碰用户的 Edge）
    st = _read_state()
    for cand in [st.get("port"), 9223]:
        if not cand:
            continue
        ver = _cdp_alive(cand)
        if ver:
            prof = st.get("profile") if cand == st.get("port") else None
            _dlog(f"复用已有调试实例 port={cand} browser={ver} profile={prof}")
            return {"ok": True, "msg": f"复用已就绪的调试 Edge（端口 {cand}）",
                    "profile": prof, "port": cand, "pid": st.get("pid"),
                    "reused": True, "relaunch_user_edge": False}

    edge = _edge_exe()
    if not edge:
        return fail("未找到 Edge 可执行文件（已查找 Program Files / Program Files (x86)）", False)
    _dlog(f"edge={edge}")

    _sweep_stale_profiles()

    # 1) 只有「调试 profile 还没播种过登录态」时才需要关一次用户 Edge
    #    （Cookies 库在 Edge 运行时被文件级独占锁死，open/copy/sqlite/esentutl 全部读不到）
    ck = DEBUG_PROFILE / "Default" / "Network" / "Cookies"
    need_seed = not (ck.exists() and ck.stat().st_size > 0)
    was_running = False
    _dlog(f"调试 profile 需要播种登录态={need_seed} ({DEBUG_PROFILE})")

    if need_seed:
        was_running = _edge_running()
        _dlog(f"用户 Edge 运行中={was_running}")
        if was_running:
            try:
                subprocess.run(["taskkill", "/F", "/IM", "msedge.exe"],
                               capture_output=True, timeout=60)
            except Exception as e:
                _dlog(f"taskkill 异常: {e}")
            gone = False
            for _ in range(40):
                time.sleep(0.5)
                if not _edge_running():
                    gone = True
                    break
            _dlog(f"Edge 已完全退出={gone}")
            if not gone:
                return fail("关闭 Edge 超时（20s 后仍有 msedge.exe 存活，可能是启动加速在自动重启）。"
                            "请手动退出 Edge 后重试。", True)
            time.sleep(1.0)  # 等文件句柄真正释放

        t0 = time.time()
        copied = copy_edge_login_state(DEBUG_PROFILE)
        ck_size = ck.stat().st_size if ck.exists() else 0
        _dlog(f"播种登录态 ok={copied} 耗时={time.time()-t0:.1f}s Cookies={ck_size}B")
        if not copied or ck_size == 0:
            if not (DEBUG_PROFILE / "Local State").exists():
                return fail("复制 Edge 登录态失败：Cookies 库不可读"
                            "（Edge 未完全退出或被安全软件锁定）", was_running)
            _dlog("播种失败但 profile 已存在，继续用现有 profile 启动")

    # 2) 清掉可能残留的单例锁（上次调试 Edge 被强杀会留下，导致新进程直接移交并退出→端口永不就绪）
    for lock in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        p = DEBUG_PROFILE / lock
        try:
            if p.is_symlink() or p.exists():
                p.unlink()
                _dlog(f"清理残留 {lock}")
        except Exception:
            pass

    # 3) 启动调试 Edge：动态空闲端口 + 直接打开元宝（用户能直观看到跳转）
    port = _free_port()
    log_path = DEBUG_PROFILE / "edge_launch.log"
    try:
        DEBUG_PROFILE.mkdir(parents=True, exist_ok=True)
        logf = open(str(log_path), "w", encoding="utf-8", errors="replace")
    except Exception as e:
        return fail(f"无法创建 Edge 日志文件: {e}", was_running)

    args = [edge,
            f"--remote-debugging-port={port}",
            "--remote-debugging-address=127.0.0.1",
            f"--user-data-dir={DEBUG_PROFILE}",
            "--no-first-run", "--no-default-browser-check",
            "--disable-blink-features=AutomationControlled",
            "--disable-session-crashed-bubble", "--hide-crash-restore-bubble",
            "--new-window", YUANBAO_URL]
    _dlog(f"启动调试 Edge port={port}")
    try:
        proc = subprocess.Popen(args, stdout=logf, stderr=subprocess.STDOUT)
    except Exception as e:
        try: logf.close()
        except Exception: pass
        return fail(f"启动调试版 Edge 失败: {e}", was_running)

    # 4) 等 CDP 真正可用（不只是端口能连上）
    t1 = time.time()
    ver = None
    for _ in range(60):
        time.sleep(0.5)
        ver = _cdp_alive(port, timeout=1)
        if ver:
            break
        if proc.poll() is not None:
            _dlog(f"Edge 进程提前退出 rc={proc.returncode}")
            break
    try: logf.close()
    except Exception: pass
    try:
        diag = log_path.read_text(errors="replace").strip()[:800]
    except Exception:
        diag = "(无日志)"

    if not ver:
        _dlog(f"调试端口未就绪，Edge 日志: {diag}")
        try: proc.kill()
        except Exception: pass
        return fail(f"调试版 Edge 30s 未就绪。Edge 日志:\n{diag}", was_running)

    _dlog(f"调试 Edge 就绪 {time.time()-t1:.1f}s browser={ver} pid={proc.pid} port={port}")
    _write_state({"port": port, "profile": str(DEBUG_PROFILE), "pid": proc.pid})
    return {"ok": True, "msg": f"已启动调试 Edge（端口 {port}，{ver}）",
            "profile": str(DEBUG_PROFILE), "port": port, "pid": proc.pid,
            "reused": False, "relaunch_user_edge": was_running}


def _pick_channel():
    env = os.environ.get("VU_YUANBAO_CHANNEL", "").strip()
    if env: return env
    import shutil
    for c in ["msedge", "chrome"]:
        if shutil.which(c): return c
    return "chrome"


def _venv_python():
    return os.environ.get("VU_PYTHON", sys.executable)


# ======== 登录 ========

LOGIN_TEMPLATE = '''\
import asyncio, json
from pathlib import Path
from playwright.async_api import async_playwright

async def main():
    p = await async_playwright().start()
    ctx = await p.chromium.launch_persistent_context(
        user_data_dir=r"{profile}", channel="{channel}",
        headless=False, args=["--disable-blink-features=AutomationControlled"])
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    await page.goto("https://yuanbao.tencent.com/", wait_until="domcontentloaded", timeout=30000)
    closed = asyncio.Event()
    page.on("close", lambda _: closed.set())
    try: await asyncio.wait_for(closed.wait(), timeout=600)
    except asyncio.TimeoutError: pass
    await ctx.close(); await p.stop()
    print(json.dumps({{"ok": True}}))

asyncio.run(main())
'''

# ======== 服务器端无头登录（QR 码扫描）========
SERVER_LOGIN_TEMPLATE = '''\
import asyncio, json, sys, base64, io, traceback
from pathlib import Path
try:
    from PIL import Image
except ImportError:
    Image = None
from playwright.async_api import async_playwright

async def main():
    profile = Path(r"{profile}")
    profile.mkdir(parents=True, exist_ok=True)
    
    try:
        import platform
        if platform.system() == "Linux":
            pw = await async_playwright().start()
            browser = await pw.chromium.launch_persistent_context(
                user_data_dir=str(profile),
                headless=True,
                executable_path="/usr/bin/chromium",
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-gpu",
                      "--disable-encryption-cookies",
                      "--disable-blink-features=AutomationControlled"])
        else:
            pw = await async_playwright().start()
            browser = await pw.chromium.launch_persistent_context(
                user_data_dir=str(profile),
                headless=False,
                channel="{channel}",
                args=["--disable-blink-features=AutomationControlled"])
    except Exception as e:
        print(json.dumps({{"error": f"browser launch fail: {{e}}"}}, ensure_ascii=False))
        return
    
    page = browser.pages[0] if browser.pages else await browser.new_page()
    try:
        await page.goto("https://yuanbao.tencent.com/", wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)
        
        # 检查是否已登录（chat 页面无登录弹窗）
        has_login = await page.evaluate("""() => {{
            return !!document.querySelector('iframe.hyc-wechat-login, [class*=\"login\"][class*=\"dialog\"], [class*=\"Login\"]');
        }}""")
        
        if not has_login:
            # 已登录
            print(json.dumps({{"ok": True, "logged_in": True}}, ensure_ascii=False))
            await browser.close()
            return
        
        # === 获取 QR 码截图 ===
        try:
            await page.wait_for_selector('iframe.hyc-wechat-login', timeout=10000, state='visible')
        except:
            pass
        await page.wait_for_timeout(2000)
        
        screenshot_bytes = await page.screenshot(type="png", full_page=False)
        screenshot_b64 = base64.b64encode(screenshot_bytes).decode("ascii")
        
        # 裁剪 iframe 区域
        qr_b64 = screenshot_b64
        try:
            qr_iframe = page.locator('iframe.hyc-wechat-login, iframe[src*="qrconnect"]').first
            if await qr_iframe.count() > 0:
                qr_box = await qr_iframe.bounding_box()
                if qr_box:
                    from PIL import Image
                    import io
                    img = Image.open(io.BytesIO(screenshot_bytes))
                    pad = 30
                    x = max(0, int(qr_box['x']-pad))
                    y = max(0, int(qr_box['y']-pad-50))  # 留标题空间
                    w = min(img.width-x, int(qr_box['width']+pad*2))
                    h = min(img.height-y, int(qr_box['height']+pad*2+50))
                    cropped = img.crop((x, y, x+w, y+h))
                    cropped = cropped.resize((cropped.width*2, cropped.height*2), Image.LANCZOS)
                    buf = io.BytesIO(); cropped.save(buf, 'PNG')
                    qr_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        except:
            pass
        
        # 先返回 QR 码给前端
        print(json.dumps({{
            "ok": True, "logged_in": False, "has_login_form": True,
            "screenshot_b64": screenshot_b64, "qr_b64": qr_b64,
        }}, ensure_ascii=False))
        sys.stdout.flush()
        
        # === 等待用户扫码（最长 120s）===
        for _ in range(80):  # 80 * 1.5s = 120s
            await asyncio.sleep(1.5)
            logged = not await page.evaluate("""() => {{
                return !!document.querySelector('iframe.hyc-wechat-login, [class*=\"login\"][class*=\"dialog\"]');
            }}""")
            if logged:
                # 登录成功！cookie 已被写入 profile
                await page.wait_for_timeout(2000)  # 等 cookie flush
                await browser.close()
                print(json.dumps({{"ok": True, "logged_in": True, "msg": "login success"}}, ensure_ascii=False))
                return
        
        # 超时
        await browser.close()
        print(json.dumps({{"ok": False, "error": "login timeout (120s)"}}, ensure_ascii=False))
        
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({{"error": str(e)}}, ensure_ascii=False))
        try: await browser.close()
        except: pass

asyncio.run(main())
'''


def login_server():
    """服务器端无头登录：返回 QR 码截图给前端，浏览器后台等扫码完成"""
    channel = _pick_channel()
    script = SERVER_LOGIN_TEMPLATE.format(profile=PROFILE_DIR, channel=channel)
    fd, tmp = tempfile.mkstemp(suffix=".py", prefix="vu_sl_")
    os.close(fd)
    Path(tmp).write_text(script, encoding="utf-8")
    
    # 用 Popen 非阻塞读取：第一行是 QR，第二行是登录结果
    proc = subprocess.Popen([_venv_python(), tmp],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, cwd=str(_HERE.parent))
    
    # 读第一行 JSON（QR 码）
    try:
        first_line = proc.stdout.readline()
    except:
        proc.kill()
        try: Path(tmp).unlink()
        except: pass
        return {"error": "no output from login subprocess"}
    
    first = json.loads(first_line.strip()) if first_line.strip() else {"error": "empty output"}
    first["_pid"] = proc.pid
    
    # 启动后台线程等登录结果（120s）
    import threading
    def _wait_login():
        try:
            remaining = proc.stdout.readline()
            result = json.loads(remaining.strip()) if remaining.strip() else {"error": "no result"}
            proc.wait(timeout=10)
        except:
            result = {"error": "subprocess wait failed"}
        finally:
            try: Path(tmp).unlink()
            except: pass
        # 把结果写到 profile 旁的标记文件
        marker = Path(PROFILE_DIR) / "login_result.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps(result, ensure_ascii=False))
    
    threading.Thread(target=_wait_login, daemon=True).start()
    return first


def login():
    channel = _pick_channel()
    script = LOGIN_TEMPLATE.format(profile=PROFILE_DIR, channel=channel)
    fd, tmp = tempfile.mkstemp(suffix=".py", prefix="vu_yl_")
    os.close(fd)
    Path(tmp).write_text(script, encoding="utf-8")
    try:
        r = subprocess.run([_venv_python(), tmp], capture_output=True,
                           text=True, timeout=620, cwd=str(_HERE.parent))
        return r.returncode == 0
    except:
        return False
    finally:
        try: Path(tmp).unlink()
        except: pass


# ======== 改写 ========

REWRITE_TEMPLATE = '''\
import asyncio, json, sys
from pathlib import Path
sys.path.insert(0, r"{station_dir}")
from playwright.async_api import async_playwright
import copy_rewriter as _R

def log(m):
    """写 stderr —— 父进程会把它落到 logs/yuanbao_debug.log"""
    try:
        print("[rw] " + str(m), file=sys.stderr, flush=True)
    except Exception:
        pass

PROMPT_VISION = (
    "请仔细观察这几张从视频中提取的画面截图（按时间顺序），"
    "描述：1. 视频拍的是什么场景和主题；2. 有什么人物、在做什么动作；"
    "3. 画面的整体氛围和风格；"
    "4. 如果画面里有文字、字幕或对话，请尽量提取原文。"
    "最后用一段话概括这个视频的内容。"
)

async def is_generating(page):
    return await page.evaluate(
        "() => [...document.querySelectorAll('button,[role=button]')]"
        ".some(b => /停止|stop/i.test(b.textContent || ''))")

async def read_last_reply(page, bl):
    """读最后一条回复，跳过元宝的中间状态文案（'正在分析图片'/'正在搜索'等）。"""
    a = page.locator('.hyc-common-markdown,[class*="answer"],[class*="reply"],[class*="bubble"]')
    cnt = await a.count()
    if cnt > bl:
        t = (await a.nth(cnt - 1).inner_text()).strip()
        skip_keywords = ["正在分析", "正在搜索", "正在生成", "正在思考", "正在处理", "图片识别中",
                         "analyzing", "searching", "generating", "thinking", "processing"]
        if t and len(t) > 2 and not any(kw in t for kw in skip_keywords):
            return t
    return ""

async def main():
    profile = Path(r"{profile}")
    profile.mkdir(parents=True, exist_ok=True)
    import platform, subprocess, socket, os
    
    # 服务器（Linux）用无头 Chromium persistent context，Windows 用 CDP + Edge
    if platform.system() == "Linux":
        p = await async_playwright().start()
        browser_ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=True,
            executable_path="/usr/bin/chromium",
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-gpu",
                  "--disable-encryption-cookies",
                  "--disable-blink-features=AutomationControlled"])
        page = browser_ctx.pages[0] if browser_ctx.pages else await browser_ctx.new_page()
    else:
        need_launch = False
        CDP_PORT = int(os.environ.get("VU_CDP_PORT", "9223"))
        CDP_URL = "http://127.0.0.1:" + str(CDP_PORT)
        try:
            s = socket.create_connection(("127.0.0.1", CDP_PORT), timeout=2)
            s.close()
        except Exception:
            print(json.dumps({{"rewritten": None, "vision_desc": "", "error": "调试端口 " + str(CDP_PORT) + " 已失效（调试 Edge 可能被关闭），请重试改写。"}}, ensure_ascii=False))
            return
        p = await async_playwright().start()
        browser_ctx = await p.chromium.connect_over_cdp(CDP_URL)
        log("connect_over_cdp ok " + CDP_URL)
        ctx = browser_ctx.contexts[0] if browser_ctx.contexts else await browser_ctx.new_context()
        urls = [(pg.url or "") for pg in ctx.pages]
        log("已有页面: " + repr(urls))
        # 精确挑选元宝页；没有就新开一个（避免误驱动用户其它标签页）
        page = None
        for pg in ctx.pages:
            if "yuanbao.tencent.com" in (pg.url or ""):
                page = pg
                break
        if page is None:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            log("未找到元宝页，将用 " + (page.url or "about:blank"))
        try:
            await page.bring_to_front()
        except Exception:
            pass

    if "yuanbao.tencent.com" not in (page.url or ""):
        log("goto 元宝")
        await page.goto("https://yuanbao.tencent.com/", wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(2)
    log("当前 URL: " + (page.url or ""))

    if any(k in page.url.lower() for k in ("/login","/sign_in","passport")):
        print(json.dumps({{"rewritten": None, "vision_desc": "", "error":"元宝未登录（页面跳到了登录页）"}}, ensure_ascii=False))
        return

    # 元宝未登录时是弹窗/iframe，不会改 URL —— 必须单独检测，否则后面会卡在找输入框
    try:
        need_login = await page.evaluate(
            "() => !!document.querySelector('iframe.hyc-wechat-login, iframe[src*=\\"qrconnect\\"]')")
    except Exception:
        need_login = False
    if need_login:
        log("检测到登录弹窗 → 复制的登录态未生效")
        print(json.dumps({{"rewritten": None, "vision_desc": "", "error": "元宝显示未登录：复制到调试 Edge 的登录态未生效（Cookie 加密绑定所致）。请在弹出的调试 Edge 窗口里扫码登录一次，之后会一直复用该实例。"}}, ensure_ascii=False))
        return

    # === 图片上传：Playwright set_input_files 直接灌真实文件路径（受信注入）===
    # 关键结论：元宝是 React 应用。直接 input.files=DataTransfer + dispatchEvent 在 React 受控组件下
    #           不触发 onChange → 元宝收不到图片（图片没带上）。必须用 Playwright 的 set_input_files，
    #           它由浏览器内部受信派发 input/change 事件，React 能正确响应，且【不弹系统文件对话框】
    #           （set_input_files 与 expect_file_chooser 无关，不会触发原生选择框）。
    frames = {frames}
    if frames:
        try:
            import base64 as _b64
            _imgs = []
            for i, b in enumerate(frames[:3]):
                _src = b
                if _src.startswith('data:'):
                    _src = _src.split(',', 1)[1]
                try:
                    _data = _b64.b64decode(_src)
                    _p = r"{station_dir}" + ("/_yb_frame_%d.png" % i)
                    with open(_p, "wb") as _f:
                        _f.write(_data)
                    _imgs.append(_p)
                except Exception:
                    if os.path.exists(b):
                        _imgs.append(b)
            if _imgs:
                # 1) 先直接定位常驻隐藏 input；不存在再点「+」展开上传菜单（只点容器，不点「图片」子项，
                #    避免元宝自行 fileInput.click() 弹出原生对话框）
                fi = page.locator('input[type="file"]').first
                if await fi.count() == 0:
                    ub = page.locator('[class*="UploadFileSelector_iconContainer"]').first
                    if await ub.count() > 0:
                        await ub.click()
                        await asyncio.sleep(0.8)
                    fi = page.locator('input[type="file"]').first
                if await fi.count() > 0:
                    # 2) 受信注入，不弹窗，且触发 React onChange
                    await fi.set_input_files(_imgs)
                    log("已 set_input_files %d 张图片" % len(_imgs))
                    # 3) 等图片缩略图真正出现在输入框，确保上传完成再发文案
                    _ok = False
                    for _ in range(30):
                        await asyncio.sleep(0.5)
                        _n = await page.locator('div[contenteditable="true"] img, [class*="upload"] img, [class*="image"] img').count()
                        if _n > 0:
                            _ok = True
                            break
                    log("图片缩略图出现: " + str(_ok))
                    await asyncio.sleep(1)
                else:
                    log("未找到 input[type=file]，图片未上传")
                for _p in _imgs:
                    try: os.remove(_p)
                    except Exception: pass
        except Exception as eu:
            log("图片上传异常: " + str(eu)[:200])

    # === 改写 ===
    raw = {raw_text}
    topic = {topic}
    pmt = _R._build_prompt(raw, template={tmpl}, max_chars={max_chars}, topic=topic)

    # 多选择器 + 可见性校验，对齐 content-yuanbao.js 的 SEL_EDITOR
    editor = None
    for _sel in ['div[contenteditable="true"]', 'textarea[placeholder*="输入"]', 'textarea[placeholder*="描述"]']:
        cands = page.locator(_sel)
        nc = await cands.count()
        for i in range(nc):
            e = cands.nth(i)
            try:
                if await e.is_visible():
                    editor = e
                    break
            except Exception:
                pass
        if editor is not None:
            break
    if editor is None:
        try: await page.screenshot(path=r"{station_dir}" + "/logs/yb_no_editor.png")
        except Exception: pass
        log("找不到可见的输入框")
        print(json.dumps({{"rewritten": None, "vision_desc": "", "error": "元宝页面找不到输入框（截图见 station/server/logs/yb_no_editor.png），可能未登录或页面结构变化。"}}, ensure_ascii=False))
        return

    # 填充：contenteditable 用 innerText，textarea 用 value；都需 dispatch input 事件
    # （Playwright 的 fill() 仅支持 input/textarea，对 contenteditable 会失效——这是此前发不出去的根因）
    await editor.evaluate(
        "(el, text) => {{ if (el.getAttribute('contenteditable') === 'true') {{ el.innerText = text; }} else {{ el.value = text; }} el.dispatchEvent(new Event('input', {{bubbles:true}})); }}",
        pmt)
    await asyncio.sleep(0.4)
    log("已填入提示词 %d 字" % len(pmt))

    # 发送：先 Enter，再回退点"发送"按钮（对齐 content-yuanbao.js）
    bl = await page.locator('.hyc-common-markdown,[class*="answer"],[class*="reply"],[class*="bubble"]').count()
    try:
        await editor.press("Enter")
    except Exception:
        pass
    await asyncio.sleep(0.5)
    if not await is_generating(page):
        try:
            btn = page.locator('button:has-text("发送"), [aria-label*="发送"]').first
            if await btn.count() > 0:
                await btn.click()
                await asyncio.sleep(0.5)
        except Exception:
            pass

    # 等回复（基线对话数 + 双采样稳定）
    rw = ""
    t0 = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - t0 < {timeout}:
        if not await is_generating(page):
            t = await read_last_reply(page, bl)
            if t:
                await asyncio.sleep(2)
                if not await is_generating(page):
                    t2 = await read_last_reply(page, bl)
                    if t2 == t:
                        rw = _R._clean_reply(t) or t
                        break
                    t = t2  # 文本还在变，继续等
        await asyncio.sleep(1.5)

    log("等待结束，结果长度=%d" % len(rw or ""))
    if platform.system() == "Linux":
        await browser_ctx.close()
    # Windows：保留调试 Edge 实例，供下次改写直接复用（不关窗口）
    await p.stop()
    err = "" if rw else "元宝在 %ss 内没有返回可用结果（可到调试 Edge 窗口看当时状态）" % {timeout}
    print(json.dumps({{"rewritten": rw or None, "vision_desc": "", "error": err}}, ensure_ascii=False))


asyncio.run(main())
'''


def vision_and_rewrite(frames, raw_text, rewrite_template=None,
                       max_chars=None, topic=None, timeout=120, headless=False,
                       reuse_edge=True):
    channel = _pick_channel()
    profile = PROFILE_DIR
    cdp_port = 9223
    relaunch_user_edge = False
    if reuse_edge:
        info = ensure_edge_debug_port()
        if not info["ok"]:
            if info.get("relaunch_user_edge"):
                _relaunch_user_edge()
            return {"rewritten": None, "vision_desc": "", "error": info["msg"]}
        cdp_port = info["port"]
        relaunch_user_edge = info.get("relaunch_user_edge", False)
        if info.get("profile"):
            profile = info["profile"]
    os.environ["VU_CDP_PORT"] = str(cdp_port)
    _dlog(f"启动改写子进程 port={cdp_port} frames={len(frames or [])} raw_len={len(raw_text or '')}")
    script = REWRITE_TEMPLATE.format(
        station_dir=str(_HERE),
        profile=str(profile),
        reuse_edge=reuse_edge,
        headless=str(headless),
        frames=repr(frames or []),
        topic=json.dumps(topic or "", ensure_ascii=False),
        raw_text=json.dumps(raw_text or "", ensure_ascii=False),
        tmpl=json.dumps(rewrite_template or "", ensure_ascii=False),
        max_chars="None" if max_chars is None else json.dumps(max_chars),
        timeout=timeout,
    )
    fd, tmp = tempfile.mkstemp(suffix=".py", prefix="vu_yb_")
    os.close(fd)
    Path(tmp).write_text(script, encoding="utf-8")
    try:
        r = subprocess.run([_venv_python(), tmp], capture_output=True,
                           text=True, timeout=timeout + 60, cwd=str(_HERE.parent))
        if r.stderr.strip():
            _dlog("子进程 stderr:\n" + r.stderr.strip()[-3000:])
        if r.stdout.strip():
            try:
                res = json.loads(r.stdout.strip())
                _dlog(f"改写结果 rewritten_len={len(res.get('rewritten') or '')} "
                      f"error={res.get('error') or '无'}")
                return res
            except json.JSONDecodeError:
                _dlog("stdout 非 JSON: " + r.stdout.strip()[:500])
                return {"rewritten": None, "vision_desc": "",
                        "error": r.stdout.strip()[:200]}
        if r.stderr.strip():
            debug_lines = [l for l in r.stderr.strip().splitlines() if l.strip()]
            summary = "\n".join(debug_lines[-6:]) if len(debug_lines) > 6 else r.stderr.strip()
            return {"rewritten": None, "vision_desc": "", "error": summary[:500]}
        _dlog(f"子进程无输出 rc={r.returncode}")
        return {"rewritten": None, "vision_desc": "",
                "error": f"无输出, rc={r.returncode}"}
    except subprocess.TimeoutExpired:
        _dlog("改写子进程超时")
        return {"rewritten": None, "vision_desc": "", "error": "子进程超时"}
    except Exception as e:
        _dlog(f"改写异常: {e}")
        return {"rewritten": None, "vision_desc": "", "error": str(e)[:200]}
    finally:
        try: Path(tmp).unlink()
        except: pass
        os.environ.pop("VU_CDP_PORT", None)
        # 调试 Edge 实例「保活」：下次改写直接复用，避免每次都重启用户的 Edge。
        # 用户手动关掉它也没关系——下次检测到端口失效会自动走完整流程（自愈）。
        if relaunch_user_edge:
            _relaunch_user_edge()
        _dlog("=== 本次改写结束 ===")


def _relaunch_user_edge():
    """把用户原本的 Edge（默认 profile、不带调试端口）重新拉起来。"""
    edge = _edge_exe()
    if not edge:
        return
    try:
        subprocess.Popen([edge], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _dlog("已重新拉起用户 Edge")
    except Exception as e:
        _dlog(f"重开用户 Edge 失败: {e}")


def _cli_arg(name, default=""):
    for i, a in enumerate(sys.argv):
        if a == name and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if "--login" in sys.argv:
        raise SystemExit(0 if login() else 1)
    if "--rewrite" in sys.argv:
        frames = [f.strip() for f in _cli_arg("--frames").split(",") if f.strip()]
        raw = _cli_arg("--raw_text")
        tmpl = _cli_arg("--template") or None
        topic = _cli_arg("--topic")
        mc = _cli_arg("--max_chars")
        r = vision_and_rewrite(
            frames, raw, rewrite_template=tmpl,
            max_chars=int(mc) if mc and mc.isdigit() else None,
            topic=topic, reuse_edge=True,
        )
        print(json.dumps(r, ensure_ascii=False))
        raise SystemExit(0)
    print(f"子进程隔离 | profile={PROFILE_DIR}")
    print(f"已初始化: {has_profile()}")
