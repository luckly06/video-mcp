# -*- coding: utf-8 -*-
"""tests/test_server_smoke.py — mcp_server.py 的 RPC 调度 + 透传单测（不开 HTTP server）。

首次在 station 引入 monkeypatch：把 mcp_server.py 导入的 pipeline 模块（aliased 为 P）
整体替换为 stub，让 handle_rpc 走完整 RPC 调度链路但不真跑 ffmpeg。覆盖：
  - tools/list schema 包含新参数（dedup_video/batch_fission）
  - batch_fission schema 不暴露 seed（pipeline.batch_fission 签名无此参数）
  - 镜像：dimensions.flip=true 时 args["flip"] 在 hook 看到前被置为 True
  - 透传：dedup_video 收 level/seed/dimensions/flip_mode/trim_phase
  - 透传：batch_fission 收 level/dimensions/flip_mode/count（不收 seed/trim_phase）
  - _summary 计算 off_diagonal_mean/min + all_pass（走 result["matrix"]["matrix"] 2D 列表）
  - _summary dedup_video 包含 phash_passed 和 applied_level
"""

import sys
from pathlib import Path

import pytest

_STATION = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_STATION / "server"))

import mcp_server as S  # noqa: E402


# ----------------- stub pipeline -----------------
class _StubPipeline:
    """替身：捕获 _exec_tool 调用参数；返回结构化结果以供 _summary 计算。"""

    def __init__(self):
        self.calls = []

    def dedup_video(self, src, params=None, out_name=None, seed=None,
                    level=None, dimensions=None, flip_mode=None, trim_phase=None):
        self.calls.append({
            "fn": "dedup_video", "src": src, "params": params, "out_name": out_name,
            "seed": seed, "level": level, "dimensions": dimensions,
            "flip_mode": flip_mode, "trim_phase": trim_phase,
        })
        return {
            "src": {"name": src, "md5": "a"},
            "output": {"md5": "b", "width": 720, "height": 1280},
            "output_path": "/tmp/x.mp4",
            "applied_params": {"level": level or "medium"},
            "fps": 30,
            "checks": {
                "md5_changed": True, "resolution_kept": True,
                "duration_close": True, "min_duration_ok": True,
                "phash": {"passed": True, "avg": 18.5, "weak_frame_ratio": 0.06},
                "all_passed": True,
            },
        }

    def batch_fission(self, src, count=5, params=None,
                      level=None, dimensions=None, flip_mode=None):
        self.calls.append({
            "fn": "batch_fission", "src": src, "count": count, "params": params,
            "level": level, "dimensions": dimensions, "flip_mode": flip_mode,
        })
        # 与真实 pipeline.batch_fission 一致的 wrapper 结构：result["matrix"] 是包装 dict，
        # 内层 matrix 字段是 2D 列表（对角 None），外层带 all_pass / count / too_close_pairs。
        n = count
        inner = [[None] * n for _ in range(n)]
        for i in range(n):
            for j in range(i + 1, n):
                inner[i][j] = inner[j][i] = 15 + i + j
        return {
            "src": src,
            "count": n,
            "all_unique": True,
            "variants": [{"index": i + 1, "output_path": f"/tmp/x_v{i+1}.mp4",
                          "md5": f"m{i}", "applied_params": {}, "checks": {}}
                         for i in range(n)],
            "matrix": {
                "count": n,
                "all_pass": True,
                "matrix": inner,
                "min_pair": None,
                "too_close_pairs": [],
            },
            "separation": {"time_leg": "present", "flip_spread": False},
        }

    # 其它工具的 stub —— 让 list_assets / probe_video 走通
    def list_assets(self):
        return [{"name": "x.mp4"}]

    def probe_video(self, src):
        return {"name": Path(src).name, "width": 720, "height": 1280,
                "duration": 10.0, "md5": "abc"}

    def list_watermark_templates(self):
        return []

    def remove_watermark(self, src, platform, out_name=None):
        return {"ok": True}

    def PipelineError(self, msg):
        return Exception(msg)

    OUTPUT_DIR = Path("/tmp")


@pytest.fixture
def stub(monkeypatch):
    sp = _StubPipeline()
    monkeypatch.setattr(S, "P", sp)
    return sp


def _req(method, **kw):
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": kw}


def _bypass_hook(monkeypatch, captured=None):
    """把 mcp_server._run_hook 替成放行版，并可选捕获 pre hook 看到的 body。"""
    def fake_hook(script, payload):
        if captured is not None and script == "pre_tool_guard.py":
            captured["body"] = dict(payload.get("tool_input") or {})
        return {"continue": True}
    monkeypatch.setattr(S, "_run_hook", fake_hook)


# ----------------- schema -----------------
def test_tools_list_contains_new_fields():
    r = S.handle_rpc(_req("tools/list"), {})
    tools = {t["name"]: t for t in r["result"]["tools"]}
    d = tools["dedup_video"]["inputSchema"]["properties"]
    f = tools["batch_fission"]["inputSchema"]["properties"]
    # dedup_video: level/dimensions/flip_mode/seed 四个新字段
    for k in ("level", "dimensions", "flip_mode", "seed"):
        assert k in d, f"dedup_video 缺字段 {k}"
    assert d["level"]["enum"] == ["light", "medium", "heavy"]
    assert d["flip_mode"]["enum"] == ["h", "v", "90"]
    # batch_fission: level/dimensions/flip_mode 三个新字段；seed 不暴露（pipeline 不收）
    for k in ("level", "dimensions", "flip_mode"):
        assert k in f, f"batch_fission 缺字段 {k}"
    assert "seed" not in f, "batch_fission 不应暴露 seed（pipeline.batch_fission 签名无此参数）"


# ----------------- 镜像 dimensions.flip -> args.flip -----------------
def test_flip_dimension_is_mirrored_to_top_level(stub, monkeypatch):
    captured = {}
    _bypass_hook(monkeypatch, captured)

    S.handle_rpc(_req(
        "tools/call",
        name="dedup_video",
        arguments={"src": "x.mp4", "dimensions": {"flip": True}},
        inputResponses={"confirm": True},
    ), {})

    assert captured["body"].get("flip") is True, \
        f"dimensions.flip 未镜像到顶层 args['flip']，hook tier2 无法命中：{captured}"


def test_no_flip_dimension_means_no_top_level_flip(stub, monkeypatch):
    captured = {}
    _bypass_hook(monkeypatch, captured)

    S.handle_rpc(_req(
        "tools/call",
        name="dedup_video",
        arguments={"src": "x.mp4", "dimensions": {"rotate": False}},
        inputResponses={"confirm": True},
    ), {})

    assert "flip" not in captured["body"], \
        f"无 flip 时不应注入顶层 flip 字段：{captured}"


# ----------------- 透传 -----------------
def test_dedup_video_passes_level_seed_dimensions_flip_mode(stub, monkeypatch):
    _bypass_hook(monkeypatch)

    S.handle_rpc(_req(
        "tools/call",
        name="dedup_video",
        arguments={
            "src": "x.mp4", "level": "heavy", "seed": 42,
            "dimensions": {"crop": False, "speed": False},
            "flip_mode": "h",
        },
        inputResponses={"confirm": True},
    ), {})

    c = stub.calls[-1]
    assert c["fn"] == "dedup_video"
    assert c["level"] == "heavy"
    assert c["seed"] == 42
    assert c["dimensions"] == {"crop": False, "speed": False}
    assert c["flip_mode"] == "h"


def test_batch_fission_passes_level_dimensions_flip_mode_count(stub, monkeypatch):
    _bypass_hook(monkeypatch)

    S.handle_rpc(_req(
        "tools/call",
        name="batch_fission",
        arguments={
            "src": "x.mp4", "count": 4, "level": "light",
            "dimensions": {"flip": True}, "flip_mode": "v",
        },
        inputResponses={"confirm": True},
    ), {})

    c = stub.calls[-1]
    assert c["fn"] == "batch_fission"
    assert c["count"] == 4
    assert c["level"] == "light"
    assert c["dimensions"] == {"flip": True}
    assert c["flip_mode"] == "v"
    # 显式确认 batch_fission 没收到 seed/trim_phase（pipeline 签名不接受）
    assert "seed" not in c
    assert "trim_phase" not in c


# ----------------- _summary -----------------
def test_summary_batch_fission_computes_off_diagonal_min_avg():
    """_summary 走 result['matrix']['matrix'] 2D 列表，过滤 None（对角线），
    返回 off_diagonal_mean / off_diagonal_min 与 all_pass。"""
    result = {
        "count": 3, "all_unique": True,
        "matrix": {
            "count": 3,
            "all_pass": True,
            "matrix": [
                [None, 15, 16],
                [15,   None, 17],
                [16,   17,  None],
            ],
            "min_pair": None, "too_close_pairs": [],
        },
    }
    s = S._summary("batch_fission", result)
    assert s["all_pass"] is True
    assert s["off_diagonal_min"] == 15
    assert s["off_diagonal_mean"] == round((15 + 16 + 15 + 17 + 16 + 17) / 6, 3)


def test_summary_batch_fission_empty_matrix():
    """count=1 时 inner matrix 是 [[None]]；_summary 不应崩，mean/min 返回 None。"""
    result = {"count": 1, "all_unique": True,
              "matrix": {"count": 1, "all_pass": True, "matrix": [[None]],
                         "min_pair": None, "too_close_pairs": []}}
    s = S._summary("batch_fission", result)
    assert s["off_diagonal_min"] is None
    assert s["off_diagonal_mean"] is None


def test_summary_dedup_video_includes_phash_passed_and_level():
    result = {
        "output_path": "/tmp/x.mp4",
        "applied_params": {"level": "heavy"},
        "checks": {"phash": {"passed": True}},
    }
    s = S._summary("dedup_video", result)
    assert s["phash_passed"] is True
    assert s["phash"] == {"passed": True}
    assert s["applied_level"] == "heavy"