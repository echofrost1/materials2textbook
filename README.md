# materials2textbook

面向数字教材构建的多源资料解析、知识点组织与多智能体教材生成系统。

当前仓库分成两条协作线：

- 数据处理线：按照 `docs/material_pipeline_forward_plan.md` 建台账、去重、分类、切片和自动质量标记。
- 多智能体编排线：读取上游 XLSX/JSONL，生成全书规划、教材草稿、审核报告、Word/Markdown 交付物，以及可被前端阅读器渲染的电子教材包。

## 快速开始

安装依赖：

```powershell
pip install -r requirements.txt
```

同伴电脑上已经有完整素材目录时，一键生成可被前端阅读的整本电子教材。推荐使用整本教材模式，并优先读取 `asset_block_map.xlsx` 自动规划章、节和知识点：

```powershell
python scripts/run_full_digital_textbook.py `
  --book-mode `
  --manifest-xlsx /ai/data/materials2textbook/work_material1/01_manifest_inventory/asset_block_map.xlsx `
  --material-root /ai/data/materials2textbook/work_material1 `
  --title "焊接技术数字教材" `
  --max-video-records 40 `
  --max-document-records 500 `
  --output-dir /ai/data/materials2textbook/work_material1/05_final_deliverables/agent_workflow `
  --student-package-output /ai/data/materials2textbook/work_material1/05_final_deliverables/digital_book.zip
```

默认采用媒体引用模式，不重复复制 `converted_mp4/` 中的视频；大素材目录推荐这样运行。需要打包小样例时再追加 `--copy-media-assets`。

默认会读取：

```text
/ai/data/materials2textbook/work_material1/02_working_processing/json/video_segments.jsonl
/ai/data/materials2textbook/work_material1/02_working_processing/json/ppt_assets.jsonl
```

并输出：

```text
/ai/data/materials2textbook/work_material1/05_final_deliverables/agent_workflow/
/ai/data/materials2textbook/work_material1/05_final_deliverables/digital_book/index.html
```

打开最新生成的数字教材：

```powershell
python scripts/open_digital_book.py
```

Windows 下也可以直接双击仓库根目录的：

```text
open_digital_book.bat
```

同伴电脑上的完整运行说明见 [docs/同伴素材一键生成电子教材.md](./docs/同伴素材一键生成电子教材.md)。

如果要继续处理新的素材大块或新章节，先按 [docs/material_preprocessing_scripts.md](./docs/material_preprocessing_scripts.md) 生成 batch、校验 batch，再合并进主 `video_segments.jsonl` / `ppt_assets.jsonl`。

运行当前 TIG 样例的多智能体编排：

```powershell
python scripts/run_pipeline.py
```

先校验同伴产出的 `video_segments.jsonl`：

```powershell
python scripts/validate_video_segments.py
```

如需进入更严格的演示模式，可以只使用已标记通过的片段生成草稿：

```powershell
python scripts/run_agent_workflow.py --approved-only
```

执行多轮审核-修订闭环：

```powershell
python scripts/run_agent_workflow.py --review-rounds 2
```

同时接入 PDF/PPT/Markdown/Text 等文档片段：

```powershell
python scripts/run_agent_workflow.py --document-segments examples/document_segments.jsonl
```

汇总阅读器导出的学生学习数据包，生成班级学习报告：

```powershell
python scripts/build_class_learning_report.py `
  --input-dir examples/study_data `
  --output-dir /ai/data/materials2textbook/work_material1/05_final_deliverables/class_learning_report
```

对生成的 `digital_book.json` 执行问书，默认使用本地检索并列出证据来源：

```powershell
python scripts/ask_digital_book.py "送丝操作要注意什么" `
  --book /ai/data/materials2textbook/work_material1/05_final_deliverables/digital_book/digital_book.json `
  --output /ai/data/materials2textbook/work_material1/05_final_deliverables/digital_book/ask_book_answer.md
```

如需生成式回答，可在配置 OpenAI-compatible LLM 后追加 `--use-llm`。

如果希望阅读器页面里的“AI 问书”调用服务端 LLM，而不是只做本地检索，可启动教师端问书服务：

```powershell
python scripts/serve_digital_book_ask.py --host 127.0.0.1 --port 8120
```

然后把生成目录里的 `digital_book/ask_config.js` 改为：

```javascript
window.DIGITAL_BOOK_ASK_ENDPOINT = 'http://127.0.0.1:8120/ask';
```

前端只发送问题和已命中的教材片段，API key 留在服务端；接口未配置或不可用时会自动回退到本地检索结果。

阅读器也可以把学习进度、笔记和书签同步到教师端目录，供班级报告脚本汇总：

```powershell
python scripts/serve_study_data_sync.py --host 127.0.0.1 --port 8121 `
  --output-dir /ai/data/materials2textbook/work_material1/05_final_deliverables/study_data_submissions
```

然后把 `digital_book/ask_config.js` 中的学习数据端点改为：

```javascript
window.DIGITAL_BOOK_STUDY_ENDPOINT = 'http://127.0.0.1:8121/study-data';
```

使用 OpenAI-compatible 的 `ecnu-plus` 增强资料分析、生成教材正文并执行审核后修订：

```powershell
copy .env.example .env
# 编辑 .env，填入 ECNU_PLUS_API_KEY / ECNU_PLUS_BASE_URL / ECNU_PLUS_MODEL
python scripts/run_agent_workflow.py --use-llm
```

`--use-llm` 默认会把调用结果缓存到 `agent_workflow/llm_cache.json`，便于多轮审核、修订和重复调试时复用；需要指定位置可加 `--llm-cache-path path/to/llm_cache.json`，临时关闭可加 `--no-llm-cache`。LLM 调用默认失败重试 2 次，可用 `--llm-max-retries` 和 `--llm-retry-backoff` 调整。

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
/ai/data/materials2textbook/work_material1/02_working_processing/json/video_segments.jsonl
```

脚本默认输出到：

```text
/ai/data/materials2textbook/work_material1/05_final_deliverables/agent_workflow/
```

主要产物：

```text
textbook_outline.md/json   三级教材目录
evidence_index.md          人工可读证据索引
textbook_draft.md/docx     教材草稿
textbook_final.md/docx     带审核修订提示的教材
review_report.md/json      审核报告
review_history.json        多轮审核-修订历史
revision_diff.md           草稿到最终稿的差异和变更清单
workflow_summary.json      工作流统计
artifact_manifest.json     本次运行的输入、输出和摘要
```

电子教材包输出：

```text
/ai/data/materials2textbook/work_material1/05_final_deliverables/digital_book/
├── digital_book.json       项目、任务、正文、视频、练习和证据引用
├── index.html              本地电子教材阅读器入口
├── styles.css
├── ask_config.js           可选 AI 问书服务端点配置，默认留空走本地检索
├── app.js
└── assets/
    ├── videos/
    ├── keyframes/
    └── images/
```

校验脚本默认输出到：

```text
/ai/data/materials2textbook/work_material1/05_final_deliverables/validation/
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
└── pyproject.toml
```

真实工作区和中间产物不在仓库内，默认位于：

```text
/ai/data/materials2textbook/work_material1
```

## 文档

- [项目架构安排](./docs/项目架构安排.md)
- [多智能体编排开发计划](./docs/多智能体编排开发计划.md)
- [切片工具说明](./docs/切片工具说明.md)
- [多智能体编排生成论文精读](./docs/多智能体编排生成论文精读.md)
- [教材素材处理精简前瞻版](./docs/material_pipeline_forward_plan.md)
- [数据组织可行性复核](./docs/data_organization_feasibility_review.md)
- [同伴素材一键生成电子教材](./docs/同伴素材一键生成电子教材.md)

## 当前能力

- 从上游 `video_segments.jsonl` 读取课程视频片段。
- 校验上游 `video_segments.jsonl` 的字段、时间码、状态、重复 ID 和证据完整性。
- 转换为统一 `EvidenceChunk`。
- 严格基于素材片段生成三级教材目录。
- 保守润色章节、目录和知识点标题，让泛标题结合素材大块形成更适合教材阅读的标题，同时不改变证据范围。
- 生成内部知识点顺序、难度标记、聚类标记和先修关系；这些字段用于规划和审核，不作为学生端主阅读内容展示。
- 生成观察定位、复述解释、分析迁移三层结构化学习活动，并绑定知识点、证据片段和评价量规。
- 生成基于证据片段的案例示例，包含学生实训情境、迁移判断、例题、参考分析和 `evidence_chunk_ids`，待复核片段会保留谨慎表述。
- 生成 Markdown 教材草稿和 Word 文档。
- 生成结构化审核报告和人工可读 Markdown 审核报告。
- 审核教材草稿中的证据引用，发现丢失或不存在的 `chunk_id`。
- 执行段落级事实支撑评分，发现无引用段落、未知引用和待复核证据未标注问题。
- 执行断言级事实支撑评分，发现无引用断言、未知引用断言、待复核证据未标注断言，以及同一证据/主题下的要求性-禁止性冲突。
- `--use-llm` 模式下可追加事实支撑、引用覆盖、教学目标和难度梯度深审。
- `--use-llm` 默认启用持久化 LLM 调用缓存和失败重试，资源分析、正文生成、事实核验、教学深审和修订共用同一调用保护层。
- 支持 `--review-rounds` 执行多轮审核-修订，并输出 `review_history.json`。
- 输出 `revision_diff.md`，保留草稿到最终稿的 Markdown 差异和变更清单。
- 输出证据覆盖率、引用覆盖率、段落事实支撑率、断言事实支撑率、可正式使用证据率、教学结构完整度和综合质量评分。
- 输出活动质量评分，检查练习难度梯度、证据绑定、知识点覆盖和评价量规。
- 输出案例质量评分，检查案例示例的证据绑定、知识点覆盖、学生画像/学习情境和迁移应用要求。
- 生成按章节/知识点组织的人工可读证据索引。
- 支持 `--book-mode` 整本教材规划：优先读取 XLSX/manifest，生成 `book_plan.json`、`book_outline.md`、`book_plan_review.md` 和 `curriculum_order.generated.yml`。
- 可从 `asset_block_map.xlsx` 自动生成课程排序和章-节结构：`material_block_cn` 作为章，`knowledge_point_cn` 作为节/知识点，并按内置焊接课程顺序稳定排序。
- 生成 `digital_book.json` 和本地前端阅读器，支持章-节目录、正文、视频、关键帧、案例示例、练习、全文搜索、阅读进度、书签、笔记、字号缩放、本地检索式 AI 问书、可选服务端 LLM 问书、学习数据 JSON 包导出/导入和可选服务端同步。
- 数字教材正文会按全书规划拆成多个小节任务；学生端不再显示系统化的“学习路径”和“重点词”块。
- 案例生成会过滤课程标准/目录型表格文本，避免把“模块、课程、教学要求、授课方法”等内部素材长串直接展示给学生。
- 提供 `scripts/open_digital_book.py` 和 `open_digital_book.bat` 一键启动本地预览服务器并打开最新正式数字教材。
- 支持对 `digital_book.json` 执行命令行问书：默认检索教材片段并列证据来源，`--use-llm` 时可基于检索片段生成回答且保留 `evidence_chunk_ids`。
- 支持聚合多个学习数据 JSON 包，生成班级学习报告，统计平均进度、当前位置分布、热门书签、笔记提交数和异常数据包。
- 班级学习报告同时输出 JSON、Markdown 和可直接打开的 HTML 页面。
- 支持草稿模式使用 `Pending_Manual_Timecode` 片段。
- 默认排除 `rejected` 片段，可用 `--include-rejected` 临时调试。
- 支持 `--approved-only` 正式模式。
- 预留 OpenAI-compatible LLM 接口，可接入 `ecnu-plus` 增强资料分析、生成教材正文并执行审核后修订。
- 支持 `--document-segments` 接入上游已解析的 PDF/PPT/Markdown/Text 文档片段，并统一转换为 `EvidenceChunk`。

## 后续方向

完成状态审计见 [docs/多智能体编排完成审计.md](./docs/多智能体编排完成审计.md)。

- 继续增强标题风格统一、人工可编辑标题表和更细粒度的前端问书交互。
- 继续增强二进制 PDF/PPT 自动解析能力；当前编排线已接收上游解析后的 `document_segments.jsonl`。
- 继续细化更复杂的跨段落语义一致性和学生画像适配细分规则。
