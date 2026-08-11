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

PROMPT_VISION = "请用一句话描述画面里拍的是什么场景、有什么人物或动作"

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
    p = await async_playwright().start()
    ctx = await p.chromium.launch_persistent_context(
        user_data_dir=str(profile), channel="{channel}",
        headless={headless},
        args=["--disable-blink-features=AutomationControlled"])
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    await page.goto("https://yuanbao.tencent.com/", wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(2)

    if any(k in page.url.lower() for k in ("/login","/sign_in","passport")):
        print(json.dumps({{"error":"未登录"}}, ensure_ascii=False))
        return

    frames = {frames}
    topic = {topic}
    vision_desc = ""

    if frames and not topic:
        try:
            inp = page.locator('input[type="file"]').first
            if await inp.count() > 0:
                await inp.set_input_files(frames[0])
                await asyncio.sleep(2)
                sel = 'textarea[placeholder*="输入"],div[contenteditable="true"]'
                ed = page.locator(sel).first
                if await ed.count() > 0:
                    await ed.click()
                    await ed.fill(PROMPT_VISION)
                    bl = await page.locator('.hyc-common-markdown,[class*="answer"],[class*="reply"],[class*="bubble"]').count()
                    await page.keyboard.press("Enter")
                    t0 = asyncio.get_event_loop().time()
                    while asyncio.get_event_loop().time() - t0 < 45:
                        if not await is_generating(page):
                            t = await read_last_reply(page, bl)
                            if t:
                                vision_desc = t
                                break
                        await asyncio.sleep(1.5)
                    topic = topic or vision_desc
        except Exception:
            pass

    raw = {raw_text}
    pmt = _R._build_prompt(raw, template={tmpl}, max_chars={max_chars}, topic=topic)
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

    await ctx.close()
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
        channel=channel,
        headless=str(headless),
        frames=json.dumps(frames or [], ensure_ascii=False),
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
            # 提取最后 3 行作为错误摘要（完整 traceback 太长）
            lines = [l for l in r.stderr.strip().splitlines() if l.strip()]
            summary = "\n".join(lines[-5:]) if len(lines) > 5 else r.stderr.strip()
            return {"rewritten": None, "vision_desc": "",
                    "error": summary[:500]}
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
