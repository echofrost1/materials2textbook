# Examples

这里保存可以提交到 Git 的轻量样例。

当前样例：

```text
examples/outputs/agent_workflow/
```

它展示了 `scripts/run_agent_workflow.py` 基于 TIG 样例片段生成的结果，包括：

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

真实运行时，脚本默认写入：

```text
work_material1/05_final_deliverables/agent_workflow/
```

该目录属于本地工作区，不默认进入 Git。
