# UI/UX Designer

UI/UX Designer is a model-agnostic design advisor skill for AI agents. It helps with UI critique, UX critique, component-choice review, interaction-flow review, design direction, HTML mockups, SVG icons, handwritten wordmarks, and file-based design feedback.

The skill defines a structured design workflow that any AI agent (Claude Code, GPT, Gemini, etc.) can execute directly using its own reasoning capability. There is no external model call, no API key requirement, and no CLI dependency.

## What This Skill Does

The skill provides five analysis modes:

| Mode | Purpose | Output |
|---|---|---|
| `ui` | Visual/UI review: layout, hierarchy, typography, color, spacing, component appearance | Markdown |
| `ux` | UX/interaction review: task model, component choice, flow, friction, state behavior | Markdown |
| `direction` | Art direction: visual metaphor, design imagery, materiality, composition, motion | Markdown |
| `html` | Standalone HTML mockup or concept page | Raw HTML |
| `svg` | SVG icon, simple illustration, or handwritten wordmark | Raw SVG |

Each mode has a dedicated prompt template embedded in `SKILL.md` that structures the design reasoning. The executing agent applies the template to its own analysis — the templates are not sent to an external model.

## Model-Agnostic Design

This skill is provider-independent:

- No specific AI provider required (Grok, OpenAI, Anthropic, Google, etc.)
- No external API calls
- No API keys
- No provider-specific CLI tools
- No installation or authorization steps

Any agent that can read `SKILL.md` and follow its workflow can execute this skill.

## What Agents Should Know

- The analysis is stateless. It only sees the task text, reference files, and reference images included in the current request.
- For visual/UI review, use the `ui` mode.
- For UX, component-choice, task-flow, interaction, friction, or state-feedback review, use the `ux` mode.
- For combined UI + UX review, run `ui` and `ux` separately and synthesize the outputs.
- For broad art direction or design imagery markdown, use the `direction` mode.
- For new standalone HTML mockups, use the `html` mode.
- For SVG icons, simple illustrations, and single handwritten wordmarks, use the `svg` mode.
- Pass complete relevant files when the analysis needs to judge an existing design.
- Pass screenshots or visual references when the visible result or state sequence matters.
- Do not ask the skill to patch project files directly. Use its advice, then apply the changes in the workspace.

## Usage Examples

```
ui mode: "给这个页面提视觉/UI设计建议"
  reference files: ./design.html
  output: design-page-ui.md

ux mode: "评审这个页面的任务流、交互摩擦和状态反馈"
  reference files: ./design.html
  output: design-page-ux.md

ui,ux mode: "同时从 UI 和 UX 角度评审这个标签编辑组件"
  reference files: ./TagEditor.tsx
  output: tag-editor-review.md (writes tag-editor-review-ui.md and tag-editor-review-ux.md)

direction mode: "给这个产品生成设计意象 markdown"
  output: product-design-imagery.md

html mode: "生成一个完整的产品页面设计稿"
  reference files: ./brief.md
  output: ./designs/product-page.html

svg mode: "为 Museon 生成一个手写 SVG 字标"
  output: museon-wordmark.svg
```

## Repository Layout

```text
SKILL.md      # Skill definition: modes, workflow, prompt templates, integrity checks
README.md     # This file
```

`SKILL.md` tells agents when and how to perform each design analysis mode. It contains the complete prompt templates for all five modes, the context rules, the output integrity checks, and the workflow steps.
