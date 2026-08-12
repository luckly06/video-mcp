# Change: 自动视频画面描述 · 视觉上下文注入

> Change ID: `add-vision-context`
> 影响级别: B（新增系统能力，跨模块改动）
> 创建日期: 2026-08-11

## Why

ASR（sherpa-onnx Paraformer）在不理想音频条件下识别严重不准（如"剑鬼.mp4"→"没以"），DeepSeek 拿到离谱原文改写出的文案与视频内容完全无关。`tts-topic` 手动输入框（2026-08-11 已上线）是临时缓解，需自动化。

视频无可用字幕时，当前降级为 ASR 直接走 rewrite，产出文案不可靠。

## What Changes

### 阶段 A：帧提取 + 识图

- **ADDED** `F-配音-007`：`_extract_frames(video, n=5)` — ffmpeg 均匀抽 5 帧 JPEG（512px max, q=60）
- **ADDED** `F-配音-007`：`_vision_describe(frames, page)` — Playwright 同一 DeepSeek context 下上传帧图片 + 识图 prompt → 产出画面描述字符串
- **MODIFIED** `F-配音-006`：`_build_prompt` 优先用自动画面描述替代 topic 手动输入；改写提取优先级改为「字幕 > 视觉识图 > ASR」
- **MODIFIED** `F-配音-003`：提取链路在无字幕时先走识图，识图不可用才降级 ASR

### 阶段 B（远期）：本地视觉模型 + 知识图谱

- 用 CLIP/BLIP 本地模型替代 DeepSeek 网页识图
- 引入认知流程层框架（Step 0-5：Routing → Retrieve → Rerank → Select → Context → Memory）

## Impact

- **Affected Features**：`F-配音-006`（_build_prompt）、`F-配音-003`（提取链路）
- **Affected code**：
  - `station/server/copy_rewriter.py` — 新增 `_extract_frames` + `_vision_describe`
  - `station/server/pipeline.py` — probe 后触发帧提取
  - `station/server/mcp_server.py` — `rewrite_copy` 自动走视觉链路
- **新增依赖**：无（Phase A 复用已有 ffmpeg + Playwright DeepSeek 会话）
- **向后兼容性**：非破坏；原有 subtitle→ASR 链路保留为兜底
- **回归范围**：改写预览 + 自动 TTS 去重（有字幕/无字幕两路）

### 五项必查清单

| # | 检查项 | 本次情况 |
|---|--------|----------|
| 1 | 接口定义变更 | 无 |
| 2 | DB 表字段变更 | 无 |
| 3 | 异步消息变更 | 无 |
| 4 | 配置项变更 | 无 |
| 5 | 定时任务变更 | 无 |

## Open Questions

- DeepSeek 网页文件上传控件的 selector 稳定性能在多大程度上依赖？（需随 DeepSeek 改版持续维护）
- 识图 + 改写的两次浏览器往返时长是否可接受？（预估增加 15-30s）
