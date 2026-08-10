## ADDED Requirements

### Requirement: sherpa-onnx 本地语音识别

系统 SHALL 在视频无内嵌字幕轨时，使用 sherpa-onnx 对音频进行本地语音识别。

#### Scenario: 无字幕视频 → ASR 识别成功
- **GIVEN** 视频无 subtitle 流，sherpa-onnx 模型已就绪
- **WHEN** 执行 dedup_video（tts_text 为空）
- **THEN** ffmpeg 抽取音频 WAV → sherpa-onnx 转录为中文文本；`tts_source="asr"`

#### Scenario: sherpa-onnx 模型缺失
- **GIVEN** 模型文件未下载到 `F:\Download\A-models\sherpa-onnx`
- **WHEN** 执行 ASR 识别
- **THEN** 跳过 ASR，TTS 仅依赖用户手动输入或内嵌字幕（降级不阻塞）

---

### Requirement: DeepSeek 文案改写

系统 SHALL 在获取原始对白文本后，通过 DeepSeek 将其改写为适合 TTS 配音的旁白文案。

#### Scenario: 原文案 → 改写成功
- **GIVEN** DeepSeek 网页会话已登录，Playwright 可用
- **WHEN** 调用 `copy_rewriter.rewrite("这段打斗太精彩了")`
- **THEN** 返回改写后的配音文案（如"你见过这么炸裂的打斗吗？这波操作直接封神！点赞关注，下期更精彩！"）

#### Scenario: DeepSeek 不可用 → 降级
- **GIVEN** Playwright 未安装或 DeepSeek 页面 DOM 变化
- **WHEN** 调用 rewrite
- **THEN** 返回 None，上层回退使用原始 asr 文本作为 TTS 输入

---

### Requirement: 全链路文案来源优先级

系统 SHALL 按以下优先级确定 TTS 文案：

1. 用户手动输入的 tts_text
2. 视频内嵌字幕轨自动提取
3. sherpa-onnx ASR + DeepSeek 改写
4. sherpa-onnx ASR 原始文本（DeepSeek 不可用时回退）

#### Scenario: 用户手动输入 → 最高优先
- **GIVEN** 用户填了 TTS 文案
- **WHEN** 执行 dedup_video
- **THEN** 直接使用用户输入，不触发字幕提取/ASR/改写；`tts_source="user"`
