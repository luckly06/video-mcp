# -*- coding: utf-8 -*-
"""
test_rules.py — 模块三 F3.1（rules.json 维度分级与校验）单测

覆盖 DD §3.2 / §3.6：
  - auto_fill    dedup_video 补 level=medium；batch_fission 补 count=5（PRD D-04）
  - body_check   flip=true 缺 flip_mode → deny（tier2 条件链）
  - classify     四级安全分级不被本期改动破坏
  - 兜底一致性   pipeline.build_filter 与 rules tier2 同语义（不静默默认 'h'）

纯配置 + 纯函数，不调 ffmpeg。
"""

import json
import sys
from pathlib import Path

import pytest

_STATION = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_STATION / "hooks"))
sys.path.insert(0, str(_STATION / "server"))

import common as C  # noqa: E402
import pipeline as P  # noqa: E402


@pytest.fixture(scope="module")
def rules():
    return C.load_rules()


# ---------------------------------------------------------------------------
# rules.json 本身可解析（防手改 JSON 破坏配置）
# ---------------------------------------------------------------------------
def test_rules_json_parses():
    with open(C.RULES_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["version"]


# ---------------------------------------------------------------------------
# auto_fill：强度档与裂变数量的缺省
# ---------------------------------------------------------------------------
def test_auto_fill_dedup_video_fills_level(rules):
    """dedup_video 未指定 level 时补 medium（DD §2.3 缺省档）。"""
    out, modified = C.apply_auto_fill("dedup_video", {"src": "微笑.mp4"}, rules)
    assert modified is True
    assert out["level"] == "medium"
    assert out["src"] == "微笑.mp4"          # 原字段不被覆盖


def test_auto_fill_does_not_override_explicit_level(rules):
    """显式传的 level 优先，auto_fill 不得覆盖。"""
    out, _ = C.apply_auto_fill("dedup_video", {"src": "a.mp4", "level": "heavy"}, rules)
    assert out["level"] == "heavy"


def test_auto_fill_skips_null_valued_keys(rules):
    """out_name 声明为 null → 不补（apply_auto_fill 跳过 None）。"""
    out, _ = C.apply_auto_fill("dedup_video", {"src": "a.mp4"}, rules)
    assert "out_name" not in out


def test_auto_fill_batch_fission_count_is_five(rules):
    """PRD D-04：裂变默认 5 个（原配置 3，本期对齐）。"""
    out, modified = C.apply_auto_fill("batch_fission", {"src": "a.mp4"}, rules)
    assert modified is True
    assert out["count"] == 5


def test_fission_count_upper_bound_is_twenty():
    """上限 20 由 pipeline 钳制（PRD D-04），与 rules 默认值配套。"""
    assert P.batch_fission.__doc__ and "20" in P.batch_fission.__doc__


# ---------------------------------------------------------------------------
# body_check tier2：flip 开则必须带 flip_mode
# ---------------------------------------------------------------------------
def test_check_body_flip_true_without_mode_denies(rules):
    ok, missing = C.check_body("dedup_video", {"src": "a.mp4", "flip": True}, rules)
    assert ok is False
    assert missing == ["flip_mode"]


@pytest.mark.parametrize("mode", ["h", "v", "90"])
def test_check_body_flip_true_with_mode_passes(rules, mode):
    ok, missing = C.check_body(
        "dedup_video", {"src": "a.mp4", "flip": True, "flip_mode": mode}, rules)
    assert ok is True and missing == []


def test_check_body_flip_false_does_not_require_mode(rules):
    """flip 关（默认）时不该逼用户填方向。"""
    ok, missing = C.check_body("dedup_video", {"src": "a.mp4", "flip": False}, rules)
    assert ok is True and missing == []


def test_check_body_flip_absent_does_not_require_mode(rules):
    ok, missing = C.check_body("dedup_video", {"src": "a.mp4"}, rules)
    assert ok is True and missing == []


def test_check_body_tier0_still_requires_src(rules):
    """新增 tier 不得削弱 tier0。"""
    ok, missing = C.check_body("dedup_video", {}, rules)
    assert ok is False and "src" in missing


def test_check_body_tier1_bitrate_still_works(rules):
    """既有 tier1（fixed 码率需 bitrate_kbps）回归。"""
    ok, missing = C.check_body(
        "dedup_video", {"src": "a.mp4", "bitrate_mode": "fixed"}, rules)
    assert ok is False and "bitrate_kbps" in missing


def test_tier2_condition_reads_flat_field(rules):
    """契约锚点：tier2 condition 必须读【顶层】flip。

    common._matches_tier_condition 只 body.get(field)，不支持 dimensions.flip 嵌套，
    且求值器按约束不改 → server 透传时需把 dimensions.flip 镜像到顶层（F3.2）。
    嵌套写法在此链路上恒不触发，故锁死字段名防回归。
    """
    tiers = rules["body_check"]["dedup_video"]["tiers"]
    tier2 = next(t for t in tiers if t.get("tier") == 2)
    assert tier2["condition"]["field"] == "flip"
    assert tier2["required"] == ["flip_mode"]
    # 嵌套路径确实不被求值器支持（说明镜像是必要的，不是多余设计）
    assert C._matches_tier_condition(
        {"field": "flip", "op": "eq", "value": True},
        {"dimensions": {"flip": True}}) is False


# ---------------------------------------------------------------------------
# 兜底一致性：pipeline 与 rules 同语义
# ---------------------------------------------------------------------------
def test_pipeline_rejects_flip_without_mode():
    """DD §3.6 兜底：直调 pipeline 绕过 hooks 时也必须拒，不得静默默认 'h'。"""
    src_info = {"width": 720, "height": 1280}
    with pytest.raises(P.PipelineError) as ei:
        P.build_filter({"flip": True}, src_info)
    assert "flip_mode" in str(ei.value)


def test_pipeline_rejects_illegal_flip_mode():
    src_info = {"width": 720, "height": 1280}
    with pytest.raises(P.PipelineError):
        P.build_filter({"flip": True, "flip_mode": "diagonal"}, src_info)


# ---------------------------------------------------------------------------
# 分级与文案：本期改动不得破坏既有安全语义
# ---------------------------------------------------------------------------
def test_classify_levels_intact(rules):
    assert C.classify("delete_output", rules) == "blocked"
    assert C.classify("dedup_video", rules) == "warned"
    assert C.classify("batch_fission", rules) == "warned"
    assert C.classify("probe_video", rules) == "audit"
    assert C.classify("check_env", rules) == "pass"


def test_ask_user_guides_mention_dimension_and_selfcheck(rules):
    """文案需说明「按所选维度/强度处理」+「含 pHash 自检」（DD §3.2）。"""
    dedup = rules["ask_user_guides"]["dedup_video"]
    assert "强度" in dedup and "维度" in dedup and "pHash" in dedup
    fission = rules["ask_user_guides"]["batch_fission"]
    assert "pHash" in fission and "20" in fission
