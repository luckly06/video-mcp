# -*- coding: utf-8 -*-
"""
asr_client.py — sherpa-onnx 本地语音识别（离线 ASR）

- 模型：SenseVoice Small（中文/英文/日/韩/粤语）
- 存放：F:/Download/A-models/sherpa-onnx
- 降级：模型缺失时 is_available()=False，不阻塞去重流程
"""

import os
import struct
import logging
from pathlib import Path

_MODEL_DIR = Path(os.environ.get("VU_ASR_MODELS", "/opt/sherpa-onnx"))
_MODEL_CANDIDATES = [
    ("sherpa-onnx-paraformer-zh-small-2024-03-09", "paraformer"),
    ("sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17", "sense_voice"),
]

logger = logging.getLogger("asr_client")

# 缓存识别器实例
_recognizer = None
_recognizer_model_type = None


def _get_recognizer():
    """惰性创建 OfflineRecognizer（仅首次调用时加载模型）。
    按优先级尝试：Paraformer → SenseVoice"""
    global _recognizer, _recognizer_model_type
    if _recognizer is not None:
        return _recognizer

    try:
        import sherpa_onnx
    except ImportError:
        logger.warning("sherpa-onnx 未安装。pip install sherpa-onnx")
        return None

    for model_name, model_type in _MODEL_CANDIDATES:
        model_path = _MODEL_DIR / model_name
        if not model_path.exists():
            continue
        onnx_files = list(model_path.glob("*.onnx"))
        tokens_file = model_path / "tokens.txt"
        if not onnx_files or not tokens_file.exists():
            continue

        try:
            if model_type == "paraformer":
                _recognizer = sherpa_onnx.OfflineRecognizer.from_paraformer(
                    paraformer=str(onnx_files[0]),
                    tokens=str(tokens_file),
                )
            elif model_type == "sense_voice":
                _recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                    model=str(onnx_files[0]),
                    tokens=str(tokens_file),
                    language="zh",
                    use_itn=True,
                )
            _recognizer_model_type = model_type
            logger.info(f"ASR 模型已加载: {model_name} ({model_type})")
            return _recognizer
        except Exception as e:
            logger.warning(f"加载 {model_name} 失败: {e}")
            continue

    logger.warning("无可用的 ASR 模型")
    return None


def is_available():
    """检查 ASR 是否可用（sherpa-onnx 已安装 + 模型已下载）。"""
    return _get_recognizer() is not None


def transcribe(audio_path):
    """对音频文件进行语音识别，返回纯文本。

    Args:
        audio_path: WAV 或 MP3 文件路径（推荐 16kHz mono WAV）

    Returns:
        str: 识别出的文本；失败返回空字符串
    """
    recognizer = _get_recognizer()
    if recognizer is None:
        return ""

    import soundfile as sf

    try:
        samples, sample_rate = sf.read(str(audio_path), dtype="float32")
    except Exception as e:
        # soundfile 可能不支持某些格式，降级用 scipy 或 wave
        try:
            import wave
            import numpy as np
            with wave.open(str(audio_path), "rb") as wf:
                sr = wf.getframerate()
                n_frames = wf.getnframes()
                raw = wf.readframes(n_frames)
                fmt_char = "h" if wf.getsampwidth() == 2 else "i"
                samples = np.frombuffer(raw, dtype=fmt_char).astype("float32") / 32768.0
                sample_rate = sr
        except Exception:
            logger.error(f"无法读取音频文件: {audio_path}")
            return ""

    if sample_rate != 16000:
        try:
            import scipy.signal
            # 简单降采样/升采样到 16k（SenseVoice 要求）
            from math import gcd
            g = gcd(sample_rate, 16000)
            up = 16000 // g
            down = sample_rate // g
            samples = scipy.signal.resample_poly(samples, up, down)
            sample_rate = 16000
        except ImportError:
            logger.warning(f"音频采样率 {sample_rate}Hz ≠ 16000，但 scipy 未安装，跳过重采样")
            return ""

    try:
        stream = recognizer.create_stream()
        stream.accept_waveform(sample_rate, samples)
        recognizer.decode_stream(stream)
        text = stream.result.text.strip()
        return text
    except Exception as e:
        logger.error(f"ASR 识别失败: {e}")
        return ""


def transcribe_video(video_path, ffmpeg_path):
    """从视频抽取音频并用 ASR 识别。

    Args:
        video_path: 视频文件路径
        ffmpeg_path: ffmpeg 可执行文件路径

    Returns:
        str: ASR 识别文本
    """
    import subprocess
    import tempfile

    fd, wav_path = tempfile.mkstemp(suffix=".wav", prefix="vu_asr_")
    os.close(fd)

    try:
        # 抽取音频为 16kHz mono WAV（SenseVoice 输入格式）
        cmd = [
            str(ffmpeg_path), "-y",
            "-i", str(video_path),
            "-vn",                          # 不要视频流
            "-acodec", "pcm_s16le",         # PCM 16bit
            "-ar", "16000",                 # 16kHz
            "-ac", "1",                     # mono
            str(wav_path),
        ]
        subprocess.run(cmd, capture_output=True, timeout=120, check=True)
        return transcribe(wav_path)
    except Exception as e:
        logger.error(f"视频音频抽取/ASR 失败: {e}")
        return ""
    finally:
        try:
            os.unlink(wav_path)
        except OSError:
            pass
