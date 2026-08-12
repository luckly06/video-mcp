# -*- coding: utf-8 -*-
"""pipeline.py 真实素材 + ffmpeg 轻量集成测试。

感知度量的数值主路径由 test_metrics.py 独立覆盖；本文件固定管线编排、
实际输出和 checks 契约，测试产物只写入 pytest 临时目录。
"""

import json
import sys
from pathlib import Path

import pytest

_STATION = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_STATION / "server"))

import pipeline as P  # noqa: E402


ASSET = _STATION / "assets" / "微笑.mp4"

pytestmark = pytest.mark.skipif(
    not (ASSET.exists() and P.FFMPEG.exists() and P.FFPROBE.exists()),
    reason="真实素材或 ffmpeg/ffprobe 缺失",
)


def test_dedup_real_asset_runs_ffmpeg_and_returns_five_checks(monkeypatch, tmp_path):
    """真实跑一次 ffmpeg，并验证 DD F2.4 的输出与五项 checks 契约。"""
    monkeypatch.setattr(P, "VIDEO_DIR", ASSET.parent)
    monkeypatch.setattr(P, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(
        P.M,
        "compare_videos",
        lambda *_args, **_kwargs: {
            "method": "test-double",
            "passed": True,
            "phash_avg": 12.0,
            "phash_min": 8,
            "weak_frame_ratio": 0.0,
            "threshold": {"applied": True},
        },
    )

    result = P.dedup_video(
        ASSET.name,
        out_name="e2e-dedup.mp4",
        seed=20260804,
        level="light",
        dimensions={
            "picture": True,
            "rotate": False,
            "crop": False,
            "flip": False,
            "speed": False,
            "trim": False,
        },
    )

    output = Path(result["output_path"])
    checks = result["checks"]

    assert output.exists() and output.parent == tmp_path
    assert result["output"]["md5"] != result["src"]["md5"]
    assert checks["md5_changed"] is True
    assert checks["resolution_kept"] is True
    assert checks["duration_close"] is True
    assert checks["min_duration_ok"] is True
    assert checks["phash"]["passed"] is True
    assert checks["all_passed"] is True


def _stream_durations(path):
    """返回媒体文件中 video/audio 流的 duration（秒）。"""
    rc, out, err = P._run([
        str(P.FFPROBE), "-v", "error", "-show_entries", "stream=codec_type,duration",
        "-of", "json", str(path),
    ], timeout=60)
    assert rc == 0, err
    streams = json.loads(out).get("streams", [])
    return {
        stream["codec_type"]: float(stream["duration"])
        for stream in streams
        if stream.get("codec_type") in {"video", "audio"} and stream.get("duration")
    }


def test_heavy_speed_keeps_long_beat_track_audio_video_aligned(monkeypatch, tmp_path):
    """Q-03：>30s 节拍音轨经 1.10x 重档变速后，音视频流时长不累积漂移。"""
    source = tmp_path / "beat-source.mp4"
    rc, _out, err = P._run([
        str(P.FFMPEG), "-y",
        "-f", "lavfi", "-i", "testsrc2=size=320x568:rate=24:duration=31",
        "-f", "lavfi", "-i", "aevalsrc=sin(2*PI*880*t)*lt(mod(t\\,1)\\,0.08):s=48000:d=31",
        "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        "-c:a", "aac", "-b:a", "96k", str(source),
    ], timeout=180)
    assert rc == 0 and source.exists(), err

    output_dir = tmp_path / "output"
    output_dir.mkdir()
    monkeypatch.setattr(P, "VIDEO_DIR", tmp_path)
    monkeypatch.setattr(P, "OUTPUT_DIR", output_dir)
    original_choice = P.random.choice
    monkeypatch.setattr(P, "_rand", lambda a, b: 0.10 if (a, b) == (0, 0.10) else a)
    monkeypatch.setattr(
        P.random,
        "choice",
        lambda values: 1 if list(values) == [-1, 1] else original_choice(values),
    )
    monkeypatch.setattr(
        P.M,
        "compare_videos",
        lambda *_args, **_kwargs: {"method": "test-double", "passed": True},
    )

    result = P.dedup_video(
        source.name,
        out_name="beat-heavy-speed.mp4",
        seed=20260804,
        level="heavy",
        dimensions={
            "picture": False,
            "rotate": False,
            "crop": False,
            "flip": False,
            "speed": True,
            "trim": False,
        },
    )

    durations = _stream_durations(result["output_path"])
    expected = result["src"]["duration"] / result["applied_params"]["speed_factor"]

    assert result["applied_params"]["speed_factor"] == pytest.approx(1.10)
    assert result["checks"]["duration_close"] is True
    assert {"video", "audio"} <= durations.keys()
    assert abs(durations["video"] - durations["audio"]) <= 0.10
    assert abs(result["output"]["duration"] - expected) <= max(0.5, expected * 0.03)
