# 教材素材整理流水线执行方案

本文档只描述当前要跑通的素材处理流水线。目标是先把甲方原始资料整理成一个可控、可检索、可深处理的教材素材库，而不是一开始就生成完整教材。

核心原则：

```text
原始资料不动
先建台账
全量前置分类
再分教材素材大块
只对需要的大块做深处理
长视频处理成可引用片段
所有结果同时输出 Excel 和 JSONL
```

---

## 1. 目录结构

```text
Textbook_Project/
├─ /ai/data/materials2textbook/raw/谢志怡工作整理/
├─ 01_manifest_inventory/
├─ 02_working_processing/
├─ 04_assets_by_course/
└─ 05_final_deliverables/
```

### 1.1 目录用途

| 目录 | 用途 | 是否必须 |
|---|---|---|
| `/ai/data/materials2textbook/raw/谢志怡工作整理/` | 保存甲方原始资料 | 必须 |
| `01_manifest_inventory/` | 保存台账、分类表、去重表、素材大块表 | 必须 |
| `02_working_processing/` | 保存转写、抽帧、切片、OCR 等深处理中间产物 | 必须存在，但不是所有素材都立刻进入 |
| `04_assets_by_course/` | 保存按科目 / 素材大块 / 知识点组织的索引 | 必须 |
| `05_final_deliverables/` | 保存最终教材、样章、导出片段包、交付说明 | 最后阶段才使用 |

`03_review_manual_check/` 暂不作为主流程目录。复核结果先放在 `01_manifest_inventory/` 的 review 表里，后续如果人工复核量很大，再单独恢复该目录。

### 1.2 原始资料处理原则

`/ai/data/materials2textbook/raw/谢志怡工作整理/` 只读保存：

- 不改名。
- 不移动。
- 不删除。
- 不在原目录里生成中间文件。

后续所有处理结果都通过 `asset_id` 和 `original_path` 指回原始文件。

---

## 2. 台账是什么

台账就是素材总登记表，类似仓库入库清单。每个原始文件都必须在台账里有一行。

台账至少记录：

| 字段 | 说明 |
|---|---|
| `asset_id` | 素材唯一编号 |
| `original_path` | 原始文件路径 |
| `filename` | 文件名 |
| `file_type` | 文件类型，如 pdf、pptx、video、audio、excel |
| `file_size` | 文件大小 |
| `duration_or_pages` | 视频/音频时长或文档页数 |
| `file_hash` | 文件 hash，用于判断完全重复 |
| `source_week` | 原始周次目录，如第一周、第二周 |
| `source_folder` | 原始子目录 |
| `subject_cn` | 科目 |
| `knowledge_point_cn` | 知识点或粗知识点 |
| `classification_level` | exact / coarse / fallback |
| `material_block_cn` | 教材素材大块 |
| `duplicate_group_id` | 完全重复组 |
| `similar_group_id` | 相似主题组 |
| `processed_status` | 已扫描 / 待深处理 / 已转写 / 已切片 |
| `review_status` | 待审核 / 已通过 / 需复核 |

第一阶段的关键产物：

```text
01_manifest_inventory/assets_manifest.xlsx
01_manifest_inventory/front_classification_all_materials.xlsx
01_manifest_inventory/material_blocks.xlsx
```

---

## 3. 流水线总览

```text
步骤 0：原始资料入库
步骤 1：扫描生成台账
步骤 2：全量前置分类
步骤 3：划分教材素材大块
步骤 4：识别重复、相似和长短版本
步骤 5：生成按素材大块组织的索引目录
步骤 6：选择素材大块进入深处理
步骤 7：长视频转写、抽帧、切片、打标签
步骤 8：输出片段级 Excel + JSONL
步骤 9：后续进入数据库 / 向量库
步骤 10：按目标教材检索素材并生成教材
```

---

## 4. 步骤 0：原始资料入库

### 4.1 输入

甲方给的所有原始文件。

### 4.2 操作

把资料放入：

```text
/ai/data/materials2textbook/raw/谢志怡工作整理/
```

保留原始目录，例如：

```text
/ai/data/materials2textbook/raw/谢志怡工作整理/
├─ 第一周/
├─ 第二周/
├─ 第三周/
├─ 第四周/
├─ 第五周/
├─ 第六周/
└─ 教材 PDF 或其他综合资料
```

### 4.3 输出

无加工输出，只确认原始资料完整存在。

### 4.4 验收

- 原始文件能正常访问。
- 没有被改名、移动或删除。

---

## 5. 步骤 1：扫描生成台账

### 5.1 输入

```text
/ai/data/materials2textbook/raw/谢志怡工作整理/
```

### 5.2 操作

程序递归扫描所有文件：

```text
读取路径
识别文件类型
计算文件大小
计算 file_hash
读取音视频时长
提取 source_week 和 source_folder
生成 asset_id
写入台账
```

### 5.3 输出

```text
01_manifest_inventory/assets_manifest.xlsx
02_working_processing/json/assets_manifest.json
```

### 5.4 验收

- 每个原始文件都在台账里有一行。
- `asset_id` 唯一。
- `original_path` 能指回原文件。
- 视频和音频尽量有时长。
- `file_hash` 可用于判断完全重复。

---

## 6. 步骤 2：全量前置分类

这一步的目标是：所有素材都必须有一个前置分类，不能留空。

### 6.1 输入

```text
assets_manifest.xlsx
文件名
原始目录名
PPT 标题或可快速提取的文本
```

### 6.2 操作

按以下顺序分类：

```text
先判断科目
→ 再判断知识点
→ 判断不准时进入粗知识点
→ 仍判断不准时用文件名作为临时候选知识点
```

分类层级：

| 层级 | 说明 | 示例 |
|---|---|---|
| `exact` | 能明确判断具体知识点 | 焊接坡口、非接触引弧、正投影法 |
| `coarse` | 只能判断大主题 | 焊接基本操作、钨极氩弧焊、气焊与气割 |
| `fallback` | 只能用文件名临时成组 | 模块2-6、焊前定位焊 |

### 6.3 输出

```text
01_manifest_inventory/front_classification_all_materials.xlsx
```

表内至少包含：

```text
asset_id
front_subject_cn
front_subject_code
front_knowledge_point_cn
front_knowledge_point_code
classification_level
classification_basis
filename
file_type
source_week
source_folder
original_path
```

### 6.4 验收

- 所有文件都有 `front_subject_cn`。
- 所有文件都有 `front_knowledge_point_cn`。
- 不出现空分类。
- `fallback` 可以存在，但后续要优先复核。

---

## 7. 步骤 3：划分教材素材大块

只按知识点还不够。一本教材不能从所有资料里检索素材，所以要先建立“素材大块”，作为教材生成时的检索边界。

### 7.1 输入

```text
front_classification_all_materials.xlsx
```

### 7.2 操作

根据科目、粗知识点、文件名和目录，把素材归入教材素材大块。

初版大块：

| 素材大块 | code | 包含内容 |
|---|---|---|
| 焊接鉴定与试题 | `welding_assessment_exam` | 第一周鉴定 Excel、题库类文件 |
| 焊条电弧焊 | `shielded_metal_arc_welding` | 焊条、药皮、焊芯、焊条电弧焊操作 |
| 焊接基本操作 | `welding_basic_operation` | 坡口、运条、收弧、焊缝连接、参数选择、缺陷 |
| 焊接设备与安全 | `welding_equipment_safety` | 设备、电源、焊钳、电缆、面罩、安全防护 |
| 钨极氩弧焊 | `tig_welding` | 非接触引弧、送丝、钨极、TIG 操作 |
| 气焊与气割 | `gas_welding_and_cutting` | 切割速度、预热火焰、焊炬角度、气割安全 |
| 机械制图-投影法 | `mechanical_drawing_projection` | 中心投影、正投影、斜投影 |
| 工程材料-性能测试 | `engineering_material_testing` | 布氏硬度、洛氏硬度、冲击韧性 |
| 教材参考资料 | `textbook_reference` | PDF 教材、综合参考材料 |
| 待细分综合资料 | `*_to_refine` | 暂时不能稳定归块的资料 |

### 7.3 输出

```text
01_manifest_inventory/material_blocks.xlsx
```

字段：

```text
asset_id
front_subject_cn
front_knowledge_point_cn
material_block_cn
material_block_code
block_reason
block_confidence
filename
file_type
original_path
```

### 7.4 验收

- 每个文件都有 `material_block_cn`。
- 一个目标教材可以明确选择允许检索的大块。
- `block_confidence < 0.7` 的素材进入待细分清单。

示例：

```text
目标教材：特殊焊接技术初级
允许检索：焊条电弧焊、焊接基本操作、钨极氩弧焊、气焊与气割、焊接设备与安全、教材参考资料
默认不检索：机械制图-投影法、工程材料-性能测试
```

---

## 8. 步骤 4：识别重复、相似和长短版本

### 8.1 输入

```text
assets_manifest.xlsx
front_classification_all_materials.xlsx
material_blocks.xlsx
```

### 8.2 操作

分三层判断。

第一层：完全重复

```text
file_hash 完全一致
→ 标记 exact_duplicate
→ 放入 duplicate_group_id
```

第二层：相似主题

```text
文件名相同或高度相似
目录相近
科目 / 素材大块相同
→ 标记 similar_group_id
```

第三层：长短版本 / 扩展版本

```text
同主题视频
→ 比较时长
→ 比较开头、中间、结尾关键帧
→ 深处理后比较字幕和音频
→ 判断 shorter_version / extended_version / partial_overlap
```

### 8.3 输出

```text
01_manifest_inventory/duplicate_groups.xlsx
```

### 8.4 验收

- 完全重复素材被分组。
- 同名或同主题素材被标记为相似。
- 不删除任何原始文件。
- 推荐一个 `preferred_for_use`，但保留所有来源记录。

---

## 9. 步骤 5：生成索引目录

### 9.1 输入

```text
material_blocks.xlsx
assets_manifest.xlsx
```

### 9.2 操作

在 `04_assets_by_course/` 下生成索引目录。

推荐结构：

```text
04_assets_by_course/
└─ 焊接技术_welding_technology/
   └─ 焊接基本操作_welding_basic_operation/
      └─ 焊接坡口_welding_groove/
         ├─ assets_index.xlsx
         └─ asset_cards.jsonl
```

注意：

- 不复制大文件。
- 不移动原始文件。
- `assets_index.xlsx` 只记录原始路径、分类、大块、去重关系和处理状态。

### 9.3 输出

```text
04_assets_by_course/<科目>/<素材大块>/<知识点>/assets_index.xlsx
04_assets_by_course/<科目>/<素材大块>/<知识点>/asset_cards.jsonl
```

### 9.4 验收

- 每个素材都能从索引目录找到。
- 索引能指回 `/ai/data/materials2textbook/raw/谢志怡工作整理/` 原始文件。
- 后续教材检索优先从 `04_assets_by_course/` 进入，而不是直接扫全量原始目录。

---

## 10. 步骤 6：选择素材大块进入深处理

### 10.1 输入

```text
material_blocks.xlsx
04_assets_by_course/
```

### 10.2 进入 02 的条件

素材不需要全部立刻进入 `02_working_processing/`。

进入条件：

```text
属于当前目标教材需要的大块
且是视频、音频、重要 PPT、重要 PDF、需要 OCR 的图片/课件
或分类不准但明显有价值
或存在长短版本关系，需要进一步判断
```

### 10.3 输出目录

```text
02_working_processing/
├─ converted_mp4/
├─ audio/
├─ transcripts/
├─ keyframes/
├─ asset_cards/
└─ json/
```

### 10.4 验收

- 被选中的素材有对应中间产物。
- 未选中的素材仍只保留在台账和索引里，不浪费处理成本。

---

## 11. 步骤 7：长视频处理

长视频不能只在文件层面打一个标签。最终教材需要的是可插入章节的小片段。

### 11.1 输入

属于目标素材大块的长视频。

### 11.2 操作

```text
长视频
→ 保留原始文件
→ 统一转码 MP4
→ 抽取音频
→ ASR 转写，生成带时间戳字幕
→ 根据字幕停顿 + 语义变化 + 画面变化生成候选片段
→ 候选片段通常控制在 30s-3min，最终优先选 1-2min
→ 抽关键帧
→ OCR 识别画面文字
→ AI 生成摘要、知识点、标签、推荐章节、有用程度评分
→ 人工 / 审查 Agent 复核片段是否可用
→ 写入 xlsx + jsonl
```

### 11.3 重要判断

30s-3min 只是建议，不是硬规则。判断标准是：

```text
片段是否表达一个完整教学动作
片段是否能独立插入教材
片段开始和结束是否自然
画面和字幕是否能支撑知识点
```

对于实操视频，字幕可能很少，不能只靠 ASR。必须同时参考：

```text
字幕停顿
语义变化
画面变化
关键帧
OCR 文本
文件名和来源目录
```

### 11.4 输出

```text
02_working_processing/converted_mp4/
02_working_processing/audio/
02_working_processing/transcripts/
02_working_processing/keyframes/
01_manifest_inventory/video_segments.xlsx
02_working_processing/json/video_segments.jsonl
```

### 11.5 video_segments 字段

```text
clip_id
source_asset_id
source_video
start_time
end_time
subject
material_block
knowledge_point
clip_summary
tags
recommended_chapter
usefulness_score
quality_score
overlap_with
unique_time_range
review_status
```

### 11.6 长短版本处理

如果 A 是 2 分钟，B 是 3 分钟，并且 B 的前 2 分钟与 A 基本一致：

```text
A = shorter_version
B = extended_version
B 的 00:00-02:00 = 与 A 重叠
B 的 02:00-03:00 = B 独有片段
```

处理原则：

- 不直接删除短版本。
- 重叠片段只推荐一个版本。
- 长版本新增内容如果有价值，单独作为片段记录。
- 如果短版本画质或讲解更清楚，也可以推荐短版本。

---

## 12. 步骤 8：输出 Excel + JSONL

### 12.1 为什么两种格式都要

| 格式 | 用途 |
|---|---|
| Excel | 给人看、筛选、复核 |
| JSONL | 给程序、AI、数据库、向量库使用 |

### 12.2 输出清单

第一阶段：

```text
01_manifest_inventory/assets_manifest.xlsx
01_manifest_inventory/front_classification_all_materials.xlsx
01_manifest_inventory/material_blocks.xlsx
01_manifest_inventory/duplicate_groups.xlsx
```

深处理阶段：

```text
01_manifest_inventory/video_segments.xlsx
02_working_processing/asset_cards/*.jsonl
02_working_processing/json/video_segments.jsonl
```

---

## 13. 步骤 9：进入数据库 / 向量库

这一步在片段级数据稳定后再做。

### 13.1 不建议入库的内容

不要直接把整个长视频作为一个向量对象入库。

### 13.2 建议入库的内容

```text
片段字幕
片段摘要
知识点标签
推荐章节
关键帧 OCR 文本
原始视频路径
start_time / end_time
质量评分
可用性评分
review_status
```

### 13.3 检索必须带过滤条件

教材生成时不能从全量素材库直接语义检索。必须先过滤：

```text
subject
material_block
knowledge_point
review_status
quality_score
```

示例：

```text
目标：生成“钨极氩弧焊非接触引弧”章节

先过滤：
subject = 焊接技术
material_block = 钨极氩弧焊
knowledge_point in [非接触引弧, 钨极氩弧焊]
review_status = 已通过

再做语义检索。
```

---

## 14. 步骤 10：教材生成

教材生成不是当前第一阶段任务，但流水线要为它准备好素材。

生成教材时的输入应该是：

```text
目标教材大纲
允许检索的素材大块
已审核片段
PPT/PDF 文本
图片和关键帧
素材引用路径
```

输出：

```text
05_final_deliverables/
├─ 教材章节初稿
├─ 素材引用清单
├─ 推荐插入视频片段
├─ 推荐插入图片/关键帧
└─ 交付说明
```

---

## 15. 当前已完成

当前项目已有：

```text
01_manifest_inventory/assets_manifest_mvp.xlsx
01_manifest_inventory/front_classification_all_materials.xlsx
01_manifest_inventory/material_blocks.xlsx
01_manifest_inventory/duplicate_groups_mvp.xlsx
01_manifest_inventory/video_segments_mvp.xlsx
```

当前分类大块初版结果：

```text
焊接基本操作       66
钨极氩弧焊        22
焊接设备与安全      21
焊条电弧焊        16
气焊与气割        15
机械制图-投影法      4
工程材料-性能测试     4
焊接鉴定与试题       2
教材参考资料        1
焊接综合待细分       3
```

说明：

- 已经不再是从全量资料里盲目检索。
- 已经有素材大块雏形。
- `焊接综合待细分` 需要后续复核。

---

## 16. 下一步执行建议

建议先跑通一个最小闭环：

```text
1. 选一个素材大块：焊接基本操作 或 钨极氩弧焊
2. 从该大块中选 3-5 个视频
3. 进入 02_working_processing/
4. 完成转码、抽音频、ASR、关键帧、OCR、候选切片
5. 生成 video_segments.xlsx 和 video_segments.jsonl
6. 人工确认片段是否真的可用于教材
7. 用确认后的片段测试章节检索
```

跑通后，再扩大到其他素材大块。
