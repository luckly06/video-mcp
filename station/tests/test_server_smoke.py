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

import json
import subprocess
import sys
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

_STATION = Path(__file__).resolve().parent.parent
# 注：把 hooks/ 也加进 path 是为了 import common（F3.3 加了 is_path_allowed）。
# 不 import pre_tool_guard —— 它在模块顶层重写 sys.stdin/stdout（GBK 修复），
# 会破坏 pytest 的 stdio 捕获。
sys.path.insert(0, str(_STATION / "hooks"))
sys.path.insert(0, str(_STATION / "server"))

import common as C  # noqa: E402
import mcp_server as S  # noqa: E402


def _run_pre_tool_guard(payload):
    """通过 subprocess 调 pre_tool_guard.py（与 server 真实用法一致）。

    不直接 import —— pre_tool_guard 在模块顶层重写 sys.stdin/stdout
    (line 36-38 的 GBK 修复)，会破坏 pytest 的 stdio 捕获。
    subprocess 调用则天然隔离。
    """
    hook = _STATION / "hooks" / "pre_tool_guard.py"
    proc = subprocess.run(
        [sys.executable, str(hook)],
        input=__import__("json").dumps(payload, ensure_ascii=False).encode("utf-8"),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10,
    )
    out = proc.stdout.decode("utf-8", "replace").strip()
    return __import__("json").loads(out) if out else {}


# ----------------- stub pipeline -----------------
class _StubPipeline:
    """替身：捕获 _exec_tool 调用参数；返回结构化结果以供 _summary 计算。"""

    def __init__(self):
        self.calls = []

    def dedup_video(self, src, params=None, out_name=None, seed=None,
                    level=None, dimensions=None, flip_mode=None, trim_phase=None,
                    tts_text=None, tts_voice="冰糖", tts_speed=1.0,
                    rewrite_template=None):
        self.calls.append({
            "fn": "dedup_video", "src": src, "params": params, "out_name": out_name,
            "seed": seed, "level": level, "dimensions": dimensions,
            "flip_mode": flip_mode, "trim_phase": trim_phase,
            "tts_text": tts_text, "tts_voice": tts_voice,
            "tts_speed": tts_speed, "rewrite_template": rewrite_template,
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
                      level=None, dimensions=None, flip_mode=None, cancel_token=None,
                      tts_text=None, tts_voice="冰糖", tts_speed=1.0,
                      rewrite_template=None):
        self.calls.append({
            "fn": "batch_fission", "src": src, "count": count, "params": params,
            "level": level, "dimensions": dimensions, "flip_mode": flip_mode,
            "cancel_token": cancel_token,
            "tts_text": tts_text, "tts_voice": tts_voice,
            "tts_speed": tts_speed, "rewrite_template": rewrite_template,
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
            "requested_count": n,
            "cancelled": False,
            "all_unique": True,
            "delivery_ready": True,
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

    class CancellationToken:
        def __init__(self):
            self.cancelled = False

        def cancel(self):
            self.cancelled = True

    def PipelineError(self, msg):
        return Exception(msg)

    OUTPUT_DIR = Path("/tmp")


@pytest.fixture
def stub(monkeypatch, tmp_path):
    sp = _StubPipeline()
    monkeypatch.setattr(S, "P", sp)
    monkeypatch.setattr(S, "_LOGS", tmp_path)
    monkeypatch.setattr(S, "_JOBS_FILE", tmp_path / "jobs.json")
    return sp


def _req(method, **kw):
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": kw}


def _confirmed_req(name, arguments):
    return _req(
        "tools/call",
        name=name,
        arguments=arguments,
        inputResponses={"confirm": True},
        requestState=S._request_state(name, arguments),
    )


def _bypass_hook(monkeypatch, captured=None):
    """把 mcp_server._run_hook 替成放行版，并可选捕获 pre hook 看到的 body。"""
    def fake_hook(script, payload):
        if captured is not None and script == "pre_tool_guard.py":
            captured["body"] = dict(payload.get("tool_input") or {})
        return {"continue": True}
    monkeypatch.setattr(S, "_run_hook", fake_hook)


# ----------------- 人工确认状态绑定 -----------------
def test_warned_tool_returns_bound_request_state(stub, monkeypatch):
    _bypass_hook(monkeypatch)
    args = {"src": "x.mp4", "dimensions": {"picture": True}}
    first = S.handle_rpc(_req(
        "tools/call", name="dedup_video", arguments=args), {})
    result = first["result"]
    assert result["resultType"] == "input_required"
    assert S._valid_request_state(result["requestState"], "dedup_video", args) is True


def test_confirm_without_request_state_is_rejected(stub, monkeypatch):
    _bypass_hook(monkeypatch)
    response = S.handle_rpc(_req(
        "tools/call",
        name="dedup_video",
        arguments={"src": "x.mp4"},
        inputResponses={"confirm": True},
    ), {})
    assert response["result"]["isError"] is True
    assert "确认状态无效" in response["result"]["content"][0]["text"]
    assert stub.calls == []


def test_confirm_state_rejects_changed_arguments(stub, monkeypatch):
    _bypass_hook(monkeypatch)
    confirmed = {"src": "x.mp4", "level": "light"}
    changed = {"src": "x.mp4", "level": "heavy"}
    response = S.handle_rpc(_req(
        "tools/call",
        name="dedup_video",
        arguments=changed,
        inputResponses={"confirm": True},
        requestState=S._request_state("dedup_video", confirmed),
    ), {})
    assert response["result"]["isError"] is True
    assert stub.calls == []


def test_confirm_state_rejects_tampered_signature(stub, monkeypatch):
    _bypass_hook(monkeypatch)
    args = {"src": "x.mp4"}
    state = S._request_state("dedup_video", args)
    response = S.handle_rpc(_req(
        "tools/call",
        name="dedup_video",
        arguments=args,
        inputResponses={"confirm": True},
        requestState=state[:-1] + ("0" if state[-1] != "0" else "1"),
    ), {})
    assert response["result"]["isError"] is True
    assert stub.calls == []


# ----------------- Hook / 本地来源安全门 -----------------
def test_pre_hook_failure_is_fail_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(S, "_HOOKS", tmp_path)
    result = S._run_hook("pre_tool_guard.py", {"tool_name": "dedup_video"})
    assert result["continue"] is False
    assert result["permissionDecision"] == "deny"


def test_post_hook_failure_does_not_retroactively_fail_operation(monkeypatch, tmp_path):
    monkeypatch.setattr(S, "_HOOKS", tmp_path)
    result = S._run_hook("post_tool_audit.py", {"tool_name": "probe_video"})
    assert result["continue"] is True
    assert result["hook_error"]


@pytest.mark.parametrize("host", ["127.0.0.1:8765", "localhost:8765", "[::1]:8765"])
def test_local_hosts_are_allowed(host):
    assert S._allowed_host(host) is True


@pytest.mark.parametrize("host", [
    "", "evil.example:8765", "127.0.0.1.evil.example",
    "[::1].evil:8765", "localhost:not-a-port",
])
def test_non_local_hosts_are_rejected(host):
    assert S._allowed_host(host) is False


@pytest.mark.parametrize("origin", [None, "null", "http://127.0.0.1:8080", "https://localhost"])
def test_local_origins_are_allowed(origin):
    assert S._allowed_origin(origin) is True


@pytest.mark.parametrize("origin", ["https://evil.example", "file://evil/path", "javascript:alert(1)"])
def test_non_local_origins_are_rejected(origin):
    assert S._allowed_origin(origin) is False


@pytest.fixture
def local_http_server():
    server = S.ThreadingHTTPServer(("127.0.0.1", 0), S.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.mark.parametrize(
    ("path", "content_type", "marker"),
    [
        ("/", "text/html", "视频去重工位"),
        ("/index.html", "text/html", "视频去重工位"),
        ("/style.css", "text/css", ":root"),
        ("/app.js", "text/javascript", "connectAndBootstrap"),
    ],
)
def test_web_assets_are_served_from_same_origin(local_http_server, path, content_type, marker):
    with urlopen(local_http_server + path, timeout=3) as response:
        body = response.read().decode("utf-8")
        assert response.status == 200
        assert response.headers["Content-Type"].startswith(content_type)
        assert response.headers["Cache-Control"] == "no-store"
    assert marker in body


def test_web_head_returns_headers_without_body(local_http_server):
    request = Request(local_http_server + "/app.js", method="HEAD")
    with urlopen(request, timeout=3) as response:
        assert response.status == 200
        assert response.headers["Content-Type"].startswith("text/javascript")
        assert int(response.headers["Content-Length"]) > 0
        assert response.read() == b""


def test_web_favicon_is_empty_and_unknown_path_is_hidden(local_http_server):
    with urlopen(local_http_server + "/favicon.ico", timeout=3) as response:
        assert response.status == 204
        assert response.read() == b""
    with pytest.raises(HTTPError) as exc_info:
        urlopen(local_http_server + "/server/mcp_server.py", timeout=3)
    assert exc_info.value.code == 404


def test_open_output_endpoint_opens_fixed_directory(local_http_server, monkeypatch, tmp_path):
    opened = tmp_path / "output"
    monkeypatch.setattr(S, "_open_output_folder", lambda filename=None: (opened, None))
    request = Request(
        local_http_server + "/local/open-output",
        data=b"{}",
        method="POST",
        headers={"Origin": "http://127.0.0.1:8080", "Content-Type": "application/json"},
    )
    with urlopen(request, timeout=3) as response:
        body = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        assert response.headers["Access-Control-Allow-Origin"] == "http://127.0.0.1:8080"
    assert body == {"ok": True, "path": str(opened), "selected": None}


def test_cancel_fission_endpoint_signals_active_task(local_http_server, monkeypatch):
    token = S.P.CancellationToken()
    task_id = "fission_http_test"
    with S._ACTIVE_FISSION_LOCK:
        S._ACTIVE_FISSION[task_id] = token
    try:
        request = Request(
            local_http_server + "/local/cancel-fission",
            data=json.dumps({"task_id": task_id}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request, timeout=3) as response:
            body = json.loads(response.read().decode("utf-8"))
            assert response.status == 200
        assert body["ok"] is True and body["found"] is True
        assert token.is_cancelled() is True
    finally:
        with S._ACTIVE_FISSION_LOCK:
            S._ACTIVE_FISSION.pop(task_id, None)


def test_cancel_fission_endpoint_reports_finished_task(local_http_server):
    request = Request(
        local_http_server + "/local/cancel-fission",
        data=json.dumps({"task_id": "fission_finished"}).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=3) as response:
        body = json.loads(response.read().decode("utf-8"))
    assert body == {"ok": True, "found": False, "message": "任务已结束或不存在"}


def test_open_output_endpoint_selects_named_artifact(local_http_server, monkeypatch, tmp_path):
    opened = tmp_path / "output"
    selected = opened / "成片.mp4"
    seen = []

    def fake_open(filename=None):
        seen.append(filename)
        return opened, selected

    monkeypatch.setattr(S, "_open_output_folder", fake_open)
    request = Request(
        local_http_server + "/local/open-output",
        data=json.dumps({"filename": "成片.mp4"}, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Origin": "http://127.0.0.1:8080", "Content-Type": "application/json"},
    )
    with urlopen(request, timeout=3) as response:
        body = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
    assert seen == ["成片.mp4"]
    assert body == {"ok": True, "path": str(opened), "selected": str(selected)}


@pytest.mark.parametrize("filename", ["../outside.mp4", "subdir/x.mp4", "C:\\outside.mp4"])
def test_open_output_folder_rejects_non_basename(monkeypatch, tmp_path, filename):
    output = tmp_path / "output"
    monkeypatch.setattr(S.P, "OUTPUT_DIR", output)
    with pytest.raises(ValueError):
        S._open_output_folder(filename)


def test_open_output_folder_rejects_missing_artifact(monkeypatch, tmp_path):
    output = tmp_path / "output"
    monkeypatch.setattr(S.P, "OUTPUT_DIR", output)
    with pytest.raises(FileNotFoundError):
        S._open_output_folder("missing.mp4")


def test_open_output_folder_selects_file_on_windows(monkeypatch, tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    artifact = output / "成片 文件.mp4"
    artifact.write_bytes(b"video")
    calls = []
    monkeypatch.setattr(S.P, "OUTPUT_DIR", output)
    monkeypatch.setattr(S.os, "name", "nt")
    monkeypatch.setattr(S.subprocess, "Popen", lambda args: calls.append(args))

    opened, selected = S._open_output_folder(artifact.name)

    assert opened == output.resolve()
    assert selected == artifact.resolve()
    assert calls == [["explorer.exe", "/select,", str(artifact.resolve())]]


def test_open_output_endpoint_rejects_non_local_origin(local_http_server, monkeypatch):
    called = False

    def fake_open():
        nonlocal called
        called = True
        return Path("output")

    monkeypatch.setattr(S, "_open_output_folder", fake_open)
    request = Request(
        local_http_server + "/local/open-output",
        data=b"",
        method="POST",
        headers={"Origin": "https://evil.example"},
    )
    with pytest.raises(HTTPError) as exc_info:
        urlopen(request, timeout=3)
    assert exc_info.value.code == 403
    assert called is False


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
    # batch_fission: level/dimensions/flip_mode/task_id；seed 不暴露（pipeline 不收）
    for k in ("level", "dimensions", "flip_mode", "task_id"):
        assert k in f, f"batch_fission 缺字段 {k}"
    assert "seed" not in f, "batch_fission 不应暴露 seed（pipeline.batch_fission 签名无此参数）"


# ----------------- 镜像 dimensions.flip -> args.flip -----------------
def test_flip_dimension_is_mirrored_to_top_level(stub, monkeypatch):
    captured = {}
    _bypass_hook(monkeypatch, captured)

    args = {"src": "x.mp4", "dimensions": {"flip": True}}
    S.handle_rpc(_confirmed_req("dedup_video", args), {})

    assert captured["body"].get("flip") is True, \
        f"dimensions.flip 未镜像到顶层 args['flip']，hook tier2 无法命中：{captured}"


def test_no_flip_dimension_means_no_top_level_flip(stub, monkeypatch):
    captured = {}
    _bypass_hook(monkeypatch, captured)

    args = {"src": "x.mp4", "dimensions": {"rotate": False}}
    S.handle_rpc(_confirmed_req("dedup_video", args), {})

    assert "flip" not in captured["body"], \
        f"无 flip 时不应注入顶层 flip 字段：{captured}"


# ----------------- 透传 -----------------
def test_dedup_video_passes_level_seed_dimensions_flip_mode(stub, monkeypatch):
    _bypass_hook(monkeypatch)

    args = {
        "src": "x.mp4", "level": "heavy", "seed": 42,
        "dimensions": {"crop": False, "speed": False},
        "flip_mode": "h",
    }
    S.handle_rpc(_confirmed_req("dedup_video", args), {})

    c = stub.calls[-1]
    assert c["fn"] == "dedup_video"
    assert c["level"] == "heavy"
    assert c["seed"] == 42
    assert c["dimensions"] == {"crop": False, "speed": False}
    assert c["flip_mode"] == "h"


def test_batch_fission_passes_level_dimensions_flip_mode_count(stub, monkeypatch):
    _bypass_hook(monkeypatch)

    args = {
        "src": "x.mp4", "count": 4, "level": "light",
        "dimensions": {"flip": True}, "flip_mode": "v",
        "task_id": "fission_test123",
    }
    S.handle_rpc(_confirmed_req("batch_fission", args), {})

    c = stub.calls[-1]
    assert c["fn"] == "batch_fission"
    assert c["count"] == 4
    assert c["level"] == "light"
    assert c["dimensions"] == {"flip": True}
    assert c["flip_mode"] == "v"
    assert c["cancel_token"] is not None
    assert "fission_test123" not in S._ACTIVE_FISSION
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
    result["delivery_ready"] = True
    s = S._summary("batch_fission", result)
    assert s["all_pass"] is True
    assert s["delivery_ready"] is True
    assert s["off_diagonal_min"] == 15
    assert s["off_diagonal_mean"] == round((15 + 16 + 15 + 17 + 16 + 17) / 6, 3)


def test_summary_batch_fission_delivery_not_ready_when_matrix_fails():
    result = {
        "count": 2, "all_unique": True, "delivery_ready": False,
        "matrix": {"count": 2, "all_pass": False,
                   "matrix": [[None, 8], [8, None]],
                   "min_pair": None, "too_close_pairs": []},
    }
    assert S._summary("batch_fission", result)["delivery_ready"] is False


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


# ===========================================================================
# F3.3 路径白名单双保险
# ===========================================================================

# ---- common.is_path_allowed 纯函数 ----
def test_is_path_allowed_inside_base():
    """相对路径 + 在 base 内 → ok。"""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        ok, reason = C.is_path_allowed("a/b.mp4", td)
        assert ok is True
        assert reason == ""


def test_is_path_allowed_absolute_outside():
    """绝对路径越界（Windows 盘符） → deny + 给出原因。"""
    ok, reason = C.is_path_allowed("C:/Windows/System32/notepad.exe",
                                   "F:/ai_agent/dev2")
    assert ok is False
    assert "不在白名单" in reason


def test_is_path_allowed_traversal():
    """.. 穿越 → deny + resolve 后的绝对路径出现在 reason。"""
    ok, reason = C.is_path_allowed("../../etc/passwd", "F:/ai_agent/dev2/xiaomi/ai/test11")
    assert ok is False
    assert "不在白名单" in reason


# ---- pre_tool_guard 形态校验（hook 第一道闸）----
def test_pre_tool_guard_denies_absolute_src():
    """guard() 收到 src 含 Windows 盘符 → permissionDecision=deny。

    通过 subprocess 调 pre_tool_guard.py —— 与 server 真实用法一致。
    直接 import 会触发 hook 顶层的 sys.stdin 重写（GBK 修复），
    进而破坏 pytest 的 stdio 捕获。
    """
    out = _run_pre_tool_guard({"tool_name": "dedup_video",
                               "tool_input": {"src": "C:/foo"},
                               "tier": "warned"})
    assert out["continue"] is False
    assert out["permissionDecision"] == "deny"
    assert "路径白名单" in out["reason"]
    assert "Windows 盘符" in out["reason"]


def test_pre_tool_guard_denies_traversal_src():
    """guard() 收到 src 含 .. 穿越 → permissionDecision=deny。"""
    out = _run_pre_tool_guard({"tool_name": "dedup_video",
                               "tool_input": {"src": "../../../etc/passwd"},
                               "tier": "warned"})
    assert out["continue"] is False
    assert out["permissionDecision"] == "deny"
    assert "路径白名单" in out["reason"]
    assert ".. 穿越" in out["reason"]


def test_pre_tool_guard_allows_normal_src():
    """普通相对中文名 → 走完 warn 分支，返回 ask（不被 hook 第一道闸拦）。"""
    out = _run_pre_tool_guard({"tool_name": "dedup_video",
                               "tool_input": {"src": "微笑.mp4"},
                               "tier": "warned"})
    assert out["continue"] is True
    # warned 工具应继续走 ask 分支
    assert out["permissionDecision"] == "ask"


# ---- pipeline._resolve_safe（第二道闸）----
def test_probe_video_rejects_outside_path():
    """probe_video(src=C:/foo) → PipelineError（拒绝越界）。"""
    try:
        S.P.probe_video("C:/Windows/System32/notepad.exe")
    except S.P.PipelineError as e:
        assert "白名单" in str(e)
        return
    raise AssertionError("应抛 PipelineError")


def test_delete_output_rejects_traversal():
    """_resolve_safe(../../../etc/passwd, OUTPUT_DIR) → PipelineError。

    对应 mcp_server.py delete_output 的 defense-in-depth：
    即使 tier=blocked 拦截了正常调用，路径遍历 bug 本身被堵住。
    """
    try:
        S.P._resolve_safe("../../etc/passwd", S.P.OUTPUT_DIR, must_exist=False)
    except S.P.PipelineError as e:
        assert "白名单" in str(e)
        return
    raise AssertionError("应抛 PipelineError")