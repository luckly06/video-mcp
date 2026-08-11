# DeepSeek 改写：从「自动管线」改为「预览→确认→执行」

## 现状

当前 DeepSeek 改写是去重管线的内部步骤：
```
去重按钮 → 字幕提取 → ASR → DeepSeek 改写 → TTS → ffmpeg 合并
```
用户看不到改写结果就直接 TTS 了。

## 目标

拆成两步，中间加人工确认：

```
[改写预览按钮] → 字幕/ASR → DeepSeek 改写 → 文案展示在页面
                                                    ↓
                                          [确认使用此文案] → 去重 + TTS
```

## 要改的地方

### 1. 前端：加「改写预览」按钮

在 TTS 自动模式区域，`启用 DeepSeek 改写` checkbox 旁边加一个按钮：

```html
<button id="btn-rewrite-preview" class="btn btn-primary" type="button">
  🔍 DeepSeek 改写预览
</button>
```

点击后：
1. 读当前视频的探测结果（需要先探测过）
2. 读模板 textarea 内容
3. 调 MCP `rewrite_copy` 工具（传视频名 + 模板）
4. 按钮变 loading 态："⏳ 正在打开 DeepSeek 改写..."
5. 返回后把改写文案显示在页面上（新增一个预览区）

### 2. 前端：加改写结果预览区

在模板 textarea 下方加：

```html
<div id="rewrite-preview" style="display:none; margin-top:8px; padding:10px; border:1px solid #B7E4D0; border-radius:8px; background:#F5FFFA;">
  <div style="font-size:11px; color:#16845B; font-weight:600; margin-bottom:4px;">DeepSeek 改写结果</div>
  <div id="rewrite-preview-text" style="font-size:13px; color:var(--text); margin-bottom:8px;"></div>
  <button id="btn-use-rewrite" class="btn btn-mini" style="background:#16845B; color:#fff;">✅ 确认使用此文案</button>
  <button id="btn-discard-rewrite" class="btn btn-mini">❌ 不用，手动输入</button>
</div>
```

### 3. 前端 JS：逻辑

- `btn-rewrite-preview` click → `callTool("rewrite_copy", { src: "素材名.mp4", template: "模板文本" })`
  - MCP 工具需要扩展：能接收 `src` 参数，自动调用 probe → 字幕/ASR → DeepSeek
- 成功后：`#rewrite-preview` 显示、文案填入 `#rewrite-preview-text`
- `btn-use-rewrite` click → 把改写文案填入 TTS 文案区，切换到手动模式
- `btn-discard-rewrite` click → 隐藏预览

### 4. 后端 mcp_server.py：扩展 `rewrite_copy` 工具

当前 `rewrite_copy` 只接受 `text` 参数。需要增加 `src` 参数，自动走提取链路：

```python
{
    "name": "rewrite_copy",
    "inputSchema": {
        "type": "object",
        "properties": {
            "src": {"type": "string", "description": "视频文件名或路径"},
            "template": {"type": "string", "description": "改写模板"},
        },
        "required": ["src"],
    },
}
```

handler 逻辑：
1. `probe_video(src)` → 拿到 `has_subtitle` / `audio_codec`
2. 有字幕 → `get_subtitle_text()` 提取原文
3. 无字幕 + 有音频 → ASR 提取原文
4. 无文案 → 返回错误
5. 有文案 + 有 template → `copy_rewriter.rewrite(text, template)` 改写
6. 返回 `{original, rewritten}` 给前端

### 5. 前端：去重时直接用确认的文案

用户点了「确认使用此文案」后，rewrite_preview_text 的内容填入 tts-text textarea，同时切换到手动模式。之后正常点击「开始单条去重」就行。

## 不改的

- pipeline.py 中的自动改写链路保留不动（和手动模式互补）

## 关键文件

| 文件 | 改动 |
|------|------|
| `station/web/index.html` | 加改写预览按钮 + 预览区 |
| `station/web/app.js` | 改写预览/确认/丢弃逻辑 |
| `station/server/mcp_server.py` | `rewrite_copy` 工具支持 `src` 参数 |
