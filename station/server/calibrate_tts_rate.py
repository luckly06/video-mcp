# -*- coding: utf-8 -*-
"""
calibrate_tts_rate.py — 校准 MiMo TTS 实际语速（字/秒）

用途：把「按视频时长预测字数上限」从拍脑袋变成可测。
运行（需联网 + MIMO_API_KEY 已配在 .env）：
    python calibrate_tts_rate.py

原理：用一段固定 N 字的中文让 TTS 合成，量出音频秒数，R = N / 秒数。
把打印出的 R 填到 mcp_server.py 的 TTS_CHARS_PER_SEC 即可。
"""
import os
import subprocess
import tempfile
from pathlib import Path

import tts_client as T

# 一段约 200 字的固定中文（覆盖常见口播节奏）
SAMPLE = (
    "你见过这么炸裂的逆转吗？最后三秒，他顶着两人包夹强行起跳，球在空中划出一道弧线，"
    "全场瞬间安静，下一秒爆发出的欢呼声几乎掀翻屋顶。这就是竞技体育最迷人的地方，"
    "不到最后一刻，你永远不知道结局。赛后他说，那一刻脑子里只有一句话，把球投进去。"
    "其实生活也是这样，很多看似不可能的任务，往往只差你敢不敢出手的勇气。下次当你犹豫时，"
    "不妨想想这个压哨绝杀，评论区聊聊，你人生里最硬的一次出手是什么？点赞关注，不迷路。"
)

# 解析 vendored ffprobe（与 pipeline.py 同款逻辑）
_VENDOR = Path(__file__).resolve().parent.parent / "vendor"
FFPROBE = Path(os.environ.get("VU_FFPROBE", _VENDOR / "ffmpeg" / "ffprobe.exe"))


def _probe_seconds(wav_path: Path) -> float:
    if not FFPROBE.exists():
        # 退回系统 PATH
        FFPROBE = "ffprobe"
    try:
        out = subprocess.check_output(
            [str(FFPROBE), "-v", "quiet", "-print_format", "json",
             "-show_format", str(wav_path)],
            timeout=30,
        ).decode("utf-8", "ignore")
        import json
        return float(json.loads(out).get("format", {}).get("duration", 0) or 0)
    except Exception as e:
        print("  ffprobe 失败：", e)
        return 0.0


def main():
    if not T.is_available():
        print("TTS 不可用：请先在 station/server/.env 配置 MIMO_API_KEY 并 pip install openai")
        return
    n_chars = len(SAMPLE)
    print(f"样本字数 N = {n_chars}")
    print("正在调用 MiMo TTS 合成（需联网）...")
    wav = T.tts_to_temp(SAMPLE, voice="冰糖", speed=1.0)
    try:
        secs = _probe_seconds(wav)
        if secs <= 0:
            print("无法测得音频时长，校准中止。")
            return
        rate = n_chars / secs
        print(f"音频时长 = {secs:.2f}s")
        print(f"实测语速 R = {rate:.2f} 字/秒")
        print(f"建议填到 mcp_server.py 的 TTS_CHARS_PER_SEC = {rate:.1f}")
        print("提示：不同音色/语速结果略有差异，可取 2~3 次平均。")
    finally:
        try:
            wav.unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    main()
