# -*- coding: utf-8 -*-
"""
test_metrics.py — 模块一 感知度量域单测（对应 DD Feature 1.1 / 1.2）。

覆盖：
  - 单对度量 compare_videos：同视频距离≈0（未达标）、不同素材距离大（达标）
  - 距离矩阵 distance_matrix：对角 null、对称、过近对命中、all_pass
  - backend 探测与 signature 兜底 method 切换

依赖真实素材 assets/下班来接我.mp4 与 assets/微笑.mp4（工程自带）。
"""

import sys
from pathlib import Path

import pytest

_SERVER = Path(__file__).resolve().parent.parent / "server"
sys.path.insert(0, str(_SERVER))

import metrics as M          # noqa: E402
import pipeline as P         # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "assets"
VID_A = FIXTURES_DIR / "下班来接我.mp4"
VID_B = FIXTURES_DIR / "微笑.mp4"

pytestmark = pytest.mark.skipif(
    not (VID_A.exists() and VID_B.exists()),
    reason="测试素材缺失（station/assets/下班来接我.mp4 或 微笑.mp4）",
)


# ---------------------------------------------------------------------------
# Feature 1.1 — 单对感知哈希度量
# ---------------------------------------------------------------------------
def test_backend_available():
    """开发环境应已安装 imagehash（requirements.txt）。"""
    assert M.has_phash_backend() is True


def test_same_video_distance_zero():
    """同一视频两两比对：距离≈0，反向阈值下判未达标。"""
    r = M.compare_videos(str(VID_A), str(VID_A), n=8)
    assert r["method"] == "phash"
    assert r["phash_avg"] == 0.0
    assert r["phash_min"] == 0
    assert r["passed"] is False          # 距离为 0 → 不够不同 → 未达标
    assert r["frames_compared"] >= 2


def test_different_videos_distance_large():
    """两个明显不同的素材：距离大，判达标（avg>=12 且 弱帧占比<=10%）。"""
    r = M.compare_videos(str(VID_A), str(VID_B), n=8)
    assert r["method"] == "phash"
    assert r["phash_avg"] >= M.PHASH_AVG_MIN
    assert r["weak_frame_ratio"] <= M.WEAK_FRAME_MAX_RATIO
    assert r["passed"] is True


# ---------------------------------------------------------------------------
# Q-01 达标口径：phash_min 退出硬门，改弱帧占比（2026-08-03 实测结论）
# ---------------------------------------------------------------------------
def test_min_no_longer_gates():
    """phash_min 仍输出但不参与判定；threshold 显式标注 min_min_enforced=False。

    回归意义：防止后来者看到 min_min 常量仍在，误以为它还是硬门而"顺手恢复"。
    """
    r = M.compare_videos(str(VID_A), str(VID_B), n=8)
    assert "phash_min" in r                      # 保留展示
    assert r["threshold"]["min_min_enforced"] is False
    # 关键语义：min 低于旧硬门(8)时，只要 avg 达标且弱帧占比合规，仍应 passed
    assert r["passed"] is True


def test_weak_ratio_gate_rejects_many_weak_frames():
    """avg 达标但弱帧过多 → 判未达标（min 硬门做不到这件事）。

    直接喂逐帧距离给 _result：avg=12.5 达标，但 8 帧里 3 帧 <8（37.5% > 10%）。
    """
    dists = [2, 4, 6, 20, 20, 20, 20, 8]         # avg=12.5, 3/8 弱帧
    avg = sum(dists) / len(dists)
    r = M._result(avg, min(dists), len(dists), "phash", dists=dists)
    assert r["phash_avg"] >= M.PHASH_AVG_MIN      # 均值达标
    assert r["weak_frame_count"] == 3
    assert r["weak_frame_ratio"] == 0.375
    assert r["passed"] is False                   # 被弱帧占比门拦下


def test_weak_ratio_gate_tolerates_single_outlier():
    """单一离群弱帧不应否决整支视频（旧 min 硬门的结构性缺陷）。

    16 帧里仅 1 帧 <8（6.25% <= 10%）→ 应达标；旧口径 min=4 会直接判死。
    """
    dists = [4] + [14] * 15                       # min=4，但仅 1/16 弱帧
    avg = sum(dists) / len(dists)
    r = M._result(avg, min(dists), len(dists), "phash", dists=dists)
    assert r["phash_min"] == 4                    # 旧硬门会 False
    assert r["weak_frame_ratio"] == 0.0625
    assert r["passed"] is True


def test_low_avg_still_rejected():
    """弱帧占比合规但均值不达标 → 仍判未达标（两个条件是 AND）。"""
    dists = [10] * 16                             # 无弱帧，但 avg=10 < 12
    r = M._result(10.0, 10, 16, "phash", dists=dists)
    assert r["weak_frame_ratio"] == 0.0
    assert r["passed"] is False


# ---------------------------------------------------------------------------
# Feature 1.2 — 距离矩阵与 signature 兜底
# ---------------------------------------------------------------------------
def test_distance_matrix_shape_and_close_pair():
    """三视频矩阵：对角 null、对称；含 A×A 过近对 → all_pass=False。"""
    a, b = str(VID_A), str(VID_B)
    res = M.distance_matrix([a, b, a], n=6)   # 第0与第2个相同 → 过近对
    assert res["count"] == 3
    mtx = res["matrix"]
    # 对角线为 null
    assert mtx[0][0] is None and mtx[1][1] is None and mtx[2][2] is None
    # 对称（[0][1] == [1][0]）
    assert mtx[0][1] == mtx[1][0]
    # 第0与第2个是同一视频 → 存在过近对
    assert len(res["too_close_pairs"]) >= 1
    assert res["all_pass"] is False
    # min_pair 应指向那对过近的（phash_avg 最小）
    assert res["min_pair"]["phash_avg"] == 0.0


def test_signature_fallback_method(monkeypatch):
    """无 phash backend 时 compare_videos 自动转 signature 兜底。"""
    monkeypatch.setattr(M, "has_phash_backend", lambda: False)
    r = M.compare_videos(str(VID_A), str(VID_B), n=6)
    assert r["method"] == "signature"
    assert "passed" in r
