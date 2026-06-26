# DTextbooks

DTextbooks 用于把本地教学素材整理、规划并生成项目化数字教材。本文档面向 Windows 试用环境：试用方会收到已经处理好的工作素材包，同时本地保存有对应的原始视频、PPT、Word、PDF、表格、音频等大文件。

推荐试用流程：

1. 解压素材包，例如 `work_material1` 或 `work_material_panjunyi`。
2. 把原始大文件放入试用根目录下的 `raw` 目录。
3. 根据需要选择“复用已有中间结果生成教材”或“重新处理原始素材后生成教材”。
4. 通过本地 HTTP 服务打开生成的数字教材。

## 1. 环境准备

打开 PowerShell，进入项目仓库根目录：

```powershell
cd "D:\DTextbooks"
```

安装 Python 依赖：

```powershell
pip install -r requirements.txt
```

如果需要重新处理原始素材，建议同时准备：

- FFmpeg：用于视频、音频处理。
- Microsoft Office 或 LibreOffice：用于 PPT、Word 等文件转换。
- OpenAI-compatible 大模型服务：用于高质量的大纲规划、正文生成、审校、能力图谱和练习题生成。

如果只是复用随包提供的 JSONL 中间结果生成教材，一般不需要重新跑视频/PPT/文档处理。

## 2. LLM 配置

启用 `--use-llm` 前，需要配置 OpenAI-compatible Chat Completions 接口。

复制环境变量模板：

```powershell
copy .env.example .env
```

编辑 `.env`：

```text
OPENAI_API_KEY=your_api_key
OPENAI_BASE_URL=https://your-openai-compatible-endpoint/v1
OPENAI_MODEL=your_model
OPENAI_TEMPERATURE=0.2
OPENAI_MAX_TOKENS=4096
OPENAI_TIMEOUT_SECONDS=120
```

也可以在命令行中直接传入：

```powershell
python scripts/run_full_digital_textbook.py `
  --material-root D:\DTextbooksTrial\work_material1 `
  --title "试用数字教材" `
  --book-mode `
  --use-llm `
  --llm-base-url "https://your-openai-compatible-endpoint/v1" `
  --llm-api-key "your_api_key" `
  --llm-model "your_model"
```

如果只是快速检查本地流程，可以先不加 `--use-llm`。不使用 LLM 时，教材质量和自动规划能力会下降，但可以验证目录、JSONL 和导出链路是否正常。

## 3. 试用目录怎么放

建议建立一个试用根目录，例如：

```text
D:\DTextbooksTrial\
├── raw\
│   ├── 潘俊屹工作整理\
│   └── 谢志怡工作整理\
├── work_material1\
└── work_material_panjunyi\
```

也就是说，试用根目录里直接放我们处理好的两个工作目录：

```text
D:\DTextbooksTrial\
├── work_material1\
└── work_material_panjunyi\
```

同时在同级 `raw` 目录下放甲方已有的原始文件：

```text
D:\DTextbooksTrial\raw\
├── 潘俊屹工作整理\
└── 谢志怡工作整理\
```

其中：

- `raw\潘俊屹工作整理`：放潘俊屹工作整理对应的原始视频、PPT、Word、PDF、表格、音频等。
- `raw\谢志怡工作整理`：放谢志怡工作整理对应的原始视频、PPT、Word、PDF、表格、音频等。
- `work_material1`：直接放我们提供的已处理工作目录。
- `work_material_panjunyi`：直接放我们提供的已处理工作目录。

`work_material1` 的目录结构示例：

```text
D:\DTextbooksTrial\work_material1\
├── 01_manifest_inventory\
├── 02_working_processing\
│   └── json\
├── 03_review_manual_check\
├── 04_assets_by_course\
└── 05_final_deliverables\
```

`work_material_panjunyi` 同理：

```text
D:\DTextbooksTrial\work_material_panjunyi\
├── 01_manifest_inventory\
├── 02_working_processing\
│   └── json\
└── 05_final_deliverables\
```

`raw` 下可以继续按课程、章节或来源嵌套：

```text
raw\潘俊屹工作整理\
├── 第1部分\
│   ├── 001.mp4
│   └── 课件.pptx
└── 参考资料\
    ├── 教案.docx
    └── 标准.pdf
```

尽量保持原始文件名和准备素材包时使用的文件名一致。如果文件名或相对目录变化很大，建议重新生成素材台账和 evidence JSONL。

## 4. 各目录含义

```text
raw\
```

存放试用方本地原始素材，位于试用根目录下，例如 `D:\DTextbooksTrial\raw`。可以包括视频、PPT、Word、PDF、Markdown、TXT、Excel、CSV、音频等。

```text
01_manifest_inventory\
```

素材台账和规划相关文件，通常包括：

```text
assets_manifest.xlsx
asset_block_map.xlsx
material_blocks.xlsx
domain_config.generated.yml
book_plan.generated.json
```

```text
02_working_processing\json\
```

教材生成读取的 evidence 中间文件，通常包括：

```text
video_segments.jsonl
ppt_assets.jsonl
reference_text_assets.jsonl
audio_segments.jsonl
structured_assets.jsonl
```

如果这些 JSONL 文件已经随包提供，可以不重新处理原始视频/PPT/文档，直接生成教材。

```text
05_final_deliverables\
```

最终输出目录：

```text
agent_workflow\
digital_book\
digital_book.zip
```

## 5. 推荐试用方式：不重新处理素材，只重新生成教材

首次试用推荐这种方式。它直接复用素材包里的：

```text
02_working_processing\json\*.jsonl
```

不会重新切视频、解析 PPT 或抽取文档，速度更快，也更稳定。

### 示例 A：work_material1

```powershell
python scripts/run_full_digital_textbook.py `
  --material-root D:\DTextbooksTrial\work_material1 `
  --title "work_material1试用数字教材" `
  --book-mode `
  --use-llm `
  --manifest-xlsx D:\DTextbooksTrial\work_material1\01_manifest_inventory\assets_manifest.xlsx `
  --llm-cache-path D:\DTextbooksTrial\work_material1\05_final_deliverables\agent_workflow\llm_cache.json `
  --student-package-output D:\DTextbooksTrial\work_material1\05_final_deliverables\digital_book.zip
```

### 示例 B：work_material_panjunyi

```powershell
python scripts/run_full_digital_textbook.py `
  --material-root D:\DTextbooksTrial\work_material_panjunyi `
  --title "work_material_panjunyi试用数字教材" `
  --book-mode `
  --use-llm `
  --manifest-xlsx D:\DTextbooksTrial\work_material_panjunyi\01_manifest_inventory\assets_manifest.xlsx `
  --llm-cache-path D:\DTextbooksTrial\work_material_panjunyi\05_final_deliverables\agent_workflow\llm_cache.json `
  --student-package-output D:\DTextbooksTrial\work_material_panjunyi\05_final_deliverables\digital_book.zip
```

第一次使用 LLM 生成教材时，`ResourceAnalystAgent` 会对 evidence 进行逐条增强分析，耗时较长，也会消耗较多 token。上面命令中的 `--llm-cache-path` 会把 LLM 调用结果保存到固定缓存文件。后续用同一批素材、同一模型和相近参数再次运行时，会优先复用缓存，能明显减少等待时间和 token 消耗。

如果只是想快速验证教材生成链路，不希望第一次试用就触发大量 Resource Analyst LLM 调用，可以加：

```powershell
--skip-resource-analyst-llm
```

这样会跳过逐条素材增强分析，但后续大纲、正文、能力图谱和练习仍可以继续使用 LLM。

输出会写入：

```text
<work_material>\05_final_deliverables\agent_workflow\
<work_material>\05_final_deliverables\digital_book\
<work_material>\05_final_deliverables\digital_book.zip
```

如果希望把视频、关键帧等媒体文件复制进 `digital_book\assets`，可以额外加：

```powershell
--copy-media-assets
```

只有在原始大文件已经放好、且 evidence 中的媒体路径能被解析时，才建议使用 `--copy-media-assets`。否则教材正文可以正常生成，但视频资源可能无法完整打进 zip 包。

## 6. 重新处理素材后再生成教材

以下情况建议重新处理素材：

- 新增或替换了原始视频、PPT、Word/PDF、表格、音频文件。
- `02_working_processing\json` 下缺少 JSONL。
- 原始文件路径或文件名变化，导致媒体无法显示。
- 希望从原始文件重新抽取 evidence。

处理脚本会读取下面两个环境变量：

```powershell
$env:DTEXTBOOKS_WORK = "D:\DTextbooksTrial\work_material1"
$env:DTEXTBOOKS_RAW = "D:\DTextbooksTrial\raw"
```

如果处理 `work_material_panjunyi`，改成：

```powershell
$env:DTEXTBOOKS_WORK = "D:\DTextbooksTrial\work_material_panjunyi"
$env:DTEXTBOOKS_RAW = "D:\DTextbooksTrial\raw"
```

### 第一步：重建素材台账

```powershell
python scripts/build_material_inventory.py `
  --material-root $env:DTEXTBOOKS_WORK `
  --raw-root $env:DTEXTBOOKS_RAW
```

主要更新：

```text
01_manifest_inventory\assets_manifest.xlsx
02_working_processing\json\assets_manifest.json
04_assets_by_course\
```

### 第二步：找到素材板块编码

打开：

```text
<work_material>\01_manifest_inventory\asset_block_map.xlsx
```

或：

```text
<work_material>\01_manifest_inventory\material_blocks.xlsx
```

找到需要处理的 `material_block_code`，下面命令中的 `<target_block_code>` 就替换为这个值。

### 第三步：先 dry run 检查会处理哪些素材

```powershell
python scripts/process_material_block_mvp.py `
  --target-block <target_block_code> `
  --limit-videos 10 `
  --limit-ppt 10 `
  --dry-run
```

### 第四步：处理视频和 PPT

`--limit-videos 0` 和 `--limit-ppt 0` 表示处理该板块下全部视频/PPT：

```powershell
python scripts/process_material_block_mvp.py `
  --target-block <target_block_code> `
  --limit-videos 0 `
  --limit-ppt 0 `
  --merge-main
```

主要写入：

```text
02_working_processing\json\video_segments.jsonl
02_working_processing\json\ppt_assets.jsonl
```

### 第五步：处理参考文档

```powershell
python scripts/process_reference_docs_mvp.py `
  --target-block <target_block_code> `
  --limit-docs 0
```

主要写入：

```text
02_working_processing\json\reference_text_assets.jsonl
```

### 第六步：处理音频和结构化文件

```powershell
python scripts/process_audio_structured_mvp.py `
  --target-block <target_block_code> `
  --limit-audio 0 `
  --limit-structured 0
```

主要写入：

```text
02_working_processing\json\audio_segments.jsonl
02_working_processing\json\structured_assets.jsonl
```

如果有多个 `material_block_code` 需要进入教材，对每个板块重复第三步到第六步。

### 第七步：生成教材

JSONL 准备好后运行：

```powershell
python scripts/run_topic_textbook.py `
  --material-root $env:DTEXTBOOKS_WORK `
  --raw-root $env:DTEXTBOOKS_RAW `
  --title "试用数字教材" `
  --use-llm true `
  --llm-cache-path "$env:DTEXTBOOKS_WORK\05_final_deliverables\agent_workflow\llm_cache.json" `
  --student-package-output "$env:DTEXTBOOKS_WORK\05_final_deliverables\digital_book.zip"
```

如果需要把媒体也复制到数字教材包中：

```powershell
python scripts/run_topic_textbook.py `
  --material-root $env:DTEXTBOOKS_WORK `
  --raw-root $env:DTEXTBOOKS_RAW `
  --title "试用数字教材" `
  --use-llm true `
  --copy-media-assets `
  --llm-cache-path "$env:DTEXTBOOKS_WORK\05_final_deliverables\agent_workflow\llm_cache.json" `
  --student-package-output "$env:DTEXTBOOKS_WORK\05_final_deliverables\digital_book.zip"
```

## 7. 两种方式的区别

| 方式 | 做什么 | 什么时候用 |
| --- | --- | --- |
| 不重新处理素材 | 直接读取已有 `02_working_processing\json\*.jsonl`，重新规划和生成教材。 | 首次试用推荐；速度快，最稳定。 |
| 重新处理素材 | 从 `raw` 原始文件重建台账和 evidence JSONL，然后生成教材。 | 原始文件变了、JSONL 缺失、媒体路径失效、需要重新抽取素材时使用。 |

无论使用哪种方式，教材生成阶段都可以自动生成：

- 领域配置
- 项目/任务式教材大纲
- 项目导学
- 能力图谱
- 学习目标
- 任务正文
- 任务评价
- 填空题和思考题
- 数字教材前端
- 可选的 `digital_book.zip`

## 8. 生成教材的结构

生成结果采用项目化教材结构：

```text
总序
前言
项目1
  项目导学
  能力图谱
  学习目标
  任务1.1
    学习导航
    情境导入
    任务实施
    任务评价
    思考与练习
  任务1.2
  任务1.3
  项目小结
项目2
...
```

只要素材证据足够，每个项目默认至少 3 个任务。素材不足时，系统会记录素材缺口，不会硬编不存在的内容。

## 9. 打开数字教材

不要直接双击 `index.html`。多数浏览器会限制 `file://` 页面读取 `digital_book.json` 和媒体资源。

使用脚本打开：

```powershell
python scripts/open_digital_book.py `
  --material-root D:\DTextbooksTrial\work_material1
```

或：

```powershell
python scripts/open_digital_book.py `
  --material-root D:\DTextbooksTrial\work_material_panjunyi
```

脚本会打开类似下面的本地 HTTP 地址：

```text
http://127.0.0.1:8767/05_final_deliverables/digital_book/index.html
```

## 10. 生成后检查哪些文件

成功生成后，至少检查：

```text
<work_material>\01_manifest_inventory\domain_config.generated.yml
<work_material>\01_manifest_inventory\book_plan.generated.json
<work_material>\01_manifest_inventory\book_plan_review.md

<work_material>\05_final_deliverables\agent_workflow\book_plan.json
<work_material>\05_final_deliverables\agent_workflow\textbook_final.md
<work_material>\05_final_deliverables\agent_workflow\textbook_final.docx

<work_material>\05_final_deliverables\digital_book\index.html
<work_material>\05_final_deliverables\digital_book\digital_book.json
<work_material>\05_final_deliverables\digital_book\app.js
<work_material>\05_final_deliverables\digital_book\styles.css

<work_material>\05_final_deliverables\digital_book.zip
```

## 11. 常见问题

### 找不到 evidence JSONL

检查：

```text
<work_material>\02_working_processing\json\
```

至少应存在下面任意一个：

```text
video_segments.jsonl
ppt_assets.jsonl
reference_text_assets.jsonl
audio_segments.jsonl
structured_assets.jsonl
```

如果没有，请使用“重新处理素材后再生成教材”。

### Evidence 数量不足

默认最低要求：

```text
20 条 evidence chunks
3 个候选项目/章节
```

小样本 smoke test 可以临时降低阈值：

```powershell
python scripts/run_topic_textbook.py `
  --material-root $env:DTEXTBOOKS_WORK `
  --title "Smoke Test" `
  --use-llm false `
  --min-evidence-chunks 2 `
  --min-candidate-chapters 1
```

正式试用输出不建议降低阈值。

### LLM 已启用但未配置

检查 `.env`，或在命令中传入：

```powershell
--llm-base-url
--llm-api-key
--llm-model
```

### 媒体不能显示

常见原因：

- 原始视频/PPT 没有放到试用根目录的 `raw` 目录。
- 原始文件名发生变化。
- JSONL 中的媒体路径来自旧机器。

建议处理：

1. 把原始文件放到正确的 `D:\DTextbooksTrial\raw` 目录。
2. 重建素材台账。
3. 重新处理受影响的素材板块。
4. 如果要生成自包含教材包，生成时加 `--copy-media-assets`。

### 数字教材页面空白

请用：

```powershell
python scripts/open_digital_book.py --material-root <work_material>
```

不要从文件管理器直接打开 `index.html`。

## 12. 验证项目代码

在仓库根目录运行：

```powershell
python -m pytest
```

当前本地分支已验证：

```text
151 passed
```
