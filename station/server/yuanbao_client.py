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
    """确保 Edge 以调试端口运行并复用其登录态（独立临时 profile，自动选空闲端口）。

    返回 (ok: bool, msg: str, tmp_profile: str|None, port: int|None, edge_pid: int|None)：
      - tmp_profile=None：复用已运行的 Edge（调试端口已有 CDP），无需清理
      - tmp_profile 非空：本函数已（1）关闭用户原 Edge 以释放 cookie 锁、
        （2）复制登录态到临时目录、（3）用独立临时 profile 启动调试版 Edge；
        并返回其 pid 供调用方精确回收（不影响用户原浏览器）
    """
    import socket
    import tempfile as _tempfile
    import shutil as _shutil

    def _cdp_ready(port):
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=2)
            s.close()
            return True
        except Exception:
            return False

    def _free_port():
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
        s.close()
        return p

    # 0) 已存在调试端口（用户手动开启或上次残留）→ 直连
    for cand in (9223,):
        if _cdp_ready(cand):
            return True, "Edge 调试端口已就绪", None, cand, None

    edge = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    if not Path(edge).exists():
        return False, f"未找到 Edge 可执行文件：{edge}", None, None, None

    # 1) 关闭用户原 Edge（仅为了释放 cookie 锁以便复制登录态；稍后会重新拉起其原浏览器）
    if _edge_running():
        try:
            subprocess.run(["taskkill", "/F", "/IM", "msedge.exe"],
                           capture_output=True, timeout=60)
        except Exception:
            pass
        for _ in range(40):
            time.sleep(0.5)
            if not _edge_running():
                break

    # 2) 复制默认 profile 登录态到临时目录（独立锁，避开启动加速单例冲突）
    tmp_profile = Path(_tempfile.mkdtemp(prefix="vu_edge_"))
    if not copy_edge_login_state(tmp_profile):
        _shutil.rmtree(str(tmp_profile), ignore_errors=True)
        return False, "复制 Edge 登录态失败（无法读取 Cookies）", None, None, None

    # 2.5) 重新拉起用户原本的 Edge（默认 profile，不带调试端口），让其浏览器回归
    try:
        subprocess.Popen([edge], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

    # 3) 选一个空闲端口，避免 9223 被残留进程占用导致绑定失败（最常见的超时根因）
    port = _free_port()
    log_path = tmp_profile / "edge_launch.log"
    try:
        proc = subprocess.Popen(
            [edge, f"--remote-debugging-port={port}",
             f"--user-data-dir={tmp_profile}",
             "--no-first-run", "--no-default-browser-check",
             "--disable-blink-features=AutomationControlled",
             "--remote-debugging-address=127.0.0.1"],
            stdout=str(log_path), stderr=subprocess.STDOUT)
    except Exception as e:
        _shutil.rmtree(str(tmp_profile), ignore_errors=True)
        return False, f"启动调试版 Edge 失败: {e}", None, None, None

    # 4) 等调试端口就绪（最多 30s）
    for _ in range(60):
        time.sleep(0.5)
        if _cdp_ready(port):
            return True, f"已以调试模式启动 Edge（端口 {port}，原浏览器已回归）", \
                   str(tmp_profile), port, proc.pid

    # 超时：抓取 Edge 自身日志辅助定位
    try:
        diag = log_path.read_text(errors="replace")[:1200]
    except Exception:
        diag = "(无日志输出)"
    try:
        proc.kill()
    except Exception:
        pass
    _shutil.rmtree(str(tmp_profile), ignore_errors=True)
    return False, f"启动调试版 Edge 超时（30s 未就绪）。Edge 日志:\n{diag}", \
           str(tmp_profile), port, proc.pid


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
        CDP_PORT = int(os.environ.get("VU_CDP_PORT", "9223"))
        CDP_URL = "http://127.0.0.1:" + str(CDP_PORT)
        need_launch = False
        try:
            s = socket.create_connection(("127.0.0.1", CDP_PORT), timeout=2)
            s.close()
        except Exception:
            need_launch = True
        if need_launch:
            if {reuse_edge}:
                print(json.dumps({{"rewritten": None, "vision_desc": "", "error": "Edge 未开调试端口(" + str(CDP_PORT) + ")。请以调试模式启动 Edge：msedge.exe --remote-debugging-port=" + str(CDP_PORT) + "（或点桌面端「启动调试版 Edge」）。"}}, ensure_ascii=False))
                return
            subprocess.Popen([
                r"C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
                "--remote-debugging-port=" + str(CDP_PORT),
                "--user-data-dir=" + str(profile),
                "--no-first-run", "--no-default-browser-check",
                "--disable-blink-features=AutomationControlled",
            ] + (["--headless=new"] if {headless} else []),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            await asyncio.sleep(3)
        p = await async_playwright().start()
        browser_ctx = await p.chromium.connect_over_cdp(CDP_URL)
        ctx = browser_ctx.contexts[0]
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    
    await page.goto("https://yuanbao.tencent.com/", wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(2)

    if any(k in page.url.lower() for k in ("/login","/sign_in","passport")):
        print(json.dumps({{"error":"未登录"}}, ensure_ascii=False))
        return

    # === 图片上传：点 + 弹菜单 → Playwright click"图片" → expect file_chooser ===
    frames = {frames}
    if frames:
        ub = page.locator('div[class*="UploadFileSelector_iconContainer"]').first
        if await ub.count() > 0:
            try: await page.screenshot(path=r"{station_dir}" + "/yb_before_click.png")
            except: pass
            await ub.click()
            await asyncio.sleep(1)
            try: await page.screenshot(path=r"{station_dir}" + "/yb_menu_open.png")
            except: pass
            # 用 Playwright 真鼠标事件点"图片"菜单（React 需要完整 MouseEvent）
            pic = page.get_by_text("图片", exact=True).first
            if await pic.count() == 0:
                pic = page.locator('div').filter(has_text="图片").first
            print(f"[yuanbao] 图片菜单 count={{await pic.count()}}", file=sys.stderr)
            try:
                # 点图片时预期触发原生文件对话框 → Playwright 拦截
                async with page.expect_file_chooser(timeout=10000) as fc:
                    await pic.click(force=True)
                chooser = await fc.value
                await chooser.set_files(frames[:3])
                print(f"[yuanbao] file_chooser ok", file=sys.stderr)
                # 等图片在输入框里出现（元宝会在 editor 里显示缩略图）
                await asyncio.sleep(5)
                try: await page.screenshot(path=r"{station_dir}" + "/yb_images_attached.png")
                except: pass
            except Exception as eu:
                print(f"[yuanbao] file_chooser err: {{eu}}", file=sys.stderr)
                # 兜底：等 file input 出现用 set_input_files
                await asyncio.sleep(1)
                n = await page.locator('input[type="file"]').count()
                print(f"[yuanbao] file inputs={{n}}", file=sys.stderr)
                if n > 0:
                    try:
                        await page.locator('input[type="file"]').last.set_input_files(frames[:3])
                        print(f"[yuanbao] set_input_files ok", file=sys.stderr)
                        await asyncio.sleep(2)
                    except Exception as eu2:
                        print(f"[yuanbao] set_input_files err: {{eu2}}", file=sys.stderr)
            try: await page.screenshot(path=r"{station_dir}" + "/yb_after_click.png")
            except: pass

    # === 改写 ===
    raw = {raw_text}
    topic = {topic}
    pmt = _R._build_prompt(raw, template={tmpl}, max_chars={max_chars}, topic=topic)

    sel = 'textarea[placeholder*="输入"],div[contenteditable="true"]'
    await page.wait_for_selector(sel, timeout=15000)
    ed = page.locator(sel).first
    await ed.click()
    await asyncio.sleep(0.3)
    await ed.fill(pmt)
    await asyncio.sleep(0.3)
    bl = await page.locator('.hyc-common-markdown,[class*="answer"],[class*="reply"],[class*="bubble"]').count()
    await page.keyboard.press("Enter")
    rw = ""
    last_text = ""
    t0 = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - t0 < {timeout}:
        if not await is_generating(page):
            t = await read_last_reply(page, bl)
            if t:
                # 等文本稳定：隔 2 秒再读一次，不变才接受
                await asyncio.sleep(2)
                if not await is_generating(page):
                    t2 = await read_last_reply(page, bl)
                    if t2 == t:
                        rw = _R._clean_reply(t) or t
                        break
                    t = t2  # 文本还在变，继续等
        await asyncio.sleep(1.5)

    # CDP disconnect / close persistent context
    if platform.system() == "Linux":
        await browser_ctx.close()
    else:
        # Windows：仅当自己 launch 的 msedge.exe 时关闭；复用已运行 Edge 时保留用户浏览器
        if need_launch:
            try:
                await browser_ctx.close()
            except Exception:
                pass
    await p.stop()
    print(json.dumps({{"rewritten": rw or None, "vision_desc": "", "error": ""}}, ensure_ascii=False))


asyncio.run(main())
'''


def vision_and_rewrite(frames, raw_text, rewrite_template=None,
                       max_chars=None, topic=None, timeout=120, headless=False,
                       reuse_edge=True):
    channel = _pick_channel()
    profile = PROFILE_DIR
    tmp_profile = None
    cdp_port = 9223
    edge_pid = None
    if reuse_edge:
        ok, msg, tmp_profile, cdp_port, edge_pid = ensure_edge_debug_port()
        if not ok:
            return {"rewritten": None, "vision_desc": "", "error": msg}
        if tmp_profile:
            profile = tmp_profile
    os.environ["VU_CDP_PORT"] = str(cdp_port)
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
        if r.stdout.strip():
            try:
                return json.loads(r.stdout.strip())
            except json.JSONDecodeError:
                return {"rewritten": None, "vision_desc": "",
                        "error": r.stdout.strip()[:200]}
        if r.stderr.strip():
            debug_lines = [l for l in r.stderr.strip().splitlines() if l.strip()]
            summary = "\n".join(debug_lines[-6:]) if len(debug_lines) > 6 else r.stderr.strip()
            return {"rewritten": None, "vision_desc": "",
                    "error": summary[:500] + ("\n[stdout]: " + r.stdout.strip()[:200] if r.stdout.strip() else "")}
        return {"rewritten": None, "vision_desc": "",
                "error": f"无输出, rc={r.returncode}"}
    except subprocess.TimeoutExpired:
        return {"rewritten": None, "vision_desc": "", "error": "子进程超时"}
    except Exception as e:
        return {"rewritten": None, "vision_desc": "", "error": str(e)[:200]}
    finally:
        try: Path(tmp).unlink()
        except: pass
        os.environ.pop("VU_CDP_PORT", None)
        if tmp_profile and edge_pid:
            import shutil as _shutil
            # 仅回收我们拉起的临时调试 Edge（按 PID 树），不影响用户原浏览器
            try:
                subprocess.run(["taskkill", "/F", "/PID", str(edge_pid), "/T"],
                               capture_output=True, timeout=30)
            except Exception:
                pass
            try:
                _shutil.rmtree(tmp_profile, ignore_errors=True)
            except Exception:
                pass


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
