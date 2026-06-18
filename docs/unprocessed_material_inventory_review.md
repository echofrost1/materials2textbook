# 未深处理素材盘点与下一步判断

更新时间：2026-06-17

## 1. 盘点结论

`/ai/data/materials2textbook/raw/谢志怡工作整理` 当前登记 154 个文件。按主素材库是否已有可用产物统计：

```text
main_kept:
  video: 38 个源文件
  ppt: 20 个源文件
  audio: 1 个源文件
  spreadsheet: 2 个源文件
  document/pdf: 1 个源文件

processed_not_kept_or_batch_only:
  audio: 2 个源文件

not_deep_processed:
  91 个源文件
```

`not_deep_processed` 里大部分不是漏跑，而是重复文件。真正 `active_for_processing=True` 且未深处理的只有 15 个：

```text
textbook_reference:
  4 个 mp4
  3 个 flv
  2 个 mp3

engineering_material_testing:
  3 个 avi

mechanical_drawing_projection:
  3 个 swf
```

## 2. 哪些和焊接教材强相关

最强相关的是：

```text
A000001 ”1+X“职业技能等级培训教材——特殊焊接技术（初级）.pdf
```

原因：

- 分类为 `textbook_reference`
- `classification_status=exact`
- `relation_type=primary`
- 能提供教材定义、原理、任务描述、规范性文字

剩余 active 未深处理素材里：

- `textbook_reference` 的 fallback 视频/音频：文件名能看出可能有用，但分类证据弱，适合后续人工/Agent 复核后再决定。
- `engineering_material_testing` 的 AVI：偏工程材料性能测试，不是当前焊接教材主线。
- `mechanical_drawing_projection` 的 SWF：偏机械制图投影法，不是当前焊接教材主线，且 SWF 转换成本较高。

## 3. 本轮补的处理能力

新增 PDF 参考资料 MVP：

```text
PDF
→ pdftotext 提取文字层
→ 按文本块切分
→ 生成 reference_text_assets 批次
→ 校验
→ 自动审核
→ keep-only 合并进主库
```

新增/扩展脚本：

```text
scripts/process_reference_docs_mvp.py
scripts/validate_material_batch.py
scripts/review_material_batch.py
scripts/merge_material_batch.py
scripts/build_material_inventory.py
```

## 4. 小批处理结果

已处理：

```text
textbook_reference_reference_text_assets_20260617_112722
```

结果：

```text
reference_text_assets.jsonl: 153 rows
validation: 0 error / 0 warning
auto review: 153 keep / 153
```

说明：这本 PDF 有可提取文字层，不是纯扫描件。抽样看，文本可读，但前几块包含封面、编委、版权等前置信息；后续生成教材时应按章节、关键词、知识点筛选引用，不应整本无差别投喂。

## 5. 是否继续扩大处理

暂不建议继续盲目扩大。

当前最有价值的 PDF 已经入主库。剩余 active 未深处理素材要么分类证据弱，要么偏离焊接教材主线，要么转换成本高。下一步更应该进入“按章节选材”：

`next_processing_queue.xlsx` 当前没有 `Queued` 项；有 9 个 `textbook_reference` 候选处于 `Needs_Confirmation`，它们是低置信 fallback 视频/音频，需要先确认价值，不建议自动深处理。

```text
video_segments
ppt_assets
audio_segments
structured_assets
reference_text_assets
→ chapter_asset_map.xlsx
→ 每章筛选 primary 视频、PPT 图文、参考文本、题库/表格
→ 再生成教材样章
```
