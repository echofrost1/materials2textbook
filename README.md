# Materials to Textbook

把一组本地教学素材自动整理成可审校、可导出的数字教材。

这个项目面向“换一个主题素材，就能重新生成一本教材”的场景。用户准备原始素材目录后，系统会完成素材台账、证据抽取、领域配置识别、全书大纲规划、逐章写作、审校修订，以及电子教材包导出。

## 功能概览

- 扫描本地素材，生成 `assets_manifest.xlsx` 和素材索引。
- 从视频、PPT、文档、音频、表格中生成 evidence JSONL。
- 使用 LLM 自动识别领域配置，不要求人工预写主题参数。
- 使用 LLM 自动生成全书大纲，失败时自动回退规则规划。
- 校验并修正大纲：默认至少 3 章，每章至少 3 节。
- 运行多智能体教材生成流程，产出 Markdown、Docx 和数字教材前端。
- 导出学生可用的 `digital_book.zip`。
- 保留人工覆盖入口：可传入外部 `domain_config.yml` 或 `book_plan.json`。

## 快速开始

进入仓库根目录：

```powershell
cd "<本仓库路径>"
```

安装依赖：

```powershell
pip install -r requirements.txt
```

准备素材目录：

```text
D:\textbook_runs\new_topic\
├── raw\                         原始素材
└── work_material1\              处理结果和最终输出
```

运行一键生成：

```powershell
python scripts/run_topic_textbook.py `
  --material-root D:\textbook_runs\new_topic\work_material1 `
  --raw-root D:\textbook_runs\new_topic\raw `
  --title "新主题数字教材" `
  --use-llm true `
  --student-package-output D:\textbook_runs\new_topic\work_material1\05_final_deliverables\digital_book.zip
```

只验证本地链路时可以不用 LLM：

```powershell
python scripts/run_topic_textbook.py `
  --material-root D:\textbook_runs\new_topic\work_material1 `
  --raw-root D:\textbook_runs\new_topic\raw `
  --title "新主题数字教材" `
  --use-llm false
```

## LLM 配置

启用 `--use-llm true` 前，需要配置 OpenAI-compatible Chat Completions 接口。

复制环境变量模板：

```powershell
copy .env.example .env
```

在 `.env` 中填写：

```text
OPENAI_API_KEY=...
OPENAI_BASE_URL=https://your-openai-compatible-endpoint/v1
OPENAI_MODEL=your_model
OPENAI_TEMPERATURE=0.2
OPENAI_MAX_TOKENS=4096
OPENAI_TIMEOUT_SECONDS=120
```

也可以直接通过命令行传入：

```powershell
python scripts/run_topic_textbook.py `
  --material-root D:\textbook_runs\new_topic\work_material1 `
  --title "新主题数字教材" `
  --use-llm true `
  --llm-base-url "https://your-openai-compatible-endpoint/v1" `
  --llm-api-key "your_api_key" `
  --llm-model "your_model"
```

LLM 调用默认缓存到：

```text
05_final_deliverables\agent_workflow\llm_cache.json
```

相关参数：

```text
--llm-cache-path path\to\llm_cache.json
--no-llm-cache
--llm-max-retries 2
--llm-retry-backoff 1.0
```

## 本地目录约定

推荐每个主题单独建目录：

```text
D:\textbook_runs\new_topic\
├── raw\
└── work_material1\
```

`raw\` 存放原始素材，可以包含：

```text
视频、音频、PPT、PDF、Word、Markdown、TXT、Excel、CSV
```

`work_material1\` 是处理和输出目录，最终结构通常为：

```text
work_material1\
├── 01_manifest_inventory\
├── 02_working_processing\
│   └── json\
├── 03_review_manual_check\
├── 04_assets_by_course\
└── 05_final_deliverables\
```

## 完整流程

### 1. 建立素材台账

如果只有原始素材，先扫描 `raw\`：

```powershell
python scripts/build_material_inventory.py `
  --material-root D:\textbook_runs\new_topic\work_material1 `
  --raw-root D:\textbook_runs\new_topic\raw
```

主要输出：

```text
01_manifest_inventory\assets_manifest.xlsx
02_working_processing\json\assets_manifest.json
04_assets_by_course\
```

### 2. 生成 Evidence JSONL

教材生成读取 `02_working_processing\json\` 下的 evidence 文件：

```text
video_segments.jsonl
ppt_assets.jsonl
reference_text_assets.jsonl
audio_segments.jsonl
structured_assets.jsonl
```

处理视频和 PPT：

```powershell
python scripts/process_material_block_mvp.py `
  --target-block automotive_repair `
  --limit-videos 20 `
  --limit-ppt 50 `
  --merge-main
```

处理参考文档：

```powershell
python scripts/process_reference_docs_mvp.py `
  --target-block automotive_repair `
  --limit-docs 100
```

处理音频和结构化素材：

```powershell
python scripts/process_audio_structured_mvp.py `
  --target-block automotive_repair `
  --limit-audio 20 `
  --limit-structured 50
```

### 3. 一键生成教材

```powershell
python scripts/run_topic_textbook.py `
  --material-root D:\textbook_runs\new_topic\work_material1 `
  --raw-root D:\textbook_runs\new_topic\raw `
  --title "新主题数字教材" `
  --use-llm true `
  --student-package-output D:\textbook_runs\new_topic\work_material1\05_final_deliverables\digital_book.zip
```

小样本 smoke test 可临时降低阈值：

```powershell
python scripts/run_topic_textbook.py `
  --material-root D:\textbook_runs\new_topic\work_material1 `
  --title "Smoke Topic" `
  --use-llm false `
  --min-evidence-chunks 2 `
  --min-candidate-chapters 1
```

生产运行不建议降低阈值。

## 自动规划机制

`run_topic_textbook.py` 会自动执行：

1. 检查或生成素材台账。
2. 读取 evidence JSONL。
3. 统计 evidence 数量和候选章节数量。
4. 自动生成领域配置。
5. 自动生成全书大纲。
6. 校验并修正大纲。
7. LLM 大纲失败时使用规则 fallback。
8. 运行 `TextbookWorkflow`。
9. 导出 `digital_book\`。
10. 可选打包 `digital_book.zip`。

大纲校验规则：

```text
至少 3 章
默认最多 12 章
每章至少 3 节
每节至少 1 个知识点
引用的 chunk_id 必须存在
知识点尽量能映射到 evidence
素材不足时标记缺口，不硬编不存在的内容
```

自动领域配置输出：

```text
01_manifest_inventory\domain_config.generated.yml
01_manifest_inventory\domain_config_review.md
```

自动大纲输出：

```text
01_manifest_inventory\book_plan.generated.json
01_manifest_inventory\book_plan_review.md
05_final_deliverables\agent_workflow\book_plan.json
```

## 人工覆盖

传入外部大纲：

```powershell
python scripts/run_topic_textbook.py `
  --material-root D:\textbook_runs\new_topic\work_material1 `
  --title "新主题数字教材" `
  --book-plan-input D:\textbook_runs\new_topic\book_plan.json `
  --use-llm true
```

传入外部领域配置：

```powershell
python scripts/run_topic_textbook.py `
  --material-root D:\textbook_runs\new_topic\work_material1 `
  --title "新主题数字教材" `
  --domain-config D:\textbook_runs\new_topic\domain_config.yml `
  --use-llm true
```

也可以使用完整流程脚本：

```powershell
python scripts/run_full_digital_textbook.py `
  --book-mode `
  --material-root D:\textbook_runs\new_topic\work_material1 `
  --title "新主题数字教材" `
  --domain-config D:\textbook_runs\new_topic\domain_config.yml `
  --book-plan-input D:\textbook_runs\new_topic\book_plan.json `
  --student-package-output D:\textbook_runs\new_topic\work_material1\05_final_deliverables\digital_book.zip
```

## 主要输出

```text
01_manifest_inventory\
├── assets_manifest.xlsx
├── domain_config.generated.yml
├── domain_config_review.md
├── book_plan.generated.json
└── book_plan_review.md

02_working_processing\json\
├── video_segments.jsonl
├── ppt_assets.jsonl
├── reference_text_assets.jsonl
├── audio_segments.jsonl
└── structured_assets.jsonl

03_review_manual_check\
└── pipeline_warnings.json

05_final_deliverables\
├── agent_workflow\
│   ├── book_plan.json
│   ├── textbook_final.md
│   ├── textbook_final.docx
│   ├── evidence_chunks.jsonl
│   ├── review_report.md
│   ├── workflow_summary.json
│   └── artifact_manifest.json
├── digital_book\
│   ├── index.html
│   ├── digital_book.json
│   ├── app.js
│   ├── styles.css
│   └── ask_config.js
└── digital_book.zip
```

打开电子教材：

```powershell
python scripts/open_digital_book.py
```

或直接打开：

```text
05_final_deliverables\digital_book\index.html
```

## 常见失败处理

### LLM 主题识别失败

系统会使用素材标题、目录名和 evidence 样本生成保守领域配置，然后继续进入规则大纲。

检查：

```text
01_manifest_inventory\domain_config_review.md
```

### LLM 大纲失败

系统会使用规则大纲，并在 review 中标记：

```text
planning_mode: rule_fallback
```

检查：

```text
01_manifest_inventory\book_plan_review.md
05_final_deliverables\agent_workflow\book_plan.json
```

### 部分素材处理失败

失败不会中断全流程。告警写入：

```text
03_review_manual_check\pipeline_warnings.json
```

只要有效 evidence 达到阈值，仍会继续生成教材。

### Evidence 不足

系统不会硬编教材，会停止并输出：

```text
05_final_deliverables\insufficient_material_report.md
```

处理方式：

- 增加原始素材。
- 处理更多 target block。
- 检查 JSONL 是否生成到正确目录。
- 检查 review_status 或 teaching_value 是否导致 evidence 被过滤。

## 验收清单

生成完成后至少确认：

```text
05_final_deliverables\agent_workflow\book_plan.json 存在
book_plan.json 中每章 sections 数量 >= 3
05_final_deliverables\agent_workflow\textbook_final.md 存在
05_final_deliverables\digital_book\digital_book.json 存在
05_final_deliverables\digital_book\index.html 存在
如指定 --student-package-output，digital_book.zip 存在
03_review_manual_check\pipeline_warnings.json 中没有阻断性错误
```

运行测试：

```powershell
python -m pytest
```

当前实现已验证：

```text
150 passed
```
