# VISION-002: 切换视觉后端到腾讯元宝

## 问题

DeepSeek 识图模式是 **OCR-only**（截图证明：识别后输出"未提取到文字"），无法描述场景/人物/动作，不能用作语义上下文来源。

Prompt 也被 DeepSeek 拒绝（"解析失败，发送至识图模式"）—— 可能因为输入框只接受 OCR query 不接受描述性 prompt。

## 解决

把视觉后端从 DeepSeek 切换到**腾讯元宝**（混元大模型 yuanbao.tencent.com）。混元支持多模态，能给图像理解。

## 实现路径

### 1. 新建 yuanbao_client.py

```python
# yuanbao_client.py — 元宝多模态视频画面描述
# - 类似 copy_rewriter.py，但目标是 yuanbao.tencent.com
# - 专用 profile: station/logs/.yuanbao-profile
# - 登录方式: 微信扫码
```

**核心 API**：
```python
def login()         # 打开有头浏览器，微信扫码
def is_available()  # profile 是否建立
def describe_frames(frames, prompt) -> str  # 上传帧图 + 拿描述
```

### 2. 提取帧 (复用现有 _extract_frames)

不重复造轮子，复制 `_extract_frames` 到 yuanbao_client.py（共用 ffmpeg）。

### 3. UI 切换 (selector)

元宝 selector 不一样：
- 输入框：textarea 或 contenteditable div（待实测）
- 上传按钮：选附件图标
- 发送按钮：Enter 或点击发送

测试后再填具体 selector。先在 `--login` 模式打开浏览器抓真实 DOM。

### 4. copy_rewriter 集成

`_rewrite_async` 现在分两段：
1. **视觉通道**（yuanbao_client.describe_frames）→ 画面描述
2. **改写通道**（DeepSeek，按原逻辑）

视觉和改写**可以在同一个浏览器会话里并行**，只要各自用独立 profile。

### 5. mcp_server 切换

```python
# rewrite_copy handler:
#   1. subtitle 提取
#   2. yuanbao_client.describe_frames(frames) -> vision_desc
#   3. copy_rewriter.rewrite(text, topic=vision_desc)  -> 改写文案
```

视觉通道独立于改写通道，可以单独调用、单独配置。

### 6. 环境变量

`VU_VISION_BACKEND=yuanbao|deepseek|local-clipl`（Phase 2 早期默认 yuanbao）

## 待解决问题

1. **元宝登录态**：微信扫码要在浏览器里手动完成，AI 侧无法代劳
2. **多帧上传**：元宝是否支持一次多张图？还是只能一张？影响画面描述质量
3. **元宝响应时长**：识别 3 帧可能需要 30s+，UX 上需进度提示
5. **专用 profile 隔离**：元宝不能和 DeepSeek 共享 profile（不同站点的 cookie 互不通用）

## Phase 拆分

| Phase | 任务 |
|-------|------|
| P1 | yuanbao_client.py 基础：login/is_available/describe_frames（OCR 模式先打通） |
| P2 | 视觉理解 prompt 调优："请描述图中的场景/人物/动作" 在元宝上能识别 |
| P3 | mcp_server 集成 + rewrite_copy 链路切换 |
| P4 | 端到端验证：上传视频 → 改写文案语义一致 |

## 入口文件

- `station/server/yuanbao_client.py` — 新建
- `station/server/copy_rewriter.py` — 现有，改 `_rewrite_async` 视觉调用指向 yuanbao
- `station/server/mcp_server.py` — `rewrite_copy` 集成视觉通道