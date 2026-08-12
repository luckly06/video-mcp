# -*- coding: utf-8 -*-
"""
test_pipeline_orchestration.py — 模块二（管线编排）单测

覆盖 DD Feature 2.1 / 2.2 / 2.3：
  - _resolve_safe   路径穿越防护（DD §0.4）
  - _resolve_level  强度档 + 维度开关展开（DD §2.3）
  - build_filter    crop 保分辨率 / flip 默认关（DD §2.5a）
  - _apply_speed    atempo 硬约束钳制（DD §2.5b）
  - _calc_trim      最短时长保护与「短素材跳过」语义（DD §0.3 / §2.5c）
  - _clamp_speed_for_floor  变速不得跌破 5s 硬下限（DD §0.3 / §2.5b）

本文件只测纯函数（不调 ffmpeg），秒级完成。真实素材端到端见
test_pipeline_e2e.py。
"""

import inspect
import random
import sys
import threading
import time
from pathlib import Path

import pytest

_STATION = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_STATION / "server"))

import pipeline as P  # noqa: E402


# ---------------------------------------------------------------------------
# Feature 2.1 — 路径穿越防护
# ---------------------------------------------------------------------------
def test_default_video_dir_is_project_input():
    project_dir = _STATION.parent
    assert P.VIDEO_DIR.resolve() == (project_dir / "input").resolve()


@pytest.mark.parametrize("bad", [
    "../../etc/passwd",
    "..\\..\\x.mp4",
    "C:\\Windows\\system32\\drivers\\etc\\hosts",
    "../output/x.mp4",
])
def test_resolve_safe_blocks_traversal(bad):
    """白名单外的路径一律拒绝，不论相对还是绝对。"""
    with pytest.raises(P.PipelineError):
        P._resolve_safe(bad, P.VIDEO_DIR)


def test_resolve_safe_allows_subdir_asset():
    """input/ 子目录内的素材路径仍属于白名单。"""
    p = P._resolve_safe("test", P.VIDEO_DIR, must_exist=False)
    assert p == (Path(P.VIDEO_DIR).resolve() / "test")


def test_resolve_safe_missing_file_raises():
    with pytest.raises(P.PipelineError):
        P._resolve_safe("不存在的素材.mp4", P.VIDEO_DIR, must_exist=True)


def test_reserve_output_path_uses_requested_name_then_increments(monkeypatch, tmp_path):
    monkeypatch.setattr(P, "OUTPUT_DIR", tmp_path)

    first = P._reserve_output_path("成片.mp4")
    second = P._reserve_output_path("成片.mp4")
    third = P._reserve_output_path("成片.mp4")

    assert first == tmp_path / "成片.mp4"
    assert second == tmp_path / "成片_2.mp4"
    assert third == tmp_path / "成片_3.mp4"
    assert first.exists() and second.exists() and third.exists()


def test_reserve_output_path_skips_existing_artifact(monkeypatch, tmp_path):
    monkeypatch.setattr(P, "OUTPUT_DIR", tmp_path)
    (tmp_path / "成片.mp4").write_bytes(b"existing")

    reserved = P._reserve_output_path("成片.mp4")

    assert reserved == tmp_path / "成片_2.mp4"
    assert (tmp_path / "成片.mp4").read_bytes() == b"existing"


def test_reserve_output_path_skips_locked_existing_file_on_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(P, "OUTPUT_DIR", tmp_path)
    locked = tmp_path / "成片.mp4"
    locked.write_bytes(b"locked")
    original_open = Path.open

    def windows_open(path, mode="r", *args, **kwargs):
        if path == locked and mode == "xb":
            raise PermissionError("file is locked")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", windows_open)

    reserved = P._reserve_output_path("成片.mp4")

    assert reserved == tmp_path / "成片_2.mp4"
    assert locked.read_bytes() == b"locked"


def test_reserve_output_path_still_blocks_traversal(monkeypatch, tmp_path):
    monkeypatch.setattr(P, "OUTPUT_DIR", tmp_path)
    with pytest.raises(P.PipelineError):
        P._reserve_output_path("../outside.mp4")


def test_ffmpeg_permission_error_is_actionable_and_compact():
    message = P._ffmpeg_error_message(
        "configuration: many flags\nF:/output/成片.mp4: Permission denied\n",
        Path("F:/output/成片.mp4"),
    )
    assert "成片.mp4" in message
    assert "播放器" in message and "资源管理器预览窗格" in message
    assert "configuration" not in message


def test_ffmpeg_other_error_keeps_only_tail_lines():
    stderr = "\n".join(f"line-{index}" for index in range(12))
    message = P._ffmpeg_error_message(stderr, Path("output.mp4"))
    assert "line-0" not in message and "line-3" not in message
    assert "line-4" in message and "line-11" in message


def test_run_cancel_token_terminates_running_process():
    token = P.CancellationToken()
    outcome = {}

    def worker():
        try:
            P._run([sys.executable, "-c", "import time; time.sleep(30)"],
                   timeout=60, cancel_token=token)
        except Exception as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=worker)
    thread.start()
    time.sleep(0.4)
    token.cancel()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert isinstance(outcome.get("error"), P.PipelineCancelled)


# ---------------------------------------------------------------------------
# Feature 2.1 — 强度档 / 维度开关
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("level,expect_crop", [
    ("light", 0.02), ("medium", 0.05), ("heavy", 0.08),
])
def test_resolve_level_crop_ratio_per_band(level, expect_crop):
    resolved, _ = P._resolve_level(level, None, None, seed=42)
    assert resolved["crop_ratio"] == expect_crop
    assert resolved["_level"] == level


def test_resolve_level_flip_off_by_default():
    """flip 是高破坏维度，缺省必须关（DD §2.3）。"""
    resolved, _ = P._resolve_level("medium", None, None, seed=42)
    assert "flip" not in resolved
    assert P.DIMENSION_DEFAULTS["flip"] is False


def test_resolve_level_flip_on_with_mode():
    resolved, _ = P._resolve_level("heavy", {"flip": True}, {"flip_mode": "v"}, seed=42)
    assert resolved["flip"] is True
    assert resolved["flip_mode"] == "v"


def test_resolve_level_illegal_falls_back_to_medium():
    resolved, _ = P._resolve_level("bogus", None, None, seed=1)
    assert resolved["_level"] == "medium"
    assert "bogus" in resolved["_level_note"]


def test_resolve_level_backfills_seed():
    """seed 缺省必须回填，便于复现与裂变互异（DD §2.5d）。"""
    resolved, seed = P._resolve_level("medium", None, None, seed=None)
    assert isinstance(seed, int) and seed > 0
    assert resolved["_seed"] == seed


def test_resolve_level_params_override_band():
    """params 逐维覆盖优先级高于 level（DD §2.3）。"""
    resolved, _ = P._resolve_level("light", None, {"crop_ratio": 0.5}, seed=1)
    assert resolved["crop_ratio"] == 0.5


# ---------------------------------------------------------------------------
# Feature 2.2 — build_filter：crop 保分辨率 / flip
# ---------------------------------------------------------------------------
SRC_INFO = {"width": 1070, "height": 1914}


def test_build_filter_crop_keeps_resolution():
    """crop 后必须 scale 回原分辨率，否则 resolution_kept 自检会挂。"""
    resolved, _ = P._resolve_level("medium", {"crop": True, "flip": False}, None, seed=3)
    vf, applied = P.build_filter(resolved, SRC_INFO)
    assert "crop=" in vf
    assert "scale=1070:1914" in vf
    assert applied["crop_ratio"] == 0.05


def test_build_filter_crop_dimensions_are_even():
    """libx264 要求宽高为偶数，裁切尺寸必须取偶。"""
    resolved, _ = P._resolve_level("heavy", {"crop": True}, None, seed=3)
    vf, _ = P.build_filter(resolved, {"width": 1071, "height": 1913})
    crop_nodes = [n for n in vf.split(",") if n.startswith("crop=")]
    # 最后一个 crop 是构图裁切（前一个是旋转补边裁切）
    cw, ch = crop_nodes[-1].replace("crop=", "").split(":")[:2]
    assert int(cw) % 2 == 0 and int(ch) % 2 == 0


def test_build_filter_flip_absent_by_default():
    resolved, _ = P._resolve_level("medium", {"crop": True, "flip": False}, None, seed=3)
    vf, _ = P.build_filter(resolved, SRC_INFO)
    assert "hflip" not in vf and "vflip" not in vf and "transpose" not in vf


@pytest.mark.parametrize("mode,node", [("h", "hflip"), ("v", "vflip"), ("90", "transpose=1")])
def test_build_filter_flip_modes(mode, node):
    resolved, _ = P._resolve_level("medium", {"flip": True}, {"flip_mode": mode}, seed=3)
    vf, applied = P.build_filter(resolved, SRC_INFO)
    assert node in vf
    assert applied["flip_mode"] == mode


# ---------------------------------------------------------------------------
# Feature 2.3 — 变速 atempo 硬约束
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("factor,expect", [
    (1.10, 1.10),   # 合法值原样透传
    (2.5, P.ATEMPO_MAX),   # 超上限钳制
    (0.1, P.ATEMPO_MIN),   # 超下限钳制
])
def test_apply_speed_clamps_to_atempo_range(factor, expect):
    setpts, atempo, clamped = P._apply_speed(factor)
    assert clamped == expect
    assert setpts == f"setpts=PTS/{expect}"
    assert atempo == f"atempo={expect}"


# ---------------------------------------------------------------------------
# Feature 2.3 — _calc_trim 最短时长保护
# ---------------------------------------------------------------------------
def test_calc_trim_normal_long_video():
    """长素材：正常裁剪，总量不超 10%，成片仍 ≥7s。"""
    random.seed(7)
    r = P._calc_trim(49.48, P.LEVELS["medium"]["trim"])
    assert r["skipped"] is False
    assert r["head"] + r["tail"] <= 49.48 * P.MAX_TRIM_RATIO + 1e-6
    assert r["out_dur"] >= P.MIN_DURATION_TRIM
    assert r["ss"] == r["head"]


def test_calc_trim_clamps_to_ten_percent():
    """heavy 档对 8s 素材会想裁 2–3s，必须被 10% 上限钳到 0.8s。"""
    random.seed(7)
    r = P._calc_trim(8.0, P.LEVELS["heavy"]["trim"])
    assert r["skipped"] is False
    assert r["head"] + r["tail"] == pytest.approx(0.8, abs=0.01)
    assert r["out_dur"] >= P.MIN_DURATION_TRIM


def test_calc_trim_pulls_back_to_seven_seconds():
    """裁后不足 7s 时，把总裁剪量收回到成片恰好 7s（保护3）。"""
    random.seed(7)
    r = P._calc_trim(7.5, P.LEVELS["heavy"]["trim"])
    assert r["skipped"] is False
    assert r["out_dur"] == pytest.approx(P.MIN_DURATION_TRIM, abs=0.01)


def test_calc_trim_skips_when_source_under_seven_seconds():
    """原时长 <7s：跳过去头尾，不抛错。"""
    random.seed(7)
    r = P._calc_trim(6.5, P.LEVELS["heavy"]["trim"])
    assert r["skipped"] is True
    assert r["head"] == 0.0 and r["tail"] == 0.0
    assert r["out_dur"] == 6.5
    assert "6.50" in r["reason"]


def test_calc_trim_short_source_is_skipped_not_an_error():
    """🔒 语义固化（DD §0.3 落地口径）：

    5s 硬下限约束的是【裁剪/变速导致成片过短】，不是【素材本来就短】。
    4s 素材属于「天生不适合 trim」→ skipped，绝不抛 PipelineError。
    这一条防止后续重构把它误改回抛错。
    """
    random.seed(7)
    r = P._calc_trim(4.0, P.LEVELS["light"]["trim"])
    assert r["skipped"] is True
    assert r["out_dur"] == 4.0
    assert r["head"] == 0.0 and r["tail"] == 0.0


# ---------------------------------------------------------------------------
# Feature 2.3 — 变速不得跌破 5s 硬下限
# ---------------------------------------------------------------------------
def test_clamp_speed_no_op_for_long_video():
    """长素材加速远不到下限，不应被钳制。"""
    factor, note = P._clamp_speed_for_floor(1.05, 49.48)
    assert factor == 1.05
    assert note is None


def test_clamp_speed_respects_hard_floor():
    """5.5s 成片最多加速到 1.1x（5.5/5.0），再快就跌破 5s。"""
    factor, note = P._clamp_speed_for_floor(1.5, 5.5)
    assert factor == pytest.approx(1.1, abs=0.001)
    assert note is not None and "5.0" in note


def test_clamp_speed_never_shortens_already_short_source():
    """原素材已 <5s：不因加速再变短（上限 1.0），也不拒绝执行。"""
    factor, note = P._clamp_speed_for_floor(1.2, 4.0)
    assert factor == 1.0
    assert note is not None


def test_clamp_speed_allows_slowdown_always():
    """减速只会让成片更长，任何情况下都不该被钳制。"""
    factor, note = P._clamp_speed_for_floor(0.9, 4.0)
    assert factor == 0.9
    assert note is None


# ---------------------------------------------------------------------------
# Feature 2.4 — batch_fission 公开默认值
# ---------------------------------------------------------------------------
def test_batch_fission_default_count_matches_prd():
    """PRD D-04、rules auto_fill 与 Web 控件统一默认生成 5 个变体。"""
    default = inspect.signature(P.batch_fission).parameters["count"].default
    assert default == 5


def test_batch_fission_rotates_flip_modes_between_variants(monkeypatch):
    """批量开启 flip 时应自动轮换 h/v/90，避免所有变体使用同一方向。"""
    monkeypatch.setattr(P, "probe_video", lambda _src: {
        "name": "x.mp4", "duration": 10.0,
    })
    seen_modes = []

    def fake_dedup(*_args, **kwargs):
        seen_modes.append(kwargs.get("flip_mode"))
        i = len(seen_modes)
        return {
            "output_path": f"x_{i}.mp4",
            "output": {"md5": str(i)},
            "applied_params": {"trim_skipped": False, "flip_mode": kwargs.get("flip_mode")},
            "checks": {"all_passed": True},
        }

    monkeypatch.setattr(P, "dedup_video", fake_dedup)
    monkeypatch.setattr(P.M, "distance_matrix", lambda _paths: {
        "count": 5, "all_pass": True, "matrix": [],
        "too_close_pairs": [], "min_pair": None,
    })
    result = P.batch_fission("x.mp4", count=5, dimensions={"flip": True}, flip_mode="h")
    assert seen_modes == ["h", "v", "90", "h", "v"]
    assert result["separation"]["flip_spread"] is True


@pytest.mark.parametrize("md5s,matrix_pass,expected", [
    (["a", "b"], True, True),
    (["a", "a"], True, False),
    (["a", "b"], False, False),
    (["a", "b"], None, False),
])
def test_batch_fission_delivery_ready_requires_both_gates(
        monkeypatch, md5s, matrix_pass, expected):
    """只有 MD5 全唯一且矩阵明确通过时才允许交付。"""
    monkeypatch.setattr(P, "probe_video", lambda _src: {
        "name": "x.mp4", "duration": 10.0,
    })
    calls = {"i": 0}

    def fake_dedup(*_args, **_kwargs):
        i = calls["i"]
        calls["i"] += 1
        return {
            "output_path": f"x_{i}.mp4",
            "output": {"md5": md5s[i]},
            "applied_params": {"trim_skipped": False},
            "checks": {"all_passed": True},
        }

    monkeypatch.setattr(P, "dedup_video", fake_dedup)
    monkeypatch.setattr(P.M, "distance_matrix", lambda _paths: {
        "count": 2, "all_pass": matrix_pass,
        "matrix": [[None, 15], [15, None]],
        "too_close_pairs": [], "min_pair": None,
    })
    result = P.batch_fission("x.mp4", count=2)
    assert result["all_unique"] is (len(set(md5s)) == len(md5s))
    assert result["delivery_ready"] is expected


def test_batch_fission_cancel_keeps_completed_variants_and_skips_matrix(monkeypatch):
    token = P.CancellationToken()
    monkeypatch.setattr(P, "probe_video", lambda _src: {
        "name": "x.mp4", "duration": 10.0,
    })
    calls = {"count": 0, "matrix": 0}

    def fake_dedup(*_args, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 2:
            token.cancel()
            raise P.PipelineCancelled("cancelled")
        return {
            "output_path": "x_1.mp4",
            "output": {"md5": "m1"},
            "applied_params": {"trim_skipped": False},
            "checks": {"all_passed": True},
        }

    def fake_matrix(_paths):
        calls["matrix"] += 1
        return {}

    monkeypatch.setattr(P, "dedup_video", fake_dedup)
    monkeypatch.setattr(P.M, "distance_matrix", fake_matrix)

    result = P.batch_fission("x.mp4", count=4, cancel_token=token)

    assert result["cancelled"] is True
    assert result["requested_count"] == 4
    assert result["count"] == 1
    assert [v["output_path"] for v in result["variants"]] == ["x_1.mp4"]
    assert result["delivery_ready"] is False
    assert result["matrix"]["cancelled"] is True
    assert calls["matrix"] == 0


# ---------------------------------------------------------------------------
# Feature 2.4 — _calc_trim phase 模式（裂变变体间时间错位铺开）
#
# 🔒 语义固化：变体间分离度靠【时间错位】。实测（docs/eval/沉淀失败原因.md
#    F2.4-01）各维度的变体间差分：speed 1.9 / rotate 4.1 / crop 7.5，全部够不到
#    阈值 12；只有时间错位（δ≥1s → 29）与 flip（→ 33）有效。故 trim 的头尾配比
#    必须按变体序号【确定性铺开】，不能各变体从同一窄区间 iid 抽样。
#    这组测试防止后续重构把 phase 改回随机、或把 budget 算错导致错位量塌缩。
# ---------------------------------------------------------------------------
def test_calc_trim_phase_spreads_head_across_window():
    """phase 0→1 必须把 head 从 0 单调铺到整个可裁窗口（错位量 = budget）。"""
    dur = 49.482
    r0 = P._calc_trim(dur, P.LEVELS["heavy"]["trim"], phase=0.0)
    r5 = P._calc_trim(dur, P.LEVELS["heavy"]["trim"], phase=0.5)
    r1 = P._calc_trim(dur, P.LEVELS["heavy"]["trim"], phase=1.0)
    assert r0["head"] == 0.0
    assert r0["head"] < r5["head"] < r1["head"]
    # budget = min(2*hi=3.0, dur*10%=4.95, dur-7s=42.5) = 3.0
    assert r1["head"] == pytest.approx(3.0, abs=0.01)
    assert r5["head"] == pytest.approx(1.5, abs=0.01)


def test_calc_trim_phase_keeps_duration_identical_across_variants():
    """各变体成片时长必须恒等（总裁剪量固定，只挪头尾配比）。

    否则变体间会同时出现「时长不同 + 内容错位」，duration_close 自检口径失稳。
    """
    dur = 49.482
    durs = {P._calc_trim(dur, P.LEVELS["medium"]["trim"], phase=p)["out_dur"]
            for p in (0.0, 0.25, 0.5, 0.75, 1.0)}
    assert len(durs) == 1


def test_calc_trim_phase_head_plus_tail_is_budget():
    """head+tail 恒等于 budget，且不超 10% 上限。"""
    dur = 8.0
    for p in (0.0, 0.5, 1.0):
        r = P._calc_trim(dur, P.LEVELS["heavy"]["trim"], phase=p)
        assert r["head"] + r["tail"] == pytest.approx(0.8, abs=0.01)
        assert r["head"] + r["tail"] <= dur * P.MAX_TRIM_RATIO + 1e-6


def test_calc_trim_phase_clamped_to_unit_interval():
    """phase 越界必须钳到 [0,1]，不得反向裁剪或超窗。"""
    dur = 49.482
    lo = P._calc_trim(dur, P.LEVELS["light"]["trim"], phase=-3.0)
    hi = P._calc_trim(dur, P.LEVELS["light"]["trim"], phase=9.0)
    assert lo["head"] == 0.0 and lo["tail"] >= 0.0
    assert hi["tail"] == 0.0 and hi["head"] >= 0.0


def test_calc_trim_phase_skips_when_window_is_zero():
    """原时长贴近 7s 下限 → 可裁窗口为 0，必须显式 skipped 而非静默出 0 裁剪。

    静默返回 head=tail=0 会让裂变以为「已铺开」，实际各变体错位量恒为 0。
    """
    r = P._calc_trim(7.0, P.LEVELS["heavy"]["trim"], phase=0.5)
    assert r["skipped"] is True
    assert "0" in r["reason"] or "窗口" in r["reason"]


def test_calc_trim_phase_none_preserves_iid_behavior():
    """phase=None（单片去重路径）必须保持原随机行为，不被裂变改动波及。"""
    random.seed(7)
    a = P._calc_trim(49.48, P.LEVELS["medium"]["trim"])
    random.seed(7)
    b = P._calc_trim(49.48, P.LEVELS["medium"]["trim"], phase=None)
    assert a == b
    # 随机路径下 head/tail 独立取值，一般不相等（seed 7 已知如此）
    assert a["head"] != a["tail"]


def test_calc_trim_phase_short_source_still_skipped():
    """<7s 素材在 phase 模式下同样 skipped —— 短素材语义不因裂变而改变。"""
    r = P._calc_trim(5.063, P.LEVELS["heavy"]["trim"], phase=0.5)
    assert r["skipped"] is True
    assert r["head"] == 0.0 and r["tail"] == 0.0

