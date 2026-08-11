# -*- coding: utf-8 -*-
"""yuanbao_client.py — 子进程隔离模式（独立 Python 进程跑浏览器操作）"""

import asyncio
import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger("yuanbao")
_HERE = Path(__file__).resolve().parent
PROFILE_DIR = os.environ.get("VU_YUANBAO_PROFILE",
                             str(_HERE / "logs" / ".yuanbao-profile"))


def has_profile():
    return (Path(PROFILE_DIR) / "Local State").exists()


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
    
    # 自动选择浏览器：Linux 用 chromium，Windows 用 msedge
    try:
        import platform
        if platform.system() == "Linux":
            pw = await async_playwright().start()
            browser = await pw.chromium.launch_persistent_context(
                user_data_dir=str(profile),
                headless=True,
                executable_path="/usr/bin/chromium",
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-gpu",
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
        await asyncio.sleep(5)
        
        # 始终截取页面截图（含可能存在的 QR 码/登录弹窗）
        screenshot_bytes = await page.screenshot(type="png", full_page=False)
        screenshot_b64 = base64.b64encode(screenshot_bytes).decode("ascii")
        
        # 检测页面是否有登录入口（QR码/手机号输入框）
        has_login_form = await page.evaluate("""() => {{
            // 检测 QR 码相关元素
            if (document.querySelector('canvas, img[src*="qr"], [class*="qrcode"], [class*="qr"], [class*="login"], [class*="Login"]'))
                return true;
            // 检测手机号输入框
            if (document.querySelector('input[type="tel"], input[placeholder*="手机"], input[placeholder*="phone"]'))
                return true;
            return false;
        }}""")
        
        # 尝试裁剪出 QR 码：找最大的 canvas 或 qrcode 元素
        qr_b64 = screenshot_b64
        try:
            # 评估页面里所有 canvas 和 img 的位置，挑最大的
            qr_rect = await page.evaluate("""() => {
                // 找所有可能是 QR 码的元素
                const candidates = [
                    ...document.querySelectorAll('canvas'),
                    ...document.querySelectorAll('[class*="qrcode"], [class*="QrCode"], [class*="qr-code"]'),
                    ...document.querySelectorAll('[class*="qrcode-img"], [class*="qrbox"]'),
                ];
                let best = null, bestSize = 0;
                for (const el of candidates) {
                    const r = el.getBoundingClientRect();
                    const sz = r.width * r.height;
                    if (sz > 5000 && sz > bestSize) {  // 至少 5万像素
                        best = {x: r.x, y: r.y, w: r.width, h: r.height};
                        bestSize = sz;
                    }
                }
                return best;
            }""")
            if qr_rect:
                # 裁剪该区域（加 padding）
                pad = 20
                from PIL import Image
                import io
                img = Image.open(io.BytesIO(screenshot_bytes))
                x = max(0, int(qr_rect['x'] - pad))
                y = max(0, int(qr_rect['y'] - pad))
                w = min(img.width - x, int(qr_rect['w'] + pad*2))
                h = min(img.height - y, int(qr_rect['h'] + pad*2))
                if w > 100 and h > 100:
                    cropped = img.crop((x, y, x+w, y+h))
                    # 放大一倍，便于扫码
                    cropped = cropped.resize((cropped.width*2, cropped.height*2), Image.LANCZOS)
                    buf = io.BytesIO()
                    cropped.save(buf, 'PNG')
                    qr_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        except Exception as ex:
            print(f"QR crop fallback: {ex}", file=sys.stderr)
        
        print(json.dumps({{
            "ok": True,
            "logged_in": not has_login_form,
            "has_login_form": has_login_form,
            "screenshot_b64": screenshot_b64,
            "qr_b64": qr_b64,
        }}, ensure_ascii=False))
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({{"error": str(e)}}, ensure_ascii=False))
    finally:
        # 不关 browser！等下一次 check/login 复用 profile
        pass

asyncio.run(main())
'''


def login_server():
    """服务器端无头登录：返回 QR 码截图给前端让用户扫码"""
    channel = _pick_channel()
    script = SERVER_LOGIN_TEMPLATE.format(profile=PROFILE_DIR, channel=channel)
    fd, tmp = tempfile.mkstemp(suffix=".py", prefix="vu_sl_")
    os.close(fd)
    Path(tmp).write_text(script, encoding="utf-8")
    try:
        r = subprocess.run([_venv_python(), tmp], capture_output=True,
                           text=True, timeout=120, cwd=str(_HERE.parent))
        if r.stdout.strip():
            try:
                return json.loads(r.stdout.strip().split("\n")[-1])
            except json.JSONDecodeError:
                return {"error": f"parse fail: {r.stdout[:200]}", "stderr": r.stderr[:500]}
        return {"error": "no output", "stderr": r.stderr[:500]}
    except subprocess.TimeoutExpired:
        return {"error": "timeout"}
    except Exception as e:
        return {"error": str(e)}
    finally:
        try: Path(tmp).unlink()
        except: pass


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
    import subprocess, socket
    # 复用已有浏览器：先试着连 CDP，连上就不启新的
    CDP_URL = "http://127.0.0.1:9223"
    need_launch = False
    try:
        s = socket.create_connection(("127.0.0.1", 9223), timeout=2)
        s.close()
    except Exception:
        need_launch = True
    if need_launch:
        subprocess.Popen([
            r"C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
            "--remote-debugging-port=9223",
            "--user-data-dir=" + str(profile),
            "--no-first-run", "--no-default-browser-check",
            "--disable-blink-features=AutomationControlled",
        ] + (["--headless=new"] if {headless} else []),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        await asyncio.sleep(3)
    p = await async_playwright().start()
    browser = await p.chromium.connect_over_cdp(CDP_URL)
    ctx = browser.contexts[0]
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

    # CDP disconnect — browser stays open for reuse
    await p.stop()
    print(json.dumps({{"rewritten": rw or None, "vision_desc": "", "error": ""}}, ensure_ascii=False))


asyncio.run(main())
'''


def vision_and_rewrite(frames, raw_text, rewrite_template=None,
                       max_chars=None, topic=None, timeout=120, headless=False):
    channel = _pick_channel()
    script = REWRITE_TEMPLATE.format(
        station_dir=str(_HERE),
        profile=PROFILE_DIR,
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


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if "--login" in sys.argv:
        raise SystemExit(0 if login() else 1)
    print(f"子进程隔离 | profile={PROFILE_DIR}")
    print(f"已初始化: {has_profile()}")
