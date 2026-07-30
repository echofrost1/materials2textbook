# 潘俊屹素材处理数据包说明与教材生成入口

本文说明 `/ai/data/materials2textbook/work_material_panjunyi` 中各部分数据的含义、用途，以及如何使用这些处理后数据生成数字教材。

适用场景：甲方已有原始素材 `/raw/潘俊屹工作整理`，我方交付处理后的素材库与生成流程。若甲方需要重新跑处理流程，应保持原始素材目录名和相对路径一致。

## 1. 数据包位置

处理后数据包：

```text
/ai/data/materials2textbook/work_material_panjunyi
```

原始素材位置，甲方如需复跑或回溯证据，需要在同级数据根目录下准备：

```text
/ai/data/materials2textbook/raw/潘俊屹工作整理
```

教材生成代码入口在：

```text
/ai/data/repos/work-manuscript
```

数据处理与校验脚本入口在：

```text
/ai/data/repos/work-data
```

每次进入环境后先执行：

```bash
source /ai/data/use_ai_env.sh
```

## 2. 数据包目录说明

### 01_manifest_inventory

用途：素材台账、分类结果、处理队列和主素材库的 Excel 版清单。

关键文件：

```text
assets_manifest.xlsx              全量素材台账，记录文件名、路径、类型、hash、大小等
active_assets.xlsx                去重后可参与处理/索引的素材列表
asset_block_map.xlsx              素材到课程大块/知识点的映射
material_blocks.xlsx              课程大块定义
next_processing_queue.xlsx        当前处理状态队列
video_segments.xlsx               主视频片段素材库的 Excel 版
ppt_assets.xlsx                   主 PPT 图文素材库的 Excel 版
reference_text_assets.xlsx        主参考文本素材库的 Excel 版
audio_segments.xlsx               主音频素材库的 Excel 版
structured_assets.xlsx            主结构化素材库的 Excel 版
batches/                          每次批处理产物的 Excel 清单
```

其中 `next_processing_queue.xlsx` 可用于查看素材当前状态：

```text
Processed_Main        已进入主素材库
Processed_Batch       有批次产物，但未必进入主库
Needs_Confirmation    候选素材，需要人工或 agent 判断
Queued                仍待处理，通常是坏文件、缺工具或未选择处理
```

### 02_working_processing

用途：机器可读的主素材库、批处理结果、转写、关键帧、PPT 图片和中间产物。

教材生成最重要的是：

```text
02_working_processing/json/video_segments.jsonl
02_working_processing/json/ppt_assets.jsonl
02_working_processing/json/reference_text_assets.jsonl
02_working_processing/json/audio_segments.jsonl
02_working_processing/json/structured_assets.jsonl
```

这 5 个 JSONL 是教材生成脚本默认读取的主素材库。

其他重要目录：

```text
02_working_processing/json/batches/      每批处理的 JSONL 产物
02_working_processing/keyframes/         视频关键帧图片
02_working_processing/ppt_images/        PPT 页面图片/抽取图片
02_working_processing/reference_text/    文档抽取后的文本
02_working_processing/transcripts/       视频/音频 ASR 转写文本
02_working_processing/converted_mp4/     转码后的视频
02_working_processing/converted_pptx/    .ppt 转 .pptx 的中间产物
02_working_processing/agent_llm/         agent/LLM 辅助审核和 ASR 修正结果
```

注意：如果交付后希望网页教材中的关键帧、PPT 图片和证据路径正常显示，不要只发 `json/`，还应同时发 `keyframes/`、`ppt_images/`、`reference_text/`、`transcripts/` 等目录。

### 03_review_manual_check

用途：质量校验、自动审核、人工复核、ASR 修正和失败清单。

关键文件：

```text
resource_bank_final_status_20260625.json              当前素材库最终状态摘要
main_resource_bank_merge_summary_20260625_195118.json 主素材库合并摘要
asr_corrections_qwen3_20260625_retry_final_1280.xlsx  ASR 纠错最终表
remaining_rescue_failures_20260625_203238.xlsx        坏文件/待补传清单
remaining_nonvideo_file_integrity_20260625.xlsx       非视频文件完整性检查
```

当前主素材库数量见 `resource_bank_final_status_20260625.json`。最近一次统计为：

```text
video_segments:          3344
ppt_assets:              6380
reference_text_assets:   1039
audio_segments:          132
structured_assets:       2
```

坏文件已单独列入 backlog。它们通常是 0 字节、文件头全 0、无法解码或无法打开，不建议继续阻塞教材生成。

### 04_assets_by_course

用途：按科目、素材大块、知识点组织的索引层，便于人工查看和回溯素材。

结构示例：

```text
04_assets_by_course/<科目>/<素材大块>/<知识点>/assets_index.xlsx
04_assets_by_course/<科目>/<素材大块>/<知识点>/asset_cards.jsonl
```

说明：`04_assets_by_course` 是索引层，不是教材生成的默认主入口。教材生成主要读取 `02_working_processing/json` 下的 5 个主 JSONL。

### 05_final_deliverables

用途：教材生成结果、章节证据包、前端预览和最终交付物。

常见内容：

```text
05_final_deliverables/chapter_work/       每章证据包、readiness 报告、生成过程
05_final_deliverables/digital_book/       前端数字教材目录
05_final_deliverables/*.zip               可分发教材包
05_final_deliverables/generation_params/  生成参数和运行脚本
```

## 3. 数据处理流程说明

本项目数据处理遵循 docs 中的标准流程：

```text
原始素材登记
-> 去重和粗分类
-> 生成 04 索引层
-> 分素材大块生成 batch
-> validate 结构校验
-> review 自动审核评分
-> keep-only reviewed batch
-> merge main 主素材库
-> 刷新 next_processing_queue 和 04 索引
```

主素材库只合并通过自动审核的 keep 结果。未通过、低价值、坏文件或需要人工确认的素材不会默认进入教材生成。

当前已经将可处理且通过审核的素材合并到主素材库。坏文件保存在：

```text
03_review_manual_check/remaining_rescue_failures_20260625_203238.xlsx
```

## 4. 教材生成前的数据入口

默认教材生成读取：

```text
work_material_panjunyi/02_working_processing/json/video_segments.jsonl
work_material_panjunyi/02_working_processing/json/ppt_assets.jsonl
work_material_panjunyi/02_working_processing/json/reference_text_assets.jsonl
work_material_panjunyi/02_working_processing/json/audio_segments.jsonl
work_material_panjunyi/02_working_processing/json/structured_assets.jsonl
```

不需要把全部素材直接喂给大模型。推荐先按章节生成证据包，再生成教材。

## 5. 生成章节证据包

进入代码目录：

```bash
source /ai/data/use_ai_env.sh
cd /ai/data/repos/work-manuscript
```

生成某一章的证据包，例如“钨极氩弧焊”：

```bash
python scripts/build_chapter_evidence_pack.py \
  --material-root /ai/data/materials2textbook/work_material_panjunyi \
  --chapter 钨极氩弧焊 \
  --output-root /ai/data/materials2textbook/work_material_panjunyi/05_final_deliverables/chapter_work/tig_welding
```

输出：

```text
chapter_evidence_pack.jsonl       该章可用证据包
chapter_evidence_pack.xlsx        人工查看版
chapter_video_segments.jsonl      该章视频证据
chapter_ppt_assets.jsonl          该章 PPT 图文证据
chapter_document_segments.jsonl   该章文档/音频/结构化证据
chapter_readiness_report.xlsx     每个知识点是否足够支撑写作
chapter_evidence_gap_log.xlsx     缺口记录
```

这个步骤主要是规则筛选和整理，不是全库大模型分析，token 消耗很低。

## 6. 生成单章数字教材

推荐使用章节入口：

```bash
source /ai/data/use_ai_env.sh
cd /ai/data/repos/work-manuscript

python scripts/run_chapter_digital_textbook.py \
  --material-root /ai/data/materials2textbook/work_material_panjunyi \
  --chapter-code tig_welding \
  --use-llm \
  --max-input-tokens 120000 \
  --max-chunks-per-knowledge-point 12 \
  --review-rounds 1 \
  --preview-only
```

常用章节代码：

```text
tig_welding                  钨极氩弧焊
welding_equipment_safety     焊接设备与安全
welding_basic_operation      焊接基本操作
shielded_metal_arc_welding   焊条电弧焊
gas_welding_and_cutting      气焊与气割
textbook_reference           教材参考资料
```

输出通常在：

```text
05_final_deliverables/chapter_work/<chapter_code>/
```

如果需要离线 zip 包，可将 `--preview-only` 换成：

```bash
--package-offline --copy-media-assets
```

## 7. 生成整本教材

整本生成入口：

```bash
source /ai/data/use_ai_env.sh
cd /ai/data/repos/work-manuscript

python scripts/run_full_digital_textbook.py \
  --material-root /ai/data/materials2textbook/work_material_panjunyi \
  --title "焊接数字教材" \
  --book-mode \
  --use-llm \
  --max-input-tokens 120000 \
  --max-chunks-per-knowledge-point 12 \
  --review-rounds 1
```

可选参数：

```text
--chapter <章节关键词>              只生成某个章节
--knowledge-point <知识点关键词>    只生成某个知识点
--max-video-records N              限制视频证据数量
--max-document-records N           限制文档/PPT证据数量
--copy-media-assets                复制媒体资源到教材包
--student-package-output PATH      指定学生端 zip 输出路径
```



## 8. 交付建议

若甲方已经持有原始数据，推荐交付：

```text
work_material_panjunyi/
repos/work-data/
repos/work-manuscript/
README 或本说明文档
```

若甲方需要完全离线复现且不能保证原始素材路径一致，再额外交付：

```text
raw/潘俊屹工作整理
```

不建议交付：

```text
/ai/data/models
/ai/data/model-cache
/root
.vscode-server
临时日志和模型缓存
```

## 9. 当前已知边界

1. 坏文件不阻塞教材生成。

   已损坏、0 字节、文件头全 0 或无法解码的素材已记录在 `remaining_rescue_failures_20260625_203238.xlsx`，不进入主素材库。

2. 生成教材以主素材库为准。

   `04_assets_by_course` 方便人工查阅，但生成脚本默认读取 `02_working_processing/json`。

3. 不建议全库 LLM 分析。

   应按章节生成 `chapter_evidence_pack`，再限量交给大模型写教材，避免 token 浪费和低质量素材干扰。

4. 路径需要保持一致。

   JSONL 中包含 `original_path`、`keyframe_paths`、`image_paths` 等相对路径。移动数据包时，应整体移动 `work_material_panjunyi`，不要只复制单个 JSONL。
