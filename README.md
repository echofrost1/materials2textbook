# materials2textbook

面向数字教材构建的多源资料解析、知识点组织与多智能体教材生成系统。

当前仓库分成两条协作线：

- 数据处理线：按照 `docs/material_pipeline_forward_plan.md` 建台账、去重、分类、切片和人工复核。
- 多智能体编排线：读取上游 `video_segments.jsonl`，生成三级教材目录、教材草稿、审核报告和 Word/Markdown 交付物。

## 快速开始

安装依赖：

```powershell
pip install -r requirements.txt
```

运行当前 TIG 样例的多智能体编排：

```powershell
python scripts/run_agent_workflow.py
```

先校验同伴产出的 `video_segments.jsonl`：

```powershell
python scripts/validate_video_segments.py
```

只使用人工审核通过的片段生成正式版草稿：

```powershell
python scripts/run_agent_workflow.py --approved-only
```

使用 OpenAI-compatible 的 `ecnu-plus` 生成教材正文并执行审核后修订：

```powershell
copy .env.example .env
# 编辑 .env，填入 ECNU_PLUS_API_KEY / ECNU_PLUS_BASE_URL / ECNU_PLUS_MODEL
python scripts/run_agent_workflow.py --use-llm
```

也可以直接传入：

```powershell
python scripts/run_agent_workflow.py --use-llm `
  --llm-base-url "https://your-openai-compatible-endpoint/v1" `
  --llm-api-key "your_api_key" `
  --llm-model "ecnu-plus"
```

运行测试：

```powershell
python -m pytest
```

## 默认输出

脚本默认读取：

```text
work_material1/02_working_processing/json/video_segments.jsonl
```

脚本默认输出到：

```text
work_material1/05_final_deliverables/agent_workflow/
```

主要产物：

```text
textbook_outline.md/json   三级教材目录
evidence_index.md          人工可读证据索引
textbook_draft.md/docx     教材草稿
textbook_final.md/docx     带审核修订提示的教材
review_report.md/json      审核报告
workflow_summary.json      工作流统计
```

校验脚本默认输出到：

```text
work_material1/05_final_deliverables/validation/
```

仓库中保留了一份已生成的样例输出：

[examples/outputs/agent_workflow](./examples/outputs/agent_workflow)

## 目录结构

```text
materials2textbook/
├── docs/                    方案、架构、分工和开发计划
├── examples/                可提交的轻量样例输入/输出
├── scripts/                 可直接运行的脚本入口
├── src/materials2textbook/  多智能体编排代码
├── tests/                   单元测试
└── work_material1/          当前数据处理工作区和中间产物
```

更详细的文件结构说明见：

[docs/file_structure.md](./docs/file_structure.md)

## 文档

- [项目架构安排](./docs/项目架构安排.md)
- [文件结构说明](./docs/file_structure.md)
- [多智能体编排开发计划](./docs/多智能体编排开发计划.md)
- [切片工具说明](./docs/切片工具说明.md)
- [多智能体编排生成论文精读](./docs/多智能体编排生成论文精读.md)
- [教材素材处理精简前瞻版](./docs/material_pipeline_forward_plan.md)
- [数据组织可行性复核](./docs/data_organization_feasibility_review.md)

## 当前能力

- 从上游 `video_segments.jsonl` 读取课程视频片段。
- 校验上游 `video_segments.jsonl` 的字段、时间码、状态、重复 ID 和证据完整性。
- 转换为统一 `EvidenceChunk`。
- 严格基于素材片段生成三级教材目录。
- 生成 Markdown 教材草稿和 Word 文档。
- 生成结构化审核报告和人工可读 Markdown 审核报告。
- 生成按章节/知识点组织的人工可读证据索引。
- 支持草稿模式使用 `Pending_Manual_Timecode` 片段。
- 默认排除 `rejected` 片段，可用 `--include-rejected` 临时调试。
- 支持 `--approved-only` 正式模式。
- 预留 OpenAI-compatible LLM 接口，可接入 `ecnu-plus` 生成教材正文并执行审核后修订。

## 后续方向

- 用 LLM 增强标题润色、ASR 纠错、教材正文改写和审核修订。
- 接入 PDF、PPT、Markdown 等非视频资料片段。
- 将 prompt、provider 和模型配置独立成稳定模块。
