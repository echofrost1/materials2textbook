# 文件结构说明

> 目标：明确当前仓库中“文档、代码、样例、真实工作区”的边界，避免数据处理线和多智能体编排线互相污染。

---

## 1. 顶层结构

```text
materials2textbook/
├── docs/
├── examples/
├── scripts/
├── src/materials2textbook/
├── tests/
├── work_material1/
├── README.md
├── requirements.txt
└── pyproject.toml
```

### `docs/`

保存项目方案、架构、分工、工具说明和论文精读。

这里放“说明项目怎么做”的文档，不放大体积数据，不放脚本输出。

### `examples/`

保存可以提交到 Git 的轻量样例。

当前样例输出在：

```text
examples/outputs/agent_workflow/
```

这些文件用于展示多智能体编排结果，不作为真实工作流的默认写入目录。

### `scripts/`

保存可直接运行的脚本入口。

当前主要脚本：

```text
scripts/run_first_round_tig.py     数据处理线样例脚本
scripts/run_agent_workflow.py      多智能体编排线入口
scripts/validate_video_segments.py 上游片段 JSONL 校验脚本
```

### `src/materials2textbook/`

保存可复用代码。

```text
src/materials2textbook/
├── adapters/      适配上游数据，如 video_segments.jsonl
├── agents/        Agent 角色，如目录规划、教材写作、审核
├── exporters/     导出器，如 Markdown 到 Word
├── llm/           OpenAI-compatible LLM Provider
├── prompts/       LLM Prompt 构造器
├── validators/    上游产物校验器
├── workflow/      编排、配置、统计和报告
├── schemas.py     核心数据结构
└── io_utils.py    文件读写工具
```

### `tests/`

保存单元测试，覆盖数据适配、目录生成、报告生成和工作流配置。

运行方式：

```powershell
python -m pytest
```

### `work_material1/`

当前真实数据处理工作区。这里保存上游处理产生的中间文件和本地运行输出。

```text
work_material1/
├── 01_manifest_inventory/
├── 02_working_processing/
└── 05_final_deliverables/
```

约定：

- `01_manifest_inventory/`：同伴维护的数据台账、人工确认表、片段表。
- `02_working_processing/`：转码、音频、转写、关键帧、JSONL 等中间产物。
- `05_final_deliverables/`：多智能体编排线生成的目录、教材、审核报告。

`work_material1/00_raw_client_materials/` 和默认编排输出目录不进入 Git。

---

## 2. 上下游接口

多智能体编排线默认读取：

```text
work_material1/02_working_processing/json/video_segments.jsonl
```

最低字段要求：

```text
clip_id
source_asset_id
source_video
original_path
start_time
end_time
subject
material_block
material_block_code
knowledge_point
clip_summary
tags
recommended_chapter
usefulness_score
transcript_status
evidence_text
keyframe_paths
review_status
```

多智能体编排线默认输出：

```text
work_material1/05_final_deliverables/agent_workflow/
```

输出目录默认不提交到 Git；如需保存一份示例，应复制或移动到 `examples/outputs/`。

上游片段校验默认输出：

```text
work_material1/05_final_deliverables/validation/
```

该目录也不提交到 Git，校验报告用于你和同伴对齐字段质量、时间码质量和人工复核状态。

---

## 3. 生成产物

```text
textbook_outline.md/json
evidence_chunks.jsonl
evidence_index.md
chapter_plan.json
textbook_draft.md/docx
review_report.md/json
workflow_summary.json
textbook_final.md/docx
```

说明：

- `textbook_outline.md/json`：三级教材目录，严格来自上游素材片段。
- `evidence_chunks.jsonl`：统一证据片段。
- `evidence_index.md`：人工可读证据索引，按章节和知识点列出来源、时间码、状态、关键帧和摘要。
- `chapter_plan.json`：面向写作 Agent 的章节计划。
- `textbook_draft.md/docx`：教材草稿。
- `review_report.md/json`：审核报告。
- `workflow_summary.json`：工作流统计。
- `textbook_final.md/docx`：带审核修订提示的最终草稿。

---

## 4. 现阶段不做的大重构

暂时不把 `schemas.py` 拆成多个文件，也不把每个 Agent 拆成复杂子包。

原因：

- 当前需求仍在快速变化。
- 接入 `ecnu-plus` 后，Provider、Prompt 和 LLM Agent 才会成为新的稳定边界。
- 过早拆太细会增加改动成本。

当前已经预留：

```text
src/materials2textbook/
├── llm/
│   └── provider.py
├── prompts/
│   └── textbook_writer.py
└── agents/
    └── textbook_writer.py
```

`textbook_writer.py` 默认使用规则版写作；启用 `--use-llm` 后，会调用 OpenAI-compatible Provider。
