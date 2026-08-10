# Tasks: add-asr-copy-rewrite

## 阶段 A：sherpa-onnx 本地 ASR
- [x] A.1 调研 sherpa-onnx Python 安装方式 + ONNX 模型下载
- [x] A.2 创建 `station/server/asr_client.py`
- [x] A.3 `is_available()`：检查模型文件是否存在
- [x] A.4 `transcribe(audio_path) -> str`：ffmpeg 抽音频 WAV → sherpa-onnx 识别 → 纯文本
- [x] A.5 `pipeline.py` 集成：无字幕轨 + 无 tts_text → 抽音频 → ASR → 得原文案
- [x] A.6 前端显示 ASR 来源标记（`tts_source: "asr"`）

## 阶段 B：DeepSeek 文案改写（含用户模板）
- [x] B.1 用 Playwright 直控 DeepSeek 网页会话（复用 openteam 技术）
- [x] B.2 确认 DeepSeek 输入框 / 发送按钮 / 回复区 DOM selector
- [x] B.3 创建 `station/server/copy_rewriter.py`
- [x] B.4 `rewrite(original_text) -> str`：注入 system prompt + 原文 → 等待 → 抓取
- [x] B.5 Playwright 使用用户 Chrome Profile 保持登录态
- [x] B.6 `pipeline.py` 集成：ASR 得原文 → DeepSeek 改写 → TTS
- [x] B.7 字幕轨也支持改写（pipeline 修复：有字幕时也走可选改写路径）
- [x] B.8 **改写模板（用户自由填写）**：`copy_rewriter.rewrite()` 支持 `template` 参数；prompt 拼接逻辑接受任意字符串作为「自定义指令」
- [x] B.9 **前端模板 UI**：TTS 自动模式下 textarea 作为改写模板主控件（可空）；示例按钮一键填入；清空按钮恢复空状态
- [x] B.10 模板为空时跳过 DeepSeek 改写，直接 TTS 原文
- [x] B.11 改写失败时降级到原文（`tts_source` 后缀 `_fallback`）
- [ ] B.12 前端显示改写状态 + 改写前后对比（保留，下个迭代做）
