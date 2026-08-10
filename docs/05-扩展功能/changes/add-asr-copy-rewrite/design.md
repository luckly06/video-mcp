# Design: add-asr-copy-rewrite

## Context

全链路 TTS 音频替换需要：视频原对白 → ASR/字幕 → 文案改写 → TTS 语音。已实现字幕提取 ✅，缺 ASR 兜底和文案优化。

## Decisions

### 1. ASR 选型：sherpa-onnx

**选择**：sherpa-onnx（本地离线，ONNX 推理）
**放弃**：MiMo ASR（¥0.5/小时，收费），whisper.cpp（量化版精度差）

| | sherpa-onnx | MiMo ASR |
|---|---|---|
| 成本 | 0 | ¥0.5/h |
| Python 支持 | pip + prebuilt wheels | openai SDK（同 TTS） |
| 中文精度 | 好 | 很好 |
| 模型大小 | ~239MB | 无本地 |

模型存放：`F:\Download\A-models\sherpa-onnx`（遵循跨项目约定）

### 2. DeepSeek 文案改写：Playwright + openteam 技术

**选择**：Playwright 操控 DeepSeek 网页，复用用户 Chrome 已登录会话
**核心技术（来自 openteam）**：
- `page.evaluate()` 在 contenteditable 元素注入文本 + 派发 InputEvent
- 轮询等待 `isGenerating` 结束 → 用 DOM selector 抓取最后一条 .ds-markdown 回复

**放弃**：DeepSeek API（增加外部付费依赖，违背 0 API Token 原则）

### 3. 文案改写 system prompt

```
你是短视频配音文案优化师。将原始对白/字幕改写为适合 TTS 配音的单段旁白。
要求：口语化、有开头钩子、有结尾互动引导、30-100 字、纯文案输出不分段。
```

### 4. 管道：原始文案 → 改写 → TTS

```
ffmpeg 抽音频 .wav → sherpa-onnx ASR → 原文
                                    ↓
                          DeepSeek 改写 → 新文案
                                    ↓
                          MiMo TTS → 新语音 → ffmpeg 替换
```

文案来源优先级：用户手动输入 > 内嵌字幕提取 > ASR 识别 > ASR+改写

## Risks

| 风险 | 缓解 |
|---|---|
| sherpa-onnx Python wheel 不兼容 | 提供手动下载 + 路径配置指引 |
| DeepSeek 网页版改 DOM | 维护 selector 白名单，失效时降级跳过改写 |
| Playwright 被 DeepSeek 识别为 bot | `headless=False` + 使用用户真实 Chrome Profile |

## Dependencies

- `sherpa-onnx`（pip 或手动安装）
- `playwright`（pip install playwright）
- ONNX 模型文件放到 `F:\Download\A-models\sherpa-onnx`
- DeepSeek 网页已登录（需用户在 Chrome 登录一次）
