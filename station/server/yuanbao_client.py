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
    a = page.locator('.hyc-common-markdown,[class*="answer"],[class*="reply"],[class*="bubble"]')
    cnt = await a.count()
    if cnt > bl:
        t = (await a.nth(cnt - 1).inner_text()).strip()
        if t and len(t) > 2:
            return t
    return ""

async def main():
    profile = Path(r"{profile}")
    profile.mkdir(parents=True, exist_ok=True)
    # 用 CDP 方式连接浏览器（不与 Python 进程绑定，退出后浏览器不会关）
    import subprocess
    subprocess.run('powershell -c "Get-Process chrome,msedge -ErrorAction SilentlyContinue | Where-Object {{$$_.CommandLine -match \\"remote-debugging-port=9223\\"}} | Stop-Process -Force"', shell=True, capture_output=True)
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
    browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9223")
    ctx = browser.contexts[0]
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    await page.goto("https://yuanbao.tencent.com/", wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(2)

    if any(k in page.url.lower() for k in ("/login","/sign_in","passport")):
        print(json.dumps({{"error":"未登录"}}, ensure_ascii=False))
        return

    frames = {frames}
    topic = {topic}
    vision_desc = ""

    async def upload_frames(fs, label=""):
        """把帧图传到元宝输入框。click input[type=file] 触发原生对话框，
        Playwright 拦截 file_chooser 设文件。"""
        if not fs:
            return False
        fs3 = fs[:3]
        inp = page.locator('input[type="file"]').first
        if await inp.count() == 0:
            print(f"[yuanbao-upload{{label}}] no input[type=file] found", file=sys.stderr)
            return False
        # 策略 A：click input → 拦截原生文件对话框 → 设文件
        try:
            async with page.expect_file_chooser(timeout=5000) as fc:
                await inp.click(force=True)
            chooser = await fc.value
            await chooser.set_files(fs3)
            print(f"[yuanbao-upload{{label}}] file_chooser ok, {{len(fs3)}} files", file=sys.stderr)
            return True
        except Exception as e1:
            print(f"[yuanbao-upload{{label}}] file_chooser err: {{e1}}", file=sys.stderr)
        # 策略 B：兜底 set_input_files
        try:
            await inp.set_input_files(fs3)
            print(f"[yuanbao-upload{{label}}] set_input_files ok", file=sys.stderr)
            return True
        except Exception as e2:
            print(f"[yuanbao-upload{{label}}] set_input_files err: {{e2}}", file=sys.stderr)
        print(f"[yuanbao-upload{{label}}] FAILED", file=sys.stderr)
        return False

    if frames:
        try:
            ok = await upload_frames(frames, "-vision")
            if ok:
                await asyncio.sleep(3)
                sel = 'textarea[placeholder*="输入"],div[contenteditable="true"]'
                ed = page.locator(sel).first
                if await ed.count() > 0:
                    await ed.click()
                    await ed.fill(PROMPT_VISION)
                    await asyncio.sleep(0.5)
                    bl = await page.locator('.hyc-common-markdown,[class*="answer"],[class*="reply"],[class*="bubble"]').count()
                    await page.keyboard.press("Enter")
                    t0 = asyncio.get_event_loop().time()
                    while asyncio.get_event_loop().time() - t0 < 60:
                        if not await is_generating(page):
                            t = await read_last_reply(page, bl)
                            if t:
                                vision_desc = t
                                break
                        await asyncio.sleep(1.5)
                    # 合并用户填的话题和元宝识图结果
                    if vision_desc:
                        topic = (topic + "。画面：" + vision_desc) if topic else vision_desc
        except Exception as e:
            print(f"[yuanbao-vision] error: {{e}}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()

    raw = {raw_text}
    pmt = _R._build_prompt(raw, template={tmpl}, max_chars={max_chars}, topic=topic)

    # 🔧 改写步骤：先重新传图，再发改写提示词
    if frames:
        try:
            ok = await upload_frames(frames, "-rewrite")
            if ok:
                await asyncio.sleep(3)
        except Exception as e:
            print(f"[yuanbao-rewrite-upload] error: {{e}}", file=sys.stderr)
            sys.stderr.flush()

    sel = 'textarea[placeholder*="输入"],div[contenteditable="true"]'
    ed = page.locator(sel).first
    await ed.click()
    await asyncio.sleep(0.3)
    await ed.fill(pmt)
    await asyncio.sleep(0.3)
    bl = await page.locator('.hyc-common-markdown,[class*="answer"],[class*="reply"],[class*="bubble"]').count()
    await page.keyboard.press("Enter")
    rw = ""
    t0 = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - t0 < {timeout}:
        if not await is_generating(page):
            t = await read_last_reply(page, bl)
            if t:
                rw = _R._clean_reply(t) or t
                break
        await asyncio.sleep(1.5)

        # CDP 模式：disconnect 即可，浏览器进程不会关
    await browser.close()
    await p.stop()
    print(json.dumps({{"rewritten": rw or None, "vision_desc": vision_desc, "error": ""}}, ensure_ascii=False))

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
