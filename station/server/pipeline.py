# -*- coding: utf-8 -*-
"""
pipeline.py — 视频去重管线（复现 CRVideoMate 面板能力）

CRVideoMate.exe 是闭源 GUI，无命令行接口，无法被程序驱动。
本模块用其【同捆的 ffmpeg】复现去重管线，对应面板勾选项：
  - 画面调整：亮度 / 对比度 / 饱和度 随机微调（ffmpeg eq 滤镜）
  - 旋转微转：极小角度旋转 + 裁切补边（rotate 滤镜）
  - 帧率设置：改变 fps
  - 降噪：hqdn3d
  - 码率调整：-b:v 控制体积/清晰度
  - 加/去水印：delogo（去）/ overlay（加，用 watermarks/*.ini 模板坐标）
  - 裂变：同一素材生成多个随机参数变体

去重原理：改变 MD5 与视频特征（哈希），绕过平台重复检测；分辨率保持不变。
"""

import os
import sys
import json
import random
import hashlib
import subprocess
import configparser
from pathlib import Path

# 感知度量域（模块一）。同目录模块，保证 pytest / server 两种入口都能导入。
sys.path.insert(0, str(Path(__file__).resolve().parent))
import metrics as M  # noqa: E402

# ---------------------------------------------------------------------------
# 路径锚定：本文件位于 video-uniqueness/station/server/pipeline.py（自包含版）
# ffmpeg / watermarks 已拷进工程内 vendor/，素材放 assets/，彻底脱离中文路径
# 均可用环境变量覆盖（VU_FFMPEG / VU_FFPROBE / VU_WATERMARKS / VU_ASSETS / VU_OUTPUT）
# 全部为 __file__ 相对锚定，push 到 GitHub 后 clone 即用，无任何硬编码绝对路径
# ---------------------------------------------------------------------------
_SERVER_DIR = Path(__file__).resolve().parent
STATION_DIR = _SERVER_DIR.parent                       # video-uniqueness/station/
PROJECT_DIR = STATION_DIR.parent                       # video-uniqueness/
_VENDOR = STATION_DIR / "vendor"                       # 自包含依赖目录

FFMPEG = Path(os.environ.get("VU_FFMPEG",  _VENDOR / "ffmpeg" / "ffmpeg.exe"))
FFPROBE = Path(os.environ.get("VU_FFPROBE", _VENDOR / "ffmpeg" / "ffprobe.exe"))
WATERMARKS_DIR = Path(os.environ.get("VU_WATERMARKS", _VENDOR / "watermarks"))

VIDEO_DIR = Path(os.environ.get("VU_ASSETS", STATION_DIR / "assets"))   # 素材目录
# 输出目录：提到工程根 video-uniqueness/output（相对锚定 = PROJECT_DIR/output）
OUTPUT_DIR = Path(os.environ.get("VU_OUTPUT", PROJECT_DIR / "output"))


class PipelineError(Exception):
    """管线执行错误（ffmpeg 失败、文件不存在等）。"""


# ---------------------------------------------------------------------------
# 强度档 → 各维度参数区间（DD §0.1 / PRD D-02、D-03）
# Q-02 待实测校验：crop/trim 百分比为工程推荐值，校验后仅改此常量表，不改结构。
# ---------------------------------------------------------------------------
LEVELS = {
    "light":  {"crop": 0.02, "speed": 0.03, "trim": (0.3, 0.5)},
    "medium": {"crop": 0.05, "speed": 0.05, "trim": (0.5, 1.0)},
    "heavy":  {"crop": 0.08, "speed": 0.10, "trim": (1.0, 1.5)},
}

# 最短时长保护（DD §0.3 / PRD §6、§12）
MIN_DURATION_HARD = 5.0      # 成片硬下限，任何情况下不得低于
MIN_DURATION_TRIM = 7.0      # 成片 <7s 禁再截
MAX_TRIM_RATIO = 0.10        # 掐头去尾总量 ≤ 原时长 10%

# atempo 单实例硬约束（ffmpeg WSOLA）
ATEMPO_MIN, ATEMPO_MAX = 0.5, 2.0

# 维度开关缺省（DD §2.3）：高破坏维度 flip 默认关
DIMENSION_DEFAULTS = {
    "picture": True, "rotate": True, "crop": True,
    "flip": False, "speed": True, "trim": True,
}


def _resolve_safe(path, base_dir, must_exist=True):
    """🔒 路径穿越防护（DD §0.4）：把 path 规范化后校验它落在 base_dir 白名单前缀内。

    - path 为相对名时，视为 base_dir 下的文件。
    - 规范化（resolve）后若不在 base_dir 子树内 → 越界，抛 PipelineError。
    - must_exist=True 时同时要求文件存在。
    返回规范化后的绝对 Path。
    """
    base = Path(base_dir).resolve()
    p = Path(path)
    if not p.is_absolute():
        p = base / p
    p = p.resolve()
    # 前缀校验：p 必须等于 base 或在其子树内
    try:
        p.relative_to(base)
    except ValueError:
        raise PipelineError(f"路径越界（不在白名单目录 {base} 内）: {p}")
    if must_exist and not p.exists():
        raise PipelineError(f"文件不存在: {p}")
    return p


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def _run(cmd, timeout=600):
    """执行命令，返回 (returncode, stdout, stderr)。二进制安全，UTF-8 解码。"""
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    out = proc.stdout.decode("utf-8", "replace")
    err = proc.stderr.decode("utf-8", "replace")
    return proc.returncode, out, err


def md5_of(path):
    """计算文件 MD5。"""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_env():
    """检查 ffmpeg/ffprobe 是否就位，返回诊断字典。"""
    return {
        "ffmpeg": FFMPEG.exists(),
        "ffprobe": FFPROBE.exists(),
        "ffmpeg_path": str(FFMPEG),
        "video_dir": str(VIDEO_DIR),
        "video_dir_exists": VIDEO_DIR.exists(),
        "output_dir": str(OUTPUT_DIR),
        "watermarks_dir_exists": WATERMARKS_DIR.exists(),
    }


def list_assets():
    """列出 video/ 目录下可处理的视频素材。"""
    if not VIDEO_DIR.exists():
        return []
    exts = {".mp4", ".mov", ".avi", ".mkv", ".flv", ".wmv", ".ts", ".webm"}
    items = []
    for p in sorted(VIDEO_DIR.iterdir()):
        if p.is_file() and p.suffix.lower() in exts:
            items.append({
                "name": p.name,
                "path": str(p),
                "size": p.stat().st_size,
                "size_mb": round(p.stat().st_size / 1024 / 1024, 2),
            })
    return items


def probe_video(path):
    """用 ffprobe 读取视频关键信息。🔒 F3.3：src 必须落在 VIDEO_DIR 白名单内。"""
    p = _resolve_safe(path, VIDEO_DIR, must_exist=True)

    if not FFPROBE.exists():
        raise PipelineError(f"ffprobe 未找到: {FFPROBE}")

    cmd = [
        str(FFPROBE), "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", str(p),
    ]
    rc, out, err = _run(cmd, timeout=60)
    if rc != 0:
        raise PipelineError(f"ffprobe 失败: {err}")

    data = json.loads(out)
    fmt = data.get("format", {})
    vstream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    astream = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), {})

    def _fps(rate):
        try:
            num, den = rate.split("/")
            den = float(den)
            return round(float(num) / den, 3) if den else 0.0
        except Exception:
            return 0.0

    return {
        "name": p.name,
        "path": str(p),
        "size": int(fmt.get("size", 0)),
        "size_mb": round(int(fmt.get("size", 0)) / 1024 / 1024, 2),
        "duration": round(float(fmt.get("duration", 0)), 3),
        "bit_rate": int(fmt.get("bit_rate", 0)),
        "width": int(vstream.get("width", 0)),
        "height": int(vstream.get("height", 0)),
        "video_codec": vstream.get("codec_name", ""),
        "fps": _fps(vstream.get("r_frame_rate", "0/0")),
        "audio_codec": astream.get("codec_name", ""),
        "md5": md5_of(p),
    }


# ---------------------------------------------------------------------------
# 去重管线核心
# ---------------------------------------------------------------------------
# 默认参数区间（对应面板默认值）
DEFAULTS = {
    "brightness": (-0.02, 0.02),   # eq brightness
    "contrast": (0.95, 1.05),      # eq contrast
    "saturation": (0.95, 1.05),    # eq saturation
    "sharpen": (1.0, 1.1),         # unsharp 强度近似
    "denoise": 4,                  # hqdn3d 亮度空间强度
    "rotate_deg": (-1.0, 1.0),     # 微旋转角度范围
    "fps_range": (24.0, 30.0),     # 帧率区间
    "bitrate_mul": (0.90, 1.20),   # 相对原码率倍率（面板"倍率"模式；收敛默认值以免重编码后体积暴涨，可覆盖）
}


def _rand(a, b):
    return round(random.uniform(a, b), 4)


def build_filter(params, src_info):
    """根据参数构建 ffmpeg -vf 滤镜链。返回 (filter_str, applied_dict)。"""
    applied = {}
    chain = []

    # 1) 画面调整：eq（亮度/对比度/饱和度）
    b = _rand(*params.get("brightness", DEFAULTS["brightness"]))
    c = _rand(*params.get("contrast", DEFAULTS["contrast"]))
    s = _rand(*params.get("saturation", DEFAULTS["saturation"]))
    chain.append(f"eq=brightness={b}:contrast={c}:saturation={s}")
    applied["brightness"] = b
    applied["contrast"] = c
    applied["saturation"] = s

    # 2) 降噪：hqdn3d
    if params.get("denoise"):
        d = params["denoise"]
        chain.append(f"hqdn3d={d}:{d}:6:6")
        applied["denoise"] = d

    # 3) 锐化：unsharp（画面微调更自然）
    sh = _rand(*params.get("sharpen", DEFAULTS["sharpen"]))
    chain.append(f"unsharp=5:5:{round(sh - 1.0, 3)}")
    applied["sharpen"] = sh

    # 4) 微旋转 + 裁切补边（保持原分辨率，黑边去除）
    if params.get("rotate", True):
        deg = _rand(*params.get("rotate_deg", DEFAULTS["rotate_deg"]))
        w, h = src_info["width"], src_info["height"]
        # 旋转后放大一点再中心裁回原尺寸，避免黑边
        rad = f"{deg}*PI/180"
        chain.append(f"scale=iw*1.06:ih*1.06")
        chain.append(f"rotate={rad}:c=black")
        chain.append(f"crop={w}:{h}")
        applied["rotate_deg"] = deg

    # 5) 🆕 裁剪 crop（构图类，DD §2.5a）：按比例中心裁切后 scale 回原分辨率
    #    保持分辨率不变 → 自检 resolution_kept 必须仍为 true。
    if params.get("crop"):
        w, h = src_info["width"], src_info["height"]
        ratio = float(params.get("crop_ratio", LEVELS["medium"]["crop"]))
        cw = round(w * (1 - ratio))
        ch = round(h * (1 - ratio))
        # 保证偶数尺寸（libx264 要求宽高为偶数），并居中裁切
        cw -= cw % 2
        ch -= ch % 2
        chain.append(f"crop={cw}:{ch}:{(w - cw) // 2}:{(h - ch) // 2}")
        chain.append(f"scale={w}:{h}")
        applied["crop_ratio"] = ratio

    # 6) 🆕 翻转 flip（构图类，DD §2.5）：高破坏维度，默认关，需显式 flip_mode
    #    🔒 硬校验（DD §3.6 兜底）：flip 开但未指定方向 → 拒绝，禁止静默默认成 'h'。
    #    上游 rules.json body_check tier2 已按顶层 flip 字段拦一道，但那条链路只在
    #    hooks 生效（且求值器不读 dimensions.flip 嵌套，按约束不改），直调 pipeline /
    #    server 透传漏镜像时会绕过 → 此处必须自己拦，否则 DD 说的兜底根本不存在。
    if params.get("flip"):
        mode = params.get("flip_mode")
        node = {"h": "hflip", "v": "vflip", "90": "transpose=1"}.get(mode)
        if node is None:
            raise PipelineError(
                f"flip 已开启但 flip_mode 缺失或非法（收到 {mode!r}）。"
                f"翻转是高破坏维度，必须显式指定方向：h（水平）/ v（垂直）/ 90（转置）。")
        chain.append(node)
        applied["flip_mode"] = mode

    return ",".join(chain), applied


def _pick_fps(params):
    lo, hi = params.get("fps_range", DEFAULTS["fps_range"])
    # 帧率取区间内整数值
    return random.choice([f for f in (24, 25, 30) if lo <= f <= hi] or [25])


# ---------------------------------------------------------------------------
# 强度档编排 + 时序维度（DD 模块二 F2.1 / F2.3）
# ---------------------------------------------------------------------------
def _resolve_level(level=None, dimensions=None, params=None, seed=None):
    """把强度档 + 维度开关展开为传给 build_filter / 时序处理的具体参数。

    - level ∈ {light, medium, heavy}，非法值回退 medium 并在 _level_note 标注。
    - dimensions 缺省 = DIMENSION_DEFAULTS（flip 默认关）。
    - params 逐维精细覆盖，优先级高于 level（DD §2.3）。
    - seed 缺省回填 random（保证裂变各变体互异）。
    返回 (resolved, seed)。resolved 含 build_filter 可读的开关与各维度落点。
    """
    note = None
    if level not in LEVELS:
        if level is not None:
            note = f"非法 level={level!r}，已回退 medium"
        level = "medium"
    band = LEVELS[level]

    dims = dict(DIMENSION_DEFAULTS)
    if dimensions:
        dims.update({k: bool(v) for k, v in dimensions.items()})

    if seed is None:
        seed = random.randint(1, 10 ** 9)
    random.seed(seed)

    resolved = {
        # 既有画面维度开关
        "rotate": dims["rotate"],
        # 构图维度
        "crop": dims["crop"],
        "crop_ratio": band["crop"],
        "flip": dims["flip"],
        # 时序维度（供 dedup_video 主体读取，不进 build_filter）
        "_speed_on": dims["speed"],
        "_trim_on": dims["trim"],
        "_speed_band": band["speed"],
        "_trim_band": band["trim"],
        "_level": level,
        "_seed": seed,
    }
    if not dims["picture"]:
        # 画面调整关闭：把 eq/unsharp 幅度收敛到 0（仍生成节点但近似恒等）
        resolved["brightness"] = (0.0, 0.0)
        resolved["contrast"] = (1.0, 1.0)
        resolved["saturation"] = (1.0, 1.0)
        resolved["sharpen"] = (1.0, 1.0)
    if not dims["flip"]:
        resolved.pop("flip", None)
    if dims["flip"] and params and params.get("flip_mode"):
        resolved["flip_mode"] = params["flip_mode"]

    # params 逐维覆盖（高级），优先级最高
    if params:
        for k, v in params.items():
            resolved[k] = v

    if note:
        resolved["_level_note"] = note
    return resolved, seed


def _apply_speed(factor):
    """生成变速的视频/音频滤镜片段（DD §2.5b，保音调）。

    返回 (setpts_node, atempo_node)。factor 越界钳制到 [0.5, 2.0]（atempo 单实例硬约束）。
    """
    clamped = min(ATEMPO_MAX, max(ATEMPO_MIN, float(factor)))
    return f"setpts=PTS/{clamped}", f"atempo={clamped}", clamped


def _calc_trim(duration, band, seed=None, phase=None):
    """算头尾裁剪时长并套最短时长保护（DD §2.5c / §0.3）。

    band: (lo, hi) 单侧裁剪秒数区间。
    返回 dict：{ss, out_dur, head, tail, skipped, reason}。

    phase ∈ [0,1]（裂变专用，缺省 None = 原 iid 随机行为）：
      把【总裁剪量固定、头尾配比按 phase 线性铺开】，而非在 band 内独立随机取。
      理由（实测，见 docs/eval/沉淀失败原因.md EXP-A/F2.4-01）：iid 随机取值使各
      变体的 head 差远小于 pHash 可分辨的时间错位量——8s 素材 heavy 档被 10% 上限
      钳后 head 差 ≤0.16s，对应实测距离仅 3.5（远低于 12），这是「变体两两不达标」
      的根因，而非阈值语义问题。
      phase 模式：budget = min(2*hi, duration*10%, duration-7s)，
                 head = budget*phase，tail = budget-head。
      各变体成片时长恒等（=duration-budget），head 跨度 = budget = 最大可达错位。

    语义边界（DD §0.3 落地口径）：
      - 掐头去尾总量 > 原时长*10% → 钳制到 10% 上限。
      - 原时长 < 7s（MIN_DURATION_TRIM）→ skipped=True，不裁。这属于「素材天生
        不适合去头尾」，不是裁剪越界：5s 硬下限约束的是【裁剪/变速导致成片过短】，
        而非【素材本来就短】，故此处不抛错。
      - 裁后 < 7s → 把总裁剪量收回到成片恰好 7s。
    成片 5s 硬下限（且需合并变速影响）由 dedup_video 统一把关，
    见 _clamp_speed_for_floor + checks.min_duration_ok。
    """
    lo, hi = band
    if phase is None:
        head = round(random.uniform(lo, hi), 3)
        tail = round(random.uniform(lo, hi), 3)
    else:
        ph = min(1.0, max(0.0, float(phase)))
        budget = min(2.0 * hi,
                     duration * MAX_TRIM_RATIO,
                     max(0.0, duration - MIN_DURATION_TRIM))
        head = round(budget * ph, 3)
        tail = round(budget - head, 3)
    total = head + tail

    def _skip(reason):
        return {"ss": 0.0, "out_dur": round(duration, 3), "head": 0.0, "tail": 0.0,
                "skipped": True, "reason": reason}

    # phase 模式下可裁窗口可能为 0（原时长贴着 7s 下限）→ 显式跳过，不静默出 0 裁剪
    if phase is not None and total <= 0:
        return _skip(f"可裁窗口为 0（原时长 {duration:.2f}s 贴近 "
                     f"{MIN_DURATION_TRIM}s 下限），跳过去头尾")

    # 保护1：总裁剪量 ≤ 原时长 10%
    max_total = duration * MAX_TRIM_RATIO
    if total > max_total and total > 0:
        scale = max_total / total
        head = round(head * scale, 3)
        tail = round(tail * scale, 3)
        total = head + tail

    # 保护2：原时长 <7s 禁再截 → 不裁（素材天生短，非越界）
    if duration < MIN_DURATION_TRIM:
        return _skip(f"原时长 {duration:.2f}s < {MIN_DURATION_TRIM}s，跳过去头尾")

    out_dur = duration - total
    # 保护3：裁后 <7s → 收回到成片恰好 7s
    if out_dur < MIN_DURATION_TRIM:
        total = duration - MIN_DURATION_TRIM
        if total <= 0:
            return _skip(f"可裁余量不足（原时长 {duration:.2f}s），跳过去头尾")
        head = round(total / 2, 3)
        tail = round(total - head, 3)
        out_dur = duration - total

    return {"ss": round(head, 3), "out_dur": round(out_dur, 3),
            "head": round(head, 3), "tail": round(tail, 3),
            "skipped": False, "reason": None}


def _clamp_speed_for_floor(factor, base_dur):
    """把变速因子钳到不会让成片跌破 5s 硬下限的范围（DD §0.3 / §2.5b）。

    DD 对越界给的是「拒绝**或**钳制」，此处取钳制：加速会缩短成片
    （成片时长 = base_dur / factor），故 factor 上限 = base_dur / MIN_DURATION_HARD。
    若 base_dur 本身已 < 5s（素材天生短），只保证「不因加速再变短」，上限取 1.0
    ——与 _calc_trim 对短素材的语义保持一致：我们不制造越界，也不因素材原生过短而拒绝。
    返回 (clamped_factor, note)；note 为 None 表示未钳制。
    """
    max_factor = base_dur / MIN_DURATION_HARD if MIN_DURATION_HARD > 0 else ATEMPO_MAX
    if max_factor < 1.0:
        max_factor = 1.0
    if factor > max_factor:
        max_factor = round(max_factor, 4)
        return max_factor, (
            f"变速因子 {factor} 会使成片跌破 {MIN_DURATION_HARD}s 硬下限，"
            f"已钳制到 {max_factor}")
    return factor, None


def dedup_video(src, params=None, out_name=None, seed=None,
                level=None, dimensions=None, flip_mode=None, trim_phase=None):
    """
    对单个视频执行去重（本期增量：强度档 + 构图/时序维度 + pHash 自检升级）。

    src: 文件名或绝对路径（经 _resolve_safe 白名单校验）
    level: 强度档 light/medium/heavy（默认 medium）
    dimensions: 维度开关 dict（picture/rotate/crop/flip/speed/trim），缺省见 DIMENSION_DEFAULTS
    flip_mode: 翻转方向 h/v/90（仅 flip 开时用）
    params: 逐维精细覆盖（高级），优先级高于 level
    out_name: 输出文件名（默认 <原名>_去重.mp4）
    seed: 随机种子（缺省随机并回填；裂变时用不同 seed 保证变体差异）
    trim_phase: ∈[0,1]，裂变专用。把去头尾配比按相位铺开而非 iid 随机取，
        使各变体在源时间轴上的错位量最大化（见 _calc_trim phase 参数说明）。
        None = 单片模式，保持原随机行为。
    返回处理报告字典（checks 含 phash 与 all_passed）。
    """
    # 路径安全：src 必须在 assets 白名单内
    src_path = _resolve_safe(src, VIDEO_DIR, must_exist=True)

    user_params = dict(params or {})
    if flip_mode and "flip_mode" not in user_params:
        user_params["flip_mode"] = flip_mode

    # 档位编排：展开为 build_filter/时序处理可读的具体参数（内部完成 random.seed 回填）
    resolved, seed = _resolve_level(level=level, dimensions=dimensions,
                                    params=user_params, seed=seed)

    src_info = probe_video(str(src_path))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stem = Path(src_info["name"]).stem
    if not out_name:
        out_name = f"{stem}_去重.mp4"
    # 输出名也过白名单，防止 out_name 带 ../ 穿越写到 output 外
    out_path = _resolve_safe(out_name, OUTPUT_DIR, must_exist=False)

    vf, applied = build_filter(resolved, src_info)
    fps = _pick_fps(resolved)
    applied["fps"] = fps
    applied["level"] = resolved["_level"]
    applied["seed"] = seed
    if resolved.get("_level_note"):
        applied["level_note"] = resolved["_level_note"]

    # --- 时序维度 ---
    # 顺序要求：先算 trim 得到成片基准时长，再据此钳制变速因子，
    # 否则「trim 后再加速」的叠加效应会绕过 5s 硬下限（DD §0.3）。
    ss = None
    out_dur = None
    if resolved.get("_trim_on"):
        trim = _calc_trim(src_info["duration"], resolved["_trim_band"],
                          seed=seed, phase=trim_phase)
        applied["trim_head"] = trim["head"]
        applied["trim_tail"] = trim["tail"]
        if trim_phase is not None:
            applied["trim_phase"] = round(float(trim_phase), 4)
        if trim["skipped"]:
            applied["trim_skipped"] = True
            applied["trim_skip_reason"] = trim["reason"]
        else:
            ss, out_dur = trim["ss"], trim["out_dur"]

    # 变速（音视频同步，保音调），并套 5s 硬下限
    af_nodes = []
    speed_factor = None
    if resolved.get("_speed_on"):
        # 在档位幅度内随机 ±，落到 [1-band, 1+band]
        band = resolved["_speed_band"]
        factor = 1.0 + random.choice([-1, 1]) * _rand(0, band)
        base_dur = out_dur if out_dur is not None else src_info["duration"]
        factor, floor_note = _clamp_speed_for_floor(factor, base_dur)
        setpts_node, atempo_node, speed_factor = _apply_speed(factor)
        vf = f"{vf},{setpts_node}" if vf else setpts_node
        af_nodes.append(atempo_node)
        applied["speed_factor"] = speed_factor
        if floor_note:
            applied["speed_clamp_note"] = floor_note

    # 码率：倍率模式（相对原码率）或定值
    if resolved.get("bitrate_kbps"):
        vbitrate = f"{resolved['bitrate_kbps']}k"
        applied["bitrate_kbps"] = resolved["bitrate_kbps"]
    else:
        mul = _rand(*resolved.get("bitrate_mul", DEFAULTS["bitrate_mul"]))
        kbps = max(500, int(src_info["bit_rate"] / 1000 * mul)) if src_info["bit_rate"] else 2500
        vbitrate = f"{kbps}k"
        applied["bitrate_mul"] = mul
        applied["bitrate_kbps"] = kbps

    # 组装 ffmpeg 命令（列表式传参，禁 shell 拼接）
    # ⚠️ -ss/-t 均放在 -i 【之前】作为输入侧选项，使去头尾在【源时间轴】上生效。
    #    DD §2.5c 字面写的是 `-ss head -i src -t out_dur`（-t 在 -i 之后 = 输出侧选项），
    #    但输出侧 -t 是在 setpts 变速【之后】度量的：变速会先把流压短，
    #    使 -t 阈值大于实际流长而不发生截断 —— 去尾被静默吞掉，
    #    applied_params 里的 trim_tail 就成了假报告（实测 15.184s 素材 heavy 档复现）。
    #    放到输入侧后 trim 与 speed 正交：读 out_dur 秒源 → 变速压成 out_dur/factor。
    cmd = [str(FFMPEG), "-y"]
    if ss is not None and ss > 0:
        cmd += ["-ss", str(ss)]           # 去头（输入定位）
    if out_dur is not None:
        cmd += ["-t", str(out_dur)]       # 去尾：源时间轴上读取的时长
    cmd += ["-i", src_info["path"]]
    cmd += ["-vf", vf, "-r", str(fps), "-b:v", vbitrate,
            "-c:v", "libx264", "-preset", "medium",
            "-c:a", "aac", "-b:a", "128k"]
    if af_nodes:
        cmd += ["-af", ",".join(af_nodes)]
    cmd += ["-metadata", f"comment=processed-{random.randint(10000, 99999)}",
            "-map_metadata", "-1", str(out_path)]

    rc, out, err = _run(cmd, timeout=600)
    if rc != 0:
        raise PipelineError(f"ffmpeg 去重失败:\n{err[-2000:]}")

    out_info = probe_video(str(out_path))

    # --- 自检升级 ---
    # duration_close：启用时序维度时用「范围口径」（预期时长 ±3%），否则沿用 |Δ|<1.0s
    if speed_factor or out_dur is not None:
        expected = out_dur if out_dur is not None else src_info["duration"]
        if speed_factor:
            expected = expected / speed_factor
        duration_close = abs(out_info["duration"] - expected) <= max(0.5, expected * 0.03)
    else:
        duration_close = abs(src_info["duration"] - out_info["duration"]) < 1.0

    md5_changed = src_info["md5"] != out_info["md5"]
    resolution_kept = (src_info["width"], src_info["height"]) == (out_info["width"], out_info["height"])

    # 5s 硬下限的【事后校验】（DD §0.3）：只在原素材本身达标时才要求成片达标，
    # 原素材天生 <5s 属素材问题、非本管线越界（与 _calc_trim 的短素材语义一致）。
    if src_info["duration"] >= MIN_DURATION_HARD:
        min_duration_ok = out_info["duration"] >= MIN_DURATION_HARD
    else:
        min_duration_ok = True

    # pHash：变体 vs 原素材（度量“够不够不同”）
    phash = M.compare_videos(str(src_path), str(out_path))

    all_passed = (md5_changed and resolution_kept and duration_close
                  and min_duration_ok and bool(phash.get("passed")))

    return {
        "src": src_info,
        "output": out_info,
        "output_path": str(out_path),
        "applied_params": applied,
        "fps": fps,
        "checks": {
            "md5_changed": md5_changed,
            "resolution_kept": resolution_kept,
            "duration_close": duration_close,
            "min_duration_ok": min_duration_ok,
            "phash": phash,
            "all_passed": all_passed,
        },
    }


def batch_fission(src, count=3, params=None,
                  level=None, dimensions=None, flip_mode=None):
    """裂变：同一素材生成 count 个不同参数的变体（本期增量：档位/维度透传 + 距离矩阵）。

    每变体用不同 seed 保证互异；产出后调 metrics.distance_matrix 计算两两感知哈希距离，
    并入顶层 matrix。既有 all_unique（MD5 维度）保留。count 上限 20（PRD D-04）。

    ⚠️ 变体间分离度靠【时间错位】而非参数随机性（实测依据见
    docs/eval/沉淀失败原因.md F2.4-01）：speed/rotate/crop 的变体间差分在
    「按比例归一化抽帧」口径下分别只有 1.9 / 4.1 / 7.5，全部够不到阈值 12；
    唯一有效的两条腿是时间错位（δ≥1s → 29）与 flip（→ 33）。
    故此处把 trim 的头尾配比按变体序号【确定性铺开】(phase=i/(count-1))，
    让 head 跨度撑满合法可裁窗口，而不是各变体从同一窄区间 iid 抽样。
    """
    count = max(1, min(int(count), 20))
    base_info = probe_video(src)
    stem = Path(base_info["name"]).stem

    results = []
    for i in range(count):
        out_name = f"{stem}_变体{i + 1}.mp4"
        # phase 均匀铺开：count=1 时取 0.0（无对照需求）；count>1 时端到端撑满 [0,1]
        phase = 0.0 if count == 1 else i / (count - 1)
        r = dedup_video(src, params=params, out_name=out_name,
                        seed=random.randint(1, 10 ** 9),
                        level=level, dimensions=dimensions, flip_mode=flip_mode,
                        trim_phase=phase)
        results.append({
            "index": i + 1,
            "output_path": r["output_path"],
            "md5": r["output"]["md5"],
            "applied_params": r["applied_params"],
            "checks": r["checks"],
        })

    md5s = [x["md5"] for x in results]

    # 变体两两感知哈希距离矩阵（度量“N 个变体之间够不够互异”，PRD §8.2）
    variant_paths = [x["output_path"] for x in results]
    try:
        matrix = M.distance_matrix(variant_paths)
    except Exception as e:
        # 矩阵计算失败不毁整个裂变结果，降级为提示
        matrix = {"error": str(e), "count": count, "all_pass": None,
                  "matrix": [], "too_close_pairs": [], "min_pair": None}

    # 分离度诊断（实测依据见 docs/eval/沉淀失败原因.md F2.4-01）：
    # 矩阵不达标时要指明【卡在哪条腿】，而不是只丢一个 all_pass=False。
    # 变体间有效腿只有两条：时间错位（trim 铺开）与 flip；
    # speed/rotate/crop 的变体间差分实测仅 1.9 / 4.1 / 7.5，调它们无用。
    trim_skipped_all = bool(results) and all(
        x["applied_params"].get("trim_skipped") for x in results)
    flip_modes = [x["applied_params"].get("flip_mode") for x in results]
    separation = {
        "time_leg": "absent" if trim_skipped_all else "present",
        "flip_spread": len(set(flip_modes)) > 1,
    }
    if matrix.get("all_pass") is False and not separation["flip_spread"]:
        if trim_skipped_all:
            separation["hint"] = (
                f"素材时长 {base_info['duration']:.2f}s < {MIN_DURATION_TRIM}s，"
                f"去头尾被最短时长保护挡掉 → 变体间无时间错位可用，"
                f"仅靠参数随机拉不开（实测 0.5–1.75）。"
                f"唯一有效杠杆：给各变体指定不同 flip_mode（h/v/90）。")
        else:
            separation["hint"] = (
                "时间错位已铺开但仍不达标 → 可裁窗口偏小；"
                "追加杠杆：给各变体指定不同 flip_mode（h/v/90）。")

    return {
        "src": base_info["name"],
        "count": count,
        "variants": results,
        "all_unique": len(set(md5s)) == len(md5s),
        "matrix": matrix,
        "separation": separation,
    }


# ---------------------------------------------------------------------------
# 水印：去 / 加
# ---------------------------------------------------------------------------
def _load_watermark_ini(platform):
    """读取 watermarks/<platform>.ini 的坐标配置。"""
    ini_path = WATERMARKS_DIR / f"{platform}.ini"
    if not ini_path.exists():
        raise PipelineError(f"水印模板不存在: {ini_path}")
    cp = configparser.ConfigParser()
    cp.read(ini_path, encoding="utf-8")
    wm = cp["Watermark"]
    return {
        "pos": wm.get("P", "RT"),
        "ref": int(wm.get("R", 720)),
        "x": int(wm.get("X", 0)),
        "y": int(wm.get("Y", 0)),
        "w": int(wm.get("W", 0)),
        "h": int(wm.get("H", 0)),
    }


def list_watermark_templates():
    """列出可用水印模板（平台名）。"""
    if not WATERMARKS_DIR.exists():
        return []
    return [p.stem for p in sorted(WATERMARKS_DIR.glob("*.ini"))]


def remove_watermark(src, platform, out_name=None):
    """
    按平台模板坐标用 delogo 去除水印。
    模板坐标基于 R（基准宽度），按实际分辨率等比缩放。
    """
    src_info = probe_video(src)
    tpl = _load_watermark_ini(platform)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 坐标按 基准宽度R -> 实际宽度 缩放
    scale = src_info["width"] / tpl["ref"] if tpl["ref"] else 1.0
    x = max(0, int(tpl["x"] * scale))
    y = max(0, int(tpl["y"] * scale))
    w = max(1, int(tpl["w"] * scale))
    h = max(1, int(tpl["h"] * scale))
    # delogo 要求区域在画面内
    w = min(w, src_info["width"] - x - 1)
    h = min(h, src_info["height"] - y - 1)

    stem = Path(src_info["name"]).stem
    # 🔒 F3.3：out_name 也走白名单，防止越界写到 OUTPUT_DIR 外。
    out_path = _resolve_safe(out_name or f"{stem}_去{platform}水印.mp4",
                             OUTPUT_DIR, must_exist=False)

    cmd = [
        str(FFMPEG), "-y", "-i", src_info["path"],
        "-vf", f"delogo=x={x}:y={y}:w={w}:h={h}",
        "-c:a", "copy",
        str(out_path),
    ]
    rc, out, err = _run(cmd, timeout=600)
    if rc != 0:
        raise PipelineError(f"去水印失败:\n{err[-2000:]}")

    return {
        "src": src_info["name"],
        "platform": platform,
        "delogo_region": {"x": x, "y": y, "w": w, "h": h},
        "output_path": str(out_path),
        "output_md5": md5_of(out_path),
    }


if __name__ == "__main__":
    # 自测
    import pprint
    pprint.pprint(check_env())
    pprint.pprint(list_assets())
