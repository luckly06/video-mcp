# -*- coding: utf-8 -*-
"""
pre_tool_guard.py — PreToolUse Hook（调用前拦截）。

对齐腾讯云文章 §4：MCP 调用链路中的同步检查点。Agent 构造完 tool call、
框架实际发起调用之前，把 tool_name + tool_input 交给本脚本裁决。

裁决流水线（简化版 9 步）：
  1. 加载外化规则 rules.json
  2. 四级安全分级 classify
  3. blocked  → deny（硬阻断，不可逆操作）
  4. 强制走链 check_chain（缺前置 → deny + 提示）
  5. 自动补全 apply_auto_fill（modifiedInput）
  6. tier 条件链字段校验 check_body（缺字段 → deny + 提示补全）
  7. warned   → ask（弹窗确认，人工决策）
  8. audit    → allow（放行，PostToolUse 记审计）
  9. pass     → allow（静默放行）

输入（stdin, JSON）: {"hook_event": "...", "tool_name": "...", "tool_input": {...}, "tier": "..."?}
输出（stdout, JSON）: {"continue": bool, "reason": "...",
                      "permissionDecision": "allow|ask|deny", "modifiedInput": {...}?}

prior_tools（强制走链所需的"本会话已成功调用工具集"）不从 stdin 拿——MCP Server
无状态，改为 common.recent_tools() 从 logs/audit.jsonl 读 status=ok 的记录还原。
"""

import sys
import io
import json

# —— GBK 编码 bug 修复 ——
# Windows 下 stdin/stdout 默认 GBK。server 用 UTF-8 编码 payload 送进 stdin，
# 含中文文件名（如 下班来接我.mp4）时若按 GBK 解码会产生代理字符，
# 后续写 UTF-8 审计日志时抛 UnicodeEncodeError → 落盘失败 → 强制走链读不到前置工具。
# 因此 stdin/stdout/stderr 三者都强制包一层 utf-8 的 TextIOWrapper。
sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

sys.path.insert(0, __file__.rsplit("\\", 1)[0] if "\\" in __file__ else ".")
from common import (
    load_rules, classify, check_chain, check_body, apply_auto_fill,
    is_path_allowed, recent_tools,
)
from pathlib import Path


# F3.3 路径白名单（hook 第一道闸）：
# tool_name -> [(field_name, "VIDEO_DIR" | "OUTPUT_DIR")]
# 注：hook 是 subprocess 运行，无法拿 pipeline.VIDEO_DIR 绝对路径（脆弱），
# 所以 hook 这一层只做**形态校验**（拒绝绝对路径前缀与 .. 穿越）；
# 真正的 resolve + relative_to 由 pipeline._resolve_safe 第二道闸保证。
_PATH_FIELDS = {
    "dedup_video":      [("src", "VIDEO_DIR")],
    "batch_fission":    [("src", "VIDEO_DIR")],
    "remove_watermark": [("src", "VIDEO_DIR")],
    "probe_video":      [("src", "VIDEO_DIR")],
    "delete_output":    [("name", "OUTPUT_DIR")],
}


def _check_path_shape(tool, body):
    """F3.3 hook 形态校验：返回 None = 通过；返回 str = deny 原因。

    拒绝：
      - Unix 绝对路径前缀（/）
      - UNC 路径前缀（\\）
      - Windows 盘符（C:）
      - 路径中任一段为 ..
    """
    rules = _PATH_FIELDS.get(tool)
    if not rules:
        return None
    for field, _base in rules:
        v = body.get(field)
        if not v or not isinstance(v, str):
            continue  # 缺字段交给 check_body 报错；非字符串由更上层兜底
        if v.startswith("/") or v.startswith("\\"):
            return f"[路径白名单] {tool}.{field}={v!r} 含绝对路径前缀（Unix/UNC），hook 拒绝"
        if len(v) >= 2 and v[1] == ":":
            return f"[路径白名单] {tool}.{field}={v!r} 含 Windows 盘符，hook 拒绝"
        if ".." in Path(v).parts:
            return f"[路径白名单] {tool}.{field}={v!r} 含 .. 穿越，hook 拒绝"
    return None


def guard(payload):
    tool = payload.get("tool_name", "")
    body = payload.get("tool_input", {}) or {}
    # prior_tools 来源：审计日志里 status=ok 的工具集（MCP 无状态，靠落盘还原）。
    # 若 stdin 显式带了 prior_tools（测试/特殊场景），取并集以兼容。
    prior = recent_tools() | set(payload.get("prior_tools", []) or [])

    rules = load_rules()
    level = classify(tool, rules)

    # 3. 硬阻断 → continue=false / deny
    if level == "blocked":
        guide = rules.get("ask_user_guides", {}).get(tool, "")
        return {
            "continue": False,
            "permissionDecision": "deny",
            "level": level,
            "reason": f"[第4级·硬阻断] 工具 `{tool}` 为不可逆操作，Agent 不可直接调用。{guide}",
        }

    # 4. 强制走链 → 缺前置则 continue=false / deny
    ok_chain, hint = check_chain(tool, prior, rules)
    if not ok_chain:
        return {
            "continue": False,
            "permissionDecision": "deny",
            "level": level,
            "reason": f"[强制走链] {hint}",
        }

    # 4.5 F3.3 路径形态校验（hook 第一道闸，越界 deny）
    path_err = _check_path_shape(tool, body)
    if path_err:
        return {
            "continue": False,
            "permissionDecision": "deny",
            "level": level,
            "reason": path_err,
        }

    # 5. 自动补全（modifiedInput）
    body, modified = apply_auto_fill(tool, body, rules)

    # 6. tier 条件链字段校验 → 缺字段则 continue=false / deny，并列出缺失字段
    ok_body, missing = check_body(tool, body, rules)
    if not ok_body:
        return {
            "continue": False,
            "permissionDecision": "deny",
            "level": level,
            "reason": (
                f"[字段校验失败] 工具 `{tool}` 缺少必填字段: {missing}。"
                f"请补全 {missing} 后重新调用。"
            ),
            "missing_fields": missing,
        }

    # 7. 写操作 → continue=true / ask（弹窗人工确认）
    if level == "warned":
        guide = rules.get("ask_user_guides", {}).get(tool, "请确认是否继续。")
        out = {
            "continue": True,
            "permissionDecision": "ask",
            "level": level,
            "reason": f"[第3级·弹窗确认] {guide}",
        }
        if modified:
            out["modifiedInput"] = body
        return out

    # 8/9. audit / pass → continue=true / allow（放行）
    out = {
        "continue": True,
        "permissionDecision": "allow",
        "level": level,
        "reason": f"[{'第2级·审计' if level == 'audit' else '第1级·放行'}] 工具 `{tool}` 放行。",
    }
    if modified:
        out["modifiedInput"] = body
    return out


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    result = guard(payload)
    sys.stdout.write(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
