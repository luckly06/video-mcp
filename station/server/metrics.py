# -*- coding: utf-8 -*-
"""
metrics.py — 感知度量域（🆕 本期新增，对齐 PRD D-01 / DD 模块一）

职责：独立、无业务耦合的感知哈希度量。输入视频路径，抽帧计算逐帧 pHash，
输出汉明距离统计与达标判定；支持一组视频两两距离矩阵（裂变用）。

设计要点（DD §1.5）：
  - 时间比例归一化抽帧：两视频按【相对时刻】(k+0.5)/n 各自换算时间戳抽帧，
    使裁剪/变速变体（时长/构图变了）仍能按「同一相对时刻」比对内容差异，
    而非被时序错位污染。
  - 反向用阈值（判「足够不同」）：距离越大越好。
    达标 = phash_avg >= 12 且 weak_frame_ratio <= 0.10；phash_min 仅展示，不参与判定。
  - 降级兜底：无 imagehash/Pillow 时走 ffmpeg signature，只给 pass/fail 二值。

依赖：imagehash（依赖 Pillow）为主；缺失时自动降级。ffmpeg 复用 pipeline 常量。
纯函数式，不依赖 pipeline 的写操作，可独立单测。
"""

import os
import json
import shutil
import tempfile
import subprocess
from pathlib import Path

# 复用 pipeline 的 ffmpeg/ffprobe 路径锚定与 probe/异常类型（唯一对 pipeline 的引用，
# 且只取只读常量与探测函数，不触碰其写操作，符合 DD 依赖方向）。
import pipeline as P

# ---------------------------------------------------------------------------
# 达标阈值与抽帧数（DD §0.2 / §1.4；Q-01 用真实素材标定后【仅改此处】，结构不动）
# ---------------------------------------------------------------------------
PHASH_AVG_MIN = 12    # 逐帧汉明距离【平均】达标下限

# ⚠️ phash_min 已【退出硬门】，仅作展示（Q-01 实测结论，2026-08-03）
#    原因：min 是极值序列统计量，对抽帧数【单调不增】——抽得越多只可能更低，
#    故「min>=8」的严苛程度随 n 漂移，不收敛。实测同一对（龙.mp4 heavy）：
#      n=8 → min=6 ; n=16 → min=6 ; n=32 → min=2 ; n=48 → min=4
#    同素材 200 次重抽样，n=16 时 min 跨度达 6、p25 跨度达 8，均不可作门；
#    而「弱帧占比」跨 n=8/12/16/24 稳定在 0.103–0.106（见下）。
PHASH_MIN_MIN = 8     # 仅展示/兼容口径，不参与 passed 判定

# 弱帧占比门（替代 min 硬门）：单帧距离 < WEAK_FRAME_DIST 视为弱帧，
# 弱帧占比 > WEAK_FRAME_MAX_RATIO 则判未达标。占比是【比例估计量】而非极值，
# 对抽帧数稳健，且保留了「不允许成片存在大量与原片过近的片段」的原意。
WEAK_FRAME_DIST = 8
WEAK_FRAME_MAX_RATIO = 0.10

SAMPLE_FRAMES = 16    # 默认抽帧数


# ---------------------------------------------------------------------------
# 轻量 ffprobe 取时长（metrics 内部用，不走白名单校验；路径安全由 pipeline 保证）
# ---------------------------------------------------------------------------
def _probe_duration(video_path):
    """ffprobe 读 format.duration。失败返回 0.0。"""
    if not P.FFPROBE.exists():
        raise P.PipelineError(f"ffprobe 未找到: {P.FFPROBE}")
    cmd = [
        str(P.FFPROBE), "-v", "quiet", "-print_format", "json",
        "-show_format", str(video_path),
    ]
    rc, out, err = P._run(cmd, timeout=60)
    if rc != 0:
        return 0.0
    try:
        data = json.loads(out)
        return float(data.get("format", {}).get("duration") or 0.0)
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# backend 探测：imagehash/Pillow 是否可用，决定主路径 or signature 兜底
# ---------------------------------------------------------------------------
def has_phash_backend():
    """探测 imagehash + Pillow 是否可用。缺失 → 走 ffmpeg signature 兜底。"""
    try:
        import imagehash  # noqa: F401
        from PIL import Image  # noqa: F401
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 抽帧：ffmpeg 按【时间比例】均匀抽 n 帧为 PNG（列表式传参，无 shell 注入面）
# ---------------------------------------------------------------------------
def _extract_frames(video_path, n=SAMPLE_FRAMES, tmpdir=None):
    """
    按时间比例 (k+0.5)/n 抽 n 帧到 tmpdir，返回帧文件路径列表（按时刻升序）。
    取帧中点避免首尾黑帧/片头片尾。极短视频按实际时长可抽帧数不足时，
    仍尽力抽（重复时刻由 ffmpeg 就近取帧），由上层按 frames_compared 反映真实值。
    """
    video_path = Path(video_path)
    if not video_path.exists():
        raise P.PipelineError(f"度量源不存在: {video_path}")
    if not P.FFMPEG.exists():
        raise P.PipelineError(f"ffmpeg 未找到: {P.FFMPEG}")

    # 轻量 ffprobe 取时长：metrics 职责是度量，路径安全由 pipeline 调用方保证
    # （不走 P.probe_video，避免对 OUTPUT_DIR 下产出文件误用 VIDEO_DIR 白名单）
    duration = _probe_duration(video_path)
    if duration <= 0:
        raise P.PipelineError(f"无法获取时长，抽帧中止: {video_path}")

    frames = []
    for k in range(n):
        ratio = (k + 0.5) / n
        ts = round(ratio * duration, 3)
        out_png = Path(tmpdir) / f"{video_path.stem}_{k:03d}.png"
        # -ss 放 -i 之前做快速定位；-frames:v 1 抽单帧；-y 覆盖
        cmd = [
            str(P.FFMPEG), "-y", "-ss", str(ts), "-i", str(video_path),
            "-frames:v", "1", "-q:v", "2", str(out_png),
        ]
        rc, out, err = P._run(cmd, timeout=60)
        if rc == 0 and out_png.exists():
            frames.append(out_png)
    if not frames:
        raise P.PipelineError(f"抽帧全部失败: {video_path}\n{err[-500:] if 'err' in dir() else ''}")
    return frames


def _phash_sequence(frame_paths):
    """逐帧 64 位 pHash，返回 imagehash.ImageHash 列表。"""
    import imagehash
    from PIL import Image
    seq = []
    for fp in frame_paths:
        with Image.open(fp) as im:
            seq.append(imagehash.phash(im))
    return seq


def _result(phash_avg, phash_min, frames_compared, method, dists=None):
    """组装 PhashResult。passed 口径见 DD §0.2（Q-01 实测后调整，见下）。

    达标 = phash_avg >= PHASH_AVG_MIN 且 weak_frame_ratio <= WEAK_FRAME_MAX_RATIO。
    phash_min 保留输出但【不再参与判定】——理由见 PHASH_MIN_MIN 注释（极值统计量
    随抽帧数漂移，不可作门）。dists 为逐帧距离列表；缺省时退化为用 phash_min
    近似（仅 signature 兜底等无逐帧数据的路径）。
    """
    if dists:
        weak_count = sum(1 for d in dists if d < WEAK_FRAME_DIST)
        weak_ratio = weak_count / len(dists)
    else:
        # 无逐帧数据：只能用 min 粗略反映（min<门 → 至少存在 1 弱帧）
        weak_count = None
        weak_ratio = 0.0 if phash_min >= WEAK_FRAME_DIST else 1.0

    passed = (phash_avg >= PHASH_AVG_MIN and weak_ratio <= WEAK_FRAME_MAX_RATIO)
    return {
        "phash_avg": round(float(phash_avg), 3),
        "phash_min": int(phash_min),
        "weak_frame_ratio": round(float(weak_ratio), 4),
        "weak_frame_count": weak_count,
        "frames_compared": int(frames_compared),
        "passed": bool(passed),
        "method": method,
        "threshold": {
            "avg_min": PHASH_AVG_MIN,
            "weak_frame_dist": WEAK_FRAME_DIST,
            "weak_frame_max_ratio": WEAK_FRAME_MAX_RATIO,
            "min_min": PHASH_MIN_MIN,          # 仅展示，不参与判定
            "min_min_enforced": False,
        },
    }


# ---------------------------------------------------------------------------
# 单对度量：两视频 → PhashResult（DD Feature 1.1）
# ---------------------------------------------------------------------------
def compare_videos(video_a, video_b, n=SAMPLE_FRAMES):
    """
    对齐抽帧 → 逐帧汉明距离 → 统计 → 达标。
    无 imagehash/Pillow 时自动转 _signature_fallback（method="signature"）。
    """
    if not has_phash_backend():
        return _signature_fallback(video_a, video_b)

    tmpdir = tempfile.mkdtemp(prefix="vu_phash_")
    try:
        frames_a = _extract_frames(video_a, n=n, tmpdir=tmpdir)
        frames_b = _extract_frames(video_b, n=n, tmpdir=tmpdir)
        # 按时间比例抽帧后，两序列位置一一对应；取较短长度对齐
        m = min(len(frames_a), len(frames_b))
        if m < 2:
            # 帧数不足以稳定度量：判未达标并提示（不抛错，交由上层展示）
            return _result(0.0, 0, m, "phash")
        seq_a = _phash_sequence(frames_a[:m])
        seq_b = _phash_sequence(frames_b[:m])
        dists = [seq_a[i] - seq_b[i] for i in range(m)]  # imagehash 重载 __sub__ = 汉明距离
        phash_avg = sum(dists) / len(dists)
        phash_min = min(dists)
        return _result(phash_avg, phash_min, m, "phash", dists=dists)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# signature 兜底：无 imagehash 时用 ffmpeg signature 滤镜出 pass/fail（DD Feature 1.2）
# ---------------------------------------------------------------------------
def _signature_fallback(video_a, video_b):
    """
    ffmpeg signature 滤镜对两视频做 MPEG-7 视频签名比对，得二值判定。
    signature 给的是「是否匹配（疑似同源）」——本产品反用：不匹配 = 足够不同 = passed。
    只填 passed/method，数值字段给占位（0），threshold 标注不适用。
    """
    va, vb = Path(video_a), Path(video_b)
    for v in (va, vb):
        if not v.exists():
            raise P.PipelineError(f"度量源不存在: {v}")
    # signature 滤镜：两输入送 signature，detectclipping/nb_inputs=2，输出匹配报告到 stderr
    cmd = [
        str(P.FFMPEG), "-i", str(va), "-i", str(vb),
        "-filter_complex",
        "[0:v][1:v]signature=nb_inputs=2:detectmode=full",
        "-f", "null", "-",
    ]
    rc, out, err = P._run(cmd, timeout=300)
    log = (out + "\n" + err).lower()
    # ffmpeg signature 命中相似片段时会打印 "matching ... " 报告；未命中则无匹配。
    matched = "matching of video" in log and "whole video matching" in log
    # 反用：matched(疑似同源) → 不够不同 → passed=False；未匹配 → passed=True
    passed = not matched
    return {
        "phash_avg": 0.0,
        "phash_min": 0,
        # 与主路径保持同构（消费方无需分支判断字段是否存在）：
        # signature 无逐帧数据，弱帧占比不可计算 → None 表示「不适用」，非 0。
        "weak_frame_ratio": None,
        "weak_frame_count": None,
        "frames_compared": 0,
        "passed": bool(passed),
        "method": "signature",
        "threshold": {
            "avg_min": PHASH_AVG_MIN,
            "weak_frame_dist": WEAK_FRAME_DIST,
            "weak_frame_max_ratio": WEAK_FRAME_MAX_RATIO,
            "min_min": PHASH_MIN_MIN,
            "min_min_enforced": False,
            "applied": False,          # signature 路径不套数值门，passed 由签名匹配直接给出
        },
        "note": "ffmpeg signature 兜底：仅 pass/fail，无逐帧汉明距离与弱帧占比数值。",
    }


# ---------------------------------------------------------------------------
# 距离矩阵：一组视频两两度量（DD Feature 1.2，裂变用）
# ---------------------------------------------------------------------------
def distance_matrix(video_paths, n=SAMPLE_FRAMES):
    """
    两两 compare_videos，聚合矩阵与过近对。
    matrix[i][j] = 变体 i 与 j 的 phash_avg（对角线 null）。

    all_pass = 所有对均 passed（口径见 _result：avg >= PHASH_AVG_MIN 且
    弱帧占比 <= WEAK_FRAME_MAX_RATIO；phash_min 已退出判定）。
    此处不自行复算阈值，一律复用 compare_videos 的 passed，避免口径二次漂移。
    """
    paths = list(video_paths)
    count = len(paths)
    matrix = [[None] * count for _ in range(count)]
    too_close = []
    min_pair = None

    for i in range(count):
        for j in range(i + 1, count):
            r = compare_videos(paths[i], paths[j], n=n)
            avg = r["phash_avg"]
            mn = r["phash_min"]
            matrix[i][j] = avg
            matrix[j][i] = avg  # 对称填充
            # pair 带上弱帧占比：新口径下「过近」可能因均值不足、也可能因弱帧过多，
            # 只报 avg 会让人看不出被拒原因（如 avg=12.25 但弱帧 31% 仍被拒）。
            pair = {"i": i, "j": j, "phash_avg": avg, "phash_min": mn,
                    "weak_frame_ratio": r.get("weak_frame_ratio"),
                    "passed": r["passed"]}
            if not r["passed"]:
                too_close.append(pair)
            if min_pair is None or avg < min_pair["phash_avg"]:
                min_pair = pair

    return {
        "count": count,
        "matrix": matrix,
        "min_pair": min_pair,
        "all_pass": len(too_close) == 0 and count >= 2,
        "too_close_pairs": too_close,
    }


if __name__ == "__main__":
    import pprint
    print("phash backend available:", has_phash_backend())
    assets = P.list_assets()
    pprint.pprint(assets)
