# -*- coding: utf-8 -*-
"""
copy_rewriter.py — DeepSeek 文案改写（Playwright + openteam 技术）

- 复用用户 Chrome 已登录的 DeepSeek 网页会话
- 通过 Playwright page.evaluate() 注入 prompt + 派发 InputEvent
- 等待 AI 回复完成后抓取 DOM 文本
- 零 API Token 成本

环境要求：
- pip install playwright
- playwright install chromium
- DeepSeek 网页已登录（chat.deepseek.com）
- 用户 Chrome Profile 路径
"""

import os
import asyncio
import logging
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


def is_available():
    """检查 Playwright + Chrome 是否可用。"""
    try:
        from playwright.sync_api import sync_playwright
        return True
    except ImportError:
        return False


def _find_browser_profile():
    """查找用户 Edge/Chrome Profile 路径（Edge 优先，国产 Windows 用户更常用）。"""
    import platform
    home = Path.home()
    local = home / "AppData" / "Local"
    if platform.system() == "Windows":
        candidates = [
            (local / "Microsoft" / "Edge" / "User Data", "msedge"),
            (local / "Google" / "Chrome" / "User Data", "chrome"),
        ]
        for path, channel in candidates:
            if path.exists():
                return str(path), channel
    return "", "chrome"


async def _rewrite_async(original_text, timeout=60):
    """异步版：Playwright 操控 DeepSeek 网页。"""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        profile_dir, channel = _find_browser_profile()
        if not profile_dir:
            logger.warning("未找到 Edge/Chrome Profile")
            return None

        context = await p.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            channel=channel,
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )

        page = await context.new_page()
        try:
            # 打开 DeepSeek 新对话
            await page.goto("https://chat.deepseek.com/", wait_until="domcontentloaded")
            await asyncio.sleep(2)  # 等页面完全加载

            # 构建 prompt
            prompt = f"{SYSTEM_PROMPT}\n\n需要改写的原文：{original_text}"

            # openteam 技术：注入文本到 contenteditable 输入框并派发 InputEvent
            await page.evaluate("""
                (text) => {
                    const editor = document.querySelector('[contenteditable="true"]');
                    if (!editor) throw new Error('未找到 DeepSeek 输入框');

                    editor.focus();
                    editor.innerHTML = '';

                    const lines = text.split('\\n');
                    lines.forEach(line => {
                        const p = document.createElement('p');
                        p.textContent = line;
                        editor.appendChild(p);
                    });

                    editor.dispatchEvent(new InputEvent('input', {
                        bubbles: true, inputType: 'insertText', data: text
                    }));
                    editor.dispatchEvent(new Event('change', { bubbles: true }));
                }
            """, prompt)

            # 点击发送按钮
            send_btn = await page.query_selector('[role="button"]:has(svg)')
            if not send_btn:
                # fallback: 找任意包含发送图标的按钮
                send_btn = await page.query_selector('button svg')
                if send_btn:
                    send_btn = await send_btn.evaluate_handle("el => el.closest('button')")
            if send_btn:
                await send_btn.click()

            # 等待回复完成（轮询 isGenerating）
            start = asyncio.get_event_loop().time()
            while True:
                generating = await page.evaluate("""
                    () => {
                        const stopBtn = document.querySelector(
                            'button[aria-label], .ds-icon-button, [class*="stop"]'
                        );
                        // 如果存在停止按钮或 loading 指示器，说明还在生成
                        const loading = document.querySelector('[class*="loading"], [class*="spinner"]');
                        return !!(stopBtn || loading);
                    }
                """)
                if not generating:
                    break
                if asyncio.get_event_loop().time() - start > timeout:
                    logger.warning("DeepSeek 回复超时")
                    return None
                await asyncio.sleep(1.5)

            # 等待一下确保最终回复渲染完成
            await asyncio.sleep(1)

            # 抓取最后一条 AI 回复
            reply_text = await page.evaluate("""
                () => {
                    const containers = document.querySelectorAll(
                        '[class*="ds-markdown"], [class*="markdown"], .ds-bot-message'
                    );
                    const last = containers[containers.length - 1];
                    return last ? last.innerText.trim() : '';
                }
            """)

            return reply_text if reply_text else None

        except Exception as e:
            logger.error(f"DeepSeek 改写失败: {e}")
            return None
        finally:
            await context.close()


def rewrite(original_text, timeout=60):
    """同步封装：调用 DeepSeek 改写文案。

    Args:
        original_text: ASR 识别或字幕提取的原始文本
        timeout: 最长等待秒数

    Returns:
        str | None: 改写后的文案，失败返回 None
    """
    try:
        return asyncio.run(_rewrite_async(original_text, timeout=timeout))
    except Exception as e:
        logger.error(f"rewrite 异常: {e}")
        return None
