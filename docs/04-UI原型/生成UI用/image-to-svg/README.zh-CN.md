# Image to SVG · 图片图标矢量化

[English](README.md) | [简体中文](README.zh-CN.md)

这是一个跨平台 Agent Skill，用于将 PNG、JPG、WebP 或截图中的图标、Logo、字形和文字标志重建为干净、可编辑、透明背景的 SVG 文件。它遵循开放的 [Agent Skills 规范](https://agentskills.io/specification)，可以用于 Codex、Claude Code、OpenCode 以及其他兼容 Agent。

它把用户提供的栅格图片作为唯一视觉依据，最终输出真实矢量几何，而不是在 SVG 里包裹原始位图。

## 主要能力

- 在矢量重建前，通过自动分析或显式视觉证据分离不透明、近纯色、纹理或棋盘格形式的背景。
- 使用路径、基础图形、渐变、遮罩和可复用几何重建平面图形。
- 保留图标与文字组合中的间距、对齐、负形和整体锁定关系。
- 在无法稳定依赖原字体时，将可见文字固化为矢量轮廓。
- 检查每个 SVG 是否嵌入位图或引用外部资源。
- 本机已有 Pillow、NumPy、Playwright 和 Chrome 时，自动用于增强测量和浏览器验收。
- 即使没有这些可选第三方依赖，也可以完成核心 SVG 重建。

## 效果示例

中间一列直接渲染仓库中的可编辑 SVG，画布本身为透明背景。差异叠加图中，青色代表原图独有像素，洋红色代表 SVG 独有像素，深色代表两者重合。

### 盾牌数据图标

Binary IoU `0.8906` · 5 条路径 · 34 个路径命令 · [打开可编辑 SVG](examples/shield-chart/result.svg)

| 原始栅格图 | 透明 SVG 渲染 | 差异叠加图 |
| --- | --- | --- |
| ![盾牌数据图标原始栅格图](examples/shield-chart/source.png) | ![盾牌数据图标透明 SVG 渲染](examples/shield-chart/result.svg) | ![盾牌数据图标差异叠加图](examples/shield-chart/difference-overlay.png) |

### 棱镜光束图标

Binary IoU `0.9689` · 10 条路径 · 29 个路径命令 · [打开可编辑 SVG](examples/prism-burst/result.svg)

| 原始栅格图 | 透明 SVG 渲染 | 差异叠加图 |
| --- | --- | --- |
| ![棱镜光束图标原始栅格图](examples/prism-burst/source.png) | ![棱镜光束图标透明 SVG 渲染](examples/prism-burst/result.svg) | ![棱镜光束图标差异叠加图](examples/prism-burst/difference-overlay.png) |

## 适用场景

- App 和 UI 图标
- Logo 与品牌图形
- 中文字标、英文字标和组合标志
- 平面插画与几何符号
- 带烘焙白底或假透明棋盘格的栅格素材

本 Skill 不适合普通照片矢量化。

## 平台兼容性

不同平台共用同一个仓库，跨平台入口是仓库根目录的 `SKILL.md`。

| 平台 | 个人/全局目录 | 项目目录 |
| --- | --- | --- |
| Codex | `~/.codex/skills/image-to-svg/` 或 `~/.agents/skills/image-to-svg/` | 使用当前 Codex 发行版支持的项目 Skill 目录 |
| Claude Code | `~/.claude/skills/image-to-svg/` | `.claude/skills/image-to-svg/` |
| OpenCode | `~/.config/opencode/skills/image-to-svg/` 或 `~/.agents/skills/image-to-svg/` | `.opencode/skills/image-to-svg/` 或 `.agents/skills/image-to-svg/` |
| 其他兼容 Agent | Host 注册的任意 Skill 目录 | Host 注册的任意项目 Skill 目录 |

`agents/openai.yaml` 只提供可选的 Codex/OpenAI 界面元数据。Claude Code、OpenCode 和其他兼容开放标准的 Agent 可以忽略该文件，直接读取 `SKILL.md`。

## 安装

根据使用的平台，选择一个发现目录进行克隆：

```bash
# Codex
git clone https://github.com/zyipeng/image-to-svg.git ~/.codex/skills/image-to-svg

# Claude Code
git clone https://github.com/zyipeng/image-to-svg.git ~/.claude/skills/image-to-svg

# OpenCode 原生目录
git clone https://github.com/zyipeng/image-to-svg.git ~/.config/opencode/skills/image-to-svg

# OpenCode 和其他兼容 Agent 支持的通用目录
git clone https://github.com/zyipeng/image-to-svg.git ~/.agents/skills/image-to-svg
```

如果所用 Agent 不支持实时发现 Skill，请重新启动 Agent 或新建会话。

核心工作流不要求额外安装依赖。可以先检查本机已有的增强验收环境：

```bash
cd /path/to/image-to-svg
python3 scripts/doctor.py
```

只有在明确希望建立独立运行环境时，才需要选择性安装：

```bash
python3 -m pip install -r requirements.txt
npm install
```

## 使用方法

上传一张栅格图片，然后对所用 Agent 说：

```text
使用 $image-to-svg，把这张 Logo 高精度重建为透明背景、可编辑的 SVG。
```

```text
把这张图标和文字组合截图转成一个 SVG，保留原始间距，并去掉图片里烘焙的背景。
```

默认交付透明背景、可编辑的 `.svg` 文件。如果本机存在增强 QA 环境，还会生成浏览器渲染图、对比图和指标报告。

## 质量验收流程

1. 检查透明度、背景、主体边界、颜色、对称性和组合关系。
2. 从源图中分离真正需要的前景。
3. 使用能够解释源像素的最简洁、平滑矢量几何进行重建。
4. 检查 SVG 是否包含嵌入位图或外部引用。
5. 条件允许时，在真实浏览器中按源图参考尺寸渲染。
6. 对比覆盖范围、边界、间距、拓扑和颜色，然后迭代校准。

Skill 不使用统一的 IoU 阈值作为机械验收标准：结构正确的平滑基础图形，通常比指标更高但轮廓嘈杂的自动描边更有价值。

## 仓库结构

```text
image-to-svg/
├── SKILL.md
├── agents/openai.yaml       # 可选的 Codex/OpenAI 界面元数据
├── examples/                # 原图、SVG 与视觉对比案例
├── scripts/
│   ├── analyze_raster.py
│   ├── compare_fit.py
│   ├── doctor.py
│   ├── render_svg.cjs
│   ├── self_test.py
│   └── svg_audit.py
├── references/
│   ├── geometry-recipes.md
│   └── qa-metrics.md
├── requirements.txt
└── package.json
```

## 验证

```bash
python3 scripts/self_test.py
python3 scripts/self_test.py --require-browser
```

浏览器完整测试会优先搜索系统和配置的 Agent 托管运行环境，不会默认要求重新安装依赖。如果某个 Agent 把可复用运行时放在其他位置，可以设置 `IMAGE_TO_SVG_RUNTIME_ROOTS`。
