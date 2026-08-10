# Change: ASR 语音转文字 + DeepSeek 文案改写

## Why

TTS 音频替换的核心链路缺两段：

1. **音频 → 文字**：大部分短视频没有内嵌字幕轨，ffmpeg 抽不出来，需要有 ASR 兜底
2. **原文字 → 配音文案**：直接拿原对白/字幕当 TTS 输入效果差（口语碎、没钩子、没互动引导），需要改写

当前只实现了「有字幕轨时抽取」（`get_subtitle_text`），无字幕素材链路断裂。

## What Changes

### 阶段 A：sherpa-onnx 本地 ASR
- **新增** `station/server/asr_client.py`：封装 sherpa-onnx，加载 ONNX 模型（~239MB 放 `F:\Download\A-models\sherpa-onnx`）
- **扩展** `pipeline.py`：`dedup_video` 在无字幕轨且无用户文案时 → 抽音频 → ASR 识别 → 得到原文案
- 模型管理：自动下载 / 路径检测 / 降级提示

### 阶段 B：DeepSeek 文案改写
- **新增** `station/server/copy_rewriter.py`：复用 openteam 的浏览器操控技术
  - Playwright 打开 DeepSeek 网页（复用用户 Chrome 已登录会话）
  - 注入 system prompt + 原文案 → 发送 → 抓取改写结果
  - 核心技术：`page.evaluate()` 执行 contentEditable 注入 + InputEvent dispatch（来自 openteam）
- **扩展** 前端：TTS 区显示 ASR 识别进度 + 改写后文案预览
- 优先级：用户手动输入 > 内嵌字幕自动提取 > ASR 识别 > ASR 改写

## Impact

- Affected specs: asr-copy-rewrite（新增）
- Affected code: `station/server/asr_client.py`（新）、`copy_rewriter.py`（新）、`pipeline.py`、`web/`
- 新增依赖：`sherpa-onnx`（~239MB ONNX 模型）、`playwright`
- sherpa-onnx 模型统一放 `F:\Download\A-models\sherpa-onnx`（遵循跨项目 C 盘不下载大文件的约定）
- 向后兼容：无声卡/无 GUI 环境 ASR 可降级跳过

## Open Questions

- sherpa-onnx 的 Python binding 用 `pip install sherpa-onnx` 还是手动下载 DLL？
- DeepSeek 网页 DOM selector 需实测确认（可能随 DeepSeek 改版失效）
