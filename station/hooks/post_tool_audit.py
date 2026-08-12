# -*- coding: utf-8 -*-
"""
post_tool_audit.py — PostToolUse Hook（调用后审计）。

对齐腾讯云文章 §4：工具实际执行完毕后，把结果落盘为一条审计记录。
这条记录同时承担双重职责：
  1. 合规审计（谁、何时、调了什么、成没成、错在哪）。
  2. 状态还原：MCP Server 无状态，pre_tool_guard.recent_tools() 靠这里
     写下的 status=ok 记录来判断"某前置工具是否已跑过"（强制走链依赖）。

输入（stdin, JSON）:
  {"hook_event": "...", "tool_name": "...", "tool_input": {...},
   "status": "ok|error", "result_summary": "...", "error": "..."?}
输出（stdout, JSON）: {"continue": true}
"""

import sys
import io
import json
from datetime import datetime

# —— GBK 编码 bug 修复 ——
# Windows 下 stdout 默认 GBK，输出含 emoji 的 JSON 会抛 UnicodeEncodeError，
# 强制包一层 utf-8 的 TextIOWrapper。
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")
# stdin 同样强制 UTF-8：server 用 json.dumps(...).encode("utf-8") 送入，
# Windows 默认按 GBK 解 stdin 会把中文文件名（如 下班来接我.mp4）解成代理字符，
# 随后写 UTF-8 审计日志时 UnicodeEncodeError → 写入失败 → probe/dedup 永不落盘 → 强制走链永远解不开。
sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8")

sys.path.insert(0, __file__.rsplit("\\", 1)[0] if "\\" in __file__ else ".")
from common import append_audit


def audit(payload):
    """把一次工具调用整理成审计记录并落盘。"""
    record = {
        "ts": datetime.now().isoformat(),          # 时间戳
        "hook_event": payload.get("hook_event", "PostToolUse"),
        "tool_name": payload.get("tool_name", ""),
        "tool_input": payload.get("tool_input", {}) or {},
        "status": payload.get("status", "ok"),     # ok / error
        "result_summary": payload.get("result_summary", ""),
    }
    # error 字段仅在有值时记录，保持记录整洁
    err = payload.get("error")
    if err:
        record["error"] = err
    append_audit(record)
    return record


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    audit(payload)
    # PostToolUse 只做记录，不干预流程，恒放行
    sys.stdout.write(json.dumps({"continue": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
