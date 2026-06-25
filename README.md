# 一键教材生成使用说明（本地运行版）

本文说明如何在本地仓库中，从一个新的主题素材目录出发，完成素材登记、证据抽取、自动领域识别、自动大纲规划、多智能体教材生成、审校修订和电子教材导出。

推荐入口：

```powershell
python scripts/run_topic_textbook.py `
  --material-root D:\textbook_runs\new_topic\work_material1 `
  --raw-root D:\textbook_runs\new_topic\raw `
  --title "新主题数字教材" `
  --use-llm true `
  --student-package-output D:\textbook_runs\new_topic\work_material1\05_final_deliverables\digital_book.zip
```

也可以使用相对路径：

```powershell
python scripts/run_topic_textbook.py `
  --material-root .\local_runs\new_topic\work_material1 `
  --raw-root .\local_runs\new_topic\raw `
  --title "新主题数字教材" `
  --use-llm false
```

## 1. 本地环境准备

进入仓库根目录：

```powershell
cd "<本仓库路径>"
```

安装依赖：

```powershell
pip install -r requirements.txt
```

检查 Python 和测试：

```powershell
python --version
python -m pytest
```

如果只想验证一键脚本参数是否正常：

```powershell
python scripts/run_topic_textbook.py --help
```

## 2. LLM 配置

如果要使用自动领域识别、自动大纲规划和教材正文增强，建议先配置 OpenAI-compatible LLM。只验证本地链路时可以使用 `--use-llm false`。

方式一：写入 `.env`：

```powershell
copy .env.example .env
```

在 `.env` 中配置通用变量：

```text
OPENAI_API_KEY=...
OPENAI_BASE_URL=https://your-openai-compatible-endpoint/v1
OPENAI_MODEL=your_model
OPENAI_TEMPERATURE=0.2
OPENAI_MAX_TOKENS=4096
OPENAI_TIMEOUT_SECONDS=120
```

方式二：命令行传入：

```powershell
python scripts/run_topic_textbook.py `
  --material-root D:\textbook_runs\new_topic\work_material1 `
  --title "新主题数字教材" `
  --use-llm true `
  --llm-base-url "https://your-openai-compatible-endpoint/v1" `
  --llm-api-key "your_api_key" `
  --llm-model "your_model"
```

LLM 调用默认有缓存：

```text
05_final_deliverables\agent_workflow\llm_cache.json
```

可用参数：

```text
--llm-cache-path path\to\llm_cache.json
--no-llm-cache
--llm-max-retries 2
--llm-retry-backoff 1.0
```

## 3. 准备本地素材目录

建议每个主题单独建目录：

```text
D:\textbook_runs\new_topic\
├── raw\                         原始素材，只读保存
└── work_material1\              工作目录，放处理结果和最终输出
```

`raw\` 可以放视频、音频、PPT、PDF、Word、Markdown、TXT、Excel、CSV 等素材。原始文件建议不要改名、不要移动、不要删除；处理脚本会在 `work_material1\` 下生成台账和 JSONL 证据。

最终工作目录结构：

```text
work_material1\
├── 01_manifest_inventory\
├── 02_working_processing\
│   └── json\
├── 03_review_manual_check\
├── 04_assets_by_course\
└── 05_final_deliverables\
```

## 4. 建立素材台账

如果只有原始素材，先扫描 `raw\`，生成素材台账和索引层：

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

台账用途：

- 给每个原始文件分配 `asset_id`
- 记录路径、类型、大小、hash、预览文本等信息
- 识别重复和疑似重复素材
- 建立素材大块和后续处理队列

注意：台账只是登记和轻量分类，不等于已经完成教材 evidence 抽取。

## 5. 处理素材并生成 Evidence JSONL

教材生成读取的是：

```text
02_working_processing\json\
```

当前可识别的 evidence 文件：

```text
video_segments.jsonl
ppt_assets.jsonl
reference_text_assets.jsonl
audio_segments.jsonl
structured_assets.jsonl
```

### 4.1 视频和 PPT

按素材大块处理视频/PPT：

```powershell
python scripts/process_material_block_mvp.py `
  --target-block automotive_repair `
  --limit-videos 20 `
  --limit-ppt 50 `
  --merge-main
```

常用参数：

```text
--target-block      要处理的素材大块
--limit-videos      限制本次处理视频数量，0 表示不限
--limit-ppt         限制本次处理 PPT 数量，0 表示不限
--dry-run           只打印将要处理的素材，不生成结果
--merge-main        将 batch 输出合并进主 video_segments.jsonl / ppt_assets.jsonl
```

输出：

```text
02_working_processing\json\video_segments.jsonl
02_working_processing\json\ppt_assets.jsonl
```

### 4.2 参考文档

处理 PDF、Word、Markdown、TXT 等文档：

```powershell
python scripts/process_reference_docs_mvp.py `
  --target-block automotive_repair `
  --limit-docs 100
```

输出：

```text
02_working_processing\json\reference_text_assets.jsonl
```

### 4.3 音频和结构化素材

处理音频、Excel、CSV 等结构化素材：

```powershell
python scripts/process_audio_structured_mvp.py `
  --target-block automotive_repair `
  --limit-audio 20 `
  --limit-structured 50
```

输出：

```text
02_working_processing\json\audio_segments.jsonl
02_working_processing\json\structured_assets.jsonl
```

### 4.4 校验 Evidence

视频片段可先校验：

```powershell
python scripts/validate_video_segments.py
```

生产运行建议至少满足：

```text
有效 evidence chunks >= 20
候选章节或素材大块 >= 3
```

如果低于阈值，一键脚本会停止生成教材，并输出素材不足报告。

## 6. 一键生成教材

当 `02_working_processing\json\` 下已有 evidence JSONL 后，运行：

```powershell
python scripts/run_topic_textbook.py `
  --material-root D:\textbook_runs\new_topic\work_material1 `
  --raw-root D:\textbook_runs\new_topic\raw `
  --title "新主题数字教材" `
  --use-llm true `
  --student-package-output D:\textbook_runs\new_topic\work_material1\05_final_deliverables\digital_book.zip
```

如果只是验证本地链路，不使用 LLM：

```powershell
python scripts/run_topic_textbook.py `
  --material-root D:\textbook_runs\new_topic\work_material1 `
  --title "新主题数字教材" `
  --use-llm false
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

## 7. 自动规划机制

`run_topic_textbook.py` 会自动执行：

1. 检查或生成素材台账
2. 读取 `02_working_processing\json\` 下的 evidence
3. 统计 evidence 数量和候选章节数量
4. 调用 `DomainConfigAgent` 自动生成领域配置
5. 调用 LLM 自动规划全书大纲
6. 程序校验并修正大纲
7. LLM 大纲失败时使用规则 fallback
8. 运行 `TextbookWorkflow`
9. 导出 `digital_book\`
10. 可选打包 `digital_book.zip`

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

如果某章不足 3 节，系统会优先用 evidence 聚类补足；素材确实不足时，会保留缺口小节并在 review 文件中标记。

## 8. 人工覆盖入口

默认不需要人工规划大纲。如果已有外部大纲，可以直接传入：

```powershell
python scripts/run_topic_textbook.py `
  --material-root D:\textbook_runs\new_topic\work_material1 `
  --title "新主题数字教材" `
  --book-plan-input D:\textbook_runs\new_topic\book_plan.json `
  --use-llm true
```

如果已有领域配置：

```powershell
python scripts/run_topic_textbook.py `
  --material-root D:\textbook_runs\new_topic\work_material1 `
  --title "新主题数字教材" `
  --domain-config D:\textbook_runs\new_topic\domain_config.yml `
  --use-llm true
```

也可以继续使用原完整脚本：

```powershell
python scripts/run_full_digital_textbook.py `
  --book-mode `
  --material-root D:\textbook_runs\new_topic\work_material1 `
  --title "新主题数字教材" `
  --domain-config D:\textbook_runs\new_topic\domain_config.yml `
  --book-plan-input D:\textbook_runs\new_topic\book_plan.json `
  --student-package-output D:\textbook_runs\new_topic\work_material1\05_final_deliverables\digital_book.zip
```

## 9. 主要输出

一键流程成功后，重点看：

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

## 10. 失败和告警处理

### 10.1 LLM 主题识别失败

系统会使用素材标题、目录名和 evidence 样本生成保守领域配置，然后继续进入规则大纲。

检查：

```text
01_manifest_inventory\domain_config_review.md
```

### 10.2 LLM 大纲失败

系统会使用规则大纲，并在 review 中标记：

```text
planning_mode: rule_fallback
```

检查：

```text
01_manifest_inventory\book_plan_review.md
05_final_deliverables\agent_workflow\book_plan.json
```

### 10.3 部分素材处理失败

失败不会中断全流程。告警写入：

```text
03_review_manual_check\pipeline_warnings.json
```

只要有效 evidence 达到阈值，仍会继续生成教材。

### 10.4 Evidence 不足

系统不会硬编教材，会停止并输出：

```text
05_final_deliverables\insufficient_material_report.md
```

处理方式：

- 增加原始素材
- 处理更多 target block
- 检查 JSONL 是否生成到正确目录
- 检查 review_status 或 teaching_value 是否导致 evidence 被过滤

### 10.5 某章素材不足

系统会继续生成，但会在大纲评审中标记章节缺口。章末正文也应列出素材缺口。

重点检查：

```text
01_manifest_inventory\book_plan_review.md
05_final_deliverables\agent_workflow\textbook_final.md
```

## 11. 推荐完整命令模板

### 11.1 从 raw 开始

```powershell
cd "<本仓库路径>"

python scripts/build_material_inventory.py `
  --material-root D:\textbook_runs\new_topic\work_material1 `
  --raw-root D:\textbook_runs\new_topic\raw

python scripts/process_material_block_mvp.py `
  --target-block automotive_repair `
  --limit-videos 50 `
  --limit-ppt 100 `
  --merge-main

python scripts/process_reference_docs_mvp.py `
  --target-block automotive_repair `
  --limit-docs 200

python scripts/process_audio_structured_mvp.py `
  --target-block automotive_repair `
  --limit-audio 50 `
  --limit-structured 100

python scripts/run_topic_textbook.py `
  --material-root D:\textbook_runs\new_topic\work_material1 `
  --raw-root D:\textbook_runs\new_topic\raw `
  --title "汽车维修数字教材" `
  --use-llm true `
  --student-package-output D:\textbook_runs\new_topic\work_material1\05_final_deliverables\digital_book.zip
```

### 11.2 已经有 Evidence JSONL

```powershell
cd "<本仓库路径>"

python scripts/run_topic_textbook.py `
  --material-root D:\textbook_runs\new_topic\work_material1 `
  --title "新主题数字教材" `
  --use-llm true `
  --student-package-output D:\textbook_runs\new_topic\work_material1\05_final_deliverables\digital_book.zip
```

### 11.3 不用 LLM，仅验证链路

```powershell
python scripts/run_topic_textbook.py `
  --material-root D:\textbook_runs\new_topic\work_material1 `
  --title "新主题数字教材" `
  --use-llm false
```

## 12. 验收清单

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
