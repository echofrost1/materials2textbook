# 素材前处理脚本说明

本文档是 `docs/material_pipeline_forward_plan.md` 的执行手册，只说明脚本怎么跑、会产生什么文件、哪些结果可以进入主素材库。总流程和原则仍以 `material_pipeline_forward_plan.md` 为准。

当前原则：

- 原始文件不改名、不移动。
- 先生成前置台账、分类、素材大块映射和 `04_assets_by_course` 索引层。
- 深处理默认只写 batch，不直接污染主 `video_segments.jsonl` / `ppt_assets.jsonl`。
- batch 必须先校验，再自动审核评分。
- 默认只把 `Agent_Keep` 的 reviewed batch 合并进主结果。
- 生成教材读取主 JSONL；未验收 batch 不参与教材生成。

## 0. 重建台账、分类、大块和索引层

对应总方案阶段 1-5：

```powershell
conda run -n textbook_asr python scripts\build_material_inventory.py `
  --material-root work_materials\work_material1
```

主要输出：

```text
01_manifest_inventory/assets_manifest.xlsx
01_manifest_inventory/active_assets.xlsx
01_manifest_inventory/duplicate_groups.xlsx
01_manifest_inventory/front_classification_all_materials.xlsx
01_manifest_inventory/material_blocks.xlsx
01_manifest_inventory/asset_block_map.xlsx
01_manifest_inventory/next_processing_queue.xlsx
02_working_processing/json/assets_manifest.json
04_assets_by_course/<科目>/<素材大块>/<知识点>/assets_index.xlsx
04_assets_by_course/<科目>/<素材大块>/<知识点>/asset_cards.jsonl
```

这一步只做登记、粗分类、索引和排队，不做 ASR、不抽帧、不切视频，也不会改动已经生成的 `video_segments` 和 `ppt_assets` 主结果。

## 1. 按素材大块生成 batch

先预览会处理哪些素材：

```powershell
conda run -n textbook_asr python scripts\process_material_block_mvp.py `
  --target-block shielded_metal_arc_welding `
  --limit-videos 8 `
  --limit-ppt 3 `
  --dry-run
```

确认后生成 batch：

```powershell
conda run -n textbook_asr python scripts\process_material_block_mvp.py `
  --target-block shielded_metal_arc_welding `
  --limit-videos 8 `
  --limit-ppt 3
```

默认输出：

```text
02_working_processing/json/batches/<block>_video_segments_<batch_id>.jsonl
02_working_processing/json/batches/<block>_ppt_assets_<batch_id>.jsonl
01_manifest_inventory/batches/<block>_video_segments_<batch_id>.xlsx
01_manifest_inventory/batches/<block>_ppt_assets_<batch_id>.xlsx
```

说明：

- 视频会做转码、抽音频、ASR、关键帧和 MVP 粗切片。
- PPT 会提取页级文字、图片路径和证据文本。
- 脚本会避开主结果和既有 batch 中已经处理过的 `source_asset_id`，减少重复处理。
- 日常不建议使用 `--merge-main`，合并应走“校验 -> 审核 -> keep-only 合并”。

## 2. 校验 batch

校验视频 batch：

```powershell
python scripts\validate_material_batch.py `
  --batch-jsonl work_materials\work_material1\02_working_processing\json\batches\shielded_metal_arc_welding_video_segments_20260616_175526.jsonl
```

校验 PPT batch：

```powershell
python scripts\validate_material_batch.py `
  --batch-jsonl work_materials\work_material1\02_working_processing\json\batches\shielded_metal_arc_welding_ppt_assets_20260616_175526.jsonl
```

输出：

```text
03_review_manual_check/<batch_name>_validation.xlsx
03_review_manual_check/<batch_name>_validation_summary.json
```

校验只判断结构是否可用，例如必填字段、ID 是否重复、时间码是否合法、关键帧/图片文件是否存在。它不负责判断教学价值。

## 3. 自动审核评分 batch

审核视频 batch：

```powershell
python scripts\review_material_batch.py `
  --batch-jsonl work_materials\work_material1\02_working_processing\json\batches\shielded_metal_arc_welding_video_segments_20260616_175526.jsonl
```

审核 PPT batch：

```powershell
python scripts\review_material_batch.py `
  --batch-jsonl work_materials\work_material1\02_working_processing\json\batches\shielded_metal_arc_welding_ppt_assets_20260616_175526.jsonl
```

输出：

```text
02_working_processing/json/batches/<batch_name>_reviewed.jsonl
01_manifest_inventory/batches/<batch_name>_reviewed.xlsx
03_review_manual_check/<batch_name>_review.xlsx
03_review_manual_check/<batch_name>_review_summary.json
```

审核脚本会写入：

```text
quality_score
auto_review_decision   keep / needs_review / reject
review_status          Agent_Keep / Needs_Review / Agent_Reject
review_comment
review_basis
domain_term_hits
```

## 4. 生成 keep-only reviewed batch

主素材库默认只合并 `keep`：

```powershell
python scripts\review_material_batch.py `
  --batch-jsonl work_materials\work_material1\02_working_processing\json\batches\shielded_metal_arc_welding_video_segments_20260616_175526.jsonl `
  --output-prefix shielded_metal_arc_welding_video_segments_20260616_175526_keep `
  --keep-only
```

```powershell
python scripts\review_material_batch.py `
  --batch-jsonl work_materials\work_material1\02_working_processing\json\batches\shielded_metal_arc_welding_ppt_assets_20260616_175526.jsonl `
  --output-prefix shielded_metal_arc_welding_ppt_assets_20260616_175526_keep `
  --keep-only
```

生成：

```text
02_working_processing/json/batches/<batch_name>_keep_reviewed.jsonl
01_manifest_inventory/batches/<batch_name>_keep_reviewed.xlsx
```

## 5. 校验 keep-only reviewed batch

```powershell
python scripts\validate_material_batch.py `
  --batch-jsonl work_materials\work_material1\02_working_processing\json\batches\shielded_metal_arc_welding_video_segments_20260616_175526_keep_reviewed.jsonl
```

```powershell
python scripts\validate_material_batch.py `
  --batch-jsonl work_materials\work_material1\02_working_processing\json\batches\shielded_metal_arc_welding_ppt_assets_20260616_175526_keep_reviewed.jsonl
```

## 6. 合并 keep-only reviewed batch

```powershell
python scripts\merge_material_batch.py `
  --batch-jsonl work_materials\work_material1\02_working_processing\json\batches\shielded_metal_arc_welding_video_segments_20260616_175526_keep_reviewed.jsonl
```

```powershell
python scripts\merge_material_batch.py `
  --batch-jsonl work_materials\work_material1\02_working_processing\json\batches\shielded_metal_arc_welding_ppt_assets_20260616_175526_keep_reviewed.jsonl
```

合并时会先备份主文件：

```text
02_working_processing/json/backups/
```

然后更新：

```text
01_manifest_inventory/video_segments.xlsx
02_working_processing/json/video_segments.jsonl
01_manifest_inventory/ppt_assets.xlsx
02_working_processing/json/ppt_assets.jsonl
```

合并后建议再跑一次第 0 步，刷新 `next_processing_queue.xlsx` 中的 `Processed_Main` 状态。

## 7. 当前已完成批次

已处理并合并进主结果：

```text
shielded_metal_arc_welding_video_segments_20260616_175526_keep_reviewed.jsonl
shielded_metal_arc_welding_ppt_assets_20260616_175526_keep_reviewed.jsonl
shielded_metal_arc_welding_video_segments_20260616_201511_keep_reviewed.jsonl
shielded_metal_arc_welding_ppt_assets_20260616_201511_keep_reviewed.jsonl
welding_equipment_safety_video_segments_20260616_203638_keep_reviewed.jsonl
welding_equipment_safety_ppt_assets_20260616_203638_keep_reviewed.jsonl
welding_basic_operation_video_segments_20260616_205015_keep_reviewed.jsonl
welding_basic_operation_ppt_assets_20260616_205015_keep_reviewed.jsonl
welding_basic_operation_video_segments_20260616_210250_keep_reviewed.jsonl
welding_equipment_safety_ppt_assets_20260616_210424_keep_reviewed.jsonl
gas_welding_and_cutting_audio_segments_20260616_211435_keep_reviewed.jsonl
welding_equipment_safety_structured_assets_20260616_211522_keep_reviewed.jsonl
welding_basic_operation_structured_assets_20260616_211736_keep_reviewed.jsonl
textbook_reference_reference_text_assets_20260617_112722_keep_reviewed.jsonl
```

结果：

```text
batch 20260616_175526:
  video keep: 12 / 12
  PPT keep: 212 / 289
  PPT needs_review: 73
  PPT reject: 4

batch 20260616_201511:
  video keep: 18 / 18
  PPT keep: 149 / 258
  PPT needs_review: 106
  PPT reject: 3

batch 20260616_203638:
  video keep: 10 / 10
  PPT keep: 31 / 106
  PPT needs_review: 71
  PPT reject: 4

batch 20260616_205015:
  target block: welding_basic_operation
  video keep: 14 / 14
  PPT keep: 2 / 17
  PPT needs_review: 11
  PPT reject: 4

batch 20260616_210250:
  target block: welding_basic_operation
  video keep: 2 / 2

batch 20260616_210424:
  target block: welding_equipment_safety
  PPT keep: 4 / 80
  PPT needs_review: 72
  PPT reject: 4

batch 20260616_211413:
  target block: tig_welding
  audio keep: 0 / 1
  audio needs_review: 1

batch 20260616_211435:
  target block: gas_welding_and_cutting
  audio keep: 1 / 2
  audio needs_review: 1

batch 20260616_211522:
  target block: welding_equipment_safety
  structured keep: 6 / 6

batch 20260616_211736:
  target block: welding_basic_operation
  structured keep: 6 / 6

batch 20260617_112722:
  target block: textbook_reference
  reference text keep: 153 / 153
```

当前主结果：

```text
video_segments.jsonl: 96 rows
  tig_welding: 40
  shielded_metal_arc_welding: 30
  welding_equipment_safety: 10
  welding_basic_operation: 16

ppt_assets.jsonl: 813 rows
  tig_welding: 415
  shielded_metal_arc_welding: 361
  welding_equipment_safety: 35
  welding_basic_operation: 2

audio_segments.jsonl: 1 row
  gas_welding_and_cutting: 1

structured_assets.jsonl: 12 rows
  welding_equipment_safety: 6
  welding_basic_operation: 6

reference_text_assets.jsonl: 153 rows
  textbook_reference: 153
```

当前 `next_processing_queue.xlsx` 已无 `Queued` 项；另有 9 个 `Needs_Confirmation` 的 `textbook_reference` 候选，均为低置信 fallback 视频/音频，不会自动深处理。两条音频没有达到 keep 门槛，保留在批次审核记录中，后续可人工或更强 ASR/术语纠错后再决定是否合入主库。
