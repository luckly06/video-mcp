# Design: add-tts-audio-replace

> **前身**：`docs/02-方案设计/video-uniqueness_dd_v1.1/video-uniqueness_dd_p1_addendum.md`
> 本文为精简版设计决策记录，完整 DD 见原 Addendum。

## Context

视频去重工位需要补齐音频维度的唯一性。选择 MiMo TTS v2.5 因为：同平台（小米生态）、OpenAI 兼容协议、当前限免。

## Decisions

### 1. TTS 客户端：OpenAI 兼容封装

**选择**：`openai.OpenAI(api_key=..., base_url="https://api.xiaomimimo.com/v1")`  
**替代方案**：直接 HTTP requests（放弃，openai SDK 自动处理 retry/streaming）  
**音色**：4 个中文预置音色（冰糖/茉莉/苏打/白桦）

### 2. 音频轨替换：两遍 ffmpeg

**选择**：第一遍画面去重 → TTS 生成 WAV → 第二遍 `-c:v copy + apad + -map` 合并  
**替代方案**：单遍 ffmpeg 内联 TTS（放弃，需要流式管道，太复杂）  
**关键设计**：
- `os.replace()` 原子替换（`unlink+rename` 被 safe-delete 沙箱拦截）
- `apad` 静音补齐（TTS 短于视频时防截断）
- `-shortest` 截断（TTS 长于视频时兜底）

### 3. 字幕提取：ffmpeg SRT → 纯文本

**选择**：`ffmpeg -i src -map 0:s:0 -f srt -` 抽到 stdout → 正则去标签  
**触发条件**：用户未填 TTS 文案 且 视频有内嵌字幕轨  
**降级**：无字幕轨 → 跳过（未来走 MiMo ASR）

### 4. 前端：快捷填入 + 降级可见

**选择**：预设按钮（带货/解说/Vlog）+ 自由输入 + 探测卡显示字幕状态  
**原则**：不填文案 → 完全跳过，行为与改动前一致

## Risks

| 风险 | 缓解 |
|---|---|
| MiMo 开始收费 | `is_available()` 可扩展为配额检查；当前限免期 |
| 字幕轨编码异常 | `_run()` timeout 60s，失败返回 None |
| apad 在某些 ffmpeg 版本不可用 | 实测 `_vendor/ffmpeg` 支持 |

## Dependencies

- `openai>=1.0`（pip install）
- `MIMO_API_KEY` 环境变量
- MiMo 平台 `mimo-v2.5-tts` 模型可用
