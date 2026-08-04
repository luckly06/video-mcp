# -*- coding: utf-8 -*-
"""
common.py — Hooks 共享工具：加载外化规则、tier 条件链求值器。

对齐腾讯云文章 §4.4：condition-chain 求值器（_matches_tier_condition + _check_tier_fields），
规则全部来自 shared/rules.json，代码不硬编码任何具体工具规则。
"""

import os
import json
from pathlib import Path

_HOOKS_DIR = Path(__file__).resolve().parent
STATION_DIR = _HOOKS_DIR.parent
RULES_PATH = STATION_DIR / "shared" / "rules.json"
AUDIT_PATH = STATION_DIR / "logs" / "audit.jsonl"


def load_rules():
    """加载外化规则（§6：配置与代码解耦）。"""
    with open(RULES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def classify(tool_name, rules):
    """四级安全分级：返回 blocked / warned / audit / pass。"""
    if tool_name in rules.get("blocked", {}).get("tools", []):
        return "blocked"
    if tool_name in rules.get("warned", {}).get("tools", []):
        return "warned"
    if tool_name in rules.get("audit", {}).get("tools", []):
        return "audit"
    return "pass"


# ---------------------------------------------------------------------------
# tier 条件链求值（对齐 §4.4.1）
# ---------------------------------------------------------------------------
def _matches_tier_condition(cond, body):
    """判断某 tier 的触发条件是否满足。支持 eq / contains / not_empty。"""
    if not cond:
        return True  # 无条件 → 无条件必检
    field = cond.get("field")
    op = cond.get("op")
    val = cond.get("value")
    actual = body.get(field)
    if op == "eq":
        # 布尔/字符串类型兼容
        return str(actual).lower() == str(val).lower()
    if op == "contains":
        return isinstance(actual, list) and val in actual
    if op == "not_empty":
        return actual not in (None, "", [], {})
    return False


def _check_tier_fields(tier, body):
    """校验一个 tier 的字段，返回缺失字段列表。"""
    missing = []
    for f in tier.get("required", []):
        v = body.get(f)
        if v in (None, "", [], {}):
            missing.append(f)
    for f in tier.get("nullable", []):
        # nullable: 必须存在（键在），值可空
        if f not in body:
            missing.append(f)
    return missing


def check_body(tool_name, body, rules):
    """
    执行 tier 条件链字段校验。
    返回 (ok, missing_fields)。ok=False 时应 deny 并提示补全。
    """
    spec = rules.get("body_check", {}).get(tool_name)
    if not spec:
        return True, []
    missing = []
    for tier in spec.get("tiers", []):
        if _matches_tier_condition(tier.get("condition"), body):
            missing.extend(_check_tier_fields(tier, body))
    return (len(missing) == 0), missing


def check_chain(tool_name, prior_tools, rules):
    """
    强制走链校验（§3.3）：某工具调用前必须已调用过前置工具。
    prior_tools: 本会话已成功调用过的工具名集合。
    返回 (ok, hint)。
    """
    rule = rules.get("chain_rules", {}).get(tool_name)
    if not rule:
        return True, ""
    for req in rule.get("requires_prior", []):
        if req not in prior_tools:
            return False, rule.get("hint", f"必须先调用 {req}")
    return True, ""


def apply_auto_fill(tool_name, body, rules):
    """自动补全缺省字段（modifiedInput）。返回补全后的 body 与是否有改动。"""
    fills = rules.get("auto_fill", {}).get(tool_name, {})
    modified = False
    out = dict(body)
    for k, v in fills.items():
        if k not in out and v is not None:
            out[k] = v
            modified = True
    return out, modified


def append_audit(record):
    """追加审计日志（§4 PostToolUse 审计记录）。"""
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(AUDIT_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_audit(limit=None):
    """读取审计日志，返回记录列表（最旧→最新）。文件不存在返回空列表。

    MCP Server 无状态，会话级"已调用工具集"/"最近记忆"都从这里还原。
    limit 为 None 时读全部；否则只返回最新 limit 条。
    """
    if not AUDIT_PATH.exists():
        return []
    records = []
    with open(AUDIT_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except Exception:
                # 单行损坏不影响整体（容错），跳过
                continue
    if limit is not None:
        records = records[-limit:]
    return records


def recent_tools():
    """从审计日志还原"本会话已成功调用过的工具集"（status=ok）。

    强制走链 check_chain 的 prior_tools 来源：MCP Server 无状态，
    只能靠 PostToolUse 落盘的审计记录来判断某前置工具是否已跑过。
    audit.jsonl 不存在 → 空集。
    """
    tools = set()
    for rec in read_audit():
        if rec.get("status") == "ok" and rec.get("tool_name"):
            tools.add(rec["tool_name"])
    return tools
