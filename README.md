# materials2textbook

面向数字教材构建的多源资料解析、知识点组织与多智能体教材生成系统。

## 文档

- [项目架构安排](./docs/项目架构安排.md)
- [多智能体编排开发计划](./docs/多智能体编排开发计划.md)
- [切片工具说明](./docs/切片工具说明.md)
- [多智能体编排生成论文精读](./docs/多智能体编排生成论文精读.md)
- [教材素材处理精简前瞻版](./docs/material_pipeline_forward_plan.md)
- [数据组织可行性复核](./docs/data_organization_feasibility_review.md)

## 多智能体编排 MVP

使用当前 TIG 样例片段运行：

```powershell
python scripts/run_agent_workflow.py
```

只使用人工审核通过的片段生成正式版草稿：

```powershell
python scripts/run_agent_workflow.py --approved-only
```

默认输出到：

```text
work_material1/05_final_deliverables/agent_workflow/
```

主要产物：

```text
textbook_outline.md/json   三级教材目录
textbook_draft.md/docx     教材草稿
textbook_final.md/docx     带审核修订提示的教材
review_report.md/json      审核报告
workflow_summary.json      工作流统计
```
