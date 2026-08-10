# -*- coding: utf-8 -*-
"""
copy_rewriter.py — DeepSeek 文案改写（Playwright + OpenTeam 技术）

- 复用用户 Edge/Chrome 已登录的 DeepSeek 网页会话
- DOM 操作手法对齐 openteam（afumu/openteam · src/content/sites/deepseek.ts）：
  · 输入框是 **textarea**（不是 contenteditable）→ value + InputEvent
  · 发送按钮按 openteam selector 轮询等待，排除禁用/附件/语音按钮
  · 生成中判定 = 页面存在「停止」按钮
  · 回复抓取 = [data-virtual-list-item-key] .ds-message .ds-markdown（排除思考内容）
- 回复完成判定叠加 openteam replyObserver 的「文本稳定 + 非生成中」双条件
- 零 API Token 成本

环境要求：
- pip install playwright
- Edge 或 Chrome 已安装
- **首次使用需登录一次**：`python station/server/copy_rewriter.py --login`

登录态放在**专用配置目录**（默认 station/logs/.deepseek-profile，可用
VU_DEEPSEEK_PROFILE 覆盖），不借用用户日常浏览器的 User Data：
Chromium 对 user_data_dir 有单例锁，Edge/Chrome 开着时 Playwright 无法启动
（表现为 TargetClosedError，或 URL 停在 about:blank）。用专用目录后，
**改写与日常浏览可以同时进行，无需关闭浏览器**。

安全：prompt 始终以 evaluate 参数传入页面，不做 JS 字符串拼接（防脚本注入）。
"""

import asyncio
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger("copy_rewriter")

SYSTEM_PROMPT = """你是短视频配音文案优化师。

## 任务
将输入的原始对白/字幕，改写为一条适合 TTS 配音的短视频旁白。

## 要求
1. **口语化**：自然说话语气，不要书面语
2. **有钩子**：开头 3 秒抓注意力（悬念/提问/冲击性陈述）
3. **有行动**：结尾留互动引导（"点赞关注"、"评论区聊聊"等）
4. **时长适配**：30-100 字，适合 15-60 秒短视频
5. **纯文案**：只输出最终文案，不要解释、前缀、标注

## 示例
输入：这段打斗太精彩了
输出：你见过这么炸裂的打斗吗？三秒之内反转三次，这操作你学不来！评论区告诉我你看到了第几遍，点赞关注不迷路！"""

# 🆕 改写模板：用户在前端选择风格，系统自动传到这里作为角色设定
REWRITE_TEMPLATES = {
    "带货": "你是带货主播。改写为口播带货文案：突出产品卖点、制造紧迫感、引导下单。语气热情有感染力。",
    "解说": "你是知识解说博主。改写为解说旁白：逻辑清晰、深入浅出、善用设问引导。语气沉稳专业。",
    "Vlog": "你是生活 Vlog 博主。改写为 Vlog 口播：自然随性、像在跟朋友聊天。语气轻松真实。",
}

# ---------------------------------------------------------------------------
# OpenTeam DeepSeek selector（原样搬运 src/content/sites/deepseek.ts）
# ---------------------------------------------------------------------------
SEL_EDITOR = (
    'textarea[name="search"], textarea[placeholder*="DeepSeek"], '
    'textarea[placeholder*="发送消息"]'
)
SEL_RESPONSE = (
    '[data-virtual-list-item-key] .ds-message .ds-markdown'
    ':not(.ds-think-content .ds-markdown)'
)
SEL_RESPONSE_CONTAINER = "[data-virtual-list-item-key]"
SEL_COMPOSER = '.aaff8b8f, ._77cefa5, [class*="composer"]'
SEL_SEND_BUTTON = (
    '.bf38813a [role="button"], .bf38813a button, '
    '[role="button"]._52c986b, button._52c986b, '
    '[role="button"].ds-icon-button, button.ds-icon-button, '
    '[role="button"].ds-button--primary.ds-button--filled.ds-button--circle, '
    'button.ds-button--primary.ds-button--filled.ds-button--circle'
)

# openteam replyObserver 的稳定判定节奏
POLL_INTERVAL = 1.0          # REPLY_POLL_INTERVAL_MS 折算
FINAL_SETTLE_SEC = 1.5       # RESPONSE_FINAL_SETTLE_MS
SHORT_REPLY_SETTLE_SEC = 5.0  # SHORT_REPLY_STABLE_SETTLE_MS（很短的回复多等一会儿）
SHORT_REPLY_CHARS = 24        # 低于此长度视为「很短的回复」，可能还在流式输出

# 注入到页面的 JS 工具集（对齐 openteam domText / waitForElement / deepseek.ts）
_JS_HELPERS = r"""
const SEL = %(sel)s;

function isVisibleInteractive(el) {
  const s = window.getComputedStyle(el);
  return !(s.display === 'none' || s.visibility === 'hidden'
           || s.opacity === '0' || s.pointerEvents === 'none');
}

// openteam domText.buttonLabelMatches
function labelMatches(btn, re) {
  const label = [btn.getAttribute('aria-label'), btn.getAttribute('title'), btn.textContent]
    .filter(Boolean).join(' ').toLowerCase();
  return re.test(label);
}

// openteam deepseek.isDeepSeekSendButton
function isSendButton(el) {
  if (el.getAttribute('aria-disabled') === 'true') return false;
  if (el instanceof HTMLButtonElement && el.disabled) return false;
  if (el.classList.contains('ds-toggle-button')) return false;
  if (labelMatches(el, /attach|upload|file|camera|image|voice|microphone|附件|上传|图片|语音/)) return false;
  const cls = el.classList;
  const primaryCircle = cls.contains('ds-button--primary')
    && cls.contains('ds-button--filled') && cls.contains('ds-button--circle');
  if (!cls.contains('_52c986b') && !cls.contains('ds-icon-button') && !primaryCircle) return false;
  return isVisibleInteractive(el);
}

// openteam deepseek.findDeepSeekSendButton — 以 composer 为范围，倒序取最后一个候选
function findSendButton() {
  const editor = document.querySelector(SEL.editor);
  const composer = (editor && editor.closest(SEL.composer)) || document.body;
  const candidates = [...composer.querySelectorAll(SEL.sendButton)];
  return candidates.reverse().find(isSendButton) || null;
}

// openteam deepseek.findDeepSeekStopButton — 存在停止按钮 = 还在生成
function findStopButton() {
  return [...document.querySelectorAll('[role="button"], button')].find(btn => {
    if (!labelMatches(btn, /stop|stopping|停止|中止/)) return false;
    if (btn.getAttribute('aria-disabled') === 'true') return false;
    if (btn instanceof HTMLButtonElement) return !btn.disabled;
    return isVisibleInteractive(btn);
  }) || null;
}

// openteam deepseek.isFinalResponseMarkdown — 排除思考内容，必须在虚拟列表项内
function isFinalResponse(el) {
  if (el.closest('.ds-think-content')) return false;
  return Boolean(el.closest(SEL.responseContainer));
}

// openteam responseContainers.keepDeepestResponseContainers
function keepDeepest(list) {
  return list.filter(c => !list.some(o => o !== c && c.contains(o)));
}

function responseContainers() {
  return keepDeepest([...document.querySelectorAll(SEL.response)].filter(isFinalResponse));
}

// openteam domText.extractCleanTextFromDom
const SKIP_TAGS = new Set(['SCRIPT', 'STYLE', 'BUTTON', 'TEXTAREA', 'SVG']);
const BLOCK_TAGS = new Set(['P','DIV','BR','LI','TR','PRE','BLOCKQUOTE','H1','H2','H3','H4','H5','H6']);
function extractCleanText(node) {
  const buf = [];
  (function visit(cur) {
    if (cur.nodeType === Node.TEXT_NODE) { buf.push(cur.textContent || ''); return; }
    if (cur.nodeType !== Node.ELEMENT_NODE) return;
    if (cur.getAttribute('aria-hidden') === 'true') return;
    if (SKIP_TAGS.has(cur.tagName)) return;
    if (BLOCK_TAGS.has(cur.tagName)) buf.push('\n');
    for (const child of cur.childNodes) visit(child);
  })(node);
  return buf.join('').replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
}
"""


def _js(body):
    """把 openteam JS 工具集与调用体拼成一个 IIFE 表达式。"""
    import json
    sel = json.dumps({
        "editor": SEL_EDITOR,
        "response": SEL_RESPONSE,
        "responseContainer": SEL_RESPONSE_CONTAINER,
        "composer": SEL_COMPOSER,
        "sendButton": SEL_SEND_BUTTON,
    }, ensure_ascii=False)
    return "(%(args)s) => {\n%(helpers)s\n%(body)s\n}" % {
        "args": "arg",
        "helpers": _JS_HELPERS % {"sel": sel},
        "body": body,
    }


_last_error = ""  # 🆕 最后一次改写失败原因（供 pipeline 传播到前端）


def is_available():
    """检查 Playwright 是否可导入。"""
    try:
        import playwright.async_api  # noqa: F401
        return True
    except ImportError:
        return False


# 专用登录态目录：__file__ 相对锚定 + 环境变量覆盖（对齐 pipeline.py 的 VU_* 约定）
_SERVER_DIR = Path(__file__).resolve().parent
PROFILE_DIR = Path(os.environ.get(
    "VU_DEEPSEEK_PROFILE", _SERVER_DIR.parent / "logs" / ".deepseek-profile"))


def _pick_channel():
    """选择浏览器通道：Edge 优先（国产 Windows 更常见），回退 Chrome。

    只判断浏览器**是否安装**，不再读取它的 User Data —— 登录态存在
    PROFILE_DIR，与用户日常浏览器完全隔离，因此不受单例锁影响。
    """
    override = os.environ.get("VU_DEEPSEEK_CHANNEL", "").strip()
    if override:
        return override
    pf86 = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    pf = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    candidates = [
        ("msedge", [pf86 / "Microsoft/Edge/Application/msedge.exe",
                    pf / "Microsoft/Edge/Application/msedge.exe"]),
        ("chrome", [pf86 / "Google/Chrome/Application/chrome.exe",
                    pf / "Google/Chrome/Application/chrome.exe"]),
    ]
    for channel, paths in candidates:
        if any(p.exists() for p in paths):
            return channel
    return "chrome"


def _cookie_db():
    """专用 profile 的 Cookies 库路径（新旧 Chromium 布局都覆盖）。"""
    for rel in (("Default", "Network", "Cookies"), ("Default", "Cookies")):
        path = PROFILE_DIR.joinpath(*rel)
        if path.exists():
            return path
    return None


def has_profile():
    """专用 profile 是否已初始化过（跑过 --login 或改写）。

    注意：**这不代表已登录**。判定登录态曾试过读 cookie，但不可靠 ——
    匿名访问 /sign_in 就会落下 HWWAFSESID / HWWAFSESTIME / ds_session_id /
    smidV2 / .thumbcache_* 等 cookie，名字还会随访问次数变多，靠名单区分
    等于长期猜谜。真正可靠的信号是打开页面后**是否被重定向到 /sign_in**，
    该判定在 _rewrite_async 里做。
    """
    return _cookie_db() is not None


def _build_prompt(original_text, template=None):
    """拼装 prompt：用户模板（角色/自定义指令 · 可空） + 通用要求 + 原文。"""
    parts = []
    if template:
        if template in REWRITE_TEMPLATES:
            parts.append("## 角色\n" + REWRITE_TEMPLATES[template])
        else:
            # 用户在前端自由输入的模板 — 原样作为「自定义指令」插入
            parts.append("## 自定义指令\n" + template)
    parts.append(SYSTEM_PROMPT)
    parts.append("需要改写的原文：" + original_text)
    return "\n\n".join(parts)


def _normalize(text):
    """对齐 openteam normalizeTextareaValue：统一换行后比较。"""
    return (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def _clean_reply(text):
    """把网页回复整理成可直接配音的一行文案。

    - DeepSeek 用 <p> 分块，extractCleanText 会留 \\n；TTS 原样接收会读出停顿，
      故合并为一行（tts_client.tts() 不做任何文本清洗，必须在这里处理）。
    - 去掉模型常见的整段引号包裹与「文案：」这类前缀。
    """
    import re
    s = _normalize(text)
    if not s:
        return ""
    # 去掉 markdown 粗体/斜体标记（TTS 会把星号读出来）
    s = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", s)
    # 常见前缀：改写后的文案： / 文案： / 输出：
    s = re.sub(r"^(改写[后的]*文案|文案|输出|旁白)\s*[:：]\s*", "", s)
    # 多行合并为一行（中文之间不补空格，避免朗读断句异常）
    s = " ".join(line.strip() for line in s.split("\n") if line.strip())
    s = re.sub(r"(?<=[一-鿿，。！？、；：]) +(?=[一-鿿])", "", s)
    # 整体被引号包裹时剥掉
    if len(s) > 1 and s[0] in "\"'“‘「『" and s[-1] in "\"'”’」』":
        s = s[1:-1].strip()
    return s


class RewriteError(Exception):
    """改写流程中可直接展示给用户的失败原因。"""


async def _fill_and_send(page, prompt, input_timeout=15.0):
    """对齐 openteam fillAndSend：等 textarea → 注入 → 校验 → 等发送按钮 → 点击。"""
    # 1) 等 textarea 出现（openteam waitForElement）
    try:
        await page.wait_for_selector(SEL_EDITOR, timeout=int(input_timeout * 1000), state="attached")
    except Exception:
        diag = await page.evaluate(_js("""
            return {
              url: location.href,
              readyState: document.readyState,
              title: document.title,
              textareaCount: document.querySelectorAll('textarea').length,
            };
        """), None)
        if "chat.deepseek.com" not in (diag.get("url") or ""):
            raise RewriteError(f"DeepSeek 页面未打开（当前 {diag.get('url') or 'about:blank'}），请检查网络")
        raise RewriteError("未找到 DeepSeek 输入框 — 可能未登录，请在浏览器里登录 chat.deepseek.com 后重试")

    # 2) 注入文本（openteam setTextareaText：value + InputEvent，不是 innerHTML）
    await page.evaluate(_js("""
        const editor = document.querySelector(SEL.editor);
        if (!editor) throw new Error('未找到 DeepSeek 输入框');
        editor.focus();
        editor.value = arg;
        editor.dispatchEvent(new InputEvent('input', {
          bubbles: true, inputType: 'insertText', data: arg
        }));
        editor.dispatchEvent(new Event('change', { bubbles: true }));
    """), prompt)

    # 3) 校验文本已被接受（openteam 的一致性断言）
    actual = await page.evaluate(_js("""
        const editor = document.querySelector(SEL.editor);
        return editor ? editor.value : '';
    """), None)
    if _normalize(actual) != _normalize(prompt):
        raise RewriteError("文案未成功写入 DeepSeek 输入框（页面结构可能已改版）")

    # 4) 轮询等待可点击的发送按钮（openteam waitForDeepSeekSendButton）
    deadline = time.monotonic() + input_timeout
    while True:
        clicked = await page.evaluate(_js("""
            const btn = findSendButton();
            if (!btn) return false;
            btn.click();
            return true;
        """), None)
        if clicked:
            return
        if time.monotonic() > deadline:
            raise RewriteError("DeepSeek 发送按钮暂不可用，请稍后重试")
        await asyncio.sleep(0.05)


async def _wait_reply(page, baseline_count, timeout=60.0):
    """等待并抓取回复。

    对齐 openteam replyObserver 的判定：只有当「最后一条回复文本连续稳定
    ≥ FINAL_SETTLE_SEC」且「页面已无停止按钮（非生成中）」时才认定完成；
    很短的回复额外多等 SHORT_REPLY_SETTLE_SEC，避免抓到流式输出的开头几个字。

    baseline_count: 发送前的回复容器数量，用于确认新回复已出现（openteam 的
                    countResponseContainers 位置基线思路，抗 DOM 重建）。
    """
    deadline = time.monotonic() + timeout
    stable_text = None
    stable_since = 0.0
    saw_generating = False

    while True:
        state = await page.evaluate(_js("""
            const containers = responseContainers();
            const last = containers[containers.length - 1];
            return {
              count: containers.length,
              text: last ? extractCleanText(last) : '',
              generating: Boolean(findStopButton()),
            };
        """), None)

        generating = bool(state.get("generating"))
        saw_generating = saw_generating or generating
        text = (state.get("text") or "").strip()
        has_new = state.get("count", 0) > baseline_count
        now = time.monotonic()

        if has_new and text:
            if text != stable_text:
                stable_text = text
                stable_since = now
            else:
                settled = now - stable_since
                need = SHORT_REPLY_SETTLE_SEC if len(text) < SHORT_REPLY_CHARS else FINAL_SETTLE_SEC
                if not generating and settled >= need:
                    return text
        else:
            stable_text = None

        if now > deadline:
            # 仍在生成 → 手上的文本是被截断的半句，不能拿去配音，宁可失败降级用原文
            if generating:
                raise RewriteError(f"DeepSeek 回复超时（{timeout:.0f}s，仍在生成中）")
            # 已停止生成、只是稳定时长还没攒够 → 内容是完整的，回收利用
            if stable_text:
                logger.warning("DeepSeek 回复超时但已停止生成，返回已抓取文本")
                return stable_text
            if not saw_generating:
                raise RewriteError("DeepSeek 未开始回复 — 可能未登录或请求被拒绝")
            raise RewriteError(f"DeepSeek 回复超时（{timeout:.0f}s）")

        await asyncio.sleep(POLL_INTERVAL)


def _dehead_ua(ua):
    """把 headless UA 改写成对应的有头 UA。

    headless 下 navigator.userAgent 自报 `HeadlessChrome/<ver>`，而 DeepSeek
    前面挂着华为 WAF（见 _ANON_COOKIES 的 HWWAFSESID），这是标准爬虫特征。
    实测两种 UA 只差 'Headless' 前缀，去掉即可，版本号取浏览器真实值、不硬编码。
    """
    return (ua or "").replace("HeadlessChrome/", "Chrome/")


async def _launch(p, channel, headless=True):
    """用专用 profile 启动浏览器；把常见启动失败翻译成可读原因。"""
    try:
        kwargs = {}
        if headless:
            # 先问出浏览器真实 UA，再去掉 Headless 标记传回去（版本号不硬编码）
            probe = await p.chromium.launch(channel=channel, headless=True)
            try:
                raw = await probe.new_page()
                kwargs["user_agent"] = _dehead_ua(
                    await raw.evaluate("() => navigator.userAgent"))
            finally:
                await probe.close()
        return await p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel=channel,
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
            **kwargs,
        )
    except Exception as e:
        msg = str(e)
        low = msg.lower()
        if "executable doesn't exist" in low or "looks like playwright" in low:
            raise RewriteError(
                f"未找到 {channel} 浏览器，请安装 Edge/Chrome，"
                f"或设 VU_DEEPSEEK_CHANNEL 指定通道"
            )
        # 专用 profile 理论上不会被占用；若真撞锁，说明上一次改写的浏览器没退干净
        if any(k in low for k in ("target page, context or browser has been closed",
                                  "another instance", "profile", "lock")):
            raise RewriteError(
                "改写用浏览器配置目录被占用 — 可能上一次改写未正常退出，"
                "请结束残留的 msedge/chrome 进程后重试"
            )
        raise RewriteError(f"浏览器启动失败: {msg[:150]}")


async def _rewrite_async(original_text, template=None, timeout=60, headless=True):
    """异步版：Playwright 操控 DeepSeek 网页，返回改写后文案。

    失败一律抛 RewriteError（携带用户可读原因），由 rewrite() 统一捕获。
    """
    from playwright.async_api import async_playwright

    prompt = _build_prompt(original_text, template=template)
    channel = _pick_channel()
    if not has_profile():
        # 连 profile 都没建过 → 必然没登录，省一次浏览器启动
        raise RewriteError(
            "DeepSeek 未登录：请先运行一次 "
            "`python station/server/copy_rewriter.py --login` 扫码登录"
        )
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        try:
            context = await _launch(p, channel, headless=headless)
        except RewriteError:
            raise
        except Exception as e:
            raise RewriteError(f"浏览器启动失败: {str(e)[:150]}")

        try:
            page = context.pages[0] if context.pages else await context.new_page()

            # 打开 DeepSeek（goto 失败必须显式报错，不能吞掉 → 否则前端只看到 about:blank）
            try:
                await page.goto("https://chat.deepseek.com/",
                                wait_until="domcontentloaded", timeout=30000)
            except Exception as ge:
                raise RewriteError(f"无法打开 DeepSeek 网页（网络或超时）: {str(ge)[:120]}")

            # 被重定向到登录页 = 未登录/登录态已过期。
            # 这是判定登录态的**唯一可靠信号**（cookie 名单法已证伪，见 has_profile）
            if "/sign_in" in page.url or "/login" in page.url:
                raise RewriteError(
                    "DeepSeek 未登录或登录态已过期：请运行 "
                    "`python station/server/copy_rewriter.py --login`"
                )

            # 记录发送前的回复容器数量作为位置基线
            baseline = await page.evaluate(_js("return responseContainers().length;"), None)

            await _fill_and_send(page, prompt)
            reply = await _wait_reply(page, baseline, timeout=float(timeout))
            return _clean_reply(reply) or None
        finally:
            try:
                await context.close()
            except Exception:
                pass


def rewrite(original_text, template=None, timeout=60, headless=True):
    """同步封装：调用 DeepSeek 改写文案。

    Args:
        original_text: ASR 识别或字幕提取的原始文本
        template: 改写模板（"带货"/"解说"/"Vlog" 或自定义角色描述），None=无模板
        timeout: 回复最长等待秒数
        headless: 默认 True（后台静默改写，不弹窗打扰用户）

    Returns:
        str | None: 改写后的文案，失败返回 None（调用 get_last_error() 获取失败原因）
    """
    global _last_error
    _last_error = ""

    if not original_text or not original_text.strip():
        _last_error = "原文为空，无需改写"
        return None

    try:
        return asyncio.run(_rewrite_async(original_text, template=template,
                                          timeout=timeout, headless=headless))
    except RewriteError as e:
        _last_error = str(e)
        logger.error(f"DeepSeek 改写失败: {e}")
        return None
    except Exception as e:
        msg = str(e)
        if "Target page, context or browser has been closed" in msg:
            _last_error = "浏览器被关闭，改写中断"
        elif any(k in msg.lower() for k in ("profile", "already", "another instance")):
            _last_error = "请先完全关闭 Edge/Chrome 再试（浏览器占用用户配置目录）"
        else:
            _last_error = msg[:200]
        logger.error(f"rewrite 异常: {e}")
        return None


def get_last_error():
    """返回最后一次 rewrite() 失败的原因；成功返回空字符串。"""
    return _last_error or ""


# ---------------------------------------------------------------------------
# 首次登录：开一个有头浏览器，人工扫码/输入账号，登录态留在 PROFILE_DIR
# ---------------------------------------------------------------------------
async def _login_async():
    from playwright.async_api import async_playwright
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    channel = _pick_channel()
    print(f"[login] 浏览器通道 : {channel}")
    print(f"[login] 登录态目录 : {PROFILE_DIR}")
    async with async_playwright() as p:
        context = await _launch(p, channel, headless=False)
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto("https://chat.deepseek.com/",
                            wait_until="domcontentloaded", timeout=60000)
            print("\n请在弹出的浏览器窗口里完成 DeepSeek 登录（扫码或账号密码）。")
            print("登录成功、看到聊天输入框后，本程序会自动检测并退出。\n")
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                try:
                    ok = await page.evaluate(
                        "sel => Boolean(document.querySelector(sel))", SEL_EDITOR)
                except Exception:
                    ok = False   # 页面正在跳转，下一轮再看
                if ok:
                    await asyncio.sleep(2)   # 等 Cookie 落盘
                    print("[login] ✅ 登录成功，登录态已保存。以后改写无需再登录。")
                    return True
                await asyncio.sleep(1.5)
            print("[login] ❌ 5 分钟内未检测到登录完成，请重试。")
            return False
        finally:
            await context.close()


def login():
    """交互式登录（首次使用时跑一次）。"""
    return asyncio.run(_login_async())


if __name__ == "__main__":
    import io
    import sys
    # Windows 控制台默认 GBK，会在 ✅/❌ 等字符上抛 UnicodeEncodeError（实测踩过）。
    # 与 hooks/pre_tool_guard.py 一致：强制包一层 UTF-8 TextIOWrapper。
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

    if "--login" in sys.argv:
        raise SystemExit(0 if login() else 1)
    if "--check" in sys.argv:
        print(f"playwright  : {is_available()}")
        print(f"浏览器通道  : {_pick_channel()}")
        print(f"登录态目录  : {PROFILE_DIR}")
        print(f"profile 已建: {has_profile()}")
        print("（是否真已登录需实际打开页面才知道，用 --probe 检测）")
        raise SystemExit(0)
    if "--probe" in sys.argv:
        # 真实探测：开一次无头浏览器，看是否被重定向到 /sign_in
        out = rewrite("测试文案：这段打斗很精彩", template="解说")
        if out:
            print(f"✅ 已登录，改写可用\n改写结果: {out}")
            raise SystemExit(0)
        print(f"❌ 不可用: {get_last_error()}")
        raise SystemExit(1)
    # 无参数 = 冒烟测试：改写一段样例文案
    text = "这段打斗太精彩了，三秒之内反转了三次"
    print(f"原文: {text}")
    out = rewrite(text, template="带货", headless=False)
    print(f"改写: {out}" if out else f"失败: {get_last_error()}")
