# -*- coding: utf-8 -*-
"""
session_start.py — SessionStart Hook（会话开始注入上下文）。

对齐腾讯云文章 §4/§5：会话启动时把"记忆 + 权限边界"注入给 Agent，
让无状态的 MCP Server 也具备跨会话连续性与自知之明。

注入两部分：
  1. 记忆：从 logs/audit.jsonl 读最近 5 条操作，作为"上次干了什么"。
  2. 权限边界：从 shared/rules.json 读四级安全分级，告诉 Agent
     哪些工具会被硬阻断/弹窗/审计/放行，避免它去撞墙。

输入（stdin, JSON 或空）: 可忽略。
输出（stdout, JSON）: {"continue": true, "context": "<markdown>"}
"""

import sys
import io
import json

# —— GBK 编码 bug 修复 ——
# Windows 下 stdout 默认 GBK，输出含 emoji 的 JSON 会抛 UnicodeEncodeError，
# 强制包一层 utf-8 的 TextIOWrapper。
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

sys.path.insert(0, __file__.rsplit("\\", 1)[0] if "\\" in __file__ else ".")
from common import load_rules, read_audit


def _build_memory_md():
    """从审计日志读最近 5 条操作，拼成"记忆"段落。"""
    recent = read_audit(limit=5)
    if not recent:
        return "（无历史操作记录，这是一次全新会话。）"
    lines = []
    for rec in recent:
        ts = rec.get("ts", "?")
        tool = rec.get("tool_name", "?")
        status = rec.get("status", "?")
        summary = rec.get("result_summary", "")
        mark = "OK" if status == "ok" else "ERR"
        line = f"- [{mark}] {ts} · `{tool}`"
        if summary:
            line += f" — {summary}"
        lines.append(line)
    return "\n".join(lines)


def _build_boundary_md(rules):
    """从 rules.json 读四级安全分级，拼成"权限边界"段落。"""
    def tools_of(level):
        return rules.get(level, {}).get("tools", [])
    return (
        f"- 第4级·硬阻断（deny，Agent 不可直接调用）：{tools_of('blocked')}\n"
        f"- 第3级·弹窗确认（ask，需人工决策）：{tools_of('warned')}\n"
        f"- 第2级·静默审计（allow + 记日志）：{tools_of('audit')}\n"
        f"- 第1级·静默放行（allow）：{tools_of('pass')}"
    )


def build_context():
    """组装注入给 Agent 的 markdown 上下文。"""
    rules = load_rules()
    memory = _build_memory_md()
    boundary = _build_boundary_md(rules)
    return (
        "# 视频去重数字员工 · 会话上下文\n\n"
        "## 最近操作记忆（最多 5 条）\n"
        f"{memory}\n\n"
        "## 工具权限边界（四级安全分级）\n"
        f"{boundary}\n\n"
        "> 说明：调用写工具（如 dedup_video）前必须先 probe_video 探测源视频（强制走链）；"
        "字段不全会被 PreToolUse 拦截并提示补全。"
    )


def main():
    # stdin 可空，读失败不影响
    try:
        json.load(sys.stdin)
    except Exception:
        pass
    out = {"continue": True, "context": build_context()}
    sys.stdout.write(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
