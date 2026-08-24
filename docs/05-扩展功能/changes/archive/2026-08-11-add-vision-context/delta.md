# Delta: add-vision-context

## ADDED Features

### F-配音-007: 视觉上下文自动描述

无字幕视频用 ffmpeg 抽帧 + DeepSeek 网页识图自动产画面描述，注入改写 prompt。

- **输入**：视频文件路径（无字幕）、ffmpeg 路径
- **核心逻辑**：
  - `_extract_frames(video, ffmpeg, n=3)` → ffmpeg 均匀抽取 3 帧 JPEG（512px max）
  - `_vision_describe(page, frames)` → Playwright 同浏览器会话：切识图模式 → 上传帧 → 发 prompt → 等回复 → 切回快速模式
  - `_rewrite_async` 新增 `frames` 参数：有 frames 无 topic → 先识图产出 `vision_desc` → 作为 topic 注入 prompt
  - 提取优先级最终定稿：**字幕 > 视觉识图 > ASR**
- **预期产出**：
  - `station/server/copy_rewriter.py`：`_extract_frames` + `_vision_describe` + `_rewrite_async` 集成
  - `station/server/mcp_server.py`：`rewrite_copy` 无字幕时自动帧提取 → 传入 rewrite
  - 前端无需改动（预览流程复用

## MODIFIED Features

### F-配音-006: DeepSeek 文案改写（扩展）

- `_rewrite_async` 新增 `frames` 参数
- `rewrite()` 同步封装新增 `frames` 参数
- prompt 拼装：`topic` 参数支持 `vision_desc` 自动来源
