# AI PRD Writer

这是基于原 `prd-document-writer` 升级后的 PRD 生成 Skill。

## 本版重点

- 将图片中的“基础七项 + AI 补充六项”完整固化为 13 个必填节点：
  - 基础七项：产品名称、用户、核心场景、核心问题、MVP 方案、本期做什么、本期不做什么。
  - AI 补充六项：输入输出、workflow、AI 作用、验证指标、badcase 兜底、风险与边界。
- 保留原 Skill 的三段式用户故事写法和表格验收标准。
- 保留并增强文本需求脑图，加入输入输出、AI、指标、兜底和边界。
- 加入产品经理三方向、四视角决策框架，强调 PRD 的核心价值是输出高质量决策。
- 默认生成简版 AI PRD；用户要求完整版时追加研发执行附录。
- 信息不足时生成带假设的初稿，不编造事实。

## 文件结构

```text
ai-prd-writer/
├── SKILL.md
├── README.md
├── agents/
│   └── openai.yaml
├── references/
│   ├── prd-template.md
│   ├── user-story-guide.md
│   ├── requirement-mindmap-guide.md
│   ├── quality-checklist.md
│   └── execution-appendix.md
└── examples/
    └── resume-jd-gap-analysis-prd.md
```

## 典型触发语

- “把这个产品想法写成一份简版 AI PRD。”
- “补全这份 PRD 的输入输出、workflow、指标和 badcase。”
- “评审这份 PRD 是否能进入研发。”
- “保留用户故事和需求脑图，帮我重写 PRD。”
