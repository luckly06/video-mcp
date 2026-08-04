# video-uniqueness · 视频去重数字员工工位

把 **CRVideoMate 的视频去重能力**，用它同捆的 **ffmpeg** 复现成一个符合 **MCP 2026-07-28 无状态规范**的本地 Server，外面套一个原生 Web「Agent 工位」壳来驱动。对齐两篇工程方法论文章：腾讯云《MCP + Skills + Hooks 三件套》与阿里《Agent 工位五层》。

> **为什么不直接调 `CRVideoMate.exe`？**
> 它是闭源 GUI，无命令行接口、无 SDK，程序驱动不了。所以本工程用它**同捆的 ffmpeg**（已拷进 `station/vendor/ffmpeg/`）复现整条去重管线：画面微调、微旋转、帧率、降噪、码率、加/去水印、裂变。`CRVideoMate/` 目录只是原始工具的**归档备份**，与工位运行**互不依赖**。

---

## 目录结构

```
video-uniqueness/
├── station/            ← 工程本体（代码 + 依赖 + 素材，自包含）
│   ├── server/         MCP 无状态 Server（mcp_server.py）+ ffmpeg 去重管线（pipeline.py）
│   ├── hooks/          PreToolUse / PostToolUse / SessionStart 三个 Hook
│   ├── shared/         rules.json（四级分级 + 强制走链，外化）+ skills/（Mermaid SOP）
│   ├── web/            Agent 工位壳（原生 HTML/CSS/JS，零框架）
│   ├── vendor/         同捆 ffmpeg + 水印模板（自包含，脱离外部路径）
│   ├── assets/         素材目录（仅此目录内的视频可被访问）
│   ├── logs/           audit.jsonl（审计流）+ jobs.json（任务 handle）
│   ├── run.py          一键启动
│   └── README.md       ★ 工程技术细节（协议、五层落地、验证记录）看这份
├── CRVideoMate/        ← 原始 GUI 工具归档（CRVideoMate.exe + 同捆 ffmpeg/水印，只读备份）
├── output/             ← 去重 / 裂变产物统一输出到这里
└── README.md           （本文件，顶层导览）
```

---

## 一键启动

```bash
cd station
python run.py
```

`run.py` 会启动 Server（`127.0.0.1:8765`）并用默认浏览器打开工位页。也可分开跑：

```bash
python station/server/mcp_server.py      # 起 server（单实例，重复启动会因端口独占而报错退出）
# 然后浏览器打开 station/web/index.html
```

依赖：**Python 3.x 标准库**即可，无需 `pip install`；ffmpeg 已同捆在 `station/vendor/`。

---

## 使用流程（Web 工位）

1. **选素材**（下拉列表来自 `station/assets/`，工位仅可访问此目录）
2. 先点 **🔍 探测**（`probe_video`，读分辨率/时长/码率/MD5）
3. 再点 **▶ 去重**（`dedup_video`）→ 弹**人工决策框** → 确认 → 出**三项自检**报告
4. 可选 **裂变**（`batch_fission`，同素材生成多个 MD5 互异变体）

> ⚠️ **必须先探测再去重**：这是 Hook 层的**强制走链**约束（`dedup_video` / `batch_fission` 前置必须有成功的 `probe_video`），防止用错误参数处理。跳过探测直接去重会被拦截。

产物统一落在工程根的 **`output/`** 目录。

---

## 安全分级（四级，外化在 `station/shared/rules.json`）

| 级别 | 含义 | 工具 |
|------|------|------|
| 🟢 **pass** | 静默放行 | `check_env` |
| 🟢 **audit** | 放行 + 审计 | `list_assets` `probe_video` `get_job` `list_watermark_templates` `list_jobs` |
| 🟠 **warned** | 弹窗人工确认 | `dedup_video` `batch_fission` `add_watermark` `remove_watermark` |
| 🔴 **blocked** | 硬阻断（Agent 不可直接调，须人工在 Web 确认） | `delete_output` |

**人机接力**：Agent 干到「70 分」（去重完成 + 三项自检）→ 人工决策是否交付 / 是否再裂变。决策点用 MCP 2026-07-28 的 `input_required` + `requestState` 承载，无状态可重放。

---

## 去重原理

改变文件 MD5 与视频特征（画面微调、微旋转、帧率、码率、元数据清除），**保持分辨率不变**，绕过平台重复检测。裂变模式下同素材产出多个 MD5 互异的变体。

**三项自检**：`md5_changed`（MD5 已变）/ `resolution_kept`（分辨率保持）/ `duration_close`（时长一致）。

---

## 边界说明

- 仅做**素材去重处理**，不剪辑、不上传、不越权访问 `assets/` 以外目录。
- `delete_output` 受硬阻断保护，须人工确认，防误删产出。
- 不修改、不驱动 `CRVideoMate/`（仅作原始工具归档，只读）。
- 全部路径为 `__file__` 相对锚定，无硬编码绝对路径，clone / push 到 GitHub 后可直接运行。

---

## 更多

工程的技术细节——MCP 2026-07-28 无状态协议实现、Skills/Hooks 五层落地、端到端验证记录——见 **[`station/README.md`](station/README.md)**。
