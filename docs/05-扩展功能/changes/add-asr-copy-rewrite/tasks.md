# Tasks: add-asr-copy-rewrite

## 阶段 A：sherpa-onnx 本地 ASR
- [x] A.1 调研 sherpa-onnx Python 安装方式 + ONNX 模型下载
- [x] A.2 创建 `station/server/asr_client.py`
- [x] A.3 `is_available()`：检查模型文件是否存在
- [x] A.4 `transcribe(audio_path) -> str`：ffmpeg 抽音频 WAV → sherpa-onnx 识别 → 纯文本
- [x] A.5 `pipeline.py` 集成：无字幕轨 + 无 tts_text → 抽音频 → ASR → 得原文案
- [x] A.6 前端显示 ASR 来源标记（`tts_source: "asr"`）

## 阶段 B：DeepSeek 文案改写
- [x] B.1 用 Playwright 直控 DeepSeek 网页会话（复用 openteam 技术）
- [x] B.2 确认 DeepSeek 输入框 / 发送按钮 / 回复区 DOM selector
- [x] B.3 创建 `station/server/copy_rewriter.py`
- [x] B.4 `rewrite(original_text) -> str`：注入 system prompt + 原文 → 等待 → 抓取
- [x] B.5 Playwright 使用用户 Chrome Profile 保持登录态
- [x] B.6 `pipeline.py` 集成：ASR 得原文 → DeepSeek 改写 → TTS
- [ ] B.7 前端显示改写状态 + 改写前后对比
