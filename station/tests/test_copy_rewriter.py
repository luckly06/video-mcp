# -*- coding: utf-8 -*-
"""copy_rewriter 纯函数单测（不起浏览器）。

覆盖 2026-08-10 那轮实测踩到的坑，防回归：
- _dehead_ua：headless UA 必须去掉 HeadlessChrome 标记（DeepSeek 前置华为 WAF）
- _clean_reply：网页回复换行/markdown/前缀/包裹引号必须清干净（tts_client 不做清洗）
- _build_prompt：预置模板走「## 角色」，自由输入走「## 自定义指令」
- rewrite()：空输入与未初始化 profile 时必须快速失败并给出可读原因，且不启动浏览器
- PROFILE_DIR：不得指向用户日常浏览器的 User Data（单例锁 → about:blank）

浏览器侧的 DOM 手法（openteam selector / 停止按钮判定 / 流式稳定）依赖真实
Chromium，属集成范畴，不在本文件覆盖。
"""

import sys
from pathlib import Path

import pytest

_STATION = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_STATION / "server"))

import copy_rewriter as R  # noqa: E402


# ---------------------------------------------------------------------------
# _dehead_ua — 反爬虫特征
# ---------------------------------------------------------------------------
def test_dehead_ua_strips_headless_marker():
    ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) HeadlessChrome/151.0.0.0 Safari/537.36 Edg/151.0.0.0")
    out = R._dehead_ua(ua)
    assert "Headless" not in out
    assert "Chrome/151.0.0.0" in out


def test_dehead_ua_keeps_version_from_browser_not_hardcoded():
    """版本号必须来自传入的真实 UA，不能被写死。"""
    assert "Chrome/999.1.2" in R._dehead_ua("x HeadlessChrome/999.1.2 y")


@pytest.mark.parametrize("ua", ["", None, "Mozilla/5.0 Chrome/151.0.0.0"])
def test_dehead_ua_tolerates_empty_or_already_clean(ua):
    out = R._dehead_ua(ua)
    assert isinstance(out, str)
    assert "Headless" not in out


# ---------------------------------------------------------------------------
# _clean_reply — 交给 TTS 前的文案规整
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw,want", [
    # DeepSeek 用 <p> 分块 → 抓回带 \n；朗读会读出停顿
    ("你见过这么炸裂的打斗吗？\n三秒之内反转三次，\n点赞关注不迷路！",
     "你见过这么炸裂的打斗吗？三秒之内反转三次，点赞关注不迷路！"),
    # 常见前缀
    ("改写后的文案：这操作你学不来！", "这操作你学不来！"),
    ("文案：三秒反转三次", "三秒反转三次"),
    ("旁白：开场就炸", "开场就炸"),
    # markdown 星号会被 TTS 读出来
    ("**炸裂**的打斗，你学不来", "炸裂的打斗，你学不来"),
    # 整段被引号包裹
    ("“你见过这么炸裂的打斗吗？”", "你见过这么炸裂的打斗吗？"),
    # 英文词间空格必须保留（不能被中文合并规则吃掉）
    ("英文 mixed word 保留空格", "英文 mixed word 保留空格"),
    # 空输入
    ("", ""),
    (None, ""),
])
def test_clean_reply(raw, want):
    assert R._clean_reply(raw) == want


def test_clean_reply_output_has_no_newline():
    """核心不变量：交给 tts_client 的文本不含换行（否则会被朗读成停顿）。"""
    out = R._clean_reply("第一句\n第二句\n\n第三句")
    assert "\n" not in out
    assert out == "第一句第二句第三句"    # 纯中文合并不留空格


# ---------------------------------------------------------------------------
# _build_prompt — 模板语义
# ---------------------------------------------------------------------------
def test_build_prompt_preset_template_uses_role_section():
    p = R._build_prompt("原文内容", template="带货")
    assert "## 角色" in p
    assert R.REWRITE_TEMPLATES["带货"] in p
    assert "原文内容" in p
    assert R.SYSTEM_PROMPT in p


def test_build_prompt_freeform_template_uses_custom_section():
    p = R._build_prompt("原文内容", template="像鲁迅那样写")
    assert "## 自定义指令" in p
    assert "像鲁迅那样写" in p
    assert "## 角色" not in p


def test_build_prompt_without_template_has_neither_section():
    p = R._build_prompt("原文内容")
    assert "## 角色" not in p
    assert "## 自定义指令" not in p
    assert "原文内容" in p


# ---------------------------------------------------------------------------
# rewrite() 守卫 — 必须快速失败，不启动浏览器
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("empty", ["", "   ", "\n\t"])
def test_rewrite_rejects_empty_text_without_launching_browser(monkeypatch, empty):
    def boom(*_a, **_kw):
        raise AssertionError("空输入不应启动浏览器")
    monkeypatch.setattr(R, "_rewrite_async", boom)

    assert R.rewrite(empty) is None
    assert R.get_last_error()          # 必须给出可读原因


def test_rewrite_reports_login_needed_when_profile_missing(monkeypatch, tmp_path):
    """profile 未初始化 → 直接提示 --login，不浪费一次浏览器启动。"""
    monkeypatch.setattr(R, "PROFILE_DIR", tmp_path / "never-created")
    assert R.has_profile() is False

    out = R.rewrite("一段原始对白")
    assert out is None
    assert "--login" in R.get_last_error()


def test_get_last_error_cleared_on_new_call(monkeypatch):
    R.rewrite("")                       # 先制造一次失败
    assert R.get_last_error()

    monkeypatch.setattr(R, "_rewrite_async",
                        lambda *_a, **_kw: _async_value("改写结果"))
    assert R.rewrite("原文") == "改写结果"
    assert R.get_last_error() == ""     # 成功后必须清空


async def _async_value(v):
    return v


# ---------------------------------------------------------------------------
# profile 隔离 — 2026-08-10 about:blank 的根因防线
# ---------------------------------------------------------------------------
def test_profile_dir_is_not_user_daily_browser_data():
    """绝不能指向用户日常 Edge/Chrome 的 User Data（Chromium 单例锁）。"""
    p = str(R.PROFILE_DIR).replace("\\", "/").lower()
    assert "microsoft/edge/user data" not in p
    assert "google/chrome/user data" not in p


def test_profile_dir_anchored_under_station_logs():
    """默认值须 __file__ 相对锚定在 station/logs 下（随工程移动，且被 .gitignore 覆盖）。"""
    import os
    if os.environ.get("VU_DEEPSEEK_PROFILE"):
        pytest.skip("环境变量已覆盖 PROFILE_DIR")
    p = R.PROFILE_DIR.resolve()
    assert p.parent == (_STATION / "logs").resolve()
    assert not p.is_absolute() or str(p).startswith(str(_STATION.resolve()))


def test_pick_channel_returns_known_channel():
    assert R._pick_channel() in ("msedge", "chrome")


def test_is_available_reflects_playwright_import():
    assert isinstance(R.is_available(), bool)
