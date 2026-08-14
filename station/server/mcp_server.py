# -*- coding: utf-8 -*-
"""
mcp_server.py — 视频去重 MCP Server（遵循 MCP 2026-07-28 无状态规范）

对齐 MCP 2026-07-28 核心变化：
  - 无状态：删除 initialize / notifications/initialized 握手（SEP-2575）
  - 删除 Mcp-Session-Id，任意请求可落在任意实例（SEP-2567）
  - 每个请求在 _meta 携带 protocolVersion / clientInfo
  - Streamable HTTP 要求 Mcp-Method / Mcp-Name 头做路由（SEP-2243）
  - tools/list 响应带 ttlMs / cacheScope，client 可缓存（SEP-2549）
  - server/discover 提供能力发现（替代握手）
  - 长任务用显式 job_id handle 由模型在调用间传递（stateless 应用状态）
  - 人工决策点返回 InputRequiredResult + requestState（SEP-2322 多轮请求）

传输：单文件 stdlib http.server，POST /mcp 收 JSON-RPC。
Hooks 由外层框架（pre_tool_guard / post_tool_audit）在调用链路注入，
本 server 在 tools/call 前后主动调用 hook 脚本，保证"Agent 无法绕开"。
"""

import os
import sys
import json
import uuid
import base64
import hashlib
import hmac
import socket
import threading
import subprocess
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, quote, unquote

sys.path.insert(0, str(Path(__file__).resolve().parent))

# 启动早期加载同目录 .env（MIMO_API_KEY 等），确保读取环境变量的模块
# （tts_client / asr_client）在导入前就能拿到配置。桌面端 local-server.js
# 拉起后端时只透传 process.env，不会注入 .env，故此处兜底加载。
try:
    _env_path = Path(__file__).resolve().parent / ".env"
    if _env_path.exists():
        with open(_env_path, "r", encoding="utf-8") as _fh:
            for _line in _fh:
                _line = _line.strip()
                if not _line or _line.startswith("#") or "=" not in _line:
                    continue
                _k, _v = _line.split("=", 1)
                _k, _v = _k.strip(), _v.strip().strip('"').strip("'")
                if _k and _k not in os.environ:
                    os.environ[_k] = _v
except Exception:
    pass

# ---------------------------------------------------------------------------
# 桌面端配置：用户自选的输出目录持久化在 ~/.video-uniqueness/config.json。
# 必须在 import pipeline 之前读取并写入 VU_OUTPUT，让 pipeline.OUTPUT_DIR
# 落到用户选的位置（打包成 exe 后 PROJECT_DIR/output 会权限失败或丢失）。
# ---------------------------------------------------------------------------
_CONFIG_PATH = Path.home() / ".video-uniqueness" / "config.json"


def _load_config():
    try:
        if _CONFIG_PATH.exists():
            with open(_CONFIG_PATH, "r", encoding="utf-8") as _cf:
                _c = json.load(_cf)
            return _c if isinstance(_c, dict) else {}
    except Exception:
        pass
    return {}


def _save_config(cfg):
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _tmp = _CONFIG_PATH.with_suffix(".json.tmp")
    with open(_tmp, "w", encoding="utf-8") as _cf:
        json.dump(cfg, _cf, ensure_ascii=False, indent=2)
    _tmp.replace(_CONFIG_PATH)


_cfg_startup = _load_config()
_od = _cfg_startup.get("output_dir")
if _od and isinstance(_od, str) and _od.strip() and "VU_OUTPUT" not in os.environ:
    os.environ["VU_OUTPUT"] = _od.strip()


def _default_output_root():
    return Path(os.environ.get("VU_OUTPUT") or (Path.home() / "Videos" / "视频去重产物")).expanduser().resolve()


def _configured_root():
    cfg = _load_config()
    raw = cfg.get("output_dir")
    if isinstance(raw, str) and raw.strip():
        return Path(raw).expanduser().resolve()
    return _default_output_root()


def _output_dir_for(kind, cfg=None):
    """返回去重/裂变各自的输出目录；兼容旧 output_dir 作为根目录。"""
    cfg = cfg if isinstance(cfg, dict) else _load_config()
    key = "fission_output_dir" if kind == "裂变" else "dedup_output_dir"
    raw = cfg.get(key)
    if isinstance(raw, str) and raw.strip():
        return Path(raw).expanduser().resolve()
    return (_configured_root() / kind).resolve()


def _safe_component(text, fallback="素材"):
    text = Path(str(text or fallback)).stem.strip() or fallback
    bad = '<>:"/\\|?*\r\n\t'
    cleaned = ''.join('_' if ch in bad or ord(ch) < 32 else ch for ch in text)
    cleaned = ' '.join(cleaned.split()).strip(' .')
    return (cleaned or fallback)[:60]


def _asset_workspace_dir(src, root_dir):
    """按原视频名+短ID生成稳定素材工作区目录，避免多素材产物混在同一层。"""
    src_path = P._resolve_safe(src, P.VIDEO_DIR, must_exist=True)
    stem = _safe_component(src_path.stem)
    try:
        digest = P.md5_of(src_path)[:8]
    except Exception:
        st = src_path.stat()
        digest = hashlib.md5(f"{src_path.name}:{st.st_size}:{int(st.st_mtime)}".encode("utf-8")).hexdigest()[:8]
    return (Path(root_dir).resolve() / f"{stem}__{digest}").resolve()


def _validate_existing_dir(value, field_name="dir"):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} 必须是非空字符串")
    p = Path(value).expanduser().resolve()
    if not p.exists() or not p.is_dir():
        raise ValueError("目录不存在或不是文件夹：" + str(p))
    return p

import pipeline as P  # noqa: E402

PROTOCOL_VERSION = "2026-07-28"
SERVER_INFO = {"name": "video-dedup-station", "version": "1.0.0", "title": "视频去重数字员工"}

_STATION = Path(__file__).resolve().parent.parent
_HOOKS = _STATION / "hooks"
_LOGS = _STATION / "logs"
_WEB = _STATION / "web"
_WEB_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}

# 长任务 handle 存储（stateless 协议 + 显式 handle 应用状态）
# key: job_id -> {status, result, ...}。文件持久化，任意进程可读。
_JOBS_FILE = _LOGS / "jobs.json"

# requestState 是客户端可见的无状态确认句柄。进程内随机密钥用于给
# name + arguments 签名，防止第二阶段请求替换用户已确认的参数。
_REQUEST_STATE_KEY = os.urandom(32)

# 当前进程内正在运行的批量任务。仅保存不可序列化的取消令牌，生命周期随请求结束；
# job handle 仍按既有方式持久化到 jobs.json。
_ACTIVE_FISSION_LOCK = threading.Lock()
_ACTIVE_FISSION = {}

# 用户自选输出目录的切换锁：ThreadingHTTPServer 下保证「切换 OUTPUT_DIR → 调用管线」
# 这一段的原子性，避免并发请求把产物写错目录。
_OUTPUT_DIR_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# job handle 持久化（显式 handle 模式，替代协议 session）
# ---------------------------------------------------------------------------
def _load_jobs():
    if _JOBS_FILE.exists():
        try:
            return json.loads(_JOBS_FILE.read_text("utf-8"))
        except Exception:
            return {}
    return {}


def _save_jobs(jobs):
    _LOGS.mkdir(parents=True, exist_ok=True)
    _JOBS_FILE.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), "utf-8")


def _new_job(kind, meta):
    jobs = _load_jobs()
    jid = "job_" + uuid.uuid4().hex[:12]
    jobs[jid] = {"job_id": jid, "kind": kind, "status": "completed", "meta": meta}
    _save_jobs(jobs)
    return jid


def _request_state(name, args):
    """生成绑定当前工具名和参数的无状态确认令牌。"""
    payload = json.dumps(
        {"name": name, "arguments": args},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    body = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    signature = hmac.new(_REQUEST_STATE_KEY, body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def _valid_request_state(state, name, args):
    """验证 requestState 未被篡改，且仍绑定当前 name + arguments。"""
    if not isinstance(state, str) or "." not in state:
        return False
    body, signature = state.rsplit(".", 1)
    expected = hmac.new(_REQUEST_STATE_KEY, body.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return False
    try:
        padded = body + "=" * (-len(body) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except Exception:
        return False
    return decoded == {"name": name, "arguments": args}


# ---------------------------------------------------------------------------
# 工具定义（tools/list 返回；带 ttlMs / cacheScope 供缓存）
# ---------------------------------------------------------------------------
TOOLS = [
    {
        "name": "list_assets",
        "description": "列出工程根 input/ 目录下可处理的视频素材（名称/大小）。",
        "inputSchema": {"type": "object", "properties": {}},
        "_tier": "audit",
    },
    {
        "name": "list_outputs",
        "description": "列出 output/ 目录下已生成的去重产物（名称/大小/时间），用于恢复丢失的下载链接。",
        "inputSchema": {"type": "object", "properties": {}},
        "_tier": "audit",
    },
    {
        "name": "probe_video",
        "description": "读取视频关键信息（分辨率/帧率/编码/时长/MD5）。去重前【必须】先探测。",
        "inputSchema": {
            "type": "object",
            "properties": {"src": {"type": "string", "description": "文件名或绝对路径"}},
            "required": ["src"],
        },
        "_tier": "audit",
    },
    {
        "name": "dedup_video",
        "description": "对单个视频去重（画面微调+微旋转+帧率+降噪+码率），保持分辨率，改变MD5。调用前须先 probe_video。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "src": {"type": "string", "description": "文件名或绝对路径"},
                "params": {"type": "object", "description": "可选：覆盖默认参数（brightness/contrast/saturation/rotate_deg/fps_range/bitrate_mul/bitrate_kbps/denoise）"},
                "out_name": {"type": "string", "description": "可选：输出文件名"},
                "level": {"type": "string", "enum": ["light", "medium", "heavy"], "description": "强度档：light=轻微、medium=中等（默认）、heavy=强烈；控制 crop/speed/trim 幅度"},
                "dimensions": {"type": "object", "description": "维度开关：picture/rotate/crop/flip/speed/trim 六个布尔；flip 默认 false，开了必传 flip_mode"},
                "flip_mode": {"type": "string", "enum": ["h", "v", "90"], "description": "翻转方向：h=水平、v=垂直、90=转置；仅 flip=true 时用"},
                "seed": {"type": "integer", "description": "随机种子；缺省随机回填"},
                "skip_phash": {"type": "boolean", "description": "跳过 pHash 自检（省 3-5 分钟 CPU 时间，仅保留 MD5+分辨率+时长校验）。默认 false"},
                "tts_text": {"type": "string", "description": "TTS 配音文案；不为空时启用 MiMo TTS 替换原音轨（手动模式）"},
                "tts_voice": {"type": "string", "description": "TTS 配音人声，默认冰糖"},
                "tts_speed": {"type": "number", "description": "TTS 语速，默认 1.0"},
                "rewrite_template": {"type": "string", "description": "元宝改写模板；非空时走「改写→配音」自动链路（自动模式下与 tts_text 互斥，tts_text 优先）"},
                "rewrite_topic": {"type": "string", "description": "改写主题/方向提示（可选），随模板一同发给元宝"},
                "rewrite_frames": {"type": "integer", "description": "改写抽帧数（默认 5），用于给元宝提供画面上下文"},
                "output_dir": {"type": "string", "description": "可选：产物输出目录（绝对路径）；不传则使用用户配置/默认目录（~/Videos/视频去重产物）。传入后立即生效，无需重启。"},
            },
            "required": ["src"],
        },
        "_tier": "audit",
    },
    {
        "name": "batch_fission",
        # 注意：批量裂变不支持 TTS 配音。tts_text/tts_voice/tts_speed 仅 dedup_video 有，
        # fission 的 inputSchema 未透传这些参数，调用链也不会触发配音（刻意保持简单、可并行、无云依赖）。
        "description": "裂变：同一素材生成 count 个不同参数的变体（每个 MD5 互不相同）。注意：暂不支持 TTS 配音（配音仅单条去重支持）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "src": {"type": "string"},
                "count": {"type": "integer", "description": "变体数量 1-20"},
                "params": {"type": "object"},
                "level": {"type": "string", "enum": ["light", "medium", "heavy"], "description": "强度档：light=轻微、medium=中等、heavy=强烈；控制 crop/speed/trim 幅度"},
                "dimensions": {"type": "object", "description": "维度开关：picture/rotate/crop/flip/speed/trim 六个布尔；flip 默认 false，开了必传 flip_mode"},
                "flip_mode": {"type": "string", "enum": ["h", "v", "90"], "description": "翻转方向：h=水平、v=垂直、90=转置；仅 flip=true 时用"},
                "task_id": {"type": "string", "description": "可选：客户端生成的批量任务句柄，用于运行中取消"},
            },
            "required": ["src", "count"],
        },
        "_tier": "warned",
    },
    {
        "name": "list_watermark_templates",
        "description": "列出可用平台水印模板（抖音/腾讯/西瓜等）。",
        "inputSchema": {"type": "object", "properties": {}},
        "_tier": "audit",
    },
    {
        "name": "remove_watermark",
        "description": "按平台模板坐标用 delogo 去除水印。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "src": {"type": "string"},
                "platform": {"type": "string", "description": "平台模板名（见 list_watermark_templates）"},
                "out_name": {"type": "string"},
            },
            "required": ["src", "platform"],
        },
        "_tier": "warned",
    },
    {
        "name": "extract_copy_context",
        "description": "提取视频改写上下文：ASR 音频识别 + 关键帧 JPEG(base64)。返回 {raw_text, source, duration, max_chars, frames_b64}，供元宝/DeepSeek 改写文案使用。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "src": {"type": "string", "description": "文件名或绝对路径"},
                "n_frames": {"type": "integer", "description": "抽帧数（默认 5）"},
            },
            "required": ["src"],
        },
        "_tier": "audit",
    },
    {
        "name": "get_job",
        "description": "按 job_id 查询任务 handle 的状态与结果（显式 handle 模式）。",
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
        "_tier": "audit",
    },
    {
        "name": "delete_output",
        "description": "删除 output/ 下的产出文件。【不可逆】，受 hook 硬拦截。",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "output/ 下的文件名"}},
            "required": ["name"],
        },
        "_tier": "blocked",
    },
]

TOOLS_BY_NAME = {t["name"]: t for t in TOOLS}


# ---------------------------------------------------------------------------
# Hook 调用（Agent 无法绕开：tools/call 路径独占，hook 是唯一道闸）
# ---------------------------------------------------------------------------
def _run_hook(script, payload):
    """执行 hook；PreToolUse 故障拒绝执行，PostToolUse 故障仅报告。"""
    fail_closed = script == "pre_tool_guard.py"

    def failed(reason):
        return {
            "continue": not fail_closed,
            "permissionDecision": "deny" if fail_closed else "allow",
            "reason": reason,
            "hook_error": reason,
        }

    path = _HOOKS / script
    if not path.exists():
        return failed(f"Hook 缺失: {path}")
    try:
        proc = subprocess.run(
            [sys.executable, str(path)],
            input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
        )
        if proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", "replace").strip()
            return failed(f"Hook 退出码 {proc.returncode}: {stderr or script}")
        out = proc.stdout.decode("utf-8", "replace").strip()
        if not out:
            return failed(f"Hook 无结构化输出: {script}")
        result = json.loads(out)
        if not isinstance(result, dict) or not isinstance(result.get("continue"), bool):
            return failed(f"Hook 输出无效: {script}")
        return result
    except Exception as e:
        return failed(f"Hook 执行异常: {e}")


# ---------------------------------------------------------------------------
# 工具执行
# ---------------------------------------------------------------------------
def _build_copy_context(src, n_frames=5):
    """抽取视频改写上下文：ASR 文字 + 关键帧（文件路径 + base64）。供元宝改写使用。

    与 extract_copy_context 工具共用同一套抽取逻辑，避免重复代码。
    frames_paths 为磁盘上的帧图路径，可直接传给 yuanbao_client.vision_and_rewrite。
    """
    import base64 as _b64
    import json as _json
    import logging
    from pathlib import Path as _Path
    import copy_rewriter as CR
    import asr_client as AC
    from pipeline import VIDEO_DIR, FFMPEG, FFPROBE, _resolve_safe

    # src 走白名单校验（与 dedup_video / probe_video 一致）
    try:
        video_path = str(_resolve_safe(src, VIDEO_DIR, must_exist=True))
    except Exception as e:
        raise P.PipelineError(f"src 不在 VIDEO_DIR 内或不存在: {e}")

    # 1) 时长（用 ffprobe 单独走一次）
    try:
        probe = subprocess.run(
            [str(FFPROBE), "-v", "error", "-show_entries", "format=duration",
             "-of", "json", video_path],
            capture_output=True, text=True, timeout=15,
        )
        duration = float(_json.loads(probe.stdout or "{}").get("format", {}).get("duration", 0) or 0)
    except Exception:
        duration = 0.0

    # 2) ASR（失败返回空字符串，不阻塞帧图）
    raw_text = ""
    source = ""
    try:
        raw_text = AC.transcribe_video(video_path, str(FFMPEG)) or ""
        source = "asr" if raw_text else ""
    except Exception as e:
        logging.getLogger("vu").warning(f"ASR 失败: {e}")

    # 3) 抽帧 → 文件路径 + base64
    frames_paths = []
    frames_b64 = []
    try:
        frame_paths = CR._extract_frames(_Path(video_path), FFMPEG, n=int(n_frames))
        for fp in frame_paths:
            frames_paths.append(str(fp))
            try:
                with open(fp, "rb") as fh:
                    frames_b64.append(_b64.b64encode(fh.read()).decode("ascii"))
            except Exception:
                continue
    except Exception:
        pass

    # 4) 最大字数上限：让元宝改写时按视频时长控制篇幅。
    #    max_chars = ceil(duration × R × 安全余量)。R = MiMo TTS 实际语速（字/秒），
    #    产物时长 = 配音时长（基于文案长度，混音用 -shortest 把视频截到配音长度），
    #    故要让「配音时长 ≈ 视频时长」：向「略短」偏置（×0.95）只裁尾部画面、旁白完整。
    #    ← 校准后改 TTS_CHARS_PER_SEC（建议 4~6，用 calibrate_tts_rate.py 实测）
    TTS_CHARS_PER_SEC = 4.5      # ← 校准后改这里（建议 4~6）
    TTS_SAFE_MARGIN = 0.95
    max_chars = max(30, int(duration * TTS_CHARS_PER_SEC * TTS_SAFE_MARGIN)) if duration > 0 else 30

    return {
        "raw_text": raw_text,
        "source": source,
        "duration": duration,
        "max_chars": max_chars,
        "frames_paths": frames_paths,
        "frames_b64": frames_b64,
    }


def _rel_to_output(path, root_dir):
    try:
        return str(Path(path).resolve().relative_to(Path(root_dir).resolve())).replace("\\", "/")
    except Exception:
        return Path(path).name


def _exec_tool(name, args):
    if name == "list_assets":
        return {"assets": P.list_assets()}
    if name == "list_outputs":
        return {"outputs": P.list_outputs()}
    if name == "probe_video":
        return P.probe_video(args["src"])
    if name == "extract_copy_context":
        # 给改写流程用的上下文：ASR 文字 + 帧图 base64（与 dedup_video 改写路径共用）
        n_frames = int(args.get("n_frames") or 5)
        ctx = _build_copy_context(args["src"], n_frames)
        return {
            "raw_text": ctx["raw_text"],
            "source": ctx["source"],
            "duration": ctx["duration"],
            "max_chars": ctx["max_chars"],
            "frames_b64": ctx["frames_b64"],
        }
    if name == "dedup_video":
        # 🆕 改写→配音：用户选「元宝改写」模式（rewrite_template 非空、且未手动填文案）时，
        # 先用 ASR+抽帧构造上下文，调 vision_and_rewrite 生成旁白文案，再喂给 TTS 混音。
        # 手动填了文案（tts_text）时优先用手动文案，跳过改写。
        tts_text = args.get("tts_text")
        rewrite_template = args.get("rewrite_template")
        rewrite_meta = {"requested": False}
        if rewrite_template and not tts_text:
            rewrite_meta["requested"] = True
            try:
                import yuanbao_client as YB
                n_frames = int(args.get("rewrite_frames") or 5)
                ctx = _build_copy_context(args["src"], n_frames)
                rw = YB.vision_and_rewrite(
                    frames=ctx["frames_paths"],
                    raw_text=ctx["raw_text"],
                    rewrite_template=rewrite_template,
                    max_chars=ctx["max_chars"],
                    topic=args.get("rewrite_topic"),
                    reuse_edge=True,
                )
                if rw.get("rewritten"):
                    tts_text = rw["rewritten"]
                    rewrite_meta["source"] = ctx["source"] or "vision"
                    rewrite_meta["rewritten_len"] = len(tts_text)
                else:
                    rewrite_meta["error"] = rw.get("error") or "元宝未在超时内返回改写结果"
                    logging.getLogger("vu").warning("改写未返回文案: %s", rewrite_meta["error"])
            except Exception as e:
                rewrite_meta["error"] = str(e)[:300]
                logging.getLogger("vu").warning(f"改写异常: {e}")

        # 🆕 兜底清洗：无论手动文案还是元宝改写，都剔除「注：文案共N字…」等会被 TTS 念出来的元信息
        if tts_text:
            try:
                import copy_rewriter as CR
                tts_text = CR._strip_tts_meta(tts_text)
            except Exception:
                pass

        # 🆕 按请求/配置指定去重输出目录（用户可单独配置「去重产物文件夹」）
        _od_arg = args.get("output_dir")
        _od_path = None
        if _od_arg:
            _od_path = _validate_existing_dir(_od_arg, "output_dir")
        else:
            _od_path = _output_dir_for("去重")
            _od_path.mkdir(parents=True, exist_ok=True)
        _workspace_dir = _asset_workspace_dir(args["src"], _od_path)
        _workspace_dir.mkdir(parents=True, exist_ok=True)
        with _OUTPUT_DIR_LOCK:
            P.OUTPUT_DIR = _workspace_dir
            os.environ["VU_OUTPUT"] = str(_workspace_dir)
            r = P.dedup_video(
                args["src"],
                params=args.get("params"),
                out_name=args.get("out_name"),
                seed=args.get("seed"),
                level=args.get("level"),
                dimensions=args.get("dimensions"),
                flip_mode=args.get("flip_mode"),
                trim_phase=args.get("trim_phase"),
                tts_text=tts_text,
                tts_voice=args.get("tts_voice"),
                tts_speed=args.get("tts_speed"),
                skip_phash=args.get("skip_phash", False),
                subdir="",
            )
        r["output_path"] = _rel_to_output(r.get("output_path"), _od_path)
        r["applied_params"]["rewrite_requested"] = rewrite_meta["requested"]
        if rewrite_meta.get("error"):
            r["applied_params"]["rewrite_error"] = rewrite_meta["error"]
        if rewrite_meta.get("source"):
            r["applied_params"]["rewrite_source"] = rewrite_meta["source"]
        r["rewrite_meta"] = rewrite_meta
        r["job_id"] = _new_job("dedup", {"src": r["src"]["name"], "output": r["output_path"]})
        return r
    if name == "batch_fission":
        # count 默认值由 rules.json auto_fill 提供 (5)；不写 fallback 3。
        # task_id 是客户端可见的运行句柄，仅用于本进程内取消，不写入 shell 参数。
        task_id = args.get("task_id") or ("fission_" + uuid.uuid4().hex[:16])
        if not isinstance(task_id, str) or not task_id.startswith("fission_") or len(task_id) > 64:
            raise P.PipelineError("批量任务 task_id 无效。")
        token = P.CancellationToken()
        with _ACTIVE_FISSION_LOCK:
            if task_id in _ACTIVE_FISSION:
                raise P.PipelineError(f"批量任务已存在: {task_id}")
            _ACTIVE_FISSION[task_id] = token
        try:
            with _OUTPUT_DIR_LOCK:
                _fission_output_dir = _output_dir_for("裂变")
                _fission_output_dir.mkdir(parents=True, exist_ok=True)
                _workspace_dir = _asset_workspace_dir(args["src"], _fission_output_dir)
                _workspace_dir.mkdir(parents=True, exist_ok=True)
                P.OUTPUT_DIR = _workspace_dir
                os.environ["VU_OUTPUT"] = str(_workspace_dir)
                r = P.batch_fission(
                    args["src"],
                    count=args.get("count"),
                    params=args.get("params"),
                    level=args.get("level"),
                    dimensions=args.get("dimensions"),
                    flip_mode=args.get("flip_mode"),
                    cancel_token=token,
                    subdir="",
                )
        finally:
            with _ACTIVE_FISSION_LOCK:
                _ACTIVE_FISSION.pop(task_id, None)
        for _v in r.get("variants") or []:
            if isinstance(_v, dict) and _v.get("output_path"):
                _v["output_path"] = _rel_to_output(_v.get("output_path"), _fission_output_dir)
        r["task_id"] = task_id
        r["job_id"] = _new_job("fission", {
            "src": r["src"], "count": r["count"],
            "requested_count": r.get("requested_count"),
            "cancelled": r.get("cancelled", False),
        })
        return r
    if name == "list_watermark_templates":
        return {"templates": P.list_watermark_templates()}
    if name == "remove_watermark":
        return P.remove_watermark(args["src"], args["platform"], out_name=args.get("out_name"))
    if name == "get_job":
        jobs = _load_jobs()
        j = jobs.get(args["job_id"])
        if not j:
            raise P.PipelineError(f"job 不存在: {args['job_id']}")
        return j
    if name == "delete_output":
        # 🔒 F3.3：用 _resolve_safe 校验 name 必须落在 OUTPUT_DIR 白名单内，
        # 否则攻击者可用 "../../../etc/passwd" 跨出 OUTPUT_DIR（Windows 上
        # Path / "../.." 经 target.exists()/target.unlink() 会让 OS 解析 ..）。
        # 该工具已被 tier=blocked 拦截，但路径遍历 bug 本身仍存在，
        # 此处做 defense-in-depth。
        target = P._resolve_safe(args["name"], P.OUTPUT_DIR, must_exist=True)
        target.unlink()
        return {"deleted": str(target)}
    raise P.PipelineError(f"未知工具: {name}")


# ---------------------------------------------------------------------------
# JSON-RPC 方法分发（无状态：无 initialize/initialized）
# ---------------------------------------------------------------------------
def handle_rpc(req, headers):
    method = req.get("method")
    rpc_id = req.get("id")
    params = req.get("params", {}) or {}

    # server/discover：替代握手的能力发现
    if method == "server/discover":
        return _ok(rpc_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "serverInfo": SERVER_INFO,
            "capabilities": {"tools": {"listChanged": False}},
        })

    if method == "tools/list":
        public = [{k: v for k, v in t.items() if not k.startswith("_")} for t in TOOLS]
        # SEP-2549：list 结果带缓存元数据
        return _ok(rpc_id, {"tools": public, "ttlMs": 300000, "cacheScope": "shared"})

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments", {}) or {}
        tool = TOOLS_BY_NAME.get(name)
        if not tool:
            return _err(rpc_id, -32602, f"未知工具: {name}")

        # --- 人工决策点：SEP-2322 多轮请求 ---
        # 若客户端未带确认，warned/blocked 工具返回 InputRequiredResult
        input_responses = params.get("inputResponses")
        if tool["_tier"] in ("warned", "blocked") and not input_responses:
            state = _request_state(name, args)
            tier_msg = "⚠️ 高风险操作" if tool["_tier"] == "warned" else "🛑 不可逆删除"
            return _ok(rpc_id, {
                "resultType": "input_required",
                "inputRequests": {
                    "confirm": {
                        "type": "elicitation",
                        "message": f"{tier_msg}：即将执行 {name}，是否继续？",
                        "schema": {"type": "boolean"},
                    }
                },
                "requestState": state,
            })
        # 若带了确认但为否，中止；确认执行必须携带与原调用绑定的 requestState。
        if input_responses and input_responses.get("confirm") is False:
            return _ok(rpc_id, _content(f"用户取消了 {name}。"))
        if tool["_tier"] in ("warned", "blocked"):
            if not isinstance(input_responses, dict) or input_responses.get("confirm") is not True:
                return _ok(rpc_id, _content(
                    "❌ 人工确认响应无效：confirm 必须为 true。", is_error=True))
            if not _valid_request_state(params.get("requestState"), name, args):
                return _ok(rpc_id, _content(
                    "❌ 人工确认状态无效或与当前参数不匹配，请重新发起操作并确认。",
                    is_error=True,
                ))

        # F3.2 镜像：tier 求值器只读 body.get(field) 顶层；dimensions.flip 嵌套
        # 不被 rules.json tier2（field=flip, value=true）命中。必须在这里镜像到
        # args["flip"] 顶层，hook 跑 check_body 时才能拦下「flip=true 缺 flip_mode」。
        # 必须放在 _run_hook 之前，否则 hook 已错过 tier 求值。
        if name in ("dedup_video", "batch_fission"):
            dims = args.get("dimensions")
            if isinstance(dims, dict) and dims.get("flip"):
                args = dict(args)
                args["flip"] = True

        # --- PreToolUse hook：拦截 + 校验 + 补全 ---
        pre = _run_hook("pre_tool_guard.py", {
            "hook_event": "PreToolUse", "tool_name": name, "tool_input": args, "tier": tool["_tier"],
        })
        if not pre.get("continue", True):
            return _ok(rpc_id, _content(f"❌ 被 Hook 拦截：{pre.get('reason', '未通过安全校验')}"))
        if pre.get("modifiedInput"):
            args = pre["modifiedInput"]

        # --- 执行 ---
        try:
            result = _exec_tool(name, args)
        except Exception as e:
            _run_hook("post_tool_audit.py", {
                "hook_event": "PostToolUse", "tool_name": name, "tool_input": args,
                "status": "error", "error": str(e),
            })
            return _ok(rpc_id, _content(f"❌ 执行失败：{e}", is_error=True))

        # --- PostToolUse hook：审计 ---
        _run_hook("post_tool_audit.py", {
            "hook_event": "PostToolUse", "tool_name": name, "tool_input": args,
            "status": "ok", "result_summary": _summary(name, result),
        })

        return _ok(rpc_id, _content(json.dumps(result, ensure_ascii=False, indent=2)))

    return _err(rpc_id, -32601, f"未知方法: {method}")


def _allowed_host(host):
    """允许白名单内的 Host 访问（含本地 IP 与外网服务器 IP）。"""
    if not isinstance(host, str) or not host:
        return False
    value = host.strip().lower()
    # 去掉端口
    hostname = value.split(":")[0] if ":" in value else value
    allowed = os.environ.get("VU_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")
    allowed = [h.strip().lower() for h in allowed if h.strip()]
    return hostname in allowed


def _allowed_origin(origin):
    """允许非浏览器请求、file:// 的 null Origin、本机网页与浏览器扩展。"""
    if origin in (None, "", "null"):
        return True
    try:
        parsed = urlsplit(origin)
    except Exception:
        return False
    # 允许浏览器扩展 chrome-extension:// / moz-extension://（不查 hostname）
    if parsed.scheme in ("chrome-extension", "moz-extension"):
        return True
    return parsed.scheme in ("http", "https") and parsed.hostname in ("127.0.0.1", "localhost")


def _cancel_fission(task_id):
    """向当前进程内的批量任务发送取消信号。"""
    if not isinstance(task_id, str) or not task_id.startswith("fission_"):
        raise ValueError("批量任务 task_id 无效")
    with _ACTIVE_FISSION_LOCK:
        token = _ACTIVE_FISSION.get(task_id)
    if token is None:
        return False
    token.cancel()
    return True


def _open_output_folder(filename=None, subdir=None):
    """打开去重/裂变各自配置的输出目录；Windows 上可安全选中目录内的指定文件。"""
    kind = "裂变" if subdir == "裂变" else "去重"
    output_dir = _output_dir_for(kind).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    target = None
    if filename:
        if not isinstance(filename, str) or not filename.strip() or Path(filename).is_absolute():
            raise ValueError("产物文件名无效")
        candidate = (output_dir / filename).resolve()
        try:
            candidate.relative_to(output_dir)
        except ValueError as exc:
            raise ValueError("产物文件必须位于产物目录内") from exc
        if not candidate.is_file():
            raise FileNotFoundError(f"产物文件不存在: {filename}")
        target = candidate

    if os.name == "nt" and target is not None:
        subprocess.Popen(["explorer.exe", "/select,", str(target)])
    elif os.name == "nt":
        os.startfile(str(output_dir))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(target)] if target else ["open", str(output_dir)])
    else:
        subprocess.Popen(["xdg-open", str(output_dir)])
    return output_dir, target


def _summary(name, result):
    if name == "dedup_video":
        checks = result.get("checks") or {}
        return {
            "output": result.get("output_path"),
            "checks": checks,
            "phash": checks.get("phash"),
            "phash_passed": bool(checks.get("phash", {}).get("passed")),
            "applied_level": (result.get("applied_params") or {}).get("level"),
        }
    if name == "batch_fission":
        wrapper = result.get("matrix") or {}
        inner = wrapper.get("matrix") if isinstance(wrapper, dict) else []
        off = []
        if isinstance(inner, list):
            for i, row in enumerate(inner):
                if not isinstance(row, list):
                    continue
                for j, v in enumerate(row):
                    if i == j or v is None:
                        continue
                    if isinstance(v, bool) or not isinstance(v, (int, float)):
                        continue
                    off.append(float(v))
        return {
            "count": result.get("count"),
            "all_unique": result.get("all_unique"),
            "all_pass": wrapper.get("all_pass") if isinstance(wrapper, dict) else None,
            "delivery_ready": result.get("delivery_ready"),
            "off_diagonal_mean": round(sum(off) / len(off), 3) if off else None,
            "off_diagonal_min": round(min(off), 3) if off else None,
        }
    return {"ok": True}


def _content(text, is_error=False):
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _ok(rpc_id, result):
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


def _err(rpc_id, code, message):
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}


# ---------------------------------------------------------------------------
# HTTP 传输：POST /mcp
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # 静默默认日志

    def _cors(self):
        origin = self.headers.get("Origin")
        if origin == "null":
            self.send_header("Access-Control-Allow-Origin", "null")
        elif origin and _allowed_origin(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, MCP-Protocol-Version, Mcp-Method, Mcp-Name")

    def _request_allowed(self):
        return (_allowed_host(self.headers.get("Host"))
                and _allowed_origin(self.headers.get("Origin")))

    def do_OPTIONS(self):
        if not self._request_allowed():
            self.send_response(403)
            self.end_headers()
            return
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        self._serve_web(head_only=False)

    def do_HEAD(self):
        self._serve_web(head_only=True)

    def _serve_web(self, head_only=False):
        if not self._request_allowed():
            self.send_response(403)
            self.end_headers()
            return
        path = urlsplit(self.path).path
        if path == "/favicon.ico":
            self.send_response(204)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        # /local/download/<filename> — 从去重/裂变各自配置的输出目录直供视频文件下载
        if path.startswith("/local/download/"):
            fname = unquote(path[len("/local/download/"):])
            query = urlsplit(self.path).query or ""
            subdir = None
            for part in query.split("&"):
                if part.startswith("subdir="):
                    subdir = unquote(part.split("=", 1)[1])
                    break
            kind = "裂变" if subdir == "裂变" else "去重"
            safe_base = _output_dir_for(kind).resolve()
            if not fname or Path(fname).is_absolute():
                self.send_response(400)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            fpath = (safe_base / fname).resolve()
            # 安全检查：允许素材工作区子目录，但必须位于对应产物根目录内
            try:
                fpath.relative_to(safe_base)
            except ValueError:
                self.send_response(400)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if not fpath.is_file():
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            content_type = "video/mp4" if fname.endswith(".mp4") else "application/octet-stream"
            try:
                file_size = fpath.stat().st_size
                fp = open(fpath, "rb")
            except OSError:
                self.send_response(500)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(fname, safe='')}")
            self.send_header("Content-Length", str(file_size))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if not head_only:
                # 流式分块发送：64KB/片，避免 100MB 一次性读进内存导致 RST + 慢
                try:
                    while True:
                        chunk = fp.read(65536)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # 客户端断连属正常，资源已在 finally 释放
                finally:
                    fp.close()
            return
        web_file = _WEB_FILES.get(path)
        if web_file is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        filename, content_type = web_file
        body = (_WEB / filename).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _handle_upload(self):
        """处理 /local/upload —— 直传视频文件到 input/ 目录（手动解析 multipart）。"""
        ct = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ct:
            self._respond_http(400, {"ok": False, "message": "需要 multipart/form-data"})
            return
        # 取 boundary
        boundary = None
        for part in ct.split(";"):
            part = part.strip()
            if part.lower().startswith("boundary="):
                boundary = part.split("=", 1)[1].strip().strip('"')
        if not boundary:
            self._respond_http(400, {"ok": False, "message": "缺少 boundary"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            # 手动解析 multipart
            b = boundary.encode("utf-8")
            parts = raw.split(b"--" + b)
            for part in parts:
                if not part.strip():
                    continue
                # 分离头/体
                header_end = part.find(b"\r\n\r\n")
                if header_end < 0:
                    continue
                headers_section = part[:header_end].decode("utf-8", errors="replace")
                body = part[header_end + 4:]
                # 去掉尾部 \r\n-- (最后一个 part 的结束标记)
                body = body.rstrip(b"\r\n--\r\n").rstrip(b"\r\n--")
                if b'filename="' in part[:header_end]:
                    # 取原始文件名
                    import re
                    m = re.search(r'filename="([^"]*)"', headers_section)
                    if not m:
                        raise ValueError("未找到文件名")
                    filename = Path(m.group(1)).name
                    dest = P.VIDEO_DIR / filename
                    # 清理旧上传（超过 1 小时）
                    now = time.time()
                    for f in P.VIDEO_DIR.glob("*"):
                        if f.is_file() and f.name != ".gitkeep" and now - f.stat().st_mtime > 3600:
                            try: f.unlink()
                            except Exception: pass
                    dest.write_bytes(body)
                    self._respond_http(200, {"ok": True, "name": filename, "size": dest.stat().st_size})
                    return
            raise ValueError("未找到上传文件")
        except Exception as e:
            self._respond_http(500, {"ok": False, "message": str(e)})

    def do_POST(self):
        if not self._request_allowed():
            self.send_response(403)
            self.end_headers()
            return
        if self.path.rstrip("/") == "/local/get-output-dir":
            try:
                _cfg = _load_config()
                _dedup_configured = bool(_cfg.get("dedup_output_dir"))
                _fission_configured = bool(_cfg.get("fission_output_dir"))
                self._respond_http(200, {
                    "ok": True,
                    "output_dir": str(P.OUTPUT_DIR.resolve()),
                    "dedup_output_dir": str(_output_dir_for("去重", _cfg)),
                    "fission_output_dir": str(_output_dir_for("裂变", _cfg)),
                    "dedup_configured": _dedup_configured,
                    "fission_configured": _fission_configured,
                    "configured": bool(_dedup_configured and _fission_configured),
                })
            except Exception as exc:
                self._respond_http(500, {"ok": False, "message": str(exc)})
            return
        if self.path.rstrip("/") == "/local/set-output-dir":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("请求体必须是 JSON 对象")
                d = payload.get("dir")
                kind = payload.get("kind")
                p = _validate_existing_dir(d, "dir")
                cfg = _load_config()
                if kind == "dedup":
                    cfg["dedup_output_dir"] = str(p)
                    msg = "去重产物文件夹已保存"
                elif kind == "fission":
                    cfg["fission_output_dir"] = str(p)
                    msg = "裂变产物文件夹已保存"
                else:
                    # 兼容旧调用：同时设置根目录与两个任务目录（子目录会自动创建）。
                    cfg["output_dir"] = str(p)
                    cfg["dedup_output_dir"] = str((p / "去重").resolve())
                    cfg["fission_output_dir"] = str((p / "裂变").resolve())
                    (p / "去重").mkdir(parents=True, exist_ok=True)
                    (p / "裂变").mkdir(parents=True, exist_ok=True)
                    msg = "产物根目录已保存"
                _save_config(cfg)
                self._respond_http(200, {
                    "ok": True,
                    "output_dir": str(p),
                    "dedup_output_dir": str(_output_dir_for("去重", cfg)),
                    "fission_output_dir": str(_output_dir_for("裂变", cfg)),
                    "message": msg,
                })
            except (ValueError, OSError) as exc:
                self._respond_http(400, {"ok": False, "message": str(exc)})
            except Exception as exc:
                self._respond_http(500, {"ok": False, "message": "无法保存配置：" + str(exc)})
            return
        if self.path.rstrip("/") in ("/local/open-output", "/local/cancel-fission"):
            endpoint = self.path.rstrip("/")
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("请求体必须是 JSON 对象")
                if endpoint == "/local/cancel-fission":
                    found = _cancel_fission(payload.get("task_id"))
                else:
                    output_dir, selected = _open_output_folder(payload.get("filename"), subdir=payload.get("subdir"))
            except (ValueError, FileNotFoundError) as exc:
                self._respond_http(400, {"ok": False, "message": str(exc)})
                return
            except Exception as exc:
                action = "取消批量裂变" if endpoint == "/local/cancel-fission" else "打开输出文件夹"
                self._respond_http(500, {"ok": False, "message": f"无法{action}: {exc}"})
                return
            if endpoint == "/local/cancel-fission":
                self._respond_http(200, {
                    "ok": True,
                    "found": found,
                    "message": "已发送取消请求" if found else "任务已结束或不存在",
                })
            else:
                self._respond_http(200, {
                    "ok": True,
                    "path": str(output_dir),
                    "selected": str(selected) if selected else None,
                })
            return
        if self.path.rstrip("/") == "/local/upload":
            self._handle_upload()
            return
        if self.path.rstrip("/") not in ("/mcp", ""):
            self.send_response(404)
            self._cors()
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            req = json.loads(raw.decode("utf-8"))
        except Exception:
            self._respond({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            return

        headers = {
            "protocol": self.headers.get("MCP-Protocol-Version", ""),
            "method": self.headers.get("Mcp-Method", ""),
            "name": self.headers.get("Mcp-Name", ""),
        }
        resp = handle_rpc(req, headers)
        self._respond(resp)

    def _respond_http(self, status, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _respond(self, obj):
        self._respond_http(200, obj)


def _port_in_use(host, port):
    """探测端口是否已被占用（防止起多个实例导致'脑裂'：
    多实例各读各的 audit.jsonl，强制走链判定会时好时坏）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def serve(host="127.0.0.1", port=8765):
    # 端口独占检测：已有实例在跑就明确报错退出，绝不叠加第二个实例
    if _port_in_use(host, port):
        print(
            f"[MCP] 端口 {host}:{port} 已被占用——可能已有一个 server 在跑。\n"
            f"      为避免多实例'脑裂'（强制走链判定失效），本次启动已中止。\n"
            f"      如需重启：先结束占用 {port} 的进程，再运行本脚本。",
            file=sys.stderr,
        )
        sys.exit(3)

    class _Server(ThreadingHTTPServer):
        allow_reuse_address = True   # Linux 安全：_port_in_use 已防脑裂，仅解决重启 TIME_WAIT

    srv = _Server((host, port), Handler)
    print(f"[MCP 2026-07-28] video-dedup-station 无状态服务已启动: http://{host}:{port}/mcp")
    srv.serve_forever()


if __name__ == "__main__":
    host = os.environ.get("VU_HOST", "127.0.0.1")
    port = int(os.environ.get("VU_PORT", "8765"))
    serve(host, port)
